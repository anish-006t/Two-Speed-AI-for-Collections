-- Joint holders and co-borrowers: household links, never identity links (CLAUDE.md rule 2).
-- Most refs do not resolve to a record in the release (decisions D-12); `resolves` says which do.
WITH refs AS (
    SELECT DISTINCT 'CORE_BANKING' AS source_system, source_id, 'CORE_BANKING' AS related_source_system,
           joint_holder_cif AS related_source_id, 'joint_holder' AS relation, account_id
    FROM {{ ref('stg_deposit_identity') }}
    WHERE joint_holder_cif IS NOT NULL AND source_id IS NOT NULL
    UNION ALL
    SELECT DISTINCT 'LENDING', source_id, 'LENDING', co_borrower_ref, 'co_borrower', account_id
    FROM {{ ref('stg_loan_identity') }}
    WHERE co_borrower_ref IS NOT NULL AND source_id IS NOT NULL
)
SELECT r.*,
       coalesce(d.source_id, l.source_id, b.source_id) IS NOT NULL AS resolves,
       '{{ invocation_id }}' AS _pipeline_run_id
FROM refs r
LEFT JOIN (SELECT DISTINCT source_id FROM {{ ref('stg_deposit_identity') }}) d
       ON r.relation = 'joint_holder' AND d.source_id = r.related_source_id
LEFT JOIN (SELECT DISTINCT source_id FROM {{ ref('stg_bureau_identity') }}) b
       ON r.relation = 'joint_holder' AND b.source_id = r.related_source_id
LEFT JOIN (SELECT DISTINCT source_id FROM {{ ref('stg_loan_identity') }}) l
       ON r.relation = 'co_borrower' AND l.source_id = r.related_source_id
