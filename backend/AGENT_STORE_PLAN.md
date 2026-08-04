# Agent Store — Plan

Companion to `V2_PLATFORM_PLAN.md`. Written before any code exists for
this. Upload this back into a fresh conversation to resume.

---

## 1. What this is

A separate, searchable catalog of runnable agents, distinct from
Archive (which answers questions grounded in the knowledge graph) and
distinct from Workbench (which proposes a one-off task decomposition
for a single problem). The Agent Store is where a *reusable, directly
invocable* capability lives, discoverable, reviewed, and, once
approved, runnable from its own page the same way the medical-report
extraction agent already is.

Populated from four sources (Section 4), reviewed through a
generalization of the debate mechanism already built (Section 3), and
linked back into decomposition so a proposed workflow can suggest real,
runnable agents for its steps, not just propose new ones from scratch
(Section 5).

---

## 2. Why a separate store, not `node_type='agent'` on the knowledge graph

The earlier default (fold agents into `knowledge_nodes`) was reasonable
for a narrower version of this requirement, but doesn't hold up against
the fuller one: self-service submission, multi-source ingestion,
external marketplace content.

**The real distinction:** a `knowledge_node` represents a fact to cite.
An agent represents a capability to invoke. These have different
lifecycles. A fact doesn't need a security review before it's safe to
reference. A piece of runnable code does, especially given the earlier
research finding: a Snyk audit of public skill marketplaces found 36%
had at least one security flaw, 76 confirmed malicious. Blurring these
into one table blurs exactly the line that matters most,
informational content versus executable capability.

**What's shared, not duplicated:** the retrieval infrastructure. The
embedding + lexical + RRF fusion machinery already built and tested
doesn't need reinventing, it needs generalizing to also query a second
table (`agents`). That's how Section 5's decomposition link gets built
without a second retrieval system.

---

## 3. Review and approval: generalizing the debate mechanism, not reusing it uniformly

Two genuinely different cases, treated with different rigor rather than
one review path for both.

### 3a. Graph-derived agents (Section 4) — reuse is close to free

A promoted decomposition *is* a task graph, exactly the artifact Layer
1's groundedness/fallacy check already evaluates, unchanged. Layer 2's
Tier 3 (simulated replay) generalizes usefully here too: running the
candidate agent against several varied, plausible inputs before
trusting it is automated pre-release testing, using infrastructure that
already exists rather than new code.

### 3b. Code-sourced agents (external marketplace, future raw-code
submissions) — review helps, but is not sufficient alone, stated
plainly rather than blurred

A panel reviewing a description and source for red flags (does behavior
match stated purpose, does it request permissions with no evident need)
is real, useful review. It is **not** equivalent to static analysis or
sandboxed execution testing. Given the 36% figure above, this has to
sit *alongside* automated scanning and eventual sandboxing, never stand
in for them. Encoded structurally below via the `runnable` field being
distinct from `review_state = approved`.

### The shared state machine

```
ingested
  -> under_review
       graph_derived:  Layer 1 groundedness/fallacy check (reused as-is)
                        + optional Layer 2 Tier 3 test against varied inputs
       code_sourced:   independent multi-reviewer critique (reuses
                        decomposition's generate+critique pattern, new rubric)
                        + automated scanning, required before runnable=true
  -> pending_human_approval   (a real scorecard, same shape as every
                                existing debate scorecard)
  -> approved | rejected      (human decision, same discipline as
                                everywhere else in this project)
```

