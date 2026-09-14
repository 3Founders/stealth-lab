# Pokemon Red agent-memory experiment — handoff

This is the authoritative doc for continuing this experiment. It was written
for a handoff to a new collaborator (Chaitanya) after the original author's
Claude usage became restricted mid-experiment. If anything below conflicts
with what you observe in the actual code or recorder output, trust what you
observe — this doc describes the repository as of the commit that added it,
and is not a substitute for `game_state`/the recorder's own files.

## A. Objective

Every run has the same objective: **defeat Brock and obtain the Boulder
Badge**, starting from the canonical checkpoint (below), using only the
tools available in that run's condition.

## B. Experimental conditions

Exactly three conditions. Do not add others without updating this doc.

- **A — RAW**: fresh Claude session, Game MCP only, no Stealth MCP, no web,
  same ROM/checkpoint/objective/action-observation interface as every other
  condition.
- **B — NAIVE NOTES**: fresh Claude session, Game MCP, a naive `notes.md`
  file the agent maintains itself as its only memory, no Stealth MCP, no
  web. Same checkpoint/objective/interface as A.
- **C — STEALTH**: fresh Claude session, Game MCP **and** Stealth MCP, no
  web. Same checkpoint/objective/interface as A.
  - **C is a fresh Stealth *discovery* experiment.** Do not ingest Run A's
    transcript, notes, or failure mode into Stealth before starting C. The
    question C answers is whether Stealth helps a fresh agent discover and
    organize useful knowledge *during* the run itself — not whether Stealth
    can replay a hint it was handed in advance.

## C. Fairness rules

- Same model where possible across conditions.
- Same ROM, same canonical checkpoint, same objective, same Game MCP
  action/observation interface.
- Fresh Claude session for every run — no conversation carries over between
  conditions.
- No web access in any condition.
- No human gameplay hints and no human rescue/intervention once a run has
  started, no matter how long the agent struggles.
- The dashboard (pokemon-agent's own web UI, if you open it) may stay open
  for your own observation, but must never be used to click/type/act on the
  game during a run.
- Do not hand Condition C the Oak's Parcel → return-to-Oak solution manually
  — that is exactly the thing C is testing whether Stealth can discover on
  its own.
- Do not import Run A's experience/notes/failure into Condition C.
- Every run starts from the canonical checkpoint, verified fresh via
  `game_state` before the objective is given to the agent.
- The canonical checkpoint itself is never modified by a run (see section H).

## D. Run A (RAW) result — actual recorded data

**Important, read before using this section for comparison:** Run A has no
recorder-format record — but the reason is more specific than "the recorder
didn't exist yet," and was confirmed by cross-checking file timestamps, git
history, and a live (read-only) query against the still-running
pokemon-agent process:

- pokemon-agent session `20260912_213553_179047` ("pokemon_experiment_start_v2_session")
  was created at **2026-09-12T21:35:53Z**, and its checkpoint save
  (`pokemon_experiment_start_v2`, `save_count: 1`) was made in that same
  session — this is the session Run A was played in.
- The recorder module and Game MCP's hook into it
  (`backend/app/experiments/pokemon_red_recorder.py`,
  `backend/app/game_mcp/server.py`) were written **later that session**, at
  **2026-09-13 03:40–03:47 IST (2026-09-12 22:10–22:17 UTC)** — i.e.
  roughly 35–40 minutes *into* Run A's session, not before it.
- The recorder's own smoke test (`20260912T221700Z-9f160f`, condition
  `recorder-smoke`) ran at **2026-09-12T22:17:00–22:17:23Z**, and its one
  `game_load` call loaded that exact same already-active Run A session —
  proving the recorder worked, mid-Run-A.
- Per pokemon-agent's own `GET /games`, that session kept accumulating
  activity (`turns: 837`) until **`updated_at` 2026-09-13T02:05:12Z** —
  **over four more hours after the recorder existed and was proven
  working.**

So the recorder was not simply "too late to exist" — it existed and worked
for the majority of Run A's session, but **`recorder.py start` was never
invoked for Run A**, at any point in its timeline. The hook
(`log_mcp_call`) is a true no-op whenever no run is active (checked via the
`.active_run` pointer file), so every one of Run A's real tool calls simply
went unlogged — not lost, never captured, because instrumentation was never
turned on for that specific run. There is no orphaned/partial run record
and no second `.json`/`.jsonl` pair anywhere in `runs/` — the only run file
on disk is the smoke test above, and it must still be excluded from any
A/B/C comparison (see `results/run-01-raw.md` and `results/summary.csv`).

