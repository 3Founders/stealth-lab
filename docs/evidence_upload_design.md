# Design: local check evidence becomes server evidence

Status: design and board request, 2026-10-23. No backend code and no migration here. Follows
`docs/stealth_status_results.md` (the local computed status) and `docs/verification_changes_prop.md` (the server-side
status this feeds). Spec v4 and `schema.md` stay frozen; the request to CORE-A is on the board.

## 0. Why

`stealthlab-mcp status --run` appends `EVIDENCE` lines to `.kell/evidence.md`. Today they stay on the machine, so the
proposal's server-side status (goal_claims, computed Goal status) would read an empty table. The server already has a
ladder (`backend/db/55_verification_results.sql`, `backend/app/services/verification.py`), but it is keyed to an execution
run, and a local check has none.

What the ladder gives and what it assumes, read from the code:

| fact | where |
|---|---|
| one current row per `(execution_run_id, criterion_id)`, a re-evaluation UPDATEs it (`ON CONFLICT ... DO UPDATE`) | `verification.py::_upsert_result` |
| `execution_run_id` is `NOT NULL` and references `execution_runs(id)` | migration 55 |
| callers never pass a state; each `record_*` computes it (`deterministic_check` pass is `verified`, `independent_agent` pass is `independently_verified`, `self_report` can only reach `claimed_done`) | `verification.py` |
| a result that the host merely reports is not Stealth-observed: a provider-reported node stays `unverified` | `execution/durable_run.py` (B26) |

## 1. The mapping, for both options

The local line:
`EVIDENCE|<target>|<method>|<result>|at=|commit=|tree=|hashes=|where=|check=<command>`.

| evidence field | (a) extend `verification_results` | (b) sibling table `local_check_evidence` |
|---|---|---|
| target (`N-3`, `L-ab12cd`) | new `target_kind` + `target_id` columns, `criterion_id` = a derived string | `target_kind` (`library_entry` or `plan_node`), `target_id`; plus nullable `goal_id`, `procedure_id`, `claim_ids` when known |
| method | `method` (CHECK already allows `deterministic_check`, `independent_agent`) | `method`, same two values to start |
| result (`pass`, `fail`) | not stored; the server computes `state` | `result` stored as reported; `state` computed by the server beside it |
| at | new `observed_at` (`created_at` is the server time) | `observed_at` (client time) and `received_at` (server time) |
| commit | new `commit_sha` | `commit_sha` |
| tree | new `tree_id` | `tree_id` |
| hashes (`path#sha=`) | new `hashes` JSONB | `hashes` JSONB, each item `{path_hmac, sha}` (see 3) |
| where (`worktree`, `in-place`) | new `where_ran` | `where_ran` |
| check (the command) | never stored (see 3) | never stored; `check_hash` only |
| none locally | `execution_run_id` made nullable; tenant, scope, provenance columns added | `client_event_id` (idempotency), tenant, scope, provenance, `client_version` |

### Pick: (b), a sibling table

Reasons, from the code:
1. **Append-only against upsert.** Local evidence is a history of runs: the status needs the latest pass, the latest fail,
   and a pass and a fail on the same code (`contested`). Migration 55 deliberately keeps "one current result per
   (run, criterion)" and an UPDATE on re-evaluation. Making that table append-only would change what every existing reader
   (`evaluate_run_completion`, `verify_completion`) assumes.
2. **No nullable foreign key.** Option (a) makes `execution_run_id` nullable, which weakens a constraint the run-scoped
   readers rely on, and its tenant isolation comes through that foreign key. A sibling table carries its own
   tenant and scope columns.
3. **Migration 55's rule survives intact.** The ladder's invariant is that callers never pass a state. A new
   `record_local_check` function in `verification.py` would take `method` and `result`, never `state`, and compute the
   state itself, in the same module as the other `record_*` functions, so there is still one place that decides.
4. **Later linking is easy.** `goal_claims` (the proposal) needs "evidence on a claim"; a table keyed by target and claim
   ids is the natural source, without touching run-scoped rows.

Cost of (b): two places hold ladder results. Mitigation: a view `verification_evidence_all` over both, and one state
function shared by both.

### State the server computes (open question for CORE-A)

