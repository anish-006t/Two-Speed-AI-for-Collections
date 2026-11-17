"""Policy lane (architecture §8): answers from the bank's documents, newest official version first, with citations.

The whole corpus is ~2,250 words, so instead of hoping retrieval finds the right chunk, the LLM sees every current
section (labelled with its document, section, type and version) plus the superseded ones clearly marked. Code then
checks every citation exists. Without an LLM, BM25 picks the best current section (Laya can rerank the top few) and
the answer quotes it. If no approved document covers the question, the lane refuses rather than guess.
"""
from __future__ import annotations

import csv
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from layer2 import adapters, llm_guard
from layer2.adapters import REPO
from layer2.parse import Parsed

RELEASE = REPO / "data" / "raw" / "maple_collections_release"
# Lower rank wins when documents disagree: current policy > regulatory summary / programme terms > procedure >
# incident and release notes > data contracts / QA checklist > FAQ > call scripts. Superseded versions come last.
RANK = {"policy": 1, "regulatory_summary": 2, "hardship_programme_terms": 2, "procedure": 3, "incident_report": 3,
        "release_note": 3, "data_contract": 4, "qa_checklist": 4, "faq": 5, "call_script": 6}


@dataclass
class Section:
    doc_id: str
    section: str          # "§5.2", "Q2", "item 7", or "" for a one-part document
    doc_type: str
    language: str
    version: str
    effective_date: str
    superseded: bool
    title: str
    text: str

    @property
    def cite(self) -> str:
        return f"{self.doc_id} {self.section}".strip()

    @property
    def rank(self) -> int:
        return RANK.get(self.doc_type, 5) + (10 if self.superseded else 0)


def _clean(text: str) -> str:
    text = re.sub(r"^>.*$", "", text, flags=re.M)          # synthetic-document banner
    text = re.sub(r"\*\*|`", "", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


@lru_cache(maxsize=1)
def corpus() -> tuple[Section, ...]:
    out = []
    with (RELEASE / "data" / "reference_documents.csv").open(newline="", encoding="utf-8") as f:
        docs = list(csv.DictReader(f))
    for d in docs:
        path = RELEASE / "files" / d["file_path"]
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        superseded = bool(d["superseded_by"].strip()) or "SUPERSEDED" in raw[:400]
        doc_id = d["doc_id"]
        meta = dict(doc_type=d["doc_type"], language=d["language"], version=d["version"],
                    effective_date=d["effective_date"], superseded=superseded, title=d["title"])
        parts = re.split(r"^##\s+", raw, flags=re.M)
        faq = re.findall(r"\*\*(Q\d+)\.\s*(.*?)\*\*\s*\n(.*?)(?=\n\*\*Q\d+\.|\Z)", raw, flags=re.S)
        if len(parts) > 1:
            for part in parts[1:]:
                head, _, body = part.partition("\n")
                m = re.match(r"(§[\d.]+)\s*(.*)", head.strip())
                sec = m.group(1) if m else head.strip()
                out.append(Section(doc_id, sec, text=_clean((m.group(2) if m else "") + "\n" + body), **meta))
        elif faq:
            for qn, question, answer in faq:
                out.append(Section(doc_id, qn, text=_clean(question + "\n" + answer), **meta))
        else:
            body = re.sub(r"^#\s+.*$", "", raw, count=1, flags=re.M)
            out.append(Section(doc_id, "", text=_clean(body), **meta))
    qa = RELEASE / "data" / "qa_checklist.csv"
    if qa.exists():
        with qa.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out.append(Section("qa_checklist", f"item {r['item_no']}", "qa_checklist", "EN", r.get("version", ""),
                                   "", False, "Call quality and compliance checklist",
                                   f"{r['item_name']}: {r['pass_criteria']}"))
    return tuple(out)


# --------------------------------------------------------------------------------------------- retrieval (no LLM)

STOP = set("the a an and or of to in on for is are be by with what how can we our does do did a customer agent "
           "should must this that it as at from about any".split())


def _tokens(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-zà-ÿ0-9]+", text.lower()) if w not in STOP]
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]   # bigrams: "call_attempts", "grace_period"


# Everyday words -> the words the documents use (keyword retrieval cannot know synonyms)
SYNONYMS = {"unhappy": "complaint dissatisfaction", "dissatisf": "complaint", "upset": "complaint", "rude": "complaint",
            "angry": "complaint", "proof of income": "income evidence", "sue": "legal action court", "lawsuit": "legal action", "court": "legal action", "garnish": "garnishment",
            "text": "text messages sms", "texting": "text messages sms", "weekend": "sunday", "night": "hours 21:00",
            "late": "late fee", "bankrupt": "bankruptcy consumer proposal", "lawyer": "legal representative",
            "counsellor": "credit counselling", "ptp": "promise to pay", "voicemail": "voicemail disclosed third parties",
            "harass": "contact frequency threatening", "threaten": "threatening legal action", "dnc": "do not call"}


