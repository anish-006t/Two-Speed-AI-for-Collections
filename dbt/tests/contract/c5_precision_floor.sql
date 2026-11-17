-- Contract rule 5: precision per source on its truth set is at or above the floor (provisional 0.98, to confirm).
SELECT truth_set, source_system, precision FROM {{ source('identity', 'match_quality') }}
WHERE run_id = (SELECT arg_max(run_id, recorded_at) FROM {{ source('identity', 'match_quality') }})
  AND truth_set = 'cases' AND precision < {{ var('precision_floor', 0.98) }}
