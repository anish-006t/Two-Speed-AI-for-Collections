-- Analyst view of the golden customer (architecture §13; decisions D-39): no names, phones, email, DOB, postal code
-- or FSA (FSA is a proxy for protected grounds, benchmark BQ-024), no source keys. Aggregate questions only.
{{ config(schema='access_analyst', materialized='view', alias='customers') }}
SELECT * EXCLUDE (crm_customer_ids, source_keys, first_name, last_name, primary_phone_e164, best_reachable_phone, email,
                  date_of_birth, postal_code, survivorship_source)
FROM {{ ref('c360_customer') }}
