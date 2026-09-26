# Hierarchical Goal-to-Procedure Retrieval Across 100 PostgreSQL/pgvector Shards

Research date: 2026-09-24. “Repo fact” below means observed in this checkout; “external finding/recommendation” means supported by the linked PostgreSQL/pgvector or research literature.

## 1. What the current repository already does, and whether it matches the stated premise

### Takeaway

The current repository does **not** centralize abstract Goal canonical rows: public Goals and Procedures are physically sharded, while the control database centrally stores routes, search projections, and Goal hierarchy edges. Its canonical retrieval path already avoids scanning every shard: it searches a centralized Goal projection, resolves concrete Goals, searches a centralized Procedure projection constrained by direct `goal_id`, and hydrates only the shards containing the selected candidates. The requested extension is therefore not “replace 100-way fanout with the existing design”; it is “make the existing Goal-first design hierarchy-aware, and decide when a full central Procedure index should give way to compact route projections plus per-shard ANN.”

### Cited Findings

- **Repo fact — topology.** The control database (`K000`) owns the shard registry, global object-to-shard routes, Goal/Claim/Procedure search projections, optional `goal_relations`, and the projection outbox. Canonical Goals, Procedures, and Claims live on the knowledge shards, including `K000`. [Repo fact: `docs/sharding.md`, lines 1–13](../../docs/sharding.md#L1-L13)

- **Repo fact — the premise differs from current placement.** A new public Goal is placed by weighted rendezvous hashing across active shards; a new Goal-local Procedure prefers its Goal’s shard and rolls over if that shard is not writable. Private/org rows remain on `K000`. A Goal can consequently own Procedures on more than one shard after rollover. [Repo fact: `docs/sharding.md`, lines 15–26 and 61–66](../../docs/sharding.md#L15-L26) [Repo fact: `app/services/shards.py`, lines 39–55 and 149–171](../../backend/app/services/shards.py#L39-L55)

- **Repo fact — Goals are not modeled as an “abstract” storage class.** The canonical `goals` status vocabulary is `candidate`, `active`, `deprecated`, or `merged`; abstraction is represented by optional `goal_relations(specific_goal_id, abstract_goal_id, relation_type='SPECIALIZES', status)` rather than a stored `is_abstract` flag. [Repo fact: `backend/db/83_goals.sql`, lines 65–118](../../backend/db/83_goals.sql#L65-L118) [Repo fact: `backend/db/95_control_plane_shards_projections.sql`, lines 83–102](../../backend/db/95_control_plane_shards_projections.sql#L83-L102)

- **Repo fact — hierarchy is optional and presently unused by the canonical retrieval service.** The service explicitly says `goal_relations` is never read; new semantic relations are initially written as `proposed`, best-effort, and never required for ingestion or retrieval. [Repo fact: `app/services/retrieval_service.py`, lines 1–32](../../backend/app/services/retrieval_service.py#L1-L32) [Repo fact: `app/services/identity_resolution.py`, lines 851–870](../../backend/app/services/identity_resolution.py#L851-L870)

- **Repo fact — current canonical search is already projection-first and Goal-constrained.** Tier 1 performs FTS and pgvector ANN on the central `goal_search_index`, fuses them with RRF, and semantically resolves at most three Goals. Tier 2 searches the central `procedure_search_index` with `goal_id = ANY(resolved_goal_ids) AND status='active'`; it then hydrates canonical rows with one batched query per involved shard. Tier 2 returns without searching Procedures when no Goal resolved. [Repo fact: `app/services/retrieval_service.py`, lines 287–329 and 380–423](../../backend/app/services/retrieval_service.py#L287-L329)

- **Repo fact — hydration does not fan out over all shards.** Candidate `procedure_row_id -> home_shard_id` pairs are grouped by shard and fetched concurrently, one call per involved shard; unreachable shards are reported as unavailable rather than interpreted as missing rows. [Repo fact: `app/services/shards.py`, lines 329–384](../../backend/app/services/shards.py#L329-L384)

- **Repo fact — projections are centralized, rebuildable, and deliberately not canonical.** The three `*_search_index` tables derive from current canonical state; the outbox coalesces refreshes, and a drainer under a per-object advisory lock always projects current state, so duplicate or out-of-order deliveries converge. The projection contains search text, embedding/model metadata, status/version, scope, direct `goal_id`, and `home_shard_id` for Procedures. [Repo fact: `app/services/search_projection.py`, lines 1–20 and 86–127](../../backend/app/services/search_projection.py#L1-L20)

- **Repo fact — consistency is eventual, with bounded inline catch-up and repair.** A local canonical write queues projection in the same transaction. A remote canonical writer enqueues on the control database after the shard write, projects immediately, and relies on reindex if that call is lost. Reads drain a small outbox backlog inline, while larger backlogs are reported as degradation rather than blocking. [Repo fact: `docs/sharding.md`, lines 36–45](../../docs/sharding.md#L36-L45) [Repo fact: `app/services/retrieval_service.py`, lines 260–274](../../backend/app/services/retrieval_service.py#L260-L274)

- **Repo fact — current Procedure indexing starts from direct ownership by a concrete Goal.** `capture_procedure()` resolves/creates the Goal, allocates stable `procedure_id` plus version-row `id`, selects `choose_child_shard(goal.home_shard_id, procedure_id, ...)`, records `object_routes` and `procedure_row_routes` for a remote write, inserts the canonical row with `achieves_goal_id` and `home_shard_id`, then enqueues and immediately projects the stable Procedure. [Repo fact: `app/services/procedures.py`, lines 251–320, 374–450, and 467–477](../../backend/app/services/procedures.py#L251-L320)

- **Repo fact — second-database tests prove the intended behavior.** Tests show a public Goal and its colocated Procedure really live only on `K001`, their route and projection rows live in the control DB, retrieval touches only `K001`, rollover may split Procedures from a Goal, and unavailable shards are not silently counted as empty for strict readers. [Repo fact: `backend/tests/test_sharded_writes_e2e.py`, lines 88–99, 119–165, and 176–189](../../backend/tests/test_sharded_writes_e2e.py#L88-L99)

- **Repo fact — local scale evidence is encouraging but not a 100-shard capacity result.** On one Windows/PostgreSQL 18 machine with synthetic data, 100,000 Goal projections produced p50/p95 vector top-20 latency of 3.9/6.4 ms; fused low-selectivity search was p50/p95 640/849 ms, and one projection drainer applied about 127 objects/s. The document explicitly labels these single-machine measurements, not a production capacity plan. [Repo fact: `docs/production_ingestion.md`, lines 71–104](../../docs/production_ingestion.md#L71-L104)

### Inferences

- The “abstract Goals remain centralized” premise should be treated as a **proposed topology change**, not a description of the checkout. The present central entities are the hierarchy relations and search projections; canonical public Goal rows are distributed. [Inference from the current placement and schema cited above.]

- For the stated scenario, a central abstract-Goal table is useful as a control plane, but it should not become a requirement for canonical Procedure identity. The durable, direct relationship already available is `Procedure.achieves_goal_id`; inherited abstract Goals should be a derived routing expansion of that direct link. This preserves retrieval when hierarchy is incomplete. [Inference grounded in the current direct Goal link and optional hierarchy design.]

- The current architecture is already the best answer to “avoid scanning 100 shards”: one centralized candidate query plus candidate-limited hydration. A hierarchy-aware extension should preserve this two-stage shape and add a route/posting expansion before the shard phase, rather than restoring a full scan as the normal path. [Inference from the current retrieval flow and the distributed routing literature in Section 2.]

- A full central Procedure HNSW is a scale cliff, not a correctness cliff. A 1,000,000-row 1,024-dimensional float32 projection contains about 4.1 GB of raw vector payload before HNSW, TOAST, text, metadata, MVCC, and index overhead. That arithmetic motivates a measured central-index budget, not a conclusion that central projection must fail. [Inference; the repository’s embedding dimension is 1,024 in migration 95 and current configuration.]

### Gaps

- The repository has no measured 100-shard workload, no central Procedure-projection capacity curve, and no production tail-latency distribution for concurrent shard hydration. The existing benchmark covers 100,000 synthetic Goals and 20,000 Procedures on one machine. [Gap from `docs/production_ingestion.md`.]

- There are no accepted-coverage, route-quality, or recall measurements for `goal_relations`; normal writers only create `proposed` edges. A safe hierarchy router cannot yet assume the hierarchy is complete or authoritative. [Gap from `identity_resolution.py` and `retrieval_service.py`.]

- The checkout does not state whether the proposed centralized abstract Goals would be full semantic Goals, routing-only summaries, or a compatibility view over remote concrete Goals. That choice changes write consistency but not the candidate-first retrieval shape. [Gap in the assignment premise relative to current schema.]

## 2. Comparing fanout, per-shard indexes, global projections, and hierarchical routing

### Takeaway

At 100 shards, the recommended normal path is **central Goal resolution plus either a central Procedure search projection or a compact central Goal→Procedure-shard posting index**, followed by local search on only `B << 100` selected shards and batched hydration. Pure 100-way fanout should be reserved for rebuild/audit, low-confidence hierarchy fallback, and completeness checks. A full central Procedure index is simpler and stronger while it fits; a compact central route projection plus per-shard pgvector indexes is the more scalable hybrid when the central vector index no longer fits comfortably. Raw shard scores should be merged directly only when the metric/model is homogeneous; FTS, vector, and hierarchy signals should use RRF or a deliberately calibrated normalization scheme.

### Cited Findings

#### 2.1 Important distinction: per-shard indexes and global fanout are not competing storage choices

- **External finding.** Per-shard indexes describe where search state resides; global fanout describes the query plan. A system can have one HNSW per shard and then either query all shards (fanout), one known-home shard, or a routed subset. The useful comparison is therefore fanout vs. targeted fanout over per-shard indexes, not “one index” vs. “many indexes.” [External engineering framing, consistent with the pgvector recommendation to partition and build per-partition indexes](https://github.com/pgvector/pgvector#indexing)

| Architecture | Normal shard probes | Consistency profile | Main advantages | Main failure/recall risks | Assessment at 100 shards |
|---|---:|---|---|---|---|
| Per-shard canonical indexes + full fanout | 100, bounded by concurrency | Strongest local read-after-write; no global projection lag | Straightforward global union; no duplicate search rows; easy rebuild | 100 index probes, 100 pool/network paths, tail equals slowest shard, global BM25/statistical distortion, partial outage ambiguity, load amplification | Appropriate for audits/rebuilds and cold hierarchy; poor default hot path |
| Per-shard indexes + known Goal/tenant route | 1 to `B` | Strong locally; depends on route completeness | Lowest query cost; canonical hydration can share the same shard query | Misses Procedures after Goal rollover, reparenting, or unindexed hierarchy; requires one-to-many route postings | Strong when the route table is durable and a complete overflow policy exists |
| Central full Goal/Procedure search projection | 0 search probes, then candidate shards | Eventual projection; inline catch-up and repair in current repo | One global ANN/FTS query, direct `goal_id` filtering, simplest global fusion, no 100-way candidate fanout | Central CPU/memory/storage bottleneck, projection lag, central availability dependency, duplicate embedding storage | Best default while measured central index size and refresh lag fit budgets |
| Central light route/posting projection + per-shard ANN/FTS | `B` selected shards, often 1–5 | Route eventual; local results fresh | Scales shard count independently; compact control plane; hierarchy can directly produce shard set | Bad/stale hierarchy silently drops candidates unless exclusions/fallback are safe; more coordinator logic | Best large-scale hybrid; needs route-quality and freshness SLOs |
| Hierarchical vector partition/meta-index | `B` selected partitions | Route/index eventual; local ANN current | Literature shows neighborhood-preserving partitions can concentrate neighbors into few shards | Partition boundaries can cut neighborhoods; wrong route is unrecoverable recall loss; retraining/rebalancing costs | Useful if placements are semantic rather than Goal-local; not necessary when explicit Goal ownership exists |

- **Repo fact.** The current checkout implements the third row: a full central Procedure search projection followed by candidate-shard hydration. [Repo fact: `app/services/retrieval_service.py`, lines 402–423](../../backend/app/services/retrieval_service.py#L402-L423)

#### 2.2 Global fanout: correctness versus query amplification

- **External finding.** A distributed kNN implementation normally asks every shard for a local candidate set and then computes the global top `k`; the coordinator cannot safely receive only a scalar “best” per shard when it needs the global ordering. Raising per-shard candidate count improves accuracy at higher cost. [Elasticsearch kNN engineering reference](https://www.elastic.co/docs/solutions/search/vector/knn)

- **External finding.** PostgreSQL 18 `postgres_fdw` can execute foreign-table `Append` subplans concurrently when `async_capable` is enabled, but serializes queries to a given foreign server. More importantly, Jonathan Katz’s pgvector experiment found distributed KNN’s `Merge Append` remained serial in the tested version, so FDW federation should not be assumed to turn 100 shard KNNs into one parallel round trip without checking the actual plan. [PostgreSQL 18 `postgres_fdw` async options](https://www.postgresql.org/docs/18/postgres-fdw.html#POSTGRES-FDW-ASYNC) [Jonathan Katz, “Distributed queries for pgvector”](https://jkatz.github.io/post/postgres/distributed-pgvector/)

- **External finding.** PostgreSQL partition pruning can remove partitions when partition constraints prove they cannot match, including at execution time for parameterized nested-loop values. It still requires a partition/routing key; 100 partitions is within PostgreSQL’s documented “few thousand” planning envelope, but leaving most partitions unpruned incurs planning, memory, and execution cost. [PostgreSQL partition pruning](https://www.postgresql.org/docs/current/ddl-partitioning.html#PARTITION-PRUNING)

- **Inference.** If the coordinator issues 100 local top-`m` searches concurrently, latency can approach the slowest shard plus merge time, but aggregate work still grows with 100. With a concurrency cap `C`, expected scheduling depth is approximately `ceil(100/C)` waves, before retries, pool contention, or stragglers. [Inference from scatter/gather execution and PostgreSQL’s parallel-append behavior](https://www.postgresql.org/docs/current/parallel-plans.html#PARALLEL-APPEND)

- **Inference.** Full fanout is the correct oracle/control experiment, not the default online strategy. It can measure the recall and latency lost by every routing shortcut and can rebuild/verify indexes. Partial shard failure must be surfaced as incompleteness; healthy-shard-only results cannot be labeled a global top-`k`. [Inference; this matches the repository’s current unavailable-shard semantics and the distributed kNN candidate model.]

#### 2.3 Per-shard pgvector indexes and metadata filters

- **External finding.** pgvector defaults to exact nearest-neighbor search, which has perfect recall; HNSW and IVFFlat trade exactness for speed. HNSW has a strong latency/recall tradeoff but slower builds and more memory. [pgvector documentation](https://github.com/pgvector/pgvector#indexing)

- **External finding.** With approximate indexes, pgvector applies ordinary filters after scanning the ANN candidates. Its own example says a predicate matching 10% of rows may leave only about 4 of the default 40 HNSW candidates; iterative HNSW/IVF scans continue until enough results or a budget is reached. For filters over many values, pgvector recommends partitioning, while a regular index on a highly selective filter can support fast exact search. [pgvector filtering and iterative scans](https://github.com/pgvector/pgvector#filtering)

- **External finding.** ACORN extends HNSW so arbitrary predicate-induced subgraphs remain navigable, avoiding the generic pre-filter versus post-filter tradeoff. It is research software, not a stock pgvector feature; Filtered-DiskANN similarly modifies graph construction using labels. These approaches matter as long-term options but should not be represented as available in the current PostgreSQL extension. [ACORN paper](https://arxiv.org/html/2403.04871) [Filtered-DiskANN paper](https://harsha-simhadri.org/pubs/Filtered-DiskANN23.pdf)

- **Metadata routing recommendation.** Use metadata in three layers:

  1. **Hard security/scope filters first:** `tenant_id`, visibility, status, exclusion, and authorization predicates remain non-compensatory and must be rechecked against authoritative canonical state. A routing hint must never widen access. This preserves the repository’s current separation between candidate generation and factual hard constraints. [Repo fact: `app/services/retrieval_service.py`, lines 425–446](../../backend/app/services/retrieval_service.py#L425-L446)

  2. **Exact route maps next:** maintain `tenant -> shard set`, `direct goal_id -> shard set`, `domain/tag -> shard set`, and, if hierarchy is adopted, `abstract goal_id -> shard set`. A Goal needs a **set**, not just its home shard, because rollover can split its Procedures. [Repo fact: `docs/sharding.md`, lines 20–23](../../docs/sharding.md#L20-L23)

  3. **Selectivity/estimate fallback:** when there is no exact route, estimate selectivity `s_f` and query-filter correlation. Route first when the estimated qualifying set is material, e.g. `N_s * s_f * c_{f,q} >= τ`; otherwise use the central projection, an overflow bucket, or full fanout. This is an operational heuristic, not a theorem, and must be measured per workload. [Inference informed by pre-/post-filter selectivity results in ACORN](https://arxiv.org/html/2403.04871)

- **Inference.** For this domain, Goal ownership is a much stronger routing signal than generic tenant/domain metadata. The route key should be the stable `procedure_id` plus its current `procedure_row_id` and `home_shard_id`; metadata is then used to trim the candidate set after routing. This avoids treating an approximate semantic label as if it were authoritative identity. [Inference from the current direct Goal relation and route tables.]

#### 2.4 Central/global projections

- **Repo fact.** The current global projections are already a full read model: compact search text and summaries, FTS vectors, HNSW-compatible embeddings, scope, lifecycle, direct Goal, and shard route. Canonical rows remain authoritative, and hydration returns the exact version row before hard checks and semantic judging. [Repo fact: `backend/db/95_control_plane_shards_projections.sql`, lines 136–228](../../backend/db/95_control_plane_shards_projections.sql#L136-L228) [Repo fact: `app/services/retrieval_service.py`, lines 415–453](../../backend/app/services/retrieval_service.py#L415-L453)

- **External recommendation.** A full central vector projection is attractive while one PostgreSQL instance can meet memory, write, and tail-latency budgets because it turns a 100-way distributed ranking problem into one ordinary FTS/ANN query. It also avoids merging per-shard BM25 statistics and gives one consistent candidate window. [Inference from PostgreSQL/pgvector capabilities and the shard-global merge behavior documented by OpenSearch](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/)

- **External finding.** OpenSearch warns that BM25 statistics are shard-local and that vector `k` is applied per shard, so shard count can change result order and RRF contributions. Rank aggregation before shard merging is not equivalent to aggregating after a global merge. [OpenSearch RRF, “effect of shard count”](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/)

- **Inference.** Do not put a full 1024-dimensional copy of every Procedure embedding into 100 shard-local indexes and also duplicate all of them centrally unless the query benefit is measured. Prefer one authoritative search representation for candidate generation; keep canonical vectors on shards for source-of-truth hydration and future re-embedding. If the central projection is retained, monitor projection storage, HNSW build time, upsert rate, `pending/failed/oldest_pending` lag, and catch-up rate. [Inference based on the current projection’s duplication and measured one-drainer throughput.]

- **Compact-projection alternative.** If central vectors are too large, centralize only fields needed for routing and coarse filtering: stable IDs, direct/inherited Goal IDs, scope, lifecycle, shard, text terms or a compact lexical signature, and optionally a reduced-dimensional/coarse vector. Query the relevant shard HNSWs with the original 1,024-dimensional vector, then globally merge and hydrate. This is a two-index representation with a clear consistency contract, so both generations must carry the same route and freshness watermark. [External recommendation grounded in two-level distributed similarity systems such as Pyramid and graph-partition routing](https://ar5iv.labs.arxiv.org/html/1906.10602)

#### 2.5 Hierarchical routing: from abstract Goal to a small shard set

- **External finding.** Pyramid builds a small meta-HNSW over semantic partitions, finds the nearest `K` meta-neighbors, queries only their corresponding sub-HNSWs, and merges local results. Its central claim is that semantically coherent partitioning makes a small subset of shards sufficient. [Pyramid paper](https://ar5iv.labs.arxiv.org/html/1906.10602)

- **External finding.** Modern graph-partition routing represents each shard by multiple sub-cluster representatives, not one centroid; it retrieves nearby representatives and routes to the owning shards. The paper argues that a single centroid can route poorly inside a large, non-convex shard and reports gains on its billion-scale workloads, but the numbers are workload-specific. [“Unleashing Graph Partitioning for Large-Scale Nearest Neighbor Search”](https://arxiv.org/html/2403.01797v1)

- **Inference.** An explicit Goal hierarchy is more reliable than an inferred centroid hierarchy for this domain: accepted semantic Goal relations plus direct `Procedure.achieves_goal_id` provide deterministic postings. The ANN literature is still relevant as a fallback when a query has no resolved Goal, metadata is ambiguous, or the direct ownership relation is absent. [Inference from the current domain model plus Pyramid and graph-partition routing.]

- **Goal closure query.** For an abstract Goal `G`, derive concrete Goals with an accepted-relations closure. `UNION`, not `UNION ALL`, is important because `goal_relations` is multi-parent and cycles should not multiply rows:

```sql
WITH RECURSIVE concrete_goal(id) AS (
    SELECT $1::uuid
  UNION
    SELECT r.specific_goal_id
    FROM goal_relations r
    JOIN concrete_goal g ON g.id = r.abstract_goal_id
    WHERE r.relation_type = 'SPECIALIZES'
      AND r.status = 'accepted'
)
SELECT id FROM concrete_goal;
```

  This is a design pseudocode query, not a claim that the current schema or service already executes it. [Based on the current relation columns and statuses in `backend/db/95_control_plane_shards_projections.sql`.]

- **Recommended route table/materialization.** At 100 shards, prefer a central derived posting keyed by abstract Goal:

```text
goal_procedure_route(
  abstract_goal_id,
  procedure_id,
  procedure_row_id,
  direct_goal_id,
  home_shard_id,
  relation_path,
  hierarchy_revision,
  status,
  visibility,
  tenant_id,
  embedding_model,
  projected_at
)
```

  Direct membership must be derived from the canonical Procedure link. Inherited membership is derived only from accepted relations and is rebuildable. The table is a routing/read projection, not a second canonical truth. [Recommendation based on the current rebuildable projection pattern and the distributed-routing requirement for a Goal→shard set.]

- **When the hierarchy is not authoritative, do not use inclusion-only pruning.** Current `coarse_route_safe_exclusions` correctly preserves objects that cannot be positively assigned to another branch, so new unclustered rows are not dropped by a stale hierarchy. Apply the same principle to shard routing: exclude only shards whose membership is proven unrelated, and retain a recent-write/overflow bucket for Procedures whose abstract membership is unresolved. [Repo fact: `app/services/hierarchy.py`, lines 1004–1072](../../backend/app/services/hierarchy.py#L1004-L1072)

- **Safe online router pseudocode:**

```text
search(task, scope):
    abstract_goals = resolve_goals_from_central_projection(task, scope)
    candidates = []

    for goal in abstract_goals:
        postings = goal_procedure_route(goal, scope, status=active)
        candidates += top_procedure_candidates(postings, task, m)

    if candidates and route_freshness_ok() and not repair_pending():
        return hydrate_and_rank(candidates)

    shard_set = union(
        goal_home_shard(abstract_goals),
        goal_procedure_route_shards(abstract_goals),
        recent_or_unrouted_shards(scope),
    )

    if completeness_required and (confidence_low() or not hierarchy_fully_built()):
        shard_set = all_readable_shards

    local_results = parallel_top_m_per_shard(shard_set, task, scope)
    candidates = globally_merge(local_results)
    return hydrate_and_rank(candidates)
```

  `route_freshness_ok()` should be backed by explicit watermarks, not a timer alone. `recent_or_unrouted_shards` is the correctness safety net; it may be narrowed over time as hierarchy coverage becomes provable. [Recommendation inferred from the current stale-index-safe exclusion implementation and distributed hierarchical search literature.]

- **Adaptive branch width.** Start with `B=1–3` candidate shards, increase to `B=8` when the top route scores are close, then use accepted-postings overflow or full fanout when coverage is unknown. This is the same confidence-adaptive beam idea already present in the repository’s hierarchy search, translated from branches to shards. [Repo fact: `app/services/hierarchy.py`, lines 19–23 and 501–504](../../backend/app/services/hierarchy.py#L19-L23)

#### 2.6 Top-k merge and score normalization

- **Same vector metric and model:** if every shard returns cosine distance from the same embedding model, do not normalize distances across shards. Fetch at least `m >= k` from each shard and perform a deterministic global heap/k-way merge:

```text
m = max(k, oversample_for_approximation_or_filtering)
heap = []
for shard in selected_shards:
    for row in shard.search(query_vector, m):
        heap.push((row.distance, row.procedure_id, row.shard_id))

return first k(heap)
```

  Tie-breaking by stable ID makes output deterministic. Fetching local `k` is sufficient for an exact global top-`k` in a single homogeneous ordering; use `m > k` for approximate indexes, aggressive filtering, or uncertainty about ties. [Inference from the standard scatter/gather top-k model documented by Elasticsearch](https://www.elastic.co/docs/solutions/search/vector/knn)

- **RRF for incomparable result legs:** for FTS and ANN lists, use ranks rather than pretending BM25 and cosine are the same quantity:

```text
rrf(d) = sum over legs l present in L_l of w_l / (k_rrf + rank_l(d))
```

  `rank` is 1-based, absent legs contribute zero, and `k_rrf=60` is the common default. This matches the current repository’s RRF and OpenSearch’s formula. [Repo fact: `app/services/identity_resolution.py`, lines 166–185](../../backend/app/services/identity_resolution.py#L166-L185) [OpenSearch RRF](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/)

- **Hierarchy as a soft leg:** accepted direct ownership or hard preconditions should filter/disqualify; a proposed/soft hierarchy score can be an additional weighted RRF leg, but it must not compensate for a visibility, tenant, status, or precondition failure. Preserve the repository’s non-compensatory applicability cascade. [Repo fact: `docs/retrieval_architecture.md`, lines 19–28](../../docs/retrieval_architecture.md#L19-L28)

- **Score normalization choices:**

```text
fixed-bound cosine similarity: s_norm = (cos + 1) / 2
query-independent calibration: z = (s - mu_l) / sigma_l
per-result-set min-max: z = (s - min_l) / (max_l - min_l)
sigmoid: z = 1 / (1 + exp(-s))
```

  Min-max preserves score margins but is sensitive to outliers and changes with each query’s returned set. L2/z-score requires stable per-leg calibration statistics. RRF is the robust default when only ordering within each leg is trustworthy. [OpenSearch comparison of RRF with min-max/L2/z-score normalization](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/)

- **Do not normalize independently per shard unless the shards are comparable populations.** With equal-sized, homogeneous shards, raw vector distance is directly mergeable. With unequal shard sizes or per-shard lexical statistics, local rank and score distributions differ; per-shard min-max can make an empty/short result set artificially extreme. Prefer one global merge for a single metric, or RRF across retrieval legs after defining a stable candidate window. [Inference from the shard-count warning in OpenSearch RRF](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/)

- **Shard health/load weight belongs to scheduling, not relevance.** Do not multiply user relevance by a health score. Use capacity/load weights to order probes, set deadlines, or choose which overflow shards to visit; final ranking should use the same embedding model and scoring semantics on every shard. [Recommendation; weighted HRW expresses placement capacity, not retrieval relevance.]

#### 2.7 Weighted rendezvous placement

- **Repo fact.** The current placement code already uses weighted HRW. For key `k` and shard `i`, it hashes a uniform value `u(k,i) in (0,1)` and minimizes `-ln(u(k,i)) / weight_i`; larger weights therefore win more keys, deterministic tie-breaking is by `shard_id`, and a non-writable shard is excluded. [Repo fact: `app/services/shards.py`, lines 142–171](../../backend/app/services/shards.py#L142-L171)

- **External finding.** HRW maps an object and server identity to a pseudorandom weight and selects the highest weight, yielding a deterministic mapping that all clients can compute without server state. The weighted form preserves minimal disruption by adjusting only the changed server’s score instead of renormalizing all server scores. [Original HRW paper](https://www.microsoft.com/en-us/research/wp-content/uploads/2017/02/HRW98.pdf) [Weighted-HRW Internet Draft](https://datatracker.ietf.org/doc/html/draft-ietf-bess-weighted-hrw) (the draft is work in progress, not a normative standard).

```text
weighted_rendezvous(key, shards, wanted=1):
    ranked = []

    for shard in shards:
        if not shard.writable or shard.weight <= 0:
            continue

        u = uniform_open_01(hash(shard.shard_id, 0x00, key))
        score = -ln(u) / shard.weight
        ranked.append((score, shard.shard_id, shard))

    if ranked is empty:
        fail closed

    ranked.sort()
    return first wanted ranked entries
```

- **Placement pseudocode for this domain:** if a direct Goal has a writable home shard, place its new Procedure there; otherwise call weighted HRW with the stable `procedure_id`. Persist the selected `home_shard_id` and use it for later versions and evidence. Do not recompute an existing object’s home merely because shard weights changed. [Recommendation grounded in current `choose_child_shard` behavior and stored routes.](../../docs/sharding.md#L15-L26)

- **Weight choice:** keep weights simple and auditable—capacity share, remaining-headroom class, or a stable deployment tier—rather than multiplying several unrelated health signals. A newly added shard attracts approximately its weight share; removing it only affects keys that selected it. Existing objects stay put, so load rebalance requires a separate deliberate migration rather than a routing-hash side effect. [Inference from HRW semantics and the current rule that existing placements never move.](../../backend/app/services/shards.py#L149-L160)

### Inferences

- **Recommended architecture:** keep a centralized abstract-Goal/control plane and a full central Procedure search projection while its measured size and refresh SLO fit. If it stops fitting, replace the central ANN with compact Goal route postings and query only the selected shard HNSWs. This is a capacity-driven evolution, not an all-or-nothing rewrite. [Inference from the current architecture plus Pyramid/graph-partition routing.]

- **Do not copy all Procedure vectors into every shard.** Replicating all vectors turns storage shards into read caches and multiplies update cost. Replicate only routing representatives, centroids, or a small number of hot Procedures if measurements justify it. [Inference from consistency and update amplification; the current repo explicitly treats projections as rebuildable rather than replicated canonical truth.]

- **The central hierarchy should route, not certify applicability.** Accepted relations may positively include a small shard set; lack of accepted membership must not prove a Procedure is irrelevant until hierarchy coverage is measured and fresh. This avoids silently hiding brand-new Procedures. [Inference grounded in the repository’s stale-index-safe exclusion rule.]

- **For fixed abstract Goals, direct postings beat traversing the full semantic hierarchy on every query.** A recursive closure is simple and authoritative, but it adds repeated graph work and still requires a Goal→shard aggregation. A rebuildable closure/posting table reduces hot-path work; periodic reconciliation must compare it to canonical direct links and accepted relations. [Recommendation based on the standard control-plane plus routing-table pattern.]

- **A high-confidence path may query one shard; a completeness-critical path may still fan out.** The objective should determine the default. Ranking/recommendation can use adaptive routing and report degraded coverage; a conformance or audit query can use full fanout. Both should record `routed_shards`, `omitted_shards`, `route_revision`, `unavailable_shards`, and whether the answer is exact, approximate, or partial. [Recommendation based on the current durable retrieval-decision and outage metadata.]

### Gaps

- No public source or repository benchmark establishes an optimal `B` (number of shards), `m` (local top candidates), or hierarchy expansion threshold for this corpus. These must be tuned against a full-fanout oracle. [Gap.]

- The literature’s reported throughput/recall gains come from particular datasets, dimensionalities, index implementations, and hardware. They support the architecture class, not a forecast for StealthLab’s 1,024-dimensional Procedures. [Gap; see the workload-specific Pyramid and graph-partition evaluations.]

- The current corpus does not expose distributions for Procedures per concrete Goal, accepted-relation fanout/depth, rollover frequency, cross-Goal cross-over, or unindexed recent writes. Those distributions determine whether a Goal→shard set is usually 1, 2, or many shards. [Gap.]

- A 100-shard PostgreSQL deployment raises connection-pool, TLS/authentication, observability, shard rebalancing, and blast-radius issues not measured here. The current `ShardPools` lazily connects and applies a short backoff, but no 100-shard capacity/load test is present. [Gap from `app/services/shards.py`.]

## 3. How a new Procedure should be indexed, refreshed, and retrieved

### Takeaway

Keep canonical Procedure identity and lifecycle on its home shard, make `goal_id`, `procedure_id`, current version-row ID, and `home_shard_id` durable routing facts, and use a central rebuildable search projection to avoid 100-way candidate search. The safest future write protocol is a **shard-local transactional outbox** shipped to the control plane, because a remote canonical commit and a subsequent control-plane enqueue cannot be one local PostgreSQL transaction. The hot read path should resolve the abstract Goal, expand accepted descendants to a direct-procedure posting set, search the central Procedure projection or the resulting shard set, merge globally, and hydrate only finalists. New, reparented, or unclassified Procedures must enter a safe overflow/recent-write route until hierarchy and projection freshness prove them indexed.

### Cited Findings

#### 3.1 Exact current indexing sequence for a new Procedure

1. **Resolve/create the direct Goal.** `capture_procedure()` calls `find_or_create_goal_cached()` and uses the resulting stable `goal.id` as `achieves_goal_id`. [Repo fact: `app/services/procedures.py`, lines 251–279](../../backend/app/services/procedures.py#L251-L279)

2. **Allocate both identities.** A stable UUIDv7 `procedure_id` is allocated before optional identity resolution, and a separate version-row UUIDv7 `id` is allocated for version 1. [Repo fact: `app/services/procedures.py`, lines 214–232 and 303–311](../../backend/app/services/procedures.py#L214-L232)

3. **Place the canonical row.** `choose_child_shard(goal.home_shard_id, procedure_id, writable_shards)` prefers the Goal’s shard and uses weighted rendezvous only if the preferred shard cannot take writes. [Repo fact: `app/services/procedures.py`, lines 303–309](../../backend/app/services/procedures.py#L303-L309)

4. **Register a remote route before the remote row write.** The writer records `(procedure_id, home_shard)` in `object_routes` and `(row_id, procedure_id, version, home_shard)` in `procedure_row_routes`, then gets the remote pool. Failure cleanup removes those claims. [Repo fact: `app/services/procedures.py`, lines 311–320 and 456–460](../../backend/app/services/procedures.py#L311-L320)

5. **Write canonical data with direct routing fields.** One INSERT includes `achieves_goal_id`, `home_shard_id`, retrieval representation, embedding provenance, scope, visibility, lifecycle, and provenance. [Repo fact: `app/services/procedures.py`, lines 374–455](../../backend/app/services/procedures.py#L374-L455)

6. **Project the current stable Procedure.** For remote rows, the writer enqueues on the control DB and immediately calls `project_object`; failure is swallowed because the outbox can repair it. Local `K000` writes get the same projection request from the database trigger. [Repo fact: `app/services/procedures.py`, lines 467–477](../../backend/app/services/procedures.py#L467-L477) [Repo fact: `backend/db/95_control_plane_shards_projections.sql`, lines 268–312](../../backend/db/95_control_plane_shards_projections.sql#L268-L312)

7. **Project one live version per stable Procedure.** `procedure_search_index` is keyed by stable `procedure_id`; its `procedure_row_id`, version, status, text, summaries, verification summary, direct `goal_id`, embedding metadata, scope, and `home_shard_id` are refreshed from the latest live version. Merged goals are removed from the Goal projection. [Repo fact: `app/services/search_projection.py`, lines 51–64, 163–180, and 216–238](../../backend/app/services/search_projection.py#L51-L64)

8. **Support bounded read-your-writes.** A Goal query drains up to 500 pending outbox entries before searching; above that threshold it reports that recent writes may be absent instead of making the request unbounded. [Repo fact: `app/services/retrieval_service.py`, lines 260–274](../../backend/app/services/retrieval_service.py#L260-L274)

#### 3.2 Recommended indexing contract for centralized abstract Goals

- **Direct membership is mandatory and derived from canonical state.** Every Procedure projection and route posting must carry `direct_goal_id`; never require a recursive abstract hierarchy lookup to make a correctly linked Procedure discoverable. [Recommendation grounded in the current direct link and optional-hierarchy principle.]

- **Inherited membership is a derived index.** A central `goal_procedure_route` can map accepted abstract Goals to Procedure/shard postings, but each row must be rebuildable from canonical `procedures.achieves_goal_id` plus `goal_relations`. Do not copy Procedure steps, evidence, or lifecycle authority into it. [Recommendation consistent with the repository’s projection-as-derived-state rule.](../../backend/app/services/search_projection.py#L1-L20)

- **Index a version watermark and hierarchy revision.** At minimum, carry `canonical_updated_at/version`, `projected_at`, `route_revision`, and `embedding_model`; otherwise the coordinator cannot tell whether a missing route is “no match” or “not indexed yet.” The repository already has analogous hierarchy freshness metrics based on canonical versus indexed row counts. [Repo fact: `app/services/hierarchy.py`, lines 837–885](../../backend/app/services/hierarchy.py#L837-L885)

- **Handle missing embeddings explicitly.** The current Procedure writer permits a null embedding, while the projection still provides FTS text. A new Procedure is therefore searchable lexically but not by the vector leg until an embedding/backfill exists; retrieval metadata should expose this model/index lag rather than silently treating a missing vector as a low score. [Repo fact: `app/services/procedures.py`, lines 322–361 and 433–435](../../backend/app/services/procedures.py#L322-L361)

- **Reindex is a repair, not a backfill of authority.** The current `reindex` enumerates routed canonical IDs, re-enqueues them, drains, and removes orphan projection rows. A hierarchy-aware reindex should additionally compare direct Procedure→Goal links and accepted relations to route postings, deleting stale inherited postings and rebuilding recent/unrouted coverage. [Repo fact: `app/services/search_projection.py`, lines 307–347](../../backend/app/services/search_projection.py#L307-L347)

#### 3.3 Consistency and refresh choices

| Mechanism | Guarantee | Cost/failure mode | Recommended use |
|---|---|---|---|
| Synchronous central projection before write response | Search sees the write when the API returns | Couples Procedure write availability/latency to control DB; cross-database transaction is still absent | Only where strict read-your-writes is a measured requirement |
| Current control-plane outbox + remote enqueue after commit | Local writes enqueue atomically; remote writes are repairable but have a crash window | A crash after remote commit and before control enqueue can leave a silent gap until reindex | Current implementation; acceptable only with lag/audit alarms and repair |
| Shard-local transactional outbox/CDC + central consumer | Canonical row and “projection/route changed” intent commit together on the shard; duplicates expected | Consumer lag, ordering, idempotency, and poison-message handling | Preferred robust cross-database write protocol |
| Timer-based full reindex | Eventually repairs arbitrary gaps | Expensive at 100 shards; does not give a precise freshness boundary | Periodic audit/repair, not primary delivery |
| Watermarked incremental projection | Coordinator knows the applied revision and can distinguish lag | Requires stable canonical revision and ordered/coalesced per-object application | Required for safe adaptive routing and degraded reporting |

- **External finding.** AWS’s transactional-outbox guidance says the database change and outbox row should be written in one transaction so a downstream failure cannot silently create a dual-write gap; consumers must be idempotent because delivery can duplicate. [AWS transactional outbox pattern](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)

- **Important boundary.** A control-plane outbox can atomically accompany a canonical write only when both are in the same PostgreSQL transaction. It cannot atomically accompany a remote shard commit; the current remote `enqueue()` after commit and reindex repair reflect that boundary. A true shard-local outbox is the external-recommendation fix for that gap. [Inference from the current write path and transactional-outbox semantics.]

- **Repo fact — version changes stay on the procedure’s home shard.** `supersede_procedure` carries `achieves_goal_id` and `home_shard_id` forward, and execution evidence is colocated with the version row. The central stable-`procedure_id` projection is therefore updated rather than duplicated as a new search identity. [Repo fact: `app/services/procedures.py`, lines 489–517](../../backend/app/services/procedures.py#L489-L517) [Repo fact: `docs/sharding.md`, lines 61–66](../../docs/sharding.md#L61-L66)

- **Goal merge/reparent refresh.** Accepting or changing a hierarchy relation must produce a new hierarchy revision and enqueue affected abstract-Goal postings. Until that update is applied, use old postings plus a recent/unrouted safety route; after it commits, new queries can use the new set. Goal merge must repoint canonical Procedures and update the stable central projection, as current sharded merge handling already does. [Recommendation; current merge semantics are described in `docs/sharding.md`, lines 61–66.]

- **Embedding migration.** Keep `embedding_model`, dimension, and projection version in every global/posting record and filter ANN to one model, as the current service does. During re-embedding, serve the old complete model generation or a complete new generation; do not compare distances from different spaces. [Repo fact: `app/services/retrieval_service.py`, lines 203–209](../../backend/app/services/retrieval_service.py#L203-L209) [Repo fact: `backend/db/95_control_plane_shards_projections.sql`, lines 136–150](../../backend/db/95_control_plane_shards_projections.sql#L136-L150)

#### 3.4 Recommended hot path in the stated centralized-abstract-Goal topology

```text
1. Embed and search the centralized Goal/abstract-Goal projection.
2. Resolve accepted abstract Goal candidates.
3. Expand accepted Goal descendants and read direct Procedure postings.
4. Filter postings by tenant, visibility, lifecycle, scope, and direct Goal membership.
5a. Full central Procedure projection available:
      run FTS + same-model ANN, goal-id constrained
      RRF fuse, select candidate rows and home_shard_id values
5b. Compact projection / central ANN budget exceeded:
      obtain a small Goal -> Procedure-shard set
      query top-m per selected shard concurrently
      merge raw vector scores for one metric, or global leg lists with RRF
6. Group selected rows by home_shard_id; hydrate one batched canonical query per shard.
7. Apply hard constraints to canonical rows, semantic judge finalists, then evidence selection.
8. Return route/projection freshness, omitted shards, and unavailable shards with the result.
```

- **Inference.** Step 5a is the current repository’s architecture and remains the preferred default at 100 shards if its central index budgets hold. Step 5b is the scale escape hatch. The canonical hydration and hard-constraint stages in step 7 should stay unchanged in spirit: candidate generation is approximate; authority and eligibility are checked against canonical rows. [Inference from `app/services/retrieval_service.py`, lines 380–499.]

- **Inference.** If abstract Goals are centralized, a useful minimal new artifact is not a second full Procedure vector index but a `goal_id -> home_shard_id` posting set with coverage revision. It can answer “where must I search?” while the current global projection answers “what candidates?” and the shard remains authoritative for the Procedure body. This cleanly separates routing, candidate generation, and authority. [Recommendation derived from the current projection/hydration split.]

### Inferences

- **Final architectural verdict:** at 100 shards, do not replace the centralized Procedure projection with mandatory 100-way fanout. Keep the current central candidate index if it fits; add hierarchy only as a way to select concrete Goals, candidate rows, or shard sets. Fall back to fanout for cold, stale, low-confidence, or audit cases. [Inference from all cited evidence.]

- **Simplest safe next design step:** make the existing central `procedure_search_index.goal_id` filter hierarchy-expanded by accepted descendants. This immediately avoids shard fanout and preserves the current hydration path. It does not require 100 local ANN queries; the route is still resolved centrally. The risk is the central vector index’s size, which should be measured before introducing local routed ANN. [Recommendation based on current SQL in `retrieval_service.py`, lines 402–408.]

- **When to move to local routed ANN:** trigger that change when measured central projection storage/HNSW build/upsert latency or control-plane tail latency breaches a stated SLO, not merely when shard count reaches 100. Keep the same `goal_id`, scope, model, and freshness contracts in both modes so ranking policy and canonical checks do not fork. [Recommendation.]

- **Consistency priority:** move remote write publication from “enqueue after commit, repair later” to a shard-local outbox/CDC before relying on hierarchy exclusions for recall-critical routing. An outbox solves delivery intent, not semantic route correctness; accepted-relation coverage and read-your-writes still need explicit revisions. [Inference from AWS outbox guidance and current failure boundary.]

- **Operational acceptance criteria:** compare each mode to a full-fanout oracle on at least Recall@10/20, NDCG or judged applicability, p50/p95/p99 latency, shards touched, bytes transferred, projection lag, and unavailable-shard behavior. A route is “accurate enough” only if its recall loss is bounded for the product’s decision class; a latency win alone is insufficient. [Recommendation; no repository numbers exist for these metrics.]

- **Security invariant:** a shard hint, hierarchy route, or global projection can reduce work but cannot authorize a result. Current canonical hydration and the non-compensatory hard-constraint cascade must remain after candidate selection. [Recommendation grounded in the current access/applicability design.]

### Gaps

- The correct target architecture depends on expected Procedure count, vector dimension/model, write rate, hierarchy depth/fanout, and acceptable read staleness. Those inputs are not supplied, so the note cannot set a final central-index size limit or shard-probe count. [Gap.]

- No direct source establishes that centralized abstract Goals are operationally superior to distributed Goals for this product. The recommendation treats centralization as a given premise and evaluates only retrieval around it. [Gap.]

- Current hierarchy edges are semantic proposals, not a guaranteed taxonomy. Before hard-routing, the product needs an acceptance/review policy and metrics for precision, coverage, depth, cycles, and stale revisions. [Gap.]

- The current remote route-before-write sequence is repairable but not an atomic distributed transaction. A crash study has not been run to quantify dangling routes, silent projection gaps, and operator recovery time across a real 100-shard topology. [Gap.]

- The external literature supports selective hierarchical routing, but none supplies turnkey PostgreSQL implementation for accepted multi-parent Goal hierarchies. The proposed posting table and watermark protocol are an architectural recommendation, not an experimentally validated StealthLab result. [Gap.]
