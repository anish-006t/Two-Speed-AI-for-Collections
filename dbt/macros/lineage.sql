{# Lineage columns carried on every silver and gold row (CLAUDE.md rule 7). #}
{% macro lineage_cols(source_system, alias=none) -%}
    '{{ source_system }}' AS _source_system,
    {{ alias ~ '.' if alias }}_source_file AS _source_file,
    {{ alias ~ '.' if alias }}_batch_id AS _batch_id,
    {{ alias ~ '.' if alias }}_loaded_at AS _loaded_at,
    '{{ invocation_id }}' AS _pipeline_run_id
{%- endmacro %}
