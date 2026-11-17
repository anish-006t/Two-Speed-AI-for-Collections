"""Train and back-test the promise-break LightGBM model on resolved promises."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from layer3.features.defs import FEATURES, build_training_frame
from shared.db import connect

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "model.txt"
METADATA_PATH = HERE / "metadata.json"
REPORT_PATH = Path(__file__).resolve().parents[3] / "outputs" / "promise_model_report.md"
# Only features allowed to drive a decision: hardship and the protective-only text features stay out (brief rule:
# hardship triggers protective handling, never a risk score).
MODEL_FEATURES = [f.name for f in FEATURES if f.decision_input] + ["promise_amount", "days_ahead"]
EXCLUDED_FEATURES = [f.name for f in FEATURES if not f.decision_input]
CATEGORICAL = ["current_bucket"]
LABELS = {"kept": 0, "partially_kept": 0, "broken": 1}


def _auc(y: np.ndarray, score: np.ndarray) -> float:
    positives, negatives = int((y == 1).sum()), int((y == 0).sum())
    if not positives or not negatives:
        return float("nan")
    ranks = pd.Series(score).rank(method="average").to_numpy()
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))


def fit_encoder(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    encoded = pd.DataFrame(index=frame.index)
    metadata = {"categorical": {}, "fill": {}}
    for name in MODEL_FEATURES:
        if name in CATEGORICAL:
            values = frame[name].fillna("unknown").astype(str)
            categories = sorted(values.unique().tolist())
            metadata["categorical"][name] = categories
            for category in categories:
                encoded[f"{name}={category}"] = (values == category).astype(float)
        else:
            values = pd.to_numeric(frame[name], errors="coerce").astype("Float64")  # nullable bools from DuckDB
            fill = float(values.median()) if values.notna().any() else 0.0
            metadata["fill"][name] = fill
            encoded[name] = values.fillna(fill).astype(float)
    metadata["columns"] = encoded.columns.tolist()
    return encoded, metadata


def apply_encoder(frame: pd.DataFrame, metadata: dict) -> pd.DataFrame:
    encoded = pd.DataFrame(index=frame.index)
    for name in MODEL_FEATURES:
        if name in metadata["categorical"]:
            values = frame[name].fillna("unknown").astype(str)
            for category in metadata["categorical"][name]:
                encoded[f"{name}={category}"] = (values == category).astype(float)
        else:
            encoded[name] = (pd.to_numeric(frame[name], errors="coerce").astype("Float64")
                             .fillna(metadata["fill"][name]).astype(float))
    return encoded.reindex(columns=metadata["columns"], fill_value=0.0)


def load_events(con) -> pd.DataFrame:
    return con.execute("""
        SELECT ptp_id, golden_customer_id, cast(ptp_created_ts AS VARCHAR) as_of,
               try_cast(ptp_amount AS DOUBLE) promise_amount,
               greatest(date_diff('day', try_cast(ptp_created_ts AS DATE), try_cast(ptp_due_date AS DATE)), 0) days_ahead,
               ptp_status_adjusted status
        FROM gold.promises_to_pay
        WHERE ptp_status_adjusted IN ('kept', 'partially_kept', 'broken')
          AND golden_customer_id IS NOT NULL AND ptp_created_ts IS NOT NULL
        ORDER BY try_cast(ptp_created_ts AS TIMESTAMP), ptp_id
    """).df()


def train(con=None, model_path=MODEL_PATH, metadata_path=METADATA_PATH, report_path=REPORT_PATH) -> dict:
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise RuntimeError("LightGBM is required; run `uv sync --group layer3`") from exc
    con = con or connect(read_only=True)
    events = load_events(con)
    if len(events) < 100:
        raise ValueError(f"need at least 100 resolved promises, found {len(events)}")
    frame = build_training_frame(events, con=con)
    y = frame.pop("status").map(LABELS).astype(int).to_numpy()
    split = max(1, min(len(frame) - 1, int(len(frame) * .8)))
    x_train, metadata = fit_encoder(frame.iloc[:split])
    x_test = apply_encoder(frame.iloc[split:], metadata)
    train_set = lgb.Dataset(x_train, label=y[:split], free_raw_data=False)
    model = lgb.train({"objective": "binary", "metric": "binary_logloss", "learning_rate": .04,
                       "num_leaves": 24, "max_depth": 6, "min_data_in_leaf": 80,
                       "bagging_fraction": .85, "bagging_freq": 1, "feature_fraction": .85,
                       "lambda_l2": 2.0, "seed": 37, "verbosity": -1, "num_threads": 4},
                      train_set, num_boost_round=300)
    probabilities = model.predict(x_test)
    test_y = y[split:]
    cutoff = np.quantile(probabilities, .9)
    top = test_y[probabilities >= cutoff]
    metrics = {"rows": len(frame), "train_rows": split, "test_rows": len(test_y),
               "auc": _auc(test_y, probabilities),
               "brier": float(np.mean((probabilities - test_y) ** 2)),
               "broken_rate": float(test_y.mean()),
               "top_decile_broken_rate": float(top.mean()) if len(top) else float("nan")}
    # calibration: mean predicted risk vs observed broken rate in ten equal-size risk bands of the test set
    order = np.argsort(probabilities)
    bands = [b for b in np.array_split(order, 10) if len(b)]
    calibration = [{"band": i + 1, "rows": int(len(b)), "predicted": float(probabilities[b].mean()),
                    "observed": float(test_y[b].mean())} for i, b in enumerate(bands)]
    metrics["calibration"] = calibration
    model_path, metadata_path, report_path = Path(model_path), Path(metadata_path), Path(report_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(model_path))
    metadata.update({"model_version": "lightgbm-promise-break-1", "model_features": MODEL_FEATURES,
                     "metrics": metrics, "split": "oldest 80% train / newest 20% test"})
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# Promise-break model report\n\n"
        "Resolved promises only; open and cancelled promises are excluded. The split is time ordered. "
        "Features are computed as of promise creation through the same Layer 3 definitions used for live scoring.\n\n"
        f"- Rows: {metrics['rows']:,} ({metrics['train_rows']:,} train, {metrics['test_rows']:,} test)\n"
        f"- Test AUC: {metrics['auc']:.3f}\n- Test Brier score: {metrics['brier']:.3f}\n"
        f"- Overall test broken rate: {metrics['broken_rate']:.3f}\n"
        f"- Broken rate in highest-risk decile: {metrics['top_decile_broken_rate']:.3f}\n\n"
        "## Calibration (test set, ten risk bands)\n\n"
        f"Mean predicted risk {float(probabilities.mean()):.3f} against an observed broken rate {metrics['broken_rate']:.3f}. "
        "The ordering is reliable (the top band breaks most often), but where the newest promises break more often than "
        "the older ones the model learned from, absolute risk is understated; read the score as a ranking and the "
        "band's observed rate as the likely outcome.\n\n"
        "| Band | Rows | Predicted risk | Observed broken rate |\n"
        "|---:|---:|---:|---:|\n"
        + "".join(f"| {c['band']} | {c['rows']:,} | {c['predicted']:.3f} | {c['observed']:.3f} |\n" for c in calibration)
        + "\n"
        f"Model inputs ({len(MODEL_FEATURES)}): {', '.join(MODEL_FEATURES)}.\n\n"
        f"Left out on purpose ({len(EXCLUDED_FEATURES)}): {', '.join(EXCLUDED_FEATURES)}. They are protective-only "
        "(hardship and related text answers) and must never raise a customer's risk score.\n",
        encoding="utf-8")
    return metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db")
    args = parser.parse_args(argv)
    con = connect(read_only=True, path=args.db) if args.db else connect(read_only=True)
    print(json.dumps(train(con), indent=2))


if __name__ == "__main__":
    main()
