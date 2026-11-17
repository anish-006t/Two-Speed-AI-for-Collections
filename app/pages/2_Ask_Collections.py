"""Ask Collections (Layer 2, owner: Het): ask a question, get an answer with its working.

    uv run streamlit run app/pages/2_Ask_Collections.py        # standalone, or as a page of app/Home.py

1. Show what it understood: the question becomes "understood as" tokens; Edit changes any of them and re-runs (the
   SQL is rebuilt by code, no model involved).
2. Show the working: every answer carries its SQL (or sources) and the data products it read.
3. Keep the analysis going: an optional AI insight (checked: only numbers that are in the result) and follow-up
   questions that the rules have already checked they can answer.
"""
from __future__ import annotations

import html
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from layer2 import adapters, present  # noqa: E402
from layer2.ask import ask  # noqa: E402
from layer2.checker import STATUS, classify  # noqa: E402
from layer2.parse import Period, parse  # noqa: E402
from layer2.semantic import QueryPlan, catalog, menus, run_plan  # noqa: E402

EXAMPLES = [
    "Which customers are most likely to break a promise to pay this week?",
    "What was the promise-kept rate for promises due in August 2026 by queue?",
    "Which 5 queues have the highest total overdue?",
    "What was the right-party contact rate on outbound calls in September 2026, by local hour of day?",
    "How many call attempts can we make on a mid-stage account in a week?",
    "Summarise the most recent recorded call on case CS-2026-614370",
    "What has the customer said about money problems in the notes for case CS-2026-905544?",
    "Le client a-t-il des difficultés financières selon les notes du dossier CS-2026-583038 ?",
    "List the gender and marital status of customers who broke a promise last month.",
]
CHIP = {"metric": "#2563eb", "group": "#0d9488", "filter": "#b45309", "period": "#15803d", "sort": "#7c3aed",
        "limit": "#c2410c"}

st.set_page_config(page_title="Ask Collections", layout="wide")
from app import shell as _shell  # noqa: E402
_shell.apply_style()  # the design layer, also when this page is opened directly
st.markdown("""
<style>
.ca-bar {background:#0f172a; color:#f8fafc; padding:14px 20px; border-radius:8px; display:flex; gap:24px;
         align-items:baseline; margin-bottom:12px}
.ca-bar b {font-size:1.25rem} .ca-bar b span {color:#f59e0b} .ca-bar small {color:#cbd5e1}
.ca-chips {display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin:2px 0 10px}
.ca-chips .lbl {color:#94a3b8; font-size:.85rem; margin-right:4px}
.ca-chip {border:1px solid; border-radius:999px; padding:2px 10px; font-size:.82rem; font-weight:600}
.ca-head {font-size:1.15rem; font-weight:700; margin:6px 0 4px}
.ca-k {font-size:.72rem; letter-spacing:.08em; text-transform:uppercase; color:#94a3b8; font-weight:700}
</style>
<div class="ca-bar"><b>Collections <span>Ask</span></b><small>Search your collections data in plain English</small></div>
""", unsafe_allow_html=True)

role = "agent"   # one role with access to everything (4 Oct, owner decision); privacy rules still apply
SHELL_MODE = st.session_state.get("ai_mode")   # the team app's AI switch, when this page runs inside app/Home.py
with st.sidebar:
    as_of = st.date_input("As of", date(2026, 9, 28), help="'Today' for relative periods (the data snapshot date).")
    use_model = st.toggle("Use AI (LLM)", value=SHELL_MODE != "rules", key="use_ai",
                          help="Off: rules only, no API calls. On: the LLM extends or writes SQL for what the rules "
                               "cannot express, writes policy and notes answers with checked citations, and offers "
                               "an insight. Numbers always come from SQL run on the data.")
    if use_model and SHELL_MODE == "cached":
        st.caption("Team AI setting is *Cached model answers only*: AI answers come from the cache, no new calls.")
    elif use_model and SHELL_MODE == "rules":
        st.caption("Team AI setting is *Offline rules*: switch it to Model or Cached to use AI answers.")
    st.caption(f"AI: {adapters.llm_status()}")

if "q_input" not in st.session_state:
    st.session_state.q_input = EXAMPLES[0]


def _queue(text: str) -> None:
    st.session_state.q_input = text
    st.session_state.run = True


def _from_pills(key: str) -> None:
    if st.session_state.get(key):
        _queue(st.session_state[key])


with st.form("ask_form", border=False):
    c1, c2 = st.columns([9, 1])
    question = c1.text_input("Question", key="q_input", label_visibility="collapsed",
                             placeholder="Which customers are most likely to break a promise to pay this week?")
    asked = c2.form_submit_button("Ask", type="primary", width="stretch")
st.pills("Try", EXAMPLES, key="ex_pill", on_change=_from_pills, args=("ex_pill",), label_visibility="collapsed")

