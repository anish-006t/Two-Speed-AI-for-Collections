"""Post-call summary: narrative in the gold style, next-step rules, approval flow and write-back to Layer 3."""
import json

import duckdb
import pytest

from layer4 import postcall, precall
from shared import db


def f(**kw):
    return {k: {"value": v} for k, v in kw.items()}


@pytest.mark.parametrize("fields, gold", [
    (f(hardship="clear", hardship_reason="job_loss", promise_amount=345.0, promise_date="2026-09-18"),
     "Customer reports job loss; promised $345 on 18 Sep; next step: hardship plan review."),
    (f(hardship="none", promise_amount=150.73, promise_date="2025-12-02"),
     "promised $151 on 2 Dec; next step: send payment link."),
    (f(hardship="possible", hardship_reason="other", promise_amount=485.0, promise_date="2026-10-04"),
     "Customer indicates finances are tight; promised $485 on 4 Oct; next step: send payment link."),
    (f(hardship="clear", hardship_reason="rate_reset", dispute=True, dispute_type="late_fee"),
     "Customer reports payment increase after rate reset; disputes late fee; next step: dispute investigation."),
    (f(hardship="clear", hardship_reason="reduced_income", dispute=True, dispute_type="late_fee", complaint=True),
     "Customer reports reduced hours / income; disputes late fee; wants to complain; next step: escalate supervisor."),
    (f(hardship="none", promise_amount=4213.0, promise_date="2026-05-26", sentiment=-0.9),
     "promised $4,213 on 26 May; next step: escalate supervisor."),
    (f(hardship="none"), "No commitment made; next step: call back."),
    (f(hardship="none", cease_request=True), "asked to stop contact; next step: stop contact."),
])
def test_narrative_matches_the_gold_style(fields, gold):
    assert postcall.narrative(fields) == gold


def test_disability_is_never_written():
    text = postcall.narrative(f(hardship="clear", hardship_reason="disability"))
    assert "disab" not in text.lower() and text.startswith("Customer reports a health-related hardship")


@pytest.fixture
def con(tmp_path, monkeypatch):
    path = tmp_path / "s.duckdb"
    monkeypatch.setenv("DHANDHO_DB", str(path))
    db.close_all()
    c = db.connect(read_only=False)
    yield c
    db.close_all()


DRAFT = {"transcript_id": "T-1", "case_id": "CS-1", "golden_customer_id": "G-1", "call_ts": "2026-09-10 10:00:00",
         "fields": {"hardship": {"value": "possible"}, "hardship_reason": {"value": None},
                    "promise_amount": {"value": 400.0}, "promise_date": {"value": "2026-09-18"},
                    "next_step": {"value": "send_payment_link"}},
         "narrative": "Customer indicates finances are tight; promised $400 on 18 Sep; next step: send payment link.",
         "model_version": "test"}


def test_approve_records_edits_audits_and_feeds_the_next_brief(con):
    sid = postcall.save_draft(DRAFT, "agent-a", con=con)
    final = {"hardship": "clear", "hardship_reason": "reduced_income", "promise_amount": 400.0,
             "promise_date": "2026-09-18", "next_step": "hardship_plan_review"}
    text = postcall.narrative({k: {"value": v} for k, v in final.items()})
    with pytest.raises(ValueError, match="give a reason"):
        postcall.approve(sid, final, text, "agent-a", con=con)
    edits = postcall.approve(sid, final, text, "agent-a", con=con, reason="customer said hours were cut")
    assert set(edits) == {"hardship", "hardship_reason", "next_step", "narrative"}
    row = con.execute("SELECT status, approved_by, narrative FROM layer4.summaries WHERE summary_id = ?",
                      [sid]).fetchone()
    assert row == ("approved", "agent-a", text)
    tf = con.execute("""SELECT source_type, golden_customer_id, hardship, hardship__prob, promise_amount
                        FROM layer3.text_features""").fetchall()
    assert tf == [("approved_summary", "G-1", "clear", 1.0, "400.0")]
    actions = [r[0] for r in con.execute("SELECT action FROM audit.events ORDER BY ts").fetchall()]
    assert actions == ["summary_drafted", "summary_approved"]
    assert precall.approved_summaries(con, "G-1")[0]["narrative"] == text      # round trip into the brief
    with pytest.raises(ValueError):
        postcall.approve(sid, final, text, "agent-a", con=con, reason="again")  # only once


