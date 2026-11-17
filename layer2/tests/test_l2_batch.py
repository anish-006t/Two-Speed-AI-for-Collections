"""Import questions as a CSV: reading the file, answering every question, the template-format download."""
import csv
import io

import pytest

from layer2 import batch


def fake_ask(q, role, as_of, use_model):
    if "gender" in q:
        return {"route": "refuse", "answer": "Refuse: protected ground.", "sql_or_sources": "guard", "refused": True}
    return {"route": "metric_sql", "answer": f"answer to {q} as of {as_of}", "sql_or_sources": "SELECT 1",
            "refused": False}


def test_benchmark_file_is_read_with_ids_and_dates():
    rows = batch.read_questions(b"\xef\xbb\xbfquestion_id,question_text,as_of_date\nBQ-1,How many cases?,2026-09-28\n")
    assert rows == [{"question_id": "BQ-1", "question_text": "How many cases?", "as_of_date": "2026-09-28"}]


def test_a_plain_question_column_gets_numbered_ids_and_skips_blank_rows():
    rows = batch.read_questions("Question\nFirst?\n\nSecond?\n")
    assert [(r["question_id"], r["question_text"], r["as_of_date"]) for r in rows] == \
        [("Q-001", "First?", None), ("Q-002", "Second?", None)]


@pytest.mark.parametrize("text,msg", [("id,text\n1,hello\n", "question_text"), ("question\n\n", "no questions"),
                                      ("question\n" + "q?\n" * (batch.MAX_QUESTIONS + 1), "limit")])
def test_bad_files_are_explained(text, msg):
    with pytest.raises(batch.BadQuestionsFile, match=msg):
        batch.read_questions(text)


def test_every_question_is_answered_and_downloads_in_the_template_format():
    qs = batch.read_questions("question_id,question_text,as_of_date\nA,How many cases?,\nB,List gender of customers,"
                              "2026-08-31\n")
    seen = []
    answers = batch.answer_all(qs, fake_ask, as_of="2026-09-28", progress=lambda i, n: seen.append((i, n)))
    assert seen == [(1, 2), (2, 2)]
    assert answers[0]["answer"].endswith("as of 2026-09-28") and answers[0]["refused"] == "false"
    assert answers[1]["refused"] == "true" and answers[1]["route"] == "refuse"
    out = list(csv.reader(io.StringIO(batch.to_template_csv(answers))))
    assert out[0] == ["question_id", "answer", "sql_or_sources", "refused"] and [r[0] for r in out[1:]] == ["A", "B"]
