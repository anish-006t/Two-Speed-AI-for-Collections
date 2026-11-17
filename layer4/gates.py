"""Confidence gates for anything a model suggests to an agent (L4-6 builds the review queue on these).

prob >= 0.9 shows as a suggestion; 0.75 to 0.9 needs the agent to confirm; below 0.75 goes to the review queue.
Hardship always needs the agent's confirmation, whatever the probability. Nothing reaches a customer automatically.

Why 0.75 (was 0.6, 4 Oct): the rules extractor never scores below 0.65, so nothing ever reached review, yet its
answers are often wrong (outputs/layer3_label_eval.md: hardship 72% right, hardship reason 47%). On 300 open-case
calls, 0.75 sends "possible hardship" (0.72, about 11% of calls) to a person and leaves clear answers with the agent.
Sentiment is a tone score shown for context, not a decision, so it is never queued (at 0.70 it would flood the queue).
"""
SUGGEST, CONFIRM, REVIEW = "suggest", "confirm", "review"
SUGGEST_AT, CONFIRM_AT = 0.9, 0.75
ALWAYS_CONFIRM = {"hardship"}
NEVER_REVIEW = {"sentiment"}


def gate(prob: float | None, kind: str = "") -> str:
    p = 0.0 if prob is None else float(prob)
    if p < CONFIRM_AT and kind not in NEVER_REVIEW:
        return REVIEW
    if kind in ALWAYS_CONFIRM or kind in NEVER_REVIEW or p < SUGGEST_AT:
        return CONFIRM
    return SUGGEST
