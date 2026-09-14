# StealthLab --- Full Backend Production Readiness & Systems Verification Plan

**Target commit:** `c5ebdea246adc4af6164207c590ab88e2abf4d27`\
**Repository:** `3Founders/stealth-lab`

## Mission

You are GPT-6 Astra, acting as an independent principal engineer,
systems auditor, security reviewer, QA lead, and production-readiness
reviewer.

Your job is NOT to confirm that the code looks reasonable. Determine
whether the backend actually behaves like the intended StealthLab system
under normal operation, malformed inputs, authorization failures,
private/global scope boundaries, concurrency, retries, crashes,
recursive execution, partial failure, stale state, conflicting
implementations, verification failure, learning/ingestion, publication,
migrations, large datasets, and adversarial callers.

Do not equate "tests pass" with production readiness. Do not trust
comments, docstrings, previous audits, or CI as proof. Use current
code + schema/migrations + runtime behavior + tests + observed outputs.

## Critical project-state constraint

The target commit is `c5ebdea246adc4af6164207c590ab88e2abf4d27`.

Important: **real production ingestion has NOT yet been performed.**

Therefore produce separate verdicts for:

1.  backend/platform code readiness
2.  real-data/ingestion readiness
3.  overall production readiness

Synthetic-corpus tests are useful platform validation, but MUST NOT be
reported as proof of real production ingestion.

The target commit itself notes that CI currently covers
packaging/harness work while `backend/tests` is not run by CI. Astra
MUST independently run and report the backend tests.

------------------------------------------------------------------------

# 1. Intended system

StealthLab is a permissioned, evidence-backed procedural capability
layer for AI agents.

It is not merely memory, a prompt library, a vector database, an LLM
wrapper, or an execution engine.

The intended chain is:

``` text
SOURCE
  ↓
ARTIFACT
  ↓
OBSERVATION
  ↓
EVIDENCE
  ↓
CLAIM
  ↓
PROCEDURE
  ↓
IMPLEMENTATION
  ↓
TASK / PLAN
  ↓
EXECUTION
  ↓
VERIFICATION
  ↓
OUTCOME + EVIDENCE
  ↓
LEARNING
  ↓
ADMISSION / PUBLICATION
  ↓
REUSABLE VERIFIED CAPABILITY
```

Core promise:

> A caller can discover a way to accomplish a task, determine whether it
> applies, resolve a real implementation, execute it under controlled
> scope, verify the result, preserve provenance, and---only when policy
> allows---turn the experience into reusable knowledge.

------------------------------------------------------------------------

# 2. Non-negotiable invariants

Astra MUST test these directly.

### I1 --- No fabricated knowledge

Never fabricate Claims, embeddings, confidence, applicability,
Implementations, execution results, artifacts, evidence, verification,
IDs, tool results, or success states.

Unknown remains UNKNOWN.

### I2 --- Knowledge/execution separation

A Procedure is reusable knowledge. An Execution is an observed runtime
event. Running a Procedure must not silently mutate canonical knowledge.

### I3 --- Evidence ≠ belief

Observation confidence, Claim belief, and verification state are
distinct.

### I4 --- Private means private

Expected scopes:

`USER_PRIVATE → WORKSPACE_PRIVATE → ORG_PRIVATE → GLOBAL`

A caller must never gain access merely because an object exists in the
database.

### I5 --- Retrieval is not authorization

Canonical retrieval must enforce the actual caller scope.

### I6 --- Procedure version pinning

An accepted execution must not silently switch Procedure versions.

### I7 --- Implementation binding

Resolution may choose a candidate; execution must bind/pin the concrete
Implementation/version/locator.

### I8 --- Verification is independent

Agent self-report is not independent verification.

### I9 --- Successful terminal state is fail-closed

A run cannot become successful merely because nodes stopped failing.
Required outcome/verification/evidence conditions must be satisfied.

### I10 --- Durable state

Process death must preserve run identity, node state, parent/child
lineage, leases, events, evidence, verification, and outcome.

