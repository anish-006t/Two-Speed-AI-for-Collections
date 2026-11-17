"""Customer story retrieval (brief L2-8, light version): one customer's or case's notes, ranked for the question.

"Hardship notes for case CS-2026-614370 in the last 90 days" -> that case's (or customer's) redacted agent notes in
the period, ranked by BM25 plus the question's topic (English and French; notes of the matching type first), each
cited by note ID. System notes only when asked; skip-trace notes (locating contact details) only for agents. No vector store: one customer
has tens of notes, so ranking them on the fly is instant. Text only from silver.agent_notes_redacted; without it,
Layer 2 applies its local redaction to the note before showing it.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, timedelta

from layer2 import adapters
from layer2.call_summary import local_redact
from layer2.parse import Parsed

# A question's topic (English and French) -> the note type that records it. The ranking boosts notes of that type
# and notes whose text shows the topic; words are general vocabulary for each topic, not tuned to test questions.
QUESTION_TOPICS = {
    "hardship": r"hardship|hrdshp|money|financ|afford|income|\bjob|\bwork|laid off|unemploy|\bsick|\bill\b|health|medic|"
                r"hospital|hours|tight|struggl|difficult|bills|budget|\bei\b|benefit|lost|argent|revenu|emploi|travail|"
                r"chômage|malad|santé|difficile|budget",
    "dispute": r"disput|disagree|contest|wrong|error|mistake|\bfees?\b|charge|fraud|billing|incorrect|unfair|refund|"
               r"revers|frais|erreur|désaccord|litige|fraude|factur|rembours|conteste",
    "payment": r"\bpa(?:y|id|ying|yment|yments)\b|promis|\bptps?\b|instal|arrangement|\bplan\b|due date|deposit|"
               r"transfer|paiement|payer|versement|promesse|entente|échéance|montant",
    "escalation": r"escalat|complain|supervisor|manager|team lead|unhappy|angry|upset|rude|ombudsman|plainte|"
                  r"superviseur|gestionnaire|responsable|mécontent|insatisf",
    "contact": r"call ?back|voicemail|\bvm\b|no answer|\breach|\brpc\b|rappel|message",
}
TOPIC_TYPE = {"hardship": "hardship_assessment", "dispute": "dispute_note", "payment": "payment_note",
              "escalation": "supervisor_note"}


def _note_topic_rx() -> dict:
    """What each topic looks like inside a note (agent shorthand, English and French); reuses the call-summary rules,
    which already cover French hardship and dispute wording."""
    from layer2.call_summary import DISPUTE_RULES, REASON_RULES, TIGHT
    hardship = "|".join(rx for _, rx in REASON_RULES) + "|" + TIGHT + \
        r"|hardship|hrdshp|\bhs\b|afford|income|\bei\b|difficult|program|programme"
    dispute = "|".join(rx for _, rx in DISPUTE_RULES) + r"|disput|contest|wrong|error|reversal|refund|frais|erreur|litige"
    return {
        "hardship": re.compile(hardship, re.I),
        "dispute": re.compile(dispute, re.I),
        "payment": re.compile(r"\bptp|promis|\bpaid\b|\bpay|paym|instal|arrang|paiement|versement|promesse|entente", re.I),
        "escalation": re.compile(r"escalat|supervisor|superviseur|complain|plainte|manager|team lead|\btl\b|mécontent",
                                 re.I),
        "contact": re.compile(r"\bcb\b|call ?back|\bvm\b|voicemail|\blm\b|no answer|\brpc\b|rappel", re.I),
    }


_TOPIC_RX: dict = {}


def question_topics(question: str) -> list[str]:
    q = question.lower()
    return [t for t, rx in QUESTION_TOPICS.items() if re.search(rx, q)]


STORY_CUES = re.compile(r"\bnotes?\b|\bstory\b|\bhistory\b|what (?:has|did) (?:the )?(?:customer|they) (?:said|say|told)", re.I)


def is_story_question(p: Parsed) -> bool:
    return bool((p.case_ids or p.golden_ids) and STORY_CUES.search(p.question))


def _notes(con, p: Parsed):
    redacted = adapters.relation_exists(con, "silver", "agent_notes_redacted")
    table, text_col = ("silver.agent_notes_redacted", "note_text_redacted") if redacted else ("bronze.agent_notes", "note_text")
    if p.case_ids:
        where, args = "case_id = ?", [p.case_ids[0]]
    else:
        where = ("crm_customer_id IN (SELECT source_id FROM identity.crosswalk WHERE source_system = 'CRM' "
                 "AND golden_customer_id = ? AND valid_to IS NULL)")
        args = [p.golden_ids[0]]
    if p.periods:
        start, end = p.periods[0].start, p.periods[0].end_exclusive()
    else:   # no period asked: all of the case's notes up to the snapshot (a case has a handful of notes)
        start, end = date(2000, 1, 1), p.as_of + timedelta(days=1)
    sql = (f"SELECT note_id, case_id, CAST(try_cast(note_ts AS TIMESTAMP) AS DATE) AS note_date, note_type, {text_col} "
           f"FROM {table} WHERE {where} AND try_cast(note_ts AS TIMESTAMP) >= ? AND try_cast(note_ts AS TIMESTAMP) < ? "
           "ORDER BY note_ts DESC LIMIT 400")
    rows = con.execute(sql, args + [str(start), str(end)]).fetchall()
    shown_sql = sql.replace("?", "{}").format(*[f"'{a}'" for a in args + [start, end]])
    return rows, redacted, shown_sql, (start, end - timedelta(days=1))


STOP = {"notes", "note", "case", "customer", "last", "days", "what", "show", "with", "about", "from", "story", "have",
        "anything", "there", "they", "their", "this", "that", "were", "been", "does", "dans", "pour", "avec", "client",
        "dossier", "quoi", "sont", "cette", "notes", "history", "historique", "give", "list", "everything", "please",
        "recent", "latest", "months", "weeks"}


# Protected information that agent notes sometimes contain (team rule 2: never part of a Layer 2 answer). Masked in
# what is shown and in what the AI reads; the search itself still uses the full text. Hardship reasons that the
# bank's own summaries use (illness, disability, separation, job loss) stay visible. Vulnerability and mental health
# are masked for every role too (one agent role since 4 Oct); Agent Assist handles vulnerable-customer treatment.
PROTECTED_IN_NOTES = {
    "religion": r"\b(?:church|mosque|temple|synagogue|gurdwara|pray(?:ed|ing|er)?|religio\w*|faith|pastor|priest|imam|"
                r"rabbi|église|mosquée|prière|prier|religieu\w*|pasteur|prêtre)\b",
    "origin": r"\b(?:newcomer|immigra\w*|refugee|citizenship|citizen|work permit|ethnic\w*|race|racial|"
              r"accent|nouvel(?:le)? arrivant\w*|réfugié\w*|citoyenneté|permis de travail|ethni\w*)\b",
    "pregnancy": r"\b(?:pregnan\w*|enceinte|grossesse|maternity|maternité)\b",
    "age": r"\b(?:\d{2} ?(?:years? old|yo|ans)|elderly|senior citizen|retiree|âgée?|personne âgée)\b",
    "family": r"\b(?:wife|husband|spouse|partner|conjoint\w*|épou\w*|mari|femme|married|marié\w*)\b",
}
VULNERABILITY_IN_NOTES = (r"\b(?:vulnerab\w*|vulnérab\w*|anxi\w*|depress\w*|mental health|suicid\w*|cognitive|"
                          r"dementia|crying|in tears|en pleurs|pleur\w*|santé mentale|very stressed|stressé\w*)\b")
_MASKS: dict = {}


def mask(text: str, role: str = "analyst") -> str:
    """Replace protected mentions, vulnerability and mental health with [protected], for every role (team rule 2:
    never part of a Layer 2 answer; Agent Assist handles hardship and vulnerability on its own)."""
    if not _MASKS:
        _MASKS.update({k: re.compile(rx, re.I) for k, rx in PROTECTED_IN_NOTES.items()})
        _MASKS["vulnerability"] = re.compile(VULNERABILITY_IN_NOTES, re.I)
    out = text or ""
    for k, rx in _MASKS.items():
        out = rx.sub("[protected]", out)   # one neutral tag, so nothing can be inferred from the tag itself
    return out


def protected_leak(text: str, role: str = "analyst") -> bool:
    """True when an answer mentions protected information (or vulnerability, for analysts). The mask tags themselves
    ("[protected]", "[vulnerability]") are not a leak."""
    plain = re.sub(r"\[(?:protected|vulnerability)\]", " ", text or "")
    return mask(plain, role) != plain


def visible(rows, question: str, role: str):
    """System notes only when asked about; skip-trace notes (locating a customer's contact details) only for agents."""
    q = question.lower()
    keep = []
    for r in rows:
        if not (r[4] or "").strip():                 # empty note text: nothing to show or search
            continue
        kind = (r[3] or "").lower()
        if kind == "system_note" and not re.search(r"system|automat|auto-", q):
            continue
        if kind == "skip_trace" and role != "agent":
            continue
        keep.append(r)
    return keep


def _rank(question: str, rows, use_types: bool = True):
    """Keyword (BM25) score + a boost for notes of the question's topic type + a boost when the note's text shows the
    topic (English and French). No topic and no keywords: most recent first."""
    if not _TOPIC_RX:
        _TOPIC_RX.update(_note_topic_rx())
    q = question.lower()
    topics = question_topics(question)
    terms = [w for w in re.findall(r"[a-zà-ÿ]{4,}", q) if w not in STOP]
    docs = [re.findall(r"[a-zà-ÿ0-9]+", (r[4] or "").lower()) for r in rows]
    n, avg = len(docs), (sum(map(len, docs)) / max(len(docs), 1)) or 1
    df = Counter(t for d in docs for t in set(d))
    if not terms and not topics:   # no topic: most recent first
        return [(0.0, r) for r in rows]
    scored = []
    for r, d in zip(rows, docs):
        tf = Counter(d)
        score = sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
                    for t in set(terms) if t in tf)
        for t in topics:
            if use_types and TOPIC_TYPE.get(t) and (r[3] or "") == TOPIC_TYPE[t]:
                score += 2.0
            if _TOPIC_RX[t].search(r[4] or ""):
                score += 1.0
        scored.append((score, r))
    ranked = [x for x in sorted(scored, key=lambda x: -x[0]) if x[0] > 0]
    if not ranked and not topics:   # only general words ("show the notes history"): a listing, most recent first
        return [(0.0, r) for r in rows]
    return ranked


