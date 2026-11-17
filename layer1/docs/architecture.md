# Layer 1: Collections 360 Data Product Factory (final architecture)

Layer 1 turns the 31 synthetic bank tables into one trusted record per customer, `gold.c360_customer`, and publishes how confident each identity link is. The design is precision-first: wrongly merging two people could disclose one person's debt to another, so we link only on strong evidence, send doubtful pairs to a person, and let identity confidence decide what an agent may disclose.

## 1. What Layer 1 delivers

Layer 1 is the Data Product Factory from the CIBC Collections Hackathon brief: raw sources in, one golden customer record (C360) out, with one ID per customer.

**Outputs**

- `gold.c360_customer`: one row per real customer, with history (`valid_from` / `valid_to`), consent, contact restrictions, care flags and lineage
- `gold.c360_accounts`, `gold.contact_timeline`, `gold.cases`, `gold.promises_to_pay`: all keyed by `golden_customer_id`
- `identity.crosswalk`: every source customer key mapped to a golden ID, with the probability, rule and reviewer behind it
- `identity_confidence` (high, medium, low) and `verify_before_disclosure` on every golden ID
- `gold.dq_report`: data quality and match quality for every run
- Feature-ready views for Layer 3, with protected and identity-only fields removed

**Consumers:** Layer 2 (plain-English questions with SQL shown), Layer 3 (feature store), the agent desktop, and the data quality owner.

**Design principles**

1. **Precision first.** A wrong merge is worse than a missed match, so doubtful pairs stay apart until a person decides.
2. **Nothing changes data without a gate.** Every change passes a test or a human approval, and is logged.
3. **AI drafts and explains; statistics and people decide.** No LLM output becomes data on its own.
4. **Source facts are never overwritten.** Corrections are added beside the original value, with a reason.

## 2. Source data and how it connects

The data is a fully synthetic snapshot dated 28 Sep 2026, with history from Oct 2016: about 1M customers, 1.84M accounts and 31 tables in CSV, Parquet by year, JSON and WAV. It splits into two worlds that need different handling.

- **Collections side.** Cases, contacts, promises to pay, notes, transcripts, model scores, monthly snapshots and salary history already carry `crm_customer_id`. They join directly once CRM duplicates are resolved.
- **Product side.** Cards, loans, deposits and bureau data each use their own customer key. The dictionary marks these links as "via identity matching".

| Source | Customer key | Name as stored | DOB as stored | Phone | Postal code |
| --- | --- | --- | --- | --- | --- |
| `customers` (CRM, ~1.02M rows) | `crm_customer_id` | Split fields + `preferred_name` | ISO `date_of_birth`; `dob_raw` mixes DD/MM and MM/DD and is never parsed (D-09) | E.164 primary; raw secondary and work | Normalized (already corrected) + raw |
| `card_accounts` (~660K) | `src_customer_ref` | `R. MITCHELL` (49%), `RYAN MITCHELL` (39%), `MITCHELL/RYAN` (10%) (D-07) | `14-03-1988` | 58% masked `519555xxx42`, 39% full 10 digits (D-04) | `N9A4J2` |
| `loan_accounts` (~404.5K) | `src_customer_ref` | `Ryan Mitchell`, mixed case, accents, some reversed (D-08) | `1988/03/14` | `+1 (519) 555 0142` | `N9A 4J2` |
| `deposit_accounts` (~780K) | `src_customer_ref` (CIF, leading zeros lost: pad to 10 digits, D-02) | `MITCHELL R J` | `19880314` | `5195550142` | `N9A 4J2` |
| `external` (~1M) | `bank_subject_ref` | `MITCHELL, RYAN JAMES` | ISO date | None | Inside the address string |

Only `customers` has `national_id_hash` and email, so product records match on name, DOB, phone and postal code alone.

**Other links that matter**

