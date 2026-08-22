# Verified Procedural Experience System — Ideal Specification

## 0. Purpose

A shared infrastructure layer that turns **experience from completed work into verified, reusable capabilities**.

> A procedure is a parameterized way of achieving an outcome, under stated conditions, backed by evidence, with known capability and failure boundaries.

```text
Experience → Observation → Claim / State → Procedure → Verification
→ Capability → Reuse → Execution → Evidence → Update / Invalidation
```

## 1. Design Principles

1. Experience is not memory; raw events become structured experience.
2. Claims describe what is believed; procedures describe how to achieve something.
3. Procedures are executable, not prompts.
4. Every procedure has applicability conditions.
5. Success is evidence, not truth.
6. Failures identify boundaries.
7. Knowledge has dependencies; changes propagate.
8. Local knowledge has priority over global knowledge.
9. Global reuse is earned through evidence and permissions.
10. Reuse must be reversible.
11. Mature procedures can replace expensive reasoning with cheaper implementations.
12. Humans control storage, sharing, execution and publication.



## 2. System Layers

```text
Applications
    ↓
Execution / Planner
    ↓
Capability + Applicability
    ↓
Procedures
    ↓
Claims / State / Graph
    ↓
Evidence / Provenance
    ↓
Events / Traces / Data
```



## 3. Core Entities

```text
Entity, Event, Observation, Episode, State
Claim, ClaimFamily, Evidence, Source
Procedure, ProcedureVersion, ProcedureStep
Implementation, ExecutionPlan, Execution, Outcome, Artifact
Capability, ApplicabilityRule, Dependency
Task, TaskGraph, ChangeSet, Review
Policy, Permission, User / Agent
```



## 4. Events, Traces and Episodes



### Event

Smallest immutable record of something that happened.

```json
{
  "id": "event_123",
  "type": "tool_call",
  "actor_id": "agent_7",
  "timestamp": "...",
  "environment_id": "repo_42",
  "input": {},
  "output": {},
  "status": "success",
  "parent_event_id": null,
  "artifacts": [],
  "source": "claude_code"
}
```



### Trace

Ordered/causally connected events belonging to an execution.

Preserve ordering, parent/child relations, actor, model, tools, inputs/outputs, errors/retries, human intervention, outcome and environment.

A trace records **what happened and how**.

### Episode

A bounded unit of work/experience.

```text
Episode
├── initial state
├── Trace(s)
├── final state
└── outcome
```

Episode = what piece of work the experience belongs to.  
Trace = what happened.  
Event = one thing that happened.

## 5. Observation

An interpretation of events, tied to its source.

```json
{
  "id": "observation_1",
  "statement": "pydantic cannot currently be imported",
  "interpretation": {
    "type": "dependency_unavailable",
    "subject": "pydantic"
  },
  "source_events": ["event_1", "event_2"],
  "confidence": 0.92,
  "created_by": "extractor_v3",
  "observed_at": "..."
}
```

Sources: deterministic extraction, tool/domain parsers, model interpretation, human input.

Observations are revisable and are **not automatically truth**.

## 6. Claim Graph

The graph is a relationship layer, not the warehouse for every object.

### Nodes

```text
Entity
Event
Observation
Claim
ClaimFamily
Evidence
State
Procedure
Execution
Review
Source
```



### Edges

```text
Observation ──supports──────► Claim
Observation ──contradicts───► Claim
Evidence ─────supports──────► Claim
Evidence ─────contradicts───► Claim

Claim ─────────derived_from─► Observation
Observation ──derived_from──► Event
Evidence ─────derived_from──► Source
Claim ─────────sourced_from─► Source

Claim ─────────depends_on───► Claim
Claim ─────────contradicts──► Claim
Claim ─────────supports─────► Claim
Claim ─────────supersedes───► Claim
Claim ─────────refines──────► Claim
Claim ─────────generalizes──► Claim
Claim ─────────specializes──► Claim

Claim ─────────about────────► Entity
Claim ─────────applies_to───► Entity
Claim ─────────applies_under► State

Procedure ─────requires─────► Claim
Procedure ─────supported_by─► Evidence
Procedure ─────derived_from─► Claim
Procedure ─────produces─────► State

Execution ─────instantiates─► Procedure
Execution ─────starts_from──► State
Execution ─────produces─────► Evidence
Execution ─────produces─────► State

Review ─────────reviews─────► Claim
Review ─────────supports────► Claim
Review ─────────contradicts─► Claim
Review ─────────supported_by► Evidence
```

