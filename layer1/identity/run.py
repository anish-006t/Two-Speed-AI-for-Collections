"""Identity resolution run: deterministic rules -> Splink -> constraints -> clustering -> crosswalk -> confidence.

Usage:  python -m layer1.identity.run [--db lake/dev.duckdb] [--skip-splink]
Writes the identity schema: deterministic_pairs, splink_input, splink_predictions, match_pairs, household_links,
review_queue, crosswalk (SCD2), golden_ids, golden_alias, golden_confidence, run_log; then evaluate.py's match_quality.
"""
import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from layer1.identity import clustering, decisions, deterministic, evaluate, splink_model
from layer1.pipeline import config
from layer1.pipeline.db import connect

PERSISTENT_DDL = """
CREATE TABLE IF NOT EXISTS identity.crosswalk (
    source_system VARCHAR, source_id VARCHAR, source_key VARCHAR, golden_customer_id VARCHAR,
    link_status VARCHAR,            -- linked | in_review | single
    match_probability DOUBLE, rule VARCHAR, match_weights JSON, reviewed_by VARCHAR,
    valid_from TIMESTAMP, valid_to TIMESTAMP, _pipeline_run_id VARCHAR
);
CREATE TABLE IF NOT EXISTS identity.golden_ids (golden_customer_id VARCHAR, seq BIGINT, created_at TIMESTAMP, created_run VARCHAR);
CREATE TABLE IF NOT EXISTS identity.golden_alias (alias_id VARCHAR, golden_customer_id VARCHAR, reason VARCHAR, run_id VARCHAR, recorded_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS identity.run_log (run_id VARCHAR, recorded_at TIMESTAMP, step VARCHAR, detail JSON);
"""

REVIEW_QUEUE_SQL = """
CREATE OR REPLACE TABLE identity.review_queue AS
WITH open_case AS (   -- keys with an open collections case, and the exposure behind them
    SELECT 'CRM:' || coll_customer_ref AS source_key, max(try_cast(total_exposure AS DOUBLE)) AS exposure
    FROM silver.stg_crm_identity c JOIN bronze.collections_cases k ON k.coll_customer_ref = c.source_id
    WHERE k.outcome IS NULL GROUP BY 1
)
SELECT *, row_number() OVER (ORDER BY has_open_case DESC, exposure DESC, certainty ASC, key_l, key_r) AS priority
FROM (
    SELECT m.key_l, m.key_r, m.match_probability, m.constraint_flags, m.det_rule, m.system_l, m.system_r,
           (ol.source_key IS NOT NULL OR orr.source_key IS NOT NULL) AS has_open_case,
           greatest(coalesce(ol.exposure, 0), coalesce(orr.exposure, 0)) AS exposure,
           abs(coalesce(m.match_probability, 0.5) - 0.5) AS certainty
    FROM identity.match_pairs m
    LEFT JOIN open_case ol ON ol.source_key = m.key_l
    LEFT JOIN open_case orr ON orr.source_key = m.key_r
    WHERE m.decision = 'review'
)
"""

CONFIDENCE_SQL = """
CREATE OR REPLACE TABLE identity.golden_confidence AS
WITH members AS (
    SELECT golden_customer_id, source_key, source_system FROM identity.crosswalk WHERE valid_to IS NULL
), pending AS (
    SELECT key_l AS source_key FROM identity.review_queue UNION SELECT key_r FROM identity.review_queue
), thin AS (
    SELECT DISTINCT x.golden_customer_id
    FROM identity.match_pairs m JOIN members x ON x.source_key = m.key_l
    WHERE m.decision = 'link' AND m.thin_evidence
), changed AS (SELECT unnest(?::VARCHAR[]) AS golden_customer_id)
SELECT g.golden_customer_id,
       count(*) AS n_keys,
       bool_or(g.source_system = 'CRM') AS has_crm,
       bool_or(p.source_key IS NOT NULL) AS in_review,
       bool_or(t.golden_customer_id IS NOT NULL) AS thin_link,
       bool_or(c.golden_customer_id IS NOT NULL) AS changed_this_run,
       CASE
           WHEN bool_or(p.source_key IS NOT NULL) OR NOT bool_or(g.source_system = 'CRM') THEN 'low'
           WHEN bool_or(t.golden_customer_id IS NOT NULL) OR bool_or(c.golden_customer_id IS NOT NULL) THEN 'medium'
           ELSE 'high'
       END AS identity_confidence
FROM members g
LEFT JOIN pending p ON p.source_key = g.source_key
LEFT JOIN thin t ON t.golden_customer_id = g.golden_customer_id
LEFT JOIN changed c ON c.golden_customer_id = g.golden_customer_id
GROUP BY g.golden_customer_id
"""


