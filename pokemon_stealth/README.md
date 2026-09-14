# pokemon-stealth

An experiment harness that answers one question:

> Can a fixed/frozen language model improve at Pokemon Red -- fewer tokens,
> fewer LLM calls, fewer failed attempts, faster wall-clock -- purely by
> accumulating reusable external knowledge (Claims / Procedures / Failures)
> from prior runs, with NO weight updates and NO hidden conversation memory?

Every run gets a brand-new LLM call sequence. The only thing that can carry
information from one run to the next is whatever got written to disk by a
previous run -- and which disk location it's allowed to read is what
distinguishes the four experiment conditions below.

## 1. Architecture

```
LLM  <--respond()-->  llm.py (Agent protocol: Anthropic / OpenAI-compat / Mock)
 |
 v
structured game state          state.py (GameState, from real RAM reads)
 |
 v
retrieved knowledge             knowledge.py (STEALTH only: BM25-ish over
 |                               Claims/Procedures/Failures, deduped)
 v
AgentDecision (JSON, validated) agent.py (schema-checked action, never trusted blindly)
 |
 v
macro action                    actions.py (move/interact/choose_move/... )
 |
 v
PyBoy (many frames per action)  emulator.py (PokemonEnv wrapper)
 |
 v
new GameState
 |
 v
success/failure check           missions.py (RAM-derived only -- LLM never
 |                               self-reports success)
 v
knowledge extraction (STEALTH)  extraction.py (one LLM call per run, zero
 |                               items is a valid answer, dedup on write)
 v
persistent Stealth files        knowledge.py writes stealth/{claims,procedures,failures,runs}/
```

`experiment.py::run_one` is the loop that wires all of this together for one
(mission, condition, model, seed). `experiment.py::run_experiment` runs it
across conditions x repeats for one mission.

## 2. Legal ROM note

This harness never downloads, bundles, or generates a Pokemon Red ROM.
Provide the path to your own legally obtained ROM via:

```
POKEMON_RED_ROM=/path/to/pokemon_red.gb
```

as a real environment variable, or in a `.env` file in this directory. If
it's unset or the path doesn't exist, every command that needs it fails
immediately with an explicit message -- nothing silently continues.

## 3. Install

```
cd pokemon_stealth
python -m pip install -e ".[dev]"
```

Requires Python >= 3.10. PyBoy 2.7 was the version actually installed and
smoke-tested while building this harness (see Testing, below) -- its
`memory[addr]` / `button_press`/`button_release` / `save_state`/`load_state`
API is what `emulator.py` targets.

## 4. Env setup

```
POKEMON_RED_ROM=/path/to/pokemon_red.gb
ANTHROPIC_API_KEY=...        # for claude-* models
OPENAI_API_KEY=...           # for gpt-* models
STUDENT_MODEL_BASE_URL=...   # optional: any other OpenAI-compatible endpoint
STUDENT_MODEL_API_KEY=...
```

## 5. Create checkpoints

There is no autonomous pathfinding in this harness by design (STEP 7) --
checkpoints are created by manually playing to the right moment once:

```
pokemon-stealth checkpoint create S0_PALLET --demo
# a window opens; play to just after picking your starter, then Ctrl+C to save
pokemon-stealth checkpoint create S1_VIRIDIAN --from-checkpoint S0_PALLET --demo
pokemon-stealth checkpoint create S3_BEFORE_BROCK --from-checkpoint S1_VIRIDIAN --demo
```

Standardize on Squirtle as the starter (per spec STEP 9) for reproducibility
across runs.

## 6. Run one mission

```
pokemon-stealth run --mission brock --condition fresh --model claude-sonnet-5
pokemon-stealth run --mission brock --condition stealth --model claude-sonnet-5
```

## 7. Run a comparison experiment

```
pokemon-stealth experiment --mission brock --conditions fresh,notes,stealth --runs 3 --model claude-sonnet-5
pokemon-stealth summary
pokemon-stealth curve brock --condition stealth
```

## 8. Teacher/student cross-model transfer (STEP 23)

```
pokemon-stealth teacher-student --mission brock --teacher-model claude-sonnet-5 --student-model claude-haiku-4-5-20251001
```

