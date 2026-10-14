# Communications: problems blocking the phase 2 arm C resume

Written 2026-10-12 after trying to follow the resume prompt (handoff section 8) on the
`chait` machine. Nothing was run. This lists what stopped it, what is risky in the plan,
and what each owner needs to decide. It records facts only; no code or config was changed.

## 1. Blockers (the run cannot start as specified)

| # | Problem | Evidence | Who can fix it |
|---|---|---|---|
| 1 | **This is not the original machine.** There is no `experiments/abtest/.local/`, so no `phase2.jsonl` (75 A rows, 7 C rows), no `ws/C` workspaces, no `C_home`. | `Test-Path experiments\abtest\.local` is False. | Owner of the original machine |
| 2 | **The resume bundle is not here.** `stealth-lab-resume-bundle-2026-10-11\` (`kel_abtest.dump`, `abtest_local_state.tar`) was never copied. It is not in git on purpose. | Path not found on this machine. | Owner of the original machine: copy it over a secure channel |
| 3 | **No `kel_abtest` database**, so `routing_observations` cannot be greater than 0. Arm C's routing memory (phase 1 plus the first 7 phase 2 tasks) does not exist here. | No scratch Postgres on 127.0.0.1:55432. | Restore from the bundle |
| 4 | **A plain `git pull` is refused.** Incoming commits touch `backend/app/mcp_server/server.py` and `packaging/npm/lib/clients.mjs`, which other sessions have uncommitted edits in. The local `main` is 38 commits behind `origin/main`. | `git diff --name-only HEAD origin/main` overlaps the dirty files. | The sessions that own those edits: commit or shelve them |
| 5 | **The working tree is not clean.** 20 modified files plus many untracked ones belong to other sessions. Running the benchmark from it would mix their changes into the product under test. | `git status`. | Run from a clean worktree at `origin/main`, not this tree |

## 2. Version drift (section 8.1 says match exactly, or write down what differs)

| Tool | Contract | This machine |
|---|---|---|
| Claude Code | 2.1.292 | 2.1.296 |
| Node | v24.18.1 | v24.16.0 |
| Python | 3.14.6 | 3.13.0 |
| PostgreSQL | 18.4 | 17 |
| OS | Windows 10 | Windows 11 |

Python packages (pytest, fastapi, schemathesis, playwright and the rest) were not checked
because the machine already failed the checks above.

Two of these matter beyond "drift":
- **PostgreSQL 17 cannot restore a dump made by 18.4.** `pg_restore` must be the same
  major version as the dump or newer. The bundle's `kel_abtest.dump` would likely fail here.
- **Python 3.13 vs 3.14** can change how the harness and graders behave; nobody has shown
  that it does not.

## 3. Problems in the handoff itself

1. **Section 8.3's restore command is destructive.** `git checkout 700cdb4 -- .` overwrites
   every tracked file in the tree. On any machine with uncommitted work it would erase it
   (this tree has 20 such files). It should check out into a separate worktree instead.
2. **Paths are machine-specific.** Section 7 uses `cd /c/Users/user/stealth-lab`; the
   workspaces' hook commands hold absolute paths. Section 8.3 handles the second with
   `setup_c.mjs`, but not the first.
3. **The bundle contains a private key** (`signing_key.pem`) and a token. It must not be
   attached to email or chat; it needs a secure transfer and deletion afterwards.
4. **The "nothing changed since 700cdb4" check is passed only for `origin/main`.** The
   check must be run against the exact tree the run will use. Here that would fail.
5. **General Compute capacity may not fit one day.** Phase 1 arm C used about 10M tokens
   per day-key; the handoff asks for at least 5M left, and the free tier is 10M per key
   per day. 68 tasks may not finish in one day. The pause-and-resume path (exit code 4)
   is the expected route, which means the run will span several days and the 00:00 UTC
   resets. That is acceptable only if it is stated in the results.
6. **Claude weekly usage** was 76% at handoff (Pro plan; resets 2026-10-13 20:00 UTC) and
   the stated budget rule stops at 80%. 68 more arm C tasks, each with Opus fallbacks and
   Sonnet test-writing, may hit that before the run finishes. Exit code 3 handles it, but
   the owner should know the run may stall until the reset.
7. **Latency is not comparable across machines.** Arm A ran on the original machine. If
   arm C finishes elsewhere, latency comparisons for phase 2 are invalid; only cost and
   solved remain comparable. The handoff says this, but the results table would still
   show a latency column unless it is dropped.
8. **Two unpaired phase 1 rows** remain (`bird-1505` and one other that `report.py`
   shows). The handoff lists only `bird-1505` for re-run.

## 4. Decisions needed

1. **Where does arm C run?** Recommended: the original machine (section 8.2). It has the
   state, the versions and the same latency conditions. The alternative is restoring here
   with PostgreSQL 18.4, Python 3.14.6, Node 24.18.1 and Claude Code 2.1.292 installed,
   from a clean worktree.
2. **If it runs elsewhere:** accept the version differences in writing, or reinstall the
   exact versions first. Do not change versions silently.
3. **Who commits or shelves** the other sessions' uncommitted edits so the main tree can
   be brought up to date.
4. **Is the second unpaired phase 1 task** also to be re-run?

## 5. What does not depend on any of this

- The deferral letter uses only finished phase 1 numbers (114/128 vs 120/128, 38% lower
  cost) and states that phase 2 is paused. It stays correct until phase 2 finishes.
- Phase 2 numbers should not be quoted in any outward-facing document until arm C has
  all 75 rows under the section 8 conditions.


## 6. Response (from the original machine's session, 2026-10-12 11:20 UTC)

Thank you; every point is valid.
- **Decision 1:** arm C runs on the original machine. The MultiPL-E JS part already ran there today, with the
  identical command and state (19/20 like A, -23%). The 50 page tasks run there after the weekly reset
  (2026-10-13 20:00 UTC).
- **Decision 2:** not needed if the run stays on the original machine.
- **Decision 3:** the other sessions' edits are not touched by this run.
- **Decision 4:** yes, and there are four unpaired tasks, not two. See handoff section 10.
- **Handoff section 3.1 (destructive checkout):** fixed. Section 8.3 now uses a separate worktree.
- **Section 3.4:** the code check must run in the exact tree used. Section 8.3 now runs it inside the new worktree.


---

# Cursor test 2026-10-14

**What this is, honestly:** the Cursor hooks were driven by an installed copy with Cursor-shaped payloads (documented fields:
`conversation_id`, `generation_id`, `model`, `workspace_roots`, `prompt`), against a real local backend and the real General Compute
models. **No real Cursor was used**: I cannot type into Cursor's chat, `cursor-agent` is not installed here, and the install went to a
sandbox home (`USERPROFILE`/`HOME` pointed at a temp folder), not to the person's own `~/.cursor`. So everything below proves the hook
path and the dispatch, and nothing about Cursor itself (whether it runs hooks on the free plan, what its real payloads carry, how a
blocked prompt looks in the chat). Those still need a person in Cursor; the steps are at the end.

## Setup (what was run)

- `git pull`: local main fast-forwarded to `fb40570`, then to `11830f7` (the Cursor real-flow fix) mid-test; steps 1-3 ran on `fb40570`,
  the re-checks on `11830f7`.
- Postgres: this machine has PostgreSQL 17 (not 18) with pgvector 0.8.0. Scratch cluster `C:\Users\chait\pg-scratch` on 127.0.0.1:55432
  (user `kel_owner`, trust), database `kel_abtest`, migrations 01-150 applied with `scripts/migrate.py` (no errors).
- Backend: `python experiments/abtest/run_backend.py` (127.0.0.1:8765, MCP at /mcp, the local test identity's token).
- Install: `stealthlab-mcp install --client cursor --with-exec --url http://127.0.0.1:8765/mcp --token <local test token>` into the sandbox
  home: wrote `.cursor/mcp.json`, `.cursor/hooks.json` (five hooks; `beforeSubmitPrompt` with `timeout: 600`) and `.stealthlab/config.json`
  (`dispatch: true`). `experiments/abtest/exec.json` copied as `exec.json`: stealth executor with gemma-4-31b-it, gpt-oss-120b,
  minimax-m2-7 on General Compute, key by variable name (`GENERAL_COMPUTE_API_KEY`, value read from `backend/.env` into the child
  process only, never printed or written).
