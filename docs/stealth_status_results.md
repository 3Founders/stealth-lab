# Local computed status for .kell: results (2026-10-23)

Lane SHIP, `packaging/npm/` only. Implements the local-first order agreed after reading
`docs/verification_changes_prop.md`: the same computed status (verified, open, failed, contested, expired), first on the
client, where the checks already run, before any server work.

## What shipped

- `kell status [--root <repo>] [--stealth-dir <dir>] [--json] [--run] [--only <ids>] [--in-place]`
  (`packaging/npm/lib/status.mjs`, wired in `bin/kell.mjs`).
- A new append-only `.kell/evidence.md`, one line per check run:
  `EVIDENCE|<target id>|deterministic_check|pass|fail|at=<iso>|commit=<sha or ->|tree=<tree id or ->|hashes=<path#sha=<hex>,...>|where=<worktree|in-place>|check=<cmd>`
  (`tree=` and `where=` were added in the follow-up below; older lines without them still parse).
  Method names follow the server ladder (`backend/app/services/verification.py`), so a line maps one to one onto a
  `verification_results` row later. A line holds the command and its exit verdict: never output, prompt text or secrets
  (a check that looks like it holds a secret is not recorded; the validator's `hasSecret` is the test).
- `--stealth-dir` (an addition to the brief): reads plan, claims and evidence from another folder while `--root` stays the
  repository whose files are checked. It is what makes the demo below possible, because `.kell/collab` describes this
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
$ kell status
N-1  node  open  marked done by hand; no recorded check run
N-2  node  open  no recorded check run
N-3  node  open  no recorded check run
N-4  node  open  no check (check=-)

$ kell status --run                      # ran 3 checks
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

## Demo 2: this repository's `.kell/collab` plan (real output)

`.kell/collab/*.md` copied to a temp folder (so evidence is not written into the tracked folder), `--root` the repository:

```
$ kell status --root . --stealth-dir <temp copy> --run --only N-1,N-5,N-8     # 3 checks, about 19 s
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
because claim O-015 cites a line of `packaging/npm/bin/kell.mjs`, which this very change edited. An expired case
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

## Follow-up after f64dd66 (2026-10-23)

### Part 0: the "known failure" was this machine's setup, now fixed in the test

`hook.test.mjs` "triage: STEALTHLAB_HOOK_TRIAGE=off never asks" failed here and passes elsewhere. **Cause:** `lib/hook.mjs`
asks the triage question when `STEALTHLAB_HOOK_TRIAGE` is on **or when dispatch is installed**
(`dispatchInstalled(env)` reads `~/.stealthlab/config.json` for `dispatch: true`, which is true on this machine). The test
did not isolate that. Findings: no `ANTHROPIC_*` or `STEALTHLAB_*` variable changed it (the only `STEALTHLAB_*` variable in the
shell is the MCP token); a clean `env -i` run failed the same way; no server was involved (the test uses a fake fetch).
**Fix:** the test points `STEALTHLAB_HOME` at an empty folder, so it never reads the developer's own install. 21 of 21 pass.

### A1: code changed outside what a check cites is flagged, never an expiry

Each evidence line now carries `tree=<id of HEAD's tree plus the uncommitted tracked diff>`, computed in memory the way
`git write-tree` computes it (`workTreeId`), so reading the status writes no git object. A unit test compares it with git's
own `write-tree` for a modified, a new tracked and a deleted file. When the latest passing evidence's hashes all still match
but its `tree=` differs from now, the target stays `verified` with `changed_since: true`; the table says "verified (code
changed since the check ran)". Old lines without `tree=` parse and give `changed_since: null`, never true. No sixth state.

```
$ kell status --run --yes
N-1  node  verified  its check passed on the current code
$ echo more >> notes.txt        # a tracked file that neither the claim nor the check cites
$ kell status
N-1  node  verified  verified (code changed since the check ran): its check passed on the current code
```

### A2: `--run` shows the commands and asks first

