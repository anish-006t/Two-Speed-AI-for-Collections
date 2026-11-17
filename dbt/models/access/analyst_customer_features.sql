-- Analyst view of gold.l3_customer_features (decisions D-39).
{{ config(schema='access_analyst', materialized='view', alias='customer_features') }}
SELECT * FROM {{ ref('l3_customer_features') }}
