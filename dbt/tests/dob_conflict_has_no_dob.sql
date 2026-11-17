-- A key whose accounts disagree on DOB must not carry a DOB, so it can never auto-link (decisions D-11; rule 1).
SELECT source_key FROM {{ ref('identity_input') }} WHERE dob_conflict AND dob IS NOT NULL
