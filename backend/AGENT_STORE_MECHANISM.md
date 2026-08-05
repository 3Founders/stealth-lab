# Agent Store: How It Works (Stages 1-6)

Companion to `AGENT_STORE_PLAN.md` (the design reasoning and build log,
chronological) and `V2_PLATFORM_PLAN.md` (the platform this sits
inside). This document explains the finished mechanism as a whole, not
the order it was built in, for anyone reading it fresh.

---

## 1. The core idea in one paragraph

A `knowledge_node` is a fact to cite. An `agent` is a capability to
invoke. Those have different lifecycles, an executable capability needs
a security review a citable fact never does, so agents live in their
own table (`agents`), with their own review state machine, but ride the
*same* retrieval infrastructure (embedding + lexical + RRF fusion)
already built and proven for the knowledge graph. An agent can come from
four places: written by hand, promoted from a proven decomposition,
submitted by a user, or ingested from an external marketplace, and
every one of those paths ends at the same place, a human explicitly
deciding whether it's approved, and separately, whether it's safe to
actually run.

---

## 2. The two facts that never get conflated: `review_state` and `runnable`

This distinction is the spine of the whole design, worth understanding
before anything else.

- **`review_state`** answers: *is this agent's description of itself
  trustworthy?* Did it pass review, did a human sign off.
- **`runnable`** answers a completely different question: *is there
  actually a safe way to execute this specific thing right now?*

An agent can be `review_state='approved'` and `runnable=false` at the
same time, and that's not a bug, it's the normal, correct state for
most code-sourced agents today. The graph structure or submission can
be sound while the *mechanism to run it* is absent (no execution
support yet) or unproven (sandbox limitations not yet acceptable for
this specific case). Nothing in this system ever infers one field from
the other.

---

## 3. The review state machine (stage 1)

```
ingested -> under_review -> pending_human_approval -> approved
                          \\                        -> rejected
                           \\-------------------------> rejected
```

One transition table (`app/services/agent_review_state_machine.py`),
row-locked so two concurrent reviewers can't both advance the same
agent, every transition appended to `agent_review_events` as an
immutable log, never a bare status update. `rejected` is reachable from
any pre-decision state, either automated review or a human can reject,
both land in the same terminal state. This mirrors `DebateStateMachine`
exactly in discipline, deliberately without sharing code with it, an
agent under review is not a debate.

---

## 4. Path A: graph-derived agents (stages 1 and 2)

### 4.1 What one is

A promoted, reusable version of a decomposition that already worked.
Tab 1 (`/v1/decompose`) proposes a task graph for one problem; if that
graph proves generalizable, it can become a standing agent anyone with
a similar problem can just run, instead of regenerating the same
decomposition from scratch.

### 4.2 Promotion (`app/services/agent_promotion.py`)

Manual, on purpose. `promote_decomposition(decomposition_id)`:

1. Requires `decompositions.status = 'approved'` and a real
   `applied_refs` (the `{ref: created_node_id}` map written when the
   decomposition was actually applied to the graph) -- refuses
   otherwise, with a specific exception for each failure reason.
2. Creates an `agents` row: `source='graph_derived'`,
   `execution_mode='graph_workflow'`, `workflow_task_ids` populated from
   `applied_refs` -- this is the ordered set of task nodes the promoted
   agent actually walks when run.
3. Generates a real embedding for `name + description` (graceful
   degradation on failure, same pattern as the original knowledge-graph
   seeding -- an embedding outage never rolls back a successful
   promotion, it just leaves the row waiting for backfill).
4. Immediately runs Layer 1 review (next section) -- always, even though
   the source decomposition was already approved once. *Safe for this
   one input* and *safe to reuse generally* are different claims.

### 4.3 Review: reusing Layer 1, correctly, not naively

`app/services/agent_review_orchestrator.py` reuses `Layer1Evaluator`
directly. This is genuinely close to free for one real reason: the
promoted content *is* a `ChangeSet`, the exact artifact Layer 1 already
checks for debate candidates.

