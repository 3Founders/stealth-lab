# Project Status & Roadmap

## What this is

A workflow governance engine: detect bottlenecks, debate fixes among a
heterogeneous AI panel, evaluate rigorously, apply only after human
approval, fully auditable, bi-temporal. A newer layer generalizes this
into an Agent Store: reusable, reviewed, runnable capabilities, sourced
from hand-written skills, promoted decompositions, user submissions, or
external marketplaces.

## Status: built and working

**Core loop (V0/V1):** bi-temporal knowledge graph, Nyaya-style debate
protocol, two-layer evaluation (deterministic groundedness/fallacy
check, validated statistics), graph visualization, grounded chat with
citation verification.

**Public platform (V2):** access control (one function owns every
visibility check), rate limiting + cost governance (Postgres advisory
locks, verified against a real concurrency race), generative
decomposition with a structural capability boundary as the actual
guarantee (not the prompt-based mitigations around it).

**Real client agent:** medical report PDF -> combined Excel, real
extraction, real numeric typing, real reference ranges, verified against
an actual client document, not synthetic data. Multi-file upload
produces one combined comparison table, not separate files.

**Agent Store (stages 1-6, all built):**
- Review state machine (`ingested -> under_review -> pending_human_approval
  -> approved/rejected`), same discipline as the debate state machine.
- Graph-derived agents: promote an approved decomposition into a
  reusable agent, reviewed via a corrected reuse of Layer 1 (gates on
  fallacies/constructiveness, not citation-groundedness, which doesn't
  apply to newly-created content).
- Code-sourced agents: independent multi-family critique + real bandit
  scanning (verified against a deliberately unsafe submission).
- A real, honestly-scoped sandbox (`unshare` network isolation + CPU/
  memory limits, all verified against real behavior; filesystem
  isolation and non-root production behavior explicitly not verified).
- `runnable` and `review_state='approved'` are always tracked
  separately, never inferred from each other.
- Search (RRF fusion, vector + lexical) and decomposition-time
  suggestions, filtered to `approved AND runnable` only.
- Frontend: Agent Store browse/search page, promotion + agent-approval
  flow inside Workbench.

**Testing discipline:** 194 offline tests, multiple live integration
checks against real Postgres, real bugs found and fixed throughout
(trace-ID collisions across 3 separate scripts, a rate-limiter race, an
AND/OR lexical search bug, a resource-limit wiring bug in the sandbox,
and others), each verified against real behavior, not asserted.

## What's planned ahead

### 1. Workbench: fix task/decomposition reuse

Resubmitting the same problem should point back at what already exists,
not regenerate a fresh decomposition. Confirmed at least two real,
distinct causes worth separating before fixing either:

- Decomposition generation itself only checks `task_nodes`/
  `knowledge_nodes` for existing coverage (via `_existing_context()`),
  it has **zero awareness the Agent Store exists at all**. Agent
  suggestion is a separate, bolted-on search that runs *after*
  generation, never informs it.
- Even within graph-only matching, `suggested_agents` only ever
  surfaces `runnable` agents. An approved-but-not-yet-runnable agent, or
  an existing task the retrieval found but the model didn't weight
  heavily enough, can be silently skipped even when it's the right
  match.

Needs investigation into which of these is actually firing in the
reported case before deciding the fix, likely both need addressing.

### 2. Docket: bring humans into the debate itself

Currently a human only ever approves or rejects a *finished* scorecard,
after the panel has already concluded. Real participation, a human
adding an argument or turn into an in-progress debate before it
concludes, doesn't exist yet. Touches the debate state machine, the
turn-taking protocol, and the Docket UI.

### 3. Visualize more, wherever it genuinely helps

Currently only the knowledge/task graph has a visual (`WorkflowGraph`).
Candidates worth considering: the debate itself as a visual thread, not
just a transcript; Agent Store results as something more than a text
list; the promotion pipeline (decomposition -> review -> approval ->
runnable) as an actual visible pipeline rather than a stamp appearing
after each step.

### Already-queued, from earlier planning

- No job queue -- everything (debates, decompositions, agent runs)
  executes synchronously inside the request handler.
- No real authentication -- private visibility is schema-ready but
  disabled until this exists.
- Sandbox filesystem isolation not built; non-root production behavior
  unconfirmed.
- No public HTTP endpoint for code-sourced agent submission -- the
  review and sandbox mechanisms are real and verified, but only reachable
  via internal service calls and integration checks right now.
- The WhatsApp reminder/questionnaire agent -- fully scoped, paused to
  build the Agent Store first, not started.
- Graph-derived promotion is still a manual trigger; no real reuse data
  exists yet to base an automatic threshold on.

## Reference docs

`AGENT_STORE_PLAN.md` (design reasoning + chronological build log),
`AGENT_STORE_MECHANISM.md` (how it works, organized by mechanism),
`FRONTEND_VERIFICATION_GUIDE.md` (exact steps to verify all of the
above through the UI), `V2_STATUS.md`, `V2_PLATFORM_PLAN.md`.
