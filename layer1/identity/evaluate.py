"""Match quality per source on the truth set that covers it (architecture §9). Truth links only evaluate.

Scored at the cluster level: a truth pair counts as found when both keys sit in the same golden cluster
(coll_customer_ref may point at a CRM duplicate, so exact-ID matching would be unfair).

precision = linked truth product keys whose cluster holds the truth CRM key / linked truth product keys whose
            cluster holds any CRM key
recall    = truth pairs in the same cluster / truth pairs
"""

TRUTH_SQL = """
CREATE OR REPLACE TEMP TABLE truth_pairs AS
WITH cur AS (SELECT source_key, source_system, golden_customer_id FROM identity.crosswalk WHERE valid_to IS NULL),
case_accts AS (   -- collections cases: CRM ref + listed accounts (cards, loans, deposits); D-13
    SELECT DISTINCT 'CRM:' || k.coll_customer_ref AS crm_key, unnest(parse_account_ids(k.account_ids)) AS account_id
    FROM bronze.collections_cases k
),
snap AS (         -- monthly snapshot: latest quarter's CRM ID per account; noisy, so reported separately (D-03)
    SELECT account_id, 'CRM:' || arg_max(crm_customer_id, month_end_date) AS crm_key
    FROM bronze.account_monthly_snapshot GROUP BY account_id
),
acct_owner AS (
    SELECT account_id, 'CARDS:' || source_id AS product_key FROM silver.stg_card_identity
    UNION ALL SELECT account_id, 'LENDING:' || source_id FROM silver.stg_loan_identity
    UNION ALL SELECT account_id, 'CORE_BANKING:' || source_id FROM silver.stg_deposit_identity WHERE source_id IS NOT NULL
)
SELECT DISTINCT 'cases' AS truth_set, o.product_key, c.crm_key FROM case_accts c JOIN acct_owner o USING (account_id)
WHERE c.crm_key IN (SELECT source_key FROM cur)
UNION ALL
SELECT DISTINCT 'monthly_snapshot', o.product_key, s.crm_key FROM snap s JOIN acct_owner o USING (account_id)
WHERE s.crm_key IN (SELECT source_key FROM cur)
"""

METRICS_SQL = """
INSERT INTO identity.match_quality
WITH cur AS (SELECT source_key, source_system, golden_customer_id FROM identity.crosswalk WHERE valid_to IS NULL),
crm_clusters AS (SELECT DISTINCT golden_customer_id FROM cur WHERE source_system = 'CRM'),
t AS (
    SELECT t.truth_set, p.source_system, t.product_key, t.crm_key,
           p.golden_customer_id = c.golden_customer_id AS same_cluster,
           p.golden_customer_id IN (SELECT golden_customer_id FROM crm_clusters) AS linked_to_crm
    FROM truth_pairs t JOIN cur p ON p.source_key = t.product_key JOIN cur c ON c.source_key = t.crm_key
)
SELECT ?, now(), truth_set, source_system, count(*) AS n_truth_pairs,
       count(*) FILTER (WHERE linked_to_crm) AS n_linked,
       count(*) FILTER (WHERE same_cluster) AS n_correct,
       count(*) FILTER (WHERE same_cluster) / nullif(count(*) FILTER (WHERE linked_to_crm), 0) AS precision,
       count(*) FILTER (WHERE same_cluster) / count(*) AS recall,
       CASE truth_set WHEN 'cases' THEN 'leans towards delinquent customers (only customers with a case)'
                      ELSE 'noisy: 20% of accounts change CRM ID across quarters (D-03)' END AS bias_note
FROM t GROUP BY truth_set, source_system
UNION ALL
-- CRM duplicates: CRM's own pointer. Incomplete, so recall only.
SELECT ?, now(), 'crm_duplicate_of', 'CRM', count(*), NULL,
       count(*) FILTER (WHERE a.golden_customer_id = b.golden_customer_id), NULL,
       count(*) FILTER (WHERE a.golden_customer_id = b.golden_customer_id) / count(*),
       'incomplete pointer: measures recall, not precision'
FROM silver.stg_crm_identity d
JOIN cur a ON a.source_key = 'CRM:' || d.source_id
JOIN cur b ON b.source_key = 'CRM:' || d.duplicate_of_crm_id
UNION ALL
-- Look-alikes: resolved joint holders / co-borrowers. Every pair must stay apart; any shared cluster is a false merge.
SELECT ?, now(), 'lookalikes_household', h.relation, count(*), NULL,
       count(*) FILTER (WHERE a.golden_customer_id = b.golden_customer_id), NULL, NULL,
       'n_correct here counts FALSE merges; must be 0'
FROM identity.household_links h
JOIN cur a ON a.source_key = h.key_a JOIN cur b ON b.source_key = h.key_b
WHERE h.resolves AND h.key_a <> h.key_b
GROUP BY h.relation
"""

DDL = """
CREATE TABLE IF NOT EXISTS identity.match_quality (
    run_id VARCHAR, recorded_at TIMESTAMP, truth_set VARCHAR, source_system VARCHAR, n_truth_pairs BIGINT, n_linked BIGINT,
    n_correct BIGINT, precision DOUBLE, recall DOUBLE, bias_note VARCHAR
)
"""


def run(con, run_id):
    con.execute(DDL)
    con.execute(TRUTH_SQL)
    con.execute(METRICS_SQL, [run_id, run_id, run_id])
    rows = con.execute("""
        SELECT truth_set, source_system, n_truth_pairs, n_linked, n_correct,
               round(precision, 4), round(recall, 4)
        FROM identity.match_quality WHERE run_id = ? ORDER BY 1, 2""", [run_id]).fetchall()
    return rows
