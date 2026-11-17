# Call transcripts: regex rules vs rules + AI fallback

## 1. Public 500 labels (rules were tuned on these; favours the rules)

Rules: all fields right 96.2% of 500 calls. The hybrid would ask the AI on 18 calls (rules found nothing); not run, to keep the daily free AI limit.

| Field | Rules |
|---|---|
| reason | 98.8% |
| hardship | 98.8% |
| dispute | 100.0% |
| complaint | 100.0% |
| insolvency | 100.0% |
| cease_request | 100.0% |
| promise_amount | 97.6% |
| promise_date | 97.6% |
| next_step | 97.8% |

## 2. Fresh calls the rules never saw (60 calls, labelled independently)

| Field | Rules | Rules + AI fallback |
|---|---|---|
| **all fields right** | **60.0%** | **61.7%** |
| reason | 100.0% | 100.0% |
| hardship | 100.0% | 100.0% |
| dispute | 100.0% | 100.0% |
| complaint | 95.0% | 96.7% |
| insolvency | 100.0% | 100.0% |
| cease_request | 95.0% | 96.7% |
| promise_amount | 100.0% | 100.0% |
| promise_date | 100.0% | 100.0% |
| next_step | 60.0% | 61.7% |

Hybrid minus rules, all fields right: +1.7 points, 95% interval [+0.0, +5.0] (paired bootstrap).
AI calls: 0 (skipped by the cap: 0); engines used: {'rules+regex': 48, 'llm (checked against the call)': 12}. Time per call: rules 0 ms, hybrid 0 ms on average.

Without the next-step convention difference (see section 3):

| Measure | Rules | Rules + AI fallback |
|---|---|---|
| 8 fields (all but next step) right, 60 calls | 95.0% | 96.7% |
| next step, 46 calls without link+hardship ambiguity | 76.1% | 78.3% |
| all 9 fields right, same calls | 76.1% | 78.3% |

Rules' mistakes on the fresh set (field: label -> rules), most frequent first:

- next_step: send_payment_link -> hardship_plan_review (x10)
- next_step: send_payment_link -> stop_contact (x4)
- next_step: call_back -> send_payment_link (x3)
- complaint: True -> False (x3)
- cease_request: True -> False (x3)
- next_step: hardship_plan_review -> escalate_supervisor (x2)
- next_step: send_payment_link -> escalate_supervisor (x2)
- next_step: call_back -> hardship_plan_review (x1)
- next_step: send_payment_link -> dispute_investigation (x1)
- next_step: stop_contact -> escalate_supervisor (x1)

## 3. How reliable are the fresh labels? (10 hidden calls with official labels)

The independent labeller matched the official labels on all fields for 8 / 10 calls. Per field: reason 100%, hardship 100%, dispute 100%, complaint 100%, insolvency 100%, cease_request 100%, promise_amount 100%, promise_date 100%, next_step 80%.
Where the labeller disagrees with official labels, the fresh-set numbers carry that much noise.
Next-step convention: in calls where the agent offers both a payment link and hardship help, the official labels mostly choose hardship_plan_review; the fresh labeller always chose send_payment_link. Those calls are excluded from the adjusted next-step numbers above.
