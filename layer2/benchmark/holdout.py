"""Holdout evaluation: questions written by someone who never saw Layer 2's rules, with their own reference SQL.

    uv run python -m layer2.benchmark.holdout --no-model     # rules only (no API calls)
    uv run python -m layer2.benchmark.holdout                # with the LLM (only uncovered questions call it)

Measure only: never change a rule to fit one holdout question (that would be overfitting the holdout). A failure
points at a general gap; fix the gap, and judge the fix on a NEW holdout file. Each answer is one of:
  correct         numbers match the reference (data), document and key facts present (policy), refused (refuse)
  honest partial  wrong or incomplete, but the answer says what was not applied
  no answer       says it cannot answer, or refused an answerable question
  silently wrong  numbers differ and nothing says so (the outcome that matters most)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

from layer2.adapters import REPO
from layer2.ask import ask

HOLDOUT = Path(__file__).parent / "holdout_v1.yaml"
REPORT = REPO / "outputs" / "layer2_holdout_report.md"


def _nums(values) -> list[float]:
    out = []
    for v in values:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out.append(float(v))
        elif isinstance(v, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", v.strip()):
            out.append(float(v))
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.06, 0.01 * abs(b))


def score_data(item: dict, out: dict) -> tuple[str, str]:
    if out["refused"]:
        return "no answer", "refused"
    ref = [n for row in (item.get("ref_result") or [])[1:] for n in _nums(row)]
    ours = [n for row in (out.get("table") or []) for n in _nums(row.values())] if isinstance(out.get("table"), list) \
        else _nums(re.findall(r"-?\d+(?:\.\d+)?", out["answer"].split("\n\nNote:")[0]))
    if not ours:
        return "no answer", out["answer"][:80]
    found = sum(any(_close(o, r) for o in ours) for r in ref)
    share = found / len(ref) if ref else 0
    if share >= 0.6:
        return "correct", f"{found}/{len(ref)} reference numbers"
    if "Not applied" in out["answer"]:
        return "honest partial", out["answer"][out["answer"].find("Not applied"):][:120]
    return "silently wrong", f"{found}/{len(ref)} reference numbers"


def score_policy(item: dict, out: dict) -> tuple[str, str]:
    if out["refused"]:
        return "no answer", "refused"
    text = (out["answer"] + " " + (out.get("sql_or_sources") or "")).lower()
    doc_ok = (item.get("doc") or "").lower() in text
    facts = item.get("key_facts") or []
    hit = 0
    for f in facts:
        ws = [w for w in re.findall(r"[a-z0-9]+", f.lower()) if len(w) > 2]
        hit += bool(ws) and sum(w in text for w in ws) / len(ws) >= 0.6
    ok = doc_ok and hit >= max(1, len(facts) // 2)
    return ("correct" if ok else "silently wrong"), f"doc {'ok' if doc_ok else 'missing'}, facts {hit}/{len(facts)}"


def evaluate(items: list[dict], use_model: bool) -> list[dict]:
    rows = []
    for it in items:
        out = ask(it["q"], role="analyst", as_of="2026-09-28", use_model=use_model)
        if it["expect"] == "refuse":
            verdict, detail = ("correct", "refused") if out["refused"] else ("silently wrong", "answered")
        elif it["kind"] == "policy":
            verdict, detail = score_policy(it, out)
        else:
            verdict, detail = score_data(it, out)
        engines = out.get("engines") or {}
        by = "AI" if "llm" in str(engines).lower() else "rules"
        rows.append({"id": it["id"], "kind": it["kind"], "verdict": verdict, "by": by, "route": out["route"],
                     "detail": detail})
        print(f"{it['id']} {it['kind']:<9} {verdict:<15} {by:<5} {detail}", flush=True)
    return rows


def write(rows: list[dict], use_model: bool, path: Path = REPORT) -> None:
    kinds = sorted({r["kind"] for r in rows})
    verdicts = ["correct", "honest partial", "no answer", "silently wrong"]
    lines = [f"# Layer 2 holdout ({'with the LLM' if use_model else 'rules only'})", "",
             "Questions and reference SQL written without seeing Layer 2's rules. Measure only; never tuned on.", "",
             "| Kind | " + " | ".join(verdicts) + " |", "|---" * (len(verdicts) + 1) + "|"]
    for k in kinds + ["all"]:
        rs = [r for r in rows if k in ("all", r["kind"])]
        lines.append(f"| {k} | " + " | ".join(str(sum(r['verdict'] == v for r in rs)) for v in verdicts) + " |")
    lines += ["", "| ID | Kind | Verdict | By | Detail |", "|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['kind']} | {r['verdict']} | {r['by']} | {r['detail'].replace('|', '/')} |"
              for r in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, default=HOLDOUT)
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    items = yaml.safe_load(args.file.read_text())
    rows = evaluate(items, use_model=not args.no_model)
    out = args.out or REPORT.with_name(REPORT.stem + ("_rules" if args.no_model else "_llm") + ".md")
    write(rows, not args.no_model, out)
    n = len(rows)
    print({v: sum(r["verdict"] == v for r in rows) for v in ("correct", "honest partial", "no answer",
                                                              "silently wrong")}, f"of {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
