# Layer 4 changelog

## 2026-10-04 (evening): review threshold 0.75 (was 0.6); sentiment never queued

- **Why:** the rules extractor never scores below 0.65, so nothing ever reached the review queue, yet its answers are
  often wrong (`outputs/layer3_label_eval.md`: hardship 72% right, hardship reason 47%). Measured on 300 open-case
  calls: 0.75 sends "possible hardship" (0.72, about 11% of calls) to review; 0.7 sends nothing; above 0.75 adds
  sentiment on 86% of calls. The demo call `1010325` now queues its hardship prompt and summary field.
- `layer4/gates.py`: `CONFIRM_AT = 0.75`; `NEVER_REVIEW = {"sentiment"}` (confirm at most). `postcall.draft()` gates
  sentiment with that kind. Review reasons say the threshold from the constant. Agent Assist shows "confirm", not
  "review queue", on empty summary fields (they are never queued). Test: `test_review_threshold_and_sentiment_never_queued`.

## 2026-10-04 (from Anish): the gateway fails at once on a provider's daily limit

- `shared/llm.py`: a 429 that names a daily quota ("free-models-per-day", "per day") now raises `LLMError` at once.
  Before, it was retried for up to about 6 minutes, which froze Ask Collections and the replay whenever OpenRouter's
  free tier (50 requests a day per account, resets 05:30 IST) was used up. Short rate limits are still retried.
  Test: `test_a_daily_limit_fails_at_once_instead_of_retrying_for_minutes`. **Affects layers 2 and 3.**

## 2026-10-04 (from Anish): "Collections 360" and a dark theme

- The Customer 360 page is now titled **Collections 360** in the navigation, on its page and on Home. The file is
  still `app/pages/1_Customer_360.py`, so links and tests are unchanged. **Affects layer 1.**
- **Dark theme** in `.streamlit/config.toml` (background `#0E1117`, teal accent `#14B8A6`). Ask Collections' grey
  labels are lightened for contrast on dark. **Affects layer 2.**

## 2026-10-04 (from Anish): an edited promise reaches the next brief; no names or Deploy button in the app

- **Approved summaries count from the day after the call** (`postcall.write_back`), Layer 3's rule for every call.
  They were stamped with the real clock time of approval (4 Oct), which is after the data snapshot (28 Sep), so
  point-in-time features never saw them. An agent's corrected promise amount now feeds the next brief's risk score.
  Test: `test_an_edited_promise_reaches_the_features_from_the_day_after_the_call`.
- **Home:** the use-case cards no longer show who built each layer. The placeholder for a missing page no longer
  names an owner.
- **No Deploy button:** `.streamlit/config.toml` sets `client.toolbarMode = "viewer"`, so the top-right menu keeps
  only viewer options.

## 2026-10-04 (from Anish): Summaries tab crash fixed; summary form says it is pre-filled

- **Review and Governance → Summaries crashed** ("the JSON object must be str, bytes or bytearray, not float") as soon
  as any summary had no edits: a draft, a rejection or one approved as drafted (`edits` is NULL, NaN in pandas).
  Fixed in `app/pages/5_Review_and_Governance.py`; new test `test_summaries_tab_shows_drafts_and_rejections_without_edits`.
- **Summary step wording:** the caption now says every field is already filled in from the call and only the fields
  marked confirm or review need a check. With no promise on the call, amount and date show "No promise heard on the
  call" instead of `0.00` and an empty box, which looked like something to fill in. Behaviour is unchanged.

## 2026-10-04 (from Anish): audit check no longer reads random IDs as phone numbers

- `shared/audit.py`: inside free-text fields, keys named `id` or ending in `_id` are not scanned for contact details.
  A random UUID (`summary_id`, `item_id`) holds 10 digits in a row about 3% of the time. That rejected the audit
  event and made `test_l4_postcall.py::test_summary_tab_drafts_edits_and_approves` and
  `test_l4_review.py::test_only_review_gated_items_are_queued_and_only_once` flaky; in the app, it could block an
  approval. Phone numbers and emails in text fields are still rejected (new test). Full suite: 4 clean runs.

