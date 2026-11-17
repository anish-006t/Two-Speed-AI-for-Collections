-- Values that failed their source's parser. They are nulled in silver and logged here with the rule; the record
-- itself continues (decisions D-18). Records whose key is invalid are dropped from identity_input and logged here.
{{ quarantine_from('stg_crm_identity', 'CRM', 'customers') }}
UNION ALL
{{ quarantine_from('stg_card_identity', 'CARDS', 'card_accounts') }}
UNION ALL
{{ quarantine_from('stg_loan_identity', 'LENDING', 'loan_accounts') }}
UNION ALL
{{ quarantine_from('stg_deposit_identity', 'CORE_BANKING', 'deposit_accounts') }}
UNION ALL
{{ quarantine_from('stg_bureau_identity', 'BUREAU', 'external', has_phone=false) }}
UNION ALL
SELECT 'CORE_BANKING', 'deposit_accounts', source_id_raw, account_id, 'src_customer_ref', source_id_raw,
       'cif_format', 'CIF is not 1 to 10 digits', _source_file, _batch_id, _loaded_at, _pipeline_run_id
FROM {{ ref('stg_deposit_identity') }} WHERE source_id IS NULL
