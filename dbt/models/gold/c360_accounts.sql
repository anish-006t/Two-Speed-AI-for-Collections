-- Every card, loan and deposit account with its golden customer ID (via identity.crosswalk).
-- dpd and dpd_bucket stay as received; dpd_bucket_recomputed is derived from dpd (NULL when dpd is missing, about 8%
-- of card and loan rows), and dpd_bucket_mismatch marks rows where the two disagree (decisions D-38).
WITH xw AS (SELECT source_key, golden_customer_id FROM {{ source('identity', 'crosswalk') }} WHERE valid_to IS NULL),
accounts AS (
    SELECT 'CARDS:' || src_customer_ref AS source_key, card_account_id AS account_id, 'card' AS product,
           product_code AS product_type, account_status, try_cast(open_date AS DATE) AS open_date,
           try_cast(current_balance AS DOUBLE) AS balance, try_cast(credit_limit AS DOUBLE) AS credit_limit,
           try_cast(current_balance AS DOUBLE) AS credit_exposure,
           try_cast(past_due_amount AS DOUBLE) AS past_due_amount, try_cast(dpd AS INTEGER) AS dpd, dpd_bucket,
           chargeoff_flag = 'true' AS chargeoff_flag, hardship_program_flag = 'true' AS hardship_program_flag,
           try_cast(snapshot_date AS DATE) AS snapshot_date,
           'CARDS' AS _source_system, _source_file, _batch_id, _loaded_at
    FROM {{ source('bronze', 'card_accounts') }}
    UNION ALL
    SELECT 'LENDING:' || src_customer_ref, loan_id, 'loan', product_type, NULL, try_cast(origination_date AS DATE),
           try_cast(total_outstanding AS DOUBLE), try_cast(original_amount AS DOUBLE),
           try_cast(total_outstanding AS DOUBLE),
           try_cast(past_due_amount AS DOUBLE), try_cast(dpd AS INTEGER), dpd_bucket,
           chargeoff_flag = 'true', hardship_program_flag = 'true', try_cast(snapshot_date AS DATE),
           'LENDING', _source_file, _batch_id, _loaded_at
    FROM {{ source('bronze', 'loan_accounts') }}
    UNION ALL
    SELECT 'CORE_BANKING:' || pad_cif(src_customer_ref), deposit_account_id, 'deposit', product_type, account_status,
           try_cast(open_date AS DATE), try_cast(current_balance AS DOUBLE), try_cast(overdraft_limit AS DOUBLE),
           try_cast(overdraft_used AS DOUBLE),
           NULL, try_cast(overdraft_dpd AS INTEGER), overdraft_dpd_bucket,
           NULL, NULL, try_cast(snapshot_date AS DATE),
           'CORE_BANKING', _source_file, _batch_id, _loaded_at
    FROM {{ source('bronze', 'deposit_accounts') }}
)
SELECT x.golden_customer_id, a.*,
       a.dpd IS NULL AS dpd_missing,
       dpd_to_bucket(a.dpd) AS dpd_bucket_recomputed,
       coalesce(a.dpd_bucket <> dpd_to_bucket(a.dpd), false) AS dpd_bucket_mismatch,
       '{{ invocation_id }}' AS _pipeline_run_id
FROM accounts a
LEFT JOIN xw x USING (source_key)
