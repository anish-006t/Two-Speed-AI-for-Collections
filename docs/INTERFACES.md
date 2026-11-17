# Interfaces between layers

This file lists every table and function one layer gives another. Code against these shapes from the start. Until the
real thing lands on `main`, the owner keeps a stub in place that returns a fixed example in the same shape, so nobody
waits. Changing a shape needs a pull request the lead approves, plus an **Affects layer N** entry in the owner's
CHANGELOG.

Status: **ready** = on `main` and running on full data; **stub** = fake data in the agreed shape; **todo** = not yet.

## Database

One DuckDB file per laptop, `lake/maple.duckdb` (full) or `lake/dev.duckdb` (50K-customer sample). Use
`shared/db.py` (`connect(read_only=True)` for reading) so everyone uses the same path, memory limit and settings.

| Schema | Owner | Who may read it |
|---|---|---|
| `bronze`, `silver`, `identity`, `gold` | Layer 1 | Everyone. `silver` only for the redacted text tables |
| `restricted` | Layer 1 | Fairness checks only (Layer 4 governance tests). Never joined into features or answers |
| `access_agent`, `access_analyst`, `access_engineer` | Layer 1 | App and Layer 2: role-based views (analyst: no direct identifiers; agent: masked contact details, `case_brief`) |
| `layer2` | Layer 2 | Everyone |
| `layer3` | Layer 3 | Layer 4, app |
| `layer4`, `audit` | Layer 4 | Everyone |

**One agent role in the app (4 Oct).** The app has a single agent role that opens every page. The `access_*` views are still the layer that limits what data is shown: the Customer 360 page reads `access_agent.case_brief` and `access_analyst.*`. No interface shape changed.

## Layer 1 gives (owner: Anish)

| Table | Key | What it is | Status |
|---|---|---|---|
| `gold.c360_customer` | `golden_customer_id` | One row per customer: contact details, consent, restrictions, `hardship_flag`, `identity_confidence` (high, medium, low), `verify_before_disclosure`, `incident_affected`. No vulnerability fields (D-37) | ready |
| `gold.c360_accounts` | `account_id` | Every card, loan and deposit account with its golden ID, `dpd_missing`, `dpd_bucket_recomputed` | ready |
| `gold.cases` | `case_id` | Collections cases, with `case_link_alarm` and `current_bucket_recomputed` | ready |
| `gold.contact_timeline` | contact | Contact history without phone hash or SMS reply text | ready |
| `gold.promises_to_pay` | promise | Promises, with INC-0914 adjustment columns | ready |
| `gold.account_monthly_snapshot` | account, month | Quarter-end account history with `month_end_date` parsed from four formats, `dpd_missing`, `dpd_bucket_recomputed` (D-38) | ready |
| `gold.incident_effects` | customer | Who incident INC-0914 affected, and how | ready |
| `gold.l3_customer_features` | `golden_customer_id` | Feature view with no identifiers or protected fields (no vulnerability fields, no `preferred_language`, D-37) | ready |
| `gold.c360_customer_history` | `golden_customer_id`, version | Versions of each golden record | ready |
| `gold.dq_report` | run, check | Data-quality results per run | ready |
| `identity.crosswalk` | source system + source key | Any source customer key to its `golden_customer_id` | ready |
| `silver.agent_notes_redacted` | `note_id` | Notes with identifiers replaced by `<ENTITY>` tags (`note_text_redacted`); read text only from here | ready |
| `silver.transcript_turns_redacted` | transcript, turn | Transcript turns with identifiers replaced; read text only from here, never the JSON files | ready |
| `access_analyst.*` | as in gold | Gold without names, phones, email, DOB, postal code or FSA: Layer 2 reads these | ready |
| `access_agent.case_brief` | `case_id` | One row per case for the agent app: masked phone and email, no DOB, verify gate; filter on `assigned_agent_id` | ready |
| `bronze.<table>` | | Every raw table as received, including the reference tables `metric_definitions`, `reference_documents`, `hardship_programs`, `qa_checklist`, `ops_events`, `code_lookups`, `agents` | ready |

Benchmark traps that Layer 1 fixes once in gold (new columns, source values kept): `channel` renamed to `channel_v2`
on 26 Sep (`gold.contact_timeline.channel_resolved`), INC-0914 promise adjustment, DPD bucket recomputed from DPD,
quarter-end dates parsed from mixed formats, missing DPD flagged: all done (D-38). Build and check everything with
`uv run python -m layer1.build --target dev|full`.

