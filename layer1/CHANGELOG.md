# Layer 1 change log

Newest first. "Affects layer N" marks changes another layer should look at.

## 2026-10-04: lineage paths use forward slashes on Windows (Anish)
- `load_bronze.py` writes `_source_file` for Parquet history with `Path.as_posix()`, so lineage is the same on every
  laptop and `test_bronze.py::test_values_kept_as_received` passes on Windows (lead's request). Linux and macOS
  output is unchanged; no rebuild is needed.

## 2026-10-04: Customer 360 page for the one agent role (Het, commit `9052411`)
- **Search by case ID or golden ID.** A golden ID shows a case picker over that customer's cases.
- **Case brief** from `access_agent.case_brief`: masked phone and email, no DOB. Debt details stay hidden until the
  agent presses "I have verified the customer's identity", which is logged as `identity_verified`.
- **Customer header and tabs** (Linked records, Accounts, Cases, Lineage) read the analyst-safe `access_analyst.*`
  views. Linked records shows counts per source and status, with no source keys. The page no longer shows the
  survivorship JSON, name, DOB, FSA or the full crosswalk.
- **Tests:** `layer1/tests/test_customer_360_page.py` rewritten for the one role.
- **Affects layers 2 and 4:** the app now has one agent role; the `access_*` views still limit what each page shows.

## 2026-10-04: note from the lead (Madhur)
- **Merged to `main`:** #1-#5, #7 and #10-#12 are reviewed and merged. From 3 Oct 23:50 there are no more pull
  requests: push directly to `main` after `git pull --rebase` and the Layer 1 tests (see `CLAUDE.md`).
- **Customer 360 page, two small follow-ups:**
  - `c360_viewed` is logged on every rerun; log it once per record per session.
  - An agent can open any case; filter on `assigned_agent_id` like the pre-call brief does.
- **Layer 4 follows your `case_brief` note:** the pre-call brief reads `access_agent.case_brief`, limits agents to their
  own cases, and holds debt details back until identity is verified when `verify_before_disclosure` is true.
- **Please fix:** `layer1/tests/test_bronze.py::test_values_kept_as_received` fails on Windows. `_source_file` is
  written with backslashes; write it with `Path.as_posix()`.

## 2026-10-03: build instructions for every teammate
- "Build Layer 1 first" section at the top of the root `README.md` and of `layer1/README.md`: one-time setup, the
  build command (`uv run python -m layer1.build --target dev|full`), the check (`--verify-only`), how to point code
  and the app at a database, and fixes for the problems seen so far. **Affects layers 2, 3 and 4:** every teammate
  builds and checks Layer 1 on their own laptop before starting (also sent as a GitHub issue).

## 2026-10-03: Layer 1 section added to the root README (L1-9)
- Root `README.md`: the Layer 1 section (results, how to run, how it works, what changed from the design, AI used),
  the one-command build in Quick start, and an updated status row. **Affects layer 4 (lead):** shared file, in PR #10
  for review.

## 2026-10-03: Customer 360 page (L1-8)
- `app/pages/1_Customer_360.py`: search a golden ID or case ID; identity confidence and the verify gate for every
  role; agents see the masked case brief and no debt until they confirm identity (logged); team leads see linked
  source keys, survivorship and lineage with contact details masked; analysts see only `access_analyst` data.
  **Affects layer 4:** the agent identity check is logged as `identity_verified` in `audit.events`, which Agent Assist
  can read for the same case.
- Note: keep the database file names (`dev.duckdb`, `maple.duckdb`); the dbt views name their catalog after the file.
  **Affects all layers.** Decision D-45.

## 2026-10-03: sensitivity tags and C360 mappings approved by Anish
- All 1,417 tags and 51 mappings in `layer1/config/` approved by Anish (Layer 1 owner) on 2026-10-03. The guard in
  `layer1/tests/test_governance.py` now enforces the approved proxies. **Affects layers 2 and 3:** the 20
  "Proxy (review)" columns (language, benefit deposits, home branch, urban/rural, military service, ...) must not be
  used as features, rule inputs or grouping in answers; the tests fail if one reaches a feature view or `layer3`.

## 2026-10-03: one-command build and README section (L1-1, L1-9)
- `uv run python -m layer1.build --target dev` (about 2 min) or `--target full` (about 16 min) builds Layer 1 and
  checks every interface table; `--verify-only` just checks. **Affects layers 2, 3 and 4:** use this to get a working
  database; the dev sample now includes every reference table (metric definitions, policies, hardship programmes,
  QA checklist, agents, model scores).
- `docs/INTERFACES.md` Layer 1 section updated (new snapshot table, access views, recomputed buckets, vulnerability
  removed). **Affects layers 2, 3 and 4.**
- `layer1/docs/README_section.md`: Layer 1 section for the root README (for Madhur). Decision D-43.

## 2026-10-03: data contract and data quality report (L1-6, L1-7)
- `deliverables/data_contract_c360.yaml`: contract for `gold.c360_customer`, generated from the build; 14 quality
  rules tied to their dbt tests. **Affects all layers:** allowed and not-allowed uses and the access views per role
  are written there.
- `deliverables/data_quality_report.md`: 30 data issues with live counts and handling, 26 of them not in
  `ops_events`. **Affects layer 4 (submission):** both files are on the submission checklist.
- Decision D-42.

## 2026-10-03: full Dagster run and dbt docs (L1-5, parts 3 and 4)
- One Dagster materialization builds everything on full data in about 16 minutes; golden IDs unchanged. Fixed on the
  way: dbt profile path under Dagster (`DHANDHO_LAKE`), steps now run one at a time (DuckDB single writer), DQ checks
  skip tables absent from a database. **Affects all layers:** add your assets to `orchestration/definitions.py`;
  they will run serially after Layer 1's.
- dbt docs: descriptions for all 459 gold columns (`python -m layer1.governance.dbt_docs`, then
  `dbt docs generate` / `dbt docs serve`). **Affects layers 2 and 3:** column meanings are in the docs site.
- Decision D-41.

## 2026-10-03: sensitivity tags and mappings proposed (L1-5, part 2)
- `layer1/config/sensitivity/tags_v1.yml` (1,417 columns: 74 Direct PII, 21 quasi-identifiers, 21 protected, 2 AML,
  20 proxies for review) and `layer1/config/mappings/mappings_v1.yml` (51 C360 columns), proposed by rules and
  waiting for a person's approval. Decision D-40. **Affects layers 2 and 3:** treat "Proxy (review)" columns
  (language, benefit deposits, home branch, urban/rural, ...) as off-limits for features and answers about groups;
  the tests fail if an approved proxy reaches a feature view or the `layer3` schema.

## 2026-10-03: role-based access views (L1-5, part 1)
- New schemas `access_analyst` (customers, accounts, account_monthly_snapshot, cases, contact_timeline,
  promises_to_pay, incident_effects, customer_features), `access_agent` (case_brief) and `access_engineer`
  (quarantine). Masking and contents in decision D-39; dbt tests guard both. **Affects layer 2:** query
  `access_analyst.*` instead of `gold.*` (no names, phones, email, DOB, postal code or FSA). **Affects layer 4:** the
  agent app reads `access_agent.case_brief`, filters on `assigned_agent_id`, and hides debt details while
  `verify_before_disclosure` is true.

## 2026-10-03: benchmark data traps fixed in gold (L1-4)
- `gold.c360_accounts` gains `dpd_missing`, `dpd_bucket_recomputed`, `dpd_bucket_mismatch`; `gold.cases` gains
  `current_bucket_recomputed`, `current_bucket_mismatch`. Source columns unchanged. **Affects layers 2 and 3:** use the
  recomputed bucket for bucket questions; about 8% of accounts have no dpd.
- New `gold.account_monthly_snapshot`: quarter-end history with `month_end_date` parsed from four formats (both slash
  orders occur), `month_end_date_format`, the dpd columns and `golden_customer_id`. **Affects layer 2:** the BQ-017
  reference SQL drops the MM/DD/YYYY rows; filter `month_end_date_format <> 'MM/DD/YYYY'` to reproduce it.
- DQ report: bucket checks exclude missing dpd (3.7% mismatch, as the trap notes say); missing dpd is its own check.
- Decision D-38. Already done before this: `channel_resolved` (BQ-011), INC-0914 effects (BQ-006).

## 2026-10-03: vulnerability out of gold, language out of the feature view (L1-3)
- All vulnerability fields moved from gold to `restricted.protected_attributes`: `vulnerability_flag` (was on
  `gold.c360_customer` and `gold.l3_customer_features`), `vulnerable_customer_flag` (was on `gold.cases`) and
  `vulnerability_disclosed_flag` (was on `gold.contact_timeline`). `hardship_flag` stays. **Affects layers 2, 3 and 4:**
  queries selecting these columns from gold now fail; vulnerability must not be a feature, rule input or answer.
- `preferred_language` removed from `gold.l3_customer_features` (possible proxy); still on `gold.c360_customer` for
  serving the customer. **Affects layer 3.**
- The `no_protected_columns_in_gold` test covers both; it now runs after the gold models. Built on sample and full
  data: 30 dbt checks pass. Decision D-37.

## 2026-10-03: repo restructure (from the lead)
- Layer 1's code moved into `layer1/`: `layer1/pipeline`, `layer1/identity`, `layer1/quality`,
  `layer1/review_app`, `layer1/tests`, `layer1/docs` (architecture, decisions, fairness audit, hand-off tasks). Run
  modules as `python -m layer1.<package>...`. The dbt project is renamed `dhandho`; its model folders, schemas and
  commands are unchanged. Working rules are now `layer1/CLAUDE.md`. Behaviour is unchanged: 126 tests pass, dbt parses
  22 models and 42 tests. **Affects all layers** (module paths).

## 2026-10-03: note from the lead
- The "no external AI APIs" decision in the entry below is **superseded**: AI APIs are allowed in every layer
  through `shared/llm.py` (root `CLAUDE.md`, rule 1). No raw PII to any model still holds. Layer 1's working rules
  moved to `layer1/CLAUDE.md`; remaining Layer 1 deliverables are in `layer1/BRIEF.md`.
  **Affects all layers.**

## 2026-10-03: redaction run on full data (Layer 1 complete)
- 760,594 notes and 388,701 transcript turns redacted; the spot check finds no phone, email, postal code, CRM ID or
  long card/account number left. **Affects layers 2 and 3:** read text only from `silver.agent_notes_redacted` and
  `silver.transcript_turns_redacted`; never from `bronze.agent_notes.note_text` or the transcript JSON files.

## 2026-10-03: tuning, review workflow, fairness audit, approvals, no AI APIs, Layer 2 hand-off
- **No external AI APIs in the project** (Layer 1 owner's decision): review explanations and DQ alerts are now
  rule-based. **Affects all layers:** do not add AI API calls; see CLAUDE.md rule 9.
- Approvals recorded: REL-0926 channel fix and INC-0914 promise adjustment. **Affects layer 2:**
  `gold.contact_timeline.channel_resolved` is now complete (it uses `channel_v2` where `channel` is null).
- Threshold sweep (`identity.tune`): bands kept at 0.95 / 0.70; precision is effectively 100% on clean truth pairs.
- Review workflow: batch export/import (`identity.review_io`) and an end-to-end test of decisions changing golden IDs.
- Fairness audit (`layer1/docs/fairness_audit.md`): no protected group differs by more than 0.5 points; customers with no
  DOB almost all wait in review. **Affects layer 4:** those customers are low confidence, so verify before disclosure.
- `gold.dq_history` moved to `quality.dq_history` (pipeline state, not a data product).
- **Affects layer 2:** five tasks handed over in `layer1/docs/handoff_tasks.md` (mappings and tags,
  role-based views, full Dagster run, dbt docs, retrieval layer).

## 2026-10-03: checked against the data dictionary workbook
- Decisions D-31: the dictionary confirms CIF zero-padding, the payment-history encoding and that `transactions` is a
  13-month deposit sample. **Affects layers 2 and 3:** `transactions` cannot show every payment (card payments are
  in `card_statements`); `payment_history_24m` starts with last month, not today.
- **Affects layers 2 and 3:** card, loan and deposit tables are documented as one row per account per snapshot date,
  but this release has one row per account; `gold.c360_accounts` holds one row per account.

## 2026-10-03: identity, gold and DQ run on full data
- Identity resolution on all 3.49M keys: 1,060,086 golden IDs; precision 99.8% and recall 97.7-98.9% per source;
  no joint holder or co-borrower merged. **Affects all layers:** use `golden_customer_id`, not `crm_customer_id`,
  to count or join customers.
- Gold built on full data; every data-contract test passes. **Affects layers 2, 3 and 4:** gold tables are ready
  (see README for the list).
- `gold.dq_report`: 84 checks, 9 warnings, 0 blocking. The `channel` rename (REL-0926) and the mismatched CRM IDs
  (2.8% of collections rows) are flagged. **Affects layer 2:** `dpd_bucket` disagrees with `dpd` on 11.6% of
  accounts (cycle-based bucket); say which one an answer uses.
- Fixed: look-alike rule wrongly blocked records with first and last names swapped (loan recall 89.8% -> 98.3%).
- Clustering rewritten to use arrays (the first full run nearly ran out of memory).

## 2026-10-03: identity resolution, gold, quality, review app and redaction written (not yet run)
Code is in place and unit-tested (121 tests), but has not run on data yet: Splink, Presidio and Streamlit are
still installing on a slow connection.
- `layer1/identity/`: deterministic rules D1/D2, Splink model, constraints and decision bands, clustering with stable
  golden IDs, crosswalk with history, review queue, identity confidence, match-quality evaluation.
- `dbt/models/gold/`: `c360_customer` (survivorship), `c360_accounts`, `cases` (with case-link alarm),
  `contact_timeline`, `promises_to_pay`, `incident_effects`, and the Layer 3 view `l3_customer_features`.
  `restricted/` holds protected attributes and the fairness audit. Data-contract rules are dbt tests.
- `layer1/quality/checks.py`: the six families of checks in `gold.dq_report`.
- `layer1/review_app/app.py`: Streamlit review queue; AI explanations see comparison results only.
- `layer1/pipeline/redact.py`: Presidio redaction of agent notes and transcript turns.
- **Affects layer 2:** `gold.contact_timeline.channel_resolved` uses `channel` as received until an engineer
  approves the REL-0926 fix; after approval it is `coalesce(channel_v2, channel)`.
- **Affects layer 3:** read features from `gold.l3_customer_features` only; it has no names, phones, email, DOB or
  protected attributes.
- **Affects layer 4:** `verify_before_disclosure` and `incident_affected` are on `gold.c360_customer`.
- Fixed: D2 deterministic pairs used the wrong key order and would never have joined their Splink scores.

## 2026-10-03: team notes folder
- Added `common/` with a folder per layer, and these Layer 1 notes.

## 2026-10-03: bronze and silver run on full data (commit 176ca7d)
- **Bronze** (`layer1/pipeline/ingest/load_bronze.py`): all 31 tables loaded into `lake/maple.duckdb` in 36 s. CSV tables
  are copied to Parquet with every column kept as text; the six history tables are read in place. Row counts and
  SHA-256 checksums match the release. **Affects all layers:** `bronze.<table>` is the shared way to read raw data.
- **Silver** (`dbt/models/silver/`): per-source parsers for names, DOBs, phones and postal codes, and
  `silver.identity_input` with 3,487,885 customer keys. Values that fail a parser go to `silver.quarantine`.
- **Dev sample** (`layer1/pipeline/ingest/sample.py`): `lake/dev.duckdb` with about 50K customers, chosen by postal area so
  a customer's records from different systems stay together. Handy for fast development in any layer.
- **Data dictionary** exported to `docs/data_dictionary/data_dictionary_{tables,columns,relationships}.csv` (1,417 columns, not the
  1,421 quoted earlier).
- **Findings** added to `layer1/docs/decisions.md` (D-01 to D-23): the noisy snapshot `crm_customer_id`, mismatched case
  references, and card and loan postal areas missing from `geo_reference`. **Affects layers 2 and 3** (see README).
- Fixed: the full dbt build failed when DuckDB spilled to disk; DuckDB options are now set when connecting.

## 2026-10-03: profiling, design updates and first code (commit a7ac2f6)
- Profiled the five sources that feed identity matching and wrote `layer1/docs/decisions.md`.
- Updated `layer1/docs/architecture.md` where the data contradicted it:
  - deposit CIFs need zero-padding;
  - card names come in three layouts;
  - 39% of card phones are not masked;
  - CRM has no record history (one version per ID);
  - CRM `dob_raw` is ambiguous;
  - joint-holder and co-borrower references rarely resolve;
  - collections cases also list deposit accounts.
- Set up the project: `pyproject.toml` (Python 3.11 through `uv`), `.gitignore` (raw data and `lake/` never go to
  git), the dbt project and tests.
- Compute check: no GPU needed; a 16 GB laptop with about 5 GB free RAM runs the whole pipeline.
