"""Redaction removes direct identifiers before anything else reads free text (CLAUDE.md rule 4).

Synthetic sentences only. Skipped when Presidio or the spaCy model is not installed.
"""
import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_md")

from layer1.pipeline import redact  # noqa: E402


@pytest.fixture(scope="module")
def engines():
    return redact.build_engines()


TEXTS = [
    "Spoke with Quilla Zarnow, call back on 555-201-0199 or 1 (555) 201 0188.",
    "Email update sent to q.zarnow@example.ca, mailing address postal K1A 0B1.",
    "Customer quoted card 4520 123 4567 8901 and account CHQ-778201, CRM ref ABC-1234.",
    "Promised 250.00 on 2026-10-15; hardship mentioned, reduced hours.",
    None,
]


def test_identifiers_are_removed(engines):
    out, counts = redact.redact_texts(*engines, TEXTS, processes=1)
    joined = " ".join(t for t in out if t)
    for value in ["Zarnow", "555-201-0199", "201 0188", "q.zarnow@example.ca", "K1A 0B1", "4520 123 4567 8901",
                  "CHQ-778201", "ABC-1234"]:
        assert value not in joined
    assert redact.leak_counts(out) == {k: 0 for k in redact.LEAK_PATTERNS}


def test_amounts_and_dates_survive(engines):
    out, _ = redact.redact_texts(*engines, TEXTS, processes=1)
    assert "250.00" in out[3] and "2026-10-15" in out[3] and "hardship" in out[3]


def test_empty_text_passes_through(engines):
    out, counts = redact.redact_texts(*engines, TEXTS, processes=1)
    assert out[4] is None and counts[4] == "{}"
