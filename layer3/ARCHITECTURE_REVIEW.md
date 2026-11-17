# Feature Store architecture review and UI verification

Reviewed 2026-10-04 against all five pages of
the phase 1 system design, including the architecture diagram.
The PDF is the requested target architecture. Existing brief allowances for substitutes do not establish exact
compliance with it. This review documents the current implementation; it does not migrate the backend.

## Current UI

`app/pages/3_Feature_Store.py` is implemented and reachable through the team Streamlit app as **Analyst**.
It displays a ten-feature catalogue, source/refresh/sensitivity/AI-derived/accuracy columns, expandable SQL
definitions and lineage, and a customer ID plus as-of date lookup. The catalogue comes directly from Python
definitions and can render without a database or published registry table.

Verification completed:

- `uv run pytest layer3/tests -q`: **12 passed**.
- Streamlit `AppTest`: Home -> Analyst -> Feature Store navigation passed.
- Catalogue and definitions rendered; an empty customer ID disabled the lookup button.
- A synthetic in-memory customer lookup rendered all ten values. Streamlit automatically converted mixed
  value types for Arrow serialization; the page had no uncaught exception.
- Missing database produced a visible error; Agent and Team lead were denied access to the page.

These are automated UI checks, not a browser visual inspection or a full-data integration test.

Follow-up verification on 2026-10-04 after downloading the main Hugging Face release:

- Built Layer 1's development database at `lake/dev.duckdb`: 52,093 C360 customers, 90,911 accounts,
  11,440 promises, 35,997 redacted notes, and 18,042 redacted transcript turns from 1,162 calls.
- The interrupted initial build was completed by rerunning redaction, then quality checks and verification.
  `python -m layer1.build --verify-only --target dev` reports **Layer 1 is ready**.
- 85 quality checks: eight warnings, zero blocking failures. The gold dbt build separately reported
  45 passes and one warning because load time exceeds the fixed source-snapshot freshness threshold.
- Published ten registry rows and 200 text-feature rows (100 transcripts and 100 notes) using rules mode.
- Live/training feature parity and the UI lookup passed against built C360 data for `GC00000001`, as of
  `2026-10-04`. These checks do not resolve the historical correctness issues below.
- `lake/maple.duckdb` currently contains the full bronze ingest, not a complete full-data C360 build.
  Use `lake/dev.duckdb` for the app.

## Comparison with the PDF

| PDF requirement | Current implementation / remaining gap |
|---|---|
| Feast, DuckDB offline store, SQLite online store (page 1) | A Python registry and DuckDB SQL queries exist; Feast and SQLite serving are absent. |
| One definition for training and live scoring | Both paths use `layer3/features/defs.py`; their equivalence test passes. Historical correctness still has the issues below. |
| DPD, broken promises in 90 days, kept rate, payday, salary delay, utilisation, bureau score change, response rate by channel (page 3) | Ten registered fields include DPD and promise aggregates. Broken promises are not windowed to 90 days; payday, salary delay, utilisation, bureau score change, and channel response rates are not registered. The safer-date helper separately uses stated income dates. |
| Text-derived features feed the same feature store | Typed questions, extraction, and a batch writer exist. The ten-feature registry and customer vector do not read `layer3.text_features`; all catalogue rows have `ai_derived=False`. |
| Fast Jev decisions; GLiNER2 amount/date extraction | `shared/decide.py` raises `NotImplementedError` for Jev. Extraction currently uses rules or LLM structured output; GLiNER2 is absent. |
| Measured accuracy against human labels | Evaluation code exists, but `outputs/layer3_label_eval.md` was absent. Catalogue `accuracy=1.0` is a dataclass default, not measured text-extraction accuracy. |
| Approved summaries flow back into Layer 3 | Documented in the interfaces, but `layer4/postcall.py` and the approval write-back implementation are absent. |
| Protected attributes excluded; hardship is protective only | Feature definitions exclude protected attributes. Vulnerability must not become a decision input even though the PDF lists it among call questions. The promise model currently includes all registered fields, including hardship; protective-only use needs enforcement. |
| Analysts see aggregates only (page 3 governance) | The current brief/UI permits an analyst to inspect an individual customer's feature vector. This is an access-policy discrepancy with the PDF. |

### Historical correctness requires work

Promise outcome features filter `ptp_created_ts`, but not `status_ts`. A synthetic check set a promise's broken
status timestamp to 2026-04-01 and queried as of 2026-03-01: `prior_broken_ptp_count` incorrectly returned 1.
Training at promise creation can therefore include outcomes learned later, including the target promise's outcome.
The passing tests do not cover this case. Outcome availability and target-event exclusion need explicit handling.

Base features read the current `gold.l3_customer_features` view and filter `_loaded_at`; they do not reconstruct
past customer/account states from history. The tests supply synthetic historical rows that the current view does
not retain. A date before the load can therefore return null base features. The UI's default 2026-09-28 must not
be treated as proof of a complete historical snapshot. Date-only lookup means midnight at the start of that date.

## Open the UI now (PowerShell)

From the existing environment:

```powershell
Set-Location 'D:\IIIT H\cibc\dhandho'
$env:DHANDHO_DB = 'lake/dev.duckdb'
$env:L3_EXTRACTOR_MODE = 'rules'
.\.venv\Scripts\python.exe -m streamlit run app/Home.py
```

Open **http://localhost:8501**, select **Analyst** in the sidebar, then **Feature Store**.
If Streamlit reports another port, use the URL it prints. Rules mode makes the Home interface probe local and
does not require an API key. The development database is now built. To reproduce the successful lookup,
use customer `GC00000001` and as-of date `2026-10-04`.

On a fresh environment, install the app dependencies first with `uv sync --group apps --group layer3`.
Do not start the feature page directly: the Home shell supplies role selection and navigation.

## Verify against Layer 1 data

Close Streamlit with **Ctrl+C** before building or writing the lake. Follow the data download/extraction steps
in the [root README](../README.md#before-you-start-build-layer-1-everyone); building cannot proceed until the
release is at `data/raw/maple_collections_release/`. Then run:

```powershell
uv sync --all-groups
uv pip install en_core_web_md@https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl
uv run python -m layer1.build --target dev
uv run python -m layer1.build --verify-only --target dev
$env:DHANDHO_DB = 'lake/dev.duckdb'
uv run python -c "from shared.db import connect; print(connect().execute('SELECT golden_customer_id, _loaded_at FROM gold.l3_customer_features LIMIT 5').fetchall())"
```

The last command prints only golden IDs and load timestamps. Use one returned ID in the UI. For a current-value
smoke check, choose an as-of date after its load timestamp (for example the next day); this does not validate
historical correctness. Relaunch the app, open Feature Store, expand **Definitions and lineage**, and click
**Load features**. Expect ten feature rows. Missing historical values should be investigated, not replaced by zero.

Optional backend materialization, while the app is stopped:

```powershell
uv run python -c "from layer3.features.defs import publish_registry; print(publish_registry())"
uv run python -m layer3.features.batch_text --db lake/dev.duckdb --limit 100
```

Registry publication should report 10. The text batch limit applies separately to transcripts and notes;
it does not represent full-data processing. These steps do not add text features to the current UI vector.

Run the existing checks with `uv run pytest layer3/tests -q`. Passing them confirms their tested behavior;
it does not close the PDF discrepancies above.
