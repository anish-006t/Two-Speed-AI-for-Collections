"""How an answer is shown: headline, "understood as" chips, data products used, follow-up questions, AI insight.

Everything here is built by rules from the answer itself (its plan, table and SQL), except `insight()`, which asks
the LLM for two sentences about the result table and keeps them only if every number they contain is in the table.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from layer2 import adapters, llm_guard

SOURCES = {   # what each Layer 2 view is built from (layer2.build); gold.* when Layer 1's gold build exists
    "f_cases": ["gold.cases"], "f_contacts": ["gold.contact_timeline"],
    "f_promises": ["gold.promises_to_pay", "bronze.model_scores (ptp_break)"],
    "f_accounts": ["gold.c360_accounts"], "f_account_month": ["bronze.account_monthly_snapshot"],
    "f_assignments": ["bronze.case_assignment_history"], "f_deposits": ["bronze.deposit_accounts"],
    "transcript_index": ["bronze.call_transcripts", "silver.transcript_turns_redacted"],
}
ENTITY = {"f_promises": "promises", "f_cases": "cases", "f_accounts": "accounts"}


def _plan(out: dict):
    return out.get("plan")


def _is_list(out: dict) -> bool:
    gp = _plan(out)
    return getattr(gp, "agg", None) == "list"


def _num(v) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:,}" if isinstance(v, int) else str(v)


def headline(out: dict) -> str | None:
    """One bold line above the table: '8 open promises most likely to break · $12,990 at risk'."""
    table = out.get("table")
    if out.get("refused") or not isinstance(table, list) or not table or out.get("route") != "metric_sql":
        return None
    gp, cols = _plan(out), list(table[0])
    if _is_list(out):
        what = ("open " if "ptp_status = 'open'" in " ".join(gp.where) else "") + ENTITY.get(gp.view, "records")
        rank = "most likely to break" if gp.column == "break_prob" else \
            f"with the {'highest' if gp.order == 'desc' else 'lowest'} {gp.column.replace('_', ' ')}"
        line = f"{len(table)} {what} {rank}"
        if "ptp_amount" in cols:
            line += f" · ${sum(r['ptp_amount'] or 0 for r in table):,.0f} at risk"
        if gp.period_sql:
            line += f" · {_period_words(gp.period_sql)}"
        return line
    nums = [c for c in cols if isinstance(table[0][c], (int, float)) and not isinstance(table[0][c], bool)]
    if not nums:
        return None
    value = nums[0]
    label = value.replace("_pct", " (%)").replace("_cad", " ($)").replace("_", " ")
    if len(table) == 1:
        return f"{label}: {_num(table[0][value])}"
    keys = [c for c in cols if c not in nums]
    if not keys:
        return None
    rows = [r for r in table if r[value] is not None]
    if not rows:
        return None
    hi, lo = max(rows, key=lambda r: r[value]), min(rows, key=lambda r: r[value])
    name = lambda r: " / ".join(str(r[k]) for k in keys)  # noqa: E731
    return f"{label} by {', '.join(keys)}: highest {name(hi)} ({_num(hi[value])}), lowest {name(lo)} ({_num(lo[value])})"


def _period_words(period_sql: str) -> str:
    d = re.findall(r"DATE '(\d{4}-\d{2}-\d{2})'", period_sql)
    if len(d) == 2:
        a, b = date.fromisoformat(d[0]), date.fromisoformat(d[1]) - timedelta(days=1)
        return f"{a:%-d %b} – {b:%-d %b %Y}"
    return ""


def chips(out: dict) -> list[tuple[str, str]]:
    """'Understood as' tokens: (kind, text), kind in metric | filter | period | sort | limit | group."""
    gp, out_chips = _plan(out), []
    if gp is None:
        return [("metric", out.get("route", "").replace("_", " "))] if out.get("route") else []
    if hasattr(gp, "measure"):   # certified plan
        from layer2.semantic import catalog
        m = catalog()["measures"][gp.measure]
        out_chips.append(("metric", m.get("label", gp.measure)))
        out_chips += [("group", f"by {d}") for d in gp.dimensions]
        out_chips += [("filter", f) for f in gp.filters]
        out_chips += [("filter", f"{k} = {', '.join(v)}") for k, v in gp.value_filters.items()]
        if gp.compare:
            out_chips.append(("period", " vs ".join(p.label for p in gp.compare)))
        elif gp.period:
            out_chips.append(("period", f"{gp.period.start:%-d %b} – {gp.period.end:%-d %b %Y}"))
        if gp.order:
            out_chips.append(("sort", "highest first" if gp.order == "desc" else "lowest first"))
        if gp.limit:
            out_chips.append(("limit", f"top {gp.limit}"))
        return out_chips
    what = {"list": f"{ENTITY.get(gp.view, gp.view)}", "count": "count", "avg": f"average {gp.column}",
            "median": f"median {gp.column}", "sum": f"total {gp.column}",
            "share": f"share where {gp.condition_label}"}[gp.agg]
    out_chips.append(("metric", what.replace("_", " ")))
    out_chips += [("group", f"by {d}") for d in gp.dims]
    out_chips += [("filter", w.replace("'", "")) for w in gp.where]
    if gp.period_sql:
        out_chips.append(("period", _period_words(gp.period_sql)))
    if gp.agg == "list":
        out_chips.append(("sort", f"by {gp.column.replace('_', ' ')} {'↓' if gp.order == 'desc' else '↑'}"))
    if gp.limit:
        out_chips.append(("limit", f"top {gp.limit}"))
    return out_chips


def data_products(out: dict) -> list[str]:
    """'layer2.f_promises ← gold.promises_to_pay, bronze.model_scores' for each view the SQL reads."""
    sql = out.get("sql_or_sources") or ""
    views = list(dict.fromkeys(re.findall(r"layer2\.([a-z_]+)", sql)))
    products = [f"layer2.{v} ← {', '.join(SOURCES.get(v, ['Layer 1']))}" for v in views]
    if "agent_notes_redacted" in sql:
        story = (out.get("engines") or {}).get("story", "")
        products.append("silver.agent_notes_redacted (Layer 1, redacted)" +
                        (" + lake/layer2_vectors.duckdb (e5 note vectors)" if "vectors" in story else ""))
    return products


def follow_ups(out: dict, question: str) -> list[str]:
    """Up to four next questions the rules can answer, built from the plan and the result."""
    table, gp, ups = out.get("table"), _plan(out), []
    if out.get("refused") or not isinstance(table, list) or not table:
        return ups
    q = question.rstrip(" ?.")
    if _is_list(out):
        if table[0].get("golden_customer_id"):
            ups.append(f"Why is {table[0]['golden_customer_id']} high risk?" if gp.column == "break_prob"
                       else f"Show case {table[0].get('case_id')}" if table[0].get("case_id") else "")
        if gp.view == "f_promises":
            when = _period_words(gp.period_sql) if gp.period_sql else ""
            ups.append("What is the total promised amount of open promises by queue" +
                       (f" due between {when.replace(' – ', ' and ')}" if when else "") + "?")
            ups.append("What is the broken promise rate by queue for promises due last month?")
        if gp.view == "f_cases":
            ups.append("Average total overdue of open cases by queue")
        return [u for u in ups if u][:4]
    if hasattr(gp, "measure"):
        from layer2.semantic import catalog
        view = catalog()["views"][gp.view]
        unused = [k for k in view.get("dimensions", {}) if k not in gp.dimensions and k not in ("month", "date")]
        splits = []
        for k in unused:
            phrase = next((ph for ph in view["dimensions"][k].get("phrases", []) if ph.startswith("by ")), None)
            if phrase:
                splits.append(f"{q}, split {phrase}" if gp.dimensions else f"{q} {phrase}")
        if gp.period and not gp.compare:
            prev = _previous(gp.period)
            if prev:
                ups.append(f"{catalog()['measures'][gp.measure].get('label', gp.measure)} "
                           f"{' '.join('by ' + d for d in gp.dimensions)} {prev} vs {gp.period.label}".replace("  ", " "))
        if "hardship" in view.get("filters", {}) and "hardship" not in gp.filters:
            ups.append(f"{q} for hardship cases")
        ups += splits
    return ups[:6]


def _previous(period) -> str | None:
    """The calendar month before a one-month period, written so the parser reads it ("August 2026")."""
    s, e = period.start, period.end
    if s.day == 1 and (e + timedelta(days=1)).day == 1 and s.month == e.month:
        p = (s - timedelta(days=1)).replace(day=1)
        return f"{p:%B %Y}"
    return None


INSIGHT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["insight"],
                  "properties": {"insight": {"type": "string"}}}


def insight(out: dict, question: str) -> tuple[str | None, str]:
    """Two sentences about the result, written by the LLM from the table only. Returns (text, problem): text is
    None when the model is unavailable or a guardrail failed (any number not in the table, personal data)."""
    from layer2.semantic import to_csv
    table = out.get("table")
    if not isinstance(table, list) or not table:
        return None, "no table"
    csv_text = to_csv(list(table[0]), [list(r.values()) for r in table[:30]])
    try:
        r = adapters.complete(f"{llm_guard.wrap_question(question)}\n\nUnderstood as: {out.get('understood', '')}\n\n"
                              f"HEADLINE: {headline(out) or '-'}\n\nRESULT\n{csv_text}",
                              system="In at most two short sentences, say what stands out in this result for a "
                                     "collections manager. Quote numbers exactly as they appear in the result or "
                                     "headline; do not compute new totals, averages or percentages; do not "
                                     "speculate about causes or individuals. " + llm_guard.QUESTION_RULE,
                              json_schema=INSIGHT_SCHEMA, max_tokens=300, fast=True)
    except adapters.LLMUnavailable as e:
        return None, str(e)[:120]
    text = r["insight"].strip()
    bad = llm_guard.ungrounded_numbers(text, [csv_text, headline(out) or ""], question)
    if bad or llm_guard.pii_leaks(text):
        adapters.audit({"action": "guardrail", "lane": "insight", "problem": f"numbers not in table {sorted(bad)}"})
        return None, "the AI insight used numbers that are not in the result, so it was not shown"
    return text, ""


def checked_follow_ups(out: dict, question: str, role: str = "analyst", as_of: str | None = None,
                       k: int = 4) -> list[str]:
    """Follow-ups that the rules answer cleanly (no refusal, a table, nothing "Not applied") with a result that
    differs from the current one. Each check is a rules-only run (milliseconds, no API call)."""
    from layer2.ask import ask
    keep, current = [], headline(out)
    for f in follow_ups(out, question):
        r = ask(f, role=role, as_of=as_of, use_model=False)
        if r["refused"] or "Not applied" in (r.get("answer") or "") or not r.get("table"):
            continue
        if headline(r) is not None and headline(r) == current:
            continue
        keep.append(f)
        if len(keep) == k:
            break
    return keep
