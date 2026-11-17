-- Analyst view of gold.c360_accounts (decisions D-39).
{{ config(schema='access_analyst', materialized='view', alias='accounts') }}
SELECT * FROM {{ ref('c360_accounts') }}
