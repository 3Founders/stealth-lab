# SS40 Public Scoreboard

Generated: 2026-08-26T17:56:30+00:00
Sources: `C:\Users\user\stealth-lab\experiments\harness\real_arms_results.jsonl` (11 task rows) · `C:\Users\user\stealth-lab\experiments\harness\real_spend.jsonl` (95 attempts)
Fixture pack: micro (8 procedures) · usable paired tasks: 9/11

> **Read first:**
> - small-n caveat: A vs B (n=1), A vs C (n=1), B vs C (n=0) have fewer than 6 discordant pairs (the RUN #1 k>=6 significance floor) - directional only, not headline evidence.
> - 2 of 11 task rows excluded from the paired statistics (missing or invalid episode in some arm); a task contributes only when EVERY arm produced a valid episode.
> - interpretive-validity caveat (independent of sample size): arm(s) C earned 100% of their counted stale_refusal credit from the substrate gate deciding before/instead of the model (see 'Stale-refusal attribution' below), with zero model-initiated refusals logged - the stale_refusal column is descriptive SYSTEM-level behavior here, not evidence of per-model staleness detection.

## Arms

| arm | n | pass | cost tot/mean $ | false_reuse | stale_refusal | unseen |
|---|---:|---:|---:|---:|---:|---:|
| A (frontier solo) | 9 | 5/9 (0.56) | 0.0568/0.0063 | 0/9 (0.00) | no offers | 0/1 |
| B (+ conventional memory) | 9 | 6/9 (0.67) | 0.0730/0.0081 | 1/9 (0.11) | 0/5(missed 0) | 0/1 |
| C (+ verified procedures) | 9 | 6/9 (0.67) | 0.0598/0.0066 | 0/9 (0.00) | 5/5(missed 0) | 0/1 |

Every rate ships with its numerator/denominator - no bare rates.

Served by: ox-alpha

## Stale-refusal attribution (gate-mechanical vs model-initiated)

| arm | gate-mechanical | model-initiated | unattributed |
|---|---:|---:|---:|
| A | 0 | 0 | 0 |
| B | 0 | 0 | 0 |
| C | 5 | 0 | 0 |

gate-mechanical = substrate check_applicability decided the refusal (model never saw the card, or a proposed reuse was blocked); model-initiated = the agent refused on its own after seeing the card; unattributed = a correct stale refusal with no journal entry naming the mechanism. A stale_refusal count with zero model-initiated entries reflects the surface's gating design, not detection by the model serving that arm.

## Pairwise comparisons (exact McNemar on pass/fail)

- **A vs B**: A vs B: 1 discordant pairs (first-arm-only 0, second-arm-only 1) | exact-p=1.0000 | power@observed-split(q=1.000,n=1)=0.00 | MDE@80%: unreachable with 1 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
- **A vs C**: A vs C: 1 discordant pairs (first-arm-only 0, second-arm-only 1) | exact-p=1.0000 | power@observed-split(q=1.000,n=1)=0.00 | MDE@80%: unreachable with 1 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
- **B vs C**: B vs C: no discordant pairs — the test has no input | p=N/A

POWER-ANALYSIS FOOTER (exact McNemar, alpha=0.05, target power=0.8)
  A vs B: 1 discordant pairs (first-arm-only 0, second-arm-only 1) | exact-p=1.0000 | power@observed-split(q=1.000,n=1)=0.00 | MDE@80%: unreachable with 1 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
  A vs C: 1 discordant pairs (first-arm-only 0, second-arm-only 1) | exact-p=1.0000 | power@observed-split(q=1.000,n=1)=0.00 | MDE@80%: unreachable with 1 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
  B vs C: no discordant pairs — the test has no input | p=N/A

## Spend

SPEND: 95 attempts (44 billed, 51 failed) · tokens 12,444in/23,479out · $0.2659 · models: ox-alpha
- shared-pool saturation (HTTP 429) attempts: 51
- billed cost by arm: A $0.0798, B $0.1032, C $0.0831
- models used: ox-alpha

## Canonical terminal rendering

```
==============================================================================
SS40 Public Scoreboard
tasks total=11 usable(all arms valid)=9
arm    n        pass      cost tot/mean $     tokens        reuse   false_reuse     stale_refusal    unseen
-----------------------------------------------------------------------------------------------------------
A      9  5/9 (0.56)    0.0568/0.0063         7,812   0/9 (0.00)    0/9 (0.00)         no offers       0/1
B      9  6/9 (0.67)     0.073/0.0081         9,845   5/9 (0.56)    1/9 (0.11)     0/5(missed 0)       0/1
C      9  6/9 (0.67)    0.0598/0.0066         8,368   5/9 (0.56)    0/9 (0.00)     5/5(missed 0)       0/1
(A: frontier solo | B: + conventional memory | C: + verified procedures)

PAIRWISE (exact McNemar on pass/fail discordant pairs)
POWER-ANALYSIS FOOTER (exact McNemar, alpha=0.05, target power=0.8)
  A vs B: 1 discordant pairs (first-arm-only 0, second-arm-only 1) | exact-p=1.0000 | power@observed-split(q=1.000,n=1)=0.00 | MDE@80%: unreachable with 1 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
  A vs C: 1 discordant pairs (first-arm-only 0, second-arm-only 1) | exact-p=1.0000 | power@observed-split(q=1.000,n=1)=0.00 | MDE@80%: unreachable with 1 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
  B vs C: no discordant pairs — the test has no input | p=N/A
==============================================================================
```
