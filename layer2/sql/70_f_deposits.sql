-- Deposit accounts with payroll signals (payroll_delay_share). No names, balances by customer or descriptors.
CREATE OR REPLACE TABLE layer2.f_deposits AS
SELECT deposit_account_id AS account_id, nullif(trim(product_type), '') AS product_type,
       nullif(trim(account_status), '') AS account_status, try_cast(snapshot_date AS DATE) AS snapshot_date,
       layer2.l2_bool(payroll_deposit_flag) AS payroll_deposit, try_cast(payroll_delay_days AS INTEGER) AS payroll_delay_days
FROM bronze.deposit_accounts;
