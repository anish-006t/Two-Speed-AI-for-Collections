"""Tier 1 deterministic rules (architecture §7, decisions D-01, D-02).

D1: two current CRM rows share national_id_hash.
D2: a deposit CIF (padded to 10 digits) equals a bureau bank_subject_ref.
Both link only when the DOBs are identical; otherwise the pair goes to review (CLAUDE.md rule 1). A key with
conflicting DOBs across its accounts has dob = NULL, so it can never pass this test.
D3 (PAD / autopay to a deposit account) is Splink evidence only and lives in splink_model.py.
"""

DETERMINISTIC_PAIRS_SQL = """
CREATE OR REPLACE TABLE identity.deterministic_pairs AS
WITH d1 AS (
    SELECT a.source_key AS key_l, b.source_key AS key_r, 'D1' AS rule,
           a.dob IS NOT NULL AND a.dob = b.dob AS dob_identical
    FROM silver.identity_input a
    JOIN silver.identity_input b
      ON a.source_system = 'CRM' AND b.source_system = 'CRM'
     AND a.national_id_hash = b.national_id_hash AND a.source_key < b.source_key
), d2 AS (
    SELECT a.source_key, b.source_key, 'D2',
           a.dob IS NOT NULL AND a.dob = b.dob
    FROM silver.identity_input a
    JOIN silver.identity_input b
      ON a.source_system = 'CORE_BANKING' AND b.source_system = 'BUREAU' AND a.source_id = b.source_id
)
-- Keys are ordered (key_l < key_r) to match Splink pairs in decisions.MATCH_PAIRS_SQL.
SELECT least(key_l, key_r) AS key_l, greatest(key_l, key_r) AS key_r, rule, dob_identical,
       CASE WHEN dob_identical THEN 'link' ELSE 'review' END AS decision,
       CASE WHEN dob_identical THEN NULL ELSE 'dob_not_identical' END AS reason
FROM (SELECT * FROM d1 UNION ALL SELECT * FROM d2)
"""


def run(con):
    con.execute("CREATE SCHEMA IF NOT EXISTS identity")
    con.execute(DETERMINISTIC_PAIRS_SQL)
    return con.execute(
        "SELECT rule, decision, count(*) FROM identity.deterministic_pairs GROUP BY ALL ORDER BY ALL"
    ).fetchall()
