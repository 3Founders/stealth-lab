PROMPT 2 — PRODUCTION GOAL-FIRST RUNTIME, FUZZY INTENT RESOLUTION,
RECURSIVE COMPILATION, LOCAL META-HARNESS, COST ENGINE & DURABLE EXECUTION
 
You are working on the StealthLab repository.
 
Prompt 1 established the canonical knowledge model and ingestion layer:
Claim, Goal, Procedure, Implementation, provenance/lineage, verification, and Goal relationships.
 
Now implement the actual runtime system.
 
==================================================
0. CORE PRINCIPLE
==================================================
 
Goal is the central runtime primitive.
 
The user does NOT necessarily provide a canonical Goal.
 
User input may be vague, colloquial, incomplete, or expressed in terms of symptoms:
 
Examples:
- "make this faster"
- "fix checkout"
- "why is this flaky?"
- "deploy this"
- "make the tests stop failing"
- "clean up this service"
 
The runtime MUST therefore resolve fuzzy human intent into one or more candidate canonical Goals before retrieving Procedures/Claims.
 
Do NOT require the user to know Goal IDs or canonical terminology.
 
==================================================
1. FUZZY INPUT → GOAL RESOLUTION
==================================================
 
Implement an explicit Goal Resolution pipeline:
 
User input
    ↓
intent/context extraction
    ↓
structured Goal representation
    ↓
Goal candidate retrieval
    ↓
Goal ranking
    ↓
selected existing Goal OR proposed new Goal
    ↓
Goal-linked Claims / Procedures / Implementations
 
The Goal normalizer may use an LLM for semantic interpretation.
 
It MUST NOT merely rewrite the user's sentence.
 
Produce a structured representation containing, where inferable:
 
- desired outcome
- object/system/component
- action
- constraints
- scope
- verification intent
- relevant entities/files/services
- uncertainty
- alternative interpretations
 
Example:
 
Input:
"make checkout faster"
 
Possible normalized representation:
 
{
  outcome: "reduce checkout request latency",
  object: "checkout service",
  action: "improve performance",
  constraints: [],
  verification: "measure request latency",
  uncertainty: [...]
}
 
The normalized representation is then used for Goal retrieval.
 
==================================================
2. GOAL RETRIEVAL
==================================================
 
Search existing Goals using the normalized semantic representation.
 
Global:
- semantic/vector retrieval
- lexical retrieval where useful
- metadata/context filters
 
Local:
- do NOT require a local vector database
- use the local Goal index/manifest as the semantic entry point
- semantic resolution may happen in memory using the Goal summaries/index
- once candidate Goal IDs are identified, use exact local retrieval
 
Rank candidates using contextual relevance, including:
 
- semantic similarity
- lexical/entity overlap
- repository scope
- current files/components
- user-provided constraints
- Goal status
- historical success
- applicability
- verification compatibility
 
Do not use a single absolute global ranking.
 
The best Goal is contextual to the current repository, task and constraints.
 
==================================================
3. NO GOOD GOAL
==================================================
 
If no existing Goal sufficiently matches:
 
1. return/propose a candidate Goal;
2. preserve the original user intent;
3. allow the user to accept/edit/create it;
4. persist it as a canonical Goal when appropriate;
5. then continue normal Goal routing.
 
Do not fabricate a precise Goal when confidence is low.
 
Support multiple candidate interpretations when the input is genuinely ambiguous.
 
==================================================
4. LOCAL CLAIM / PROCEDURE RETRIEVAL
==================================================
 
After Goal resolution, Claims and Procedures are retrieved through the Goal.
 
Example:
 
Goal G42
  → Claims C12,C19,C31
  → Procedures P7,P11
  → Implementations I4,I9
 
The local `.stealth` files remain grep-friendly.
 
Example:
 
GOAL|G42|Improve checkout API latency
CLAIMS|C12,C19,C31
PROCEDURES|P7,P11
 
Then retrieve exact records:
 
rg "C12|C19|C31" .stealth/claims.md
rg "P7|P11" .stealth/procedures.md
 
IMPORTANT:
 
`rg` is NOT responsible for understanding fuzzy natural language.
 
Semantic understanding happens before exact local retrieval.
 
The local filesystem is the durable, inspectable ABI.
 
==================================================
5. LOCAL .STEALTH ABI
==================================================
 
Maintain:
 
.stealth/
├── index.md
├── goals.md
├── claims.md
├── procedures.md
├── run.md
├── events.jsonl
└── artifacts/
 
