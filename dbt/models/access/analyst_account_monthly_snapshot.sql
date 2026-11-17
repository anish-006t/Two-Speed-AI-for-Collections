-- Analyst view of gold.account_monthly_snapshot (decisions D-39).
{{ config(schema='access_analyst', materialized='view', alias='account_monthly_snapshot') }}
SELECT * FROM {{ ref('account_monthly_snapshot') }}