### I11 --- Runtime ≠ canonical dependency

Runtime Procedure invocation must not automatically create a canonical
Procedure dependency.

### I12 --- No synthetic fallback

Unavailable real capability must fail honestly.

### I13 --- `.stealth/` is projection, not canonical truth

Canonical backend state remains authoritative.

### I14 --- Learning is private-first

Execution-derived knowledge must not silently become global.

### I15 --- Migration correctness

Fresh installation and upgrade installation must converge to the same
intended schema.

------------------------------------------------------------------------

# 3. Part A --- ingestion and knowledge verification

Verify the complete knowledge lifecycle.

## A1 Source registry

Test source identity, type, locator, provenance, ownership/scope,
status, idempotency, duplicates, versioning, malformed sources,
inaccessible sources, unauthorized sources, and mutated sources.

## A2 Ingestion context

Every ingestion must have durable context including source, actor/owner,
scope, timestamps, traceability, and failure state.

Every downstream object must be traceable to its ingestion context.

## A3 Artifacts and artifact blocks

Verify identity, provenance, deterministic parsing, block extraction,
hashing/references, large artifacts, malformed artifacts, and duplicate
behavior.

## A4 Observations

Verify:

`Artifact → Observation`

Observations must represent actual source content. Test repetition,
contradiction, parser failure, and deterministic reprocessing.

## A5 Claims

Verify:

`Observation → candidate Claim`

The model may propose a Claim but must not independently prove it.

Test schema validation, entity resolution, deduplication, existing-Claim
matching, provenance, scope, status, belief, and evidence linkage.

## A6 Typed procedural Claims

Verify procedural relations such as preconditions, required-before,
produces, depends-on, conditional applicability, and verification
requirements.

## A7 Procedures

Verify:

`Claims + evidence → Procedure candidate`

A procedural document should produce a candidate Procedure; a
non-procedural document should not. Test duplicates, versions,
provenance, scope, and missing evidence.

## A8 Screening

Verify procedural relevance, minimum evidence, duplicate handling,
rejection reasons, and durable screening decisions.

## A9 Admission

Verify candidate → reusable Procedure only through intended admission
gates.

No raw source/artifact may jump directly into globally reusable
knowledge.

## A10 Composition

Test Procedure dependencies, cycles, depth limits, composition
semantics, and separation of canonical vs runtime relationships.

## A11 Claim belief/impact

Verify belief and impact are distinct from source observation confidence
and that unsupported Claims cannot become verified.

## A12 Publication

Verify:

`private candidate → policy/dependency/admission checks → sanitized global candidate`

Publication must be explicit and gated.

## A13 Retrieval

Test exact/local/family/workspace/org/global retrieval, scope,
applicability, evidence/status filtering, bounded results, deterministic
hydration, stale indexes, and unknowns.

## A14 Hierarchical retrieval

For Claims, Procedures, and Implementations verify:

``` text
query
→ coarse routing
→ lexical/semantic candidates
→ scope/applicability filtering
→ evidence/status filtering
→ rerank
→ canonical object hydration
```

Indexes must reference canonical IDs and be rebuildable. Verify
`canonical_revision`, `indexed_revision`, `index_lag`, stale behavior,
and no fabricated hierarchy membership.

## A15 Knowledge isolation

Use multiple users and workspaces.

Prove: - User A private data is retrievable by A - User B cannot
retrieve A's private data - workspace-private data requires
authorization - org-private data requires authorization - global data
follows global policy

Test service/MCP paths and direct canonical retrieval paths where
feasible.

------------------------------------------------------------------------

# 4. Part B --- B1--B38 individual verification

For every item below, Astra must inspect implementation, run targeted
tests, run negative tests, inspect persisted state, and assign PASS /
PASS WITH LIMITATION / PARTIAL / FAIL / NOT TESTABLE.

## B1 --- `auto`

Verify normalize → Procedure retrieval → relevant Claims → applicability
→ Implementation resolution → route.

