"""Agent-notes retrieval eval (layer2/docs/rag_plan.md §5): does the right kind of note come first?

    uv run python -m layer2.benchmark.notes_eval              # keyword methods only (no model)
    uv run python -m layer2.benchmark.notes_eval --vectors    # + hybrid with the embedding model

300 random cases (fixed seed) with >= 3 notes and at least one hardship_assessment / dispute_note / payment_note /
supervisor_note. For each such type in a case, one question is made from templates written by a separate agent that
never saw Layer 2's code (notes_eval_templates.yaml; 4 English + 2 French per topic, avoiding the type names). The
note type is the label: a hit means a note of that type is in the top 3. Because the full ranking also boosts by note
type, "text only" variants (no type boost) show what the note text alone gives; vectors are judged on those. Templates and thresholds are not changed
after seeing results; a later change is judged on a new seed.
"""
from __future__ import annotations

import argparse
import math
import random
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import yaml

from layer2 import adapters
from layer2.adapters import REPO

TEMPLATES = Path(__file__).parent / "notes_eval_templates.yaml"
REPORT = REPO / "outputs" / "layer2_notes_eval.md"
TYPE = {"hardship": "hardship_assessment", "dispute": "dispute_note", "payment": "payment_note",
        "escalation": "supervisor_note"}


def baseline_rank(question: str, rows):
    """story._rank as committed before the RAG work (frozen copy, for comparison)."""
    topics = {"hardship": ["hardship", "hrdshp", "income", "hours", "laid", "job", "sick", "ei", "tight", "afford", "cut"],
              "promise": ["ptp", "promise", "promised", "pay", "payment", "paid"],
              "dispute": ["dispute", "disputes", "fee", "fraud", "error", "billing"],
              "complaint": ["complaint", "complain", "unhappy", "escalate", "supervisor"],
              "contact": ["cb", "callback", "vm", "voicemail", "lm", "no answer", "rpc"]}
    q = question.lower()
    terms = [w for t, words in topics.items() if t in q for w in words]
    terms += [w for w in re.findall(r"[a-z]{4,}", q) if w not in {"notes", "note", "case", "customer", "last", "days",
                                                                  "what", "show", "with", "about", "from", "story"}]
    docs = [re.findall(r"[a-z0-9]+", (r[4] or "").lower()) for r in rows]
    n, avg = len(docs), (sum(map(len, docs)) / max(len(docs), 1)) or 1
    df = Counter(t for d in docs for t in set(d))
    scored = []
    for r, d in zip(rows, docs):
        tf = Counter(d)
        score = sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
                    for t in set(terms) if t in tf)
        scored.append((score, r))
    if not terms:
        return [(0.0, r) for r in rows]
    return [x for x in sorted(scored, key=lambda x: -x[0]) if x[0] > 0]


def sample(n_cases: int = 300, seed: int = 7):
    con = adapters.connect(read_only=True)
    try:
        cases = [c for (c,) in con.execute(f"""
            SELECT case_id FROM silver.agent_notes_redacted GROUP BY case_id
            HAVING count(*) >= 3 AND count(*) FILTER (WHERE note_type IN ({", ".join(f"'{t}'" for t in TYPE.values())})) > 0
            ORDER BY hash(case_id || '{seed}') LIMIT {n_cases}""").fetchall()]
        rows = con.execute(f"""
            SELECT note_id, case_id, CAST(try_cast(note_ts AS TIMESTAMP) AS DATE), note_type, note_text_redacted,
                   note_language
            FROM silver.agent_notes_redacted WHERE case_id IN ({", ".join(f"'{c}'" for c in cases)})
            ORDER BY note_ts DESC""").fetchall()
    finally:
        con.close()
    by_case: dict[str, list] = {}
    for r in rows:
        by_case.setdefault(r[1], []).append(r)
    return by_case


def questions(by_case, seed: int = 7):
    tpl = yaml.safe_load(TEMPLATES.read_text())
    rng = random.Random(seed)
    out = []
    for case, rows in sorted(by_case.items()):
        for topic, kind in TYPE.items():
            target = [r for r in rows if r[3] == kind]
            if target:
                q = rng.choice(tpl[topic]).replace("{case}", case)
                french = all((r[5] or "") == "FR" for r in target)
                out.append({"case": case, "topic": topic, "type": kind, "q": q, "french": french})
    return out


def score(method, items, by_case, role="analyst"):
    from layer2.story import visible
    hits, rr, secs = [], [], []
    for it in items:
        rows = [r[:5] for r in by_case[it["case"]]]
        if method != "baseline":
            rows = visible(rows, it["q"], role)
        t = time.perf_counter()
        ranked = method_fn(method)(it["q"], rows)
        secs.append(time.perf_counter() - t)
        kinds = [r[3] for _, r in ranked]
        hits.append(it["type"] in kinds[:3])
        rr.append(next((1 / (i + 1) for i, k in enumerate(kinds) if k == it["type"]), 0.0))
    return hits, rr, secs