## Layer 2 gives (owner: Het)

```python
# layer2/ask.py
def ask(question: str, role: str = "analyst") -> dict:
    """Answer one plain-English question.
    Returns {
      "answer": str,            # text; tables as small CSV or 'key: value' lines (benchmark format)
      "sql_or_sources": str,    # the SQL that ran, or document IDs and sections / transcript IDs used
      "refused": bool,
      "reason": str,            # why refused, or what was understood (metric, filters, period)
      "route": str,             # metric_sql | policy | call_summary | lookup | refuse
      "table": list[dict] | None,  # result rows for the app to render
    }"""
```

| Output | What it is | Status |
|---|---|---|
| `outputs/benchmark_answers.csv` | `question_id, answer, sql_or_sources, refused`, written only by `python -m layer2.benchmark.run` | todo |
| `layer2.semantic_metrics` (or `layer2/semantic/metrics.yaml`) | The 15 certified metrics from `bronze.metric_definitions`, with SQL templates | todo |
| `layer2/policy.py: answer_policy(question) -> dict` | Same shape as `ask()`, policy route only. Layer 4's policy chatbot reuses it | todo |

## Layer 3 gives (owner: Ayush)

```python
# layer3/text/extract.py
def extract(text: str, source_type: str) -> dict:
    """source_type: 'transcript' | 'note' | 'turn'. Runs the typed questions in layer3/text/questions.yaml.
    Returns {question_id: {"answer": str | float | bool | None, "prob": float,
                           "evidence": str, "model_version": str}}"""

# layer3/features/defs.py
def get_features(golden_customer_id: str, as_of: str) -> dict:
    """Every registered feature for one customer, using only data known at as_of (ISO date).
    The same definitions build training rows, so training and live scoring see the same values."""

def build_training_frame(events) -> "pandas.DataFrame":
    """events: rows with golden_customer_id and as_of. Returns one feature row per event (point in time)."""

# layer4/models/promise_break/score.py   (owned by Ayush)
def score_promise(golden_customer_id: str, amount: float, due_date: str, as_of: str) -> dict:
    """Returns {"risk": float 0-1, "reasons": [str, str, str],   # plain English, from SHAP
                "safer_date": str | None,                         # e.g. day after usual salary credit
                "model_version": str}"""
```

Status: `extract()`, `get_features()` and `build_training_frame()` are **ready**. `score_promise()` has a transparent
baseline and complete LightGBM training/scoring path; the trained full-data artifact and report are **todo**.

| Table | Key | What it is | Status |
|---|---|---|---|
| `layer3.text_features` | `source_type`, `source_id` | One row per transcript or note: `golden_customer_id`, `case_id`, `event_ts`, each typed answer and its probability, `model_version`. Approved post-call summaries are appended with `source_type = 'approved_summary'` | todo |
| `layer3.feature_registry` | `feature_name` | Name, entity, definition (SQL or function), source, refresh, owner, sensitivity, AI-derived flag, accuracy | todo |

## Layer 4 gives (owner: Madhur)

| Item | What it is | Status |
|---|---|---|
| `shared/llm.py: complete(prompt, model=None, system=None, json_schema=None, max_tokens=1024, tag="") -> str or dict` | The only way to call an LLM. Disk cache keyed by a hash of all inputs; logs model, tokens and latency. Usage: `shared/README.md` | ready |
| `shared/decide.py: decide(text, question) -> {"answer", "prob", "evidence", "model_version"}` | Typed decision (choice, yes/no, score, extract). Uses Jev when `JEV_API_KEY` is set (not wired yet), otherwise the LLM with structured output | ready (LLM path) |
| `shared/db.py: connect(read_only=True)` | DuckDB connection with team settings | ready |
| `shared/audit.py: log(event: dict)` | Append-only log to `audit.events`: ts, user, role, action, input ref, output, prob, approved_by | ready |
| `layer4.summaries` | Post-call summaries: draft, final, edits, approver, status | todo |
| `layer4.review_queue` | Items waiting for an agent or team lead: type, case, prob, reason, status, decided_by | todo |
| `app/Home.py` + `app/pages/` | The team app. Each layer owns its own page file | todo |
