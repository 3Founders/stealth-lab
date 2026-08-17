You are the principal architect and implementation lead for this repository.

We are redesigning the existing agent/HTN backend into the first version of a general-purpose experiential memory and reusable procedural memory system, with an IDE/agentic-coding environment as the first concrete domain.

IMPORTANT:
- Do NOT immediately implement.
- First deeply inspect the existing repository and understand the architecture.
- Use Wayfinder to turn the problem into a decision map.
- We want architectural decisions grounded in the actual existing code, not a greenfield redesign detached from the current implementation.
- Preserve useful existing infrastructure wherever possible.
- Do not replace the HTN/DAG execution engine. Reposition it as the execution/planning layer downstream of procedural memory.
- Do not prematurely implement RDF/OWL or a heavyweight ontology.
- Do not prematurely implement a complete TMS.
- Do not prematurely implement mobile.
- The first milestone is an IDE/agentic-trace memory system built on a general memory substrate.

============================================================
MISSION
============================================================

Build the architectural foundation for:

RAW EXPERIENCE
    ↓
NORMALIZED AGENTIC TRACE
    ↓
EPISODE
    ↓
OBSERVATIONS
    ↓
CLAIMS / STATE
    ↓
PROCEDURE CANDIDATES
    ↓
VERIFIED PROCEDURES
    ↓
APPLICABILITY / LOCAL RETRIEVAL
    ↓
HTN / DAG INSTANTIATION
    ↓
EXECUTION
    ↓
EVIDENCE / OUTCOME
    ↓
PROCEDURE UPDATE / VERSION / INVALIDATION
    ↓
CLAIM/TMS UPDATE
    ↓
FUTURE REUSE

The core thesis is:

"Events are not memories. Claims are not procedures. Procedures are not agents. Traces are evidence-bearing experiences from which claims and procedures can be learned."

The system must eventually support:
1. ingesting agentic traces;
2. reconstructing meaningful episodes;
3. representing state before and after execution;
4. extracting durable claims;
5. mining reusable procedures;
6. verifying procedures through repeated successful execution;
7. reusing procedures conditionally;
8. updating/versioning procedures when evidence changes;
9. invalidating stale procedures when dependencies change;
10. maintaining provenance from durable knowledge back to raw traces/evidence;
11. retrieving LOCAL relevant memory rather than dumping global history;
12. eventually discovering abstract behavioral motifs across domains.

============================================================
FIRST PRINCIPLE: INSPECT BEFORE DESIGNING
============================================================

Before proposing changes, inspect the entire relevant backend.

Specifically find and understand:

- knowledge_nodes
- task_nodes
- edges
- episodes
- episode_links
- traces
- retrieval/search code
- embeddings
- lexical retrieval
- RRF/hybrid retrieval
- hierarchical retrieval
- subtask reuse
- reusable method/procedure library
- HTN planner
- DAG representation
- executor
- replanning
- evidence capture
- node telemetry
- preconditions
- postconditions
- agent store
- agent review/runnable state
- API routes
- database migrations
- repositories/data access layer
- schemas/models
- background jobs/queues
- authentication/ownership/visibility
- tests
- existing telemetry
- existing serialization formats

Search for all current references to:
- episode
- trace
- task_node
- knowledge_node
- edge
- method
- reusable
- reuse
- evidence
- provenance
- precondition
- postcondition
- telemetry
- retrieval
- RRF
- embedding
- hierarchical
- subtask
- agent
- execution
- replan

Do not assume names from this specification exist exactly as stated.

Produce an architecture inventory before making changes.

============================================================
EXISTING ARCHITECTURE MUST BE PRESERVED WHERE APPROPRIATE
============================================================

The current architecture already contains valuable concepts.

Treat these as likely reusable:

- episodes as raw/non-lossy experience
- traces as structured execution information
- knowledge_nodes as an existing graph primitive
- polymorphic edges
- temporal validity
- provenance
- embeddings
- lexical retrieval
- RRF/hybrid retrieval
- hierarchical/local dependency retrieval
- reusable subtask/procedure detection
- task_nodes as execution-time planning nodes
- HTN/DAG execution
- preconditions/postconditions
- replanning
- evidence
- node telemetry
- agent lifecycle/review/runnable state

Do not rewrite these simply because a greenfield design would use different names.

Instead determine:
- what should remain;
- what should be generalized;
- what should become a compatibility layer;
- what should become first-class;
- what should be deprecated;
- what should be migrated.

============================================================
TARGET SEMANTIC SEPARATION
============================================================

The final architecture MUST preserve these distinctions:

