"""Pre-call brief on a small synthetic database: disclosure lock, flags with policy, options by rules."""
import json

import duckdb
import pytest

from layer4 import precall

AS_OF = "2026-09-28"
PROTECTED = ("gender", "marital", "citizenship", "household", "newcomer", "accessibility", "vulnerab", "accent",
             "age_band", "aml", "date_of_birth", "dob")


def _program(pid, products, level, max_dpd, mob, approver="agent", income="false", insolv="true", broken="true"):
    return (pid, "2026.1", pid.title(), json.dumps(products), "§4.2", "desc", level, str(max_dpd), str(mob), "1",
            insolv, broken, income, approver, "2026-03-01", "9999-12-31")


@pytest.fixture
def con(tmp_path):
    c = duckdb.connect(str(tmp_path / "t.duckdb"))
    c.execute("CREATE SCHEMA access_agent; CREATE SCHEMA gold; CREATE SCHEMA bronze")
    c.execute("""CREATE TABLE access_agent.case_brief (case_id VARCHAR, assigned_agent_id VARCHAR, queue VARCHAR,
        golden_customer_id VARCHAR, identity_confidence VARCHAR, verify_before_disclosure BOOLEAN,
        first_name VARCHAR, last_name VARCHAR, phone_masked VARCHAR, best_reachable_phone_masked VARCHAR,
        email_masked VARCHAR, preferred_language VARCHAR, province_code VARCHAR, time_zone VARCHAR,
        permitted_call_start_local VARCHAR, permitted_call_end_local VARCHAR, sunday_contact_permitted VARCHAR,
        consent_call VARCHAR, cease_communication_flag BOOLEAN, internal_do_not_call_flag BOOLEAN,
        deceased_flag BOOLEAN, bankruptcy_flag BOOLEAN, consumer_proposal_flag BOOLEAN,
        legal_representative_flag BOOLEAN, credit_counselling_agency_flag BOOLEAN, hardship_flag BOOLEAN,
        incident_affected BOOLEAN, current_bucket VARCHAR, current_dpd INTEGER, total_overdue DOUBLE,
        total_exposure DOUBLE, current_ptp_id VARCHAR, current_ptp_amount DOUBLE, current_ptp_due_date VARCHAR,
        case_link_alarm BOOLEAN)""")
    c.execute("""CREATE TABLE gold.cases (case_id VARCHAR, golden_customer_id VARCHAR, assigned_agent_id VARCHAR, queue VARCHAR,
        case_status VARCHAR, outcome VARCHAR, case_open_date VARCHAR, primary_account_id VARCHAR,
        primary_product VARCHAR, account_id_list VARCHAR[], hardship_flag VARCHAR, hardship_source VARCHAR,
        hardship_reason VARCHAR, hardship_plan_type VARCHAR, hardship_plan_start VARCHAR, hardship_plan_end VARCHAR,
        payment_plan_flag VARCHAR, dispute_flag VARCHAR, dispute_type VARCHAR, dispute_status VARCHAR,
        complaint_flag VARCHAR, complaint_stage VARCHAR, cease_contact_flag VARCHAR, third_party_rep_flag VARCHAR,
        insolvency_hold_flag VARCHAR, deceased_hold_flag VARCHAR, legal_status VARCHAR, contact_cap_7d VARCHAR,
        contacts_last_7d VARCHAR, promises_kept VARCHAR, promises_broken VARCHAR, promises_open VARCHAR,
        last_contact_ts VARCHAR, next_scheduled_action VARCHAR)""")
    c.execute("""CREATE TABLE gold.c360_accounts (golden_customer_id VARCHAR, account_id VARCHAR, product VARCHAR,
        product_type VARCHAR, account_status VARCHAR, open_date DATE, balance DOUBLE, credit_limit DOUBLE,
        past_due_amount DOUBLE, dpd INTEGER, dpd_bucket_recomputed VARCHAR, dpd_missing BOOLEAN,
        hardship_program_flag BOOLEAN, chargeoff_flag BOOLEAN)""")
    c.execute("""CREATE TABLE gold.promises_to_pay (case_id VARCHAR, ptp_id VARCHAR, ptp_created_ts VARCHAR,
        ptp_amount VARCHAR, ptp_due_date VARCHAR, ptp_status_adjusted VARCHAR, amount_paid_against VARCHAR,
        ptp_channel VARCHAR, adjustment_reason VARCHAR)""")
    c.execute("""CREATE TABLE gold.contact_timeline (case_id VARCHAR, contact_ts_utc VARCHAR, direction VARCHAR,
        channel_resolved VARCHAR, outcome_desc VARCHAR, rpc_flag VARCHAR, ptp_made_flag VARCHAR,
        dispute_raised_flag VARCHAR, hardship_mentioned_flag VARCHAR)""")
    c.execute("""CREATE TABLE bronze.hardship_programs (program_id VARCHAR, version VARCHAR, program_name VARCHAR,
        product_types VARCHAR, policy_section VARCHAR, description VARCHAR, min_hardship_level VARCHAR,
        max_dpd VARCHAR, min_months_on_book VARCHAR, max_uses_12m VARCHAR, excludes_insolvency VARCHAR,
        excludes_broken_plan_90d VARCHAR, requires_income_evidence VARCHAR, approver_level VARCHAR,
        effective_from VARCHAR, effective_to VARCHAR)""")
    c.execute("""CREATE TABLE bronze.offers (offer_id VARCHAR, case_id VARCHAR, account_id VARCHAR,
        program_id VARCHAR, offer_ts VARCHAR, response_ts VARCHAR, customer_response VARCHAR, plan_status VARCHAR)""")
    c.executemany("INSERT INTO bronze.hardship_programs VALUES (" + ",".join("?" * 16) + ")", [
        _program("HP-CARD-MIN", ["card"], "possible", 90, 6),
        _program("HP-CARD-RATE", ["card"], "clear", 120, 12),
        _program("HP-CARD-FPP", ["card"], "clear", 150, 12, approver="hardship_specialist", income="true"),
        _program("HP-SETTLE", ["card", "loc"], "none", 999, 6, approver="team_lead", broken="false"),
        _program("HP-LOAN-EXT", ["personal_loan"], "clear", 120, 12),
        ("HP-OLD", "2025.1", "Old", '["card"]', "§4.2", "d", "none", "999", "0", "1", "true", "true", "false",
         "agent", "2025-03-01", "2026-02-28"),
    ])
    add_case(c, "CS-1", verify=True)
    return c


