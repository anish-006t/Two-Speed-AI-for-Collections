# layer2/: Insight and NLP (owner: Het)

Plain-English questions in, answers with their SQL or sources out. Design: `docs/architecture.md`.

Status (3 Oct, late night): dev benchmark 21 / 21 with the LLM on and with rules only; 35 answers, exactly 6
refusals; call summaries 96.2% exact on the 500 public labels; 165 tests. Questions outside the 15 certified metrics
get a rule-built plain aggregate (average / median / total / share / count) before any model is asked. LLM: OpenRouter (`OPENROUTER_API_KEY` in `.env`),
behind guardrails (`llm_guard.py`).
Without an API key or Laya everything still answers; with them, policy answers are written (not quoted) and
unusual questions get an uncertified, guarded SQL answer.

```bash
uv run python -m layer2.build                    # safe views into schema layer2 (after Layer 1; ~25 s)
uv run python -m layer2.benchmark.eval_summaries # call summaries vs the 500 public labels
uv run python -m layer2.benchmark.laya_check     # Laya speed / memory / agreement (why it is used only for coverage)
uv run streamlit run app/pages/2_Ask_Collections.py
uv run streamlit run layer2/checker_app.py       # query checker: paste questions, see which can be processed
uv run python -m layer2.benchmark.holdout --no-model   # hidden holdout (written without seeing the rules)
uv run python -m layer2.benchmark.typos          # typo robustness: misspelt vs clean answers (L2_NORMALIZE=0 to compare)
uv run python -m layer2.vectors --download       # once: the notes embedding model (~470 MB, outside the repo)
uv run python -m layer2.benchmark.notes_eval --vectors   # notes retrieval: right note type in the top 3
# Optional Laya (CPU): uv pip install torch --index-url https://download.pytorch.org/whl/cpu && uv pip install laya
uv run python -m layer2.benchmark.run            # outputs/benchmark_answers.csv
# 4 Oct 19:00: bash layer2/benchmark/final_run.sh <extra.csv>   (see "Final run" below)
uv run python -m layer2.benchmark.eval_dev       # outputs/benchmark_dev_report.md
# Without Layer 1: uv run python -m layer2.devdb, then add --db lake/l2dev.duckdb / DHANDHO_DB=lake/l2dev.duckdb
```

| File | What it does |
|---|---|
| `ask.py` | `ask(question, role, as_of)` and `answer_policy(question)`: the entry points (shape in `docs/INTERFACES.md`) |
| `parse.py` | IDs and time periods, resolved against the snapshot date (28 Sep 2026), never the clock |
| `guard.py` | refusals: protected attributes, proxy targeting, bulk personal data, off-topic, outside the role |
| `router.py` | lanes: metric_sql, policy, call_summary, lookup (rules first, then a typed decision) |
| `semantic/` | `catalog.yaml` (15 certified metrics + plain aggregates, dimensions, filters) and the plan -> SQL compiler; `generic.py`: rule-built plain aggregates for other questions |
| `sql_guard.py` | every query: one SELECT, safe views only, no personal or protected columns, row limit |
| `policy.py` | answers from current documents with checked citations; superseded versions only when asked |
| `call_summary.py` | most recent recorded call of a case -> typed facts -> one gold-style sentence |
| `lookup.py` | case and promise facts; uncertified fallback (LLM SQL behind the guard, labelled) |
| `story.py` | a case's or customer's redacted notes ranked for the question (keywords, note types, French), cited by note ID; for asking questions a cited AI answer (RAG) |
| `vectors.py` | e5-small embeddings and the notes vector store (`lake/layer2_vectors.duckdb`); loaded only when a notes question needs it |
| `adapters.py` | the only door to shared/, other layers, the LLM (OpenRouter or Anthropic) and Laya; budget, breaker |
| `llm_guard.py` | guardrails on every LLM input and output (injection, personal data, grounding, summary facts) |
| `decide_laya.py` | local Laya typed decisions; measured, now used only for the policy coverage check |
| `build.py`, `sql/` | schema `layer2`: safe views with the data traps fixed once |
| `devdb.py` | Layer 2 dev database straight from the release files |
| `semantic/coverage.py` | does the rule plan cover the whole question? lists what it leaves out (thresholds, top N, changes between periods, distinct counts, per-X ratios, unused data words) |
| `freeform.py` | AI-written SQL for what rules cannot express: extends the rule SQL or writes it, behind the SQL guard |
| `normalize.py` | fixes clear typing errors before answering (rules; real words, IDs and names untouched); `ask.py` adds one AI rewrite only for soft refusals |
| `present.py` | how an answer is shown: headline, "understood as" chips, data products, checked follow-ups, AI insight |
| `checker.py`, `checker_app.py` | query checker: can these questions be processed, and how (rules, AI, partly, refused)? |
| `benchmark/` | runner, dev scorer, call-summary evaluation, Laya check, `final_run.sh`, `holdout.py` (hidden test sets) |
| `../app/pages/2_Ask_Collections.py` | the Ask Collections page (editable plan, SQL, sources) |

## Final run (4 Oct, 19:00; commit by 20:45)

1. Close Streamlit and notebooks; `git pull`; save the extra questions as a CSV with the released columns.
2. `bash layer2/benchmark/final_run.sh path/to/extra.csv`: tests, dev 21 / 21, every question (released + extra)
   into `outputs/benchmark_answers.csv`, then counts, refusals, anything unanswered and the new answers for a glance.
3. A wrong new answer: fix the rule, catalog or prompt (never the CSV) and run step 2 again.
4. `git add outputs/benchmark_answers.csv && git commit -m "L2: final benchmark answers"`; push; PR to `main`.

Released answers are cached, so a rerun costs no API calls; if the AI service is busy the rules still answer.

## How an unusual question is answered (never silently wrong)

1. Rules: a certified metric, else a rule-built plain aggregate (average, median, total, share, distinct count, with
   breakdowns, filters, thresholds such as "more than 5 attempts", top N and periods).
2. Coverage check (`semantic/coverage.py`): anything the rule plan leaves out (a change between periods, a per-case
   ratio, a list of records, a data word it did not use, a likely different metric) is listed.
3. With the AI (Qwen, then Nemotron, then Gemma): it extends the rule SQL to cover the list, or confirms nothing is
   missing, or says the data cannot answer it. No rule plan at all: it writes the SQL from the schema card (columns,
   real values, join keys, certified formulas). Every query passes the SQL guard; answers say "not certified".
4. Without the AI: the rule answer plus "Not applied: …", or an honest "can't answer".

Overfitting guard: the hidden holdout sets (`benchmark/holdout_v*.yaml`) are written by a separate agent that never
reads the rules. The first run on a set is the honest number; after a set is used for diagnosis, a new one is written.