The client is an untrusted host. A client-reported pass should not reach the same rung as a check Stealth ran itself
(compare `durable_run.py`: provider-reported nodes stay `unverified`). Proposal: a client `deterministic_check` pass is
stored as `checked`; it counts toward `verified` only when corroborated, by at least two independent groups (the existing
`independence_group` idea in `procedure_evidence_stats`), for example two machines or two organizations. A client
`independent_agent` pass (the reviewer, section 4) is stored as `checked` for the same reason, and its method is kept so a
later policy can weigh it higher. A fail is stored as `failed_verification`. Nothing here lets a caller choose a state.

## 2. What a row attaches to

- **A library entry.** `library.md` GOAL has `g=<global goal id or ->` and PROC has `p=<global procedure id or ->`; the
  routing lines (`routing.md`) know the goal for a route. When one is present, the row carries `goal_id` and `procedure_id`
  (uuids the server already knows). When none is, the row attaches to `target_id` (the local `L-` id plus a hashed repo
  identity, section 3) only, and is not visible to Goal-level readers. This is the common case today: most entries have `g=-`.
- **A run.md node.** It has no server object: `step=P-n:k` is a local planner reference and `claims=` are local ids. So a
  node row attaches only to `(repo identity, target_id)`. Later, when `goal_claims` exists, a node would link through its
  claim ids: the local claim id maps to a server claim when the survey's claims are uploaded, and `goal_claims` ties that
  claim to a Goal with a role. Until then node evidence is stored but unlinked, and says so.

## 3. The upload path, and what leaves the machine

**Mechanism.** A new MCP tool, `report_check_evidence`, not an extension of `report_result`. `report_result` belongs to a
model plan (`instance_key`, a recommendation to condition on); evidence has different fields and no "next model" reply.
It goes through the same executor-layer outbox as `report_model_run` and `report_result` (`lib/exec/evidence.mjs`):
whitelisted fields (a `CHECK_EVIDENCE_FIELDS` list next to `REPORT_FIELDS`), every string through `redact()`, offline or
refused-for-now reports written to `~/.stealthlab/outbox/` and retried, permanent refusals to `outbox/rejected/`, and a
payload the server could never accept (no target) to `outbox/held/`. The outbox entry stores the tool name, so the flush
replays `report_check_evidence` without new plumbing.

**Sent (and only this):**

| field | form |
|---|---|
| target | `target_kind`, `target_id` |
| goal_id, procedure_id | server uuids, only when `library.md` already holds them |
| method, result | the two enums |
| observed_at, commit_sha, tree_id | timestamps and git object ids |
| hashes | `{path_hmac, sha}` pairs: the path is replaced by an HMAC, the blob sha is sent as is |
| where_ran | `worktree` or `in-place` |
| check_hash | HMAC of the normalized command text |
| client_event_id | a uuid written into the evidence line when it is appended |
| client_version | the package version |

**Never sent:** the check command text, its output, file paths in the clear, prompt text, diffs, claim statements, the
reviewer's review text, repository names or URLs. The command can hold paths, names and URLs, which is why only its hash
leaves. The HMAC key is a random 32-byte secret that never leaves the machine (`packaging/npm/lib/evidence_key.mjs`): it is created on
first use in `.kell/evidence.key`, mode 0600 where the operating system has mode bits, listed in `.kell/.gitignore`,
and never uploaded, printed or written into `evidence.md`. It is NOT derived from the repository identity: that hash is
already sent to the server, and for a public repository anyone can compute it, so a key built from it would let the server,
or anyone who learns it, test guesses such as `npm test` against the hashes.

**What the server can and cannot learn.** Without the key it cannot recover a command or a path from its HMAC, and it cannot
test a guess. It can match the same check, and the same file, only within the scope of one key, so two machines with their own
keys look unrelated to it. Blob SHAs are sent as they are: for a file that is public, the SHA identifies exactly which version
of that file it was, and anyone holding the public file can compute it. A team that wants the server to match the same check
across its machines can choose to share the key (copy one `evidence.key` to each machine's `.kell/`, or point
`STEALTHLAB_EVIDENCE_KEY_FILE` at one shared file); sharing it is a choice, off by default, and whoever holds the key can test
guesses against the hashes, so it should stay inside the group that may already see the commands and paths. Path HMACs serve
one purpose: the server can tell that two runs saw the same file, without learning its name. This is the point a security reviewer will check
("what leaves your machine"), and it should be stated beside the hook's existing data-path statement in the README
(see the open node N-8 on the collab plan).

