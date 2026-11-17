# Layer 1 decisions log

What the real data showed, and what we decided because of it. Each entry gives the evidence (counts from profiling the
release of 28 Sep 2026), the decision, and the section of `layer1_architecture.md` it changed. Newest entries go at the
bottom of each group. Counts were taken on the full release; nothing here shows customer-level values.

Profiled on 2026-10-03 from `maple_collections_release.zip` (31 tables, 7.7 GB unzipped).

## Source profile for `identity_input`

| Source | Rows | Distinct customer keys | Name fill | DOB fill | Phone fill | Postal fill |
|---|---:|---:|---:|---:|---:|---:|
| `customers` (current: `is_deleted = false`) | 1,017,885 | 1,017,885 | 100% | 99.51% | 98.51% (E.164) | 99.00% |
| `card_accounts` | 660,000 | 550,000 `src_customer_ref` | 100% | 100% | 97.03% | 98.99% |
| `loan_accounts` | 404,500 | 340,000 `src_customer_ref` | 100% | 99.00% | 97.97% | 98.98% |
| `deposit_accounts` | 780,000 | 644,202 raw CIFs, 580,000 after padding | 100% | 97.98% | 96.03% | 99.01% |
| `external` (bureau) | 1,000,000 | 1,000,000 `bank_subject_ref` | 96.05% | 96.05% | none | 95.08% (address string) |

About 3.49M source records enter `identity_input`. Card, loan and deposit keys use disjoint formats (`AA9999999`,
`A99999999`, digits) and never collide.

## Open items from CLAUDE.md

### D-01 national_id_hash fill rate
- **Evidence:** 93.60% of current CRM rows have `national_id_hash` (`national_id_present_flag` agrees). 18,426 hashes are
  shared, every one by exactly two rows. 18,255 of those pairs have identical `date_of_birth`, 171 do not.
  `duplicate_of_crm_id` is filled on 5,941 current rows: 5,497 coincide with a shared-hash pair, 431 involve a row
  with no hash (pairs D1 cannot find), and 57 point between rows with different DOBs.
- **Decision:** keep rule D1 as designed. 18,255 pairs merge deterministically; 171 go to review.
  `duplicate_of_crm_id` stays an evaluation set for recall (§9), not a link rule: its 431 no-hash pairs must be found
  by Splink, and its 57 differing-DOB pairs show CRM's own pointer is not always safe. 1,017,885 − 18,426 =
  999,459 golden IDs from CRM alone, which matches the ~1M people in the README, so the sanity check in §9 holds.
- **Architecture:** no change to §7; §14 open item closed.

### D-02 Deposit CIF equals external.bank_subject_ref
- **Evidence:** deposit `src_customer_ref` has lost its leading zeros: 623,163 rows are 10 digits, 142,665 are 8 and
  14,172 are 7. Raw overlap with `external.bank_subject_ref` is 495,420 of 644,202 (76.9%). After `lpad(cif, 10, '0')`
  the 644,202 raw values collapse to 580,000 CIFs and **all 580,000** are in `external`. DOBs agree on 99.70% of joined
  rows. `bureau_history.bank_subject_ref` uses the same 10-digit CIF. `joint_holder_ref` is always 10 digits.
- **Decision:** silver pads every CIF to 10 digits (`pad_cif`) and keeps the raw value beside it (rule 3). D2 is a
  deterministic link on the padded CIF. The DOB still has to be identical; the 0.3% that differ go to review.
  420,000 bureau subjects have no deposit account and reach CRM only through Splink.
- **Architecture:** §2 "Other links", §6 parser table, §7 D2.

### D-03 account_monthly_snapshot.crm_customer_id
- **Evidence:** filled on all 12,569,692 rows and 1,635,858 accounts (cards `C-`, loans `L-`, deposits `CHQ-`, `SAV-`,
  `TFS-`, `USD-`). It covers 585,978 of 660,000 card accounts, 361,732 of 404,500 loans and 688,148 of 780,000 deposit
  accounts. But it is noisy: 97.2% of rows point to a current CRM ID, 249,817 distinct IDs it uses do not exist in
  `customers` at all, and 328,520 accounts (20%) carry more than one CRM ID across quarters.
- **Decision:** usable as an evaluation set for all three product sources, including deposits, but not as clean truth
  and never as a link rule (§9: truth links only evaluate). Per account we take the CRM ID from the latest quarter,
  only when it is a current CRM ID, and report its agreement with the case-based truth set beside every metric so its
  noise is visible. The 20% of accounts whose CRM ID changes are reported separately.