The one quantitative artifact that *did* survive, because pokemon-agent
tracks it independently of our recorder, is that session's own turn
counter: **`turns: 837`** (per live `GET /games` as of this correction).
This is **not** equivalent to the recorder's `game_actions` (different
counting method, tracked by a different system, not validated against our
`_classify_call` logic) — report it as a separate, pokemon-agent-native
data point if you cite it, never relabel it as `game_actions`. No
`game_state_calls` / `game_screenshot_calls` / saves / loads / resets /
wall-clock-seconds are recoverable at all — pokemon-agent's schema doesn't
track those, and this doc does not invent numbers for them.

**Also of note:** as of this correction, that session's live emulator state
still matches Run A's exact end-of-run state (Squirtle, Oak's Parcel in
bag, Viridian City, 0 badges) — nobody has reset or reloaded it since. If
that session is ever reset/reloaded/overwritten before someone deliberately
saves this state under its own name, that end-state is gone permanently.

What is known about Run A beyond the above is the qualitative outcome,
established from the actual play session:

**Outcome: FAILURE.** RAW Claude did not obtain the Boulder Badge.

It successfully:
- Loaded the canonical checkpoint.
- Navigated the opening sequence.
- Obtained Squirtle.
- Won the first rival battle.
- Reached Viridian City.
- Obtained Oak's Parcel.

It then failed because it **never connected obtaining Oak's Parcel with
returning to deliver it to Professor Oak in Pallet Town.** Consequently the
game's gate on the northern Route 2 path (which only opens after that
delivery) stayed shut, and the agent concluded that route was simply
inaccessible rather than recognizing it needed to backtrack to Oak first. It
did not reach Brock.

This is baseline data for comparison against B and C. **Do not turn this
into a hint and do not inject it into Condition C** — C must discover (or
fail to discover) the Oak's Parcel connection on its own.

Going forward, **always run `recorder.py start` before the very first tool
call of a run**, not just before the objective is handed to the agent —
Run A's own gap happened because the recorder was built mid-session and
never explicitly started for the run already in progress. Run **both** B
and C through the recorder from the start
(`recorder.py start ... --condition {notes|stealth}`) so their runs get
real quantitative data that Run A lacks, and treat Run A itself as
qualitative-only plus the one recovered pokemon-agent-native data point
(`turns: 837`) in any comparison — do not backfill fabricated recorder
counters for it.

## E. Recorder usage

`experiments/pokemon-red/recorder.py` is a thin CLI shim; all real logic is
in `backend/app/experiments/pokemon_red_recorder.py` (shared with the hook
`app/game_mcp/server.py` calls on every pokemon-agent HTTP request, so the
CLI and the automatic call-counting can never disagree about file formats).

```bash
# Start a run (refuses if a run is already active)
python experiments/pokemon-red/recorder.py start \
  --condition stealth \
  --checkpoint pokemon_experiment_start_v2 \
  --model claude-sonnet-5

# ... play the run — every game_state/game_screenshot/game_action/
# game_save/game_load/game_reset call through Game MCP is counted
# automatically, no extra step needed ...

# Finish on success
python experiments/pokemon-red/recorder.py finish --success

# Or finish on failure, with a reason
python experiments/pokemon-red/recorder.py finish --failure-reason "did not connect Oak's Parcel to returning to Oak"

# Check whether a run is currently active
python experiments/pokemon-red/recorder.py status

# Rebuild results/summary.csv from every run record on disk
python experiments/pokemon-red/recorder.py summarize
```

Where things live:
- One run = two files under `experiments/pokemon-red/runs/`: `<run_id>.json`
  (the final summary record) and `<run_id>.jsonl` (a chronological event
  log — `run_started`, one event per counted tool call, `run_finished`).
- `experiments/pokemon-red/results/summary.csv` is always rebuilt in full
  from every `runs/*.json` on `finish` (or `summarize`) — it is a derived
  view, never hand-edited.
- `run_id` format: `<UTC timestamp>-<6 hex chars>`, e.g.
  `20260912T221700Z-9f160f`.

What is recorded, precisely:
- `game_actions` counts individual action **strings**, not MCP calls — one
  `game_action(["press_a","wait_60"])` call adds **2**, not 1.
