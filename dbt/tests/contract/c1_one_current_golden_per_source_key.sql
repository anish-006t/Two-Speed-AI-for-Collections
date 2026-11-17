-- Contract rule 1: each source_system + source_id maps to exactly one current golden ID.
SELECT source_key FROM {{ source('identity', 'crosswalk') }} WHERE valid_to IS NULL GROUP BY 1 HAVING count(*) > 1
