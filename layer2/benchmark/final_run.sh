#!/usr/bin/env bash
# 4 Oct, 19:00 final run (L2-9): released + extra questions -> outputs/benchmark_answers.csv, then checks.
#   bash layer2/benchmark/final_run.sh path/to/extra_questions.csv
# Close Streamlit and notebooks first (DuckDB allows one writer). Commit the CSV by 20:45; never edit it by hand.
set -euo pipefail
cd "$(dirname "$0")/../.."
EXTRA="${1:?usage: final_run.sh path/to/extra_questions.csv}"
RELEASED=data/raw/maple_collections_release/data/benchmark_questions.csv
PY="env -u PYTHONPATH uv run --no-sync python"
export DHANDHO_DB=lake/maple.duckdb

echo "== 1. tests (rules only, no API calls)"
env -u PYTHONPATH uv run --no-sync pytest layer2/tests -q
echo "== 2. dev benchmark must stay 21/21"
$PY -m layer2.benchmark.eval_dev | tail -1
head -3 outputs/benchmark_dev_report.md
echo "== 3. every question, released + extra (cached answers are reused, so released ones cost no API calls)"
$PY -m layer2.benchmark.run --questions "$RELEASED" "$EXTRA"
echo "== 4. sanity: counts, refusals, unanswered"
$PY - "$EXTRA" <<'PY'
import csv, sys
rows = list(csv.DictReader(open("outputs/benchmark_answers.csv")))
extra = {r["question_id"] for r in csv.DictReader(open(sys.argv[1]))}
print(len(rows), "answers;", sum(r["refused"] == "true" for r in rows), "refused")
bad = [r["question_id"] for r in rows if not r["answer"].strip() or "Could not answer" in r["answer"]
       or "internal error" in r["answer"] or "AI service busy" in r["answer"]]
print("needs a look:", bad or "none")
print("\n-- new questions, for a human glance (fix rules, not answers) --")
for r in rows:
    if r["question_id"] in extra:
        print(f'{r["question_id"]} refused={r["refused"]}  {r["answer"].splitlines()[0][:110] if r["answer"] else ""}')
PY
echo "== done. If all is well: git add outputs/benchmark_answers.csv && git commit -m 'L2: final benchmark answers'"
