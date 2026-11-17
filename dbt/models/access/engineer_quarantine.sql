-- Engineer view of silver.quarantine (decisions D-39): rule, reason and keys, with the failing value masked.
{{ config(schema='access_engineer', materialized='view', alias='quarantine') }}
SELECT * EXCLUDE (raw_value), {{ mask_text('raw_value') }} AS raw_value_masked FROM {{ ref('quarantine') }}
