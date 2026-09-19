You are working on the existing StealthLab repository.

This is the FIRST major production implementation pass.

Your job is to turn the current ingestion system into the canonical knowledge
and Goal layer for Stealth.

Do not build a parallel architecture.

Audit the current production code and schema first, then extend the live paths.

This is not a design-only task.

======================================================================
0. PRODUCT MODEL
======================================================================

Stealth's canonical conceptual model is:

CLAIM
    what is true.

GOAL
    what outcome is desired.

PROCEDURE
    how to decompose a Goal into lower-level Goals.

IMPLEMENTATION
    how to directly realize a Goal.

Execution later recursively resolves:

Goal
├── direct Implementation
└── Procedure
      └── subgoals
            ├── Implementation
            └── Procedure
                  └── ...

Goal is the central interface between abstraction levels.

Goal is also the unit of:

    search
    retrieval
    routing
    benchmarking
    cost estimation
    marketplace competition
    improvement
    execution economics

======================================================================
1. AUDIT THE LIVE SYSTEM FIRST
======================================================================

Inspect the actual reachable production paths for:

    skill ingestion
    trace ingestion
    source parsing
    admission/screening
    Claim extraction
    Procedure extraction
    Implementation extraction
    embeddings
    retrieval documents
    deduplication
    provenance
    evidence
    implementation_registry
    procedure_implementations
    knowledge_nodes
    supersession/versioning
    MCP exposure
    DB schema
    object storage if present

Verify the actual current architecture.

Do not rely only on docs/comments.

Return a concise audit map, then continue implementing in the same task.

======================================================================
2. GOAL MUST BECOME A CANONICAL LIGHTWEIGHT OBJECT
======================================================================

Goal is important enough to have stable identity.

Do NOT make Goal a giant knowledge ontology.

Create/reuse a lightweight canonical representation with semantics equivalent to:

Goal:
    id
    canonical_name
    description
    input_schema where useful
    expected_outcome
    verification_requirement
    scope
    status
    owner/source
    created_from
    version
    aliases/tags where useful

Goal should remain significantly lighter than Procedure or Implementation.

But it must have stable IDs because:

    Procedure achieves Goal
    ProcedureStep references Goal
    Implementation satisfies Goal
    Benchmark targets Goal
    cost is estimated at Goal level
    submissions target Goal
    frontend searches Goal
    execution records Goal

======================================================================
3. GOAL RELATIONSHIPS
======================================================================

Support:

Procedure:
    achieves_goal_id

ProcedureStep:
    goal_id

Implementation:
    goal_id

Conceptually:

Goal G1
    ↓
Procedure P1
    ↓
Step S1 → Goal G2
Step S2 → Goal G3

and:

Goal G2
    ↓
Implementation I7

Do not force one Procedure per Goal.

Support many alternatives.

======================================================================
4. PROCEDURE SEMANTICS
======================================================================

Procedure is an abstract reusable decomposition of a Goal.

A Procedure contains:

    goal it achieves
    applicability
    Steps
    Step dependencies
    invariants
    failure modes
    verification requirements

ProcedureStep should no longer contain duplicated semantic text where a Goal
already expresses that meaning.

Conceptually:

ProcedureStep:
    step_id
    goal_id
    ordering/dependencies
    Procedure-specific bindings/constraints
    optional contextual description

The reusable semantic outcome lives in Goal.

======================================================================
5. IMPLEMENTATION SEMANTICS
======================================================================

Implementation directly attempts a Goal.

Implementation must preserve:

    goal_id
    name
    description
    kind
    provider
    version
    lifecycle/status
    access/locator
    invocation
    input contract
    output contract
    requirements
    auth requirements
    resource requirements
    expected outcome
    verification contract
    provenance
    ownership
    lineage
    scope
    execution location

Use existing Implementation schema wherever possible.

Do not create a replacement Implementation architecture.

======================================================================
6. INGEST GOALS FROM SOURCES
======================================================================

Every source ingestion path should be capable of producing:

    0..N Claims
    0..N Goals
    0..N Procedures
    0..N Implementations
    relations between them

Sources include:

    SKILL.md
    AGENTS.md
    CLAUDE.md
    runbooks
    GitHub workflows
    package.json
    Makefile
    Justfile
    Taskfile
    shell scripts
    Python entry points
    MCP schemas
    OpenAPI specs
    docs
    execution traces
    papers

Do not require every source to produce every object type.

Zero objects is valid.

======================================================================
7. GOAL EXTRACTION
======================================================================

Goal extraction must answer:

    What reusable outcome is being attempted?

Examples:

"find all references to symbol X"
    → reference_search

"regenerate derived API bindings"
    → artifact_regeneration

"verify authentication behavior"
    → authentication_behavior_verification

"deploy safely to production"
    → safe_deployment

"modify generated API safely"
    → high-level Goal which may have a Procedure decomposition

Goals may exist at arbitrary levels of abstraction.

