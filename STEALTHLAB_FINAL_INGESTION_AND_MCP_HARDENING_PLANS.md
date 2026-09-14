# StealthLab — Final Ingestion Repair & MCP Hardening Plans

**Repository:** `3Founders/stealth-lab`  
**Audited revision:** `main` at `301b9bf3f0de4cf0d51dbd25e8761f62d3a89498`  
**Primary schema:** `schema.md` / Verified Procedural Experience System schema

## Executive decision

The current code has substantial ingestion and MCP infrastructure, but the dominant ingestion paths do **not yet enforce the canonical schema end-to-end**.

The two final workstreams are:

1. **Fix ingestion:** make local and global ingestion follow one logical ontology and preserve explicit scope/storage boundaries.
2. **Harden MCP:** make Stealth a server-enforced procedural execution/provenance boundary, fix `auto`, support recursive child executions, and automatically persist complete reporting/evidence lineage.



### Non-negotiable rules

- One logical knowledge model; local/global are scope and storage modes, not different meanings.
- Private knowledge never becomes global implicitly.
- Every procedure remains an independent searchable object.
- Parent/child nesting belongs to the **execution graph**, not procedure identity.
- Missing evidence is represented as missing evidence; it is never fabricated.
- Do not build second registries, provenance graphs, evidence stores, or task schedulers





- # Part II — Plan A: Fix Ingestion



## A0 — Freeze the canonical ingestion contract

Every ingestion source must enter through:

```text
Source → Artifact
       → Event/Trace/Episode where applicable
       → Observation
       → Claim
       → ClaimFamily where applicable
       → Evidence
       → Procedure candidate
```

Execution-derived sources additionally produce:

```text
ExecutionPlan → Execution → Outcome
```

A source that lacks execution evidence must not pretend to have execution evidence.

---

## A1 — Create `IngestionContext`

Every ingestion gets:

```yaml
ingestion_id:
source_id:
source_type:
source_uri:
source_hash:
actor_id:
workspace_id:
scope:
environment_id:
extractor_id:
extractor_version:
classification:
started_at:
completed_at:
```

Every derived object must be traceable to it.

Acceptance test: any procedure can answer **where it came from, who/what produced it, under what scope, and what evidence supports it**.

---



## A2 — Normalize Source + Artifact

Make Source and Artifact the universal source boundary.

Examples:

- SKILL.md
- AGENTS.md
- CLAUDE.md
- runbook
- CI configuration
- public document
- API result
- benchmark
- execution artifact

Artifacts are immutable. Improved extraction creates new Observations, not modified Artifacts.

---

## A3 — Make Event / Trace / Episode explicit for trace and agent runtime ingestion

Execution-derived ingestion:

```text
Event → Trace → Episode
```

Static-document ingestion:

```text
Artifact → document-ingestion Episode
```

Do not fabricate runtime Events where none exist.

---

## A4 — Canonical Observation extraction

The current parser can remain the parsing layer, but semantic output first becomes Observations.

Example:

```yaml
Observation:
  statement: "The source states that workflow X requires Y."
  interpretation:
    type: prerequisite
    subject: workflow-X
  source_events: [...]
  scope: ...
  confidence: ...
  created_by: skill_md_extractor@version
  observed_at: ...
```

Prose remains prose unless a real structured source supports a formal predicate.

---



## A5 — Claim derivation

Normalize Observations into Claims.

Example:

```text
Observation: source states Y is required
        ↓
Claim: workflow X requires Y
```

Claims must retain version, validity, status, belief assessment, provenance, dependencies, and permissions.

Extraction does not equal truth.

---

## A6 — ClaimFamily

Every normalized Claim should participate in family candidate generation:

```text
same_family
related_family
generalizes
specializes
```

Never delete the source claims because they belong to one family. Evidence remains attached to its original claim.

---

## A7 — Evidence

Every procedure candidate gets explicit evidence.

Static source:

```text
Evidence.type = document
```

Execution:

```text
Evidence.type = execution_result
```

Human review:

```text
Evidence.type = human_review
```

Independence must be recorded. Repeated copies of one source do not become independent evidence.

---



## A8 — Refactor global SKILL.md ingestion

