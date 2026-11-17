"""Benchmark runner: every question through ask(), answers written in the template format (never edited by hand).

    uv run python -m layer2.benchmark.run                       # all questions -> outputs/benchmark_answers.csv
    uv run python -m layer2.benchmark.run --ids BQ-003 BQ-016   # a subset, printed only
    # 4 Oct 19:00, extra questions in their own file: released + extra -> one CSV covering every question
    uv run python -m layer2.benchmark.run --questions data/raw/maple_collections_release/data/benchmark_questions.csv path/to/extra.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

from layer2.adapters import REPO
from layer2.ask import ask

RELEASE = REPO / "data" / "raw" / "maple_collections_release"
QUESTIONS = RELEASE / "data" / "benchmark_questions.csv"
TEMPLATE = RELEASE / "labels" / "benchmark_answers_template.csv"
OUTPUT = REPO / "outputs" / "benchmark_answers.csv"
RUN_LOG = REPO / "lake" / "layer2_benchmark_run.jsonl"


def load_questions(path: Path = QUESTIONS) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run(questions: list[dict], use_model: bool = True, verbose: bool = True) -> list[dict]:
    rows = []
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("w") as log:
        for q in questions:
            out = ask(q["question_text"], role="analyst", as_of=q.get("as_of_date") or None, use_model=use_model)
            rows.append({"question_id": q["question_id"], "answer": out["answer"],
                         "sql_or_sources": out["sql_or_sources"], "refused": "true" if out["refused"] else "false"})
            log.write(json.dumps({"question_id": q["question_id"], **{k: v for k, v in out.items() if k != "table"}},
                                 default=str) + "\n")
            if verbose:
                first = out["answer"].splitlines()[0][:90] if out["answer"] else ""
                print(f"{q['question_id']} {out['route']:<12} {out['seconds']:>5}s  {first}", flush=True)
    return rows


def write(rows: list[dict], path: Path = OUTPUT) -> None:
    with TEMPLATE.open(newline="") as f:
        columns = next(csv.reader(f))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=Path, nargs="+", default=[QUESTIONS],
                    help="one or more question CSVs (released + extra); duplicates by question_id keep the last")
    ap.add_argument("--ids", nargs="*", help="only these question IDs (printed, not written)")
    ap.add_argument("--no-model", action="store_true", help="rules only: no Laya, no LLM")
    ap.add_argument("--out", type=Path, default=OUTPUT)
    args = ap.parse_args(argv)
    by_id = {}
    for path in args.questions:
        for q in load_questions(path):
            q.setdefault("as_of_date", "")
            by_id[q["question_id"]] = q
    questions = list(by_id.values())
    if args.ids:
        questions = [q for q in questions if q["question_id"] in set(args.ids)]
    start = time.time()
    rows = run(questions, use_model=not args.no_model)
    if not args.ids:
        write(rows, args.out)
        shown = args.out.relative_to(REPO) if args.out.is_relative_to(REPO) else args.out
        print(f"wrote {len(rows)} answers to {shown} in {time.time() - start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
