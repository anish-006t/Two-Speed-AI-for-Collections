# Layer 2 agent-notes retrieval eval

300 random cases (seed 11), 366 questions from templates written without seeing Layer 2's code; label = note type; hit = a note of that type in the top 3.

| Method | hit@3 | MRR | hit@3 French-only | p50 ms | p95 ms |
|---|---|---|---|---|---|
| baseline | 7.7% | 0.060 | 25.0% (n=44) | 0.0 | 0.1 |
| keyword (text only) | 75.1% | 0.649 | 56.8% (n=44) | 0.1 | 0.4 |
| keyword | 93.7% | 0.911 | 88.6% (n=44) | 0.1 | 0.4 |
| hybrid rrf (text only) | 86.1% | 0.714 | 77.3% (n=44) | 237.4 | 714.8 |
| hybrid rrf | 94.8% | 0.839 | 93.2% (n=44) | 49.7 | 59.6 |
| hybrid (text only) | 85.2% | 0.673 | 77.3% (n=44) | 51.8 | 66.2 |
| hybrid | 96.7% | 0.941 | 93.2% (n=44) | 48.5 | 59.0 |

| Topic | n | baseline | keyword (text only) | keyword | hybrid rrf (text only) | hybrid rrf | hybrid (text only) | hybrid |
|---|---|---|---|---|---|---|---|---|
| hardship | 171 | 13.5% | 66.7% | 89.5% | 80.1% | 91.8% | 78.4% | 95.3% |
| dispute | 42 | 11.9% | 78.6% | 92.9% | 92.9% | 95.2% | 92.9% | 95.2% |
| payment | 27 | 0.0% | 7.4% | 92.6% | 48.1% | 88.9% | 48.1% | 92.6% |
| escalation | 126 | 0.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