index.md is the entry point.
 
It should tell an agent:
 
- what this repository knows
- how Goals are represented
- where Claims live
- where Procedures live
- how to resolve a task
- how to retrieve records
- how to continue/resume runs
 
Records MUST be grep-friendly and line-addressable.
 
Do not replace the file-first local system with a local vector database.
 
==================================================
6. GOAL → CLAIMS → PROCEDURES → IMPLEMENTATIONS
==================================================
 
Once a Goal is selected:
 
1. retrieve relevant Claims;
2. use Claims to ground repository-specific reality;
3. retrieve applicable Procedures;
4. retrieve direct Implementations;
5. compare direct Implementation routes against Procedure routes;
6. recursively expand Procedure child Goals;
7. continue until concrete executable Implementations or human nodes are reached.
 
Conceptually:
 
Goal
  ↓
Claims
  ↓
Procedures / Implementations
  ↓
child Goals
  ↓
Procedures / Implementations
  ↓
concrete execution DAG
 
Claims describe what is true.
 
Goals describe desired outcomes.
 
Procedures describe reusable ways to achieve Goals.
 
Implementations are concrete executable realizations.
 
Do not collapse these concepts.
 
==================================================
7. RECURSIVE GOAL COMPILER
==================================================
 
Implement a production compiler:
 
resolve_goal()
→ ground_goal()
→ select_routes()
→ recursively resolve child Goals
→ bind concrete Implementations
→ construct execution DAG
→ validate DAG
→ estimate cost/success/risk
→ execute
 
The final runtime DAG MUST contain concrete implementation-bound nodes.
 
Do not use the historical `task_nodes` table as the scheduling model.
 
Use the existing live runtime structures where appropriate, but audit the repository first.
 
==================================================
8. IMPLEMENTATION REGISTRY MUST BECOME LIVE
==================================================
 
Audit current execution paths.
 
The compiler MUST actually consult:
 
implementations
procedure_implementations
 
when selecting execution routes.
 
Do not merely register/inspect Implementations while bypassing them during normal execution.
 
A selected DAG node should identify:
 
- Goal
- Procedure if applicable
- Implementation
- inputs
- dependencies
- verification
- fallback
- estimated cost
- expected duration
- resource requirements
 
==================================================
9. VERIFICATION
==================================================
 
Every executable Goal route MUST have a verification contract.
 
Verification can be:
 
- deterministic command
- test
- assertion
- metric threshold
- artifact inspection
- LLM evaluation where deterministic verification is impossible
- human approval
 
Verification MUST be represented explicitly.
 
A successful model response is NOT equivalent to successful Goal completion.
 
==================================================
10. FALLBACK / RETRY
==================================================
 
Support:
 
Implementation failure
    ↓
verification failure
    ↓
fallback Implementation
    ↓
alternative Procedure
    ↓
human intervention
 
Track all attempts.
 
Do not silently substitute a different Goal.
 
==================================================
11. COST / SUCCESS / RISK
==================================================
 
Estimate Goal execution cost.
 
Direct Implementation:
 
execution cost
+ expected retry cost
+ verification cost
 
Procedure:
 
sum expected child Goal costs
+ orchestration
+ verification
+ expected fallback/retry
 
Track:
 
- estimated cost
- actual cost
- latency
- success/failure
- retry count
- verification result
- model/tool/compute usage
 
Goal routing should optimize an appropriate combination of:
 
success probability
cost
latency
risk
 
==================================================
12. DURABLE EXECUTION
==================================================
 
Make execution resumable.
 
Persist enough state to survive:
 
- process crash
- worker restart
- network failure
- implementation failure
- verification failure
 
Use leases/idempotency where appropriate.
 
A resumed run MUST continue from persisted execution state rather than reconstructing an inconsistent DAG.
 
==================================================
13. RUN.MD
==================================================
 
`.stealth/run.md` is the human/agent-readable execution trace.
 
Every concrete node should have a grep-able section containing:
 
- node ID
- Goal
- Procedure
- Implementation
- inputs
- dependencies
- status
- start/end
- verification
- output/artifact
- failure
- retry/fallback
 
Example:
 
NODE|N17
GOAL|G42
PROCEDURE|P7
IMPLEMENTATION|I9
STATUS|verified
VERIFY|pytest tests/checkout
RESULT|passed
 
Keep run artifacts durable and inspectable.
 