- Env for the hooks: `STEALTHLAB_CURSOR_MODEL=claude-4.5-sonnet` (stand-in for the model picked in Cursor; "auto" is not a model),
  `STEALTHLAB_DISPATCH_CHECK="python -m pytest -q"`.
- Test repo: `calc.py` (`add` returns `a - b`), `tests/test_calc.py` (add, sub, mul), `conftest.py` (sys.path). Two copies were used:
  `repo` (first run; I added `.stealth/` by hand, see step 0) and `repo2` (fresh, no `.stealth/`, used for the re-check on `11830f7`).

## Results

| # | Prompt / action | Expected | Result | Time |
|---|---|---|---|---|
| 0 | first prompt in a fresh repo with **no** `.stealth/` (code at `fb40570`) | dispatch from the first prompt | **Not dispatched**: hook answered `{"continue":true}` at once, nothing logged. Cause: `cursor_dispatch.mjs` returned null when `findStealthRoot` found no `.stealth/`; the Claude Code hook created it, the Cursor one did not. **Fixed upstream in `11830f7` while I was testing** (`ensureDispatchRoot`). I changed no product code | 0.1 s |
| 0b | same, on `11830f7`, fresh `repo2` | dispatch | **Pass**: stopped with "Done by gpt-oss-120b in 45 s ... $0.0002 ... applied: calc.py ... stealthlab-mcp undo", plus a three-sentence explanation. `calc.py` now `a + b`, tests 3 passed, `.stealth/` created | 45.8 s |
| 1 | "add is broken, fix it pls" (on `fb40570`, `.stealth/` present) | stopped with "Done by <cheap model> in N s", explanation, fix in tree | **Pass**: "Done by minimax-m2-7 in 51 s ... $0.0011", explanation present, `add` fixed, tests 3 passed, one file changed | 53.1 s |
| 2 | "now also make it reject strings - raise a TypeError" | "it" = add(), fixed the same way | **Partly**: dispatched, stopped with "Done by gemma-4-31b-it in 53 s ... $0.0016" (first run) and "minimax-m2-7 in 53 s ... $0.0020" (re-check on `11830f7`); "it" was understood as the calculator's functions, **but the change put the TypeError check in `add`, `sub` and `mul`**, not only `add`. The first run also rewrote `tests/test_calc.py` (added `raises` blocks for all three). Tests passed. Scope is wider than the request | 56.6 s / 53.7 s |
| 3 | `stealthlab-mcp undo` in the repo | only the last change undone | **Pass**: "undone u-...: restored calc.py, tests/test_calc.py"; `add` kept its step-1 fix (`a + b`), the string checks and the test edits were gone | instant |
| 4 | "!opus explain what calc.py does" | no dispatch, Cursor's model answers | **Pass**: `{"continue":true}`, no dispatch. Cursor cannot rewrite the prompt, so the agent will see "!opus" as plain text (documented limit) | 0.1-0.4 s |
| 5 | "thanks" | no dispatch | **Pass**: `{"continue":true}` | 0.1 s |
| 6a | a large task (turn calc into a `mathkit` package with parser, evaluator, history, CLI, REPL, 25+ tests, README) | cheap models fail, Cursor's model does it, "tried" note | `{"continue":true}` in 5 s; hooks.log: `dispatch: not dispatched (size=large)`. So the brief gate refused it and **no cheap model tried**, so there is no "tried" note | 5.0 s |
| 6b | the same plumbing with a check no change can pass (`STEALTHLAB_DISPATCH_CHECK="python -c ... sys.exit(1)"`), prompt "add is broken, fix it pls" | cheap models fail, prompt goes on, "tried" note at the first tool call | **Pass**: `{"continue":true}` after 43 s; the next `postToolUse` (first tool call) returned `additional_context`: "StealthLab first tried this on gemma-4-31b-it (failed), minimax-m2-7 (failed), gpt-oss-120b (failed); none passed the check and nothing was applied. Do the task yourself" | 43.2 s + 0.2 s |
| 7 | do the hooks run on the free plan? | | **Not tested** (no Cursor GUI) | |

