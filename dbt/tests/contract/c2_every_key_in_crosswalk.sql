-- Contract rule 2: every current CRM row and every card, loan and deposit key is in the crosswalk.
SELECT i.source_key FROM {{ ref('identity_input') }} i
LEFT JOIN {{ source('identity', 'crosswalk') }} x ON x.source_key = i.source_key AND x.valid_to IS NULL
WHERE x.source_key IS NULL