**Opt-in and off-switch.** Upload is off unless the workspace policy allows it (the per-organization switch of N-8), and
`STEALTHLAB_EVIDENCE_UPLOAD=off` turns it off per machine. The local status works either way.

**Idempotency.** The outbox retries, so a line can be sent twice. `client_event_id` makes the insert idempotent (unique
index); a second send changes nothing.

## 4. The reviewer

Dispatch's reviewer judges an accepted answer ("fixes", "does_not_fix", "unsure" in `hooks.log`) and the accepted answer
creates the library entry. Today that verdict is logged and not recorded as evidence. The client change (SHIP lane, not
part of this item) is: when dispatch writes the library entry, it also appends two evidence lines for that `L-` id, at the
entry's `touches` hashes: the reproduction test as `deterministic_check`, and the reviewer as `independent_agent`
(`pass` only for "fixes"; "unsure" records nothing). Independence is real only if the reviewer is not the executor: the
line carries the reviewer's model name, which is already an allowed field, and the server rejects an `independent_agent`
row whose reviewer model equals the executor model of the same attempt. The review text never leaves the machine; it can
quote code.

## 5. Fresh-start rules and the proving tests

- **No backfill.** Existing `evidence.md` lines are not uploaded retroactively, and no old table is migrated. Only lines
  appended after the feature exists are sent.
- **No dual reads.** Goal-level readers read the new view only for client evidence; run-scoped readers keep reading
  `verification_results` only. No code path falls back from one to the other.
- **Migration discipline.** Additive and idempotent (`CREATE TABLE IF NOT EXISTS`, `DO $$` guarded constraints), the next
  free number at the time (151 was the highest when this was written), TEXT + CHECK for enums, mandatory
  visibility, owner and scope columns, nullable at the DB layer with the V0 gate (`v0_gate.py`) requiring them at the write
  boundary, strict RLS as migration 150, and the bi-temporal columns the other tables carry. Evidence is append-only (the
  `[H]` class of `evidence`): UPDATE and DELETE refused by trigger, with the same tombstone exception.
- **Half-gate rule.** Any trigger that reads this table (for example corroboration promoting a state) lands in the same
  change as the writer (`record_local_check` and the tool).

Proving tests the migration change ships with (offline fakes, plus the DB-backed test under `DATABASE_URL`):
1. A caller cannot set `state`: the service function has no such parameter, and a direct INSERT with a state the method
   cannot reach is refused by CHECK.
2. A `self_report` never reaches `checked` or `verified`; a client `deterministic_check` pass reaches `checked`, not
   `verified`; two independent groups are needed for `verified`.
3. The same `client_event_id` twice leaves one row (outbox replay).
4. Tenant isolation: tenant A's rows are invisible to tenant B; with no tenant bound, queries return nothing (migration 150).
5. UPDATE and DELETE are refused; the tombstone exception works.
6. V0 gate: a row without scope or provenance is refused with the gate's own message.
7. The tool rejects a payload carrying any field outside the whitelist (no command text, no path, no output), and a
   `redact()` test on every string field.
8. An `independent_agent` row whose reviewer model equals the executor model is refused.
9. `test_schema_drift.py` passes with the new table.

## 6. Open questions for CORE-A

1. The state a client-reported pass reaches (`checked`, as proposed, or `verified`) and the corroboration rule.
2. Whether `verification_evidence_all` (a view over both tables) is wanted, or the readers name the two tables.
3. How server-side `expired` is computed when the server only holds path HMACs and blob shas: by supersession (a newer line
   for the same target and check hash with different hashes), or by a snapshot the client reports.
4. Whether the tool name is `report_check_evidence` and its scope (`EXECUTION_RUN` in `backend/app/api/economy.py` is the
   scope `/usage-events` uses; this may want its own).

## 7. Implemented: the server half (migration 152, 2026-10-23)

