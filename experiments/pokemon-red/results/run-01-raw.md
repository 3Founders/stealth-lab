# Run 1 — results snapshot

**Read this first:** the row currently in `results/summary.csv` and in
`runs/20260912T221700Z-9f160f.json` is a **recorder smoke test**, not the
RAW (Condition A) experimental run. Condition A has no machine-recorded
metrics, but *not* because the recorder didn't exist during it — an
investigation (file timestamps, git history, and a live read-only query
against pokemon-agent) found the recorder was built and smoke-tested
roughly 35–40 minutes *into* Run A's session, and Run A's session kept
running for over four more hours after that. `recorder.py start` was
simply never invoked for Run A at any point — the instrumentation existed
and worked, but was never turned on for that specific run. See
`README.md` section D for the full timeline. Both facts (smoke test vs.
Condition A) are laid out separately below — do not merge them.

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

## Condition A (RAW) — actual played run, qualitative outcome + one recovered metric

No recorder-format JSON/JSONL record exists for this run — not because it
predates the recorder, but because `recorder.py start` was never invoked
for it (see `README.md` section D for the evidence). This doc does not
invent recorder-schema numbers (`game_actions`, call counts, wall-clock)
for it. One number *is* independently recoverable, from pokemon-agent's
own session bookkeeping rather than our recorder:

| Field | Value |
|---|---|
| Condition | A — RAW |
| Run ID | not recorded (recorder never started for this run) |
| pokemon-agent session | `20260912_213553_179047` |
| Start checkpoint | `pokemon_experiment_start_v2` |
| Outcome | **Failure** — did not obtain the Boulder Badge |
| Session created_at | `2026-09-12T21:35:53Z` (live `GET /games`) |
| Session updated_at (last activity) | `2026-09-13T02:05:12Z` (live `GET /games`) |
| pokemon-agent `turns` (session-native counter, **not** the recorder's `game_actions`) | 837 |
| Wall clock | not recorded (recorder schema; approximate elapsed span above is ~4h29m by session timestamps, not the same measurement) |
| game_state_calls / game_screenshot_calls | not recorded |
| Saves | not recorded (session has 1 save total — the checkpoint itself, `save_count: 1`) |
| Loads / Resets | not recorded |
| Failure reason | Never connected obtaining Oak's Parcel to returning it to Professor Oak in Pallet Town, so the northern Route 2 gate stayed shut; concluded the route was inaccessible rather than backtracking to Oak |

`turns: 837` and the recorder's `game_actions` are **not interchangeable**
— different system, different counting method, never cross-validated
against each other. Report them separately if you cite both; don't relabel
one as the other in any comparison table.

As of this correction, that same session's live emulator state is still
sitting at Run A's exact end point (Squirtle, Oak's Parcel in bag, Viridian
City, 0 badges) — it has not been reset or reloaded since. That state will
be lost the moment the session is reset/reloaded/overwritten unless it's
explicitly saved under its own name first.

### Narrative

RAW Claude loaded the canonical checkpoint, played through the opening,
obtained Squirtle, won the first rival battle, and reached Viridian City,
where it picked up Oak's Parcel. From there it explored but never made the
inference that the parcel needed to be hand-delivered back to Professor Oak
in Pallet Town before Route 2 north would open — it treated the blocked
route as a dead end rather than a gated one, and never reached Brock or the
Boulder Badge.

## What to do differently for B and C

Run A's gap was not a timing problem (recorder-doesn't-exist-yet) — it was
that `recorder.py start` was never actually run. Start the recorder
(`recorder.py start --condition {notes|stealth} ...`) as the **very first
step**, before the checkpoint is even loaded, and finish it
(`recorder.py finish --success` / `--failure-reason "..."`) the moment each
run reaches a genuine terminal outcome. Check `recorder.py status` right
after starting to confirm it actually took effect. That will give B and C
real `game_actions` / call-count / wall-clock data that this baseline
lacks — comparable to each other, and comparable to Condition A only via
its qualitative outcome and the recovered `turns: 837` data point.
