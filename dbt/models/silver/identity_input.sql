-- One row per source customer key across CRM, cards, loans, deposits and bureau (architecture §6).
-- This is the only table Splink reads.
WITH crm AS (
    SELECT 'CRM' AS source_system, source_id, first_name, first_initial, middle_initial, last_name, name_suffix,
           preferred_name, dob, false AS dob_conflict, phone_e164, phone_first6, phone_last2, false AS phone_masked,
           postal_code, fsa, email, national_id_hash, NULL::VARCHAR[] AS joint_or_coborrower_refs,
           snapshot_date, _source_system, _source_file, _batch_id, _loaded_at, _pipeline_run_id
    FROM {{ ref('stg_crm_identity') }}
),
households AS (
    SELECT source_system, source_id, list(DISTINCT related_source_id ORDER BY related_source_id) AS refs
    FROM {{ ref('household_refs') }}
    GROUP BY ALL
),
products AS (
    {% for m in ['card_customers', 'loan_customers', 'deposit_customers', 'bureau_subjects'] %}
    SELECT source_system, source_id, first_name, first_initial, middle_initial, last_name, name_suffix,
           NULL::VARCHAR AS preferred_name, dob, dob_conflict, phone_e164, phone_first6, phone_last2, phone_masked,
           postal_code, fsa, NULL::VARCHAR AS email, NULL::VARCHAR AS national_id_hash,
           snapshot_date, _source_system, _source_file, _batch_id, _loaded_at, _pipeline_run_id
    FROM {{ ref(m) }}
    {% if not loop.last %}UNION ALL{% endif %}
    {% endfor %}
)
SELECT source_system || ':' || source_id AS source_key, * FROM crm
UNION ALL BY NAME
SELECT p.source_system || ':' || p.source_id AS source_key, p.*, h.refs AS joint_or_coborrower_refs
FROM products p
LEFT JOIN households h USING (source_system, source_id)