## 2026-10-04 (late afternoon): one agent role (owner decision)

- **`app/shell.py`:** `ROLES = {"agent": "Agent"}`; every page is open to the agent; `require_role()` is kept as a
  no-op for compatibility. Home has no Role selector, only the name for the audit log and the AI mode.
- **Agent Assist:** the agent picker has **All agents** (default), so any open case can be picked or typed; picking
  an agent ID filters the list; Demo case still selects `AG-7816`. Wording: "review queue" instead of "team lead".
- **Review and Governance:** no role block; any agent reviews any item (no four-eyes rule, owner decision); overrides
  still need a reason and every decision is audited with the reviewer's name.
- **Feature Store:** no analyst-only block. **Customer 360** (Layer 1) merged for the agent: see `layer1/CHANGELOG.md`.
- Tests: `test_l4_app_shell.py` checks the one role sees all five pages. **Affects layers 1, 2 and 3.**

## 2026-10-04 (handover)

- **Lead decision: layers have no owners.** Anyone, or any agent, may work on any path. `CLAUDE.md` (layout and rule
  5) and `docs/TEAM_PLAN.md` are updated.
- **`docs/HANDOFF.md`:** the current state, how to run, how to keep the LLM free, and the open items (submission
  first). **Affects layers 1, 2 and 3.**

## 2026-10-04 (final UI and model evaluations)

- **Final UI.**
  - **Home:** the four use cases, a step-by-step demo path and system status. Status is read from settings and
    files, so the page never calls a model.
  - **Agent Assist** is one guided flow for one case: 1 Brief, 2 Call, 3 Summary.
    - The brief lists the calls on the case, each with Replay.
    - The replay ends with "Draft the summary".
    - Approval leads back to the brief, where the summary shows under History.
    - A **Demo case** button opens `CS-2026-971262` (agent AG-7816), call `1010325`.
  - **Sidebar, "Understanding call text":** cached model answers only (the default; never calls an API), model
    (live, free plan) or offline rules.
  - Theme in `.streamlit/config.toml`. `app/README.md` has the run steps and the demo path.
- **Demo call cached with the model** (Groq gpt-oss-20b):
  - hardship flagged at **43.92 s, exactly at the labelled first mention** (the rules were 7 s late);
  - the $400 promise scored at 88% risk, with safer date 25 Sep;
  - the summary equals the gold label word for word.
  - Checked end to end in cached mode on the real database, with no API calls.
- **Model evaluation on 30 labelled calls** (`outputs/layer4_summary_eval_model.md`, one request per call):
  - exact summary 67% (rules on 500 calls: 40%);
  - token F1 0.87 (rules: 0.70);
  - fields: hardship 80%, promise amount 100%, date 97%, dispute 100%, next step 83%.
  - The per-turn replay evaluation with the model was stopped (a hung request; within free-plan limits). The
    500-call rules evaluation stands.
- **`postcall.write_back` sets `available_at`** (approval time), so approved summaries reach Layer 3's point-in-time
  text features and the next risk score. **Affects layer 3.**
- **`layer2/adapters.py`:** gateway errors now become `LLMUnavailable` (see Layer 2's CHANGELOG).
- `eval_summary --whole-call --out` and `eval_replay --hardship-only --out` run small model samples.

## 2026-10-04 (from Anish, Layer 3): repo-wide test guard; a note on reasoning models

- Root `conftest.py` strips the LLM keys and provider settings (including `GROQ_API_KEY`) and gives every test its own
  cache, so no test in any layer can call a real LLM. Pytest temp files now go to `lake/tmp/pytest`. Details are in
  `layer3/CHANGELOG.md`.
- **For the gateway (yours):** with `LLM_PROVIDER=openai_compatible` and the reasoning model `free/gpt-6-luna`
  (apinex), about a third of structured answers came back cut off at `max_tokens`. The extra reasoning room in
  `_call_openai_compatible` applies only to models named `gpt-oss`. A token floor for structured calls on every
  OpenAI-compatible model (I used 4,000) fixed it in my version.

## 2026-10-04 (Groq working, free plan)

- **Groq fixes:**
  - Cloudflare rejected Python's default user agent ("error code: 1010"); the gateway now sends its own.
  - Groq no longer serves the Llama 3.x models, so the defaults are now `openai/gpt-oss-20b` (fast) and
    `openai/gpt-oss-120b` (smart), with `reasoning_effort=low` and room left for reasoning tokens.
  - Rate-limit retries wait up to about 6 minutes in total.
- **Plan check:** the key is on Groq's free plan (1,000 requests a day and 8,000 tokens a minute per model, from the
  response headers). Limits return 429, never a charge. **Don't add a payment method.**
