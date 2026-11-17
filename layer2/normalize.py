"""Fix typing errors before a question is answered (rules only, no API call, every question).

"promis kept rate by queu" -> "promise kept rate by queue". A word is corrected only when it is NOT a real word:
not in the English word list (layer2/semantic/english_words.txt.gz: SCOWL American and British/Canadian
spellings, lowercase entries only), the data
vocabulary (columns, category values, catalog phrases), the bank's documents, or the guard's and router's words.
So "whether", "falling", "women" are never touched. IDs, numbers, dates, ALL-CAPS acronyms and capitalised names
mid-sentence ("Montreal") are left alone. The correction must be close (Damerau-Levenshtein distance 1 for words of
4-6 letters, 2 for longer ones; a swap of two letters counts as 1) and unambiguous: exactly one candidate at the
smallest distance; on a tie a word that names data wins, then a word from the bank's documents, then the closer
spelling ("chanel" -> "channel", not "chapel"; "delinqueny" -> "delinquency", not "delinquent"); else unchanged.

Measured on the 244 clean questions we have (benchmark, own, holdouts): see layer2/CHANGELOG.md.
"""
from __future__ import annotations

import gzip
import re
from functools import lru_cache
from pathlib import Path

from layer2.parse import CASE_ID, DOC_ID, GOLDEN_ID, PTP_ID, TRANSCRIPT_ID

WORDS_FILE = Path(__file__).parent / "semantic" / "english_words.txt.gz"
# contractions written without the apostrophe are words, not typos
CONTRACTIONS = set("whats thats lets dont cant wont isnt doesnt didnt wasnt werent arent hows whos wheres theres "
                   "youre theyre were ive weve youve id im".split())
CONTRACTIONS |= set("jan feb mar apr jun jul aug sep sept oct nov dec ytd mtd qtd".split())   # date abbreviations
TOKEN = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]*")      # accented letters stay inside the word


@lru_cache(maxsize=1)
def english() -> frozenset:
    return frozenset(gzip.decompress(WORDS_FILE.read_bytes()).decode().split())


@lru_cache(maxsize=1)
def data_vocab() -> frozenset:
    """Words that name data: columns, category values, catalog phrases and cues (raw, not stemmed), plus the guard's
    and router's cue words that are whole words (regex stems like "disabilit" are not words)."""
    from layer2 import guard, router
    from layer2.semantic import data_text
    out = set(re.findall(r"[a-z]{2,}", data_text(None).lower().replace("_", " ")))
    cue_text = " ".join((guard.PROTECTED, guard.PROXY, guard.PII, guard.OFF_TOPIC, guard.DOMAIN, guard.TARGETING,
                         guard.BULK_VERB, router.POLICY_CUES, router.METRIC_CUES))
    out |= {w for w in re.findall(r"[a-z]{2,}", cue_text) if w in english()}
    return frozenset(out)


@lru_cache(maxsize=1)
def doc_vocab() -> frozenset:
    """Every word in the bank's documents (English and French)."""
    from layer2.policy import _vocab
    return frozenset(w for w in _vocab() if w.isalpha())


@lru_cache(maxsize=1)
def domain() -> frozenset:
    return data_vocab() | doc_vocab()


@lru_cache(maxsize=1)
def _by_length() -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for w in english() | domain():
        out.setdefault(len(w), []).append(w)
    return out


def known(word: str) -> bool:
    w = word.lower().replace("’", "'")
    if "'" in w:                                  # "customer's", "what's", "they're"
        w = w.split("'")[0]
    if w in CONTRACTIONS or len(w) < 2:
        return True
    vocab_e, vocab_d = english(), domain()
    forms = {w}                                   # plurals by English rules only, so "casees" is not "case" + "es"
    if w.endswith("ies") and len(w) > 4:
        forms.add(w[:-3] + "y")
    if re.search(r"(?:s|x|z|ch|sh|o)es$", w):
        forms.add(w[:-2])
    if w.endswith("s") and not w.endswith("ss") and len(w) >= 5:
        forms.add(w[:-1])
    if any(f in vocab_e or f in vocab_d for f in forms):
        return True
    if "-" in w:                                  # "late-fee", "right-party": every part known
        return all(known(part) for part in w.split("-") if part)
    return False


