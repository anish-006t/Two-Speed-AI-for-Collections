-- Fairness audit (architecture §9): link, single-record and review rates per protected group, aggregated only.
-- A gap triggers an investigation of cleaning rules or comparisons; it never changes an individual decision.
{% set groups = ['gender_code', 'marital_status', 'citizenship_status', 'newcomer_program_flag',
                 'accessibility_needs_flag', 'age_band'] %}
WITH g AS (
    SELECT p.*, f.identity_confidence, f.n_keys, f.in_review
    FROM {{ ref('protected_attributes') }} p
    JOIN {{ source('identity', 'golden_confidence') }} f USING (golden_customer_id)
)
{% for grp in groups %}
SELECT '{{ grp }}' AS attribute, coalesce({{ grp }}::VARCHAR, '(missing)') AS grp, count(*) AS customers,
       avg((n_keys > 1)::INT) AS linked_rate, avg((n_keys = 1)::INT) AS single_record_rate,
       avg(in_review::INT) AS review_rate, avg((identity_confidence = 'high')::INT) AS high_confidence_rate
FROM g GROUP BY 1, 2 HAVING count(*) >= 50     -- small groups suppressed
{% if not loop.last %}UNION ALL{% endif %}
{% endfor %}