**One thing worth understanding precisely, because it was a real design
correction, not a footnote:** Layer 1's own `passed` field requires a
groundedness score (does this cite real, existing facts), calibrated for
a debate candidate arguing about an *existing* task. A graph-derived
promotion is almost entirely `create_task_node` ops, brand-new
structure, with nothing existing to cite by construction -- the
capability boundary (see `V2_PLATFORM_PLAN.md`) forbids generated
content from referencing existing nodes at all. Gating on citation
groundedness would fail nearly every real promotion regardless of
quality. So `AgentReviewOrchestrator` computes its *own* pass condition
for this content: `constructive AND no fallacy flags AND no structural
problems` -- the parts of Layer 1 that genuinely transfer -- while still
recording the groundedness score for a human reviewer to see, just not
gating on it.

### 4.4 Human approval and `runnable` (stage 2, `agent_decision.py`)

Once an agent reaches `pending_human_approval`, a human calls
`decide_agent(..., decision='approved' | 'rejected')`. On approval,
`_compute_runnable()` checks, for real, whether *every* task node in
`workflow_task_ids` has a `skill_ref` that actually resolves in the
`SkillRegistry` -- not assumed, queried. If one step's skill doesn't
exist, the agent is correctly `approved` (the graph structure passed
review) and correctly `runnable=false` (there's a step in it that can't
actually execute).

This is deliberately safe to compute automatically, unlike the
code-sourced path below: a `graph_workflow` only ever invokes skills
already in the closed, hand-written `SkillRegistry`. Running one is
running already-trusted internal code in a different order, not running
untrusted code.

---

## 5. Path B: code-sourced agents (stages 5 and 6)

### 5.1 What these are

`source='user_submitted'` (a structured request -- desired input/output/
category, deliberately *not* raw code or a no-code builder, that's a
much larger, separate project) or `source='external_marketplace'`
(content ingested from somewhere like `anthropics/skills`, which may
include actual source). Both are untrusted by default in a way graph-
derived content structurally isn't: this is the one place actual
third-party code, or a request that could be crafted in bad faith, is
in play. Stored in `agents.source_detail` (JSONB -- `{"code": ...}` for
marketplace content with real source, `{"requested_input": ...}` for a
structured request).

### 5.2 Review (`app/services/code_review.py`)

Two things, and it matters that neither alone is a safety proof:

1. **Independent multi-reviewer critique.** At least 2 reviewers from
   *genuinely distinct model families* -- enforced at construction time,
   not hoped for: `CodeSourcedReviewOrchestrator.__init__` raises if
   given fewer than 2 reviewers or if any two share a family. Each
   judges independently whether the agent's actual behavior matches its
   stated purpose, and whether it requests capabilities beyond evident
   need. A reviewer failing to respond counts *against* the submission,
   it is never silently skipped, an incomplete review passing by
   default would be worse than no review.
2. **Real static scanning (bandit)**, only when `source_detail.code` is
   present. Verified against actually unsafe code during construction,
   not a mocked scanner, a submission with a hardcoded credential and an
   `os.system` call was genuinely caught. Bandit catches a specific,
   real class of issue; it does not catch logic bugs, and a scan that
   fails to run is treated as a failure, never treated as equivalent to
   a clean scan.

Passing this moves the agent to `pending_human_approval`, same as the
graph-derived path. **It never, by itself, sets `runnable=true`.**
`_compute_runnable` hard-checks `source` before anything else: for
`user_submitted`/`external_marketplace`, `runnable` starts `False`
regardless of execution_mode or review outcome, because until stage 6,
there was no way to safely execute this content at all.

### 5.3 The sandbox (`app/services/sandbox.py`, stage 6)

Read this section as a report of what's actually true, not a claim of
completeness.

**Verified against real behavior, not documentation:**
- **Network isolation** (`unshare --net`) -- the identical code was
  confirmed to reach a real external host normally, and reliably
  blocked (no network interface exists in the isolated namespace at
  all) when run inside it.
