# Verified Procedural Experience System — Ideal Specification

## 0. Purpose

A shared infrastructure layer that turns **experience from completed work into verified, reusable capabilities**.

Its core object is a **procedure**:

> A parameterized way of achieving an outcome, under stated conditions, backed by evidence, with a known capability level and known failure boundaries.

Core loop:

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
Implementation, Execution, Outcome, Artifact
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

Preserve:

- ordering
- parent/child relations
- actor
- model
- tools
- inputs/outputs
- errors/retries
- human intervention
- outcome
- environment

A trace records **what happened and how**.

### Episode

Bounded unit of work/experience.

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

Sources:

- deterministic extraction
- tool/domain parsers
- model interpretation
- human input

Observations are revisable and are **not automatically truth**.

## 6. Claim Graph

The graph is a relationship layer, not the warehouse for every object.

### Node types

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



### Edge types

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



### Canonical pattern

```text
Source
  ↓
Observation
  ↓ supports / contradicts
Claim
  ↓ depends_on
Claim

Procedure
  ↓
Execution
  ↓
Evidence
  └──────────────► Claim
```



## 7. Claim

A Claim is the complete knowledge object.

Natural language is preserved; a structured proposition makes it machine-operable.

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

`statement` = what was actually asserted.

`proposition` = what the system currently understands it to mean.

The proposition may be uncertain or absent without losing the original statement.

## 8. Claim Proposition Types

Do not force every claim into subject–predicate–object.

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
Events
  ↓
Observations
  ↓
Evidence for / against
  ↓
Claim evaluation
  ↓
Status + belief
```

Evaluation considers:

- evidence direction
- evidence strength
- independence
- freshness
- scope
- source reliability
- contradictions

Preserve **why** a claim is believed, not only a score.

`belief` is an assessment, not “probability that the claim is true.”

## 10. Claim Families and Cross-Domain Similarity

A Claim Family represents **propositional identity**, not merely topic or sentence similarity.

Two claims may belong to the same family when their underlying proposition is essentially the same, even if they come from different communities.

Example:

```text
CV:
"Data augmentation improves image classification."

Classical ML:
"Data augmentation improves classification performance."
```

These may be the same family if the normalized proposition and conditions align.

But:

```text
CV:
random cropping → ImageNet accuracy

Classical ML:
Gaussian noise → tabular accuracy
```

are better treated as different families that are related.

Use three relationships:

```text
same_family
related_family
generalizes / specializes
```

Model family structure hierarchically:

```text
General:
"augmentation can improve learning"
        │
        ├── Vision:
        │   "image augmentation improves image classification"
        │
        └── Classical ML:
            "tabular augmentation improves classification"
```



### Family matching

Do not use semantic similarity alone.

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

The matcher may produce:

```text
same_family      0.93
related_family   0.97
contradictory    0.04
```

while retaining the reasons and evidence.

Community/domain is normally metadata or a condition, not the sole family boundary.

## 11. Evidence

Evidence explains why a claim is supported or contradicted.

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

```json
{
  "id": "evidence_1",
  "type": "execution_result",
  "source_id": "execution_17",
  "content_ref": "...",
  "strength": {
    "score": 0.91,
    "method": "..."
  },
  "independence_group": "user_17",
  "created_at": "..."
}
```

Repeated identical evidence from the same source does not count as independent evidence.

## 12. State

State = what is believed to be true at a point in time.

```json
{
  "id": "state_123",
  "scope": "repository",
  "entity_id": "repo_42",
  "valid_from": "...",
  "valid_until": null,
  "claims": ["claim_1", "claim_2"],
  "snapshot_hash": "..."
}
```

```text
State S0
   ↓
Trace
   ↓
State S1
```

State answers: **Is this procedure applicable now?**

Trace answers: **How did we get here?**

- claim id is stored in claim field here



## 13. Procedure

A **procedure** is a reusable, parameterized way to achieve an outcome under defined conditions.

```
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
    # State pattern in which this procedure is applicable
    claims:
      - claim_id: string
        claim_version: integer

  preconditions:
    # Checks that must pass before execution/steps
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
    origin_evidence_ids: [string]
    verification_evidence_ids: [string]

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
  version: integer
```

### Required state vs. preconditions

```
Required State
    ↓
"Is this the right procedure for this situation?"

Preconditions
    ↓
