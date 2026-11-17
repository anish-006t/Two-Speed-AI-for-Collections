"""Agent Assist (Layer 4, Madhur): one case, three steps. 1 Brief (L4-3), 2 Call: live replay (L4-4),
3 Summary: draft, edit, approve (L4-5). Low-confidence output goes to the review queue (L4-6)."""
import re
from datetime import date

import pandas as pd
import streamlit as st

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

if str(Path(__file__).resolve().parents[2]) not in sys.path:  # a page opened directly, before Home set the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.shell import apply_style, get_con, get_role, get_user
apply_style()  # the design layer, also when this page is opened directly
from layer4 import live_replay, postcall, precall, review
from shared import audit, db

con = get_con()
if con is None:
    st.warning("No database yet: build Layer 1 first.")
    st.stop()
if not (db.table_exists(con, "access_agent", "case_brief") and db.table_exists(con, "gold", "cases")):
    st.warning("Layer 1's agent view `access_agent.case_brief` isn't built in this database yet: run the gold dbt "
               "build (see README.md).")
    st.stop()

DEMO_CASE, DEMO_AGENT, DEMO_CALL = "CS-2026-971262", "AG-7816", "1010325"
ALL_AGENTS = "All agents"
STEPS = ["1 · Brief", "2 · Call", "3 · Summary"]
LEVEL_BOX = {"stop": st.error, "caution": st.warning, "info": st.info}
STATUS_ICON = {"eligible": ":material/check_circle:", "check": ":material/help:", "ineligible": ":material/block:"}
GATE_LABEL = {"suggest": ":green-badge[suggestion]", "confirm": ":orange-badge[confirm]",
              "review": ":red-badge[review queue]"}
ss = st.session_state


def money(v):
    return "" if v is None else f"${float(v):,.2f}"


def go(step: str, **values):
    """Button callback: move to a step (and set values) before the widgets render."""
    ss.update(values)
    ss["aa_step"] = step


def use_demo():
    ss["aa_case"] = DEMO_CASE           # the case-change reset below keeps the demo call
    ss["agent_id"] = DEMO_AGENT
    ss["case_pick"] = DEMO_CASE
    ss["case_typed"] = ""
    ss["replay_tid"] = DEMO_CALL
    ss["aa_step"] = STEPS[0]


def log_once(action, case_id, gid, **extra):
    key = f"logged:{action}:{case_id}"
    if not ss.get(key):
        audit.log({"action": action, "user": get_user(), "role": get_role(), "case_id": case_id,
                   "golden_customer_id": gid, **extra}, con=con)
        ss[key] = True


# ------------------------------------------------------------------------------------------------- case picker

def pick_case():
    with st.sidebar:
        st.divider()
        st.button("Demo case", icon=":material/play_circle:", on_click=use_demo, use_container_width=True,
                  help=f"Case {DEMO_CASE} (agent {DEMO_AGENT}) with call {DEMO_CALL}: hardship and a promise.")
        # one agent role with access to everything: "All agents" (default) lists every open case; picking an agent
        # ID narrows the list to that agent's cases (the Demo case button picks AG-7816)
        agents = [ALL_AGENTS] + precall.agents_with_open_cases(con)
        if ss.get("agent_id") not in agents:
            ss["agent_id"] = ALL_AGENTS
        chosen_agent = st.selectbox("Agent", agents, key="agent_id")
        agent_id = None if chosen_agent == ALL_AGENTS else chosen_agent
        with_calls = st.toggle("Only cases with a recorded call", value=True, key="with_calls",
                               help="Only 13% of open cases have a recorded call. Off: every open case, newest first "
                                    "(the newest ones rarely have a call yet).")
        has_calls = db.table_exists(con, "bronze", "call_transcripts")
        cases = open_cases_with_calls(agent_id) if (with_calls and has_calls) else \
            precall.list_open_cases(con, agent_id=agent_id, limit=200)
        options = [c["case_id"] for c in cases]
        if ss.get("case_pick") and ss["case_pick"] not in options:
            options = [ss["case_pick"]] + options
        typed = st.text_input("Case ID", placeholder="CS-2026-...", key="case_typed").strip()
        chosen = st.selectbox(f"Open cases ({len(cases)})", options, key="case_pick") if options else None
    return (typed or chosen), agent_id


