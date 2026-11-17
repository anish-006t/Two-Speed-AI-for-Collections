-- depends_on: {{ ref('c360_customer') }} {{ ref('c360_accounts') }} {{ ref('cases') }} {{ ref('contact_timeline') }}
-- depends_on: {{ ref('promises_to_pay') }} {{ ref('incident_effects') }} {{ ref('l3_customer_features') }} {{ ref('c360_customer_history') }} {{ ref('account_monthly_snapshot') }}
-- CLAUDE.md rule 7: every gold table carries the five lineage columns.
WITH t AS (SELECT DISTINCT table_name FROM information_schema.columns WHERE table_schema = 'gold'),
need AS (SELECT unnest(['_source_system', '_source_file', '_batch_id', '_loaded_at', '_pipeline_run_id']) AS c)
SELECT t.table_name, need.c FROM t, need
WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns i
                  WHERE i.table_schema = 'gold' AND i.table_name = t.table_name AND i.column_name = need.c)
