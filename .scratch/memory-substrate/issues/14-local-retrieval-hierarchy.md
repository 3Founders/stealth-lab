# Local retrieval hierarchy

Type: grilling
Status:
Blocked by: 05, 10

## Question

What is the local-first retrieval design, and how does it sit on top of the retrieval machinery that already exists?

spec.md wants a retrieval hierarchy of structural locality → temporal locality → causal/graph locality → semantic retrieval → reranking. The current task should identify a local epistemic neighbourhood (repository, branch, current files, symbols, dependencies, recent commits, recent failures, related tests, recent procedure executions, relevant claims, artifacts). It explicitly forbids dumping global memory into the model, and requires that as memory grows the relevant model context stays approximately local.

The relevant existing facts — this is the best-developed area of the codebase:

- `HybridRetriever` (`retrieval.py`): pgvector cosine + Postgres FTS, fused by RRF with `RRF_K=60`, then a bounded one-hop graph expansion via `GraphStore.traverse_from`. `PARENT_OF` edges are skipped during expansion; hierarchy-group nodes are excluded from both legs. Vector failure degrades to lexical-only, loudly.
- `hierarchy.py`: beam descent over an HTN tree with internal nodes routing on the mean of children's embeddings, confidence-adaptive beam widening, and a `used_flat_fallback` signal returned to the caller rather than a silent fallback.
- `call_graph.py`: tree-sitter, name-based static reachability, explicitly advisory, bounded at 4000 files / 2 hops / 200 nodes. **This is the repo's only existing structural-locality mechanism.**
- `reuse_detection.py` uses raw cosine with thresholds rather than RRF ranks, deliberately, because reuse needs an absolute number.
- **There is no reranker anywhere** in project code.
- **`retrieval.py` has zero test coverage.** The RRF arithmetic, the `plainto_tsquery` AND→OR rewrite, the hierarchy-group exclusion and the `PARENT_OF` expansion skip are all untested; the one test file that references it monkeypatches `retrieve` away. `graph_store.traverse_from` is likewise untested and marked NOT LOAD TESTED in-source.
- `graph_memory.py` deliberately reports flat retrieval and hierarchical routing **separately and never blends them**.

Decide:

- What defines the local neighbourhood, as an actual query? "Current repository, branch, files, symbols" is a description; the decision is which of those are indexed columns, which are graph traversals, and which are computed per request.
- How do the five tiers compose — a cascade where each tier filters the next, a union with tier-weighted scoring, or a budget allocated across tiers?
- Does reranking land in milestone 1? Nothing exists today, and adding a cross-encoder or LLM reranker is a per-query cost on the hot path.
- What is retrieved *for what*? Retrieving procedures for reuse, claims for context, and episodes for evidence are three different queries with three different relevance criteria — should they share one retriever?

Grill these:

- The strongest thing this repo already has is `call_graph.py`, and it is advisory and name-based. Structural locality in a codebase is the tier spec.md ranks *first*. Is name-based reachability good enough to lead the hierarchy, or does leading with a weak signal poison everything downstream?
- **Context size is a stated metric, and the constraint is that context stays approximately local as memory grows.** What is the actual budget, and what enforces it? Without a hard cap, "local-first" is a preference that degrades silently.
- Is the overlap with ticket 12's "relevant local graph neighbourhood" a duplication? Decide whether applicability and retrieval share one neighbourhood computation or genuinely need two.
- Retrieval is the load-bearing component with zero tests. Does anything here get built on top of it before the existing behaviour is pinned down by tests? Weigh this against the migration/testing fog on the map.