Replace:

```text
SKILL.md → capture_procedure()
```

with:

```text
SKILL.md
 → Source
 → Artifact
 → Observations
 → Claims
 → ClaimFamilies
 → Evidence(document)
 → Procedure candidate
```

Keep the existing parser, injection screening, capability abstraction, retrieval-document construction, and novelty logic where valid. Change the persistence boundary, not the useful parsing work.

---



## A9 — Procedure admission

Every candidate must have:

```text
goal
inputs
required_state
preconditions
steps
branches
required_capabilities
required_tools
expected_effects
postconditions
verification definition
known failures
failure conditions
cost
verification state
staleness
availability
approval status
evidence refs
capability statement
extractor provenance
scope
```

Never mark a procedure `verified`, `trusted`, `safe`, or `authorized` merely because a source document says so.

---



## A10 — Separate static-source and execution verification

Static source:

```text
candidate
```

unless independent evidence supports a higher state.

Execution-derived:

```text
Execution
 → Outcome
 → Evidence
 → capability/verification update
```

Procedure definition and proof that it works remain separate.

---



## A11 — Make local learning schema-aligned.

[the [claim.md](http://claim.md) must be modified for this as well]

---



## A12 — Local storage specification

Fill the things below about what you are planning

### Local machine



### Stealth cloud

---



## A13 — Local → cloud synchronization

Implement an explicit sync protocol:

```text
local object
 → classification/policy
 → sync eligibility
 → USER_PRIVATE cloud object
```

Requirements:

- idempotent;
- content-hash based;
- version-aware;
- scope-preserving;
- resumable;
- auditable.

Sync must never silently convert private data into global data.

---



## A14 — Global publication

Final publication:

```text
USER_PRIVATE
 → explicit publish
 → dependency traversal
 → privacy/confidentiality/IP/license checks
 → sanitization
 → GLOBAL CANDIDATE
 → independent executions
 → GLOBAL VERIFIED
```

Traverse:

```text
Procedure
 → version
 → implementation
 → claims
 → observations
 → sources
 → artifacts
 → evidence
 → execution lineage
```

Private evidence does not become global verification automatically.

---



## A15 — Independent procedures

If A invokes B:

```text
Knowledge:
  Procedure A
  Procedure B

Execution:
  Execution A
    └── Execution B
```

A and B remain independently searchable, versioned, applicable, and verifiable.

Runtime invocation alone must not create canonical `A depends_on B`.

---



## A16 — Implementations

Executable procedures must bind a first-class Implementation:

```text
Procedure → Implementation
```

A knowledge candidate can exist without one, but execution cannot silently invent one.

---



## A17 — Golden ingestion tests



### Global

Assert:

```text
Source exists
Artifact exists
Observation exists
Claim exists
ClaimFamily exists where justified
Evidence exists
Procedure exists
Procedure references evidence
Procedure has scope/provenance
Procedure is candidate unless independently verified
```



### Local

Assert:

```text
Event exists
Trace exists
Episode exists
Artifact exists
Observation exists
Claim exists
Evidence exists
Private Procedure exists
Owner can retrieve it
Other user cannot
Global search cannot retrieve it
```



### Publication

Assert:

```text
explicit publish
 → policy checks
 → Global Candidate
```

and:

```text
private evidence ≠ global verification
```

---



## A18 — Ingestion implementation order

```text
A0  ontology contract
A1  IngestionContext
A2  Source/Artifact
A3  Event/Trace/Episode
A4  Observations
A5  Claims
A6  ClaimFamilies
A7  Evidence
A8  global SKILL refactor
A9  procedure admission/verification
A10 local schema-aligned learning
A11 local storage + sync
A12 publication hardening
A13 implementation binding
A14 golden E2E
A15 corpus migration/backfill
A16 remove/quarantine legacy shortcuts
```



### Definition of done

No production ingestion path can create a reusable procedure while bypassing canonical scope, provenance, and evidence rules.

---



# Part III — Plan B: MCP Hardening

---



## B1 — Fix `auto`

Current behavior is too simplistic:

```text
strong procedure → assistance
no match + repo → full_run
```

Final:

```text
find_best_way(auto)
 → task intent
 + procedure quality
 + applicability
 + environment
 + authorization
 → assist | plan | execute | ask | refuse
```

Examples:


| Intent                       | Route                |
| ---------------------------- | -------------------- |
| "What is the best way to X?" | assist               |
| "How should I do X?"         | assist               |
| "Give me a concrete plan"    | plan                 |
| "Plan the fix"               | plan                 |
| "Do X in this repository"    | execute              |
| "Fix this bug"               | execute              |
| "Execute procedure P"        | execute              |
| "Is P applicable?"           | assist/applicability |
| Unsafe/unauthorized          | refuse               |
| Uncertain applicability      | ask/plan             |


Keep explicit:

```text
lookup_only
plan_only
full_run
auto
```

---



## B2 — `RouteDecision`

Persist:

```yaml
RouteDecision:
  route: assist | plan | execute | ask | refuse
  reason:
  confidence:
  procedure_candidate:
  applicability:
  environment:
  authorization:
  requires_repository:
  requires_confirmation:
```

Routing becomes observable and testable.

---



## B3 — `StealthExecutionContext`

Every real execution gets:

```yaml
stealth_run_id:
task_id:
caller_identity:
workspace_id:
scope:
procedure_id:
procedure_version:
implementation_id:
execution_plan_id:
task_graph_id:
trace_id:
parent_run_id:
parent_node_id:
status:
```

This is the runtime backbone.

---



## B4 — Stealth Execution Contract

The server enforces:

```text
TASK_CREATED
 → DISCOVERY
 → PROCEDURE_EVALUATED
 → APPLICABILITY_CHECKED
 → PROCEDURE_VERSION_PINNED
 → IMPLEMENTATION_PINNED
 → EXECUTION_STARTED
 → EXECUTION_EVENTS
 → VERIFICATION
 → OUTCOME
 → EVIDENCE
 → FINALIZED
```

A run cannot claim Stealth procedural provenance without this chain.

---



## B5 — Do not try to intercept every Claude action

MCP availability does not force Claude to call an MCP tool.

Do not build a brittle interception proxy.

Use two modes:

### Advisory

Claude can retrieve/check/inspect and then act normally.

### Stealth execution

Once a Stealth execution is entered:

```text
server owns identity
server owns lifecycle
server owns provenance
server owns terminal state
server records evidence
```

Correct product claim:

> Every execution claiming Stealth procedural provenance passes through the server-enforced Stealth execution lifecycle.

Not:

> Every Claude action always uses Stealth.

---



## B6 — Plan-only execution lease

For Claude-managed execution:

```text
plan
 → execution lease
 → Claude executes
 → report_execution
 → server validates
 → evidence/outcome finalized
```

Report:

```text
execution_id
procedure/version
implementation
planned steps
actual steps
tool evidence
artifact references
verification
outcome
deviations
```

A model self-report alone is not objective verification.

---



## B7 — Unified ExecutionRecorder

Use one recorder over the existing execution/evidence substrate:

```text
start_run()
append_event()
record_node_transition()
record_child_run()
record_artifact()
record_verification()
record_outcome()
finalize_run()
```

No second DB.

---



## B8 — Durable event types

At minimum:

```text
run_started
route_decided
procedure_retrieved
applicability_checked
plan_created
implementation_bound
node_started
tool_called
tool_result
knowledge_requested
child_run_created
node_waiting
child_run_completed
node_resumed
verification_started
verification_completed
run_failed
run_finalized
```

Large data is stored as artifact references/hashes.

---



## B9 — Recursive procedure execution

Final behavior:

```text
Execution A
 └── Procedure A:v3
      └── node 3
           └── NEEDS_KNOWLEDGE
                → MCP retrieval
                → Procedure B:v2
                → Execution B
                → verification
                → B terminal
                → resume A
```

A and B remain independent procedures.

---



## B10 — Child execution lifecycle

Parent:

```text
RUNNING
 → WAITING_CHILD
```

Child:

```text
CREATED
 → RUNNING
 → SUCCEEDED | FAILED | RETRYABLE | ABORTED
```

Parent:

```text
child terminal
 → RESUME
 → continue
```

Persist:

```text
parent_run_id
parent_node_id
child_run_id
child_procedure_id
child_procedure_version
```

---



## B11 — Recursive failure semantics

If B fails:

```text
B FAILED
 → A receives structured outcome
 → retry B
   OR search alternative
   OR branch
   OR ask user
   OR fail A
```

Never leave the parent permanently RUNNING after a terminal child.

---



## B12 — Cycle protection

Prevent:

```text
A → B → C → A
```

with:

- ancestor-chain detection;
- maximum recursion depth;
- maximum child executions;
- time budget;
- token budget;
- execution-cost budget;
- tool budget;
- idempotency keys.

---



## B13 — Dynamic child execution around the existing TaskGraph

Do not replace the existing task engine.

Add:

```text
TaskNode
 → needs knowledge
 → create ChildExecution
 → WAITING_CHILD
 → child terminal
 → resume TaskNode
```

The child gets its own ExecutionPlan/TaskGraph/Execution/Trace/Outcome/Evidence where applicable.

---



## B14 — Re-search during execution

Claude must be able to call MCP again:

```text
A step
 → needs knowledge
 → search_procedures()
 → get_procedure(B)
 → check_applicability(B)
 → execute B
 → B completes
 → resume A
```

The selected B version is pinned to the child execution.

No duplicate Procedure B is created merely because it was retrieved.

---



## B15 — Relationship separation

Keep three distinct concepts:

### Runtime

```text
Execution A → Execution B
```



### Observed runtime relationship

```text
A:v3 invoked B:v2 during execution R
```



### Canonical dependency

```text
A:v3 depends_on B:v2
```

Only create the third when the procedure definition itself requires B.

---



## B16 — Automatic reporting

Every Stealth execution must terminate with:

```text
terminal state
+ outcome
+ verification
+ evidence
+ trace reference
```

Successful execution without persisted terminal reporting is a system failure.

Failures retain:

```text
failure class
retryability
current node
child state
error/artifact evidence
```

---



## B17 — Planned vs actual execution

Persist separately:

```text
planned procedure
planned implementation
planned steps
```

and:

```text
actual procedure/version
actual implementation
actual steps
actual tools
actual artifacts
actual outcome
```

This enables adherence, deviation, false-reuse, and failure analysis.

---



## B18 — MCP → learning loop

After execution:

```text
Execution
 → Trace
 → Episode
 → Observations
 → Claims
 → ClaimFamilies
 → Evidence
 → procedure learning
```

If an existing procedure was reused, do not automatically duplicate it.

If genuinely new repeatable behavior was discovered, create an independent candidate.

---



## B19 — Private execution

Private execution remains:

```text
scope = USER_PRIVATE
```

Publishing is explicit:

```text
USER_PRIVATE
 → publish
 → policy/dependency checks
 → sanitized Global Candidate
```

Global verification requires independent evidence.

---



## B20 — Recovery requirements

Mandatory E2E:

```text
A succeeds
B transiently fails
failure persists
process dies
resume
A is NOT rerun
B retries
B succeeds
C executes
verification runs
final state persists
lineage intact
```

Repeat with:

```text
A → child B → child C
```

and process loss at every level.

---



## B21 — Authorization/security

Local/loopback:

- bind to loopback;
- require configured HTTP authentication;
- preserve caller identity;
- restrict filesystem access through controlled workspace/sandbox.

Hosted execution:

```text
authenticated principal
 → organization
 → authorized repository
 → authorized workspace
 → sandbox
```

Never treat an arbitrary host filesystem path as hosted execution authorization.

Currently, there's only general compute, we want the user as well to be able to add api keys of his to this, if not, and we also want our own platform as well to decide, which task can be done deterministically, by SLM, or by an LLM and then call the relevant model using say general compute 

---



## B22 — Definitive MCP E2E

```text
Claude
 → find_best_way(auto)
 → auto chooses execute
 → A retrieved
 → applicability checked
 → A starts
 → A requests knowledge
 → MCP searches
 → B retrieved
 → B applicability checked
 → child B starts
 → B verifies
 → A resumes
 → A completes
 → verification
 → outcome
 → evidence
 → observations/claims
 → learning
 → private procedure state
```

Assert every state transition and lineage edge.

---



# Part IV — Combined implementation order

```text
1.  Schema contract tests
2.  IngestionContext / provenance manifest
3.  Source + Artifact normalization
4.  Observation normalization
5.  Claim normalization
6.  ClaimFamily integration
7.  Evidence normalization
8.  Global ingestion refactor
9.  Local schema-aligned representation
10. Local/cloud sync + scope
11. Publication hardening
12. Auto Router
13. StealthExecutionContext
14. ExecutionRecorder
15. Stealth Execution Contract
16. Plan-only execution lease
17. Recursive child executions
18. Recovery/resume/retry
19. MCP → ingestion learning loop
20. Full privacy/provenance E2E
21. Corpus migration/backfill
22. Remove/quarantine legacy shortcuts
```

---



# Part V — Explicit storage map


| Object                        | Local/private-only mode                     | Connected private mode                                              | Global                               |
| ----------------------------- | ------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------ |
| Source metadata               | local                                       | Stealth cloud, private scope                                        | cloud                                |
| Raw Artifact                  | local                                       | preferably local/object storage, policy-controlled metadata cloud   | sanitized/public storage             |
| Event                         | local JSONL                                 | canonical cloud event/trace metadata, raw payload policy-controlled | only publishable lineage             |
| Trace                         | local                                       | cloud metadata + optional local raw trace                           | publishable summary/lineage only     |
| Episode                       | local/derived                               | cloud                                                               | cloud if public lineage is permitted |
| Observation                   | local structured store                      | cloud `USER_PRIVATE` / workspace/org                                | cloud global                         |
| Claim                         | local structured store                      | cloud private scope                                                 | cloud global                         |
| ClaimFamily                   | local/derived                               | cloud private scope                                                 | cloud global                         |
| Evidence                      | local/raw + structured metadata             | cloud structured metadata, sensitive content local if required      | only cleared evidence                |
| Procedure                     | SQLite/cache                                | canonical cloud private procedure                                   | Global Commons                       |
| Implementation                | local/cache                                 | cloud private                                                       | global only if cleared               |
| ExecutionPlan                 | local                                       | cloud execution substrate                                           | cloud if publishable                 |
| Execution                     | local trace + metadata                      | cloud canonical execution metadata                                  | only allowed public lineage          |
| Outcome                       | local                                       | cloud                                                               | cloud                                |
| Secrets/raw confidential data | local only unless policy explicitly permits | never global; policy-controlled                                     | never                                |
| Embeddings                    | local cache or cloud private                | cloud private                                                       | global index after publication       |


**Important:** local SQLite is a local registry/cache, not a competing canonical global database.

---



# Part VI — Final acceptance bar

StealthLab is hardened only when this is true:

> Any source entering Stealth produces a scope-correct, provenance-correct chain of evidence and knowledge objects; any learned procedure is independently searchable; private procedures remain private until explicitly published; global procedures are admitted through publication and verification gates; and any execution claiming Stealth procedural provenance passes through a durable server-enforced lifecycle with complete outcome/evidence lineage, including recursively invoked child procedures.

Final architecture:

```text
                    STEALTHLAB
                        │
        ┌───────────────┴───────────────┐
        │                               │
     INGESTION                         MCP
        │                               │
 Source → Artifact                 Claude → Entry
        │                               │
 Experience layer                  Auto Router
        │                         assist / plan / execute
 Observations                            │
        │                       Stealth Execution Contract
 Claims                                  │
        │                       Execution State Machine
 ClaimFamilies                           │
        │                      ┌─────────┴─────────┐
 Evidence                       │                   │
        │                  Execution A        Execution B
 Procedure A                     │                   │
        │                       Trace              Trace
 Implementation                  │                   │
        │                    Evidence             Evidence
 ExecutionPlan                    └─────────┬─────────┘
        │                                    │
 Execution                              Learning
        │                                    │
 Outcome ────────────────────────────────────┘
```



## Final architectural distinction

```text
same ontology
≠
same physical storage
```

and:

```text
nested executions
≠
nested procedure identity
```

That is the implementation target.