- Deposit CIF equals `external.bank_subject_ref`, **confirmed after padding the CIF to 10 digits** (all 580,000 padded CIFs are in `external`; DOBs agree 99.7%). Unpadded, only 77% match (D-02).
- `loan_accounts.pad_source_account` and `card_accounts.autopay_source_account` point to a deposit account, or hold `EXTERNAL` (D-14).
- `deposit_accounts.joint_holder_ref` and `loan_accounts.co_borrower_ref` point to other people who often share an address, phone and surname. Fewer than 1% of these refs resolve to a record in the data (D-12).
- `collections_cases.coll_customer_ref` (a CRM ID) plus `account_ids` (card, loan and deposit IDs; about 5% are a bare ID instead of a JSON array) link CRM to products for every customer with a case (D-13).
- `customers.duplicate_of_crm_id` is CRM's own incomplete duplicate pointer.
- `customers` is shaped as an SCD2 extract, but the release holds one version per ID; only `is_deleted` (2,115 rows) removes rows (D-05).

**Traps planted in the data**

- `contact_history.channel_v2` is a renamed `channel` column from an upstream release on 26 Sep.
- Incident INC-0914 (app outage): payments made in the app posted a day late, and late fees were charged in error.
- The card phone is masked on 58% of cards (first 6 and last 2 digits visible), so those can only partly match.
- Deposit CIFs lost their leading zeros (7, 8 and 10 digits occur).
- CRM `dob_raw` mixes day-first and month-first dates.

## 3. Architecture at a glance

Data flows top to bottom through five zones; a person can step in at review, at every mapping and fix the AI proposes, and at every downstream use of the flags.

Flow: sources -> bronze (files as received + manifest) -> quality and anomaly gate (quarantine) -> silver (per-source parsers, redaction, identity_input) -> identity resolution (deterministic -> Splink -> constraints -> human review -> clustering and ledger) -> gold C360 -> Layer 2, Layer 3, agent desktop, DQ report. Optional event lane replays notes and transcripts into bronze and looks up golden IDs in the crosswalk.

The highlighted gold box is the deliverable. Every stage above it exists to make that one record trustworthy, and every consumer below it reads the identity confidence along with the data.

## 4. Stages, tools and timing

Every stage runs as a daily batch except the optional event lane; Dagster orchestrates the run, one asset per table.

| Stage | What it does | Tools | Runs |
| --- | --- | --- | --- |
| Bronze ingest | Stores every file exactly as received and writes a manifest row (table, file, row count, hash, batch). A file whose hash is already loaded is skipped, so reloads are safe. | Python, DuckDB `read_csv` / `read_parquet` | Daily |
| Quality and anomaly gate | Contract, schema-drift and anomaly checks (section 10). Failing rows go to `silver.quarantine` with the rule and reason; the pipeline continues with clean rows. | dbt tests, DuckDB SQL; LLM writes alert text | Every load |
| Silver | Keeps current CRM rows only, parses each source with its own fixed format, normalizes phones and postal codes, redacts free text, and builds `identity_input` (section 6). | dbt with DuckDB, Microsoft Presidio | Daily |
| Identity resolution | Deterministic rules, Splink scoring, constraints, human review, clustering and the identity ledger (section 7). | Splink on DuckDB, Streamlit review queue, LLM via API | Daily; review on demand |
| Gold C360 | Builds the golden record, history, accounts, timeline, cases, promises and the DQ report, with lineage on every row. | dbt with DuckDB, dbt docs | Daily |
| Event lane (optional) | Replays notes and transcripts; each event takes its golden ID from the crosswalk and never reruns matching. | Redpanda, Python consumer | Minutes |

Lineage columns on every gold row: `_source_system`, `_source_file`, `_batch_id`, `_loaded_at`, `_pipeline_run_id`.

## 5. Storage layout

Raw files stay in a folder-based lake, and one DuckDB database holds the silver, identity, gold and restricted schemas on top of it. Large history tables are never copied: DuckDB reads their yearly Parquet files in place through views.

