# Layer 3 status: Feature Store and the promise-break model

Updated 2026-10-04 after the full-data build. Everything in the brief is done except the stretch item.

| # | Deliverable | State |
|---|---|---|
| L3-1 | `extract()`, `get_features()`, `score_promise()` | Done (real implementations) |
| L3-2 | Typed-question registry `text/questions.yaml` | Done; options aligned with the label vocabularies (v2) |
| L3-3 | Extractor `text/extract.py` | Done: `shared.decide` when an API key is set, auditable rules otherwise |
| L3-4 | Label evaluation | Done on full data: `outputs/layer3_label_eval.md` (+ `.json`) |
| L3-5 | Batch text features `layer3.text_features` | Done on full data: 318,808 rows (all 25,000 transcripts + notes for open cases, last 12 months) |
| L3-6 | Feature definitions and `layer3.feature_registry` | Done: 30 point-in-time features; measured accuracy filled in; history tables built by `python -m layer3.build` |
| L3-7 | Promise-break model | Done: trained on 198,119 promises, AUC 0.775, top decile 96.3% broken; artifact committed; `outputs/promise_model_report.md` |
| L3-8 | Feature Store page | Done: catalogue, aggregate summary, one-customer vector |
| L3-9 | QA checklist scoring | Not started (stretch) |

## How to rebuild
```bash
uv run python -m layer1.build --target full       # Layer 1 first
uv run python -m layer3.build                      # about 5 minutes
uv run python -m layer3.text.eval_labels
uv run python -m layer4.models.promise_break.train --db lake/maple.duckdb
```

## The three claims (checked by `layer3/tests/test_l3_claims.py`)
1. **SQL + LLM features.** 19 structured features are SQL; 11 AI-derived features come from the typed-question
   extractor over voice (call transcripts) and text (agent notes). Voice means the call transcripts; no acoustic
   features are used (pitch, accent, speaking rate and pauses can stand in for protected grounds).
2. **LLM features are tested.** `layer3.feature_registry` gives every AI-derived feature its prompt, prompt version,
   the extractor version behind the stored values, and its accuracy with the number of labels and the extractor it
   was measured with. Every answer records `<extractor>|q:<question>@<version>`.
3. **Same values everywhere.** Training (`build_training_frame`) and live scoring (`get_features`, used by
   `score_promise()` in the pre-call brief and the live replay) run the same SQL; the tests compare them on real
   customers, and compare stored text answers with a fresh live extraction of the same text.

## When the API key is added (one step)
Put `ANTHROPIC_API_KEY` in `.env`, then:
```bash
uv run python -m layer3.build --extractor llm      # re-extract every text feature through the LLM (cached)
uv run python -m layer3.text.eval_labels           # accuracy of the LLM against the human labels
uv run python -m layer4.models.promise_break.train --db lake/maple.duckdb
```
The registry, report and page then show the LLM's version and accuracy instead of the rules'. Until then the stored
text features and their accuracy are from the offline rules extractor, and the catalogue says so.

## Known limits (honest notes for the deck)
- **Rules extractor without an API key:** strong on yes/no questions (complaint, cease request, insolvency, dispute:
  0.88-1.00), weaker on hardship reason (0.36-0.47) and next step (0.40-0.55), and close to zero on promise amount,
  date and intent, which need the model path (`L3_EXTRACTOR_MODE=llm` with a key) to resolve "Friday" into a date.
- **Calibration:** the model ranks risk well, but the newest promises break more often than the older ones it learned
  from, so absolute risk is understated in the middle bands (see the report's calibration table).
- **Two history tables come from bronze** (salary credits, bureau pulls), built in schema `layer3` through Layer 1's
  crosswalk, as the lead suggested; there is no gold version of them.
- **Not built (brief allows skipping):** GLiNER2 for amounts and dates, Jev in `shared/decide.py`, Feast, QA checklist
  scoring.
