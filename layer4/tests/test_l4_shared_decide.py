"""shared/decide.py: typed answers, evidence check, model_version, Jev routing. complete() is faked."""
import pytest

from shared import decide as d

TEXT = "Customer: I lost my job last month so I can only pay $50 on Friday."
HARDSHIP = {"id": "hardship", "version": 2, "type": "choice", "options": ["none", "possible", "clear"],
            "wording": "Does the customer describe financial hardship?"}


@pytest.fixture
def answer(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    seen = {}

    def set_reply(reply):
        def fake(prompt, **kw):
            seen.update(kw, prompt=prompt)
            return reply
        monkeypatch.setattr(d, "complete", fake)
        return seen
    return set_reply


def test_choice_answer_with_verbatim_evidence(answer):
    seen = answer({"answer": "clear", "confidence": 0.92, "evidence": "I lost my job"})
    out = d.decide(TEXT, HARDSHIP, model="m1")
    assert out == {"answer": "clear", "prob": 0.92, "evidence": "I lost my job",
                   "model_version": "llm:m1|q:hardship@2"}
    assert seen["json_schema"]["properties"]["answer"]["enum"] == ["none", "possible", "clear", "unknown"]
    assert seen["tag"] == "decide.hardship"


def test_quote_not_in_text_is_dropped_and_prob_capped(answer):
    answer({"answer": "clear", "confidence": 0.95, "evidence": "I was fired yesterday"})
    out = d.decide(TEXT, HARDSHIP, model="m1")
    assert out["evidence"] == "" and out["prob"] == 0.5


def test_unknown_becomes_none_with_no_evidence(answer):
    answer({"answer": "unknown", "confidence": 0.8, "evidence": "lost my job"})
    assert d.decide(TEXT, HARDSHIP)["answer"] is None
    assert d.decide(TEXT, HARDSHIP)["evidence"] == ""


def test_yes_no_string_question_and_bool_answer(answer):
    answer({"answer": "yes", "confidence": 0.7, "evidence": "pay $50"})
    out = d.decide(TEXT, "Is a promise to pay made?")
    assert out["answer"] is True and out["model_version"].endswith("q:adhoc@1")


def test_score_is_clamped_and_extract_is_text(answer):
    answer({"answer": "7", "confidence": 2.0, "evidence": ""})
    out = d.decide(TEXT, {"id": "s", "type": "score", "wording": "w", "min": -1, "max": 1})
    assert out["answer"] == 1.0 and out["prob"] == 1.0
    answer({"answer": "$50", "confidence": 0.9, "evidence": "$50"})
    assert d.decide(TEXT, {"id": "amt", "type": "extract", "wording": "Amount?"})["answer"] == "$50"


def test_off_list_choice_is_none(answer):
    answer({"answer": "severe", "confidence": 0.9, "evidence": ""})
    assert d.decide(TEXT, HARDSHIP)["answer"] is None


def test_bad_questions_are_rejected():
    with pytest.raises(ValueError):
        d.decide(TEXT, {"id": "x", "type": "choice", "wording": "w"})
    with pytest.raises(ValueError):
        d.decide(TEXT, {"id": "x", "type": "essay", "wording": "w"})


def test_jev_key_routes_to_jev(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "k")
    with pytest.raises(NotImplementedError):
        d.decide(TEXT, HARDSHIP)


def test_decide_many_asks_once_and_checks_each_answer(answer):
    seen = answer({"hardship": {"answer": "clear", "confidence": 0.9, "evidence": "I lost my job"},
                   "promise": {"answer": "yes", "confidence": 0.8, "evidence": "pay $50 on Friday"},
                   "amt": {"answer": "$50", "confidence": 0.7, "evidence": "made up quote"}})
    out = d.decide_many(TEXT, [HARDSHIP, {"id": "promise", "wording": "Promise?"},
                               {"id": "amt", "type": "extract", "wording": "Amount?"}], model="m1")
    assert out["hardship"]["answer"] == "clear" and out["promise"]["answer"] is True
    assert out["amt"]["evidence"] == "" and out["amt"]["prob"] == 0.5          # unverified quote: capped
    assert set(seen["json_schema"]["required"]) == {"hardship", "promise", "amt"}
    assert out["hardship"]["model_version"] == "llm:m1|q:hardship@2" and out["amt"]["model_version"] == "llm:m1|q:amt@1"
