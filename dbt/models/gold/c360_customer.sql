-- gold.c360_customer: one row per golden customer (architecture §8 survivorship, §11 data contract).
-- Contact details: most recently updated CRM record; product values only when CRM has none.
-- DOB: the CRM value. Consent: most recent when identity confidence is high, most restrictive otherwise.
-- Restrictions and the hardship care flag: true if any merged record (or case) is true.
-- Protected attributes (including vulnerability, decisions D-37), age, age_band and AML fields are not here; they
-- live in restricted.protected_attributes.
WITH xw AS (
    SELECT * FROM {{ source('identity', 'crosswalk') }} WHERE valid_to IS NULL
),
conf AS (SELECT * FROM {{ source('identity', 'golden_confidence') }}),
crm AS (
    SELECT x.golden_customer_id, x.source_key, c.*, s.phone_e164 AS phone_norm, s.postal_code AS postal_norm,
           s.secondary_phone_e164, s.work_phone_e164,
           coalesce(try_cast(c.last_profile_update_ts AS TIMESTAMP), TIMESTAMP '1900-01-01') AS profile_ts,
           coalesce(try_cast(c.consent_last_updated_ts AS TIMESTAMP), TIMESTAMP '1900-01-01') AS consent_ts
    FROM xw x
    JOIN {{ source('bronze', 'customers') }} c ON x.source_system = 'CRM' AND c.crm_customer_id = x.source_id
    JOIN {{ ref('stg_crm_identity') }} s ON s.source_id = c.crm_customer_id
),
crm_agg AS (
    SELECT golden_customer_id,
        list(DISTINCT crm_customer_id ORDER BY crm_customer_id) AS crm_customer_ids,   -- DISTINCT: rows repeat per phone below
        arg_max(source_key, profile_ts) AS profile_source,
        arg_max(source_key, consent_ts) AS consent_source,
        arg_max(first_name, profile_ts) AS first_name,
        arg_max(last_name, profile_ts) AS last_name,
        arg_max(phone_norm, profile_ts) FILTER (WHERE phone_norm IS NOT NULL) AS primary_phone_e164,
        arg_max(nullif(lower(trim(email)), ''), profile_ts) FILTER (WHERE email IS NOT NULL) AS email,
        arg_max(try_cast(date_of_birth AS DATE), profile_ts) FILTER (WHERE date_of_birth IS NOT NULL) AS date_of_birth,
        arg_max(postal_norm, profile_ts) FILTER (WHERE postal_norm IS NOT NULL) AS postal_code,
        arg_max(province_code, profile_ts) AS province_code,
        arg_max(time_zone, profile_ts) AS time_zone,
        arg_max(preferred_language, profile_ts) AS preferred_language,
        -- contact rules from the most recently updated record
        arg_max(permitted_call_start_local, profile_ts) AS permitted_call_start_local,
        arg_max(permitted_call_end_local, profile_ts) AS permitted_call_end_local,
        bool_and(sunday_contact_permitted = 'true') AS sunday_contact_permitted,      -- restrictive across records
        bool_and(voicemail_permitted = 'true') AS voicemail_permitted,
        bool_and(contact_at_work_permitted = 'true') AS contact_at_work_permitted,
        -- consent: most recent, and most restrictive (chosen below by confidence)
        {% for c in ['consent_call', 'consent_call_mobile', 'consent_autodialer', 'consent_sms', 'consent_email', 'consent_push'] %}
        arg_max({{ c }} = 'true', consent_ts) AS {{ c }}_recent,
        bool_and({{ c }} = 'true') AS {{ c }}_strict,
        {% endfor %}
        -- restrictions: true if any merged record is true
        {% for f in ['cease_communication_flag', 'internal_do_not_call_flag', 'deceased_flag', 'bankruptcy_flag',
                     'consumer_proposal_flag', 'legal_representative_flag', 'credit_counselling_agency_flag'] %}
        bool_or({{ f }} = 'true') AS {{ f }},
        {% endfor %}
        list(DISTINCT p) FILTER (WHERE p IS NOT NULL) AS crm_phones,   -- for best_reachable_phone
        bool_or(internal_do_not_call_flag = 'true') AS any_dnc,
        max(_loaded_at) AS _loaded_at,
        arg_max(_source_file, profile_ts) AS _source_file,
        arg_max(_batch_id, profile_ts) AS _batch_id
    FROM crm, LATERAL (SELECT unnest([phone_norm, secondary_phone_e164, work_phone_e164]) AS p)
    GROUP BY golden_customer_id
),
product_fallback AS (   -- name, phone, postal from products, used only when the cluster has no CRM record
    SELECT x.golden_customer_id,
        arg_max(i.source_key, i.snapshot_date) AS fallback_source,
        arg_max(i.first_name, i.snapshot_date) FILTER (WHERE i.first_name IS NOT NULL) AS first_name,
        arg_max(i.last_name, i.snapshot_date) FILTER (WHERE i.last_name IS NOT NULL) AS last_name,
        arg_max(i.phone_e164, i.snapshot_date) FILTER (WHERE i.phone_e164 IS NOT NULL) AS phone_e164,
        arg_max(i.postal_code, i.snapshot_date) FILTER (WHERE i.postal_code IS NOT NULL) AS postal_code,
        arg_max(i.dob, i.snapshot_date) FILTER (WHERE i.dob IS NOT NULL) AS dob,
        max(i._loaded_at) AS _loaded_at, arg_max(i._source_file, i.snapshot_date) AS _source_file,
        arg_max(i._batch_id, i.snapshot_date) AS _batch_id
    FROM xw x JOIN {{ ref('identity_input') }} i ON i.source_key = x.source_key
    WHERE x.source_system <> 'CRM'
    GROUP BY x.golden_customer_id
),
keys AS (
    SELECT golden_customer_id,
        list(source_key ORDER BY source_key) AS source_keys,
        list(DISTINCT source_system ORDER BY source_system) AS source_systems,
        min(match_probability) FILTER (WHERE rule = 'splink') AS match_probability
    FROM xw GROUP BY golden_customer_id
),
products AS (
    SELECT golden_customer_id,
        count(*) AS product_count,
        bool_or(product = 'card') AS has_card, bool_or(product = 'loan') AS has_loan,
        bool_or(product = 'deposit') AS has_deposit,
        sum(coalesce(credit_exposure, 0)) AS total_credit_exposure,
        max(dpd) AS max_dpd_current,
        bool_or(hardship_program_flag) AS product_hardship_flag
    FROM {{ ref('c360_accounts') }} WHERE golden_customer_id IS NOT NULL GROUP BY 1
),
cases AS (
    SELECT golden_customer_id,
        arg_max(case_id, case_open_date) FILTER (WHERE outcome IS NULL) AS open_case_id,
        arg_max(current_bucket, case_open_date) FILTER (WHERE outcome IS NULL) AS current_bucket,
        bool_or(hardship_flag = 'true') AS case_hardship_flag
    FROM {{ ref('cases') }} WHERE golden_customer_id IS NOT NULL GROUP BY 1
),
reachable AS (   -- best_reachable_phone: most recent right-party contact on a CRM phone the customer consented to
    SELECT ca.golden_customer_id, arg_max(p.phone, h.contact_ts) AS best_reachable_phone
    FROM crm_agg ca, LATERAL (SELECT unnest(ca.crm_phones) AS phone) p
    JOIN (SELECT crm_customer_id, phone_number_hash, try_cast(contact_ts_local AS TIMESTAMP) AS contact_ts
          FROM {{ source('bronze', 'contact_history') }}
          WHERE rpc_flag = 'true' AND phone_number_hash IS NOT NULL) h
      ON h.phone_number_hash = phone_hash(p.phone)
     AND list_contains(ca.crm_customer_ids, h.crm_customer_id)
    WHERE NOT ca.any_dnc AND (ca.consent_call_recent OR ca.consent_call_mobile_recent)
    GROUP BY 1
),
incidents AS (SELECT DISTINCT golden_customer_id FROM {{ ref('incident_effects') }} WHERE golden_customer_id IS NOT NULL)
SELECT
    k.golden_customer_id,
    c.crm_customer_ids,
    k.source_keys,
    k.match_probability,
    f.identity_confidence,
    f.identity_confidence <> 'high' AS verify_before_disclosure,
    coalesce(c.first_name, pf.first_name) AS first_name,
    coalesce(c.last_name, pf.last_name) AS last_name,
    coalesce(c.primary_phone_e164, pf.phone_e164) AS primary_phone_e164,
    r.best_reachable_phone,
    c.email,
    coalesce(c.date_of_birth, pf.dob) AS date_of_birth,
    coalesce(c.postal_code, pf.postal_code) AS postal_code,
    c.province_code, c.time_zone, c.preferred_language,
    coalesce(pr.product_count, 0) AS product_count,
    coalesce(pr.has_card, false) AS has_card, coalesce(pr.has_loan, false) AS has_loan,
    coalesce(pr.has_deposit, false) AS has_deposit,
    coalesce(pr.total_credit_exposure, 0) AS total_credit_exposure,
    pr.max_dpd_current, cs.open_case_id, cs.current_bucket,
    {% for c_ in ['consent_call', 'consent_call_mobile', 'consent_autodialer', 'consent_sms', 'consent_email', 'consent_push'] %}
    CASE WHEN f.identity_confidence = 'high' THEN c.{{ c_ }}_recent ELSE c.{{ c_ }}_strict END AS {{ c_ }},
    {% endfor %}
    c.permitted_call_start_local, c.permitted_call_end_local, c.sunday_contact_permitted, c.voicemail_permitted,
    c.contact_at_work_permitted,
    {% for fl in ['cease_communication_flag', 'internal_do_not_call_flag', 'deceased_flag', 'bankruptcy_flag',
                  'consumer_proposal_flag', 'legal_representative_flag', 'credit_counselling_agency_flag'] %}
    coalesce(c.{{ fl }}, false) AS {{ fl }},
    {% endfor %}
    coalesce(cs.case_hardship_flag, false) OR coalesce(pr.product_hardship_flag, false) AS hardship_flag,
    i.golden_customer_id IS NOT NULL AS incident_affected,
    k.source_systems,
    to_json(struct_pack(
        name_phone_email_address := coalesce(c.profile_source, pf.fallback_source),
        rule_contact := CASE WHEN c.profile_source IS NOT NULL THEN 'most recently updated CRM record'
                             ELSE 'latest product record (no CRM record)' END,
        dob := CASE WHEN c.date_of_birth IS NOT NULL THEN c.profile_source ELSE pf.fallback_source END,
        consent := CASE WHEN f.identity_confidence = 'high' THEN 'most recent: ' || c.consent_source
                        ELSE 'most restrictive across merged CRM records' END,
        restrictions := 'true if any merged CRM record is true',
        care_flags := 'hardship: true if any account or case is true; protective use only'
    )) AS survivorship_source,
    CASE WHEN c.crm_customer_ids IS NOT NULL THEN 'CRM' ELSE 'PRODUCT' END AS _source_system,
    coalesce(c._source_file, pf._source_file) AS _source_file,
    coalesce(c._batch_id, pf._batch_id) AS _batch_id,
    coalesce(c._loaded_at, pf._loaded_at) AS _loaded_at,
    '{{ invocation_id }}' AS _pipeline_run_id
FROM keys k
JOIN conf f USING (golden_customer_id)
LEFT JOIN crm_agg c USING (golden_customer_id)
LEFT JOIN product_fallback pf USING (golden_customer_id)
LEFT JOIN products pr USING (golden_customer_id)
LEFT JOIN cases cs USING (golden_customer_id)
LEFT JOIN reachable r USING (golden_customer_id)
LEFT JOIN incidents i USING (golden_customer_id)
