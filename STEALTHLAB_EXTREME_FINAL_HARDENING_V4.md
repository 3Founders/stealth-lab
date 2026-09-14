# StealthLab — Final Ingestion Repair & MCP Hardening Plans

**Repository:** `3Founders/stealth-lab`  
**Audited revision:** `main` at `301b9bf3f0de4cf0d51dbd25e8761f62d3a89498`  
**Primary schema:** `schema.md` / Verified Procedural Experience System schema

## Executive decision

### Final architecture correction applied in this version

This version resolves the remaining contradictions in earlier drafts:

```text
Claims are independent general knowledge; Procedures point to relevant Claims.
Procedures are the reusable action/skill object; there is no second reusable Task ontology.
TaskGraph/PlanNode are runtime execution state only.
Procedure↔Implementation is many-to-many and evidence/applicability scoped.
MCP retrieval is not the endpoint: Stealth remains procedure-conditioned during execution.
Verification is explicit and may include optional structured human review.
`.stealth/` is a tiny generated working-set projection, not another knowledge store.
Hierarchical indexes are derived routing infrastructure, not canonical global Markdown.
Multi-agent coordination is expressed in the execution graph + declared file intents.
Production paths fail closed; missing knowledge/tool/evidence is never replaced by synthetic success.
```


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
- Do not build second registries, provenance graphs, evidence stores, or task schedulers.



# Part II — Plan A: Fix Ingestion



## A0 — Freeze the canonical ingestion contract

Every ingestion source MUST first cross the same immutable source boundary:

```text
Source → Artifact
```

From that boundary, derivation is an explicit graph, not a mandatory linear chain:

```text
Artifact
 ├─→ Observation ─→ Claim ─→ Claim-family / contradiction relations
 ├─→ Procedure candidate        (only when genuine executable structure exists)
 ├─→ Implementation candidate   (only when a concrete invocable mechanism exists)
 └─→ Evidence(document)         (only for propositions/procedures the source actually supports)
```

Execution-derived sources additionally produce:

```text
ExecutionPlan / ProcedureRun
  → Execution
  → Events / Artifacts / Outcome
  → Observations
  → Evidence
  → Claim candidates and Procedure/Implementation capability updates where justified
```

No stage may manufacture an object merely to satisfy a pipeline shape. A source that lacks execution evidence MUST NOT pretend to have execution evidence; a source that contains no meaningful proposition MUST NOT produce a Claim; a source that contains no independently actionable method MUST NOT produce a Procedure; and a source that names no concrete executable mechanism MUST NOT produce an Implementation.

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



## A3 — Make Event / Trace / Episode explicit

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



## A6 — EquivalentClaims

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

## A11 — Local schema-aligned learning

Local runtime learning uses the same logical ontology as global knowledge, but starts in the user's private scope.

```text
Execution
  ↓
events / artifacts / observations
  ↓
candidate Claims
  ↓
candidate Procedures
  ↓
candidate Implementations where concrete mechanisms were discovered
  ↓
USER_PRIVATE knowledge
```

Local learning preserves scope, owner/workspace, execution lineage, artifact references, observations, evidence, and verification.

The local agent-facing projection may contain:

```text
.stealth/
  claims.md
  procedures.md
  implementations.md
  run.md
  exploration.md
  meta.json
  index/
```

These files are a projection/cache, not canonical truth. Local SQLite/structured storage is a local registry/cache.

Private learning may happen automatically after execution. Global publication never happens automatically.

```text
private candidate
  ↓
surface strongest reusable candidates
  ↓
user chooses "Share with Global"
  ↓
publication checks
  ↓
GLOBAL CANDIDATE
```

Do not upload raw private repository contents, secrets, credentials, or confidential execution traces merely because they contributed to a candidate.

---

## A12 — Local storage specification

Local storage and cloud storage implement the same logical ontology but have different trust, privacy, durability, and latency responsibilities.

### Local machine

The local workspace MUST contain only data needed for private execution, low-context retrieval, recovery, and local evidence capture.

Canonical local private store (reuse existing local SQLite/structured store if present):

```text
Source metadata for local/private sources
Artifact metadata + local content references
Events / Trace / Episode metadata
Observations
Local Claims
Claim relations/families needed by the workspace
Private Procedure candidates and cached selected global Procedures
Implementation availability/resolution cache
ExecutionPlan / ProcedureRun / TaskGraph / PlanNode state
Outcome / Evidence metadata
content hashes / provenance / versions
sync cursors
```

Raw repository files, raw traces, secrets, credentials, large tool outputs, and confidential artifacts remain in the workspace or approved local artifact store unless an explicit policy allows transfer.

The agent-facing `.stealth/` directory is a GENERATED projection, not canonical storage. Keep it intentionally small:

```text
.stealth/
  context.md     # compact router + relevant local/global Claims + selected Procedure/run summary
  run.json       # machine-readable current run, node ownership, file intents, versions/cursors
  meta.json      # workspace/scope IDs, projection revision, hashes, sync cursor
```

Do not create five large Markdown mirrors. `context.md` MUST be index-first and block-addressable so agents can `head`, `grep`, and read exact anchors/ranges. It contains only the current working set, never the entire global graph.

`context.md` sections:

```text
[ROUTER]
[CLAIMS:LOCAL]
[CLAIMS:GLOBAL_RELEVANT]
[PROCEDURES:SELECTED]
[IMPLEMENTATIONS:RELEVANT]
[COORDINATION]
```

Each index row contains only stable ID, short label, scope/status, tags, version, and stable anchor. Detailed blocks appear only for objects selected into the working set. Stable IDs/anchors are authoritative inside the projection; literal line numbers are disposable optimization metadata and MUST be regenerated after rewrites.

### Stealth cloud

The cloud is authoritative for connected private/global reusable knowledge and durable hosted metadata:

```text
Sources/Artifacts that policy permits storing
Observations
Claims + Claim graph/families + provenance
Procedures + immutable versions
Procedure↔Claim typed references
Implementations + immutable versions
Procedure↔Implementation relations
Evidence / Verification metadata and cleared artifacts
private workspace/org knowledge when sync is enabled
ProcedureRun / Execution metadata for connected runs
publication/admission records
derived retrieval indexes/embeddings
durable sync/change-log cursors
```

The cloud MUST NOT receive raw confidential repository contents, secrets, credentials, or raw traces by default. Sync transfers structured objects and references only when classification/policy permits them.

### Transfer matrix

```text
LOCAL ONLY BY DEFAULT
- raw repository content
- secrets/credentials
- unrestricted shell/tool output
- raw private traces
- sensitive artifacts
- transient exploration scratch

ELIGIBLE FOR USER_PRIVATE CLOUD SYNC
- normalized Claims/Observations
- private Procedures
- Procedure↔Claim refs
- Implementation metadata
- structured Evidence metadata
- run/execution metadata
- sanitized artifact references/hashes
- projection/sync cursors

GLOBAL ONLY AFTER EXPLICIT PUBLICATION + POLICY
- sanitized reusable Claims
- reusable Procedures
- public/cleared Implementations
- publishable Evidence
- provenance/license metadata
- benchmark/evaluation results safe for publication
```

No local object becomes global through ordinary synchronization.

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
EquivalentClaims exists where justified
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



# Part II-A — Explicit Ingestion Derivation Mechanisms

This section is normative. It replaces any ambiguous wording such as “observations become claims” with an explicit transformation contract.

## 1. Universal rule

No downstream object is created merely because an upstream object exists.

Every transformation MUST define:

1. input object(s);
2. extraction/derivation operation;
3. validation rules;
4. identity/deduplication rule;
5. conflict behavior;
6. provenance written to the result;
7. scope inherited by the result;
8. failure/ambiguity behavior;
9. persistence transition;
10. tests proving the transition.

Every derived object MUST answer:

```text
WHAT CREATED ME?
FROM EXACTLY WHAT INPUT?
BY WHAT TRANSFORMATION/RULE?
WHAT EVIDENCE JUSTIFIES MY CURRENT STATUS?
```

