"""Ask Collections: the one entry point of Layer 2 (shape in docs/INTERFACES.md, design in layer2/docs/architecture.md).

ask(question) -> fix typing errors (rules) -> parse -> guard -> route -> lane -> answer with its receipt (SQL or
sources), always as a dict: {answer, sql_or_sources, refused, reason, route, table} plus `understood`, `engines`,
`read_as` and `corrections` for the app and the audit log. A lane that cannot answer returns an honest "could not
answer" rather than raising.

Typing errors (layer2/normalize.py, plan "the mix"):
  1. rules fix clear typos in every question, for free ("promis kept rate by queu" -> "promise kept rate by queue");
  2. hard refusals (guard.HARD) are checked on the typed AND the corrected text, so "rank by gendr" is refused and
     a correction can never remove a refusal;
  3. only when the answer is still a soft refusal or "cannot answer", the LLM rewrites the typed question (spelling
     and abbreviations only; it must keep every number and ID) and the question is answered once more, if the
     rewrite changed anything. Hard refusals never reach the AI.
L2_NORMALIZE=0 switches 1 and 3 off (kill switch for the final run).
"""
from __future__ import annotations

import os
import time
import traceback

from layer2 import adapters, guard, llm_guard, normalize, router
from layer2.parse import CASE_ID, DOC_ID, GOLDEN_ID, PTP_ID, parse

CANNOT = ("does not match any certified metric", "Could not build a safe query", "because of an internal error")
REWRITE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["rewritten", "changed"],
                  "properties": {"rewritten": {"type": "string"}, "changed": {"type": "boolean"}}}
REWRITE_SYSTEM = ("You fix typing errors in a question for a bank's collections analytics assistant. Correct "
                  "spelling, expand obvious abbreviations and fix garbled word order. Do not change the meaning; do "
                  "not add, drop or change any condition, number, date, ID or name; do not answer the question. If "
                  "there are no typing errors, return it unchanged with changed=false.")


def _result(answer="", sql_or_sources="", refused=False, reason="", route="", table=None, **extra) -> dict:
    return {"answer": answer, "sql_or_sources": sql_or_sources, "refused": refused, "reason": reason,
            "route": route, "table": table, **extra}


def _lane(route_name: str):
    if route_name == "metric_sql":
        from layer2.semantic import answer_metric
        return answer_metric
    if route_name == "policy":
        from layer2.policy import answer_policy_parsed
        return answer_policy_parsed
    if route_name == "call_summary":
        from layer2.call_summary import answer_call_summary
        return answer_call_summary
    from layer2.lookup import answer_lookup
    return answer_lookup


def _refusal(g) -> dict:
    alts = "; ".join(g.alternatives)
    return _result(answer=g.reason + (f" I can answer instead: {alts}." if alts else ""),
                   sql_or_sources="guard: " + g.category, refused=True, reason=g.reason, route="refuse",
                   engines={"guard": g.engine}, refusal_category=g.category)


