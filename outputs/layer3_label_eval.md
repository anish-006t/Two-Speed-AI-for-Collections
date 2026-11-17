# Layer 3 public-label evaluation

Public labels are used only for evaluation. Scores below are produced by the configured extractor: **rules (offline; no API key set)**.

Where a label uses a different scale from the answer, both are put on one scale first: sentiment is compared by tone (negative, neutral, positive) and promise intent as firm or not firm. 'none' counts as an answer for choice questions that offer it.

| Source | Question | N | Accuracy | Macro F1 | Brier |
|---|---|---:|---:|---:|---:|
| transcript | hardship | 500 | 0.720 | 0.626 | 0.199 |
| transcript | hardship_reason | 203 | 0.473 | 0.611 | 0.227 |
| transcript | stated_delay_reason | 421 | 0.444 | 0.411 | 0.239 |
| transcript | promise_mentioned | 500 | 0.820 | 0.798 | 0.129 |
| transcript | promise_amount | 373 | 0.147 | 0.095 | 0.664 |
| transcript | promise_date | 373 | 0.000 | 0.000 | 0.607 |
| transcript | promise_intent_strength | 373 | 0.177 | 0.127 | 0.502 |
| transcript | dispute | 500 | 0.884 | 0.577 | 0.117 |
| transcript | dispute_type | 66 | 0.636 | 0.394 | 0.189 |
| transcript | complaint | 500 | 0.960 | 0.846 | 0.085 |
| transcript | cease_request | 500 | 0.962 | 0.663 | 0.086 |
| transcript | insolvency_mention | 500 | 0.998 | 0.944 | 0.058 |
| transcript | third_party_present | 500 | 0.984 | 0.496 | 0.066 |
| transcript | legal_threat_by_agent | 500 | 0.990 | 0.497 | 0.063 |
| transcript | sentiment | 500 | 0.478 | 0.357 | 0.299 |
| transcript | next_step | 500 | 0.548 | 0.266 | 0.243 |
| transcript | protected_attribute_mentioned | 500 | 0.942 | 0.485 | 0.095 |
| note | hardship | 500 | 0.810 | 0.647 | 0.155 |
| note | hardship_reason | 105 | 0.362 | 0.406 | 0.277 |
| note | stated_delay_reason | 274 | 0.453 | 0.549 | 0.220 |
| note | promise_mentioned | 500 | 0.676 | 0.570 | 0.226 |
| note | promise_amount | 207 | 0.101 | 0.080 | 0.486 |
| note | promise_date | 207 | 0.000 | 0.000 | 0.513 |
| note | promise_intent_strength | 207 | 0.082 | 0.106 | 0.424 |
| note | dispute | 500 | 0.994 | 0.883 | 0.060 |
| note | dispute_type | 8 | 0.250 | 0.333 | 0.472 |
| note | complaint | 500 | 0.992 | 0.831 | 0.061 |
| note | cease_request | 500 | 1.000 | 1.000 | 0.057 |
| note | insolvency_mention | 500 | 1.000 | 1.000 | 0.057 |
| note | third_party_present | 500 | 0.982 | 0.495 | 0.067 |
| note | sentiment | 500 | 0.328 | 0.207 | 0.359 |
| note | next_step | 500 | 0.400 | 0.515 | 0.246 |
| note | protected_attribute_mentioned | 500 | 0.972 | 0.604 | 0.074 |

21 rows labelled `disability` are left out of `hardship_reason`: it is a protected ground (POL-COLL-004 §4.6) and the extractor is not asked for it.

## Time to flag hardship

Flagged 158 of 262 labelled hardship calls (104 missed); delay after the customer first mentions hardship: mean 0.5 s, median 0.0 s.
