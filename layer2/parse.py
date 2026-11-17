"""Step 0: read the question. IDs by regex; time periods resolved against the snapshot date, never the clock.

Every period has an inclusive `start` and `end` (dates). A period that would run past `as_of` is clipped to `as_of`:
"July-September 2026" asked on 28 Sep 2026 means 1 Jul to 28 Sep.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

SNAPSHOT = date(2026, 9, 28)

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_YEAR = r"(20\d\d)"

CASE_ID = re.compile(r"\bCS-\d{4}-\d{4,7}\b", re.I)
PTP_ID = re.compile(r"\bPTP-\d{5,}\b", re.I)
CONTACT_ID = re.compile(r"\bCT-\d{5,}\b", re.I)
GOLDEN_ID = re.compile(r"\bG[C]?-?\d{5,}\b")
DOC_ID = re.compile(r"\b(?:POL|PRC|SCR|HPT|REG|IR|REL|DC|FAQ)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
TRANSCRIPT_ID = re.compile(r"\btranscript\s+#?(\d{3,})\b", re.I)


@dataclass
class Period:
    start: date
    end: date            # inclusive
    label: str

    def end_exclusive(self) -> date:
        return self.end + timedelta(days=1)


@dataclass
class Parsed:
    question: str
    as_of: date
    case_ids: list = field(default_factory=list)
    ptp_ids: list = field(default_factory=list)
    golden_ids: list = field(default_factory=list)
    doc_ids: list = field(default_factory=list)
    transcript_ids: list = field(default_factory=list)
    periods: list = field(default_factory=list)       # in order found; a comparison adds a second period
    month_ends: list = field(default_factory=list)    # "end of March 2026" -> date(2026, 3, 31)
    within_days: int | None = None                    # "within 30 days"
    today: bool = False
    amounts: list = field(default_factory=list)


def _month(name: str) -> int:
    name = name.lower()
    return MONTHS.get(name, MONTHS.get(name[:3]))


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _clip(start: date, end: date, as_of: date, label: str) -> Period:
    return Period(start, min(end, as_of), label)


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def parse(question: str, as_of: date | str | None = None) -> Parsed:
    if isinstance(as_of, str) and as_of:
        as_of = date.fromisoformat(as_of)
    as_of = as_of or SNAPSHOT
    q = question.strip()
    ql = q.lower()
    p = Parsed(question=q, as_of=as_of)
    p.case_ids = [m.upper() for m in CASE_ID.findall(q)]
    p.ptp_ids = [m.upper() for m in PTP_ID.findall(q)]
    p.golden_ids = GOLDEN_ID.findall(q)
    p.doc_ids = DOC_ID.findall(q)
    p.transcript_ids = TRANSCRIPT_ID.findall(q)
    p.amounts = [float(a.replace(",", "")) for a in re.findall(r"\$\s?([\d,]+(?:\.\d+)?)", q)]
    p.today = bool(re.search(r"\b(today|right now|currently|as of now|at the moment)\b", ql))
    m = re.search(r"within (\d+) days?", ql)
    p.within_days = int(m.group(1)) if m else None

    used = []   # character spans already turned into periods

    def take(match):
        used.append(match.span())

    def free(match) -> bool:
        a, b = match.span()
        return all(b <= u0 or a >= u1 for u0, u1 in used)

    # month ends: "end of March 2026"
    for m in re.finditer(rf"end of {_MON}\s+{_YEAR}", ql):
        y, mo = int(m.group(2)), _month(m.group(1))
        p.month_ends.append(_month_end(y, mo))
        take(m)

    # day range in one month: "between 15 and 22 September 2026", "15-22 Sep 2026", "on 23-24 September 2026"
    for m in re.finditer(rf"(?:between\s+)?(\d{{1,2}})(?:\s*(?:-|–|to|and)\s*)(\d{{1,2}})\s+{_MON}\s+{_YEAR}", ql):
        if not free(m):
            continue
        y, mo = int(m.group(4)), _month(m.group(3))
        start, end = date(y, mo, int(m.group(1))), date(y, mo, int(m.group(2)))
        p.periods.append(_clip(start, end, as_of, f"{start.day}-{end.day} {calendar.month_abbr[mo]}"))
        take(m)

    # a date range across months: "between 1 July and 28 September 2026", "from 1 April to 30 June" (the year is the
    # one written, else the latest such date on or before the snapshot)
    for m in re.finditer(rf"(?:between|from)?\s*\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}(?:\s+{_YEAR})?\s*"
                         rf"(?:-|–|to|and|until|through|till)\s*(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}"
                         rf"(?:\s+{_YEAR})?", ql):
        if not free(m):
            continue
        m1, m2 = _month(m.group(2)), _month(m.group(5))
        y2 = int(m.group(6)) if m.group(6) else (as_of.year if m2 <= as_of.month else as_of.year - 1)
        y1 = int(m.group(3)) if m.group(3) else (y2 if m1 <= m2 else y2 - 1)
        try:
            start, end = date(y1, m1, int(m.group(1))), date(y2, m2, int(m.group(4)))
        except ValueError:
            continue
        if start <= end:
            p.periods.append(_clip(start, end, as_of, f"{start.isoformat()} to {end.isoformat()}"))
            take(m)

    # "the N days before" / "the previous N days" -> comparison period before the first period
    m = re.search(r"(?:the\s+)?(\d+)\s+days?\s+before|previous\s+(\d+)\s+days", ql)
    if m and p.periods:
        n = int(m.group(1) or m.group(2))
        first = p.periods[0]
        end = first.start - timedelta(days=1)
        start = end - timedelta(days=n - 1)
        p.periods.append(Period(start, end, f"{start.day}-{end.day} {calendar.month_abbr[end.month]}"))

    # "in the 12 months to 28 September 2026", "last 12 months"
    m = re.search(rf"(\d+)\s+months?\s+(?:to|ending|up to)\s+(\d{{1,2}})\s+{_MON}\s+{_YEAR}", ql)
    if m and free(m):
        end = date(int(m.group(4)), _month(m.group(3)), int(m.group(2)))
        start = _add_months(end, -int(m.group(1))) + timedelta(days=1)
        p.periods.append(_clip(start, end, as_of, f"{m.group(1)} months to {end.isoformat()}"))
        take(m)
    for m in re.finditer(r"(?:last|past|previous)\s+(\d+)\s+(day|week|month)s?", ql):
        if not free(m):
            continue
        n, unit = int(m.group(1)), m.group(2)
        start = as_of - timedelta(days=n - 1) if unit == "day" else (
            as_of - timedelta(weeks=n) + timedelta(days=1) if unit == "week" else _add_months(as_of, -n) + timedelta(days=1))
        p.periods.append(Period(start, as_of, m.group(0)))
        take(m)

    # month range: "July-September 2026", "July to September 2026"
    for m in re.finditer(rf"{_MON}\s*(?:-|–|to|through)\s*{_MON}\s+{_YEAR}", ql):
        if not free(m):
            continue
        y = int(m.group(3))
        start, end = date(y, _month(m.group(1)), 1), _month_end(y, _month(m.group(2)))
        p.periods.append(_clip(start, end, as_of, f"{calendar.month_abbr[start.month]}-{calendar.month_abbr[end.month]} {y}"))
        take(m)

    # quarter: "Q3 2026"
    for m in re.finditer(rf"\bq([1-4])\s+{_YEAR}", ql):
        if not free(m):
            continue
        y, qn = int(m.group(2)), int(m.group(1))
        start, end = date(y, 3 * qn - 2, 1), _month_end(y, 3 * qn)
        p.periods.append(_clip(start, end, as_of, f"Q{qn} {y}"))
        take(m)

    # quarter in words: "second quarter of 2026"
    for m in re.finditer(rf"\b(first|second|third|fourth|1st|2nd|3rd|4th) quarter(?: of)?\s+{_YEAR}", ql):
        if not free(m):
            continue
        qn = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}[m.group(1)]
        y = int(m.group(2))
        p.periods.append(_clip(date(y, 3 * qn - 2, 1), _month_end(y, 3 * qn), as_of, f"Q{qn} {y}"))
        take(m)

    # half year: "first half of 2026", "H2 2025"
    for m in re.finditer(rf"\b(?:(first|second|1st|2nd) half(?: of)?|h([12]))\s+{_YEAR}", ql):
        if not free(m):
            continue
        y, first = int(m.group(3)), (m.group(1) or "").startswith(("first", "1st")) or m.group(2) == "1"
        start, end = (date(y, 1, 1), date(y, 6, 30)) if first else (date(y, 7, 1), date(y, 12, 31))
        p.periods.append(_clip(start, end, as_of, f"H{1 if first else 2} {y}"))
        take(m)

    # single month: "September 2026", "in July 2026"
    for m in re.finditer(rf"{_MON}\s+{_YEAR}", ql):
        if not free(m):
            continue
        y, mo = int(m.group(2)), _month(m.group(1))
        p.periods.append(_clip(date(y, mo, 1), _month_end(y, mo), as_of, f"{calendar.month_name[mo]} {y}"))
        take(m)

    # a month without a year ("between July and August 2026", "July vs August"): the year of the next explicit
    # year in the question, else the latest such month on or before the snapshot. "may" is skipped (a verb too).
    found = list(p.periods)

    def inside(start: date, end: date) -> bool:   # restates a period already found, e.g. "Q2 2026 (April to June)"
        return any(f.start <= start and end <= f.end for f in found)

    def year_for(mo: int, after: int) -> int:
        nxt = re.search(r"\b(20\d{2})\b", ql[after:])
        return int(nxt.group(1)) if nxt else (as_of.year if mo <= as_of.month else as_of.year - 1)

    for m in re.finditer(rf"(?<!\d\s)(?<!\d)\b{_MON}\s*(?:-|–|to|through|until)\s*{_MON}\b(?!\s*\d)", ql):
        if not free(m):
            continue
        m1, m2 = _month(m.group(1)), _month(m.group(2))
        y2 = year_for(m2, m.end())
        y1 = y2 if m1 <= m2 else y2 - 1
        start, end = date(y1, m1, 1), _month_end(y2, m2)
        take(m)
        if not inside(start, end):
            p.periods.append(_clip(start, end, as_of, f"{calendar.month_abbr[m1]}-{calendar.month_abbr[m2]} {y2}"))
    for m in re.finditer(rf"(?<!\d\s)(?<!\d)\b{_MON}\b(?!\s*\d)", ql):
        if not free(m) or m.group(1) == "may" or not re.fullmatch(r"[a-z]+", m.group(1)):
            continue
        mo = _month(m.group(1))
        y = year_for(mo, m.end())
        take(m)
        if not inside(date(y, mo, 1), _month_end(y, mo)):
            p.periods.append(_clip(date(y, mo, 1), _month_end(y, mo), as_of, f"{calendar.month_name[mo]} {y}"))

    # relative: last month / this month / this year / this week / last week
    rel = {
        "last month": lambda: (_add_months(as_of.replace(day=1), -1), as_of.replace(day=1) - timedelta(days=1)),
        "this month": lambda: (as_of.replace(day=1), _month_end(as_of.year, as_of.month)),   # whole month: due dates
        "next month": lambda: (_add_months(as_of.replace(day=1), 1),                            # can be after today
                               _month_end(_add_months(as_of.replace(day=1), 1).year,
                                          _add_months(as_of.replace(day=1), 1).month)),
        "next week": lambda: (as_of + timedelta(days=7 - as_of.weekday()), as_of + timedelta(days=13 - as_of.weekday())),
        "next 7 days": lambda: (as_of, as_of + timedelta(days=6)),
        "this year": lambda: (date(as_of.year, 1, 1), as_of),
        "this week": lambda: (as_of - timedelta(days=as_of.weekday()), as_of + timedelta(days=6 - as_of.weekday())),
        "last week": lambda: (as_of - timedelta(days=as_of.weekday() + 7), as_of - timedelta(days=as_of.weekday() + 1)),
        "yesterday": lambda: (as_of - timedelta(days=1), as_of - timedelta(days=1)),
    }
    for phrase, fn in rel.items():
        for m in re.finditer(rf"\b{phrase}\b", ql):
            if free(m):
                s, e = fn()
                p.periods.append(Period(s, e, phrase))
                take(m)

    # bare year: "in 2026", "opened in 2025" (not part of a date already used)
    for m in re.finditer(rf"\b(?:(?:in|during|for|of|since)\s+(?:the\s+)?(?:(?:calendar|fiscal)\s+)?(?:year\s+)?|"
                         rf"(?:calendar\s+)?year\s+){_YEAR}\b", ql):
        if not free(m):
            continue
        y = int(m.group(1))
        start = date(y, 1, 1)
        end = as_of if "since" in m.group(0) else date(y, 12, 31)
        p.periods.append(_clip(start, end, as_of, str(y) if "since" not in m.group(0) else f"since {y}"))
        take(m)

    # single day: "on 14 September 2026", "28 September 2026"
    for m in re.finditer(rf"(\d{{1,2}})\s+{_MON}\s+{_YEAR}", ql):
        if not free(m):
            continue
        d = date(int(m.group(3)), _month(m.group(2)), int(m.group(1)))
        p.periods.append(Period(d, d, d.isoformat()))
        take(m)

    # the same dates written twice ("Q2 2026 (1 April to 30 June)") are one period, not a comparison
    seen, unique = set(), []
    for per in p.periods:
        if (per.start, per.end) not in seen:
            seen.add((per.start, per.end))
            unique.append(per)
    p.periods = unique
    return p
