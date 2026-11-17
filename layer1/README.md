# Layer 1: Data Product Factory (owner: Anish)

Layer 1 turns the 31 synthetic Maple Bank tables into one trusted record per customer, `gold.c360_customer`, with an
identity confidence on every golden ID. The design is precision-first: we link records only on strong evidence,
send doubtful pairs to a person, and never merge two people on a guess.

- Design: [docs/architecture.md](docs/architecture.md)
- What the real data showed and what we changed: [docs/decisions.md](docs/decisions.md)
- Working rules for the build: [CLAUDE.md](CLAUDE.md); deliverables: [BRIEF.md](BRIEF.md); changes: [CHANGELOG.md](CHANGELOG.md)

## Build Layer 1 first (everyone, on your own laptop)

Every layer reads Layer 1's database, so build it once before you start, then check it. One command does it all.

```bash
# once: environment and data (about 25 GB disk; the data is not in git)
pip install --user uv
uv sync --all-groups
uv pip install en_core_web_md@https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl
hf download nuxsh/maple-collections-hackathon maple_collections_release.zip --repo-type dataset --local-dir data/maple_data
unzip -n data/maple_data/maple_collections_release.zip -d data/raw/     # PowerShell: tar -xf ... -C data/raw

# build: start with the 50K-customer sample (about 2 minutes), then the full data when you need it
uv run python -m layer1.build --target dev      # writes lake/dev.duckdb
uv run python -m layer1.build --target full     # writes lake/maple.duckdb, about 16 minutes, ~6 GB free RAM

# check: every table other layers use is there and data quality has no blocking failure
uv run python -m layer1.build --verify-only --target dev
```

The check ends with **"Layer 1 is ready"**. Then point your code and the app at a database:
`DHANDHO_DB=lake/dev.duckdb` in `.env` for the sample (the default is `lake/maple.duckdb`), and read it with
`shared.db.connect(read_only=True)`.

**If something goes wrong**

| Symptom | Fix |
|---|---|
| "Conflicting lock" or "another process holds it" | Close Streamlit, notebooks and any other DuckDB connection; DuckDB allows one writer at a time |
| `No module named 'en_core_web_md'` | Run the `uv pip install en_core_web_md@...` line again: every `uv sync` removes the spaCy model |
| `data/raw/maple_collections_release/ is missing` | Unzip the release first (commands above) |
| Out of memory during `--target full` | Close the browser and other heavy apps; add `--skip-redaction` to save 11 minutes and about 1.5 GB |
| Access views fail with `Catalog ... does not exist` | Keep the database file names `dev.duckdb` and `maple.duckdb`; don't rename or copy them under another name |
| The build stops at a step | It names the step; fix and rerun the same command (finished steps are safe to repeat) |

## Status

| Step | State | What exists |
|---|---|---|
| 0. Environment | Done | `uv sync --all-groups` (Python 3.11) + spaCy `en_core_web_md` |
| 1. Profiling and decisions | Done | `docs/decisions.md` (D-01 to D-38) |
| 2. Bronze ingest | Done, full data | 31 tables, counts and checksums match the release |
| 3. Silver parsers | Done, full data | `identity_input`: 3,487,885 keys |
| 4. Identity resolution | Done, full data | 1,060,086 golden IDs; precision 99.8% and recall 97.7-98.9% per source; 0 false household merges |
| 5. Gold C360 | Done, full data | `gold.c360_customer` + accounts, cases, timeline, promises, incidents; all data-contract tests pass |
| 6. Data quality report | Done, full data | 86 checks in `gold.dq_report`; 11 warnings (all explained), 0 blocking |
| 7. Threshold tuning | Done, full data | bands kept at 0.95 / 0.70; sweep in `identity.threshold_sweep` |
| 8. Review workflow | Done | Streamlit app + batch CSV export/import; end-to-end test proves decisions split and merge |
| 9. Fairness audit | Done, full data | [docs/fairness_audit.md](docs/fairness_audit.md): no protected group differs by more than 0.5 points |
| 10. Redaction | Done, full data | 760,594 notes and 388,701 transcript turns; 0 identifiers left in the spot check |
| 11. Orchestration | Done, full data | one Dagster run builds everything in about 16 min (D-41) |
| 12. Protected fields | Done, full data | vulnerability fields only in `restricted`; `preferred_language` out of the feature view (D-37) |
| 13. Benchmark traps | Done, full data | recomputed DPD buckets, missing-DPD flags, parsed snapshot dates (D-38) |
| 14. Access views | Done, full data | `access_analyst`, `access_agent`, `access_engineer` schemas with tests (D-39) |
| 15. Tags and mappings | Approved by Anish | `layer1/config/` (1,417 tags, 51 mappings) with tests (D-40) |
| 16. dbt docs | Done | all 459 gold columns described (D-41) |
| 17. Contract and DQ report | Done, full data | `deliverables/data_contract_c360.yaml`, `deliverables/data_quality_report.md` (D-42) |
| 18. Build command, README section | Done | `python -m layer1.build`, README section in the root README (D-43) |
| 19. Customer 360 page | Done, full data | `app/pages/1_Customer_360.py`, masked by role (D-45) |
| Tests | Passing | 172 Layer 1 pytest tests; 45 dbt checks |

## Match quality (full data, scored at the cluster level)

| Truth set | Source | Precision | Recall |
|---|---|---|---|
| Collections cases | Cards | 99.78% | 98.93% |
| Collections cases | Deposits | 99.83% | 97.66% |
| Collections cases | Loans | 99.76% | 98.02% |
| CRM duplicate pointer | CRM | n/a | 98.99% |
| Joint holders and co-borrowers (1,079 pairs) | n/a | 0 false merges | n/a |