- **Budget:**
  - Model runs are for warming the demo calls and for small evaluation samples (about 3 replay turns a minute).
  - Batch jobs (`layer3.build`, the 500-call evaluations) stay on `L3_EXTRACTOR_MODE=rules`.
  - **Affects layers 2 and 3.**

## 2026-10-04 (model file line endings)

- **`.gitattributes` keeps `layer4/models/**/model.txt` and `metadata.json` byte-exact.** On Windows, Git's
  `core.autocrlf` checked the committed LightGBM model out with CRLF line endings, and LightGBM can't read that
  ("Model format error, expect a tree here").
  - **Affects layers 1, 2 and 3 (Windows laptops):** after pulling, delete those two files and run
    `git checkout -- layer4/models/promise_break/model.txt layer4/models/promise_break/metadata.json`.
  - With the fix and `python -m layer3.build`, the brief and the replay show real LightGBM scores (checked on a live
    case: 57% risk, three reasons, a safer date after payday).

## 2026-10-04 (free LLM provider)

- **`shared/llm.py` can call Groq, Cerebras, Ollama or any OpenAI-compatible API** as well as Anthropic. Pick one
  with `LLM_PROVIDER`.
  - Same cache and call log.
  - JSON mode, with the schema in the prompt and the answer checked against it. An answer that doesn't match raises
    `LLMError` and is not cached.
  - Calls are paced for free-tier limits and retried on 429s and 5xx errors.
  - `uv run python -m shared.llm` checks the key and lists the provider's models.
  - **Defaults:** `.env.example` now uses `LLM_PROVIDER=groq` (openai/gpt-oss-20b fast, openai/gpt-oss-120b
    smart). **Affects layers 1, 2 and 3:** set `GROQ_API_KEY` in your `.env`.
- **`shared/decide.py`:** `decide_many(text, questions)` answers several typed questions in one call, with each
  answer checked exactly like `decide()`. Layer 3's `extract()` now uses it: one call per text instead of 17.
  **Affects layer 3.**
- Layer 4 tests always run Layer 3's offline rules, so they never spend API quota.

## 2026-10-04 (L4-7: governance tests and report)

