# Benchmark submission

The file to submit is **`submission/benchmark_answers.csv`**. It has one row per benchmark question in the format of
`labels/benchmark_answers_template.csv`: `question_id, answer, sql_or_sources, refused`.

| File | What it is |
|---|---|
| `benchmark_answers.csv` | The answers to submit: 35 released + 15 extra questions, produced by running the system |
| `benchmark_questions_extra.csv` | The 15 extra questions released at 19:00 IST on 4 Oct (BQ-101 to BQ-115) |
| `run_benchmark.sh` | Regenerates `benchmark_answers.csv` by running Ask Collections on every question, then checks the format |
| `check.py` | Format check: template columns, every released and extra question in order, `refused` true/false, no empty field |

## Regenerate and check

```bash
uv run python -m layer2.build                 # once, after Layer 1 (Layer 2's views)
bash submission/run_benchmark.sh              # with the model (Groq through shared/llm.py; cached answers are reused)
bash submission/run_benchmark.sh --no-model   # rules only, no API calls
uv run python -m submission.check             # format check only
```

Close the Streamlit app first: DuckDB allows one writer. Answers are never edited by hand (team rule 7): to change
one, fix the system and run it again.

## How the answers were checked

- **Dev split** (questions with gold answers): 21/21 pass, by `uv run python -m layer2.benchmark.eval_dev --from-csv
  submission/benchmark_answers.csv`.
- **Extra questions** (no gold answers released): each answer was checked against the data and the bank documents.
  Examples: BQ-108, 12.05% of SMS payment links sent in September clicked; BQ-110, 34,872 open hardship cases;
  BQ-111, average days to cure filtered on the cure date; BQ-112, filtered on the promise due date; BQ-115, 6,581
  cases on insolvency hold.
- **Refusals:** 9, each with the reason (personal data export, protected grounds, out of scope).
