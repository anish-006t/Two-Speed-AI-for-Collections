-- Agent view: one row per collections case (architecture §13; decisions D-39). The app must filter on
-- assigned_agent_id so an agent sees only their own cases. Phone and email are masked and there is no DOB. When
-- verify_before_disclosure is true the agent must confirm identity before discussing any debt detail in this row.
{{ config(schema='access_agent', materialized='view', alias='case_brief') }}
SELECT
    k.case_id, k.assigned_agent_id, k.queue, k.case_status, k.case_open_date, k.primary_product,
    c.golden_customer_id, c.identity_confidence, c.verify_before_disclosure,
    c.first_name, c.last_name,                                   -- needed to greet and identify the right party
    {{ mask_phone('c.primary_phone_e164') }} AS phone_masked,
    {{ mask_phone('c.best_reachable_phone') }} AS best_reachable_phone_masked,
    {{ mask_email('c.email') }} AS email_masked,
    c.preferred_language, c.province_code, c.time_zone,
    c.permitted_call_start_local, c.permitted_call_end_local, c.sunday_contact_permitted, c.voicemail_permitted,
    c.contact_at_work_permitted,
    c.consent_call, c.consent_call_mobile, c.consent_autodialer, c.consent_sms, c.consent_email, c.consent_push,
    c.cease_communication_flag, c.internal_do_not_call_flag, c.deceased_flag, c.bankruptcy_flag,
    c.consumer_proposal_flag, c.legal_representative_flag, c.credit_counselling_agency_flag,
    c.hardship_flag, c.incident_affected,
    -- debt details: show only after identity is verified when verify_before_disclosure is true
    k.current_bucket_recomputed AS current_bucket, try_cast(k.current_dpd AS INTEGER) AS current_dpd,
    try_cast(k.total_overdue AS DOUBLE) AS total_overdue, try_cast(k.total_exposure AS DOUBLE) AS total_exposure,
    k.current_ptp_id, try_cast(k.current_ptp_amount AS DOUBLE) AS current_ptp_amount, k.current_ptp_due_date,
    k.case_link_alarm,
    'ACCESS_AGENT' AS _source_system, k._source_file, k._batch_id, k._loaded_at, k._pipeline_run_id
FROM {{ ref('cases') }} k
LEFT JOIN {{ ref('c360_customer') }} c USING (golden_customer_id)