def open_cases_with_calls(agent_id: str | None, limit: int = 200) -> list[dict]:
    """Open cases that have at least one recorded call, newest first (same columns as precall.list_open_cases)."""
    where, params = "", []
    if agent_id:
        where, params = "AND k.assigned_agent_id = ?", [agent_id]
    cur = con.execute(f"""SELECT k.case_id, k.assigned_agent_id, k.queue, k.case_status, k.primary_product
                          FROM gold.cases k WHERE k.outcome IS NULL {where}
                            AND EXISTS (SELECT 1 FROM bronze.call_transcripts t WHERE t.case_id = k.case_id)
                          ORDER BY k.case_open_date DESC LIMIT ?""", params + [limit])
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def calls_on_case(case_id: str) -> list[dict]:
    if not db.table_exists(con, "bronze", "call_transcripts"):
        return []
    rows = con.execute("""SELECT transcript_id, call_start_ts, duration_sec, direction FROM bronze.call_transcripts
                          WHERE case_id = ? ORDER BY call_start_ts DESC LIMIT 10""", [case_id]).fetchall()
    return [{"transcript_id": r[0], "when": str(r[1])[:16], "seconds": r[2], "direction": r[3]} for r in rows]


# ------------------------------------------------------------------------------------------------------ 1 Brief

def render_brief(case_id: str, agent_id: str | None):
    verified = ss.setdefault("verified", {})
    try:
        b = precall.build_brief(case_id, con=con, identity_verified=verified.get(case_id, False), agent_id=agent_id)
    except (LookupError, PermissionError) as e:
        st.error(str(e))
        return
    c, gid = b["customer"], b["customer"]["golden_customer_id"]
    log_once("brief_opened", case_id, gid)

    st.subheader(f"{c['name'] or 'Customer'} · {case_id}")
    m = st.columns(4)
    m[0].metric("Identity confidence", c["identity_confidence"] or "unknown")
    m[1].metric("Product", b["case"]["primary_product"] or "")
    m[2].metric("Case status", b["case"]["case_status"] or "")
    m[3].metric("Queue", b["case"]["queue"] or "")
    st.caption(f"Phone {c['phone_masked'] or '—'} · email {c['email_masked'] or '—'} · {c['province_code'] or ''} · "
               f"golden ID {gid} · data as of {b['as_of']}")

    if b["debt_locked"]:
        st.warning("**Verify identity before discussing the debt.** Identity confidence is "
                   f"{c['identity_confidence']}: confirm the customer's identity (for example date of birth and a "
                   "security question) before any balance, payment or promise is mentioned.",
                   icon=":material/lock:")
        if st.button("Identity verified", type="primary", icon=":material/verified_user:"):
            verified[case_id] = True
            audit.log({"action": "identity_verified", "user": get_user(), "role": get_role(), "case_id": case_id,
                       "golden_customer_id": gid, "reason": "agent confirmed identity before disclosure"}, con=con)
            st.rerun()

    st.markdown("#### Before you dial")
    if not b["flags"]:
        st.success("No restrictions on this case.")
    for f in b["flags"]:
        cite = f" · *{f['policy']}*" if f["policy"] else ""
        LEVEL_BOX[f["level"]](f"**{f['title']}.** {f['action']}{cite}")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Hardship and options")
        h = b["hardship"]
        st.write(f"Hardship: **{h['level'] or 'not recorded'}**"
                 + (f" ({h['reason'].replace('_', ' ')})" if h.get("reason") else "") + f" · source: {h['source']}")
        st.caption("Options come from the hardship programme rules in force on the data date, not from a model.")
        for o in b["options"]:
            with st.expander(f"{o['name']} ({o['program_id']}): {o['status']}", icon=STATUS_ICON[o["status"]]):
                st.write(o["description"])
                for w in o["why_not"]:
                    st.write(f"- Not eligible: {w}")
                for ch in o["checks"]:
                    st.write(f"- Check: {ch}")
                for n in o["notes"]:
                    st.write(f"- Note: {n}")
                st.caption(f"{o['policy']} · version {o['version']}")
        if not b["options"]:
            st.write("No hardship programme covers this product.")
    with right:
        st.markdown("#### Debt and promises")
        if b["debt_locked"]:
            st.info("Hidden until identity is verified.", icon=":material/lock:")
        else:
            d = b["debt"]
            k = d["case"]
            n = st.columns(3)
            n[0].metric("Overdue", money(k["total_overdue"]))
            n[1].metric("Days past due", k["current_dpd"] if k["current_dpd"] is not None else "missing")
            n[2].metric("Bucket", k["current_bucket"] or "")
            r = d["risk"]
            if r.get("available"):
                label = "What-if promise" if r["what_if"] else "Open promise"
                with st.container(border=True):
                    st.markdown(f"**Promise-break risk: {r['risk']:.0%}** · {label}: {money(r['amount'])} by "
                                f"{r['due_date']}")
                    for reason in r["reasons"]:
                        st.write(f"- {reason}")
                    if r.get("safer_date"):
                        st.write(f"Safer date to suggest: **{r['safer_date']}**")
                    st.caption(f"Model {r['model_version']}. A suggestion for the agent, never sent to the customer.")
            else:
                st.caption(r.get("reason", "No risk score."))
            if d["accounts"]:
                st.dataframe(pd.DataFrame(d["accounts"]), hide_index=True)
            if d["promises"]:
                st.caption("Last promises on this case")
                st.dataframe(pd.DataFrame(d["promises"]), hide_index=True)

    st.markdown("#### Calls on this case")
    calls = calls_on_case(case_id)
    if not calls:
        st.caption("No recorded calls on this case.")
    for cl in calls:
        a, b_, c_ = st.columns([3, 2, 1])
        a.write(f"Call `{cl['transcript_id']}` · {cl['when']} · {cl['direction']}")
        b_.caption(f"{cl['seconds']} s")
        c_.button("Replay", key=f"replay:{cl['transcript_id']}", icon=":material/play_arrow:", on_click=go,
                  args=(STEPS[1],), kwargs={"replay_tid": cl["transcript_id"]})

    st.markdown("#### History")
    if b["summaries"]:
        st.caption("Approved call summaries")
        for s in b["summaries"]:
            st.write(f"- {str(s['approved_at'])[:16]} ({s['case_id']}, approved by {s['approved_by']}): "
                     f"{s['narrative']}")
    else:
        st.caption("No approved call summaries yet.")
    if b["contacts"]:
        st.caption("Last contacts on this case")
        st.dataframe(pd.DataFrame(b["contacts"]), hide_index=True)