`--run` prints the distinct commands it will run (they come from the repository's own files), then: in a terminal it asks
once, `Run these? [y/N]`, and a no runs nothing; with no terminal it runs only with `--yes`, otherwise it prints "pass --yes to
run these checks" and exits 3.

```
$ kell status --run          # stdin piped, no terminal, no --yes
--run will run 1 command(s) from the repository's own files, each in a throwaway worktree:
  $ node check.mjs
pass --yes to run these checks
exit code: 3
```

Honest note: this sample is the real non-terminal output. I could not capture an interactive terminal session in this
environment, so the `[y/N]` question itself is covered by a test with an injected prompt (`tty: true`, answers `n` and `y`),
not by a recorded session.

### A3: `--in-place` is said and recorded

The human output starts with "checks ran in your checkout, not a throwaway copy" and each evidence line carries
`where=worktree` or `where=in-place` (also when a non-git folder forces the fallback). Both count as `deterministic_check`.

```
EVIDENCE|N-1|deterministic_check|pass|at=...|commit=<sha>|tree=<tree>|hashes=src/math.js#sha=ad6187c7a7b5|where=worktree|check=node check.mjs
EVIDENCE|N-1|deterministic_check|pass|at=...|commit=<sha>|tree=<tree>|hashes=src/math.js#sha=ad6187c7a7b5|where=in-place|check=node check.mjs
```

### A4: `status --compact`

Rewrites `evidence.md`, keeping for each target and check only the latest pass, the latest fail and every line recorded on the
tree as it is now. (Per check, not per target: a library entry with two checks would otherwise lose one check's pass and
change status.) It refuses when `evidence.md` has uncommitted changes in git, and keeps the old file as `evidence.md.prev`.
Note: `.kell/` is usually git-ignored, in which case git reports nothing and the refusal cannot trigger; the `.prev`
copy is the safety net there.

### Tests and counts

- `test/status.test.mjs`: 20 tests, 20 pass (13 from before; 7 new: tree id equals git's, A1 twice, A2 twice, A3, A4).
- Full packaging suite (`node --test test/*.test.mjs` from `packaging/npm/`): **641 tests, 638 pass, 0 fail, 3 skipped** with
  `--test-concurrency=2`. With the default concurrency on this machine two timing tests failed in two separate runs, a
  different one each time ("dispatch: the capped run is cancelled at its model's cap" asserts it took under 3 s, and "hang
  (spec 8.4)" asserts a 5 s window); each passes alone, three runs out of three. They are load-sensitive, not related to this
  change; I did not change them.
- The earlier line in this note about a "known failure" is superseded by Part 0.

### What is still not covered

- The check-depends-on-an-uncited-file limit is now visible (the `changed_since` flag) but not closed: the status does not
  expire on it, by design.
- Untracked files are not in the throwaway worktree, nor in `tree=`.
- Nothing is uploaded: see `docs/evidence_upload_design.md`.

## Parts 3 to 5: evidence on the server, the upload, and a Goal's computed status (2026-10-23)

**What ran against what.** Everything below ran against a **fresh local scratch Postgres 17** (a new cluster on 127.0.0.1:55432, database
`kel_evidence_test`, the whole migration chain 01 to 153 applied cleanly) and, for the client demo, the real MCP server (`run_backend.py`,
which pins every database variable to a loopback host) on 127.0.0.1:8777. **No hosted database was touched**; the migration was never
applied anywhere else. The backend `.env` database is over its plan quota, which is why the scratch cluster was used.

### Part 3: the server table, the service and the tool (migration 152)

Real output of the tool function called against the scratch database (the payload is what the client builds from an EVIDENCE line):

```
1) one evidence line uploaded (the payload the client builds from a real EVIDENCE line):
   in : {"repo_ref": "r:0123456789abcdef", "target_kind": "plan_node", "target_id": "N-3", "method": "deterministic_check", "result": "pass", "observed_at": "2026-10-23T10:00:00.000Z", "check_hash": "6b9d3d4a ...
   out: {"recorded": true, "duplicate": false, "id": "01a16938-144c-76e8-8245-9d318cec216c", "state": "checked"}
   again (the same client_event_id): {"recorded": false, "duplicate": true, "id": "01a16938-144c-76e8-8245-9d318cec216c", "state": "checked"}
   row: [{'target_id': 'N-3', 'method': 'deterministic_check', 'result': 'pass', 'state': 'checked', 'provenance': 'company_ingested', 'scope_type': 'repository', 'scope_entity_id': 'r:0123456789abcdef', 'owner_id': 'demo-user', 'check_hash': '6b9d3d4ad364', ' ...
2) a reviewer that is the same model as the executor:
   out: REFUSED: the reviewer is the same model as the executor: independent_agent evidence needs a different model
3) a field outside the whitelist:
   out: REFUSED: fields not allowed: ['command'] (allowed: ['repo_ref', 'target_kind', 'target_id', 'goal_id', 'procedure_id', 'method', 'result', 'observed_at', 'commit_sha', 'tree_id', 'hashes', 'where_ran', 'check_hash', 'client_event_id', 'client_version', ...
   rows now: 1
```

The demo connection is the scratch cluster's owner (a superuser, which bypasses row-level security). The isolation proofs use a plain role:
`backend/tests/test_local_check_evidence_e2e.py` (8 tests: states, idempotence, append-only, the engine's CHECKs, RLS across accounts and
tenants, two-account corroboration, goal link).

### Part 4: the client upload (off by default)

Real output, `STEALTHLAB_EVIDENCE_UPLOAD=on`, the packaged CLI against the local server (a throwaway git repository, one plan node):

```
$ STEALTHLAB_EVIDENCE_UPLOAD=on kell status --run --yes --json     # client -> local MCP server -> scratch Postgres
{"targets": [{"id": "N-1", "status": "verified", "why": "its check passed on the current code"}], "run": {"appended": 1, "upload": {"built": 1, "queued": 0, "sent": 1, "skipped": 0}}}
outbox files after the send: 0
row on the server:
plan_node N-1 | deterministic_check | pass | state=checked | company_ingested | repository=r:fedcba9876543210 | owner=abtest-user | where=worktree | hashes=1
--- the local file keeps the command; the server never sees it:
1
0
--- the key file exists, is ignored, and is not in the local evidence or the server row:
present
0
0
--- the same line offline (server stopped is not needed: unreachable URL):
upload: {'built': 1, 'queued': 1, 'sent': 0, 'skipped': 0} | status: verified
outbox files: 1
--- later, back online, the next flush sends it:
{"sent":1,"remaining":0,"rejected":0}
outbox files: 0
rows on the server: 2
```

Honest limits: the client demo used a local test identity (the issuer `run_backend.py` trusts), not a hosted sign-in. `status --run` sends
with a short bound and queues what it cannot send; dispatch only queues. Two accounts only corroborate each other when they share the
evidence key, which is the team's choice (Part 1).

### Part 5: a Goal's contract as linked claims, and its computed status (migration 153)

`goal_claims(goal_id, claim_id, role)` links a Goal to claims in `knowledge_nodes` (`node_type = 'claim'`, enforced by a trigger that ships
with the writer `link_goal_claim`). `role` is `assumption` (must hold before) or `guarantee` (must hold after). A link is ended, never edited
or deleted. Nothing stores a status: the view `goal_status` computes **verified | open | failed | contested | expired** from the evidence
already recorded on the guarantee claims, and `goals.status` keeps its own meaning (the record's lifecycle). The rules, in the order they win:

- **expired**: a claim in the contract (either role) was closed (superseded, stale, retracted or `t_invalid`) at or after the Goal's first evidence.
  A link to a claim closed before any evidence is a dead link, not an expiry.
- **failed / contested**: a guarantee has failing evidence (contested when it also has passing evidence).
- **verified**: every live guarantee has passing evidence and none fails. Only `execution_result` and `reproduction` evidence counts; testimony
  (observation, document, human review, a self-report) never does. Independent groups are counted once each, as for procedures.
- **open**: anything else, including an empty contract.

It is read-only on the goal read path: the MCP resource `stealth://goals/<id>/claims` now starts with one `GOAL_STATUS|...` line, then the
contract's claims (topic = role), then related claims. `kell status --server` asks for that resource once per Goal and adds it
to each row (`server` in `--json`, `[server: ...]` in the table); the local status is never changed by it, and an unreachable server is
not an error.

Real output (scratch database only; the resource function called against it, as the account `user-a`):

```
link guarantee: {'linked': True, 'duplicate': False, 'id': '01a16964-3dbe-78e8-876c-56d9f4a2e9e5', 'goal_id': '3a4af628-c703-4aab-8bd8-7331995c18e4', 'claim_id': 'd16d9f72-9841-48d8-be64-9879e0c195b0', 'role': 'guarantee'}
link assumption: {'linked': True, 'duplicate': False, 'id': '01a16964-3dcc-7f9e-8da1-2e3a9bb94df5', 'goal_id': '3a4af628-c703-4aab-8bd8-7331995c18e4', 'claim_id': '9acdf9f1-9e02-47c5-891b-c58c4427a6d2', 'role': 'assumption'}

--- no evidence yet ---
GOAL_STATUS|3a4af628-c703-4aab-8bd8-7331995c18e4|open|guarantees=1|verified=0|failed=0|contested=0|open=1|assumptions=1|closed=0|client_checks=-|why=0 of 1 guarantee claim(s) verified; the rest have no machine-checked evidence yet
CLAIM|d16d9f72-9841-48d8-be64-9879e0c195b0|supported|guarantee|global|signing keys rotate every 90 days|version=1
CLAIM|9acdf9f1-9e02-47c5-891b-c58c4427a6d2|supported|assumption|global|the vault is reachable|version=1

--- after one passing execution_result on the guarantee claim ---
GOAL_STATUS|3a4af628-c703-4aab-8bd8-7331995c18e4|verified|guarantees=1|verified=1|failed=0|contested=0|open=0|assumptions=1|closed=0|client_checks=-|why=all 1 guarantee claim(s) have passing evidence and none fails
CLAIM|d16d9f72-9841-48d8-be64-9879e0c195b0|candidate|guarantee|global|signing keys rotate every 90 days|version=1
CLAIM|9acdf9f1-9e02-47c5-891b-c58c4427a6d2|supported|assumption|global|the vault is reachable|version=1

--- after the guarantee claim is superseded ---
GOAL_STATUS|3a4af628-c703-4aab-8bd8-7331995c18e4|expired|guarantees=0|verified=0|failed=0|contested=0|open=0|assumptions=1|closed=1|client_checks=-|why=a claim in this Goal's contract was superseded after the evidence was recorded
CLAIM|d16d9f72-9841-48d8-be64-9879e0c195b0|superseded|guarantee|global|signing keys rotate every 90 days|version=1
CLAIM|9acdf9f1-9e02-47c5-891b-c58c4427a6d2|supported|assumption|global|the vault is reachable|version=1
```

The same Goal, the same evidence row, a different answer once the claim it was about no longer holds as written.

Tests: backend `test_goal_claims_offline.py` (11), `test_goal_claims_e2e.py` (10, scratch Postgres, a plain role under RLS: gate and writer,
no stored status, open with no evidence, a self-report never verifies, failed and contested, groups counted once, verified then expired for a
guarantee and for an assumption, a dead link, retracted evidence stops counting, owner isolation); packaging `status_server.test.mjs` (6).

**Not built (the proposal's section 6 minus this):** composition across the Goal tree (a parent verified from its children) and human review as
typed evidence. The corroboration rule of Part 3 still needs a shared evidence key between accounts. Part 3 and 5 migrations were applied to
the scratch database only.
