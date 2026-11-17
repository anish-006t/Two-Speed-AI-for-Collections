-- One row per promise to pay. Resolved = kept, partially kept or broken (certified rates exclude open and cancelled).
-- agent_site: the site of the agent who took the promise; self_serve when no agent.
-- break_prob and break_reason_1..3: the bank's promise-break model (bronze.model_scores, model ptp_break, which the
-- bank marks uses_protected_attributes = false); latest score per promise; the reasons are the model's own text.
CREATE OR REPLACE TABLE layer2.f_promises AS
SELECT p.ptp_id, p.case_id,
       __GOLDEN__ AS golden_customer_id,
       p.account_id,
       nullif(trim(p.product), '') AS product,
       nullif(trim(p.ptp_channel), '') AS ptp_channel,
       p.agent_id,
       coalesce(a.site, 'self_serve') AS agent_site,
       try_cast(p.ptp_created_ts AS TIMESTAMP) AS ptp_created_ts,
       try_cast(p.ptp_due_date AS DATE) AS ptp_due_date,
       try_cast(p.paid_date AS DATE) AS paid_date,
       try_cast(p.ptp_amount AS DOUBLE) AS ptp_amount,
       try_cast(p.amount_paid_against AS DOUBLE) AS amount_paid_against,
       nullif(trim(p.ptp_status), '') AS ptp_status,
       coalesce(trim(p.ptp_status) IN ('kept', 'partially_kept', 'broken'), false) AS is_resolved,
       try_cast(p.prior_broken_ptp_count AS INTEGER) AS prior_broken_ptp_count,
       least(try_cast(p.prior_broken_ptp_count AS INTEGER), 3) AS prior_broken,
       nullif(trim(p.broken_reason_agent_coded), '') AS broken_reason,
       __ADJUSTED__ AS ptp_status_adjusted,
       c.queue, c.primary_product, c.test_cell, c.assignment_method, c.randomised, c.current_bucket, c.province_code,
       s.break_prob, s.break_reason_1, s.break_reason_2, s.break_reason_3, s.break_score_date
FROM __PROMISES__ p
LEFT JOIN bronze.agents a ON a.agent_id = p.agent_id
LEFT JOIN layer2.f_cases c USING (case_id)
LEFT JOIN (
    SELECT ptp_id, round(try_cast(score AS DOUBLE), 3) AS break_prob,
           nullif(trim(top_reason_1_text), '') AS break_reason_1, nullif(trim(top_reason_2_text), '') AS break_reason_2,
           nullif(trim(top_reason_3_text), '') AS break_reason_3, try_cast(score_date AS DATE) AS break_score_date
    FROM bronze.model_scores
    WHERE model_name = 'ptp_break' AND coalesce(uses_protected_attributes, 'false') = 'false' AND ptp_id IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY ptp_id ORDER BY score_date DESC, score_ts DESC) = 1
) s USING (ptp_id);