EVENT:
    What literally happened.

OBSERVATION:
    A semantic interpretation of one or more events.

EPISODE:
    A coherent bounded experience/task consisting of intent, state, events, actions, artifacts, and outcome.

CLAIM:
    What the system currently believes about the world/user/environment.

STATE:
    Time-sensitive claims representing the current situation.

PROCEDURE:
    A reusable conditional capability describing how to move from an input state toward an intended outcome.

PROCEDURE EXECUTION:
    A concrete instantiation/execution of a procedure against a specific state.

TRACE:
    The detailed causal execution history.

EVIDENCE:
    Artifacts/observations/results supporting or contradicting claims or procedures.

AGENT:
    An executable/authorized capability that may instantiate a procedure.

TASK NODE:
    A concrete execution/planning node in one run.

HTN/DAG:
    An execution-time decomposition/coordination structure.

MOTIF:
    An abstract structural pattern shared by multiple concrete procedures; initially non-executable.

TMS:
    The future mechanism that propagates additions/retractions/invalidations through justifications.

Do NOT collapse these concepts into one generic "memory node."

============================================================
AGENTIC TRACE INGESTION
============================================================

Design a provider-independent canonical trace format.

Claude Code is the first adapter.

The canonical trace must support at minimum:

Identity:
- trace_id
- episode_id
- session_id
- parent_trace_id
- parent_event_id
- agent_id
- actor_id
- provider
- provider_version

Intent:
- user_goal
- task description
- constraints
- requested autonomy
- task identifiers where available

Environment:
- cwd
- repository identity
- repository root
- branch
- commit SHA
- dirty/clean state if available
- relevant environment/runtime versions
- application/editor identity
- OS/device identity where appropriate and privacy-safe

Events:
- sequence number
- timestamp
- event type
- actor
- tool
- tool call ID
- parent tool/agent relationship
- tool input
- tool output
- success/failure
- duration
- permission state
- error information
- provenance
- raw provider payload reference

Agent events:
- model invocation
- user prompt
- assistant turn
- tool invocation
- tool result
- tool failure
- retry
- subagent start
- subagent stop
- task created
- task completed
- permission request/denial
- compaction
- session start/end

Artifacts:
- file paths
- git diffs
- commits
- test outputs
- build outputs
- generated artifacts
- issue/PR references
- hashes
- storage references

State:
- state_before
- state_after
- state_delta

Outcome:
- success
- failure
- partial success
- abandoned
- blocked
- unknown

Evidence:
- evidence IDs
- evidence type
- provenance
- supports/contradicts relation

The raw provider payload must remain recoverable where privacy policy permits.

Do not make the normalized schema provider-specific.

============================================================
CLAUDE CODE ADAPTER
============================================================

Design a Claude Code ingestion adapter.

Claude Code currently exposes lifecycle/tool hook events including:
- SessionStart
- UserPromptSubmit
- PreToolUse
- PostToolUse
- PostToolUseFailure
- PostToolBatch
- SubagentStart
- SubagentStop
- TaskCreated
- TaskCompleted
- PreCompact
- PostCompact
- SessionEnd
and related lifecycle events.

Use the actual current Claude Code hook schema after inspecting official documentation if needed.

The adapter should NOT write directly into semantic memory.

Preferred architecture:

Claude Code hook
    ↓
local HTTP/event collector
    ↓
raw event persistence
    ↓
normalization
    ↓
episode assembler
    ↓
semantic memory compiler

Support:
- retries
- idempotency
- duplicate event handling
- ordering
- missing events
- late events
- provider version changes
- schema versioning

Do not make memory correctness depend on hooks firing perfectly.

The raw trace should be recoverable/replayable.

============================================================
EPISODE ASSEMBLY
============================================================

Create a concept/component that converts raw agent events into coherent episodes.

An episode should have:

- episode_id
- owner/user
- session(s)
- intent
- start/end time
- environment snapshot
- participating agents
- relevant entities
- event/trace references
- artifacts
- state_before
- state_after
- outcome
- evidence
- episode status

Episode boundaries may use:
- explicit task lifecycle
- user prompt
- agent task
- subagent hierarchy
- git commit
- PR
- test completion
- session boundaries
- temporal proximity
- shared repository/files/entities
- semantic goal similarity

Do not use one simplistic timeout rule.

The design should support deterministic boundaries plus later semantic segmentation.

============================================================
STATE MODEL
============================================================

Add first-class support for state snapshots/deltas.

Every meaningful episode should attempt to represent:

S_before
    ↓
execution
    ↓
S_after

