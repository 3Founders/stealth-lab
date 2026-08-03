# v1 Held Items — Resume-From Doc

Upload this back into a fresh conversation to pick up exactly here. Each
item below was designed (some partially built) during v0 development but
deliberately deferred — none of these block the current v0's core loop.

---

## 1. SLM + task-graph efficiency routing

**Status:** Designed, not built. Genuinely gated on a real dependency —
not stalled arbitrarily.

**The idea:** not every bottleneck needs the full 4-model debate panel.
Route by precedent:

```
Trigger fires
   -> existing approved precedent for this TaskNode type + low blast_radius?
        yes -> apply via a cheap, distilled SLM, tagged in provenance
               as "precedent-applied" not "freshly deliberated"
        no  -> full Vada debate (current system, unchanged)
```

**Where it plugs into what already exists:**
- `TaskNode.skill_ref` — natural home for "which distilled model handles
  this task type"
- `blast_radius` (already computed, already live-tested) — the routing
  signal
- `scorecards.recommendation` — extend to distinguish
  "adjudicated by full panel" vs. "applied via established precedent"

**Hard dependency, not skippable:** needs Layer 2 (empirical replay
testing, v1.1) to exist first — distillation needs real labeled
resolution trajectories, and there are currently zero completed debates
to learn from. Don't start this before Layer 2 lands.

**Also reopens Section 10:** an SLM *deciding* fits the current
overlay-shaped design. An SLM *executing* the task (not just proposing
graph changes) only makes sense if the product owns execution — the
still-unresolved runtime-vs-overlay question. Resolve that before
building the execution half of this.

---

## 2. Task decomposition visualization

**Status:** Backend done and live-verified. Frontend not started.

**Backend (done):** `GET /v1/graph/{task_id}?depth=N` in
`app/api/graph.py`, wired into `main.py`. Reuses `GraphStore.traverse_from`
directly — no new graph logic. Returns `{center, nodes[], edges[]}` shaped
for direct consumption by a node-graph library. Tested against a real
running server (first endpoint in the project verified via actual HTTP,
not just direct function calls) — confirmed correct against the seeded
demo workflow's real structure.

**Frontend (not started):** a new view — either `/approvals/[id]/graph`
or a tab on the existing case file — rendering the endpoint's response via
React Flow (recommended: handles directed, labeled-edge graphs well).
Style to match the existing case-file palette (paper-colored node boxes
on the ink background, edges labeled with their type, center node
highlighted). No backend work needed to start this — endpoint is ready.

---

## 3. Chat interface to query the knowledge system

**Status:** Designed only. This is the one place `VECTOR(1024)` columns
and Voyage AI config have existed since the very first schema, unused.

**Build order:**
1. **Generate real embeddings.** `Onboarder.seed()` needs to actually call
   Voyage at creation time and populate the existing `embedding` column
   (index type already chosen correctly for this: HNSW).
2. **Hybrid retrieval endpoint.** Embed the query, vector + full-text
   search, take top entrypoints, then `GraphStore.traverse_from` those
   entrypoints for connected context — not isolated matches. Reuses the
   same traversal function the graph-viz endpoint (#2 above) already
   uses.
3. **Chat endpoint**, grounded in the retrieved subgraph. **Hold this
   standard explicitly:** answers need the same citation discipline the
   debate panel already enforces — reuse `Layer1Evaluator`'s groundedness
   logic rather than building a second, looser version of "is this
   grounded" for chat specifically.

---

## Resume checklist

When this doc comes back in:
- Confirm which of the three is the actual next priority (they don't
  depend on each other, except #1's hard Layer 2 gate)
- For #1: confirm Layer 2 has actually landed before starting
- For #2: just needs the frontend half — no backend blockers
- For #3: needs an embedding-pipeline decision made before any code