# ------------------------------------------------------------------------------------------------------- 2 Call

STOP_RULES = {"R-INSOLVENCY", "R-CEASE", "R-THIRD-PARTY", "R-AGENT-THREAT"}


def eligible_options(state: dict) -> list[str]:
    """Hardship programme names the brief allows for this call's case (computed once per call, rules only)."""
    if "options" not in state:
        try:
            b = precall.build_brief(state["call"]["case_id"], con=con)
            opts = b.get("options", [])
            ok = [o["name"] for o in opts if o["status"] == "eligible"]
            state["options"] = (ok or [o["name"] for o in opts if o["status"] == "check"])[:3]
        except Exception:  # noqa: BLE001 - the coach still works without the options
            state["options"] = []
    return state["options"]


def coach(state: dict) -> tuple[str, str, list[str]]:
    """What the agent should say or do next, from what the replay has seen so far: (level, headline, still to do).
    Uses only the flags, the promise check and the QA checklist already computed: no extra model call."""
    steps, call = state["steps"], state["call"]
    if not steps:
        return "info", "Open the call: say your name, that you are calling from Maple Bank, and that the call is " \
                       "recorded.", []
    qa = steps[-1]["qa"]
    prompts = [p for s_ in steps for p in s_["prompts"]]
    rules = {p["rule"] for p in prompts}
    promises = [s_["promise"] for s_ in steps if s_["promise"]]
    progress = len(steps) / max(len(call["turns"]), 1)
    todo = [item["name"] for item in qa.values() if item["status"] == "pending"
            and not (item["name"].startswith("Hardship") and "R-HARDSHIP" not in rules)
            and not (item["name"].startswith("Promise") and not promises)]
    stop = next((p for p in reversed(prompts) if p["rule"] in STOP_RULES), None)
    if stop:
        return "stop", f"{stop['title']}: {stop['prompt']}", todo
    if qa["identity_verified_before_disclosure"]["status"] == "failed":
        return "stop", "The debt was mentioned before identity was verified. Verify identity now and note it.", todo
    if qa["recording_notice_given"]["status"] == "pending":
        return "info", "Tell the customer the call is recorded.", todo
    if qa["agent_identified_self_and_purpose"]["status"] == "pending":
        return "info", "Say your name and that you are calling from Maple Bank about the account.", todo
    if qa["identity_verified_before_disclosure"]["status"] == "pending":
        return "caution", "Verify identity (date of birth or postal code) before you mention any amount owed.", todo
    if "R-HARDSHIP" in rules and qa["hardship_handled_per_policy"]["status"] == "pending":
        opts = eligible_options(state)
        return "caution", ("Hardship was mentioned: acknowledge it and offer an option before asking for payment"
                           + (f" ({', '.join(opts)})." if opts else ".")), todo
    if promises:
        p = promises[-1]
        if p.get("status") == "risky" and p.get("suggestion"):
            tip = re.sub(r"^Risk \d+%\.\s*", "", p["suggestion"])
            return "caution", f"This promise is risky ({p['risk']:.0%} break risk). {tip}", todo
        if qa["ptp_details_confirmed_back"]["status"] == "pending":
            what = " on ".join(x for x in (money(p.get("amount")), p.get("due_date")) if x)
            return "info", f"Read the promise back to the customer{': ' + what if what else ''}, and confirm how " \
                           "they will pay.", todo
    elif progress > 0.35:
        return "info", "Ask what the customer can pay and by when; agree an amount and a date.", todo
    if progress > 0.8 and qa["respectful_close_and_next_steps"]["status"] == "pending":
        return "info", "Close: summarise what was agreed and the next steps, and thank the customer.", todo
    return "info", "Listen and acknowledge; no action needed on this turn.", todo


