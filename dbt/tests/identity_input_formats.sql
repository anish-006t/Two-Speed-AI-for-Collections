-- Parsed identity fields must have their target formats (architecture §6; contract rule 8).
SELECT source_key
FROM {{ ref('identity_input') }}
WHERE (phone_e164 IS NOT NULL AND NOT regexp_full_match(phone_e164, '\+1\d{10}'))
   OR (postal_code IS NOT NULL AND NOT regexp_full_match(postal_code, '[A-Z]\d[A-Z] \d[A-Z]\d'))
   OR (fsa IS NOT NULL AND fsa <> left(postal_code, 3))
   OR (source_system = 'CORE_BANKING' AND NOT regexp_full_match(source_id, '\d{10}'))
   OR (phone_masked AND phone_e164 IS NOT NULL)