- **Architecture:** §9 truth-set table.

### D-04 Card phone mask pattern
- **Evidence:** `cardholder_phone_raw` is 58.2% masked as `999999xxx99` (first 6 and last 2 digits visible, 3 digits
  hidden), 38.8% a full unmasked 10-digit number, and 3.0% null.
- **Decision:** `card_phone_partial` stores `phone_first6`, `phone_last2` and `phone_masked`. Unmasked card phones become
  normal E.164 values. Masked phones compare only on first 6 + last 2 ("consistent with the mask" level in Splink),
  and cannot use the "last 7 digits" blocking rule. A card record whose phone evidence is only a mask match stays
  capped at medium confidence (§7).
- **Architecture:** §2 source table, §6 parser table, §7 comparison and blocking tables.

### D-05 SCD2 versions in customers
- **Evidence:** 1,020,000 rows and 1,020,000 distinct `crm_customer_id`: one version per ID. Every `effective_to_ts` is
  9999-12-31. `cdc_operation` is U 916,466, I 101,419, D 2,115; `is_deleted = true` on exactly the 2,115 D rows.
- **Decision:** the current-row filter stays (`effective_to_ts = 9999-12-31 and is_deleted = false`), but in practice
  only `is_deleted` removes rows. There is no CRM history to replay, so `gold.c360_customer_history` starts from our own
  first run. The 2,115 deleted rows are excluded from matching but kept in bronze.
- **Architecture:** §2 bullet on SCD2.

### D-06 phone_number_hash method
- **Evidence:** `contact_history.phone_number_hash` is 32 hex characters, filled on about 30% of contacts. In a sample
  of 20,000 distinct (customer, hash) pairs, 18,090 equal `md5` of the 10-digit national number of the CRM primary
  phone (no `+1`, no salt), 2 match the secondary phone, and no other variant (SHA-1/256/512, 11 digits, E.164) matched.
- **Decision:** `best_reachable_phone` is buildable: hash each CRM phone with the same method and join. Only consented,
  non-DNC numbers qualify (§8). Because an unsalted MD5 of a 10-digit number can be reversed by enumeration,
  `phone_number_hash` is treated as **direct PII** for masking and role access, not as a safe pseudonym.
- **Architecture:** §8 conditions on `best_reachable_phone`, §13 privacy.

## Other places where the data contradicts the design

### D-07 Card names come in three layouts
- **Evidence:** `cardholder_name_raw` shapes: `R. MITCHELL` 49.4%, `RYAN MITCHELL` 39.3%, `MITCHELL/RYAN` 10.0%, plus
  hyphenated and multi-token variants (20 shapes in all). Token order was checked against the CRM name vocabularies:
  in `A B`, the first token is a CRM first name 95.5% of the time; in `A/B`, the first token is a CRM surname 97.3%.
- **Decision:** `parse_card_name` handles all three: `I. LAST` gives an initial only; `FIRST LAST` and `LAST/FIRST`
  give a full first name. Only the initial-only layout counts as a "thin" card record.
- **Architecture:** §2 source table, §6 parser table, §7 confidence note on thin card records.

### D-08 Loan names: mixed case, accents, some reversed
- **Evidence:** 73% `Ryan Mitchell`, 20% `RYAN MITCHELL`, with accented letters (`é`) and 1,119 shapes. In two-token
  names, the first token is a CRM first name 81.6% and a CRM surname 31.9% of the time, so a minority look reversed.
- **Decision:** `norm_name` upper-cases and strips accents (`strip_accents`) and punctuation. Splink gets a name-swap
  comparison level (first and last reversed) rather than the parser guessing the order.
- **Architecture:** §6 rules for every source, §7 comparison table.

### D-09 CRM dob_raw is ambiguous; use date_of_birth only
- **Evidence:** `dob_raw` mixes `YYYY-MM-DD` (609,542), `99/99/9999` (354,791) and `Mon DD YYYY` (50,641). Against
  `date_of_birth`, the slash values are DD/MM in 207,657 rows and MM/DD in 158,346 rows; 128,735 rows have day and
  month both ≤ 12 and different, so their order cannot be told from the value. `date_of_birth` is ISO in every filled
  row (99.51%), and equals `dob_raw` whenever `dob_raw` is ISO.
- **Decision:** silver reads `date_of_birth` only and never parses `dob_raw` (rule 8). `dob_raw` stays in bronze.
- **Architecture:** §2 source table, §6 parser table.

