-- One row per contact attempt. channel: REL-0926 moved `channel` to `channel_v2` from 26 Sep, so take either.
-- No phone hash, SMS text or free text here.
CREATE OR REPLACE TABLE layer2.f_contacts AS
WITH h AS (
    SELECT *, coalesce(nullif(trim(channel_v2), ''), nullif(trim(channel), '')) AS channel_any FROM __CONTACTS__
)
SELECT h.contact_id, h.case_id,
       __GOLDEN__ AS golden_customer_id,
       h.channel_any AS channel,
       nullif(trim(h.direction), '') AS direction,
       nullif(trim(h.sub_channel), '') AS sub_channel,
       try_cast(h.contact_ts_local AS TIMESTAMP) AS contact_ts_local,
       CAST(try_cast(h.contact_ts_local AS TIMESTAMP) AS DATE) AS contact_date,
       try_cast(h.contact_hour_local AS INTEGER) AS contact_hour_local,
       nullif(trim(h.day_of_week), '') AS day_of_week,
       layer2.l2_bool(h.within_permitted_hours_flag) AS within_permitted_hours,
       layer2.l2_bool(h.stat_holiday_flag) AS stat_holiday,
       layer2.l2_bool(h.rpc_flag) AS rpc_flag,
       layer2.l2_bool(h.ptp_made_flag) AS ptp_made_flag,
       layer2.l2_bool(h.payment_taken_flag) AS payment_taken_flag,
       try_cast(h.payment_amount AS DOUBLE) AS payment_amount,
       layer2.l2_bool(h.dispute_raised_flag) AS dispute_raised_flag,
       layer2.l2_bool(h.complaint_raised_flag) AS complaint_raised_flag,
       layer2.l2_bool(h.hardship_mentioned_flag) AS hardship_mentioned_flag,
       layer2.l2_bool(h.cease_request_flag) AS cease_request_flag,
       (nullif(trim(h.payment_link_id), '') IS NOT NULL) AS payment_link,   -- a payment link was sent (SMS, email)
       layer2.l2_bool(h.link_clicked_flag) AS link_clicked,
       layer2.l2_bool(h.link_payment_flag) AS link_paid,
       try_cast(h.contact_cost_cad AS DOUBLE) AS contact_cost_cad,
       nullif(trim(h.outcome_code), '') AS outcome_code,
       nullif(trim(h.system_event_type), '') AS system_event_type,
       nullif(trim(h.ops_incident_id), '') AS ops_incident_id,
       h.agent_id,
       nullif(trim(h.agent_site), '') AS agent_site,
       nullif(trim(h.test_cell), '') AS test_cell,
       nullif(trim(h.strategy_code), '') AS strategy_code,
       nullif(trim(h.treatment_code), '') AS treatment_code,
       nullif(trim(h.transcript_id), '') AS transcript_id,
       (h.channel_any = 'system' OR trim(h.direction) = 'internal') AS is_system,
       (trim(h.direction) = 'outbound' AND h.channel_any = 'call') AS is_outbound_call,
       c.province_code, c.queue, c.current_bucket, c.bucket_from_dpd, c.primary_product,
       c.is_open AS case_is_open, c.randomised
FROM h
LEFT JOIN layer2.f_cases c USING (case_id);