- **CPU limit** (`resource.RLIMIT_CPU`) -- confirmed a genuine infinite
  loop gets killed, not merely slowed.
- **Memory limit** (`resource.RLIMIT_AS`) -- confirmed a genuine
  over-allocation raises `MemoryError` rather than succeeding.
- **Credential stripping** -- an allowlist (`PATH`, `LANG`, `LC_ALL`
  only), not a denylist, so nothing needs to be enumerated by name to
  stay excluded. Confirmed a real secret set in the parent process is
  invisible inside.

**Explicitly not verified:** whether this holds identically for a
non-root production user. Every check here ran as root, since that's
what the build environment was. Unprivileged user namespaces are common
on modern Linux but not universal, some hardened environments disable
them for security reasons. **This must be confirmed on the actual
deployment before relying on it.**

**Filesystem: a real, verified first step, not a full solution.**
`/etc`, `/root`, and `/home` are hidden behind an empty tmpfs inside the
sandbox's own mount namespace (`unshare --mount`). Verified directly:
`cat /etc/passwd` succeeds normally outside the sandbox and fails with a
genuine "No such file or directory" inside it -- the real file is
unreachable, not access-denied on a still-visible path. This is a
denylist of the three most common actual attack targets, not a full
chroot/allowlist -- most of the rest of the host filesystem (`/usr`,
`/lib`, `/proc`, ...) is still visible, since Python's own interpreter
needs it to run at all. A submission that specifically goes looking for
something outside those three paths and outside its own working
directory could still find it.

**Two real bugs, found and fixed during construction, not shipped
silently:**
1. Resource limits were first wired via `preexec_fn` on the *outer*
   subprocess call -- which would have limited the `unshare`/`timeout`
   wrapper processes, not the actual untrusted code they run. Caught by
   tracing what `preexec_fn` actually wraps; fixed by injecting the
   limit-setting code into the generated script itself.
2. The wall-clock timeout test initially failed -- not because isolation
   failed (the process genuinely was killed on time), but because
   detection only checked the outer Python-level timeout, missing the
   far more common case: the inner `timeout` command killing the
   process first (exit code 124). Fixed and re-verified.

### 5.4 Requiring a human to actually decide, not the code

Because of the gaps above, a clean sandbox run does not, by itself,
flip `runnable`. `decide_agent(..., acknowledge_sandbox_limitations=True)`
must be explicitly passed. Without it, `runnable` stays `False` even for
genuinely clean code. With it, the sandbox actually runs the submission
as a smoke test, and `runnable` reflects whether it executed cleanly (no
crash, no timeout, isolation genuinely engaged, not silently skipped). A
submission that crashes in the sandbox stays `runnable=false` even with
acknowledgment, since the smoke test itself failed, that's a correctness
signal, not a policy decision.

If `unshare` itself can't run at all (missing binary, kernel refusal),
the sandbox **fails closed** -- it never falls back to running the code
unsandboxed. A sandbox that quietly skips isolation when its own
mechanism breaks is worse than refusing to run.

---

## 6. Discovery: search and suggestion (stages 3 and 4)

### 6.1 Search/browse (`app/services/agent_search.py`, `GET /v1/agent-store`)

Reuses the exact RRF fusion pattern already proven for the knowledge
graph: vector search and lexical search each return a ranked list, and
only rank *position* is combined (`1 / (60 + rank + 1)` per entrypoint),
never raw scores, since cosine distance and `ts_rank` live on
incomparable scales. Vector search degrades gracefully to lexical-only
on any embedding failure, logged, not silently worse. The lexical query
itself uses the same fix already found and verified for the knowledge
graph, `plainto_tsquery` ANDs every word together and fails almost any
real multi-word search, rebuilt to OR the same stemmed terms instead.

