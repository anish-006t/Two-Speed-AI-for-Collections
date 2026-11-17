-- Promises to pay keyed by golden customer ID. ptp_status stays as received; ptp_status_adjusted applies the
-- INC-0914 rule only after the business owner approves it (var approved_fixes, id INC-0914-ptp).
WITH xw AS (SELECT source_key, golden_customer_id FROM {{ source('identity', 'crosswalk') }} WHERE valid_to IS NULL),
affected AS (SELECT DISTINCT ptp_id FROM {{ ref('incident_effects') }} WHERE signal = 'ptp_broken_paid_next_day')
SELECT x.golden_customer_id, p.*,
       a.ptp_id IS NOT NULL AS incident_candidate,
       {% if is_approved('INC-0914-ptp') == 'True' %}
       CASE WHEN a.ptp_id IS NOT NULL THEN 'kept' ELSE p.ptp_status END AS ptp_status_adjusted,
       CASE WHEN a.ptp_id IS NOT NULL THEN 'payment posted a day late during the app outage' END AS adjustment_reason,
       CASE WHEN a.ptp_id IS NOT NULL THEN 'INC-0914' END AS incident_id,
       {% else %}
       p.ptp_status AS ptp_status_adjusted, NULL::VARCHAR AS adjustment_reason, NULL::VARCHAR AS incident_id,
       {% endif %}
       'COLLECTIONS' AS _source_system, '{{ invocation_id }}' AS _pipeline_run_id
FROM {{ source('bronze', 'promises_to_pay') }} p
LEFT JOIN xw x ON x.source_key = 'CRM:' || p.crm_customer_id
LEFT JOIN affected a ON a.ptp_id = p.ptp_id
