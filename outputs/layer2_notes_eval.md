# Layer 2 agent-notes retrieval eval

300 random cases (seed 7), 349 questions from templates written without seeing Layer 2's code; label = note type; hit = a note of that type in the top 3.

| Method | hit@3 | MRR | hit@3 French-only | p50 ms | p95 ms |
|---|---|---|---|---|---|
| baseline | 9.2% | 0.077 | 19.4% (n=36) | 0.0 | 0.1 |
| keyword (text only) | 75.4% | 0.647 | 47.2% (n=36) | 0.1 | 0.4 |
| keyword | 94.6% | 0.930 | 91.7% (n=36) | 0.1 | 0.4 |
| hybrid (text only) | 87.1% | 0.713 | 80.6% (n=36) | 124.5 | 256.2 |
| hybrid | 95.4% | 0.843 | 97.2% (n=36) | 22.9 | 31.4 |

| Topic | n | baseline | keyword (text only) | keyword | hybrid (text only) | hybrid |
|---|---|---|---|---|---|---|
| hardship | 149 | 14.8% | 67.8% | 90.6% | 81.2% | 93.3% |
| dispute | 49 | 18.4% | 77.6% | 91.8% | 91.8% | 91.8% |
| payment | 29 | 3.4% | 6.9% | 96.6% | 55.2% | 93.1% |
| escalation | 122 | 0.0% | 100.0% | 100.0% | 100.0% | 100.0% |