**Non-negotiable, enforced in the SQL itself, not application logic
layered on top:** only `review_state = 'approved'` agents are ever
returned, and every query is scoped through the same
`visibility_predicate()` used everywhere else in this project, not a
parallel access check that could drift out of sync.

### 6.2 Decomposition suggestions (`suggested_agents` on `/v1/decompose`)

Same search, called a second time alongside decomposition, filtered one
step further: only agents that are both `approved` **and** `runnable`
ever surface as a suggestion. An approved-but-not-runnable agent is
real, reviewed, listed content, just not something safe to suggest
someone actually run. A search failure here degrades gracefully too, it
never breaks decomposition itself, agent suggestion is an enhancement,
not the point of that endpoint.

---

## 7. The full picture, both lifecycles side by side

```
GRAPH-DERIVED                          CODE-SOURCED
--------------                         ------------
decomposition approved                 submitted / ingested
        |                                      |
        v                                      v
promote_decomposition()                agent row created,
  - creates agents row                 source_detail populated
  - embeds it                                  |
  - runs Layer 1 (own pass bar)                v
        |                              CodeSourcedReviewOrchestrator
        v                                - >=2 independent reviewers,
  pending_human_approval                   distinct families (enforced)
        |                                - bandit scan if code present
        v                                      |
  decide_agent()                               v
    approved -> compute runnable:        pending_human_approval
      every workflow_task_id's                 |
      skill_ref resolves in                    v
      SkillRegistry? (checked,          decide_agent(
      not assumed)                        acknowledge_sandbox_
                                           limitations=?)
                                             approved -> compute runnable:
                                               no ack -> False, always
                                               ack + no code -> False
                                               ack + code -> run_sandboxed(),
                                                 clean run required
```

Both converge on the same two facts (`review_state`, `runnable`), the
same search index, and the same suggestion mechanism, from genuinely
different risk profiles, handled with genuinely different rigor.

---

## 8. File map

| Concern | File |
|---|---|
| Schema: catalog, review, promotion | `db/07_agents.sql`, `db/08_graph_workflow_execution.sql`, `db/10_code_sourced_agents.sql` |
| Seeded internal agent | `db/09_seed_internal_agents.sql` |
| Models | `app/models/agent.py` |
| Review state machine | `app/services/agent_review_state_machine.py` |
| Graph-derived review (Layer 1 reuse) | `app/services/agent_review_orchestrator.py` |
| Promotion | `app/services/agent_promotion.py` |
| Human decision + runnable computation | `app/services/agent_decision.py` |
| Code-sourced review (critique + bandit) | `app/services/code_review.py` |
| Sandbox | `app/services/sandbox.py` |
| Search/browse (RRF) | `app/services/agent_search.py` |
| API routes | `app/api/agent_store.py` (`GET /v1/agent-store`, `POST /v1/agent-store/promote`, `POST /v1/agent-store/{id}/decide`, `GET /v1/agent-store/{id}`) |
| Decomposition suggestions | `app/api/decompose.py` (`suggested_agents` field) |
| Frontend browse/search | `frontend_v2/app/agents/page.tsx` |
| Embedding backfill | `scripts/backfill_agent_embeddings.py` |
| Live verification | `integration_check_v2_agent_review.py`, `integration_check_v2_agent_promotion.py`, `integration_check_v2_code_review.py`, `integration_check_v2_sandbox.py` |

---

## 9. What's genuinely still open

Carried forward honestly from `AGENT_STORE_PLAN.md`, not resolved by
writing this document:

- Graph-derived promotion is still a manual trigger. No real reuse data
  exists yet to threshold an automatic one against.
- The sandbox's non-root behavior is unconfirmed in production.
- Filesystem isolation for the sandbox doesn't exist.
- No agent currently has `execution_mode` wired to actually *run* the
  code-sourced path end to end in a real request handler, the sandbox
  and its gate exist and are verified standalone, but nothing yet
  routes a live `POST` at a `runnable=true` external-marketplace agent
  through it the way `ExecutionHarness` does for internal skills.