def _expand(question: str) -> str:
    extra = [v for k, v in SYNONYMS.items() if re.search(rf"\b{k}", question.lower())]
    return question + (" " + " ".join(extra) if extra else "")


def bm25(question: str, sections, k1: float = 1.4, b: float = 0.75) -> list[tuple[float, Section]]:
    question = _expand(question)
    docs = [_tokens(s.title + " " + s.text) for s in sections]
    avg = sum(map(len, docs)) / max(len(docs), 1)
    df = Counter(t for d in docs for t in set(d))
    n = len(docs)
    q = [t for t in _tokens(question) if len(t) > 1]
    scored = []
    for s, d in zip(sections, docs):
        tf = Counter(d)
        score = sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * tf[t] * (k1 + 1)
                    / (tf[t] + k1 * (1 - b + b * len(d) / avg)) for t in q if t in tf)
        scored.append((score, s))
    return sorted(scored, key=lambda x: (-x[0], x[1].rank))


def _language(question: str) -> str:
    fr = len(re.findall(r"\b(le|la|les|des|une|est|pour|que|quel|quelle|peut|doit|client)\b", question.lower()))
    return "FR" if fr >= 3 else "EN"


def current_sections(language: str = "EN") -> list[Section]:
    return [s for s in corpus() if not s.superseded and s.language == language]


EVENT_QUESTION = re.compile(r"\bwhy (?:did|was|were|is|are)\b|what happened|\bdrop(?:ped)?\b|\bspike|\boutage|"
                            r"\bincident|\bdip\b|\bfell\b|\bsurge|what changed|renamed|release note|\bcolumn\b", re.I)


GENERIC = set("what does do can could should must would will the and for are with how our you your this that they their them "
              "customer customers agent agents tell tells told says said say ask asks before after when who which why "
              "where there here make made get give need needed needs allowed allow okay any about into from than then "
              "also just only still yet very much many more most less least".split())


@lru_cache(maxsize=1)
def _vocab() -> frozenset:
    words = set()
    for s in corpus():
        words |= set(re.findall(r"[a-zà-ÿ0-9]+", (s.title + " " + s.text).lower()))
    return frozenset(words)


def unknown_share(question: str) -> float:
    """Share of the question's key words that appear in no bank document ("sue", "gym", "taxes")."""
    vocab = _vocab()
    words = [w for w in re.findall(r"[a-z][a-z0-9-]+", question.lower()) if w not in GENERIC and len(w) >= 3]
    unknown = [w for w in words if not any(p in vocab or p.rstrip("s") in vocab or p + "s" in vocab
                                           for p in w.split("-"))]
    return len(unknown) / len(words) if words else 1.0


def _key_words(question: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z][a-z0-9-]+", _expand(question).lower())
            if w not in GENERIC and w not in STOP and len(w) >= 3}


def overlap(question: str, section: Section) -> int:
    """Distinct key words of the question found in the section (a quote sharing fewer than 2 is not an answer)."""
    text = (section.title + " " + section.text).lower().replace("_", " ")   # "consent_email" mentions email
    return sum(bool(re.search(rf"\b{re.escape(w)}", text)) for w in _key_words(question))


def relevance(question: str) -> int:
    """Key words shared with the best current section (0 when nothing matches)."""
    top = retrieve(question, k=1, use_model=False)
    return overlap(question, top[0]) if top else 0


def coverage(question: str, sections: list[Section]) -> tuple[float | None, str]:
    """Probability that the best section answers the question (Laya yes/no), else None.

    The one place Laya is used: on 10 questions it scored every in-scope one >= 0.66 and three of four
    out-of-scope ones <= 0.01 (the fourth is caught by unknown_share)."""
    best = None
    for s in sections[:3]:
        try:
            d = adapters.decide(s.title + ". " + s.text[:1800],
                                {"answers": {"type": "noul", "instructions": f"Does this passage answer: {question}"}},
                                engines=("laya",))["answers"]
        except adapters.DecisionUnavailable:
            return None, "none"
        best = max(best or 0.0, d["prob"] or 0.0)
    return best, "laya"


def retrieve_score(question: str) -> float:
    """BM25 score of the best current section (the router's signal when a question has no policy or data cue)."""
    pool = current_sections(_language(question)) or current_sections("EN")
    ranked = bm25(question, pool)
    return ranked[0][0] if ranked else 0.0


