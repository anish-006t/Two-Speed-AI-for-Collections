"""Constraints and decision bands (architecture §7; CLAUDE.md rules 1 and 2).

Every scored pair gets one decision:
  link     probability >= 0.95, identical non-null DOBs, and no constraint fires
  review   probability 0.70-0.95, or a constraint that needs a person, or a near-miss DOB with strong other evidence
  no_link  everything else, and every joint-holder / co-borrower pair (household link, never identity)
Saved human decisions override every score. Deterministic pairs (D1, D2) enter with their own link/review decision.
"""

AUTO_LINK = 0.95
REVIEW = 0.70

# Splink gamma values follow the level order in splink_model.COMPARISONS (highest = first non-null level).
GAMMA_DOB_SWAP, GAMMA_DOB_ONE_PART = 2, 1
GAMMA_PHONE_MASK = 1
GAMMA_FIRST_INITIAL_ONLY = 1

PERSISTENT_DDL = """
CREATE SCHEMA IF NOT EXISTS identity;
-- Human decisions, kept across runs; they override scores and become constraints (must-link / cannot-link).
CREATE TABLE IF NOT EXISTS identity.review_decisions (
    key_l VARCHAR, key_r VARCHAR,          -- key_l < key_r
    decision VARCHAR,                      -- link | no_link | unsure
    reviewer VARCHAR, reason VARCHAR, decided_at TIMESTAMP
);
"""

