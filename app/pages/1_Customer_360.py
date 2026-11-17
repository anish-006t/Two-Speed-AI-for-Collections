"""Customer 360 (Layer 1, Anish; brief L1-8): one trusted record per customer.

One role (4 Oct, owner decision): the agent sees everything this page offers, with the privacy rules kept:
  - search a case ID (CS-...) or a golden customer ID (GC...);
  - customer header and tabs from the analyst-safe views (access_analyst.*: no names, contact details, DOB or postal
    code); linked records shown as counts per source, not source keys;
  - the case brief from access_agent.case_brief (masked phone and email, no DOB); debt details stay hidden while
    verify_before_disclosure is true, until the agent confirms identity (logged in audit.events).
Every view is logged (c360_viewed). Architecture §13, decisions D-39, D-45.
"""
import pandas as pd
import streamlit as st

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

if str(Path(__file__).resolve().parents[2]) not in sys.path:  # a page opened directly, before Home set the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.shell import apply_style, get_con, get_role, get_user
apply_style()  # the design layer, also when this page is opened directly
from shared import audit

st.title("Collections 360")
st.caption("One trusted record per customer, built from CRM, cards, loans, deposits and bureau. "
           "Confidence says how sure the match is; below high, verify identity before discussing any debt.")

con = get_con()
if con is None:
    st.warning("No database yet: build Layer 1 first (`uv run python -m layer1.build --target dev`).")
    st.stop()

role = get_role()
CONFIDENCE = {"high": ("High", "green"), "medium": ("Medium", "orange"), "low": ("Low", "red")}
LINEAGE = ["_source_system", "_source_file", "_batch_id", "_loaded_at", "_pipeline_run_id"]


def df(sql, params=()):
    return con.execute(sql, list(params)).df()


def resolve(query: str):
    """Return (golden_customer_id, case_id) for a case ID or a golden customer ID."""
    q = query.strip().upper()
    if not q:
        return None, None
    if q.startswith("CS-"):
        row = con.execute("SELECT golden_customer_id, case_id FROM gold.cases WHERE case_id = ?", [q]).fetchone()
        return (row[0], row[1]) if row else (None, None)
    row = con.execute("SELECT golden_customer_id FROM gold.c360_customer WHERE golden_customer_id = ?", [q]).fetchone()
    return (row[0], None) if row else (None, None)


def confidence_header(conf, verify, prob):
    label, colour = CONFIDENCE.get(conf, (conf, "gray"))
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Identity confidence**  \n:{colour}-badge[{label}]")
    c2.markdown(f"**Verify before disclosure**  \n{':red-badge[Yes]' if verify else ':green-badge[No]'}")
    c3.markdown(f"**Lowest link probability**  \n{'n/a' if prob is None or pd.isna(prob) else f'{prob:.3f}'}")
    if verify:
        st.warning("Identity is not confirmed to a high standard. Verify the customer's identity before discussing "
                   "any debt.", icon=":material/verified_user:")


def case_brief(case_id: str, gid: str) -> None:
    """The agent's brief for one case: masked contacts, restrictions, consent; debt behind the identity gate."""
    rows = df("SELECT * FROM access_agent.case_brief WHERE case_id = ?", [case_id])
    if rows.empty:
        st.info(f"No case brief for {case_id}.")
        return
    b = rows.iloc[0]
    name = f"{b.first_name or ''} {b.last_name or ''}".strip() or "Customer"
    st.subheader(f"Case {case_id} · {name}")
    c1, c2, c3 = st.columns(3)
    c1.write(f"Phone: {b.phone_masked or 'none'}  \nBest reachable: {b.best_reachable_phone_masked or 'none'}")
    c2.write(f"Email: {b.email_masked or 'none'}  \nLanguage: {b.preferred_language or 'n/a'}")
    c3.write(f"Call window: {b.permitted_call_start_local or '?'} to {b.permitted_call_end_local or '?'} "
             f"({b.time_zone or '?'})  \nSunday contact: {'yes' if b.sunday_contact_permitted else 'no'}")
    stops = [k.replace("_flag", "").replace("_", " ") for k in
             ["cease_communication_flag", "internal_do_not_call_flag", "deceased_flag", "bankruptcy_flag",
              "consumer_proposal_flag", "legal_representative_flag", "credit_counselling_agency_flag"] if b[k]]
    if stops:
        st.error("Contact restrictions: " + ", ".join(stops))
    if b.hardship_flag:
        st.info("Hardship recorded: use protective handling (specialist routing, hardship options).")
    if b.incident_affected:
        st.info("Affected by incident INC-0914 (app payments posted late): check before treating a promise as broken.")
    consent = {k.replace("consent_", ""): bool(b[k]) for k in
               ["consent_call", "consent_call_mobile", "consent_autodialer", "consent_sms", "consent_email",
                "consent_push"]}
    st.write("Consent: " + ", ".join(f"{k} {'yes' if v else 'no'}" for k, v in consent.items()))

    key = f"verified_{case_id}"
    if b.verify_before_disclosure and not st.session_state.get(key):
        st.markdown("**Debt details are hidden until identity is verified.**")
        if st.button("I have verified the customer's identity", type="primary"):
            audit.log({"user": get_user(), "role": role, "action": "identity_verified", "case_id": case_id,
                       "golden_customer_id": gid, "reason": "verified on call before disclosure"}, con=con)
            st.session_state[key] = True
            st.rerun()
    else:
        st.dataframe(pd.DataFrame([{
            "case": b.case_id, "status": b.case_status, "queue": b.queue, "bucket": b.current_bucket,
            "days past due": b.current_dpd, "overdue (CAD)": b.total_overdue, "exposure (CAD)": b.total_exposure,
            "current promise": b.current_ptp_id, "promise amount": b.current_ptp_amount,
            "promise due": b.current_ptp_due_date}]), hide_index=True)
        if b.case_link_alarm:
            st.warning("An account in this case belongs to a different customer record: check before discussing it.")


