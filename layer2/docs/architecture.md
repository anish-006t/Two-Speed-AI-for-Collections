# Layer 2 architecture: Ask Collections (owner: Het)

Status: design, 2026-10-03. Supersedes `L2_CIBC.pdf` (Postgres / Qdrant / Tree-sitter / Next.js design). Code follows
this file; record deviations in `layer2/CHANGELOG.md`.

## 1. Purpose and how it is scored

- Plain-English question → answer over the C360, with the SQL or sources shown, out-of-scope questions refused.
- Benchmark CSV (35 now + ~10 at 19:00 on 4 Oct) is scored automatically: numbers within `tolerance`; policy answers
  need key facts **and** the cited document; refusals need `refused = true`; answerable questions must not be refused.
  Judges also ask unseen questions live. Mix today: ~17 metric, 9 policy, 3 call summary, 6 refuse.
- Output contract (`docs/INTERFACES.md`): `ask(question, role="analyst", as_of=None) -> {answer, sql_or_sources,
  refused, reason, route, table}`; `answer_policy(question)` for Layer 4.

## 2. Design rules

1. **AI chooses, code writes.** No model writes certified SQL. A decision model (Laya) fills a typed query plan from
   menus (metric, dimensions, filter values); a deterministic compiler turns the plan into SQL.
2. **Traps are fixed once, in data, not in prompts.** Layer 2 safe views encode every known trap, so every
   question, including unseen ones, inherits the fix.
3. **Layer 2 adapts to other layers, never the reverse.** No edits outside `layer2/`. Every dependency on another
   layer goes through an adapter that uses the other layer's version when it exists and a Layer 2 fallback when it
   doesn't (§10).
4. **Time is anchored to the snapshot.** Relative periods resolve against `as_of` (question's `as_of_date`, default
   2026-09-28), never the system clock.
5. **Every answer carries its receipt:** SQL or sources, what was understood, and data caveats.
6. **Deterministic reruns.** All model calls cached by input hash, temperature 0.

## 3. Flow

```
ask(question, role, as_of)
 │
 ├─ 0 PARSE        regex: case/customer/PTP/doc IDs, amounts; period parser anchored to as_of
 ├─ 1 GUARD        rules (deny lists) → Laya choice (category) → refuse with reason + alternatives
 ├─ 2 ROUTE        rules first → Laya choice over {metric_sql, policy, call_summary, lookup, refuse}
 │
 ├─ metric_sql   3a PLAN     Laya choices: metric → dimensions → filter values (menus from semantic catalog)
 │               3b COMPILE  plan → SQL over layer2 safe views (Python, no AI)
 │               3c GUARD    sqlglot: SELECT only, allow-listed views/columns, LIMIT; read-only connection
 │               3d RUN      DuckDB; sanity checks (rates 0–1, counts ≤ population, empty result)
 │               3e FORMAT   unit-aware (pct vs ratio), CSV / key: value, "understood as", caveats
 ├─ policy       4  BM25 over section chunks, current versions only, precedence rank → LLM answer with
 │                  DOC §section citations → code checks citations; Laya yes/no groundedness check
 │                  (structured-first: hardship eligibility answered from bronze.hardship_programs)
 ├─ call_summary 5  SQL: case → most recent contact WITH a transcript → redacted turns → LLM fields →
 │                  one-line gold-style summary; cites transcript ID
 ├─ lookup       6  entity cards (case, customer) from templates; else uncertified NL2SQL over safe views
 │                  (LLM writes SQL → same guard → one retry on error), labelled "not a certified metric"
 │
 └─ 7 RESPOND      dict per contract + audit log + cache
```

## 4. Files

| Path | Does |
|---|---|
| `layer2/ask.py` | `ask()`, `answer_policy()`; orchestrates the flow |
| `layer2/parse.py` | ID regexes, amounts, period parser (`as_of`-anchored: "last 90 days", "this week", "Q3", "September") |
| `layer2/guard.py` | refusal rules + Laya category; refusal messages with alternatives |
| `layer2/router.py` | rule routes + Laya route choice; returns probabilities |
| `layer2/semantic/catalog.yaml` | metrics, dimensions, filter menus, caveats (§6) |
| `layer2/semantic/planner.py` | fills `QueryPlan` with Laya choices (LLM structured fallback) |
| `layer2/decide_laya.py` | loads Laya once, runs choice / yes-no questions, caches results |
| `layer2/semantic/compiler.py` | `QueryPlan` → DuckDB SQL |
| `layer2/sql_guard.py` | sqlglot validation (allow lists, SELECT only, LIMIT) |
| `layer2/policy.py` | index build + retrieval + answer + citation check |
| `layer2/call_summary.py` | case → transcript → summary |
| `layer2/lookup.py` | entity cards + uncertified fallback |
| `layer2/format.py` | answer text per benchmark format; caveats |
| `layer2/adapters.py` | db, llm, decide, audit, layer3 extract (§10) |
| `layer2/build.py` | builds schema `layer2` from `layer2/sql/*.sql` and the policy index; run after Layer 1 |
| `layer2/sql/*.sql` | safe views (§7) |
| `layer2/benchmark/run.py`, `eval_dev.py` | runner and dev scorer (§9) |
| `app/pages/2_Ask_Collections.py` | page: question, editable plan, answer, SQL, sources, refusal reason |

