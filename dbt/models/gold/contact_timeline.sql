-- Contact history keyed by golden customer ID.
-- channel: REL-0926 renamed it to channel_v2 from 26 Sep (null `channel` for most rows). channel_resolved applies
-- coalesce(channel_v2, channel) only once an engineer approves the fix; until then it is the column as received.
-- phone_number_hash (reversible MD5, D-06), free-text SMS replies (not yet redacted) and vulnerability_disclosed_flag
-- (protected, D-37) are left out of gold.
WITH xw AS (SELECT source_key, golden_customer_id FROM {{ source('identity', 'crosswalk') }} WHERE valid_to IS NULL)
SELECT x.golden_customer_id,
       {% if is_approved('REL-0926-channel') == 'True' %}coalesce(c.channel_v2, c.channel){% else %}c.channel{% endif %} AS channel_resolved,
       {{ is_approved('REL-0926-channel') }} AS channel_fix_applied,
       c.* EXCLUDE (phone_number_hash, sms_reply_text, vulnerability_disclosed_flag),   -- last one protected (D-37)
       'COLLECTIONS' AS _source_system, '{{ invocation_id }}' AS _pipeline_run_id
FROM {{ source('bronze', 'contact_history') }} c
LEFT JOIN xw x ON x.source_key = 'CRM:' || c.crm_customer_id