Canonical pattern:

```text
Source → Observation → Claim → dependent Claim
                         ↑
Procedure → Execution → Evidence
```



## 7. Claim

A Claim is the complete knowledge object. Natural language is preserved; a structured proposition makes it machine-operable.

```yaml
Claim:
  id: string
  version: integer

  statement:
    text: string
    language: string

  proposition:
    type:
      fact | property | conditional | causal | temporal |
      probabilistic | comparative | procedural | negative | composite
    content: object

  scope:
    type:
      global | organization | team | project | repository |
      user | session | task | entity
    entity_id: string | null

  validity:
    valid_from: datetime | null
    valid_until: datetime | null
    observed_at: datetime

  status:
    supported | disputed | uncertain | stale |
    superseded | invalid | retracted

  belief:
    score: float
    method: string
    last_updated: datetime

  provenance:
    created_by: SourceRef
    created_at: datetime

  permissions:
    owner_id: string
    visibility:
      private | team | organization | community | global
```

`statement` = what was asserted.  
`proposition` = what the system currently understands it to mean.

Do not force every claim into subject–predicate–object.

## 8. Claim Proposition Types

```text
fact
property
conditional
causal
temporal
probabilistic
comparative
procedural
negative
composite
```

Example:

```json
{
  "type": "causal",
  "content": {
    "cause": "...",
    "effect": "...",
    "conditions": [...]
  }
}
```

Natural language remains the surface; structured propositions support computation.

## 9. Claim Verification

```text
Events → Observations → Evidence for / against → Claim evaluation
→ Status + belief
```

Consider evidence direction, strength, independence, freshness, scope, source reliability and contradictions.

`belief` is an assessment, not “probability that the claim is true.”

## 10. Claim Families and Cross-Domain Similarity

A Claim Family represents **propositional identity**, not merely topic or sentence similarity.

Claims from different communities may share a family if the normalized proposition and conditions essentially match.

Example:

```text
CV:
"Data augmentation improves image classification."

Classical ML:
"Data augmentation improves classification performance."
```

These may be one family if proposition and conditions align.

But:

```text
CV: random cropping → ImageNet accuracy
ML: Gaussian noise → tabular accuracy
```

are better treated as different families that are related.

Use:

```text
same_family
related_family
generalizes / specializes
```

Family structure can be hierarchical:

```text
"augmentation can improve learning"
        │
        ├── Vision:
        │   image augmentation → image classification
        │
        └── Classical ML:
            tabular augmentation → classification
```

Family matching:

```text
candidate claims
      ↓
semantic similarity
      ↓
proposition matching
      ↓
entity / ontology matching
      ↓
condition matching
      ↓
outcome matching
      ↓
scope / domain
      ↓
contradiction check
      ↓
family decision
```

Similarity is candidate generation, not identity. Domain/community is normally metadata or a condition, not the sole family boundary.

## 11. Evidence

Evidence is the basis used to support or contradict a claim.

Types:

```text
execution_result
observation
experiment
benchmark
document
human_review
external_source
artifact
reproduction
```

```yaml
Evidence:
  id: string
  type: string
  source_id: string
  content_ref: string | null
  strength:
    score: float
    method: string
  independence_group: string
  created_at: datetime
```

Repeated identical evidence from the same source does not count as independent evidence.

### Artifact vs Observation

```text
Event
  ↓ produces
Artifact
  ↓ interpreted as
Observation
  ↓ supports
Claim
```

Artifact = concrete produced/recorded object: report, screenshot, file, model output, etc.

Observation = interpretation of what the artifact/events show.

Keep artifacts immutable so improved extractors can create new observations without changing the underlying evidence.

## 12. State

State = what is believed to be true at a point in time.

```yaml
State:
  id: string
  scope:
    type: string
    entity_id: string
  valid_from: datetime
  valid_until: datetime | null

  claims:
    - claim_id: string
      claim_version: integer

  snapshot_hash: string
```

`claims` contains **references/IDs, not full Claim objects**.

A state therefore means:

```text
At time T, for scope S:
claim_1:v3
claim_2:v7
claim_9:v1
```

State answers: **Is this procedure applicable now?**

Trace answers: **How did we get here?**

## 13. Procedure

A **procedure** is a reusable, parameterized way to achieve an outcome under defined conditions.

