# Corpus wave — Phase 10, deliverable 2: claim retrieval + claim-graph check

Run 2026-09-02 against live Supabase (`wckeklqxmiglivfolujn`, ap-south-1 session pooler).
Reproducible probe: `.scratch/corpus_wave/_claim_probe.py` (hand-run, not pytest).
Raw output: `.scratch/corpus_wave/_claimprobe_out.json`.

Callables exercised (all `app.services.claim_graph_api`, scope `AccessScope.unrestricted()`):
`get_claim_graph_overview(link_mode="both")`, `get_claim_neighbors`, `traverse_claim_graph`,
plus `app.services.domain_search.search_global(object_types=["claim"])` for the worked example.

## 1. `get_claim_graph_overview(link_mode="both", limit=200)`

| metric | value |
|---|---|
| claims_total | 15 |
| claims_shown | 15 |
| edges | 30 |
| edges_by_kind | `{"similarity": 30}` |
| relation edges | **0** |
| by_status | `{"current": 15}` |

All 15 corpus_wave claims are live, embedded, and visible. The graph is held together
entirely by **computed k-NN similarity edges** (`sim_k=3`, `sim_threshold=0.55`,
cosine in the 1024-d claim-embedding space). There are **zero `relation` edges**
(`custom_edge_type ∈ ALL_CLAIM_RELATIONS`) — no SUPPORTS / CONTRADICTS / DEPENDS_ON
claim↔claim edges were written this wave. This matches `claim_graph_api.py`'s own
docstring ("the relation graph is sparse-to-empty in real corpora"): the ingest path
(`app.services.claims.capture_claim`) records each claim + its embedding and lets the
overview compute similarity adjacency; it does not assert typed inter-claim relations.
That is a real, expected negative result, not a bug — typed relations would come from a
later synthesis/debate pass, not from bulk claim capture.

## 2. `get_claim_neighbors` / `traverse_claim_graph` on 3 seed claims

| seed claim | source | relation_neighbors | traverse supporting / contradicting / other |
|---|---|---|---|
| `f38d9dec…` "LLM accuracy degrades non-uniformly as input length grows" | S20 | 0 | 0 / 0 / 0 |
| `36c151f0…` "Trimming MCP tool context … ~69% weighted-avg, 100% sufficiency" | S24 | 0 | 0 / 0 / 0 |
| `be5d06b5…` "A code fix is verified iff it flips FAIL_TO_PASS while keeping PASS_TO_PASS green" | S36 | 0 | 0 / 0 / 0 |

Both relation-graph primitives return empty for every seed — consistent with §1
(no typed claim↔claim edges exist yet). The **similarity** layer is where the corpus
connectivity lives; `get_claim_graph_overview` and `search_global`'s claim leg both
surface it, so claim retrieval is not blind here — it just runs on embedding proximity
rather than asserted relations.

## 3. Worked example: query → seed claim → neighbours → corpus source

Query (`search_global`, `object_types=["claim"]`, `AccessScope.unrestricted()`):

> "does a shorter focused prompt beat a long one full of context"

- **Seed claim** (rank 1, RRF score 0.0328): `5d9413d7-7d2e-4518-b9a6-9e425f94f2b4`
  — *"Focused (~300 tok) prompts beat full (~113k tok) prompts across all models on
  LongMemEval"* — **source S20** (Chroma Context-Rot tech report).
- **Similarity neighbours of the seed** (from the overview's k-NN edges):

  | neighbour claim | weight | corpus source |
  |---|---|---|
  | "LLM accuracy degrades non-uniformly as input length grows" | 0.877 | **S20** |
  | "Models score higher on shuffled haystacks than logically-structured ones" | 0.799 | **S20** |
  | "DFSDT … improves LLM planning on multi-tool tasks vs linear planning" | 0.810 | **S50** (ToolBench) |
  | "Trimming MCP tool context to task-relevant reduced context ~69% weighted-avg …" | 0.803 | **S24** (Save-The-Token) |
  | "A skill should be admitted only when a benchmark shows it beats baseline and generalizes" | 0.792 | **S02** (skill-creator) |
  | "OWASP treats passwords <15 chars as weak without MFA …" | 0.706 | **S46** (OWASP) |

  So a plain-English question about prompt length lands on the correct S20 experimental
  claim and, one similarity hop out, reaches the *other* S20 findings **and** the S24
  tool-context-trimming benchmark that S20 is supposed to support — the intended
  "focused context beats full context" bridge, recovered by embedding proximity with no
  hand-authored edge.

## 4. Is the S20 Context-Rot experimental cluster connected?

**Yes.** The 5 S20 EXPERIMENTAL claims form a single connected component over similarity
edges (`reachable_count = 5`, `connected = true`), with `f38d9dec…` ("degrades
non-uniformly …") as the hub:

- internal S20↔S20 similarity edges: **7** (weights 0.787 – 0.877)
- edges bridging the S20 cluster to the other 10 corpus claims: **11**
  (to S24 ×2, S02 ×2, S50, S46 ×2, S33 — weights 0.706 – 0.810)

The cluster is neither isolated nor a separate island: it is the densest region of the
corpus claim graph and is the anchor the other sources' derived claims attach to, exactly
as the wave design intended.

## Bottom line

- Claim retrieval works: `get_claim_graph_overview` returns all 15 claims + 30 similarity
  edges; `search_global`'s claim leg ranks the right claim first for a paraphrased query
  and the overview's k-NN edges walk outward to the semantically related corpus sources.
- The **relation** graph (typed claim↔claim edges) is empty this wave — expected, and the
  similarity layer covers connectivity in its place.
- The S20 Context-Rot cluster is connected internally and bridged to the rest of the corpus.
