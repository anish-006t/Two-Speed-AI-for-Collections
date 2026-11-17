"""Layer 3 feature catalogue and point-in-time customer feature vector."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from layer3.features.defs import build_training_frame, get_features, registry_records  # noqa: E402
from shared.db import connect  # noqa: E402

st.set_page_config(page_title="Feature Store", layout="wide")
from app import shell as _shell  # noqa: E402
_shell.apply_style()  # the design layer, also when this page is opened directly
st.title("Feature Store")
st.caption("One governed definition for historical training rows and live scoring. Protected attributes are excluded.")

try:
    _con = connect(read_only=True)
except Exception:
    _con = None
catalogue = pd.DataFrame(registry_records(_con)).rename(columns={
    "modality": "Comes from", "prompt": "Prompt", "question_version": "Prompt version", "extractor": "Extractor",
    "accuracy_n": "Labels", "accuracy_measured_with": "Accuracy measured with",
    "name": "Feature", "entity": "Entity", "definition": "Definition (SQL)", "source": "Source",
    "refresh": "Refresh", "sensitivity": "Sensitivity", "ai_derived": "AI-derived", "accuracy": "Accuracy",
    "decision_input": "Decision input",
})
st.subheader("Catalogue")
st.dataframe(catalogue[["Feature", "Comes from", "Source", "Refresh", "Sensitivity", "AI-derived", "Accuracy",
                        "Decision input"]], width="stretch", hide_index=True)
st.markdown("**AI-derived features: prompt, version and accuracy against human labels**")
st.dataframe(catalogue[catalogue["AI-derived"]][["Feature", "Prompt", "Prompt version", "Extractor", "Accuracy",
                                                 "Labels", "Accuracy measured with"]], width="stretch", hide_index=True)
st.caption("Accuracy is measured against the public human labels (outputs/layer3_label_eval.md). Features that are "
           "not decision inputs (hardship and related text answers) are for protective handling only and never "
           "feed the promise-break risk score.")
with st.expander("Definitions and lineage"):
    st.dataframe(catalogue[["Feature", "Definition (SQL)", "Source"]], width="stretch", hide_index=True)

@st.cache_data(show_spinner="Computing feature summaries over a sample of customers...", ttl=3600)
def feature_summary(as_of: str, n: int = 300) -> pd.DataFrame:
    """Coverage and distribution of every feature over a fixed sample of customers with a case (aggregates only)."""
    con = connect(read_only=True)
    ids = [r[0] for r in con.execute("""SELECT DISTINCT golden_customer_id FROM gold.cases
                                         WHERE golden_customer_id IS NOT NULL ORDER BY hash(golden_customer_id)
                                         LIMIT ?""", [n]).fetchall()]
    frame = build_training_frame([{"golden_customer_id": g, "as_of": as_of} for g in ids], con=con)
    rows = []
    for name in [c for c in frame.columns if c not in ("golden_customer_id", "as_of")]:
        col = frame[name]
        numeric = pd.to_numeric(col, errors="coerce")
        rows.append({"Feature": name, "Customers": len(col), "Filled": f"{col.notna().mean():.0%}",
                     "Median": None if numeric.notna().sum() == 0 else round(float(numeric.median()), 3),
                     "Most common": None if col.dropna().empty else str(col.dropna().mode().iloc[0])})
    return pd.DataFrame(rows)


st.subheader("Feature summary (aggregates)")
st.caption("How filled and how distributed each feature is across 300 customers with a collections case, as of the "
           "date below. No individual values are shown here.")
summary_as_of = st.date_input("Summary as of", date(2026, 9, 28), key="summary_as_of")
if st.button("Compute summary"):
    try:
        st.dataframe(feature_summary(summary_as_of.isoformat()), width="stretch", hide_index=True)
    except (FileNotFoundError, RuntimeError) as exc:
        st.error(str(exc))

st.subheader("Point-in-time feature vector")
left, right = st.columns(2)
customer_id = left.text_input("Golden customer ID", placeholder="GC00012345")
as_of = right.date_input("As of", date(2026, 9, 28))
if st.button("Load features", type="primary", disabled=not customer_id.strip()):
    try:
        vector = get_features(customer_id.strip(), as_of.isoformat())
        st.dataframe(pd.DataFrame([{"Feature": key, "Value": value} for key, value in vector.items()]),
                     width="stretch", hide_index=True)
        st.info("Values use only records timestamped on or before the selected as-of date.")
    except (FileNotFoundError, RuntimeError) as exc:
        st.error(str(exc))