The canonical system is a graph, not a single mandatory linear chain.

Core derivation:

```text
Source → Artifact → Observation → Claim
```

A source may independently yield a Procedure candidate when it contains executable structure:

```text
Artifact + Observations/Claims → Procedure candidate
```

A Procedure may bind one or more concrete Implementations:

```text
Procedure → Implementation
```

A concrete run is an Execution:

```text
Procedure/version + Implementation/version + inputs/context
  → Execution
  → Outcome / Artifacts / Observations
  → Evidence
  → Verification
```

Evidence supports Claims, Procedures, Implementations, and Verification; it is not a mandatory predecessor of every object.

There is no canonical `Task` knowledge object. `ExecutionPlan`/`TaskGraph` are runtime orchestration structures whose nodes represent concrete execution state.

There is no separate universal `Admission` mechanism. Promotion of candidates into canonical reusable knowledge is performed by explicit ingestion gates/policy transitions.

Embeddings/indexes are derived retrieval infrastructure, never canonical knowledge.


---



## 2. Source registration mechanism



### Input

A URL, repository file, uploaded document, execution trace, Git history, Claude/ChatGPT history, benchmark, runbook, workflow, or other permitted source.

### Operation

Create or reuse a `Source` using:

```text
source identity =
    normalized locator
    + publisher/owner
    + source type
```

Then fetch/snapshot the content and compute a content hash.

### Deduplication

If:

```text
same normalized locator
AND
same content hash
```

already exists, reuse the existing immutable source snapshot.

If the locator is the same but content hash differs, create a new source version/snapshot.

### Output

```yaml
Source:
  id:
  type:
  locator:
  publisher:
  title:
  license:
  visibility:
  scope:
  created_at:
  provenance:
```

No knowledge object is created at this stage.

---



## 3. Source → Artifact mechanism



### Operation

Convert the fetched source into an immutable `Artifact`.

Persist:

```yaml
Artifact:
  id:
  source_id:
  source_version:
  mime_type:
  raw_content_hash:
  normalized_content_hash:
  content_location:
  retrieved_at:
```

The raw artifact is immutable.

Normalization creates a derived representation rather than mutating the raw artifact.

### Required provenance

```text
Artifact.source_id
Artifact.source_version
Artifact.content_hash
Artifact.retrieval_time
```



### Failure

If the source cannot be fetched or verified:

```text
Source → FETCH_FAILED
```

No downstream semantic object is created.

---



## 4. Artifact normalization mechanism

The normalizer converts heterogeneous source material into addressable blocks.

Examples:

```text
HTML
 → title
 → sections
 → paragraphs
 → lists
 → tables
 → code blocks

Markdown
 → headings
 → paragraphs
 → ordered lists
 → unordered lists
 → code blocks

Git repository
 → files
 → symbols
 → AST nodes
 → comments

Trace
 → ordered events
 → parent/child events
```

Every normalized block MUST retain source location information:

```yaml
Block:
  id:
  artifact_id:
  type:
  text:
  source_start:
  source_end:
  parent_block_id:
```

This is what makes later claims auditable back to the source.

---



## 5. Security/policy screening mechanism

Run before semantic ingestion.

Checks:

```text
prompt injection
malicious instructions
secret/credential exposure
privacy restrictions
license restrictions
source trust
policy restrictions
unsafe executable material
```

Each check produces a decision:

```text
ALLOW
QUARANTINE
REJECT
```

Persist the decision and detector provenance.

Do not silently delete rejected material.

A rejection is itself an auditable ingestion outcome.

---



## 6. Structural classification mechanism

Before extracting procedures, classify what the source actually contains.

Allowed primary shapes:

```text
PROCEDURE
REFERENCE
CLAIM
EXPERIMENT
TUTORIAL
ROUTER
OPINION
DISCUSSION
UNKNOWN
```

The classifier examines:

```text
ordered actions
step markers
imperatives
prerequisites
inputs
outputs
expected results
failure modes
verification instructions
claim-like assertions
experimental measurements
```



### Hard rule

A prose document without executable structure MUST NOT be converted into a procedure.

Forbidden:

```text
description
  ↓
steps = [description]
```

If no genuine ordered actions exist:

```text
procedure_candidate = false
```

The source can still produce claims/reference knowledge.

---



## 7. Artifact → Observation mechanism

An Observation represents what the source/event says or what is directly observed, not what Stealth believes to be universally true.

### Extraction

For every addressable source block:

1. detect a meaningful statement/event interpretation;
2. preserve the original statement;
3. identify its semantic interpretation;
4. attach exact source/event references;
5. assign extractor confidence.

Example:

```text
Source:
"Run poetry install before pytest."
```

creates:

```yaml
Observation:
  statement: "The source instructs the user to run poetry install before pytest."
  interpretation:
    type: prerequisite
    subject: test_execution
    object: poetry_install
  source_artifacts:
    - artifact_123
  source_offsets:
    - start: 4812
      end: 4860
  created_by: extractor_v3
  confidence: 0.99
```



### Validation

Reject an Observation if:

```text
statement is empty
OR source reference is missing
OR interpretation type is invalid
OR extractor cannot establish provenance
```



### Important

Observation confidence means:

```text
"How confident are we that the source/event actually supports this observation?"
```

It does NOT mean:

```text
"How true is the proposition in reality?"
```

---



## 8. Observation → Claim mechanism

This transformation MUST be explicit.

this must sort of answer how true is the proposition in reality

For each Observation:

```text
Observation
  ↓
candidate proposition extraction
  ↓
proposition validation
  ↓
entity resolution
  ↓
condition extraction
  ↓
scope extraction
  ↓
claim normalization
  ↓
existing-claim lookup
  ↓
new Claim OR link Observation to existing Claim
```



### Step 1 — Proposition extraction

Convert the observation into:

```yaml
proposition:
  type:
  subject:
  predicate:
  object:
  conditions:
  temporal_scope:
```

Example:

```text
Observation:
"The source recommends pytest-xdist for slow test suites."
```

Candidate:

```yaml
type: procedural
subject: test_suite
predicate: recommended_tool
object: pytest_xdist
condition:
  test_suite: slow
```



### Step 2 — Do not overclaim

The following are distinct:

```text
"The author recommends X."
"The author reports X works."
"X works."
"X improves performance by 40%."
```

The derivation engine MUST preserve the strongest proposition actually supported by the observation.

It MUST NOT convert a recommendation into experimentally established truth.

### Step 3 — Proposition validation

A claim candidate is valid only if:

```text
type is known
AND subject is resolvable or explicitly unresolved
AND predicate is known
AND proposition is semantically meaningful
AND provenance exists
```

If not:

```text
Observation remains an Observation.
```

No Claim is fabricated.

### Step 4 — Entity resolution

Resolve entities against the existing entity vocabulary.

Example:

```text
"pytest"
"PyTest"
"pytest framework"
```

may resolve to the same entity when equivalence is established.

If resolution is uncertain:

```text
entity_status = unresolved
```

Do not silently choose an entity.

### Step 5 — Scope extraction

Extract explicit scope:

```text
repository
language
runtime
version
platform
tool
dataset
time period
user/workspace
```

Unknown scope remains unknown.

Do not turn an unspecified condition into a universal condition.

---



## 9. Claim identity and deduplication mechanism

Normalize the proposition into a canonical representation.

Conceptually:

```text
claim_identity =
    normalized proposition
    + compatible scope
```

Search in this order:

```text
exact canonical proposition
→ same subject/predicate/object
→ semantically equivalent proposition
→ compatible scoped equivalent
```



### Exact/equivalent match

Do NOT create another Claim.

Instead:

```text
Observation
  └── supports/derives_from → existing Claim
```



### Similar but not equivalent

Create a new Claim.

Example:

```text
C1: parallel execution reduces test runtime

C2: parallel execution reduces test runtime by >40%
```

These are separate claims.

### Contradiction

Never overwrite the old Claim.

Create both and record:

```text
C1 --contradicts--> C2
```

