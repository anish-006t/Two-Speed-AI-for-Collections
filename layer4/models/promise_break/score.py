"""Live promise-break scoring with SHAP-style LightGBM contributions."""
from __future__ import annotations

import json
import math
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta

import pandas as pd

from layer3.features.defs import get_features
from layer4.models.promise_break.train import METADATA_PATH, MODEL_PATH, apply_encoder
from shared.db import connect

BASELINE_VERSION = "promise-baseline-1"


def _salary_safer_date(golden_customer_id: str, as_of: date) -> str | None:
    """Day after the customer's most commonly stated income day, if known."""
    try:
        rows = connect(read_only=True).execute(
            "SELECT customer_stated_income_date FROM gold.promises_to_pay "
            "WHERE golden_customer_id = ? AND try_cast(ptp_created_ts AS DATE) <= ? "
            "AND customer_stated_income_date IS NOT NULL", [golden_customer_id, as_of]).fetchall()
    except Exception:
        return None
    days = []
    for (raw,) in rows:
        try:
            parsed = raw if isinstance(raw, (date, datetime)) else date.fromisoformat(str(raw)[:10])
            days.append(parsed.day)
        except (TypeError, ValueError):
            continue
    if not days:
        return None
    salary_day = Counter(days).most_common(1)[0][0]
    year, month = as_of.year, as_of.month
    for _ in range(2):
        day = min(salary_day, monthrange(year, month)[1])
        candidate = date(year, month, day) + timedelta(days=1)
        if candidate >= as_of:
            return candidate.isoformat()
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return None


def _payday_safer_date(usual_payday, as_of: date, due: date) -> str | None:
    """The day after the customer's usual salary credit (Layer 3 feature usual_payday), on or after the promised
    date's month, if the promised date falls before payday; None when payday is unknown or the date is already safe."""
    try:
        day = int(round(float(usual_payday)))
    except (TypeError, ValueError):
        return None
    year, month = due.year, due.month
    for _ in range(2):
        candidate = date(year, month, min(day, monthrange(year, month)[1])) + timedelta(days=1)
        if candidate >= due and candidate >= as_of:
            return None if (candidate - due).days == 0 else candidate.isoformat()
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return None


def _baseline(amount: float, due: date, as_of: date,
              why: str = "No trained model artifact is installed, so this is a transparent baseline score") -> dict:
    days_ahead = max((due - as_of).days, 0)
    risk = .15 + .42 * min(days_ahead / 30, 1) + .28 * min(math.log10(max(amount, 1)) / 4, 1)
    return {"risk": round(min(max(risk, 0), 1), 3), "reasons": [
        f"The promised date is {days_ahead} days away; longer horizons have more uncertainty",
        f"The promised amount is ${amount:,.2f}",
        why,
    ], "safer_date": None, "model_version": BASELINE_VERSION}


def _reason(column: str, contribution: float) -> str:
    labels = {"days_ahead": "Days until the promised payment", "promise_amount": "Promised payment amount",
              "prior_broken_ptp_count": "Prior broken promises", "prior_kept_ptp_rate": "Prior kept-promise rate",
              "prior_resolved_ptp_count": "Prior resolved promises", "prior_avg_promised_amount": "Usual promise amount",
              "max_dpd_current": "Current days past due", "total_credit_exposure": "Current credit exposure",
              "product_count": "Number of linked products", "hardship_flag": "Operational hardship care flag",
              "incident_affected": "Known incident effect", "current_bucket": "Current collections bucket",
              "broken_promises_90d": "Promises broken in the last 90 days", "utilisation": "Card utilisation",
              "usual_payday": "Usual salary day", "salary_delay_days": "Salary arriving later than usual",
              "bureau_score_change": "Recent change in bureau score",
              "response_rate_call_90d": "Answer rate to calls (90 days)", "response_rate_sms_90d": "Replies to SMS (90 days)",
              "response_rate_email_90d": "Email clicks (90 days)", "response_rate_push_90d": "App notification opens (90 days)",
              "text_stated_delay_reason": "Reason given for the late payment", "text_promise_mentioned": "Promise said on the call",
              "text_promise_amount": "Amount said on the call", "text_promise_intent_strength": "How firm the promise sounded"}
    base = column.split("=", 1)[0]
    direction = "raised" if contribution >= 0 else "lowered"
    detail = f" ({column.split('=', 1)[1]})" if "=" in column else ""
    return f"{labels.get(base, base.replace('_', ' ').title())}{detail} {direction} the estimated risk"


def score_promise(golden_customer_id: str, amount: float, due_date: str, as_of: str) -> dict:
    """Return calibrated break risk, three explanations, and a non-speculative safer date."""
    due, today = date.fromisoformat(due_date), date.fromisoformat(as_of[:10])
    amount = float(amount)
    if amount < 0:
        raise ValueError("amount cannot be negative")
    if not MODEL_PATH.exists() or not METADATA_PATH.exists():
        return _baseline(amount, due, today)
    import lightgbm as lgb
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    try:
        values = get_features(golden_customer_id, as_of)
    except Exception:   # history not built on this laptop: never fail Layer 4, say why the score is a baseline
        return _baseline(amount, due, today, "Feature history is not built here (run python -m layer3.build), "
                                             "so this is a transparent baseline score")
    values.update({"promise_amount": amount, "days_ahead": max((due - today).days, 0)})
    matrix = apply_encoder(pd.DataFrame([values]), metadata)
    model = lgb.Booster(model_file=str(MODEL_PATH))
    risk = float(model.predict(matrix)[0])
    contributions = model.predict(matrix, pred_contrib=True)[0][:-1]
    ranked = sorted(zip(matrix.columns, contributions), key=lambda item: (item[1] <= 0, -abs(item[1])))[:3]
    reasons = [_reason(column, float(value)) for column, value in ranked]
    while len(reasons) < 3:
        reasons.append("Other registered features had little effect on this estimate")
    return {"risk": round(min(max(risk, 0), 1), 4), "reasons": reasons,
            "safer_date": _payday_safer_date(values.get("usual_payday"), today, due)
                          or _salary_safer_date(golden_customer_id, today),
            "model_version": metadata["model_version"]}
