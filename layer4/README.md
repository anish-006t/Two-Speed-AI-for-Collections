# layer4/: Agent Assist + Post-call Summary (owner: Madhur; `models/` owned by Ayush)

- `precall.py`: the pre-call brief for a case
- `live_replay.py`: steps through a transcript, runs `extract()` per turn, raises prompts and the promise check
- `postcall.py`: draft summary, agent approval, write-back to `layer3.text_features`
- `review.py`: confidence gates and the team-lead queue (`layer4.review_queue`)
- `models/promise_break/`: `train.py`, `score.py` (`score_promise()`), `explain.py` (Ayush)
- Tests in `layer4/tests/`