For coding, state may include:
- repository
- branch
- commit
- working tree status
- relevant files
- symbols
- tests
- build status
- dependency state
- issue/task state

Do NOT snapshot the entire world on every event.

Use:
- relevant local state
- references to immutable artifacts
- deltas
- hashes
- temporal validity

The architecture must eventually support mobile/personal state, so avoid hardcoding state to code repositories.

============================================================
OBSERVATION LAYER
============================================================

Introduce an observation layer between raw events and claims.

Raw event:
    Edit file X

Observation:
    Authentication implementation was modified

Claim:
    User commonly validates authentication changes immediately after editing

Observations MUST retain:
- source event IDs
- episode ID
- extractor/model
- confidence
- timestamp
- provenance
- extraction version

Observations are NOT automatically facts.

============================================================
CLAIM GRAPH
============================================================

Generalize the existing graph architecture to support first-class claims.

A claim should support:

- claim_id
- owner
- subject
- predicate
- object/value
- claim type
- status
- confidence
- temporal validity
- provenance
- created_at
- invalidated_at
- extraction/version metadata

Claims must support:
- supports
- contradicts
- derived_from
- depends_on
- supersedes
- valid_during
- observed_at

Do not over-normalize if the existing knowledge_nodes/edges architecture can represent this cleanly.

Determine whether:
- claim is a node type;
- claim is a dedicated table;
- claim uses existing knowledge_nodes;
- or a hybrid is best.

Explain the tradeoff before implementing.

============================================================
PROVENANCE / JUSTIFICATION
============================================================

A claim must answer:

"Why do we believe this?"

Example:

Claim C17:
    User runs tests after meaningful code edits.

Supported by:
    Observation O41
    Observation O52
    Observation O61

Contradicted by:
    Observation O88

The system must preserve this graph.

Do NOT reduce justification to a scalar confidence score.

Confidence can be derived from evidence, but evidence/justification is canonical.

This is the foundation for a future TMS.

============================================================
PROCEDURE MODEL
============================================================

Introduce a first-class procedure abstraction.

A procedure is NOT a previous trajectory.

It should contain:

- procedure_id
- family_id if applicable
- name
- goal
- parameter schema
- preconditions
- required state
- actions/steps
- expected effects
- postconditions
- invariants
- failure conditions
- scope
- exclusions
- version
- lifecycle status
- verification statistics
- evidence references
- source episodes
- provenance
- created_at
- updated_at

Procedures must be parameterized.

BAD:
    edit auth/middleware.py
    run pytest tests/auth.py

GOOD:
    edit(target_module)
    run(target_tests)

Then instantiate parameters from the current task/state.

============================================================
PROCEDURE APPLICABILITY
============================================================

Reuse must NOT be based solely on semantic similarity.

Define applicability as a combination of:

1. explicit preconditions;
2. current state;
3. scope;
4. exclusions;
5. temporal validity;
6. environment compatibility;
7. procedure verification status;
8. semantic similarity;
9. relevant local graph neighborhood.

Conceptually:

applicability(P, S_current)

must be evaluated before reuse.

Embeddings find analogues.

The graph and state determine relevance.

============================================================
LOCALITY
============================================================

Implement local-first retrieval.

The current task should identify a local epistemic neighborhood.

For an IDE this may include:
- current repository
- branch
- current files
- current symbols
- dependencies
- recent commits
- recent failures
- related tests
- recent procedure executions
- relevant claims
- relevant artifacts

Retrieval hierarchy:

1. structural locality
2. temporal locality
3. causal/graph locality
4. semantic retrieval
5. reranking

Do not dump global memory into the model.

As memory grows, the relevant model context should remain approximately local.

============================================================
PROCEDURE EXECUTION / EVIDENCE
============================================================

Every reuse must produce an execution record.

Procedure execution must record:

- procedure version
- instantiated parameters
- initial state
- concrete plan/HTN
- actual actions
- actual trace
- final state
- outcome
- evidence
- failures
- deviations from procedure
- duration/cost
- model/tool versions

This is what makes a procedure empirically verifiable.

============================================================
PROCEDURE LIFECYCLE
============================================================

Do not use a simple boolean "verified."

Use lifecycle states such as:

CANDIDATE
    ↓
VERIFIED
    ↓
STALE
    ↓
REVALIDATED → VERIFIED
or
RETIRED

Failures must be classified:

1. transient/contextual failure
   - do not necessarily modify procedure

2. precondition violation
   - procedure may remain valid

3. scope violation
   - narrow scope or exclusions

4. environment/dependency change
   - procedure may become stale

