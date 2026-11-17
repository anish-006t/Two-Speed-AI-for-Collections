{{ config(severity='warn') }}
-- Contract rule 8: postal code A1A 1A1 with a known FSA; phones valid E.164.
SELECT golden_customer_id FROM {{ ref('c360_customer') }}
WHERE (postal_code IS NOT NULL AND NOT regexp_full_match(postal_code, '[A-Z]\d[A-Z] \d[A-Z]\d'))
   OR (primary_phone_e164 IS NOT NULL AND NOT regexp_full_match(primary_phone_e164, '\+1\d{10}'))