```yaml
Procedure:
  id: string
  version: integer

  goal:
    description: string
    expected_outcome: string

  inputs:
    - name: string
      type: string
      required: boolean

  required_state:
    # Broad state pattern used to decide whether this is the right procedure
    claims:
      - claim_id: string
        claim_version: integer

  preconditions:
    # Checks that must pass before execution / a step
    - type: claim_check | tool_check | environment_check | custom_check
      definition: object

  steps:
    - id: string
      action: string
      inputs: object
      depends_on: [step_id]
      preconditions: [check]
      implementation_ids: [string]

  branches:
    - condition: check
      next_steps: [step_id]

  required_capabilities:
    - capability_id: string
      minimum_level: number

  required_tools:
    - tool_id: string

  expected_effects:
    - description: string
      state_changes: [ClaimRef]

  postconditions:
    - type: claim_check | test | output_check | custom_check
      definition: object

  verification:
    method: string
    required_evidence_types: [string]
    success_criteria: object

  evidence:
    origin_evidence_ids: [EvidenceRef]
    verification_evidence_ids: [EvidenceRef]

  known_failures:
    - failure_id: string
      condition: string
      consequence: string
      mitigation: string

  applicability:
    rule_id: string

  cost:
    tokens: number | null
    time: number | null
    money: number | null

  capability:
    capability_id: string

  dependencies:
    claims: [ClaimRef]
    procedures: [ProcedureRef]
    tools: [ToolRef]

  permissions:
    policy_id: string

  created_at: datetime
```

Required state asks:

> **Is this the right procedure for this situation?**

Preconditions ask:

> **Can I safely/validly perform this step now?**

A procedure is **not a prompt**.

Implementations can be:

```text
deterministic code
shell / API
rule
lookup / cached result
SLM
frontier LLM
human
another procedure
```



## 14. Procedure Instantiation

Instantiation turns reusable knowledge into a concrete job.

Generic:

```text
debug dependency conflict
```

becomes:

```text
repo = payments-api
python = 3.12
dependency = pydantic
test = test_auth.py
```

Process:

1. find candidate procedures
2. bind parameters
3. check required state
4. resolve claims
5. check preconditions
6. resolve tools
7. build task graph
8. select implementations
9. check safety
10. produce ExecutionPlan

Failed binding or failed critical checks prevents automatic execution.

## 15. Execution Plan

Instantiation produces an `ExecutionPlan`, **not a new Procedure**.

It is the concrete, executable form of a Procedure for one task.

```yaml
ExecutionPlan:
  id: string

  procedure:
    id: string
    version: integer

  task:
    description: string

  parameters: object

  starting_state:
    state_id: string

  resolved_claims:
    - claim_id: string
      version: integer

  selected_branches:
    - branch_id: string

  task_graph:
    id: string

  implementations:
    step_id: implementation_id

  safety_check:
    status: passed | failed | requires_review

  verification_plan:
    object
```

Lifecycle:

```text
Procedure
    ↓ instantiate
ExecutionPlan
    ↓ execute
Execution
    ↓
Trace + Outcome + Evidence
    ↓
Observation / Claim updates
    ↓
Procedure version update if needed
```

The plan is task-specific and references the immutable Procedure version rather than copying or modifying it.

and procedure should inherently be related with Execution plan

## 16. Applicability

Applicability asks:

> Does this procedure fit the current situation?

Results:

```text
applicable
probably_applicable
uncertain
not_applicable
unsafe
```

Based on explicit conditions, not semantic similarity alone.

## 17. Capability

Capability asks:

> How reliably can this procedure/implementation achieve its required outcome under stated conditions?

Conceptually:

```text
P(required outcome | state, procedure, implementation)
```

Conditional on task, state, environment, inputs, implementation and constraints.

Suggested levels:

```text
0 Unknown
1 Observed
2 Reproduced
3 Validated
4 Generalized
5 Trusted
```

Capability can decrease after failures or environment changes.

## 18. Verification

```text
A Structural
B Output
C Test
D Independent reproduction
E Real-world outcome
```

Use the strongest available verification.

## 19. Procedure Lifecycle

```text
Candidate
→ Proposed
→ Tested
→ Verified
→ Reusable
→ Generalized
→ Trusted
```

Failures can cause:

```text
Trusted
→ Degraded
→ Restricted
→ Stale
→ Retired
```

Never silently overwrite history.

## 20. Versioning and ChangeSets

Claims, procedures, applicability rules and implementations are immutable versions.

```text
P(v1)
 ↓
ChangeSet
 ↓
P(v2)
```

