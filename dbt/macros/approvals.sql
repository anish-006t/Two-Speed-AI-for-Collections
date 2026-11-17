{# True when a fix or adjustment rule has a recorded approval in var('approved_fixes'). #}
{% macro is_approved(fix_id) -%}
    {%- set ns = namespace(ok=false) -%}
    {%- for f in var('approved_fixes', []) -%}
        {%- if f.id == fix_id and f.approved_by -%}{%- set ns.ok = true -%}{%- endif -%}
    {%- endfor -%}
    {{- ns.ok -}}
{%- endmacro %}