Do not force all Goals to be atomic.

======================================================================
8. GOAL NORMALIZATION / DEDUPLICATION
======================================================================

The corpus will produce many synonymous Goal formulations.

Examples:

    find callers
    find references
    locate symbol usages

may represent the same Goal.

Implement robust deduplication using:

1. exact canonical name match
2. aliases
3. normalized semantic representation
4. embedding similarity where justified
5. LLM adjudication only for ambiguous candidates

Do NOT automatically merge semantically distinct Goals merely because text
similarity is high.

Preserve aliases.

Support explicit merge/review workflow if ambiguity exists.

======================================================================
9. GLOBAL VS LOCAL GOALS
======================================================================

GLOBAL Goal:
    reusable across projects/contexts

LOCAL Goal:
    repo/workspace-specific desired outcome

Prefer generalized global Goals where possible.

Examples:

GLOBAL:
    regenerate derived artifacts

LOCAL:
    regenerate src/generated from schema/api.yaml

The local outcome should usually be a contextual binding of the global Goal,
not a new global Goal.

Avoid corpus explosion from accidental local specificity.

======================================================================
10. CLAIMS REMAIN WORLD STATE
======================================================================

Claim remains:

    what is true.

Examples:

    schema/api.yaml is source-of-truth
    generated code must not be edited directly
    auth decisions live in src/auth/core
    pnpm generate-api regenerates bindings

Claims should be:

    atomic
    scoped
    provenance-backed
    versioned
    evidence-linked

Do not make Claims children of Procedures.

Claims later ground Goals and Procedures into local reality.

======================================================================
11. PROCEDURE EXTRACTION
======================================================================

Extract Procedure only where a source genuinely expresses a reusable
decomposition.

Example:

Goal:
    modify generated API safely

Procedure:
    identify source-of-truth
    modify source
    regenerate artifacts
    inspect delta
    verify consistency
    verify behavior

Each Step should point to an existing/new Goal.

Avoid:

    source numbered list
      → blindly create runtime nodes

Procedure is reusable method, not execution DAG.

======================================================================
12. IMPLEMENTATION EXTRACTION
======================================================================

Extract concrete mechanisms separately.

Examples:

    rg
    LSP references
    pnpm generate-api
    pytest tests/api
    MCP tool call
    shell command
    API endpoint
    Claude prompt
    Cline task
    GitHub Action
    Python function

Each Implementation should satisfy a Goal.

Use deterministic parsing whenever possible for:

    package scripts
    CLI commands
    shell scripts
    workflow jobs
    MCP tool schemas
    OpenAPI operations
    tests

Use LLM only for semantic interpretation:

    Goal
    expected outcome
    applicability
    verification semantics
    abstraction

======================================================================
13. SKILL.md IS A SOURCE, NOT A CANONICAL OBJECT
======================================================================

A Skill should be treated as a package/source.

SKILL.md
    ↓
semantic decomposition
    ↓
Goals
Claims
Procedures
Implementations
verification knowledge

Do not create:

Skill == Procedure
Skill == Implementation

unless the content genuinely maps that way.

======================================================================
14. VERIFICATION CONTRACT
======================================================================

Every Implementation should expose:

    expected_outcome
    verification_contract

Verification types may include:

    deterministic
    test
    external_system
    LLM
    human

Self-report alone must not count as trusted verification.

Example:

Goal:
    generated_consistency_verification

Implementation:
    pnpm generated-drift

Expected outcome:
    generated files match schema

Verification:
    exit_code == 0

======================================================================
15. OWNERSHIP / ATTRIBUTION
======================================================================

Preserve economic provenance from day one.

For Goals, Procedures, Implementations preserve:

    source
    author/contributor where known
    owner
    derived_from
    lineage
    license
    ingestion source
    version

Do not implement payouts yet.

But do not discard attribution needed later.

======================================================================
16. STORAGE-FIRST INGESTION
======================================================================

Storage is a major constraint.

Use this pipeline:

source fetch
    ↓
content hash
    ↓
exact dedup
    ↓
local/deterministic parse
    ↓
screen/admission
    ↓
normalize
    ↓
semantic extraction
    ↓
Goal/Claim/Procedure/Implementation dedup
    ↓
ONLY THEN persistent remote writes
    ↓
selective embeddings

Do not persist millions of raw duplicate source files in Postgres.

Use content-addressed/object storage only where justified.

======================================================================
17. SELECTIVE EMBEDDINGS
======================================================================

Do not embed everything automatically.

Preferred V1:

Goals:
    embed globally

Procedures:
    embed globally

Implementations:
    embed selectively; Goal lookup should be primary

Global Claims:
    embed selectively

Local Claims:
    lexical/structured/rg first

Relations:
    never embed

Execution events:
    never embed by default

Raw sources:
    do not embed by default

Document final policy.

======================================================================
18. GOAL RETRIEVAL
======================================================================

Goal retrieval becomes a primary product capability.

