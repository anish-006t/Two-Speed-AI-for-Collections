"""Tune the decision bands on the truth sets (architecture §7 "initial values, tuned on the truth set").

CLAUDE.md rule 1 fixes the auto-link floor at 0.95, so the sweep only considers auto-link thresholds >= 0.95 and
review floors below them. Scored at pair level on product <-> CRM Splink pairs, against a consensus truth set:
pairs where the collections case and the latest monthly snapshot name the same CRM record (their errors are
independent, so agreement removes most truth-set noise; decisions D-30). A link counts as correct when the CRM key
it points to is in the same golden cluster as the truth CRM key (CRM duplicates merged by D1 are the same person).

Writes identity.threshold_sweep and prints the table. It does not change the bands: a person picks from the table
and edits AUTO_LINK / REVIEW in layer1/identity/decisions.py.

Usage:  python -m layer1.identity.tune [--db lake/maple.duckdb]
"""
import argparse

from layer1.identity import evaluate
from layer1.pipeline import config
from layer1.pipeline.db import connect

AUTO_LINK_GRID = [0.95, 0.97, 0.98, 0.99, 0.995, 0.999]
REVIEW_GRID = [0.50, 0.60, 0.70, 0.80, 0.90]

SWEEP_SQL = """
CREATE OR REPLACE TABLE identity.threshold_sweep AS
WITH cur AS (SELECT source_key, golden_customer_id FROM identity.crosswalk WHERE valid_to IS NULL),
consensus AS (   -- product key -> truth CRM key where cases and the snapshot agree
    SELECT DISTINCT a.product_key, a.crm_key FROM truth_pairs a JOIN truth_pairs b USING (product_key, crm_key)
    WHERE a.truth_set = 'cases' AND b.truth_set = 'monthly_snapshot'
),
cand AS (        -- every product <-> CRM Splink pair touching a consensus product key
    SELECT CASE WHEN m.system_l = 'CRM' THEN m.key_r ELSE m.key_l END AS product_key,
           CASE WHEN m.system_l = 'CRM' THEN m.key_l ELSE m.key_r END AS crm_key,
           m.match_probability AS p,
           m.dob_identical AND NOT (m.c_household OR m.c_dob_conflict OR m.c_suffix_conflict OR m.c_lookalike
                                    OR m.c_possible_twins) AS eligible,
           m.dob_near_miss AND m.p_without_dob >= 0.95 AS near_miss
    FROM identity.match_pairs m
    WHERE m.match_probability IS NOT NULL AND (m.system_l = 'CRM') <> (m.system_r = 'CRM')
      AND (CASE WHEN m.system_l = 'CRM' THEN m.key_r ELSE m.key_l END) IN (SELECT product_key FROM consensus)
),
scored AS (
    SELECT c.*, g1.golden_customer_id = g2.golden_customer_id AS correct
    FROM cand c JOIN consensus t USING (product_key)
    JOIN cur g1 ON g1.source_key = c.crm_key JOIN cur g2 ON g2.source_key = t.crm_key
),
grid AS (SELECT a.t AS auto_link, r.t AS review_floor FROM (SELECT unnest(?::DOUBLE[]) t) a,
                                                        (SELECT unnest(?::DOUBLE[]) t) r WHERE r.t < a.t)
SELECT g.auto_link, g.review_floor,
       count(*) FILTER (WHERE s.eligible AND s.p >= g.auto_link) AS auto_links,
       count(*) FILTER (WHERE s.eligible AND s.p >= g.auto_link AND NOT s.correct) AS wrong_auto_links,
       1 - count(*) FILTER (WHERE s.eligible AND s.p >= g.auto_link AND NOT s.correct)
             / nullif(count(*) FILTER (WHERE s.eligible AND s.p >= g.auto_link), 0) AS precision,
       count(DISTINCT s.product_key) FILTER (WHERE s.eligible AND s.p >= g.auto_link AND s.correct)
             / (SELECT count(DISTINCT product_key) FROM consensus) AS recall_auto,
       count(*) FILTER (WHERE (s.p >= g.review_floor AND NOT (s.eligible AND s.p >= g.auto_link)) OR s.near_miss)
             AS review_pairs,
       count(*) FILTER (WHERE s.p >= g.review_floor AND s.p < g.auto_link AND s.correct) AS true_pairs_in_review
FROM grid g CROSS JOIN scored s
GROUP BY ALL ORDER BY g.auto_link, g.review_floor
"""


def run(con):
    con.execute("CREATE OR REPLACE TEMP TABLE truth_pairs AS SELECT * FROM (" + evaluate.TRUTH_SQL.split(" AS\n", 1)[1] + ")")
    con.execute(SWEEP_SQL, [AUTO_LINK_GRID, REVIEW_GRID])
    return con.execute("SELECT * FROM identity.threshold_sweep").df()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(config.FULL_DB))
    args = ap.parse_args(argv)
    con = connect(args.db)
    df = run(con)
    print("Pairs scored on the consensus truth set (product keys with matching case and snapshot CRM references).")
    print(df.to_string(index=False, float_format=lambda v: f"{v:.5f}"))
    con.close()


if __name__ == "__main__":
    main()
