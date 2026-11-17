-- Analyst view of gold.promises_to_pay (decisions D-39).
{{ config(schema='access_analyst', materialized='view', alias='promises_to_pay') }}
SELECT * FROM {{ ref('promises_to_pay') }}
