-- One row per deposit customer key (rolled up from account rows; see macros/staging.sql).
{{ customer_rollup('stg_deposit_identity', 'CORE_BANKING') }}