```json
{
  "id": "changeset_1",
  "author": "agent_7",
  "changes": [
    {"operation": "invalidate", "target": "claim_42"},
    {"operation": "create_version", "target": "procedure_8"}
  ],
  "reason": "...",
  "evidence": [],
  "review_status": "pending"
}
```



## 21. Truth Maintenance / Update Propagation

```text
Claim A changes
   ↓
dependent claims
   ↓
dependent procedures
   ↓
dependent implementations/routes
   ↓
re-evaluate affected objects
```

Results:

```text
valid
unchanged
weakened
stale
invalid
requires_review
```

Use an indexed dependency queue, not full-graph recomputation.

## 22. Execution

An Execution is the actual run of an ExecutionPlan.

```json
{
  "id": "execution_123",
  "execution_plan_id": "plan_91",
  "procedure_version": "procedure_42:v7",
  "state_id": "state_99",
  "implementation_id": "slm_4",
  "parameters": {},
  "task_graph_id": "graph_7",
  "trace_id": "trace_8",
  "outcome_id": "outcome_8"
}
```

Always record the exact Procedure version, ExecutionPlan and implementation.

## 23. Capability-Based Routing

Choose the cheapest implementation that meets required capability and safety.

```text
Required capability ≥ 0.90

Rule system   0.72 → reject
SLM           0.93 → use
Frontier LLM  0.97 → unnecessary
```



## 24. Task DAG

```text
Task
├── predictable node
├── predictable node
├── uncertain node
├── predictable node
└── high-risk node
```

Predictable nodes can use deterministic code, cached results, small models, specialized models and verified procedures.

Uncertain/high-risk nodes use stronger reasoning or human approval.

## 25. Reuse Decision

```text
Applicable?
    ↓
Capability sufficient?
    ↓
Evidence current?
    ↓
Dependencies valid?
    ↓
Permissions valid?
    ↓
Risk acceptable?
    ↓
YES → execute
NO  → adapt / ask / reason from scratch
```

“Do not reuse” is a first-class outcome.

## 26. Agent Trace Ingestion

Normalize:

```text
prompt
observation
tool call
tool result
file change
model response
human intervention
error
retry
outcome
```

Sources may include Claude Code, Cursor, IDEs, browser agents, API agents, enterprise agents and mobile assistants.

The substrate must not depend on one vendor.

## 27. Why Traces Preserve Models and Tools

Capability depends on implementation.

Two executions can differ because of:

```text
model
tool
tool version
environment
permissions
```

Therefore:

```text
Procedure
 ↓
ExecutionPlan
 ↓
Execution
 ↓
Model + Tools + Environment
 ↓
Outcome
```

is required to learn which implementation actually demonstrated capability.

## 28. Retrieval

```text
Current state
 ↓
scope filter
 ↓
entity/project filter
 ↓
dependency filter
 ↓
procedure-family match
 ↓
semantic match
 ↓
applicability
 ↓
capability
 ↓
risk
```

Retrieval is candidate generation, not the final reuse decision.

## 29. Global vs Local Knowledge

Scopes:

```text
global
organization
team
project
repository
branch
user
session
task
```

Preferred lookup:

```text
exact local
→ local family
→ organization
→ trusted global
→ broad global
```

Global knowledge must not override stronger local evidence.

## 30. Research-Paper Ingestion

```text
Raw papers
   ↓
Object / data lake
   ↓
Extraction
   ↓
Candidate observations / claims
   ↓
Entity resolution
   ↓
Claim normalization
   ↓
Claim-family grouping
   ↓
Evidence aggregation
   ↓
Accepted knowledge graph
```

Do not make every model extraction a trusted claim.

Retain:

```yaml
extraction:
  extractor_id: string
  extractor_version: string
  source_span: string
  extraction_confidence: float
  normalized_at: datetime
```



## 31. Physical Storage

Do not make the graph database the warehouse for everything.

```text
Raw papers / traces / artifacts
    → object storage / data lake

Claims / observations / evidence / versions
    → relational or columnar storage

Relationships / dependency traversal
    → graph store

Semantic retrieval
    → vector index

Full-text retrieval
    → search index

Current state / hot lookups
    → relational or key-value store
```

The graph stores **relationships and IDs**; large content stays in appropriate stores.

## 32. Scalability Rules

