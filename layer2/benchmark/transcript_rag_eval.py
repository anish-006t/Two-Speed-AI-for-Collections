"""Transcripts: is RAG better than the regex rules? (layer2/docs/rag_plan.md §4; an experiment, not a change)

    uv run python -m layer2.benchmark.transcript_rag_eval --dry      # rules + retrieval only, no AI call
    uv run python -m layer2.benchmark.transcript_rag_eval            # 20 AI calls

Labelled calls sampled with a fixed seed from the 500 public labels. A = call_summary.fields_rules (today).
B = RAG: embed the call's turns, retrieve the turns most similar to the summary fields' questions (top 8, kept in
call order), one LLM call extracts the same fields (call_summary.fields_llm), same sentence builder (render).
Decision rule (fixed before running): switch to B only if it beats A by >= 5 points on exact sentences with no
field worse. Caveat: the rules were tuned on these 500 labels and no other labels exist, so the test favours A;
with 20 calls, 5 points is one call.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from datetime import date

from layer2 import adapters
from layer2.adapters import REPO
from layer2.benchmark.eval_summaries import LABELS, RELEASE, gold_fields, load_paths
from layer2.call_summary import fields_llm, fields_rules, local_redact, render

KEYS = ["reason", "hardship", "dispute", "complaint", "insolvency", "cease_request", "promise_amount", "promise_date",
        "next_step"]
FIELD_QUERIES = [   # what each summary field is about, as retrieval queries
    "why the customer cannot pay: job loss, illness, reduced hours, separation, rate increase, disaster",
    "the customer promises to pay an amount on a date",
    "the customer disputes a fee or charge, complains, mentions bankruptcy or asks to stop calls",
    "the agent says what happens next: payment link, hardship review, callback, supervisor",
]
REPORT = REPO / "outputs" / "layer2_transcript_rag_eval.md"


def retrieve_turns(turns, k: int = 8):
    from layer2 import vectors
    if len(turns) <= k:
        return turns
    tv = vectors.embed([f"{s}: {t}" for s, t in turns], "passage")
    qv = vectors.embed(FIELD_QUERIES, "query")
    best = (tv @ qv.T).max(axis=1)
    keep = sorted(sorted(range(len(turns)), key=lambda i: -best[i])[:k])
    return [turns[i] for i in keep]


def _ok(k, a, b) -> bool:
    if k == "promise_amount":
        return (a is None and b is None) or (a is not None and b is not None and round(float(a)) == round(float(b)))
    return a == b


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--dry", action="store_true", help="no AI call: rules and retrieval only")
    args = ap.parse_args(argv)
    paths = load_paths()
    labels = list(csv.DictReader(LABELS.open(newline="", encoding="utf-8")))
    sample = random.Random(args.seed).sample(labels, args.n)
    score = {m: {"exact": 0, **{k: 0 for k in KEYS}} for m in ("A rules", "B rag")}
    calls, secs, failed, n = 0, [], 0, 0
    for r in sample:
        fp, start = paths.get(r["transcript_id"], (None, None))
        if not fp:
            continue
        doc = json.loads((RELEASE / "files" / fp).read_text(encoding="utf-8"))
        turns = [(t.get("speaker") or "", local_redact(t.get("text") or ""))
                 for t in sorted(doc.get("turns", []), key=lambda t: t.get("turn_index") or 0)]
        day = date.fromisoformat(start[:10]) if start else None
        gold, n = gold_fields(r), n + 1
        a = fields_rules(turns, day)
        t0 = time.time()
        picked = retrieve_turns(turns)
        b = None
        if not args.dry:
            try:
                b = fields_llm(picked, day)
                calls += 1
            except adapters.LLMUnavailable:
                failed += 1
        secs.append(time.time() - t0)
        for m, f in (("A rules", a), ("B rag", b)):
            if f is None:
                continue
            score[m]["exact"] += render(f) == r["label_summary_gold"]
            for k in KEYS:
                score[m][k] += _ok(k, f.get(k), gold.get(k))
        print(f"{r['transcript_id']}: {len(turns)} turns -> {len(picked)} retrieved"
              + ("" if b is None else f" | A {'ok' if render(a) == r['label_summary_gold'] else 'x'}"
                                       f" B {'ok' if render(b) == r['label_summary_gold'] else 'x'}"), flush=True)
    done_b = n - failed if not args.dry else 0
    pct = lambda m, k, d: 100 * score[m][k] / max(d, 1)  # noqa: E731
    a_ex, b_ex = pct("A rules", "exact", n), pct("B rag", "exact", done_b)
    worse = [k for k in KEYS if done_b and pct("B rag", k, done_b) < pct("A rules", k, n)]
    switch = bool(done_b) and b_ex >= a_ex + 5 and not worse
    lines = [f"# Transcripts: RAG vs regex rules ({n} labelled calls, seed {args.seed})", "",
             "| Field | A rules | B rag |", "|---|---|---|",
             f"| **exact sentence** | {a_ex:.1f}% | {b_ex:.1f}% |" if done_b else f"| exact sentence | {a_ex:.1f}% | (dry) |"]
    lines += [f"| {k} | {pct('A rules', k, n):.1f}% | " + (f"{pct('B rag', k, done_b):.1f}% |" if done_b else "- |")
              for k in KEYS]
    lines += ["", f"AI calls: {calls} (failed {failed}); retrieval + extraction {sum(secs) / max(len(secs), 1):.1f} s per "
              "call on average (rules: milliseconds, no call).", "",
              f"Decision (rule fixed before running: B >= A + 5 points exact, no field worse): "
              f"**{'switch to RAG' if switch else 'keep the regex rules'}**" + (f"; fields worse with RAG: {worse}" if worse else ""),
              "", "Caveat: the rules were tuned on these 500 labels, so this favours A; with this sample size one call "
              "is 5 points."]
    if not args.dry:
        REPORT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
