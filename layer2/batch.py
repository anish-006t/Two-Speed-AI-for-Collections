"""Answer a CSV of questions in one go (Ask Collections' "Import questions" option).

The CSV needs a question column: `question_text` (the benchmark's name) or `question`, any case. `question_id` and
`as_of_date` are optional. Every question goes through the same `ask()` as the question box, with the same rules,
refusals and AI switch; the answers come back in the benchmark template format
(`question_id, answer, sql_or_sources, refused`) plus `route` for the screen.
"""
from __future__ import annotations

import csv
import io

MAX_QUESTIONS = 200          # one upload; keeps a model run inside the free plan's daily limit
QUESTION_COLUMNS = ("question_text", "question")
TEMPLATE_COLUMNS = ("question_id", "answer", "sql_or_sources", "refused")


class BadQuestionsFile(ValueError):
    pass


def read_questions(data: bytes | str) -> list[dict]:
    """Rows with question_id, question_text and as_of_date (None when absent). Raises BadQuestionsFile."""
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    reader = csv.DictReader(io.StringIO(text))
    fields = {(f or "").strip().lower(): f for f in reader.fieldnames or []}
    qcol = next((fields[c] for c in QUESTION_COLUMNS if c in fields), None)
    if qcol is None:
        raise BadQuestionsFile("The CSV needs a 'question_text' or 'question' column.")
    idcol, ascol = fields.get("question_id"), fields.get("as_of_date")
    rows = []
    for n, r in enumerate(reader, start=1):
        q = (r.get(qcol) or "").strip()
        if not q:
            continue
        rows.append({"question_id": ((r.get(idcol) or "").strip() if idcol else "") or f"Q-{n:03d}",
                     "question_text": q,
                     "as_of_date": ((r.get(ascol) or "").strip() if ascol else "") or None})
    if not rows:
        raise BadQuestionsFile("The CSV has no questions.")
    if len(rows) > MAX_QUESTIONS:
        raise BadQuestionsFile(f"The CSV has {len(rows)} questions; the limit is {MAX_QUESTIONS} per upload.")
    return rows


def answer_all(questions: list[dict], ask, role: str = "agent", as_of: str | None = None, use_model: bool = False,
               progress=None) -> list[dict]:
    """ask() each question; progress(i, n) is called after each one. A question's own as_of_date wins."""
    out = []
    for i, q in enumerate(questions, start=1):
        res = ask(q["question_text"], role=role, as_of=q.get("as_of_date") or as_of, use_model=use_model)
        out.append({"question_id": q["question_id"], "question": q["question_text"], "route": res.get("route", ""),
                    "answer": res.get("answer") or "", "sql_or_sources": res.get("sql_or_sources") or "",
                    "refused": "true" if res.get("refused") else "false"})
        if progress:
            progress(i, len(questions))
    return out


def to_template_csv(answers: list[dict]) -> str:
    """The answers in the benchmark template format (UTF-8, RFC 4180 quoting)."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=TEMPLATE_COLUMNS, extrasaction="ignore", quoting=csv.QUOTE_MINIMAL)
    w.writeheader()
    w.writerows(answers)
    return buf.getvalue()
