-- Contract rule 6 (CLAUDE.md rule 2): no automatic link joins a joint holder, co-borrower, Jr/Sr or look-alike pair.
SELECT key_l, key_r, constraint_flags FROM {{ source('identity', 'match_pairs') }}
WHERE decision = 'link' AND decided_by = 'splink'
  AND (c_household OR c_suffix_conflict OR c_lookalike OR c_possible_twins OR c_dob_conflict)
