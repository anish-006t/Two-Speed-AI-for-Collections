# Transcripts: RAG vs regex rules (20 labelled calls, seed 5)

| Field | A rules | B rag |
|---|---|---|
| **exact sentence** | 90.0% | 60.0% |
| reason | 95.0% | 95.0% |
| hardship | 95.0% | 90.0% |
| dispute | 100.0% | 100.0% |
| complaint | 100.0% | 100.0% |
| insolvency | 100.0% | 100.0% |
| cease_request | 100.0% | 100.0% |
| promise_amount | 95.0% | 85.0% |
| promise_date | 95.0% | 85.0% |
| next_step | 90.0% | 70.0% |

AI calls: 20 (failed 0); retrieval + extraction 17.7 s per call on average (rules: milliseconds, no call).

Decision (rule fixed before running: B >= A + 5 points exact, no field worse): **keep the regex rules**; fields worse with RAG: ['hardship', 'promise_amount', 'promise_date', 'next_step']

Caveat: the rules were tuned on these 500 labels, so this favours A; with this sample size one call is 5 points.