---



## 10. Claim status mechanism

Initial claim status is determined from evidence class.

Examples:

```text
source assertion only
    → uncertain/source-derived

direct deterministic observation
    → observed

independent experiment
    → experimentally supported

repeated independent experiments
    → stronger supported state

contradictory evidence
    → disputed

known obsolete proposition
    → stale/superseded

explicit retraction
    → retracted
```

Do not promote status merely because an LLM has high confidence.

---



## 11. Claim belief mechanism

Maintain two separate values:

```text
observation_confidence
claim_belief
```

Observation confidence answers:

```text
Did the source/event really support this statement?
```

Claim belief answers:

```text
How strongly does the available evidence support the proposition?
```

A source saying:

```text
"X is 10x faster"
```

can yield:

```text
observation_confidence = high
claim_belief = low/moderate
```

until independent evidence exists.

Belief updates must cite the evidence that caused the update.

---



## 12. Claim conflict mechanism

When new evidence conflicts with a claim:

```text
new evidence
 ↓
candidate proposition
 ↓
existing claims
 ↓
conflict detector
```

If contradictory:

```text
Evidence E
  --contradicts-->
Claim C
```

Do not delete C.

If the contradiction is explained by conditions, derive narrower conditions.

Example:

```text
C1:
fresh context improves long-running agents

C2:
fresh context without external state loses continuity
```

The system can derive:

```text
C1 applies when external state is sufficient.
C2 applies when external state is insufficient.
```

Only create such conditional refinement when the evidence supports it.

---



## 13. EquivalentClaims assignment mechanism

A EquivalentClaims groups related propositions without merging them.

Compute a family candidate from:

```text
domain
+ canonical subject
+ primary concept/predicate
```

Then compare against existing families.

Results:

```text
same_family
related_family
generalizes
specializes
unrelated
```

Claims remain independent objects.

Evidence remains attached to the original Claim.

---



## 14. Evidence creation mechanism

Evidence is created only when there is something that bears on a Claim or Procedure.

Evidence classes include:

```text
document
tool_result
execution_result
benchmark
human_review
external_verification
multi_episode
```

For each Evidence:

```yaml
Evidence:
  id:
  type:
  claim_refs:
  procedure_refs:
  source_refs:
  execution_refs:
  result:
  independence:
  quality:
  created_at:
```



### Independence

Copies of the same article are NOT independent evidence.

Five executions from the same deterministic fixture may be repeated evidence, but their independence must be represented accurately.

Never inflate evidence count by counting duplicates as independent.

---



## 15. Artifact → Procedure Candidate mechanism

1. A Procedure Candidate is created only when the source contains an independently executable method.
2. Required semantic components:

```text
goal
inputs
required state
preconditions
ordered actions
expected effects
postconditions
verification
known failures
failure conditions
```

Not every field must be known at extraction time, but missing critical execution structure prevents admission as executable.

### Procedure test

Ask:

```text
Can an independent agent execute this method from the extracted representation?
```

If no:

```text
reference/claim knowledge
```

rather than fake procedure.

---



## 16. Procedure step derivation mechanism

For each candidate action:

```text
source block
 ↓
action
 ↓
target
 ↓
parameters
 ↓
ordering
 ↓
condition
 ↓
expected state/result
```

Example:

```text
"Run poetry install before pytest."
```

becomes:

```yaml
step: 1
action: execute_command
command: poetry install
expected_state: dependencies_installed
```

and:

```yaml
step: 2
action: execute_command
command: pytest
precondition: dependencies_installed
expected_state: tests_executed
```

The source wording must remain available as provenance.

---



## 17. Procedure precondition mechanism

Every explicit prerequisite becomes an applicability rule.

Example:

```text
"Requires Python 3.12"
```

becomes:

```yaml
subject: python
predicate: version_gte
value: 3.12
```

At execution time:

```text
TRUE
FALSE
UNKNOWN
```

are distinct results.

`UNKNOWN` MUST NOT be treated as `TRUE`.

---



## 18. Procedure → Claim linking mechanism

For each procedure component, determine whether it:

```text
requires a Claim
is supported by a Claim
was derived from a Claim
```

Example:

```text
Claim:
symbol-level retrieval reduces irrelevant context

Procedure:
use symbol-level retrieval

Procedure --requires/supports/derived_from--> Claim
```

Do not create claim relationships merely because the objects occur in the same source.

The relationship itself requires semantic support.

---



## 19. Procedure deduplication/versioning mechanism

Compare candidate procedure against existing procedures using:

```text
goal
preconditions
steps
expected effects
postconditions
verification
scope
implementation
```

Cases:

```text
exact same procedure
    → reuse existing procedure/version + add provenance/evidence

same method, improved definition
    → new procedure version

same goal, different method
    → independent procedure

same method, different scope
    → scoped variant/version
```

Never erase historical versions.

---



## 20. Procedure → Execution mechanism

A Procedure is the reusable executable specification. A Task is not a second canonical knowledge type.

When work is requested:

```text
user request → Execution request
             → Procedure selected (if one exists)
             → Implementation selected
             → Execution
```

If no reusable Procedure exists, Stealth may start an exploratory Execution. The execution can later produce a Procedure candidate through the learning/ingestion pipeline.

The existing `ExecutionPlan` and `TaskGraph` remain runtime orchestration structures:

```text
Procedure
  ↓
ExecutionPlan
  ↓
TaskGraph
  ↓
Execution
```

Do not create a canonical Task merely because a Procedure has steps.

---

## 21. Procedure → Implementation mechanism

An Implementation is a concrete executable mechanism capable of realizing a Procedure.

It may be:

```text
compiled C/C++/Rust/Go binary
script
WASM module
container
MCP tool
HTTP/API service
RPC service
user-local tool
third-party hosted service
model/agent
```

The implementation language, ownership, and hosting location do not change the ontology.

```text
Procedure P
  --implemented_by-->
Implementation I
```

Required semantic fields include:

```yaml
provider:
locator_or_resolution:
invocation:
version:
input_contract:
output_contract:
requirements:
execution_location:
provenance:
license:
```

A textual suggestion such as `"use AST parsing"` is not an Implementation. It remains knowledge/procedure content until a concrete executable mechanism is identified.

Execution cannot silently invent an Implementation.

---

## 22. Procedure → TestSpec mechanism

For every testable procedure/candidate:

```text
procedure
 ↓
test generator
 ↓
TestSpec
```

TestSpec contains:

```text
fixture
baseline
treatment
metrics
success criteria
environment
replication requirements
verification method
```

For optimization candidates measure as applicable:

```text
input tokens
output tokens
total tokens
LLM calls
tool calls
latency
cost
successful completion
```

For reliability:

```text
failure rate
retry rate
```

For quality:

```text
accuracy
output quality
```

For complexity:

```text
steps
tool calls
files touched
```

The smallest meaningful test should be selected.

---



## 23. Test execution → Result mechanism

Execute:

```text
baseline
+
treatment
```

under the same fixture/environment wherever possible.

Normalize raw results into:

```yaml
Result:
  candidate_id:
  baseline:
    tokens:
    latency_ms:
    cost:
    success:
  treatment:
    tokens:
    latency_ms:
    cost:
    success:
  delta:
    tokens_pct:
    latency_pct:
    cost_pct:
    success_delta:
  evidence_quality:
```

Never accept a verbal claim of improvement as the result.

---



## 24. Result → Evidence mechanism

A Result becomes Evidence only after:

```text
execution completed
AND
measurements are present
AND
fixture/environment is recorded
AND
result provenance is intact
```

Example:

```text
baseline = 10,000 tokens
treatment = 5,700 tokens
```

creates measurable evidence:

```text
token delta = -43%
```

but only supports the claim that the tested treatment produced that result under that fixture.

It does not automatically prove universal 43% savings.

---



## 25. Failure → Knowledge mechanism

A failed execution creates evidence too.

Persist:

```text
candidate
hypothesis
procedure
test
failure
failure reason
environment
artifacts
```

