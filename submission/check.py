"""Check submission/benchmark_answers.csv against the submission format (release README, "Benchmark questions"):
the template's columns, one row per released and extra question in order, refused = true/false, no empty fields.

    uv run python -m submission.check [submission/benchmark_answers.csv]
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "data" / "raw" / "maple_collections_release"
TEMPLATE = RELEASE / "labels" / "benchmark_answers_template.csv"
QUESTIONS = [RELEASE / "data" / "benchmark_questions.csv", ROOT / "submission" / "benchmark_questions_extra.csv"]


def check(path: Path) -> list[str]:
    problems = []
    with TEMPLATE.open(newline="", encoding="utf-8") as f:
        columns = next(csv.reader(f))
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header, rows = reader.fieldnames, list(reader)
    if header != columns:
        problems.append(f"columns are {header}, the template has {columns}")
    wanted = []
    for q in QUESTIONS:
        with q.open(newline="", encoding="utf-8") as f:
            wanted += [r["question_id"] for r in csv.DictReader(f)]
    got = [r["question_id"] for r in rows]
    if got != wanted:
        missing, extra = sorted(set(wanted) - set(got)), sorted(set(got) - set(wanted))
        problems.append(f"question IDs differ from released + extra (missing {missing}, unexpected {extra})")
    for r in rows:
        if r.get("refused") not in ("true", "false"):
            problems.append(f"{r['question_id']}: refused must be true or false, not {r.get('refused')!r}")
        for col in ("answer", "sql_or_sources"):
            if not (r.get(col) or "").strip():
                problems.append(f"{r['question_id']}: empty {col}")
    return problems


def main(argv=None) -> int:
    path = Path((argv or sys.argv[1:] or ["submission/benchmark_answers.csv"])[0])
    problems = check(path)
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if problems:
        print(f"{path}: {len(problems)} problem(s)")
        for p in problems:
            print("  -", p)
        return 1
    print(f"{path}: OK. {len(rows)} answers in the template format, "
          f"{sum(r['refused'] == 'true' for r in rows)} refused.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