Support:

    lexical search
    aliases
    semantic/vector retrieval
    structured filters
    scope
    status
    Goal hierarchy/context where applicable

Return:

    Goal identity
    description
    expected outcome
    verification requirement
    available Procedures
    available Implementations
    available evidence/benchmark summary

======================================================================
19. USER-CREATED GOALS
======================================================================

The system must support future frontend creation.

Users should be able to:

    search Goal
    inspect close matches
    select existing Goal
    create new Goal if none is satisfactory

New Goal should support:

    name
    description
    expected outcome
    verification requirement
    optional input contract
    scope
    owner

Before creation:

    search near matches
    show likely aliases

Allow "create anyway" where genuinely distinct.

New user Goals begin in an appropriate candidate lifecycle.

======================================================================
20. GOAL QUALITY
======================================================================

Prevent Goal explosion.

Reject or review Goals that are:

    pure implementation names
    meaningless labels
    hyper-specific accidental local bindings
    duplicates
    untestable vague outcomes

Examples:

BAD:
    use rg command
    fix stuff
    run this exact command in repo X

GOOD:
    find references
    verify generated consistency
    safely deploy service
    inspect semantic code delta

======================================================================
21. TRACE INGESTION
======================================================================

Execution traces should be able to produce:

    observations
    Claims
    Goal candidates
    Procedure candidates
    Implementation evidence

Do not conflate:

    document assertion
    execution evidence

A successful execution can strengthen:

    Implementation × Goal × Context

but must retain evidence provenance.

======================================================================
22. EVIDENCE
======================================================================

Preserve distinctions between:

    source/document evidence
    execution evidence
    benchmark evidence
    human review evidence

Do not count document existence as execution success.

======================================================================
23. RETRIEVAL DOCUMENTS
======================================================================

Update canonical retrieval representations so Goal retrieval is excellent.

Goal retrieval docs should include:

    canonical name
    description
    aliases
    expected outcome
    verification semantics
    common contexts
    related Procedure names where useful

Avoid stuffing every relationship into one text blob.

======================================================================
24. BACKWARD COMPATIBILITY
======================================================================

Existing Procedure/Implementation data must survive.

Backfill Goal identities from existing:

    ProcedureStep.goal
    capability fields
    Implementation semantics

where possible.

Do not require full reingestion unless genuinely unavoidable.

Generate migration/backfill tooling.

======================================================================
25. MCP / API
======================================================================

Expose canonical capabilities equivalent to:

search_goals
inspect_goal
list_goal_procedures
list_goal_implementations
create_goal where authorized

search_claims
inspect_claim

search_procedures
inspect_procedure

inspect_implementation

Do not overgrow the MCP surface if generic tools already exist.

======================================================================
26. TESTS
======================================================================

Test:

[ ] Goal extraction
[ ] Goal dedup
[ ] alias recognition
[ ] high-level Goal
[ ] low-level Goal
[ ] local/global distinction
[ ] Procedure achieves Goal
[ ] Step references Goal
[ ] Implementation satisfies Goal
[ ] deterministic Implementation extraction
[ ] ambiguous Goal merge
[ ] user-created Goal
[ ] ownership/provenance
[ ] selective embedding
[ ] backward compatibility
[ ] no fabricated objects
[ ] storage-efficient ingestion
[ ] million-scale batch behavior where feasible

======================================================================
27. REAL INGESTION REHEARSAL
======================================================================

Run real sources through:

source
→ parse
→ Claims
→ Goals
→ Procedures
→ Implementations
→ relations
→ dedup
→ selective embedding
→ retrieval

Show actual persisted IDs.

Demonstrate:

Goal G
├── multiple Procedures
└── multiple Implementations

and:

Procedure P
→ Step
→ child Goal

======================================================================
28. ACCEPTANCE CRITERIA
======================================================================

Complete only when:

[ ] Goal has stable identity
[ ] Goal is central linking primitive
[ ] Procedure achieves Goal
[ ] ProcedureSteps reference Goals
[ ] Implementations satisfy Goals
[ ] Goals are searchable
[ ] Goals can be user-created
[ ] Goal dedup works
[ ] high-level and low-level Goals coexist
[ ] Claims remain separate world-state
[ ] ingestion supports all canonical types
[ ] provenance survives
[ ] storage is not wasted on raw duplicates
[ ] embeddings are selective
[ ] current production ingestion path remains canonical

======================================================================
29. FINAL DELIVERABLE
======================================================================

Return:

starting SHA
ending SHA
branch
commits
migrations
backfills
schema changes
files changed
actual ingestion call graph
Goal schema
Goal relationship model
retrieval path
MCP/API surface
tests
storage impact
real ingestion trace
acceptance matrix
remaining limitations

Classify limitations:

BLOCKING
NON-BLOCKING
INTENTIONAL FUTURE WORK

There must be zero known blocking internal gaps in the canonical knowledge +
Goal ingestion layer.