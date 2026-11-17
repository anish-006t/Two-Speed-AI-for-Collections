"""Rule-based general aggregates for questions no certified metric covers (no model needed).

"average total overdue of open cases by queue", "median days past due for card accounts", "what percent of contacts
in September 2026 were SMS": an aggregate word + a column of one safe view (+ "by X") + filters + a period. Every
column comes from the view's real schema, the SQL is built by code and checked by sql_guard, and the answer is
labelled as a plain aggregate (not one of the 15 certified metrics). Returns None when it cannot build a confident
query, so the LLM planner (or an honest "can't answer") takes over.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from layer2.parse import Parsed

# Which safe view a question is about, from the nouns it uses (first match wins).
ENTITIES = [
    ("f_promises", r"\bpromises?\b|\bptps?\b"),
    ("f_contacts", r"\bcontacts?\b|\bcalls?\b|\battempts?\b|\bsms\b|\btexts?\b|\bemails?\b|\bletters?\b|\bdisputes?\b"),
    ("f_accounts", r"\baccounts?\b|\bcards?\b|\bloans?\b"),
    ("f_deposits", r"\bdeposits?\b|\bchequing\b|\bpayroll\b"),
    ("f_cases", r"\bcases?\b|\bqueues?\b|\bcustomers?\b|\bdebtors?\b"),
]
# Everyday words for numeric columns (column -> phrases); the column's own name is always a phrase too.
ALIASES = {
    "current_dpd": ["days past due", "dpd", "arrears age"], "dpd": ["days past due", "dpd"],
    "total_overdue": ["overdue", "total overdue", "past due amount", "arrears"], "total_exposure": ["exposure"],
    "contact_cost_cad": ["cost", "contact cost"], "ptp_amount": ["promised amount", "promise amount", "promise size"],
    "amount_paid_against": ["amount paid", "paid amount"], "payment_amount": ["payment amount"],
    "balance": ["balance"], "chargeoff_amount": ["charge-off amount", "charged off amount"],
    "contact_attempts_total": ["attempts", "contact attempts"], "days_to_cure": ["days to cure", "time to cure"],
    "payroll_delay_days": ["payroll delay"], "contact_hour_local": ["hour"], "payments_since_open_amount": ["payments"],
    "prior_broken_ptp_count": ["prior broken promises"],
    "break_prob": ["likely to break", "break probability", "probability of breaking", "chance of breaking",
                   "risk of breaking", "break risk", "breaking risk", "likely to miss", "at risk"],
}
# A ranked list of records ("which customers are most likely to break a promise this week", "top 10 cases by
# total overdue"): the record's IDs and a few descriptive columns (never names, phones or addresses).
LIST_COLUMNS = {
    "f_promises": ["golden_customer_id", "ptp_id", "case_id", "product", "ptp_amount", "ptp_due_date"],
    "f_cases": ["golden_customer_id", "case_id", "queue", "primary_product", "current_dpd", "total_overdue"],
    "f_accounts": ["golden_customer_id", "account_id", "product_type", "dpd", "balance"],
}
LIST_INTENT = (r"\b(?:which|list|show|give me|who|top \d+|bottom \d+|the \d+)\b.*\b(?:customers?|clients?|debtors?|"
               r"borrowers?|promises?|ptps?|cases|accounts)\b.*\b(?:most|least|highest|lowest|largest|biggest|"
               r"smallest|likely|top|riskiest|at risk)\b|\b(?:top|bottom) \d+ (?:customers|promises|ptps|cases|accounts)\b")
# a break probability only means something for a promise that is still open
LIST_DEFAULT_WHERE = {"break_prob": ["ptp_status = 'open'"]}
AGGS = [("median", r"\bmedian\b"), ("avg", r"\baverage\b|\bmean\b|\bavg\b|\btypical\b"),
        ("share", r"\bshare\b|\bpercent(?:age)?\b|\bproportion\b|\bfraction\b|\bwhat %|\brate of\b"),
        ("sum", r"\btotal\b|\bsum\b|\bhow much\b"), ("count", r"\bhow many\b|\bnumber of\b|\bcount\b")]
TIME_DEFAULT = {"f_contacts": "contact_ts_local", "f_promises": "ptp_due_date", "f_cases": "case_open_date",
                "f_accounts": None, "f_deposits": None, "f_assignments": "assigned_ts"}


@dataclass
class GenericPlan:
    view: str
    agg: str
    column: str | None = None
    condition: str | None = None          # share: the SQL condition whose share is asked for
    condition_label: str = ""
    dims: list = field(default_factory=list)
    where: list = field(default_factory=list)
    period_sql: str | None = None
    order: str | None = None
    limit: int | None = None
    distinct: str | None = None           # count: count(DISTINCT this id) ("how many customers")
    vocab: list = field(default_factory=list)      # words this plan explains (for coverage.gaps)
    handled: set = field(default_factory=set)      # operations it implements (threshold, top_n, distinct)

    def understood(self) -> str:
        what = {"list": f"records ranked by {self.column} ({self.order}, top {self.limit})",
                "count": f"count of distinct {self.distinct}" if self.distinct else "count", "avg": f"average of {self.column}", "median": f"median of {self.column}",
                "sum": f"total of {self.column}", "share": f"share where {self.condition_label}"}[self.agg]
        parts = [f"plain aggregate (rule-built, not a certified metric): {what} over {self.view}"]
        if self.dims:
            parts.append("by " + ", ".join(self.dims))
        if self.where:
            parts.append("filters: " + " and ".join(self.where))
        if self.period_sql:
            parts.append("period: " + self.period_sql)
        return "; ".join(parts)


def _phrase(text: str, phrase: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(phrase) + r"(?:s|es)?(?![\w-])", text) is not None


def _columns(con, view: str) -> dict[str, str]:
    return {c: t.upper() for c, t in con.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'layer2' "
        "AND table_name = ?", [view]).fetchall()}


def _phrases(c: str) -> list[str]:
    return [c.replace("_", " ")] + ALIASES.get(c, [])


GT = {">": r"more than|greater than|higher than|bigger than|larger than|over|above|exceeding|exceeds",
      ">=": r"at least|no less than", "<": r"less than|fewer than|lower than|smaller than|under|below",
      "<=": r"at most|no more than"}
GT_AFTER = {">=": r"\+|or more|plus", "<=": r"or less|or fewer"}


def thresholds(q: str, numeric: set) -> list[tuple[str, str, float]]:
    """"more than 5 contact attempts", "balance above 5000", "90+ days past due" -> (column, op, number). The column
    must be a numeric column of the view named right after the number or right before the comparison."""
    from layer2.semantic.coverage import blank_periods
    q = blank_periods(q)
    out = []
    num = r"\$?(\d[\d,]*(?:\.\d+)?)"
    cands = [(op, m) for op, words in GT.items() for m in re.finditer(rf"\b(?:{words})\s+{num}", q)]
    cands += [(op, m) for op, words in GT_AFTER.items() for m in re.finditer(rf"{num}\s*(?:{words})", q)]
    for op, m in cands:
        n = float(m.group(1).replace(",", ""))
        after, before = q[m.end():m.end() + 40], q[max(0, m.start() - 40):m.start()]
        best = None
        for c in numeric:
            for ph in _phrases(c):
                if re.match(rf"\s*(?:\w+\s+)?{re.escape(ph)}(?:s|es)?\b", after) or \
                        re.search(rf"\b{re.escape(ph)}(?:s|es)?\s+(?:of\s+|is\s+|was\s+|are\s+|were\s+)?$", before):
                    if best is None or len(ph) > best[1]:
                        best = (c, len(ph), ph)
        if best:
            out.append((best[0], op, n, best[2]))
    return out


def build(p: Parsed, con) -> GenericPlan | None:
    from layer2.semantic import _limit, _menu_rows, _order, catalog, choose_dimensions, choose_filters
    q = p.question.lower()
    view = next((v for v, rx in ENTITIES if re.search(rx, q)), None)
    agg = "list" if re.search(LIST_INTENT, q) and view in LIST_COLUMNS else \
        next((a for a, rx in AGGS if re.search(rx, q)), None)
    if not view or not agg:
        return None
    cols = _columns(con, view)
    if not cols:
        return None
    numeric = {c for c, t in cols.items() if any(x in t for x in ("INT", "DOUBLE", "DECIMAL", "FLOAT", "REAL"))}
    from layer2.semantic import ORDER_WORDS
    gp = GenericPlan(view=view, agg=agg, order=_order(q), limit=_limit(q))
    if gp.order:
        gp.vocab.append(ORDER_WORDS)
    gp.vocab.append(re.search(dict(AGGS)[agg], q).group(0) if agg in dict(AGGS) else "list")
    if gp.limit:
        gp.handled.add("top_n")
        gp.order = gp.order or "desc"
    vcat = catalog()["views"].get(view, {})
    gp.dims = [vcat["dimensions"][k]["column"] for k in choose_dimensions(q, vcat)][:2] if vcat else []
    for k in choose_dimensions(q, vcat) if vcat else []:
        gp.vocab += vcat["dimensions"][k].get("phrases", []) + [k]
    gp.vocab += gp.dims
    limits = thresholds(q, numeric)
    for c, op, n, ph in limits:
        gp.where.append(f"{c} {op} {n:g}")
        gp.vocab += [ph, c]
        gp.handled.add("threshold")
    if agg == "count":
        ids = {"customer": "golden_customer_id", "debtor": "golden_customer_id", "borrower": "golden_customer_id",
               "client": "golden_customer_id", "account": "account_id", "case": "case_id", "promise": "ptp_id"}
        m = re.search(r"\b(?:how many|number of|count of|count)\s+(?:distinct\s+|unique\s+|different\s+)?"
                      r"(customer|debtor|borrower|client|account|case|promise)s?\b", q)
        if m and (ids[m.group(1)] in cols) and (m.group(1) in ("customer", "debtor", "borrower", "client")
                                                 or re.search(r"\b(?:distinct|unique|different)\b", q)):
            gp.distinct = ids[m.group(1)]
            gp.handled.add("distinct")
    from layer2.semantic import COVERED_BY_FILTER
    filter_keys = choose_filters(q, vcat) if vcat else []
    gp.where += [vcat["filters"][f]["sql"] for f in filter_keys]
    for f in filter_keys:
        gp.vocab += vcat["filters"][f].get("phrases", [])
    covered = set().union(*[COVERED_BY_FILTER.get(f, set()) for f in filter_keys]) if filter_keys else set()
    # values named in the question ("sms", "app", "mid_stage") and the share condition
    values = []
    for fld, value in _menu_rows(con, view):
        v = (value or "").lower()
        if len(v) >= 3 and not v.replace("-", "").isdigit() and fld in cols and fld not in gp.dims and \
                fld not in covered and (
                _phrase(q, v) or ("_" in v and _phrase(q, v.replace("_", " ")))):
            values.append((fld, value))
    if agg == "list":
        hits = []
        for c in numeric:
            if c in {t[0] for t in limits}:
                continue
            best = max((len(ph) for ph in _phrases(c) if _phrase(q, ph)), default=0)
            if best:
                hits.append((best, c))
        if not hits:
            return None
        gp.column = max(hits)[1]
        gp.vocab += _phrases(gp.column) + ["promise to pay", "pay", "record", "list", "who", "likely"]
        gp.order = gp.order or "desc"
        gp.limit = gp.limit or 10
        gp.handled |= {"list_records", "top_n"}
        for w in LIST_DEFAULT_WHERE.get(gp.column, []):
            if w not in gp.where:
                gp.where.append(w)
                gp.vocab += ["open"]
    if agg in ("avg", "median", "sum"):
        hits = []
        for c in numeric:
            if c in {t[0] for t in limits}:      # the threshold's column is a filter, not what is averaged
                continue
            best = max((len(ph) for ph in _phrases(c) if _phrase(q, ph)), default=0)
            if best:
                hits.append((best, c))
        if not hits:
            return None
        gp.column = max(hits)[1]
        gp.vocab += _phrases(gp.column)
    if agg == "share":
        # the condition is a yes/no column named after "were/are/was/is" ("links ... were clicked" -> link_clicked),
        # else the last named value; the others filter
        after_verb = [c for c, t in cols.items() if t == "BOOLEAN" and re.search(
            r"\b(?:were|was|are|is|got|get|been|being)\s+" + re.escape(c.replace("_flag", "").split("_")[-1]) + r"\b", q)]
        if after_verb:
            gp.condition, gp.condition_label = after_verb[0], after_verb[0]
            gp.vocab += [after_verb[0], after_verb[0].replace("_flag", "").split("_")[-1]]
        elif values:
            fld, value = values[-1]
            gp.condition, gp.condition_label = f"{fld} = '{value}'", f"{fld} = {value}"
            gp.vocab += [value, fld]
            values = values[:-1]
        else:
            flags = [c for c, t in cols.items() if t == "BOOLEAN" and _phrase(q, c.replace("_flag", "").replace("_", " "))]
            if not flags:
                return None
            gp.condition, gp.condition_label = flags[0], flags[0]
            gp.vocab += [flags[0]]
    for fld, value in values:
        gp.where.append(f"{fld} = '{value}'")
        gp.vocab += [value, fld]
    # a yes/no column the question names ("with a hardship flag", "on legal hold") is a filter, unless it is the
    # share's condition or a catalog filter already covers it
    for c, t in cols.items():
        name = c.replace("_flag", "").replace("is_", "").replace("_", " ")
        if t == "BOOLEAN" and c != gp.condition and c not in covered and len(name) >= 4 and \
                (_phrase(q, c.replace("_", " ")) or _phrase(q, name)) and c not in " ".join(gp.where):
            gp.where.append(c)
            gp.vocab += [c, name]
    tcol = TIME_DEFAULT.get(view)
    from layer2.semantic import time_key_for       # "cases that cured between 1 July and 28 September" -> cure date
    key = time_key_for(q, vcat) if vcat else None
    if key and vcat.get("time", {}).get(key) in cols:
        tcol = vcat["time"][key]
    if p.periods and tcol and tcol in cols:
        per = p.periods[0]
        gp.period_sql = f"{tcol} >= DATE '{per.start}' AND {tcol} < DATE '{per.end_exclusive()}'"
        for cues in vcat.get("time_cues", {}).values():
            gp.vocab += list(cues)
        gp.vocab.append(tcol)
    return gp


def _list_sql(gp: GenericPlan) -> str:
    cols = [c for c in LIST_COLUMNS[gp.view] if c != gp.column] + [gp.column]
    if gp.column == "break_prob":
        cols.append("break_reason_1 AS top_reason")
    where = list(dict.fromkeys(gp.where + ([gp.period_sql] if gp.period_sql else []) + [f"{gp.column} IS NOT NULL"]))
    return (f"SELECT {', '.join(cols)}\nFROM layer2.{gp.view}\nWHERE " + "\n  AND ".join(where) +
            f"\nORDER BY {gp.column} {(gp.order or 'desc').upper()}, {LIST_COLUMNS[gp.view][1]}\nLIMIT {gp.limit or 10}")


def to_sql(gp: GenericPlan) -> str:
    if gp.agg == "list":
        return _list_sql(gp)
    value = {"count": f"count(DISTINCT {gp.distinct})" if gp.distinct else "count(*)", "avg": f"round(avg({gp.column}), 2)", "median": f"round(median({gp.column}), 2)",
             "sum": f"round(sum({gp.column}), 2)",
             "share": f"round(100.0 * avg(CASE WHEN {gp.condition} THEN 1 ELSE 0 END), 2)"}[gp.agg]
    name = {"count": {"golden_customer_id": "customers", "account_id": "accounts", "case_id": "cases",
                       "ptp_id": "promises"}.get(gp.distinct, "count"), "avg": f"avg_{gp.column}", "median": f"median_{gp.column}", "sum": gp.column if gp.column and gp.column.startswith("total_") else f"total_{gp.column}",
            "share": "share_pct"}[gp.agg]
    select = gp.dims + [f"{value} AS {name}"] + (["count(*) AS n"] if gp.agg != "count" else [])
    where = list(dict.fromkeys(gp.where + ([gp.period_sql] if gp.period_sql else [])))
    sql = f"SELECT {', '.join(select)}\nFROM layer2.{gp.view}"
    if where:
        sql += "\nWHERE " + "\n  AND ".join(where)
    if gp.dims:
        idx = ", ".join(str(i + 1) for i in range(len(gp.dims)))
        order = f"{len(gp.dims) + 1} {gp.order.upper()} NULLS LAST, {idx}" if gp.order else idx
        sql += f"\nGROUP BY {idx}\nORDER BY {order}"
        if gp.limit:
            sql += f"\nLIMIT {gp.limit}"
    return sql