Then optionally derive:

```text
failure-mode observation
→ failure-mode claim
→ contradiction/condition on procedure
```

Example:

```text
Procedure P succeeds on Python 3.12.
Procedure P fails on Python 3.9.
```

can produce:

```text
P may be inapplicable below Python 3.10
```

only after sufficient evidence supports that boundary.

---



## 26. Evidence → Verification mechanism

Verification is a separate operation from execution.

The verifier receives:

```text
procedure
expected outcome
actual outcome
artifacts
test result
environment
```

and determines:

```text
VERIFIED
FAILED_VERIFICATION
INCONCLUSIVE
```

Self-report is not sufficient where independent verification is possible.

Verification must reference the evidence it used.

Verification is separate from execution. Execution produces observations/evidence; verification evaluates that evidence against explicit criteria. Repeated independently verified successful executions can accumulate as evidence for the reliability and applicability of a Procedure.

## 27. Applicability derivation mechanism

Applicability is built from:

```text
explicit source prerequisites
+
implementation requirements
+
successful execution environments
+
failed execution environments
+
observed invariants
```

For example:

```
Procedure P42

applicability:
  - requires_claim: C17
  - requires_claim: C31
```

Or more abstractly:

```
P42
 ├── condition: Python >= 3.11
 └── condition: uv-managed project
```

Then the applicability engine retrieves only Claims relevant to those conditions.

```
Procedure
   ↓
applicability conditions
   ↓
targeted Claim retrieval
   ↓
evaluate each condition
   ↓
TRUE / FALSE / UNKNOWN
```

Each condition gets provenance.

Do not infer universal applicability from one successful execution.

At retrieval time:

```text
known environment + rule
→ TRUE/FALSE/UNKNOWN
```

---

and routing must respect UNKNOWN.

Global Claims are reusable knowledge, not local assertions. A Global Claim may be used in a local context only after its applicability is evaluated against locally known facts and environment observations. `UNKNOWN` must remain an explicit outcome and must not be treated as `TRUE`.

## 28. Generalization mechanism

Given successful independent episodes:

```text
P1 in context A
P2 in context B
P3 in context C
```

find common:

```text
goal
action structure
required invariants
expected outcome
```

If common structure is sufficiently supported:

```text
P-general
```

is created as a new independent procedure.

It retains:

```text
derived_from P1
derived_from P2
derived_from P3
```

The original procedures remain independently searchable.

Generalization must not erase specific procedures.

## 29. Procedure composition/reference mechanism

Procedure identity remains independent. Composition is represented only when the Procedure definition itself requires another reusable Procedure.

```text
Procedure A:v
  step/ref ──requires/invokes_as_definition──> Procedure B:v
```

Rules:

1. The child reference MUST pin a resolvable Procedure family/version constraint according to existing version semantics.
2. A runtime re-search that happens to select B MUST NOT silently mutate A into a canonical dependency.
3. A Procedure step remains plain structured guidance unless it is a genuinely reusable subproblem.
4. Cycles in canonical Procedure composition are invalid and MUST be rejected at validation.
5. Runtime recursion is represented by parent/child executions, not by changing Procedure identity.
6. A child Procedure is independently searchable, applicable, implementable, verifiable, and versioned.

The host may dynamically call `find_best_way` during execution. That creates a child ProcedureRun/Execution relationship only after the child is selected and accepted.

---

## 30. Relevant Claim working-set derivation mechanism

Claims are an independent global/local knowledge substrate. Procedure retrieval does not own Claims; it requests a bounded relevant working set.

For a request/run, derive:

```text
Claim working set =
  authored Procedure↔Claim refs
  + Claims relevant to goal/subproblem
  + Claims relevant to environment/constraints
  + local/workspace Claims
  + relevant global Claims whose scope/applicability can be evaluated
```

Every returned Claim reference MUST include:

```text
claim_id
version
scope
status/belief
reason_for_relevance
provenance/evidence summary pointer
applicability result if used for a decision
```

Global Claims MUST NOT be copied into local canonical ownership. They are referenced/projected with their canonical global identity and version.

The working set MUST be bounded. Default MCP responses return compact references/summaries, not full Claim bodies or evidence. Full Claim/evidence retrieval is lazy and explicit.

A Claim retrieved at runtime MUST NOT be written into a Procedure version merely because it was useful during one execution.

---

## 31. Canonical object → embedding mechanism

After semantic validation:

```text
canonical object
 ↓
retrieval representation
 ↓
embedding
 ↓
index
```

Procedure embedding representation should contain, as applicable:

```text
goal
purpose
scope
preconditions
steps
failure modes
verification
```

Claim representation should contain:

```text
statement
proposition
scope
conditions
```

There is no canonical reusable Task knowledge representation. Execution objectives and PlanNodes are indexed only as runtime/search-support state when operationally necessary.

Do not use raw source text as a substitute for the canonical semantic representation.

---



## 32. Provenance invariant

Every derived object must retain:

```yaml
provenance:
  ingestion_id:
  source_ids:
  artifact_ids:
  parent_object_ids:
  extractor_id:
  extractor_version:
  created_by:
  created_at:
  derivation_rule:
```

For claims specifically:

```text
Claim
 → derived_from Observation
 → Observation
 → Artifact
 → Source
```

For evidence:

```text
Evidence
 → Execution/Source/Review
 → underlying Artifact/Event/Result
```

For procedures:

```text
Procedure
 → derived_from Claims/Observations
 → supported_by Evidence
 → implemented_by Implementation
 → tested_by TestSpec/TestRun
 → verified_by Verification
```

---



## 33. Scope propagation mechanism

Default rule:

```text
derived object scope = source scope
```

unless an explicit policy transition changes scope.

Allowed:

```text
USER_PRIVATE
WORKSPACE_PRIVATE
ORG_PRIVATE
GLOBAL
```

A child derived object cannot silently become more public than its parent.

Example:

```text
USER_PRIVATE source
→ USER_PRIVATE observation
→ USER_PRIVATE claim
→ USER_PRIVATE procedure
```

Global publication requires an explicit transition.

---



## 34. Private → Global mechanism

Publication:

```text
USER_PRIVATE
 ↓
explicit publish request
 ↓
dependency traversal
 ↓
privacy/confidentiality/IP/license checks
 ↓
sanitization
 ↓
GLOBAL CANDIDATE
 ↓
independent global testing
 ↓
GLOBAL VERIFIED
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

Private evidence does not automatically become global verification.

---



## 35. Recursive procedure recording mechanism

Procedure identity remains flat:

```text
Procedure A
Procedure B
Procedure C
```

Execution identity can be nested:

```text
Execution A
 └── Execution B
      └── Execution C
```

If A invokes B during execution:

```text
runtime relationship:
Execution A → Execution B
```

Record:

```text
A:v3 invoked B:v2 during Execution R
```

Do NOT automatically create:

```text
A:v3 depends_on B:v2
```

The canonical dependency is created only if A's procedure definition itself requires B.

Every B invocation gets its own:

```text
procedure
procedure version
applicability check
implementation
execution
verification
evidence
outcome
```



## 36. Agent-facing working-set projection mechanism

The `.stealth/` projection exists to reduce model context, not to mirror the database.

Canonical form:

```text
.stealth/
  context.md
  run.json
  meta.json
