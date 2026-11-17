# Layer 4 post-call summary: evaluation on the public labels

Generated 2026-10-04 04:47 by `python -m layer4.eval_summary` on 30 labelled calls (model version `postcall-rules-1|llm:openai/gpt-oss-20b|q:hardship@1`).

| Measure | Value |
|---|---|
| Template fidelity: gold fields written by `narrative()` equal the gold sentence | 30 (100%) |
| End to end: drafted sentence equals the gold sentence | 20 (67%) |
| End to end: mean token F1 against the gold sentence | 0.87 |

| Field (end to end) | Correct |
|---|---|
| hardship | 24 (80%) |
| dispute | 30 (100%) |
| promise_amount | 30 (100%) |
| promise_date | 29 (97%) |
| next_step | 25 (83%) |

Disability is written as "a health-related hardship" on purpose (protected ground, POL-COLL-004 §4.6), so those calls never match the gold sentence exactly.
