-- Contract rule 3 (CLAUDE.md rule 1): differing DOBs are linked only through a logged human approval.
SELECT key_l, key_r FROM {{ source('identity', 'match_pairs') }}
WHERE decision = 'link' AND decided_by <> 'human' AND NOT dob_identical