### D-10 Product DOB formats confirmed
- **Evidence:** card `DD-MM-YYYY`: the month field is never > 12 (the day field is > 12 in 398,847 rows). Loan
  `YYYY/MM/DD`: month never > 12. Deposit `YYYYMMDD`: month never > 12. Bureau ISO.
- **Decision:** parse each with its fixed format; anything else goes to quarantine.

### D-11 One source key can carry conflicting DOBs
- **Evidence:** after parsing (silver, full data), keys whose accounts carry more than one distinct DOB: cards 337,
  loans 171, deposits 596 (after CIF padding). The first profiling pass reported 1,379 loans and 5,836 deposits
  because it counted a missing DOB as a distinct value; the silver numbers are the correct ones.
- **Decision:** `identity_input` has one row per source key. When the key's DOBs disagree, `dob` is null,
  `dob_conflict = true`, the distinct values are kept in `dob_variants`, and the key can never auto-link (rule 1
  requires identical DOBs), so it goes to review.
- **Architecture:** §6 output columns.

### D-12 Joint holder and co-borrower refs rarely resolve
- **Evidence:** `joint_account_flag` true on 140,521 deposit rows and `co_borrower_flag` true on 69,651 loan rows, but
  only 0.62% of `joint_holder_ref` values match a deposit CIF (1.06% match a bureau ref), and 0.30% of
  `co_borrower_ref` values match a loan `src_customer_ref`.
- **Decision:** refs that resolve become `household_links` and a hard never-auto-link constraint, as designed. Because
  most do not resolve, the look-alike constraint matters more: a pair sharing an address or phone but with a different
  first name, or flagged joint / co-borrower on either side, goes to review instead of auto-linking.
- **Architecture:** §2 "Other links", §7 constraints.

### D-13 collections_cases account_ids and coll_customer_ref
- **Evidence:** about 5% of `account_ids` values are a bare ID (`C-…`) rather than a JSON array. Cases list card
  (`C-`), loan (`L-`) **and deposit (`CHQ-`) accounts**. 96.97% of `coll_customer_ref` values are current CRM IDs.
- **Decision:** `parse_account_ids` accepts both forms. Deposit links can be measured partly from cases, not only from
  a hand-labelled sample. Of the 11,137 case refs that are not current CRM IDs, 772 point to deleted CRM rows and
  10,365 are well-formed IDs that exist nowhere in `customers` (planted mismatched IDs). Both are left out of the
  truth set and counted in the DQ report as referential-integrity warnings.
- **Architecture:** §2 "Other links", §9 truth-set table.

### D-14 More payment-account evidence than D3 assumed
- **Evidence:** loan `pad_source_account`: 123,535 deposit account IDs (`CHQ-999999`), 179,502 literal `EXTERNAL`,
  101,463 null. Card `autopay_source_account`: 41,518 deposit account IDs, 123,047 `EXTERNAL`.
- **Decision:** D3 covers both loan PAD and card autopay; both are Splink evidence only, never a link on their own,
  and still need identical DOBs (the deposit may be joint).
- **Architecture:** §7 Tier 1.

### D-15 CRM postal_code is already corrected
- **Evidence:** `postal_code` equals `postal_code_raw` without spaces except in 1,884 rows, where the raw value has
  letter/digit confusions (`AAA 9A9`, `A9A 9AA`, …) and `postal_code` holds a valid A1A 1A1.
- **Decision:** use `postal_code`; validate its FSA against `geo_reference` as designed.

### D-16 Product rows are one per account, some stale
- **Evidence:** card, loan and deposit tables have one row per account ID. 80–82% have `snapshot_date = 2026-09-28`;
  the rest carry older daily or month-end snapshots (3,464 distinct dates).
- **Decision:** keep `snapshot_date` on every silver row so survivorship and the DQ report can tell stale values.

### D-17 Machine and environment
- **Evidence:** 10 threads (not 20), 15 GB RAM with 4–6 GB free in practice, no discrete GPU. Python 3.11 was not
  installed (system 3.14 is too new for spaCy and Presidio).
- **Decision:** Python 3.11 through `uv`. DuckDB runs with `memory_limit = 4GB`, `threads = 8` and a spill directory.
  No GPU is needed: Splink is CPU statistics, Presidio runs CPU spaCy (`en_core_web_md`), and LLM calls go to an API.
- **Architecture:** §14 compute budget.

## Build decisions

### D-18 Field-level quarantine
- **Evidence:** parse failures are rare and field-specific (for example 1,884 malformed CRM raw postal codes, 9-digit
  phones), while the rest of the record is still good evidence.
