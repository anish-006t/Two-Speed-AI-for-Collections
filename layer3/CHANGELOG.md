# Layer 3 changelog

Newest first. Mark changes another layer must know with **Affects layer N**.

## 2026-10-04: the agent's approved summary wins over the machine reading of the same call (Anish)
- Text features take the latest known row. An approved summary is now known the day after its call (Layer 4 change),
  the same moment as the machine reading of that call. On a tie, `approved_summary` now wins, instead of whichever
  `source_id` sorted last. Registry `l3-features-5`; republish with `python -m layer3.build --extractor rules` (or
  `publish_registry`). **Affects layer 4.**

## 2026-10-04: training and live feature values are bit-identical (Anish)
- Sums and averages in `layer3/features/defs.py` are rounded: money to 2 decimals, rates and utilisation to 6.
  DuckDB adds in parallel, so an unrounded sum could differ in the last bit between two runs (11142.78 vs
  11142.779999999999). That made the claim-3 parity test fail about one run in five. Registry version
  `l3-features-4`, republished; no rebuild of the history is needed. **Affects layer 4:** feature values change only
  below the rounding (the model is unchanged).

## 2026-10-04: tests can no longer call a real LLM (Anish)
- **Root `conftest.py`:** every test in every layer runs with the LLM keys and provider settings removed and with its
  own empty cache and call log, so a developer's `.env` cannot send anything to a provider or change test results.
  Before this, with a key in `.env`, the Layer 3 claim tests sent extraction prompts for about two redacted sample
  transcripts to the configured provider. Live checks on purpose: `PYTEST_ALLOW_LIVE_LLM=1 uv run pytest ...`.
  **Affects all layers.**
- `pyproject.toml`: pytest temp files go to `lake/tmp/pytest`. Copies of the sample database filled the RAM-backed
  `/tmp`, so about 50 tests failed with "Disk quota exceeded". **Affects all layers.**
- My own OpenAI-compatible provider code was dropped in favour of the lead's version (same `.env` settings).
  `eval_labels.py` uses `config.llm_available()`.

## 2026-10-04: from the lead (Madhur): `extract()` makes one model call per text
- **Batched questions:** `extract()` calls `shared.decide.decide_many()` with all applicable questions in one call,
  instead of `decide()` 17 times. Same answer shape, the same per-answer checks, and the same per-question
  `model_version` (`llm:<model>|q:<id>@<v>`).
- **Key check:** "has a key" now means the configured provider has what it needs (`shared.config.llm_available()`),
  so Groq works too.
- **Why:** free tiers (Groq) allow far fewer requests than 17 per turn. **Affects layer 3.**

## 2026-10-04: the three Layer 3 claims made checkable (Anish)
- **Registry carries the evidence:** every AI-derived feature now has its prompt, prompt version, the extractor
  version behind the stored values, accuracy, number of labels and the extractor the accuracy was measured with;
  every feature says where it comes from (SQL, or voice = call transcripts and text = agent notes). Rules answers
  record `rules:l3-text-1|q:<id>@<version>`, the same shape as the LLM's. **Affects layer 4:** `model_version` on
  rules answers now ends with `|q:<id>@<version>`.
- **`python -m layer3.build --extractor llm`** re-extracts through the LLM once the API key is in `.env`.
- **Tests** (`test_l3_claims.py`): claims checked on the code and on real sample customers (training vs live
  feature values; stored text answers vs a fresh live extraction).
- Feature Store page shows prompt, version, extractor and accuracy per AI-derived feature. Full data rebuilt.

## 2026-10-04: Layer 3 completed on full data (Anish)
- **Label vocabularies aligned** (questions.yaml v2): `hardship_reason` and `dispute_type` options follow the labels
  (`disability` left out: protected ground); `next_step` and `stated_delay_reason` are choices over the label values;
  the four promise questions point at the real `label_ptp_*` columns. Rules updated to match. **Affects layer 4:**
  `next_step`, `dispute_type` and `stated_delay_reason` now return codes from those lists (Layer 4 already uses the
  same codes); `sentiment` stays a score from -1 to 1.
- **Evaluation fixed and run on full data:** label files read as text (IDs join to the database), "none" counts as an
  answer where it is an option, sentiment compared by tone and intent as firm/not firm. `outputs/layer3_label_eval.md`
  (+ `.json`), rules extractor: hardship 0.72 (calls) / 0.81 (notes), complaint 0.96/0.99, cease request 0.96/1.00,
  dispute 0.88/0.99; promise amount and date stay low with rules (the LLM path is for those). Hardship flagged on 158
  of 262 labelled calls.