- `layer4/governance.py` (`uv run python -m layer4.governance`) writes `outputs/governance_report.md`:
  1. **Counterfactual.** 100 labelled calls run twice: male forms with the name "James Miller", then female forms
     with "Wei Chen". It compares live prompts, the promise check, summary fields and the summary line.
     **0 of 100 changed.** With the rules extractor this is a floor; rerun with the model extractor.
  2. **Leak scan.** Columns in `layer2`, `layer3`, `layer4`, `audit` and `access_analyst` are checked against Layer
     1's approved Protected, AML and Direct-PII tags and protected words, and `outputs/` is checked for emails and
     phone numbers. **None found.** Allowed on purpose, with the reasons in the report:
     `protected_attribute_mentioned` and the agent skill `agent_certified_vulnerable`.
  3. **Refusals in Ask Collections.** **7 of 10 refused.** Three are not:
     - "vulnerable customers" (it fails in a lane);
     - "by preferred language" (a proxy);
     - "by FSA" (a proxy; the release's BQ-024 flags it).

     Logged for Layer 2. **Affects layer 2.**
  4. **Fairness** (restricted schema, measurement only). On 5,000 of the newest resolved promises, mean promise-break
     risk differs by at most **2.4 points across gender** and **1.6 points across age bands**. The model is equally
     calibrated in every group: it under-predicts by 5 to 9 points everywhere.
- `postcall.draft()` accepts an already loaded call (used by the counterfactual test).
- Tests: `layer4/tests/test_l4_governance.py`.

## 2026-10-04 (L4-6: human review and confidence gates)

- **`layer4/review.py` and `layer4.review_queue`:** model output below 0.6 confidence is queued for a team lead,
  once per source and subject. That covers live prompts and promises from the replay, and drafted summary fields.
  - The team lead accepts or overrides each item. An override needs a reason and a corrected value.
  - `review_queued`, `review_accept` and `review_override` go to `audit.events`.
- **Gates everywhere** (`layer4/gates.py`):
  - 0.9 or more is a suggestion.
  - 0.6 to 0.9 is confirmed by the agent; the live prompt shows Confirm and Dismiss, and both are logged.
  - Below 0.6 goes to the queue.
  - Hardship always needs confirmation.
- **Summary overrides:** changing an AI-drafted summary field now needs a reason (`postcall.approve(...,
  reason=...)`). The page asks for it, and the reason is logged.
- **Review and Governance page (team lead):**
  - counts;
  - the open queue, with Accept, or Override with value and reason;
  - decided items;
  - every summary with its edited fields;
  - the audit trail, filterable by case and action.
- Tests: `layer4/tests/test_l4_review.py`, including the page end to end.

## 2026-10-04 (L4-5: post-call summary)

- `layer4/postcall.py`:
  - **`draft(transcript_id)`:** builds the summary fields from Layer 3's `extract()` over the whole redacted call.
    The live replay's promise, read back by the agent, takes precedence. Each field keeps its probability,
    evidence, source and confidence gate.
  - **The narrative is written by rules from the fields, not by an LLM,** so it can't say anything the fields
    don't. It follows the gold label style ("Customer reports job loss; promised $345 on 18 Sep; next step:
    hardship plan review.").
  - **Next step:** chosen by a rule order fitted to the labels' own fields, which agrees with 494 of 500.
  - **`save_draft`, `approve` and `reject`:** these write `layer4.summaries` (draft, final, edits, approver,
    status). A rejection needs a reason. Every action goes to `audit.events`.
  - **On approval:** the approved fields are appended to `layer3.text_features` with `source_type =
    'approved_summary'`, in Layer 3's own row layout (`batch_text._flatten`), and appear in the next pre-call brief.
  - **Disability is a protected ground** (POL-COLL-004 §4.6). It is written as "a health-related hardship" and
    stored as reason `health`. **Affects layer 3.**
- **Agent Assist page, Post-call summary tab:**
  - draft the summary of the call open in the replay, or any transcript ID;
  - edit the fields, each shown with its gate, probability and evidence;
  - the next step and the sentence rebuild from the fields, and the agent can still edit the sentence;
  - approve, or reject with a reason.
- `layer4/eval_summary.py` writes `outputs/layer4_summary_eval.md`:
  - **Template fidelity:** 97% (484/500). The other 16 are the deliberate disability wording.
  - **End to end with the rules extractor:** 40% exact sentences and a mean token F1 of 0.70. Fields:
    hardship 70%, dispute 88%, promise amount 83%, promise date 83%, next step 61%. These should rise with the
    model extractor.
- `precall.py` reads Layer 3's `hardship__prob` column name.
- Tests: `layer4/tests/test_l4_postcall.py` (16 tests, including the page end to end on a one-call database).

## 2026-10-04 (L4-4: live call replay)

- `layer4/live_replay.py` steps through a real call from `silver.transcript_turns_redacted`.
  - **Each turn:** it runs through Layer 3's `extract()`, with the two turns before it as context.
  - **Prompts:** eight rules (`RULES`) turn the typed answers into prompts. Hardship, insolvency, cease request,
    dispute, third party, complaint and protected detail apply to customer turns; threatening wording applies to
    agent turns. Each prompt carries the customer's words, the rule ID, the policy citation, the probability and the
    model version. Each rule fires once per call, at the turn it is said.
  - **Promise check:** a spoken promise is turned into an amount and a date.
    - Spoken numbers and relative dates are understood ("four hundred", "the 18th", "Friday").
    - The agent's read-back ("Just to confirm, $400 on September 18") counts as hearing the promise.
    - The promise is scored with `score_promise()`. At 50% risk or more, the safer date is suggested.
  - **QA checklist:** six items tick from the agent's words, and disclosure before identity verification is
    marked as failed.
  - `iter_replay()` computes one turn at a time, so the page only pays for the turns shown. `replay()` runs the
    whole call.
- `layer4/gates.py`: the confidence gates every suggestion uses.
  - 0.9 or more: a suggestion.
  - 0.6 to 0.9: the agent confirms.
  - Below 0.6: team lead review.
  - Hardship always needs the agent's confirmation.
- **Agent Assist page, Live call replay tab:**
  - pick a labelled demo call or any transcript ID, then use Next turn or Play to end;
  - the transcript sits beside the prompts (newest first, with gate badges), the promise check and the QA
    checklist;
  - the agent confirms or dismisses each prompt, and each decision is logged to `audit.events`;
  - on labelled calls the tab shows time-to-flag against the label.
- `layer4/eval_replay.py` writes `outputs/layer4_replay_eval.md`. On all 500 public labels with the rules
  extractor (no API key yet):
  - **Hardship:** caught in 60% of hardship calls, 45% on the exact turn, with 0 false alarms in 238 calls without
    hardship.
  - **Promises:** heard in 85%; amount right 80%, date right 77%.
  - The model path should lift the hardship numbers; rerun the evaluation once `ANTHROPIC_API_KEY` is set.
- Tests: `layer4/tests/test_l4_live_replay.py` (33 tests; the page test replays a real call when the database
  exists).

## 2026-10-04 (Layer 3 integration)

- Pulled Layer 3's real `extract()`, `get_features()` and the LightGBM `score_promise()`.
- **Fixed in Layer 3's code, logged in their CHANGELOG:** label leakage in the promise-history features (AUC 0.993
  became an honest 0.806) and a training crash on nullable booleans.