with st.expander("Import questions (CSV)", icon=":material/upload_file:",
                 expanded=bool(st.session_state.get("batch_answers"))):
    from layer2 import batch
    st.caption(f"A CSV with a `question_text` (or `question`) column; `question_id` and `as_of_date` are optional, "
               f"up to {batch.MAX_QUESTIONS} questions. Each one is answered like the box above, with the same rules, "
               "refusals and AI switch. Download the answers in the benchmark template format.")
    upload = st.file_uploader("Questions CSV", type=["csv"], key="batch_file", label_visibility="collapsed")
    if upload is not None:
        try:
            questions = batch.read_questions(upload.getvalue())
        except (batch.BadQuestionsFile, UnicodeDecodeError, ValueError) as e:
            st.error(str(e) if isinstance(e, batch.BadQuestionsFile) else "Could not read the file as a UTF-8 CSV.")
            questions = []
        if questions:
            st.write(f"**{len(questions)} questions** in `{upload.name}`"
                     + (" · AI on: new questions use the model's daily quota" if use_model else " · rules only"))
            if st.button("Answer all", type="primary", icon=":material/play_arrow:"):
                bar = st.progress(0.0, text="Answering…")
                answers = batch.answer_all(questions, ask, role=role, as_of=as_of.isoformat(), use_model=use_model,
                                           progress=lambda i, n: bar.progress(i / n, text=f"Answered {i} of {n}"))
                bar.empty()
                st.session_state.batch_answers = {"file": upload.name, "answers": answers}
    done = st.session_state.get("batch_answers")
    if done:
        import pandas as pd
        rows = done["answers"]
        st.write(f"**{len(rows)} answered** from `{done['file']}` · "
                 f"{sum(r['refused'] == 'true' for r in rows)} refused")
        st.dataframe(pd.DataFrame([{"question_id": r["question_id"], "question": r["question"], "route": r["route"],
                                    "refused": r["refused"], "answer": r["answer"].splitlines()[0][:160]}
                                   for r in rows]), width="stretch", hide_index=True)
        st.download_button("Download answers (CSV)", batch.to_template_csv(rows).encode("utf-8"),
                           file_name=done["file"].rsplit(".", 1)[0] + "_answers.csv", mime="text/csv",
                           icon=":material/download:")

if (asked or st.session_state.pop("run", False)) and st.session_state.q_input.strip():
    q = st.session_state.q_input.strip()
    with st.spinner("Answering…"):
        res = ask(q, role=role, as_of=as_of.isoformat(), use_model=use_model)
    res["question"] = q
    res["follow_ups"] = present.checked_follow_ups(res, q, role=role, as_of=as_of.isoformat())
    st.session_state.result = res


def show_table(rows) -> None:
    import pandas as pd
    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True)
    num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
    if 1 < len(df) <= 40 and num and df.columns[0] not in num and not df.columns[0].endswith("_id"):
        st.bar_chart(df.set_index(df.columns[0])[num[0]])


def editor(res: dict) -> None:
    """Edit what was understood and re-run; the SQL is rebuilt by code (no model)."""
    qp = res.get("plan")
    if isinstance(qp, QueryPlan):
        m = menus()
        measure = st.selectbox("Metric", list(m["measures"]), index=list(m["measures"]).index(qp.measure),
                               format_func=lambda k: m["measures"][k]["label"] + ("" if m["measures"][k]["certified"]
                                                                                  else " (aggregate)"))
        view = qp.view if measure == qp.measure else catalog()["measures"][measure]["view"]
        vmenu = m["views"][view]
        dims = st.multiselect("Break down by", vmenu["dimensions"],
                              default=[d for d in qp.dimensions if d in vmenu["dimensions"]])
        filters = st.multiselect("Filters", vmenu["filters"], default=[f for f in qp.filters if f in vmenu["filters"]])
        period = None
        if qp.period or vmenu["time"]:
            if st.checkbox("Limit to a period", value=qp.period is not None):
                d1, d2 = st.columns(2)
                start = d1.date_input("From", qp.period.start if qp.period else date(2026, 9, 1))
                end = d2.date_input("To", qp.period.end if qp.period else as_of)
                period = Period(start, end, f"{start} to {end}")
        if st.button("Re-run with these choices"):
            edited = QueryPlan(measure=measure, view=view, dimensions=dims, filters=filters,
                               value_filters=qp.value_filters if measure == qp.measure else {}, period=period,
                               time_key=qp.time_key if measure == qp.measure else catalog()["measures"][measure].get("time"),
                               variant=qp.variant if measure == qp.measure else None, within_days=qp.within_days,
                               order=qp.order, limit=qp.limit, engines={"edited": "by user"})
            out = run_plan(edited, parse(res["question"], as_of.isoformat()))
            out["question"], out["follow_ups"] = res["question"], res.get("follow_ups", [])
            st.session_state.result = out
            st.rerun()
    elif getattr(qp, "agg", None) is not None:
        from layer2.semantic import _answer_generic
        limit = st.number_input("How many rows (top N)", 1, 1000, value=qp.limit or 10) if qp.agg == "list" or qp.limit \
            else None
        order = st.radio("Sort", ["desc", "asc"], index=0 if (qp.order or "desc") == "desc" else 1, horizontal=True,
                         format_func=lambda o: "highest first" if o == "desc" else "lowest first")
        where = st.multiselect("Filters", qp.where, default=qp.where)
        if st.button("Re-run with these choices"):
            edited = replace(qp, limit=limit, order=order, where=list(where))
            con = adapters.connect(read_only=True)
            try:
                out = _answer_generic(edited, con) or {"answer": "No rows match.", "sql_or_sources": "",
                                                       "refused": False, "route": "metric_sql", "table": None}
            finally:
                con.close()
            out["question"], out["follow_ups"] = res["question"], res.get("follow_ups", [])
            st.session_state.result = out
            st.rerun()
    else:
        st.caption("Nothing to edit for this kind of answer; rephrase the question instead.")


