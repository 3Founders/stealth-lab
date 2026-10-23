# Local computed status for .stealth: results (2026-10-23)

Lane SHIP, `packaging/npm/` only. Implements the local-first order agreed after reading
`docs/verification_changes_prop.md`: the same computed status (verified, open, failed, contested, expired), first on the
client, where the checks already run, before any server work.

## What shipped

- `stealthlab-mcp status [--root <repo>] [--stealth-dir <dir>] [--json] [--run] [--only <ids>] [--in-place]`
  (`packaging/npm/lib/status.mjs`, wired in `bin/stealthlab-mcp.mjs`).
- A new append-only `.stealth/evidence.md`, one line per check run:
  `EVIDENCE|<target id>|deterministic_check|pass|fail|at=<iso>|commit=<sha or ->|hashes=<path#sha=<hex>,...>|check=<cmd>`.
  Method names follow the server ladder (`backend/app/services/verification.py`), so a line maps one to one onto a
  `verification_results` row later. A line holds the command and its exit verdict: never output, prompt text or secrets
  (a check that looks like it holds a secret is not recorded; the validator's `hasSecret` is the test).
- `--stealth-dir` (an addition to the brief): reads plan, claims and evidence from another folder while `--root` stays the
  repository whose files are checked. It is what makes the demo below possible, because `.stealth/collab` describes this
  repository and its claims cite files that are not in a bare temp copy.

## How the status is computed

| status | rule |
|---|---|
| expired | something the target relies on changed after its evidence was recorded: a cited claim the survey validator would no longer keep (run on a copy; nothing is stamped or written) or that is already `stale`; a library entry marked stale; a file in a library entry's `touches=` whose blob hash differs; or the latest run of a check recorded against files whose hash differs now |
| failed | the latest evidence for its check, on the current code, failed |
| contested | a pass and a fail on the same code (same hashes) |
| verified | a check exists, its latest run on the current code passed, and every `deps=` node is itself verified |
| open | everything else: no check (`check=-`), no evidence, or a dependency that is not verified |

- A run.md node marked `done` by hand with no recorded run is **open**. The hand-written status is a claim, not evidence.
- Missing or contradictory evidence is never `verified`. Every read error fails open to `open` or `expired`, never to `verified`.
- "Current code" means the content hash of the cited files: a node's claim sources, a library entry's `touches`. The hash
  is the git blob sha (`git hash-object`), the kind library.md `touches=` already records; outside a git work tree it is
  computed the same way from the bytes. `commit=` is stored, and is used only when a line carries no hashes.
- A library entry added by dispatch or the Stop hook has `verified_at` and `touches` hashes. That counts as one pass,
  recorded at those hashes (outcome `pass`; a `fail` outcome counts as one fail; `historical` counts as none). library.md
  is not rewritten.
- Expiry reuses `lib/survey/validate.mjs` (`validateClaim`, `makeContext`) and `lib/library.mjs` (`currentShas`,
  `parseLibrary`); nothing was re-implemented. `--run` reuses `lib/exec/worktree.mjs`.
- Nothing runs in the prompt hook. Without `--run`, no process is started and no file is written.

## `--run`

Each check runs in a throwaway worktree (HEAD plus the uncommitted tracked diff), so a check cannot change the checkout;
`--in-place` runs it in the checkout instead. The exit code decides pass or fail; a timeout or a failure to start records
nothing and is reported. Exactly one evidence line is appended per check that ran.

## Demo 1: a toy repository (real output)

A git repo with `src/math.js`, one claim citing its line 1, and four nodes (`done` by hand, a dependent, a check that
fails on purpose, and `check=-`):

```
$ stealthlab-mcp status
N-1  node  open  marked done by hand; no recorded check run
N-2  node  open  no recorded check run
N-3  node  open  no recorded check run
N-4  node  open  no check (check=-)

$ stealthlab-mcp status --run                      # ran 3 checks
N-1  node  verified  its check passed on the current code
N-2  node  verified  its check passed on the current code
N-3  node  failed    the latest recorded run of its check failed on the current code
N-4  node  open      no check (check=-)

$ echo "// tail edited" >> src/math.js             # line 2; the claim cites line 1
N-1  node  expired   the latest run of "node check.mjs" was recorded against files that have changed
N-2  node  expired   the latest run of "grep -q add README.md" was recorded against files that have changed
N-3  node  expired   the latest run of "node -e "process.exit(1)"" was recorded against files that have changed
N-4  node  open      no check (check=-)

$ sed -i s/add/plus/ src/math.js                   # the cited line itself
N-1..N-3  expired    claim T-001 no longer holds (its cited line changed); the latest run ... recorded against files that have changed
N-4       expired    claim T-001 no longer holds (its cited line changed)
```

## Demo 2: this repository's `.stealth/collab` plan (real output)

`.stealth/collab/*.md` copied to a temp folder (so evidence is not written into the tracked folder), `--root` the repository:

```
$ stealthlab-mcp status --root . --stealth-dir <temp copy> --run --only N-1,N-5,N-8     # 3 checks, about 19 s
N-1   verified   its check passed on the current code
N-2   open       marked done by hand; no recorded check run
N-3   open       marked done by hand; no recorded check run
N-4   open       marked done by hand; no recorded check run
N-5   open       dependency not verified: N-2 is open, N-3 is open, N-4 is open
N-6   open       no recorded check run
N-7   open       no recorded check run
N-8   failed     the latest recorded run of its check failed on the current code
N-9   expired    claim O-015 no longer holds (its cited line changed)
N-10  open       no recorded check run
N-11  open       no recorded check run
N-12  open       no recorded check run
N-13  expired    claim O-015 no longer holds (its cited line changed)
N-14  open       no recorded check run
verified 1  ·  open 10  ·  failed 1  ·  contested 0  ·  expired 2
```

What it shows: five nodes the plan calls `done` are only `open` until their checks are run; N-8 (`grep -q "what leaves"
packaging/npm/README.md`) fails, which is true, since that node is `ready` and unbuilt; and N-9/N-13 are `expired`
because claim O-015 cites a line of `packaging/npm/bin/stealthlab-mcp.mjs`, which this very change edited. An expired case
produced by editing a file, found by the status and not by hand.

## Tests

- `packaging/npm/test/status.test.mjs`: 13 tests, 13 pass. They cover: `check=-` is open and a `done` node without evidence
  is open; a pass at the current hashes is verified and editing a cited file makes it expired; a claim the validator no
  longer keeps (and one already `stale`) expires the node; a fail is failed and a pass plus a fail at the same hashes is
  contested; a node with an unverified dependency stays open (and a cycle is never verified); a library entry is verified
  until a touched file changes, then expired, and one without recorded hashes is open; without `--run` no process starts and
  no file is written, and `--run` appends exactly one line per check; evidence never holds check output or a secret-looking
  value; lines round-trip with a `|` inside `check=`; missing files fail open.
- Full packaging suite (`node --test test/*.test.mjs`, from `packaging/npm/`): 634 tests, 630 pass, 1 fail, 3 skipped.
  The one failure, `hook.test.mjs` "triage: STEALTHLAB_HOOK_TRIAGE=off never asks; the question has its own short timeout",
  also fails on a clean `git archive` of HEAD `e71968a` (before this change), so it is not caused by it.

## Honest limits

- A check can depend on a file the target does not cite (in the toy, `grep -q add README.md` depends on README.md, but only
  `src/math.js` is hashed). Editing README.md would not expire that node. Cite what the check relies on, or add the file to
  a claim.
- The worktree holds HEAD plus the uncommitted tracked diff. Untracked files are not in it; a check that needs them fails
  there. Use `--in-place` for such a check.
- Checks run exactly as written in run.md or library.md (they are the repository's own commands). `--run` is a command the
  user starts; it never runs by itself.
- No human review, no composition beyond `deps=`, and nothing is sent to the server.

## Next (not built here)

1. Send `evidence.md` lines to the server as `verification_results` rows (`deterministic_check`, plus the reviewer as
   `independent_agent`), with `commit` and `hashes`, so `expired` works on the server too.
2. The proposal's `goal_claims` link and the server-side computed status, once evidence is flowing.
3. Human review as typed evidence, and composition across the Goal tree.
