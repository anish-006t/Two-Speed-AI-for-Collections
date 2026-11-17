-- Loan PAD and card autopay accounts that point at a deposit account (rule D3, decisions D-14).
-- Splink evidence only, never a link on its own; 'EXTERNAL' is ignored.
WITH pay AS (
    SELECT 'LENDING' AS source_system, source_id, account_id, 'loan_pad' AS link_type,
           pad_source_account AS deposit_account_id
    FROM {{ ref('stg_loan_identity') }}
    WHERE pad_source_account IS NOT NULL AND pad_source_account <> 'EXTERNAL'
    UNION ALL
    SELECT 'CARDS', source_id, account_id, 'card_autopay', autopay_source_account
    FROM {{ ref('stg_card_identity') }}
    WHERE autopay_source_account IS NOT NULL AND autopay_source_account <> 'EXTERNAL'
)
SELECT p.*, d.source_id AS deposit_cif, d.joint_account_flag = 'true' AS deposit_is_joint,
       d.source_id IS NOT NULL AS resolves, '{{ invocation_id }}' AS _pipeline_run_id
FROM pay p
LEFT JOIN {{ ref('stg_deposit_identity') }} d ON d.account_id = p.deposit_account_id