def render_replay(case_id: str | None):
    if not (db.table_exists(con, "bronze", "call_transcripts")
            and db.table_exists(con, "silver", "transcript_turns_redacted")):
        st.info("Needs Layer 1's transcripts (`bronze.call_transcripts`, `silver.transcript_turns_redacted`).")
        return
    options = [c["transcript_id"] for c in calls_on_case(case_id)] if case_id else []
    demo = live_replay.demo_calls(con)
    own = set(options)
    labels = {c["transcript_id"]: f"practice call from another case · hardship at "
                                  f"{c['hardship_first_mention_sec']:.0f}s · promise ${c['label_ptp_amount']}"
              for c in demo if c["transcript_id"] not in own}
    for t in labels:
        if t not in options:
            options.append(t)
    if ss.get("replay_tid") and ss["replay_tid"] not in options:
        options.insert(0, ss["replay_tid"])
    if not options:
        st.info("No calls to replay.")
        return
    if case_id and not own:
        st.warning("This case has no recorded call. The list below holds labelled practice calls from other cases; "
                   "what you see in them is not about this customer.", icon=":material/info:")
    if ss.get("replay_tid") not in options:
        ss["replay_tid"] = options[0]
    tid = st.selectbox("Call", options, key="replay_tid",
                       format_func=lambda t: f"{t} · {labels[t]}" if t in labels else f"{t} · call on this case")
    state = ss.get("replay")
    if not state or state["tid"] != tid:
        try:
            call = live_replay.load_call(tid, con=con)
        except LookupError as e:
            st.error(str(e))
            return
        state = {"tid": tid, "call": call, "gen": live_replay.iter_replay(call), "steps": [], "done": False}
        ss["replay"] = state
    call = state["call"]

    def advance(n):
        for _ in range(n):
            try:
                state["steps"].append(next(state["gen"]))
            except StopIteration:
                break
        state["done"] = len(state["steps"]) >= len(call["turns"])

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    if c1.button("Next turn", icon=":material/skip_next:", disabled=state["done"], type="primary"):
        with st.spinner("Listening to the turn..."):
            advance(1)
    if c2.button("Play to end", icon=":material/fast_forward:", disabled=state["done"]):
        with st.spinner("Replaying the call (a turn the model hasn't seen takes a few seconds)..."):
            advance(len(call["turns"]))
    if c3.button("Restart", icon=":material/replay:"):
        ss.pop("replay")
        st.rerun()
    c4.progress(len(state["steps"]) / max(len(call["turns"]), 1),
                text=f"Turn {len(state['steps'])} of {len(call['turns'])} · case {call['case_id']} · "
                     f"{call['call_date']}")

    label = next((c for c in demo if c["transcript_id"] == tid), None)
    left, right = st.columns([3, 2])
    with left:
        if not state["steps"]:
            st.caption("Press Next turn to start the call.")
        for step in state["steps"]:
            t = step["turn"]
            with st.chat_message("user" if t["speaker"] == "customer" else "assistant",
                                 avatar=":material/person:" if t["speaker"] == "customer" else ":material/headset_mic:"):
                st.markdown(f"**{t['speaker'].capitalize()}** · {t['start_sec']:.1f}s  \n{t['text']}")
                for p in step["prompts"]:
                    LEVEL_BOX[p["level"]](f"**{p['title']}**: {p['prompt']}", icon=":material/bolt:")
    with right:
        level, say, todo = coach(state)
        with st.container(border=True):
            st.markdown("#### :material/lightbulb: Suggestions")
            LEVEL_BOX[level](f"**Next:** {say}", icon=":material/lightbulb:")
            if todo:
                st.caption("Still to do on this call: " + " · ".join(todo))
        st.markdown("#### Prompts")
        prompts = [p for s_ in state["steps"] for p in s_["prompts"]]
        if not prompts:
            st.caption("Nothing flagged yet.")
        for p in reversed(prompts):
            LEVEL_BOX[p["level"]](f"**{p['title']}** {GATE_LABEL[p['gate']]}  \n{p['prompt']}  \n"
                                  f"*Customer said:* “{p['evidence']}” at {p['said_at_sec']:.1f}s  \n"
                                  f"*{p['rule']} · {p['policy']} · p={p['prob']:.2f} · {p['model_version']}*")
            key = f"{tid}:{p['rule']}"
            decided = ss.setdefault("prompt_decisions", {}).get(key)
            if decided:
                st.caption(f"You {decided} this prompt.")
            elif p["gate"] != "suggest":
                x, y = st.columns(2)
                for col, label_, verb in ((x, "Confirm", "confirmed"), (y, "Dismiss", "dismissed")):
                    if col.button(label_, key=f"{key}:{verb}"):
                        audit.log({"action": f"prompt_{verb}", "user": get_user(), "role": get_role(),
                                   "case_id": call["case_id"], "golden_customer_id": call["golden_customer_id"],
                                   "input_ref": f"transcript:{tid}", "prob": p["prob"],
                                   "output": {"rule": p["rule"], "answer": p["answer"]},
                                   "reason": f"agent {verb} the live prompt"}, con=con)
                        ss["prompt_decisions"][key] = verb
                        st.rerun()
        promises = [s_["promise"] for s_ in state["steps"] if s_["promise"]]
        if promises:
            p = promises[-1]
            st.markdown("#### Promise check")
            if p["status"] in ("risky", "ok"):
                (st.warning if p["status"] == "risky" else st.success)(
                    f"**{money(p['amount'])} by {p['due_date']}** · break risk **{p['risk']:.0%}** "
                    f"{GATE_LABEL[p['gate']]}  \n" + "  \n".join(f"- {r}" for r in p["reasons"])
                    + (f"  \n**{p['suggestion']}**" if p.get("suggestion") else ""))
                st.caption(f"Model {p['model_version']}. A suggestion for the agent, never sent to the customer.")
            else:
                st.info(f"Promise heard ({money(p['amount']) or 'amount ?'} by {p['due_date'] or 'date ?'}); "
                        f"missing: {', '.join(p['missing'])}. Confirm the details with the customer.")
        st.markdown("#### QA checklist")
        qa = state["steps"][-1]["qa"] if state["steps"] else live_replay.new_qa_state()
        icon = {"done": ":material/check_box:", "failed": ":material/cancel:",
                "pending": ":material/check_box_outline_blank:"}
        for item in qa.values():
            st.markdown(f"{icon[item['status']]} {item['name']}"
                        + (f" · turn {item['turn']}" if item["turn"] else ""))
        if state["done"] and not state.get("queued"):
            state["queued"] = True
            n = review.queue_from_replay(con, call, state["steps"])
            if n:
                st.toast(f"{n} low-confidence item(s) sent to the review queue.")
        if label and state["done"]:
            flagged = live_replay.time_to_flag({"steps": state["steps"]})
            truth = label["hardship_first_mention_sec"]
            st.markdown("#### Against the label")
            st.write(f"Hardship first said at {truth:.1f}s; flagged at "
                     + (f"{flagged:.1f}s ({flagged - truth:+.1f}s)" if flagged is not None else "never"))
        if state["done"]:
            st.button("Draft the summary", type="primary", icon=":material/edit_note:", on_click=go,
                      args=(STEPS[2],), kwargs={"summary_tid": tid})