query = st.text_input("Search", placeholder="Case ID (CS-...) or golden customer ID (GC...)")
gid, case_id = resolve(query)
if not query:
    st.info("Enter a case ID (e.g. CS-2026-614370) or a golden customer ID (e.g. GC00778254) to open the record.")
    st.stop()
if gid is None:
    st.error("No customer found for that ID.")
    st.stop()

audit.log({"user": get_user(), "role": role, "action": "c360_viewed", "case_id": case_id,
           "golden_customer_id": gid}, con=con)

# ------------------------------------------------------------------ customer (analyst-safe view: no identifiers)
c = df("SELECT * FROM access_analyst.customers WHERE golden_customer_id = ?", [gid]).iloc[0]
confidence_header(c.identity_confidence, bool(c.verify_before_disclosure), c.match_probability)
st.markdown(f"#### Customer `{gid}`")
c1, c2, c3 = st.columns(3)
c1.write(f"Province: {c.province_code or 'n/a'}  \nProducts: {c.product_count}")
c2.write(f"Exposure: {c.total_credit_exposure:,.0f} CAD  \n"
         f"Max days past due: {'n/a' if pd.isna(c.max_dpd_current) else int(c.max_dpd_current)}")
c3.write(f"Open case: {c.open_case_id or 'none'}  \nBucket: {c.current_bucket or 'n/a'}")
flags = {"Cease communication": c.cease_communication_flag, "Internal do not call": c.internal_do_not_call_flag,
         "Deceased": c.deceased_flag, "Bankruptcy": c.bankruptcy_flag, "Consumer proposal": c.consumer_proposal_flag,
         "Legal representative": c.legal_representative_flag, "Credit counselling": c.credit_counselling_agency_flag,
         "Hardship (protective use only)": c.hardship_flag, "Affected by INC-0914": c.incident_affected}
on = [k for k, v in flags.items() if v]
st.write("Flags: " + (", ".join(on) if on else "none"))

# ------------------------------------------------------------------ case brief (searched case, or pick one)
cases = df("""SELECT case_id, case_status, case_open_date FROM access_analyst.cases
              WHERE golden_customer_id = ? ORDER BY case_open_date DESC""", [gid])
if case_id is None and not cases.empty:
    ids = cases.case_id.tolist()
    default = ids.index(c.open_case_id) if c.open_case_id in ids else 0
    case_id = st.selectbox("Case", ids, index=default,
                           format_func=lambda x: f"{x} · {cases.set_index('case_id').loc[x, 'case_status']}")
if case_id:
    with st.container(border=True):
        case_brief(case_id, gid)

# ------------------------------------------------------------------ details
tab_links, tab_accounts, tab_cases, tab_lineage = st.tabs(["Linked records", "Accounts", "Cases", "Lineage"])
with tab_links:   # how many records were merged per source, not the source keys
    links = df("""SELECT source_system, link_status FROM identity.crosswalk
                  WHERE golden_customer_id = ? AND valid_to IS NULL""", [gid])
    st.dataframe(links.groupby(["source_system", "link_status"]).size().rename("records").reset_index(),
                 hide_index=True)
with tab_accounts:
    st.dataframe(df("""SELECT account_id, product, product_type, account_status, balance, credit_limit, dpd,
                              dpd_bucket_recomputed AS dpd_bucket, dpd_missing, snapshot_date
                       FROM access_analyst.accounts WHERE golden_customer_id = ? ORDER BY product, account_id""",
                    [gid]), hide_index=True)
with tab_cases:
    st.dataframe(df("""SELECT case_id, case_status, case_open_date, primary_product, current_bucket_recomputed AS
                              bucket, current_dpd, total_overdue, outcome, case_link_alarm
                       FROM access_analyst.cases WHERE golden_customer_id = ? ORDER BY case_open_date DESC""",
                    [gid]), hide_index=True)
with tab_lineage:
    st.json({k: str(c[k]) for k in LINEAGE + ["source_systems"]})