Test ask/clarify, assist, plan, execute, refuse, lookup_only, plan_only,
full_run, auto. Confirm lookup_only does not create an execution lease.

## B2 --- RouteDecision

Verify durable route/reason/confidence/Procedure
candidate/applicability/environment/authorization/repository/confirmation
fields and readback.

## B3 --- StealthExecutionContext

Verify live context includes/resolves procedure_run_id, request_id,
objective, caller identity, workspace, scope, Procedure/version, route
decision, plan, task graph, trace, parent run/node, status, Claim
working-set revision, Implementation bindings, verification plan.

## B4 --- Stealth Execution Contract

Enforce and test:

`RUN_CREATED → DISCOVERY → PROCEDURE_EVALUATED → APPLICABILITY_CHECKED → PROCEDURE_VERSION_PINNED → IMPLEMENTATION_PINNED → EXECUTION_STARTED → EXECUTION_EVENTS → VERIFICATION → OUTCOME → EVIDENCE → FINALIZED`

Attempt to skip required states and prove fail-closed behavior.

## B5 --- Do not intercept every Claude action

Verify there is no false claim that arbitrary host actions are
automatically intercepted. Stealth-controlled execution owns provenance;
outside behavior must not be falsely represented as Stealth-observed.

## B6 --- Host-executed Procedure lease

Verify worker identity, lease acquisition/expiry, progress reporting,
authorized scope, pinned Procedure/version, Implementation binding,
verification, and completion. Test competing workers and expired leases.

## B7 --- ExecutionRecorder

Verify durable start_run, event, node transition, child run, artifact,
verification, outcome, and finalize operations through one canonical
event mechanism.

## B8 --- Durable events

Verify required event vocabulary, ordering, sequence, timestamps,
parent/child relationships, tool calls/results, knowledge requests,
verification, failure, and finalization.

## B9 --- Recursive execution

Exercise:

`A → Procedure A → node needs knowledge → Procedure B → child Execution B → verify B → resume A`

Verify independent A/B identities.

## B10 --- Child lifecycle

Verify parent waiting, child creation/running/terminal states, persisted
lineage, and correct parent resumption.

## B11 --- Recursive failure semantics

Test retry, search_alternative, branch, ask_user, and fail_parent where
V4 requires them. A terminal child must never leave the parent
permanently RUNNING.

## B12 --- Cycle/budget protection

Test ancestor cycles, recursion depth, child count, wall-clock, token,
cost, tool-call budgets, and idempotency. Use only real usage signals;
never invent pricing.

## B13 --- Dynamic child execution

Verify each child has independent execution identity, plan, graph,
trace, outcome, and evidence with correct trace inheritance semantics.

## B14 --- Re-search/continue

Verify pinned Procedure, current node, bounded Claims, Implementation
options, next-action packet, and justified dynamic subproblem retrieval.

## B15 --- Relationship separation

Verify runtime parent/child, observed runtime invocation, and canonical
Procedure dependency are three distinct concepts.

## B16 --- Automatic reporting / terminal enforcement

Negative tests: - success without verification - success without
evidence - incomplete criteria - direct terminal success attempt - valid
verified success - valid failure

The successful terminal transition must be fail-closed.

## B17 --- Planned vs actual execution

Compare TaskGraph plan with execution nodes. Detect implementation
divergence, retries, failures, missing planned nodes, unexpected
executed nodes, and material deviation.

## B18 --- MCP → learning loop

Verify:

`Execution → Trace → Episode → Observations → Claims → ClaimFamilies → Evidence → Procedure candidate`

Use the real extraction pipeline and verify deduplication.

## B19 --- Private execution

Treat as P0. Test private-by-default learning, caller-scoped retrieval,
workspace/org authorization, explicit publication, no global ad-hoc
capture, and no unrestricted private retrieval.

Use multiple users/workspaces.

## B20 --- Crash recovery

Test:

`A succeeds → B fails → failure persists → process dies → resume → A not rerun → B retries → B succeeds → C executes → verification → final state`

Repeat with recursive child chains.

