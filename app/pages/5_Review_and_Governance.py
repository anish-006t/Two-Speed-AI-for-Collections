"""Review and Governance (Layer 4, Madhur): the review queue (L4-6), summaries and the audit trail. One role since 4 Oct:
any agent reviews; every decision is audited with the reviewer's name."""
import json

import pandas as pd
import streamlit as st

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

if str(Path(__file__).resolve().parents[2]) not in sys.path:  # a page opened directly, before Home set the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import shell
from app.shell import apply_style, get_con, get_role, get_user
apply_style()  # the design layer, also when this page is opened directly
from layer4 import review
from shared import audit, db

st.title("Review and Governance")
st.caption("Model output under 0.75 confidence waits here for a reviewer (any agent). Hardship always needs the agent's "
           "confirmation, and nothing reaches a customer automatically. An override needs a reason; every decision "
           "is in the audit trail.")
con = get_con()
if con is None:
    st.warning("No database yet: build Layer 1 first.")
    st.stop()

s = review.stats(con)
summaries = s.get("summaries", {})
waiting = s.get("open", 0)
shell.stat_cards([
    ("Waiting for review", waiting, "AI output below 0.75 confidence", "alert",
     "Queue clear" if not waiting else "Needs a person", "ok" if not waiting else "warn"),
    ("Accepted", s.get("accepted", 0), "The AI was right; a reviewer confirmed it", "check"),
    ("Overridden", s.get("overridden", 0), "Corrected by a reviewer, with a reason", "edit"),
    ("Summaries approved", summaries.get("approved", 0),
     f"Drafts open {summaries.get('draft', 0)} · rejected {summaries.get('rejected', 0)}", "check"),
])

queue_tab, decided_tab, summaries_tab, audit_tab = st.tabs(["Review queue", "Decided", "Summaries", "Audit trail"])

with queue_tab:
    items = review.queue(con, "open")
    if not items:
        st.success("Nothing waiting for review.")
    for it in items:
        answer = json.loads(it["answer"]) if it["answer"] else None
        with st.container(border=True):
            st.markdown(f"**{it['subject']}** · {it['kind'].replace('_', ' ')} · case {it['case_id'] or '—'} · "
                        f"p={it['prob'] if it['prob'] is not None else 0:.2f}")
            st.write(f"Model answer: `{answer}` · {it['why']}")
            if it["evidence"]:
                st.caption(f"Evidence: “{it['evidence']}” · {it['source_ref']} · {it['model_version']}")
            a, b, c = st.columns([1, 2, 2])
            if a.button("Accept", key=f"acc:{it['item_id']}", icon=":material/check:"):
                review.decide(con, it["item_id"], "accept", get_user(), role=get_role())
                st.rerun()
            new = b.text_input("Correct value", key=f"val:{it['item_id']}", placeholder="Correct value",
                               label_visibility="collapsed")
            why = c.text_input("Reason", key=f"why:{it['item_id']}", placeholder="Reason (required to override)",
                               label_visibility="collapsed")
            if st.button("Override", key=f"ovr:{it['item_id']}", icon=":material/edit:"):
                try:
                    review.decide(con, it["item_id"], "override", get_user(), role=get_role(),
                                  final_value=new or None, reason=why)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

with decided_tab:
    done = [i for i in review.queue(con, "all") if i["status"] != "open"]
    if done:
        st.dataframe(pd.DataFrame(done)[["decided_at", "status", "subject", "case_id", "answer", "final_value",
                                         "reason", "decided_by", "prob"]], hide_index=True)
    else:
        st.caption("No decisions yet.")

with summaries_tab:
    if db.table_exists(con, "layer4", "summaries"):
        rows = con.execute("""SELECT created_at, case_id, status, coalesce(narrative, narrative_draft) AS summary,
                                     edits, approved_by, reject_reason FROM layer4.summaries
                              ORDER BY created_at DESC LIMIT 200""").df()
        # edits is NULL (NaN here) for drafts, rejections and summaries approved as drafted
        rows["edited"] = rows["edits"].map(lambda e: ", ".join(sorted(json.loads(e))) if isinstance(e, str) and e else "")
        st.dataframe(rows.drop(columns=["edits"]), hide_index=True)
    else:
        st.caption("No summaries yet.")

with audit_tab:
    f1, f2 = st.columns(2)
    case = f1.text_input("Filter by case ID").strip() or None
    action = f2.text_input("Filter by action (e.g. identity_verified, summary_approved)").strip()
    events = audit.recent(500, case_id=case, con=con)
    if action:
        events = [e for e in events if action in e["action"]]
    if events:
        st.dataframe(pd.DataFrame(events), hide_index=True)
    else:
        st.caption("No audit events match.")