res = st.session_state.get("result")
if res:
    route = res.get("route", "")
    status = classify(res)
    icon, label, meaning = STATUS[status]

    # 0. what it read (typing errors fixed by rules, or rewritten by the AI after a soft refusal)
    if res.get("read_as"):
        fixes = ", ".join(f"{a} → {b}" for a, b in res.get("corrections") or [])
        how = "AI rewrite" if (res.get("engines") or {}).get("normalize") == "llm rewrite" else "typos fixed"
        st.markdown('<div class="ca-chips"><span class="lbl">Read as:</span>'
                    f'<span class="ca-chip" style="color:#be185d;border-color:#be185d">✏️ {html.escape(res["read_as"])}'
                    f'</span><span class="lbl">({html.escape(how + (": " + fixes if fixes else ""))})</span></div>',
                    unsafe_allow_html=True)

    # 1. what it understood
    chips = present.chips(res)
    c1, c2 = st.columns([10, 1.2])
    c1.markdown('<div class="ca-chips"><span class="lbl">Understood as:</span>' + "".join(
        f'<span class="ca-chip" style="color:{CHIP.get(k, "#475569")};border-color:{CHIP.get(k, "#475569")}">'
        f'{html.escape(t)}</span>' for k, t in chips) + f'<span class="lbl">· {icon} {label}</span></div>',
        unsafe_allow_html=True)
    with c2.popover("✎ Edit", width="stretch"):
        editor(res)

    # the answer
    if res["refused"]:
        st.error(res["answer"])
    elif isinstance(res.get("table"), list) and res["table"] and route in ("metric_sql", "lookup"):
        head = present.headline(res)
        if head:
            st.markdown(f'<div class="ca-head">{html.escape(head)}</div>', unsafe_allow_html=True)
        elif route == "lookup" and (res.get("engines") or {}).get("story"):
            engine = res["engines"]["story"]
            if "+llm" in engine:                         # the cited AI answer, then the notes it cites
                written = res["answer"].split("\n\n")[0]
                st.success(written)
                st.caption("Written by the AI from these notes only; every fact cites a note ID (✓ in the table).")
                cited = set(__import__("re").findall(r"\bN-\d+\b", written))
                res["table"] = [{"cited": "✓" if r.get("note_id") in cited else "", **r} for r in res["table"]]
            else:
                st.info(res["answer"].split("\n")[0])
            st.caption(f"Search: {engine.replace('bm25', 'keywords').replace('+vectors', ' + meaning (vectors)').replace('+llm', ' → AI answer')}"
                       " · [protected] = masked under the team's protected-attributes rule")
        elif route == "lookup":
            st.success(res["answer"].split("\n\nNote: ")[0] if "," not in res["answer"].split("\n")[0]
                       else "Facts for the record asked about:")
        show_table(res["table"])
        note = res["answer"].split("\n\nNote: ", 1)
        if len(note) == 2:
            (st.warning if "Not applied" in note[1] else st.info)("Note: " + note[1])
    else:
        st.success(res["answer"])

    # 2. the working, 3. keep going
    left, right = st.columns([3, 2])
    with left:
        st.markdown('<div class="ca-k">How I got this</div>', unsafe_allow_html=True)
        src = res.get("sql_or_sources") or ""
        if "SELECT" in src.upper():
            st.code(src, language="sql")
        elif src:
            st.write(src)
        products = present.data_products(res)
        if products:
            st.caption("Data products: " + " · ".join(products))
        st.caption(f"{meaning} Lane: {route}. {res.get('reason', '')[:300]}")
    with right:
        st.markdown('<div class="ca-k">Next steps</div>', unsafe_allow_html=True)
        if isinstance(res.get("table"), list) and res["table"] and not res["refused"]:
            if "insight" in res:
                (st.info if res["insight"][0] else st.caption)(res["insight"][0] or res["insight"][1])
            elif use_model and adapters.llm_available() and st.button("✨ AI insight on this result"):
                res["insight"] = present.insight(res, res.get("question", ""))
                st.session_state.result = res
                st.rerun()
        if res.get("follow_ups"):
            st.pills("Follow-up questions", res["follow_ups"], key=f"fu_{hash(res.get('question'))}",
                     on_change=_from_pills, args=(f"fu_{hash(res.get('question'))}",))
        with st.expander("Why this answer"):
            st.json({k: v for k, v in res.items() if k in ("engines", "seconds", "route", "understood")})
