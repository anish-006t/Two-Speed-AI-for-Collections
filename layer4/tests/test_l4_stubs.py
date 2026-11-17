"""The Layer 3 interface stubs return exactly the shapes in docs/INTERFACES.md (and keep doing so once real)."""
import pytest

from layer3.text.extract import extract
from layer4.models.promise_break.score import score_promise


def test_extract_shape():
    out = extract("I lost my job, but I can pay $120.50 on Friday.", "turn")
    assert out
    for qid, a in out.items():
        assert set(a) == {"answer", "prob", "evidence", "model_version"}, qid
        assert isinstance(a["answer"], (str, float, int, bool, type(None))), qid
        assert 0.0 <= a["prob"] <= 1.0 and isinstance(a["evidence"], str) and a["model_version"]


def test_extract_stub_flags_hardship_and_promise():
    out = extract("I lost my job, but I can pay $120.50 on Friday.", "turn")
    assert out["hardship"]["answer"] == "clear" and out["hardship"]["evidence"].lower() == "lost my job"
    assert out["promise_mentioned"]["answer"] is True
    assert out["promise_amount"]["answer"] == 120.50
    assert extract("Thanks, bye.", "turn")["hardship"]["answer"] == "none"


def test_extract_rejects_unknown_source_type():
    with pytest.raises(ValueError):
        extract("x", "email")


def test_score_promise_shape(tmp_path, monkeypatch):
    """The interface shape, on the baseline path (no trained artifact), so it doesn't depend on Layer 3's build."""
    from layer4.models.promise_break import score
    monkeypatch.setattr(score, "MODEL_PATH", tmp_path / "absent.txt")
    out = score_promise("G-1", 500.0, "2026-10-20", "2026-10-03")
    assert set(out) == {"risk", "reasons", "safer_date", "model_version"}
    assert 0.0 <= out["risk"] <= 1.0 and len(out["reasons"]) == 3 and all(isinstance(r, str) for r in out["reasons"])
    assert out["safer_date"] is None or len(out["safer_date"]) == 10