```

`context.md` is a small, regenerated, index-first projection containing only Claims, Procedures, Implementations, and coordination entries relevant to the current scope/run. It MUST support cheap navigation:

```text
head .stealth/context.md
grep -n 'claim:G-...' .stealth/context.md
grep -n 'payments\|retry' .stealth/context.md
```

The first router section MUST stay below a configured byte/token budget. When the working set exceeds that budget, partition by generated section files under `.stealth/index/` and keep the root router as pointers only. Do not make the agent load all partitions.

`run.json` is machine-readable and contains the exact ProcedureRun/Execution identifiers, current PlanNodes, node owners, dependencies, declared file read/write intents, status, verification state, and change cursor.

`meta.json` contains projection revision, workspace/scope IDs, canonical object versions/hashes, generated timestamp, and last durable change/sync cursor.

Files are regenerated atomically (`write temp → fsync where supported → rename`) and MUST never be accepted as canonical truth. Agent edits to projection files are proposals only and must go through MCP/API validation before canonical persistence.

---

## 37. Final ingestion acceptance test

For every source, the test suite should be able to assert the actual chain, not merely the final procedure row.

### Global

```text
Source exists
Artifact exists
Observation exists where extraction produced one
Claim exists where proposition was meaningful
EquivalentClaims exists where justified
Evidence exists where support exists
Procedure exists only when actionable procedure structure exists
Procedure references claims/evidence appropriately
Procedure has scope
Procedure has provenance
Procedure remains candidate until verification/admission
```



### Non-procedural source

```text
Source exists
Artifact exists
Observation(s) exist
Claim(s) may exist
NO fake Procedure
```



### Local execution

```text
Event
→ Trace
→ Episode
→ Artifact
→ Observation
→ Claim
→ Evidence
→ private Procedure candidate
```



### Publication

```text
private
→ explicit publish
→ policy checks
→ global candidate
```



### Recursive execution

```text
A
→ needs knowledge
→ B independently retrieved
→ B applicability
→ child execution B
→ B verification
→ A resumes
```

Every transition and lineage edge must be asserted.

---



## 38. Implementation order for this expanded ingestion work

Update the existing A-series to:

```text
A0  ontology/schema contract tests
A1  IngestionContext + provenance manifest
A2  Source/Artifact normalization
A3  Event/Trace/Episode normalization
A4  structural source classification
A5  Observation derivation engine
A6  Observation validation
A7  Claim derivation engine
A8  Claim normalization
A9  entity resolution
A10 claim identity/deduplication
A11 conflict detection
A12 EquivalentClaims assignment
A13 Evidence derivation/normalization
A14 Procedure candidate extraction
A15 Procedure validation
A16 Procedure ↔ Claim linking
A17 Procedure composition/reference validation
A18 Implementation extraction/validation + Procedure↔Implementation relation
A19 TestSpec generation
A20 result normalization
A21 verification
A22 applicability/invariant derivation
A23 procedure versioning/generalization
A24 relevant Claim working-set derivation
A25 canonical persistence
A26 embedding/indexing
A27 global SKILL refactor
A28 local schema-aligned learning
A29 `.stealth/` working-set projection
A30 local/cloud sync + scope
A31 publication hardening
A32 golden ingestion E2E
A33 historical corpus migration/backfill
A34 remove/quarantine legacy shortcuts
```



### Definition of done

No production ingestion path can:

```text
create a claim without provenance
create a procedure from non-procedural prose
create evidence without a supporting source/result
promote source assertion into experimental truth
create duplicate equivalent claims/procedures
lose scope
lose derivation lineage
silently globalize private knowledge
mark a procedure verified without verification evidence
```

And no ingestion stage may merely say:

```text
"X results in Y"
```

without implementing the explicit transformation, validation, identity, provenance, and failure behavior defined above.

---



# Part III — Plan B: MCP Procedure-Conditioned Execution Hardening

## B1 — Fix `auto`

`find_best_way` is the primary discovery entry point. It MUST select the best applicable Procedure(s) and return an executable assistance contract, not merely a search result.

`auto` determines the requested degree of Stealth involvement:

```text
find_best_way(auto)
 → normalize objective/context/constraints
 → retrieve Procedures
 → retrieve relevant Claims
 → evaluate applicability
 → resolve candidate Implementations
 → determine missing decision-critical facts
 → choose route:
      ask | assist | plan | execute | refuse
```

Routing rules are deterministic/testable at the policy level:

| Intent / state | Route |
|---|---|
| informational "best way/how should I" | assist |
| explicit plan request | plan |
| explicit change/execute/fix request + authorized environment | execute |
| decision-critical applicability unknown | ask |
| no acceptable Procedure but exploration is allowed | plan/execute exploratory run, clearly unverified |
| unsafe/unauthorized/missing mandatory execution mechanism | refuse |
| ambiguous intent with side effects | ask |

Explicit modes remain supported:

```text
lookup_only
plan_only
full_run
auto
```

`lookup_only` MUST NOT create an execution lease or imply execution provenance.

For `assist`, `plan`, and `execute`, the response MUST include a stable `procedure_run_id` (or the exact existing equivalent) once a Procedure is accepted for use. Subsequent MCP guidance is anchored to that run and exact Procedure version.

The product behavior is:

```text
retrieve Procedure
→ instantiate it for this environment
→ help the host execute THAT Procedure
→ detect deviations/missing requirements
→ verify completion against THAT Procedure
→ report evidence/learning
```

Do not degrade to "return SKILL.md text and hope the host follows it."

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

Every accepted Procedure run gets one durable context:

```yaml
procedure_run_id:
request_id:
objective:
caller_identity:
workspace_id:
scope:
procedure_id:
procedure_version:
route:
execution_plan_id:
task_graph_id:
trace_id:
parent_run_id:
parent_node_id:
status:
claim_working_set_revision:
implementation_bindings:
verification_plan_id:
```

`task_id` is not required as a canonical knowledge identifier. If an existing Task row is required for backward compatibility, record it as `legacy_task_id` and do not make new global semantics depend on it.

This context is the runtime join point between Procedure guidance, Claim retrieval, Implementation resolution, TaskGraph state, verification, and reporting.

---

## B4 — Stealth Execution Contract

The server enforces:

```text
RUN_CREATED
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

A run cannot claim Stealth procedural provenance without this chain. Every transition MUST be persisted transactionally with an idempotency key/version guard. Invalid transitions MUST fail closed with a typed error; they must not be silently coerced to the nearest valid state.

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



## B6 — Host-executed Procedure lease

For host-managed execution after Stealth selects a Procedure:

```text
find_best_way / continue_run
 → exact Procedure version + ProcedureRun lease
 → host executes with native/external tools
 → host periodically reports structured progress/evidence
 → Stealth guides the next Procedure step/subproblem
 → verify_completion
 → report_execution/finalize
```

The lease records the exact Procedure version, required checks, authorized scope, declared Implementation bindings where known, and expiry/revision. Host execution outside the lease may still occur, but it cannot be represented as verified Stealth Procedure execution without sufficient recorded evidence.

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

## Make sure you don't hardcode these things, and discuss before implementing those

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



## B14 — Re-search / continue during execution

The MCP MUST support both normal continuation of the selected Procedure and dynamic recursive retrieval for a genuinely new reusable subproblem.

Normal progression:

```text
continue_run(procedure_run_id, progress/evidence)
 → load pinned Procedure version
 → load current PlanNode/run state
 → load bounded relevant Claim refs
 → resolve Implementation options for current need
 → return the smallest next-action packet
```

Dynamic subproblem:

```text
current Procedure node
 → reusable subproblem detected by host or Procedure definition
 → find_best_way(
      goal=subproblem,
      parent_run_id=...,
      parent_node_id=...,
      current_context=...
   )
 → candidate child Procedure
 → applicability
 → exact version pin
 → child ProcedureRun/Execution
 → verification
 → child terminal
 → parent resumes
```

Do not require the host to manually orchestrate `search_procedures → get_procedure → check_applicability → execute` as four unrelated calls when the semantic operation is one `find_best_way`/child-run decision.

The child Procedure remains independent knowledge. Runtime invocation alone does not create a canonical Procedure dependency.

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



## B23 — Implementation Registry architecture

Use the existing Implementation Registry/provider abstraction as the single registry.

The global relation is many-to-many:

```text
Procedure
   ↕
ProcedureImplementation
   ↕
Implementation
```

`ProcedureImplementation` is first-class relation metadata, not a second registry. It MUST support:

```yaml
procedure_id:
procedure_version_constraint_or_id:
implementation_id:
implementation_version_constraint_or_id:
role: primary | supporting | partial | verification
supported_steps_or_capabilities:
applicability:
interface_binding:
evidence_refs:
status:
created_by:
created_at:
```