This is not new architecture. It's `LoopOrchestrator -> Evaluator ->
Scorecard -> human approval`, the same shape already built and tested,
triggered by "an agent was proposed" instead of "a bottleneck was
detected."

---

## 4. The four sources

**Internal.** Hand-written, already trusted, register directly. The two
existing skills (PDF extraction, Excel generation) are this.

**Graph-derived** (the "Dify" idea, generalized: not a specific
product, the concept of promoting a validated decomposition into a
reusable agent). When a Tab 1 decomposition proves generalizable, not
overly specific to one person's exact wording, it can be promoted from
a one-off applied instance into a directly invocable agent. Future
users with a similar problem get served immediately rather than
re-running decomposition and debate from scratch. This is also the
concrete mechanism for the long-deferred V1 backlog item (1a: route
precedented bottlenecks to a cheap path instead of full debate),
precedent, formalized and made executable, not a new idea, a way to
finally build the old one.

*Open question, not yet resolved:* what makes a decomposition
"generalizable enough" to promote is a real judgment call. Candidate
criteria worth considering when this stage is built: has it been
approved and successfully applied more than once, does its input schema
avoid overfitting to one specific problem's wording. Not decided yet,
flagging so it isn't silently assumed.

**User-submitted.** Scoped deliberately narrow for a first version: a
structured request (desired input, output, category, description), not
a no-code agent builder, that's a different, much larger project. Enters
the same code-sourced review pipeline as external content, since a
bad-faith or careless public submission is a real risk the moment this
is multi-user, the same adversarial-incentive point already established
for the original Tab 2 vision.

**External marketplace** (`anthropics/skills`, the curated aggregators
from earlier research). Ingest as `ingested`, run whatever automated
scanning is feasible, require the same human review before `approved`,
and `runnable` gated separately, per Section 3b.

---

## 5. The decomposition link

`/v1/decompose` already retrieves related existing workflow context via
`HybridRetriever` before generating. Add a second, parallel retrieval
call, same underlying machinery, pointed at `agents` instead of
`task_nodes`/`knowledge_nodes`, and extend the response with a
`suggested_agents` field, ideally matched to *which* proposed task node
each agent could fulfill, not a flat list.

**Non-negotiable constraint:** only `approved` (and, for execution
suggestions specifically, `runnable`) agents may ever surface here. An
unreviewed or rejected agent appearing as a suggestion would undermine
the entire point of the review gate.

---

## 6. Schema

```sql
CREATE TABLE agents (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                      TEXT NOT NULL,
    description               TEXT NOT NULL,
    embedding                 VECTOR(1024),

    source                    TEXT NOT NULL CHECK (source IN
                                ('internal', 'graph_derived',
                                 'user_submitted', 'external_marketplace')),
    source_decomposition_id   UUID,   -- set only for graph_derived,
                                       -- traceable back to what it was
                                       -- promoted from

    execution_mode            TEXT NOT NULL CHECK (execution_mode IN
                                ('local_skill', 'remote_http')),
    skill_ref                 TEXT,   -- for local_skill: maps into SkillRegistry
    remote_config              JSONB,  -- for remote_http: {url, auth, ...}

    input_schema              JSONB NOT NULL DEFAULT '{}',
    output_schema             JSONB NOT NULL DEFAULT '{}',

    review_state              TEXT NOT NULL DEFAULT 'ingested' CHECK (review_state IN
                                ('ingested', 'under_review',
                                 'pending_human_approval', 'approved', 'rejected')),
    runnable                  BOOLEAN NOT NULL DEFAULT FALSE,
        -- distinct from approved: a code-sourced agent can be
        -- discoverable and even approved for listing before it is
        -- cleared to actually execute (Section 3b)

    visibility                visibility_level NOT NULL DEFAULT 'public',
    owner_id                  TEXT,

    t_valid                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid                 TIMESTAMPTZ,
    t_created                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by                TEXT
);
```

Bi-temporal, same pattern as everything else in this project, not a
plain mutable row. Agents improve over time the same way task nodes do;
invalidate-and-append keeps the same audit story, an agent's history
stays reconstructable, not just its current state.

**Two fields worth flagging as real design choices, not incidental:**

`execution_mode` is a genuine fork, not a detail. `local_skill` runs
in-process through the existing `SkillRegistry`, deliberately kept
closed and hand-written specifically to avoid running untrusted code.
`remote_http` never executes third-party code in our process, it makes
an outbound HTTP call, closer to calling any external API. A
misconfigured or malicious registered URL is a real SSRF-shaped risk
worth a deliberate check when this is built (don't let a registered
endpoint resolve to an internal-only address), and whatever a remote
agent *returns* needs the same untrusted-input discipline as anything
else, never flow into a graph update without the same validation
already applied to generated content elsewhere.

`runnable` is separate from `review_state = approved` specifically to
allow "discoverable and listed" without "cleared to execute" for the
code-sourced case, per Section 3b.

---

## 7. Staged build order

1. **Schema + review state machine**, wired to reuse Layer 1 for the
   `graph_derived` path specifically, lowest risk, mostly reuses tested
   code.
2. **The promotion mechanism**, turning an approved decomposition into
   a candidate agent entry. Requires resolving the open "generalizable
   enough" question from Section 4.
3. **Agent Store search/browse UI**, its own surface, generalized
   retrieval.
4. **Decomposition integration** (`suggested_agents` in `/v1/decompose`).
5. **The code-sourced review rubric** (independent critique) plus
   automated scanning, for `user_submitted` and `external_marketplace`.
6. **Sandboxed execution**, the real gate before any code-sourced agent
   becomes `runnable`, not `approved`, container isolation, no
   filesystem/network access by default, resource limits. Its own
   design effort, not a bolt-on.

---

## 8. What's reused vs. genuinely new

**Reused directly:** `Layer1Evaluator` (groundedness/fallacy check),
`SimulatedReplayEvaluator` (Tier 3 pre-release testing),
`DebateStateMachine`'s state-transition discipline, `HybridRetriever`
and the RRF fusion logic, the decomposition generate+critique pattern,
the bi-temporal update pattern, the V2 visibility predicate (one
function, same constraint as everywhere else).

**Genuinely new:** the `agents` table and its review state machine, the
promotion mechanism, the code-sourced review rubric, automated security
scanning, sandboxed execution, the Agent Store UI itself.

---

## Stage 1 status: done, verified

- `db/07_agents.sql` -- `agents`, `agent_reviews`, `agent_review_events`.
  CHECK constraint enforcing execution_mode/config pairing verified both
  ways (valid combinations insert, the invalid one is rejected) against
  real Postgres.
- `app/models/agent.py` -- `Agent`, `AgentReview`.
- `app/services/agent_review_state_machine.py` -- mirrors
  `DebateStateMachine`'s exact discipline (row-locked transitions,
  immutable event log) without sharing code with it, since an agent
  under review is a genuinely different entity with a different
  lifecycle. 8 offline tests on the transition table.
- `app/services/agent_review_orchestrator.py` -- wires `Layer1Evaluator`
  directly into the `graph_derived` review path, per Section 3a's whole
  premise: this review is close to free because nothing new needed
  building for it. Verified live against real Postgres
  (`integration_check_v2_agent_review.py`, 9 checks): a genuinely
  well-grounded proposal passes and reaches human review, a genuinely
  fallacious one is rejected outright with the real reason logged (not
  left pending), calling this path on a code-sourced agent is refused as
  a programming error rather than silently reviewed, and review results
  actually persist.

**Test status:** 194 offline tests (was 186), plus the new live check.

**Not yet built:** stages 2 through 6 (promotion mechanism, Agent Store
UI, decomposition integration, code-sourced review rubric, sandboxed
execution).

---

## Stage 2 status: done, verified

**A real design flaw was found and corrected mid-build, not just a test
bug.** Section 3a's original premise ("review is close to free, reuses
Layer1Evaluator unchanged") was wrong in one specific way: Layer 1's
`passed` gate requires a groundedness score, calibrated for a debate
candidate citing existing company facts. A graph-derived promotion is
mostly `create_task_node` ops, brand-new structure with nothing existing
to cite by construction (the capability boundary forbids generated
content from referencing existing nodes at all). Gating on that bar
would fail nearly every real promotion regardless of quality.
`AgentReviewOrchestrator` now computes its own pass condition
(constructive, no fallacies, no structural problems) for graph-derived
review, while still recording the groundedness score for a human
reviewer's visibility. The fallacy/constructiveness checks transfer
correctly; groundedness-as-a-gate doesn't, for this content specifically.

**Schema gap found and fixed additively:** Stage 1's `execution_mode`
only accounted for `local_skill` and `remote_http`. A promoted
decomposition is neither, it's an ordered sequence of task nodes with
dependencies. `db/08_graph_workflow_execution.sql` adds
`graph_workflow` as a third mode plus `workflow_task_ids`, verified both
that the new enum value is genuinely usable (not just added) and that
the updated CHECK constraint enforces correctly in both directions.

**Built:**
- `db/08_graph_workflow_execution.sql`
- `app/services/agent_promotion.py` -- `promote_decomposition()`.
  Manual trigger by design (no real usage data yet to threshold an
  automatic trigger against), always re-reviews even though the source
  decomposition was already approved once ("safe for this input" and
  "safe to reuse generally" are different claims).
- `app/services/agent_decision.py` -- `decide_agent()`. Computes
  `runnable` for real at approval time by checking whether every
  constituent task node's `skill_ref` actually resolves in the
  registry, distinct from `review_state='approved'` on purpose.
- `app/api/agent_store.py` -- `POST /v1/agent-store/promote`,
  `POST /v1/agent-store/{id}/decide`, `GET /v1/agent-store/{id}`.

**Verified against real Postgres**
(`integration_check_v2_agent_promotion.py`, 8 checks): a real approved
decomposition promotes and passes review despite zero citations (the
corrected, honest outcome), a non-approved decomposition is refused,
approval correctly computes `runnable=True` when a skill resolves, and
-- the case that matters most -- `runnable=False` when it doesn't, even
though `review_state='approved'`, proving the two are genuinely
separate facts, not just separate fields. Also verified over real live
HTTP through the actual routes, not just the service functions
directly: promote, decide, and a GET confirming every field persisted
correctly, including `workflow_task_ids` and the traceable
`source_decomposition_id`.

**Test status:** 194 offline tests (unchanged -- this stage's
verification lives in live checks, since nearly every meaningful branch
here is database-dependent). Two integration check scripts now cover
the Agent Store: `integration_check_v2_agent_review.py` (Stage 1),
`integration_check_v2_agent_promotion.py` (Stage 2).

**Not yet built:** stages 3 through 6 (Agent Store UI, decomposition
integration, code-sourced review rubric, sandboxed execution).

---

## Stage 3 status: done, verified

**Built:**
- `app/services/agent_search.py` -- browse (no query) and lexical search
  (with one), reusing the same OR-based tsquery fix already proven in
  `HybridRetriever` for exactly the same reason: `plainto_tsquery`
  AND-ing every word fails almost any real multi-word search. Only
  `review_state='approved'` agents are ever returned, enforced in the
  query itself, and scoped through the same `visibility_predicate` used
  everywhere else, not a parallel access check.
- `GET /v1/agent-store` (browse/search), added to the existing
  `app/api/agent_store.py`.
- `app/agents/page.tsx` -- the Agent Store browse/search page. Every
  page's "Agents" nav link now points here rather than directly at the
  one hardcoded PDF-extraction page.

**Vector search closed out same day, not deferred.** `agent_promotion.py`
now generates a real embedding at promotion time (graceful degradation,
same pattern as `Onboarder._embed_seeded()` -- an embedding-provider
failure logs and leaves the row NULL rather than rolling back an
otherwise-successful promotion, verified directly: promotion succeeds
even with no embedder package installed at all). `agent_search.py` now
fuses vector and lexical entrypoints via the same RRF pattern already
proven in `HybridRetriever`. `scripts/backfill_agent_embeddings.py`
added for agents that predate this or were seeded directly via SQL.

**A second, adjacent gap found and fixed while wiring this in:** the
real, already-shipped medical-report-extraction agent had never actually
been registered as a row in `agents` at all -- its endpoint builds its
own `SkillRegistry` inline and never touches the table. Closing the
embedding gap would have had nothing real to search over without also
fixing this. `db/09_seed_internal_agents.sql` registers it directly as
`source='internal'`, `review_state='approved'` (already trusted and
already in production use, not routed through the new-submission review
flow), idempotent via a `WHERE NOT EXISTS` guard, verified re-running it
is a genuine no-op.

**Verified against real Postgres**, including the case that matters
most: a semantically related agent ("Blood Panel Interpreter") found via
a query sharing zero keywords with it, findable only through the vector
signal, plus the lexical-only case, plus embedder-failure degrading
gracefully to lexical-only rather than erroring the whole search.

**Frontend build clean, 6 routes.** Runnable agents display a "Runnable"
badge; a card only links to a real run page if one exists for that
agent (currently only the medical-report-extraction one) -- a generic
runner for arbitrary `graph_workflow` agents is out of scope for this
stage.

**Not yet built:** stage 4 (decomposition integration --
`suggested_agents` in `/v1/decompose`), stage 5 (code-sourced review
rubric), stage 6 (sandboxed execution).

---

## Stages 4, 5, 6 status: done, all built in one session, verified with real rigor at each step

**Stage 4 -- decomposition integration.** `suggested_agents` added to
`/v1/decompose`'s response, reusing `search_agents()` directly. Verified
live: a relevant-but-not-runnable agent is correctly excluded from
suggestions even though it's approved and would match the query -- the
non-negotiable constraint from Section 5 is enforced, not just stated.

**Stage 5 -- code-sourced review.** `app/services/code_review.py`:
independent critique from at least 2 reviewers of genuinely distinct
families (construction-time guard, not a runtime hope), plus real
bandit static scanning when `source_detail.code` is present. Verified
against a submission with a real, deliberately-planted hardcoded
credential and an `os.system` call -- bandit actually caught it, this
wasn't asserted against a mocked scanner. **The critical enforcement,
verified directly:** `runnable` stays `False` even after
`review_state='approved'`, for both user_submitted and
external_marketplace, because no execution mechanism existed yet at
that point -- caught this in `_compute_runnable` explicitly rather than
letting execution_mode branching accidentally decide it.

**Stage 6 -- sandboxed execution.** `app/services/sandbox.py`. Read its
own docstring before trusting anything it produces -- it states plainly
what's verified (network isolation via `unshare --net`, confirmed
against real network access, not assumed from documentation; CPU and
memory limits, confirmed against a genuine infinite loop and a genuine
over-allocation), what's unverified (non-root production behavior --
every check here ran as root, since that's what this environment is),
and what's simply not built (filesystem isolation -- an absolute-path
read of the host filesystem is not prevented).

A real bug was found and fixed during construction, not just claimed
fixed: the first version set resource limits via `preexec_fn` on the
*outer* subprocess call, which would have limited the `unshare`/`timeout`
wrapper processes rather than the actual Python process running
untrusted code -- caught before shipping by tracing through what
`preexec_fn` actually wraps, fixed by injecting the limit-setting code
into the generated script itself.

A second bug was found by the verification suite, not by inspection: the
wall-clock timeout test initially failed. Not because isolation failed,
the process genuinely was killed on time, but because the *detection*
only checked the outer Python-level timeout, missing the far more common
case where the inner `timeout` command kills the process first (exit
code 124). Fixed and re-verified.

**Wired into the actual approval decision, not left as a standalone
module:** `decide_agent` now takes `acknowledge_sandbox_limitations`.
An automated clean sandbox run does not, by itself, flip `runnable` to
`True` -- given the real, stated gaps above, that's a considered human
decision, not a formality the code should grant silently. Verified all
branches: no acknowledgment blocks it even with clean code; acknowledged
plus a genuine sandbox failure (planted a crashing submission) still
blocks it; a user_submitted request with no code has nothing to test and
stays blocked regardless.

**Test status:** 194 offline tests (unchanged -- everything in stages
4-6 is either live-verified, given how database- and OS-primitive-
dependent it is, or, for stage 4's filtering logic, covered by the same
live check). Two new integration check scripts:
`integration_check_v2_code_review.py` (9 checks),
`integration_check_v2_sandbox.py` (10 checks).

**Honest summary of where this leaves the Agent Store:** every stage
from the original plan is now built. Graph-derived agents have a
complete, real, low-risk path from decomposition to running agent.
Code-sourced agents have real review and a real (if honestly partial)
sandbox, gated behind an explicit human acknowledgment rather than an
automatic pass -- which is the correct state for genuinely novel,
unverified-at-scale infrastructure, not a placeholder to feel bad about.

---

## 9. Open decisions, not yet resolved

- Graph-derived promotion criteria (Section 4)
- Automated scanning tooling for code-sourced agents, not selected yet
- Sandboxing approach for stage 6 (container-per-execution vs. a
  persistent sandboxed runtime, resource limit specifics)
- Whether `remote_http` outbound calls need their own rate limiting
  separate from the existing per-endpoint governance, given they call
  out to arbitrary registered URLs rather than known LLM providers
