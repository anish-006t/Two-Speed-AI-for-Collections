-- D-39: the agent brief never shows a full phone number, a full email address or a date of birth.
SELECT case_id FROM {{ ref('agent_case_brief') }}
WHERE (phone_masked IS NOT NULL AND NOT regexp_full_match(phone_masked, '\*\*\*-\*\*\*-\d{4}'))
   OR (email_masked IS NOT NULL AND NOT regexp_full_match(email_masked, '.\*\*\*@.+'))
UNION ALL
SELECT 'column:' || column_name FROM information_schema.columns
WHERE table_schema = 'access_agent' AND column_name IN ('date_of_birth', 'dob', 'primary_phone_e164', 'email',
                                                        'best_reachable_phone', 'national_id_hash')
