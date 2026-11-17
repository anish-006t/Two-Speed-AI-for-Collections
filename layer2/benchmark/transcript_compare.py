"""Call transcripts: regex rules vs rules + AI fallback (the production hybrid), on two test sets.

    uv run python -m layer2.benchmark.transcript_compare --max-calls 20

1. The 500 public labels (the rules were tuned on them, so they favour the rules): rules only, plus how many calls
   the hybrid would send to the AI. Text read exactly as production reads it (load_turns).
2. Fresh set (benchmark/transcripts_fresh_labels.yaml): calls the rules were never tuned on, labelled by a separate
   agent that never saw Layer 2's code. Rules vs hybrid on every field and on "all fields right"; a paired bootstrap
   gives a 95% interval for the difference. The hybrid calls the AI only where the rules found nothing (capped by
   --max-calls to protect the daily free limit; skipped calls count as rules-only and are reported).
3. Calibration: 10 calls in the fresh file also have official labels (the labeller was not told which); their
   agreement shows how far the fresh labels can be trusted.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from collections import Counter
from pathlib import Path

import yaml

from layer2 import adapters
from layer2.adapters import REPO
from layer2.benchmark.eval_summaries import LABELS, gold_fields
from layer2.call_summary import fields_hybrid, fields_rules, load_turns, rules_found_nothing

FRESH = Path(__file__).parent / "transcripts_fresh_labels.yaml"
REPORT = REPO / "outputs" / "layer2_transcript_compare.md"
KEYS = ["reason", "hardship", "dispute", "complaint", "insolvency", "cease_request", "promise_amount", "promise_date",
        "next_step"]


def norm(k, v):
    if k == "promise_amount":
        return None if v in (None, "", "null") else round(float(v))
    if k == "promise_date":
        return None if v in (None, "", "null") else str(v)[:10]
    if k in ("complaint", "insolvency", "cease_request"):
        return str(v).lower() == "true"
    return (str(v) if v not in (None, "") else "none").lower()


def same(a: dict, g: dict, k: str) -> bool:
    return norm(k, a.get(k)) == norm(k, g.get(k))


def call_day(con, tid):
    from datetime import date
    row = con.execute("SELECT call_start_ts FROM layer2.transcript_index WHERE transcript_id = ?", [tid]).fetchone()
    return date.fromisoformat(str(row[0])[:10]) if row and row[0] else None


def evaluate(items, method, con, max_calls: int, state: dict):
    """items: [(tid, gold)] -> per-item dict of field correctness + all-right flag."""
    out = []
    for tid, gold in items:
        turns, _ = load_turns(con, tid)
        if not turns:
            continue
        day = call_day(con, tid)
        t0 = time.time()
        if method == "rules":
            f = fields_rules(turns, day)
        else:
            use_ai = rules_found_nothing(fields_rules(turns, day))
            if use_ai and state["calls"] >= max_calls:
                state["skipped"] += 1
                use_ai = False
            before = adapters._budget_left()
            f, engine = fields_hybrid(turns, day, use_model=use_ai)
            state["calls"] += max(0, before - adapters._budget_left())
            state["engines"][engine] += 1
        state["secs"].append(time.time() - t0)
        right = {k: same(f, gold, k) for k in KEYS}
        out.append({"tid": tid, "right": right, "all": all(right.values()), "ours": f, "gold": gold})
    return out


def pct(rows, k=None):
    if not rows:
        return 0.0
    return 100 * sum((r["all"] if k is None else r["right"][k]) for r in rows) / len(rows)


def bootstrap(a_rows, b_rows, n=2000, seed=1):
    by = {r["tid"]: r["all"] for r in b_rows}
    pairs = [(r["all"], by[r["tid"]]) for r in a_rows if r["tid"] in by]
    rng = random.Random(seed)
    diffs = []
    for _ in range(n):
        s = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        diffs.append(100 * (sum(b for _, b in s) - sum(a for a, _ in s)) / len(s))
    diffs.sort()
    return diffs[int(0.025 * n)], diffs[int(0.975 * n)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=20)
    ap.add_argument("--fresh", type=Path, default=FRESH)
    args = ap.parse_args(argv)
    public = {r["transcript_id"]: gold_fields(r) for r in csv.DictReader(LABELS.open(newline="", encoding="utf-8"))}
    fresh_all = yaml.safe_load(args.fresh.read_text())
    calib = [(str(x["transcript_id"]), x) for x in fresh_all if str(x["transcript_id"]) in public]
    fresh = [(str(x["transcript_id"]), x) for x in fresh_all if str(x["transcript_id"]) not in public]
    con = adapters.connect(read_only=True)
    lines = ["# Call transcripts: regex rules vs rules + AI fallback", ""]
    try:
        # 1. public 500, rules only
        st = {"calls": 0, "skipped": 0, "secs": [], "engines": Counter()}
        pub = evaluate(list(public.items()), "rules", con, 0, st)
        would = 0
        for tid, _ in public.items():
            turns, _ = load_turns(con, tid)
            if turns and rules_found_nothing(fields_rules(turns, call_day(con, tid))):
                would += 1
        lines += [f"## 1. Public 500 labels (rules were tuned on these; favours the rules)", "",
                  f"Rules: all fields right {pct(pub):.1f}% of {len(pub)} calls. The hybrid would ask the AI on {would} "
                  f"calls (rules found nothing); not run, to keep the daily free AI limit.", "",
                  "| Field | Rules |", "|---|---|"] + [f"| {k} | {pct(pub, k):.1f}% |" for k in KEYS]
        # 2. fresh set
        st_r = {"calls": 0, "skipped": 0, "secs": [], "engines": Counter()}
        st_h = {"calls": 0, "skipped": 0, "secs": [], "engines": Counter()}
        fr_r = evaluate(fresh, "rules", con, 0, st_r)
        fr_h = evaluate(fresh, "hybrid", con, args.max_calls, st_h)
        lo, hi = bootstrap(fr_r, fr_h)
        lines += ["", f"## 2. Fresh calls the rules never saw ({len(fr_r)} calls, labelled independently)", "",
                  "| Field | Rules | Rules + AI fallback |", "|---|---|---|",
                  f"| **all fields right** | **{pct(fr_r):.1f}%** | **{pct(fr_h):.1f}%** |"]
        lines += [f"| {k} | {pct(fr_r, k):.1f}% | {pct(fr_h, k):.1f}% |" for k in KEYS]
        avg = lambda xs: 1000 * sum(xs) / max(len(xs), 1)  # noqa: E731
        lines += ["", f"Hybrid minus rules, all fields right: {pct(fr_h) - pct(fr_r):+.1f} points, 95% interval "
                      f"[{lo:+.1f}, {hi:+.1f}] (paired bootstrap).",
                  f"AI calls: {st_h['calls']} (skipped by the cap: {st_h['skipped']}); engines used: "
                  f"{dict(st_h['engines'])}. Time per call: rules {avg(st_r['secs']):.0f} ms, hybrid "
                  f"{avg(st_h['secs']):.0f} ms on average."]
        # next_step convention: when the agent offers both a payment link and hardship help, the official labels
        # mostly say hardship_plan_review, the fresh labeller always said send_payment_link. Report without that noise.
        import re
        amb = set()
        for tid, _ in fresh:
            turns, _ = load_turns(con, tid)
            agent = " ".join(t for sp, t in (turns or []) if not sp.lower().startswith(("cust", "client"))).lower()
            if re.search(r"link|lien", agent) and re.search(r"hardship|difficult|programme|program", agent):
                amb.add(tid)
        eight = lambda rows: 100 * sum(all(v for k, v in r["right"].items() if k != "next_step") for r in rows) / len(rows)  # noqa: E731
        clean = lambda rows: [r for r in rows if r["tid"] not in amb]  # noqa: E731
        lines += ["", "Without the next-step convention difference (see section 3):", "",
                  "| Measure | Rules | Rules + AI fallback |", "|---|---|---|",
                  f"| 8 fields (all but next step) right, {len(fr_r)} calls | {eight(fr_r):.1f}% | {eight(fr_h):.1f}% |",
                  f"| next step, {len(clean(fr_r))} calls without link+hardship ambiguity | "
                  f"{pct(clean(fr_r), 'next_step'):.1f}% | {pct(clean(fr_h), 'next_step'):.1f}% |",
                  f"| all 9 fields right, same calls | {pct(clean(fr_r)):.1f}% | {pct(clean(fr_h)):.1f}% |"]
        errs = Counter()
        for r in fr_r:
            for k in KEYS:
                if not r["right"][k]:
                    errs[(k, str(norm(k, r["gold"].get(k))), str(norm(k, r["ours"].get(k))))] += 1
        lines += ["", "Rules' mistakes on the fresh set (field: label -> rules), most frequent first:", ""]
        lines += [f"- {k}: {g} -> {o} (x{n})" for (k, g, o), n in errs.most_common(15)] or ["- none"]
        # 3. calibration
        agree = {k: 0 for k in KEYS}
        for tid, lab in calib:
            for k in KEYS:
                agree[k] += same(lab, public[tid], k)
        n = max(len(calib), 1)
        all_agree = sum(all(same(lab, public[tid], k) for k in KEYS) for tid, lab in calib)
        lines += ["", f"## 3. How reliable are the fresh labels? ({len(calib)} hidden calls with official labels)", "",
                  f"The independent labeller matched the official labels on all fields for {all_agree} / {len(calib)} "
                  "calls. Per field: " + ", ".join(f"{k} {100 * agree[k] / n:.0f}%" for k in KEYS) + ".",
                  "Where the labeller disagrees with official labels, the fresh-set numbers carry that much noise.",
                  "Next-step convention: in calls where the agent offers both a payment link and hardship help, the "
                  "official labels mostly choose hardship_plan_review; the fresh labeller always chose "
                  "send_payment_link. Those calls are excluded from the adjusted next-step numbers above."]
    finally:
        con.close()
    REPORT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
