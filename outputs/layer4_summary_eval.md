# Layer 4 post-call summary: evaluation on the public labels

Generated 2026-10-04 02:34 by `python -m layer4.eval_summary` on 500 labelled calls (model version `postcall-rules-1|rules:l3-text-1`).

| Measure | Value |
|---|---|
| Template fidelity: gold fields written by `narrative()` equal the gold sentence | 484 (97%) |
| End to end: drafted sentence equals the gold sentence | 200 (40%) |
| End to end: mean token F1 against the gold sentence | 0.70 |

| Field (end to end) | Correct |
|---|---|
| hardship | 350 (70%) |
| dispute | 442 (88%) |
| promise_amount | 417 (83%) |
| promise_date | 415 (83%) |
| next_step | 304 (61%) |

Disability is written as "a health-related hardship" on purpose (protected ground, POL-COLL-004 §4.6), so those calls never match the gold sentence exactly.