def retrieve(question: str, k: int = 5, use_model: bool = True) -> list[Section]:
    pool = current_sections(_language(question)) or current_sections("EN")
    if EVENT_QUESTION.search(question):   # "why did X happen" is answered by incident reports and release notes
        pool = [s for s in pool if s.doc_type in ("incident_report", "release_note")] or pool
    ranked = [(score, s) for score, s in bm25(question, pool)[: max(k * 2, 8)] if score > 0]
    if not ranked:
        return []
    # among the three best matches, the higher-precedence document leads (policy before FAQ before script)
    head = sorted(ranked[:3], key=lambda x: (x[1].rank, -x[0]))
    top = [s for _, s in head] + [s for _, s in ranked[3:]]
    return top[:k]


# --------------------------------------------------------------------------------------------- answer

ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["covered", "answer", "citations", "overrides"],
    "properties": {
        "covered": {"type": "boolean"},
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["doc_id", "section"],
            "properties": {"doc_id": {"type": "string"}, "section": {"type": "string"}}}},
        "overrides": {"type": "string"},
    },
}

SYSTEM = """You answer questions from Maple Bank collections staff using ONLY the bank documents provided.
Rules:
- Current documents outrank superseded ones. When current documents disagree, the lower precedence number wins
  (1 = current policy ... 6 = call script). Never answer from a superseded document unless the question asks
  about an old version.
- State the rule precisely with its numbers (limits, durations, percentages), in one to three short sentences.
  Write every number as digits, exactly as the document does ("3 outbound call attempts in any rolling 7 days",
  not "three ... seven-day").
- Cite every document section you relied on as doc_id plus section exactly as labelled (for example
  POL-COLL-001 §5.2, FAQ-COLL-001 Q2, qa_checklist item 7). Cite the governing current document first.
- If a lower-precedence or superseded document says something different, put one short sentence in `overrides`
  (for example "FAQ-COLL-001 Q1 says 6 months, but POL-COLL-004 v4.2 §4.2 overrides it."), else an empty string.
- If no provided document answers the question, set covered=false, answer with one sentence saying the
  documents do not cover it, and cite nothing."""


def _context(language: str) -> str:
    current = [s for s in corpus() if not s.superseded and s.language in (language, "EN")]
    old = [s for s in corpus() if s.superseded and s.language in (language, "EN")]
    def fmt(s: Section) -> str:
        return (f"[{s.cite}] ({s.doc_type}, precedence {RANK.get(s.doc_type, 5)}, v{s.version}, effective "
                f"{s.effective_date or 'n/a'}) {s.title}\n{s.text}")
    return ("CURRENT DOCUMENTS\n\n" + "\n\n".join(fmt(s) for s in sorted(current, key=lambda s: s.rank))
            + "\n\nSUPERSEDED DOCUMENTS (do not use unless asked about old versions)\n\n"
            + "\n\n".join(fmt(s) for s in old))


def supporting(question: str, answer: str, cites: list[str], k: int = 3, share: float = 0.6) -> list[str]:
    """Citations the model left out but its answer demonstrably rests on (pure code, no model call):
    - a programme or product code the answer uses (e.g. HP-LOAN-RED3): the one current English section defining it;
    - one of the top retrieved sections containing most (>= 60%) of the answer's key words.
    Documents the answer only names (often to say they are overridden) are not added: the model chose its citations."""
    text = answer or ""
    have = set(cites)
    doc_ids = {sec.doc_id for sec in corpus()}
    current_en = [sec for sec in corpus() if not sec.superseded and sec.language == "EN"]
    codes = {c for c in re.findall(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)+\b", text)
             if c not in doc_ids and not any(c.startswith(d + "-") or d.startswith(c) for d in doc_ids)}
    extra = []
    for code in sorted(codes):
        hits = sorted((sec for sec in current_en if code in sec.text or code in sec.title), key=lambda sec: sec.rank)
        if hits and hits[0].cite not in have:
            extra.append(hits[0].cite)
    words = {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in GENERIC and w not in STOP}
    for sec in retrieve(question, k=k, use_model=False):
        body = (sec.title + " " + sec.text).lower()
        if sec.cite not in have and sec.cite not in extra and not sec.superseded and words and \
                sum(w in body for w in words) / len(words) >= share:
            extra.append(sec.cite)
    return list(dict.fromkeys(extra))


def _valid(cites: list[dict]) -> list[str]:
    known = {s.cite: s for s in corpus()}
    by_doc = {s.doc_id for s in corpus()}
    one_part = {s.doc_id for s in corpus() if not s.section}      # incident reports, release notes, scripts
    out = []
    for c in cites:
        doc, section = c["doc_id"].strip(), c["section"].strip()
        cite = f"{doc} {section}".strip()
        if cite in known or (not section and doc in by_doc):
            out.append(cite)
        elif doc in one_part:                                     # a label on a document without sections
            out.append(doc)
    return list(dict.fromkeys(out))