Sandbox hooks (installed copy, no server work): `cursor-session` answered with the context note in 118 ms; `cursor-prompt` for a plain
non-dispatch prompt answers `{"continue":true}` in about 0.1-0.2 s; `cursor-tool` and `cursor-stop` answered `{}` in about 0.1 s. A
first attempt with hand-quoted JSON gave "malformed payload" in hooks.log for all five hooks and every one failed open, as documented.

## Lines from hooks.log (sandbox `~/.stealthlab/hooks.log`)

```
cursor-dispatch test-first: Not logged in · Please run /login (attempt 1)
cursor-dispatch test-first: Not logged in · Please run /login (attempt 2)
cursor-dispatch dispatch: handled by minimax-m2-7|stealth
cursor-dispatch dispatch: handled by gemma-4-31b-it|stealth
cursor-dispatch dispatch: handled by gpt-oss-120b|stealth
cursor-dispatch dispatch: not dispatched (size=large)
cursor-lookup no knowledge added (fetch failed)        <- only when the backend was not running
```

## Findings and what to look at

1. **Fixed upstream already:** a fresh repo did not dispatch on its first prompt in Cursor (step 0). `11830f7` fixes it; re-check passed.
2. **Scope creep on step 2:** "make it reject strings" changed three functions, and once edited the tests too. The brief took "it" as the
   whole calculator. Worth a look at how the brief resolves "it" when the previous task touched one function.
