-- One row per card customer key (rolled up from account rows; see macros/staging.sql).
{{ customer_rollup('stg_card_identity', 'CARDS') }}
