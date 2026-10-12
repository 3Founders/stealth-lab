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
