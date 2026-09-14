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
- Do not build second registries, provenance graphs, evidence stores, or task schedulers.



# Part II — Plan A: Fix Ingestion



## A0 — Freeze the canonical ingestion contract

Every ingestion source must enter through:

```text
Source → Artifact
       → Event/Trace/Episode where applicable
       → Observation
       → Claim
       → EquivalentClaims where applicable
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

## A12 — Local storage specification

specify list what should be added locally, and transferred globally

### Local machine



### Stealth cloud



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

The canonical chain is:

```text
Source
  ↓
Artifact
  ↓
Observation
  ↓
Claim
  ↓
EquivalentClaims
  ↓
Evidence
  ↓
Procedure candidate
  ↓
Task
  ↓
Implementation
  ↓
TestSpec
  ↓
Execution/TestRun
  ↓
Result
  ↓
Verification
  ↓
Admission
  ↓
Canonical object
  ↓
Embedding/index
```

Branches are allowed. For example, a source can produce claims without producing a procedure.

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



## 20. Procedure → Task mechanism

A Task is created only when a procedure unit is independently reusable.

Test:

```text
Can this unit be invoked independently?
```

If yes:

```text
Task candidate
```

If no:

```text
procedure step only
```

Task must contain:

```text
goal
inputs
preconditions
action
outputs
failure conditions
verification
```

Do not generate tasks simply from headings.

---



## 21. Task → Implementation mechanism

An Implementation is created only when there is an actual executable mechanism.

Examples:

```text
CLI
script
MCP tool
WASM
deterministic function
SLM
frontier model
```

Required fields:

```yaml
provider:
locator:
invocation:
version:
requirements:
provenance:
license:
```

A textual suggestion such as:

```text
"use AST parsing"
```

is not an Implementation.

It is knowledge/procedure content until an executable implementation is identified.

Execution cannot silently invent an implementation.

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

Task representation should contain:

```text
goal
inputs
outputs
constraints
```

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
A17 Task extraction/validation
A18 Implementation extraction/validation
A19 TestSpec generation
A20 result normalization
A21 verification
A22 applicability/invariant derivation
A23 procedure versioning/generalization
A24 admission gates/scoring
A25 canonical persistence
A26 embedding/indexing
A27 global SKILL refactor
A28 local schema-aligned learning
A29 local/cloud sync + scope
A30 publication hardening
A31 golden ingestion E2E
A32 historical corpus migration/backfill
A33 remove/quarantine legacy shortcuts
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
6.  EquivalentClaims integration
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
| EquivalentClaims              | local/derived                               | cloud private scope                                                 | cloud global                         |
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

# Testing

Evidence