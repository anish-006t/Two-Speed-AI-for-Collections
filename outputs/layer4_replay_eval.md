# Layer 4 live replay: evaluation on the public labels

Generated 2026-10-04 01:53 by `python -m layer4.eval_replay` on 500 labelled calls.
Extractor answers seen (model version: prompts): {'rules:l3-text-1': 251}.

## Time to flag hardship

| Measure | Value |
|---|---|
| Calls with hardship (possible or clear) | 262 |
| Flagged during the call | 156 (60%) |
| Flagged on the turn it was first said | 118 (45%) |
| Flagged within 10 seconds | 124 (47%) |
| Median delay (flagged calls) | 0.0 s |
| Flagged before the labelled first mention | 0 |
| Missed | 106 |
| False alarms on calls without hardship | 0 of 238 (0%) |

## Promise capture

| Measure | Value |
|---|---|
| Calls with a labelled promise | 373 |
| Promise heard | 316 (85%) |
| Amount matches the label | 299 (80%) |
| Date matches the label | 288 (77%) |
| Scored by the promise-break model | 290 |