## 9. Interpreting results

`results/runs.csv` / `results/runs.jsonl` hold one row per run (STEP 19).
`pokemon-stealth summary` groups by condition and reports success_rate,
mean_llm_calls, mean_tokens, mean_cost_usd, mean_actions, mean_wall_time_s,
cost_per_success, success_per_1k_tokens (STEP 20). `pokemon-stealth curve`
gives the sequential learning-curve rows for one mission/condition (STEP 21)
-- plot `tokens`/`success`/`macro_actions` against `run_index` yourself
(no plotting library is bundled, to avoid an unnecessary dependency).

## 10. Game state support

Extracted with HIGH confidence (see `memory_map.py`'s provenance note --
addresses are the public pret/pokered community disassembly, not
independently re-verified against a real ROM in this environment since none
was available):

- `map_id`, position (x, y), badges, money, party (species/level/hp/max_hp/
  status/move ids), party count.

Extracted with MEDIUM/UNVERIFIED confidence (surfaced, but flagged):

- in-battle detection, opponent species/hp; opponent max_hp/level.

Explicitly **not** extracted (no confident source found -- reported as
`None`/absent rather than guessed): facing direction, dialogue/text-box
active state, item names (bag slots are addressable by index only), move
names (moves are surfaced as raw ids).

Run `pokemon-stealth verify-memory-map` once you have a real ROM to sanity
check decoded values against what a fresh boot should show.

## 11. Actions supported

`move`, `interact`, `advance_dialogue`, `open_menu`, `close_menu`,
`select_menu_item`, `choose_move`, `switch_pokemon`, `run_from_battle`,
`use_item` (by bag index), `heal_at_pokemon_center`, `save_game` (best-effort;
success is always reported as unknown -- no RAM-verifiable save-completion
flag was found).

## 12. Missions supported

`starter` (obtain first Pokemon), `reach_viridian`, `reach_pewter`, `brock`
(obtain Boulder Badge). See `missions.py` -- every success/failure condition
reads only `GameState`, never an LLM's self-report.

## 13. Knowledge format

Plain markdown + YAML frontmatter under `stealth/{claims,procedures,failures,runs}/`.

```markdown
---
id: C-0001
scope: pokemon-red
status: observed
evidence: [R-0004]
tags: [brock, rock, water]
---

# Claim

Rock-type Pokemon encountered in Brock's gym are vulnerable to Water-type attacks.
```

Every item here is machine-written by `extraction.py` after a real STEALTH
run, never hand-seeded (STEP 34) -- there is no bootstrap content in this
repo. `knowledge.py`'s dedup (token-overlap >= 0.72) merges evidence onto an
existing near-duplicate instead of creating a new item.

## 14. Baselines: how isolation is enforced

- **fresh**: reads mission + current state + this run's own local history.
  Touches no persistent store.
- **raw_history**: also gets verbatim (unranked) excerpts of past run traces
  from `raw_history/runs/` -- its OWN directory, never `stealth/runs/`.
- **notes**: also gets a flat, unstructured per-mission notes file from
  `notes_baseline/` -- appended one deterministic line per run, no LLM
  extraction, no schema, no retrieval ranking. Exists specifically to prove
  Stealth beats "just give the agent notes."
- **stealth**: also gets ranked Claims/Procedures/Failures from
  `stealth/`, and is the ONLY condition that ever calls extraction.py.

This isolation is structural (see `test_experiment_isolation_offline.py`),
not a convention someone could accidentally violate: `run_one()` branches
on `Condition` and each branch's persistence code only touches its own
directory.

## 15. Test results (offline suite, no ROM/API key required)

```
cd pokemon_stealth
python -m pip install -e ".[dev]"
python -m pytest tests -q
```

Actual result from this environment: **67 passed, 4 skipped** (the 4 skips
are the ROM-dependent smoke tests in `test_emulator_actions_e2e.py`, which
correctly skip without `POKEMON_RED_ROM`).

To run the ROM-dependent tests locally:

```
POKEMON_RED_ROM=/path/to/pokemon_red.gb pytest tests/test_emulator_actions_e2e.py -q
```