Most "wrong" links are noise in the truth set: in 555 of about 650, the case points to a CRM record of a different
person (different name and DOB) while the product's DOB matches the linked record (decisions D-30). The case truth
set covers only customers with a collections case, so it leans towards delinquent customers.

Identity confidence: 803,477 high, 128,298 medium, 128,311 low (`verify_before_disclosure` is true for medium and low).

## Key findings from profiling (details in decisions.md)

- About 3.49M source records feed identity matching. These are 1,017,885 current CRM rows plus 550K card, 340K
  loan, 580K deposit and 1M bureau customer keys.
- CRM duplicates: 18,426 pairs share a national ID hash. That leaves 999,459 people, matching the expected ~1M.
- Deposit customer numbers (CIF) lost their leading zeros. Once padded to 10 digits, every one matches a bureau record.
- 58% of card phones are masked (`519555xxx42`) and 39% are full numbers. Card names come in three layouts.
- The CRM raw DOB mixes day-first and month-first dates, so only the clean ISO `date_of_birth` is used.
- The monthly snapshot's CRM ID covers all products but is noisy (20% of accounts change CRM ID), so it is used only
  to evaluate matching.
- The contact-history phone hash is unsalted MD5 of the 10-digit number. That makes `best_reachable_phone`
  possible, but the hash is treated as PII.

## Compute

No GPU is needed. Splink is a CPU statistical model and Presidio runs CPU spaCy. The project uses no external AI APIs. The
laptop (10 threads, 15 GB RAM, about 4–6 GB free) is enough. DuckDB runs with a 4 GB memory limit and spills to
`lake/tmp`, and about 25 GB of disk covers the data, bronze copies and the database.

## For other layers

**Gold is ready to use.** Read customers through these tables, all keyed by `golden_customer_id`:

| Table | What it is | Main users |
|---|---|---|
| `gold.c360_customer` | One row per customer: contact details, consent, restrictions, `hardship_flag`, `identity_confidence`, `verify_before_disclosure`, `incident_affected` | Layers 2, 4 |
| `gold.c360_accounts` | Every card, loan and deposit account, with `dpd_missing` and `dpd_bucket_recomputed` | Layers 2, 3 |
| `gold.account_monthly_snapshot` | Quarter-end account history with parsed `month_end_date` and recomputed DPD buckets | Layers 2, 3 |
| `gold.cases` | Collections cases, with `case_link_alarm` and `current_bucket_recomputed` | Layers 2, 3, 4 |
| `gold.contact_timeline` | Contact history (no phone hash, no SMS reply text) | Layers 2, 3 |
| `gold.promises_to_pay` | Promises, with the INC-0914 adjustment columns | Layers 2, 3 |
| `gold.incident_effects` | Which customers INC-0914 affected, and how | Layers 2, 4 |
| `gold.l3_customer_features` | Feature view without identifiers or protected fields | Layer 3 |
| `gold.c360_customer_history` | Versions of each golden record over time | Layer 2 |
| `gold.dq_report` | Data quality results of every run | everyone |
| `identity.crosswalk` | Any source customer key to its golden ID | everyone |
| `silver.agent_notes_redacted`, `silver.transcript_turns_redacted` | Notes and transcript turns with identifiers replaced by `<ENTITY>` tags | Layers 2, 3 |

## What other layers should know

These facts come from profiling and hold whether or not identity resolution is finished.

- **Customer IDs:** a person can have two CRM records. 18,426 pairs share a national ID hash, and about 1M real
  people sit behind 1,017,885 current CRM rows. Counting `crm_customer_id` overcounts people until gold exists.
- **Mismatched IDs:** 10,365 `collections_cases.coll_customer_ref` values point to CRM IDs that exist nowhere, and
  772 point to deleted CRM rows. `account_monthly_snapshot.crm_customer_id` is noisy: about 250K of its IDs don't
  exist in `customers`, and 20% of accounts change CRM ID across quarters. **Affects layers 2 and 3.**
- **`collections_cases.account_ids`:** usually a JSON array, but about 5% hold one bare ID such as `C-123`. It lists
  card (`C-`), loan (`L-`) and deposit (`CHQ-`) accounts. **Affects layers 2 and 3.**
- **Deposit customer numbers (CIF)** lost their leading zeros in `deposit_accounts.src_customer_ref`. Pad to 10
  digits before joining to `external` or `bureau_history`.
- **`contact_history.phone_number_hash`** is an unsalted MD5 of the 10-digit phone number, which can be reversed.
  Treat it as personal data, not as an anonymous key. **Affects all layers.**
- **CRM `dob_raw`** mixes day-first and month-first dates. Use `date_of_birth` only.
- **Planted traps** from the architecture doc: `contact_history.channel_v2` is a renamed `channel` column from a
  26 Sep release, and incident INC-0914 (app outage) posted app payments a day late and charged late fees in error.
  **Affects layers 2, 3 and 4.**

## What Layer 1 will promise other layers

- `golden_customer_id` on every gold table, and `identity.crosswalk` from any source customer key to it.
- `identity_confidence` (high, medium, low) and `verify_before_disclosure`. When that flag is true, an agent or
  Layer 4 must verify identity before discussing any debt. **Affects layer 4.**
- Protected attributes (gender, marital status, citizenship, household, newcomer, accessibility, vulnerability
  type), `age`, `age_band` and the AML fields never appear in gold or in Layer 3 views. **Affects layer 3.**
- Lineage columns on every gold row: `_source_system`, `_source_file`, `_batch_id`, `_loaded_at`,
  `_pipeline_run_id`.