Do not invent numeric coverage/quality scores unless they come from recorded evaluation. Evidence belongs to the relation when it concerns how well a particular Implementation realizes a particular Procedure.

An Implementation is a durable, versioned description of a concrete mechanism and may be owned/hosted by Stealth, the user, or a third party:

```text
Implementation
├── provider
├── type
├── locator / resolution
├── invocation specification
├── input/output contract
├── requirements
├── execution location
├── provenance
└── license
```

Example:

```text
Graphify
  → primary for "Build code dependency graph"
  → primary/supporting for "Find callers"
  → supporting for "Assess change impact"
```

A tool builder MUST be able to submit/register an Implementation against one or more existing Procedures without creating a reusable TaskNode.

Do not create a second tool registry.

---

## B24 — Implementation resolution and binding

Resolution is separate from Procedure retrieval:

```text
Procedure
  ↓
candidate Implementations
  ↓
applicability
requirements
environment
permissions
availability
verification/evidence
freshness
cost / latency
  ↓
selected Implementation:v
  ↓
bind to Execution
```

The Execution pins the exact Procedure version, Implementation version, resolved locator, and immutable artifact digest where available.

If resolution is ambiguous or unavailable, route to `ask`, `plan`, or `refuse` rather than silently selecting an unsuitable mechanism.

---

## B25 — Implementation adapter architecture

Provider-specific execution logic belongs behind adapters:

```text
Implementation
      ↓
Adapter Resolver
      ↓
Binary | MCP | HTTP/API | Container | WASM | Model | Local
      ↓
concrete runtime
```

Conceptual adapter contract:

```text
resolve()
validate()
prepare()
invoke()
collect_result()
collect_artifacts()
collect_evidence()
cleanup()
```

Adapters are execution infrastructure, not new ontology.

---

## B26 — Black-box Implementation verification

Black-box means Stealth verifies observable behavior at the implementation boundary:

```text
input
  ↓
Implementation
  ↓
observable output/effects
  ↓
Verification
```

Stealth does not need source-code knowledge or internal algorithm visibility. The implementation may be compiled C/Rust, proprietary software, an MCP tool, or a remote service.

Verification can inspect output schemas, artifacts, semantic correctness, postconditions, side effects, errors, performance, and security constraints.

Evidence must identify how the result was obtained. Distinguish Stealth-observed execution from provider-reported, user-reported, or third-party-attested evidence.

Never use a permanent `verified=true` as the only verification state.

---

## B27 — External implementation hosting

Externally hosted implementations are first-class:

```text
Procedure
  ↓
Implementation
  type = HTTP_API / MCP_TOOL / ...
  execution_location = THIRD_PARTY_HOSTED
  ↓
external provider
```

Record what Stealth requested, the concrete endpoint/tool/version, returned results, observed artifacts, and what was independently verified versus merely reported.

External hosting changes the observability/provenance boundary; it does not make an Implementation conceptually different.

---

## B28 — Local sandbox implementation

For Stealth-controlled execution:

```text
Implementation
  ↓
resolve concrete artifact
  ↓
verify identity/digest
  ↓
create isolated runtime
  ↓
mount declared inputs
  ↓
invoke
  ↓
capture outputs/artifacts
  ↓
verify
  ↓
record evidence
```

Sandbox policy is derived from implementation requirements, execution authorization, workspace policy, and security policy.

Registration, authorization, sandboxing, and verification are separate controls.

---

## B29 — Implementation lifecycle

Implementation lifecycle is evidence-based:

```text
DISCOVERED
  ↓
REGISTERED
  ↓
RESOLVABLE
  ↓
AVAILABLE
  ↓
VERIFIED_IN_CONTEXT
  ↓
REUSED
```

Failures may yield:

```text
UNAVAILABLE
INCOMPATIBLE
FAILED_EXECUTION
FAILED_VERIFICATION
STALE
RETIRED
```

Historical evidence remains. A new implementation version does not inherit verification automatically.

---

## B30 — Runtime relationship between Procedure, Implementation, and Execution

```text
Procedure P42:v3
      ↓ resolve
Implementation I17:v1.4.2
      ↓ bind
Execution E991
      ├── invocation
      ├── artifacts
      ├── observations
      ├── outcome
      └── verification
```

Recursive execution remains separate:

```text
Execution E991
   └── child Execution E992
          └── Procedure P17:v2
                 └── Implementation I88:v4
```

Reusable graph:

```text
Procedure → Procedure
Procedure → Claim
Procedure → Implementation
```

Runtime graph:

```text
Execution → Execution
Execution → Implementation invocation
Execution → Artifact
Execution → Evidence
```

---

## B31 — Execution and publication boundaries

Keep three boundaries explicit:

```text
Knowledge:
  Claims / Procedures / Implementations

Execution:
  concrete invocations, runtime state, artifacts, outcomes

Publication:
  private knowledge → explicit global candidate
```

Execution produces evidence and candidates. Ingestion gates decide whether candidates become reusable knowledge. Publication policy decides whether private knowledge becomes global.

---

## B32 — Minimal MCP surface and exact host contract

The production MCP surface SHOULD expose a small semantic interface and hide backend graph mechanics.

Required public operations (reuse equivalent existing names where they already exist):

```text
find_best_way
continue_run
inspect_procedure
get_relevant_claims
get_run_context
verify_completion
report_execution
submit_procedure
submit_implementation
```

Do not expose separate low-level tools for every internal table/edge merely because those services exist.

### `find_best_way`

Input MUST support:

```yaml
goal:
context:
constraints:
current_state:
current_subproblem:
available_tools:
exclusions:
desired_outputs:
mode:
parent_run_id:
parent_node_id:
```

All fields except `goal`/mode-required identifiers may be omitted only when genuinely unknown. Unknown values remain UNKNOWN.

Output MUST contain one of:

```text
NEEDS_CLARIFICATION
NO_APPLICABLE_PROCEDURE
ASSIST
PLAN_READY
EXECUTION_READY
REFUSED
```

For a selected Procedure, return:

```yaml
procedure_id:
procedure_version:
procedure_run_id:
why_selected:
applicability:
alternatives:
relevant_claim_refs:
implementation_candidates:
missing_required_implementations:
success_criteria:
verification_plan:
next_action_packet:
```

No fabricated confidence. No fabricated Implementation. No hidden fallback to nearest semantic result when applicability/evidence is absent.

### `continue_run`

Input:

```yaml
procedure_run_id:
observations_or_progress:
artifact_refs:
tool_results:
current_environment_delta:
```

Output is a bounded next-action packet tied to the pinned Procedure version:

```yaml
current_phase_or_node:
objective:
required_preconditions:
relevant_claim_refs:
recommended_implementations:
required_checks:
allowed_branches:
blocking_unknowns:
next_when_satisfied:
```

### `get_relevant_claims`

Returns compact independent Claim references first. It MUST NOT return the whole Claim graph by default.

### `verify_completion`

Evaluates explicit Procedure/run success criteria. It never equates host self-report with verification.

### `report_execution`

Persists actual outcome/evidence/deviations and feeds the Observation/Claim learning pipeline. It MUST be idempotent.

---

## B33 — Procedure-conditioned execution assistance

Once `find_best_way` selects a Procedure, Stealth stays anchored to that Procedure for the run.

```text
selected Procedure
→ current procedure step/phase
→ relevant Claims
→ best available Implementation(s)
→ checks/postconditions
→ host action
→ progress/evidence
→ next Procedure step/branch
```

Stealth's job is not merely retrieval. It actively reduces implementation error by:

```text
- exposing decision-critical Claims only when needed;
- resolving concrete tools/Implementations for the current Procedure need;
- detecting unsatisfied preconditions;
- keeping track of required checks/postconditions;
- detecting material deviation from the selected Procedure;
- re-searching only when a new reusable subproblem or changed environment justifies it;
- refusing to mark the Procedure complete until required verification is satisfied.
```

A canonical skill package/Markdown source is input knowledge. It is not itself proof that the host followed or successfully executed the skill.

