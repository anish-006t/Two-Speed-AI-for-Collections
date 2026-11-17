# Notes AI answers check

6 / 10 cited answers passed every guardrail (others fell back to the ranked notes).

| Pass | Question | Answer (first part) |
|---|---|---|
| yes | case CS-2025-148804 notes: any complaint from the customer? | The notes do not answer this question. |
| yes | case CS-2026-172704: any notes on job loss, illness or reduced hours affecting ability to pay? | Yes, the notes mention a pending disability claim since August and the customer being off work sick since August, with requests to reduce the payment [N-2027969] [N-2036499] [N-2032218]. |
| yes | case CS-2026-919135: any notes on job loss, illness or reduced hours affecting ability to pay? | Notes report job loss and financial hardship affecting ability to pay [N-7526932][N-7521930][N-7531050][N-7524130][N-7526903]. No illness or reduced hours are noted. |
| no:  | dossier CS-2024-918645 : y a-t-il une note sur une entente de paiement ou une date de versement? | No agent notes for CS-2024-918645 between 2025-09-28 and 2026-09-28. |
| yes | dossier CS-2025-103698 : le cas a-t-il été transféré à un gestionnaire d'après les notes? | The notes do not answer this question. |
| no:  | case CS-2024-795142 notes: any complaint from the customer? | No agent notes for CS-2024-795142 between 2025-09-28 and 2026-09-28. |
| yes | Do the notes for case CS-2026-790380 say anything about the customer struggling financially? | Yes, the notes say the customer’s shifts or hours were cut at work since June and that they asked about reducing the payment or hardship options [N-0032289] [N-0037954]. |
| yes | why can't the customer pay? check the notes on case CS-2026-896672 | The customer could not pay because they were hospitalized the previous month and had no income [N-6034739], and had been on medical leave since May [N-6024732]. They also asked whether the payment could be reduced [N-6032148]. |
| no: bm25+vectors | Please summarize what the agent notes indicate regarding the customer's financial difficulties on case CS-2026-721606. | 3 of 3 notes for CS-2026-721606 (2025-09-28 to 2026-09-28), most relevant first:
2026-09-26 [N-3037870] spoke w/ cust. mortgage renewed at higher rate. can pay $260 on Oct 16. asked about hardship options. refer to hardship team
2026-08-05 [N-3026790] CUST CALLED IN MORTGAGE RENEWED AT HIGHER RATE I |
| no:  | Did a manager get involved? Look at the notes for case CS-2021-718384. | No agent notes for CS-2021-718384 between 2025-09-28 and 2026-09-28. |

## Re-run after two general fixes (same 4 questions, so not an independent sample)
The 4 fallbacks above came from the notes search, not the AI: (1) without a period asked, only the last 12 months
of notes were searched, so three older cases (opened 2021 and 2024) showed "no notes"; now all of a case's notes are
searched; (2) "Please summarize…" was not recognised as a question; summarise / explain / describe / tell me now are.
After the fixes all 4 passed every guardrail, e.g. "Yes, a manager was involved: the case was escalated to the CE on
2021-04-25 [N-2003775], and a TL review was completed on 2021-04-27 [N-2003781]."
