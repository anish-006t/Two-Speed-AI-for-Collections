"""Pre-call brief for one collections case (L4-3).

build_brief(case_id) gathers what an agent needs before dialling: who the customer is (identity confidence), what
must stop or change the call (flags, each with the policy it comes from), the debt (held back until identity is
verified when verify_before_disclosure is true), recent promises and contacts, last approved summaries, promise-break
risk with three reasons, and the hardship options policy allows. Options come from bronze.hardship_programs and
rules here, never from an LLM.

Reads access_agent.case_brief (Layer 1's agent view: masked phone and email, no DOB) plus gold tables by ID. Never
reads restricted.*; vulnerability and other protected attributes are not in any table read here.
"""
from datetime import date, timedelta

from layer1.pipeline import config as l1
from layer4.models.promise_break.score import score_promise
from shared import db

AS_OF_DEFAULT = l1.SNAPSHOT_DATE  # the release is a snapshot; "today" in the data is 28 Sep 2026
HARDSHIP_LEVELS = {"none": 0, "possible": 1, "clear": 2}

POLICY = {
    "insolvency": "POL-COLL-001 §7.3",
    "insolvency_programs": "POL-COLL-004 §4.2",
    "cease": "POL-COLL-001 §5.4",
    "representative": "POL-COLL-001 §5.4",
    "consent": "POL-COLL-001 §5.3",
    "hours": "POL-COLL-001 §5.1",
    "frequency": "POL-COLL-001 §5.2",
    "language": "POL-COLL-001 §5.5",
    "hardship": "POL-COLL-004 §4.2",
    "evidence": "POL-COLL-004 §4.3",
    "dispute": "PRC-COLL-012 §6.1",
    "incident_fee": "PRC-COLL-012 §6.2",
    "complaint": "POL-COLL-007 §8.1",
}

# Debt details: held back until identity is verified when verify_before_disclosure is true.
DEBT_CASE_FIELDS = ("current_bucket", "current_dpd", "total_overdue", "total_exposure", "current_ptp_id",
                    "current_ptp_amount", "current_ptp_due_date")


def _rows(con, sql, params=()):
    cur = con.execute(sql, list(params))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _one(con, sql, params=()):
    rows = _rows(con, sql, params)
    return rows[0] if rows else None


def _true(v) -> bool:
    return v is True or str(v).strip().lower() == "true"


