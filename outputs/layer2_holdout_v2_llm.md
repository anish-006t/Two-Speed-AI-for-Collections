# Layer 2 holdout (with the LLM)

Questions and reference SQL written without seeing Layer 2's rules. Measure only; never tuned on.

| Kind | correct | honest partial | no answer | silently wrong |
|---|---|---|---|---|
| aggregate | 5 | 1 | 0 | 4 |
| hard | 3 | 6 | 1 | 0 |
| metric | 7 | 0 | 0 | 5 |
| policy | 2 | 0 | 0 | 2 |
| refuse | 3 | 0 | 0 | 1 |
| all | 20 | 7 | 1 | 12 |

| ID | Kind | Verdict | By | Detail |
|---|---|---|---|---|
| V-01 | metric | correct | rules | 14/14 reference numbers |
| V-02 | metric | silently wrong | rules | 0/7 reference numbers |
| V-03 | metric | silently wrong | rules | 1/5 reference numbers |
| V-04 | metric | correct | rules | 6/6 reference numbers |
| V-05 | metric | correct | AI | 7/7 reference numbers |
| V-06 | metric | silently wrong | rules | 1/2 reference numbers |
| V-07 | metric | correct | rules | 6/6 reference numbers |
| V-08 | metric | correct | AI | 7/7 reference numbers |
| V-09 | metric | correct | rules | 26/26 reference numbers |
| V-10 | metric | silently wrong | rules | 1/10 reference numbers |
| V-11 | metric | silently wrong | rules | 0/2 reference numbers |
| V-12 | metric | correct | AI | 2/2 reference numbers |
| V-13 | aggregate | correct | rules | 6/6 reference numbers |
| V-14 | aggregate | silently wrong | AI | 3/10 reference numbers |
| V-15 | aggregate | silently wrong | AI | 1/2 reference numbers |
| V-16 | aggregate | correct | AI | 2/2 reference numbers |
| V-17 | aggregate | silently wrong | AI | 2/4 reference numbers |
| V-18 | aggregate | correct | AI | 3/3 reference numbers |
| V-19 | aggregate | honest partial | AI | Not applied: words not used: inbound. AI check: The available views do not include a call duration or end timestamp for  |
| V-20 | aggregate | correct | AI | 2/2 reference numbers |
| V-21 | aggregate | correct | AI | 5/5 reference numbers |
| V-22 | aggregate | silently wrong | rules | 2/12 reference numbers |
| V-23 | hard | correct | AI | 3/3 reference numbers |
| V-24 | hard | correct | AI | 9/9 reference numbers |
| V-25 | hard | no answer | rules | No rows match: metric: case count (plain aggregate, not a certified metric); by  |
| V-26 | hard | correct | AI | 8/8 reference numbers |
| V-27 | hard | honest partial | AI | Not applied: words not used: dispute. |
| V-28 | hard | honest partial | AI | Not applied: a threshold (at least 100); top/bottom N (top 5); words not used: agent. |
| V-29 | hard | honest partial | AI | Not applied: words not used: queue, product. |
| V-30 | hard | honest partial | AI | Not applied: words not used: dollar. |
| V-31 | hard | honest partial | rules | Not applied: a comparison of periods (vs); words not used: loan. |
| V-32 | hard | honest partial | rules | Not applied: a comparison of periods (against). |
| V-33 | policy | silently wrong | rules | doc missing, facts 0/3 |
| V-34 | policy | correct | rules | doc ok, facts 1/3 |
| V-35 | policy | silently wrong | rules | doc missing, facts 0/3 |
| V-36 | policy | correct | rules | doc ok, facts 1/3 |
| V-37 | refuse | correct | rules | refused |
| V-38 | refuse | correct | rules | refused |
| V-39 | refuse | correct | rules | refused |
| V-40 | refuse | silently wrong | rules | answered |