def _distance(a: str, b: str) -> int:
    try:
        from rapidfuzz.distance import DamerauLevenshtein
        return DamerauLevenshtein.distance(a, b)
    except ImportError:   # pragma: no cover - rapidfuzz is installed; plain Levenshtein with swaps as a fallback
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            prev = cur
        return prev[-1]


def suggest(word: str) -> str | None:
    """The one real word this typo stands for, or None when unsure."""
    w = word.lower()
    limit = 1 if len(w) <= 6 else 2
    best, cands = limit + 1, []
    by_len = _by_length()
    for n in range(len(w) - limit, len(w) + limit + 1):
        for c in by_len.get(n, ()):
            if len(c) < 3:
                continue
            d = _distance(w, c)
            if d < best:
                best, cands = d, [c]
            elif d == best:
                cands.append(c)
    if best > limit or not cands:
        return None
    same_start = [c for c in cands if c[0] == w[0]]   # people rarely mistype the first letter ("leeve" -> leave,
    if same_start:                                     # not sleeve)
        cands = same_start
    # ties: a word that names data beats a document word beats any English word; then the closer spelling.
    # (A "clearly closer spelling wins first" rule was tried for "wich" -> "which" and measured worse on the typo test,
    # 234 vs 239 of 242: the similarity score favours longer words, e.g. "caess" -> "caress" instead of "cases".)
    if len(cands) == 1:
        return cands[0]
    for tier in (data_vocab(), doc_vocab(), None):
        pool = [c for c in cands if tier is None or c in tier]
        if not pool:
            continue
        if len(pool) == 1:
            return pool[0]
        from rapidfuzz import fuzz
        ranked = sorted(pool, key=lambda c: -fuzz.ratio(w, c))
        if fuzz.ratio(w, ranked[0]) > fuzz.ratio(w, ranked[1]):
            return ranked[0]
        return None
    return None


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for rx in (CASE_ID, PTP_ID, GOLDEN_ID, DOC_ID, TRANSCRIPT_ID):
        spans += [m.span() for m in rx.finditer(text)]
    return spans


def normalize(question: str) -> tuple[str, list[tuple[str, str]]]:
    """(question with typing errors fixed, [(typed, corrected), …]). Unchanged when nothing is clearly a typo, and
    for French questions (no French word list: "selon" must not become "salon")."""
    from layer2.policy import _language
    if _language(question) == "FR":
        return question, []
    ids = _protected_spans(question)
    out, last, fixes = [], 0, []
    for m in TOKEN.finditer(question):
        a, b = m.span()
        word = m.group(0).strip("'’-")
        if any(a < e and b > s for s, e in ids) or len(word) < 4 or word.isupper():
            continue
        if not word.isascii():   # French or other accented words: never "corrected" (no French word list)
            continue
        starts_sentence = a == 0 or re.search(r"[.?!:;]\s*$|^\s*$", question[:a]) is not None
        if word[0].isupper() and not starts_sentence:     # a name: Montreal, Calgary, Tremblay
            continue
        if known(word):
            continue
        parts = word.split("-")
        fixed_parts = []
        for part in parts:
            new = part if (len(part) < 4 or known(part)) else (suggest(part) or part)
            fixed_parts.append(new[0].upper() + new[1:] if part[:1].isupper() and new != part else new)
        fixed = "-".join(fixed_parts)
        if fixed.lower() == word.lower():
            continue
        if word[0].isupper():
            fixed = fixed[0].upper() + fixed[1:]
        start = a + m.group(0).index(word)
        out.append(question[last:start] + fixed)
        last = start + len(word)
        fixes.append((word, fixed))
    return "".join(out) + question[last:], fixes