def _num(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def list_open_cases(con, agent_id: str | None = None, limit: int = 200) -> list[dict]:
    """Open cases (outcome not set), newest first; for the agent role, only the agent's own cases."""
    where, params = "WHERE k.outcome IS NULL", []
    if agent_id:
        where += " AND k.assigned_agent_id = ?"
        params.append(agent_id)
    return _rows(con, f"""SELECT k.case_id, k.assigned_agent_id, k.queue, k.case_status, k.primary_product
                          FROM gold.cases k {where} ORDER BY k.case_open_date DESC LIMIT ?""", params + [limit])


def agents_with_open_cases(con, limit: int = 500) -> list[str]:
    return [r[0] for r in con.execute("""SELECT assigned_agent_id FROM gold.cases WHERE outcome IS NULL
                                         AND assigned_agent_id IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC
                                         LIMIT ?""", [limit]).fetchall()]


def build_brief(case_id: str, con=None, as_of: str | None = None, identity_verified: bool = False,
                agent_id: str | None = None) -> dict:
    """Everything the brief shows, as plain data. agent_id: when given, the case must be assigned to that agent."""
    con = con or db.connect()
    as_of = as_of or AS_OF_DEFAULT
    brief = _one(con, "SELECT * FROM access_agent.case_brief WHERE case_id = ?", [case_id])
    if brief is None:
        raise LookupError(f"No case {case_id}")
    if agent_id and brief["assigned_agent_id"] != agent_id:
        raise PermissionError(f"Case {case_id} is not assigned to agent {agent_id}")
    case = _one(con, """SELECT case_id, case_status, outcome, case_open_date, primary_account_id, primary_product,
                               account_id_list, hardship_flag, hardship_source, hardship_reason, hardship_plan_type,
                               hardship_plan_start, hardship_plan_end, payment_plan_flag, dispute_flag, dispute_type,
                               dispute_status, complaint_flag, complaint_stage, cease_contact_flag,
                               third_party_rep_flag, insolvency_hold_flag, deceased_hold_flag, legal_status,
                               contact_cap_7d, contacts_last_7d, promises_kept, promises_broken, promises_open,
                               last_contact_ts, next_scheduled_action
                        FROM gold.cases WHERE case_id = ?""", [case_id])
    gid = brief["golden_customer_id"]
    locked = _true(brief["verify_before_disclosure"]) and not identity_verified

    hardship = hardship_level(con, brief, case)
    accounts = _accounts(con, gid, case)
    promises = _promises(con, case_id, as_of)
    debt = {
        "case": {f: brief.get(f) for f in DEBT_CASE_FIELDS},
        "accounts": accounts,
        "promises": promises,
        "risk": promise_risk(gid, brief, as_of),
    }
    return {
        "case_id": case_id,
        "as_of": as_of,
        "customer": {
            "golden_customer_id": gid,
            "name": " ".join(x for x in (brief.get("first_name"), brief.get("last_name")) if x) or None,
            "phone_masked": brief.get("phone_masked"),
            "best_reachable_phone_masked": brief.get("best_reachable_phone_masked"),
            "email_masked": brief.get("email_masked"),
            "identity_confidence": brief.get("identity_confidence"),
            "verify_before_disclosure": _true(brief.get("verify_before_disclosure")),
            "province_code": brief.get("province_code"),
            "time_zone": brief.get("time_zone"),
            "permitted_hours": _hours(brief),
        },
        "case": {"case_status": case.get("case_status"), "queue": brief.get("queue"),
                 "assigned_agent_id": brief.get("assigned_agent_id"), "case_open_date": case.get("case_open_date"),
                 "primary_product": case.get("primary_product"), "legal_status": case.get("legal_status"),
                 "next_scheduled_action": case.get("next_scheduled_action"),
                 "promises_kept": case.get("promises_kept"), "promises_broken": case.get("promises_broken")},
        "flags": flags(brief, case),
        "hardship": hardship,
        "options": hardship_options(con, gid, case, accounts, hardship["level"], as_of, brief),
        "contacts": _contacts(con, case_id, as_of),
        "summaries": approved_summaries(con, gid),
        "debt_locked": locked,
        "debt": None if locked else debt,
    }


def _hours(brief) -> str | None:
    start, end = brief.get("permitted_call_start_local"), brief.get("permitted_call_end_local")
    if not (start and end):
        return None
    sunday = "Sunday 13:00-17:00 allowed" if _true(brief.get("sunday_contact_permitted")) else "no Sunday calls"
    return f"{start}-{end} local ({brief.get('time_zone')}); {sunday}"


def flags(brief: dict, case: dict) -> list[dict]:
    """Things that stop or change the call. level: stop (do not proceed), caution (change how), info."""
    out = []

    def add(level, title, action, policy=None):
        out.append({"level": level, "title": title, "action": action, "policy": policy})

    insolvent = [n for n, v in (("bankruptcy", brief.get("bankruptcy_flag")),
                                ("consumer proposal", brief.get("consumer_proposal_flag")),
                                ("insolvency hold on the case", case.get("insolvency_hold_flag"))) if _true(v)]
    if insolvent:
        add("stop", f"Insolvency: {', '.join(insolvent)}",
            "Stay of proceedings: no collection contact. Hardship programmes do not apply.", POLICY["insolvency"])
    if _true(brief.get("deceased_flag")) or _true(case.get("deceased_hold_flag")):
        add("stop", "Customer recorded as deceased", "Do not call. Send the case to the team lead for estate handling.")
    if _true(brief.get("cease_communication_flag")) or _true(case.get("cease_contact_flag")):
        add("stop", "Cease-communication request", "No calls, texts or emails; required letters only.",
            POLICY["cease"])
    reps = [n for n, v in (("lawyer or legal representative", brief.get("legal_representative_flag")),
                           ("credit counselling agency", brief.get("credit_counselling_agency_flag")),
                           ("third-party representative on the case", case.get("third_party_rep_flag"))) if _true(v)]
    if reps:
        add("stop", f"Represented: {', '.join(reps)}",
            "Contact the representative only; never disclose debt details to anyone else.", POLICY["representative"])
    if not _true(brief.get("consent_call")):
        add("stop", "No consent to call", "Do not call; use a channel the customer consented to.", POLICY["consent"])
    if _true(brief.get("internal_do_not_call_flag")):
        add("stop", "Internal do-not-call flag", "Do not call this customer.", POLICY["consent"])
    cap, used = _num(case.get("contact_cap_7d")) or 3, _num(case.get("contacts_last_7d"))
    if used is not None and used >= cap:
        add("stop", f"Contact limit reached ({int(used)} of {int(cap)} attempts in 7 days)",
            "No more outbound attempts until the 7-day window allows one.", POLICY["frequency"])
    if _true(case.get("dispute_flag")) and (case.get("dispute_status") or "open") == "open":
        add("caution", f"Open dispute{': ' + case['dispute_type'] if case.get('dispute_type') else ''}",
            "Do not ask for the disputed amount until the dispute is resolved; fraud claims go to the fraud team.",
            POLICY["dispute"])
    if _true(brief.get("incident_affected")):
        add("caution", "Affected by incident INC-0914 (app outage, 14 Sep)",
            "Late fees from the outage are reversed without proof of payment; a promise broken that day may have "
            "been paid the next day.", POLICY["incident_fee"])
    if _true(case.get("complaint_flag")):
        add("caution", f"Complaint on file{' (' + case['complaint_stage'] + ')' if case.get('complaint_stage') else ''}",
            "Acknowledge the complaint and follow the complaints process.", POLICY["complaint"])
    if not brief.get("golden_customer_id"):
        add("caution", "Customer not matched to a golden record",
            "The case points to a CRM record that does not exist or was deleted: confirm who the customer is before "
            "discussing anything, and tell the data team.")
    if _true(brief.get("case_link_alarm")):
        add("caution", "Account link alarm",
            "An account on this case may belong to a different customer: confirm which accounts are theirs before "
            "discussing them.")
    if _true(brief.get("hardship_flag")) or _true(case.get("hardship_flag")):
        add("caution", "Hardship recorded", "Offer an eligible option before asking for payment; do not ask for a "
            "payment the customer said they cannot afford.", POLICY["hardship"])
    if (brief.get("preferred_language") or "").upper().startswith("FR"):
        add("info", "French service", "Contact in French (Montréal site).", POLICY["language"])
    hours = _hours(brief)
    if hours:
        add("info", "Permitted call hours", hours, POLICY["hours"])
    order = {"stop": 0, "caution": 1, "info": 2}
    return sorted(out, key=lambda f: order[f["level"]])


def hardship_level(con, brief: dict, case: dict) -> dict:
    """Recorded hardship (case or account flag) counts as clear. Otherwise the latest Layer 3 text answer, when that
    table exists. Otherwise unknown: the call may reveal it."""
    if _true(case.get("hardship_flag")) or _true(brief.get("hardship_flag")):
        reason = case.get("hardship_reason")
        return {"level": "clear", "source": f"recorded ({case.get('hardship_source') or 'flag'})",
                "reason": reason, "evidence": None}
    text = _text_hardship(con, brief["golden_customer_id"])
    if text:
        return text
    return {"level": None, "source": "not recorded", "reason": None, "evidence": None}


def _text_hardship(con, gid):
    try:
        if not db.table_exists(con, "layer3", "text_features"):
            return None
        row = _one(con, """SELECT hardship, hardship__prob AS hardship_prob, event_ts, source_type FROM layer3.text_features
                           WHERE golden_customer_id = ? AND hardship IS NOT NULL ORDER BY event_ts DESC LIMIT 1""",
                   [gid])
    except Exception:  # noqa: BLE001 - Layer 3's table may not have these columns yet
        return None
    if not row or row["hardship"] not in HARDSHIP_LEVELS:
        return None
    return {"level": row["hardship"], "source": f"{row['source_type']} on {str(row['event_ts'])[:10]} "
            f"(model, p={_num(row['hardship_prob']) or 0:.2f})", "reason": None, "evidence": None}


def _accounts(con, gid, case) -> list[dict]:
    ids = list(case.get("account_id_list") or []) or [case.get("primary_account_id")]
    return _rows(con, f"""SELECT account_id, product, product_type, account_status, open_date, balance, credit_limit,
                                 past_due_amount, dpd, dpd_bucket_recomputed AS dpd_bucket, dpd_missing,
                                 hardship_program_flag, chargeoff_flag
                          FROM gold.c360_accounts
                          WHERE account_id IN ({', '.join('?' for _ in ids)}) ORDER BY account_id""", ids)


def _promises(con, case_id, as_of) -> list[dict]:
    return _rows(con, """SELECT ptp_id, ptp_created_ts, ptp_amount, ptp_due_date, ptp_status_adjusted AS ptp_status,
                                amount_paid_against, ptp_channel, adjustment_reason
                         FROM gold.promises_to_pay
                         WHERE case_id = ? AND try_cast(ptp_created_ts AS TIMESTAMP)::DATE <= ?::DATE
                         ORDER BY ptp_created_ts DESC LIMIT 5""", [case_id, as_of])


def _contacts(con, case_id, as_of) -> list[dict]:
    return _rows(con, """SELECT contact_ts_utc, direction, channel_resolved AS channel, outcome_desc, rpc_flag,
                                ptp_made_flag, dispute_raised_flag, hardship_mentioned_flag
                         FROM gold.contact_timeline
                         WHERE case_id = ? AND try_cast(contact_ts_utc AS TIMESTAMP)::DATE <= ?::DATE
                         ORDER BY contact_ts_utc DESC LIMIT 5""", [case_id, as_of])


def approved_summaries(con, gid, limit: int = 3) -> list[dict]:
    """Last approved post-call summaries for the customer (layer4.summaries, from L4-5). Empty until it exists."""
    try:
        if not db.table_exists(con, "layer4", "summaries"):
            return []
        return _rows(con, """SELECT case_id, approved_at, approved_by, narrative FROM layer4.summaries
                             WHERE golden_customer_id = ? AND status = 'approved'
                             ORDER BY approved_at DESC LIMIT ?""", [gid, limit])
    except Exception:  # noqa: BLE001 - schema arrives with L4-5
        return []


def promise_risk(gid, brief, as_of) -> dict:
    """Risk on the open promise; without one, on a what-if promise of the overdue amount due in 7 days."""
    if not gid:
        return {"available": False, "reason": "No golden customer record for this case, so no history to score"}
    amount, due = _num(brief.get("current_ptp_amount")), brief.get("current_ptp_due_date")
    what_if = not (amount and due and str(due)[:10] >= as_of)
    if what_if:
        amount = _num(brief.get("total_overdue")) or 0.0
        due = (date.fromisoformat(as_of) + timedelta(days=7)).isoformat()
    if not amount:
        return {"available": False, "reason": "No open promise and no overdue amount to score"}
    try:
        out = score_promise(gid, float(amount), str(due)[:10], as_of)
    except Exception as e:  # noqa: BLE001 - a failing model must never take the brief down
        return {"available": False, "reason": f"Risk model unavailable ({type(e).__name__})"}
    return {"available": True, "what_if": what_if, "amount": amount, "due_date": str(due)[:10], **out}


def _months_between(start, end: date) -> int | None:
    if not start:
        return None
    s = start if isinstance(start, date) else date.fromisoformat(str(start)[:10])
    return (end.year - s.year) * 12 + end.month - s.month - (end.day < s.day)


def hardship_options(con, gid, case, accounts, level, as_of, brief) -> list[dict]:
    """Every programme in force on as_of for the case's product, with eligible / ineligible / check and why.
    Unknown inputs are 'check' with what to ask; nothing is guessed."""
    today = date.fromisoformat(as_of)
    product = case.get("primary_product")
    programs = _rows(con, """SELECT * FROM bronze.hardship_programs
                             WHERE try_cast(effective_from AS DATE) <= ?::DATE
                               AND try_cast(effective_to AS DATE) >= ?::DATE ORDER BY program_id""", [as_of, as_of])
    primary = next((a for a in accounts if a["account_id"] == case.get("primary_account_id")), None)
    mob = _months_between(primary and primary.get("open_date"), today)
    dpd = _num(brief.get("current_dpd"))
    insolvent = any(_true(v) for v in (brief.get("bankruptcy_flag"), brief.get("consumer_proposal_flag"),
                                       case.get("insolvency_hold_flag")))
    history = _offer_history(con, gid, as_of)
    out = []
    for p in programs:
        products = _json_list(p["product_types"])
        if product not in products:
            continue
        why_not, checks, notes = [], [], []
        if insolvent and _true(p["excludes_insolvency"]):
            why_not.append("customer is in bankruptcy, a consumer proposal or an insolvency hold")
        need = p["min_hardship_level"] or "none"
        if HARDSHIP_LEVELS.get(need, 0) > 0:
            if level is None:
                checks.append(f"needs {need} hardship: confirm on the call")
            elif HARDSHIP_LEVELS[level] < HARDSHIP_LEVELS[need]:
                why_not.append(f"needs {need} hardship; recorded level is {level}")
        max_dpd = _num(p["max_dpd"])
        if max_dpd is not None:
            if dpd is None:
                checks.append(f"days past due unknown (limit {int(max_dpd)})")
            elif dpd > max_dpd:
                why_not.append(f"{int(dpd)} days past due is over the limit of {int(max_dpd)}")
        min_mob = _num(p["min_months_on_book"])
        if min_mob:
            if mob is None:
                checks.append(f"account age unknown (needs {int(min_mob)} months)")
            elif mob < min_mob:
                why_not.append(f"account open {mob} months; needs {int(min_mob)}")
        max_uses = _num(p["max_uses_12m"])
        used = history["accepted_12m"].get(p["program_id"], 0)
        if max_uses is not None and used >= max_uses:
            why_not.append(f"already used {used} time(s) in the last 12 months (limit {int(max_uses)})")
        if _true(p["excludes_broken_plan_90d"]) and history["broken_90d"]:
            why_not.append("a hardship plan was broken in the last 90 days")
        if _true(p["requires_income_evidence"]):
            notes.append(f"needs income and expense evidence ({POLICY['evidence']})")
        if p["approver_level"] and p["approver_level"] != "agent":
            notes.append(f"approval by {p['approver_level'].replace('_', ' ')}")
        if p["program_id"] == "HP-DMP" and not _true(brief.get("credit_counselling_agency_flag")):
            checks.append("only when a non-profit credit counsellor represents the customer")
        status = "ineligible" if why_not else "check" if checks else "eligible"
        out.append({"program_id": p["program_id"], "version": p["version"], "name": p["program_name"],
                    "description": p["description"], "status": status, "why_not": why_not, "checks": checks,
                    "notes": notes, "policy": f"POL-COLL-004 {_section(p['policy_section'])}"})
    rank = {"eligible": 0, "check": 1, "ineligible": 2}
    return sorted(out, key=lambda o: (rank[o["status"]], o["program_id"]))


def _offer_history(con, gid, as_of) -> dict:
    """Hardship offers on any of the customer's accounts or cases: accepted in 12 months, broken plans in 90 days."""
    rows = _rows(con, """SELECT o.program_id, o.customer_response, o.plan_status,
                                try_cast(coalesce(o.response_ts, o.offer_ts) AS TIMESTAMP)::DATE AS d
                         FROM bronze.offers o
                         WHERE (o.account_id IN (SELECT account_id FROM gold.c360_accounts WHERE golden_customer_id = ?)
                                OR o.case_id IN (SELECT case_id FROM gold.cases WHERE golden_customer_id = ?))
                           AND try_cast(o.offer_ts AS TIMESTAMP)::DATE <= ?::DATE""", [gid, gid, as_of])
    today = date.fromisoformat(as_of)
    accepted, broken = {}, False
    for r in rows:
        if r["d"] is None:
            continue
        if r["customer_response"] == "accepted" and r["d"] >= today - timedelta(days=365):
            accepted[r["program_id"]] = accepted.get(r["program_id"], 0) + 1
        if r["plan_status"] == "broken" and r["d"] >= today - timedelta(days=90):
            broken = True
    return {"accepted_12m": accepted, "broken_90d": broken}


def _json_list(v) -> list:
    import json
    if isinstance(v, list):
        return v
    try:
        return json.loads(v) if v else []
    except (TypeError, ValueError):
        return [x.strip() for x in str(v).split(",") if x.strip()]


def _section(s) -> str:
    s = (s or "").strip()
    return "§" + s.lstrip("§�").strip() if s else ""