- **Pre-call brief:**
  - A failing risk model now shows "unavailable" with the reason instead of breaking the brief.
  - Cases without a golden customer get a caution flag. Those are cases pointing to missing CRM records (D-13).
- **On full data with the real model:** 72 sampled open cases, 0 errors, about 0.16 s per brief. 70 have a risk
  score; the other 2 have no golden record.
- `test_l4_stubs.py` keeps the interface-shape checks but drops the stub-only "bigger amount means more risk" check.
- Layer 2's views (`python -m layer2.build --db lake/maple.duckdb`) are needed for Layer 2's database tests once the
  lake exists.

## 2026-10-03 23:50 (lead decision)

- No more pull-request reviews: everyone pushes directly to `main` after `git pull --rebase` and their layer's
  tests. `CLAUDE.md` (How we work, shared files) and `docs/TEAM_PLAN.md` (Git workflow) are updated.
  **Affects layers 1, 2 and 3.**

## 2026-10-03 (L4-3: pre-call brief)

- `layer4/precall.py`: `build_brief(case_id, as_of=None, identity_verified=False, agent_id=None)` returns the brief as
  plain data. `list_open_cases()` and `agents_with_open_cases()` feed the case picker.
  - **Sources:** it reads Layer 1's `access_agent.case_brief` (masked contact details, no date of birth) and gold
    tables by ID. It never reads `restricted`, and no protected attribute appears; a test checks this.
  - **Disclosure lock:** when `verify_before_disclosure` is true, the debt (balances, DPD, accounts, promises,
    risk) is `None` in the data itself until the agent confirms identity. Agents see only their own cases.
  - **Flags:** stop, caution or info, each with the policy it comes from. They cover insolvency (POL-COLL-001
    §7.3), deceased, cease-communication and representatives (§5.4), consent and internal do-not-call (§5.3),
    the 3-attempts-in-7-days limit (§5.2), open dispute (PRC-COLL-012 §6.1), INC-0914 fee reversal (§6.2),
    complaint, account link alarm, recorded hardship (POL-COLL-004 §4.2), French service and permitted hours.
  - **Hardship options:** computed by rules from `bronze.hardship_programs` on the data date, with no LLM.
    - Inputs: product, hardship level, DPD limit, months on book, uses in 12 months and broken plans in 90 days
      (from `bronze.offers`), and the insolvency exclusion.
    - Each programme is eligible, ineligible (with the reasons) or "check" (what to confirm on the call), plus notes
      on income evidence and approver.
  - **Risk:** promise-break risk from `score_promise()` (stub until Layer 3's model lands) on the open promise.
    Without one, it scores a what-if promise of the overdue amount in 7 days. It returns three reasons and a safer
    date.
  - **Data date:** the brief runs as of 28 Sep 2026, the release snapshot.
- Agent Assist page, Pre-call brief tab:
  - the agent picks their agent ID and an open case, or types a case ID;
  - flags sit at the top, then options, then debt;
  - an "Identity verified" button unlocks the debt;
  - `brief_opened` and `identity_verified` go to `audit.events`.
- Tests: `layer4/tests/test_l4_precall.py`, rules and lock on a synthetic database plus the page end to end.

## 2026-10-03 (L4-2: app shell)

- `app/Home.py` and `app/shell.py`: `uv run streamlit run app/Home.py` opens the team app.
  - **Sidebar:** a role switch (agent, team lead, analyst) and the user name for audit.
  - **Navigation:** `st.navigation` shows each role only its pages.
  - **Page slots:** a slot shows a placeholder until its owner adds the file in `app/pages/`.
  - **Home:** table counts, whether each interface is a stub or real, and LLM usage.
  - **Connection:** one read-write connection per app (`get_con()`). The app never creates an empty database.
  - Placeholder pages `4_Agent_Assist.py` and `5_Review_and_Governance.py`. Page 5 shows the audit trail.
  - How to add a page: `app/README.md`. **Affects layers 1, 2 and 3.**
- Lead: reviewed and merged Layer 1 PRs #1-#5 and #7. `docs/INTERFACES.md` now lists `gold.account_monthly_snapshot` and
  the `access_*` views, and the feature view is marked ready without vulnerability fields. **Affects layers 2 and 3.**

## 2026-10-03 (L4-1: shared code)

- Fix: `shared/llm.py` reports every API failure (missing key, rate limit after retries, network, bad request) as
  `LLMError`. A missing key used to surface as the SDK's `TypeError`, which callers' rule fallbacks didn't catch
  (found reviewing Layer 2's PR #6). **Affects layer 2:** catch `shared.llm.LLMError`.
- Added `anthropic` and `python-dotenv` to the core dependencies (everyone imports `shared/`). `.env.example` gains
  `LLM_CACHE_DIR`, `LLM_OFFLINE` (cache-only mode) and `DHANDHO_USER`. **Affects layers 1, 2 and 3:** run
  `uv sync --all-groups`.
- `shared/config.py` (settings from `.env`) and `shared/db.py`: `connect(read_only=True)` with the Layer 1 DuckDB
  settings. One connection per file per process, handed out as cursors, so a read-only request in a process that
  already writes (the app) shares it instead of failing. `DHANDHO_DB` picks full or dev data. **Affects layers 2, 3.**
- `shared/llm.py`: `complete(prompt, model=None, system=None, json_schema=None, max_tokens=1024, tag="")`.
  - **Cache:** answers are cached under `lake/llm_cache/`, keyed by a hash of every input.
  - **Call log:** every call goes to `lake/llm_calls.jsonl` with tag, model, tokens, latency and cache hit. The log
    never holds prompt text.
  - **Models:** `model="fast"` and `"smart"` resolve from `.env`.
  - **Structured output:** passing `json_schema` uses the API's structured outputs (`output_config.format`) and
    returns a dict. Forced tool use is not used, because Sonnet 5.5 rejects it.
  - **Refusals:** on Sonnet 5.5 and Opus 5.x the server-side refusal fallback is on. A refusal raises
    `LLMRefusal` and is not cached.
  - **Offline:** `LLM_OFFLINE=1` answers only from the cache.
  - `usage_summary()` gives calls and tokens per tag.
  - **Affects layers 1, 2, 3:** tag your calls (`l2.policy`, `l3.extract`).
- `shared/decide.py`: `decide(text, question)` returns `{"answer", "prob", "evidence", "model_version"}`.
  - **Question types:** yes_no (answer is a bool), choice (one of the options), score (clamped to min/max), extract
    (text).
  - **Unknown:** an unknown answer comes back as `None`.
  - **Evidence:** a quote that is not in the text is dropped, and its prob is capped at 0.5.
  - **`model_version`:** `llm:<model>|q:<id>@<version>`, so a changed prompt or model is a new feature version.
  - **Jev:** setting `JEV_API_KEY` routes to `_jev_decide`, which raises until Jev's API is wired in.
  - **Affects layer 3.**
- `shared/audit.py`: `log(event)` appends to `audit.events` and `recent(n, case_id)` reads it.
  - **Columns:** event_id, ts, user, role, action, case_id, golden_customer_id, input_ref, output, prob,
    approved_by, reason.
  - **Insert-only.**
  - **Refused events:** keys that name PII or protected attributes (any depth), contact details in free-text
    fields, and unknown fields.
- Interface stubs, as agreed with Ayush: `extract()` in `layer3/text/extract.py` (keyword rules) and
  `score_promise()` in `layer4/models/promise_break/score.py`. Both are deterministic, return the
  `docs/INTERFACES.md` shapes and have `model_version="stub-0"`. Request logged in `layer3/CHANGELOG.md`.
  **Affects layer 3.**
- Docs: `shared/README.md` has usage for every module. `docs/INTERFACES.md` marks the four shared modules
  **ready** and `extract()`/`score_promise()` as **stub**. **Affects layers 1, 2 and 3.**

## 2026-10-03 (later)

- Lead: restructured the repo so every layer is one self-contained folder. Layer 1's code moved from the repo
  root into `layer1/` (`pipeline/`, `identity/`, `quality/`, `review_app/`, tests and docs) with `git mv`, so history
  follows it. Module names now start with `layer1.` (for example `python -m layer1.identity.run`). `common/` is gone:
  team docs are in `docs/`, each layer's brief, rules and changelog sit in its own folder. The dbt project is renamed
  `dhandho` (it serves all layers); model folders and schemas are unchanged. All 126 tests pass, dbt parses all 22
  models and 42 tests, and the Dagster definitions load. **Affects layers 1, 2 and 3.**

## 2026-10-03

- Lead: the repo becomes the team repo. Root `CLAUDE.md` is now team-wide; Layer 1's working rules moved to
  `layer1/CLAUDE.md`. Added `docs/TEAM_PLAN.md`, `docs/INTERFACES.md`, a `BRIEF.md` per layer, and
  skeleton folders `layer2/`, `layer3/`, `layer4/`, `shared/`, `app/`, `deliverables/`, `outputs/`, `layerN/tests/`.
  **Affects layers 1, 2 and 3.**
- Lead decision: AI APIs are allowed in every layer, through `shared/llm.py` and `shared/decide.py`. This replaces
  the earlier no-API rule. **Affects layers 1, 2 and 3.**
- Lead decision: tasks 1 to 4 of `layer1/docs/handoff_tasks.md` move back to Layer 1; Layer 2 focuses on the
  benchmark. **Affects layers 1 and 2.**
- `vulnerability` is a protected attribute in the release; it must not be a feature or decision input. Layer 1 drops
  `vulnerability_flag` from `gold.l3_customer_features`. **Affects layers 1 and 3.**