==================================================
14. MCP / META-HARNESS
==================================================
 
Expose MCP operations for:
 
- Goal search
- Goal resolution from fuzzy input
- Goal detail
- Claim retrieval
- Procedure retrieval
- Implementation retrieval
- Goal compilation
- execution
- run status
- verification
- artifacts
 
The MCP interface should allow an agent to perform the entire workflow without manually knowing internal database structure.
 
==================================================
15. FRONTEND
==================================================
 
Expose enough API/UI to inspect:
 
- Goal
- Claims
- Procedures
- Implementations
- compiled route
- execution DAG
- run
- verification
- cost
- success
- alternatives
 
The Goal page should make the route from:
 
Goal → Claims → Procedures → Implementations → Run
 
visible.
 
==================================================
16. LLM USAGE
==================================================
 
Use LLMs for semantic work:
 
- fuzzy intent interpretation
- Goal normalization
- ambiguity resolution
- semantic extraction
- semantic evaluation where required
 
Use deterministic code for:
 
- ID resolution
- graph construction
- dependency handling
- ranking arithmetic
- cost calculation
- persistence
- leases
- state transitions
- verification when deterministic
- execution dispatch
 
Do not use an LLM where deterministic logic is sufficient.
 
==================================================
17. LOCAL VS GLOBAL
==================================================
 
Global:
 
- PostgreSQL
- pgvector where useful
- semantic Goal retrieval
- global Procedures
- global Implementations
- global Claims
- provenance/lineage
 
Local:
 
- `.stealth/`
- file-first
- exact/grep retrieval after Goal resolution
- repository-specific Claims
- repository-specific Goal context
- local Procedures
- execution traces
 
The local system should remain lightweight and agent-readable.
 
==================================================
18. IMPORTANT RETRIEVAL TESTS
==================================================
 
Add tests specifically for fuzzy inputs.
 
Examples:
 
Input:
"make this faster"
 
Input:
"fix the checkout thing"
 
Input:
"why does this keep breaking?"
 
Input:
"deploy it"
 
For each test:
 
1. fuzzy input is normalized;
2. candidate Goals are generated;
3. relevant existing Goal is retrieved when one exists;
4. Goal-linked Claims are retrieved;
5. Goal-linked Procedures are retrieved;
6. exact local records are loaded;
7. correct route is compiled.
 
Also test ambiguity:
 
Input:
"make it faster"
 
where multiple Goals are plausible.
 
The system should return multiple candidates or request clarification rather than confidently selecting the wrong Goal.
 
==================================================
19. END-TO-END REHEARSAL
==================================================
 
Perform a real repository rehearsal:
 
User:
"make checkout faster"
 
Expected:
 
fuzzy input
→ normalized Goal
→ existing Goal retrieval
→ local Goal resolution
→ Claim retrieval
→ Procedure retrieval
→ Implementation selection
→ recursive compilation if necessary
→ concrete DAG
→ execution
→ verification
→ durable run record
 
Then perform a crash/resume rehearsal.
 
Then perform a case where the first Implementation fails and a fallback succeeds.
 
Then perform a case where no suitable Goal exists and a new Goal is proposed.
 
==================================================
20. ACCEPTANCE CRITERIA
==================================================
 
The implementation is complete only when:
 
- vague human input can resolve to canonical Goals;
- Goal retrieval works semantically;
- local Goals can be resolved without requiring literal keyword matches;
- local Claims/Procedures can then be retrieved exactly by Goal-linked IDs;
- Claims ground repository-specific decisions;
- Procedures recursively resolve Goals;
- Implementations are actually selected and executed;
- final DAG nodes are concrete;
- verification is explicit;
- cost/success/latency are measured;
- execution survives restart;
- `.stealth` remains grep-friendly;
- MCP exposes the workflow;
- existing production behavior remains compatible;
- tests cover fuzzy, ambiguous, missing-Goal, fallback, and crash/resume cases.
 
Before changing architecture, inspect the live repository and existing production paths.
 
Do not invent parallel/dead schemas.
 
Reuse, migrate, or deprecate existing structures based on actual callers and readers.
 
Deliver:
 
1. implementation
2. migrations
3. tests
4. MCP/API changes
5. `.stealth` local ABI implementation
6. end-to-end rehearsal results
7. list of files changed
8. remaining technical debt
9. explicit explanation of the fuzzy-input → Goal → local Claim/Procedure retrieval path.