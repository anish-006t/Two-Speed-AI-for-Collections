"""Metric lane (architecture §3a-3e): question -> QueryPlan (menu choices) -> SQL (code) -> guarded run -> answer.

The plan is filled slot by slot: phrase rules from the catalog first, then a typed decision (Laya) over the menu,
then the LLM with a schema restricted to the menu. No model ever writes the SQL of a certified metric.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from layer2 import adapters, llm_guard, sql_guard
from layer2.adapters import REPO
from layer2.parse import Parsed, Period

CATALOG = Path(__file__).with_name("catalog.yaml")
METRIC_DEFINITIONS = REPO / "data" / "raw" / "maple_collections_release" / "data" / "metric_definitions.csv"


@lru_cache(maxsize=1)
def catalog() -> dict:
    cat = yaml.safe_load(CATALOG.read_text())
    defs = {}
    if METRIC_DEFINITIONS.exists():
        with METRIC_DEFINITIONS.open(newline="", encoding="utf-8") as f:
            defs = {r["metric_id"]: r for r in csv.DictReader(f)}
    for mid, m in cat["measures"].items():
        d = defs.get(mid)
        m["id"] = mid
        m["definition"] = d["formula"] if d else None
        m["label"] = d["metric_name"] if d else mid.replace("_", " ")
        if d:   # certified synonyms are cues too
            m["cues"] = list(dict.fromkeys(m.get("cues", []) + [s.lower() for s in yaml.safe_load(d["synonyms"]) or []]
                                           + [d["metric_name"].lower()]))
    return cat


@dataclass
class QueryPlan:
    measure: str
    view: str
    dimensions: list = field(default_factory=list)          # dimension keys of the view
    filters: list = field(default_factory=list)             # filter keys of the view
    value_filters: dict = field(default_factory=dict)       # column -> [values]
    period: Period | None = None
    compare: list = field(default_factory=list)             # two periods -> a "period" dimension
    time_key: str | None = None
    variant: str | None = None                              # e.g. count_and_amount, assignments
    within_days: int | None = None
    order: str | None = None                                # "desc" for most/highest, "asc" for least/lowest
    limit: int | None = None                                # "top 5", "which 3 queues"
    engines: dict = field(default_factory=dict)             # slot -> engine that filled it

    def understood(self) -> str:
        m = catalog()["measures"][self.measure]
        parts = [f"metric: {m['label']}" + ("" if m.get("certified") else " (plain aggregate, not a certified metric)")]
        if self.dimensions:
            parts.append("by " + ", ".join(self.dimensions))
        if self.filters or self.value_filters:
            parts.append("filters: " + ", ".join(self.filters + [f"{k} in {v}" for k, v in self.value_filters.items()]))
        if self.compare:
            parts.append("compare: " + " vs ".join(p.label for p in self.compare))
        elif self.period:
            parts.append(f"period: {self.period.start} to {self.period.end} ({self.time_key})")
        if self.within_days:
            parts.append(f"within {self.within_days} days")
        return "; ".join(parts)


# --------------------------------------------------------------------------------------------- planning

def _has(text: str, phrase: str) -> bool:
    """Whole-word phrase match: 'cases open' does not match 'cases opened'."""
    return re.search(r"(?<![\w-])" + re.escape(phrase) + r"(?:s|es)?(?![\w-])", text) is not None


def _score(text: str, phrases) -> int:
    return sum(len(p) for p in phrases if _has(text, p))


def choose_measure(p: Parsed, use_model: bool) -> tuple[str | None, str, float | None]:
    q = p.question.lower()
    # a metric named in full ("cure rate") beats one that only shares a word with the question ("hardship cases")
    measures = catalog()["measures"]
    matched = {mid: [c for c in m.get("cues", []) if _has(q, c)] for mid, m in measures.items()}

    def named(mid: str) -> bool:   # "PTP rate" inside "broken PTP rate" names the broken-promise rate, not PTP rate
        label = measures[mid]["label"].lower()
        return _has(q, label) and not any(label in c and len(c) > len(label)
                                          for other, cs in matched.items() if other != mid for c in cs)
    scores = {mid: sum(len(c) for c in matched[mid]) + (100 if named(mid) else 0) for mid in measures}
    best = max(scores.values())
    top = [mid for mid, s in scores.items() if s == best and s > 0]
    if len(top) == 1:
        return top[0], "rules", None
    if not top:          # nothing matched: the LLM planner (answer_metric) gets the whole question in one call
        return None, "rules", None
    pool = top
    if use_model:
        criteria = {mid: (catalog()["measures"][mid]["label"] + ": " + ", ".join(catalog()["measures"][mid]["cues"][:3]))[:120]
                    for mid in pool}
        criteria["none"] = "none of these metrics fits the question"
        try:
            d = adapters.decide(p.question, {"metric": {"type": "choice", "instructions":
                                                        "Which collections metric does the question ask for?",
                                                        "criteria": criteria}}, engines=("llm",))["metric"]
            if d["answer"] in catalog()["measures"]:
                return d["answer"], d["engine"], d["prob"]
            return None, d["engine"], d["prob"]
        except adapters.DecisionUnavailable:
            pass
    return (top[0] if top else None), "rules-tiebreak", None


def _matched_spans(q: str, phrases) -> list[tuple[int, int, str]]:
    out = []
    for ph in phrases:
        for m in re.finditer(r"(?<![\w-])" + re.escape(ph) + r"(?:s|es)?(?![\w-])", q):
            out.append((m.start(), m.end(), ph))
    return out


# A breakdown is asked with a grouping word before the dimension's noun: "by the channel the promise came through",
# "for each collections queue", "split by product", "each agent site", "which province", "give it by province".
GROUP_STRONG = (r"(?:by|per|for each|for every|in each|for all|each|every|across(?: all)?|split by|split|broken down by|"
                r"grouped by|break(?:ing)? (?:it |this )?down by|breakdown by)")
GROUP_ASK = r"(?:which|what)"
# a word between the grouping word and the noun ("each collections queue"); never a period or linking word, so
# "by channel this month" is one breakdown, not two
FILLER = (r"(?!(?:this|last|next|previous|that|these|those|in|on|of|for|and|or|to|at|with|from|during|per|by|"
          r"month|week|year|day|quarter|today)\b)[a-z-]+")


def _dim_nouns(key: str, dim: dict) -> set[str]:
    nouns = {key.replace("_", " ")}
    if re.fullmatch(r"[a-z_]+", dim["column"]):
        nouns.add(dim["column"].replace("_", " "))
    for ph in dim.get("phrases", []):
        nouns.add(re.sub(r"^(?:by|per|each)\s+", "", ph))
    return {n for n in nouns if len(n) >= 3}


def choose_dimensions(q: str, view: dict) -> list[str]:
    """Longest phrase wins, so 'by primary product' beats 'by product'. "Which queue has…" means "by queue"."""
    q = re.sub(r"\bwhich (?:\d+ )?(?=[a-z])", "by ", q)
    hits = []
    for key, dim in view.get("dimensions", {}).items():
        for a, b, ph in _matched_spans(q, dim.get("phrases", [])):
            hits.append((b - a, a, b, key))
        for noun in _dim_nouns(key, dim):
            n = re.escape(noun) + r"(?:s|es)?(?![\w-])"
            for rx in (rf"\b{GROUP_STRONG}\s+(?:(?:the|our|their|its|a|an|\d+)\s+)?(?:{FILLER}\s+){{0,2}}?({n})",
                       rf"\b{GROUP_ASK}\s+(?:\d+\s+)?(?:{FILLER}\s+)?({n})"):
                for m in re.finditer(rx, q):
                    hits.append((m.end(1) - m.start(1), m.start(1), m.end(1), key))
    chosen, used = [], []
    for _, a, b, key in sorted(hits, reverse=True):
        if key in chosen or any(not (b <= u0 or a >= u1) for u0, u1 in used):
            continue
        chosen.append(key)
        used.append((a, b))
    return sorted(chosen, key=lambda k: min(a for _, a, _, kk in hits if kk == k))


def choose_filters(q: str, view: dict) -> list[str]:
    out = []
    for key, f in view.get("filters", {}).items():
        if any(_has(q, ph) for ph in f.get("phrases", [])) and not any(_has(q, u) for u in f.get("unless", [])):
            out.append(key)
    if "card_and_loan" in out:
        out = [k for k in out if k not in ("card_only", "loan_only")]
    if "outbound_call" in out:
        out = [k for k in out if k != "outbound"]
    if "randomised_pilot" in out:
        out = [k for k in out if k != "randomised"]
    return out


def value_filters(q: str, view_name: str, con) -> dict:
    """Explicit values named in the question (test cells, queues, product codes) become IN filters."""
    out = {}
    fields = {"test_cell", "queue", "product_code", "primary_product"}
    try:
        rows = con.execute("SELECT field, value FROM layer2.menu_values WHERE view_name = ?", [view_name]).fetchall()
    except Exception:  # noqa: BLE001 - menus are optional
        return out
    for field_name, value in rows:
        if field_name in fields and value and len(value) > 3:
            pattern = r"\b" + re.escape(value.lower()).replace("_", "[_ -]") + r"\b"
            if re.search(pattern, q):
                out.setdefault(field_name, []).append(value)
    return out


# a date cue right before a range ("cured between", "due in September", "opened during Q3") names the period's column
RANGE_AFTER = (r"\s+(?:between|from|during|since|before|after|in\s+(?:the\s+)?(?:\d{4}|q[1-4]|last|this|past|previous|"
               r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))")


def time_key_for(q: str, view: dict, default: str | None = None) -> str | None:
    """The time column the question's period is about. A cue followed by a range wins ("promises due between 1 and 20
    September ... the channel the promise was taken on" -> due); otherwise the first cue named; else the default."""
    cues = view.get("time_cues", {})
    for key, words in cues.items():
        if any(re.search(r"(?<![\w-])" + re.escape(c) + RANGE_AFTER, q) for c in words):
            return key
    for key, words in cues.items():
        if any(_has(q, c) for c in words):
            return key
    return default