def log(con, run_id, step, detail):
    print(f"[{step}] {detail}", flush=True)
    con.execute("INSERT INTO identity.run_log VALUES (?, now(), ?, ?)", [run_id, step, json.dumps(detail, default=str)])


def cluster_and_crosswalk(con, run_id, now):
    # Node ids, hash codes and edges come straight from DuckDB as arrays (millions of links fit in a few hundred MB).
    con.execute("""CREATE OR REPLACE TEMP TABLE node AS
                   SELECT unique_id, (row_number() OVER (ORDER BY unique_id) - 1)::BIGINT AS idx,
                          coalesce(dense_rank() OVER (ORDER BY national_id_hash) - 1, -1) AS hash_rank,
                          national_id_hash IS NULL AS no_hash
                   FROM identity.splink_input""")
    nodes = con.execute("SELECT unique_id, CASE WHEN no_hash THEN -1 ELSE hash_rank END FROM node ORDER BY idx").fetchnumpy()
    keys = nodes["unique_id"].tolist()
    hash_code = np.asarray(nodes[list(nodes)[1]], dtype=np.int64)
    e = con.execute("""
        SELECT nl.idx AS a, nr.idx AS b, coalesce(m.match_probability, 1.0) AS prob, m.decided_by = 'splink' AS removable,
               m.key_l, m.key_r
        FROM identity.match_pairs m JOIN node nl ON nl.unique_id = m.key_l JOIN node nr ON nr.unique_id = m.key_r
        WHERE m.decision = 'link' ORDER BY a, b""").fetchnumpy()
    kept, labels, still_bad = clustering.enforce_cluster_limits_arrays(
        len(keys), np.asarray(e["a"], dtype=np.int64), np.asarray(e["b"], dtype=np.int64),
        np.asarray(e["prob"], dtype=float), np.asarray(e["removable"], dtype=bool), hash_code)
    n_demoted = int((~kept).sum())
    if n_demoted:
        con.register("demoted_df", pd.DataFrame({"key_l": e["key_l"][~kept], "key_r": e["key_r"][~kept]}))
        con.execute("""
            UPDATE identity.match_pairs m SET decision = 'review',
                   constraint_flags = concat_ws(',', nullif(m.constraint_flags, ''), 'cluster_limit')
            FROM demoted_df d WHERE m.key_l = d.key_l AND m.key_r = d.key_r""")
        con.unregister("demoted_df")
    n_kept = int(kept.sum())
    del e, kept

    components = {}
    for k, lab in zip(keys, labels.tolist()):
        components.setdefault(lab, []).append(k)
    previous = dict(con.execute(
        "SELECT source_key, golden_customer_id FROM identity.crosswalk WHERE valid_to IS NULL").fetchall())
    created = dict(con.execute("SELECT golden_customer_id, seq FROM identity.golden_ids").fetchall())
    next_seq = (max(created.values()) + 1) if created else 1
    assignment, aliases, changed, next_seq = clustering.assign_golden_ids(components, previous, created, next_seq)

    new_ids = sorted(set(assignment.values()) - set(created))
    if new_ids:
        con.register("new_ids_df", pd.DataFrame({
            "golden_customer_id": new_ids, "seq": [int(g[len(clustering.ID_PREFIX):]) for g in new_ids]}))
        con.execute("INSERT INTO identity.golden_ids SELECT golden_customer_id, seq, ?, ? FROM new_ids_df", [now, run_id])
        con.unregister("new_ids_df")
    if aliases:
        con.register("alias_df", pd.DataFrame(aliases, columns=["alias_id", "golden_customer_id", "reason"]))
        con.execute("INSERT INTO identity.golden_alias SELECT alias_id, golden_customer_id, reason, ?, ? FROM alias_df",
                    [run_id, now])
        con.unregister("alias_df")

    con.register("assign_df", pd.DataFrame({"source_key": list(assignment), "golden_customer_id": list(assignment.values())}))
    con.execute("CREATE OR REPLACE TEMP TABLE assign AS SELECT * FROM assign_df")
    con.unregister("assign_df")
    # Per key: the strongest accepted link that attaches it, and whether it has a pending review.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE xw_new AS
        WITH edges AS (
            SELECT key_l AS k, * FROM identity.match_pairs WHERE decision = 'link'
            UNION ALL SELECT key_r AS k, * FROM identity.match_pairs WHERE decision = 'link'
        ), best AS (
            SELECT k, arg_max(decided_by, coalesce(match_probability, 1.0)) AS rule,
                   max(coalesce(match_probability, 1.0)) AS match_probability,
                   arg_max(to_json(struct_pack(first_name := bf_first_name, last_name := bf_last_name, dob := bf_dob,
                           phone := bf_phone, postal_code := bf_postal_code, email := bf_email,
                           national_id_hash := bf_national_id_hash, pay_accounts := bf_pay_accounts)),
                           coalesce(match_probability, 1.0)) AS match_weights,
                   max(reviewer) AS reviewed_by
            FROM edges GROUP BY k
        ), pending AS (SELECT key_l AS k FROM identity.match_pairs WHERE decision = 'review'
                       UNION SELECT key_r FROM identity.match_pairs WHERE decision = 'review')
        SELECT i.source_system, i.source_id, a.source_key, a.golden_customer_id,
               CASE WHEN b.k IS NOT NULL THEN 'linked' WHEN p.k IS NOT NULL THEN 'in_review' ELSE 'single' END AS link_status,
               b.match_probability, coalesce(b.rule, 'single') AS rule, b.match_weights, b.reviewed_by
        FROM assign a
        JOIN identity.splink_input i ON i.unique_id = a.source_key
        LEFT JOIN best b ON b.k = a.source_key
        LEFT JOIN pending p ON p.k = a.source_key
    """)
    # SCD2: close rows whose mapping changed, insert new current rows.
    con.execute("""
        UPDATE identity.crosswalk c SET valid_to = ?
        FROM xw_new n
        WHERE c.valid_to IS NULL AND c.source_key = n.source_key
          AND (c.golden_customer_id <> n.golden_customer_id OR c.link_status <> n.link_status
               OR c.rule <> n.rule OR c.match_probability IS DISTINCT FROM n.match_probability)""", [now])
    con.execute("""
        INSERT INTO identity.crosswalk
        SELECT n.*, ?::TIMESTAMP, NULL::TIMESTAMP, ?
        FROM xw_new n
        WHERE NOT EXISTS (SELECT 1 FROM identity.crosswalk c WHERE c.valid_to IS NULL AND c.source_key = n.source_key)
    """, [now, run_id])
    return {"accepted_links": n_kept, "demoted_to_review": n_demoted, "clusters_still_over_limit": len(still_bad),
            "golden_ids": len(set(assignment.values())), "new_golden_ids": len(new_ids), "aliases": len(aliases),
            "changed_golden_ids": len(changed)}, sorted(changed)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(config.FULL_DB))
    ap.add_argument("--skip-splink", action="store_true", help="reuse identity.splink_predictions from the last run")
    ap.add_argument("--threshold", type=float, default=0.3, help="lowest Splink probability kept as a candidate pair")
    args = ap.parse_args(argv)

    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    con = connect(args.db)
    con.execute("CREATE SCHEMA IF NOT EXISTS identity")
    con.execute(PERSISTENT_DDL)
    log(con, run_id, "start", {"db": args.db})

    log(con, run_id, "deterministic", deterministic.run(con))
    if not args.skip_splink:
        model_path = config.LAKE / f"splink_model_{Path(args.db).stem}.json"
        splink_model.train_and_predict(con, threshold=args.threshold, model_path=model_path)
        log(con, run_id, "splink", {"pairs": con.execute("SELECT count(*) FROM identity.splink_predictions").fetchone()[0],
                                    "model": str(model_path)})
    log(con, run_id, "decisions", decisions.run(con))
    detail, changed = cluster_and_crosswalk(con, run_id, now)
    log(con, run_id, "clustering", detail)
    con.execute(REVIEW_QUEUE_SQL)
    con.execute(CONFIDENCE_SQL, [changed])
    log(con, run_id, "confidence", con.execute(
        "SELECT identity_confidence, count(*) FROM identity.golden_confidence GROUP BY 1 ORDER BY 1").fetchall())
    log(con, run_id, "match_quality", evaluate.run(con, run_id))
    con.close()


if __name__ == "__main__":
    main()
