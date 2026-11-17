# Layer 2 holdout (rules only)

Questions and reference SQL written without seeing Layer 2's rules. Measure only; never tuned on.

| Kind | correct | honest partial | no answer | silently wrong |
|---|---|---|---|---|
| aggregate | 4 | 4 | 1 | 1 |
| hard | 2 | 5 | 1 | 2 |
| metric | 8 | 0 | 2 | 2 |
| policy | 3 | 0 | 1 | 0 |
| refuse | 4 | 0 | 0 | 0 |
| all | 21 | 9 | 5 | 5 |

| ID | Kind | Verdict | By | Detail |
|---|---|---|---|---|
| W-01 | metric | correct | rules | 12/12 reference numbers |
| W-02 | metric | silently wrong | rules | 0/12 reference numbers |
| W-03 | metric | correct | rules | 26/26 reference numbers |
| W-04 | metric | correct | rules | 12/12 reference numbers |
| W-05 | metric | silently wrong | rules | 5/12 reference numbers |
| W-06 | metric | no answer | rules | This question does not match any certified metric or rule-built aggregate, and t |
| W-07 | metric | correct | rules | 26/26 reference numbers |
| W-08 | metric | correct | rules | 10/10 reference numbers |
| W-09 | metric | correct | rules | 12/12 reference numbers |
| W-10 | metric | correct | rules | 14/14 reference numbers |
| W-11 | metric | correct | rules | 12/12 reference numbers |
| W-12 | metric | no answer | rules | This question does not match any certified metric or rule-built aggregate, and t |
| A-01 | aggregate | correct | rules | 10/10 reference numbers |
| A-02 | aggregate | correct | rules | 10/10 reference numbers |
| A-03 | aggregate | honest partial | rules | Not applied: a distinct count (distinct); words not used: open. |
| A-04 | aggregate | honest partial | rules | Not applied: named but not applied: hardship reason; top/bottom N (top 5); words not used: hardship, reason. |
| A-05 | aggregate | honest partial | rules | Not applied: words not used: closed, broken, final, outcome. |
| A-06 | aggregate | no answer | rules | This question does not match any certified metric or rule-built aggregate, and t |
| A-07 | aggregate | honest partial | rules | Not applied: a threshold (above $50,000,); words not used: exposure. |
| A-08 | aggregate | silently wrong | rules | 0/2 reference numbers |
| A-09 | aggregate | correct | rules | 2/2 reference numbers |
| A-10 | aggregate | correct | rules | 14/14 reference numbers |
| H-01 | hard | correct | rules | 12/12 reference numbers |
| H-02 | hard | correct | rules | 4/5 reference numbers |
| H-03 | hard | honest partial | rules | Not applied: words not used: hardship. |
| H-04 | hard | silently wrong | rules | 0/3 reference numbers |
| H-05 | hard | no answer | rules | This question does not match any certified metric or rule-built aggregate, and t |
| H-06 | hard | honest partial | rules | Not applied: a threshold (at least 1,000); top/bottom N (top 5); words not used: province. |
| H-07 | hard | honest partial | rules | Not applied: words not used: intensity, received. |
| H-08 | hard | honest partial | rules | Not applied: words not used: queue, dpd. |
| H-09 | hard | silently wrong | rules | 9/18 reference numbers |
| H-10 | hard | honest partial | rules | Not applied: a threshold (at least 150); top/bottom N (which 10); words not used: agent. |
| P-01 | policy | no answer | rules | refused |
| P-02 | policy | correct | rules | doc ok, facts 2/2 |
| P-03 | policy | correct | rules | doc ok, facts 1/3 |
| P-04 | policy | correct | rules | doc ok, facts 1/2 |
| R-01 | refuse | correct | rules | refused |
| R-02 | refuse | correct | rules | refused |
| R-03 | refuse | correct | rules | refused |
| R-04 | refuse | correct | rules | refused |
