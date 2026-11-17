"""Evaluate the live replay on the public labels: time-to-flag hardship and promise capture.

    uv run python -m layer4.eval_replay [--limit N]     # writes outputs/layer4_replay_eval.md

For every labelled call with hardship (possible or clear), the delay is (time the R-HARDSHIP prompt fired) minus
hardship_first_mention_sec. Calls without hardship count false alarms. Promise capture compares the replay's amount
and date with label_ptp_amount and label_ptp_date. Which extractor ran (model or rules) is in the report.
"""
import argparse
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from layer1.pipeline import config
from layer4 import live_replay as lr
from shared import db

OUT = config.ROOT / "outputs" / "layer4_replay_eval.md"


def evaluate(limit: int | None = None, con=None, hardship_only: bool = False) -> dict:
    con = con or db.connect()
    path = (config.RAW / "labels" / "call_transcripts_labels_public_500.csv").as_posix()
    rows = con.execute(f"""SELECT transcript_id, label_hardship, hardship_first_mention_sec, label_ptp_mentioned,
                                  label_ptp_amount, label_ptp_date
                           FROM read_csv_auto('{path}', all_varchar=true)
                           {"WHERE label_hardship IN ('possible', 'clear')" if hardship_only else ''}
                           ORDER BY transcript_id
                           {'LIMIT ' + str(int(limit)) if limit else ''}""").fetchall()
    delays, missed, false_alarms, no_hardship = [], 0, 0, 0
    promise = Counter()
    versions = Counter()
    for tid, hardship, first_sec, ptp, amount, due in rows:
        r = lr.replay(tid, con=con)
        for s in r["steps"]:
            for p in s["prompts"]:
                versions[p["model_version"]] += 1
        flagged = lr.time_to_flag(r)
        if hardship in ("possible", "clear"):
            if flagged is None:
                missed += 1
            else:
                delays.append(flagged - float(first_sec))
        else:
            no_hardship += 1
            false_alarms += flagged is not None
        if ptp == "true":
            got = [s["promise"] for s in r["steps"] if s["promise"]]
            promise["labelled"] += 1
            if got:
                last = got[-1]
                promise["heard"] += 1
                promise["amount_right"] += bool(amount and last.get("amount") and
                                                abs(float(last["amount"]) - float(amount)) < 0.5)
                promise["date_right"] += bool(due and last.get("due_date") == due[:10])
                promise["scored"] += last.get("status") in ("risky", "ok")
    hardship_calls = len(delays) + missed
    return {"calls": len(rows), "hardship_calls": hardship_calls, "flagged": len(delays), "missed": missed,
            "median_delay": statistics.median(delays) if delays else None,
            "within_0s": sum(d <= 0.01 for d in delays), "within_10s": sum(d <= 10 for d in delays),
            "early": sum(d < -0.01 for d in delays), "no_hardship_calls": no_hardship, "false_alarms": false_alarms,
            "promise": dict(promise), "extractor": dict(versions)}


def report(m: dict) -> str:
    pct = lambda a, b: f"{a / b:.0%}" if b else "n/a"  # noqa: E731
    p = m["promise"]
    lines = [
        "# Layer 4 live replay: evaluation on the public labels",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} by `python -m layer4.eval_replay` on {m['calls']} labelled calls.",
        f"Extractor answers seen (model version: prompts): {m['extractor'] or 'none fired'}.",
        "",
        "## Time to flag hardship",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Calls with hardship (possible or clear) | {m['hardship_calls']} |",
        f"| Flagged during the call | {m['flagged']} ({pct(m['flagged'], m['hardship_calls'])}) |",
        f"| Flagged on the turn it was first said | {m['within_0s']} ({pct(m['within_0s'], m['hardship_calls'])}) |",
        f"| Flagged within 10 seconds | {m['within_10s']} ({pct(m['within_10s'], m['hardship_calls'])}) |",
        f"| Median delay (flagged calls) | {m['median_delay'] if m['median_delay'] is None else round(m['median_delay'], 1)} s |",
        f"| Flagged before the labelled first mention | {m['early']} |",
        f"| Missed | {m['missed']} |",
        f"| False alarms on calls without hardship | {m['false_alarms']} of {m['no_hardship_calls']} "
        f"({pct(m['false_alarms'], m['no_hardship_calls'])}) |",
        "",
        "## Promise capture",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Calls with a labelled promise | {p.get('labelled', 0)} |",
        f"| Promise heard | {p.get('heard', 0)} ({pct(p.get('heard', 0), p.get('labelled', 0))}) |",
        f"| Amount matches the label | {p.get('amount_right', 0)} ({pct(p.get('amount_right', 0), p.get('labelled', 0))}) |",
        f"| Date matches the label | {p.get('date_right', 0)} ({pct(p.get('date_right', 0), p.get('labelled', 0))}) |",
        f"| Scored by the promise-break model | {p.get('scored', 0)} |",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--hardship-only", action="store_true", help="only labelled calls with hardship")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    m = evaluate(args.limit, hardship_only=args.hardship_only)
    Path(args.out).write_text(report(m), encoding="utf-8")
    print(report(m))


if __name__ == "__main__":
    main()
