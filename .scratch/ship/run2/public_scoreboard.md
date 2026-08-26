# SS40 Public Scoreboard

Generated: 2026-08-26T17:56:31+00:00
Sources: `C:\Users\user\sl-measure\experiments\harness\real_arms_run2.jsonl` (15 task rows) · `C:\Users\user\sl-measure\experiments\harness\real_spend_run2.jsonl` (113 attempts)
Fixture pack: micro (8 procedures) · usable paired tasks: 11/15

> **Read first:**
> - small-n caveat: A vs B (n=2), A vs C (n=3), B vs C (n=1) have fewer than 6 discordant pairs (the RUN #1 k>=6 significance floor) - directional only, not headline evidence.
> - 4 of 15 task rows excluded from the paired statistics (missing or invalid episode in some arm); a task contributes only when EVERY arm produced a valid episode.

## Arms

| arm | n | pass | cost tot/mean $ | false_reuse | stale_refusal | unseen |
|---|---:|---:|---:|---:|---:|---:|
| A (frontier solo) | 11 | 9/11 (0.82) | 0.0850/0.0077 | 0/11 (0.00) | no offers | 0/1 |
| B (+ conventional memory) | 11 | 7/11 (0.64) | 0.1166/0.0106 | 2/11 (0.18) | 0/7(missed 0) | 0/1 |
| C (+ verified procedures) | 11 | 6/11 (0.55) | 0.0841/0.0076 | 0/11 (0.00) | 7/7(missed 0) | 0/1 |

Every rate ships with its numerator/denominator - no bare rates.

Served by: openai/gpt-4o-mini, ox-alpha

## Stale-refusal attribution (gate-mechanical vs model-initiated)

| arm | gate-mechanical | model-initiated | unattributed |
|---|---:|---:|---:|
| A | 0 | 0 | 0 |
| B | 0 | 0 | 0 |
| C | 6 | 1 | 0 |

gate-mechanical = substrate check_applicability decided the refusal (model never saw the card, or a proposed reuse was blocked); model-initiated = the agent refused on its own after seeing the card; unattributed = a correct stale refusal with no journal entry naming the mechanism. A stale_refusal count with zero model-initiated entries reflects the surface's gating design, not detection by the model serving that arm.

## Pairwise comparisons (exact McNemar on pass/fail)

- **A vs B**: A vs B: 2 discordant pairs (first-arm-only 2, second-arm-only 0) | exact-p=0.5000 | power@observed-split(q=1.000,n=2)=0.00 | MDE@80%: unreachable with 2 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
- **A vs C**: A vs C: 3 discordant pairs (first-arm-only 3, second-arm-only 0) | exact-p=0.2500 | power@observed-split(q=1.000,n=3)=0.00 | MDE@80%: unreachable with 3 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
- **B vs C**: B vs C: 1 discordant pairs (first-arm-only 1, second-arm-only 0) | exact-p=1.0000 | power@observed-split(q=1.000,n=1)=0.00 | MDE@80%: unreachable with 1 discordant pairs | n-for-80%@observed-ratio=beyond planning cap

POWER-ANALYSIS FOOTER (exact McNemar, alpha=0.05, target power=0.8)
  A vs B: 2 discordant pairs (first-arm-only 2, second-arm-only 0) | exact-p=0.5000 | power@observed-split(q=1.000,n=2)=0.00 | MDE@80%: unreachable with 2 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
  A vs C: 3 discordant pairs (first-arm-only 3, second-arm-only 0) | exact-p=0.2500 | power@observed-split(q=1.000,n=3)=0.00 | MDE@80%: unreachable with 3 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
  B vs C: 1 discordant pairs (first-arm-only 1, second-arm-only 0) | exact-p=1.0000 | power@observed-split(q=1.000,n=1)=0.00 | MDE@80%: unreachable with 1 discordant pairs | n-for-80%@observed-ratio=beyond planning cap

## Spend

SPEND: 113 attempts (64 billed, 49 failed) · tokens 18,447in/38,088out · $0.4270 · models: openai/gpt-4o-mini, ox-alpha
- shared-pool saturation (HTTP 429) attempts: 44
- billed cost by arm: A $0.1227, B $0.1666, C $0.1373
- models used: openai/gpt-4o-mini, ox-alpha

## Canonical terminal rendering

```
==============================================================================
SS40 Public Scoreboard
tasks total=15 usable(all arms valid)=11
arm    n        pass      cost tot/mean $     tokens        reuse   false_reuse     stale_refusal    unseen
-----------------------------------------------------------------------------------------------------------
A     11 9/11 (0.82)     0.085/0.0077        11,214  0/11 (0.00)   0/11 (0.00)         no offers       0/1
B     11 7/11 (0.64)    0.1166/0.0106        15,148  6/11 (0.55)   2/11 (0.18)     0/7(missed 0)       0/1
C     11 6/11 (0.55)    0.0841/0.0076        11,455  6/11 (0.55)   0/11 (0.00)     7/7(missed 0)       0/1
(A: frontier solo | B: + conventional memory | C: + verified procedures)

PAIRWISE (exact McNemar on pass/fail discordant pairs)
POWER-ANALYSIS FOOTER (exact McNemar, alpha=0.05, target power=0.8)
  A vs B: 2 discordant pairs (first-arm-only 2, second-arm-only 0) | exact-p=0.5000 | power@observed-split(q=1.000,n=2)=0.00 | MDE@80%: unreachable with 2 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
  A vs C: 3 discordant pairs (first-arm-only 3, second-arm-only 0) | exact-p=0.2500 | power@observed-split(q=1.000,n=3)=0.00 | MDE@80%: unreachable with 3 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
  B vs C: 1 discordant pairs (first-arm-only 1, second-arm-only 0) | exact-p=1.0000 | power@observed-split(q=1.000,n=1)=0.00 | MDE@80%: unreachable with 1 discordant pairs | n-for-80%@observed-ratio=beyond planning cap
==============================================================================
```