ASKING = re.compile(r"^\s*(?:what|why|did|does|do|has|have|had|is|was|were|are|any|how|when|which|who|can|could|"
                    r"quel|quelle|quels|pourquoi|est-ce|a-t-il|a-t-elle|y a-t-il|comment)\b", re.I)


def asks(question: str) -> bool:
    """An asking question ("what has the customer said about…") rather than a listing ("show the notes for…")."""
    if re.search(r"\b(?:summari[sz]e|summary|explain|describe|tell me|résume|résumer|expliquer)\b", question, re.I):
        return True
    return bool(ASKING.search(question)) or question.strip().endswith("?") and not re.match(
        r"\s*(?:show|list|give|display|get)\b", question, re.I)


def _rank_hybrid(question: str, rows, use_types: bool = True, mode: str = "tiebreak"):
    """Keyword + vectors (rag_plan.md §3.4).
    tiebreak (default): keyword ranking first; vectors order notes with equal keyword scores and rank the notes
    keyword search missed, after the ones it found. Chosen on a fresh sample (seed 11) over rrf, which lowered MRR.
    rrf: reciprocal-rank fusion with equal weights."""
    from layer2 import vectors
    kw = _rank(question, rows, use_types=use_types)
    cos = vectors.note_scores(question, rows)
    if mode == "tiebreak":
        kws = {r[0]: sc for sc, r in kw}
        return sorted(((kws.get(r[0], 0.0) + 1e-3 * cos.get(r[0], 0.0), r) for r in rows),
                      key=lambda x: (-(x[1][0] in kws), -x[0]))
    kw_rank = {r[0]: i for i, (_, r) in enumerate(kw)}
    vec_rank = {nid: i for i, nid in enumerate(sorted(cos, key=lambda n: -cos[n]))}
    big = len(rows) + 60
    fused = [(1 / (60 + kw_rank.get(r[0], big)) + 1 / (60 + vec_rank.get(r[0], big)), r) for r in rows]
    return sorted(fused, key=lambda x: -x[0])


