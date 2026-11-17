"""Typing errors: rules fix clear typos, real words are never touched, hard refusals can't be dodged, and the AI
rewrite is called only for soft refusals (fake provider: no network, no budget)."""
from pathlib import Path

import pytest

from layer2.normalize import normalize

DB = Path(__file__).resolve().parents[2] / "lake" / "maple.duckdb"
needs_db = pytest.mark.skipif(not DB.exists(), reason="needs the built database")


@pytest.mark.parametrize("text", [
    "For promises falling due in September 2026, whether women or men",           # real words, never "corrected"
    "The customer sends a request; calls started late",
    "Which neighbourhoods get a cheque under the programme?",                     # British / Canadian spellings
    "What's the rate in Montreal for customer's case CS-2026-219317?",            # contraction, name, possessive, ID
    "Why is GC00001753 high risk? PTP-4511214 and POL-COLL-001 §5.2 in Q3 2026",  # IDs and references
    "whats the RPC rate by DPD bucket",                                           # contraction without ', acronyms
])
def test_real_words_names_and_ids_untouched(text):
    assert normalize(text) == (text, [])


@pytest.mark.parametrize("typed, fixed", [
    ("promis kept rate by queu", "promise kept rate by queue"),
    ("cases recieved more than 5 atempts", "cases received more than 5 attempts"),
    ("payment remindrs by emial without consnet", "payment reminders by email without consent"),
    ("avg promised amount by chanel", "avg promised amount by channel"),
    ("late-fee disptues in mid-Septmber 2026", "late-fee disputes in mid-September 2026"),
])
def test_clear_typos_fixed(typed, fixed):
    assert normalize(typed)[0] == fixed


def test_no_close_word_left_alone():
    assert normalize("show the zqxwv report")[1] == []   # nothing within one edit: unchanged, never guessed


@needs_db
def test_misspelt_protected_question_is_refused():
    from layer2.ask import ask
    out = ask("rank customers by gendr", use_model=False)
    assert out["refused"] and out["refusal_category"] == "protected_attribute" and out["read_as"].endswith("gender")


@needs_db
def test_kill_switch(monkeypatch):
    from layer2.ask import ask
    monkeypatch.setenv("L2_NORMALIZE", "0")
    assert "read_as" not in ask("promis kept rate by queu", use_model=False)


@pytest.fixture
def fake_ai(monkeypatch):
    """adapters.complete that answers only the rewrite prompt (with `reply`) and counts the calls."""
    from layer2 import adapters, freeform
    from layer2 import ask as ask_mod
    calls = []

    def install(reply):
        def complete(prompt, system=None, json_schema=None, **kw):
            if json_schema is ask_mod.REWRITE_SCHEMA:
                calls.append(prompt)
                return reply(prompt)
            if json_schema is freeform.SCHEMA:     # the SQL writer cannot read the garbled question either
                return {"verdict": "cannot", "sql": "", "explanation": "unclear question"}
            raise adapters.LLMUnavailable("other lanes answer by rules in this test")
        monkeypatch.setattr(adapters, "complete", complete)
        monkeypatch.setattr(adapters, "llm_available", lambda: True)
        return calls
    return install


@needs_db
def test_rewrite_only_for_soft_refusals(fake_ai):
    from layer2.ask import ask
    calls = fake_ai(lambda p: {"rewritten": "", "changed": False})
    ask("rank customers by gendr")                                        # hard refusal: never sent to the AI
    assert calls == []
    out = ask("Who will win the Stanley Cup this season?")               # soft refusal: one rewrite call
    assert out["refused"] and len(calls) == 1


@needs_db
def test_rewrite_answers_when_rules_cannot(fake_ai):
    from layer2.ask import ask
    calls = fake_ai(lambda p: {"rewritten": "What is the promise kept rate by queue for promises due in August 2026?",
                               "changed": True})
    out = ask("wat iz da prmse kpt rte bi qeue 4 promises due in August 2026")
    assert len(calls) == 1 and not out["refused"] and out["engines"]["normalize"] == "llm rewrite"
    assert "promise_kept_rate_pct" in out["answer"]


def test_rewrite_that_changes_numbers_or_ids_is_rejected(fake_ai):
    from layer2.ask import rewrite
    fake_ai(lambda p: {"rewritten": "Summarise the last call on case CS-2026-000001", "changed": True})
    assert rewrite("sumarise the last cal on case CS-2026-614370") is None


def test_rewrite_must_keep_counts_but_may_read_texting(fake_ai):
    from layer2.ask import rewrite
    fake_ai(lambda p: {"rewritten": "Show the top 10 queues by total overdue", "changed": True})
    assert rewrite("shw the top 5 queus by totl overdue") is None                # 5 became 10: rejected
    fake_ai(lambda p: {"rewritten": "Kept rate for promises due in August 2026", "changed": True})
    assert rewrite("kpt rate 4 promises due in August 2026") == "Kept rate for promises due in August 2026"


def test_french_questions_are_left_alone():
    q = "Le client a-t-il des dificultés financiéres selon les notes du dossier CS-2026-212575 ?"
    assert normalize(q) == (q, [])