MATCH_PAIRS_SQL = f"""
CREATE OR REPLACE TABLE identity.match_pairs AS
WITH scored AS (
    SELECT least(unique_id_l, unique_id_r) AS key_l, greatest(unique_id_l, unique_id_r) AS key_r,
           match_probability, match_weight,
           gamma_first_name, gamma_last_name, gamma_dob, gamma_phone, gamma_postal_code, gamma_email,
           gamma_national_id_hash, gamma_pay_accounts,
           bf_first_name, bf_last_name, bf_dob, bf_phone, bf_postal_code, bf_email, bf_national_id_hash,
           bf_pay_accounts, bf_tf_adj_last_name
    FROM identity.splink_predictions
),
pairs AS (   -- union of Splink pairs and deterministic pairs (a pair can be both)
    SELECT coalesce(s.key_l, d.key_l) AS key_l, coalesce(s.key_r, d.key_r) AS key_r,
           s.* EXCLUDE (key_l, key_r), d.rule AS det_rule, d.decision AS det_decision
    FROM scored s FULL OUTER JOIN identity.deterministic_pairs d ON s.key_l = d.key_l AND s.key_r = d.key_r
),
households AS (
    SELECT DISTINCT least(k1, k2) AS key_l, greatest(k1, k2) AS key_r FROM (
        SELECT source_system || ':' || source_id AS k1, related_source_system || ':' || related_source_id AS k2
        FROM silver.household_refs WHERE resolves)
),
human AS (
    SELECT key_l, key_r, arg_max(decision, decided_at) AS decision, arg_max(reviewer, decided_at) AS reviewer
    FROM identity.review_decisions WHERE decision IN ('link', 'no_link') GROUP BY ALL
),
flags AS (
    SELECT p.*,
        l.source_system AS system_l, r.source_system AS system_r,
        l.dob IS NOT NULL AND r.dob IS NOT NULL AND l.dob = r.dob                          AS dob_identical,
        l.dob_conflict OR r.dob_conflict                                                   AS c_dob_conflict,
        hh.key_l IS NOT NULL                                                               AS c_household,
        coalesce(l.name_suffix <> r.name_suffix, false)                                    AS c_suffix_conflict,
        -- Look-alike: same address or phone, both full first names, clearly different (jr/sr, spouse, sibling).
        (l.postal_code = r.postal_code OR l.phone_e164 = r.phone_e164)
          AND l.first_name IS NOT NULL AND r.first_name IS NOT NULL
          AND jaro_winkler_similarity(l.first_name, r.first_name) < 0.92
          AND coalesce(l.preferred_name, '') <> r.first_name AND coalesce(r.preferred_name, '') <> l.first_name
          -- first and last name swapped (D-08) is the same person written in the other order, not a relative
          AND NOT (l.first_name = r.last_name AND l.last_name = r.first_name)
                                                                                           AS c_lookalike,
        l.dob = r.dob AND l.last_name = r.last_name AND l.postal_code = r.postal_code
          AND l.first_name IS NOT NULL AND r.first_name IS NOT NULL AND l.first_name <> r.first_name
                                                                                           AS c_possible_twins,
        p.gamma_dob IN ({GAMMA_DOB_SWAP}, {GAMMA_DOB_ONE_PART})                           AS dob_near_miss,
        -- Probability the pair would have without the DOB comparison: strong other evidence despite a DOB miss.
        1 / (1 + pow(2, -(p.match_weight - log2(greatest(p.bf_dob, 1e-12)))))              AS p_without_dob,
        -- Thin evidence: a card key known only by an initial whose phone matched only through the mask.
        ((l.source_system = 'CARDS' AND l.first_name IS NULL) OR (r.source_system = 'CARDS' AND r.first_name IS NULL))
          AND p.gamma_first_name = {GAMMA_FIRST_INITIAL_ONLY} AND p.gamma_phone = {GAMMA_PHONE_MASK}
                                                                                           AS thin_evidence,
        h.decision AS human_decision, h.reviewer
    FROM pairs p
    JOIN identity.splink_input l ON l.unique_id = p.key_l
    JOIN identity.splink_input r ON r.unique_id = p.key_r
    LEFT JOIN human h ON h.key_l = p.key_l AND h.key_r = p.key_r
    LEFT JOIN households hh ON hh.key_l = p.key_l AND hh.key_r = p.key_r
)
SELECT *,
    CASE
        WHEN human_decision IS NOT NULL                     THEN human_decision
        WHEN c_household                                    THEN 'no_link'
        WHEN det_decision IS NOT NULL                       THEN det_decision
        WHEN match_probability >= {AUTO_LINK} AND dob_identical
             AND NOT (c_dob_conflict OR c_suffix_conflict OR c_lookalike OR c_possible_twins)
                                                            THEN 'link'
        WHEN match_probability >= {REVIEW}                  THEN 'review'
        WHEN dob_near_miss AND p_without_dob >= {AUTO_LINK} THEN 'review'
        ELSE 'no_link'
    END AS decision,
    CASE
        WHEN human_decision IS NOT NULL THEN 'human'
        WHEN c_household                THEN 'household'
        WHEN det_rule IS NOT NULL       THEN det_rule
        ELSE 'splink'
    END AS decided_by,
    concat_ws(',',
        CASE WHEN c_household THEN 'household' END,
        CASE WHEN c_dob_conflict THEN 'dob_conflict' END,
        CASE WHEN c_suffix_conflict THEN 'suffix_conflict' END,
        CASE WHEN c_lookalike THEN 'lookalike' END,
        CASE WHEN c_possible_twins THEN 'possible_twins' END,
        CASE WHEN NOT dob_identical THEN 'dob_not_identical' END) AS constraint_flags
FROM flags
"""

HOUSEHOLD_LINKS_SQL = """
CREATE OR REPLACE TABLE identity.household_links AS
SELECT source_system || ':' || source_id AS key_a, related_source_system || ':' || related_source_id AS key_b,
       relation, account_id, resolves
FROM silver.household_refs
"""


def run(con):
    con.execute(PERSISTENT_DDL)
    con.execute(MATCH_PAIRS_SQL)
    con.execute(HOUSEHOLD_LINKS_SQL)
    return con.execute(
        "SELECT decided_by, decision, count(*) FROM identity.match_pairs GROUP BY ALL ORDER BY ALL"
    ).fetchall()
