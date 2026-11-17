#!/usr/bin/env bash
# Regenerate submission/benchmark_answers.csv by running the system on every benchmark question:
# the 35 released (data/raw/maple_collections_release/data/benchmark_questions.csv) and the 15 extra released at 19:00
# on 4 Oct (submission/benchmark_questions_extra.csv). Answers are never edited by hand.
#
#   bash submission/run_benchmark.sh              # with the model (LLM settings from .env; cached answers are reused)
#   bash submission/run_benchmark.sh --no-model   # rules only, no API calls
#
# Needs the built database (lake/maple.duckdb) and Layer 2's views: uv run python -m layer2.build
# Close the Streamlit app first (DuckDB allows one writer).
set -euo pipefail
cd "$(dirname "$0")/.."

RELEASED=data/raw/maple_collections_release/data/benchmark_questions.csv
EXTRA=submission/benchmark_questions_extra.csv
OUT=submission/benchmark_answers.csv

uv run python -m layer2.benchmark.run --questions "$RELEASED" "$EXTRA" --out "$OUT" "$@"
uv run python -m submission.check "$OUT"