def _check_llm_answer(question: str, out: dict, cites: list[str]) -> str:
    """Guardrails on a model-written policy answer: real citations, no personal data, no invented numbers."""
    text = out["answer"] + " " + out["overrides"]
    if out["covered"] and not cites:
        return "no valid citation"
    leaks = llm_guard.pii_leaks(text)
    if leaks:
        return f"personal data in the answer ({', '.join(leaks)})"
    by_cite = {s.cite: s for s in corpus()}
    named = {d.rstrip(".") for d in re.findall(r"\b[A-Z]{2,5}(?:-[A-Z0-9.]+)+|qa_checklist", out["overrides"])}
    docs = {c.split()[0] for c in cites} | named
    # a document named in the answer counts with all its versions (the superseded script that "says 5 per week")
    sources = [by_cite[c].text for c in cites if c in by_cite] + \
              [s.text for s in corpus() if any(s.doc_id == d or s.doc_id.startswith(d + "-") or
                                               d.upper().startswith(s.doc_id.upper()) for d in docs)]
    invented = llm_guard.ungrounded_numbers(text, sources, question) if out["covered"] else set()
    return f"numbers not in the cited sections: {sorted(invented)}" if invented else ""


def _best_sentences(question: str, text: str, k: int = 2) -> str:
    """The k sentences of a section that share the most words with the question, in their original order."""
    parts = [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9-])|\s(?=\d\.\s)|\s(?=- )", text) if x.strip()]
    if len(parts) <= k:
        return text
    q = set(_tokens(_expand(question)))
    scored = [(len(q & set(_tokens(x))), i) for i, x in enumerate(parts)]
    keep = sorted(i for _, i in sorted(scored, key=lambda t: (-t[0], t[1]))[:k]) if max(scored)[0] else list(range(k))
    return " ".join(parts[i] for i in keep)


def answer_policy_parsed(p: Parsed, role: str = "analyst", use_model: bool = True) -> dict:
    question, language = p.question, _language(p.question)
    if use_model and adapters.llm_available():
        try:
            out = adapters.complete(f"{_context(language)}\n\n{llm_guard.wrap_question(question)}",
                                    system=SYSTEM + "\n" + llm_guard.QUESTION_RULE,
                                    json_schema=ANSWER_SCHEMA, max_tokens=2000)
            cites = _valid(out["citations"])
            problem = _check_llm_answer(question, out, cites)
            if problem:
                adapters.audit({"action": "guardrail", "lane": "policy", "question": question, "problem": problem})
                raise adapters.LLMUnavailable(f"guardrail: {problem}")
            if not out["covered"]:
                reason = "Refuse: outside scope. No approved Maple Bank policy, procedure or report covers this question."
                return {"answer": reason + " " + out["answer"], "sql_or_sources": "documents searched: all current",
                        "refused": True, "reason": reason, "route": "policy", "table": None,
                        "engines": {"policy": "llm"}}
            if cites:
                answer = out["answer"].strip() + (" " + out["overrides"].strip() if out["overrides"].strip() else "")
                cites = cites + supporting(question, answer, cites)
                return {"answer": answer + f" (Sources: {'; '.join(cites)})", "sql_or_sources": "; ".join(cites),
                        "refused": False, "reason": "answered from current documents", "route": "policy",
                        "table": None, "engines": {"policy": "llm"}}
        except adapters.LLMUnavailable:
            pass
    # extractive fallback: best current section, quoted, if a document covers the question at all
    top = retrieve(question, k=3, use_model=use_model)
    covered, how = coverage(question, top) if (top and use_model) else (None, "none")
    not_covered = (covered is not None and covered < 0.30) or (covered is None and unknown_share(question) > 0.5) \
        or (bool(top) and overlap(question, top[0]) < 2)
    if top and not_covered:
        reason = "Refuse: outside scope. No approved Maple Bank policy, procedure or report covers this question."
        return {"answer": reason, "sql_or_sources": "documents searched: all current (best: " + top[0].cite + ")",
                "refused": True, "reason": reason, "route": "policy", "table": None,
                "engines": {"policy": f"bm25+{how}", "coverage": covered}}
    if not top:
        reason = "Refuse: outside scope. No approved Maple Bank document matches this question."
        return {"answer": reason, "sql_or_sources": "documents searched: all current", "refused": True,
                "reason": reason, "route": "policy", "table": None, "engines": {"policy": "bm25"}}
    best = top[0]
    head, _, body = best.text.partition("\n") if best.section else ("", "", best.text)
    body = _best_sentences(question, " ".join(body.split()) or head)
    lead = f"According to {best.cite}" + (f" ({head.strip()})" if body != head and head.strip() else "")
    return {"answer": f"{lead}: {body}", "sql_or_sources": "; ".join(s.cite for s in top),
            "refused": False, "reason": "quoted from the best-matching current section", "route": "policy",
            "table": None, "engines": {"policy": "bm25"}}
