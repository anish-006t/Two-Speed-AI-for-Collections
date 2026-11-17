{# Masking helpers for the role-based access views (architecture §13, decisions D-39). #}
{% macro mask_phone(col) -%}
    CASE WHEN {{ col }} IS NOT NULL THEN '***-***-' || right({{ col }}, 4) END
{%- endmacro %}
{% macro mask_email(col) -%}
    CASE WHEN {{ col }} LIKE '%@%' THEN left({{ col }}, 1) || '***@' || split_part({{ col }}, '@', 2) END
{%- endmacro %}
{% macro mask_text(col) -%}
    CASE WHEN {{ col }} IS NOT NULL THEN left({{ col }}, 1) || repeat('*', least(length({{ col }}) - 1, 8)) END
{%- endmacro %}
