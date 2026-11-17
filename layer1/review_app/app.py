"""Identity review queue (architecture §7 Tier 3).

The reviewer sees the two records of one pair side by side, each field's comparison and weight, and an optional
rule-based explanation built from comparison results only. Decisions (link, no link, unsure) are saved with the reviewer's
name in identity.review_decisions and act as must-link / cannot-link constraints from the next identity run.
The queue is ordered: open collections case first, then exposure, then uncertainty.

Run:  uv run streamlit run layer1/review_app/app.py -- --db lake/dev.duckdb
Close the app before running the pipeline: DuckDB allows one writer at a time.
"""
import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
from layer1.identity import explain  # noqa: E402
from layer1.identity.comparisons import LEVEL_LABELS  # noqa: E402

FIELDS = [("first_name", "First name"), ("middle_initial", "Middle initial"), ("last_name", "Last name"),
          ("name_suffix", "Suffix"), ("dob", "Date of birth"), ("phone_e164", "Phone"),
          ("phone_first6", "Phone (first 6)"), ("phone_last2", "Phone (last 2)"), ("postal_code", "Postal code"),
          ("email", "Email")]
COMPARISON_OF = {"first_name": "first_name", "last_name": "last_name", "dob": "dob", "phone_e164": "phone",
                 "postal_code": "postal_code", "email": "email"}


def db_path():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="lake/dev.duckdb")
    args, _ = ap.parse_known_args()
    return args.db


@st.cache_resource
def connection(path):
    return duckdb.connect(path)


def load_queue(con, only_open):
    return con.execute(f"""
        SELECT q.priority, q.key_l, q.key_r, q.match_probability, q.constraint_flags, q.has_open_case, q.exposure,
               q.system_l, q.system_r
        FROM identity.review_queue q
        WHERE NOT EXISTS (SELECT 1 FROM identity.review_decisions d
                          WHERE d.key_l = q.key_l AND d.key_r = q.key_r AND d.decision IN ('link', 'no_link'))
          {"AND q.has_open_case" if only_open else ""}
        ORDER BY q.priority LIMIT 200""").df()


def load_pair(con, key_l, key_r):
    pair = con.execute("SELECT * FROM identity.match_pairs WHERE key_l = ? AND key_r = ?", [key_l, key_r]).df()
    recs = con.execute("SELECT * FROM identity.splink_input WHERE unique_id IN (?, ?)", [key_l, key_r]).df()
    return pair.iloc[0].to_dict(), recs.set_index("unique_id")


def main():
    st.set_page_config(page_title="Identity review", layout="wide")
    con = connection(db_path())
    st.title("Identity review queue")
    st.caption("Wrong merges can disclose one person's debt to another. When unsure, keep records apart.")

    with st.sidebar:
        reviewer = st.text_input("Your name (logged with every decision)")
        only_open = st.checkbox("Only pairs with an open collections case", value=True)
        decided = con.execute("SELECT decision, count(*) FROM identity.review_decisions GROUP BY 1").fetchall()
        st.write("Decisions so far:", dict(decided) or "none")

    queue = load_queue(con, only_open)
    if queue.empty:
        st.success("Nothing waiting for review.")
        return
    st.write(f"{len(queue)} pairs shown (top of the queue).")
    labels = [f"#{r.priority}  {r.system_l} ↔ {r.system_r}  p={r.match_probability if pd.notna(r.match_probability) else 'n/a'}"
              f"{'  [open case]' if r.has_open_case else ''}" for r in queue.itertuples()]
    choice = st.selectbox("Pair", range(len(queue)), format_func=lambda i: labels[i])
    row = queue.iloc[choice]
    pair, recs = load_pair(con, row.key_l, row.key_r)

    left, right = recs.loc[row.key_l], recs.loc[row.key_r]
    table = []
    for col, name in FIELDS:
        comp = COMPARISON_OF.get(col)
        gamma = pair.get(f"gamma_{comp}") if comp else None
        bf = pair.get(f"bf_{comp}") if comp else None
        table.append({
            "Field": name, row.key_l: left.get(col), row.key_r: right.get(col),
            "Comparison": LEVEL_LABELS[comp].get(int(gamma), "") if comp and pd.notna(gamma) else "",
            "Weight": round(math.log2(bf), 1) if bf is not None and pd.notna(bf) and bf > 0 else None,
        })
    st.dataframe(pd.DataFrame(table).astype(str), hide_index=True, width="stretch")
    c1, c2, c3 = st.columns(3)
    c1.metric("Match probability", f"{pair['match_probability']:.3f}" if pd.notna(pair.get("match_probability")) else "n/a")
    c2.metric("Constraint flags", pair.get("constraint_flags") or "none")
    c3.metric("Exposure (open case)", f"{row.exposure:,.0f}")

    if st.button("Explain this pair (comparison results only)"):
        text, source = explain.explain(pair)
        st.info(text)
        st.caption("Rule-based suggestion, not a decision.")

    reason = st.text_input("Reason (optional)")
    b1, b2, b3 = st.columns(3)
    for col, decision, label in [(b1, "link", "Same person: link"), (b2, "no_link", "Different people: keep apart"),
                                 (b3, "unsure", "Unsure")]:
        if col.button(label, disabled=not reviewer.strip()):
            con.execute("INSERT INTO identity.review_decisions VALUES (?, ?, ?, ?, ?, ?)",
                        [row.key_l, row.key_r, decision, reviewer.strip(), reason or None,
                         datetime.now(timezone.utc).replace(tzinfo=None)])
            st.success(f"Saved: {decision}. It applies from the next identity run.")
            st.rerun()
    if not reviewer.strip():
        st.caption("Enter your name in the sidebar to record decisions.")


if __name__ == "__main__":
    main()