- **Measured accuracy in the catalogue:** `layer3.feature_registry` fills `accuracy` for the text features from the
  evaluation.
- **Point-in-time fixes:** `hardship_flag` now comes from cases opened by the as-of date and `incident_affected` from
  incidents that had ended by then (both were always empty because the gold view's load date is after every as-of).
- **Full data:** `python -m layer3.build` in about 5 minutes: 14.4M account-history, 6.0M salary, 4.7M bureau and
  1.7M contact rows; **318,808 text-feature rows** (all 25,000 transcripts and the notes for open cases in the last 12
  months).
- **Promise-break model trained and committed** (`layer4/models/promise_break/model.txt`, `metadata.json`,
  `outputs/promise_model_report.md`): 198,119 resolved promises, time split, test AUC 0.775, Brier
  0.193, broken rate 96.3% in the top risk decile vs 59.7% overall; calibration
  table in the report. Only decision inputs are used: `hardship_flag` and the protective-only text answers are left
  out. **Affects layer 4:** `score_promise()` now returns the LightGBM score, plain-English reasons and a safer date
  (the day after the customer's usual salary day).
- **Feature Store page:** an aggregate summary (fill rate, median, most common value over 300 customers) beside the
  one-customer vector; catalogue shows measured accuracy and whether each feature may drive a decision.

## 2026-10-04: `main` fixed (Anish, finishing Layer 3)
- **History builds without missing gold tables:** `layer3/features/history.py` builds `layer3.salary_history`
  (golden ID through the deposit account, else the CRM key) and `layer3.bureau_history` (through the bureau key)
  from bronze in schema `layer3`, as the lead suggested; every row has `available_at` (salary: the day after the
  month closes; bureau: the day after the pull), so no feature sees data before it was known.
- **`python -m layer3.build`** now exists: history, batch text features, registry (`--skip-text`, `--limit`).
- **`layer3.text_features` has a fixed typed schema** from the question registry, created with the history, so
  lookups work before (or without) a text run; `available_at = event_ts + 1 day` is now written (the text
  features filter on it); the note window counts back from the release's last note, not today's clock.
- **`score_promise()` never raises to Layer 4:** if features can't be built it returns the baseline score with the
  reason in `reasons`. **Affects layer 4.**
- Tests: the 4 failing tests fixed with a shared history fixture; new tests for salary/bureau timing and the
  scoring fallback. Dev build of the history: 3 s.

## 2026-10-04 04:00: from the lead (Madhur): `main` is broken by 90a05f7, please fix first
- **Missing gold tables:** `layer3/features/history.py` reads `gold.salary_credit_history` and `gold.bureau_history`.
  No dbt model builds them; only `bronze.salary_credit_history` and `bronze.bureau_history` exist, and the
  `sources.yml` entries alone don't create gold tables. Either build them in your own schema from the bronze
  tables, through `identity.crosswalk`, or add dbt models under `dbt/models/layer3/`.
- **Missing command:** `python -m layer3.build`, named in the error message, isn't in the repo. Commit it, or the
  work still in your stash (`STATUS.md` mentions `stash@{0}`).
- **Your tests fail on `main`:** `test_l3_features.py` (3 tests) and `test_l3_promise_model.py` (1) fail because their
  fixtures don't create `layer3.account_history`.
- **Live scoring raises when features can't be computed.** With a trained `model.txt` present,
  `get_features()` and `score_promise()` raise ("Run python -m layer3.build first"). Layer 4 survives it (the brief
  shows "risk unavailable" and the replay shows "unscored"), but the demo has no risk scores until the history is
  built. Consider falling back to the baseline score when features fail, with the reason in `reasons`.
- **Good:** the leakage fix is kept (strict `< cutoff`, outcomes known by the cutoff), and dated account history
  replaces the old `_loaded_at` filter. **Affects layer 4.**

## 2026-10-04: note from the lead (Madhur), on label vocabularies
- **Hardship reasons:** the labels use `reduced_income, job_loss, separation, illness, rate_reset, disability,
  disaster_evacuation, ei_waiting, bereavement, strike_lockout, other`. `questions.yaml` offers `medical,
  natural_disaster, cost_of_living, family_emergency`, which never match a label. Please align the options to the
  label vocabulary, but leave out `disability`: it is a protected ground (POL-COLL-004 §4.6), and Layer 4 maps it to
  `health`.
- **Next step:** the labels use six values (`send_payment_link, hardship_plan_review, escalate_supervisor,
  dispute_investigation, stop_contact, call_back`). `next_step` is a free-text "extract" question; a "choice" over
  these six would score directly.
- **Approved post-call summaries** are now appended to `layer3.text_features` as `source_type = 'approved_summary'`,
  using your `_flatten` layout, with prob 1.0 and model_version `approved_summary`. If you rebuild the table, keep
  those rows. **Affects layer 3.**

## 2026-10-04: fixes from the lead (Madhur), please review
- **Leakage in the promise-history features (`layer3/features/defs.py`).**
  - The four promise-history features used `ptp_created_ts <= as_of`, so the promise being scored counted in its
    own history.
  - They also used final statuses of earlier promises that were not yet resolved at `as_of`.
  - Result: test AUC 0.993, with a 100% broken rate in the top decile.
  - Fix: count only promises created strictly before `as_of` whose outcome was already known (`status_ts <=
    as_of`). The two-placeholder SQL contract is kept.
  - Retrained on full data: **AUC 0.806, Brier 0.180, top-decile broken rate 0.894** (base rate 0.427).
  - Regression test: `test_promise_history_excludes_the_scored_promise_and_unknown_outcomes`.
- **`train.py`:** `fit_encoder` and `apply_encoder` crashed on DuckDB's nullable booleans (`hardship_flag`). Fixed by
  casting to `Float64` before filling.
- **Test fixtures:** they now include `status_ts`, which the real `gold.promises_to_pay` has.
- **Still open, for you:**
  - The customer-level features filter `gold.l3_customer_features` on `_loaded_at <= as_of`. `_loaded_at` is the
    build date, so these features are empty for every historical `as_of` and add nothing to the model. Either drop
    them or build them from dated history (`gold.account_monthly_snapshot`, `gold.cases` dates).
  - `model.txt`, `metadata.json` and `outputs/promise_model_report.md` are not committed. Regenerate them with
    `uv run python -m layer4.models.promise_break.train --db lake/maple.duckdb` (seconds) and commit them if
    teammates should get real scores without training. **Affects layer 4.**

## 2026-10-04

- Downloaded the main synthetic release and ran the existing Layer 1 development pipeline. Readiness checks
  passed with 52,093 C360 profiles and zero blocking quality failures. Verified the Feature Store UI and
  live/training parity against built C360 data; published ten registry rows and a 200-row rules-mode text sample.
  Updated the verification guide; the previously documented architecture gaps remain unresolved.
- Reviewed the newly added system-design PDF and documented exact-architecture gaps, verified UI behavior,
  Windows launch/data verification steps, and a reproduced future promise-outcome leakage issue in
  `ARCHITECTURE_REVIEW.md`. This is a documentation change; backend compliance is not claimed.
  **Affects layer 4:** historical promise outcome features can include outcomes unavailable at scoring time.
- Added the complete versioned typed-question registry, model-backed extraction through `shared.decide`, and an
  auditable offline fallback. `extract()` now returns every applicable registered question while preserving its
  agreed signature and answer shape. **Affects layer 4.**
- Added point-in-time feature definitions, `get_features()`, `build_training_frame()`, registry publication, and a
  batch writer for redacted transcript/note features. Training and live paths execute the same definitions.
  **Affects layer 4.**
- Added public-label evaluation (accuracy, macro F1, Brier calibration, hardship flag delay), the analyst Feature
  Store page, usage docs, and Layer 3 tests.
- Replaced the promise scoring stub with a trainable, time-split LightGBM pipeline, model-native feature
  contributions for three explanations, model report generation, and an explicit deterministic fallback for
  environments where the trained artifact has not yet been installed. **Affects layer 4.**

## 2026-10-03

- From Layer 4 (Madhur), as agreed: stubs of `extract()` in `layer3/text/extract.py` and of `score_promise()` in
  `layer4/models/promise_break/score.py`, both with the shapes in `docs/INTERFACES.md` and
  `model_version = "stub-0"`. They count as L3-1 for those two functions; `get_features()` is not stubbed (Layer 4
  reads `gold.l3_customer_features` until yours lands).
  - **What `extract()` does now:** it uses keyword rules for hardship, hardship_reason, promise_mentioned,
    promise_amount, promise_date, dispute, cease_request, insolvency_mention, third_party_present, sentiment and
    next_step.
  - **Request:** replace the function bodies freely, but keep the signatures, and keep those question IDs or tell
    Layer 4. The live replay keys its prompts on them. `layer4/tests/test_l4_stubs.py` checks the shapes.
    **Affects layer 4.**