"Is it safe/valid to perform this step now?"
```

Example:

```
Required state:
  Python repository
  using Poetry

        ↓

Step 1: inspect dependencies
  precondition:
    pyproject.toml exists

        ↓

Step 2: identify conflict
  precondition:
    dependency tree available

        ↓

Step 3: change dependency
  precondition:
    specific conflict identified

        ↓

Step 4: run tests
  precondition:
    lockfile regenerated successfully

        ↓

Postcondition:
  dependency conflict resolved
  required tests pass
```

### A procedure is **not a prompt**

It specifies **what must happen and how success is verified**, while the implementation can vary:

```
Procedure
    │
    ├── deterministic code
    ├── shell / API
    ├── rule
    ├── lookup / cached result
    ├── SLM
    ├── frontier LLM
    ├── human
    └── another procedure
```

This separation is what lets the system learn:

> **“This procedure works”**

independently from:

> **“This particular model/tool was used to execute it.”**

## 14. Procedure Instantiation

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

Instantiation:

1. find candidates
2. bind parameters
3. check preconditions
4. resolve claims
5. resolve tools
6. build task graph
7. select implementations
8. check safety
9. produce executable plan

Failed binding prevents automatic execution.

## 15. Applicability

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

## 16. Capability

Capability asks:

> How reliably can this procedure/implementation achieve its required outcome under stated conditions?

Conceptually:

```text
P(required outcome | state, procedure, implementation)
```

Conditional on:

- task
- state
- environment
- inputs
- implementation
- constraints

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

## 17. Verification

```text
A Structural
B Output
C Test
D Independent reproduction
E Real-world outcome
```

Use the strongest available verification.

## 18. Procedure Lifecycle

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

## 19. Versioning and ChangeSets

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



## 20. Truth Maintenance / Update Propagation

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

What should this do?

- Merging of claims and then downstream changes across

Which claims/problems (and problems refers to big ones, not necessarily part of our schema) should be debated

## Ranking Problems

It is important to select problems 

## 21. Execution

```json
{
  "id": "execution_123",
  "procedure_version": "procedure_42:v7",
  "state_id": "state_99",
  "implementation_id": "slm_4",
  "parameters": {},
  "task_graph_id": "graph_7",
  "trace_id": "trace_8",
  "outcome_id": "outcome_8"
}
```

Always record the exact procedure version and implementation.

## 22. Capability-Based Routing

Choose the cheapest implementation that meets required capability and safety.

```text
Required capability ≥ 0.90

Rule system   0.72 → reject
SLM           0.93 → use
Frontier LLM  0.97 → unnecessary
```



## 23. Task DAG

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

## 24. Reuse Decision

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

## 25. Agent Trace Ingestion

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

Sources may include:

```text
Claude Code
Cursor
IDEs
browser agents
API agents
enterprise agents
mobile assistants
```

The substrate must not depend on one vendor.

## 26. Why Traces Preserve Models and Tools

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
Execution
 ↓
Model + Tools + Environment
 ↓
Outcome
```

is required to learn which implementation actually demonstrated capability.

## 27. Retrieval

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

## 28. Global vs Local Knowledge

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

## 29. Research-Paper Ingestion

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



## 30. Physical Storage

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

## 31. Scalability Rules

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



## 32. Privacy and Sharing

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

## 33. Security

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

## 34. Peer Review

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

Preserve:

```text
reviewer
position
reason
evidence
timestamp
independence
```

Model agreement alone is not peer review.

## 35. Failure Learning

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



## 36. Knowledge Decay / Revalidation

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

## 37. Core APIs

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
POST /procedures/:id/execute

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



## 38. Minimum Invariants

1. Every execution references an exact procedure version.
2. Every verified procedure has evidence.
3. Every procedure has applicability conditions.
4. Every capability has a defined task and evaluation criterion.
5. Every claim has provenance.
6. Every knowledge change creates a version/change record.
7. Invalid dependencies cannot silently leave procedures trusted.
8. Private evidence cannot automatically become public.
9. Failure can reduce capability.
10. The system can refuse reuse.
11. Implementation capability is evidence-based, not model-brand-based.
12. Outcomes are distinguishable from model self-reports.
13. Raw events and source material are immutable.
14. Candidate extractions are distinguishable from accepted knowledge.



## 39. Evaluation

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



## 40. What Is Being Built

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

