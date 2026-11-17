"""Evaluate typed questions against the public transcript and note labels."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

from layer3.text.extract import extract, load_questions
from shared.db import connect

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LABELS = ROOT / "data" / "raw" / "maple_collections_release" / "labels"
DEFAULT_OUTPUT = ROOT / "outputs" / "layer3_label_eval.md"


def _column(frame: pd.DataFrame, *candidates: str) -> str | None:
    lookup = {str(c).lower(): c for c in frame.columns}
    return next((lookup[c.lower()] for c in candidates if c.lower() in lookup), None)


def _normalise(value, keep_none: bool = False):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "none" if keep_none else None
    text = str(value).strip().lower()
    if keep_none and text == "none":
        return "none"
    if text in {"", "nan", "none", "null", "unknown", "n/a"}:
        return None
    if text in {"true", "yes", "y", "1", "1.0"}:
        return True
    if text in {"false", "no", "n", "0", "0.0"}:
        return False
    try:
        return round(float(text.replace("$", "").replace(",", "")), 4)
    except ValueError:
        return text.replace(" ", "_")


TONE = {"cooperative": "positive", "neutral": "neutral", "distressed": "negative", "frustrated": "negative",
        "hostile": "negative"}


def _comparable(qid, expected, predicted):
    """Put label and answer on the same scale where the labels use a different one (questions.yaml notes)."""
    if expected is None:
        return None, predicted
    if qid == "sentiment":          # labels are tone words, answers a score from -1 to 1
        tone = (None if predicted is None or isinstance(predicted, str) else
                "negative" if predicted <= -0.3 else "positive" if predicted >= 0.3 else "neutral")
        return TONE.get(str(expected)), tone
    if qid == "promise_intent_strength":   # labels are a strength score; answers firm / conditional / weak
        firm_label = isinstance(expected, float) and expected >= 0.6
        return ("firm" if firm_label else "not_firm"), (None if predicted is None else
                                                        "firm" if predicted == "firm" else "not_firm")
    return expected, predicted


def _macro_f1(expected: list, predicted: list) -> float:
    classes = set(expected) | set(predicted)
    if not classes:
        return float("nan")
    scores = []
    for cls in classes:
        tp = sum(a == cls and b == cls for a, b in zip(expected, predicted))
        fp = sum(a != cls and b == cls for a, b in zip(expected, predicted))
        fn = sum(a == cls and b != cls for a, b in zip(expected, predicted))
        precision = tp / (tp + fp) if tp + fp else 0
        recall = tp / (tp + fn) if tp + fn else 0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0)
    return sum(scores) / len(scores)


def evaluate_frame(frame: pd.DataFrame, source_type: str) -> list[dict]:
    text_col = _column(frame, "text_redacted", "transcript_text_redacted", "note_text_redacted", "text",
                       "transcript", "note_text")
    if not text_col:
        raise ValueError(f"no text column found in {source_type} labels")
    questions = {q["id"]: q for q in load_questions() if source_type in q["applies_to"]}
    collected = {qid: {"expected": [], "predicted": [], "prob": []} for qid in questions}
    excluded: dict = {}
    for record in frame.to_dict("records"):
        output = extract(str(record.get(text_col) or ""), source_type)
        for qid, question in questions.items():
            label_col = _column(frame, question["label_column"], qid, f"label_{qid}", f"{qid}_label")
            if not label_col:
                continue
            keep_none = "none" in question.get("options", [])        # 'none' is an answer, not missing
            expected = _normalise(record.get(label_col), keep_none)
            predicted = _normalise(output[qid]["answer"], keep_none)
            expected, predicted = _comparable(qid, expected, predicted)
            if expected is None:
                continue
            if qid == "hardship_reason" and expected == "disability":  # protected ground: not asked (D-37)
                excluded[qid] = excluded.get(qid, 0) + 1
                continue
            collected[qid]["expected"].append(expected)
            collected[qid]["predicted"].append(predicted)
            collected[qid]["prob"].append(float(output[qid]["prob"]))
    results = []
    for qid, values in collected.items():
        if not values["expected"]:
            continue
        correct = [a == b for a, b in zip(values["expected"], values["predicted"])]
        brier = sum((p - int(ok)) ** 2 for p, ok in zip(values["prob"], correct)) / len(correct)
        results.append({"source": source_type, "question": qid, "n": len(correct),
                        "accuracy": sum(correct) / len(correct),
                        "macro_f1": _macro_f1(values["expected"], values["predicted"]),
                        "brier": brier, "excluded": excluded.get(qid, 0)})
    return results


def _attach_redacted_text(frame: pd.DataFrame, source_type: str, con) -> pd.DataFrame:
    """Join labels that contain only IDs to the permitted redacted silver text."""
    if _column(frame, "text_redacted", "transcript_text_redacted", "note_text_redacted", "text",
               "transcript", "note_text"):
        return frame
    id_col = _column(frame, "transcript_id" if source_type == "transcript" else "note_id")
    if not id_col:
        raise ValueError(f"{source_type} labels contain neither text nor a source ID")
    ids = frame[[id_col]].drop_duplicates().rename(columns={id_col: "source_id"})
    con.register("l3_eval_ids", ids)
    try:
        if source_type == "transcript":
            text = con.execute("""
                SELECT t.transcript_id source_id,
                       string_agg(t.text_redacted, '\n' ORDER BY t.turn_index) text_redacted
                FROM silver.transcript_turns_redacted t JOIN l3_eval_ids i ON i.source_id = t.transcript_id
                GROUP BY t.transcript_id""").df()
        else:
            text = con.execute("""
                SELECT n.note_id source_id, n.note_text_redacted text_redacted
                FROM silver.agent_notes_redacted n JOIN l3_eval_ids i ON i.source_id = n.note_id""").df()
    finally:
        con.unregister("l3_eval_ids")
    return frame.merge(text, left_on=id_col, right_on="source_id", how="left").drop(columns=["source_id"])


def time_to_hardship(frame: pd.DataFrame, con=None) -> dict | None:
    id_col = _column(frame, "transcript_id")
    mention_col = _column(frame, "hardship_first_mention_sec", "label_hardship_first_mention_sec")
    if not id_col or not mention_col:
        return None
    con = con or connect(read_only=True)
    delays = []
    misses = 0
    for record in frame.to_dict("records"):
        gold = record.get(mention_col)
        if pd.isna(gold) or str(gold).strip() == "":
            continue
        try:
            gold = float(gold)
        except (TypeError, ValueError):
            continue
        turns = con.execute("SELECT start_sec, text_redacted FROM silver.transcript_turns_redacted "
                            "WHERE transcript_id = ? ORDER BY turn_index", [record[id_col]]).fetchall()
        flagged = None
        for start_sec, text in turns:
            if extract(text or "", "turn")["hardship"]["answer"] in {"possible", "clear"}:
                flagged = float(start_sec)
                break
        if flagged is None:
            misses += 1
        else:
            delays.append(flagged - gold)
    return {"labelled": len(delays) + misses, "flagged": len(delays), "misses": misses,
            "mean_delay_sec": sum(delays) / len(delays) if delays else None,
            "median_delay_sec": float(pd.Series(delays).median()) if delays else None}


def extractor_mode() -> str:
    import os
    mode = os.environ.get("L3_EXTRACTOR_MODE", "auto").strip().lower()
    from shared import config
    has_key = config.llm_available() or bool(os.environ.get("JEV_API_KEY"))
    return ("rules (offline; no API key set)" if mode == "rules" or (mode == "auto" and not has_key)
            else f"LLM {config.model_fast()} via shared.decide")


def evaluate(labels_dir=DEFAULT_LABELS, output=DEFAULT_OUTPUT, con=None) -> Path:
    labels_dir, output = Path(labels_dir), Path(output)
    transcript_path = labels_dir / "call_transcripts_labels_public_500.csv"
    note_path = labels_dir / "agent_notes_labels_public_500.csv"
    missing = [str(path) for path in (transcript_path, note_path) if not path.exists()]
    if missing:
        raise FileNotFoundError("missing public label files: " + ", ".join(missing))
    # IDs as text (they join to the database), and keep the label value "none" as written
    read = dict(dtype=str, keep_default_na=False, na_values=[""])
    transcripts, notes = pd.read_csv(transcript_path, **read), pd.read_csv(note_path, **read)
    if con is None:
        con = connect(read_only=True)
    transcripts = _attach_redacted_text(transcripts, "transcript", con)
    notes = _attach_redacted_text(notes, "note", con)
    results = evaluate_frame(transcripts, "transcript") + evaluate_frame(notes, "note")
    timing = time_to_hardship(transcripts, con=con)
    lines = ["# Layer 3 public-label evaluation", "",
             "Public labels are used only for evaluation. Scores below are produced by the configured extractor: "
             f"**{extractor_mode()}**.", "",
             "Where a label uses a different scale from the answer, both are put on one scale first: sentiment is "
             "compared by tone (negative, neutral, positive) and promise intent as firm or not firm. 'none' counts "
             "as an answer for choice questions that offer it.", "",
             "| Source | Question | N | Accuracy | Macro F1 | Brier |",
             "|---|---|---:|---:|---:|---:|"]
    for row in results:
        lines.append(f"| {row['source']} | {row['question']} | {row['n']} | {row['accuracy']:.3f} | "
                     f"{row['macro_f1']:.3f} | {row['brier']:.3f} |")
    excluded = sum(r.get("excluded", 0) for r in results)
    if excluded:
        lines.extend(["", f"{excluded} rows labelled `disability` are left out of `hardship_reason`: it is a protected "
                          "ground (POL-COLL-004 §4.6) and the extractor is not asked for it."])
    lines.extend(["", "## Time to flag hardship", ""])
    if timing:
        mean, median = timing['mean_delay_sec'], timing['median_delay_sec']
        lines.append(f"Flagged {timing['flagged']} of {timing['labelled']} labelled hardship calls "
                     f"({timing['misses']} missed); delay after the customer first mentions hardship: mean "
                     f"{'n/a' if mean is None else f'{float(mean):.1f}'} s, median "
                     f"{'n/a' if median is None else f'{float(median):.1f}'} s.")
    else:
        lines.append("Not measured: the label file has no transcript ID or hardship-first-mention column.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # machine-readable copy: the feature registry reads measured accuracy from here
    output.with_suffix(".json").write_text(json.dumps({"results": results, "time_to_flag_hardship": timing,
                                                       "extractor": extractor_mode()},
                                                      indent=2, default=str), encoding="utf-8")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels-dir", default=str(DEFAULT_LABELS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    print(evaluate(args.labels_dir, args.output))


if __name__ == "__main__":
    main()