| Zone | Location | What it holds | Format |
| --- | --- | --- | --- |
| Bronze | `lake/bronze/<table>/load_date=2026-09-28/` | All 31 tables as delivered; history keeps its `year=` partitions; notes, transcripts and voice as files; `_load_manifest` | CSV, Parquet, JSON, WAV |
| Event lane (optional) | Redpanda topics `notes`, `transcripts` | Replayed events; a consumer appends them to bronze | JSON messages |
| Silver | DuckDB schema `silver` | One cleaned table per source, `identity_input`, redacted notes and transcript turns, `quarantine` | Tables; views over history Parquet |
| Identity | DuckDB schema `identity` | `crosswalk`, `golden_alias`, `household_links`, `review_queue`, `review_decisions` (must-link and cannot-link), `match_pairs` for the current run | Tables |
| Gold | DuckDB schema `gold` | `c360_customer` and `c360_customer_history`, `c360_accounts`, `contact_timeline`, `cases`, `promises_to_pay`, `dq_report`; semantic tables passed through for Layer 2 | Tables, exported to Parquet |
| Restricted | DuckDB schema `restricted` | Protected attributes keyed by golden ID, for the fairness audit only | Tables; fairness reviewer role only |
| Retrieval (optional) | DuckDB `vss` extension or LanceDB | Node and edge tables built from gold keys; embeddings of redacted notes, transcripts and policy documents, each tagged with golden ID, case ID, date and source ID | Tables and vector index |

**Why DuckDB.** It reads Parquet in place, handles the ~20M-row `loan_instalments` and ~10M-row `transactions` tables on a laptop, and is Splink's fastest backend. Everything is SQL, so Layer 2 can show its queries.

**History.** The supplied tables already carry history (`account_monthly_snapshot`, `card_statements`, `loan_instalments`, `bureau_history`, `salary_credit_history`). We add history only for facts we create: the crosswalk and the golden record.

**Retrieval layer.** It is derived from gold and refreshed after it. The gold SQL tables stay the source of truth; the graph and vectors only help Layer 2 find a customer's story and the text behind it.

## 6. Silver: cleaning rules per source

Each source gets its own fixed parser, because the formats are known from the dictionary. Day and month are never guessed: a value that doesn't fit its source's format goes to quarantine.

