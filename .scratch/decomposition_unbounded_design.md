# Unbounded Decomposition — Design Note

Written 2026-09-01, against `main` @ `b428c4e`. Real change made this pass,
not a proposal.

## What existed

`app/services/decomposition.py::MAX_GENERATED_NODES = 15` — a hard-coded
semantic ceiling on generative task decomposition. It did two things:

1. Told the model, in `DECOMPOSE_SYSTEM`: "Use at most 15 tasks."
2. Rejected (`structural_problems.append(...)`, `safe_to_propose=False`) any
   proposal whose `node_count` exceeded 15, regardless of whether every
   node was legitimate, non-duplicate, and load-bearing.

`tests/test_decomposition.py::test_oversized_decomposition_is_rejected`
asserted this as correct behavior (a 30-node proposal *should* be
rejected, on count alone).

## What changed

- `MAX_GENERATED_NODES` constant removed entirely — not renamed, not
  raised to a bigger number.
- `DECOMPOSE_SYSTEM` prompt no longer instructs a step-count ceiling.
  Replaced with problem-sized guidance: generate exactly what the
  problem requires, no padding, no omission to fit a budget, one atomic
  task is a legitimate decomposition, so is a much larger graph.
- The `if result.node_count > MAX_GENERATED_NODES:` rejection block is
  gone. Removed, not replaced with a different threshold.
- `test_oversized_decomposition_is_rejected` replaced with
  `test_decomposition_size_is_bounded_by_the_problem_not_a_constant`,
  parametrized over `[1, 3, 15, 30, 200]` — a structurally valid
  decomposition of any of these sizes must be accepted. No single
  number in that list is special; the point of parametrizing is that
  none of them reads as "the new limit."
- Added `test_decomposition_padding_is_still_caught_by_dedup_not_a_
  node_count` — proves the REAL protection against a padding/
  malfunctioning generator (30 literally-identical proposed steps) is
  the existing `dedupe_changeset_ops` lexical-similarity collapse
  (`app/services/dedup.py`), not a node-count check. This was already
  wired into `decompose()` before this change and is untouched here.

## What was NOT touched, and why each is a legitimate resource control
(directive §3/§8's own distinction: infrastructure/resource limits stay,
architectural size limits go)

- `validate_generative()` (`app/models/change.py`) — the capability
  boundary: acyclic, no dangling refs, no duplicate refs, new-nodes-only.
  This is what actually stops a hijacked generator from doing damage; it
  has nothing to do with counting nodes.
- `dedupe_changeset_ops()` — collapses proposal-internal duplicates
  (new-vs-new). Unrelated to and unaffected by this change.
- `resolve_subtask_reuse()` — collapses duplicates of already-existing
  graph content (new-vs-existing). Unrelated and unaffected.
- The generator's own `max_tokens` (`app/debate/panel.py`, default 2000)
  — a real, existing infrastructure bound on how much any single model
  response can contain. This already naturally caps how many nodes one
  decomposition call can emit in one response; it is a token/resource
  limit, not an architectural "at most N tasks" decision, so it is the
  correct kind of control to lean on and was left exactly as it was.
- `app/services/call_graph.py`'s `max_nodes`/`max_hops` and
  `app/services/local_retrieval.py`'s `max_hops`/`max_nodes` — grepped
  and confirmed these are a DIFFERENT subsystem (call-graph reachability
  analysis, local-repo context retrieval), not decomposition semantics.
  Untouched; they were never in scope.
- No HTTP payload size limit exists yet at `app/api/decompose.py`'s own
  layer (grepped, confirmed absent) — flagged here as a real, honest gap
  rather than silently invented. Worth adding later as a genuine
  transport-layer resource control (request body size cap), but that is
  a different mechanism from a node-count semantic rule and was not
  fabricated here to compensate for removing the old one.

## Verification

```
cd backend && python -m pytest tests/test_decomposition.py -q
34 passed
```

`grep -rln "MAX_GENERATED_NODES"` across `app/` and `tests/` returns
nothing (only a stale `.pyc` bytecode cache matched before recompilation).

## What remains open

Hierarchical decomposition for genuinely large problems (directive §7 —
representing hierarchy through the existing execution/task graph
mechanisms rather than a second HTN system) was not built this pass;
today's decomposition still produces one flat proposed graph regardless
of size. That is real, separate work, not implied by removing the node
ceiling.