def method_fn(method):
    if method == "baseline":
        return baseline_rank
    from layer2 import story
    if method == "keyword":
        return story._rank
    if method == "keyword (text only)":                 # no note-type boost: what the note text alone gives
        return lambda q, rows: story._rank(q, rows, use_types=False)
    if method == "hybrid (text only)":
        return lambda q, rows: story._rank_hybrid(q, rows, use_types=False)
    if method == "hybrid rrf":
        return lambda q, rows: story._rank_hybrid(q, rows, mode="rrf")
    if method == "hybrid rrf (text only)":
        return lambda q, rows: story._rank_hybrid(q, rows, use_types=False, mode="rrf")
    return story._rank_hybrid


def answers_check(n: int, seed: int = 13) -> list[dict]:
    """Gate-3 check (rag_plan.md §6): n asking questions through ask() with the AI on; did the cited answer pass
    every guardrail? Uses one LLM call per question (cached afterwards)."""
    from layer2.ask import ask
    items = questions(sample(max(n, 20), seed), seed)
    rng = random.Random(seed)
    out = []
    for it in rng.sample(items, min(n, len(items))):
        res = ask(it["q"], role="analyst", use_model=True)
        engine = (res.get("engines") or {}).get("story", "")
        out.append({"q": it["q"], "topic": it["topic"], "passed": "+llm" in engine, "engine": engine,
                    "answer": res["answer"].split("\n\n")[0][:300]})
        print(f"{'PASS' if out[-1]['passed'] else 'FALLBACK'} | {engine} | {it['q'][:70]}", flush=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", type=int, default=0, help="also check N cited AI answers (N LLM calls)")
    ap.add_argument("--vectors", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cases", type=int, default=300)
    args = ap.parse_args(argv)
    if args.answers:
        rows = answers_check(args.answers)
        passed = sum(r["passed"] for r in rows)
        text = [f"# Notes AI answers check", "", f"{passed} / {len(rows)} cited answers passed every guardrail "
                "(others fell back to the ranked notes).", "", "| Pass | Question | Answer (first part) |", "|---|---|---|"]
        text += [f"| {'yes' if r['passed'] else 'no: ' + r['engine']} | {r['q']} | {r['answer'].replace('|', '/')} |"
                 for r in rows]
        (REPO / "outputs" / "layer2_notes_answers_check.md").write_text("\n".join(text) + "\n")
        print(text[2])
        return 0
    by_case = sample(args.cases, args.seed)
    items = questions(by_case, args.seed)
    methods = ["baseline", "keyword (text only)", "keyword"] + (
        ["hybrid rrf (text only)", "hybrid rrf", "hybrid (text only)", "hybrid"] if args.vectors else [])
    lines = ["# Layer 2 agent-notes retrieval eval", "",
             f"{len(by_case)} random cases (seed {args.seed}), {len(items)} questions from templates written without "
             "seeing Layer 2's code; label = note type; hit = a note of that type in the top 3.", "",
             "| Method | hit@3 | MRR | hit@3 French-only | p50 ms | p95 ms |", "|---|---|---|---|---|---|"]
    for m in methods:
        hits, rr, secs = score(m, items, by_case)
        fr = [h for h, it in zip(hits, items) if it["french"]]
        ms = sorted(s * 1000 for s in secs)
        line = (f"| {m} | {100 * sum(hits) / len(hits):.1f}% | {statistics.mean(rr):.3f} | "
                f"{(100 * sum(fr) / len(fr)) if fr else 0:.1f}% (n={len(fr)}) | {ms[len(ms) // 2]:.1f} | "
                f"{ms[int(len(ms) * 0.95)]:.1f} |")
        lines.append(line)
        print(line, flush=True)
    lines += ["", "| Topic | n | " + " | ".join(methods) + " |", "|---|---|" + "---|" * len(methods)]
    for topic in TYPE:
        sub = [i for i, it in enumerate(items) if it["topic"] == topic]
        cells = []
        for m in methods:
            hits, _, _ = score(m, [items[i] for i in sub], by_case)
            cells.append(f"{100 * sum(hits) / max(len(hits), 1):.1f}%")
        lines.append(f"| {topic} | {len(sub)} | " + " | ".join(cells) + " |")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    (REPORT if args.seed == 7 else REPORT.with_name(f"{REPORT.stem}_seed{args.seed}.md")).write_text("\n".join(lines) + "\n")
    print("\n".join(lines[-6:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
