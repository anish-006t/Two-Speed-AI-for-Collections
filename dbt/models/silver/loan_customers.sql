-- One row per loan customer key (rolled up from account rows; see macros/staging.sql).
{{ customer_rollup('stg_loan_identity', 'LENDING') }}
