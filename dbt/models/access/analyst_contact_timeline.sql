-- Analyst view of gold.contact_timeline (decisions D-39).
-- Free-text SMS and email content removed (may carry names).
{{ config(schema='access_analyst', materialized='view', alias='contact_timeline') }}
SELECT * EXCLUDE (sms_text, email_subject) FROM {{ ref('contact_timeline') }}