- `game_state_calls` / `game_screenshot_calls` / `game_saves` /
  `game_loads` / `game_resets` count real MCP tool invocations by their
  actual HTTP call shape. `game_load`'s internal session-routing probes
  (`GET /games/current`, `GET /games`) are **not** counted as separate tool
  calls or extra loads — only the real `POST /load` or
  `POST /games/{sid}/load` call is counted as one `game_load`.
- **No LLM token or turn counts, ever.** Claude Code does not expose a
  reliable local count of either, and this recorder does not estimate one
  from MCP call counts — an MCP call is not a model turn, and a game action
  is not a token. These fields are simply absent from every run record;
  do not add estimated values by hand.
- **`blackouts` and `battle_losses` are always `null`.** pokemon-agent's own
  `/state` exposes no battle-result/blackout/faint signal to detect these
  from (only `in_battle` / battle type / enemy while a battle is active).
  Inferring a loss from something like "all party HP hit 0" would be
  exactly the kind of vague-state-change guess this benchmark's design
  forbids — so these stay `null`, not guessed.
- No conversation text, prompts, env vars, or credentials are ever
  recorded. No response body (a `/state` JSON blob, a `/screenshot` PNG) is
  ever written to the event log — only the small, specific request-body
  fields the recorder's `_classify_call` names (a save/load `name`, a reset
  session `name`, the literal action-string list).
- The existing `recorder-smoke` run (`20260912T221700Z-9f160f`) is a
  recorder self-test, not an experiment — exclude it from any analysis (see
  `results/run-01-raw.md`).

## F. Game MCP

`backend/app/game_mcp/server.py` — a **separate** MCP server from the
`stealthlab` one (`backend/app/mcp_server/server.py`); it never imports
`app.mcp_server` and never touches StealthLab's Postgres-backed knowledge
graph. **Its six tools are frozen for this experiment — do not add, remove,
or change the signature of any of them:**

| Tool | Wraps |
|---|---|
| `game_state` | `GET /state` |
| `game_screenshot` | `GET /screenshot` |
| `game_action` | `POST /action` `{"actions": [...]}` |
| `game_save` | `POST /save` `{"name": ...}` |
| `game_load` | `POST /load` or, when a game session is active, `POST /games/{sid}/load` (see below) |
| `game_reset` | `POST /games/new` `{"name": ...}` |

It is a thin pass-through HTTP client for the already-running
`pokemon-agent` server — no game logic, action-grammar parsing, or state
interpretation lives in this MCP; whatever `pokemon-agent` returns is
returned verbatim (or the tool raises, on any non-2xx / connection failure —
never a fake success). It does not write to `.stealth/` or touch StealthLab
in any way.

`game_load` has one caller-side fix worth knowing: `pokemon-agent`'s flat
`POST /load` only reads its legacy flat `saves/` directory and 404s on a
session-scoped save even when one exists. If a game session is active,
`game_load` instead checks that session's own latest save name (via
`GET /games`) and, **only if it exactly matches the requested name**, loads
it through `POST /games/{sid}/load`. On a name mismatch it refuses with a
clear error rather than silently restoring the wrong save under the wrong
claimed name. It can only load a session's single most-recent save, never
an older one further back in that session's history.

### Configure / start Game MCP

Requires `pokemon_agent_base_url` in `backend/app/config.py`'s `Settings`
(default `http://localhost:8765`, overridable via the
`POKEMON_AGENT_BASE_URL` env var) and `pokemon-agent` already running at
that address (see section G).

Add it to your own `.mcp.json` (do not commit a machine-specific absolute
path — see section I):

```json
{
  "mcpServers": {
    "game-mcp": {
      "type": "stdio",
      "command": "python",
      "args": ["<path-to-your-clone>/backend/app/game_mcp/server.py"]
    }
  }
}
```

Verify the tool surface offline (no `pokemon-agent` needed):

```bash
cd backend
python -m pytest tests/test_game_mcp_offline.py -q
```

## G. pokemon-agent setup

`pokemon-agent` is a **separate project this repo does not start, own, or
vendor** — it is a Game Boy emulator + HTTP server that Game MCP is a thin
client for. This repo does not include its source, and neither the ROM nor
any emulator save state is committed here (see `.gitignore` and the note in
section H).

1. Obtain `pokemon-agent` and set it up independently — outside this repo,
   per its own instructions.
2. **Obtain a Pokemon Red ROM independently and legally** (e.g. dump it
   yourself from a cartridge you own). This repo does not distribute it and
   GitHub is not a source for it.