**Why not `dbt/models/layer2/`:** Layer 1's documented step `dbt build --exclude silver` would build them inside
the gold step, before redaction exists, and a Layer 2 failure would break every teammate's Layer 1 build. `layer2.build`
runs as its own step after Layer 1 (`uv run python -m layer2.build --db ...`). It can be wrapped as a Dagster asset later.

## 5. Typed decisions: measured, then narrowed

Laya (`pip install laya`, `convaiinnovations/laya`, Apache-2.0) replaced Jev (free, local, no key). It was then
**measured** on Layer 2's own decisions (`benchmark/laya_check.py` -> `outputs/layer2_laya_check.md`, multilingual
checkpoint, CPU): refuse-or-answer 21/34, lane 13/29, metric 13/22, wrong answers at up to 0.98-1.00 confidence (it
refused "arrears rate for active loan accounts" at 0.98 and let "rank customers by disability status" through), ~1.3 s
per call, 2.4 GB RSS. Refining call-summary reasons with it dropped exact sentences from 90% to 67%.

Decision: confidence that does not track correctness cannot be thresholded, so **Laya is used only for the policy
coverage check** ("does this passage answer the question?", yes/no), where it scored every in-scope question >= 0.66
and out-of-scope ones near 0. Everything else is rules first; where rules are silent, the LLM (structured output
limited to the menu) if a key is configured, otherwise the rules' best candidate. Rules alone score 21/21 on the dev
questions, 34/34 on our own questions and 88% exact call summaries, so no decision depends on a model being present.
Laya stays outside the team lock (CPU PyTorch, optional): `uv pip install torch --index-url
https://download.pytorch.org/whl/cpu && uv pip install laya`.

### LLM provider and guardrails (3 Oct, night)

The LLM runs through OpenRouter (free Qwen3.8 27B, backups Nemotron 3 Super and Gemma 4 31B) or Anthropic, behind
`llm_guard.py`: question wrapped as data, injection refused, schema-checked JSON, no personal data out, numbers in
policy answers grounded in the cited sections, summary facts checked against the call, model SQL through the SQL
guard, a daily call budget and a circuit breaker. Where the rules are proven (router, refusals on collections
questions, call summaries) the model is not consulted; it handles what the rules cannot: written policy answers,
menu-only planning for unrecognised metric questions, and guarded uncertified SQL.

## 6. Semantic catalog (`catalog.yaml`)

Generated once from `bronze.metric_definitions` (id, name, business_definition, formula, grain, time_basis,
default_filters, unit, good_direction, synonyms, source_tables, certified_flag), then each metric gets hand-written SQL
checked against the dev answers:

```yaml
- id: <metric_id>
  view: layer2.f_promises          # one wide fact view per grain
  expr: "count(*) FILTER (WHERE status_final = 'broken')::DOUBLE / nullif(count(*), 0)"
  unit: pct                        # pct → "23.4%"; ratio → "0.234" (from metric_definitions.unit)
  time_column: due_date
  default_filters: ["status_final NOT IN ('open', 'cancelled')"]
  dimensions: [prior_broken_bucket, product, queue, channel, test_cell, bucket]
  caveats: [promise_status_note]
  definition_text: <verbatim formula, shown in the answer>
```

