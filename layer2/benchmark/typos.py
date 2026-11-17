"""Typo robustness: does a misspelt question get the same answer as the clean one?

    uv run python -m layer2.benchmark.typos                 # rules only (free): current system vs typo fix
    uv run python -m layer2.benchmark.typos --model         # with the AI (calls only for soft refusals)

Every known question (benchmark, own, holdouts) gets 1-2 typos in content words, made by code with a fixed seed: a
dropped, doubled, swapped or neighbouring-key letter. Nothing is hand-picked or hand-fixed. A typo'd question
"matches" when it has the same refused flag and the same numbers (data answers) or the same cited sources (others)
as the clean question in the same mode. Modes: current (L2_NORMALIZE=0) and the typo fix (L2_NORMALIZE=1).
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import random
import re
import sys
from pathlib import Path

import yaml

from layer2.adapters import REPO
from layer2.semantic.coverage import STOP

RELEASE = REPO / "data" / "raw" / "maple_collections_release" / "data" / "benchmark_questions.csv"
REPORT = REPO / "outputs" / "layer2_typo_report.md"
KEYS = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
ID = re.compile(r"\b(?:CS|PTP|GC|POL|PRC|FAQ|SCR|HPT|INC|IR)-?[\w-]*\d[\w-]*\b", re.I)


def questions() -> list[str]:
    qs = [r["question_text"] for r in csv.DictReader(RELEASE.open())] if RELEASE.exists() else []
    qs += [c["q"] for c in yaml.safe_load((REPO / "layer2" / "tests" / "questions_own.yaml").read_text())]
    for f in sorted(glob.glob(str(REPO / "layer2" / "benchmark" / "holdout_v*.yaml"))):
        qs += [i["q"] for i in yaml.safe_load(Path(f).read_text())]
    return list(dict.fromkeys(qs))


def _neighbour(c: str, rng: random.Random) -> str:
    for row in KEYS:
        i = row.find(c)
        if i >= 0:
            return row[max(0, i - 1)] if (rng.random() < 0.5 and i > 0) or i == len(row) - 1 else row[i + 1]
    return c


def _typo(word: str, rng: random.Random) -> str:
    i = rng.randrange(1, len(word) - 1)                  # keep the first and last letter, as people mostly do
    kind = rng.choice(["drop", "double", "swap", "key"])
    if kind == "drop":
        return word[:i] + word[i + 1:]
    if kind == "double":
        return word[:i] + word[i] + word[i:]
    if kind == "swap" and i < len(word) - 1:
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    return word[:i] + _neighbour(word[i], rng) + word[i + 1:]


def misspell(q: str, seed: int) -> str:
    """1-2 typos in lowercase content words of 5+ letters (not IDs, names or acronyms)."""
    rng = random.Random(seed)
    ids = [m.span() for m in ID.finditer(q)]
    spots = [m for m in re.finditer(r"\b[a-z]{5,}\b", q)
             if m.group(0) not in STOP and not any(a <= m.start() < b for a, b in ids)]
    if not spots:
        return q
    out = q
    for m in sorted(rng.sample(spots, min(len(spots), rng.choice([1, 2]))), key=lambda m: -m.start()):
        out = out[:m.start()] + _typo(m.group(0), rng) + out[m.end():]
    return out


def signature(out: dict) -> tuple:
    nums = ()
    if isinstance(out.get("table"), list) and out["table"]:
        nums = tuple(sorted(round(v, 2) for r in out["table"] for v in r.values()
                            if isinstance(v, (int, float)) and not isinstance(v, bool)))
    src = tuple(sorted(set(re.findall(r"\b[A-Z]{2,4}-[A-Z0-9-]+(?: §[\d.]+| Q\d+)?", out.get("sql_or_sources") or ""))))
    return bool(out["refused"]), nums if nums else src


def run(use_model: bool) -> dict:
    from layer2.ask import ask
    qs = questions()
    pairs = [(q, misspell(q, 1000 + i)) for i, q in enumerate(qs)]
    pairs = [(q, t) for q, t in pairs if t != q]
    results = {}
    for mode, flag in (("current", "0"), ("typo fix", "1")):
        os.environ["L2_NORMALIZE"] = flag
        same, rows = 0, []
        for q, t in pairs:
            clean, typo = ask(q, use_model=use_model), ask(t, use_model=use_model)
            ok = signature(clean) == signature(typo)
            same += ok
            rows.append((q, t, ok, typo.get("read_as")))
        results[mode] = (same, rows)
        print(f"{mode}: {same} / {len(pairs)} typo'd questions answered like the clean one", flush=True)
    os.environ["L2_NORMALIZE"] = "1"
    return {"pairs": len(pairs), "results": results}


def write(res: dict, use_model: bool, path: Path) -> None:
    n = res["pairs"]
    lines = [f"# Layer 2 typo robustness ({'with the AI' if use_model else 'rules only'})", "",
             "Each known question with 1-2 machine-made typos (fixed seed); \"same\" = same refusal and same numbers "
             "or sources as the clean question.", "", "| Mode | Same answer | Share |", "|---|---|---|"]
    for mode, (same, _) in res["results"].items():
        lines.append(f"| {mode} | {same} / {n} | {100 * same / n:.1f}% |")
    fixed_rows = res["results"]["typo fix"][1]
    lines += ["", "## Still different with the typo fix", "", "| Typed | Read as |", "|---|---|"]
    lines += [f"| {t.replace('|', '/')} | {(r or '-').replace('|', '/')} |" for q, t, ok, r in fixed_rows if not ok]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="store_true", help="allow the AI (only soft refusals call it)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    res = run(args.model)
    write(res, args.model, args.out or REPORT.with_name(REPORT.stem + ("_llm" if args.model else "_rules") + ".md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
