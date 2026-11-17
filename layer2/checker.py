"""Can Layer 2 process these questions? Runs each through ask() and says how it was handled.

Used by the query checker page (layer2/checker_app.py) and usable from the shell:
    uv run python -m layer2.checker "question one" "question two"          # rules only, no API calls
    uv run python -m layer2.checker --model "question"                     # AI allowed where rules fall short
"""
from __future__ import annotations

import sys
import time

from layer2 import adapters
from layer2.ask import ask

# status -> (icon, short label, what it means)
STATUS = {
    "rules": ("✅", "Answered by rules", "Built by code from certified definitions or documents; no AI involved."),
    "rules_checked": ("✅", "Rules, AI-confirmed", "The rules answered; the AI confirmed nothing in the question was left out."),
    "aggregate": ("✅", "Rule-built aggregate", "A plain average / median / total / share / count built by code (not a certified metric)."),
    "ai_extended": ("🤖", "AI-extended SQL", "The rules answered part; the AI extended their SQL. Checked by the SQL guard; not certified."),
    "ai_written": ("🤖", "AI-written SQL", "No rule fitted; the AI wrote the SQL. Checked by the SQL guard; not certified."),
    "ai_notes": ("🤖", "AI answer from notes (cited)", "Written by the AI from the case's notes only; every fact cites a "
                 "note ID, and numbers and personal data were checked."),
    "ai_text": ("🤖", "AI-written answer", "Policy or summary text written by the AI; citations and facts checked by guardrails."),
    "partial": ("⚠️", "Partly answered", "Answered, but the answer says which parts of the question were not applied."),
    "refused": ("🛑", "Refused", "Out of scope or not allowed (protected attributes, personal data, off-topic)."),
    "cannot": ("❌", "Cannot answer", "No rule fits and the AI could not (or was not allowed to) answer."),
}
NEEDS_AI = {"partial", "cannot"}


def classify(out: dict) -> str:
    answer, engines = out.get("answer") or "", str(out.get("engines") or {}).lower()
    if out.get("refused"):
        return "refused"
    if out.get("error") or any(s in answer for s in ("does not match any certified metric", "Could not answer",
                                                       "Could not build a safe query", "AI service busy",
                                                       "internal error")):
        return "cannot"
    if "extended" in engines:
        return "ai_extended"
    if "llm-sql" in engines:
        return "ai_written"
    if "Not applied" in answer:
        return "partial"
    if "llm checked" in engines:
        return "rules_checked"
    if "+llm" in engines and "'story'" in engines:
        return "ai_notes"
    if "'policy': 'llm'" in engines or "'call_summary': 'llm" in engines:
        return "ai_text"
    if "general aggregate" in engines:
        return "aggregate"
    return "rules"


def check(question: str, use_model: bool = False, role: str = "analyst", as_of: str = "2026-09-28") -> dict:
    """One question -> {status, icon, label, route, answer, sql_or_sources, reason, understood, seconds, ai_calls}."""
    before = adapters._budget_left()
    start = time.time()
    out = ask(question, role=role, as_of=as_of, use_model=use_model)
    status = classify(out)
    icon, label, _ = STATUS[status]
    return {"question": question, "status": status, "icon": icon, "label": label, "route": out.get("route", ""),
            "answer": out.get("answer", ""), "sql_or_sources": out.get("sql_or_sources", ""),
            "reason": out.get("reason", ""), "understood": out.get("understood", ""),
            "engines": out.get("engines") or {}, "table": out.get("table"), "read_as": out.get("read_as") or "",
            "seconds": round(time.time() - start, 2), "ai_calls": max(0, before - adapters._budget_left())}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    use_model = "--model" in args
    for q in [a for a in args if a != "--model"]:
        r = check(q, use_model=use_model)
        first = r["answer"].splitlines()[0][:100] if r["answer"] else ""
        print(f"{r['icon']} {r['label']:<22} {r['route']:<12} {q[:70]}\n   {first}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
