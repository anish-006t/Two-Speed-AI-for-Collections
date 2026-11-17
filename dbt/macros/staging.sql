{#
  Shared shape of the row-level staging models (one row per source row, parsed but not yet rolled up):
  account_id, source_id, snapshot_date, *_raw values, flattened name / dob / phone / postal fields, lineage.
#}

{# Flatten a name struct from parse_*_name into columns. #}
{% macro name_cols(expr) -%}
    ({{ expr }}).first_name     AS first_name,
    ({{ expr }}).first_initial  AS first_initial,
    ({{ expr }}).middle_initial AS middle_initial,
    ({{ expr }}).last_name      AS last_name,
    ({{ expr }}).name_suffix    AS name_suffix
{%- endmacro %}

{% macro phone_cols(expr) -%}
    ({{ expr }}).phone_e164   AS phone_e164,
    ({{ expr }}).phone_first6 AS phone_first6,
    ({{ expr }}).phone_last2  AS phone_last2,
    ({{ expr }}).phone_masked AS phone_masked
{%- endmacro %}

{# Postal code is kept only when its shape is valid AND its FSA exists in geo_reference (architecture §6). #}
{% macro postal_cols(shape_expr, geo_alias='g') -%}
    {{ shape_expr }} AS postal_shape,
    CASE WHEN {{ geo_alias }}.fsa IS NOT NULL THEN {{ shape_expr }} END AS postal_code,
    CASE WHEN {{ geo_alias }}.fsa IS NOT NULL THEN {{ geo_alias }}.fsa END AS fsa
{%- endmacro %}

{% macro geo_join(shape_expr, geo_alias='g') -%}
    LEFT JOIN (SELECT DISTINCT fsa FROM {{ source('bronze', 'geo_reference') }}) {{ geo_alias }}
      ON {{ geo_alias }}.fsa = left({{ shape_expr }}, 3)
{%- endmacro %}

{#
  Field-level parse failures of one staging model, for silver.quarantine. A failing value is nulled in silver and
  logged here with the rule that failed; the rest of the record continues (architecture §4, decisions D-18).
#}
{% macro quarantine_from(stg, source_system, source_table, has_phone=true) -%}
    SELECT '{{ source_system }}' AS source_system, '{{ source_table }}' AS source_table, source_id, account_id,
           'name' AS column_name, name_raw AS raw_value, 'name_layout' AS rule,
           'name does not fit this source''s layout' AS reason,
           _source_file, _batch_id, _loaded_at, _pipeline_run_id
    FROM {{ ref(stg) }} WHERE name_raw IS NOT NULL AND last_name IS NULL
    UNION ALL
    SELECT '{{ source_system }}', '{{ source_table }}', source_id, account_id,
           'dob', dob_raw, 'dob_format', 'date of birth does not fit this source''s format or is out of range',
           _source_file, _batch_id, _loaded_at, _pipeline_run_id
    FROM {{ ref(stg) }} WHERE dob_raw IS NOT NULL AND dob IS NULL
    {% if has_phone %}
    UNION ALL
    SELECT '{{ source_system }}', '{{ source_table }}', source_id, account_id,
           'phone', phone_raw, 'phone_format', 'phone is not a 10-digit North American number or a known mask',
           _source_file, _batch_id, _loaded_at, _pipeline_run_id
    FROM {{ ref(stg) }} WHERE phone_raw IS NOT NULL AND phone_e164 IS NULL AND phone_first6 IS NULL
    {% endif %}
    UNION ALL
    SELECT '{{ source_system }}', '{{ source_table }}', source_id, account_id,
           'postal_code', postal_raw, 'postal_format', 'postal code is not A1A 1A1',
           _source_file, _batch_id, _loaded_at, _pipeline_run_id
    FROM {{ ref(stg) }} WHERE postal_raw IS NOT NULL AND postal_shape IS NULL
    UNION ALL
    SELECT '{{ source_system }}', '{{ source_table }}', source_id, account_id,
           'postal_code', postal_raw, 'fsa_unknown', 'FSA is not in geo_reference',
           _source_file, _batch_id, _loaded_at, _pipeline_run_id
    FROM {{ ref(stg) }} WHERE postal_shape IS NOT NULL AND postal_code IS NULL
{%- endmacro %}

{#
  Roll account-level staging rows up to one row per source customer key (decisions D-11, D-16).
  Each name, phone and postal field comes from the most recent snapshot that has it; a full phone beats a masked one. DOB is kept only when every parsed value
  agrees; otherwise dob is NULL, dob_conflict is true and the key cannot auto-link.
#}
{% macro customer_rollup(stg, source_system) -%}
    SELECT
        '{{ source_system }}'                                         AS source_system,
        source_id,
        arg_max(first_name, snapshot_date)     FILTER (WHERE first_name IS NOT NULL)     AS first_name,
        arg_max(first_initial, snapshot_date)  FILTER (WHERE first_initial IS NOT NULL)  AS first_initial,
        arg_max(middle_initial, snapshot_date) FILTER (WHERE middle_initial IS NOT NULL) AS middle_initial,
        arg_max(last_name, snapshot_date)      FILTER (WHERE last_name IS NOT NULL)      AS last_name,
        arg_max(name_suffix, snapshot_date)    FILTER (WHERE name_suffix IS NOT NULL)    AS name_suffix,
        count(DISTINCT name_raw)                                      AS name_variant_count,
        CASE WHEN count(DISTINCT dob) = 1 THEN min(dob) END           AS dob,
        count(DISTINCT dob) > 1                                       AS dob_conflict,
        list(DISTINCT dob) FILTER (WHERE dob IS NOT NULL)             AS dob_variants,
        -- A full phone beats a masked one; first6/last2 then come from that full phone.
        arg_max(phone_e164, snapshot_date)   FILTER (WHERE phone_e164 IS NOT NULL)   AS phone_e164,
        coalesce(substr(arg_max(phone_e164, snapshot_date) FILTER (WHERE phone_e164 IS NOT NULL), 3, 6),
                 arg_max(phone_first6, snapshot_date) FILTER (WHERE phone_first6 IS NOT NULL)) AS phone_first6,
        coalesce(right(arg_max(phone_e164, snapshot_date) FILTER (WHERE phone_e164 IS NOT NULL), 2),
                 arg_max(phone_last2, snapshot_date) FILTER (WHERE phone_last2 IS NOT NULL))  AS phone_last2,
        CASE WHEN count(phone_e164) > 0 THEN false
             WHEN count(phone_first6) > 0 THEN true END               AS phone_masked,
        arg_max(postal_code, snapshot_date)  FILTER (WHERE postal_code IS NOT NULL)  AS postal_code,
        arg_max(fsa, snapshot_date)          FILTER (WHERE postal_code IS NOT NULL)  AS fsa,
        list(DISTINCT account_id ORDER BY account_id)                 AS account_ids,
        max(snapshot_date)                                            AS snapshot_date,
        '{{ source_system }}'                                         AS _source_system,
        arg_max(_source_file, snapshot_date)                          AS _source_file,
        arg_max(_batch_id, snapshot_date)                             AS _batch_id,
        max(_loaded_at)                                               AS _loaded_at,
        '{{ invocation_id }}'                                         AS _pipeline_run_id
    FROM {{ ref(stg) }}
    WHERE source_id IS NOT NULL
    GROUP BY source_id
{%- endmacro %}