def test_write_back_fits_layer3s_existing_table(con):
    con.execute("CREATE SCHEMA layer3")
    con.execute("""CREATE TABLE layer3.text_features (source_type VARCHAR, source_id VARCHAR,
                   golden_customer_id VARCHAR, case_id VARCHAR, event_ts TIMESTAMP, hardship VARCHAR,
                   hardship__prob DOUBLE, legal_threat_by_agent VARCHAR, model_version VARCHAR)""")
    sid = postcall.save_draft(DRAFT, "agent-a", con=con)
    postcall.approve(sid, {"hardship": "possible", "promise_amount": 400.0, "promise_date": "2026-09-18"},
                     DRAFT["narrative"], "agent-a", con=con)                     # unchanged fields: no reason needed
    assert con.execute("SELECT hardship, legal_threat_by_agent, model_version FROM layer3.text_features").fetchone() \
        == ("possible", None, "approved_summary")


def test_an_edited_promise_reaches_the_features_from_the_day_after_the_call(con):
    """The approved summary counts as known the day after the call (Layer 3's rule for every call), not at the
    real clock time of approval, so the next brief's risk score uses the agent's corrected amount."""
    from layer3.features.defs import FEATURES
    sid = postcall.save_draft(DRAFT, "agent-a", con=con)
    postcall.approve(sid, {"hardship": "possible", "promise_amount": 300.0, "promise_date": "2026-09-18"},
                     DRAFT["narrative"], "agent-a", con=con, reason="customer corrected the amount")
    assert str(con.execute("SELECT available_at FROM layer3.text_features").fetchone()[0]) == "2026-09-11 10:00:00"
    amount = next(f.definition for f in FEATURES if f.name == "text_promise_amount")

    def as_of(ts):
        return con.execute(f"SELECT ({amount}) FROM (SELECT 'G-1' AS golden_customer_id, "
                           f"TIMESTAMP '{ts}' AS cutoff) e").fetchone()[0]
    assert float(as_of("2026-09-12 00:00:00")) == 300.0      # the corrected amount, not the drafted 400
    assert as_of("2026-09-10 12:00:00") is None               # not yet known on the day of the call
    # the machine reading of the same call is known at the same moment: the agent's approved version still wins
    con.execute("""INSERT INTO layer3.text_features (source_type, source_id, golden_customer_id, event_ts,
                   available_at, promise_amount) VALUES ('transcript', 'zzz', 'G-1', TIMESTAMP '2026-09-10 10:00:00',
                   TIMESTAMP '2026-09-11 10:00:00', '400.0')""")
    assert float(as_of("2026-09-12 00:00:00")) == 300.0


def test_reject_needs_a_reason(con):
    sid = postcall.save_draft(DRAFT, "agent-a", con=con)
    with pytest.raises(ValueError):
        postcall.reject(sid, "  ", "agent-a", con=con)
    postcall.reject(sid, "wrong customer on the call", "agent-a", con=con)
    assert con.execute("SELECT status, reject_reason FROM layer4.summaries").fetchone() == \
        ("rejected", "wrong customer on the call")
    assert not db.table_exists(con, "layer3", "text_features")                  # nothing written on reject
    assert postcall.summaries_for(con, case_id="CS-1")[0]["status"] == "rejected"


def test_draft_on_a_real_call_when_the_database_exists():
    path = db.db_path()
    if not path.exists():
        pytest.skip("needs the built database")
    try:
        d = postcall.draft("1010325", con=duckdb.connect(str(path), read_only=True))
    except duckdb.IOException:
        pytest.skip("database in use")
    assert d["fields"]["promise_amount"]["value"] == 400.0 and d["fields"]["promise_date"]["value"] == "2026-09-18"
    assert d["narrative"].endswith(".") and "promised $400 on 18 Sep" in d["narrative"]
    json.dumps(d["fields"], default=str)