FRENCH = re.compile(r"\b(?:parlé|client(?:e)?\s+(?:dit|veut|va)|paiement|pas de|il y a|veut|rappel|message laissé)\b",
                    re.I)


def use_vectors(question: str, rows, ranked) -> bool:
    """Gate 2: vectors only for an asking question, when keyword search finds fewer than 3 notes, or when the notes
    include French. Plain listings never load the model."""
    from layer2 import vectors
    if not vectors.enabled() or len(rows) < 2:
        return False
    if not question_topics(question) and not [w for w in re.findall(r"[a-zà-ÿ]{4,}", question.lower())
                                              if w not in STOP]:
        return False                                  # a plain listing ("show the notes"): most recent first
    french = sum(bool(FRENCH.search(r[4] or "")) for r in rows) > 0
    return (asks(question) or len(ranked) < 3 or french) and vectors.available()


ANSWER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer", "cited", "covered"],
                 "properties": {"answer": {"type": "string"}, "cited": {"type": "array", "items": {"type": "string"}},
                                "covered": {"type": "boolean"}}}
ANSWER_SYSTEM = ("You answer a bank collections analyst's question using ONLY the agent notes given. Notes are short "
                 "agent shorthand, English or French, with personal data replaced by tags like <PERSON> and "
                 "protected information replaced by [protected]; never guess what a tag hides. Do not describe the "
                 "customer's health, mental state, vulnerability, religion, origin, age or family; state only the "
                 "financial facts (hardship reason such as illness or job loss, amounts, dates, agreed actions). "
                 "Answer in "
                 "English, in 1-3 short sentences. Cite every fact with its note ID in square brackets, e.g. "
                 "[N-1234]. Use only numbers and dates that appear in the notes. If the notes do not answer the "
                 "question, set covered=false and say so. Do not speculate beyond the notes and never output "
                 "personal data.")


