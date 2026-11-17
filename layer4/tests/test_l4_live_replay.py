"""Live replay logic with a controlled extract(): prompts fire once at the right turn, promises are checked, QA ticks."""
import pytest

from layer4 import gates
from layer4 import live_replay as lr

CALL_DATE = "2026-09-10"


def call(turns, gid="G-1"):
    return {"transcript_id": "T-1", "case_id": "CS-1", "golden_customer_id": gid, "call_date": CALL_DATE,
            "turns": [{"index": i + 1, "speaker": s, "start_sec": 10.0 * i, "end_sec": 10.0 * i + 5, "text": t}
                      for i, (s, t) in enumerate(turns)]}


def fake_extract(text, source_type):
    """Answers from the LAST line of the window only, so a rule must fire on the turn that says it."""
    last = text.splitlines()[-1].lower()
    a = lambda v, p=0.95, ev="": {"answer": v, "prob": p, "evidence": ev, "model_version": "fake"}  # noqa: E731
    out = {"hardship": a("clear" if "lost my job" in last else "none", 0.93, "lost my job" if "lost" in last else ""),
           "promise_mentioned": a("i'll pay" in last or "confirm" in last),
           "promise_amount": a("$400" if "$400" in last else ("four hundred" if "four hundred" in last else None)),
           "promise_date": a("September 18" if "september 18" in last else ("the 18th" if "18th" in last else None)),
           "third_party_present": a("speaker" in last), "insolvency_mention": a("bankrupt" in last, 0.8),
           "legal_threat_by_agent": a("arrest" in last), "dispute": a(False), "cease_request": a(False),
           "complaint": a(False), "protected_attribute_mentioned": a(False),
           "promise_intent_strength": a("firm" if "i'll pay" in last else None)}
    return out


@pytest.fixture(autouse=True)
def controlled(monkeypatch):
    monkeypatch.setattr(lr, "extract", fake_extract)
    monkeypatch.setattr(lr, "score_promise", lambda gid, amount, due, as_of: {
        "risk": 0.7 if amount >= 300 else 0.2, "reasons": ["a", "b", "c"], "safer_date": "2026-09-16",
        "model_version": "fake-model"})


DEMO = [("agent", "Hi, this is <PERSON> calling from Maple Bank. This call may be recorded."),
        ("agent", "Can you confirm your date of birth?"),
        ("customer", "Sure."),
        ("agent", "The amount past due is $502.04."),
        ("customer", "I lost my job last month."),
        ("agent", "I'm sorry. We have hardship options like a reduced payment."),
        ("customer", "I lost my job, like I said. I'll pay four hundred the 18th."),
        ("agent", "Just to confirm, a payment of $400 on September 18."),
        ("agent", "Thank you for your time today.")]


def test_hardship_fires_once_at_the_turn_it_is_said():
    r = lr.replay("T-1", call=call(DEMO))
    hardship = [(s["turn"]["index"], p) for s in r["steps"] for p in s["prompts"] if p["rule"] == "R-HARDSHIP"]
    assert len(hardship) == 1 and hardship[0][0] == 5
    p = hardship[0][1]
    assert p["policy"] == "POL-COLL-004 §4.2" and p["evidence"] == "lost my job" and p["gate"] == "confirm"
    assert lr.time_to_flag(r) == 40.0


def test_spoken_promise_is_scored_and_a_safer_date_suggested():
    r = lr.replay("T-1", call=call(DEMO))
    promises = [s["promise"] for s in r["steps"] if s["promise"]]
    assert len(promises) == 1                          # the agent's confirmation repeats it; not re-raised
    p = promises[0]
    assert p["amount"] == 400.0 and p["due_date"] == "2026-09-18" and p["status"] == "risky"
    assert "2026-09-16" in p["suggestion"] and p["model_version"] == "fake-model"


def test_incomplete_promise_says_what_is_missing():
    r = lr.replay("T-1", call=call([("customer", "I'll pay four hundred.")]))
    p = r["steps"][0]["promise"]
    assert p["status"] == "incomplete" and p["missing"] == ["date"]


def test_customer_rules_ignore_agent_turns_and_vice_versa():
    r = lr.replay("T-1", call=call([("agent", "Is anyone on speaker with you?"), ("customer", "My son is on speaker."),
                                     ("agent", "You could face arrest."), ("customer", "Arrest? Really?")]))
    fired = [(s["turn"]["index"], p["rule"]) for s in r["steps"] for p in s["prompts"]]
    assert fired == [(2, "R-THIRD-PARTY"), (3, "R-AGENT-THREAT")]