## B21 --- Authorization/security

Verify authenticated principal → organization → authorized repository →
authorized workspace → sandbox.

Test missing/invalid auth, wrong user/workspace/repository, traversal,
symlink escape, arbitrary filesystem paths, token validation, identity
propagation, local/hosted mode.

## B22 --- Definitive MCP E2E

Run root find_best_way → child → child verification → parent resume →
verify_completion → report_execution → learning → DB cross-check. Never
accept HTTP 200 alone.

## B23 --- Implementation Registry

Verify Procedure relation, Implementation, role, supported
steps/capabilities, applicability, interface binding, evidence, status,
creator, version, execution location, provenance, and license.

## B24 --- Implementation resolution

Verify applicability, requirements/environment, authorization,
availability, evidence/verification, version, locator, digest. Ambiguous
resolution must not silently select.

## B25 --- Adapter architecture

Verify exact lifecycle:

`resolve → validate → prepare → invoke → collect_result → collect_artifacts → collect_evidence → cleanup`

All concrete adapters must actually compose it.

## B26 --- Black-box verification

Verify:

`input → Implementation → observable output/effects → Evidence → Verification`

Stealth must not require Implementation source inspection. No permanent
verified flag.

## B27 --- External hosting

Test HTTP API/MCP tool, third-party hosting, endpoint/tool, version,
request, response, artifacts, evidence, and independent verification. Do
not treat merely storable adapter types as executable.

## B28 --- Local sandbox

Verify concrete artifact, digest, isolation, declared inputs, execution,
outputs, artifact hashes, timeout, exit status, evidence,
network/credential policy, cleanup, malicious paths.

## B29 --- Implementation lifecycle

Verify:

`DISCOVERED → REGISTERED → RESOLVABLE → AVAILABLE → VERIFIED_IN_CONTEXT → REUSED`

and legitimate failure states:

`UNAVAILABLE, RETIRED, INCOMPATIBLE, FAILED_EXECUTION, FAILED_VERIFICATION, STALE`

Do not fabricate lifecycle signals. If a state requires a real signal
absent from the schema, identify the exact missing signal rather than
giving false PASS.

## B30 --- Procedure/Implementation/Execution relationship

Verify:

`Procedure P:v3 → resolve → Implementation I:v1 → bind → Execution E`

and independent runtime artifacts/outcomes/evidence.

## B31 --- Knowledge/execution/publication boundaries

Verify `Knowledge ≠ Execution ≠ Publication`. Raw execution artifacts
cannot become global Claims without extraction/admission/publication
gates.

## B32 --- Minimal MCP host contract

Verify required operations and exact structured output states:

`NEEDS_CLARIFICATION, NO_APPLICABLE_PROCEDURE, ASSIST, PLAN_READY, EXECUTION_READY, REFUSED`

Verify unknowns stay UNKNOWN, Claims are bounded, no Implementation is
invented, reporting is idempotent, and self-report is not verification.

## B33 --- Procedure-conditioned assistance

Verify exact Procedure version pinning, decision-critical Claims,
concrete Implementation resolution, blocking unknown preconditions,
required checks, deviation detection, justified re-search, and
verification-gated completion.

## B34 --- Verification ladder/human review

Verify evidence classes:
`SELF_REPORT, ARTIFACT_INSPECTION, DETERMINISTIC_CHECK, INDEPENDENT_AGENT, HUMAN_REVIEW, REAL_WORLD_OUTCOME`

Verify states:
`CLAIMED_DONE, CHECKED, VERIFIED, INDEPENDENTLY_VERIFIED, FAILED_VERIFICATION, INCONCLUSIVE`

Verify strongest state is derived from actual evidence.

Human review must require reviewer identity, target, criterion, answers,
timestamp, evidence linkage, and bounded review packet. Bare approval
must never bypass verification.

## B35 --- `.stealth/` projection

Verify bounded/atomic projection, revision metadata, canonical Postgres
rehydration, no invented content, routing/navigation, Claims,
Procedures, Implementations, run context, ownership, file intents, and
symbols where required.

