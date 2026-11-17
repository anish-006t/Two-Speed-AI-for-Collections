"""Score the dev questions against labels/benchmark_dev_answers.csv, the way the organisers describe scoring:
numbers within each question's tolerance; document answers need the key facts and the cited document; refusals must
be refused and nothing else may be. Writes outputs/benchmark_dev_report.md.

    uv run python -m layer2.benchmark.eval_dev                 # runs ask() on the dev questions, then scores
    uv run python -m layer2.benchmark.eval_dev --from-csv outputs/benchmark_answers.csv
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

from layer2.adapters import REPO
from layer2.benchmark.run import RELEASE, load_questions, run

DEV_ANSWERS = RELEASE / "labels" / "benchmark_dev_answers.csv"
REPORT = REPO / "outputs" / "benchmark_dev_report.md"
NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def tolerance(text: str) -> float | None:
    t = (text or "").lower()
    if not t:
        return None
    if "exact" in t and "±" not in t:
        return 0.0
    m = re.search(r"±\s*([\d.]+)", t)
    return float(m.group(1)) if m else None


def to_num(v: str):
    v = (v or "").strip().replace("%", "").replace("$", "").replace(",", "")
    try:
        return float(v)
    except ValueError:
        return None


def parse_table(text: str) -> list[list[str]]:
    """The first CSV-looking block of an answer: header line plus rows until a blank or prose line."""
    lines = [ln for ln in (text or "").strip().splitlines()]
    block = []
    for ln in lines:
        if not ln.strip():
            if block:
                break
            continue
        if block and "," not in ln and "," in block[0]:   # prose after a multi-column table ends it
            break
        if block and "," not in block[0] and len(block) >= 2:   # single-column: header plus one value
            break
        block.append(ln)
    return [row for row in csv.reader(io.StringIO("\n".join(block)))] if block else []


def _key(v: str) -> str:
    n = to_num(v)
    return f"{n:g}" if n is not None else v.strip().lower()


def score_table(gold: str, ours: str, tol: float) -> tuple[bool, str]:
    g, o = parse_table(gold), parse_table(ours)
    if len(g) < 2:
        return False, "gold not a table"
    if len(o) < 2:
        return False, "answer has no table"
    gh, grows, orows = g[0], g[1:], o[1:]
    nkey = next((i for i, v in enumerate(grows[0]) if to_num(v) is not None), len(gh))
    if nkey == 0 and len(gh) > 1:   # first column is a numeric key (hour of day, prior broken count)
        nkey = 1
    if nkey == 0:   # single value, e.g. promise_kept_rate_pct\n35.1
        gv, ov = to_num(grows[0][0]), to_num(orows[0][0]) if orows and orows[0] else None
        ok = ov is not None and abs(gv - ov) <= tol + 1e-9
        return ok, f"gold {gv} ours {ov}"
    ours_by_key = {}
    for r in orows:
        if len(r) > nkey:
            ours_by_key[tuple(_key(x) for x in r[:nkey])] = r
    misses, worst = [], 0.0
    for r in grows:
        key = tuple(_key(x) for x in r[:nkey])
        mine = ours_by_key.get(key)
        gv = to_num(r[nkey])
        ov = to_num(mine[nkey]) if mine and len(mine) > nkey else None
        if ov is None:
            misses.append(f"{'/'.join(key)} missing")
            continue
        worst = max(worst, abs(gv - ov))
        if abs(gv - ov) > tol + 1e-9:
            misses.append(f"{'/'.join(key)}: gold {gv} ours {ov}")
    detail = f"{len(grows) - len(misses)}/{len(grows)} rows within ±{tol} (max diff {worst:.3g})"
    return not misses, detail + ("; " + "; ".join(misses[:4]) if misses else "")


def key_numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(text or "")}


def score_document(gold: str, sources: list[str], ours: str, our_sources: str) -> tuple[bool, str]:
    docs = {re.split(r"\s+§|\s+Q\d|\s+item", s)[0].strip() for s in sources}
    docs = {d for d in docs if not d.lower().startswith("qa_checklist")}
    cited = all(d in (our_sources or "") or d in (ours or "") for d in docs)
    need = key_numbers(gold)
    have = key_numbers(ours)
    facts = need <= have
    return cited and facts, f"docs cited {cited} ({', '.join(sorted(docs))}); key numbers {sorted(need - have) or 'all present'}"


def score_summary(gold: str, sources: list[str], ours: str, our_sources: str) -> tuple[bool, str]:
    tid = re.findall(r"\d+", " ".join(sources))
    cited = all(t in (our_sources or "") for t in tid)
    need = key_numbers(gold)
    have = key_numbers(ours)
    step = re.search(r"next step:\s*([^.;]+)", gold or "")
    step_ok = (step.group(1).strip().lower() in (ours or "").lower()) if step else True
    ok = cited and need <= have and step_ok
    return ok, f"transcript cited {cited}; numbers missing {sorted(need - have) or 'none'}; next step ok {step_ok}"


def evaluate(answers: dict, questions: dict) -> list[dict]:
    results = []
    with DEV_ANSWERS.open(newline="", encoding="utf-8") as f:
        for g in csv.DictReader(f):
            qid = g["question_id"]
            a = answers.get(qid)
            q = questions.get(qid, {})
            if a is None:
                results.append({"qid": qid, "kind": "?", "ok": False, "detail": "not answered"})
                continue
            refused = str(a["refused"]).lower() == "true"
            gold = g["gold_answer"]
            sources = json.loads(g["gold_sources"]) if g["gold_sources"].strip() else []
            if gold.lower().startswith("refuse"):
                kind, ok, detail = "refuse", refused, "refused" if refused else "answered a refusal question"
            elif refused:
                kind, ok, detail = "answerable", False, "refused an answerable question"
            elif any(s.lower().startswith("transcript") for s in sources):
                kind = "call_summary"
                ok, detail = score_summary(gold, sources, a["answer"], a["sql_or_sources"])
            elif sources:
                kind = "document"
                ok, detail = score_document(gold, sources, a["answer"], a["sql_or_sources"])
            else:
                kind = "metric"
                tol = tolerance(q.get("tolerance", ""))
                ok, detail = score_table(gold, a["answer"], tol if tol is not None else 0.0)
            results.append({"qid": qid, "kind": kind, "ok": ok, "detail": detail})
    return results


def report(results: list[dict], path: Path = REPORT) -> str:
    kinds = sorted({r["kind"] for r in results})
    lines = ["# Layer 2 dev benchmark report", "",
             f"**{sum(r['ok'] for r in results)} / {len(results)} dev questions pass.**", "",
             "| Kind | Pass |", "|---|---|"]
    for k in kinds:
        rs = [r for r in results if r["kind"] == k]
        lines.append(f"| {k} | {sum(r['ok'] for r in rs)} / {len(rs)} |")
    lines += ["", "| Question | Kind | Pass | Detail |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['qid']} | {r['kind']} | {'yes' if r['ok'] else 'NO'} | {r['detail'].replace('|', '/')} |")
    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-csv", type=Path, help="score an existing answers CSV instead of running ask()")
    ap.add_argument("--no-model", action="store_true")
    args = ap.parse_args(argv)
    questions = {q["question_id"]: q for q in load_questions()}
    dev_ids = [qid for qid, q in questions.items() if q.get("split") == "dev"]
    if args.from_csv:
        with args.from_csv.open(newline="", encoding="utf-8") as f:
            answers = {r["question_id"]: r for r in csv.DictReader(f)}
    else:
        rows = run([questions[i] for i in dev_ids], use_model=not args.no_model)
        answers = {r["question_id"]: r for r in rows}
    print(report(evaluate(answers, questions)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
