# Data quality report: Layer 1 (Data Product Factory)

Generated 2026-10-03 by `python -m layer1.quality.report` from the built database; every count is queried at
generation time. Source snapshot 28 Sep 2026; full release (31 tables, about 1M customers).

## Summary

- 30 issues: 4 that the operations team listed in `ops_events`, and **26 that `ops_events` does
  not list**, found by profiling and checks.
- Source facts are never overwritten. Every correction is a new column beside the original, with a reason, and every
  fix that changes meaning was approved by a named role before it applied.
- Automated checks: the latest run of `gold.dq_report` has 86 checks, 11 warnings and 0
  blocking failures (run `743b88be`). The data-contract rules in `deliverables/data_contract_c360.yaml` all pass.
- Scale: 3,487,885 source customer keys were matched into 1,060,086 golden customers; 10,575 individual
  values were quarantined with their rule and reason.

## Issues listed in ops_events

| # | Area | Issue | Records affected | How it was handled | Decision | Event |
|---|---|---|---:|---|---|---|
| 1 | Schema drift | REL-0926: `contact_history.channel` renamed to `channel_v2` from 26 Sep; `channel` empty on most new rows | 19,463 | Caught by the null-rate and drift checks; `gold.contact_timeline.channel_resolved = coalesce(channel_v2, channel)` after an engineer approved the fix (REL-0926-channel); source columns kept | D-29, D-35 | REL-0926 |
| 2 | Incident | INC-0914: app payments posted a day late and late fees were charged in error (contacts tagged with the incident) | 959 | `gold.incident_effects` and `incident_affected` on the golden record; promises broken on the outage day but paid next day get `ptp_status_adjusted` (approved rule INC-0914-ptp; 1 promise); source status kept | D-24 | INC-0914 |
| 3 | Incident | INC-0923: dialer outage on 23-24 Sep, call capacity down about 60% (contacts tagged) | 8,121 | Labelled as a known incident in drift and volume checks so it is not mistaken for a pipeline fault; no data change | D-30 | INC-0923 |
| 4 | Source feed | DFI-0812: bureau file converted with a shifted column for some rows (rows in the August bureau refresh) | 4,443 | Checked: the identity fields used for matching (name, DOB, postal code) parse on every bureau row (0 quarantined); score fields are outside Layer 1 and flagged to Layers 2 and 3 | D-30 | DFI-0812 |

## Issues found by Layer 1 that ops_events does not list

