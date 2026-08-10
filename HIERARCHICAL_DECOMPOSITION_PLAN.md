# Hierarchical Decomposition & Reuse Consolidation — Plan (v2, tested)

## Part A — Reuse Consolidation (build first)

**Problem**: `reuse_detection.py` only checks one incoming problem string at query time. New nodes generated within a single decompose call aren't checked against each other, and duplicates entering independently over time never get reconciled.

**Mechanism**: complete-linkage clustering — a candidate joins a cluster only if it matches *every* existing member, not just its nearest neighbor.
- Naive transitive union-find was tested and confirmed broken: it chains unrelated nodes together through intermediate near-duplicates (reproduced empirically — two nodes at 0.78 similarity, threshold 0.90, still got merged via a chain).
- Complete-linkage fixes this and was confirmed to split the same chain correctly.
- Reuses existing thresholds (`FULL_MATCH_THRESHOLD = 0.90`) — no new constants.

**Entry points**:
1. Inline, before persisting a new `ChangeSet` — check new nodes against each other and the existing graph.
2. Periodic batch sweep — catches duplicates from independent insertions over time.

**On merge**: earliest-created node is canonical; others get `t_invalid` set + a `DUPLICATE_OF` edge (bi-temporal model unchanged, nothing destructive). Merges in an ambiguous similarity band (~0.85–0.90) route through the existing debate/approval mechanism instead of auto-merging.
    
**Open decision**: logical edge redirection (resolve at query time) vs. physical rewrite — default to logical, safer.

---

## Part B — Hierarchical Tree (build later, once corpus scale justifies it)

### Tree construction
Recursive: at each node, decide decompose-or-terminal; if decomposing, must yield ≥2 children (enforces real structure, no single-child chains). No predefined schema — structure emerges from content, applies to any domain.

**How the decompose-or-terminal decision gets made** — three options, cost/quality tradeoff:
1. **LLM judgment** (original proposal) — best semantic quality, most expensive.
2. **Geometric clustering** (agglomerative / divisive k-means / HDBSCAN) on embeddings, stopping on a variance/silhouette threshold — deterministic, ~free, no semantic awareness.
3. **Hybrid** (recommended default): clustering proposes splits for free; LLM only called to validate ambiguous splits and to name/summarize internal nodes.

Cost is one-time at ingestion (confirmed: not a per-query cost); still, absolute cost across a large corpus is real — hybrid keeps it small.

### Embeddings
- **Construction**: LLM-authored rollup summary per internal node (generated free alongside the decompose call), embedded directly through the same embedder. Tested against plain vector-averaging for *routing quality* — see below; this step is about representation quality, not routing algorithm.
- **Maintenance**: cheap O(1) running-mean update when a new child attaches later; full LLM re-summarization only in periodic maintenance sweeps (avoids drift from repeated non-deterministic LLM calls).

### Query-time search — tested, results below
- **Routing signal**: mean vector. Tested against a representative-point-set alternative (farthest-point-sampled children, score = max similarity) — **mean vector won in every configuration tested**, sometimes by a large margin. Cause: max-of-K scoring inflates nodes with more internal spread regardless of true relevance — noise, not signal. Representative-set idea is dropped.
- **Beam width**: confidence-adaptive (start narrow, widen only when top-2 candidates at a level are within a small score gap) — confirmed to recover most of fixed-wide-beam's accuracy at meaningfully lower cost (e.g. 84.5% vs. 92.2% accuracy, at 76 vs. 135 comparisons, one tested config). Never use beam=1 fixed/pure-greedy — confirmed materially worse than any wider option.
- **Leaf level**: always exact comparison against real embeddings, never the tree's approximate signal (coarse-to-fine — shortlist via tree, decide via exact match).
- **Fallback**: if best available branch score is below an absolute floor at any level, fall back to flat scan rather than trust a low-confidence path.
- **Fuse lexical + vector at every level** (reuse existing RRF pattern), not just at leaves.

### Known limits (from testing, not yet mitigated)
- Accuracy degrades under query noise (paraphrase drift) regardless of routing method — this is a property of embedding similarity under drift, not specific to the tree, but greedy descent compounds it (a wrong turn at the top can't be corrected lower down). Not solved by anything tested so far; worth watching once real queries are running.
- Pathological tree shapes (too deep/thin or too flat/bushy) aren't prevented by the ≥2-children rule alone — give the decomposer a soft target branching factor, and check shape during the periodic maintenance sweep.

---

## Integration with existing architecture

- **Two separate trees**, one per node type (`task_nodes`, `knowledge_nodes`) — not one merged tree. Decomposition semantics differ (subtask vs. sub-clause); use cases differ (decompose-endpoint reuse vs. citation retrieval).
- **Cross-type relationships stay as typed edges** (`PRODUCES`, `VALIDATED_BY`, etc.) exactly as they work today — the tree is an internal per-type index, not a replacement for the relational graph.
- **Type classification** (task vs. knowledge vs. agent) happens once, before decomposition, via LLM or schema rule — decides which tree a node lands in; decomposition then proceeds within that type.
- **Bonus for knowledge_nodes specifically**: atomic leaf facts make Layer 1 citations precise (cite a specific clause, not a whole policy document) — direct improvement to groundedness checking, not just structural compatibility.
- Reuses existing infra throughout: `reuse_detection.py` thresholds, bi-temporal model, debate mechanism for ambiguous merges, `HybridRetriever`'s RRF fusion.

---

## Build order
1. Part A, inline check.
2. Part A, batch sweep.
3. Part B — prototype against a real corpus (SWE-bench-scale) once ingested, not synthetic data.
