"""Tier 2: Splink scoring across CRM, cards, loans, deposits and bureau (architecture §7).

One input table (identity.splink_input) holds every source key; Splink deduplicates it, which links across sources
and finds duplicates within a source in one pass. m-probabilities come from expectation maximisation over several
blocking rules, u-probabilities from random sampling. No labels are used: truth sets only evaluate (evaluate.py).
"""
import json

from splink import DuckDBAPI, Linker, SettingsCreator, block_on

from layer1.pipeline import config

INPUT_SQL = """
CREATE OR REPLACE TABLE identity.splink_input AS
WITH pay AS (   -- D3 evidence: deposit accounts each key pays from (loans, cards) or holds (deposits)
    SELECT source_system, source_id, list(DISTINCT deposit_account_id ORDER BY deposit_account_id) AS pay_accounts
    FROM silver.account_links WHERE resolves GROUP BY ALL
    UNION ALL
    SELECT 'CORE_BANKING', source_id, account_ids FROM silver.deposit_customers
)
SELECT i.source_key AS unique_id, i.source_system, i.source_id,
       i.first_name, i.first_initial, i.middle_initial, i.last_name, i.name_suffix, i.preferred_name,
       i.dob, i.dob_conflict, year(i.dob) AS dob_year,
       i.phone_e164, i.phone_first6, i.phone_last2, i.phone_masked, right(i.phone_e164, 7) AS phone_last7,
       i.postal_code, i.fsa, i.email, i.national_id_hash, p.pay_accounts
FROM silver.identity_input i
LEFT JOIN pay p USING (source_system, source_id)
"""


from layer1.identity.comparisons import COMPARISONS  # noqa: E402  (kept free of Splink so the review app can import it)

PREDICTION_BLOCKING_RULES = [
    block_on("dob", "fsa"),
    block_on("dob", "last_name"),
    block_on("phone_first6", "phone_last2"),
    block_on("phone_last7"),
    block_on("first_initial", "last_name", "postal_code"),
    block_on("pay_accounts", arrays_to_explode=["pay_accounts"]),
]

# Each comparison is trained in at least one session (a session can't train what it blocks on).
EM_BLOCKING_RULES = [
    block_on("dob", "fsa"),                                  # trains names, phone, email, ID, pay accounts
    block_on("first_initial", "last_name", "postal_code"),   # trains dob, phone
    block_on("phone_first6", "phone_last2"),                 # trains names, dob, postal
]

# Prior: about 1M people behind about 3.49M keys. Estimated from a near-deterministic rule; the decision bands are
# tuned on the truth sets afterwards, so the prior only needs to be the right order of magnitude.
PRIOR_RULES = ["l.dob = r.dob AND l.last_name = r.last_name AND l.first_name = r.first_name AND l.postal_code = r.postal_code"]
PRIOR_RECALL = 0.6


def settings():
    return SettingsCreator(
        link_type="dedupe_only",
        unique_id_column_name="unique_id",
        comparisons=COMPARISONS,
        blocking_rules_to_generate_predictions=PREDICTION_BLOCKING_RULES,
        retain_matching_columns=True,
        retain_intermediate_calculation_columns=True,   # bf_* per comparison: review explanations and near-miss DOB rule
        additional_columns_to_retain=["source_system"],
    )


def train_and_predict(con, threshold=0.3, model_path=None, u_max_pairs=5e6):
    """Build the input, train the model, score candidate pairs into identity.splink_predictions."""
    con.execute(INPUT_SQL)
    con.execute("CREATE OR REPLACE VIEW splink_input AS SELECT * FROM identity.splink_input")
    db_api = DuckDBAPI(connection=con)
    linker = Linker("splink_input", settings(), db_api)

    linker.training.estimate_probability_two_random_records_match(PRIOR_RULES, recall=PRIOR_RECALL)
    linker.training.estimate_u_using_random_sampling(max_pairs=u_max_pairs, seed=42)
    for rule in EM_BLOCKING_RULES:
        linker.training.estimate_parameters_using_expectation_maximisation(rule, estimate_without_term_frequencies=True)

    if model_path:
        linker.misc.save_model_to_json(str(model_path), overwrite=True)

    preds = linker.inference.predict(threshold_match_probability=threshold)
    con.execute(f"CREATE OR REPLACE TABLE identity.splink_predictions AS SELECT * FROM {preds.physical_name}")
    return linker


def model_weights(model_path):
    """Per-level match weights from a saved model, for review explanations."""
    model = json.loads(open(model_path).read())
    out = {}
    for comp in model["comparisons"]:
        out[comp["output_column_name"]] = [
            (lvl_.get("label_for_charts"), lvl_.get("m_probability"), lvl_.get("u_probability"))
            for lvl_ in comp["comparison_levels"]
        ]
    return out