`.stealth/` must remain a projection/cache, not a second canonical DB.

## B36 --- Multi-agent coordination

Verify node
ownership/objective/dependencies/status/read/write/symbol/lease fields.

Test exact write/write, write/read, glob, symbol, dependency, and
stale-lease conflicts. Conflicts must fail safely and identify the
conflict.

## B37 --- Global hierarchical retrieval

Verify Claims, Procedures, and Implementations all traverse the
hierarchical pipeline with canonical IDs, rebuildability,
revision/freshness metadata, stale behavior, bounded retrieval, and no
second knowledge store.

## B38 --- No synthetic/fallback execution

Systematically inspect production fallbacks and prove real failure
cause, honest failure state, no fake ID/embedding/tool
result/evidence/success/verification/applicability/Implementation/adapter.

Test every production fallback branch required by the specification.

------------------------------------------------------------------------

# 5. MCP surface and resources

Enumerate every registered MCP tool and verify exact name, input schema,
authorization, scope, persistence, idempotency, errors, output schema,
and side effects.

Pay particular attention to:

-   find_best_way
-   continue_run
-   get_relevant_claims
-   get_procedure
-   get_run_context
-   verify_completion
-   report_execution
-   submit_procedure
-   submit_implementation
-   generate_review_packet
-   submit_approval
-   decide_decomposition
-   propose_synthesis
-   resume/retry/inspection operations
-   problem/solution/evaluation/run tools

Verify resources:

-   `stealth://procedures/{id}`
-   `stealth://problems/{id}`
-   `stealth://problems/{id}/solutions`
-   `stealth://claims/{id}`
-   `stealth://evaluations/{id}`
-   `stealth://implementations/{id}`
-   `stealth://tasks/{id}/implementations`
-   `stealth://runs/{id}`

Resources must respect authorization/scope.

------------------------------------------------------------------------

# 6. Database and migrations

Test both:

### Fresh DB

`empty DB → all migrations → expected schema`

### Upgrade DB

Representative older schema/data → migration head.

Verify no data loss, constraints, indexes, foreign keys, triggers, state
checks, ordering, and intended idempotency.

Critical invariants must be DB-enforced where appropriate, not merely
application-validated.

------------------------------------------------------------------------

# 7. Concurrency and idempotency

Run concurrent tests for:

-   two workers claiming one node
-   two lease refreshes
-   duplicate result submissions
-   duplicate publication
-   duplicate admissions
-   duplicate bindings
-   duplicate child creation
-   identical request IDs
-   conflicting file intents
-   simultaneous verification

For every mutation test:

`once → twice → timeout/retry → process restart → concurrent repeat`

Verify no duplicate logical state, lost updates, privilege escalation,
or duplicate harmful execution.

------------------------------------------------------------------------

# 8. Failure injection

Inject failures into:

-   DB transactions
-   network calls
-   MCP calls
-   HTTP implementations
-   local subprocesses
-   artifact writes
-   verification
-   learning extraction
-   publication
-   index builds
-   `.stealth/` updates
-   process termination
-   lease expiry
-   parent/child transitions

Expected:

`no false success, no orphaned run, no lost lineage, no corrupted state, no privilege expansion, no accidental publication`

------------------------------------------------------------------------

# 9. Security

Treat security failures as P0/P1.

Test authentication: missing/invalid/expired tokens and identity
mismatches.

Test authorization: users, workspaces, organizations, repositories,
Implementations, Procedures, Claims, runs.

Test filesystem: traversal, absolute paths, symlink escape, unauthorized
workspace, unauthorized repository, arbitrary host paths.

Test SSRF/external implementations: loopback, private networks, metadata
endpoints, redirects, malicious URLs, DNS-rebinding considerations
according to policy.

Test MCP abuse: bypass approval, claim verification, access another
user's data, force global scope, inject evidence, skip lifecycle, bypass
Implementation resolution.

------------------------------------------------------------------------

# 10. Retrieval adversarial tests