5. structural/procedural failure
   - procedure needs revision

6. ambiguous failure
   - do not automatically mutate durable memory

Do not automatically rewrite a verified procedure after one failure.

============================================================
PROCEDURE VERSIONING
============================================================

Never silently overwrite verified procedures.

Support:

P17 v1
    ↓ superseded by
P17 v2

Retain:
- prior version
- reason for revision
- evidence triggering revision
- source execution
- validity interval
- migration relationship

If a dependency changes, mark the relevant procedure STALE rather than deleting it.

Revalidation should be an explicit process.

============================================================
REVALIDATION
============================================================

Design a revalidation mechanism:

procedure
    ↓
check preconditions
    ↓
representative execution
    ↓
collect evidence
    ↓
compare expected effects
    ↓
pass → verified new version
fail → revise/retire

Do not implement autonomous revalidation blindly.

Initially expose it as an internal operation/test harness.

============================================================
REUSE FEEDBACK LOOP
============================================================

Every reuse must feed evidence back into memory.

Conceptually:

retrieve procedure
    ↓
check applicability
    ↓
instantiate
    ↓
execute
    ↓
observe outcome
    ↓
update execution evidence
    ↓
update reliability
    ↓
possibly update scope
    ↓
possibly create candidate revision
    ↓
future reuse

This is the core learning loop.

============================================================
PROCEDURE FAMILIES
============================================================

Support a distinction between:

abstract procedure family:
    locate → inspect → modify → validate → iterate

and concrete procedures:
    Python/pytest debugging
    JavaScript/Jest debugging
    invoice reconciliation
    spreadsheet correction

The family captures invariant structure.

The concrete procedure captures domain-specific realization.

Do NOT make abstract motifs directly executable initially.

============================================================
BEHAVIORAL MOTIFS
============================================================

Later support an abstract motif layer.

Example:

IDE:
    edit → test → inspect → correct → test

Billing:
    modify invoice → calculate → inspect discrepancy → correct → calculate

Spreadsheet:
    modify formula → recalculate → inspect → correct → recalculate

Candidate motif:
    ITERATIVE_VERIFICATION

Motifs must initially be hypotheses, not durable user facts.

Require:
- supporting episodes
- contradiction counts
- confidence
- domains
- provenance
- optional user confirmation

Do not silently infer sensitive personal characteristics.

============================================================
TMS PREPARATION
============================================================

Do not implement a complete TMS in the first milestone.

But design every claim/procedure relationship so future retraction is possible.

Example:

C1:
pytest is test framework
    ↓ supports
P17:
run pytest
    ↓ used by
P17 execution
    ↓ supports
C2:
tests pass

If C1 becomes invalid:
    C1 invalidated
        ↓
    P17 affected
        ↓
    P17 marked stale
        ↓
    dependent claims/effects reconsidered

The architecture must make this dependency graph explicit.

============================================================
SECURITY / PRIVACY
============================================================

Agentic traces may contain extremely sensitive information.

Design for:
- owner isolation
- project isolation
- explicit visibility
- encryption where appropriate
- secret redaction
- credential/token redaction
- configurable path exclusion
- configurable tool exclusion
- configurable event sampling
- retention policy
- deletion
- provenance-preserving deletion semantics
- no cross-user leakage
- local-first deployment compatibility

Do NOT send raw traces to an external LLM by default.

Semantic extraction should be configurable.

Prefer local deterministic processing first.

============================================================
PERFORMANCE
============================================================

Do not make every tool call trigger an expensive LLM call.

Pipeline:

raw events
    ↓
cheap normalization
    ↓
episode aggregation
    ↓
meaningful segment detection
    ↓
semantic extraction only when needed

Support:
- asynchronous processing
- batching
- deduplication
- idempotency
- backpressure
- replay
- eventual consistency

The synchronous agent execution path must not become dependent on the memory compiler.

Memory ingestion should be able to lag behind execution.

============================================================
REPLAYABILITY
============================================================

A major design requirement:

Given a raw trace, we should be able to replay the ingestion pipeline and regenerate:

- normalized events
- episode
- observations
- claims
- procedure candidates

Version all extraction/normalization logic.

This is critical for research.

We must be able to say:

"Claim C17 was produced by extractor version X from trace E42."

============================================================
MIGRATION
============================================================

Do not destroy existing data.

Design a migration strategy from:

existing episodes
existing traces
existing knowledge_nodes
existing task_nodes
existing reusable methods
existing edges

into the new architecture.

If an existing object can map cleanly, preserve its ID where safe.

