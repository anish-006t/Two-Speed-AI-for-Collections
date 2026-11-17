-- One row per case assignment. randomised_pilot assignments are the unbiased test of agent effects.
-- The pilot runs in mid_stage, which cases reach after 30 days, so a 30-day cure there counts from the assignment.
CREATE OR REPLACE TABLE layer2.f_assignments AS
SELECT h.assignment_id, h.case_id, h.agent_id,
       nullif(trim(h.assignment_method), '') AS assignment_method,
       coalesce(trim(h.assignment_method) = 'randomised_pilot', false) AS randomised_pilot,
       layer2.l2_bool(h.certified_for_vulnerable_flag) AS agent_certified_vulnerable,
       layer2.l2_bool(h.hardship_flag_at_assignment) AS hardship_at_assignment,
       try_cast(h.assigned_ts AS TIMESTAMP) AS assigned_ts,
       CAST(try_cast(h.assigned_ts AS TIMESTAMP) AS DATE) AS assigned_date,
       nullif(trim(h.queue), '') AS queue,
       c.cure_flag, c.cure_date, c.days_to_cure, c.case_open_date, c.hardship_flag, c.primary_product, c.is_open
FROM bronze.case_assignment_history h
LEFT JOIN layer2.f_cases c USING (case_id);