Create exact, similar-but-wrong, obsolete, private, globally verified,
unverified, incompatible, disabled, and contradictory candidates.

Verify retrieval respects scope, applicability, evidence/status, and
does not select a merely similar Procedure or invent applicability.

------------------------------------------------------------------------

# 11. Verification adversarial tests

Attempt to make the system falsely declare success:

-   self-report says done but output is wrong
-   artifact exists but criterion is unmet
-   deterministic check fails
-   human approves wrong target
-   evidence belongs to another run
-   evidence belongs to another Procedure/version
-   verification belongs to an old Implementation version
-   contradictory evidence
-   omitted required criterion

Expected: no false VERIFIED / INDEPENDENTLY_VERIFIED / successful
terminal state.

------------------------------------------------------------------------

# 12. Performance and scale

Measure p50/p95/p99 for retrieval, find_best_way, plan_only,
verify_completion, report_execution, run creation, node claim, event
append, verification, finalization, recursive child creation, and
resume.

Benchmark representative dataset sizes, preferably:

-   1K Claims
-   10K
-   100K
-   1M if practical

and equivalent Procedure/Implementation scales.

Measure index build/refresh, memory, latency, candidate counts,
concurrency, and contention.

Do not invent SLOs. If no formal SLO exists, report measured results and
state that no formal SLO was provided.

------------------------------------------------------------------------

# 13. Synthetic corpus validation

Because real ingestion has not occurred, construct a synthetic but
structurally realistic corpus containing:

-   procedural documents
-   non-procedural documents
-   duplicates
-   near-duplicates
-   conflicting procedures
-   versions
-   scopes
-   implementations
-   invalid/malformed documents
-   long documents
-   multilingual data if supported
-   repeated procedures
-   stale candidates

Measure ingestion, extraction, admission, retrieval, and indexing.

Label every result:

**SYNTHETIC-CORPUS VALIDATION**

Never call it production-corpus validation.

------------------------------------------------------------------------

# 14. Property/invariant tests

Where practical, use property-based tests for:

1.  no successful run without required verification
2.  no private object returned to unauthorized caller
3.  no terminal child leaves parent permanently running
4.  no runtime dependency silently becomes canonical dependency
5.  no Procedure version changes mid-run
6.  no Implementation executes without valid resolution/binding
7.  no fabricated success from fallback
8.  idempotent requests do not duplicate logical state
9.  restart preserves durable state
10. index rebuild does not mutate canonical knowledge
11. publication never bypasses policy
12. unknown critical prerequisites never become satisfied by default

------------------------------------------------------------------------

# 15. Observability and operations

Verify operators can answer:

-   What is this run?
-   Who initiated it?
-   Which Procedure/version?
-   Which Implementation/version?
-   Why was it selected?
-   Which Claims influenced it?
-   What authorization/scope applied?
-   What happened to every node?
-   Which tools ran?
-   What evidence exists?
-   How was verification performed?
-   Was it independently verified?
-   Did execution deviate?
-   Did a child run occur?
-   Why did it fail?
-   Can it resume?
-   What knowledge was learned?
-   Is learned knowledge private?
-   Was anything published?
-   Which index revision served retrieval?

Verify trace IDs and parent/child IDs end-to-end.

------------------------------------------------------------------------

# 16. Privacy and retention

Verify logs/artifacts/transcripts do not unnecessarily become global,
secrets are not leaked through errors, private data remains private,
external responses are not accidentally published, and `.stealth/`
cannot become an accidental exfiltration surface.

------------------------------------------------------------------------

# 17. Golden end-to-end journeys

Astra MUST execute or reconstruct these.

### Journey 1 --- Pure retrieval

caller → Claim → Procedure → Implementation → scope verified → no
execution

### Journey 2 --- Planning

request → find_best_way → Procedure → Claims → applicability →
Implementation → plan → persisted plan → no execution

### Journey 3 --- Successful execution

request → route → pin → bind → execute → evidence → verification →
outcome → finalization

### Journey 4 --- Execution failure