def written_answer(question: str, top, role: str = "analyst") -> tuple[str | None, str]:
    """Gate 3 (rag_plan.md §3.5): one LLM call over the top notes; kept only if it cites only these notes, every
    number is in them, and it holds no personal data. Returns (answer, problem)."""
    from layer2 import llm_guard
    ids = {r[0] for _, r in top}
    notes = "\n".join(f"[{r[0]}] {r[2]} {r[3]}: {(r[4] or '').strip()}" for _, r in top)
    try:
        out = adapters.complete(f"NOTES\n{notes}\n\n{llm_guard.wrap_question(question)}",
                                system=ANSWER_SYSTEM + " " + llm_guard.QUESTION_RULE, json_schema=ANSWER_SCHEMA,
                                max_tokens=400)
    except adapters.LLMUnavailable as e:
        return None, f"AI unavailable: {str(e)[:100]}"
    text = " ".join(out["answer"].split())
    named = set(re.findall(r"\bN-\d+\b", text)) | set(out["cited"])
    if not out["covered"]:
        return "The notes do not answer this question.", ""
    problem = ""
    if not named or not named <= ids:
        problem = "cited notes that were not retrieved" if named else "no note cited"
    elif llm_guard.ungrounded_numbers(re.sub(r"\bN-\d+\b", " ", text), [notes], question):
        problem = "numbers not in the notes"
    elif llm_guard.pii_leaks(text):
        problem = "personal data"
    elif protected_leak(text, role):
        problem = "protected information"
    if problem:
        adapters.audit({"action": "guardrail", "lane": "notes", "question": question, "problem": problem})
        return None, problem
    if not re.search(r"\bN-\d+\b", text):          # the citations were only in the structured field: show them
        text += " [" + "] [".join(dict.fromkeys(out["cited"])) + "]"
    return text, ""


def answer_story(p: Parsed, role: str = "analyst", use_model: bool = True) -> dict:
    con = adapters.connect(read_only=True)
    try:
        rows, redacted, sql, (start, end) = _notes(con, p)
    finally:
        con.close()
    who = p.case_ids[0] if p.case_ids else p.golden_ids[0]
    rows = visible(rows, p.question, role)
    if not rows:
        return {"answer": f"No agent notes for {who} between {start} and {end}.", "sql_or_sources": sql, "refused": False,
                "reason": "no notes in period", "route": "lookup", "table": None}
    ranked = _rank(p.question, rows)
    engine = "bm25"
    if use_vectors(p.question, rows, ranked):
        try:
            ranked, engine = _rank_hybrid(p.question, rows), "bm25+vectors"
        except Exception as e:  # noqa: BLE001 - the model or store failed: keep the keyword ranking
            adapters.audit({"action": "vectors", "problem": f"{type(e).__name__}: {e}"[:300]})
    ranked = [(sc, (r[0], r[1], r[2], r[3], mask(r[4], role))) for sc, r in ranked[:8]]
    if not ranked:
        return {"answer": f"{len(rows)} notes for {who} between {start} and {end}, none about that topic.",
                "sql_or_sources": sql, "refused": False, "reason": "no matching notes", "route": "lookup", "table": None}
    lines, table = [], []
    for _, (note_id, case_id, day, kind, text) in ranked:
        text = text if redacted else local_redact(text or "")
        lines.append(f"{day} [{note_id}] {(text or '').strip()}")
        table.append({"note_id": note_id, "case_id": case_id, "date": str(day), "type": kind, "text": text})
    span = f"all dates to {end}" if start.year <= 2000 else f"{start} to {end}"
    answer = f"{len(ranked)} of {len(rows)} notes for {who} ({span}), most relevant first:\n" + "\n".join(lines)
    written, problem = (None, "")
    if asks(p.question) and use_model and adapters.llm_available() and (question_topics(p.question) or [
            w for w in re.findall(r"[a-zà-ÿ]{4,}", p.question.lower()) if w not in STOP]):
        written, problem = written_answer(p.question, ranked[:5], role)
        if written:
            answer, engine = f"{written}\n\n" + answer, engine + "+llm"
        elif problem:
            engine += f" (AI answer dropped: {problem})"
    return {"answer": answer, "sql_or_sources": "notes " + ", ".join(t["note_id"] for t in table) + f"\n{sql}",
            "refused": False, "reason": "customer story from redacted agent notes", "route": "lookup", "table": table,
            "engines": {"story": engine + ("" if redacted else " (local redaction)")}}