---

## B34 — Verification ladder including human verification

Verification methods are explicit evidence classes:

```text
SELF_REPORT
ARTIFACT_INSPECTION
DETERMINISTIC_CHECK
INDEPENDENT_AGENT
HUMAN_REVIEW
REAL_WORLD_OUTCOME
```

Each VerificationPlan item defines:

```yaml
criterion_id:
statement:
method:
required_or_optional:
evidence_type:
target_artifacts_or_files:
command_or_probe_if_deterministic:
questions_if_agent_or_human:
success_condition:
failure_condition:
timeout_or_expiry:
```

Human review is optional unless Procedure/policy marks it required.

For human review, generate a bounded review packet:

```text
objective
exact Procedure/version
criterion(s) being reviewed
exact files/diff/ranges to inspect
relevant Claim refs
automated evidence already collected
specific yes/no/structured questions
```

Do not ask a human to "review the repo". Do not accept `approved=true` without reviewer identity, reviewed targets, criterion answers, timestamp, and Evidence linkage.

Terminal verification states:

```text
CLAIMED_DONE
CHECKED
VERIFIED
INDEPENDENTLY_VERIFIED
FAILED_VERIFICATION
INCONCLUSIVE
```

The strongest satisfied state is derived from actual Evidence; it is not writable directly by the host.

---

## B35 — Low-context `.stealth/` projection

Use one compact human/agent-readable context file plus machine-readable runtime files:

```text
.stealth/
  context.md
  run.json
  meta.json
```

Do not maintain large duplicated `claims.md`, `procedures.md`, `implementations.md`, `run.md`, and `exploration.md` files unless an existing integration strictly requires them. If legacy files exist, generate them from the same projection service and mark them deprecated.

`context.md` contains a small router and only the current working set:

```text
[ROUTER]
[LOCAL CLAIMS]
[RELEVANT GLOBAL CLAIMS]
[SELECTED PROCEDURES]
[RELEVANT IMPLEMENTATIONS]
[COORDINATION]
```

The router is hierarchical when needed:

```text
root → domain → topic → object anchor
```

The root MUST remain bounded. Detailed blocks are fetched/read only after `grep`/anchor selection.

`run.json` is authoritative only as a projection of canonical run state and includes:

```text
procedure_run_id
execution_plan_id/task_graph_id
node states
node owners
dependencies
declared read/write file intents
implementation bindings
verification state
change cursor
```

`meta.json` contains projection revision/hashes/versions and sync cursor.

Projection writes MUST be atomic. Projection reads MUST tolerate a missing/stale projection by rehydrating from canonical state; they MUST NOT invent replacement content.

---

## B36 — Multi-agent coordination on the execution graph

For coordinated runs, each executable PlanNode MUST be able to declare before substantial work:

```yaml
node_id:
owner_agent_id:
objective:
depends_on:
status:
files:
  read_exact:
  read_globs:
  write_exact:
  write_globs:
symbols_expected_to_modify:
lease_expires_at:
```

File declarations are advisory coordination leases, not OS filesystem locks.

Before assigning/starting a node, detect:

```text
exact write/write overlap
write/read overlap when ordering matters
overlapping write globs
dependency violations
expired/stale leases
```

On conflict, the server returns a typed conflict containing the conflicting run/node/owner and exact overlapping files/globs. It MUST NOT silently allow two nodes to claim the same write scope and later pretend the run was coordinated.

Claims remain separate from coordination:

```text
Claims = what is believed/known.
Execution graph = what is planned/running.
File intents = where concrete work is expected.
```

No WebSocket is required for correctness. If a change feed exists, it is cursor-based and durable; WebSocket/SSE delivery is an optimization over persisted events.

---

## B37 — Global hierarchical retrieval indexes

Global Claims, Procedures, and Implementations remain canonical database objects. Hierarchical indexes are derived routing structures, not a second knowledge store.

Required retrieval stages:

```text
query
→ coarse domain/topic routing
→ lexical + semantic candidate retrieval
→ scope/applicability filtering
→ evidence/status filtering
→ rerank
→ exact canonical objects
```

Index entries MUST carry canonical object ID + version/family identity. They MUST be rebuildable from canonical storage.

Do not use a single monolithic Markdown global index. Implement the hierarchy as database/materialized index metadata or the existing search/index infrastructure.

Index freshness MUST be measurable:

```text
canonical_revision
indexed_revision
index_lag
```

A stale index may return candidates, but authoritative applicability/version checks MUST occur against canonical rows before selection/execution.

---

## B38 — No synthetic/fallback execution rule

This hardening pass fails if production code silently falls back to synthetic success.

The following are forbidden in production paths:

```text
placeholder Procedure/Claim/Implementation IDs
fake embeddings
fake tool results
synthetic benchmark/evidence records
stub execution success
"verified" derived from source wording or model confidence
nearest-neighbor Procedure treated as applicable without applicability checks
invented Implementation when resolution fails
automatic use of mock/fake adapters outside explicit test fixtures
catch-all exception paths that return success/empty-safe objects
```

Mocks/fakes are allowed ONLY under explicit test configuration and MUST be impossible to activate in production through missing credentials/configuration.

Missing dependencies/configuration produce typed terminal/nonterminal errors such as:

```text
NO_APPLICABLE_PROCEDURE
MISSING_IMPLEMENTATION
IMPLEMENTATION_UNAVAILABLE
AUTH_REQUIRED
APPLICABILITY_UNKNOWN
VERIFICATION_INCONCLUSIVE
UNAUTHORIZED
SOURCE_FETCH_FAILED
INDEX_STALE
```

Each production fallback branch MUST be enumerated in tests. Unknown/unhandled states fail closed.

---

# Part IV — Combined implementation order

Execute this as one gated hardening program. Do not proceed to a later gate while required tests for the previous gate are red.

```text
G0  Freeze baseline SHA + schema/API contract + production config inventory
G1  IngestionContext / provenance manifest
G2  Source + Artifact normalization and immutable block addressing
G3  Security/policy screening
G4  Observation extraction/validation
G5  Independent Claim normalization, belief, dedup, conflict/family handling
G6  Evidence normalization + independence accounting
G7  Procedure extraction/validation/versioning
G8  Procedure↔Claim typed refs + applicability integration
G9  Procedure composition validation
G10 Implementation Registry validation + Procedure↔Implementation many-to-many relation
G11 Static/global ingestion refactor and corpus migration/backfill
G12 Local schema-aligned learning + scope/private sync
G13 `.stealth/` projection service
G14 Global hierarchical retrieval/index freshness
G15 `find_best_way` route + ProcedureRun contract
G16 `continue_run` + bounded Claim working-set retrieval
G17 ExecutionRecorder + durable state machine
G18 Host-executed Procedure lease + planned-vs-actual recording
G19 Recursive child ProcedureRuns + recovery/resume/cycle protection
G20 Implementation resolution/binding/adapters + no-synthetic rule
G21 Multi-agent node ownership/file-intent conflict detection
G22 Verification ladder + optional human review
G23 `report_execution` → Observation/Evidence/Claim-candidate learning
G24 Publication/privacy/license/IP dependency traversal
G25 Full MCP/API/auth/rate-limit/idempotency/concurrency hardening
G26 Golden E2E + fault injection + migration rollback tests
G27 Remove/quarantine legacy shortcuts only after replacements are proven
G28 Freeze release candidate and run acceptance matrix
```

Every gate MUST produce:
- exact files/migrations changed;
- exact tests added;
- exact commands run;
- exact pass/fail counts;
- explicit remaining blockers.