- **Decision:** a value that fails its parser is nulled in silver and logged in `silver.quarantine` with the source,
  key, column, raw value, rule and reason. The record continues with that field missing. Only a record whose key is
  invalid (a CIF that is not 1 to 10 digits) is dropped from `identity_input`. Raw values in quarantine are PII and
  are never printed; report counts per rule.
- **Architecture:** §4 quality gate, §6.

### D-19 Parsers are DuckDB macros in one dbt file
- **Decision:** every parser is a DuckDB SQL macro in `dbt/macros/parsers.sql`, created by dbt `on-run-start`.
  `tests/test_parsers.py` runs the same file in an in-memory DuckDB, so tests and pipeline can't drift. The macros are
  stored in the database, so later Python steps (Splink, review app) can call them too.

### D-20 Bronze load results
- **Evidence:** all 31 tables loaded in 36 s; every row count equals the release README and every file's SHA-256
  equals `MANIFEST.csv`. Peak memory was 5.1 GB despite `memory_limit = 4GB` (CSV reader buffers and hashing sit
  outside DuckDB's limit). Bronze Parquet is 1.0 GB.
- **Decision:** acceptable for now; if free RAM drops below about 6 GB, load the large CSVs one at a time with
  `--tables` or lower `DUCKDB_THREADS`.

### D-21 Data dictionary size
- **Evidence:** `DATA_DICTIONARY.xlsx` lists 1,417 columns, not the 1,421 that CLAUDE.md and the architecture quote;
  31 tables and 48 relationships as expected.
- **Decision:** the exported CSVs in `docs/` are the reference.

### D-22 Card and loan postal codes outside geo_reference
- **Evidence:** 3,918 card and 2,485 loan postal codes are well formed (A1A 1A1) but their FSA is not in
  `geo_reference`. They spread over 920 and 845 distinct FSAs (about 4 records each), look like real Canadian FSAs, and
  none is a letter/digit typo of a known FSA. `geo_reference` has 656 FSAs (Canada has about 1,650). CRM and deposit
  postal codes never fall outside it.
- **Decision:** keep the rule from §6: the postal code is nulled in `identity_input` and logged in quarantine as
  `fsa_unknown`. This removes evidence rather than adding wrong evidence, which is the safe direction. The well-formed
  value stays in staging (`postal_shape`) if Splink later needs it. Reported in the DQ report as a warning.

### D-23 Silver results on full data
- **Evidence:** `identity_input` holds 3,487,885 keys (CRM 1,017,885, cards 550,000, loans 340,000, deposits 580,000,
  bureau 1,000,000). Quarantine: 4,172 CRM phones (9 digits), plus D-22. 297,147 card keys (54%) have only a masked
  phone and 44.96% have only a first initial. 1,492 of 140,521 joint-holder refs and 207 of 69,651 co-borrower refs
  resolve. All 165,053 PAD and autopay links resolve to a deposit account; 29,514 of those deposits are joint.
  The full silver build takes 1 min 42 s with a 5.2 GB peak.
- **Decision:** dbt-duckdb must get `memory_limit` and `temp_directory` as connect-time `config_options`, not
  `settings`: settings are re-applied per thread and DuckDB refuses to move the spill directory once used.

## Identity resolution and gold (phase 2)

### D-24 INC-0914 shows up as tagged contacts, not as late-posted promises
- **Evidence:** of 371 promises broken on the outage day (14 Sep), one has a credit posted to its account the next
  day, so the design's adjustment rule finds almost nothing. The incident's footprint is 959 contacts tagged
  `ops_incident_id = INC-0914` (887 customers) with broken-promise and dispute outcomes, which matches the incident
  report ("late-fee disputes about 3x baseline").
- **Decision:** `gold.incident_effects` records three signals: tagged contacts, broken promises paid the next day,
  and card statements due that day, paid the next day and carrying a fee. `incident_affected` is true for any of
  them. The promise adjustment (`ptp_status_adjusted`) is applied only after the business owner approves rule
  `INC-0914-ptp`; source `ptp_status` never changes.
- **Architecture:** §10 incident handling.

### D-25 Redaction of French text
- **Decision:** notes and transcript turns in French run through the English Presidio pipeline. Pattern
  recognizers (phones, emails, postal codes, SINs, account and CRM IDs) are language-independent; person-name
  detection is weaker in French. `silver.redaction_spot_check` counts any phone, email, postal code or CRM ID left
  in redacted text. A French spaCy model can be added if the spot check shows misses.

### D-26 Swapped first and last names are not look-alikes
- **Evidence:** on the 50K sample, 434 loan-to-CRM truth pairs were sent to review because the look-alike
  constraint fired: the loan record has first and last name reversed (D-08), so "first names differ" was true. They
  scored probability 1.0 with exact DOB, phone and postal code.
- **Decision:** the look-alike constraint excludes pairs whose names are swapped. Loan recall on the case truth set
  rose from 89.8% to 98.3% with precision unchanged at 100%. Test: `test_swapped_first_and_last_name_is_not_a_lookalike`.

### D-27 Identity confidence for single CRM records
- **Decision:** the architecture says "single-source and unverified" is low confidence. A golden customer with a CRM
  record counts as verified (CRM is the KYC'd master record), so a CRM-only customer can be high. A cluster with no
  CRM record (products or bureau only) is low, as is any cluster with a pair waiting in review.
- **Architecture:** §7 identity confidence.

### D-28 Clustering memory
- **Evidence:** the first full run built the cluster-limit input as 4.4M Python dicts and peaked near 4.7 GB with swap
  almost full.
- **Decision:** clustering now takes integer arrays straight from DuckDB (`enforce_cluster_limits_arrays`); results
  on the sample are identical. The dict version stays as a thin wrapper for the unit tests.

### D-29 Approvals recorded
- REL-0926 channel fix: approval requested by the Layer 1 owner on 2026-10-03; the approver's name is to be
  recorded in `dbt_project.yml` (`approved_fixes`) before it applies.
- INC-0914 promise adjustment: not approved (left to the business owner).

### D-30 Full-data identity results and DQ check corrections
- **Evidence:** 1,060,086 golden IDs from 3,487,885 keys. Precision on the case truth set 99.78% / 99.83% / 99.76%
  (cards / deposits / loans), recall 98.93% / 97.66% / 98.02%; CRM duplicate recall 98.99%; no joint holder or
  co-borrower pair merged. In 555 of about 650 "wrong" case links, the case's CRM record is a different person
  (different first name, surname and DOB) and the product's DOB matches the linked CRM record: truth-set noise from
  mismatched case references, not false merges. Precision on clean truth pairs is about 99.97%.
- **DQ corrections:** `payment_history_24m` starts with last month's status (`floor(dpd_m01 / 30)`, exact match on
  all rows), so the check compares with `dpd_m01`. "Every kept promise has a matched payment" cannot be checked:
  `transactions.ptp_id_matched` is filled on 9,516 rows because transactions cover deposit-side payments only; it is
  replaced by "a matched payment on a promise still marked broken" (0 found). `dpd_bucket` disagrees with `dpd` on
  11.6% of card and loan rows, mostly `1-30` with DPD 0, consistent with a cycle-based bucket lagging daily DPD; it
  stays a warning for the business owner to confirm.
- **Compute:** identity from saved Splink scores 46 s (6.9 GB peak: close other apps); gold 36 s; DQ 7 s.

### D-31 Checked against DATA_DICTIONARY.xlsx
- **Confirms D-02:** the dictionary's example deposit CIF `0007730115` is also its example `bank_subject_ref`; the CIF
  is 10 digits with leading zeros, which the release dropped. Padding is the documented form.
- **Confirms D-30:** `payment_history_24m` is "most recent first (0=current, 1=30, 2=60…, C=closed, X=no data)"; the
  first character is the latest completed month (`dpd_m01`). The check now skips `C` and `X`. `cycles_delinquent` is
  "billing cycles (NOT days)", supporting the cycle-versus-days reading of the `dpd_bucket` gap.
- **Confirms D-30:** `transactions` holds "deposit transactions, last 13 months only, for a customer sample", so it
  cannot prove every kept promise was paid.
- **Contradiction kept:** the dictionary lists `account_monthly_snapshot.crm_customer_id` as a foreign key to
  `customers`, but about 250K of its IDs do not exist there (D-03); it stays an evaluation set only.
- **Grain:** card, loan and deposit tables are documented as one row per account per `snapshot_date`, but this release
  has exactly one row per account. Gold keeps one row per account and tests uniqueness, so a future multi-snapshot
  extract would fail that test loudly instead of double-counting.
- **Relationships:** the 48 documented relationships include no link for `joint_holder_ref`, `co_borrower_ref` or
  card `autopay_source_account`; they are used as household links and Splink evidence only, as designed.

### D-32 Decision bands kept at 0.95 / 0.70 after tuning
- **Evidence:** `python -m layer1.identity.tune` sweeps auto-link 0.95-0.999 and review floor 0.50-0.90 on a consensus
  truth set (pairs where the collections case and the latest snapshot name the same CRM record; 268K product keys).
  Splink probabilities are sharply split: raising auto-link from 0.95 to 0.999 removes 20 of 400 "wrong" links and
  loses about 90 correct ones. Of the 26 wrong links left after clustering, every one points at the same person's
  other CRM record (same national ID hash, one record missing its DOB, so D1 sent the CRM pair to review).
- **Decision:** keep 0.95 (also the floor in CLAUDE.md rule 1) and 0.70. Results in `identity.threshold_sweep`.

### D-33 Review workflow
- **Decision:** reviews happen in the Streamlit app or in batches: `python -m layer1.identity.review_io export` writes a
  prioritised CSV to `lake/review/` (PII, git-ignored), a person fills decision / reviewer / reason, and `import`
  validates and saves them. Decisions apply from the next identity run; `tests/test_review_flow_e2e.py` proves on a
  copy of the dev database that "no_link" splits a cluster, "link" merges and keeps the oldest ID, and both are logged.
  The review queue on full data holds about 165K pairs; unreviewed pairs stay apart at low confidence (safe default).

### D-34 Fairness audit
- **Evidence and decision:** see `layer1/docs/fairness_audit.md`. No protected group differs from the overall rates by more
  than 0.5 points. The 5,018 customers without a DOB are almost all in review; recommended (not applied) to put them
  first in the queue or confirm DOB at next contact.

### D-35 No external AI APIs
- **Decision (user, 2026-10-03):** the project calls no AI APIs. The review explanation, DQ alerts and their causes
  are rule-based (`layer1/identity/explain.py`, `layer1/quality/narrative.py`). The architecture's LLM steps (§10 root cause, §12
  mapping, tagging, explanations) are done by rules or by a person. CLAUDE.md hard rule 9.
