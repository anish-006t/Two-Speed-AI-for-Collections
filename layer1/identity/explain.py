"""Plain-English explanations of a grey-zone pair for the reviewer (architecture §7 Tier 3).

Built from comparison results and weights only, such as "last name: exact (weight +6.6)", never from names, dates of
birth, phones or IDs. The project uses no external AI APIs (CLAUDE.md), so the explanation is rule-based: the
strongest evidence for and against, the constraint flags, and a cautious suggestion. The reviewer decides.
"""
import math

from layer1.identity.comparisons import LEVEL_LABELS

COMPARISONS = ["first_name", "last_name", "dob", "phone", "postal_code", "email", "national_id_hash", "pay_accounts"]


def comparison_summary(pair: dict) -> list[str]:
    """Turn one identity.match_pairs row into PII-free lines. Uses only gamma_*, bf_*, flags and probability."""
    lines = []
    for c in COMPARISONS:
        gamma = pair.get(f"gamma_{c}")
        if gamma is None:
            continue
        label = LEVEL_LABELS[c].get(int(gamma), f"level {gamma}")
        bf = pair.get(f"bf_{c}")
        weight = f" (weight {math.log2(bf):+.1f})" if bf and bf > 0 else ""
        lines.append(f"{c.replace('_', ' ')}: {label}{weight}")
    if pair.get("match_probability") is not None:
        lines.append(f"overall match probability: {pair['match_probability']:.3f}")
    if pair.get("constraint_flags"):
        lines.append(f"constraint flags: {pair['constraint_flags']}")
    if pair.get("det_rule"):
        lines.append(f"deterministic rule: {pair['det_rule']}")
    lines.append(f"record types: {pair.get('system_l')} and {pair.get('system_r')}")
    return lines


def weights(pair: dict) -> list[tuple[str, str, float]]:
    out = []
    for c in COMPARISONS:
        gamma, bf = pair.get(f"gamma_{c}"), pair.get(f"bf_{c}")
        if gamma is None or bf is None or bf <= 0 or int(gamma) == -1:
            continue
        out.append((c.replace("_", " "), LEVEL_LABELS[c].get(int(gamma), f"level {gamma}"), math.log2(bf)))
    return out


def suggestion(pair: dict) -> str:
    flags = pair.get("constraint_flags") or ""
    p = pair.get("match_probability")
    if "household" in flags:
        return "keep apart (joint holder or co-borrower)"
    if any(f in flags for f in ("lookalike", "possible_twins", "suffix_conflict")):
        return "unsure: check whether these are relatives at the same address"
    if p is not None and p >= 0.95 and "dob" in flags:
        return "unsure: strong match except the date of birth; confirm the DOB with the customer record"
    if p is not None and p >= 0.95:
        return "link if the record details agree on inspection"
    return "keep apart unless other evidence appears (wrong merges are worse than missed ones)"


def explain(pair: dict) -> tuple[str, str]:
    """Returns (text, 'rules'). No external calls."""
    ws = sorted(weights(pair), key=lambda w: w[2], reverse=True)
    pro = [f"{n} {lab} ({w:+.1f})" for n, lab, w in ws if w > 0][:3]
    con = [f"{n} {lab} ({w:+.1f})" for n, lab, w in ws[::-1] if w < 0][:3]
    lines = [f"Strongest evidence for a match: {', '.join(pro) or 'none'}.",
             f"Strongest evidence against: {', '.join(con) or 'none'}."]
    if pair.get("constraint_flags"):
        lines.append(f"Constraints: {pair['constraint_flags']}.")
    if pair.get("det_rule"):
        lines.append(f"Deterministic rule {pair['det_rule']} matched.")
    lines.append(f"Suggestion: {suggestion(pair)}.")
    return " ".join(lines), "rules"
