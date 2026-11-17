-- Collections cases with the case's golden customer ID, and a case-link alarm (architecture §7 constraints):
-- an account in the case belongs to a different golden customer than the case's CRM reference.
WITH xw AS (SELECT source_key, golden_customer_id FROM {{ source('identity', 'crosswalk') }} WHERE valid_to IS NULL),
case_accounts AS (
    SELECT k.case_id, unnest(parse_account_ids(k.account_ids)) AS account_id FROM {{ source('bronze', 'collections_cases') }} k
),
alarm AS (
    SELECT ca.case_id, list(DISTINCT a.golden_customer_id) AS account_golden_ids
    FROM case_accounts ca JOIN {{ ref('c360_accounts') }} a USING (account_id)
    WHERE a.golden_customer_id IS NOT NULL GROUP BY 1
)
SELECT x.golden_customer_id,
       x.golden_customer_id IS NULL AS customer_ref_unresolved,   -- deleted or non-existent CRM ID (D-13)
       coalesce(NOT list_contains(al.account_golden_ids, x.golden_customer_id)
                OR len(al.account_golden_ids) > 1, false) AS case_link_alarm,
       al.account_golden_ids,
       -- current_bucket stays as received; about 4% disagree with current_dpd (benchmark trap, decisions D-38)
       dpd_to_bucket(try_cast(k.current_dpd AS INTEGER)) AS current_bucket_recomputed,
       coalesce(k.current_bucket <> dpd_to_bucket(try_cast(k.current_dpd AS INTEGER)), false) AS current_bucket_mismatch,
       parse_account_ids(k.account_ids) AS account_id_list,
       k.* EXCLUDE (vulnerable_customer_flag),   -- protected (D-37): restricted.protected_attributes
       'COLLECTIONS' AS _source_system, '{{ invocation_id }}' AS _pipeline_run_id
FROM {{ source('bronze', 'collections_cases') }} k
LEFT JOIN xw x ON x.source_key = 'CRM:' || k.coll_customer_ref
LEFT JOIN alarm al USING (case_id)
