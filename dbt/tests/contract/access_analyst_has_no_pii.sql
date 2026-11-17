-- depends_on: {{ ref('analyst_customers') }} {{ ref('analyst_contact_timeline') }} {{ ref('analyst_cases') }}
-- depends_on: {{ ref('analyst_accounts') }} {{ ref('analyst_account_monthly_snapshot') }} {{ ref('analyst_promises_to_pay') }}
-- depends_on: {{ ref('analyst_incident_effects') }} {{ ref('analyst_customer_features') }}
-- Architecture §13 / D-39: the analyst schema holds no direct identifiers (dictionary "Direct PII" columns and their
-- gold equivalents), no postal code or FSA, and no protected attributes.
SELECT table_name, column_name FROM information_schema.columns
WHERE table_schema = 'access_analyst'
  AND (column_name IN ('first_name', 'middle_name', 'last_name', 'preferred_name', 'full_name_raw', 'name_suffix',
                       'date_of_birth', 'dob', 'national_id_hash', 'email', 'address_line1', 'address_line2',
                       'postal_code', 'fsa', 'note_text', 'sms_reply_text', 'sms_text', 'email_subject',
                       'phone_number_hash', 'card_number_masked', 'joint_holder_ref', 'co_borrower_ref',
                       'vehicle_vin_hash', 'crm_customer_ids', 'source_keys', 'gender_code', 'marital_status',
                       'citizenship_status', 'age', 'age_band')
       OR column_name ILIKE '%phone_e164%' OR column_name ILIKE '%_name_raw' OR column_name ILIKE '%vulnerab%'
       OR column_name = 'best_reachable_phone')
