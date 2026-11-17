# layer3/: Feature Store (owner: Ayush)

Features from structured data and from text, one definition for training and live scoring.

Status and remaining work: [`STATUS.md`](STATUS.md). Built and evaluated on full data on 2026-10-04
(318,808 text-feature rows; promise model AUC 0.775, top-decile broken rate 0.963).
Architecture notes: [`ARCHITECTURE_REVIEW.md`](ARCHITECTURE_REVIEW.md).

- `text/questions.yaml`: versioned typed questions; `text/extract.py`: `extract(text, source_type)`;
  `text/eval_labels.py`: accuracy on the public labels
- `features/defs.py`: `get_features(id, as_of)`, `build_training_frame(events)`, the feature registry
- The promise-break model lives in `layer4/models/promise_break/` (also Ayush's)
- SQL features go in `dbt/models/layer3/` (schema `layer3`); tests in `layer3/tests/`

## Run

```bash
# Offline deterministic extraction (default when no API key is configured)
uv run python -c "from layer3.text.extract import extract; print(extract('I will pay $50 Friday', 'turn'))"

# After Layer 1 (uv run python -m layer1.build): history tables, all text features and the registry in one command
uv run python -m layer3.build                    # full data: about 5 minutes (--db lake/dev.duckdb for the sample)
uv run python -m layer3.build --skip-text        # history and registry only, under a minute

# Accuracy on the public labels (outputs/layer3_label_eval.md + .json; the registry reads the accuracy from it)
uv run python -m layer3.text.eval_labels
# Promise-break model (writes model.txt, metadata.json, outputs/promise_model_report.md); about 10 seconds
uv run python -m layer4.models.promise_break.train --db lake/maple.duckdb
uv run pytest layer3/tests layer4/tests
```

Set `L3_EXTRACTOR_MODE=llm` to require `shared.decide`'s model path, `rules` to force the auditable local baseline,
or leave it at `auto` (model when a key is present, rules otherwise). Every returned answer includes both the model
version and, for model-backed extraction, the question ID/version.