3. Local setup convention used during this experiment: name the ROM file
   `pokemon_red.gb` (or `.gbc`) and point `pokemon-agent`'s own config at
   it — check `pokemon-agent`'s own docs for the exact config key, since
   that project is not part of this repo.
4. Start the `pokemon-agent` server. It defaults to
   **`http://localhost:8765`** — the same address `game_mcp`'s
   `pokemon_agent_base_url` setting expects by default.
5. `pokemon-agent` ships a dashboard you can open locally in a browser for
   your own observation (see the fairness rule in section C: never use it
   to act on the game during a run).
6. Verify it's up and reachable before starting any run:
   ```bash
   curl http://localhost:8765/
   # expect: {"name": "pokemon-agent", ...}
   curl http://localhost:8765/state
   ```

**Port collision to know about:** `pokemon-agent` defaults to `:8765`, and
so does StealthLab's own MCP HTTP server
(`uvicorn app.mcp_server.server:app --port 8765`, see
`backend/README_MCP_SERVER.md`). If Condition C needs both Game MCP (which
just needs `pokemon-agent` reachable) **and** the Stealth MCP server running
at the same time, start the StealthLab MCP server on a different port (its
`_MCP_PORT` in `backend/app/mcp_server/server.py`, and the matching URL in
your own `.mcp.json`) rather than moving `pokemon-agent` off its default —
`game_mcp`'s default already assumes `pokemon-agent` is on `:8765`.

## H. Checkpoint reproducibility

The canonical checkpoint is:

    pokemon_experiment_start_v2

(The earlier `experiment_start` checkpoint is invalid/pre-boot — **do not
use it** for any experimental run.)

**Do not commit `pokemon_experiment_start_v2.state`** (or any other save
file) to this repo — it is a local emulator artifact, not source code, and
is excluded by `.gitignore`.

The checkpoint represents this exact game state:
- Pokemon Red, player name **RED**, rival name **BLUE**.
- Location: **Red's House 2F**, player has control, standing **before**
  leaving the bedroom / before the Professor Oak intro sequence triggers.
- `map_id 38`, `x=3`, `y=6`, facing **up**.
- **$3000**, **0 badges**, **empty party**, **empty bag**.

**Honest limitation — this is not push-button-deterministic from this
repo's current tooling.** There is no script in this repository that
recreates this save file automatically; it was produced by manually
playing `pokemon-agent`'s boot sequence up to this exact point and then
calling `game_save`. Re-running the Game Boy's boot RNG and dialog timing
on a different machine is not guaranteed to reproduce a byte-identical
`.state` file, and this doc does not claim it will. What **is** reproducible
is the *state*, verified through the documented interface:

1. `game_reset` (→ `POST /games/new`) for a fresh boot with no save loaded.
2. Play through the standard Pokemon Red opening far enough to name the
   player **RED** and the rival **BLUE**, stopping the instant control
   returns to the player in Red's House 2F, before walking downstairs /
   before Oak's sequence triggers.
3. Call `game_state` and confirm every field above matches
   (`map_id 38`, `x=3`, `y=6`, facing up, $3000, 0 badges, empty
   party/bag) — treat this as the pass/fail check, not visual inspection.
4. Only once verified, call `game_save(name="pokemon_experiment_start_v2")`.

Treat the *fields returned by `game_state`* as the source of truth for "is
this the canonical checkpoint," not the raw save file's bytes.

## I. Chaitanya setup

Commands below are POSIX-shell; translate `export`/paths for PowerShell if
needed. Two **separate** Python environments are involved — do not mix
them.

1. **Clone this repo.**
   ```bash
   git clone https://github.com/3Founders/stealth-lab
   cd stealth-lab
   ```
2. **System dependencies:** Python 3.12+, Node (for the frontend, only if
   you need the dashboard/graph UI), Postgres 15+ with pgvector (only
   needed for the Stealth MCP server itself — Game MCP alone needs none of
   this).