# ---------------------------------------------------------------------------------------------------- 3 Summary

def render_summary():
    if not db.table_exists(con, "silver", "transcript_turns_redacted"):
        st.info("Needs Layer 1's redacted transcripts.")
        return
    replay_state = ss.get("replay")
    if not ss.get("summary_tid") and replay_state:
        ss["summary_tid"] = replay_state["tid"]
    tid = st.text_input("Call (transcript ID)", key="summary_tid",
                        help="Defaults to the call you replayed.").strip()
    if not tid:
        st.info("Replay a call first, or type a transcript ID.")
        return
    drafts = ss.setdefault("summary_drafts", {})
    if tid not in drafts:
        if st.button("Draft summary", type="primary", icon=":material/edit_note:"):
            with st.spinner("Drafting from the call..."):
                result = None
                if replay_state and replay_state["tid"] == tid and replay_state["done"]:
                    result = {"steps": replay_state["steps"]}
                try:
                    d = postcall.draft(tid, con=con, replay_result=result)
                except LookupError as e:
                    st.error(str(e))
                    return
                d["summary_id"] = postcall.save_draft(d, get_user(), con=con)
                queued = review.queue_from_summary(con, d["summary_id"], d)
                if queued:
                    st.toast(f"{queued} low-confidence field(s) sent to the review queue.")
                drafts[tid] = d
                st.rerun()
        if ss.get("last_approved"):
            st.success(ss["last_approved"])
            st.button("Back to the brief", icon=":material/arrow_back:", on_click=go, args=(STEPS[0],))
        return
    d = drafts[tid]
    fv = d["fields"]
    st.caption(f"Case {d['case_id']} · draft {d['summary_id'][:8]} · {d['model_version']}. Every field below is "
               "already filled in from the call: you don't type anything. Check the fields marked confirm or review, "
               "change one only if it is wrong (that needs a reason), then Approve. An empty field means it was not "
               "heard on the call. Nothing is saved to the customer record until you approve.")

    def hint(k):
        x = fv.get(k, {})
        prob = "" if x.get("prob") is None else f"p={x['prob']:.2f} · "
        gate = x.get("gate")
        if gate == "review" and x.get("value") in (None, False, "none"):   # empty fields are never queued (review.py)
            gate = "confirm"
        return f"{GATE_LABEL.get(gate, '')} {prob}{x.get('source', '')}" + (
            f" · “{x['evidence'][:80]}”" if x.get("evidence") else "")

    def opt(options, value):
        return options.index(value) if value in options else 0

    c1, c2 = st.columns(2)
    with c1:
        levels = ["none", "possible", "clear"]
        hardship = st.selectbox("Hardship", levels, index=opt(levels, fv["hardship"]["value"] or "none"),
                                key=f"{tid}:hardship")
        st.caption(hint("hardship"))
        reasons = [""] + list(postcall.REASON_PHRASE)
        reason = st.selectbox("Hardship reason", reasons, index=opt(reasons, fv["hardship_reason"]["value"] or ""),
                              key=f"{tid}:reason", disabled=hardship == "none")
        heard = fv["promise_amount"]["value"]
        amount = st.number_input("Promised amount ($)", min_value=0.0, step=1.0, key=f"{tid}:amount",
                                 value=float(heard) if heard else None, placeholder="No promise heard on the call")
        due = st.text_input("Promised date (YYYY-MM-DD)", value=fv["promise_date"]["value"] or "", key=f"{tid}:due",
                            placeholder="No promise date heard on the call")
        st.caption(hint("promise_amount"))
    with c2:
        dispute = st.checkbox("Dispute", value=bool(fv["dispute"]["value"]), key=f"{tid}:dispute")
        dtypes = [""] + list(postcall.DISPUTE_PHRASE)
        dtype = st.selectbox("Dispute type", dtypes, index=opt(dtypes, fv["dispute_type"]["value"] or ""),
                             key=f"{tid}:dtype", disabled=not dispute)
        complaint = st.checkbox("Complaint", value=bool(fv["complaint"]["value"]), key=f"{tid}:complaint")
        cease = st.checkbox("Asked to stop contact", value=bool(fv["cease_request"]["value"]), key=f"{tid}:cease")
        insolvency = st.checkbox("Mentions bankruptcy / consumer proposal",
                                 value=bool(fv["insolvency_mention"]["value"]), key=f"{tid}:insolvency")
    final = {"hardship": hardship, "hardship_reason": (reason or None) if hardship != "none" else None,
             "promise_amount": amount or None, "promise_date": due or None, "dispute": dispute,
             "dispute_type": (dtype or None) if dispute else None, "complaint": complaint, "cease_request": cease,
             "insolvency_mention": insolvency, "sentiment": fv["sentiment"]["value"],
             "third_party_present": fv["third_party_present"]["value"]}
    steps = list(postcall.NEXT_STEPS)
    suggested = postcall.next_step({k: {"value": v} for k, v in final.items()})
    final["next_step"] = st.selectbox("Next step", steps, index=steps.index(suggested), key=f"{tid}:step:{suggested}",
                                      help="Suggested by rules from the fields above; change it if needed.")
    if final["promise_date"]:
        try:
            final["promise_date"] = date.fromisoformat(final["promise_date"]).isoformat()
        except ValueError:
            st.error("Promised date must be YYYY-MM-DD.")
            return
    auto = postcall.narrative({k: {"value": v} for k, v in final.items()})
    text = st.text_area("Summary line", value=auto, key=f"{tid}:text:{auto}")
    changed = [k for k in final if k in fv and str(final[k]) != str(fv[k]["value"])]
    why = ""
    if changed:
        why = st.text_input(f"Why did you change {', '.join(changed)}? (required: it overrides the draft)",
                            key=f"{tid}:why")
    a, b, c = st.columns([1, 1, 2])
    if a.button("Approve", type="primary", icon=":material/check:"):
        try:
            edits = postcall.approve(d["summary_id"], final, text, get_user(), role=get_role(), con=con, reason=why)
        except ValueError as e:
            st.error(str(e))
        else:
            drafts.pop(tid)
            ss["last_approved"] = (f"Approved{' with edits to ' + ', '.join(sorted(edits)) if edits else ' as drafted'}"
                                   ". It now shows under History in this customer's brief.")
            st.rerun()
    reason_txt = c.text_input("Reason to reject", key=f"{tid}:reject_reason", label_visibility="collapsed",
                              placeholder="Reason to reject (required)")
    if b.button("Reject", icon=":material/close:"):
        if not reason_txt.strip():
            st.error("Give a reason to reject.")
        else:
            postcall.reject(d["summary_id"], reason_txt, get_user(), role=get_role(), con=con)
            drafts.pop(tid)
            st.info("Rejected; nothing was written to the customer record.")


# --------------------------------------------------------------------------------------------------------- page

st.title("Agent Assist")
case_id, agent_id = pick_case()
if "aa_case" in ss and ss["aa_case"] != case_id:   # switched case: forget the previous case's call and summary
    for k in ("replay", "replay_tid", "summary_tid", "last_approved"):
        ss.pop(k, None)
ss["aa_case"] = case_id
if ss.get("aa_step") not in STEPS:
    ss["aa_step"] = STEPS[0]
step = st.segmented_control("Step", STEPS, key="aa_step", label_visibility="collapsed")
st.caption("Before the call, during it, and after it. Every suggestion has a reason; you confirm, edit or dismiss it.")
if step == STEPS[0]:
    if case_id:
        render_brief(case_id, agent_id)
    else:
        st.info("Pick a case in the sidebar, or press Demo case.")
elif step == STEPS[1]:
    render_replay(case_id)
else:
    render_summary()
