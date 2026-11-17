"""Data quality and anomaly checks (architecture §10) -> gold.dq_report.

Six families: contract and schema, volume and completeness, business rules, cross-table reconciliation,
distribution drift, matching health. Schema, key and matching-health failures block the release; the rest warn.
A finding inside an ops_events window on an affected table is labelled a known incident, not a pipeline failure.
Thresholds are initial values to tune on the first loads. Every number here comes from SQL; nothing is generated.

Usage:  python -m layer1.quality.checks [--db lake/maple.duckdb]
"""
import argparse
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from layer1.pipeline import config
from layer1.pipeline.db import connect

TOLERANCE = 0.01          # business rules: ±1% (to confirm with the business owner)
PSI_ALERT = 0.25
SPIKE_RATIO = 1.5         # matching health: a metric moving by more than 50% against the previous run blocks


@dataclass
class Result:
    family: str
    check: str
    table: str
    severity: str             # block | warn
    metric: float | None
    threshold: str
    status: str               # pass | warn | fail
    detail: dict = field(default_factory=dict)
    known_incident: str | None = None


def scalar(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def rate_check(con, family, check, table, sql_bad, sql_total, severity="warn", limit=0.0, detail=None):
    bad = scalar(con, sql_bad)
    total = scalar(con, sql_total) or 1
    rate = bad / total
    status = "pass" if rate <= limit else ("fail" if severity == "block" else "warn")
    return Result(family, check, table, severity, round(rate, 6), f"<= {limit}", status,
                  {"bad_rows": bad, "rows": total, **(detail or {})})


# ---------------- 1. contract and schema ----------------

def contract_checks(con):
    out = []
    schema = json.loads((config.RAW / "schema" / "schema.json").read_text())
    present = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'bronze'").fetchall()}
    for table, meta in schema.items():
        if table not in present:      # the dev sample carries only the tables Layer 1 needs
            out.append(Result("contract", "columns_match_contract", table, "warn", None, "table loaded", "pass",
                              {"skipped": "table not in this database (dev sample)"}))
            continue
        expected = [c["name"] for c in meta["columns"]]
        actual = [r[0] for r in con.execute(f"DESCRIBE bronze.{table}").fetchall()
                  if not r[0].startswith("_") and r[0] != "year"]
        missing, extra = sorted(set(expected) - set(actual)), sorted(set(actual) - set(expected))
        out.append(Result("contract", "columns_match_contract", table, "block", len(missing) + len(extra), "= 0",
                          "pass" if not (missing or extra) else "fail", {"missing": missing, "unexpected": extra}))
    pks = {"customers": "crm_customer_id", "card_accounts": "card_account_id", "loan_accounts": "loan_id",
           "deposit_accounts": "deposit_account_id", "collections_cases": "case_id", "contact_history": "contact_id",
           "promises_to_pay": "ptp_id", "external": "bureau_request_id", "agent_notes": "note_id"}
    for table, pk in pks.items():
        if table not in present:
            continue
        dupes = scalar(con, f"SELECT count(*) - count(DISTINCT {pk}) FROM bronze.{table}")
        nulls = scalar(con, f"SELECT count(*) FILTER (WHERE {pk} IS NULL) FROM bronze.{table}")
        out.append(Result("contract", "primary_key_unique", table, "block", dupes + nulls, "= 0",
                          "pass" if dupes + nulls == 0 else "fail", {"duplicates": dupes, "nulls": nulls, "key": pk}))
    # Referential integrity along the dictionary relationships that carry CRM IDs.
    for table, col in [("collections_cases", "coll_customer_ref"), ("contact_history", "crm_customer_id"),
                       ("promises_to_pay", "crm_customer_id"), ("agent_notes", "crm_customer_id")]:
        if table not in present:
            continue
        out.append(rate_check(con, "contract", "references_existing_crm_id", table,
                              f"""SELECT count(*) FROM bronze.{table} t WHERE t.{col} IS NOT NULL
                                  AND NOT EXISTS (SELECT 1 FROM bronze.customers c WHERE c.crm_customer_id = t.{col})""",
                              f"SELECT count(*) FROM bronze.{table}", limit=0.0,
                              detail={"column": col, "note": "mismatched IDs (decisions D-13)"}))
    out.append(rate_check(con, "contract", "outcome_code_in_code_lookups", "contact_history",
                          """SELECT count(*) FROM bronze.contact_history h WHERE outcome_code IS NOT NULL AND outcome_code
                             NOT IN (SELECT code FROM bronze.code_lookups WHERE code_type = 'outcome_code')""",
                          "SELECT count(*) FROM bronze.contact_history WHERE outcome_code IS NOT NULL"))
    out.append(rate_check(con, "contract", "postal_and_phone_format", "silver.quarantine",
                          "SELECT count(*) FROM silver.quarantine", "SELECT count(*) FROM silver.identity_input",
                          limit=0.01, detail=dict(con.execute(
                              "SELECT rule, count(*) FROM silver.quarantine GROUP BY 1").fetchall())))
    return out


# ---------------- 2. volume and completeness ----------------

def volume_checks(con, previous):
    out = []
    tables = ["customers", "card_accounts", "loan_accounts", "deposit_accounts", "collections_cases",
              "contact_history", "promises_to_pay", "external"]
    for t in tables:
        n = scalar(con, f"SELECT count(*) FROM bronze.{t}")
        prev = previous.get(("volume", "row_count", t))
        change = None if not prev else abs(n - prev) / prev
        status = "pass" if change is None or change <= 0.10 else "warn"
        out.append(Result("volume", "row_count", t, "warn", n, "within 10% of previous run", status,
                          {"previous": prev, "change": change}))
    # Completeness drift inside one load: the null rate of contact_history.channel by day (catches the rename).
    rows = con.execute("""
        SELECT left(contact_ts_local, 10) AS d, avg((channel IS NULL)::INT) AS null_rate, count(*) AS n
        FROM bronze.contact_history WHERE contact_ts_local >= '2026-09-14' GROUP BY 1 ORDER BY 1""").fetchall()
    base = [r[1] for r in rows if r[0] < "2026-09-26"]
    baseline = sum(base) / len(base) if base else 0
    spikes = [(d, round(r, 3)) for d, r, n in rows if r > baseline + 0.2]
    out.append(Result("volume", "null_rate_by_day", "contact_history.channel", "warn",
                      max((r for _, r in spikes), default=baseline), f"<= baseline {baseline:.3f} + 0.2",
                      "warn" if spikes else "pass", {"spike_days": spikes}))
    return out


# ---------------- 3. business rules ----------------

def business_checks(con):
    tol = TOLERANCE
    num = lambda c: f"try_cast({c} AS DOUBLE)"
    checks = [
        ("card_utilisation_is_balance_over_limit", "card_accounts",
         f"{num('credit_limit')} > 0 AND abs({num('utilisation')} - {num('current_balance')} / {num('credit_limit')}) > {tol}"),
        ("card_available_credit_is_limit_minus_balance", "card_accounts",
         f"abs({num('available_credit')} - ({num('credit_limit')} - {num('current_balance')})) > {tol} * greatest({num('credit_limit')}, 1)"),
        # Bucket vs DPD only where DPD exists; missing DPD (about 8%) is its own check (decisions D-38).
        ("card_dpd_bucket_matches_dpd", "card_accounts",
         "dpd IS NOT NULL AND dpd_bucket <> dpd_to_bucket(try_cast(dpd AS INT))"),
        # payment_history_24m starts with LAST month's status: floor(dpd_m01 / 30), capped at 7 (decisions D-30)
        ("card_payment_history_first_char_matches_last_month_dpd", "card_accounts",
         # C = closed and X = no data (data dictionary) carry no DPD, so they are not compared.
         """left(payment_history_24m, 1) NOT IN ('C', 'X')
            AND left(payment_history_24m, 1) <> least(7, floor(try_cast(dpd_m01 AS INT) / 30))::INT::VARCHAR"""),
        ("loan_total_is_principal_interest_fees", "loan_accounts",
         f"abs({num('total_outstanding')} - ({num('principal_balance')} + {num('accrued_interest')} + {num('fees_outstanding')}))"
         f" > {tol} * greatest({num('total_outstanding')}, 1)"),
        ("loan_dpd_bucket_matches_dpd", "loan_accounts",
         "dpd IS NOT NULL AND dpd_bucket <> dpd_to_bucket(try_cast(dpd AS INT))"),
        ("card_dpd_missing", "card_accounts", "dpd IS NULL"),
        ("loan_dpd_missing", "loan_accounts", "dpd IS NULL"),
    ]
    out = [rate_check(con, "business_rule", name, table,
                      f"SELECT count(*) FROM bronze.{table} WHERE {cond}", f"SELECT count(*) FROM bronze.{table}",
                      limit=0.001) for name, table, cond in checks]
    # A payment matched to a promise that is still marked broken: the INC-0914 symptom (decisions D-30). The design's
    # "every kept promise has a matched payment" can't be checked here: transactions only cover deposit-side payments.
    out.append(rate_check(con, "business_rule", "matched_payment_on_broken_promise", "promises_to_pay",
                          """SELECT count(DISTINCT p.ptp_id) FROM bronze.promises_to_pay p
                             JOIN bronze.transactions t ON t.ptp_id_matched = p.ptp_id WHERE p.ptp_status = 'broken'""",
                          """SELECT count(DISTINCT ptp_id_matched) FROM bronze.transactions
                             WHERE ptp_id_matched IS NOT NULL""", limit=0.0))
    return out


# ---------------- 4. cross-table reconciliation ----------------

def reconciliation_checks(con):
    base = """
        WITH acc AS (SELECT k.case_id, try_cast(k.total_overdue AS DOUBLE) AS total_overdue,
                            try_cast(k.current_dpd AS INT) AS current_dpd,
                            sum(a.past_due_amount) AS sum_past_due, max(a.dpd) AS max_dpd
                     FROM bronze.collections_cases k, unnest(parse_account_ids(k.account_ids)) u(account_id)
                     JOIN gold.c360_accounts a ON a.account_id = u.account_id
                     WHERE k.outcome IS NULL GROUP BY ALL)
        SELECT count(*) FILTER (WHERE {cond}) FROM acc"""
    total = "SELECT count(*) FROM bronze.collections_cases WHERE outcome IS NULL"
    return [
        rate_check(con, "reconciliation", "case_total_overdue_equals_account_past_due", "collections_cases",
                   base.format(cond=f"abs(total_overdue - sum_past_due) > {TOLERANCE} * greatest(total_overdue, 1)"),
                   total, limit=0.01),
        rate_check(con, "reconciliation", "case_current_dpd_equals_max_account_dpd", "collections_cases",
                   base.format(cond="current_dpd <> max_dpd"), total, limit=0.01),
    ]


# ---------------- 5. distribution drift ----------------

def psi(expected: dict, actual: dict) -> float:
    import math
    keys = set(expected) | set(actual)
    e_tot, a_tot = sum(expected.values()) or 1, sum(actual.values()) or 1
    value = 0.0
    for k in keys:
        e = max(expected.get(k, 0) / e_tot, 1e-4)
        a = max(actual.get(k, 0) / a_tot, 1e-4)
        value += (a - e) * math.log(a / e)
    return value


def drift_checks(con, previous):
    out = []
    # Within the load: enum frequency of channel in the last 3 days vs the 4 weeks before (catches REL-0926).
    for col in ["channel", "coalesce(channel_v2, channel)"]:
        recent = dict(con.execute(f"""SELECT coalesce({col}, '(null)'), count(*) FROM bronze.contact_history
                                      WHERE contact_ts_local >= '2026-09-26' GROUP BY 1""").fetchall())
        before = dict(con.execute(f"""SELECT coalesce({col}, '(null)'), count(*) FROM bronze.contact_history
                                      WHERE contact_ts_local >= '2026-08-29' AND contact_ts_local < '2026-09-26'
                                      GROUP BY 1""").fetchall())
        v = psi(before, recent)
        out.append(Result("drift", "psi_last_3_days_vs_prior_4_weeks", f"contact_history.{col}", "warn", round(v, 4),
                          f"<= {PSI_ALERT}", "warn" if v > PSI_ALERT else "pass"))
    # Against the previous run: balance and DPD distributions.
    for name, sql in {
        "card_balance_decile": "SELECT ntile, count(*) FROM (SELECT ntile(10) OVER (ORDER BY try_cast(current_balance AS DOUBLE)) AS ntile FROM bronze.card_accounts) GROUP BY 1",
        "card_dpd_bucket": "SELECT dpd_bucket, count(*) FROM bronze.card_accounts GROUP BY 1",
        "loan_dpd_bucket": "SELECT dpd_bucket, count(*) FROM bronze.loan_accounts GROUP BY 1",
    }.items():
        cur = {str(k): v for k, v in con.execute(sql).fetchall()}
        prev = previous.get(("drift_dist", name))
        v = psi(prev, cur) if prev else None
        out.append(Result("drift", "psi_vs_previous_run", name, "warn", v, f"<= {PSI_ALERT}",
                          "pass" if v is None or v <= PSI_ALERT else "warn", {"distribution": cur}))
    return out


# ---------------- 6. matching health ----------------

def matching_checks(con, previous):
    out = []
    metrics = dict(con.execute("""
        WITH cur AS (SELECT * FROM identity.crosswalk WHERE valid_to IS NULL),
        sizes AS (SELECT golden_customer_id, count(*) n FROM cur GROUP BY 1)
        SELECT 'golden_ids', count(*)::DOUBLE FROM sizes
        UNION ALL SELECT 'merge_rate', avg((n > 1)::INT) FROM sizes
        UNION ALL SELECT 'max_cluster_size', max(n) FROM sizes
        UNION ALL SELECT 'review_queue', (SELECT count(*) FROM identity.review_queue)
        UNION ALL SELECT 'crm_rows_per_golden_with_crm',
            (SELECT count(*) FILTER (WHERE source_system = 'CRM') / count(DISTINCT golden_customer_id)
                    FILTER (WHERE source_system = 'CRM') FROM cur)
    """).fetchall())
    for name, value in metrics.items():
        prev = previous.get(("matching", name))
        ratio = None if not prev else value / prev
        spike = ratio is not None and (ratio > SPIKE_RATIO or ratio < 1 / SPIKE_RATIO)
        out.append(Result("matching_health", name, "identity", "block", value, f"within x{SPIKE_RATIO} of previous run",
                          "fail" if spike else "pass", {"previous": prev}))
    churn = scalar(con, """SELECT count(DISTINCT source_key) FROM identity.crosswalk
                           WHERE valid_to = (SELECT max(valid_to) FROM identity.crosswalk)""")
    out.append(Result("matching_health", "golden_id_churn", "identity", "warn", churn or 0, "keys whose mapping changed",
                      "pass"))
    for row in con.execute("""SELECT truth_set, source_system, precision, recall FROM identity.match_quality
                              WHERE run_id = (SELECT arg_max(run_id, recorded_at) FROM identity.match_quality)""").fetchall():
        out.append(Result("matching_health", f"quality_{row[0]}", row[1], "warn", row[2], "precision; recall in detail",
                          "pass", {"precision": row[2], "recall": row[3]}))
    return out


def label_incidents(con, results):
    events = con.execute("""SELECT event_id, affected_tables, start_ts, end_ts, description FROM bronze.ops_events""").fetchall()
    for r in results:
        if r.status == "pass":
            continue
        base_table = r.table.split(".")[0]
        for eid, affected, start, end, desc in events:
            if base_table in json.loads(affected or "[]"):
                if r.check in ("null_rate_by_day", "psi_last_3_days_vs_prior_4_weeks") and eid == "REL-0926":
                    r.known_incident = eid
                elif r.check == "references_existing_crm_id":
                    continue
                elif eid.startswith("INC") and r.family in ("volume", "drift"):
                    r.known_incident = r.known_incident or eid
    return results


DDL = """
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS quality;   -- pipeline state, not a data product
CREATE TABLE IF NOT EXISTS gold.dq_report (
    run_id VARCHAR, recorded_at TIMESTAMP, family VARCHAR, check_name VARCHAR, table_name VARCHAR, severity VARCHAR,
    metric DOUBLE, threshold VARCHAR, status VARCHAR, known_incident VARCHAR, detail JSON,
    _source_system VARCHAR, _source_file VARCHAR, _batch_id VARCHAR, _loaded_at TIMESTAMP, _pipeline_run_id VARCHAR
);
CREATE TABLE IF NOT EXISTS quality.dq_history (run_id VARCHAR, recorded_at TIMESTAMP, key JSON, value JSON);
"""


def load_previous(con):
    rows = con.execute("""SELECT key, value FROM quality.dq_history
                          WHERE run_id = (SELECT arg_max(run_id, recorded_at) FROM quality.dq_history)""").fetchall()
    return {tuple(json.loads(k)): json.loads(v) for k, v in rows}


def save_history(con, run_id, results):
    rows = []
    for r in results:
        if r.family == "volume" and r.check == "row_count":
            rows.append((["volume", "row_count", r.table], r.metric))
        if r.family == "matching_health" and r.metric is not None:
            rows.append((["matching", r.check], r.metric))
        if r.check == "psi_vs_previous_run":
            rows.append((["drift_dist", r.table], r.detail["distribution"]))
    for k, v in rows:
        con.execute("INSERT INTO quality.dq_history VALUES (?, now(), ?, ?)", [run_id, json.dumps(k), json.dumps(v)])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(config.FULL_DB))
    args = ap.parse_args(argv)
    con = connect(args.db)
    con.execute(DDL)
    run_id = str(uuid.uuid4())
    previous = load_previous(con)
    results = (contract_checks(con) + volume_checks(con, previous) + business_checks(con)
               + reconciliation_checks(con) + drift_checks(con, previous) + matching_checks(con, previous))
    label_incidents(con, results)
    lineage = con.execute("SELECT any_value(_batch_id), max(_loaded_at) FROM bronze.customers").fetchone()
    for r in results:
        con.execute("INSERT INTO gold.dq_report VALUES (?, now(), ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PIPELINE', 'dq_checks', ?, ?, ?)",
                    [run_id, r.family, r.check, r.table, r.severity, r.metric, r.threshold, r.status, r.known_incident,
                     json.dumps(r.detail, default=str), lineage[0], lineage[1], run_id])
    save_history(con, run_id, results)
    blocking = [r for r in results if r.status == "fail" and r.severity == "block" and not r.known_incident]
    for r in results:
        if r.status != "pass":
            print(f"{r.status.upper():5s} {r.severity:5s} {r.family:15s} {r.check:45s} {r.table:30s} "
                  f"{r.metric} {('[' + r.known_incident + ']') if r.known_incident else ''}")
    print(f"{len(results)} checks, {sum(r.status != 'pass' for r in results)} not passing, {len(blocking)} blocking")
    con.close()
    if blocking:
        raise SystemExit("release blocked: " + ", ".join(f"{r.check}({r.table})" for r in blocking))


if __name__ == "__main__":
    main()
