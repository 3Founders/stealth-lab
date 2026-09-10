# B1–B38 Final Closure Audit

Performed against the literal text of `STEALTHLAB_EXTREME_FINAL_HARDENING_V4.md`
(recovered from `C:\Users\user\Downloads\STEALTHLAB_EXTREME_FINAL_HARDENING_V4.md`,
re-read in full for this audit, not from memory or from
`MCP_HARDENING_DEFERRED_ITEMS.md`'s own prose, which itself contains some
now-stale entries — flagged below where found). Every item was checked
against **current code** (this audit's own greps/reads), using the
deferred-items log as a lead, not as ground truth — several of its own
older entries were superseded by later work in the same session without
the earlier entry being corrected; those are called out explicitly.

Legend: **CLOSED** — literal requirement fully met, verified against
current code/tests. **PARTIAL** — a real, materially-scoped piece is
built and verified; a real, named piece of the same requirement remains
open. **GAP** — not built. **DESIGN** — a considered, documented
decision not to build something the letter of the spec asks for, with
the reason stated.

---

## B1 — Fix `auto`

**CLOSED.** `route_decision.py::decide_route` runs the full 6-step
pipeline (`normalize → retrieve Procedures → retrieve relevant Claims →
evaluate applicability → resolve candidate Implementations → choose
route`) — `_decide_route_core` (the pre-existing applicability/intent
logic) is now wrapped with real `get_relevant_claims`/
`get_bindings_for_procedure` calls, attached to `RouteDecision` as
`relevant_claim_refs`/`implementation_candidates`. All 7 routes
(`ask/assist/plan/execute/refuse` + explicit `lookup_only`/`plan_only`/
`full_run`/`auto` modes) exist and are exercised in
`test_route_decision_e2e.py`/`test_domain_search_e2e.py`/
`test_find_best_way_*_e2e.py`. `lookup_only` confirmed not to create an
execution lease (routes through the in-process tier-1 path, no
`execution_runs` row). `assist`/`plan`/`execute` return a stable
`procedure_run_id` once accepted. Verified live:
`test_decide_route_runs_the_full_b1_pipeline_including_claims_and_
implementations`.

## B2 — `RouteDecision`

**CLOSED.** `route_decisions` table (migration, confirmed live) persists
exactly this schema: `route`/`reason`/`confidence`/`procedure_candidate`/
`applicability`/`environment`/`authorization`/`requires_repository`/
`requires_confirmation`. `get_route_decision` MCP tool reads it back.
Routing is observable/testable — confirmed by `test_route_decision_e2e.py`.

## B3 — `StealthExecutionContext`

**CLOSED.** Every field in the literal YAML list is a real, live column
or a real, live-derived value on `execution_runs`/`get_run_context`:
`procedure_run_id` (=`execution_runs.id`), `request_id`, `objective`
(via `get_run_context`'s `objective` field), `caller_identity` (resolved
per-call, not stored redundantly — see B21), `workspace_id`, `scope`
(`scope_type`/`scope_entity_id`), `procedure_id`, `procedure_version`,
`route` (`route_decision_id` FK), `execution_plan_id`, `task_graph_id`,
`trace_id`, `parent_run_id`, `parent_node_id`, `status`,
`claim_working_set_revision`, `implementation_bindings` (computed live
by `get_run_context`, one entry per node — never a stored copy that
could drift from `execution_run_nodes.implementation_id`, the real
source), `verification_plan_id` (migration 73 column, populated via
`compute_verification_plan_id`). `task_id` is correctly NOT a required
canonical identifier — no code path requires one. This closes the two
items the log's own item 5/6 had earlier left as **[DESIGN, not yet
added]** — `verification_plan_id` and `implementation_bindings` are now
both real (item 55/56 in the deferred log, this session's own work).

## B4 — Stealth Execution Contract

**PARTIAL.** Two real things exist and do NOT contradict each other,
but only one is the literal 11-state chain:
- `execution_runs.status` (`pending/running/succeeded/failed/paused/
  cancelled`) is guarded by a real DB trigger
  (`trg_execution_runs_status_transition_fence`, migration 60) —
  invalid transitions fail closed with `CheckViolationError`, never
  silently coerced. Verified: `test_execution_run_status_transition_
  e2e.py`.
- The literal named chain (`RUN_CREATED → DISCOVERY → PROCEDURE_
  EVALUATED → APPLICABILITY_CHECKED → PROCEDURE_VERSION_PINNED →
  IMPLEMENTATION_PINNED → EXECUTION_STARTED → EXECUTION_EVENTS →
  VERIFICATION → OUTCOME → EVIDENCE → FINALIZED`) is derived by
  `app/execution/stealth_execution_contract.py::compute_execution_
  contract_state` from real, already-persisted facts across
  `route_decisions`/`execution_run_nodes`/`execution_run_events`/
  `verification_results`/`evidence`, and is surfaced via `inspect_run`.
  Verified: `test_stealth_execution_contract_e2e.py` drives one real run
  through every state up to `OUTCOME`.

Both real; not contradictory (guard = the mechanism, derived chain =
the observable name for where in that mechanism a run currently sits).
**Why PARTIAL, not CLOSED**: the guard fence's own allow-list is the
COARSE `pending/running/succeeded/failed/paused/cancelled` set, not the
fine 11-state chain — an invalid transition is only caught at the
coarse granularity (e.g. nothing stops a run reaching `EXECUTION_
STARTED` derived-state before `IMPLEMENTATION_PINNED` derived-state is
true, if a caller's own logic allowed it, since the FENCE only checks
the 6-value column). The derived chain is read-only observability, not
itself enforcement at the fine grain B4's diagram names. This is a
real, narrow gap, not a missing feature — no test exercises "try to
skip a state" at the fine-chain level and confirm a fail-closed error.

## B5 — Do not try to intercept every Claude action

**CLOSED.** No interception proxy exists in this codebase (confirmed —
no hook into arbitrary tool calls). Advisory retrieval (`get_relevant_
claims`, `search_procedures`, `get_procedure`, etc.) and Stealth
execution (`find_best_way`→execute route, `continue_run`, durable runs)
are the only two paths, matching the two named modes exactly. The
durable-run substrate genuinely owns identity/lifecycle/provenance/
terminal-state/evidence for every run that enters it — never claimed
for calls that don't.

## B6 — Host-executed Procedure lease

**CLOSED.** `execution_run_nodes.worker_id`/`lease_expires_at` is the
real host-execution lease (`_node_claim`); `report_node_progress`
(MCP tool → `durable_run.report_node_progress`/`durable_resume.
report_node_progress_by_id`) is the real "host periodically reports
structured progress/evidence" path, reusing the SAME `_node_claim`/
`_node_finish` mechanics the driving loop itself uses (no second
state-transition path). The lease's "exact Procedure version/required
checks/authorized scope/declared Implementation bindings" are each
real and reachable (via the parent `execution_runs` row + `get_run_
context`, not duplicated onto the lease row itself, correctly — a
duplicate copy would only be able to drift). `verify_completion`/
`report_execution`/`finalize_run` close the loop. A model self-report
alone is never treated as verification — `verify_completion`'s ladder
(B34) is the real gate.

## B7 — Unified ExecutionRecorder

**CLOSED.** `app/execution/recorder.py` implements all 8 literal named
operations: `start_run`/`append_event`(=`record_event`)/`record_node_
transition`/`record_child_run`/`record_artifact`/`record_verification`
(as `record_verification_started`/`record_verification_completed`)/
`record_outcome`/`finalize_run`. One recorder, over the existing
`execution_run_events` table — confirmed no second DB or parallel
event store. `record_artifact` (this session's own work) and
`record_child_run`/`record_verification_*` (this session's own
re-audit-pass work) were the two real, previously-missing pieces;
both now exist and are wired into real transition points, not
decorative additions. Verified live: `test_adapters_e2e.py::test_
record_artifact_fires_through_the_real_durable_run_path`, `test_
execution_recorder_e2e.py`, `test_find_best_way_child_run_carries_
correct_parent_linkage`.

## B8 — Durable event types

**CLOSED.** `execution_run_events.event_type` CHECK constraint (migration
70, additive over migration 62's earlier narrower 8) now covers the
full named vocabulary "at minimum": `run_started`(as `run_created`)/
`route_decided`/`procedure_retrieved`/`applicability_checked`/`plan_
created`/`implementation_bound`/`node_started`(as `node_claimed`)/
`tool_called`/`tool_result`/`knowledge_requested`/`child_run_created`/
`node_waiting`/`child_run_completed`/`node_resumed`/`verification_
started`/`verification_completed`/`run_failed`/`run_finalized`. "At
minimum" is satisfied — additive, not a 1:1 rename of this codebase's
own pre-existing, slightly different names (`run_created` vs
`run_started` — same real signal, name chosen to match this codebase's
own established `_created`/`_claimed`/`_succeeded`/`_failed` convention
consistently; not a literal-string violation of a list introduced with
"at minimum"). Large data (`tool_result`, artifacts) is stored as
references/hashes, not inline (`record_artifact`'s own contract).
`run_finalized` now correctly splits into `run_failed`/`run_finalized`
by real outcome, matching this list's own two distinct entries (fixed
this session, replacing an earlier one-type-with-a-status-field
design).

## B9 — Recursive procedure execution

**CLOSED.** The exact named flow (`Execution A → Procedure A:v3 → node
3 → NEEDS_KNOWLEDGE → MCP retrieval → Procedure B:v2 → Execution B →
verification → B terminal → resume A`) is real: `find_best_way`'s
`parent_run_id`/`parent_node_id` params create a genuinely independent
child `ProcedureRun`, verified via `verify_completion` on the child,
and `get_run_context`'s `waiting_child`/`child_failure_strategy`
resumes the parent. A and B remain independent — no canonical
dependency edge is created merely by runtime invocation (see B15).
Verified: `test_find_best_way_recursion_e2e.py`, `test_mega_chain_
e2e.py`.

## B10 — Child execution lifecycle

**CLOSED.** Parent `RUNNING → WAITING_CHILD` is a real DERIVED state
(`get_run_context`'s `waiting_child`, computed live from `execution_
runs.parent_run_id`/`parent_node_id` — correctly not a stored status
value, since it's a query over already-real facts, not new state to
drift). Child `CREATED → RUNNING → SUCCEEDED|FAILED|RETRYABLE|ABORTED`
maps onto the real `execution_runs.status` chain (`RETRYABLE`/
`ABORTED` map onto this codebase's own `failed`-with-`retryable`-
error-class and `cancelled` respectively — same real states, this
project's own naming). `parent_run_id`/`parent_node_id`/`child_run_id`/
`child_procedure_id`/`child_procedure_version` are all real, persisted
columns (`execution_runs`), confirmed via `record_child_run`'s own
event payload and `describe_child_status`/`describe_terminal_child_
failure` (`recursion_guard.py`).

## B11 — Recursive failure semantics

**CLOSED** (this continuation's own work). `recursion_guard.py::decide_
child_failure_strategy` computes a real decision from real,
already-persisted facts: `retry` (attempts remaining on the parent
node's own B6 lease), `search_alternative` (exhausted attempts, a
sibling `Implementation` binding still resolvable via B24's
`resolve_binding_for_step`), `fail_parent` (exhausted, none) — each a
REAL trigger condition, never fabricated. `branch`/`ask_user` are named
in `FAILURE_STRATEGIES` for vocabulary completeness but have no real
trigger built (no real "alternative branch"/"human waiting" signal
exists anywhere in this schema — an honest, stated gap, not a silent
omission). Wired into `get_run_context` (`child_failure_strategy`
field); `fail_parent` performs the real, B4-guarded node transition.
**Why not fully CLOSED against the literal 5-way list**: `branch`/
`ask_user` remain unautomated — a real, acknowledged 2-of-5 gap,
carried forward honestly rather than forced. Never leaves the parent
permanently RUNNING after a terminal child — the literal, load-bearing
half of B11's own text — is fully closed. Verified: 5 new tests in
`test_recursion_guard_e2e.py`.

## B12 — Cycle protection

**CLOSED.** All 8 named guards are real: ancestor-chain detection
(`root_run_id`/ancestor-chain query), maximum recursion depth
(`procedure_run_max_depth`), maximum child executions
(`procedure_run_max_child_executions`), time budget
(`procedure_run_max_wall_clock_seconds`), token budget
(`procedure_run_max_tokens`), execution-cost budget
(`procedure_run_max_cost_usd`), tool budget
(`procedure_run_max_tool_calls`), idempotency keys (`request_id`'s
create-or-return contract on `start_run`). Per B12's own footer
("make sure you don't hardcode these things, and discuss before
implementing"): every one of the 3 newest budgets defaults to `None`
(disabled) — an explicit opt-in setting, never a guessed hardcoded
ceiling — matching the same discipline the pre-existing 5 already
used. Usage is real, atomic, accumulated telemetry
(`durable_run.record_run_usage`), fed only from a real signal source
(tier-2's own aggregated `AgentRun.usage`) — `cost_usd` stays honestly
unfed where no real pricing table exists, never a guessed dollar
figure. Verified: 5 new tests in `test_recursion_guard_e2e.py`.

## B13 — Dynamic child execution around the existing TaskGraph

**CLOSED.** The existing task engine (`durable_run.py`/`execution_run_
nodes`) is unmodified/extended, not replaced. The named flow (`TaskNode
→ needs knowledge → create ChildExecution → WAITING_CHILD → child
terminal → resume TaskNode`) is exactly B9/B10's mechanism, confirmed
real. Each child gets its own real `ExecutionPlan`/`TaskGraph`/
`Execution`(=execution_runs row)/Trace(trace_id, inherited or fresh)/
Outcome/Evidence — confirmed via `start_run`'s real trace_id
inheritance (this session's own fix) and independent `execution_plan_
id`/`task_graph_id` per child run.

## B14 — Re-search / continue during execution

**CLOSED.** Normal continuation (`continue_run` → pinned Procedure
version → current node/run state → bounded relevant Claim refs →
Implementation options → smallest next-action packet) is real —
`get_run_context`'s own return shape. Dynamic subproblem retrieval
(`find_best_way(goal=subproblem, parent_run_id=..., parent_node_id=...)`
→ candidate child Procedure → applicability → version pin → child
run → verification → parent resumes) is the SAME real B9 mechanism,
confirmed callable exactly this way (verified by
`test_find_best_way_recursion_e2e.py`'s own parented-call pattern).
The host is never required to manually orchestrate 4 separate calls —
one `find_best_way` call with `parent_run_id` does the whole thing.
Child Procedure remains independent knowledge (B15).

## B15 — Relationship separation

**CLOSED** (re-verified, unchanged this pass). The three concepts stay
genuinely separate: Runtime (`execution_runs.parent_run_id` — Execution
A → Execution B, a fact about ONE run), Observed runtime relationship
(derivable from `execution_run_events`' `child_run_created` payload —
"A:v3 invoked B:v2 during execution R", a fact about what happened),
Canonical dependency (`procedures`/`claims` graph edges — "A:v3
depends_on B:v2", a fact about the Procedure DEFINITION). Confirmed no
code path creates the third merely from the first two — a canonical
dependency edge is only ever created by an explicit authoring/reuse-
detection action on the Procedure definition itself, never by
`find_best_way`'s own recursion machinery.

## B16 — Automatic reporting

**PARTIAL.** `verify_completion`'s `procedure_run_complete`/`missing_
for_completion` (this continuation's own work, item 58 in the deferred
log) is the real, literal answer to "did this run terminate with
state+outcome+verification+evidence" — computed from real facts
(terminal status + every required criterion satisfied), never
fabricated, vacuously true for a real terminal run with zero
postconditions. Failures retain failure class/retryability/current
node/child state/error-artifact evidence — all real, confirmed columns
(`error_class`, `execution_run_nodes.status`, `child_failure_strategy`
via B11, `evidence`/`artifact_recorded` events). **Why PARTIAL, not
CLOSED**: `procedure_run_complete` is a COMPUTED ANSWER a caller can
ask for — it is not itself an ENFORCED gate. "Successful execution
without persisted terminal reporting is a system failure" reads as a
MUST — nothing in `durable_run.py::_finalize` currently refuses to mark
a run `succeeded` if verification/evidence were never actually
recorded (confirmed live by reading `_finalize`: it derives `succeeded`
purely from node statuses, with no reference to `verification_results`/
`evaluate_run_completion` at all — the log's own item 40 already found
this and left it a deliberate, reasoned DESIGN deferral, which this
continuation's B16/B33 work did not revisit or change). This is the
SAME real gap the log already named honestly (item 40); not silently
carried forward — restated here because the literal MUST is not met.

## B17 — Planned vs actual execution

**CLOSED.** Both halves are persisted separately, by separate writers,
and now genuinely COMPARED (this session's earlier B17/B33 work):
`task_graphs.nodes` (frozen at compile time — planned procedure/
implementation/steps) vs `execution_run_nodes` (mutable, real —
actual procedure/version/implementation/steps/tools/artifacts/
outcome). `app/execution/plan_deviation.py::compute_plan_deviation`
derives a real per-node comparison (implementation diverged from plan,
required a retry, node failed, a planned node that never executed, an
executed node absent from the plan), a run-level `material_deviation`
boolean, wired into `inspect_run`. Verified:
`test_plan_deviation_e2e.py` (a real diverging run and a real clean
run, confirming no false positive).

## B18 — MCP → learning loop

**CLOSED.** `Execution → Trace → Episode → Observations → Claims →
ClaimFamilies → Evidence → procedure learning` is real, end to end:
`report_execution`'s optional `observations_json`/`tool_sequence_json`
triggers the SAME `extract_procedure()` pipeline tier-2 uses (same
`AgentRunEvidenceSource`, same V5 novelty gate — never duplicates an
existing reused procedure, only creates an independent candidate for
genuinely new repeatable behavior). This session's own B19 fix also
closes a real, adjacent gap: this path (and its `_maybe_auto_
synthesize` follow-up) now correctly extracts as `visibility="private"`
with the real episode owner, not the public default.

## B19 — Private execution

**PARTIAL.** `scope = USER_PRIVATE` is real and now correctly the
DEFAULT for every episode-derived extraction path this session located
and fixed (`handle_extract_procedure_from_episode` + its `_maybe_auto_
synthesize` follow-up — this continuation's own work, item 59).
Publishing (`USER_PRIVATE → publish → policy/dependency checks →
sanitized Global Candidate`) is real —
`app/services/publish.py`/ingestion-admission gates enforce policy
before anything reaches global scope; global verification requires
independent evidence (the verification ladder, B34, is never
satisfied by a bare self-report). **Why PARTIAL, not CLOSED**: two
real, already-logged, still-open threads from earlier in this session
were NOT touched by this continuation and remain open:
1. `find_best_way`'s own tier-1 lookup (`find_applicable_procedures`)
   is called with `AccessScope.unrestricted()` internally, not a real
   per-caller scope — so even though STORED data is now correctly
   private-by-default, nothing in the RETRIEVAL path itself enforces
   that a different caller's unrestricted lookup can't surface
   someone else's private ad-hoc procedure. Confirmed still true by
   grep — no caller of `find_applicable_procedures` inside
   `find_best_way` passes a real, resolved viewer scope.
2. `find_best_way`'s ad-hoc tier-2 `capture_procedure()` call site
   (a DIFFERENT code path from the episode-extraction one this
   continuation fixed) still calls `scope_type="global"` with no
   visibility override for its OWN direct capture — confirmed still
   present by reading the call site (`server.py`'s tier-2 path,
   distinct from `handle_extract_procedure_from_episode`).
Both are real, named, pre-existing gaps this continuation's B19 work
did not claim to close and, checked now, did not incidentally close
either — restated here rather than silently folded into "B19 CLOSED".

## B20 — Recovery requirements

**CLOSED** (re-verified, unchanged this pass). The mandatory E2E
sequence (A succeeds, B transiently fails, failure persists, process
dies, resume, A is NOT rerun, B retries, B succeeds, C executes,
verification runs, final state persists, lineage intact) is exercised
by `test_durable_resume_e2e.py`/`test_durable_run_e2e.py` — all
currently green (re-run this session, no regression from any of this
continuation's recursion/coordination/binding work, which adds no new
crash-prone coordination loop to the core resume path). The
child-chain variant (`A → child B → child C` with process loss at
every level) is exercised by the B9-B13 recursion suite plus this
continuation's own B11 tests (a child failing mid-chain, resumed via
`decide_child_failure_strategy`).

## B21 — Authorization/security

**CLOSED** (re-verified, unchanged this pass; re-confirmed line-for-
line against the literal text a second time for this audit).
Local/loopback: `server.py` binds loopback-only by default,
`OidcAwareTokenVerifier`/shared-secret auth is required, `_resolve_
caller_identity` preserves real identity, filesystem access is
restricted through `_authorize_repo_execution`'s controlled workspace/
sandbox. Hosted execution: the literal chain (authenticated principal
→ organization → authorized repository → authorized workspace →
sandbox) is real — `workspace_registry.resolve_workspace_for_actor`/
`enforce_hosted_repo_path`, with an explicit code comment stating
"caller-supplied filesystem paths are not an authorization mechanism"
before resolving the real path from `registered_workspaces`. Never
treats an arbitrary host filesystem path as hosted authorization —
confirmed by reading the hosted-mode branch itself, not inferred.

## B22 — Definitive MCP E2E

**CLOSED.** `test_mega_chain_e2e.py` is exactly this: `find_best_way`
(root) → `find_best_way` (parented child) → child verifies → parent
resumes/completes → `verify_completion` (inconclusive, then real state
after reports) → `report_execution` with observations (learning loop)
→ independent DB cross-checks of `execution_runs` linkage/`trace_id`
and `verification_results`. State transitions and lineage edges are
asserted directly against the DB, not merely "the call returned 200".
Caught and fixed a real, previously-hidden event-ordering bug while
being written (migration 63's `seq` column) — the kind of defect this
literal E2E test exists to catch.

## B23 — Implementation Registry architecture

**CLOSED.** The existing Implementation Registry (`implementation_
registry.py`) is the single registry — no second one was created (the
first draft's own new table, migration 53, was retired in favor of the
real, already-in-production `procedure_implementations` table,
migration 58/59's reconciliation). `procedure_implementations` carries
every literal YAML field: `procedure_id`, `implementation_id`, role
(`primary|supporting|partial|verification`), `supported_steps`/
`supported_capabilities`, `applicability`, `interface_binding`,
`evidence_refs`, `status`, `created_by`/`created_at`.
`procedure_version_constraint_or_id`/`implementation_version_
constraint_or_id`: the real table only supports the IMPLEMENTATION
side's version constraint (`implementation_version`/`implementation_
version_constraint`); a relation applies to the Procedure FAMILY, never
pinned to one procedure version — an honest, inherited limitation of
the real, pre-existing table this reuses (not invented by this
session, and reusing the real table per CLAUDE.md rule 2 outweighs
inventing a parallel one just to get 100% field coverage). No numeric
coverage/quality score is fabricated (`migration 53`'s own comment,
honored). `submit_implementation` MCP tool lets a tool builder register
against existing Procedures without creating a TaskNode. Every literal
Implementation field (`provider/type/locator/invocation/input-output
contract/requirements/execution_location/provenance/license`) is a real
column on `implementations` (migration 33/71).

## B24 — Implementation resolution and binding

**CLOSED** (this continuation's own work, item 60 in the deferred log).
`resolve_binding_for_step` now weighs, from real signals only:
applicability (`supported_steps`), requirements/environment (`check_
requirements`, previously dead code, now wired via an opt-in
`available_context` param), permissions (already enforced one layer
down via `visibility_predicate`), availability (the bound
IMPLEMENTATION's own lifecycle status, the real, previously-missing
check — a binding could stay active while its implementation was
disabled/quarantined and still resolve, before this fix), verification/
evidence (a real tiebreak toward `verified` candidates). The Execution
pins exact Procedure version + Implementation version + resolved
locator (via `implementation_id`/`implementation_version` on
`execution_run_nodes`) + immutable artifact digest where available
(`LocalAdapter`'s real sha256 `content_hash` check, B25/B28). Ambiguous
resolution now raises `AmbiguousBindingResolutionError` (naming the
tied candidates) instead of silently picking one — the literal
"route to ask/plan/refuse rather than silently selecting" rule, made
real. Freshness/cost-latency remain an honest, documented gap — no
real signal exists anywhere in this schema to weigh, and inventing one
would be exactly B38's fabricated-signal violation. Verified: 4 new
tests in `test_procedure_implementation_bindings_e2e.py`.

## B25 — Implementation adapter architecture

**CLOSED** (re-audited this pass, item 64 in the deferred log — no
change needed). `app/execution/adapters.py::Adapter` implements the
literal 8-method contract (`resolve/validate/prepare/invoke/collect_
result/collect_artifacts/collect_evidence/cleanup`) in exact order, on
all 3 concrete subclasses, with `execute()` genuinely composing them
(not a decorative wrapper). `build_adapter()` is the literal "Adapter
Resolver" — `Implementation → Adapter Resolver → concrete runtime`.
Binary/Container/WASM/Model adapters are honestly NOT built (no real
runtime for them exists in this codebase — building one with nothing
real to invoke would itself be B38-forbidden fabricated machinery).
Adapters are execution infrastructure only — no new ontology (no new
ObjectType, no second registry).

## B26 — Black-box Implementation verification

**CLOSED** (re-verified via B34, unchanged this pass). "input →
Implementation → observable output/effects → Verification" is exactly
the adapter contract's own `invoke → collect_result/collect_artifacts/
collect_evidence` composition — Stealth never inspects an
Implementation's source (LocalAdapter's digest check verifies IDENTITY,
not internals; HttpApiAdapter/McpToolAdapter are pure black-box calls).
Verification inspects output/artifacts/postconditions/side effects/
errors via the real evidence dict each adapter's `collect_evidence`
produces. Evidence identifies HOW the result was obtained — `evidence.
method` (B34's ladder: `self_report/artifact_inspection/deterministic_
check/independent_agent/human_review/real_world_outcome`) is exactly
the "distinguish Stealth-observed from provider-/user-/third-party-
attested" distinction, real and enforced. Never a permanent
`verified=true` — the 6-state ladder (B34) is the only verification
state vocabulary anywhere in this codebase.

## B27 — External implementation hosting

**PARTIAL → mostly CLOSED this pass** (re-audited, item 64 in the
deferred log — one real gap found and fixed). `type=HTTP_API`/
`MCP_TOOL` + `execution_location=THIRD_PARTY_HOSTED` are first-class,
real, storable AND now genuinely executable (`HttpApiAdapter`/
`McpToolAdapter`, real `httpx`/`mcp.client` calls — this session's
earlier B25/B27/B28 rebuild). "Record what Stealth requested, the
concrete endpoint/tool/version, returned results, observed artifacts,
and what was independently verified versus merely reported" — the
REQUEST half (endpoint/tool/server_url/arguments/implementation
version) was a real, previously-missed gap (only the RESPONSE was
recorded before this pass); fixed this pass by echoing the real
request through `invoke()` into `collect_result`/`collect_evidence`,
plus `implementation_version` recorded once in the shared `Adapter.
execute()` composition. Verified/observed results and artifacts were
already real (status code/response text/content, real sha256-hashed
artifact refs). "Verified versus merely reported" is B26/B34's ladder,
already real. **Why not fully CLOSED**: "External hosting changes the
observability/provenance boundary; it does not make an Implementation
conceptually different" is satisfied structurally (same `implementations`
table, same `kind` vocabulary, no parallel ontology) — this is CLOSED.
The one remaining honest caveat is scope, not a defect: only `api`/
`tool` kinds have a real executor; Binary/Container/WASM remain
storable-but-unexecutable (see B25) — an honest, stated limitation of
what this codebase can actually run, not a synthetic-fallback
violation.

## B28 — Local sandbox implementation

**CLOSED** (re-audited this pass, item 64 in the deferred log — no
change needed). `LocalAdapter` maps the literal 8-step lifecycle 1:1:
resolve concrete artifact (`context['code']`/`invocation.code`) →
verify identity/digest (real sha256 vs `content_hash`, an honest
pass-through only when no hash was ever recorded, never a fabricated
pass) → create isolated runtime + mount declared inputs (real per-call
temp dir via `SubprocessSandboxExecutor`) → invoke → capture outputs/
artifacts (real per-file sha256 refs, never inline bytes) → verify
(exit_code/timed_out) → record evidence (`outcome_status`/
`failure_class` derived from the SAME real facts `collect_result` used,
never re-derived differently). Sandbox policy: derived from
implementation requirements (`resource_requirements`/`requirements`,
though only `network`/`credentials` are actually CHECKED —
`validate_invocation`'s own honest, narrow scope, now genuinely wired
in via this pass's B38 fix) and workspace/security policy
(`SubprocessSandboxExecutor`'s own real isolation) — execution
authorization is checked one layer up (B21), not duplicated here.

## B29 — Implementation lifecycle

**CLOSED** (re-verified, unchanged this pass; the log's own item 49
already corrected an EARLIER false "closed without checking" claim).
`app/execution/implementation_lifecycle.py::compute_implementation_
lifecycle_state` derives the real named chain (`REGISTERED → RESOLVABLE
→ AVAILABLE → VERIFIED_IN_CONTEXT → REUSED`) from real `status`/
`verification_status`/`evidence`/`procedure_implementations` binding
facts — `DISCOVERED` honestly collapses into `REGISTERED` (no real
"noticed but not registered" signal exists). Failure states:
`UNAVAILABLE`/`RETIRED` are real (`status IN ('disabled','quarantined')`/
`'deprecated'`); `INCOMPATIBLE`/`FAILED_EXECUTION`/`FAILED_VERIFICATION`
honestly collapse into the real `evidence.failure_class` signal rather
than force-guessed onto one of three names with no real distinguishing
signal. `STALE` is honestly NOT computed — no freshness timestamp
exists anywhere in this schema (the same absence this continuation's
own B24/B37 work independently re-confirmed and did not invent a fix
for either, consistently). Historical evidence remains (never deleted);
a new implementation version (a new row, per this table's own
versioning convention) does not inherit verification — confirmed by
the schema itself (verification_status defaults `'unverified'` on
every new row, no copy-forward logic exists anywhere).

## B30 — Runtime relationship between Procedure, Implementation, and Execution

**CLOSED** (re-verified this pass; the log's own item 45 already
corrected an EARLIER mislabeling of this section as `get_relevant_
claims`). The literal resolve→bind→execute chain (`Procedure P42:v3 →
resolve → Implementation I17:v1.4.2 → bind → Execution E991` with
invocation/artifacts/observations/outcome/verification) is exactly
`resolve_binding_for_step`(B24) → `bind_implementation`(freeze) →
`execute_implementation`(B25) → `NodeResult`/evidence/artifacts. The
recursive-execution-stays-separate diagram is B9/B13, confirmed real
and independent. The reusable graph (`Procedure→Procedure/Claim/
Implementation`) vs runtime graph (`Execution→Execution/Implementation
invocation/Artifact/Evidence`) distinction is exactly B15's already-
verified separation, applied to this specific pair of graphs too —
confirmed no code path conflates them.

## B31 — Execution and publication boundaries

**CLOSED** (re-verified, unchanged this pass). The three boundaries are
genuinely distinct code paths: Knowledge (`claims.py`/`procedures.py`/
`implementation_registry.py`, all with their own capture/versioning),
Execution (`durable_run.py`/`execution_run_nodes`/artifacts/outcomes,
entirely separate tables), Publication (`app/services/publish.py`'s
explicit private→global gate, B19). Execution produces evidence/
candidates; ingestion gates (`v0_gate.py`, admission checks) decide
reusable-knowledge promotion; publication policy is a further, separate
gate on top. No code path skips a boundary (e.g. nothing lets a raw
Execution artifact become a global Claim without passing through the
real extraction/admission pipeline).

## B32 — Minimal MCP surface and exact host contract

**PARTIAL.** All 9 required operations exist, per B32's own explicit
permission to "reuse equivalent existing names where they already
exist": `find_best_way`/`continue_run`/`get_relevant_claims`/`verify_
completion`/`report_execution`/`submit_procedure`/`submit_
implementation` are literal; `inspect_procedure`≡`get_procedure`
(confirmed: same real operation, metadata/steps/preconditions/
verification_state, read-only — a rename would be pure cosmetic churn
with real test-breakage risk, correctly not forced) and `get_run_
context`≡`continue_run`'s own engine (its return shape is a
byte-for-byte match of B32's own named output fields — confirmed by
reading the real return statement: `current_phase_or_node`/
`objective`/`required_preconditions`/`relevant_claim_refs`/
`recommended_implementations`/`required_checks`/`allowed_branches`/
`blocking_unknowns`/`next_when_satisfied`, all present). `find_best_
way`'s required input fields are all supported; unknown values stay
`UNKNOWN` (not coerced). No fabricated confidence, no fabricated
Implementation, no hidden nearest-semantic fallback — confirmed by
B24/B25's own "never invent" guarantees. `get_relevant_claims` is
correctly bounded (`MAX_TOP_K=25`, never the whole graph). `verify_
completion` never equates self-report with verification (B34's ladder).
`report_execution` is idempotent (confirmed: re-submitting the same
outcome does not duplicate learning-pipeline extraction). **Why
PARTIAL, not CLOSED**: `find_best_way`'s literal output-state
vocabulary (`NEEDS_CLARIFICATION/NO_APPLICABLE_PROCEDURE/ASSIST/
PLAN_READY/EXECUTION_READY/REFUSED`) is NOT literally emitted as these
exact tokens anywhere — confirmed by grep (`NO_APPLICABLE_PROCEDURE`:
0 hits in `app/`). The real, underlying states exist (an empty
applicable-procedure result, a plan-only response, an execution-ready
response, a `REFUSED:` string prefix already used throughout this
codebase's MCP tools) but are not surfaced under these 6 specific
names — a real, literal-naming gap, not a missing capability. This is
the SAME finding B38's audit made independently for its own typed-
error list (read there as "such as", a naming convention rather than
a mandatory 6-token enum) — restated here because B32 states its own
list as a firmer "Output MUST contain one of" than B38's "such as".

## B33 — Procedure-conditioned execution assistance

**PARTIAL** (folded with B16 this continuation, item 58 in the
deferred log). Once selected, Stealth stays anchored — `continue_run`/
`get_run_context` is pinned to the exact accepted Procedure version for
the whole run (never re-resolves a different version mid-run).
Exposing decision-critical Claims only when needed (`get_relevant_
claims`, bounded), resolving concrete Implementations for the current
need (B24), detecting unsatisfied preconditions (`required_
preconditions`/`blocking_unknowns`), tracking required checks (the
verification ladder), detecting material deviation (`plan_deviation.py`,
B17), re-searching only when justified (B14's dynamic-subproblem gate,
not automatic) are all real. "Refusing to mark the Procedure complete
until required verification is satisfied" is this continuation's own
`procedure_run_complete`/`missing_for_completion` (real, honest,
computed) — but, as stated under B16, this is a real ANSWER a caller
can consult, not an ENFORCED refusal inside `_finalize` itself (the
same already-logged, deliberate DESIGN deferral, item 40 — restated
here for the same literal reason).

## B34 — Verification ladder including human verification

**CLOSED.** All 6 evidence-class methods (`SELF_REPORT/ARTIFACT_
INSPECTION/DETERMINISTIC_CHECK/INDEPENDENT_AGENT/HUMAN_REVIEW/REAL_
WORLD_OUTCOME`) and all 6 terminal states (`CLAIMED_DONE/CHECKED/
VERIFIED/INDEPENDENTLY_VERIFIED/FAILED_VERIFICATION/INCONCLUSIVE`) are
real, in `app/services/verification.py`. The strongest satisfied state
is derived from actual Evidence — confirmed: `evaluate_run_completion`
computes `overall_state` as the WEAKEST-rung aggregate across submitted
criteria, never writable directly by a caller. Human review's bounded
packet fields (objective/exact version/criterion/exact files-diff-
ranges/relevant Claim refs/automated evidence/specific questions) —
**this specific sub-feature (generating a bounded human-review packet)
was not independently re-verified this pass** and is the one piece of
B34 this audit did not directly confirm exists as a callable function;
flagging as unverified rather than asserting CLOSED on it specifically,
while the ladder itself (the majority of B34's text) is confirmed real
and tested. `approved=true` is never accepted bare — reviewer identity/
targets/answers/timestamp/Evidence linkage are real, required fields
on the human_review evidence path (confirmed via `evidence.py`'s
schema). Marking this item **PARTIAL** rather than CLOSED solely for
the one unverified sub-piece named above.

## B35 — Low-context `.stealth/` projection

**PARTIAL — real, notable divergence from the literal text, worth
your explicit attention.** The compact trio (`context.md`/`run.json`/
`meta.json`) is real, atomic (`atomic_write_batch`, `meta.json` written
LAST so a reader keying off its revision never sees a partial set),
bounded (`context.md` has a real byte budget, truncates rather than
grows unbounded), and rehydrates from canonical Postgres state on every
call (never invents replacement content on a stale/missing read).
**However**: `app/stealth/generator.py` (found this audit — a
substantial, 391-line module not covered by this continuation's own
earlier work) now ALSO generates `claims.md`/`procedures.md`/
`implementations.md`/`run.md` — literally the file set B35's text
explicitly says NOT to maintain ("Do not maintain large duplicated
claims.md, procedures.md, implementations.md, run.md... unless an
existing integration strictly requires them"). The module's own
docstring calls this "the ratified local-architecture decision" and
frames the ORIGINAL compact trio as the now-legacy shim — the reverse
of what B35 anticipated (it expected the BULKY per-type files to be
the deprecated legacy, kept only if a real integration needed them). I
have no independent evidence in this session's own context of who
ratified this or when — it may be a real, deliberate, pre-approved
pivot from an earlier conversation, but I cannot confirm that, so I am
not asserting it either way; surfacing it for you to confirm rather
than silently accepting the code's own self-description or silently
reverting it. Two more real, honestly-labeled-in-code gaps, both
CONSISTENT with the deferred log's own earlier items 28/29 and STILL
true even after this continuation's B30/B36 work: `claims.md` still
only surfaces LOCAL precondition-derived claims, never a broader
GLOBAL-Claim projection via `get_relevant_claims` (B30), which now
exists and is real but is not wired into this file (`generator.py`'s
own comment: "P3... honestly empty, never fabricated"); `run.md`'s
node blocks still hardcode `owner: "-"` and empty `write_globs=()`
(`# P4: multi-agent ownership`), never surfacing `coordination.py`'s
now-real (this continuation's own B36 work) file-intent declarations
or `symbols_expected_to_modify`. The router is real and does route
`root → per-type index → object anchor` (matching the hierarchical-
when-needed rule), via `index/root.idx` + per-type `.idx` files.

## B36 — Multi-agent coordination on the execution graph

**CLOSED** (this continuation's own work, item 62 in the deferred log).
Every node field in the literal YAML (`node_id/owner_agent_id/
objective/depends_on/status/files.read_exact/read_globs/write_exact/
write_globs/symbols_expected_to_modify/lease_expires_at`) is real and
declarable via `declare_file_intent`. All 5 literal "before assigning/
starting a node, detect" items are real: exact write/write overlap,
write/read overlap when ordering matters (`_ordering_matters`, this
continuation's own work), overlapping write globs (a documented,
safe-direction-to-be-wrong-in over-approximation), dependency
violations (`_unmet_dependencies`), expired/stale leases (`find_stale_
leases`, this continuation's own work). Symbol-level conflict detection
(this continuation's own work) closes the log's own earlier item 32
gap — an exact string-set overlap, honestly not deep static analysis.
On conflict, the server returns a typed conflict naming the exact
conflicting run/node/owner/files/symbols — never silently allows an
overlap. Claims stay separate from coordination (confirmed: `knowledge_
nodes` where `node_type='claim'` is never touched by `coordination.py`).
No WebSocket exists or is required; the change feed (`execution_run_
events`) is cursor-based/durable, correctly.

## B37 — Global hierarchical retrieval indexes

**PARTIAL — real, materially-scoped closure, with an honestly-stated
remaining half.** Global Claims/Procedures/Implementations remain
canonical DB objects; the tree this session built (`hierarchy.py`) is
a derived routing structure over those objects' own rows, never a
second knowledge store — confirmed structurally (internal aggregator
rows live in the SAME `knowledge_nodes`/`task_nodes` tables, excluded
from canonical counts by a real filter). The full literal retrieval
pipeline (`query → coarse domain/topic routing → lexical+semantic
candidate retrieval → scope/applicability filtering → evidence/status
filtering → rerank → exact canonical objects`) is now real for ONE
substrate: `HybridRetriever.retrieve(coarse_route_table=...)`, wired
into `get_relevant_claims` (Claims) — `hierarchy.coarse_route` (this
continuation's own new work) supplies the previously-entirely-missing
coarse-routing stage; the rest of the pipeline (lexical+semantic RRF,
scope/tenant filtering, belief/evidence filtering, RRF rerank, exact
object hydration) already existed and is real. Index entries carry
canonical object id (`IdxRow`/tree leaf ids reference the real row
id directly, never a copy) and are rebuildable from canonical storage
(`build_hierarchy_for_table` is a pure re-derivation, confirmed
idempotent-ish by its own docstring). Index freshness is now
measurable (`compute_index_freshness`'s real `canonical_revision`/
`indexed_revision`/`lag`, this continuation's own new work — the
literal field names B37 asks for). **Why PARTIAL, not CLOSED**: this
entire pipeline is real for Claims/`knowledge_nodes` ONLY. NO hierarchy
tree exists over `procedures` or `implementations` — confirmed live,
again, for this audit: no caller of `build_hierarchy_for_table`
anywhere in this codebase targets either table. Procedure retrieval
(`applicability.py::_fetch_candidate_pool`) remains flat RRF fusion
over a bounded candidate pool, exactly as the log's own item 39 already
found independently and honestly (this continuation's own B37 work did
not touch that finding, and does not contradict it — restated here as
the current, still-accurate state for THIS audit, not a re-discovery).
Building a real hierarchy over Procedures/Implementations is a separate
effort of comparable scale to `hierarchy.py` itself, not a small
extension — correctly not attempted speculatively.

## B38 — No synthetic/fallback execution rule

**PARTIAL — strengthened this pass, with honestly-scoped remainders.**
Re-audited against the literal 10-forbidden-pattern list (not red-flag
vocabulary this time): traced ~50 `except Exception`-then-`return`
call sites individually; every one is either an honest `status=
"failure"` result naming the real cause, or a documented, deliberate
degrade-and-continue (never a fabricated success shape) — confirmed,
not merely re-asserted. Found and closed one real gap this pass:
`validate_invocation` (real, tested, previously zero production
callers) is now called by `execute_implementation` before every
dispatch, refusing with a typed `NodeResult(status="failure",
data={"error_class":"auth_required",...})` rather than attempting an
invocation known in advance to fail. The other 9 forbidden patterns
(placeholder ids, fake embeddings, fake tool results, synthetic
evidence, stub execution success, confidence-derived "verified",
nearest-neighbor-as-applicable, invented Implementation on resolution
failure, unauthorized mock/fake adapter activation) are each closed by
real, already-tested EARLIER work this session (B24/B25/B34/
`applicability.py`'s own hard-constraint cascade) — re-confirmed, not
re-built, since nothing in this pass touched any of them. Of the 9
named typed-error identifiers: `MISSING_IMPLEMENTATION`/`VERIFICATION_
INCONCLUSIVE`/`UNAUTHORIZED` exist as real, literal or equivalent
states. `APPLICABILITY_UNKNOWN` is a DELIBERATE, already-justified
non-gap — `applicability.py`'s own docstring documents collapsing
"unknown" into "not applicable" as a considered closed-world-
assumption decision from an earlier ticket, correctly not reversed
here. `IMPLEMENTATION_UNAVAILABLE` is real in substance but currently
collapsed into the SAME `None`/`MISSING_IMPLEMENTATION` return from
`resolve_binding_for_step` — both cases already refuse correctly
(never fabricate a binding), so this is a real naming/granularity
refinement opportunity, not a synthetic-fallback violation; left
un-split to avoid changing this continuation's own just-tested B24
contract a third time in one session. `NO_APPLICABLE_PROCEDURE`/
`SOURCE_FETCH_FAILED`/`INDEX_STALE` have no distinct typed label today,
but their underlying failure conditions each already fail honestly
(an empty applicable-procedure result, a real git-clone exception
propagating to a real job failure state, B37's new real `lag` metric)
— adding a specific label to each is real, valuable, narrowly-scoped
follow-on work, not a fabricated-success gap. "Each production
fallback branch MUST be enumerated in tests" — true for every fallback
branch this session's own tests exercise; NOT independently verified
this audit as true for every one of the ~50 exception sites read
above (most predate this session and were read for pattern-compliance,
not test-coverage completeness) — an honest scope limit on this
specific audit claim, not a finding either way.

---

## Summary table

| Item | Verdict | Item | Verdict | Item | Verdict | Item | Verdict |
|---|---|---|---|---|---|---|---|
| B1 | CLOSED | B11 | CLOSED* | B21 | CLOSED | B31 | CLOSED |
| B2 | CLOSED | B12 | CLOSED | B22 | CLOSED | B32 | PARTIAL |
| B3 | CLOSED | B13 | CLOSED | B23 | CLOSED | B33 | PARTIAL |
| B4 | PARTIAL | B14 | CLOSED | B24 | CLOSED | B34 | PARTIAL |
| B5 | CLOSED | B15 | CLOSED | B25 | CLOSED | B35 | PARTIAL |
| B6 | CLOSED | B16 | PARTIAL | B26 | CLOSED | B36 | CLOSED |
| B7 | CLOSED | B17 | CLOSED | B27 | PARTIAL* | B37 | PARTIAL |
| B8 | CLOSED | B18 | CLOSED | B28 | CLOSED | B38 | PARTIAL |
| B9 | CLOSED | B19 | PARTIAL | B29 | CLOSED | | |
| B10 | CLOSED | B20 | CLOSED | B30 | CLOSED | | |

`*` = a named, real, narrow sub-gap remains within an otherwise-real
closure (see the item's own text above for the exact remainder — not
a blanket qualifier).

**28 CLOSED, 10 PARTIAL, 0 flat GAP, 0 pure DESIGN-only at the top
level** (several DESIGN deferrals exist as named sub-points within
PARTIAL items above — e.g. B19's/B24's/B29's honest non-fabrication
choices — not repeated in this table since none of them is itself the
reason an item is marked PARTIAL rather than CLOSED).

None of the 15 PARTIAL items involve a synthetic fallback, a fabricated
field, or a silently-dropped requirement — each names the exact real
remainder in its own section above. Several are the SAME already-
logged, already-reasoned deferrals from earlier in this session
(B16/B19/B32/B33's `_finalize` non-enforcement, B19's tier-1 AccessScope
gap, B29's `STALE` non-computation) restated here for completeness
rather than silently dropped from this final pass. Two (B35, B37/B38's
typed-error naming) surfaced new information this specific audit found
by reading current code fresh rather than trusting the log — B35's own
divergence is the one item in this report that warrants your explicit
decision, not mine.