def choose_time(q: str, measure: dict, view: dict) -> str | None:
    t = measure.get("time")
    if t in (None, "none", "snapshot"):
        return t
    return time_key_for(q, view, t)


STOP_SAFE = {"the", "and", "for", "with", "per", "by", "of", "in", "on", "to", "rate", "count", "share"}
ORDER_WORDS = "most highest top best largest biggest max maximum least lowest fewest worst smallest min minimum rank ranked"


def _order(q: str) -> str | None:
    if re.search(r"\b(most|highest|top|best|largest|biggest|max(?:imum)?)\b", q):
        return "desc"
    if re.search(r"\b(least|lowest|fewest|worst|smallest|min(?:imum)?)\b", q):
        return "asc"
    return None


def _limit(q: str) -> int | None:
    """"top 5", "bottom 3", "which 3 queues", "the 10 highest" -> N (numbers inside periods do not count)."""
    from layer2.semantic.coverage import blank_periods
    m = re.search(r"\b(?:top|bottom|first|best|worst)\s+(\d+)\b|\bwhich\s+(\d+)\b|"
                  r"\b(\d+)\s+(?:highest|lowest|largest|biggest|smallest|most|least|best|worst)\b", blank_periods(q))
    n = next((int(g) for g in m.groups() if g), None) if m else None
    return n if n and 0 < n <= 1000 else None


