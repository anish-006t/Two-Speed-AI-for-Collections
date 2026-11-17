"""Lookup lane (architecture §6): facts about one case or promise, and the uncertified fallback.

Entity cards are fixed SQL templates over the safe views (no personal data). Questions that fit no certified metric
go to the uncertified fallback: the LLM writes SQL over the safe views only, the guard checks it, it runs read-only
with one retry on error, and the answer is labelled "not a certified metric". No LLM -> an honest "can't answer".
"""
from __future__ import annotations

import re

from layer2 import adapters
from layer2.parse import Parsed
from layer2.semantic import to_csv

CASE_CARD = """SELECT case_id, queue, primary_product, current_bucket, current_dpd, is_open, outcome, hardship_flag,
       test_cell, strategy_code, current_treatment, total_overdue, contact_attempts_total, case_open_date
FROM layer2.f_cases WHERE case_id = ?"""
PROMISE_CARD = """SELECT ptp_id, case_id, product, ptp_amount, ptp_due_date, ptp_status, amount_paid_against, paid_date,
       prior_broken_ptp_count, agent_site
FROM layer2.f_promises WHERE ptp_id = ?"""


RISK_CARD = """SELECT ptp_id, case_id, product, ptp_amount, ptp_due_date, ptp_status, prior_broken_ptp_count, break_prob,
       break_reason_1, break_reason_2, break_reason_3, break_score_date
FROM layer2.f_promises WHERE {key} = ? AND break_prob IS NOT NULL
ORDER BY ptp_status = 'open' DESC, break_prob DESC LIMIT 5"""
RISK_QUESTION = r"\bwhy\b|\brisk|\breason|likely to break|break probability"


def answer_risk(p: Parsed, con) -> dict | None:
    """"Why is GC00001753 high risk?": the customer's (or promise's) promises with the bank model's break
    probability and its own three reasons. Facts only; the reasons are the model's text, not ours."""
    key, value = ("golden_customer_id", p.golden_ids[0]) if p.golden_ids else (
        ("ptp_id", p.ptp_ids[0]) if p.ptp_ids else (None, None))
    if key is None:
        return None
    sql = RISK_CARD.format(key=key)
    cur = con.execute(sql, [value])
    cols, rows = [d[0] for d in cur.description], cur.fetchall()
    shown = sql.replace("?", f"'{value}'")
    if not rows:
        return {"answer": f"No scored promises found for {value}.", "sql_or_sources": shown, "refused": False,
                "reason": "no break score", "route": "lookup", "table": None}
    top = dict(zip(cols, rows[0]))
    reasons = [top[f"break_reason_{i}"] for i in (1, 2, 3) if top[f"break_reason_{i}"]]
    n_open = sum(1 for r in rows if r[cols.index("ptp_status")] == "open")
    text = (f"{value}: {n_open} open promise(s) scored. Highest break probability {top['break_prob']:.2f} on "
            f"{top['ptp_id']} (${top['ptp_amount']:,.0f} due {top['ptp_due_date']}, status {top['ptp_status']}). "
            f"The bank's promise-break model gives these reasons: " + "; ".join(reasons) + ". "
            f"Prior broken promises: {top['prior_broken_ptp_count']}. Model scored on {top['break_score_date']}; "
            "it does not use protected attributes. A score is a prompt for the agent, not a decision.")
    said = [int(n) for r in reasons for n in re.findall(r"(\d+) broken ptp", r.lower())]
    if said and top["prior_broken_ptp_count"] is not None and any(n != top["prior_broken_ptp_count"] for n in said):
        text += (f" Caution: the model's reasons mention {', '.join(map(str, said))} broken promises, but the promise "
                 f"record shows {top['prior_broken_ptp_count']}; check the record before relying on the reasons.")
    return {"answer": text, "sql_or_sources": shown + "\n-- source: bronze.model_scores (ptp_break) via layer2.f_promises",
            "refused": False, "reason": "break-risk facts from the bank's model", "route": "lookup",
            "table": [dict(zip(cols, r)) for r in rows], "engines": {"lookup": "rules (risk card)"}}


def answer_uncertified(p: Parsed, role: str = "analyst", use_model: bool = True, con=None) -> dict:
    """No rule plan fits: the LLM writes one guarded SELECT (layer2.freeform), labelled not certified."""
    from layer2 import freeform
    own = con is None
    con = con or adapters.connect(read_only=True)
    try:
        if not (use_model and adapters.llm_available()):
            return {"answer": "This question does not match any certified metric or rule-built aggregate, and the "
                              "AI query helper is not available, so I can't answer it reliably.", "sql_or_sources": "",
                    "refused": False, "reason": "no rule plan and no LLM", "route": "lookup", "table": None}
        try:
            r = freeform.run(p, con)
        except adapters.LLMUnavailable as e:   # not cached and no calls left today: an honest "can't answer"
            return {"answer": "This question does not match any certified metric or rule-built aggregate, and the "
                              "AI query helper cannot be reached right now, so I can't answer it reliably.",
                    "sql_or_sources": "", "refused": False, "reason": f"AI unavailable: {str(e)[:120]}",
                    "route": "lookup", "table": None}
        if r["status"] == "cannot":
            reason = "Refuse: the data cannot answer this (no matching metric or columns). " + r["explanation"]
            return {"answer": reason, "sql_or_sources": "", "refused": True, "reason": reason, "route": "lookup",
                    "table": None, "engines": {"lookup": "llm-sql"}}
        if r["status"] != "ok":
            return {"answer": f"Could not build a safe query for this question ({r['explanation']}).",
                    "sql_or_sources": r.get("sql", ""), "refused": False, "reason": r["explanation"],
                    "route": "lookup", "table": None, "engines": {"lookup": "llm-sql"}}
        text = to_csv(r["columns"], r["rows"]) + "\n\nNote: not a certified metric; query written by the AI and " \
                                                 "checked by the SQL guard."
        return {"answer": text, "sql_or_sources": r["sql"], "refused": False, "reason": r["explanation"],
                "route": "lookup", "table": [dict(zip(r["columns"], row)) for row in r["rows"]],
                "engines": {"lookup": "llm-sql"}}
    finally:
        if own:
            con.close()


def answer_lookup(p: Parsed, role: str = "analyst", use_model: bool = True) -> dict:
    from layer2.story import answer_story, is_story_question
    if is_story_question(p):
        return answer_story(p, role=role, use_model=use_model)
    con = adapters.connect(read_only=True)
    try:
        if (p.golden_ids or p.ptp_ids) and re.search(RISK_QUESTION, p.question.lower()):
            out = answer_risk(p, con)
            if out:
                return out
        for ids, sql in ((p.case_ids, CASE_CARD), (p.ptp_ids, PROMISE_CARD)):
            if ids:
                cur = con.execute(sql, [ids[0]])
                cols, rows = [d[0] for d in cur.description], cur.fetchall()
                if not rows:
                    return {"answer": f"No record found for {ids[0]}.", "sql_or_sources": sql.replace("?", f"'{ids[0]}'"),
                            "refused": False, "reason": "not found", "route": "lookup", "table": None}
                return {"answer": to_csv(cols, rows), "sql_or_sources": sql.replace("?", f"'{ids[0]}'"),
                        "refused": False, "reason": f"facts of {ids[0]}", "route": "lookup",
                        "table": [dict(zip(cols, r)) for r in rows]}
        return answer_uncertified(p, role=role, use_model=use_model, con=con)
    finally:
        con.close()