request → real failure → evidence → classification →
retry/alternative/fail → durable terminal state

### Journey 5 --- Recursive execution

A → child B → B verifies → A resumes → A completes

### Journey 6 --- Recursive recovery

A → B → B fails → strategy → process death → resume → recovery

### Journey 7 --- Learning

execution → observations → Claims → Procedure candidate → private scope
→ admission

### Journey 8 --- Publication

private knowledge → explicit publication → gates → global candidate

### Journey 9 --- Human verification

run → bounded review packet → human review → evidence linkage →
verification → completion

### Journey 10 --- Multi-agent coordination

agent A claims node → file intent → agent B conflicts → B blocked → A
finishes → B proceeds

### Journey 11 --- Private isolation

User A creates private Procedure → A can retrieve → B cannot

### Journey 12 --- Restart

run → process kill → restart → resume → no duplicate completed work →
correct final state

------------------------------------------------------------------------

# 18. Full-system mega-chain

Build the strongest end-to-end scenario possible:

1.  source supplied
2.  Artifact created
3.  Observations extracted
4.  Claims created
5.  Procedure candidate created
6.  screening/admission
7.  Implementation resolved
8.  caller asks best way
9.  applicability checked
10. plan produced
11. execution begins
12. a node requires a child Procedure
13. child executes
14. child verifies
15. parent resumes
16. execution deviates from plan
17. deviation persists
18. verification runs
19. evidence persists
20. outcome persists
21. learning occurs
22. learned knowledge remains private
23. explicit publication requested
24. publication gates run
25. global retrieval finds published object
26. unauthorized caller cannot access private data
27. complete lineage is inspectable

Cross-check the database after the journey. Never accept response codes
alone.

------------------------------------------------------------------------

# 19. Test-suite quality audit

Do not only run tests; audit whether the tests are strong.

For important tests ask:

-   Does it execute real production code?
-   Does it assert persisted state?
-   Does it include negative behavior?
-   Could the implementation be broken while the test stays green?
-   Is a mock hiding the real adapter/service?
-   Is HTTP 200 being mistaken for semantic success?
-   Does it cover authorization?
-   concurrency?
-   restart?
-   failure?

Identify weak tests, uncovered production branches, and missing
negative/integration/security tests.

------------------------------------------------------------------------

# 20. Production scoring

For every requirement use:

-   **PASS** --- fully demonstrated
-   **PASS WITH LIMITATION** --- works and limitation is explicitly
    outside release scope
-   **PARTIAL** --- meaningful implementation but required behavior
    remains
-   **FAIL** --- broken
-   **NOT TESTABLE** --- environment prevents valid test
-   **NOT YET VALIDATED** --- especially real ingestion/corpus behavior

Do not turn PARTIAL/FAIL into PASS because the code is conceptually
close.

Severity:

### P0 --- release blocker

Examples: private-data exposure, arbitrary filesystem access,
unauthorized execution, false successful execution, fabricated
verification, corruption, broken migration, harmful duplicate execution,
unrecoverable durable state.

### P1 --- production blocker

Examples: lifecycle bypass, broken recursive recovery, wrong binding,
publication bypass, serious retrieval correctness issue, systematic
evidence loss.

### P2 --- significant defect

Bounded feature failures, major observability gaps, degraded
performance, incomplete secondary adapter behavior.

### P3 --- improvement

Convenience, diagnostics, noncritical optimization/documentation.

Any P0 means NOT PROD-LEVEL READY. A P1 affecting security/core
execution should normally also block production.

------------------------------------------------------------------------

# 21. Production-readiness gates

Answer each explicitly:

A. Correctness\
B. Security\
C. Provenance\
D. Verification\
E. Durability\
F. Concurrency\
G. Retrieval\
H. Learning\
I. Publication\
J. Operability\
K. Performance\
L. Real ingestion

Gate L should be **NOT YET VALIDATED** unless Astra finds evidence of a
real production ingestion run.

------------------------------------------------------------------------

# 22. Required final report