def plan(p: Parsed, con, use_model: bool = True) -> QueryPlan | None:
    q = p.question.lower()
    mid, engine, prob = choose_measure(p, use_model)
    if mid is None:
        return None
    m = catalog()["measures"][mid]
    view_name = m["view"]
    variant = None
    if mid == "cure_rate" and re.search(r"pilot|certified|agent", q):
        view_name, variant = "f_assignments", "assignments"
    if mid == "write_off_rate" and any(_has(q, c) for c in m["count_and_amount"]["cues"]):
        variant = "count_and_amount"
    view = catalog()["views"][view_name]
    qp = QueryPlan(measure=mid, view=view_name, variant=variant)
    qp.engines["metric"] = engine if prob is None else f"{engine} p={prob:.2f}"
    qp.dimensions = choose_dimensions(q, view)
    qp.filters = choose_filters(q, view)
    qp.value_filters = value_filters(q, view_name, con)
    qp.time_key = choose_time(q, m, view)
    qp.within_days = p.within_days or (int(x.group(1)) if (x := re.search(r"(\d+)-day", q)) else None)
    if len(p.periods) >= 2 and qp.time_key not in (None, "none", "snapshot"):
        qp.compare = p.periods[:2]
    elif p.periods and qp.time_key not in (None, "none", "snapshot"):
        qp.period = p.periods[0]
    qp.engines["dimensions"] = qp.engines["filters"] = "rules"
    qp.order = _order(q)
    qp.limit = _limit(q) if qp.dimensions else None
    if qp.limit and not qp.order:
        qp.order = "asc" if re.search(r"\bbottom\b", q) else "desc"
    if not qp.dimensions and use_model:
        asked = re.findall(r"\b(?:by|per|for each|across|split by|broken down by)\s+([a-z][a-z _-]{2,30}?)"
                           r"(?=[,?.]|$|\s(?:for|in|among|and|during|over|from|between|on)\b)", q)
        if asked:
            criteria = {k: (d.get("phrases") or [k])[0].replace("by ", "") for k, d in view.get("dimensions", {}).items()}
            criteria["none"] = "none of these"
            try:
                d = adapters.decide(f"Group the result by: {asked[0]}", {"dim": {
                    "type": "choice", "instructions": "Which column matches this grouping?", "criteria": criteria}},
                    engines=("llm",))["dim"]
                if d["answer"] in view.get("dimensions", {}) and (d["prob"] or 0) >= 0.5:
                    qp.dimensions = [d["answer"]]
                    qp.engines["dimensions"] = f"{d['engine']} p={d['prob']:.2f}"
            except adapters.DecisionUnavailable:
                pass
    return qp


# --------------------------------------------------------------------------------------------- compiling

def _lit(d: date) -> str:
    return f"DATE '{d.isoformat()}'"


def _period_sql(column: str, per: Period) -> str:
    return f"{column} >= {_lit(per.start)} AND {column} < {_lit(per.end_exclusive())}"


def compile_plan(qp: QueryPlan, p: Parsed) -> str:
    m = catalog()["measures"][qp.measure]
    if m.get("special") == "roll_rate":
        return _compile_roll_rate(qp, p)
    if m.get("special") == "cure_rate":
        return _compile_cure_rate(qp, p)
    view = catalog()["views"][qp.view]
    where = list(m.get("where", []))
    where += [view["filters"][f]["sql"] for f in qp.filters]
    for col, values in qp.value_filters.items():
        where.append(f"{col} IN (" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + ")")
    time_col = view.get("time", {}).get(qp.time_key) if qp.time_key not in (None, "none", "snapshot") else None
    select = []
    for key in qp.dimensions:
        select.append(f"{view['dimensions'][key]['column']} AS {key}")
    if qp.compare and time_col:
        cases = " ".join(f"WHEN {_period_sql(time_col, per)} THEN '{per.label}'" for per in qp.compare)
        select.insert(0, f"CASE {cases} END AS period")
        where.append("(" + " OR ".join(f"({_period_sql(time_col, per)})" for per in qp.compare) + ")")
    period_sql = _period_sql(time_col, qp.period) if (qp.period and time_col) else None
    if qp.time_key == "snapshot":
        where.append(f"{m['snapshot_column']} = {_lit(p.as_of)}")
    decimals = 2
    if qp.variant == "count_and_amount":
        where += list(m["count_and_amount"].get("where", []))
        for name, expr in m["count_and_amount"]["measures"].items():
            select.append(f"round({expr}, {decimals}) AS {name}")
        if period_sql:
            where.append(period_sql)
    else:
        expr = m["expr"]
        if m.get("period_in_expr"):
            expr = expr.replace("__PERIOD__", period_sql or "TRUE")
        elif period_sql:
            where.append(period_sql)
        select.append(f"round({expr}, {decimals}) AS {m['name']}")
        if m.get("count"):
            select.append(f"{m.get('count_expr', 'count(*)')} AS {m['count']}")
    groups = (["period"] if qp.compare else []) + qp.dimensions
    where = list(dict.fromkeys(where))
    sql = f"SELECT {', '.join(select)}\nFROM layer2.{qp.view}"
    if where:
        sql += "\nWHERE " + "\n  AND ".join(f"({w})" if " OR " in w else w for w in where)
    if groups:
        idx = ", ".join(str(i + 1) for i in range(len(groups)))
        value_pos = len(groups) + 1
        order = f"{value_pos} {qp.order.upper()} NULLS LAST, {idx}" if qp.order else idx
        sql += f"\nGROUP BY {idx}\nORDER BY {order}"
        if qp.limit:
            sql += f"\nLIMIT {qp.limit}"
    return sql


