-- What known incidents did to customers (architecture §10 "Incident handling"). Source facts are not changed.
-- INC-0914 (app outage, 14 Sep 09:10-15:40 ET): app payments posted a day late and late fees were charged in error.
-- Signals: contacts the operations team tagged INC-0914; promises due on the outage day marked broken whose account
-- received a payment posted the next day; card statements due that day whose first payment came the next day and
-- that carry a fee. decisions.md D-24 records how often each signal fires.
WITH inc AS (
    SELECT event_id, try_cast(start_ts AS TIMESTAMP) AS start_ts, try_cast(end_ts AS TIMESTAMP) AS end_ts
    FROM {{ source('bronze', 'ops_events') }} WHERE event_id = 'INC-0914'
),
tagged AS (
    SELECT DISTINCT 'CRM:' || crm_customer_id AS source_key, 'contact_tagged' AS signal, NULL AS account_id, NULL AS ptp_id
    FROM {{ source('bronze', 'contact_history') }} WHERE ops_incident_id = 'INC-0914'
),
late_ptp AS (
    SELECT DISTINCT 'CRM:' || p.crm_customer_id, 'ptp_broken_paid_next_day', p.account_id, p.ptp_id
    FROM {{ source('bronze', 'promises_to_pay') }} p, inc
    WHERE p.ptp_status = 'broken' AND try_cast(p.ptp_due_date AS DATE) = inc.start_ts::DATE
      AND EXISTS (SELECT 1 FROM {{ source('bronze', 'transactions') }} t
                  WHERE t.year = year(inc.start_ts) AND t.account_id = p.account_id AND t.direction = 'credit'
                    AND t.posting_date = inc.start_ts::DATE + 1)
),
late_fee AS (
    SELECT DISTINCT a.source_key, 'late_fee_in_window', s.card_account_id, NULL
    FROM {{ source('bronze', 'card_statements') }} s
    JOIN {{ ref('c360_accounts') }} a ON a.account_id = s.card_account_id, inc
    WHERE s.year = year(inc.start_ts) AND s.due_date = inc.start_ts::DATE
      AND s.first_payment_after_due_date = inc.start_ts::DATE + 1 AND s.fees_amount > 0
)
SELECT x.golden_customer_id, e.*, 'INC-0914' AS incident_id,
       'OPS_EVENTS' AS _source_system, l._source_file, l._batch_id, l._loaded_at, '{{ invocation_id }}' AS _pipeline_run_id
FROM (SELECT * FROM tagged UNION ALL SELECT * FROM late_ptp UNION ALL SELECT * FROM late_fee) e
LEFT JOIN {{ source('identity', 'crosswalk') }} x ON x.source_key = e.source_key AND x.valid_to IS NULL
CROSS JOIN (SELECT any_value(_source_file) AS _source_file, any_value(_batch_id) AS _batch_id,
                   max(_loaded_at) AS _loaded_at FROM {{ source('bronze', 'ops_events') }}) l
