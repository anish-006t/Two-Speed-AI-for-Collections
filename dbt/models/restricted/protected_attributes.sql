-- Restricted zone (architecture §13): protected attributes, age, age band and AML fields, keyed by golden ID,
-- for the fairness audit only. Nothing outside this schema may select from it except aggregated fairness views.
WITH xw AS (SELECT source_key, golden_customer_id FROM {{ source('identity', 'crosswalk') }} WHERE valid_to IS NULL)
SELECT x.golden_customer_id, c.crm_customer_id,
       c.gender_code, c.marital_status, c.citizenship_status, c.household_size, c.dependants_count,
       c.newcomer_program_flag, c.newcomer_arrival_date, c.accessibility_needs_flag, c.accessibility_type,
       c.vulnerability_flag, c.vulnerability_type, c.vulnerability_source, c.age, c.age_band, c.aml_risk_rating, c.pep_flag,
       d.ccb_deposit_flag, v.case_vulnerable_flag, v.contact_vulnerability_disclosed,
       'CRM' AS _source_system, c._source_file, c._batch_id, c._loaded_at, '{{ invocation_id }}' AS _pipeline_run_id
FROM {{ source('bronze', 'customers') }} c
JOIN xw x ON x.source_key = 'CRM:' || c.crm_customer_id
LEFT JOIN (
    SELECT a.golden_customer_id, bool_or(b.ccb_deposit_flag = 'true') AS ccb_deposit_flag
    FROM {{ ref('c360_accounts') }} a JOIN {{ source('bronze', 'deposit_accounts') }} b ON b.deposit_account_id = a.account_id
    GROUP BY 1
) d ON d.golden_customer_id = x.golden_customer_id
LEFT JOIN (   -- vulnerability flags held on cases and contacts, moved out of gold (D-37)
    SELECT golden_customer_id, bool_or(case_flag) AS case_vulnerable_flag, bool_or(contact_flag) AS contact_vulnerability_disclosed
    FROM (
        SELECT x2.golden_customer_id, k.vulnerable_customer_flag = 'true' AS case_flag, NULL::BOOLEAN AS contact_flag
        FROM {{ source('bronze', 'collections_cases') }} k JOIN xw x2 ON x2.source_key = 'CRM:' || k.coll_customer_ref
        UNION ALL
        SELECT x3.golden_customer_id, NULL, h.vulnerability_disclosed_flag = 'true'
        FROM {{ source('bronze', 'contact_history') }} h JOIN xw x3 ON x3.source_key = 'CRM:' || h.crm_customer_id
    ) GROUP BY 1
) v ON v.golden_customer_id = x.golden_customer_id
WHERE c.is_deleted = 'false'