| Source | Name parser | DOB parser | Phone | Postal code |
| --- | --- | --- | --- | --- |
| `customers` | Use split fields; `preferred_name` as a nickname | `date_of_birth` (ISO); never `dob_raw` | `primary_phone_e164`; secondary and work normalized to E.164 | `postal_code` |
| `card_accounts` | `R. MITCHELL` → initial + last; `RYAN MITCHELL` → first + last; `MITCHELL/RYAN` → last + first | `DD-MM-YYYY` | Masked → `phone_first6` + `phone_last2`; unmasked → E.164 | Insert the space: `N9A4J2` → `N9A 4J2` |
| `loan_accounts` | `Ryan Mitchell` → first + last (accents stripped; order left to Splink's swap level) | `YYYY/MM/DD` | To E.164 | As is |
| `deposit_accounts` | `MITCHELL R J` → last, first initial, middle initial; CIF padded to 10 digits | `YYYYMMDD` | To E.164 | As is |
| `external` | `MITCHELL, RYAN JAMES` → last, first, middle | ISO date | None | Parsed from the address string |

**Rules for every source**

- `customers` keeps current rows only: `effective_to_ts = 9999-12-31` and `is_deleted = false`.
- One row per source customer key. When a key's accounts disagree on DOB, `dob` is null, `dob_conflict = true` and the key goes to review (D-11).
- Names are also stripped of accents.
- Names are upper-cased and stripped of punctuation; suffixes (Jr, Sr, III) go to their own column.
- Postal codes must match A1A 1A1 and their FSA must exist in `geo_reference`.
- `contact_history.channel_v2` is mapped back to `channel` once an engineer approves the fix (section 10).
- Notes, SMS replies and transcript turns are redacted with Presidio before anything else reads them.

**Output: `silver.identity_input`.** One row per source customer key: `source_system`, `source_id`, `first_name`, `first_initial`, `middle_initial`, `last_name`, `name_suffix`, `dob`, `phone_e164`, `phone_partial`, `postal_code`, `fsa`, `email` and `national_id_hash` (CRM only), plus `joint_or_coborrower_refs`, `dob_conflict`, `phone_first6`, `phone_last2` and `phone_masked`. This is the only table Splink reads.

## 7. Identity resolution

Records become one customer only through strong rules, a high Splink score with an exact DOB, or a person's approval. Everything else stays apart, flagged for identity checks.

### Tier 1: deterministic rules

| Rule | Condition | Result |
| --- | --- | --- |
| D1 | Two CRM rows share `national_id_hash` | Merge if DOBs are identical; otherwise send to review |
| D2 | Deposit `src_customer_ref` (CIF, padded to 10 digits) equals `external.bank_subject_ref` | Link the bureau record to that deposit customer if DOBs are identical; otherwise review (confirmed, D-02) |
| D3 | `loan_accounts.pad_source_account` or `card_accounts.autopay_source_account` equals a deposit account ID | Used as Splink evidence, not a link on its own; `EXTERNAL` values are ignored. The deposit account may be joint, so DOBs must be identical. |

### Tier 2: Splink scoring

Splink runs `link_and_dedupe` across five sources: CRM, cards, loans, deposits and bureau.

| Field | Comparison levels |
| --- | --- |
| First name | Exact; Jaro-Winkler ≥ 0.92; initial matches; matches `preferred_name`; first and last swapped |
| Last name | Exact; Jaro-Winkler ≥ 0.92, with term-frequency adjustment for common surnames |
| DOB | Exact; day and month swapped; one part differs |
| Phone | E.164 exact; last 7 digits equal; consistent with the card mask (first 6 + last 2) |
| Postal code | Exact; same FSA |
| Email, ID hash | Exact (CRM pairs only) |

**Blocking** (a union of four rules, with caps on very common keys): surname Double Metaphone + DOB year; DOB + FSA; last 7 phone digits (first 6 + last 2 for masked card phones); first initial + surname + postal code.

**Training.** u-probabilities come from random sampling; m-probabilities from expectation maximisation over at least two blocking rules; the prior comes from Tier 1 links. No labels are needed.

**Decision bands** (initial values, tuned on the truth set):

| Band | Condition | Outcome |
| --- | --- | --- |
| Auto-link | Probability ≥ 0.95, identical DOB, no constraint flag | Linked |
| Review | Probability 0.70 to 0.95, or a DOB that differs only by a swap or one part | Human review queue |
| No link | Probability < 0.70, or a constraint that forbids the link | Kept apart |

### Constraints: never auto-linked

- DOBs that are not identical. A swap or a one-part difference can only be linked by a person.
- Pairs that are each other's joint holder or co-borrower. They go to `household_links`. Most refs don't resolve (D-12), so a pair sharing an address or phone with a different first name, or flagged joint or co-borrower on either side, goes to review.
- A source key with conflicting DOBs (`dob_conflict`).
- Name suffix conflict (Jr vs Sr).
- Possible twins: same DOB, surname and postal code, different first name. Sent to review.
- Case-link alarm: Splink puts an account under golden ID X, but its collections case points to Y. Sent to review.
- Saved human decisions (must-link and cannot-link) override every score.

### Tier 3: human review

The reviewer sees both records side by side, with the probability and each field's weight. The LLM receives only comparison results (for example "surname exact, first initial matches, DOB exact, phone differs"), never raw PII, and writes an explanation with a suggestion. The reviewer chooses link, no link or unsure; the decision is logged with their name and saved as a constraint for every later run.

The queue is ordered by open collections case first, then total exposure, then uncertainty. Pairs nobody has reviewed stay unlinked, with low confidence and the identity check switched on. This is the safe default, and it lets the demo review only a prioritised slice.

### Clustering and stable IDs

- Clusters are connected components over auto-links and approved links only.
- A cluster with more than 10 records, or with two different ID hashes, sends its weakest links back to review.
- A cluster that overlaps an existing golden ID keeps it. On a merge, the oldest ID survives and the others go to `golden_alias`. On a split, the largest part keeps the ID.

**Crosswalk columns:** `source_system`, `source_id`, `golden_customer_id`, `match_probability`, `rule`, `match_weights`, `reviewed_by`, `valid_from`, `valid_to`.

### Identity confidence

| Tier | When | Effect |
| --- | --- | --- |
| High | Deterministic, human-approved, or probability ≥ 0.95 with no constraint flags | Normal servicing |
| Medium | Auto-linked on thin evidence (for example a card initial plus a masked phone), or merged or split in the last run | `verify_before_disclosure = true` |
| Low | In review, unreviewed, or single-source and unverified | `verify_before_disclosure = true` |

When `verify_before_disclosure` is true, the agent desktop and Layer 4 must verify identity before discussing any debt.

Thin card records (the `R. MITCHELL` layout plus a masked phone) are expected to cause most matching errors, which is why they are capped at medium confidence. Twins that cannot be told apart stay separate, the safe direction.

## 8. Survivorship: which value the golden record keeps

When merged records disagree, recent values win for contact details and the safest value wins for anything that protects the customer. Each choice is recorded in `survivorship_source`.

| Field | Rule |
| --- | --- |
| Name, phone, email, address | Most recently updated CRM record; product values only when CRM has none |
| DOB | The CRM value. Records with different DOBs can only be merged by a person, so the reviewer has already confirmed it. |
| Consent flags | Most recent `consent_last_updated_ts` when identity confidence is high; the most restrictive value otherwise |
| Restrictions: cease communication, internal do-not-call, deceased, bankruptcy, consumer proposal, legal representative, credit counselling | Most restrictive wins: true if any merged record is true |
| Care flag: hardship | True if any merged account or case is true; used only to protect the customer. Vulnerability is a protected attribute in the release and lives only in `restricted` (D-37) |
| `best_reachable_phone` | The most recent number with a right-party contact (`rpc_flag`), kept beside the CRM primary phone |

**Conditions on `best_reachable_phone`.** `contact_history` stores only `phone_number_hash`. CRM phones can be hashed the same way: the hash is an unsalted MD5 of the 10-digit national number (D-06). The field is filled only for numbers the customer has consented to be called on (`consent_call`, `consent_call_mobile`, not `internal_do_not_call_flag`). If the hash can't be matched, the field stays empty rather than guessed.

## 9. Measuring match quality

We report precision and recall per source every run, each from the truth set that actually covers that source. Truth links are used only to evaluate, never to create links.

| What is measured | Truth set | Notes |
| --- | --- | --- |
| Cards and loans to CRM | `collections_cases.coll_customer_ref` + `account_ids` | Covers customers with a case, so it leans towards delinquent customers |
| Deposits to CRM | Deposit (`CHQ-`) accounts listed in case `account_ids`, plus `account_monthly_snapshot.crm_customer_id` (latest quarter, current CRM IDs only) | The snapshot covers all products but is noisy: 20% of accounts change CRM ID across quarters (D-03) |
| CRM duplicates | `customers.duplicate_of_crm_id` | Incomplete: it gives known duplicates, so it measures recall, not precision |
| Look-alikes | Hard-mode set: joint holders, co-borrowers, Jr/Sr pairs and possible twins | Every pair here should stay apart; any link is a false merge |

**Scored at the cluster level.** `coll_customer_ref` was copied from CRM when the case opened and may point to a duplicate CRM record. A link counts as correct when the account lands in the same golden cluster as that CRM ID, not when it matches the exact ID.

**Sanity check.** About 1.02M CRM rows should collapse to about 1M golden IDs.

**Fairness audit.** In the restricted zone, a fairness reviewer compares link, single-record and review rates across protected groups. A gap triggers an investigation of the cleaning rules or comparisons; it never changes an individual decision.

The truth-set bias towards delinquent customers is printed beside every metric.

## 10. Data quality and anomaly detection

Six families of checks run on every load; schema, key and matching-health failures stop the release, the rest raise warnings. All thresholds below are initial values to tune on the first loads.

| Check family | Examples | Severity |
| --- | --- | --- |
| Contract and schema | Columns and types against the contract (catches the `channel_v2` rename); primary-key uniqueness; referential integrity along the dictionary's relationships; enums against `code_lookups`; postal and phone formats | Block for schema and keys; warn for formats |
| Volume and completeness | Row counts and null rate per column, compared with the same weekday or previous month-end rather than fixed limits | Warn |
| Business rules | `utilisation` ≈ balance ÷ limit; `available_credit` = limit − balance; `dpd_bucket` consistent with `dpd`; loan `total_outstanding` = principal + interest + fees; first character of `payment_history_24m` consistent with DPD; a kept promise has a matched payment (`ptp_id_matched`) | Warn (tolerance ±1%, to confirm) |
| Cross-table reconciliation | Case `total_overdue` = sum of account `past_due_amount`; case `current_dpd` = highest account DPD; latest `account_monthly_snapshot` agrees with the account tables | Warn |
| Distribution drift | Population stability index (PSI) on balances, DPD and enum frequencies against the previous run; PSI > 0.25 raises an alert | Warn |
| Matching health | Merge rate, cluster sizes, golden-ID churn and review-queue size against the previous run; a spike usually means a broken cleaning rule | Block on a spike |

### Incident handling

A spike inside an `ops_events` window is labelled a known incident, not a pipeline failure. For INC-0914, where app payments posted a day late and late fees were charged in error, Layer 1 records the effect without overwriting source facts:

- `ptp_status` stays as received. We add `ptp_status_adjusted`, `adjustment_reason` and `incident_id` for promises marked broken during the outage that have a payment posted the next day.
- Late fees charged on affected accounts inside the window are flagged with the same `incident_id`.
- Affected customers get `incident_affected = true`. Layer 1 takes no customer action itself; Layer 4 uses the flag (for example to pause broken-promise follow-ups) with a person able to review.
- The business owner approves the adjustment rule before it is applied, and Layer 2 answers covering the affected period carry a note.

### AI root cause

When a drift check fires, the LLM matches it to `reference_documents` release notes (`doc_type = release_note`, `related_table`) and to `ops_events`. It drafts a plain-English alert and a proposed fix, for example mapping `channel_v2` back to `channel`. An engineer approves the fix. The numbers always come from the checks; the LLM only writes the words.

## 11. Data contract: gold.c360_customer

One row per real customer, rebuilt daily, with identity, confidence, products, contact rules and lineage. Version 0.1 (draft).

| Item | Value |
| --- | --- |
| Business owner | Head of Collections Operations (name to confirm) |
| Technical steward | Collections data engineering (our team for the hackathon) |
| Grain | One row per `golden_customer_id`; history kept with `valid_from` / `valid_to` |
| Refresh | Daily batch after the nightly source extracts; event-lane lookups through the crosswalk within minutes |
| Consumers | Agent desktop, Layer 2 question answering, Layer 3 feature views |

**Schema**

| Column group | Columns and type | Source | Sensitivity |
| --- | --- | --- | --- |
| Identity | `golden_customer_id` string PK; `crm_customer_ids` array; `source_keys` array; `match_probability` decimal | Crosswalk | Internal |
| Identity confidence | `identity_confidence` enum (high, medium, low); `verify_before_disclosure` boolean | Identity ledger | Internal |
| Name and contact | `first_name`, `last_name`, `primary_phone_e164`, `best_reachable_phone`, `email` string | `customers`, `contact_history` | Direct PII, masked by role |
| Identity only | `date_of_birth` date | `customers` | Direct PII; matching only, never a feature |
| Location | `postal_code`, `province_code`, `time_zone` string | `customers` | Quasi-identifier |
| Products | `product_count` int; `has_card`, `has_loan`, `has_deposit` boolean; `total_credit_exposure` decimal | Account tables | Internal |
| Delinquency | `max_dpd_current` int; `open_case_id` string; `current_bucket` enum | Accounts, `collections_cases` | Internal |
| Consent | `consent_call`, `consent_call_mobile`, `consent_autodialer`, `consent_sms`, `consent_email`, `consent_push` boolean | `customers` | Internal |
| Contact rules | `permitted_call_start_local`, `permitted_call_end_local` string; `sunday_contact_permitted`, `voicemail_permitted`, `contact_at_work_permitted` boolean | `customers` | Internal |
| Restrictions | `cease_communication_flag`, `internal_do_not_call_flag`, `deceased_flag`, `bankruptcy_flag`, `consumer_proposal_flag`, `legal_representative_flag`, `credit_counselling_agency_flag` boolean | `customers` | Internal |
| Care and incidents | `hardship_flag`, `incident_affected` boolean | `collections_cases`, account tables, `ops_events` | Restricted; protective use only |
| Lineage | `source_systems` array; `survivorship_source` json; `_loaded_at` timestamp; `_pipeline_run_id` string | Pipeline | Internal |

**Excluded from gold:** the 12 columns tagged protected (including `gender_code`, `marital_status`, `citizenship_status`, `household_size`, `dependants_count`, `newcomer_*`, `accessibility_*`, `vulnerability_type`, `ccb_deposit_flag`), every other vulnerability field (`vulnerability_flag`, `collections_cases.vulnerable_customer_flag`, `contact_history.vulnerability_disclosed_flag`; D-37), plus `age` and `age_band`, and the AML fields (`aml_risk_rating`, `pep_flag`). `national_dncl_flag` and `casl_express_consent_email` are marketing-only and never gate collections contact.

**Quality rules** (checked every run; a blocking rule stops the release)

| # | Rule | Severity |
| --- | --- | --- |
| 1 | `golden_customer_id` is unique, and each `source_system` + `source_id` maps to exactly one current golden ID | Block |
| 2 | Every current CRM row and every card, loan and deposit customer key appears in the crosswalk (linked, in review or single) | Block |
| 3 | No auto-link joins records with non-identical DOBs; differing DOBs appear only through a logged human approval | Block |
| 4 | Each restriction flag equals the OR of the same flag across merged CRM records | Block |
| 5 | Precision per source, on that source's truth set, is at or above the agreed floor (to confirm) | Block |
| 6 | No auto-link breaks a constraint (joint holder, co-borrower, Jr/Sr) | Block |
| 7 | Every golden ID has an `identity_confidence`; when it is not high, `verify_before_disclosure` is true | Block |
| 8 | `postal_code` matches A1A 1A1 and its FSA exists in `geo_reference`; phones are valid E.164 | Warn |
| 9 | `_loaded_at` is within one day of the source snapshot date | Warn |

**Allowed uses:** collections servicing and contact planning, the agent's customer view, aggregate analysis in Layer 2, and Layer 3 features through approved views only.

**Not allowed:** marketing, credit underwriting, any use of protected attributes, and using care flags to justify adverse action.

## 12. Where AI is used and who checks it

AI speeds up building, matching and explaining, but no AI output becomes data until a test passes or a person approves it.

| Task | What the AI does | Gate |
| --- | --- | --- |
| Source-to-target mapping | An LLM reads the 1,421-column dictionary and proposes mappings to the C360 schema, each with a confidence and a reason | A reviewer approves each mapping; approved mappings are saved as versioned YAML |
| Transformation code | The LLM drafts silver SQL, such as the per-source name and DOB parsers | Contract tests pass and a teammate reviews the code |
| Sensitivity tagging | The dictionary tags 70 of 1,421 columns; the LLM proposes tags for untagged columns that may be sensitive or act as proxies, such as `salutation` or `preferred_language` | A data steward confirms; tags drive masking and exclusion |
| Identity matching | Splink, a statistical model, learns match weights without labels and scores every candidate pair | Thresholds tuned on the truth set; the grey zone goes to a person |
| Grey-zone explanations | The LLM explains a pair from comparison results and weights only, never raw PII | The reviewer decides, and the decision is logged |
| Drift root cause | A deterministic check finds the change; the LLM links it to release notes and incidents and proposes a fix | An engineer approves the fix |
| DQ report narrative | The LLM writes the summary of the quality and match report | Every number comes from the checks |
| Text redaction | Microsoft Presidio (named-entity recognition) removes PII from notes, SMS replies and transcripts | Runs before any LLM sees text; a sample is spot-checked |

**Why this split.** LLMs are good at reading documentation, drafting code and explaining. They are weak at exact, repeatable decisions, so those stay with tests, statistics and people.

## 13. Governance and security

Access follows roles, every identity change is logged, and protected attributes never leave the restricted zone.

| Role | Can see | Cannot see |
| --- | --- | --- |
| Collections agent | Own cases in the agent desktop; masked identifiers; the verify gate | Other customers, raw IDs, protected attributes |
| Analyst (Layer 2) | Aggregates and gold tables through SQL | Direct PII, protected attributes |
| Data engineer | Pipeline tables, quarantine, DQ report | Raw PII in gold (masked) |
| Identity reviewer | The two records in a review pair | Anything outside the pair |
| Data steward | Sensitivity tags, contract, mapping approvals | Customer-level protected data |
| Fairness reviewer | Restricted zone, aggregated by group | Individual decisions to change |

**Where a person reviews**

- Every grey-zone match and every DOB difference (Tier 3)
- Every source-to-target mapping, sensitivity tag and drift fix before it applies
- The incident adjustment rule, approved by the business owner
- Downstream customer actions that use `incident_affected` or care flags (Layer 4)

**Fairness and hardship**

- Protected attributes, `age`, `age_band` and AML fields are excluded from gold and from every Layer 3 view.
- The hardship flag travels with the golden record but only triggers protective handling, such as specialist routing; it never justifies adverse action. Vulnerability fields are protected attributes and stay in `restricted` (D-37).
- The matching itself is audited for unequal link and review rates across groups (section 9).

**Privacy**

- Presidio redacts free text before any LLM reads it, and LLMs receive comparison results, never raw PII.
- `contact_history.phone_number_hash` is an unsalted MD5 of a 10-digit number and can be reversed, so it is handled as direct PII (D-06).
- `verify_before_disclosure` stops an agent discussing debt with someone whose identity link is not high-confidence.
- The audit log records every merge, split, review decision, mapping approval and incident adjustment, with who and when.

## 14. Scalability: batch and real time

The data is a daily snapshot, so almost everything runs as a nightly batch; only the event lane works in minutes, and it never reruns matching.

| Runs daily in batch | Runs in the event lane (optional, minutes) |
| --- | --- |
| Ingest, quality gate, silver cleaning | Replayed notes and call transcripts |
| Identity resolution and golden ID assignment | Golden ID lookup through the crosswalk |
| Gold tables, history, DQ report | Redaction before the text is stored |
| Retrieval layer refresh (optional) | |

Human review runs on demand, and its decisions are applied in the next batch.

**Compute budget.** The build machine has 10 threads, 15 GB of RAM (4 to 6 GB free for the pipeline) and no GPU; none is needed (D-17). DuckDB runs with `memory_limit = 4GB`, `threads = 8` and a spill directory. To fit:

- History Parquet is read in place and processed year by year.
- Blocking caps on common keys keep Splink to roughly 10 to 20M candidate pairs.
- No local LLM; LLM calls go to an API and only see redacted text or comparison results.

**Scale path.** The same SQL runs on a cloud warehouse, Splink runs on Spark, and Dagster schedules both, when volume outgrows one machine.

**Confirmed from the data** (see `layer1/docs/decisions.md`): `national_id_hash` fill 93.6% (D-01); deposit CIF = bureau ref after padding (D-02); card phone mask (D-04); one SCD2 version per customer (D-05); `phone_number_hash` = unsalted MD5 of the 10-digit number (D-06).

`account_monthly_snapshot.crm_customer_id` is filled but noisy, so it is an evaluation set only (D-03).

**Still to confirm:** business-rule tolerances and the precision floor; the business owner's name.
