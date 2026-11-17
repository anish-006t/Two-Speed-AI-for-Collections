{% snapshot c360_customer_history %}
{{ config(target_schema='gold', unique_key='golden_customer_id', strategy='check', check_cols=['row_hash'],
          invalidate_hard_deletes=True,
          pre_hook="ALTER TABLE IF EXISTS {{ this }} DROP COLUMN IF EXISTS vulnerability_flag") }}
-- The pre-hook removes the protected vulnerability_flag (D-37) from history built before it left gold; dbt snapshots
-- never drop columns on their own.
-- History of the golden record (architecture §1, §5): valid_from / valid_to are dbt_valid_from / dbt_valid_to.
-- A new version is written only when a business column changes (row_hash leaves out the run's lineage values).
SELECT b.*, md5(to_json(b)) AS row_hash, c._loaded_at, c._pipeline_run_id
FROM (SELECT * EXCLUDE (_pipeline_run_id, _loaded_at) FROM {{ ref('c360_customer') }}) b
JOIN {{ ref('c360_customer') }} c USING (golden_customer_id)
{% endsnapshot %}