def test_qa_checklist_ticks_in_order_and_catches_disclosure_before_verification():
    r = lr.replay("T-1", call=call(DEMO))
    qa = r["steps"][-1]["qa"]
    assert qa["recording_notice_given"]["status"] == "done" and qa["recording_notice_given"]["turn"] == 1
    assert qa["identity_verified_before_disclosure"]["turn"] == 2
    assert qa["hardship_handled_per_policy"]["turn"] == 6
    assert qa["ptp_details_confirmed_back"]["turn"] == 8
    assert qa["respectful_close_and_next_steps"]["status"] == "done"
    early = lr.replay("T-1", call=call([("agent", "You owe $500 past due."), ("agent", "Confirm your date of birth?")]))
    assert early["steps"][-1]["qa"]["identity_verified_before_disclosure"]["status"] == "failed"


def test_low_confidence_goes_to_review():
    r = lr.replay("T-1", call=call([("customer", "We might go bankrupt.")]))
    p = r["steps"][0]["prompts"][0]
    assert p["rule"] == "R-INSOLVENCY" and p["gate"] == "confirm"
    assert gates.gate(0.95, "dispute") == "suggest" and gates.gate(0.95, "hardship") == "confirm"
    assert gates.gate(0.5, "dispute") == "review" and gates.gate(None) == "review"


@pytest.mark.parametrize("value, expected", [
    ("2026-09-18", "2026-09-18"), ("September 18", "2026-09-18"), ("sept 18th", "2026-09-18"), ("the 18th", "2026-09-18"),
    ("the 5th", "2026-10-05"), ("tomorrow", "2026-09-11"), ("Friday", "2026-09-11"), ("next week", "2026-09-17"),
    ("soon", None), (None, None), ("the 31st of February", None)])
def test_resolve_date(value, expected):
    assert lr.resolve_date(value, CALL_DATE) == expected


@pytest.mark.parametrize("value, expected", [
    (400, 400.0), ("$1,250.50", 1250.5), ("four hundred", 400.0), ("two hundred fifty", 250.0),
    ("one thousand two hundred", 1200.0), ("nothing", None), (None, None), (0, None)])
def test_parse_amount(value, expected):
    assert lr.parse_amount(value) == expected


def test_replay_tab_steps_through_a_real_call():
    """End to end on the built database (skipped without it): the demo call replays and the QA list fills in."""
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    from shared import db
    if not db.db_path().exists():
        pytest.skip("needs the built database")
    page = str(Path(__file__).resolve().parents[2] / "app" / "pages" / "4_Agent_Assist.py")
    at = AppTest.from_file(page, default_timeout=120)
    at.session_state["role"] = "team_lead"
    at.session_state["aa_step"] = "2 · Call"
    at.session_state["replay_tid"] = "1010325"
    at.run()
    assert not at.exception
    play = next(b for b in at.button if b.label == "Play to end")
    play.click().run()
    assert not at.exception
    assert at.session_state["replay"]["done"] and any("Hardship first said" in m.value for m in at.markdown)
    assert any(b.label == "Draft the summary" for b in at.button)
    db.close_all()


@pytest.mark.parametrize("value, expected", [
    ("Just to confirm, a payment of $400.00 on September 18.", "2026-09-18"),
    ("a payment of $1,250.50 on the 20th", "2026-09-20"), ("$400.00 by Friday", "2026-09-11"),
    ("pay at 10:30 on the 12th", "2026-09-12"), ("18th of September", "2026-09-18"),
    ("$400.00", None), ("January 5", "2027-01-05")])
def test_resolve_date_ignores_money_and_reads_the_confirmation(value, expected):
    assert lr.resolve_date(value, CALL_DATE) == expected


def test_review_threshold_and_sentiment_never_queued():
    # 0.75 (4 Oct): the rules' "possible hardship" (0.72) reaches a person; clear answers (0.9) stay with the agent
    assert gates.gate(0.72, "hardship") == "review" and gates.gate(0.78, "promise") == "confirm"
    assert gates.gate(0.9, "hardship") == "confirm" and gates.gate(0.76, "dispute") == "confirm"
    # sentiment is context, not a decision: confirm at most, never the review queue
    assert gates.gate(0.7, "sentiment") == "confirm" and gates.gate(None, "sentiment") == "confirm"