**Independently verified in this environment** (not part of `pytest`, since
it deliberately avoids touching the copyrighted ROM): PyBoy 2.7.0 is
actually installed here, and `emulator.py`'s full wrapper (start, tick,
button press/release, `memory[addr]` reads, `save_state`/`load_state`,
cartridge-header title decode) was smoke-tested end-to-end against PyBoy's
own bundled `default_rom.gb` test cartridge -- confirming the PyBoy API
this harness targets is correct, without needing Pokemon Red itself:

```
frame_count after tick(30): 30
cartridge title: DEFAULT-ROM
frame_count after press: 38
byte at 0xC000: 0
savestate size bytes: 200445
load_state ok, frame_count still counts wrapper ticks: 88
ALL OK
```

## 16. Performance

Not yet measured against a real Pokemon Red ROM (none was available in this
environment -- see Limitations). Structurally: `emulator.py.tick()` runs
headless (`window="null"`) with no per-frame Python-side state extraction --
`extract_state()` is only called at macro-action boundaries, and each macro
action already holds/ticks 8-14+ frames per button press. LLM call frequency
is exactly one per macro action (never per-frame), bounded per-mission by
`Mission.max_llm_calls` (40-120 depending on mission).

## 17. First real results

**None yet.** STEP 32 explicitly forbids fabricating results, and this
harness could not execute a real mission run in this environment because
no ROM was available (STEP 2's legal requirement) -- even with API
credentials theoretically reachable, running Pokemon Red itself requires a
file this environment must not provide. The exact commands to produce real
first results, once you have a ROM:

```
pokemon-stealth checkpoint create S3_BEFORE_BROCK --demo   # manual, one-time
pokemon-stealth experiment --mission brock --conditions fresh,notes,stealth --runs 3 --model claude-sonnet-5
pokemon-stealth summary
```

## 18. Storage footprint

Measured in this environment (source, no venv):

```
src/pokemon_stealth/   ~13 files, ~55 KB of Python
tests/                 11 files, ~20 KB
```

Not measurable without a ROM: checkpoint savestate size (PyBoy's bundled
`default_rom.gb` savestate was 200,445 bytes -- Pokemon Red's will differ),
per-run knowledge-file growth, `results/runs.jsonl` growth rate.

## 19. Remaining limitations

- **No real Pokemon Red run has been executed** (no ROM in this
  environment) -- the entire pipeline is verified end-to-end against
  PyBoy's own test ROM and via mocked-LLM offline tests, but not against
  the real game.
- `battles_lost` is never incremented (left at 0, not guessed) -- a
  `battle_ended` event alone can't distinguish win/loss/flee from the state
  deltas currently read; would need an explicit "battle outcome" RAM field
  this harness didn't find a confident source for.
- Facing direction, dialogue-active detection, item names, and move names
  are unavailable (see Game State Support) -- callers get raw ids/None, not
  fabricated values.
- `save_game`'s success is always reported as unverifiable.
- Retrieval is a hand-rolled BM25 over a corpus that starts empty and grows
  only from real runs -- fine at hundreds of items, would need re-thinking
  well before thousands.
- Cross-model teacher/student (`teacher-student` command) shares one
  `stealth/` knowledge root between phases, which is correct for testing
  "does model B benefit from model A's knowledge" but means the two phases
  are not statistically independent samples of the same starting state --
  by design, since that IS the transfer being tested.
- `raw_history` and `notes` conditions were added because the spec
  explicitly requires proving Stealth beats "just notes"/"just raw
  history" (STEP 12) -- they are deliberately dumber than Stealth and
  aren't meant to be a good baseline design in their own right.

## 20. Next best experiment

Once a ROM is available: run the smallest real comparison first --
`pokemon-stealth experiment --mission brock --conditions fresh,stealth --runs 3 --model claude-haiku-4-5-20251001`
(haiku, not sonnet, to keep it cheap) and inspect `stealth/claims/` +
`stealth/procedures/` by hand after the stealth runs to confirm extraction
is producing genuinely durable, non-trivial knowledge (not restatements of
the mission) before investing in the full sequential 20-run learning-curve
experiment (STEP 21).