- Approvals recorded on 2026-10-03: REL-0926 channel fix (Layer 1 owner) and INC-0914 promise adjustment (delegated
  by the Layer 1 owner); `channel_resolved` now has no gaps and 1 promise carries `ptp_status_adjusted = kept`.

### D-36 Redaction results
- **Evidence:** Presidio with `en_core_web_md` plus Canadian patterns redacted 760,594 agent notes and 388,701
  transcript turns (25,000 transcripts) in 10 min 50 s, 1.4 GB peak. The spot check finds 0 phones, emails, postal
  codes, CRM IDs or 12-19 digit numbers left in redacted text. A first pass on the sample left one 15-digit card or
  account number written in groups (synthetic numbers fail Presidio's Luhn check), so a `LONG_NUMBER` recognizer was
  added. Dates and amounts are kept on purpose.
- **Decision:** other layers read only `silver.agent_notes_redacted` and `silver.transcript_turns_redacted`;
  raw `note_text` and transcript files stay in bronze. Tests: `tests/test_redact.py`.

### D-37 Vulnerability leaves gold; preferred_language leaves the feature view
- **Evidence:** the release README lists vulnerability among the protected attributes that "must not be used in
  decisions"; the dictionary tags only `vulnerability_type`. The first gold build carried `vulnerability_flag` on
  `c360_customer` and `l3_customer_features` as a care flag (architecture §8), plus `vulnerable_customer_flag` on
  `gold.cases` and `vulnerability_disclosed_flag` on `gold.contact_timeline`.
- **Decision (team lead brief L1-3):** every vulnerability field moves to `restricted.protected_attributes`
  (`vulnerability_flag`, `vulnerability_source`, `case_vulnerable_flag`, `contact_vulnerability_disclosed`), for the
  fairness audit only. `hardship_flag` stays in gold for protective handling. `preferred_language` is removed from
  `gold.l3_customer_features` as a possible proxy for origin, but stays on `gold.c360_customer`, where agents need it
  to serve the customer in their language. The history snapshot drops the old column through a pre-hook, and the
  `no_protected_columns_in_gold` test now fails on any gold column containing "vulnerab" and on any language column in
  the feature view. On full data, `restricted` holds the flags for the fairness audit; gold has none.
- **Architecture:** §8 survivorship, §11 contract (care flags, excluded columns), §13.

### D-38 Benchmark data traps fixed once in gold (L1-4)
- **Evidence:** the dev benchmark's trap notes list data problems (BQ-011, 012, 017, 035). Profiling them on full data:
  `dpd` is empty on 7.99% of card and 7.96% of loan accounts and on 1,005,143 of 12,569,692 snapshot rows; where dpd
  exists, the stored bucket disagrees with it on 3.67% of card and 3.65% of loan accounts and 3.7% of snapshot rows
  (the earlier 11.6% in D-30 counted missing dpd as a mismatch). `account_monthly_snapshot.month_end_date` comes in four
  formats: YYYY-MM-DD (11,314,540), YYYYMMDD (418,081), DD/MM/YYYY (418,752) and MM/DD/YYYY (418,319).
- **Decision:** source values stay; corrected columns sit beside them.
  - `gold.c360_accounts`: `dpd_missing`, `dpd_bucket_recomputed` (from dpd, NULL when dpd is missing; never from
    `cycles_delinquent`), `dpd_bucket_mismatch`.
  - `gold.cases`: `current_bucket_recomputed` from `current_dpd`, `current_bucket_mismatch`.
  - New `gold.account_monthly_snapshot`: parsed `month_end_date` with `month_end_date_format`, the same dpd columns,
    and `golden_customer_id` through the account (its own `crm_customer_id` is noisy, D-03).
  - Slash dates are read in whichever order gives a month end. A month-end day is 28-31 and can never be a month, so
    only one order is ever valid: the order comes from the value, not a guess (rule 8). Every row parses.
- **Note for Layer 2:** the dev reference SQL for BQ-017 reads only DD/MM/YYYY slash dates, so it drops the 418,319
  MM/DD/YYYY rows. To reproduce the gold answer, filter `month_end_date_format <> 'MM/DD/YYYY'`; the fully parsed
  column is the more correct one. BQ-010 (out-of-hours calls) needs no data fix: `within_permitted_hours_flag` is
  given; the pattern is the finding.
- **DQ:** the bucket checks now compare only rows with dpd; missing dpd is its own warning.
- Macros `dpd_to_bucket`, `parse_month_end`, `month_end_format` with tests in `layer1/tests/test_parsers.py`.

### D-39 Role-based access views (L1-5, task 2 of the hand-off)
- **Decision:** DuckDB has no row-level security, so each role gets a schema of views and every app or Layer 2 query
  goes through them (architecture §13):
  - `access_analyst` (8 views over gold): no names, phones, emails, DOB, postal code, FSA (a proxy for protected
    grounds, benchmark BQ-024), source keys, or free-text SMS and email content. Protected attributes were never in gold.
  - `access_agent.case_brief`: one row per case with `assigned_agent_id` (the app filters on it), name for greeting
    the right party, phone as `***-***-1234`, email as `a***@domain`, no DOB, contact rules, consent, restrictions,
    hardship and incident flags, and the debt details that may be discussed only once `verify_before_disclosure` is
    cleared. The gate itself is enforced in the agent app (Layer 4).
  - `access_engineer.quarantine`: the failing value masked to its first character.
  - The identity reviewer works in the review app (one pair at a time); the fairness reviewer reads only
    `restricted.fairness_link_rates`.
- **Tests:** `access_analyst_has_no_pii` fails on any dictionary "Direct PII" column (or its gold equivalent), postal
  code, FSA or protected attribute in `access_analyst`; `access_agent_masked` fails on any unmasked phone or email or
  any DOB column in `access_agent`. Built into the Dagster gold asset.

### D-40 Sensitivity tags and source-to-target mappings (L1-5, task 1 of the hand-off)
- **Decision:** `python -m layer1.governance.propose tags|mappings` writes proposals by rules (no model call):
  `layer1/config/sensitivity/tags_v1.yml` covers all 1,417 dictionary columns; `layer1/config/mappings/mappings_v1.yml`
  covers all 51 columns of `gold.c360_customer` with source and survivorship rule. The dictionary's 70 tags are kept
  or made stricter; the release README's protected list adds every vulnerability field and `age`/`age_band`; AML
  fields are "Restricted (AML)"; 20 columns are proposed as "Proxy (review)" (language columns, interpreter use,
  benefit deposits, home branch city, urban/rural, military service, power of attorney, third-party relationship).
  Agent certification columns are internal (they describe staff training, not customers).
- **Approval:** every entry starts as `proposed`; a person approves with
  `python -m layer1.governance.propose approve FILE --by "<name>" [--where tag=...] [--ids ...]`, which records name
  and date. A later version is a new `_v2` file, so git history is the audit trail.
- **Guards** (`layer1/tests/test_governance.py`): every column tagged once; dictionary tags kept or stricter;
  approved entries name a person; every C360 column mapped; Protected or AML columns (any status) never appear in
  gold, access, layer2 or layer3 schemas; approved proxies never appear in the feature views or the layer3 schema.
  Proxies stay allowed in gold for servicing (for example `preferred_language` to serve the customer).

### D-41 Full Dagster run and dbt docs (L1-5, tasks 3 and 4)
- **Evidence:** the first full materialization found three bugs that step-by-step runs hid. (1) dagster-dbt runs
  dbt from inside `dbt/`, so the profile's relative `lake/...` path pointed nowhere; the profile now reads
  `DHANDHO_LAKE` (absolute, set by the Dagster definitions; default `lake` for manual runs from the repo root).
  (2) Redaction and the silver build ran in parallel and collided on DuckDB's single-writer lock; the graph now uses
  the in-process executor, one step at a time, which also keeps memory within budget. (3) The DQ checks assumed all
  31 bronze tables exist; tables missing from a database (the dev sample) are now recorded as skipped.
- **Result:** one materialization of all assets succeeds on the sample (about 1.5 min) and on full data in about
  16 minutes: bronze 9 s, redaction 11 min 8 s, silver 1 min 36 s, identity 1 min 21 s (Splink retrained, same
  4,564,735 pairs), gold 1 min 25 s, DQ 7 s. Golden IDs are stable across the rebuild: 0 new, 0 changed.
- **dbt docs:** `python -m layer1.governance.dbt_docs` writes descriptions for all 459 columns of the 8 gold models
  into `dbt/models/gold/schema.yml`, from the C360 mappings, the data dictionary and a list for derived columns
  (sensitivity tag included where not internal). `dbt docs generate` builds the site with lineage from the bronze
  and identity sources to gold.

### D-42 Data contract and data quality report (L1-6, L1-7)
- **Data contract:** `deliverables/data_contract_c360.yaml` (DC-C360-001 v1.0.0) is generated by
  `python -m layer1.governance.contract` in the shape of the bank's own contract (DC-COLL-001: owner, refresh,
  quality rules, allowed and not-allowed uses) plus architecture §11. Schema (51 columns, types from the built table),
  sensitivity from the tags, source and survivorship from the mappings, 14 quality rules each naming the dbt test that
  enforces it, with severity read from that test (blocking or warning). `layer1/tests/test_contract.py` fails if the
  file and the build disagree. The business owner's name is still to confirm.