## Executive verdict

Use exactly:

``` text
CODE/PLATFORM: PROD-LEVEL READY / NOT PROD-LEVEL READY
SECURITY: PASS / FAIL
DATA/INGESTION: VALIDATED / NOT YET VALIDATED
OVERALL: PROD-LEVEL READY / NOT PROD-LEVEL READY
```

Then explain.

## System scorecard

  Domain                Status                            Evidence   Critical finding
  --------------------- --------------------------------- ---------- ------------------
  Part A ingestion                                                   
  Claims                                                             
  Procedures                                                         
  Implementations                                                    
  Retrieval                                                          
  MCP                                                                
  Execution                                                          
  Verification                                                       
  Recursive execution                                                
  Learning                                                           
  Publication                                                        
  Security                                                           
  Durability                                                         
  Concurrency                                                        
  `.stealth/`                                                        
  Migrations                                                         
  Observability                                                      
  Performance                                                        
  Real ingestion        NOT YET VALIDATED unless proven              

## B1--B38 matrix

For EVERY B item provide:

``` text
B#
Requirement
Implementation evidence
Tests executed
Negative tests
Persisted-state evidence
Security implications
Status
Defects
```

Do not group items in a way that hides an individual failure.

## Part A matrix

For every ingestion/knowledge subsystem:

``` text
component
requirement
test
result
evidence
status
```

## Golden journeys

For Journeys 1--12:

``` text
PASS / FAIL / PARTIAL
steps completed
database evidence
unexpected behavior
```

## Failure inventory

For every defect:

``` text
ID
Severity
Component
Exact failure
Reproduction
Impact
Likely cause
Recommended fix
Release blocking? YES/NO
```

## Test execution inventory

List exact commands and outcomes for:

-   unit tests
-   backend integration tests
-   MCP tests
-   packaging tests
-   migration tests
-   security tests
-   concurrency tests
-   crash/recovery tests
-   synthetic corpus tests
-   performance tests
-   property tests

Do not say "all tests pass" without listing what actually ran.

## Untested areas

Be explicit.

## Final decision

Use:

### GREEN

Production-ready for the tested scope.

### YELLOW

Core platform appears sound but specific blockers/validation gaps
remain.

### RED

Not production-ready.

### GREEN-CODE / YELLOW-DATA

Use this when platform/code is production-grade but real production
ingestion/corpus validation has not yet happened.

This distinction is expected to be important for this project.

------------------------------------------------------------------------

# 23. Astra operating rules

1.  Be adversarial.
2.  Assume hidden bugs exist until invariants are demonstrated.
3.  Prefer runtime evidence over static reasoning.
4.  Prefer persisted DB evidence over response codes.
5.  Prefer negative tests for security/lifecycle.
6.  Never invent missing signals.
7.  Never infer production readiness from CI alone.
8.  Never mark real ingestion validated without a real ingestion run.
9.  Do not modify code during this audit unless explicitly instructed.
10. Reproduce defects before declaring them.
11. If environment prevents testing, report NOT TESTABLE.
12. Distinguish implemented from proven.
13. Distinguish synthetic corpus validation from real corpus validation.
14. Do not silently weaken requirements.
15. Do not trust previous Claude/GPT audits as evidence.
16. Inspect actual implementation.
17. Inspect migrations and constraints.
18. Test race conditions.
19. Test failure paths.
20. Test security boundaries from an attacker's perspective.
21. Test the system as a whole.

## Final principle

The question is not:

> "Does StealthLab have all the features?"

The question is:

> "Can we trust this backend to serve as a production
> capability/provenance/execution layer for AI agents without silently
> exposing private knowledge, inventing capabilities, falsely claiming
> verification, losing execution state, corrupting canonical knowledge,
> or producing unrecoverable runtime state?"

Only answer PROD-LEVEL READY if the evidence supports it.

And remember:

**Real ingestion has not yet been performed.**

Therefore even an excellent code/platform result must not be represented
as proof that production ingestion and real-world corpus behavior are
validated.