1. Keep raw sources immutable.
2. Separate candidate knowledge from accepted knowledge.
3. Deduplicate/group claims without losing source claims.
4. Resolve entities to canonical IDs.
5. Index dependencies for selective update propagation.
6. Never recompute the whole graph after a local change.
7. Keep provenance for every extracted/derived object.
8. Version extraction models.
9. Keep graph edges controlled.
10. Store large payloads outside the graph.



## 33. Privacy and Sharing

Every object has:

```text
owner
scope
access policy
sharing policy
retention policy
```

Support:

```text
private data
private procedure
anonymized procedure
shared procedure
global procedure
```

Private evidence must not leak through a public procedure.

## 34. Security

Enforce:

- tenant isolation
- identity
- authorization
- encryption
- audit logs
- procedure permissions
- tool permissions
- data-flow restrictions
- publication controls
- deletion
- revocation

A procedure cannot grant authority the user does not have.

## 35. Peer Review

Reviewers can:

```text
support
reject
challenge
reproduce
identify boundary conditions
propose changes
provide counterexamples
```

Preserve reviewer, position, reason, evidence, timestamp and independence.

Model agreement alone is not peer review.

## 36. Failure Learning

```text
Execution
 ↓
Failure
 ↓
classify cause
 ↓
update applicability
 ↓
update capability
 ↓
new procedure version if needed
```

Distinguish:

```text
procedure wrong
implementation wrong
environment changed
input abnormal
verification wrong
external failure
```



## 37. Knowledge Decay / Revalidation

Freshness depends on knowledge type and dependencies.

```text
user preference → long-lived
software version → short-lived
policy → strict
location → very short-lived
procedure → evidence/dependency-dependent
```

Revalidation may be:

```text
scheduled
dependency-triggered
failure-triggered
confidence-triggered
environment-triggered
sampled
reviewer-requested
```

Revalidation creates new evidence.

## 38. Core APIs

```text
POST /events
POST /traces
POST /episodes

GET  /claims
POST /claims
GET  /state
POST /state
GET  /graph/query

POST /procedures
GET  /procedures/:id
GET  /procedures/:id/versions
POST /procedures/:id/propose
POST /procedures/:id/verify
POST /procedures/:id/revalidate
POST /procedures/:id/instantiate

GET  /execution-plans/:id
POST /execution-plans/:id/execute

GET  /capabilities/:id
POST /capabilities/:id/evidence

POST /tasks/plan
POST /tasks/execute
POST /executions
GET  /executions/:id

POST /reviews
POST /changesets
POST /changesets/:id/approve
POST /changesets/:id/reject
```



## 39. Minimum Invariants

1. Every execution references an exact ExecutionPlan.
2. Every ExecutionPlan references an exact Procedure version.
3. Every verified procedure has evidence.
4. Every procedure has applicability conditions.
5. Every capability has a defined task and evaluation criterion.
6. Every claim has provenance.
7. Every knowledge change creates a version/change record.
8. Invalid dependencies cannot silently leave procedures trusted.
9. Private evidence cannot automatically become public.
10. Failure can reduce capability.
11. The system can refuse reuse.
12. Implementation capability is evidence-based, not model-brand-based.
13. Outcomes are distinguishable from model self-reports.
14. Raw events and source material are immutable.
15. Candidate extractions are distinguishable from accepted knowledge.
16. State stores Claim references/version references, not embedded Claims.
17. Instantiation never silently modifies the source Procedure.



## 40. Evaluation

Compare:

```text
A. Frontier agent from scratch
B. Frontier agent + conventional memory
C. Frontier agent + verified procedural experience
```

Measure:

- task success
- unseen-task success
- procedure reuse
- false reuse
- transfer
- tool calls
- tokens
- latency
- cost
- human intervention
- failure recovery
- stale-procedure detection
- capability prediction

Strongest result:

> After learning from previous executions, an agent solves new tasks better and more cheaply while correctly refusing procedures whose assumptions no longer hold.



## 41. What Is Being Built

Not primarily:

- vector database
- chat-history store
- RAG
- knowledge graph
- workflow database
- agent framework
- SLM router

Those are components.

The intended system is:

> **A continuously verified procedural memory and capability layer for AI.**

Core loop:

```text
DO
 ↓
OBSERVE
 ↓
GENERALIZE
 ↓
VERIFY
 ↓
RATE CAPABILITY
 ↓
STORE
 ↓
REUSE
 ↓
EXECUTE
 ↓
MEASURE
 ↓
UPDATE
```

Long-term shared asset:

> **A global, permissioned, evidence-backed library of how AI can reliably do things.**

