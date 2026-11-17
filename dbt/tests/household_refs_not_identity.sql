{{ config(severity="warn") }}
-- Joint holders and co-borrowers are household links, never the same key (CLAUDE.md rule 2).
SELECT * FROM {{ ref('household_refs') }}
WHERE source_system = related_source_system AND source_id = related_source_id
