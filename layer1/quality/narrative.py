"""Plain-English DQ alerts and run summary (architecture §10 "AI root cause", §12).

A deterministic lookup links each check that did not pass to release notes (`reference_documents`, doc_type
release_note, related_table) and incidents (`ops_events`, affected_tables), and writes the alert from templates.
The project uses no external AI APIs (CLAUDE.md). Every number comes from gold.dq_report; a fix applies only after
an engineer approves it (var approved_fixes in dbt_project.yml).

Usage:  python -m layer1.quality.narrative [--db lake/maple.duckdb]
"""
import argparse
import json

from layer1.pipeline import config
from layer1.pipeline.db import connect

def linked_documents(con, table):
    base = table.split(".")[0]
    docs = con.execute("""
        SELECT doc_id, doc_type, title, file_path FROM bronze.reference_documents
        WHERE doc_type IN ('release_note', 'incident_report', 'data_contract') AND related_table ILIKE '%' || ? || '%'
        UNION
        SELECT r.doc_id, r.doc_type, r.title, r.file_path FROM bronze.ops_events e
        JOIN bronze.reference_documents r ON r.doc_id = e.reference_doc_id
        WHERE e.affected_tables LIKE '%"' || ? || '"%'""", [base, base]).fetchall()
    out = []
    for doc_id, doc_type, title, path in docs:
        f = config.RAW / "files" / path if path else None
        text = f.read_text()[:1500] if f and f.exists() else ""
        out.append({"doc_id": doc_id, "type": doc_type, "title": title, "text": text})
    return out


FIXES = {   # proposed fixes by check; an engineer approves before any of them is applied
    "null_rate_by_day": "map channel_v2 back to channel (coalesce(channel_v2, channel)); approval id REL-0926-channel",
    "psi_last_3_days_vs_prior_4_weeks": "same as the null-rate alert: the channel column was renamed upstream",
    "references_existing_crm_id": "exclude these rows from customer counts and truth sets; ask the source owner to fix",
    "card_dpd_bucket_matches_dpd": "use dpd_bucket_recomputed in gold.c360_accounts (D-38); source bucket kept as received",
    "loan_dpd_bucket_matches_dpd": "use dpd_bucket_recomputed in gold.c360_accounts (D-38); source bucket kept as received",
    "card_dpd_missing": "exclude or report separately using dpd_missing; never infer days from cycles_delinquent",
    "loan_dpd_missing": "exclude or report separately using dpd_missing; never infer days from cycles_delinquent",
    "case_total_overdue_equals_account_past_due": "list the cases for the collections data owner; no automatic change",
}


def write_alerts(findings):
    lines, blocking = [], 0
    for f in findings:
        # Name a cause only when the check was matched to a known incident or release; table overlap alone is not one.
        cause = f"known {f['known_incident']}" if f["known_incident"] else "unexplained: no incident or release covers it"
        blocking += f["severity"] == "block" and f["status"] == "fail"
        lines.append(f"- [{f['status'].upper()}] {f['check']} on {f['table']}: {f['metric']} against {f['threshold']}. "
                     f"Cause: {cause}. Proposed fix: {FIXES.get(f['check'], 'investigate')} (needs engineer approval).")
    known = sum(1 for f in findings if f["known_incident"])
    summary = (f"{len(findings)} checks did not pass, {blocking} blocking. {known} are explained by known incidents "
               f"or releases. Proposed fixes apply only after approval.")
    return summary + "\n" + "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(config.FULL_DB))
    args = ap.parse_args(argv)
    con = connect(args.db)
    run_id = con.execute("SELECT arg_max(run_id, recorded_at) FROM gold.dq_report").fetchone()[0]
    rows = con.execute("""SELECT family, check_name, table_name, severity, metric, threshold, status, known_incident, detail
                          FROM gold.dq_report WHERE run_id = ? AND status <> 'pass' ORDER BY severity, family""",
                       [run_id]).fetchall()
    findings = [{"family": r[0], "check": r[1], "table": r[2], "severity": r[3], "metric": r[4], "threshold": r[5],
                 "status": r[6], "known_incident": r[7], "detail": json.loads(r[8] or "{}"),
                 "documents": linked_documents(con, r[2])} for r in rows]
    for f in findings:   # keep the prompt small: drop bulky distributions from the detail
        f["detail"].pop("distribution", None)
    text, source = write_alerts(findings), "rules"
    con.execute("""CREATE TABLE IF NOT EXISTS gold.dq_narrative (run_id VARCHAR, recorded_at TIMESTAMP, source VARCHAR,
                   text VARCHAR, _source_system VARCHAR, _source_file VARCHAR, _batch_id VARCHAR, _loaded_at TIMESTAMP,
                   _pipeline_run_id VARCHAR)""")
    con.execute("INSERT INTO gold.dq_narrative VALUES (?, now(), ?, ?, 'PIPELINE', 'dq_narrative', ?, now(), ?)",
                [run_id, source, text, run_id, run_id])
    print(text)
    con.close()


if __name__ == "__main__":
    main()