3. **Test-first writer unavailable here:** every dispatch logs `test-first: Not logged in · Please run /login`. That is the sandbox: the headless Claude
   login lives in the real home, which I replaced. It is not a product bug, but it shows a Cursor-only user (no Claude Code login) gets no test-first
   step, and dispatch carries on without it. Say so for a customer who uses Cursor only.
4. **"tried" note not exercised by a real failure on a large task:** large tasks are not dispatched (6a); the note appears only when cheap models actually
   fail the check (6b, forced). For the owner's step 6, pick a task that is not "large" but that the cheap models genuinely fail.
5. **Latency:** every dispatch took 45-57 s before Cursor's chat would answer; the hook's `timeout` is 600 s. A blocked prompt for about a minute is
   the user-visible cost of dispatch in Cursor.

## What is NOT done and needs a person in Cursor (the owner's steps)

- Install into the real Cursor: `stealthlab-mcp install --client cursor --with-exec --url http://127.0.0.1:8765/mcp --token <token>`; copy
  `experiments/abtest/exec.json` to `~/.stealthlab/exec.json`; the General Compute key must be set as the user environment variable named in that file
  (`GENERAL_COMPUTE_API_KEY`) so Cursor's hook process inherits it; set `STEALTHLAB_CURSOR_MODEL` to the model picked in Cursor (not "auto"); restart Cursor.
  Note the install is **global**: hooks run in every Cursor workspace until `stealthlab-mcp uninstall --client cursor`.
- Send the six prompts in one chat and watch `~/.stealthlab/hooks.log`. Check: do hooks run at all on the free plan, does a blocked prompt show the
  message, what `model` Cursor really sends, and whether the first-tool-call context reaches the agent.
- Backend for that run: `python experiments/abtest/run_backend.py` with the scratch Postgres on 55432 (`pg_ctl -D C:/Users/chait/pg-scratch/data ... start`).

## Cleanup

Left running or on disk: scratch Postgres (`C:\Users\chait\pg-scratch`), sandbox home and the two test repos under the job's tmp folder. Nothing was written to
the real `~/.cursor` or `~/.stealthlab`, and no key was printed or stored.
