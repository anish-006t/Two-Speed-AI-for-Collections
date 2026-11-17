"""Evaluate post-call summaries against the gold summaries in the public labels.

    uv run python -m layer4.eval_summary [--limit N]     # writes outputs/layer4_summary_eval.md

Two measures, kept apart so a miss can be traced: (1) template fidelity, the gold fields run through narrative()
against the gold sentence (does the writing match when the fields are right?), and (2) end to end, draft() on the
redacted call against the gold fields and sentence (how good is extraction plus writing?).
"""
import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from layer1.pipeline import config
from layer4 import postcall
from shared import db

OUT = config.ROOT / "outputs" / "layer4_summary_eval.md"


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9$]+", (s or "").lower())


def token_f1(a: str, b: str) -> float:
    ta, tb = Counter(tokens(a)), Counter(tokens(b))
    common = sum((ta & tb).values())
    if not common:
        return 0.0
    p, r = common / sum(ta.values()), common / sum(tb.values())
    return 2 * p * r / (p + r)


def gold_fields(j: dict, row: dict) -> dict:
    ptp = j.get("ptp") or {}
    return {"hardship": j.get("hardship"), "hardship_reason": j.get("reason") if j.get("hardship") != "none" else None,
            "dispute": bool(j.get("dispute")), "dispute_type": row["label_dispute_type"],
            "complaint": row["label_complaint"] == "true", "cease_request": row["label_cease_request"] == "true",
            "insolvency_mention": row["label_insolvency_mention"] == "true",
            "promise_amount": ptp.get("amount"), "promise_date": ptp.get("date"), "next_step": j.get("next_step"),
            "sentiment": -1.0 if j.get("sentiment") == "hostile" else 0.0}


def evaluate(limit: int | None = None, con=None, whole_call: bool = False) -> dict:
    """whole_call: draft from one read of the whole call (one model request per call) instead of a turn-by-turn
    replay; the right setting for model runs on a free tier."""
    con = con or db.connect()
    path = (config.RAW / "labels" / "call_transcripts_labels_public_500.csv").as_posix()
    cur = con.execute(f"""SELECT transcript_id, summary_gold_json, label_summary_gold, label_dispute_type,
                                 label_complaint, label_cease_request, label_insolvency_mention
                          FROM read_csv_auto('{path}', all_varchar=true) ORDER BY transcript_id
                          {'LIMIT ' + str(int(limit)) if limit else ''}""")
    names = [c[0] for c in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    fidelity, exact, f1s, fields_ok, n = 0, 0, [], Counter(), len(rows)
    for row in rows:
        j = json.loads(row["summary_gold_json"].replace("NaN", "null"))
        g = gold_fields(j, row)
        gold = row["label_summary_gold"]
        fidelity += postcall.narrative({k: {"value": v} for k, v in g.items()}) == gold
        d = postcall.draft(row["transcript_id"], con=con, replay_result={"steps": []} if whole_call else None)
        v = {k: x["value"] for k, x in d["fields"].items()}
        exact += d["narrative"] == gold
        f1s.append(token_f1(d["narrative"], gold))
        fields_ok["hardship"] += v.get("hardship") == g["hardship"]
        fields_ok["dispute"] += bool(v.get("dispute")) == g["dispute"]
        fields_ok["promise_amount"] += (v.get("promise_amount") is None and g["promise_amount"] is None) or (
            v.get("promise_amount") is not None and g["promise_amount"] is not None
            and abs(float(v["promise_amount"]) - float(g["promise_amount"])) < 0.5)
        fields_ok["promise_date"] += str(v.get("promise_date") or "") == str(g["promise_date"] or "")
        fields_ok["next_step"] += v.get("next_step") == g["next_step"]
    return {"calls": n, "fidelity": fidelity, "exact": exact, "mean_f1": sum(f1s) / n if n else 0,
            "fields": dict(fields_ok), "extractor": d["model_version"] if rows else ""}


def report(m: dict) -> str:
    n = m["calls"]
    pct = lambda a: f"{a / n:.0%}" if n else "n/a"  # noqa: E731
    lines = ["# Layer 4 post-call summary: evaluation on the public labels", "",
             f"Generated {datetime.now():%Y-%m-%d %H:%M} by `python -m layer4.eval_summary` on {n} labelled calls "
             f"(model version `{m['extractor']}`).", "",
             "| Measure | Value |", "|---|---|",
             f"| Template fidelity: gold fields written by `narrative()` equal the gold sentence | {m['fidelity']} "
             f"({pct(m['fidelity'])}) |",
             f"| End to end: drafted sentence equals the gold sentence | {m['exact']} ({pct(m['exact'])}) |",
             f"| End to end: mean token F1 against the gold sentence | {m['mean_f1']:.2f} |", "",
             "| Field (end to end) | Correct |", "|---|---|"]
    lines += [f"| {k} | {v} ({pct(v)}) |" for k, v in m["fields"].items()]
    lines += ["", "Disability is written as \"a health-related hardship\" on purpose (protected ground, "
              "POL-COLL-004 §4.6), so those calls never match the gold sentence exactly.", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--whole-call", action="store_true", help="one model request per call (no per-turn replay)")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    m = evaluate(args.limit, whole_call=args.whole_call)
    Path(args.out).write_text(report(m), encoding="utf-8")
    print(report(m))


if __name__ == "__main__":
    main()
