"""Measure call summaries on the 500 public call labels (labels/call_transcripts_labels_public_500.csv).

Labels are used only to evaluate and tune, never as examples. Reports the exact-sentence match with
`label_summary_gold` and the accuracy of each typed fact, per engine.

    uv run python -m layer2.benchmark.eval_summaries                 # keyword rules (no model)
    uv run python -m layer2.benchmark.eval_summaries --engine laya   # rules + Laya refinement
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date

from layer2.adapters import REPO
from layer2.call_summary import fields_rules, local_redact, refine_with_laya, render

RELEASE = REPO / "data" / "raw" / "maple_collections_release"
LABELS = RELEASE / "labels" / "call_transcripts_labels_public_500.csv"
REPORT = REPO / "outputs" / "layer2_summary_eval.md"


def gold_fields(r: dict) -> dict:
    return {"reason": r["label_hardship_reason"] or "none", "hardship": r["label_hardship"],
            "dispute": r["label_dispute_type"] or "none", "complaint": r["label_complaint"] == "true",
            "insolvency": r["label_insolvency_mention"] == "true", "cease_request": r["label_cease_request"] == "true",
            "promise_amount": float(r["label_ptp_amount"]) if r["label_ptp_amount"] else None,
            "promise_date": r["label_ptp_date"] or None, "next_step": r["label_next_step"]}


def load_paths() -> dict:
    with (RELEASE / "data" / "call_transcripts.csv").open(newline="", encoding="utf-8") as f:
        return {r["transcript_id"]: (r["file_path"], r["call_start_ts"]) for r in csv.DictReader(f)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["rules", "laya"], default="rules")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    paths = load_paths()
    with LABELS.open(newline="", encoding="utf-8") as f:
        labels = list(csv.DictReader(f))
    if args.limit:
        labels = labels[: args.limit]
    keys = ["reason", "hardship", "dispute", "complaint", "insolvency", "cease_request", "promise_amount",
            "promise_date", "next_step"]
    right = {k: 0 for k in keys}
    exact, n, misses = 0, 0, []
    for r in labels:
        fp, start = paths.get(r["transcript_id"], (None, None))
        if not fp:
            continue
        doc = json.loads((RELEASE / "files" / fp).read_text(encoding="utf-8"))
        turns = [(t.get("speaker") or "", local_redact(t.get("text") or ""))
                 for t in sorted(doc.get("turns", []), key=lambda t: t.get("turn_index") or 0)]
        day = date.fromisoformat(start[:10]) if start else None
        ours = fields_rules(turns, day)
        if args.engine == "laya":
            ours = refine_with_laya(turns, ours)
        gold = gold_fields(r)
        n += 1
        for k in keys:
            a, b = ours.get(k), gold.get(k)
            if k == "promise_amount":
                ok = (a is None and b is None) or (a is not None and b is not None and round(a) == round(b))
            else:
                ok = a == b
            right[k] += ok
        text = render(ours)
        if text == r["label_summary_gold"]:
            exact += 1
        elif len(misses) < 12:
            misses.append((r["transcript_id"], r["label_summary_gold"], text))
    lines = [f"# Call-summary evaluation ({args.engine}, {n} labelled calls)", "",
             f"**Exact sentence match: {exact} / {n} ({100 * exact / max(n, 1):.1f}%)**", "",
             "| Fact | Accuracy |", "|---|---|"]
    lines += [f"| {k} | {100 * right[k] / max(n, 1):.1f}% |" for k in keys]
    lines += ["", "| Transcript | Gold | Ours |", "|---|---|---|"]
    lines += [f"| {t} | {g} | {o} |" for t, g, o in misses]
    text = "\n".join(lines) + "\n"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
