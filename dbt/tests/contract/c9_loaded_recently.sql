{{ config(severity='warn') }}
-- Contract rule 9: _loaded_at within one day of the source snapshot date.
SELECT golden_customer_id FROM {{ ref('c360_customer') }}
WHERE _loaded_at > TIMESTAMP '{{ var("snapshot_date") }}' + INTERVAL 2 DAY
LIMIT 1
