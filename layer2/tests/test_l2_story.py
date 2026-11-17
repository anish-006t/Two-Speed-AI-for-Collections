"""Agent-notes retrieval: visibility rules, topic ranking, when vectors run, and the hybrid tie-break.
A fake embedder replaces the model, so these tests never download or load it."""
import pytest

from layer2 import story, vectors

ROWS = [   # (note_id, case_id, date, note_type, text)
    ("N-1", "CS-1", "2026-09-20", "call_note", "LM, no answer"),
    ("N-2", "CS-1", "2026-09-10", "hardship_assessment", "hrs cut at plant, can only pay 50"),
    ("N-3", "CS-1", "2026-09-05", "dispute_note", "says late fee wrong, paid on time in app"),
    ("N-4", "CS-1", "2026-09-01", "skip_trace", "new number found via <PERSON>"),
    ("N-5", "CS-1", "2026-08-30", "system_note", "auto dialer attempt"),
    ("N-6", "CS-1", "2026-08-20", "payment_note", "PTP 120 on 2026-09-15"),
    ("N-7", "CS-1", "2026-08-10", "call_note", None),
    ("N-8", "CS-1", "2026-08-01", "supervisor_note", "client mécontent, escaladé au superviseur"),
]


def test_visibility_rules():
    analyst = {r[0] for r in story.visible(ROWS, "show the notes", "analyst")}
    agent = {r[0] for r in story.visible(ROWS, "show the notes", "agent")}
    assert "N-4" not in analyst and "N-4" in agent          # skip trace: agents only
    assert "N-5" not in analyst and "N-7" not in analyst    # system notes unless asked; empty notes never
    assert "N-5" in {r[0] for r in story.visible(ROWS, "show the system notes", "analyst")}


@pytest.mark.parametrize("q, first", [
    ("What has the customer said about money problems?", "N-2"),
    ("Did they disagree with a charge?", "N-3"),
    ("Anything about paying?", "N-6"),
    ("Le client a-t-il fait une plainte?", "N-8"),            # French question, French note
])
def test_topic_ranking(q, first):
    rows = story.visible(ROWS, q, "analyst")
    assert story._rank(q, rows)[0][1][0] == first


def test_listing_is_most_recent_first_and_never_loads_vectors(monkeypatch):
    monkeypatch.setattr(vectors, "available", lambda: pytest.fail("a listing must not load the model"))
    rows = story.visible(ROWS, "Show the notes history", "analyst")
    assert [r[0] for _, r in story._rank("Show the notes history", rows)] == [r[0] for r in rows]
    assert story.use_vectors("Show the notes history", rows, []) is False


def test_vectors_switch_off(monkeypatch):
    monkeypatch.setenv("L2_VECTORS", "0")
    rows = story.visible(ROWS, "q", "analyst")
    assert story.use_vectors("What did the customer say about money?", rows, []) is False


def test_tiebreak_keeps_keyword_order_and_ranks_misses_by_vectors(monkeypatch):
    rows = story.visible(ROWS, "q", "analyst")
    cos = {"N-1": 0.9, "N-2": 0.1, "N-3": 0.2, "N-6": 0.95, "N-8": 0.3}
    monkeypatch.setattr(vectors, "note_scores", lambda q, rs: {r[0]: cos[r[0]] for r in rs})
    ranked = [r[0] for _, r in story._rank_hybrid("What has the customer said about money problems?", rows)]
    assert ranked[0] == "N-2"                       # the keyword winner stays first despite a low cosine
    found = {r[0] for _, r in story._rank("What has the customer said about money problems?", rows)}
    misses = [n for n in ranked if n not in found]
    assert misses == sorted(misses, key=lambda n: -cos[n])   # the rest follow by meaning


@pytest.fixture
def fake_llm(monkeypatch):
    from layer2 import adapters
    def install(reply):
        monkeypatch.setattr(adapters, "complete", lambda *a, **k: reply)
    return install


TOP = [(1.0, ROWS[1]), (0.9, ROWS[5])]


def test_written_answer_cites_only_retrieved_notes(fake_llm):
    fake_llm({"answer": "Hours were cut and they can only pay 50 [N-2]; a PTP of 120 was set [N-6].",
              "cited": ["N-2", "N-6"], "covered": True})
    assert story.written_answer("What about money problems?", TOP)[0].startswith("Hours were cut")


@pytest.mark.parametrize("reply, problem", [
    ({"answer": "They lost their job [N-9].", "cited": ["N-9"], "covered": True}, "cited notes that were not retrieved"),
    ({"answer": "They can pay 75 next week [N-2].", "cited": ["N-2"], "covered": True}, "numbers not in the notes"),
    ({"answer": "Call them on 416-555-0199 [N-2].", "cited": ["N-2"], "covered": True}, "numbers not in the notes"),
    ({"answer": "Hours were cut.", "cited": [], "covered": True}, "no note cited"),
])
def test_written_answer_guardrails(fake_llm, monkeypatch, reply, problem):
    from layer2 import adapters
    monkeypatch.setattr(adapters, "audit", lambda e: None)
    fake_llm(reply)
    assert story.written_answer("What about money problems?", TOP) == (None, problem)


def test_not_covered_says_so(fake_llm):
    fake_llm({"answer": "", "cited": [], "covered": False})
    assert story.written_answer("Did they mention a lawyer?", TOP)[0] == "The notes do not answer this question."


@pytest.mark.parametrize("text, analyst, agent", [
    ("très anxieux a mentionné aller à l'église", "très [protected] a mentionné aller à l'[protected]",
     "très [protected] a mentionné aller à l'[protected]"),
    ("client vulnérable. client en pleurs.", "client [protected]. client [protected].",
     "client [protected]. client [protected]."),     # vulnerability masked for every role (team rule 2)
    ("spoke to wife; newcomer, pregnant", "spoke to [protected]; [protected], [protected]",
     "spoke to [protected]; [protected], [protected]"),
    ("on medical leave since Feb; laid off; separated; paid with Visa", "on medical leave since Feb; laid off; separated; paid with Visa",
     "on medical leave since Feb; laid off; separated; paid with Visa"),     # hardship reasons and card brands stay
])
def test_protected_information_is_masked(text, analyst, agent):
    assert story.mask(text, "analyst") == analyst and story.mask(text, "agent") == agent


def test_ai_answer_with_protected_information_is_dropped(fake_llm, monkeypatch):
    from layer2 import adapters
    monkeypatch.setattr(adapters, "audit", lambda e: None)
    fake_llm({"answer": "The customer goes to church and is vulnerable [N-2].", "cited": ["N-2"], "covered": True})
    assert story.written_answer("Why can't they pay?", TOP) == (None, "protected information")


def test_mask_tags_are_not_a_leak():
    assert story.protected_leak("They mentioned [vulnerability] and [protected] [N-2].") is False
    assert story.protected_leak("They are vulnerable [N-2].") is True
