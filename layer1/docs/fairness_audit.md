# Fairness audit of identity matching

Run on full data, 2026-10-03. Aggregated by group only (groups under 50 customers suppressed); computed in the
`restricted` schema from `restricted.fairness_link_rates`. Architecture §9: a gap triggers an investigation of the
cleaning rules or comparisons; it never changes an individual decision.

**Overall** (1,017,885 CRM customers): linked to at least one other record 99.46%, waiting in review 6.69%,
high identity confidence 80.46%.

## Result

- Every protected group (gender, marital status, citizenship, newcomer programme, accessibility needs, age band)
  is within **0.5 percentage points** of the overall rate on linking, review and high confidence. No protected
  attribute is associated with different matching outcomes. Investigation threshold: 5 points.
- **One gap, from missing data rather than a protected group:** the 5,018 customers with no date of birth (age band
  missing) can never be auto-linked, because CLAUDE.md rule 1 requires identical DOBs. 99.86% of them wait in review
  and 0.14% are high confidence, so agents must verify identity before discussing their debt.
  - This is the intended precision-first behaviour, but unlike every other group they almost all stay unverified.
  - **Recommended action** (for the business owner, not applied): review them first in the queue, or confirm their
    DOB at the next contact, so they do not stay at low confidence indefinitely.

## Rates by group

| Attribute | Group | Customers | Linked | In review | High confidence |
|---|---|---:|---:|---:|---:|
| accessibility_needs_flag | false | 987,337 | 99.46% | 6.69% | 80.46% |
| accessibility_needs_flag | true | 30,548 | 99.39% | 6.73% | 80.50% |
| age_band | 25-34 | 231,801 | 99.96% | 6.23% | 80.82% |
| age_band | 35-44 | 223,584 | 99.95% | 6.26% | 80.83% |
| age_band | 45-54 | 202,497 | 99.96% | 6.23% | 80.86% |
| age_band | 18-24 | 131,700 | 99.95% | 6.19% | 80.62% |
| age_band | 55-64 | 130,848 | 99.95% | 6.23% | 81.09% |
| age_band | 65+ | 92,437 | 99.96% | 6.20% | 81.04% |
| age_band | (missing) | 5,018 | 0.00% | 99.86% | 0.14% |
| citizenship_status | citizen | 793,908 | 99.47% | 6.68% | 80.44% |
| citizenship_status | permanent_resident | 143,388 | 99.45% | 6.71% | 80.58% |
| citizenship_status | work_permit | 40,499 | 99.41% | 6.74% | 80.54% |
| citizenship_status | study_permit | 30,008 | 99.43% | 6.79% | 80.19% |
| citizenship_status | (missing) | 10,082 | 99.46% | 6.52% | 80.87% |
| gender_code | M | 494,149 | 99.47% | 6.63% | 80.48% |
| gender_code | F | 493,359 | 99.45% | 6.74% | 80.43% |
| gender_code | X | 10,258 | 99.50% | 7.00% | 80.51% |
| gender_code | (missing) | 10,177 | 99.35% | 6.77% | 80.41% |
| gender_code | U | 9,942 | 99.59% | 6.78% | 80.93% |
| marital_status | married | 353,392 | 99.37% | 6.82% | 80.40% |
| marital_status | single | 244,211 | 99.63% | 6.48% | 80.60% |
| marital_status | common_law | 128,531 | 99.48% | 6.77% | 80.38% |
| marital_status | (missing) | 81,129 | 99.49% | 6.60% | 80.63% |
| marital_status | divorced | 67,646 | 99.33% | 6.72% | 80.47% |
| marital_status | separated | 60,970 | 99.34% | 6.81% | 80.13% |
| marital_status | undisclosed | 45,242 | 99.48% | 6.65% | 80.51% |
| marital_status | widowed | 36,764 | 99.48% | 6.61% | 80.47% |
| newcomer_program_flag | false | 937,329 | 99.47% | 6.70% | 80.45% |
| newcomer_program_flag | true | 80,556 | 99.42% | 6.63% | 80.59% |

Rates are per golden customer that has a CRM record. "Linked" counts any second record, including the bureau record
almost every customer has, so it is close to 100% everywhere; the review and confidence rates are the informative
columns.
