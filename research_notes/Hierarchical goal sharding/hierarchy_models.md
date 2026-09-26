# Hierarchical goal sharding: models, mapping, and recommendation

## Key Question 1 — Which hierarchy or graph structures satisfy the requirements?

### Takeaway

The best semantic structure is a **versioned, labeled directed acyclic graph whose canonical storage is an edge/adjacency table**: `specific_goal_id SPECIALIZES abstract_goal_id`, with edge lifecycle, provenance, and audit fields. A tree or nested-set representation fails the multiple-parent requirement; a closure table is valuable only as a rebuildable reachability projection; RDF/property graphs/knowledge graphs/OWL/SKOS supply useful semantics and interchange ideas but do not remove the need for an application-level review workflow.

### Cited Findings

#### The candidate models are different layers, not mutually exclusive storage products

- A rooted tree has exactly one parent per non-root entity, while a child can have many children. Descendant queries using a scalar `parent_id` require iterative traversal. This is the standard adjacency-list tree shape. — [Colby, *Integrating and Using Hierarchical Vocabularies in SAS*](http://support.sas.com/resources/papers/proceedings14/2064-2014.pdf)
- A relational **adjacency list** can mean either a scalar `parent_id` column or an edge table containing adjacent node pairs. The former encodes a tree; the latter can encode a general graph. PostgreSQL supports hierarchical and graph traversal from immediate relationships through recursive CTEs, and its documentation explicitly requires cycle detection for cyclic graphs. — [PostgreSQL recursive-query documentation](https://www.postgresql.org/docs/current/queries-with.html)
- A **DAG** is the logical acyclic shape, not a physical storage format. It can be stored as an adjacency/edge table. The acyclicity requirement is separate from the decision to use PostgreSQL rather than a graph database.
- A **property graph** is a graph data model with labels and properties on nodes and edges; the GQL standard defines operations for creating, accessing, querying, maintaining, and controlling property graphs. A formal description of the GQL model treats it as a mixed multigraph whose nodes and edges carry labels and property values. — [ISO/IEC 39075:2024](https://www.iso.org/standard/76120.html); [Green, Guagliardo, and Libkin, *Property graphs and paths in GQL*](https://doi.org/10.54285/ldbc.tzjp7279)
- A **knowledge graph** is an umbrella application/data model rather than one required physical schema: the standard survey defines it as a graph of real-world knowledge and notes that the underlying data graph may be a directed edge-labeled graph, heterogeneous graph, or property graph. — [Hogan et al., *Knowledge Graphs*, ACM Computing Surveys](https://dl.acm.org/doi/fullHtml/10.1145/3447772)
- An **ontology/specialization model** is principally a semantic layer. OWL 2 represents a subclass relation as a set-inclusion axiom and supports inference from a subclass to a superclass. Its ontology is a set of axioms, so a class can participate in more than one `SubClassOf` axiom; no single-parent constraint is imposed. — [W3C OWL 2 Structural Specification](https://www.w3.org/TR/owl2-syntax/); [W3C OWL 2 Direct Semantics](https://www.w3.org/TR/owl2-semantics/)

#### Relational hierarchy-model comparison

| Model | Multiple abstract parents | Versioned edge state | Audit/provenance | Routing performance | Fit here |
|---|---|---|---|---|---|
| Parent/child tree (`parent_id`) | **No**: one parent per node | No natural edge row; a changed parent mutates one node | Node attributes cannot distinguish provenance for each parent relation | Recursive traversal; direct parent lookup is simple | Reject |
| Edge-list adjacency list | **Yes** | **Yes**: lifecycle belongs on the edge row | **Yes**: source, confidence, decision, and timestamps belong on the edge | Direct endpoints are indexed; transitive traversal is recursive | **Canonical representation** |
| DAG | **Yes** | Not inherent; supplied by versioned edge records | Not inherent; supplied by edge records | Recursive or closure-based | **Canonical logical shape** |
| Nested set (`lft`, `rgt`) | No natural multiple inheritance; it models nested containment | Awkward: moving a node changes boundaries for many rows | No natural edge identity | Fast subtree/ancestor range reads, expensive writes | Reject |
| Closure table | Yes, if the base relation is a DAG | Closure rows are derived, not a trustworthy place for proposals/reviews | Base edge table still required | Fast reachability after materialization; write/rebuild cost | Derived routing projection only |
| Labeled directed graph / RDF-style edge-labeled graph | Yes | Edge properties or reified edge records can version state | Reification/named graphs can add metadata | Native or recursive traversal | Suitable conceptual model |
| Property graph | Yes | Edge properties can carry state/version fields | Edge properties or qualified edge objects can carry provenance | Native graph traversal in a graph DB; recursive SQL in PostgreSQL | Semantically suitable, operationally unnecessary |
| Hypergraph | Supports n-ary relations, hence multiple participants | Edge/claim metadata can be versioned | Same as any asserted relation | More complex incidence traversal | Overkill for binary `SPECIALIZES` |
| Knowledge graph | Depends on the underlying graph | Application graph plus temporal/audit structures required | Identity, context, provenance, and quality layers are separate concerns | Depends on implementation | Useful goal, not a schema choice |
| SKOS/OWL specialization | Yes | Ontology versioning exists, but assertion review is application state | Annotations/PROV can help; acceptance still needs workflow | Reasoner or closure can expand | Optional semantic interchange/projection |

The source-backed distinctions are important:

- The SAS hierarchy study describes adjacency-list reads as iterative, path enumeration as a precomputed transitive closure, and nested sets as fast containment reads whose `left`/`right` values must be recalculated when nodes are inserted, moved, or deleted. Its DAG example requires a hybrid representation with nodes, edges, paths, labels, and edge types. — [Colby, *Integrating and Using Hierarchical Vocabularies in SAS*](http://support.sas.com/resources/papers/proceedings14/2064-2014.pdf)
- A 2024 empirical/model comparison calls closure tables suitable for multiparental structures because hierarchy links are separate from node records, while nested sets are described as suitable for uniparental DAGs. It also notes that closure tables require an extra path relation and maintenance on hierarchy changes. — [Novotný et al., *The relational modeling of hierarchical data in biodiversity databases*](https://pmc.ncbi.nlm.nih.gov/articles/PMC11466226/)
- Precomputed closure accelerates reachability, but transitive closure can become large for even modest relations; precomputation is justified by repeated queries, not by hierarchy semantics alone. — [Jagadish, *A compression technique to materialize transitive closure*](https://doi.org/10.1145/99935.99944)
- PostgreSQL recursive CTEs can traverse direct and indirect relationships, but cyclic data requires explicit cycle/path detection. That makes an edge table sufficient for a bounded, acyclic specialization graph without immediate closure materialization. — [PostgreSQL recursive-query documentation](https://www.postgresql.org/docs/current/queries-with.html)

#### Labeled graphs, hypergraphs, and ontologies

- RDF represents knowledge as subject-predicate-object triples; RDF datasets can contain named graphs, but graph names alone do not impose a review state or a temporal policy. — [W3C RDF 1.1 Concepts and Abstract Syntax](https://www.w3.org/TR/rdf11-concepts/)
- SKOS deliberately distinguishes direct `broader`/`narrower` links from inferred transitive closure. Applications that want query expansion infer `broaderTransitive`/`narrowerTransitive`; the direct asserted links remain distinct from the inferred closure. This supports storing asserted direct edges canonically and materializing closure separately. — [W3C SKOS Primer, §§4.5 and 2.3.1](https://www.w3.org/TR/skos-primer/); [W3C SKOS Reference](https://www.w3.org/TR/skos-reference/)
- OWL's `SubClassOf` semantics provide formal set containment and inference, but an OWL reasoner does not provide a `proposed/accepted/rejected` adjudication workflow. Those states must be represented outside the bare logical axiom unless the application explicitly models them.
- A hyperedge connects one or more vertices and is intended for genuinely multi-adic relationships. Multiple abstract parents do **not** by themselves require a hyperedge: two `SPECIALIZES(child, parent)` binary edges express the same semantic fact without conflating independent parent assertions into one indivisible relation. — [Ouvrard, *Hypergraphs: an introduction and review*](https://arxiv.org/html/2002.05014v2)
- A hypergraph becomes appropriate only if the application needs a joint assertion that is true only for a set of parents, such as “Goal G is a specialization **only under the combined context** of A and B.” That is a different proposition from two independently accepted `G SPECIALIZES A` and `G SPECIALIZES B` edges and should not be introduced speculatively.

#### Requirement support

| Requirement | Best supporting structure | Why |
|---|---|---|
| Multiple abstract parents | DAG / labeled edge table | One specific Goal can have many outgoing parent edges and many incoming child edges |
| Versioned edges | Append-only versioned edge relation | A row can be closed and a successor row appended; status is not encoded in a mutable node field |
| Acceptance/rejection | Application-level edge state machine | Tree/DAG/OWL semantics do not natively model human adjudication |
| Provenance | Edge/qualified-relation record plus source registry | The evidence and reviewer for one asserted relation must remain distinguishable from endpoint facts |
| Central Goal registry | Small central control-plane projection/registry keyed by stable Goal ID | Search, exact identity, placement, and hierarchy can be globally routed without centralizing all Procedure bodies |
| Distributed Procedures | Direct `Procedure --ACHIEVES--> Goal` references plus route-aware retrieval | Procedures can remain on their home shards; the registry resolves where each candidate lives |
| Fast hierarchical routing | Accepted-edge closure projection, added only when measured need exists | Direct-edge recursive CTE is authoritative; closure is a rebuildable cache |

### Inferences

- The target structure is a **versioned semantic specialization DAG stored as an adjacency/edge list**; the current `goal_relations` table is the unversioned edge-list starting point. “DAG,” “adjacency list,” and “labeled graph” are not competing choices: the first describes shape, the second physical representation, and the third semantics.
- A strict Goal `SPECIALIZES` relation should be acyclic among accepted edges. A self-link check is insufficient because a longer cycle can invalidate transitive routing. PostgreSQL's documented path/cycle technique can enforce the check when an edge is accepted.
- Accepted direct edges and inferred transitive relations should not share the same status semantics. A transitive path can disappear when any direct edge is rejected or superseded; therefore inferred closure must be rebuildable from the current accepted edge set.
- “Abstract” is a **role**, not a Goal subtype. A Goal can be abstract relative to one child and specific relative to another parent. An `is_abstract` boolean on `goals` would become inconsistent and should be avoided.
- A hypergraph would hide independently reviewable parent assertions and make rejection of one parent ambiguous. Binary edges preserve independent provenance and decisions.

### Gaps

- No workload evidence was found showing that transitive hierarchy queries are frequent or expensive enough to justify a closure table now. PostgreSQL recursive traversal should be the baseline.
- The product meaning of `SPECIALIZES` needs a precise rule: formal set containment, informal broader/narrower meaning, or a context-dependent narrower outcome. The recommended cycle and routing rules differ slightly among those interpretations.
- The repository schema does not specify conflict/disjointness rules (for example, whether a child may specialize mutually exclusive parents). These belong in an ontology/constraint layer, not in the initial binary edge table.

## Key Question 2 — How should the models map to this repository, and what is the minimum auditable schema?

### Takeaway

Keep canonical `goals` sharded, keep Procedures on their home shards with a direct `achieves_goal_id`, and centralize only the small control plane needed for global identity, search, placement, and accepted hierarchy. Reframe `goal_relations` as an append-only versioned edge fact; add a closure projection only after hierarchy starts influencing routing. The present table is structurally a good edge-list DAG but is not yet a complete versioned adjudication model.

### Cited Findings

#### Current repository mapping

- `goals` is intentionally lightweight and already carries stable identity, normalized name, description/outcome/verification data, lifecycle status, merge target, provenance/source path, aliases/tags, bitemporal fields, version, creator, visibility/owner, scope, and (after migration 95) home shard. — `backend/db/83_goals.sql:65-119`; `backend/db/95_control_plane_shards_projections.sql:53-56`
- Procedures have a stable `procedure_id` across versions, a versioned row `id`, bitemporal fields, verification/staleness/availability axes, evidence, provenance, and a direct `achieves_goal_id` reference/index. After cross-shard writes landed, that reference uses route-aware validation rather than a physical cross-database FK. This is the correct shape for a distributed reusable-method catalog. — `backend/db/18_procedures.sql:33-45`, `backend/db/18_procedures.sql:71-123`; `backend/db/83_goals.sql:192-196`; `backend/db/96_sharded_canonical_writes.sql:72-103`
- Migration 95 explicitly defines `goal_relations` as separate from Goals because it is optional, multi-parent, asynchronous, and must never be used for sharding or required by retrieval. It allows multiple parents through `(specific_goal_id, abstract_goal_id)` and supports `proposed/accepted/rejected`; it is not versioned or bitemporal. — `backend/db/95_control_plane_shards_projections.sql:83-102`
- The only observed production relation writer calls `propose_goal_relations()` and always inserts `status='proposed'`; the hierarchy is explicitly best-effort and non-blocking. — `backend/app/services/identity_resolution.py:851-870`
- Goal search states that it is deliberately a flat dedicated search over `goals`, “with no hierarchy/graph expansion.” Current `get_goal()` returns Procedures whose `achieves_goal_id` equals that exact Goal. — `backend/app/services/goals.py:516-583`
- Current runtime Procedure selection queries `procedures WHERE achieves_goal_id = $1`, applies hard applicability constraints, ranks feasible Procedures, and then resolves child Goals from the chosen Procedure's own steps. It does not infer Procedure achievement through `SPECIALIZES`. — `backend/app/execution/goal_resolution.py:118-148`; `backend/app/execution/goal_resolution.py:215-260`; `backend/app/execution/goal_resolution.py:367-415`
- The sharded control plane already has the right registry ingredients: global exact-name identity (`goal_names`), object-to-home-shard routing (`object_routes`), rebuildable lexical/vector Goal search (`goal_search_index`), rebuildable Procedure search carrying `goal_id` and `home_shard_id` (`procedure_search_index`), retrieval-decision audit, and an outbox. — `backend/db/95_control_plane_shards_projections.sql:17-63`; `backend/db/95_control_plane_shards_projections.sql:139-166`; `backend/db/95_control_plane_shards_projections.sql:168-246`; `backend/db/95_control_plane_shards_projections.sql:248-266`
- Cross-database physical FKs were deliberately removed. A route-aware trigger accepts a Goal reference only when the Goal is local or present in the central route table. This is the appropriate integrity mechanism for a central relation registry over sharded Goals. — `backend/db/96_sharded_canonical_writes.sql:18-64`; `backend/db/96_sharded_canonical_writes.sql:72-103`
- The existing `sources` table is the stable origin registry, with source type, locator, publisher, reliability, provenance, visibility/owner, scope, and bitemporal fields. Free-text relation provenance is therefore weaker than the repository's existing provenance model. — `backend/db/64_sources.sql:58-125`
- A system-versioned relational design keeps current rows plus previous versions with start/end validity periods, supporting audit and point-in-time reconstruction. This is evidence for the append-only pattern, although StealthLab additionally needs its own transaction/recorded time. — [Microsoft Learn, temporal tables](https://learn.microsoft.com/en-us/sql/relational-databases/tables/temporal-tables?view=sql-server-ver16)
- PROV-O distinguishes entities, activities, and agents and provides derivation, revision, generation/invalidation, attribution, and qualified-influence patterns. This supports recording who proposed/accepted a relation, what evidence informed it, and which edge version a route used. — [W3C PROV-O](https://www.w3.org/TR/prov-o/)

#### Current gaps in `goal_relations`

| Current field/behavior | Gap for the stated objective | Consequence |
|---|---|---|
| Composite PK `(specific_goal_id, abstract_goal_id, relation_type)` | Permanently admits only one row for that assertion | A rejected proposal cannot be reconsidered; accepted/rejected transitions cannot be preserved as versions |
| Mutable `status`, `confidence`, `updated_at` | No append-only edge version, stable relation identity, valid time, or recorded time | A route cannot prove which edge assertion/version it used |
| `decision_id UUID` with no declared FK | Identity decision can be dangling or ambiguous | Weak audit referential integrity |
| `provenance TEXT` | No stable source identity; multiple sources cannot be first-class | Edge evidence cannot be independently audited or reliability-scored |
| No `reviewed_by`, `reviewed_at`, or rationale | Acceptance/rejection has no accountable actor or reason | Human or automated adjudication is not reconstructable |
| No `tenant_id`, `scope_type`, `scope_entity_id`, `visibility`, or `owner_id` | A relation between private Goals has no independent access boundary | Potential cross-scope metadata leakage even when endpoints are filtered |
| Self-link check only | Does not prevent longer accepted cycles | Recursive routing and transitive closure can loop or become semantically invalid |
| `idx_goal_relations_abstract` only | No status-aware reverse/current index; PK supports all-status child lookup | Review queues and accepted-only ancestor/descendant queries scan irrelevant rows |
| No closure generation | No way to identify the exact hierarchy snapshot used by a route | Historical route replay can differ from current hierarchy |
| No observed writer for `accepted`/`rejected` | Allowed values are schema aspiration, not an implemented workflow | Production hierarchy remains entirely proposed and cannot drive routing |

#### Minimum edge-version schema

A repository-native shape can mirror the Procedure version pattern: a row `id` identifies one immutable version, while `relation_id` remains stable across versions.

| Field | Minimum meaning |
|---|---|
| `id UUID` | Immutable row/version identity; suitable target in route audit |
| `relation_id UUID` | Stable logical edge identity across proposal/review/reconsideration |
| `version INTEGER` | Monotonic version within `relation_id` |
| `specific_goal_id UUID` | Child/specific endpoint |
| `abstract_goal_id UUID` | Parent/abstract endpoint |
| `relation_type TEXT` | Initially closed to `SPECIALIZES` |
| `status TEXT` | `proposed`, `accepted`, or `rejected` for the current immutable version |
| `confidence NUMERIC NULL` | Model/evidence confidence for a proposal; retain as evidence after review but do not let it override an accepted decision |
| `identity_decision_id UUID` | Existing `identity_decisions.id` that proposed the semantic relation; enforce FK if lifecycle permits |
| `source_id UUID` or relation-source join | Existing `sources.id`; use a child relation when multiple sources are allowed |
| `proposed_by TEXT` | Agent/user/workflow that created the proposal |
| `decided_by TEXT` | Agent/user/policy that accepted or rejected it |
| `decided_at TIMESTAMPTZ` | Decision time |
| `decision_reason TEXT` | Human-readable but non-authoritative rationale; structured decision remains authoritative |
| `tenant_id UUID NULL` | Access boundary when non-global relations are admitted |
| `scope_type TEXT NULL`, `scope_entity_id TEXT NULL` | Context in which the edge is asserted |
| `visibility visibility_level`, `owner_id TEXT` | Required pair if private/owner-scoped relations are supported |
| `t_valid TIMESTAMPTZ` | When the assertion became accepted in domain/world time |
| `t_invalid TIMESTAMPTZ NULL` | When a later version superseded it |
| `t_created TIMESTAMPTZ` | When StealthLab recorded this version |
| `t_expired TIMESTAMPTZ NULL` | Optional source/claim expiration already used elsewhere in the repository |

If multiple evidence sources are normal, add a small child table rather than a JSON array:

`goal_relation_sources(relation_version_id, source_id, role, locator, confidence, t_created)` with a unique `(relation_version_id, source_id, role)`.

Required invariants:

1. `specific_goal_id <> abstract_goal_id`.
2. `UNIQUE (relation_id, version)`.
3. At most one live version per logical relation and one live direct assertion per `(specific_goal_id, abstract_goal_id, relation_type)`.
4. `accepted` or `rejected` requires `decided_by` and `decided_at`; a model-generated `identity_decision_id` is evidence, not a substitute for accountability.
5. Proposed edges may remain non-routable. Only the current accepted version enters hierarchy expansion.
6. A rejected edge is historical. New evidence creates a new version, not a mutation that erases rejection.
7. Before accepting an edge, a recursive path check rejects any path from the proposed parent back to the child. PostgreSQL documents the path/cycle method. — [PostgreSQL cycle detection](https://www.postgresql.org/docs/current/queries-with.html)
8. Goal endpoints use the existing route-aware validation mechanism because they may live on different shards. — `backend/db/96_sharded_canonical_writes.sql:18-64`
9. Scope and visibility compatibility must be checked before relation acceptance; an edge cannot silently bridge a private Goal into a public registry.
10. No normal hard deletion: close the version and append a replacement, consistent with the repository's invalidate-and-append discipline.

This is a minimum **logical** contract, not a proposed migration. In particular, it does not require changing the frozen specification merely to name `SPECIALIZES` differently.

#### Minimum indexes

PostgreSQL partial indexes contain only rows matching their predicate and are selected only when the planner can prove the query predicate implies the index predicate. That makes status/live partial indexes appropriate for small proposal queues and accepted-only routing, provided queries use matching predicates. — [PostgreSQL partial-index documentation](https://www.postgresql.org/docs/current/indexes-partial.html)

**Canonical relation rows**

1. `UNIQUE (specific_goal_id, abstract_goal_id, relation_type) WHERE t_invalid IS NULL` — one current direct assertion; safe under concurrent proposal.
2. `(specific_goal_id, abstract_goal_id) WHERE status='accepted' AND t_invalid IS NULL` — accepted ancestor traversal.
3. `(abstract_goal_id, specific_goal_id) WHERE status='accepted' AND t_invalid IS NULL` — accepted descendant expansion.
4. `(decided_at, id) WHERE status='proposed' AND t_invalid IS NULL` — oldest-first review queue.
5. `(relation_id, version DESC)` — complete edge history and point-in-time inspection.
6. `(identity_decision_id)` and relation-source `(source_id)` indexes for audit joins.
7. `(tenant_id, scope_type, scope_entity_id, status)` if scoped/private relations are allowed.

**Goal registry and search**

- Preserve the current exact identity indexes: `goal_names(scope_key, normalized_name)` and the per-shard global/local normalized-name uniqueness rules. — `backend/db/95_control_plane_shards_projections.sql:127-137`; `backend/db/83_goals.sql:153-176`
- Preserve `object_routes(object_type, object_id)` plus `(home_shard_id, object_type)` for O(1) placement lookup. — `backend/db/95_control_plane_shards_projections.sql:43-51`
- Preserve the central Goal projection's GIN full-text index, HNSW vector index, and `(scope_type, scope_entity_id, status)` index. — `backend/db/95_control_plane_shards_projections.sql:139-166`
- A “central abstract Goal registry” can initially be a view/projection over Goals that appear as `abstract_goal_id` in current accepted edges. Do not maintain a second canonical `is_abstract` flag. If materialized for latency, carry `goal_id`, `home_shard_id`, `canonical_name`, lifecycle status, and `hierarchy_generation`.

**Optional closure projection**

`goal_ancestor_closure(descendant_goal_id, abstract_goal_id, min_depth, max_depth, hierarchy_generation)` should contain only paths induced by current accepted direct edges. Recommended indexes:

1. `UNIQUE (hierarchy_generation, descendant_goal_id, abstract_goal_id)`.
2. `(descendant_goal_id, abstract_goal_id) INCLUDE (min_depth, max_depth)`.
3. `(abstract_goal_id, descendant_goal_id) INCLUDE (min_depth, max_depth)`.

The generation or equivalent digest must be recorded in each route decision. Rejection/supersession of a direct edge increments the generation and rebuilds or incrementally updates affected closure rows.

**Procedure routing**

- The existing `procedures(achieves_goal_id)` partial index is the direct authoritative lookup. — `backend/db/83_goals.sql:192-196`
- For the runtime's current ordering, a composite partial index such as `(achieves_goal_id, verification_state, t_created DESC) WHERE t_invalid IS NULL` can support exact-Goal candidate retrieval, but its final columns should match the actual candidate predicate and `EXPLAIN` plan rather than being added speculatively. — `backend/app/execution/goal_resolution.py:133-140`
- The central `procedure_search_index(goal_id)` index already supports global candidate projection; add status/home-shard columns to the key only if measured plan shapes require it. — `backend/db/95_control_plane_shards_projections.sql:168-199`

**Routing audit**

`retrieval_decisions` currently records selected Goal and Procedure IDs plus a JSON `detail` field. For hierarchy-based routing, the minimum audit addition is a typed `goal_relation_version_ids UUID[]` plus nullable `hierarchy_generation`; if schema stability is preferred, the IDs and generation can initially live in `detail`, but they should never be omitted. — `backend/db/95_control_plane_shards_projections.sql:229-246`

#### Routing path using the control plane

1. Resolve fuzzy intent against the central `goal_search_index` (FTS + ANN + RRF), respecting Goal status, scope, and visibility.
2. Hydrate the selected canonical Goal through `object_routes` and its home shard.
3. Generate hierarchy candidates using only current accepted `SPECIALIZES` versions:
   - direct edges initially;
   - recursive CTE for bounded ancestor/descendant expansion;
   - closure projection later if measurements justify it.
4. Retrieve Procedures whose direct `achieves_goal_id` equals each candidate Goal through the central Procedure projection, then hydrate the selected version rows from their home shards.
5. Apply the existing non-compensatory hard applicability gate before any Procedure is considered feasible. — `backend/app/execution/goal_resolution.py:118-148`
6. Rank feasible Procedures using the existing evidence/applicability machinery.
7. Record candidate Goals, relation-version IDs, hierarchy generation, Procedure row/version IDs, applicability outcomes, selected Procedure, and fallback order in `retrieval_decisions`.
8. Compile any selected Procedure's own steps into a run-specific execution graph; do not use the semantic DAG as the scheduler.

This follows the small-reference-data/control-plane pattern used by distributed relational systems: Citus documents reference tables as small data replicated to workers for frequent joins, and separately notes that placement/join behavior depends on co-location. The analogy supports a small central registry; it does not imply that StealthLab must adopt Citus. — [Citus DDL and reference tables](https://docs.citusdata.com/en/stable/develop/reference_ddl.html); [Citus data modeling and co-location](https://docs.citusdata.com/en/stable/sharding/data_modeling.html)

### Inferences

- The repository already has the major primitives for a central registry: stable Goal IDs, global exact identity, search projections, shard routes, relation storage, and route-aware validation. A new full canonical Goal table would duplicate truth and contradict migration 95's “control plane, not a second canonical store” design. — `backend/db/95_control_plane_shards_projections.sql:1-14`
- “Central abstract Goal registry” should mean a central **registry/projection of abstract-role Goals**, not centralized ownership of every canonical Goal row and not a Procedures table.
- Versioning should follow the stable-ID-plus-row-version pattern already used by Procedures and Claims, not mutable `updated_at` alone.
- A closure table should be an optimization of accepted-edge reachability, not the place where semantic truth is written. This preserves the ability to explain which direct relation versions caused a route.
- The current accepted/rejected CHECK constraints should be paired with an implemented state-transition writer in the same change; otherwise they remain unexercised vocabulary in the observed application path.

### Gaps

- The intended reviewer authority and policy for `accepted`/`rejected` are not documented: named human only, automated evidence policy, or both.
- The correct temporal meaning of a rejected proposal is not settled. A proposal may be “false now” in epistemic review while the underlying semantic question remains open; the row status, decision event, and future reconsideration need separate semantics.
- The repository has no measured hierarchy fan-out, depth, or route-query latency. Consequently, the exact closure-table schema and whether closure is needed remain unproven.
- The search for an acceptance writer found none. This may be intentional, deferred, or hidden behind raw SQL; it should be confirmed before designing an API around accepted edges.

## Key Question 3 — What should this repository adopt, and how should semantic hierarchy be kept separate from execution DAGs?

### Takeaway

Adopt the existing `goals + goal_relations(SPECIALIZES) + procedures.achieves_goal_id` direction, but make `goal_relations` an immutable, versioned, access-controlled adjudication record in the central control plane. Use accepted-edge reachability only to generate semantic Goal/Procedure candidates. Keep Procedure decomposition, concrete run nodes, dependencies, ordering, leases, and verification in the existing execution structures; a `SPECIALIZES` edge must never be interpreted as “decompose into,” “must run after,” or “placed on the same shard.”

### Cited Findings

#### Recommended architecture

1. **Goal nodes remain canonical and shardable.** A Goal is a stable desired-outcome identity, not a folder and not a task instance.
2. **Accepted semantic hierarchy is a labeled DAG.** Store direct, versioned `SPECIALIZES` edges in the central control plane. A Goal may be both abstract and specific in different edges.
3. **No `is_abstract` field.** Abstractness is derived from appearing as an endpoint of accepted direct edges. This is what permits multiple parents and mixed abstract/specific roles.
4. **Procedures retain direct achievement claims.** A Procedure is linked to the most specific Goal it actually claims to achieve. Do not copy that link to every ancestor or descendant.
5. **Hierarchy is candidate generation, not entitlement.** Accepted specialization may add candidate Goals; the existing applicability and verification gates decide whether a Procedure is usable.
6. **The control plane centralizes identity, search, routes, hierarchy, and decisions; canonical Procedure bodies stay distributed.**
7. **Closure is optional and derived.** Start with accepted-edge recursive CTEs. Add a generation-stamped closure only when query evidence warrants it.

#### Semantic entailment is asymmetric

Under formal specialization, a specific Goal's successful outcome entails the broader abstract Goal, but the reverse is not guaranteed. SKOS defines a broader concept as one whose scope contains the narrower concept's scope; OWL defines subclass extension inclusion. — [W3C SKOS Primer](https://www.w3.org/TR/skos-primer/); [W3C OWL 2 Direct Semantics](https://www.w3.org/TR/owl2-semantics/)

Therefore:

- A Procedure proven to achieve a child Goal is a logically valid—but potentially narrow or over-constrained—candidate for an ancestor Goal, because satisfying the child entails satisfying the parent. It should not be copied into a canonical direct `ACHIEVES` link for every ancestor.
- A generic Procedure linked to an abstract Goal is not automatically a valid solution for a child; it is a fallback candidate only if it also satisfies the child's specific constraints and verification requirements.
- A child Procedure may be considered for an abstract request only after context establishes that the child's extra specificity is acceptable. Pooling every descendant without context could arbitrarily overconstrain the route.
- Acceptance of `child SPECIALIZES parent` changes semantic navigation and candidate generation. It does not change `verification_state`, `availability`, or evidence of any Procedure.

This is the practical reason not to materialize inherited “Procedure achieves ancestor” edges: those links would be derived, specificity-sensitive, and vulnerable to becoming stale.

#### Semantic hierarchy versus execution decomposition

HTN planning literature makes a useful distinction: compound/abstract tasks are decomposed through methods into task networks until primitive tasks remain. The task network and its ordering constraints are part of planning semantics. — [Erol, Hendler, and Nau, *Semantics for Hierarchical Task-Network Planning*](http://hdl.handle.net/1903/624)

That is analogous to Procedure-step decomposition, but it is **not** analogous to `SPECIALIZES`:

| Concern | Semantic Goal hierarchy | Procedure decomposition | Run execution DAG |
|---|---|---|---|
| Core question | What outcomes are narrower/broader? | How does this chosen reusable Procedure expand into subgoals/actions? | In what run-specific order can concrete nodes execute? |
| Cardinality | Many parents and children | Ordered steps/subtasks selected by a chosen Procedure | Concrete run nodes and dependencies |
| Stability | Shared across runs and contexts | Reusable template, but selection is contextual | Run-specific and mutable until terminal |
| Edge meaning | `SPECIALIZES` | Procedure step/subgoal | Requires/precedes/data-flow/resource conflict |
| Routing role | Candidate discovery and semantic explanation | Expands the selected route | Scheduling, retries, leases, verification |
| Correct isolation | Never implies order or action | Never rewrites semantic parentage | Never changes ontology or Goal identity |

Repository evidence already follows this separation:

- Procedure `steps` are planner-neutral and must be translated into a concrete DAG at instantiation; the Procedure row does not carry scheduling dependencies. — `backend/db/18_procedures.sql:41-45`
- Runtime resolution recursively resolves a chosen Procedure's step Goals, detects recursion-path cycles, and enforces a depth limit. — `backend/app/execution/goal_resolution.py:215-260`; `backend/app/execution/goal_resolution.py:306-338`
- `goal_tree_to_knowledge()` explicitly says its Procedure list is a reading order, not an execution order, and the planner decides order. — `backend/app/execution/goal_knowledge.py:63-68`
- Durable execution is already separately persisted as `execution_runs` and `execution_run_nodes`, with statuses, attempts, leases, result/error references, and terminal-state fences. — `backend/db/36_durable_execution_runs.sql:1-29`; `backend/db/36_durable_execution_runs.sql:31-138`
- Procedure dependency references have their own table and resolution status, separate from semantic Goal relations. — `backend/db/58_procedure_implementations_and_dependencies.sql:108-129`

#### Guardrails against conflation

- Name the semantic relation `SPECIALIZES`; do not overload `parent_id`, `depends_on`, `child`, or execution node ordering.
- Do not use hierarchy membership to assign `home_shard_id`. Migration 95 explicitly says hierarchy is never used for sharding. — `backend/db/95_control_plane_shards_projections.sql:83-86`
- Do not use an unaccepted proposal in search, routing, closure, or execution.
- Do not treat a rejected proposal as a permanent assertion that the opposite relation is true. It records that this proposal was rejected.
- Do not infer a DAG dependency from reachability: “A is reachable from B” means semantic specialization, not “run A after B.”
- Do not put run state on the shared semantic edge. Route decisions reference immutable edge versions; run state remains on execution rows.
- Do not make a child Goal an execution child merely because it specializes a parent. A Procedure's ordered steps establish decomposition.
- Keep a hierarchy generation or edge-version list on every route decision so replay explains why a descendant or ancestor Procedure became a candidate.
- Keep canonical source evidence separate from edge confidence. `sources.reliability_score` and an edge's model confidence answer different questions, matching the repository's existing `sources` design. — `backend/db/64_sources.sql:67-72`

#### Suggested adoption sequence

1. **Freeze the meaning first.** Define `SPECIALIZES`, the acceptance authority, cycle policy, scope policy, and whether a rejected relation may be reconsidered.
2. **Version the direct edge.** Introduce the stable relation/version contract and source/decision references while continuing to write only `proposed` rows.
3. **Implement adjudication atomically.** Add the review/state-transition path, audit fields, authorization, and tests in the same change. Do not expose accepted hierarchy before the writer exists.
4. **Keep routing exact initially.** Continue to retrieve Procedures only by direct `achieves_goal_id`; do not change behavior merely because the status vocabulary exists.
5. **Add bounded accepted-edge expansion.** Use recursive CTEs with status, validity, scope, path, and cycle filters; log relation-version IDs in retrieval decisions.
6. **Add closure only on evidence.** Trigger it on measured hierarchy query cost or routing latency, not on fashion. Rebuild from accepted versions and expose a generation.
7. **Add ontology exports only if needed.** OWL/SKOS/RDF can be generated interchange views for interoperability or reasoning. They should not replace the operational edge/audit model.
8. **Consider hyperedges only for real joint assertions.** A jointly contextualized specialization needs a named n-ary proposition and its own acceptance semantics; it should not be simulated by ambiguous overlapping binary edges.

### Inferences

- The current product direction—lightweight Goals, evidence-backed Procedures, hard applicability, and explicit execution state—is compatible with a small semantic DAG; a full ontology reasoner or graph database is unnecessary.
- The best definition of the central abstract Goal registry is: **the current accepted-edge view over globally searchable Goals, with stable identity and shard routes, while detailed canonical Goal and Procedure records remain distributed**.
- The strongest audit unit is not just `(child, parent)` but `(relation_id, version, decision_id, source_ids, hierarchy_generation)`. A route that records only Goal and Procedure IDs cannot later prove which semantic claim produced the candidate.
- The recommended architecture keeps semantic hierarchy stable across procedures and runs while allowing Procedure selection and execution topology to vary by context, evidence, cost, and failure.

### Gaps

- The repository does not state whether specialization is a universal set-containment claim or a retrieval heuristic. This must be settled before transitive routing is enabled.
- It is not yet specified whether an accepted edge can cross tenant/scope boundaries, or whether cross-scope edges are forbidden and represented only as local mappings. The default should be no implicit cross-boundary inheritance.
- No evidence shows a need for a formal OWL reasoner, RDF export, property-graph database, or hypergraph today. They remain optional interoperability/extension layers, not the recommended operational substrate.
- Acceptance/rejection product UX, delegated authority, and the audit retention policy for rejected proposals remain unspecified.

Overall recommendation: **keep a versioned, accepted-only specialization DAG as semantic metadata; use the existing sharded Goal registry/search/routes and distributed Procedures; add closure only as a measured optimization; and compile execution DAGs only from the selected Procedure and run context.**