def add_case(c, case_id, verify=False, dpd=45, hardship="false", bankrupt=False, dispute=None, consent="true",
             contacts=1, ptp=None, opened="2024-01-01", offers=()):
    gid, acct = f"G-{case_id}", f"C-{case_id}"
    c.execute("INSERT INTO access_agent.case_brief VALUES (" + ",".join("?" * 35) + ")", [
        case_id, "AG-1", "early", gid, "medium" if verify else "high", verify, "Ann", "Lee", "***-***-1234", None,
        "a***@x.com", "EN", "ON", "America/Toronto", "08:00", "21:00", "false", consent, False, False, False,
        bankrupt, False, False, False, hardship == "true", False, "31-60", dpd, 900.0, 4000.0,
        ptp and "P-1", ptp and ptp[0], ptp and ptp[1], False])
    c.execute("INSERT INTO gold.cases (case_id, golden_customer_id, assigned_agent_id, case_status, primary_account_id, "
              "primary_product, account_id_list, hardship_flag, hardship_reason, dispute_flag, dispute_status, "
              "contact_cap_7d, contacts_last_7d) VALUES (?, ?, 'AG-1', 'open', ?, 'card', [?], ?, ?, ?, ?, '3', ?)",
              [case_id, gid, acct, acct, hardship, "job_loss" if hardship == "true" else None,
               "true" if dispute else "false", dispute, str(contacts)])
    c.execute("INSERT INTO gold.c360_accounts VALUES (?, ?, 'card', 'visa', 'open', ?, 4000, 5000, 900, ?, '31-60', "
              "false, false, false)", [gid, acct, opened, dpd])
    for i, (pid, resp, status, ts) in enumerate(offers):
        c.execute("INSERT INTO bronze.offers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  [f"O-{case_id}-{i}", case_id, acct, pid, ts, ts, resp, status])


def status(brief):
    return {o["program_id"]: o["status"] for o in brief["options"]}


def test_debt_is_held_back_until_identity_is_verified(con):
    locked = precall.build_brief("CS-1", con=con, as_of=AS_OF)
    assert locked["debt_locked"] and locked["debt"] is None
    assert locked["customer"]["verify_before_disclosure"] is True
    opened = precall.build_brief("CS-1", con=con, as_of=AS_OF, identity_verified=True)
    assert not opened["debt_locked"] and opened["debt"]["case"]["total_overdue"] == 900.0
    assert opened["debt"]["accounts"][0]["account_id"] == "C-CS-1"


def test_high_confidence_needs_no_verification(con):
    add_case(con, "CS-2", verify=False)
    assert precall.build_brief("CS-2", con=con, as_of=AS_OF)["debt"] is not None


def test_agent_sees_only_their_cases(con):
    with pytest.raises(PermissionError):
        precall.build_brief("CS-1", con=con, as_of=AS_OF, agent_id="AG-9")
    with pytest.raises(LookupError):
        precall.build_brief("NOPE", con=con, as_of=AS_OF)


def test_options_follow_the_rules_and_say_why(con):
    b = precall.build_brief("CS-1", con=con, as_of=AS_OF)
    s = status(b)
    assert "HP-OLD" not in s and "HP-LOAN-EXT" not in s          # not in force on as_of; not a card product
    assert s["HP-SETTLE"] == "eligible"                           # no hardship needed, DPD within 999
    assert s["HP-CARD-MIN"] == s["HP-CARD-RATE"] == "check"       # hardship not recorded: confirm on the call
    fpp = next(o for o in b["options"] if o["program_id"] == "HP-CARD-FPP")
    assert any("income" in n for n in fpp["notes"]) and any("hardship specialist" in n for n in fpp["notes"])
    assert all(o["policy"] == "POL-COLL-004 §4.2" for o in b["options"])


def test_recorded_hardship_and_dpd_limit(con):
    add_case(con, "CS-3", dpd=100, hardship="true")
    b = precall.build_brief("CS-3", con=con, as_of=AS_OF)
    s = status(b)
    assert b["hardship"]["level"] == "clear" and b["hardship"]["reason"] == "job_loss"
    assert s["HP-CARD-RATE"] == "eligible" and s["HP-CARD-MIN"] == "ineligible"
    why = next(o for o in b["options"] if o["program_id"] == "HP-CARD-MIN")["why_not"]
    assert why == ["100 days past due is over the limit of 90"]


def test_insolvency_stops_the_call_and_excludes_programmes(con):
    add_case(con, "CS-4", bankrupt=True, hardship="true")
    b = precall.build_brief("CS-4", con=con, as_of=AS_OF)
    assert b["flags"][0]["level"] == "stop" and b["flags"][0]["policy"] == "POL-COLL-001 §7.3"
    assert all(o["status"] == "ineligible" for o in b["options"])


def test_usage_limit_broken_plan_and_account_age(con):
    add_case(con, "CS-5", hardship="true", opened="2026-01-15",
             offers=[("HP-CARD-RATE", "accepted", "active", "2026-02-01 10:00:00"),
                     ("HP-CARD-MIN", "accepted", "broken", "2026-08-15 10:00:00")])
    b = precall.build_brief("CS-5", con=con, as_of=AS_OF)
    rate = next(o for o in b["options"] if o["program_id"] == "HP-CARD-RATE")
    assert any("already used 1" in w for w in rate["why_not"])
    assert any("broken in the last 90 days" in w for w in rate["why_not"])
    assert any("account open 8 months; needs 12" == w for w in rate["why_not"])


def test_flags_carry_policy_and_order(con):
    add_case(con, "CS-6", dispute="open", consent="false", contacts=3, hardship="true")
    titles = [(f["level"], f["title"]) for f in precall.build_brief("CS-6", con=con, as_of=AS_OF)["flags"]]
    levels = [lvl for lvl, _ in titles]
    assert levels == sorted(levels, key=["stop", "caution", "info"].index)
    assert ("stop", "No consent to call") in titles
    assert any(t.startswith("Contact limit reached") for _, t in titles)
    assert any(t.startswith("Open dispute") for _, t in titles)


def test_risk_uses_open_promise_or_what_if(con, monkeypatch):
    monkeypatch.setattr(precall, "score_promise", lambda gid, amount, due, as_of: {   # isolate from Layer 3's state
        "risk": 0.4, "reasons": ["a", "b", "c"], "safer_date": None, "model_version": "fake"})
    add_case(con, "CS-7", ptp=(250.0, "2026-10-05"))
    r = precall.build_brief("CS-7", con=con, as_of=AS_OF)["debt"]["risk"]
    assert r["available"] and not r["what_if"] and r["amount"] == 250.0 and len(r["reasons"]) == 3
    add_case(con, "CS-8")
    r = precall.build_brief("CS-8", con=con, as_of=AS_OF)["debt"]["risk"]
    assert r["what_if"] and r["due_date"] == "2026-10-05" and r["amount"] == 900.0


def test_no_protected_or_identifying_fields_in_the_brief(con):
    b = precall.build_brief("CS-1", con=con, as_of=AS_OF, identity_verified=True)

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    found = [k for k in keys(b) if any(p in k.lower() for p in PROTECTED)]
    assert found == []


def test_agent_assist_page_renders_a_locked_brief_and_unlocks_it(con, tmp_path, monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    from shared import db
    path = con.execute("SELECT path FROM duckdb_databases() WHERE database_name = current_database()").fetchone()[0]
    con.close()
    monkeypatch.setenv("DHANDHO_DB", path)
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "llm"))
    db.close_all()
    page = str(Path(__file__).resolve().parents[2] / "app" / "pages" / "4_Agent_Assist.py")
    at = AppTest.from_file(page, default_timeout=30)
    at.session_state["role"] = "team_lead"
    at.run()
    assert not at.exception
    assert any("Verify identity" in w.value for w in at.warning)
    assert any("Hidden until identity is verified" in i.value for i in at.info)
    at.button[0].click().run()
    assert not at.exception
    assert not any("Verify identity" in w.value for w in at.warning)
    assert any(m.label == "Overdue" and m.value == "$900.00" for m in at.metric)
    actions = [r[0] for r in db.connect().execute("SELECT action FROM audit.events ORDER BY ts").fetchall()]
    assert actions == ["brief_opened", "identity_verified"]
    db.close_all()


def test_a_failing_risk_model_does_not_break_the_brief(con, monkeypatch):
    def boom(*a):
        raise RuntimeError("feature store not built")
    monkeypatch.setattr(precall, "score_promise", boom)
    r = precall.build_brief("CS-1", con=con, as_of=AS_OF, identity_verified=True)["debt"]["risk"]
    assert r == {"available": False, "reason": "Risk model unavailable (RuntimeError)"}