3. **StealthLab Python environment** (separate from pokemon-agent's):
   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate        # or .venv\Scripts\activate on Windows
   ```
4. **Install StealthLab's dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
5. **Set up `pokemon-agent` separately** — its own repo, own Python
   environment (or however it's packaged); do not install its deps into
   the StealthLab `.venv` from step 3. See section G.
6. **Obtain/build the Pokemon Red ROM independently** (section G, step 2).
   Never commit it here.
7. **Start `pokemon-agent`** (its own instructions; defaults to
   `http://localhost:8765`).
8. **Start Game MCP** — add the `.mcp.json` entry from section F (with your
   own clone path and Python interpreter), pointing at the StealthLab
   `.venv`'s Python from step 3.
9. **Start Stealth MCP when needed** (Condition C only) — see
   `backend/README_MCP_SERVER.md` for `STEALTHLAB_MCP_TOKEN` setup and the
   exact `uvicorn app.mcp_server.server:app --port <port>` command; mind
   the port collision noted in section G.
10. **Verify all MCP servers:**
    ```bash
    cd backend
    python -m pytest tests/test_game_mcp_offline.py -q        # Game MCP, offline
    python -m pytest tests/test_mcp_six_tool_surface_offline.py tests/test_mcp_minimal_surface_offline.py -q  # StealthLab MCP, offline
    curl http://localhost:8765/                                 # pokemon-agent, live
    curl -X POST http://127.0.0.1:8765/mcp -H "Authorization: Bearer $STEALTHLAB_MCP_TOKEN"  # Stealth MCP, live (only if running it)
    ```
11. **Verify the canonical checkpoint** — with `pokemon-agent` up, use
    Game MCP's `game_load`/`game_state` (or `pokemon-agent`'s own API
    directly) to load `pokemon_experiment_start_v2` and confirm the fields
    in section H before starting any timed run.
12. **Run C** (fresh Stealth discovery — section J).
13. **Run B** (naive notes — section J).
14. **Finish/summarize the recorder** after each run:
    ```bash
    python experiments/pokemon-red/recorder.py finish --success   # or --failure-reason "..."
    python experiments/pokemon-red/recorder.py summarize
    ```
15. **Commit the resulting experiment metadata/results** — the new
    `runs/*.json` / `*.jsonl` files and the refreshed `results/summary.csv`
    only. Never commit ROM files, `.state`/`.sav` files, or `.env`/tokens.

## J. Experimental execution protocol

### Run C (Stealth) — do this first

1. Fresh Claude session.
2. Load `pokemon_experiment_start_v2` (via Game MCP's `game_load`) and
   verify state via `game_state` (section H's field list).
3. Give the agent the objective exactly as written in section A — nothing
   more, no hints about Oak's Parcel or any other route-blocking mechanic.
4. No web access.
5. Stealth MCP **and** Game MCP both enabled. **Do not seed Stealth with
   Run A's transcript, notes, or failure mode** — this is a fresh-discovery
   run.
6. Start the recorder before giving the agent the objective:
   ```bash
   python experiments/pokemon-red/recorder.py start --condition stealth --checkpoint pokemon_experiment_start_v2 --model <model-id>
   ```
7. Let the agent operate until it reaches the Boulder Badge or genuinely
   gets stuck/gives up — do not rescue it, no matter how long it takes or
   how obviously stuck it looks.
8. Finish the recorder with the real outcome:
   ```bash
   python experiments/pokemon-red/recorder.py finish --success
   # or
   python experiments/pokemon-red/recorder.py finish --failure-reason "<what actually happened>"
   ```

### Run B (Naive notes) — do this second

1. Fresh Claude session (no memory of Run C).
2. Same checkpoint, same objective, same verification as Run C steps 2–3.
3. Game MCP only — no Stealth MCP, no web.
4. The agent maintains its own `notes.md` as its only memory aid across its
   own turns within this one run — a plain scratch file it reads/writes
   itself, not pre-seeded with anything from Run A or Run C.
5. Start the recorder (`--condition notes`), let it run to a genuine
   terminal outcome without rescue, finish the recorder the same way as
   Run C step 8.

Do not redesign either protocol beyond what's written above — e.g. don't
add a fourth condition, don't give C a partial hint "to be fair," don't let
B's `notes.md` be pre-populated.

## Files in this directory

| Path | What it is |
|---|---|
| `recorder.py` | CLI entry point (see section E) |
| `runs/<run_id>.json` | One run's final summary record |
| `runs/<run_id>.jsonl` | One run's chronological event log |
| `results/summary.csv` | Rebuilt from every `runs/*.json` on `finish`/`summarize` |
| `results/run-01-raw.md` | Human-readable writeup of the one run currently on disk (the recorder smoke test) plus Run A's known qualitative outcome — see that file for the important distinction between the two |
