"""One versioned SQL definition for historical training and live feature lookup.

History is replayed against the release's resolved identity map. Unavailable
historical state remains NULL; today's C360 is never backfilled into the past.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
import re
import pandas as pd
from shared.db import connect

PROHIBITED_TOKENS = ("gender", "marital", "citizenship", "household", "newcomer", "accessibility",
                     "vulnerability", "accent", "age", "language", "aml")
REGISTRY_VERSION = "l3-features-5"


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    entity: str
    definition: str
    source: str
    refresh: str = "daily and on request"
    owner: str = "Layer 3"
    sensitivity: str = "internal"
    ai_derived: bool = False
    accuracy: float | None = None
    dtype: str = "float"
    decision_input: bool = True
    version: str = REGISTRY_VERSION


def feature(name, sql, source, **kwargs):
    return FeatureDefinition(name, "customer", sql, source, **kwargs)


ACCOUNT_ROWS = """SELECT * FROM layer3.account_history
 WHERE golden_customer_id=e.golden_customer_id AND available_at<=e.cutoff
 QUALIFY row_number() OVER (PARTITION BY account_id ORDER BY available_at DESC,priority DESC)=1"""
PROMISES = """FROM gold.promises_to_pay p WHERE p.golden_customer_id=e.golden_customer_id
 AND try_cast(p.ptp_created_ts AS TIMESTAMP)<e.cutoff
 AND (e.exclude_ptp_id IS NULL OR p.ptp_id<>e.exclude_ptp_id)"""
RESOLVED = """ AND try_cast(p.status_ts AS TIMESTAMP)<=e.cutoff
 AND try_cast(p.status_ts AS TIMESTAMP)>=try_cast(p.ptp_created_ts AS TIMESTAMP)
 AND p.ptp_status_adjusted IN ('kept','partially_kept','broken')"""
TEXT_ROWS = """FROM layer3.text_features t WHERE t.golden_customer_id=e.golden_customer_id
 AND t.available_at<=e.cutoff AND t.event_ts<=e.cutoff
 ORDER BY t.available_at DESC,t.event_ts DESC,t.source_type='approved_summary' DESC,t.source_id DESC LIMIT 1"""
# An approved summary is known at the same moment as the machine reading of its call; the agent's version wins.


FEATURES = (
    feature("product_count", f"SELECT count(DISTINCT product) FROM ({ACCOUNT_ROWS})", "gold.account_monthly_snapshot"),
    # Sums and averages are rounded: DuckDB adds in parallel, so unrounded doubles can differ in the last bit between
    # two runs, and training and live values must be identical.
    feature("total_credit_exposure", f"SELECT round(sum(credit_exposure), 2) FROM ({ACCOUNT_ROWS})", "gold.account_monthly_snapshot"),
    feature("max_dpd_current", f"SELECT max(dpd) FROM ({ACCOUNT_ROWS})", "gold.account_monthly_snapshot"),
    feature("current_bucket", f"SELECT CASE WHEN max(dpd) IS NULL THEN NULL WHEN max(dpd)=0 THEN 'current' "
            f"WHEN max(dpd)<30 THEN '1-29' WHEN max(dpd)<60 THEN '30-59' WHEN max(dpd)<90 THEN '60-89' ELSE '90+' END "
            f"FROM ({ACCOUNT_ROWS})", "gold.account_monthly_snapshot", dtype="string"),
    feature("utilisation", f"SELECT round(sum(balance) FILTER (WHERE product='card') / "
            f"nullif(sum(credit_limit) FILTER (WHERE product='card'),0), 6) FROM ({ACCOUNT_ROWS})", "gold.account_monthly_snapshot"),
    # Point in time (the gold view's _loaded_at is the build date, so filtering on it left both always empty):
    feature("hardship_flag", "SELECT coalesce(bool_or(c.hardship_flag='true'), false) FROM gold.cases c "
            "WHERE c.golden_customer_id=e.golden_customer_id AND try_cast(c.case_open_date AS TIMESTAMP)<=e.cutoff",
            "gold.cases", dtype="bool", decision_input=False, sensitivity="protective only"),
    feature("incident_affected", "SELECT count(*)>0 FROM gold.incident_effects i JOIN bronze.ops_events o "
            "ON o.event_id=i.incident_id WHERE i.golden_customer_id=e.golden_customer_id "
            "AND try_cast(o.end_ts AS TIMESTAMP)<=e.cutoff", "gold.incident_effects", dtype="bool"),
    feature("prior_resolved_ptp_count", f"SELECT count(*) {PROMISES} {RESOLVED}", "gold.promises_to_pay"),
    feature("prior_broken_ptp_count", f"SELECT count(*) {PROMISES} {RESOLVED} AND p.ptp_status_adjusted='broken'", "gold.promises_to_pay"),
    feature("broken_promises_90d", f"SELECT count(*) {PROMISES} {RESOLVED} AND p.ptp_status_adjusted='broken' "
            "AND try_cast(p.status_ts AS TIMESTAMP)>e.cutoff-INTERVAL 90 DAY", "gold.promises_to_pay"),
    feature("prior_kept_ptp_rate", f"SELECT round(avg(CASE WHEN p.ptp_status_adjusted='broken' THEN 0.0 ELSE 1.0 END), 6) {PROMISES} {RESOLVED}", "gold.promises_to_pay"),
    feature("prior_avg_promised_amount", f"SELECT round(avg(try_cast(p.ptp_amount AS DOUBLE)), 2) {PROMISES}", "gold.promises_to_pay"),
    feature("usual_payday", "SELECT median(day(last_credit_date)) FROM layer3.salary_history WHERE golden_customer_id=e.golden_customer_id "
            "AND available_at<=e.cutoff AND available_at>e.cutoff-INTERVAL 180 DAY AND is_payroll AND credit_count>0", "gold.salary_credit_history"),
    feature("salary_delay_days", "SELECT CASE WHEN max(last_credit_date) IS NULL THEN NULL ELSE "
            "greatest(date_diff('day',max(last_credit_date),cast(e.cutoff AS DATE))-31,0) END FROM layer3.salary_history "
            "WHERE golden_customer_id=e.golden_customer_id AND available_at<=e.cutoff AND is_payroll", "gold.salary_credit_history"),
    feature("bureau_score_change", "SELECT max(CASE WHEN rn=1 THEN risk_score END)-max(CASE WHEN rn=2 THEN risk_score END) FROM "
            "(SELECT risk_score,row_number() OVER (ORDER BY available_at DESC) rn FROM layer3.bureau_history "
            "WHERE golden_customer_id=e.golden_customer_id AND available_at<=e.cutoff)", "gold.bureau_history"),
) + tuple(
    feature(f"response_rate_{channel}_90d", f"SELECT round(avg(response), 6) FROM layer3.contact_responses "
            f"WHERE golden_customer_id=e.golden_customer_id AND channel='{channel}' "
            "AND available_at<=e.cutoff AND event_ts>e.cutoff-INTERVAL 90 DAY", "gold.contact_timeline")
    for channel in ("call", "sms", "email", "push")
) + tuple(
    feature(f"text_{qid}", (f"SELECT CASE {qid} WHEN 'firm' THEN 0.85 WHEN 'conditional' THEN 0.45 WHEN 'weak' THEN "
                            f"0.3 END {TEXT_ROWS}") if qid == "promise_intent_strength" else f"SELECT {qid} {TEXT_ROWS}",
            "layer3.text_features", ai_derived=True,
            dtype=dtype, decision_input=allowed, sensitivity="internal" if allowed else "protective only")
    for qid, dtype, allowed in (
        ("hardship", "string", False), ("hardship_reason", "string", False),
        ("stated_delay_reason", "string", True), ("promise_mentioned", "bool", True),  # delay reason: v2 choice
        ("promise_amount", "float", True), ("promise_date", "string", False),
        ("promise_intent_strength", "float", True), ("dispute", "bool", False),
        ("complaint", "bool", False), ("cease_request", "bool", False), ("sentiment", "float", False))
)


def _validate_registry():
    if len({f.name for f in FEATURES})!=len(FEATURES):
        raise ValueError("duplicate feature names")
    for f in FEATURES:
        words=re.split(r"[^a-z]+|_", f"{f.name} {f.definition}".lower())
        if set(words)&set(PROHIBITED_TOKENS):
            raise ValueError(f"protected input: {f.name}")


_validate_registry()


EVAL_JSON = __import__("pathlib").Path(__file__).resolve().parents[2] / "outputs" / "layer3_label_eval.json"


def measured_accuracy() -> dict:
    """question id -> accuracy from the latest label evaluation (n-weighted over transcripts and notes)."""
    import json
    try:
        results = json.loads(EVAL_JSON.read_text(encoding="utf-8"))["results"]
    except (OSError, ValueError, KeyError):
        return {}
    totals: dict = {}
    for r in results:
        n, correct = totals.get(r["question"], (0, 0.0))
        totals[r["question"]] = (n + r["n"], correct + r["accuracy"] * r["n"])
    return {q: round(c / n, 4) for q, (n, c) in totals.items() if n}


def evaluation_meta() -> dict:
    """question id -> (labelled rows used, extractor the accuracy was measured with)."""
    import json
    try:
        data = json.loads(EVAL_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    meta: dict = {}
    for r in data.get("results", []):
        n, _ = meta.get(r["question"], (0, None))
        meta[r["question"]] = (n + r["n"], data.get("extractor"))
    return meta


def stored_extractor(con=None) -> dict:
    """question id -> the extractor version(s) behind the values stored in layer3.text_features."""
    if con is None:
        return {}
    try:
        cols = [r[0] for r in con.execute("""SELECT column_name FROM information_schema.columns WHERE table_schema='layer3'
                                             AND table_name='text_features' AND column_name LIKE '%__model_version'""").fetchall()]
        out = {}
        for c in cols:
            versions = [r[0] for r in con.execute(f'SELECT DISTINCT "{c}" FROM layer3.text_features '
                                                  f'WHERE "{c}" IS NOT NULL LIMIT 3').fetchall()]
            out[c[: -len("__model_version")]] = ", ".join(sorted(versions))
        return out
    except Exception:
        return {}


def registry_records(con=None):
    """The catalogue: every feature with its definition and, for AI-derived features, the prompt, question version,
    extractor and measured accuracy against the human labels (claim: each LLM feature ships with all four)."""
    from layer3.text.extract import load_questions
    questions = {q["id"]: q for q in load_questions()}
    accuracy, meta, stored = measured_accuracy(), evaluation_meta(), stored_extractor(con)
    records = []
    for f in FEATURES:
        record = asdict(f)
        record.update(modality="structured (SQL)", prompt=None, question_version=None, extractor="SQL",
                      accuracy_n=None, accuracy_measured_with=None)
        if f.ai_derived and f.name.startswith("text_"):
            qid = f.name[len("text_"):]
            q = questions.get(qid, {})
            n, measured_with = meta.get(qid, (None, None))
            record.update(modality="voice (call transcripts) and text (agent notes)",
                          prompt=q.get("wording"), question_version=q.get("version"),
                          extractor=stored.get(qid) or "LLM via shared.decide; rules fallback without an API key",
                          accuracy=accuracy.get(qid, f.accuracy), accuracy_n=n, accuracy_measured_with=measured_with)
        records.append(record)
    return records


def publish_registry(con=None):
    own=con is None
    con=con or connect(read_only=False)
    con.register("l3_registry_frame", pd.DataFrame(registry_records(con)))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS layer3")
        con.execute("CREATE OR REPLACE TABLE layer3.feature_registry AS SELECT * FROM l3_registry_frame")
    finally:
        con.unregister("l3_registry_frame")
        if own:
            con.close()
    return len(FEATURES)


def _normalise_as_of(value):
    parsed=datetime.fromisoformat(str(value).replace("Z", "+00:00")) if not isinstance(value,datetime) else value
    if parsed.tzinfo:
        raise ValueError("Use a naive local timestamp: release events use customer-local time.")
    return parsed.isoformat()


def build_training_frame(events, con=None):
    frame=events.copy() if isinstance(events,pd.DataFrame) else pd.DataFrame(events)
    if missing:={"golden_customer_id","as_of"}-set(frame):
        raise ValueError(f"events is missing columns: {sorted(missing)}")
    if frame.empty:
        return pd.DataFrame(columns=[*frame.columns,*(f.name for f in FEATURES)])
    if frame.golden_customer_id.isna().any() or frame.golden_customer_id.astype(str).str.strip().eq("").any():
        raise ValueError("golden_customer_id is required")
    if set(frame)&{f.name for f in FEATURES}:
        raise ValueError("events must not already contain feature columns")
    original=list(frame.columns)
    frame["as_of"]=frame.as_of.map(_normalise_as_of)
    frame["golden_customer_id"]=frame.golden_customer_id.astype(str)
    frame["cutoff"]=pd.to_datetime(frame.as_of)
    frame["exclude_ptp_id"]=frame["ptp_id"].astype(str) if "ptp_id" in frame else pd.Series([None]*len(frame),index=frame.index,dtype='string')
    frame["_row_order"]=range(len(frame))
    own=con is None
    con=con or connect()
    con.register("l3_training_events",frame)
    columns=",".join(f'e."{c}"' for c in original)
    expressions=",".join(f'({f.definition}) AS "{f.name}"' for f in FEATURES)
    try:
        return con.execute(f"SELECT {columns},{expressions} FROM l3_training_events e ORDER BY e._row_order").df()
    except Exception as exc:
        raise RuntimeError(f"Feature lookup failed. Run python -m layer3.build first. ({exc})") from exc
    finally:
        con.unregister("l3_training_events")
        if own:
            con.close()


def get_features(golden_customer_id,as_of):
    frame=build_training_frame([{"golden_customer_id":golden_customer_id,"as_of":as_of}])
    return {f.name:None if pd.isna(frame.iloc[0][f.name]) else frame.iloc[0][f.name] for f in FEATURES}