def _compile_roll_rate(qp: QueryPlan, p: Parsed) -> str:
    q = p.question.lower()
    ends = sorted(p.month_ends)
    if len(ends) >= 2:
        start, end = ends[0], ends[-1]
    else:   # last two quarter-ends on or before the snapshot
        qe = [date(y, mo, d) for y in range(p.as_of.year - 1, p.as_of.year + 1)
              for mo, d in ((3, 31), (6, 30), (9, 30), (12, 31)) if date(y, mo, d) <= p.as_of]
        start, end = qe[-2], qe[-1]
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*days? past due", q)
    lo, hi = (int(m.group(1)), int(m.group(2))) if m else (1, 30)
    dims = [d for d in qp.dimensions if d == "product_type"] or (["product_type"] if "product" in q else [])
    sel = ", ".join([f"a.{d}" for d in dims] + [
        f"round(100.0 * avg(CASE WHEN b.dpd > {hi} THEN 1 ELSE 0 END), 2) AS roll_rate_pct", "count(*) AS accounts"])
    sql = (f"WITH s AS (SELECT account_id, product_type, month_end, dpd FROM layer2.f_account_month\n"
           f"           WHERE dpd IS NOT NULL AND month_end IN ({_lit(start)}, {_lit(end)}))\n"
           f"SELECT {sel}\nFROM s a JOIN s b ON a.account_id = b.account_id AND b.month_end = {_lit(end)}\n"
           f"WHERE a.month_end = {_lit(start)} AND a.dpd BETWEEN {lo} AND {hi}")
    if dims:
        sql += "\nGROUP BY 1\nORDER BY 1"
    qp.engines["roll"] = f"{start} -> {end}, dpd {lo}-{hi}, worse = dpd > {hi}"
    return sql


def _compile_cure_rate(qp: QueryPlan, p: Parsed) -> str:
    view = catalog()["views"][qp.view]
    m = catalog()["measures"]["cure_rate"]
    if qp.view == "f_assignments":
        # cured within N days of the assignment (pilot cases are already 30+ days old when assigned)
        n = qp.within_days or 30
        cured = f"cure_flag AND cure_date >= assigned_date AND cure_date <= assigned_date + {n}"
    elif qp.within_days:
        cured = f"cure_flag AND days_to_cure <= {qp.within_days}"
    elif qp.period:
        cured = f"cure_flag AND cure_date <= {_lit(qp.period.end)}"
    else:
        cured = "cure_flag"
    where = list(m["where"]) if qp.view == "f_cases" else []
    where += [view["filters"][f]["sql"] for f in qp.filters]
    if qp.period:
        where.append(_period_sql("case_open_date", qp.period))
    dims = [f"{view['dimensions'][k]['column']} AS {k}" for k in qp.dimensions]
    sel = ", ".join(dims + [f"round(100.0 * avg(CASE WHEN {cured} THEN 1 ELSE 0 END), 2) AS cure_rate_pct",
                            "count(*) AS cases"])
    sql = f"SELECT {sel}\nFROM layer2.{qp.view}"
    if where:
        sql += "\nWHERE " + "\n  AND ".join(where)
    if dims:
        idx = ", ".join(str(i + 1) for i in range(len(dims)))
        order = f"{len(dims) + 1} {qp.order.upper()} NULLS LAST, {idx}" if qp.order else idx
        sql += f"\nGROUP BY {idx}\nORDER BY {order}"
    return sql


# --------------------------------------------------------------------------------------------- answering

def _fmt(v) -> str:
    """Plain decimals, never scientific notation: 2526900.5 -> '2526900.5', 37.0 -> '37', None -> 'unknown'."""
    if v is None:
        return "unknown"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        text = f"{v:.2f}".rstrip("0").rstrip(".")
        return "0" if text in ("-0", "") else text
    return str(v)


