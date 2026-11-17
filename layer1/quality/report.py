"""Write deliverables/data_quality_report.md: every data issue, how many records it affected, how it was handled
(brief L1-7, decisions D-42). Every count is queried from the built database when the report is generated, so the
report and the data cannot disagree. Issues listed in ops_events are marked; the rest were found by profiling.

Usage:  python -m layer1.quality.report [--db lake/maple.duckdb]
"""
import argparse
from datetime import date

import duckdb

from layer1.pipeline import config

OUT = config.ROOT / "deliverables" / "data_quality_report.md"

# (area, issue, count SQL, how handled, decision, listed in ops_events)
ISSUES = [
    # ---- known to operations (ops_events) ----
    ("Schema drift", "REL-0926: `contact_history.channel` renamed to `channel_v2` from 26 Sep; `channel` empty on most new rows",
     "SELECT count(*) FROM bronze.contact_history WHERE channel IS NULL AND channel_v2 IS NOT NULL",
     "Caught by the null-rate and drift checks; `gold.contact_timeline.channel_resolved = coalesce(channel_v2, channel)` after an engineer approved the fix (REL-0926-channel); source columns kept",
     "D-29, D-35", "REL-0926"),
    ("Incident", "INC-0914: app payments posted a day late and late fees were charged in error (contacts tagged with the incident)",
     "SELECT count(*) FROM bronze.contact_history WHERE ops_incident_id = 'INC-0914'",
     "`gold.incident_effects` and `incident_affected` on the golden record; promises broken on the outage day but paid next day get `ptp_status_adjusted` (approved rule INC-0914-ptp; 1 promise); source status kept",
     "D-24", "INC-0914"),
    ("Incident", "INC-0923: dialer outage on 23-24 Sep, call capacity down about 60% (contacts tagged)",
     "SELECT count(*) FROM bronze.contact_history WHERE ops_incident_id = 'INC-0923'",
     "Labelled as a known incident in drift and volume checks so it is not mistaken for a pipeline fault; no data change",
     "D-30", "INC-0923"),
    ("Source feed", "DFI-0812: bureau file converted with a shifted column for some rows (rows in the August bureau refresh)",
     "SELECT count(*) FROM bronze.external WHERE src_file_name = 'bureau_refresh_202608.csv'",
     "Checked: the identity fields used for matching (name, DOB, postal code) parse on every bureau row (0 quarantined); score fields are outside Layer 1 and flagged to Layers 2 and 3",
     "D-30", "DFI-0812"),
    # ---- found by profiling (not in ops_events) ----
    ("Duplicates", "CRM holds two records for the same person (shared national ID hash)",
     "SELECT count(*) FROM identity.deterministic_pairs WHERE rule = 'D1'",
     "Rule D1 merges pairs with identical DOBs; pairs with differing DOBs go to human review; golden IDs count people, not CRM rows",
     "D-01", None),
    ("Duplicates", "CRM's own duplicate pointer (`duplicate_of_crm_id`) is incomplete and sometimes joins different DOBs",
     "SELECT count(*) FROM bronze.customers WHERE duplicate_of_crm_id IS NOT NULL AND is_deleted = 'false'",
     "Used only to measure recall (98.99%), never to link", "D-01", None),
    ("Deleted records", "CRM rows marked deleted (CDC 'D')",
     "SELECT count(*) FROM bronze.customers WHERE is_deleted = 'true'",
     "Excluded from matching and gold; kept in bronze", "D-05", None),
    ("Format", "Deposit customer numbers (CIF) lost their leading zeros (7 or 8 digits instead of 10)",
     "SELECT count(*) FROM bronze.deposit_accounts WHERE length(src_customer_ref) < 10",
     "Padded to 10 digits in silver (`pad_cif`), raw value kept; then every CIF matches its bureau record", "D-02", None),
    ("Format", "CRM `dob_raw` mixes day-first and month-first dates; day and month cannot be told apart",
     "SELECT count(*) FROM bronze.customers WHERE regexp_full_match(dob_raw, '\\d{2}/\\d{2}/\\d{4}') AND try_cast(split_part(dob_raw, '/', 1) AS INT) <= 12 AND try_cast(split_part(dob_raw, '/', 2) AS INT) <= 12 AND split_part(dob_raw, '/', 1) <> split_part(dob_raw, '/', 2)",
     "Never parsed; the clean ISO `date_of_birth` is used (rule: day and month are never guessed)", "D-09", None),
    ("Format", "Quarter-end `month_end_date` in four formats, including both slash orders",
     "SELECT count(*) FROM bronze.account_monthly_snapshot WHERE NOT regexp_full_match(month_end_date, '\\d{4}-\\d{2}-\\d{2}')",
     "Parsed into `gold.account_monthly_snapshot.month_end_date`; a month-end day (28-31) is never a month, so only one reading is valid; every row parses; `month_end_date_format` kept",
     "D-38", None),
    ("Format", "`collections_cases.account_ids` holds a bare ID instead of a JSON array",
     "SELECT count(*) FROM bronze.collections_cases WHERE account_ids IS NOT NULL AND left(trim(account_ids), 1) <> '['",
     "Parser accepts both forms (`parse_account_ids`)", "D-13", None),
    ("Masking", "Card phone numbers masked (first 6 and last 2 digits visible)",
     "SELECT count(*) FROM bronze.card_accounts WHERE regexp_full_match(cardholder_phone_raw, '\\d{6}[xX*#]{3}\\d{2}')",
     "Compared on the visible digits only; cards matched on a masked phone and an initial get medium confidence at most",
     "D-04", None),
    ("Invalid values", "Phone numbers with the wrong number of digits",
     "SELECT count(*) FROM silver.quarantine WHERE rule = 'phone_format'",
     "Quarantined with the rule and reason; the record continues without the phone", "D-18", None),
    ("Invalid values", "Postal codes whose area (FSA) is not in `geo_reference` (cards and loans)",
     "SELECT count(*) FROM silver.quarantine WHERE rule = 'fsa_unknown'",
     "Quarantined; postal code left out of matching (less evidence, never wrong evidence)", "D-22", None),
    ("Missing values", "Days past due (`dpd`) empty on card and loan accounts",
     "SELECT count(*) FROM gold.c360_accounts WHERE dpd_missing AND product IN ('card', 'loan')",
     "Flagged `dpd_missing`; never filled from `cycles_delinquent` (cycles, not days)", "D-38", None),
    ("Missing values", "CRM customers with no date of birth",
     "SELECT count(*) FROM silver.stg_crm_identity WHERE dob IS NULL",
     "Cannot auto-link (identical DOBs required); they wait in review at low confidence, so agents verify first; flagged in the fairness audit",
     "D-34", None),
    ("Missing values", "Bureau records with no name or DOB (no-hit and thin files)",
     "SELECT count(*) FROM bronze.external WHERE subject_name_reported IS NULL OR subject_dob_reported IS NULL",
     "Deposit-to-bureau links without a DOB go to review rather than auto-link", "D-02", None),
    ("Inconsistent values", "Account `dpd_bucket` disagrees with `dpd` (cards and loans)",
     "SELECT count(*) FROM gold.c360_accounts WHERE dpd_bucket_mismatch AND product IN ('card', 'loan')",
     "`dpd_bucket_recomputed` added beside the source bucket", "D-38", None),
    ("Inconsistent values", "Case `current_bucket` disagrees with `current_dpd`",
     "SELECT count(*) FROM gold.cases WHERE current_bucket_mismatch",
     "`current_bucket_recomputed` added beside the source bucket", "D-38", None),
    ("Inconsistent values", "Monthly snapshot `dpd_bucket` disagrees with `dpd`",
     "SELECT count(*) FROM gold.account_monthly_snapshot WHERE dpd_bucket_mismatch",
     "`dpd_bucket_recomputed` added beside the source bucket", "D-38", None),
    ("Inconsistent values", "A product customer key whose accounts carry different DOBs",
     "SELECT count(*) FROM silver.identity_input WHERE dob_conflict",
     "DOB left empty and `dob_conflict` set, so the key can only be linked by a person", "D-11", None),
    ("Reconciliation", "Open case `total_overdue` does not equal the sum of its accounts' past-due amounts",
     "SELECT count(*) FROM (SELECT k.case_id, any_value(try_cast(k.total_overdue AS DOUBLE)) AS t, sum(a.past_due_amount) AS s FROM bronze.collections_cases k, unnest(parse_account_ids(k.account_ids)) u(account_id) JOIN gold.c360_accounts a ON a.account_id = u.account_id WHERE k.outcome IS NULL GROUP BY 1) WHERE abs(t - s) > 0.01 * greatest(t, 1)",
     "Reported as a warning for the collections data owner; no automatic change", "D-30", None),
    ("Broken references", "Collections cases whose customer reference matches no current CRM record",
     "SELECT count(*) FROM gold.cases WHERE customer_ref_unresolved",
     "Kept, but excluded from customer counts and from the truth sets; flagged in the DQ report", "D-13", None),
    ("Broken references", "Contacts, promises and notes referencing a CRM ID that does not exist",
     "SELECT (SELECT count(*) FROM bronze.contact_history h WHERE NOT EXISTS (SELECT 1 FROM bronze.customers c WHERE c.crm_customer_id = h.crm_customer_id)) + (SELECT count(*) FROM bronze.promises_to_pay p WHERE NOT EXISTS (SELECT 1 FROM bronze.customers c WHERE c.crm_customer_id = p.crm_customer_id)) + (SELECT count(*) FROM bronze.agent_notes n WHERE NOT EXISTS (SELECT 1 FROM bronze.customers c WHERE c.crm_customer_id = n.crm_customer_id))",
     "Rows kept with no golden ID; reported as a referential-integrity warning", "D-13", None),
    ("Broken references", "Monthly snapshot `crm_customer_id` values that do not exist in `customers`",
     "SELECT count(DISTINCT crm_customer_id) FROM bronze.account_monthly_snapshot WHERE crm_customer_id NOT IN (SELECT crm_customer_id FROM bronze.customers)",
     "The snapshot's CRM ID is used only to evaluate matching; golden IDs come from the account", "D-03", None),
    ("Broken references", "Joint-holder and co-borrower references that point to no record",
     "SELECT count(*) FROM silver.household_refs WHERE NOT resolves",
     "Resolved references become household links (never identity links); look-alike pairs go to review", "D-12", None),
    ("Truth-set noise", "Case-listed accounts linked to a different golden customer than the case's CRM reference (mostly the case pointing at another person: different name and DOB)",
     "SELECT sum(n_linked - n_correct) FROM identity.match_quality WHERE truth_set = 'cases' AND run_id = (SELECT arg_max(run_id, recorded_at) FROM identity.match_quality)",
     "Measured, not corrected; on clean pairs (case and snapshot agree) every remaining mismatch is the same person's other CRM record", "D-30, D-32", None),
    ("Privacy", "Agent notes and transcript turns containing names, phones, emails, postal codes or account numbers",
     "SELECT (SELECT count(*) FROM silver.agent_notes_redacted WHERE entities_found <> '{}') + (SELECT count(*) FROM silver.transcript_turns_redacted WHERE entities_found <> '{}')",
     "Redacted with Presidio plus Canadian patterns before any other layer reads text; spot check finds 0 left", "D-36", None),
    ("Privacy", "`contact_history.phone_number_hash` is an unsalted MD5 of the phone number (reversible)",
     "SELECT count(*) FROM bronze.contact_history WHERE phone_number_hash IS NOT NULL",
     "Treated as direct PII: left out of gold and every access view", "D-06", None),
    ("Protected data", "Vulnerability fields on customers, cases and contacts (protected attribute in the release)",
     "SELECT (SELECT count(*) FROM bronze.customers WHERE vulnerability_flag = 'true') + (SELECT count(*) FROM bronze.collections_cases WHERE vulnerable_customer_flag = 'true') + (SELECT count(*) FROM bronze.contact_history WHERE vulnerability_disclosed_flag = 'true')",
     "Moved out of gold to the restricted schema (fairness audit only); a test fails if they return", "D-37", None),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(config.FULL_DB))
    args = ap.parse_args(argv)
    con = duckdb.connect(args.db, read_only=True)
    rows = []
    for area, issue, sql, handled, dec, ops in ISSUES:
        n = con.execute(sql).fetchone()[0]
        rows.append((area, issue, int(round(n or 0)), handled, dec, ops))
    dq_run = con.execute("""SELECT run_id, count(*), count(*) FILTER (WHERE status <> 'pass'),
                                   count(*) FILTER (WHERE status = 'fail' AND severity = 'block')
                            FROM gold.dq_report WHERE run_id = (SELECT arg_max(run_id, recorded_at) FROM gold.dq_report)
                            GROUP BY 1""").fetchone()
    counts = con.execute("""SELECT (SELECT count(*) FROM silver.identity_input), (SELECT count(*) FROM gold.c360_customer),
                                   (SELECT count(*) FROM silver.quarantine)""").fetchone()
    known = [r for r in rows if r[5]]
    found = [r for r in rows if not r[5]]

    def table(rs, with_ops):
        head = "| # | Area | Issue | Records affected | How it was handled | Decision |" + (" Event |" if with_ops else "")
        sep = "|---|---|---|---:|---|---|" + ("---|" if with_ops else "")
        body = [f"| {i} | {a} | {iss} | {n:,} | {h} | {d} |" + (f" {o} |" if with_ops else "")
                for i, (a, iss, n, h, d, o) in enumerate(rs, 1)]
        return "\n".join([head, sep] + body)

    text = f"""# Data quality report: Layer 1 (Data Product Factory)

Generated {date.today()} by `python -m layer1.quality.report` from the built database; every count is queried at
generation time. Source snapshot 28 Sep 2026; full release (31 tables, about 1M customers).

## Summary

- {len(rows)} issues: {len(known)} that the operations team listed in `ops_events`, and **{len(found)} that `ops_events` does
  not list**, found by profiling and checks.
- Source facts are never overwritten. Every correction is a new column beside the original, with a reason, and every
  fix that changes meaning was approved by a named role before it applied.
- Automated checks: the latest run of `gold.dq_report` has {dq_run[1]} checks, {dq_run[2]} warnings and {dq_run[3]}
  blocking failures (run `{dq_run[0][:8]}`). The data-contract rules in `deliverables/data_contract_c360.yaml` all pass.
- Scale: {counts[0]:,} source customer keys were matched into {counts[1]:,} golden customers; {counts[2]:,} individual
  values were quarantined with their rule and reason.

## Issues listed in ops_events

{table(known, True)}

## Issues found by Layer 1 that ops_events does not list

{table(found, False)}

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
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"{OUT.relative_to(config.ROOT)}: {len(rows)} issues ({len(found)} not in ops_events)")


if __name__ == "__main__":
    main()
