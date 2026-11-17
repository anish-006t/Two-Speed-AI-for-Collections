-- Analyst view of gold.incident_effects (decisions D-39).
{{ config(schema='access_analyst', materialized='view', alias='incident_effects') }}
SELECT * FROM {{ ref('incident_effects') }}
