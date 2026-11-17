"""Query checker (Layer 2): paste questions, see whether each can be processed and how.

    uv run streamlit run layer2/checker_app.py

One button checks every question with the rules (free, instant). With "Use AI" switched on, the same click then sends
only the questions the rules could not fully answer to the AI, so the free daily AI calls go where they are needed.
Nothing here changes data.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layer2 import adapters  # noqa: E402
from layer2.checker import NEEDS_AI, STATUS, check  # noqa: E402

SAMPLE = """What is the promise-kept rate for promises due in August 2026 by queue?
Which 5 queues have the highest total overdue?
How many open cases have more than 5 contact attempts?
What is the growth in dispute count from August to September 2026?
Ratio of SMS to calls by queue in September 2026
Can we call a customer on Sunday?
Summarise the most recent recorded call on case CS-2026-614370
Which FSAs have the worst broken promise rates?"""

st.set_page_config(page_title="Query checker", layout="wide")
st.title("Query checker")
st.caption("Can Layer 2 process these questions? Rules first (free); the AI only for what the rules can't fully answer.")

with st.sidebar:
    role = "agent"   # one role with access to everything (4 Oct, owner decision)
    st.caption("Checking as **Agent**")
    as_of = st.text_input("As of (snapshot date)", "2026-09-28")
    st.divider()
    st.markdown(f"**AI:** {adapters.llm_status()}")
    st.markdown(f"**AI calls left today:** {adapters._budget_left()}")
    st.caption("Repeated questions are answered from the cache and use no calls.")
    with st.expander("What the statuses mean"):
        for icon, label, meaning in STATUS.values():
            st.markdown(f"{icon} **{label}**: {meaning}")

text = st.text_area("Questions, one per line", SAMPLE, height=200)
upload = st.file_uploader("…or a CSV with a `question` or `question_text` column", type="csv")
questions = [q.strip() for q in text.splitlines() if q.strip()]
if upload is not None:
    import pandas as pd
    df_in = pd.read_csv(io.BytesIO(upload.getvalue()))
    col = next((c for c in ("question", "question_text", "q") if c in df_in.columns), None)
    if col is None:
        st.error("The CSV needs a column named question, question_text or q.")
    else:
        questions = [str(q).strip() for q in df_in[col].dropna() if str(q).strip()]

c1, c2 = st.columns([1, 2])
use_ai = c2.toggle("Use AI for questions the rules can't fully answer", value=False, key="use_ai",
                   help="Off: rules only, no AI calls. On: rules first, then the AI only for the questions the rules "
                        "answered partly or not at all (cached answers are free).")
if c1.button(f"Check {len(questions)} question(s)", type="primary", disabled=not questions, key="check"):
    bar, done = st.progress(0.0, text="Checking with rules…"), []
    for n, q in enumerate(questions, 1):
        done.append(check(q, use_model=False, role=role, as_of=as_of))
        bar.progress(n / len(questions), text=f"Rules: {n} / {len(questions)}")
    st.session_state.results = done
    pending = [i for i, r in enumerate(done) if r["status"] in NEEDS_AI]
    if use_ai and pending and not adapters.llm_available():
        st.info(f"The AI is not available right now ({adapters.llm_status()}); showing the rules results.")
    elif use_ai and pending:
        # free models take 5-25 s per call: show each result as it arrives so the page never looks frozen
        log = st.container()
        for n, i in enumerate(pending, 1):
            bar.progress((n - 1) / len(pending), text=f"AI {n} / {len(pending)}: {done[i]['question'][:80]}")
            done[i] = check(done[i]["question"], use_model=True, role=role, as_of=as_of)
            st.session_state.results = done           # keep what is done even if the run is stopped
            log.write(f"{done[i]['icon']} {done[i]['label']} ({done[i]['seconds']} s): {done[i]['question'][:90]}")
    bar.empty()
results = st.session_state.get("results", [])

if results:
    import pandas as pd
    counts = {k: sum(r["status"] == k for r in results) for k in STATUS}
    processed = sum(v for k, v in counts.items() if k not in ("cannot",))
    m = st.columns(5)
    m[0].metric("Processed", f"{processed} / {len(results)}")
    m[1].metric("✅ Rules", counts["rules"] + counts["rules_checked"] + counts["aggregate"])
    m[2].metric("🤖 AI", counts["ai_extended"] + counts["ai_written"] + counts["ai_text"])
    m[3].metric("⚠️ Partly / ❌ not", f"{counts['partial']} / {counts['cannot']}")
    m[4].metric("🛑 Refused", counts["refused"])
    st.caption(f"AI calls used in this check: {sum(r['ai_calls'] for r in results)}")

    table = pd.DataFrame([{"#": i + 1, "Status": f"{r['icon']} {r['label']}", "Lane": r["route"],
                           "Question": r["question"], "Read as (typos fixed)": r.get("read_as", ""),
                           "Seconds": r["seconds"], "AI calls": r["ai_calls"]}
                          for i, r in enumerate(results)])
    st.dataframe(table, width="stretch", hide_index=True)
    st.download_button("Download results (CSV)", pd.DataFrame(
        [{k: r[k] for k in ("question", "label", "route", "answer", "sql_or_sources", "reason", "seconds", "ai_calls")}
         for r in results]).to_csv(index=False), "layer2_query_check.csv", "text/csv")

    st.subheader("Details")
    for i, r in enumerate(results):
        with st.expander(f"{i + 1}. {r['icon']} {r['question'][:110]}"):
            st.markdown(f"**{r['label']}**: {STATUS[r['status']][2]}")
            if r.get("read_as"):
                st.markdown(f"✏️ **Read as:** {r['read_as']}")
            if r["understood"]:
                st.markdown(f"**Understood as:** {r['understood']}")
            if isinstance(r.get("table"), list) and r["table"] and r["route"] in ("metric_sql", "lookup"):
                st.dataframe(pd.DataFrame(r["table"]), width="stretch", hide_index=True)
                note = r["answer"].split("\n\nNote: ", 1)
                if len(note) == 2:
                    (st.warning if "Not applied" in note[1] else st.info)("Note: " + note[1])
            elif r["status"] == "refused":
                st.error(r["answer"])
            else:
                st.write(r["answer"])
            if r["sql_or_sources"]:
                if "SELECT" in r["sql_or_sources"].upper():
                    st.code(r["sql_or_sources"], language="sql")
                else:
                    st.caption("Sources: " + r["sql_or_sources"])
            st.caption(f"Lane: {r['route']} · engines: {r['engines']} · {r['seconds']} s · AI calls: {r['ai_calls']}")
