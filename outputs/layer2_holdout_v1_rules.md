# Layer 2 holdout (rules only)

Questions and reference SQL written without seeing Layer 2's rules. Measure only; never tuned on.

| Kind | correct | honest partial | no answer | silently wrong |
|---|---|---|---|---|
| aggregate | 3 | 7 | 0 | 0 |
| hard | 0 | 6 | 0 | 2 |
| metric | 1 | 10 | 1 | 2 |
| policy | 0 | 0 | 1 | 3 |
| refuse | 3 | 0 | 0 | 1 |
| all | 7 | 23 | 2 | 8 |

| ID | Kind | Verdict | By | Detail |
|---|---|---|---|---|
| H-01 | metric | honest partial | rules | Not applied: words not used: due, channel. |
| H-02 | metric | honest partial | rules | Not applied: words not used: product. |
| H-03 | metric | no answer | rules | This question does not match any certified metric or rule-built aggregate, and t |
| H-04 | metric | honest partial | rules | Not applied: words not used: made, montreal, site. |
| H-05 | metric | honest partial | rules | Not applied: words not used: queue. |
| H-06 | metric | honest partial | rules | Not applied: words not used: cost. |
| H-07 | metric | silently wrong | rules | 0/12 reference numbers |
| H-08 | metric | honest partial | rules | Not applied: words not used: past, open. |
| H-09 | metric | honest partial | rules | Not applied: words not used: opened, hardship. |
| H-10 | metric | honest partial | rules | Not applied: words not used: product, type, personal, auto, mortgage, loc. |
| H-11 | metric | silently wrong | rules | 0/3 reference numbers |
| H-12 | metric | honest partial | rules | Not applied: words not used: card, loan. |
| H-13 | metric | honest partial | rules | Not applied: words not used: active, right. |
| H-14 | metric | correct | rules | 14/21 reference numbers |
| H-15 | aggregate | honest partial | rules | Not applied: words not used: queue. |
| H-16 | aggregate | honest partial | rules | Not applied: words not used: treatment. |
| H-17 | aggregate | honest partial | rules | Not applied: named but not applied: hardship reason; top/bottom N (top 5); words not used: hardship, reason. |
| H-18 | aggregate | correct | rules | 7/7 reference numbers |
| H-19 | aggregate | honest partial | rules | Not applied: words not used: channel, came. |
| H-20 | aggregate | correct | rules | 7/7 reference numbers |
| H-21 | aggregate | honest partial | rules | Not applied: words not used: transcript, inbound. |
| H-22 | aggregate | honest partial | rules | Not applied: a distinct count (distinct); words not used: open. |
| H-23 | aggregate | correct | rules | 10/10 reference numbers |
| H-24 | aggregate | honest partial | rules | Not applied: an exclusion (excluded); words not used: event, holiday. |
| H-25 | hard | honest partial | rules | Not applied: a change between periods (change); words not used: queue. |
| H-26 | hard | silently wrong | rules | 1/12 reference numbers |
| H-27 | hard | honest partial | rules | Not applied: a threshold (more than 5); words not used: queue. |
| H-28 | hard | silently wrong | rules | 0/5 reference numbers |
| H-29 | hard | honest partial | rules | Not applied: words not used: made. |
| H-30 | hard | honest partial | rules | Not applied: words not used: strategy. |
| H-31 | hard | honest partial | rules | Not applied: a comparison of periods (versus); a distinct count (different); an exclusion (without); words not used: har |
| H-32 | hard | honest partial | rules | Not applied: words not used: right, party. |
| H-33 | policy | silently wrong | rules | doc missing, facts 0/3 |
| H-34 | policy | silently wrong | rules | doc missing, facts 0/2 |
| H-35 | policy | silently wrong | rules | doc missing, facts 0/3 |
| H-36 | policy | no answer | rules | refused |
| H-37 | refuse | correct | rules | refused |
| H-38 | refuse | silently wrong | rules | answered |
| H-39 | refuse | correct | rules | refused |
| H-40 | refuse | correct | rules | refused |