def _answer_once(text: str, role: str, as_of: str | None, use_model: bool) -> dict:
    """guard -> route -> lane on one wording of the question."""
    p = parse(text, as_of)
    g = guard.check(text, role, use_model=use_model)
    if g.refused:
        return _refusal(g)
    r = router.route(p, use_model=use_model)
    try:
        out = _lane(r.route)(p, role=role, use_model=use_model)
    except adapters.LLMUnavailable as e:   # every model busy or out of quota: say so plainly, details to the log
        out = _result(answer="Could not answer right now: the AI service is busy or over its daily limit, and "
                             "this question needs it. Please try again shortly or rephrase it as a metric "
                             "(for example 'share of contacts by channel in September 2026').",
                      reason="AI service unavailable", error=str(e)[:500])
    except Exception as e:  # noqa: BLE001 - a broken lane must not crash the runner or show internals
        out = _result(answer="Could not answer this question because of an internal error; it has been logged.",
                      reason="lane error", error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")
    out["route"] = out.get("route") or r.route
    out.setdefault("engines", {})
    out["engines"].update({"guard": g.engine, "router": r.engine})
    if r.prob is not None:
        out["engines"]["router_prob"] = round(r.prob, 3)
    return out


def soft_failure(out: dict) -> bool:
    """A refusal that is not final (off-topic, no document covers it, the data cannot answer it) or a "cannot
    answer": the cases where a typo may be to blame and the AI rewrite is worth one call."""
    if out.get("refused"):
        return out.get("refusal_category") not in guard.HARD
    return any(m in (out.get("answer") or "") for m in CANNOT)


def _ids(text: str) -> set[str]:
    return {m.upper() for rx in (CASE_ID, PTP_ID, GOLDEN_ID, DOC_ID) for m in rx.findall(text)}


def rewrite(question: str) -> str | None:
    """The LLM's spelling-fixed version of the question, or None (no change, or it broke a guardrail: it must
    keep every number and ID, and it must not look like an injection)."""
    out = adapters.complete(llm_guard.wrap_question(question), system=REWRITE_SYSTEM + " " + llm_guard.QUESTION_RULE,
                            json_schema=REWRITE_SCHEMA, max_tokens=300, fast=True)
    text = " ".join(out["rewritten"].split())
    if not out["changed"] or not text or text.lower() == " ".join(question.split()).lower():
        return None
    dropped = llm_guard._numbers(question) - llm_guard._numbers(text)
    texting = {"4": "for", "2": "to"}                    # "due 4 promises" -> "for"; any other lost number is a change
    words_q, words_t = question.lower().split(), text.lower().split()
    dropped = {n for n in dropped if not (n in texting and words_t.count(texting[n]) > words_q.count(texting[n]))}
    if dropped or _ids(question) != _ids(text) or llm_guard.looks_like_injection(text):
        adapters.audit({"action": "guardrail", "lane": "rewrite", "problem": "rewrite changed numbers or IDs",
                        "question": question})
        return None
    return text


def ask(question: str, role: str = "analyst", as_of: str | None = None, use_model: bool = True) -> dict:
    start = time.time()
    enabled = os.environ.get("L2_NORMALIZE", "1") != "0"
    text, fixes = normalize.normalize(question) if enabled else (question, [])
    typed = guard.rule_check(question, role)            # a misspelling cannot hide a hard refusal…
    if typed is not None and typed.category in guard.HARD:
        out = _refusal(typed)
    else:
        out = _answer_once(text, role, as_of, use_model)  # …and the corrected text is checked inside
    read_as, how = (text, "rules") if fixes else (None, None)
    if enabled and soft_failure(out) and use_model and adapters.llm_available():
        try:
            better = rewrite(question)
        except adapters.LLMUnavailable:
            better = None
        if better and better.lower() != text.lower():
            second = _answer_once(better, role, as_of, use_model)
            if not soft_failure(second):
                out, read_as, how = second, better, "llm rewrite"
            else:
                out.setdefault("engines", {})["rewrite"] = "tried, still not answerable"
    if read_as:
        out["read_as"], out["corrections"] = read_as, [list(f) for f in fixes] if how == "rules" else []
        out.setdefault("engines", {})["normalize"] = how
    out["seconds"] = round(time.time() - start, 2)
    out["question"] = question
    adapters.audit({"action": "ask", "role": role, "question": question, "read_as": read_as, "route": out["route"],
                    "refused": out["refused"], "sql_or_sources": out["sql_or_sources"], "answer": out["answer"][:2000],
                    "engines": out.get("engines"), "seconds": out["seconds"], "error": out.get("error")})
    return out


def answer_policy(question: str) -> dict:
    """Policy lane only, same shape as ask(); reused by Layer 4's policy chatbot."""
    from layer2.policy import answer_policy_parsed
    return answer_policy_parsed(parse(question), role="agent")
