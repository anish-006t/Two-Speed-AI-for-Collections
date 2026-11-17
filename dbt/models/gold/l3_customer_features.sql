-- Feature-ready view for Layer 3 (architecture §1, §13). Removed: direct identifiers (names, phones, email, DOB),
-- protected attributes including vulnerability, age and AML fields (never in gold), and preferred_language, a
-- possible proxy for origin (decisions D-37). hardship_flag stays, for protective handling only.
{{ config(materialized='view') }}
SELECT golden_customer_id, identity_confidence, verify_before_disclosure,
       province_code, time_zone,
       product_count, has_card, has_loan, has_deposit, total_credit_exposure, max_dpd_current, open_case_id,
       current_bucket,
       consent_call, consent_call_mobile, consent_autodialer, consent_sms, consent_email, consent_push,
       cease_communication_flag, internal_do_not_call_flag, deceased_flag, bankruptcy_flag, consumer_proposal_flag,
       legal_representative_flag, credit_counselling_agency_flag,
       hardship_flag, incident_affected,
       _source_system, _source_file, _batch_id, _loaded_at, _pipeline_run_id
FROM {{ ref('c360_customer') }}