Dimensions and filters are columns on the metric's view, so compiling is `SELECT dims, expr FROM view WHERE
default_filters AND plan_filters AND period GROUP BY dims ORDER BY dims`. Filter menus are distinct values cached at
build time (from `bronze.code_lookups` and the views).

## 7. Safe views (schema `layer2`, built by `layer2.build`)

Only these, the reference tables (`metric_definitions`, `hardship_programs`, `reference_documents`, `ops_events`,
`code_lookups`, `qa_checklist`) and `layer2.policy_chunks` are queryable. Types are cast (bronze is all text); no names,
phones, emails, DOBs, postal codes, phone hashes, free text or protected attributes. Customers are counted with
`golden_customer_id`.

| View | Grain | Built from | Traps handled |
|---|---|---|---|
| `f_promises` | promise | `gold.promises_to_pay`, `gold.cases` | final status from `ptp_status_adjusted`; open/cancelled flagged; prior broken count bucketed; INC-0914 flag |
| `f_contacts` | contact | `gold.contact_timeline` | `channel_resolved`; local hour/day; RPC; incident tag |
| `f_cases` | case | `gold.cases` | bucket recomputed from DPD; `customer_ref_unresolved` excluded from customer counts; `randomised` flag for strategy comparisons |
| `f_accounts` | account | `gold.c360_accounts` | `dpd_missing` flag; bucket recomputed |
| `f_account_month` | account × month | `bronze.account_monthly_snapshot` | `month_end_date` parsed from mixed formats; bucket from DPD; next-month bucket for roll rates; golden ID via account (snapshot CRM ID is noisy) |
| `f_customers` | customer | `gold.c360_customer` | only non-PII columns (confidence, flags, product counts, exposure) |
| `transcript_index` | transcript | `bronze.call_transcripts`, `gold.contact_timeline` | case → transcripts with start time, for "most recent recorded call" |
| `policy_chunks` | doc section | `files/docs/` + `bronze.reference_documents` | version, effective date, superseded flag, doc type rank, language |

**Adaptive build:** each view checks whether Layer 1's fix exists (for example a recomputed bucket column on
`gold.cases`, or `access_analyst.*`) and uses it; otherwise it computes the fix itself. When Layer 1 ships, the next
`layer2.build` switches over with no code change.

## 8. Guard, policy and call-summary details

**Refusal categories** (rules first, high precision; Jev for the rest):
- protected attributes and synonyms: gender/sex, marital, citizenship/immigration/newcomer, accessibility/disability,
  vulnerability type, accent/ethnicity/religion, age/age band, household/dependants, AML fields;
- proxy targeting: treatment decided by FSA/postal area/neighbourhood/language/branch ("FSAs to stop offering hardship");
- bulk PII export: list/export/download + names/phones/emails/addresses/DOB;
- off topic; outside the role (analyst asking for one customer's contact details);
- undefined KPI (decided after planning).
Each refusal returns `refused = true`, the category, a one-line reason and 1–2 answerable alternatives.
`hardship_flag` questions are answerable (protective use). Guard tests run both ways on the dev set.

**Policy:** chunks = markdown sections (`§` ids) with doc metadata. Retrieval: BM25 (DuckDB `fts`) top 8 → drop
superseded unless the question asks about an old version → rank by precedence policy > procedure > hardship terms >
FAQ > script, then by `effective_date` → Laya rerank → LLM answers only from the given sections and returns
`{answer, citations: [doc_id, section]}` → code rejects citations not in the given chunks (one retry). If a lower-rank
document disagrees, the answer says which one overrides it (FAQ 6 months vs POL-COLL-004 v4.2 3 months at 50%).
English doc preferred for English questions; French when asked in French.

**Call summary:** case ID → latest contact that has a transcript (most contacts have none) → redacted turns ordered by
turn index → LLM structured fields (reason, hardship, promise amount and date, dispute, next step) → one line in the
gold style → cites `transcript_id` and `case_id`. Uses Layer 3 `extract()` for the fields when it exists. Agent role:
show the `verify_before_disclosure` banner for medium or low identity confidence.

**Auto caveats** appended to answers when they apply: question period overlaps an `ops_events` window (INC-0914 dispute
and broken-promise spike); the metric uses DPD buckets (11.6% source disagreement, this answer uses DPD); uncertified
metric; low planner confidence.

## 9. Benchmark harness

- `run.py`: reads `benchmark_questions.csv`, calls `ask(q, as_of=as_of_date)`, writes `outputs/benchmark_answers.csv`
  in the template's exact columns (`question_id, answer, sql_or_sources, refused`); never edited by hand.
- `eval_dev.py`: scores dev questions against `labels/benchmark_dev_answers.csv` within `tolerance` (parses %, pts,
  CAD, counts), refusal in both directions, policy key facts plus cited doc; writes `outputs/benchmark_dev_report.md`
  per route.
- Generalisation set `layer2/tests/questions_own.yaml`: our own paraphrases per metric, dimension and refusal type
  (never the dev answers), to prepare for unseen and live questions.
- Dev answers are used only to score and tune; never as prompt examples and never copied into outputs.

## 10. Adapters (`layer2/adapters.py`)

| Need | Uses when present | Fallback until then |
|---|---|---|
| DB | `shared.db.connect(read_only=True)` | `duckdb.connect($DHANDHO_DB, read_only=True)` with 4 GB limit |
| LLM | `shared.llm.complete` | Anthropic SDK with a disk cache in `lake/layer2_cache/` (temporary; removed once shared lands) |
| Typed decisions | `shared.decide.decide` | local Laya (`layer2/decide_laya.py`), then LLM structured output |
| Audit | `shared.audit.log` | append to `layer2.ask_log` |
| Text fields for summaries | `layer3.text.extract.extract` | Layer 2's own structured prompt |
| Analyst columns | `access_analyst.*` | Layer 2 column allow list over `gold.*` |

## 11. Changes from the earlier designs

| Earlier | Now | Why |
|---|---|---|
| PostgreSQL C360 | DuckDB `lake/maple.duckdb`, read-only | the C360 already lives there; no copy of 12.5M+ rows; Layer 1 macros work |
| Tree-sitter AST check | sqlglot (DuckDB dialect) | resolves stars, aliases and CTEs to real columns |
| Qdrant | DuckDB full-text search (BM25); vectors optional for customer stories | 19 short policy docs; one store; no service to run |
| Next.js + FastAPI | Streamlit page + `ask()` | team app is Streamlit; runner calls `ask()` directly |
| 2 routes (numbers / text) | 5 routes | policy (9 questions) had no corpus; call summaries need lookup, not search |
| LLM writes SQL from definitions (brief L2-4) | Laya fills a plan, code compiles | wrong SQL becomes a visible, editable wrong menu choice |
| Keyword moderation gate | rule + Laya categories, tested both ways | catches proxies and bulk export, avoids refusing hardship questions |
| RAG over raw notes and transcripts | redacted silver tables only | raw text holds PII |
| `dbt/models/layer2/` | `layer2/sql/` + `layer2.build` | keeps Layer 1's build untouched (§4) |

## 12. Build order (deadlines from `layer2/BRIEF.md`)

1. Dataset downloaded to `data/maple_data/` (3 Oct); build Layer 1 locally with its own commands (`lake/maple.duckdb`, sample `lake/dev.duckdb`).
2. `adapters.py`, `ask()` stub, `benchmark/run.py`, `eval_dev.py` — Sat 19:00.
3. `parse.py`, `guard.py`, `router.py`; all refusals right — Sat 22:00.
4. `layer2/sql/*` + `build.py`, `catalog.yaml`, planner, compiler, sql_guard; dev metrics within tolerance — Sun 09:00.
5. `policy.py` (index + answer), `call_summary.py` — Sun 09:00.
6. `lookup.py` fallback, caveats, generalisation set — Sun 11:00.
7. Ask Collections page with editable plan — Sun 13:00.
8. Customer story retrieval (BM25 over redacted notes, filtered by golden ID) — Sun 15:00, cut first.
9. Final run on extra questions, commit CSV — Sun 20:45.

## 13. What the data showed (implementation, 3 Oct)

- Dev questions reproduce the organisers' reference SQL from the safe views row by row (19 / 21 on rules alone; the 2
  call summaries need Layer 1's redacted transcripts).
- `month_end_date`: four shapes, including month-first (418,319 rows). Certified parsing follows the reference SQL
  (ISO, day-first, compact); `month_end_lenient` keeps the rest and the roll-rate answer notes the difference.
- Vulnerable-certified pilot: cases enter `mid_stage` after 30 days, so its 30-day cure rate counts from assignment.
- The policy corpus is ~2,250 words: the LLM sees every current section; citations are checked in code; the no-LLM
  fallback is BM25 with "best of the top three by precedence" and an incident-report pool for "why did" questions.
- Gold summary sentences follow a fixed part order (reason, dispute, promise, complaint, insolvency, stop contact,
  next step), so code renders them from typed facts; both dev summaries render word for word from their facts.

- Call summaries: the 500 public labels follow a fixed next-step rule (stop contact > complaint or agent transfer >
  dispute > clear hardship > promise > call back) and the agent always reads the promise back, so keyword rules reach
  88% exact sentences; models only refine. Laya is installed separately (CPU PyTorch) to keep 3 GB of GPU wheels out
  of the team lock.

## 14. Open items (confirm on data)

Exact 15 metrics and their formulas; which column marks randomised cases (`collections_cases.assignment_method` or
`case_assignment_history.assignment_method = randomised_pilot`); `month_end_date` formats; doc section markup;
answer template examples; Laya CPU latency and RAM; whether `bronze.metric_definitions` units are pct or ratio per metric.
