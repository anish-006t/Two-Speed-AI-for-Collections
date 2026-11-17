# Layer 1 working rules (owner: Anish)

> Engineering rules for Layer 1. Code comments cite them as "CLAUDE.md rule N" (the hard rules below).

We are building Layer 1 (Data Product Factory): 31 synthetic bank tables in, one golden customer record (`gold.c360_customer`) out, with an identity confidence on every golden ID. The full design is in `layer1/docs/architecture.md`: read it at the start of any Layer 1 phase, follow it, and flag any place the real data contradicts it.

## Reference files (read when needed, do not import whole)
- `docs/data_dictionary/data_dictionary_columns.csv`: all 1,421 columns (table, column, type, key, description, example, sensitivity)
- `docs/data_dictionary/data_dictionary_tables.csv` and `docs/data_dictionary/data_dictionary_relationships.csv`: table list and the 48 relationships
- Raw data lives in `data/raw/maple_collections_release/` and is never committed to git
- `layer1/docs/decisions.md`: what the real data showed and what we changed because of it (read before changing parsers or matching)

## Stack
Python 3.11, DuckDB, dbt-duckdb, Splink 4 (DuckDB backend), Microsoft Presidio, Streamlit, Dagster. Optional: Redpanda.

## Project layout
- `layer1/pipeline/ingest/`: bronze loaders and `_load_manifest`
- `dbt/models/silver/`, `dbt/models/gold/`: SQL models and tests
- `layer1/identity/`: deterministic rules, Splink config, constraints, clustering, crosswalk
- `layer1/review_app/`: Streamlit review queue
- `layer1/quality/`: anomaly checks and the DQ report
- `layer1/tests/`: pytest for parsers, constraints and survivorship
- `layer1/docs/`: architecture, decisions log, fairness audit, hand-off tasks
- `orchestration/` (repo root): Dagster graph, shared with other layers
- `lake/`: bronze files and the DuckDB database (git-ignored)

## Hard rules (never break these)
1. Precision first. Auto-link only when probability >= 0.95, DOBs are identical and no constraint fires. Everything else goes to review or stays apart.
2. Joint holders and co-borrowers are household links, never identity links.
3. Never overwrite source values. Corrections go in new columns with a reason.
4. Never send raw PII to an LLM. Redact text with Presidio first; for match explanations send comparison results only.
5. Protected attributes, `age`, `age_band` and AML fields never enter gold or Layer 3 views. They live only in the `restricted` schema.
6. Never print full rows of customer PII to the terminal or logs. Show counts, masked samples or IDs.
7. Every gold table carries lineage columns: `_source_system`, `_source_file`, `_batch_id`, `_loaded_at`, `_pipeline_run_id`.
8. Day and month in dates are never guessed. Parse each source with its known format; failures go to quarantine.

9. AI APIs are allowed (lead decision 2026-10-03 15:00; replaces the earlier no-API rule). Rule 4 still applies: no raw PII to any model; redact first, or send comparison results only. Every call goes through `shared/llm.py` (cached and logged). A person still approves mappings, tags and match decisions.

## How to work
- Four people build four layers; this repo session is Layer 1. After every change, add a dated entry to `layer1/CHANGELOG.md` and keep `layer1/README.md` current; mark anything that changes what another layer sees with "Affects layer N". Never edit other layers' folders.
- Start each phase in plan mode: read the relevant part of the architecture and the dictionary, then propose a plan before writing code.
- Develop on a sample first (for example 50K customers), then run on full data.
- Write a test for every parser, constraint and survivorship rule before relying on it.
- Machine limits: about 6 GB free RAM, no GPU. Read Parquet in place, process history year by year, cap common blocking keys.
- After profiling, record what the real data shows for each open item in `layer1/docs/decisions.md` (national_id_hash fill rate, CIF = bureau ref, monthly snapshot crm_customer_id, card phone mask, SCD2 versions, phone hash method).

