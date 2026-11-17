# Promise-break model report

Resolved promises only; open and cancelled promises are excluded. The split is time ordered. Features are computed as of promise creation through the same Layer 3 definitions used for live scoring.

- Rows: 198,119 (158,495 train, 39,624 test)
- Test AUC: 0.775
- Test Brier score: 0.193
- Overall test broken rate: 0.597
- Broken rate in highest-risk decile: 0.963

## Calibration (test set, ten risk bands)

Mean predicted risk 0.520 against an observed broken rate 0.597. The ordering is reliable (the top band breaks most often), but where the newest promises break more often than the older ones the model learned from, absolute risk is understated; read the score as a ranking and the band's observed rate as the likely outcome.

| Band | Rows | Predicted risk | Observed broken rate |
|---:|---:|---:|---:|
| 1 | 3,963 | 0.158 | 0.083 |
| 2 | 3,963 | 0.298 | 0.377 |
| 3 | 3,963 | 0.361 | 0.482 |
| 4 | 3,963 | 0.415 | 0.546 |
| 5 | 3,962 | 0.469 | 0.603 |
| 6 | 3,962 | 0.526 | 0.632 |
| 7 | 3,962 | 0.599 | 0.690 |
| 8 | 3,962 | 0.696 | 0.738 |
| 9 | 3,962 | 0.795 | 0.861 |
| 10 | 3,962 | 0.887 | 0.963 |

Model inputs (24): product_count, total_credit_exposure, max_dpd_current, current_bucket, utilisation, incident_affected, prior_resolved_ptp_count, prior_broken_ptp_count, broken_promises_90d, prior_kept_ptp_rate, prior_avg_promised_amount, usual_payday, salary_delay_days, bureau_score_change, response_rate_call_90d, response_rate_sms_90d, response_rate_email_90d, response_rate_push_90d, text_stated_delay_reason, text_promise_mentioned, text_promise_amount, text_promise_intent_strength, promise_amount, days_ahead.

Left out on purpose (8): hardship_flag, text_hardship, text_hardship_reason, text_promise_date, text_dispute, text_complaint, text_cease_request, text_sentiment. They are protective-only (hardship and related text answers) and must never raise a customer's risk score.