What landed (CORE-A, with the scoped grant recorded on the board), and where it differs from sections 1 to 6:

- **Table and views:** `backend/db/152_local_check_evidence.sql`: the sibling table `local_check_evidence` (section 1, option b),
  `local_check_current` (the latest live row per account, repository, target, method and check) and `local_check_effective` (the
  derived state per check, with counts of independent groups). Applied to a fresh scratch Postgres 17 (127.0.0.1:55432, database
  `kel_evidence_test`) together with the whole chain 01 to 151: the chain applies cleanly. Never to a hosted database.
- **Service:** `record_local_check`, `validate_local_check`, `local_check_state` and `local_check_effective` in
  `backend/app/services/verification.py`. No function takes a state. A reported pass is `checked`; a fail is
  `failed_verification`; `verified` is derived by the view when at least two distinct accounts pass the same check on the same
  files (the newest file signature reported). A pass and a fail on those files is `contested`.
- **Tool:** `report_check_evidence(evidence)` in `backend/app/mcp_server/server.py`. Differences from the sketch: it takes ONE
  object argument (`evidence`) and the service refuses any key outside the whitelist, because an MCP tool with typed arguments
  silently drops unknown ones. It needs a signed-in user, goes through three abuse buckets (600 per hour and 5000 per day per
  user, 20000 per hour across all users) and is on the discoverable (`V1_TOOLS`) surface, not the listed core.
- **Isolation:** strict RLS on BOTH the reporting account (`app.owner_subject`, migration 150's `sl_owner_scope_allows`) and the
  tenant (`sl_tenant_scope_allows`); the system scope sees all. The cross-account count in the view runs under the system scope
  and returns counts only.
- **V0 gate:** provenance `company_ingested` (the client's own machine) with scope `repository` = the hashed repo identity. The V0
  vocabulary has no "client reported" value and `v0_gate.py` is not changed; the row's own `state` and `method` carry the trust
  level.
- **The reviewer rule** is enforced twice: the service refuses a reviewer that is the executor (case-insensitive), and a CHECK
  constraint refuses it in the engine.
- **Known limit:** because every machine's evidence key is random (Part 1), two accounts only corroborate each other when they
  share the key, which is the team's explicit choice. Otherwise their check hashes differ and the state stays `checked`.

Tests: `backend/tests/test_local_check_evidence_offline.py` (44) and `..._e2e.py` (8, loopback database only, refuses any other
host). The surface tests that pin the MCP tool list were updated for the new tool and for the already-registered `report_audit`,
whose absence from them was a pre-existing failure.

## 8. Implemented: the client half (2026-10-23)

- **Off by default.** `STEALTHLAB_EVIDENCE_UPLOAD=on` turns it on, and a saved token is needed. With it off nothing is built, queued or
  sent, and not even the key file is created. The packaging README says it stays off until the whole package's "what leaves your
  machine" statement (board node N-8) is settled.
- **Where it runs.** `status --run` sends each new line with a short bound (the first send that fails queues the rest without waiting
  again). Dispatch, after an accepted answer is recorded as a library entry, writes the check as `deterministic_check` and, when the
  strong reviewer said "fixes" and is not the model that wrote the answer, that verdict as `independent_agent`, both at the entry's
  touched-file hashes, and only QUEUES them (no network, no wait).
- **The outbox.** `report_check_evidence` is a third tool in `lib/exec/evidence.mjs` next to `report_model_run` and `report_result`,
  with the same rules: offline or signed out it waits in `~/.stealthlab/outbox/`; a final refusal (`REFUSED`) goes to
  `outbox/rejected/`; a payload that fails the whitelist is held in `outbox/held/` without keeping the offending value; a server
  rate limit is a tool error, so it stays queued. The strict whitelist (`validateUploadPayload`) is applied again at the outbox,
  and nothing in the argument goes through `redact()` (it could rewrite a 64-hex HMAC).
- **What goes over the wire** is asserted exactly by tests: one argument, `evidence`, with only the whitelisted keys; no command, path,
  output, reviewer text or key anywhere in the request or in the queued file.
- **Fails open.** A missing or unreadable key, a malformed record, a network error or a refused report never changes a status, a run
  result or an answer.