| # | Area | Issue | Records affected | How it was handled | Decision |
|---|---|---|---:|---|---|
| 1 | Duplicates | CRM holds two records for the same person (shared national ID hash) | 18,426 | Rule D1 merges pairs with identical DOBs; pairs with differing DOBs go to human review; golden IDs count people, not CRM rows | D-01 |
| 2 | Duplicates | CRM's own duplicate pointer (`duplicate_of_crm_id`) is incomplete and sometimes joins different DOBs | 5,941 | Used only to measure recall (98.99%), never to link | D-01 |
| 3 | Deleted records | CRM rows marked deleted (CDC 'D') | 2,115 | Excluded from matching and gold; kept in bronze | D-05 |
| 4 | Format | Deposit customer numbers (CIF) lost their leading zeros (7 or 8 digits instead of 10) | 156,837 | Padded to 10 digits in silver (`pad_cif`), raw value kept; then every CIF matches its bureau record | D-02 |
| 5 | Format | CRM `dob_raw` mixes day-first and month-first dates; day and month cannot be told apart | 128,980 | Never parsed; the clean ISO `date_of_birth` is used (rule: day and month are never guessed) | D-09 |
| 6 | Format | Quarter-end `month_end_date` in four formats, including both slash orders | 1,255,152 | Parsed into `gold.account_monthly_snapshot.month_end_date`; a month-end day (28-31) is never a month, so only one reading is valid; every row parses; `month_end_date_format` kept | D-38 |
| 7 | Format | `collections_cases.account_ids` holds a bare ID instead of a JSON array | 18,314 | Parser accepts both forms (`parse_account_ids`) | D-13 |
| 8 | Masking | Card phone numbers masked (first 6 and last 2 digits visible) | 384,422 | Compared on the visible digits only; cards matched on a masked phone and an initial get medium confidence at most | D-04 |
| 9 | Invalid values | Phone numbers with the wrong number of digits | 4,172 | Quarantined with the rule and reason; the record continues without the phone | D-18 |
| 10 | Invalid values | Postal codes whose area (FSA) is not in `geo_reference` (cards and loans) | 6,403 | Quarantined; postal code left out of matching (less evidence, never wrong evidence) | D-22 |
| 11 | Missing values | Days past due (`dpd`) empty on card and loan accounts | 84,913 | Flagged `dpd_missing`; never filled from `cycles_delinquent` (cycles, not days) | D-38 |
| 12 | Missing values | CRM customers with no date of birth | 5,018 | Cannot auto-link (identical DOBs required); they wait in review at low confidence, so agents verify first; flagged in the fairness audit | D-34 |
| 13 | Missing values | Bureau records with no name or DOB (no-hit and thin files) | 58,669 | Deposit-to-bureau links without a DOB go to review rather than auto-link | D-02 |
| 14 | Inconsistent values | Account `dpd_bucket` disagrees with `dpd` (cards and loans) | 38,974 | `dpd_bucket_recomputed` added beside the source bucket | D-38 |
| 15 | Inconsistent values | Case `current_bucket` disagrees with `current_dpd` | 3,807 | `current_bucket_recomputed` added beside the source bucket | D-38 |
| 16 | Inconsistent values | Monthly snapshot `dpd_bucket` disagrees with `dpd` | 461,396 | `dpd_bucket_recomputed` added beside the source bucket | D-38 |
| 17 | Inconsistent values | A product customer key whose accounts carry different DOBs | 1,104 | DOB left empty and `dob_conflict` set, so the key can only be linked by a person | D-11 |
| 18 | Reconciliation | Open case `total_overdue` does not equal the sum of its accounts' past-due amounts | 1,972 | Reported as a warning for the collections data owner; no automatic change | D-30 |
| 19 | Broken references | Collections cases whose customer reference matches no current CRM record | 11,137 | Kept, but excluded from customer counts and from the truth sets; flagged in the DQ report | D-13 |
| 20 | Broken references | Contacts, promises and notes referencing a CRM ID that does not exist | 130,540 | Rows kept with no golden ID; reported as a referential-integrity warning | D-13 |
| 21 | Broken references | Monthly snapshot `crm_customer_id` values that do not exist in `customers` | 249,817 | The snapshot's CRM ID is used only to evaluate matching; golden IDs come from the account | D-03 |
| 22 | Broken references | Joint-holder and co-borrower references that point to no record | 208,473 | Resolved references become household links (never identity links); look-alike pairs go to review | D-12 |
| 23 | Truth-set noise | Case-listed accounts linked to a different golden customer than the case's CRM reference (mostly the case pointing at another person: different name and DOB) | 646 | Measured, not corrected; on clean pairs (case and snapshot agree) every remaining mismatch is the same person's other CRM record | D-30, D-32 |
| 24 | Privacy | Agent notes and transcript turns containing names, phones, emails, postal codes or account numbers | 176,815 | Redacted with Presidio plus Canadian patterns before any other layer reads text; spot check finds 0 left | D-36 |
| 25 | Privacy | `contact_history.phone_number_hash` is an unsalted MD5 of the phone number (reversible) | 825,236 | Treated as direct PII: left out of gold and every access view | D-06 |
| 26 | Protected data | Vulnerability fields on customers, cases and contacts (protected attribute in the release) | 133,877 | Moved out of gold to the restricted schema (fairness audit only); a test fails if they return | D-37 |

## How issues are caught on every run

| Check family | What it does | Severity |
|---|---|---|
| Contract and schema | Columns against the contract, primary keys, references to CRM, code lists, formats | Block on schema and keys; warn on formats |
| Volume and completeness | Row counts against the previous run; null rate by day (caught the channel rename) | Warn |
| Business rules | Utilisation, available credit, DPD bucket, payment history, loan totals, missing DPD | Warn (±1% tolerance, to confirm) |
| Reconciliation | Case totals against account past-due amounts and DPD | Warn |
| Distribution drift | PSI against the previous run and last 3 days against the prior 4 weeks; PSI > 0.25 alerts | Warn |
| Matching health | Merge rate, cluster sizes, golden-ID churn, review queue, precision and recall | Block on a spike |

A finding inside an `ops_events` window on an affected table is labelled with that event instead of being treated as
a pipeline fault. Results of every run are in `gold.dq_report`; the plain-English alert text is in `gold.dq_narrative`.
Decisions referenced above are in `layer1/docs/decisions.md`.
