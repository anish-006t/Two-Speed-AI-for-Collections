-- depends_on: {{ ref('c360_customer') }} {{ ref('c360_accounts') }} {{ ref('cases') }} {{ ref('contact_timeline') }}
-- depends_on: {{ ref('promises_to_pay') }} {{ ref('incident_effects') }} {{ ref('l3_customer_features') }} {{ ref('c360_customer_history') }} {{ ref('account_monthly_snapshot') }}
-- CLAUDE.md rule 5 / team rule 2: protected attributes (including any vulnerability field, D-37), age, age_band and
-- AML fields never appear in gold; preferred_language (possible proxy) never appears in the Layer 3 feature view.
SELECT table_name, column_name FROM information_schema.columns
WHERE table_schema = 'gold'
  AND (column_name IN ('gender_code', 'marital_status', 'citizenship_status', 'household_size', 'dependants_count',
                       'newcomer_program_flag', 'newcomer_arrival_date', 'accessibility_needs_flag', 'accessibility_type',
                       'ccb_deposit_flag', 'customer_accent_region', 'age', 'age_band', 'aml_risk_rating', 'pep_flag')
       OR column_name ILIKE '%vulnerab%'
       OR (table_name = 'l3_customer_features' AND column_name ILIKE '%language%'))