No gate is complete because code compiles or an API returns 200.

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
| EquivalentClaims              | local/derived                               | cloud private scope                                                 | cloud global                         |
| Evidence                      | local/raw + structured metadata             | cloud structured metadata, sensitive content local if required      | only cleared evidence                |
| Procedure                     | SQLite/cache                                | canonical cloud private procedure                                   | Global Commons                       |
| Implementation                | local/cache + resolution metadata           | cloud private registry + resolver metadata                         | global only if cleared               |
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
             ┌────────────────────┴────────────────────┐
             │                                         │
        KNOWLEDGE / INGESTION                         MCP
             │                                         │
      Source → Artifact                         Claude / Agent
             │                                         │
        Observation                              Auto Router
             │                              assist / plan / execute
          Claim                                      │
             │                                Execution Contract
       Procedure Graph                               │
             │                                   Resolution
             ├───────────────┐                       │
             │               │                       ▼
             │               └──────────────> Implementation
             │                                   Registry
             │                                      │
             │                               Adapter Resolver
             │                                      │
             │                         Binary / MCP / API /
             │                         Container / WASM / Model
             │                                      │
             └──────────────────────────────────────┘
                                                    │
                                               Execution E
                                                    │
                                  ┌─────────────────┼─────────────────┐
                                  │                 │                 │
                                Events           Artifacts          Child E
                                  │                 │                 │
                                  └──────────┬──────┘                 │
                                             ▼                        │
                                        Observations                  │
                                             │                        │
                                          Claims                       │
                                             │                        │
                                          Evidence ◄──────────────────┘
                                             │
                                         Verification
                                             │
                                          Outcome
                                             │
                                          Learning
                                             │
                                    USER_PRIVATE candidate
                                             │
                                   explicit publication only
                                             ▼
                                      GLOBAL CANDIDATE
                                             │
                                  ingestion gates +
                                  independent verification
                                             ▼
                                      GLOBAL KNOWLEDGE
```

Critical relationships:

```text
Procedure ──requires/supports/derived_from──> Claim
Procedure ──implemented_by──────────────────> Implementation
Procedure ──composes/requires───────────────> Procedure

Execution ──executes────────────────────────> Procedure
Execution ──invokes─────────────────────────> Implementation
Execution ──child───────────────────────────> Execution
Execution ──produces/references─────────────> Artifact
Execution ──produces────────────────────────> Observation
Verification ──evaluates────────────────────> Execution/Evidence
Evidence ──supports─────────────────────────> Claim/Procedure/Implementation/Verification
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

# Testing

This section is normative. "Tested" means the real production path was exercised with assertions on durable state, provenance, authorization, and failure semantics.

## T1 — Test environment separation

Maintain explicit configurations:

```text
TEST
STAGING
PRODUCTION
```

Mocks/fakes may exist only in TEST. Starting STAGING/PRODUCTION with a fake provider, fake embedder, fake verifier, in-memory substitute for required durable storage, or missing mandatory credential MUST fail startup.

A test MUST prove production configuration cannot activate test adapters.

## T2 — Database and migration tests

For every new migration:

```text
fresh database → migrate head
previous production-compatible revision → migrate head
migrate with representative existing rows
constraints/indexes/FKs verified
backfill idempotency verified
duplicate/retry run verified
```

If downgrade is supported, test it. If downgrade is intentionally unsupported, document the exact irreversible migration and operational recovery procedure.

## T3 — Ingestion golden tests

Use real fixture artifacts for:
- procedural Markdown;
- non-procedural prose;
- HTML;
- repository/config/workflow;
- execution trace;
- benchmark/result;
- tool/API schema.

Assert every created canonical object and every absent object. A non-procedural source MUST NOT create a fake Procedure.

## T4 — Claim graph tests

Assert:
- Claim with valid provenance and zero TaskNode links is valid;
- Claim retrieval is independent of Procedure retrieval;
- one Claim can support multiple Procedures;
- contradictory Claims coexist and are linked;
- belief/status changes cite Evidence;
- global Claim projected locally preserves global ID/version/scope;
- `UNKNOWN` applicability is never treated as `TRUE`.

## T5 — Procedure tests

Assert:
- exact Procedure version pinning;
- applicability TRUE/FALSE/UNKNOWN;
- canonical composition refs only when definition requires them;
- runtime child retrieval does not create canonical dependency;
- recursive composition cycle rejection;
- source assertion never marks Procedure verified.

## T6 — Implementation tests

Use at least three real adapter classes in staging where available (for example local executable, HTTP/API, MCP). Assert:
- many-to-many Procedure↔Implementation;
- partial/supporting roles;
- unavailable implementation returns typed error;
- no fabricated fallback implementation;
- exact implementation version/locator/digest pinning;
- new implementation version does not inherit old verification.

## T7 — MCP contract tests

For every public MCP tool, validate:
- input schema;
- authentication/identity propagation;
- scope authorization;
- idempotency;
- typed failures;
- response size bound;
- no secret leakage;
- no hidden synthetic data.

Test `find_best_way` across:
- assist;
- plan;
- execute;
- clarification;
- no applicable Procedure;
- unavailable required Implementation;
- unauthorized;
- unsafe.

## T8 — Procedure-conditioned execution E2E

Real scenario:

```text
host → find_best_way
→ Procedure P:v pinned
→ ProcedureRun created
→ relevant Claims selected
→ Implementation resolved
→ continue_run
→ actual host/tool evidence reported
→ verify_completion
→ report_execution
→ terminal outcome
→ observations/evidence/claim candidates persisted
```

Assert the MCP continues helping execute the same selected Procedure rather than returning unrelated generic advice.

## T9 — Recursive child execution E2E

Exercise:

```text
A → child B → child C
```

with process death:
- before child creation;
- during B;
- after B terminal but before A resume;
- during C verification.

Assert no completed side-effecting node is rerun, parent state never remains orphaned, lineage remains intact, and idempotency keys prevent duplicate finalization/evidence.

## T10 — Verification tests

For each verification class:
- self-report;
- artifact;
- deterministic check;
- independent agent;
- human review;
- real-world outcome;

assert the maximum verification state obtainable.

A self-report MUST NOT satisfy an independently verifiable deterministic criterion.

Human review fixture MUST include reviewer, exact inspected targets, criterion answers, and Evidence ref.

## T11 — `.stealth/` context-budget tests

Generate a workspace with thousands of Claims/Procedures.

Assert:
- root `context.md` router stays within configured byte/token budget;
- only relevant global Claims are projected;
- stable anchors resolve;
- projection regeneration is atomic;
- stale projection is detected through `meta.json`;
- full canonical knowledge is never serialized merely because the workspace is large;
- `head/grep/exact-block` navigation retrieves the needed object without reading the full corpus.

## T12 — Multi-agent coordination tests

Create two agents with overlapping plans.

Assert:
- exact write/write conflict detected;
- glob overlap detected;
- non-overlapping work allowed;
- expired lease handled;
- dependency violation rejected;
- conflict response names exact nodes/owners/files;
- Claims are unaffected by coordination leases.

## T13 — Security tests

At minimum:
- path traversal;
- symlink escape;
- absolute sibling;
- auth spoofing;
- replayed approval;
- replayed report_execution;
- cross-workspace Claim/Artifact access;
- private→global leakage;
- prompt injection in ingested documents;
- malicious Implementation metadata;
- SSRF through HTTP implementation locator where relevant;
- unsafe shell argument construction;
- secret redaction/logging.

## T14 — Load/latency tests

Measure at p50/p95/p99:
- `find_best_way`;
- Claim lookup;
- `continue_run`;
- applicability;
- index query;
- report ingestion;
- projection regeneration.

Run concurrent writers/readers against Claim updates, node transitions, and report finalization. State exact concurrency level, dataset size, hardware, and DB configuration. Do not claim production capacity without these numbers.

## T15 — Acceptance matrix

The final report MUST mark every requirement in this document:

```text
CLOSED
PARTIAL
OPEN
```

`PARTIAL` or `OPEN` on any release-critical requirement means:

```text
EXTREME FINAL HARDENING INCOMPLETE
```

Only when every release-critical requirement is CLOSED and all production E2E/security/migration tests pass may the report say:

```text
EXTREME FINAL HARDENING COMPLETE
```

No TODOs, placeholder adapters, synthetic success paths, unexecuted required migrations, or "future work" labels are permitted for release-critical items.