## Commands
Run everything from the repo root. Python 3.11 lives in `.venv` (managed by `uv`); prefix commands with `uv run`.
- Setup: `uv sync` (installs Python deps), then the spaCy model: `uv pip install en_core_web_md@https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl`
- One-command build and check: `uv run python -m layer1.build --target dev|full` (`--verify-only` to check only; `--skip-redaction` to save 11 minutes on full)
- Data: `unzip -n data/maple_data/maple_collections_release.zip -d data/raw/` (release lands in `data/raw/maple_collections_release/`)
- Bronze ingest (full, idempotent): `uv run python -m layer1.pipeline.ingest.load_bronze` (`--tables customers,card_accounts` for a subset). Writes `lake/maple.duckdb` and `lake/bronze/`.
- Dev sample (~50K customers by FSA): `uv run python -m layer1.pipeline.ingest.sample` (writes `lake/dev.duckdb`; needs the full bronze first)
- Silver on the sample: `uv run dbt build --project-dir dbt --profiles-dir dbt --target dev --select silver --indirect-selection cautious` (cautious: gold contract tests wait for the identity run)
- Silver on full data: same with `--target full`
- Identity resolution: `uv run python -m layer1.identity.run --db lake/dev.duckdb` (full: `--db lake/maple.duckdb`; `--skip-splink` reuses saved Splink scores). Writes the `identity` schema and match quality.
- Threshold sweep: `uv run python -m layer1.identity.tune --db lake/maple.duckdb` (writes `identity.threshold_sweep`; does not change the bands)
- Batch review: `uv run python -m layer1.identity.review_io export|import <csv>|status --db lake/dev.duckdb` (exports PII to `lake/review/`, git-ignored)
- Gold + restricted + contract tests + history: `uv run dbt build --project-dir dbt --profiles-dir dbt --target dev --exclude silver` (after identity)
- Data quality report: `uv run python -m layer1.quality.checks --db lake/dev.duckdb` (writes `gold.dq_report`; exits non-zero on a blocking failure)
- DQ alert text: `uv run python -m layer1.quality.narrative --db lake/dev.duckdb` (Claude drafts it when API credentials are set; otherwise a template)
- Redaction: `uv run python -m layer1.pipeline.redact --db lake/dev.duckdb` (needs `en_core_web_md`; `--limit N` for a quick run)
- Review app: `uv run streamlit run layer1/review_app/app.py -- --db lake/dev.duckdb` (close it before pipeline runs: one DuckDB writer at a time)
- Orchestration: `DAGSTER_HOME=$PWD/lake/dagster DHANDHO_TARGET=dev uv run dagster dev -m orchestration.definitions`
- Tests: `uv run pytest layer1/tests` (parsers, bronze, constraints, clustering, explainer; the review end-to-end test runs when `lake/dev.duckdb` exists)
- Tags and mappings: `uv run python -m layer1.governance.propose tags|mappings`, then a person approves: `... approve <file> --by "<name>" --where tag=Proxy (review)`
- dbt docs: `uv run python -m layer1.governance.dbt_docs` (column descriptions), then `uv run dbt docs generate --project-dir dbt --profiles-dir dbt --target dev` and `uv run dbt docs serve --project-dir dbt --profiles-dir dbt`
- Full rebuild through Dagster (serial, ~16 min on full data): `DAGSTER_HOME=$PWD/lake/dagster DHANDHO_TARGET=full uv run dagster asset materialize --select '*' -m orchestration.definitions`
- Submission files: `uv run python -m layer1.governance.contract` (data contract YAML) and `uv run python -m layer1.quality.report` (DQ report), both into `deliverables/`, after a full build
- Approvals: fixes apply only after an entry in `approved_fixes` in `dbt/dbt_project.yml` with the approver's name
- Data dictionary CSVs: `uv run python -m layer1.pipeline.ingest.export_dictionary` (from `DATA_DICTIONARY.xlsx` into `docs/data_dictionary/`)
