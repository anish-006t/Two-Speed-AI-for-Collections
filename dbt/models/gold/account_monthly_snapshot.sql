-- Quarter-end account history (Dec 2023 to Jun 2026, plus charged-off accounts) with clean dates and buckets.
-- Source values stay as received; corrected columns sit beside them (decisions D-38):
--   month_end_date      parsed from four formats (YYYY-MM-DD, YYYYMMDD, DD/MM/YYYY, MM/DD/YYYY); NULL unless a month end
--   month_end_date_format  which one; the benchmark reference SQL for BQ-017 reads only DD/MM/YYYY slash dates
--   dpd_missing         dpd is empty (about 8% of rows); never filled from cycles
--   dpd_bucket_recomputed / dpd_bucket_mismatch   bucket derived from dpd, and whether the source bucket disagrees
-- golden_customer_id comes from the account (gold.c360_accounts), not from this table's crm_customer_id, which is noisy
-- (decisions D-03).
SELECT
    a.golden_customer_id,
    s.account_id,
    s.crm_customer_id AS crm_customer_id_as_received,
    s.product_type,
    s.month_end_date AS month_end_date_raw,
    parse_month_end(s.month_end_date) AS month_end_date,
    month_end_format(s.month_end_date) AS month_end_date_format,
    s.account_status,
    try_cast(s.balance AS DOUBLE) AS balance,
    try_cast(s.limit_or_original_amount AS DOUBLE) AS limit_or_original_amount,
    try_cast(s.minimum_due AS DOUBLE) AS minimum_due,
    try_cast(s.payment_made AS DOUBLE) AS payment_made,
    try_cast(s.dpd AS INTEGER) AS dpd,
    s.dpd IS NULL AS dpd_missing,
    s.dpd_bucket,
    dpd_to_bucket(try_cast(s.dpd AS INTEGER)) AS dpd_bucket_recomputed,
    coalesce(s.dpd_bucket <> dpd_to_bucket(try_cast(s.dpd AS INTEGER)), false) AS dpd_bucket_mismatch,
    s.overlimit_flag, s.write_off_flag,
    try_cast(s.salary_credit_amount AS DOUBLE) AS salary_credit_amount,
    'ACCOUNT_HISTORY' AS _source_system, s._source_file, s._batch_id, s._loaded_at,
    '{{ invocation_id }}' AS _pipeline_run_id
FROM {{ source('bronze', 'account_monthly_snapshot') }} s
LEFT JOIN {{ ref('c360_accounts') }} a ON a.account_id = s.account_id
