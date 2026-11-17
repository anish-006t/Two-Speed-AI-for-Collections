"""LLM-written SQL for what the rules cannot express, behind the same guards (the uncertified lane).

Two uses, one call each (plus one retry with the error message):
  - write: no rule plan fits the question -> the model writes one SELECT over the safe views;
  - extend: a rule plan answers part of the question and coverage.gaps() lists the rest -> the model gets the
    rule-built SQL and the missing parts, and either extends the SQL or says the SQL already answers the question.
The model sees only the schema card (views, column types, real category values, join keys, the certified metric
formulas), never customer rows. Its SQL passes sql_guard (one SELECT, safe views, no personal or protected columns,
row limit), runs on a read-only connection, and the result is scanned for personal data. Answers are labelled
"not a certified metric". Without a model the caller answers from rules and says what was not applied.
"""
from __future__ import annotations

from layer2 import adapters, llm_guard, sql_guard

VIEWS = ("f_cases", "f_contacts", "f_promises", "f_accounts", "f_account_month", "f_assignments", "f_deposits")
JOINS = ("case_id links f_cases, f_contacts, f_promises and f_assignments; account_id links f_accounts and "
         "f_account_month; golden_customer_id links every view to one customer (count customers with it).\n"
         "TIME: f_cases and f_accounts describe TODAY (the snapshot): current_dpd, current_bucket, dpd, balance, "
         "is_open are values now, so they cannot give a past month's value; f_account_month has month-end history "
         "(use month_end_lenient). Events carry their own dates: contact_ts_local/contact_date, ptp_due_date, "
         "ptp_created_ts, paid_date, case_open_date, cure_date, chargeoff_date.")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "sql", "explanation"],
          "properties": {"verdict": {"type": "string", "enum": ["answer", "already_correct", "cannot"]},
                         "sql": {"type": "string"}, "explanation": {"type": "string"}}}
SYSTEM = """You write one DuckDB SELECT query over the Layer 2 views listed, to answer a collections analytics question.
Rules:
- use only the views and columns listed; join on the keys given; CTEs and window functions are fine;
- when the question names a certified metric, use its formula and always-on filters exactly as listed;
- COUNTING: f_cases has one row per case, f_contacts one per contact, f_promises one per promise. "How many cases"
  (or contacts, promises) is count(*). Use count(DISTINCT golden_customer_id) ONLY when the question asks about
  customers, clients, people or borrowers;
- rates as round(100.0 * avg(CASE WHEN ... THEN 1 ELSE 0 END), 2); round other decimals to 2;
- relative dates ("this month", "last month", "today") are relative to the snapshot date given, never the clock;
- category values must be ones listed (exact spelling); group and order by what the question asks; at most 50 rows;
- a change between periods: show each period's value and the difference; if the data has no history for that value,
  reply "cannot" and say why rather than reinterpreting the question;
- the "sql" field holds only SQL (no comments or prose); explanations go in "explanation".
Reply verdict "answer" with the SQL; "already_correct" (empty sql) only when you are given a SQL that already answers
the whole question; "cannot" (empty sql) when the views cannot answer it, explaining what is missing."""

_CARD: dict = {}


def schema_card(con) -> str:
    """Views with column types and real category values, join keys, and the certified metric formulas."""
    key = id(con)
    if key in _CARD:
        return _CARD[key]
    from layer2.semantic import _menu_rows, catalog
    lines = []
    for t in VIEWS:
        cols = con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = "
                           "'layer2' AND table_name = ? ORDER BY ordinal_position", [t]).fetchall()
        if not cols:
            continue
        values: dict[str, list] = {}
        for field, value in _menu_rows(con, t):
            values.setdefault(field, []).append(value)
        parts = []
        for c, d in cols:
            v = values.get(c)
            parts.append(f"{c} {d.lower()}" + (f" [{', '.join(map(str, v[:12]))}{', ...' if len(v) > 12 else ''}]"
                                               if v else ""))
        lines.append(f"layer2.{t}(" + ", ".join(parts) + ")")
    lines.append("JOINS: " + JOINS)
    lines.append("CERTIFIED METRICS (name: view, value, always-on filters):")
    for k, m in catalog()["measures"].items():
        if m.get("certified") and m.get("expr"):
            lines.append(f"- {m.get('label', k)}: {m['view']}, {m['expr']}, where {' AND '.join(m.get('where', [])) or '-'}")
    _CARD[key] = "\n".join(lines)
    return _CARD[key]


def run(p, con, base_sql: str | None = None, missing: list[str] | None = None) -> dict:
    """One guarded LLM query. Returns {"status": "ok"|"already_correct"|"cannot"|"failed", "columns", "rows", "sql",
    "explanation"}; raises adapters.LLMUnavailable when no model can be reached."""
    from layer2.semantic import to_csv
    prompt = f"Snapshot date: {p.as_of}\n\nVIEWS\n{schema_card(con)}\n\n{llm_guard.wrap_question(p.question)}"
    if base_sql:
        prompt += (f"\n\nA rule-based planner wrote this SQL for the question:\n{base_sql}\n\nIt does NOT apply: "
                   f"{'; '.join(missing or [])}.\nExtend the SQL so it answers the whole question, keeping the metric "
                   "formula and filters it already has, or reply already_correct if those parts do not change the "
                   "answer.")
    error = ""
    for _ in range(2):   # one retry with the error message
        out = adapters.complete(prompt + (f"\n\nYour previous query failed: {error}" if error else ""),
                                system=SYSTEM + "\n" + llm_guard.QUESTION_RULE, json_schema=SCHEMA, max_tokens=2000)
        if out["verdict"] == "already_correct" and base_sql:
            return {"status": "already_correct", "explanation": out["explanation"]}
        if out["verdict"] != "answer" or not out["sql"].strip():
            return {"status": "cannot", "explanation": out["explanation"]}
        ok, problem, sql = sql_guard.check(out["sql"])
        if not ok:
            error = problem
            continue
        try:
            cur = con.execute(sql)
        except Exception as e:  # noqa: BLE001 - fed back once
            error = str(e).splitlines()[0][:300]
            continue
        cols, rows = [d[0] for d in cur.description], cur.fetchall()
        leaks = llm_guard.pii_leaks(to_csv(cols, rows))
        if leaks:   # the views hold no personal data, so this should never fire; if it does, show nothing
            adapters.audit({"action": "guardrail", "lane": "freeform", "problem": f"personal data in result {leaks}"})
            return {"status": "failed", "explanation": "the result looked like it contained personal data", "sql": sql}
        return {"status": "ok", "columns": cols, "rows": rows, "sql": sql, "explanation": out["explanation"]}
    return {"status": "failed", "explanation": error}
