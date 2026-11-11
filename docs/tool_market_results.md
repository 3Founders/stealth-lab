# Tool market report

> Source of this copy: a local scratch Postgres (the 186-195 migrations, the Phase 2 comparison runs and the P3 checks), 2026-11-11, not production. Re-run the command below on a database with real traffic for the live numbers.

Generated 2026-11-11 14:02 UTC by `python scripts/tool_market_report.py --since 7d`. Window: the last 7.0 days. Goals: web.

## Exploration

- Exploration rate: 100% (21/21) of verified tool attempts in the window went to a card with fewer than 10 judged attempts before it, or to a trial card.
- Cards tried in the window: 1; with a trial: 0.

## Graduation and retirement

- Graduation rate: 100% (1/1) of cards past the 'new' stage.
- Time to graduation: median 0.0 days, 20 judged attempts (n=1 cards with a run history).
- Stages now: graduate 1, watch 0, new 0, retiring 0, retired 0.
- Replacement rate: no data (no card is retired).

## Failure and harm

- Failure rate: 0% (0/21) of verified tool attempts in the window failed.
- Gate decisions in the window: 212; denied or sent for approval: 83% (175/212); of those, R3/R4: 97% (170/175).
- Harm: no harm signal is recorded yet beyond gate refusals (no incident table); this line stays explicit until one exists.

## Money (credits only)

- Provider-funded trial spend: no data.
- Listing charges: owner/refunded: 1 calls, $0.0040 (provider $0.0034, kell $0.0006); owner/settled: 2 calls, $0.0080 (provider $0.0068, kell $0.0012); provider_trial/held: 1 calls, $0.0040 (provider $0.0034, kell $0.0006).
- Cost per verified success (tool arm): $0.0070 ($0.1470 / 21 passes).

## Trust calibration

Cards binned by their trust lower bound from evidence before the window; observed pass rate inside it. A calibrated bound is at or below the observed rate.

| lower bound bin | cards | attempts in window | mean lower bound | observed pass rate | calibrated |
|---|---|---|---|---|---|
| 0.0-0.2 | 1 | 21 | 0.050 | 100% | yes |
| 0.2-0.4 | 0 | 0 | - | - | no data |
| 0.4-0.6 | 0 | 0 | - | - | no data |
| 0.6-0.8 | 0 | 0 | - | - | no data |
| 0.8-1.0 | 0 | 0 | - | - | no data |

## Caveats

- 21 verified tool attempts in the window, 0 before it. Rates on fewer than 30 attempts are indicative only.
- Verified attempts are comparison runs (task_runs) only; real-use observations count in tool_trust but carry no timestamps per card here.

