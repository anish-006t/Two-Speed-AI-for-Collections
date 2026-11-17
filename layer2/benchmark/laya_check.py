"""Measure Laya on Layer 2's own decisions before trusting it: speed, memory and accuracy.

Uses the labelled questions in layer2/tests/questions_own.yaml plus the benchmark questions (their expected lane comes
from the rules, which are 100% on them). For each question Laya answers the three choices Layer 2 asks it (guard
category, lane, metric) without any rule help; the report says how often it agrees with the expected answer and how
confident it is when wrong, which sets the thresholds in guard.py and semantic/.

    uv run python -m layer2.benchmark.laya_check
"""
from __future__ import annotations

import os
import resource
import sys
import time

import yaml

from layer2 import adapters
from layer2.adapters import REPO
from layer2.semantic import catalog

OWN = REPO / "layer2" / "tests" / "questions_own.yaml"
REPORT = REPO / "outputs" / "layer2_laya_check.md"


def main() -> int:
    try:
        import laya  # noqa: F401
    except ImportError:
        print("Laya is not installed: uv pip install torch --index-url https://download.pytorch.org/whl/cpu && uv pip install laya")
        return 1
    cases = yaml.safe_load(OWN.read_text())
    measures = catalog()["measures"]
    metric_criteria = {mid: (m["label"] + ": " + ", ".join(m["cues"][:3]))[:120] for mid, m in measures.items()}
    lane = {"metric_sql": "asks for a number, rate, count, average or table computed from collections data",
            "policy": "asks what the bank's policy, procedure, script or FAQ says, what an agent must do, or why an "
                      "incident happened",
            "call_summary": "asks to summarise a specific recorded call",
            "lookup": "asks for the facts or notes of one specific case, promise or customer"}
    guard = {"answerable": "a question about collections data, metrics, cases, calls, policies or procedures",
             "refuse": "off topic, or uses protected attributes (gender, age, citizenship...), or targets help by area "
                       "or language, or asks to export personal data"}
    t0 = time.time()
    adapters.decide("warm up", {"x": {"type": "noul", "instructions": "Is this a test?"}}, engines=("laya",))
    load_s = time.time() - t0
    rows, times = [], []
    for c in cases:
        qs = {"guard": {"type": "choice", "instructions": "Should this request be answered or refused?", "criteria": guard},
              "lane": {"type": "choice", "instructions": "Which kind of answer does this question need?", "criteria": lane}}
        if c["route"] == "metric_sql":
            qs["metric"] = {"type": "choice", "instructions": "Which collections metric does the question ask for?",
                            "criteria": metric_criteria}
        t = time.time()
        d = adapters.decide(c["q"], qs, engines=("laya",))
        times.append(time.time() - t)
        expect_guard = "refuse" if c["route"] == "refuse" else "answerable"
        rows.append({"q": c["q"], "guard": (d["guard"]["answer"], d["guard"]["prob"], expect_guard),
                     "lane": (d["lane"]["answer"], d["lane"]["prob"], c["route"]) if c["route"] != "refuse" else None,
                     "metric": (d["metric"]["answer"], d["metric"]["prob"], c["measure"]) if "metric" in d else None})
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    def acc(key):
        xs = [r[key] for r in rows if r[key]]
        right = [x for x in xs if x[0] == x[2]]
        wrong_conf = [x[1] or 0 for x in xs if x[0] != x[2]]
        return len(right), len(xs), (max(wrong_conf) if wrong_conf else 0.0)

    lines = ["# Laya check on Layer 2 decisions", "",
             f"Model: `{os.environ.get('LAYA_MODEL', 'multilingual')}`; load {load_s:.1f}s; "
             f"median decision {sorted(times)[len(times) // 2] * 1000:.0f} ms (3 questions per call); peak RSS {peak_mb:.0f} MB.", "",
             "| Decision | Agrees with expected | Highest confidence when wrong |", "|---|---|---|"]
    for key in ("guard", "lane", "metric"):
        r, n, w = acc(key)
        lines.append(f"| {key} | {r} / {n} | {w:.2f} |")
    lines += ["", "Rules decide first in Layer 2; Laya is consulted only where rules are silent, and only above the "
                  "confidence that this table shows is safe.", "", "| Question | guard | lane | metric |", "|---|---|---|---|"]
    for r in rows:
        cell = lambda x: "" if not x else f"{x[0]} ({(x[1] or 0):.2f}){'' if x[0] == x[2] else ' ✗ ' + x[2]}"  # noqa: E731
        lines.append(f"| {r['q'][:70]} | {cell(r['guard'])} | {cell(r['lane'])} | {cell(r['metric'])} |")
    text = "\n".join(lines) + "\n"
    REPORT.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
