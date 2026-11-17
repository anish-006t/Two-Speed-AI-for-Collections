-- One row per card or loan account (this release has one row per account; snapshot_date can be older than 28 Sep).
-- dpd is missing for ~8% of card accounts: kept as NULL and flagged, never treated as 0.
CREATE OR REPLACE TABLE layer2.f_accounts AS
WITH a AS (
    SELECT card_account_id AS account_id, 'card' AS product, 'card' AS product_type,
           nullif(trim(product_code), '') AS product_code, nullif(trim(account_status), '') AS account_status,
           try_cast(snapshot_date AS DATE) AS snapshot_date, try_cast(dpd AS INTEGER) AS dpd,
           nullif(trim(dpd_bucket), '') AS dpd_bucket, layer2.l2_bool(chargeoff_flag) AS chargeoff_flag,
           try_cast(chargeoff_date AS DATE) AS chargeoff_date, try_cast(chargeoff_amount AS DOUBLE) AS chargeoff_amount,
           try_cast(current_balance AS DOUBLE) AS balance,
           coalesce(trim(account_status) IN ('active', 'blocked'), false) AS is_active
    FROM bronze.card_accounts
    UNION ALL
    SELECT loan_id, 'loan', nullif(trim(product_type), ''), nullif(trim(product_code), ''), NULL,
           try_cast(snapshot_date AS DATE), try_cast(dpd AS INTEGER), nullif(trim(dpd_bucket), ''),
           layer2.l2_bool(chargeoff_flag), try_cast(chargeoff_date AS DATE), try_cast(chargeoff_amount AS DOUBLE),
           try_cast(total_outstanding AS DOUBLE), NOT layer2.l2_bool(chargeoff_flag)
    FROM bronze.loan_accounts
)
SELECT a.*, a.dpd IS NULL AS dpd_missing, layer2.bucket_from_dpd(a.dpd) AS bucket_from_dpd,
       __ACCOUNT_GOLDEN__ AS golden_customer_id
FROM a __ACCOUNT_JOIN__;
