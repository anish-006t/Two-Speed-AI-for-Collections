-- Analyst view of gold.cases (decisions D-39).
{{ config(schema='access_analyst', materialized='view', alias='cases') }}
SELECT * FROM {{ ref('cases') }}
