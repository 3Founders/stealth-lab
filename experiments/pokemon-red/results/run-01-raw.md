# Run 1 — results snapshot

**Read this first:** the row currently in `results/summary.csv` and in
`runs/20260912T221700Z-9f160f.json` is a **recorder smoke test**, not the
RAW (Condition A) experimental run. The recorder did not exist yet when
Condition A was actually played, so that run has no machine-recorded
metrics. Both facts are laid out separately below — do not merge them.

## The only recorder run on disk (smoke test — excluded from analysis)

| Field | Value |
|---|---|
| Condition | `recorder-smoke` (not an experimental condition — see `README.md` section B) |
| Run ID | `20260912T221700Z-9f160f` |
| Model | `claude-sonnet-5` |
| Start checkpoint | `pokemon_experiment_start_v2` |
| Outcome | success (trivially — the smoke test's own pass condition) |
| Wall clock | 22.767 s |
| Game actions | 0 |
| State calls | 1 |
| Screenshot calls | 1 |
| Saves | 0 |
| Loads | 1 |
| Resets | 0 |
| Failure reason | — |
| Notes | "smoke test complete, no gameplay actions taken" |

This run proved the recorder's hook, file formats, and `summarize` command
work end to end (one `game_load`, one `game_state`, one `game_screenshot`,
correctly tallied and written to `runs/` and `results/summary.csv`). It
involved zero gameplay and is not comparable to a real A/B/C run — **exclude
it from any cross-condition comparison.**

## Condition A (RAW) — actual played run, qualitative outcome only

No JSON/JSONL record exists for this run (played before the recorder was
built), so the table below has no quantitative columns to report — this doc
does not invent wall-clock/action/call counts for it.

| Field | Value |
|---|---|
| Condition | A — RAW |
| Run ID | not recorded (recorder did not exist yet) |
| Start checkpoint | `pokemon_experiment_start_v2` |
| Outcome | **Failure** — did not obtain the Boulder Badge |
| Wall clock | not recorded |
| Game actions | not recorded |
| State calls | not recorded |
| Screenshot calls | not recorded |
| Saves | not recorded |
| Loads | not recorded |
| Resets | not recorded |
| Failure reason | Never connected obtaining Oak's Parcel to returning it to Professor Oak in Pallet Town, so the northern Route 2 gate stayed shut; concluded the route was inaccessible rather than backtracking to Oak |

### Narrative

RAW Claude loaded the canonical checkpoint, played through the opening,
obtained Squirtle, won the first rival battle, and reached Viridian City,
where it picked up Oak's Parcel. From there it explored but never made the
inference that the parcel needed to be hand-delivered back to Professor Oak
in Pallet Town before Route 2 north would open — it treated the blocked
route as a dead end rather than a gated one, and never reached Brock or the
Boulder Badge.

## What to do differently for B and C

Start the recorder (`recorder.py start --condition {notes|stealth} ...`)
*before* giving the agent the objective, and finish it
(`recorder.py finish --success` / `--failure-reason "..."`) the moment each
run reaches a genuine terminal outcome. That will give B and C real
`game_actions` / call-count / wall-clock data that this baseline lacks —
comparable to each other, even though Condition A predates the
instrumentation.
