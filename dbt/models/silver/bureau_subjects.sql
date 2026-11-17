-- One row per bureau subject (CIF). The release has one pull per subject; the rollup still guards against more.
{{ customer_rollup('stg_bureau_identity', 'BUREAU') }}