def test_promise_comes_before_complaint_and_insolvency_stops_contact():
    assert postcall.narrative(f(hardship="none", dispute=True, dispute_type="late_fee", promise_amount=125.0,
                                promise_date="2026-10-09", complaint=True)) == \
        "disputes late fee; promised $125 on 9 Oct; wants to complain; next step: escalate supervisor."
    assert postcall.narrative(f(hardship="possible", promise_amount=1765.0, promise_date="2026-09-18",
                                insolvency_mention=True)) == \
        ("Customer indicates finances are tight; promised $1,765 on 18 Sep; mentions consumer proposal / bankruptcy; "
         "next step: stop contact.")


def test_summary_tab_drafts_edits_and_approves(tmp_path, monkeypatch):
    """The page end to end on a one-call database: draft, change hardship, approve; written to layer4 and layer3."""
    from pathlib import Path

    from streamlit.testing.v1 import AppTest
    path = tmp_path / "ui.duckdb"
    c = duckdb.connect(str(path))
    c.execute("CREATE SCHEMA silver; CREATE SCHEMA bronze; CREATE SCHEMA identity; CREATE SCHEMA gold; "
              "CREATE SCHEMA access_agent")
    c.execute("CREATE TABLE access_agent.case_brief AS SELECT 'CS-1' AS case_id")
    c.execute("CREATE TABLE gold.cases (case_id VARCHAR, outcome VARCHAR, assigned_agent_id VARCHAR, queue VARCHAR, "
              "case_status VARCHAR, primary_product VARCHAR, case_open_date VARCHAR)")
    c.execute("""CREATE TABLE bronze.call_transcripts AS SELECT 'T-9' AS transcript_id, 'CS-1' AS case_id,
                 'AG-1' AS agent_id, TIMESTAMP '2026-09-10 10:00:00' AS call_start_ts, 60 AS duration_sec,
                 'outbound' AS direction, 'CRM-1' AS crm_customer_id""")
    c.execute("""CREATE TABLE identity.crosswalk AS SELECT 'CRM:CRM-1' AS source_key, 'G-9' AS golden_customer_id,
                 NULL::TIMESTAMP AS valid_to""")
    c.execute("""CREATE TABLE silver.transcript_turns_redacted (transcript_id VARCHAR, turn_index INTEGER,
                 speaker VARCHAR, start_sec DOUBLE, end_sec DOUBLE, text_redacted VARCHAR)""")
    c.executemany("INSERT INTO silver.transcript_turns_redacted VALUES ('T-9', ?, ?, ?, ?, ?)", [
        (1, "agent", 1.0, 4.0, "Hi, this is <PERSON> calling from Maple Bank."),
        (2, "customer", 5.0, 9.0, "Money is tight right now."),
        (3, "agent", 10.0, 15.0, "Just to confirm, a payment of $200.00 on September 20."),
    ])
    c.close()
    monkeypatch.setenv("DHANDHO_DB", str(path))
    monkeypatch.setenv("L3_EXTRACTOR_MODE", "rules")
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "llm"))
    db.close_all()
    page = str(Path(__file__).resolve().parents[2] / "app" / "pages" / "4_Agent_Assist.py")
    at = AppTest.from_file(page, default_timeout=60)
    at.session_state["role"] = "agent"
    at.session_state["aa_step"] = "3 · Summary"
    at.run()
    at.text_input(key="summary_tid").set_value("T-9").run()
    next(b for b in at.button if b.label == "Draft summary").click().run()
    assert not at.exception
    at.selectbox(key="T-9:hardship").set_value("clear").run()
    at.selectbox(key="T-9:reason").set_value("reduced_income").run()
    next(b for b in at.button if b.label == "Approve").click().run()
    assert not at.exception and any("give a reason" in e.value for e in at.error)    # override needs a reason
    at.text_input(key="T-9:why").set_value("customer said hours were cut").run()
    next(b for b in at.button if b.label == "Approve").click().run()
    assert not at.exception
    con = db.connect()
    status, narrative_, edits = con.execute("SELECT status, narrative, edits FROM layer4.summaries").fetchone()
    assert status == "approved" and "hardship" in json.loads(edits)
    assert narrative_.startswith("Customer reports reduced hours / income; promised $200 on 20 Sep")
    assert con.execute("SELECT hardship FROM layer3.text_features").fetchone() == ("clear",)
    db.close_all()