- **DQ report:** `deliverables/data_quality_report.md` is generated by `python -m layer1.quality.report`: 30 issues
  (4 listed in `ops_events`, 26 found by Layer 1), each with a count queried at generation time, how it was handled
  and the decision behind it. A test checks every issue has a count and a handling line.

### D-43 One-command build for teammates (L1-1) and the README section (L1-9)
- **Decision:** `python -m layer1.build --target dev|full` runs every Layer 1 step in order and then checks that each
  interface table exists with a sensible row count and that the latest DQ run has no blocking failure
  (`--verify-only` for just the check). Sharing a built database was rejected: the full file is 7.5 GB and its bronze
  views point at absolute paths on the machine that built it, while a local build takes about 16 minutes (2 on the
  sample). The dev sample now copies every remaining table (reference, policy and operations tables whole;
  `model_scores` by sampled customer) so Layers 2 to 4 can develop on it; the checker found that the metric
  definitions were missing from it.
- **README:** the Layer 1 section for the root README is in `layer1/docs/README_section.md` for the lead to merge.

### D-44 Tags and mappings approved
- Approved on 2026-10-03 by Anish (Layer 1 owner) with `python -m layer1.governance.propose approve ... --by "Anish"`:
  1,417 sensitivity tags and 51 C360 mappings (decision D-40). Approved proxies are now enforced by the tests.

### D-45 Customer 360 page (L1-8)
- **Decision:** `app/pages/1_Customer_360.py` (slot in Madhur's app shell) searches a golden ID or case ID and shows
  identity confidence and `verify_before_disclosure` to every role; the rest depends on the role:
  - agent: `access_agent.case_brief` only, by case ID; masked phone and email, no DOB; contact rules, consent,
    restrictions, hardship and incident flags; debt details stay hidden while `verify_before_disclosure` is true
    until the agent confirms identity, which is logged (`identity_verified` in `audit.events`);
  - team lead: golden record with phone and email masked, no DOB ("on file"), postal code cut to its FSA; every
    linked source key with link status, rule, probability and reviewer; pending review pairs; survivorship; lineage;
  - analyst: `access_analyst` views only; merged-record counts per source instead of source keys.
  Every view of a record is logged (`c360_viewed`, IDs only).
- **Tests:** `layer1/tests/test_customer_360_page.py` runs the page headlessly per role on a copy of the dev
  database. Rendered on full data for all three roles in under a second each.
- **Limitation found:** dbt-built views name their catalog after the database file (`dev`, `maple`), so a copy of
  the database must keep its file name, or the access views break.