If not, create explicit compatibility links.

Existing reusable plans should become:
- candidate procedures
or
- legacy procedure records

rather than silently disappearing.

============================================================
API DESIGN
============================================================

Design APIs for:

POST /traces/events
POST /traces/batch
GET /traces/:id
GET /episodes/:id

POST /memory/compile/:episode_id

GET /claims
GET /claims/:id
GET /claims/:id/evidence

GET /procedures
GET /procedures/:id
POST /procedures/:id/revalidate
POST /procedures/:id/retire

POST /procedures/:id/execute
GET /procedures/:id/executions

POST /memory/retrieve-local

Exact paths are not mandatory; integrate with existing API conventions.

============================================================
TESTING
============================================================

Create tests for:

Trace ingestion:
- ordering
- duplicates
- missing events
- late events
- retries
- provider schema changes

Episode assembly:
- same-task grouping
- separate-task boundaries
- subagent grouping
- git commit boundaries

State:
- before/after
- deltas
- missing state
- partial state

Claims:
- support
- contradiction
- provenance
- temporal validity
- invalidation

Procedures:
- candidate creation
- applicability
- parameter binding
- successful verification
- failure classification
- stale marking
- versioning
- revalidation
- retirement

Retrieval:
- local > global relevance
- structural filtering
- temporal filtering
- semantic fallback
- no context explosion

Replay:
- deterministic normalization
- versioned extraction

Privacy:
- secrets
- cross-owner isolation
- excluded paths/tools

============================================================
BENCHMARK / EXPERIMENT
============================================================

Build the architecture so we can evaluate:

A:
No procedural memory.

B:
Global procedural memory.

C:
Local procedural memory.

D:
Local procedural memory + abstract motifs.

Metrics:
- task success
- first-attempt success
- replanning rate
- tokens
- tool calls
- latency
- procedure reuse rate
- procedure verification rate
- procedure transfer rate
- stale-procedure rate
- false reuse rate
- memory retrieval precision
- context size

The key hypothesis:

"Relevant local procedural memory should outperform indiscriminate global memory, and abstracted procedures/motifs may enable cross-context transfer."

Do not build a fake benchmark just to produce positive results.

Build instrumentation so the experiment can be run honestly.

============================================================
WAYFINDER PROCESS
============================================================

Before implementation:

1. Install/use Wayfinder.
2. Inspect the repository deeply.
3. Create a map of architectural decisions.
4. Identify uncertainties and unknowns.
5. Turn each meaningful uncertainty into a decision ticket.
6. Resolve decisions one at a time.
7. Use research tickets for external questions.
8. Use prototype tickets for uncertain technical design choices.
9. Use grilling tickets for architectural challenges/risks.
10. Keep a "Not yet specified" section for issues that are visible but not sufficiently understood.
11. Do not implement merely because a ticket exists.
12. Stop planning when the architecture is sufficiently specified to implement without major unknowns.

Use the existing codebase as evidence for decisions.

============================================================
FIRST DELIVERABLE
============================================================

DO NOT CODE YET.

First return:

1. Repository architecture map.
2. Current data model map.
3. Current execution flow.
4. Current memory/reuse flow.
5. Current retrieval flow.
6. Current evidence/provenance flow.
7. Current HTN/DAG flow.
8. Current API flow.
9. Current test coverage.
10. Exact gaps relative to the target architecture.
11. Proposed minimal architecture.
12. Migration strategy.
13. Risks and failure modes.
14. Wayfinder decision map.
15. Recommended order of implementation.

For every proposed change:
- cite the exact existing files/classes/functions involved;
- explain what is reused;
- explain what changes;
- explain why;
- identify backwards compatibility implications.

Do not invent existing functionality.

============================================================
ARCHITECTURAL STANDARD
============================================================

Optimize for:

- correctness over novelty
- provenance over opaque confidence
- local relevance over global context
- evidence over assertions
- versioning over mutation
- replayability over convenience
- deterministic normalization over unnecessary LLM calls
- modularity over premature abstraction
- provider independence
- privacy
- backwards compatibility
- researchability
- measurable improvement

The system should eventually make this loop possible:

EXPERIENCE
    ↓
TRACE
    ↓
EPISODE
    ↓
OBSERVATION
    ↓
CLAIM
    ↓
PROCEDURE
    ↓
VERIFICATION
    ↓
REUSE
    ↓
NEW EXPERIENCE
    ↓
EVIDENCE
    ↓
UPDATE
    ↓
BETTER PROCEDURE

That loop is the product/research core.

Do not lose that objective while making implementation decisions.