-- Quarter-end history of every account. month_end_date comes in several formats; parse each one.
-- month_end follows the certified parsing (ISO, day-first, compact), as in the organisers' reference SQL;
-- month_end_lenient also reads month-first dates (03/31/2026: ~3% of rows), which month_end leaves NULL.
-- Golden ID is not taken from this table's crm_customer_id (noisy: 20% of accounts change it, decisions D-03).
CREATE OR REPLACE TABLE layer2.f_account_month AS
SELECT account_id,
       nullif(trim(product_type), '') AS product_type,
       nullif(trim(account_status), '') AS account_status,
       __MONTH_END__ AS month_end,
       __MONTH_END_LENIENT__ AS month_end_lenient,
       try_cast(dpd AS INTEGER) AS dpd,
       nullif(trim(dpd_bucket), '') AS dpd_bucket,
       layer2.bucket_from_dpd(try_cast(dpd AS INTEGER)) AS bucket_from_dpd,
       layer2.l2_bool(write_off_flag) AS write_off,
       try_cast(balance AS DOUBLE) AS balance
FROM bronze.account_monthly_snapshot;
