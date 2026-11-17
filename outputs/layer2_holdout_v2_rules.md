# Layer 2 holdout (rules only)

Questions and reference SQL written without seeing Layer 2's rules. Measure only; never tuned on.

| Kind | correct | honest partial | no answer | silently wrong |
|---|---|---|---|---|
| aggregate | 2 | 5 | 2 | 1 |
| hard | 1 | 8 | 1 | 0 |
| metric | 5 | 1 | 1 | 5 |
| policy | 1 | 0 | 1 | 2 |
| refuse | 4 | 0 | 0 | 0 |
| all | 13 | 14 | 5 | 8 |

| ID | Kind | Verdict | By | Detail |
|---|---|---|---|---|
| V-01 | metric | correct | rules | 14/14 reference numbers |
| V-02 | metric | silently wrong | rules | 0/7 reference numbers |
| V-03 | metric | silently wrong | rules | 1/5 reference numbers |
| V-04 | metric | correct | rules | 6/6 reference numbers |
| V-05 | metric | no answer | rules | No rows match: metric: Cost per contact; by queue; filters: channel in ['system' |
| V-06 | metric | silently wrong | rules | 1/2 reference numbers |
| V-07 | metric | correct | rules | 6/6 reference numbers |
| V-08 | metric | correct | rules | 7/7 reference numbers |
| V-09 | metric | correct | rules | 26/26 reference numbers |
| V-10 | metric | silently wrong | rules | 1/10 reference numbers |
| V-11 | metric | silently wrong | rules | 0/2 reference numbers |
| V-12 | metric | honest partial | rules | Not applied: words not used: active. |
| V-13 | aggregate | correct | rules | 6/6 reference numbers |
| V-14 | aggregate | honest partial | rules | Not applied: words not used: came. |
| V-15 | aggregate | honest partial | rules | Not applied: a distinct count (different); words not used: open. |
| V-16 | aggregate | correct | rules | 2/2 reference numbers |
| V-17 | aggregate | no answer | rules | This question does not match any certified metric or rule-built aggregate, and t |
| V-18 | aggregate | no answer | rules | This question does not match any certified metric or rule-built aggregate, and t |
| V-19 | aggregate | honest partial | rules | Not applied: words not used: inbound. |
| V-20 | aggregate | honest partial | rules | Not applied: words not used: request. |
| V-21 | aggregate | honest partial | rules | Not applied: words not used: more, past, due. |
| V-22 | aggregate | silently wrong | rules | 2/12 reference numbers |
| V-23 | hard | correct | rules | 2/3 reference numbers |
| V-24 | hard | honest partial | rules | Not applied: words not used: new, opened. |
| V-25 | hard | no answer | rules | No rows match: metric: case count (plain aggregate, not a certified metric); by  |
| V-26 | hard | honest partial | rules | Not applied: a threshold (more than $10,000); words not used: overdue. |
| V-27 | hard | honest partial | rules | Not applied: words not used: dispute. |
| V-28 | hard | honest partial | rules | Not applied: a threshold (at least 100); top/bottom N (top 5); words not used: agent. |
| V-29 | hard | honest partial | rules | Not applied: words not used: queue, product. |
| V-30 | hard | honest partial | rules | Not applied: words not used: dollar. |
| V-31 | hard | honest partial | rules | Not applied: a comparison of periods (vs); words not used: loan. |
| V-32 | hard | honest partial | rules | Not applied: a comparison of periods (against). |
| V-33 | policy | silently wrong | rules | doc missing, facts 0/3 |
| V-34 | policy | no answer | rules | refused |
| V-35 | policy | silently wrong | rules | doc missing, facts 0/3 |
| V-36 | policy | correct | rules | doc ok, facts 1/3 |
| V-37 | refuse | correct | rules | refused |
| V-38 | refuse | correct | rules | refused |
| V-39 | refuse | correct | rules | refused |
| V-40 | refuse | correct | rules | refused |