def to_csv(columns, rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([_fmt(v) for v in r])
    return buf.getvalue().strip()


def caveats(qp: QueryPlan, p: Parsed, con, rows, columns) -> list[str]:
    notes = []
    m = catalog()["measures"][qp.measure]
    if not m.get("certified"):
        notes.append("This is a plain count or share of a stored field, not one of the 15 certified metrics.")
    per = qp.compare or ([qp.period] if qp.period else [])
    relevant = {"f_contacts": ("app_outage", "dialer_outage"), "f_promises": ("app_outage",)}.get(qp.view, ())
    if per and relevant:
        try:
            types = ", ".join(f"'{t}'" for t in relevant)
            for eid, desc, s, e in con.execute(
                    "SELECT event_id, description, try_cast(start_ts AS DATE), try_cast(end_ts AS DATE) FROM bronze.ops_events "
                    f"WHERE event_type IN ({types}) ORDER BY 3").fetchall():
                if s and e and any(x.start <= e + timedelta(days=8) and x.end >= s for x in per):
                    notes.append(f"Known incident {eid} ({str(desc)[:110].rstrip('.')}) affects this period.")
        except Exception:  # noqa: BLE001 - caveats are best effort
            pass
    if qp.measure == "roll_rate":
        try:
            n = con.execute("SELECT count(*) FROM layer2.f_account_month WHERE month_end IS NULL "
                            "AND month_end_lenient IS NOT NULL").fetchone()[0]
            if n:
                notes.append(f"month_end_date is parsed in the standard ISO, day-first and compact formats; {n:,} "
                             "snapshot rows written month-first (for example 03/31/2026) are not included.")
        except Exception:  # noqa: BLE001
            pass
    if any(d in ("current_bucket",) for d in qp.dimensions):
        notes.append("Buckets are as stored on the case; about 4% of stored buckets disagree with current_dpd.")
    if qp.view == "f_accounts" and qp.measure == "delinquency_rate":
        notes.append("Accounts with missing days past due (about 8% of cards) are excluded, not counted as current.")
    if qp.measure in ("promise_kept_rate", "broken_promise_rate"):
        notes.append("Open and cancelled promises are excluded, as the certified definition requires.")
    if qp.filters and "randomised" in qp.filters:
        notes.append("Only randomised cases are compared; rule-based assignment would bias the comparison.")
    if qp.variant == "assignments" and len(rows) == 2 and columns[0] == "certified":
        vals = {str(r[0]).lower(): r[1] for r in rows}
        if "true" in vals and "false" in vals and vals["true"] is not None and vals["false"] is not None:
            direction = "higher" if vals["true"] > vals["false"] else "lower" if vals["true"] < vals["false"] else "the same"
            notes.insert(0, f"Certified agents' {qp.within_days or 30}-day cure rate is {direction}: "
                            f"{_fmt(vals['true'])}% vs {_fmt(vals['false'])}% for other agents (randomised pilot, "
                            "counted from the assignment date because pilot cases are already past 30 days old).")
    return notes


def menus() -> dict:
    """What the app shows as editable choices: measures and, per view, its dimensions and filters."""
    cat = catalog()
    return {"measures": {mid: {"label": m["label"], "view": m["view"], "certified": m.get("certified", False)}
                         for mid, m in cat["measures"].items()},
            "views": {v: {"dimensions": list(d.get("dimensions", {})), "filters": list(d.get("filters", {})),
                          "time": list(d.get("time", {}))} for v, d in cat["views"].items()}}


def run_plan(qp: QueryPlan, p: Parsed) -> dict:
    """Run a (possibly user-edited) plan: compile, guard, execute. No model involved."""
    con = adapters.connect(read_only=True)
    try:
        return _execute(qp, p, con)
    finally:
        con.close()


# A filter key covers these fields, so a value of that field in the question is already applied.
COVERED_BY_FILTER = {"outbound_call": {"channel", "direction"}, "outbound": {"direction"}, "open": {"outcome"},
                     "card_only": {"product", "product_type"}, "loan_only": {"product", "product_type"},
                     "card_and_loan": {"product", "product_type"}, "active": {"account_status"},
                     "randomised": {"assignment_method"}, "randomised_pilot": {"assignment_method"},
                     "hardship": {"hardship_flag", "hardship_reason"}}
SKIP_FIELDS = {"ptp_status", "outcome_code", "direction", "assignment_method"}


def _menu_rows(con, view_name: str) -> list[tuple[str, str]]:
    try:
        return con.execute("SELECT field, value FROM layer2.menu_values WHERE view_name = ?", [view_name]).fetchall()
    except Exception:  # noqa: BLE001 - no menus (unit tests, fresh database)
        return []


def _used_fields(qp: QueryPlan) -> set:
    view = catalog()["views"][qp.view]
    used = {view["dimensions"][k]["column"] for k in qp.dimensions} | set(qp.value_filters)
    for f in qp.filters:
        used |= COVERED_BY_FILTER.get(f, set())
    return used


def _named(q: str, qp: QueryPlan, con) -> tuple[list[tuple[str, str]], list[str]]:
    """Values ("app" in ptp_channel) and columns ("strategy code") the question names that the plan does not use."""
    q = q.lower()
    view = catalog()["views"][qp.view]
    used = _used_fields(qp)
    implied = " ".join(catalog()["measures"][qp.measure].get("cues", []))   # "charged off" is the write-off metric
    values = []
    rows = _menu_rows(con, qp.view)
    for field, value in rows:
        v = (value or "").lower()
        if field in used or field in SKIP_FIELDS or len(v) < 3 or v.replace("-", "").isdigit():
            continue
        if v.replace("_", " ") in implied:
            continue
        if _has(q, v) or ("_" in v and _has(q, v.replace("_", " "))):
            values.append((field, value))
    columns = []
    for col in sorted({d["column"] for d in view.get("dimensions", {}).values()} | {f for f, _ in rows}):
        if col in used or col in SKIP_FIELDS or not re.fullmatch(r"[a-z_]+", col):
            continue
        phrase = col.replace("_", " ")
        if " " in phrase and _has(q, phrase):
            columns.append(col)
    return values, columns


def auto_apply(q: str, qp: QueryPlan, con) -> None:
    """Apply what the question names without a model: a value that belongs to exactly one field of the view becomes a
    filter ("through the app" -> ptp_channel = 'app'); a named column that is a breakdown of the view becomes one
    ("which strategy code" -> by strategy_code). Anything ambiguous is left for the planner or the note."""
    values, columns = _named(q, qp, con)
    fields_by_value: dict[str, set] = {}
    for field, value in values:
        fields_by_value.setdefault(value.lower(), set()).add(field)
    applied = []
    for field, value in values:
        if len(fields_by_value[value.lower()]) == 1:
            qp.value_filters.setdefault(field, [])
            if value not in qp.value_filters[field]:
                qp.value_filters[field].append(value)
                applied.append(f"{field}={value}")
    dims = catalog()["views"][qp.view].get("dimensions", {})
    for col in columns:
        key = next((k for k, d in dims.items() if d["column"] == col), None)
        if key and key not in qp.dimensions and col not in qp.value_filters and len(qp.dimensions) < 2:
            qp.dimensions.append(key)
            applied.append(f"by {key}")
    if applied:
        qp.engines["auto_applied"] = ", ".join(applied)


def unused_terms(q: str, qp: QueryPlan, con) -> list[str]:
    """What the question names that the plan does not use: a column ("strategy code") or a value ("app", "sms").
    Answering without it would be silently wrong, so the caller asks the planner again or says what was not applied."""
    values, columns = _named(q, qp, con)
    out = [f"{v} ({f.replace('_', ' ')})" for f, v in values] + [c.replace("_", " ") for c in columns]
    return list(dict.fromkeys(out))


PLAN_SYSTEM = """You map a collections analytics question to a query plan by choosing ONLY from the menu given.
Pick the metric that answers the question (or "none" if no metric fits), the breakdowns it asks for ("by X",
"which X has the most"), the filters it states and any filter values it names, exactly as written in the menu.
Do not invent fields or values. Time periods are handled separately; do not include them."""


def llm_plan(p: Parsed, con) -> QueryPlan | None:
    """One LLM call restricted to the menu; every choice is checked against the real menu before use."""
    cat = catalog()
    lines = ["METRICS (id: name - definition; table):"]
    for mid, m in cat["measures"].items():
        lines.append(f"- {mid}: {m['label']} - {m.get('definition') or 'plain aggregate'}; {m['view']}")
    all_dims, all_filters, all_fields = set(), set(), set()
    for vname, v in cat["views"].items():
        dims, filts = list(v.get("dimensions", {})), list(v.get("filters", {}))
        all_dims |= set(dims)
        all_filters |= set(filts)
        values = {}
        for field, value in _menu_rows(con, vname):
            values.setdefault(field, []).append(value)
        all_fields |= set(values)
        lines.append(f"TABLE {vname}: breakdowns {dims}; filters {filts}; values "
                     + "; ".join(f"{f}: {vals[:12]}" for f, vals in values.items()))
    schema = {"type": "object", "additionalProperties": False, "required": ["measure", "dimensions", "filters", "values"],
              "properties": {
                  "measure": {"type": "string", "enum": list(cat["measures"]) + ["none"]},
                  "dimensions": {"type": "array", "items": {"type": "string", "enum": sorted(all_dims)}},
                  "filters": {"type": "array", "items": {"type": "string", "enum": sorted(all_filters)}},
                  "values": {"type": "array", "items": {
                      "type": "object", "additionalProperties": False, "required": ["field", "value"],
                      "properties": {"field": {"type": "string", "enum": sorted(all_fields) or ["none"]},
                                     "value": {"type": "string"}}}}}}
    out = adapters.complete("\n".join(lines) + "\n\n" + llm_guard.wrap_question(p.question),
                            system=PLAN_SYSTEM + "\n" + llm_guard.QUESTION_RULE, json_schema=schema, max_tokens=1500)
    mid = out["measure"]
    if mid not in cat["measures"]:
        return None
    m = cat["measures"][mid]
    q = p.question.lower()
    view_name = "f_assignments" if (mid == "cure_rate" and re.search(r"pilot|certified", q)) else m["view"]
    view = cat["views"][view_name]
    qp = QueryPlan(measure=mid, view=view_name)
    if mid == "write_off_rate" and any(_has(q, c) for c in m["count_and_amount"]["cues"]):
        qp.variant = "count_and_amount"
    elif view_name == "f_assignments":
        qp.variant = "assignments"
    qp.dimensions = [d for d in dict.fromkeys(out["dimensions"]) if d in view.get("dimensions", {})][:2]
    qp.filters = [f for f in dict.fromkeys(out["filters"]) if f in view.get("filters", {})]
    real = {(f, v.lower()): v for f, v in _menu_rows(con, view_name)}
    for item in out["values"]:
        actual = real.get((item["field"], item["value"].lower()))
        if actual and item["field"] not in {view["dimensions"][d]["column"] for d in qp.dimensions}:
            qp.value_filters.setdefault(item["field"], []).append(actual)
    qp.time_key = choose_time(q, m, view)
    qp.within_days = p.within_days or (int(x.group(1)) if (x := re.search(r"(\d+)-day", q)) else None)
    if len(p.periods) >= 2 and qp.time_key not in (None, "none", "snapshot"):
        qp.compare = p.periods[:2]
    elif p.periods and qp.time_key not in (None, "none", "snapshot"):
        qp.period = p.periods[0]
    qp.order = _order(q)
    qp.engines = {"metric": "llm planner (menu only)", "dimensions": "llm", "filters": "llm"}
    return qp


def plan_vocab(qp: QueryPlan) -> tuple[list[str], set]:
    """Words a certified plan explains (for coverage.gaps) and the operations it implements."""
    cat = catalog()
    m, view = cat["measures"][qp.measure], cat["views"][qp.view]
    vocab = [qp.measure, m.get("label", ""), m.get("name", ""), m.get("definition", "")] + list(m.get("cues", []))
    vocab += list(m.get("count_and_amount", {}).get("cues", []))
    vocab += list(view.get("time_cues", {}).get(qp.time_key, []) or [])
    for k in qp.dimensions:
        vocab += [k, view["dimensions"][k]["column"]] + list(view["dimensions"][k].get("phrases", []))
    for f in qp.filters:
        vocab += list(view["filters"][f].get("phrases", []))
    for col, values in qp.value_filters.items():
        vocab += [col] + list(values)
    if qp.order:
        vocab.append(ORDER_WORDS)
    handled = set()
    if qp.compare:
        handled.add("compare")
    if qp.limit:
        handled.add("top_n")
    if m.get("special") == "roll_rate":   # its compiler reads the DPD range and the two month-ends
        handled |= {"threshold", "change"}
        vocab += ["roll", "rolled", "worse", "bucket", "past due", "dpd", "end"]
    if m.get("special") == "cure_rate":
        vocab += ["cure", "cured", "current", "returned", "back", "within"]
    return [str(v) for v in vocab if v], handled


_DATA_WORDS: dict = {}


def data_words(con) -> set[str]:
    """Every word that refers to data: column names of the safe views, category values, catalog phrases and cues,
    plain-aggregate aliases. Leftover words outside this set (and not proper nouns) cannot change a query."""
    from layer2.semantic.coverage import words
    if "all" not in _DATA_WORDS:      # the schema does not change within a process
        _DATA_WORDS["all"] = words(data_text(con)) - STOP_SAFE
    return _DATA_WORDS["all"]


def data_text(con) -> str:
    """The raw text behind data_words() (column names, category values, catalog phrases, cues, aliases)."""
    from layer2.semantic import generic
    if "text" in _DATA_WORDS:
        return _DATA_WORDS["text"]
    own = con is None
    if own:
        try:
            con = adapters.connect(read_only=True)
        except Exception:  # noqa: BLE001 - no database: catalog words only
            con = None
    text = []
    try:
        text += [c for (c,) in con.execute("SELECT DISTINCT column_name FROM information_schema.columns "
                                           "WHERE table_schema = 'layer2'").fetchall()]
        text += [f"{f} {v}" for f, v in con.execute("SELECT field, value FROM layer2.menu_values").fetchall()]
    except Exception:  # noqa: BLE001 - no database (unit tests): catalog words only
        pass
    finally:
        if own and con is not None:
            con.close()
    cat = catalog()
    for v in cat["views"].values():
        for d in v.get("dimensions", {}).values():
            text += list(d.get("phrases", []))
        for f in v.get("filters", {}).values():
            text += list(f.get("phrases", []))
        for cues in v.get("time_cues", {}).values():
            text += list(cues)
    for k, m in cat["measures"].items():
        text += [k, m.get("label") or ""] + list(m.get("cues", []))
    for c, al in generic.ALIASES.items():
        text += [c] + al
    _DATA_WORDS["text"] = " ".join(str(t) for t in text if t)
    return _DATA_WORDS["text"]


def other_metric(q: str, chosen: str, con) -> str | None:
    """A different metric whose own words (label, formula, cues) cover at least 2 more of the question's data words
    than the chosen one: a sign the cue match picked the wrong metric. Margin 2 raised no alarm on the 35 benchmark
    and 60 own metric questions (margin 1 raised 3). Only flags; the LLM or the reader decides."""
    from layer2.semantic.coverage import STOP, words
    dw = data_words(con)
    ws = {w for w in words(q) if w in dw and w not in STOP and len(w) > 2}
    cat = catalog()["measures"]

    def cover(k):
        m = cat[k]
        return len(ws & words(" ".join([m.get("label") or "", m.get("definition") or "", " ".join(m.get("cues", [])), k])))
    scores = {k: cover(k) for k in cat}
    best = max(scores, key=scores.get)
    return cat[best].get("label") or best if best != chosen and scores[best] >= scores[chosen] + 2 else None


def missing_parts(p: Parsed, explained: list[str], handled: set, unused: list[str] = (), con=None,
                  definition: str = "") -> list[str]:
    from layer2.semantic.coverage import gaps
    out = gaps(p.question, explained, handled=handled, periods=len(p.periods),
               data_words=data_words(con) if con is not None else None, definition=definition)
    if unused:
        out.insert(0, "named but not applied: " + ", ".join(unused))
    return out


def answer_metric(p: Parsed, role: str = "analyst", use_model: bool = True) -> dict:
    """Rules first (certified metric, else a plain aggregate). If the plan leaves part of the question out
    (coverage.gaps), the LLM extends the rule-built SQL or confirms nothing is missing; without a model the answer
    says what was not applied. No rule plan at all -> the LLM menu planner, then LLM-written SQL (freeform)."""
    con = adapters.connect(read_only=True)
    try:
        llm = use_model and adapters.llm_available()
        qp = plan(p, con, use_model=use_model)
        gp = None
        if qp is None:   # no certified metric: try the rule-built general aggregate before any model
            from layer2.semantic import generic
            try:
                gp = generic.build(p, con)
            except Exception:  # noqa: BLE001 - the engine is optional
                gp = None
        if qp is not None and catalog()["measures"][qp.measure].get("unit") == "pct" and \
                "llm" not in str(qp.engines.get("metric")):
            from layer2.semantic import generic
            try:
                alt = generic.build(p, con)
            except Exception:  # noqa: BLE001
                alt = None
            if alt is not None and alt.agg in ("avg", "median", "sum") and alt.column:
                qp, gp = None, alt      # "average number of days to cure" is an average, not the cure rate
        if qp is not None and "llm" not in str(qp.engines.get("metric")):
            # the certified metric leaves words out ("total overdue on open cases" is not "amount collected"), but a
            # plain aggregate explains the whole question: use the aggregate, labelled as not certified
            v0, h0 = plan_vocab(qp)
            if missing_parts(p, v0, h0, unused_terms(p.question, qp, con), con=con):
                from layer2.semantic import generic
                try:
                    alt = generic.build(p, con)
                except Exception:  # noqa: BLE001
                    alt = None
                if alt is not None and alt.agg != "list" and \
                        not missing_parts(p, alt.vocab + alt.where, alt.handled, con=con):
                    qp, gp = None, alt
        if qp is None and gp is None and llm:
            try:
                qp = llm_plan(p, con)
            except adapters.LLMUnavailable:
                qp = None
        if qp is None and gp is None:
            from layer2.policy import answer_policy_parsed, relevance
            if relevance(p.question) >= 3:   # no data plan, but a bank document matches well
                out = answer_policy_parsed(p, role=role, use_model=use_model)
                if not out["refused"]:
                    out.setdefault("engines", {})["rerouted"] = "metric_sql -> policy (no data plan)"
                    return out
            from layer2.lookup import answer_uncertified
            return answer_uncertified(p, role=role, use_model=use_model, con=con)
        if qp is not None:
            auto_apply(p.question, qp, con)
            out = _execute(qp, p, con)
            vocab, handled = plan_vocab(qp)
            m = catalog()["measures"][qp.measure]
            definition = " ".join(str(x) for x in [m.get("label"), m.get("definition"), m.get("expr"),
                                                   " ".join(m.get("where", [])), " ".join(m.get("cues", []))] if x)
            missing = missing_parts(p, vocab + [definition], handled, unused_terms(p.question, qp, con), con=con,
                                    definition=definition)
            alt = other_metric(p.question, qp.measure, con) if "llm" not in str(qp.engines.get("metric")) else None
            if alt:
                missing.insert(0, f"the question may ask for a different metric (closest: {alt})")
        else:
            out = _answer_generic(gp, con)
            if out is None:
                from layer2.lookup import answer_uncertified
                return answer_uncertified(p, role=role, use_model=use_model, con=con)
            missing = missing_parts(p, gp.vocab + gp.where, gp.handled, con=con)
        if not missing or out.get("table") is None:
            return out
        return _complete(p, con, out, missing, llm)
    finally:
        con.close()


def _complete(p: Parsed, con, out: dict, missing: list[str], llm: bool) -> dict:
    """The rule answer leaves `missing` out: the LLM extends its SQL (or confirms it is already right); else say so."""
    from layer2 import freeform
    if llm:
        try:
            r = freeform.run(p, con, base_sql=out["sql_or_sources"], missing=missing)
        except adapters.LLMUnavailable:
            r = {"status": "unavailable"}
        if r["status"] == "already_correct":
            out.setdefault("engines", {})["coverage"] = "llm checked: nothing missing (" + "; ".join(missing) + ")"
            return out
        if r["status"] == "ok" and r["rows"]:
            note = ("Note: the rule-built query was extended by the AI to apply " + "; ".join(missing) +
                    ". Not a certified metric; the SQL passed the SQL guard.")
            understood = (out.get("understood") or "") + "; extended by the AI with: " + "; ".join(missing)
            return {"answer": to_csv(r["columns"], r["rows"]) + "\n\n" + note, "sql_or_sources": r["sql"],
                    "refused": False, "reason": r["explanation"] + " " + note, "route": "metric_sql",
                    "table": [dict(zip(r["columns"], row)) for row in r["rows"]], "understood": understood,
                    "engines": {**out.get("engines", {}), "extended": "llm-sql"}}
        out.setdefault("engines", {})["llm_extend"] = r["status"]
    note = "Not applied: " + "; ".join(missing) + "."
    why = (r.get("explanation") or "").strip() if llm and r.get("status") == "cannot" else ""
    from layer2.llm_guard import pii_leaks
    if why and not pii_leaks(why):
        note += " AI check: " + (why[:200] + ("…" if len(why) > 200 else ""))
    out["answer"] += ("\n\nNote: " if "\n\nNote: " not in out["answer"] else " ") + note
    out["reason"] += " " + note
    out.setdefault("engines", {})["unused_terms"] = missing
    return out


def _answer_generic(gp, con) -> dict | None:
    from layer2.semantic import generic
    ok, problem, sql = sql_guard.check(generic.to_sql(gp))
    if not ok:
        return None
    try:
        cur = con.execute(sql)
    except Exception:  # noqa: BLE001
        return None
    columns, rows = [d[0] for d in cur.description], cur.fetchall()
    if not rows:
        return None
    text = to_csv(columns, rows) + ("\n\nNote: plain aggregate built by rules from the question, not one of the 15 "
                                    "certified metrics.")
    return {"answer": text, "sql_or_sources": sql, "refused": False, "reason": "Understood as " + gp.understood(),
            "route": "metric_sql", "table": [dict(zip(columns, r)) for r in rows], "understood": gp.understood(),
            "plan": gp, "engines": {"metric": "rules (general aggregate)"}}


def _execute(qp: QueryPlan, p: Parsed, con) -> dict:
    """Compile, guard and run one plan; the answer carries its SQL and what was understood."""
    sql = compile_plan(qp, p)
    ok, problem, sql = sql_guard.check(sql)
    if not ok:
        return {"answer": f"Could not answer safely: {problem}", "sql_or_sources": sql, "refused": False,
                "reason": problem, "route": "metric_sql", "table": None}
    cur = con.execute(sql)
    columns = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if not rows:
        text = "No rows match: " + qp.understood()
    else:
        text = to_csv(columns, rows)
        notes = caveats(qp, p, con, rows, columns)
        if notes:
            text += "\n\nNote: " + " ".join(notes)
    table = [dict(zip(columns, r)) for r in rows]
    m = catalog()["measures"][qp.measure]
    reason = "Understood as " + qp.understood() + (f". Certified definition: {m['definition']}" if m.get("definition") else "")
    return {"answer": text, "sql_or_sources": sql, "refused": False, "reason": reason, "route": "metric_sql",
            "table": table, "understood": qp.understood(), "plan": qp, "engines": dict(qp.engines)}
