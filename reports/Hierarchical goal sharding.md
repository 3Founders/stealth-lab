# Centralize Goal Semantics, Shard Procedure Bodies

Use a two-plane design: keep the control database as the sole canonical authority for Goals, identity, semantic hierarchy, placement, routes, and audit, while giving each public Procedure one canonical home among 100 independently provisioned PostgreSQL data stores. Allocate the stable `procedure_id` first, map it to a stable logical bucket, and select the bucket’s current physical owner with weighted HRW; do not use a Goal’s identity as the default shard key because a hot or multiply-applicable Goal would otherwise become a placement hotspot. Store direct Procedure-to-Goal claims on the Procedure shard, derive Goal-to-Procedure postings and Goal-to-shard routes in the control plane, and keep semantic `SPECIALIZES` edges separate from execution DAGs and from physical placement. Retain the current central full Procedure search projection as the initial low-fanout read path, but build the compact posting and per-shard search contract alongside it so a measured capacity limit can trigger a mode change without redesigning identity or routing. This is a proposed target, not the current checkout: Goals are currently distributed by HRW and Procedures prefer their Goal’s shard. The design is deterministic by default, permits only versioned capacity or locality overrides, and fails closed rather than guessing when no eligible shard or authoritative Goal validation is available.

## The target separates semantic authority from physical ownership

The repository already has a useful control-plane substrate, but its present topology is materially different from the requested one. The control database is also the built-in `K000` knowledge shard; it owns the shard registry, object routes, rebuildable search projections, optional hierarchy, identity decisions, retrieval decisions, and a projection outbox, while canonical Goals, Procedures, and Claims are written to the registered knowledge shards ([sharding.md](../docs/sharding.md#L6-L13)). A new public Goal is selected with weighted rendezvous hashing over the active shard set, and its stored `home_shard_id` is preferred for a new Goal-local Procedure; if that shard is full, readonly, or unhealthy, only the new object rolls over ([sharding.md](../docs/sharding.md#L15-L25); [shards.py](../backend/app/services/shards.py#L142-L171)). The actual write path follows that rule: `find_or_create_goal()` allocates a Goal ID and chooses its home, while `capture_procedure()` resolves the Goal, allocates a stable `procedure_id`, chooses the child shard, records remote routes, writes the canonical row, and projects it ([goals.py](../backend/app/services/goals.py#L413-L495); [procedures.py](../backend/app/services/procedures.py#L251-L320); [procedures.py](../backend/app/services/procedures.py#L374-L477)). **This is the current implementation, not the target topology.**

| Current repository fact | Exact evidence | Target implication |
|---|---|---|
| `K000` is both the control database and a canonical knowledge home | [95_control_plane_shards_projections.sql](../backend/db/95_control_plane_shards_projections.sql#L16-L38) | The target needs distinct `control`, `procedure_data`, and `private_home` roles even if K000 remains physically one PostgreSQL instance. |
| Public Goals use weighted HRW; Procedures prefer the Goal home and roll over independently | [docs/sharding.md](../docs/sharding.md#L17-L25) | A centralized Goal cannot be the Procedure placement key. Procedure homes must be assigned from the data-plane registry. |
| Canonical rows are physically on knowledge shards; control tables are not a second canonical store | [95_control_plane_shards_projections.sql](../backend/db/95_control_plane_shards_projections.sql#L1-L11) | Centralizing Goals changes the canonical owner deliberately; it must not create a duplicate mutable Goal row. |
| `goal_relations` is an optional, multi-parent, asynchronous edge list; it is not used for sharding or retrieval | [95_control_plane_shards_projections.sql](../backend/db/95_control_plane_shards_projections.sql#L83-L102) | Semantic hierarchy can guide candidate generation, but it cannot silently move a Procedure. |
| The observed relation writer records only `proposed` edges and treats hierarchy failure as non-blocking | [identity_resolution.py](../backend/app/services/identity_resolution.py#L851-L871) | Accepted hierarchy is not an authoritative routing input until an adjudication writer and revision contract exist. |
| The canonical retrieval path searches central Goal and Procedure projections, then hydrates only involved shards | [retrieval_service.py](../backend/app/services/retrieval_service.py#L287-L329); [retrieval_service.py](../backend/app/services/retrieval_service.py#L380-L423) | The target should preserve candidate-first retrieval and replace only the candidate source when a compact route mode is enabled. |
| Remote projection publication is repairable, not an atomic cross-database transaction | [search_projection.py](../backend/app/services/search_projection.py#L9-L19); [procedures.py](../backend/app/services/procedures.py#L467-L477) | The target needs a shard-local transactional outbox or CDC so canonical data and projection intent commit together. |
| Private and organizational rows stay on K000 | [shards.py](../backend/app/services/shards.py#L39-L55); [sharding.md](../docs/sharding.md#L72-L75) | The 100 public Procedure shards must not become a bypass for private or tenant-isolated data. |

The target recommendation assumes that “100 shards” means independently provisioned PostgreSQL clusters or databases with independent capacity, backup, network, and recovery boundaries, not 100 schemas or 100 local partitions in one database. PostgreSQL documents that a connection is scoped to one database and that databases in one cluster still share server resources and WAL; therefore schemas and partitions cannot provide the intended failure isolation ([PostgreSQL database overview](https://www.postgresql.org/docs/current/manage-ag-overview.html); [PostgreSQL partitioning](https://www.postgresql.org/docs/current/ddl-partitioning.html)). The default is one authoritative home per logical Procedure, with replicas selected separately if the product later requires them. The default visibility policy remains public data on the 100 data shards and private, organizational, personal-sync, and other protected material on the authorized K000/private path. All Goals, including concrete Goals, are centralized in K000 initially; abstractness is a semantic role, not a separate storage class.

That choice resolves the most important conflict among the research notes. A central directory over distributed Goal rows is sufficient for routing, but it leaves remote concrete Goal endpoints, control-plane association foreign keys, and remote Procedure references unnecessarily complicated. Centralizing the whole `goals` table in K000 gives the control plane one canonical identity, name, version, merge, hierarchy, and scope authority while still allowing a small read-only Goal anchor projection on each data shard. If the founder later requires concrete Goals to be distributed, that should be a separate policy and route change, not an accidental consequence of hash placement. The existing Goal schema is already a stable-ID, bitemporal, versioned object with scope, visibility, and merge fields ([83_goals.sql](../backend/db/83_goals.sql#L65-L119)); centralizing it does not require a second abstract-Goal table or an `is_abstract` boolean.

The semantic hierarchy should be a versioned labeled DAG stored as a direct edge list, with `specific_goal_id SPECIALIZES abstract_goal_id` recorded on immutable relation versions. A tree with a scalar `parent_id` cannot represent multiple accepted abstract parents, and a nested-set representation would force boundary rewrites and still would not provide edge-level provenance. PostgreSQL recursive CTEs can traverse a bounded acyclic graph, including cycle detection, so direct edges are sufficient initially ([PostgreSQL recursive queries](https://www.postgresql.org/docs/current/queries-with.html)). A closure table is useful only after query evidence shows that repeated reachability traversal is expensive; it is a rebuildable projection stamped with a hierarchy generation, never the place where a proposal or review decision is written. This follows the distinction SKOS makes between asserted direct `broader`/`narrower` links and inferred transitive properties ([SKOS Reference](https://www.w3.org/TR/skos-reference/); [SKOS Primer](https://www.w3.org/TR/skos-primer/)). It also follows the W3C provenance distinction between a versioned assertion and the activity, agent, source, and decision that produced it ([PROV-O](https://www.w3.org/TR/prov-o/)).

`SPECIALIZES` is semantic entailment, not decomposition or scheduling. A child Goal’s Procedure can become a candidate for an abstract request, but a Procedure directly linked to an abstract Goal is not automatically valid for a more specific child request; the child’s preconditions and verification requirements still have to pass. The existing Procedure `steps` field is explicitly planner-neutral, and runtime resolution expands a selected Procedure’s own steps into a resolution tree; the returned procedure list is a reading order, not an execution order ([18_procedures.sql](../backend/db/18_procedures.sql#L33-L45); [goal_resolution.py](../backend/app/execution/goal_resolution.py#L215-L260); [goal_knowledge.py](../backend/app/execution/goal_knowledge.py#L63-L68)). Durable run state has its own `execution_runs` and `execution_run_nodes` tables with leases, attempts, errors, and terminal-state fences ([36_durable_execution_runs.sql](../backend/db/36_durable_execution_runs.sql#L31-L138)). The separate approximate `OWNS/PARENT_OF` clustering tree in `hierarchy.py` is not the semantic `SPECIALIZES` graph and must not be used to infer execution order ([hierarchy.py](../backend/app/services/hierarchy.py#L1-L33)). **Design judgment:** the target should use accepted semantic edges for candidate expansion, Procedure steps for decomposition, and execution tables for run scheduling—three separate mechanisms.

## Bucketized HRW is the placement contract

The design problem has two independent layers. The first is representing semantic relationships: a multi-parent DAG, versioned edges, and optional closure. The second is assigning a Procedure family to a physical owner: a deterministic mapping from a stable logical bucket to one eligible shard. A directory is necessary for auditability and rebalancing, but it should not become a per-Goal placement oracle. The recommended combination is **bucketized weighted HRW with a versioned bucket-owner catalog, plus optional capacity and locality policy**. This is a design judgment grounded in the cited mechanisms, not a claim that a paper prescribes this exact schema.

| Structure or algorithm | What it solves | Strength for this workload | Failure or limitation | Decision |
|---|---|---|---|---|
| Scalar-parent tree | One parent and fast direct navigation | Very simple writes and reads | Cannot represent multiple abstract parents; parent changes have poor edge-level audit semantics | Reject for semantic hierarchy |
| Versioned edge-list DAG | Multiple parents, edge lifecycle, source and reviewer per assertion | Fits `SPECIALIZES`, scope checks, bitemporal audit, and future acceptance workflow | Reachability requires traversal or a projection | **Canonical hierarchy representation** |
| Closure table | Fast ancestor/descendant reachability | Removes repeated recursive work after acceptance | Can be large; must be invalidated when an edge is rejected or superseded; cannot hold unaccepted decisions | Derived optimization only |
| Directory or lookup table | Explicit `bucket -> shard` and policy state | Makes placement, overrides, forwarding, and rebalancing auditable | Central metadata becomes a dependency if reads cannot use a cached snapshot | **Use as a bucket catalog and audit record** |
| HRW / rendezvous hashing | Deterministic `key -> server` selection | Stateless computation, stable backups, and deterministic results; the original HRW paper explicitly describes this property ([HRW paper](https://www.microsoft.com/en-us/research/wp-content/uploads/2017/02/HRW98.pdf)) | Raw membership changes can remap keys; capacity is only reflected through candidate weights or filters | **Default base placement** |
| Weighted HRW | Capacity-weighted deterministic selection | A larger capacity weight wins a proportional share without changing Procedure identity | The weighted-HRW draft is an engineering proposal, not a normative standard; the scoring convention must be versioned ([weighted-HRW draft](https://datatracker.ietf.org/doc/html/draft-ietf-bess-weighted-hrw-00)) | **Use with a frozen formula and epoch** |
| Consistent-hash ring | Successor lookup and virtual-node ownership | Mature alternative when the platform already operates ring metadata; consistent hashing minimizes disruption relative to naïve modulo mapping ([Karger et al.](https://people.csail.mit.edu/karger/Papers/web.pdf); [Cassandra architecture](https://cassandra.apache.org/doc/stable/cassandra/architecture/dynamo.html)) | Adds token metadata, vnode repair, gossip/failure detection, and hot-token management | Alternative, not required for 100 PostgreSQL shards |
| Range or prefix partitioning | Ordered, auditable locality | Useful when reads are naturally ordered by Goal family or region; PostgreSQL ranges are explicit and non-overlapping ([PostgreSQL partitioning](https://www.postgresql.org/docs/current/ddl-partitioning.html)) | Hot prefixes, awkward multi-Goal ownership, and range migration state; raw `goal_id/` prefixes create hot ranges | Optional coarse policy, not the default |
| Graph partitioning | Minimize cross-partition communication under a measured workload | Can reduce transaction cuts and balance partitions when co-access traces are known; the formulation is NP-hard but approximations are practical ([Golab et al.](https://ar5iv.labs.arxiv.org/html/1312.0285); [Schism](https://15799.courses.cs.cmu.edu/fall2013/static/papers/schism-vldb2010.pdf)) | Requires a workload graph, periodic recomputation, stable labels, and a deterministic fallback | Periodic co-planner or migration generator |
| Locality-aware assignment | Minimize latency, geography, load, or legal-domain cost | Can use hard capability filters and soft affinity; Akkio shows a production locality manager assigning related micro-shards to shards ([Akkio](https://www.usenix.org/system/files/osdi18-annamalai.pdf)) | Live scores can oscillate, create migration churn, and become unsafe during a control-plane outage | Policy wrapper around the stable base mapping |
| Central full search projection | One global FTS/ANN candidate query | Preserves the current two-stage path and avoids 100-way search fanout; pgvector supports HNSW and iterative scans but filtering is applied after approximate-index scanning ([pgvector indexing](https://github.com/pgvector/pgvector#indexing); [pgvector filtering](https://github.com/pgvector/pgvector#filtering)) | Duplicates vectors/text, creates a central capacity and freshness bottleneck | **Launch mode while measured budgets hold** |
| Compact route postings plus per-shard search | Candidate-shard reduction followed by local ANN/FTS | Central control state stays small and shard indexes remain authoritative for Procedure bodies | Requires global merge, route-quality measurement, and a safe overflow path when postings are stale | **Scale escape hatch and target-compatible second mode** |

The comparison also clarifies why a central directory and a hash are complements. HRW chooses a default owner without asking a directory for every new Procedure, while the materialized bucket catalog makes the choice reproducible, auditable, and overridable. A ring is equivalent in spirit if the organization already has ring membership and vnode operations, but it is unnecessary complexity for a catalog of roughly 1,024 logical buckets. Graph partitioning and locality-aware assignment should influence bucket ownership during a planned epoch, not make an unversioned live decision for every insert.

### Exact placement algorithm

Let `P` be the stable `procedure_id`, `E` the active placement epoch, and `B` the fixed logical bucket count. **Design judgment:** start with `B=1024` for 100 physical shards, then choose the final count from Procedure volume, target bucket size, and migration-window measurements. The bucket must not depend on the physical shard count or on mutable Goal semantics.

```text
place_new_procedure(request, procedure_id, goal_links, placement_epoch):
    validate provenance, scope, visibility, and tenant constraints
    allocate or reuse the stable procedure_id and version-row id

    if visibility is private or org:
        eligible = [the authorized K000/private home]
        return that home with placement_class = private_home

    bucket_id = int(SHA256(
        "stealthlab:procedure-placement:v1\0" + procedure_id
    )) modulo placement_epoch.bucket_count

    if an immutable override exists for (placement_epoch, bucket_id):
        owner = override.home_shard_id
        record reason, actor, and decision hash
    else:
        candidates = [
            shard for shard in placement_epoch.shards
            if shard.role = "procedure_data"
            and shard.status = "active"
            and shard.weight > 0
            and shard.current_epoch = placement_epoch.epoch_id
            and shard.capacity_reserve is not exhausted
            and shard.zone satisfies every hard legal/tenant constraint
        ]

        if candidates is empty:
            persist a retryable NO_WRITABLE_DATA_SHARD intent
            return fail_closed(NO_WRITABLE_DATA_SHARD)

        owner = argmin over candidates of
            (-ln(uniform_open_01(SHA256(
                "stealthlab:hrw:v1\0" + placement_epoch.catalog_hash
                + "\0" + bucket_id + "\0" + shard.shard_id
            ))) / shard.weight, shard.shard_id)

    persist procedure_id, bucket_id, placement_epoch, owner,
    procedure_row_id, goal_versions, decision_hash, and placement_reason
    reserve the route as pending, publish the Goal anchors, then commit
    the Procedure transaction on owner
```

The formula is intentionally compatible in spirit with the current code, which hashes a key/shard pair and minimizes `-ln(u) / weight` with `shard_id` as the deterministic tie-break ([shards.py](../backend/app/services/shards.py#L142-L160)). The difference is that the hashed key is a **logical bucket**, not a mutable shard membership directly. The epoch’s registry snapshot and catalog hash are persisted with the decision, so a retry with the same intent cannot silently choose a different home after weights or health change. If a new epoch is activated, an existing Procedure remains on its recorded home until an explicit bucket migration moves it.

A Goal may have many Procedures, but it is not a physical parent. All Procedures for a hot Goal still receive independent UUIDv7 keys and therefore spread across the data plane. A multiply-applicable Procedure has one primary direct Goal for its claim/explanation and additional canonical direct links in `procedure_goal_links`; the home is computed once from `procedure_id`, not from the union of Goal IDs. A Goal-family locality class may be introduced later as a hard zone or soft affinity, but a raw `goal_id` hash must not be the default. If two applicable Goals impose conflicting hard placement zones, the operation fails with an explicit `ambiguous_placement_class` rather than picking a winner.

Capacity and locality are deliberately separated from semantic identity. Capacity is a hard eligibility filter plus a stable `weight`; a shard that is over its hard reserve is excluded. Geographic, legal, tenant, or hardware constraints are also hard filters. A soft locality score, if product evidence later justifies one, is computed from a frozen epoch snapshot and cannot trade away a hard constraint or change user relevance. An explicit bucket override is the only way to place a bucket outside the default HRW result, and it must carry an expiry or an epoch. This is the distinction between deterministic placement and a controlled capacity/locality override; dynamic least-loaded assignment should not be introduced on the online write path without workload evidence.

If no eligible public shard exists, the write returns a retryable service-unavailable result tied to the idempotency key. The canonical Procedure is not inserted into a full or readonly shard, and it is not silently routed to the control database. Private and organizational writes remain on K000, matching the current rule that protected rows do not leave the home shard ([shards.py](../backend/app/services/shards.py#L39-L55)). Replicas, if introduced, are selected from distinct subsequent HRW scores or a ring preference list; ownership and durability are separate decisions.

## Cross-database writes are workflows, not transactions

The target schema should make the authority boundary visible in table names and fields. The existing `knowledge_shards`, `object_routes`, `procedure_row_routes`, `goal_search_index`, `procedure_search_index`, and `projection_outbox` are useful foundations, but the target adds explicit placement epochs, logical buckets, anchor generations, route state, canonical link rows, and shard-local delivery records. The following is a logical design, not a proposed migration to apply now.

### Control database

| Table | Canonical fields and purpose | Required indexes |
|---|---|---|
| `knowledge_shards` | Extend the current registry with `role` (`control`, `procedure_data`, `private_home`, `retired`), `status`, `placement_weight`, `capacity_rows`, `capacity_bytes`, `hard_reserve`, `zone`, `failure_domain`, `epoch_id`, `dsn_env`, and `last_health_at`. The registry stores environment-variable names, never DSNs, as it does today ([95_control_plane_shards_projections.sql](../backend/db/95_control_plane_shards_projections.sql#L16-L34)). | `(role, status, placement_weight)`, `(zone, role)`, `(status, last_health_at)` |
| `placement_epochs` | `epoch_id`, `bucket_count`, `hash_version`, `catalog_version`, `registry_snapshot_hash`, `state` (`draft`, `active`, `retired`), `created_by`, `created_at`, `activated_at`, and `retired_at`. Only one epoch is active for new placement. | Unique partial index on `state = 'active'`; `(created_at DESC)` |
| `placement_bucket_owners` | `(epoch_id, bucket_id)` primary key; `home_shard_id`, `previous_shard_id`, `state` (`stable`, `copying`, `draining`, `forwarding`), `weight_snapshot`, `override_reason`, `decision_hash`, and `updated_at`. This is the explicit directory for logical-bucket ownership. | `(epoch_id, home_shard_id, state)`, `(epoch_id, state)`, unique `(epoch_id, bucket_id)` |
| `object_routes` | Retain `(object_type, object_id) -> home_shard_id`; for Procedures add `placement_epoch_id`, `bucket_id`, `route_state` (`intent`, `active`, `forwarding`, `retired`), `canonical_version`, `placement_decision_hash`, and `last_event_id`. For Goals, `home_shard_id = K000` in the target. | Existing `(object_type, object_id)` primary key; `(home_shard_id, object_type, route_state)`; `(placement_epoch_id, bucket_id)` |
| `procedure_row_routes` | Retain `row_id`, `procedure_id`, `version`, and `home_shard_id`; add `placement_epoch_id`, `bucket_id`, and `route_state` so a version row can be addressed during migration. | `(procedure_id, version)`, `(home_shard_id, route_state)`, `(placement_epoch_id, bucket_id)` |
| `goal_anchors` | `goal_id` primary key and local FK to the canonical `goals` row; `canonical_home_shard_id`, `current_version`, `anchor_generation`, `status`, `scope_type`, `scope_entity_id`, `visibility`, `owner_id`, `content_hash`, and `updated_at`. This is an authority/generation record, not a second mutable Goal payload. | `(anchor_generation)`, `(status, anchor_generation)`, `(scope_type, scope_entity_id, status)` |
| `goal_relation_versions` | `relation_id`, `version`, `specific_goal_id`, `abstract_goal_id`, `relation_type`, `status`, `confidence`, `source_id`, `identity_decision_id`, `proposed_by`, `decided_by`, `decided_at`, `t_valid`, `t_invalid`, `t_created`, and `decision_reason`. The current direct edge is the authority; old versions remain auditable. | Unique `(relation_id, version)`; unique current direct assertion; accepted-current indexes on both endpoints; `(status, decided_at)` review queue |
| `goal_closure_projection` | Optional derived table: `hierarchy_generation`, `descendant_goal_id`, `abstract_goal_id`, `min_depth`, `max_depth`, and `source_relation_version_ids`. It contains only paths induced by current accepted edges. | Unique `(hierarchy_generation, descendant_goal_id, abstract_goal_id)`; both endpoint directions |
| `procedure_goal_postings` | Rebuildable direct-membership projection: `procedure_id`, `procedure_row_id`, `direct_goal_id`, `home_shard_id`, `placement_epoch_id`, `bucket_id`, `status`, `visibility`, `scope_type`, `scope_entity_id`, `tenant_id`, `canonical_version`, `source_event_id`, and `projected_at`. It is not a second Procedure authority. | `(direct_goal_id, status, home_shard_id)`, `(procedure_id)`, `(home_shard_id, direct_goal_id)`, `(source_event_id)` |
| `inherited_goal_postings` | Optional derived projection for accepted hierarchy: `hierarchy_generation`, `abstract_goal_id`, `procedure_id`, `procedure_row_id`, `direct_goal_id`, `home_shard_id`, and `projected_at`. Inherited membership is never written back as a canonical `ACHIEVES` claim. | `(hierarchy_generation, abstract_goal_id, home_shard_id)`, `(hierarchy_generation, procedure_id)`, `(abstract_goal_id, procedure_id)` |
| `goal_shard_routes` | Compact aggregate for per-shard search: `hierarchy_generation`, `goal_id`, `home_shard_id`, `posting_count`, `oldest_canonical_version`, `projected_at`, and `coverage_state` (`complete`, `lagging`, `unknown`). It answers “which shards must be searched?” without copying Procedure bodies. | `(goal_id, hierarchy_generation, coverage_state)`, `(home_shard_id, coverage_state)`, `(projected_at)` |
| `placement_intents` | `idempotency_key` unique, `procedure_id`, `procedure_row_id`, `goal_versions`, `placement_epoch_id`, `bucket_id`, `desired_shard_id`, `decision_hash`, `state`, `attempts`, `last_error`, and timestamps. This is the durable pre-write handshake and repair record. | `(state, updated_at)`, `(procedure_id)`, unique `idempotency_key` |
| `control_outbox` | `event_id` primary key, aggregate type/id, event type, payload, `source_epoch`, attempts, status, and timestamps. Goal changes, hierarchy revisions, placement decisions, and route publication use this durable intent. | `(status, created_at)`, `(aggregate_type, aggregate_id)` |
| `retrieval_decisions` | Extend the current decision row with `placement_epoch_id`, `bucket_id`, `hierarchy_generation`, `goal_relation_version_ids`, `routing_mode`, `route_revision`, `routing_freshness`, `shards_probed`, `omitted_shards`, `unavailable_shards`, and `fallback_reason`. | `(created_at)`, `(routing_mode, created_at)`, `(hierarchy_generation)` |

`goal_search_index` remains a rebuildable central search projection because Goals are centralized and its FTS/HNSW indexes are already part of the current retrieval path ([95_control_plane_shards_projections.sql](../backend/db/95_control_plane_shards_projections.sql#L136-L166)). `procedure_search_index` can remain the launch-mode full central projection, but its current one-`goal_id` row cannot by itself represent multiple applicable Goals ([95_control_plane_shards_projections.sql](../backend/db/95_control_plane_shards_projections.sql#L168-L199)). In the target it stores the primary direct Goal for compatibility while `procedure_goal_postings` carries every direct link. If a product decision requires secondary-Goal search without route expansion, a separate many-to-many search projection can be added; it must still be rebuildable and must carry the same embedding-model generation as the local indexes.

### Each data shard

| Table or object | Target contents | Invariant |
|---|---|---|
| `procedures` | Retain the current versioned canonical row and `procedure_id`; add or validate `placement_epoch_id`, `bucket_id`, `placement_key_version`, `placement_decision_hash`, `goal_version`, and `goal_snapshot_hash`. `home_shard_id` is the authoritative local home. | New versions carry the same home, bucket, and epoch unless an explicit bucket migration changes them. |
| `procedure_goal_links` | Canonical local many-to-many claims: `procedure_id`, `goal_id`, `link_kind`, `is_primary`, `goal_version`, `goal_snapshot_hash`, `scope/visibility`, `source_id`, provenance, and validity timestamps. The current `procedures.achieves_goal_id` remains the mandatory primary pointer during transition; it must not be independently writable from the link table. | One primary direct Goal per live Procedure; additional links are explicit claims, not inferred hierarchy edges. |
| `goal_anchor_projection` | `(goal_id, goal_version)` primary key, `anchor_generation`, `status`, `merged_into_id`, normalized/display name, `scope_type`, `scope_entity_id`, `visibility`, `owner_id`, `content_hash`, `source_event_id`, and `projected_at`. | Read-only, versioned, repairable projection; never a second identity, permission, merge, or hierarchy authority. |
| `procedure_local_search` | Current version, FTS text, vector, embedding model/version/dimension, status, scope, and stable IDs. Build GIN and HNSW-compatible local indexes. | Local search is authoritative only for candidate generation; canonical rows remain the source of truth. |
| `procedure_local_outbox` | `event_id`, `procedure_id`, `procedure_row_id`, event type, canonical version, payload or normalized changed fields, attempts, status, and timestamps. | Inserted in the same local transaction as the Procedure change. |
| `event_inbox` | Consumer name, `event_id`, received/applied timestamps, and outcome. | Makes at-least-once delivery idempotent and suppresses duplicate or out-of-order events. |
| `placement_receipts` | `idempotency_key`, `procedure_id`, `procedure_row_id`, selected epoch/bucket/home, Goal-anchor versions, and commit state. | A retry returns the original decision or its current durable state; it never recomputes a different home. |
| `route_cache` | Last verified placement epoch, bucket-owner map digest, forwarding records, and refresh timestamp. | Cache miss is distinguishable from control-plane outage; a stale cache cannot authorize data. |
| Procedure-local evidence, claims, edges, plans, and execution state | Keep procedure-targeted evidence, claims, edges, and run-specific execution records on the Procedure home whenever possible. The current code already routes versions and evidence to the Procedure home ([procedures.py](../backend/app/services/procedures.py#L604-L710); [procedures.py](../backend/app/services/procedures.py#L818-L830)). | Same-shard relationships may use local foreign keys; cross-shard relationships use stable IDs and route metadata. |

A local foreign key from a Procedure link to `goal_anchor_projection` can prove only that the local projection row exists. It cannot prove that the current central Goal has the same version, scope, or visibility, so the write path must validate the central route and recheck security at read/execution time. The current route-aware trigger demonstrates the boundary: it first checks local tables and then accepts a remote route only when the route points away from K000 ([96_sharded_canonical_writes.sql](../backend/db/96_sharded_canonical_writes.sql#L18-L42)). A K000-owned Goal referenced by a remote Procedure therefore needs the new local anchor contract before that trigger can be safely adapted.

### Write and publication sequence

The cross-database write is a resumable state machine, not a distributed SQL transaction:

```text
control transaction:
    resolve the canonical Goal set and primary Goal version
    allocate or reuse procedure_id and row_id
    read one frozen placement epoch
    compute bucket and HRW owner, or use an audited bucket override
    insert placement_intent and an idempotency reservation
    enqueue Goal-anchor delivery for the owner
    commit

owner-shard transaction:
    verify the required Goal-anchor projection and current security metadata
    insert the Procedure version, canonical Goal links, and local search data
    insert the local outbox event
    commit

control consumer:
    deduplicate by event_id
    upsert object_routes and procedure_row_routes
    upsert direct and hierarchy-derived postings
    refresh projections and aggregate Goal-to-shard routes
    mark the placement intent active
    acknowledge or retry idempotently
```

If the control database fails after the owner-shard commit, the Procedure remains canonical and the local outbox retains the publication intent. The current implementation calls `enqueue()` on the control database after a remote commit and relies on reindex repair, so it is eventually repairable but has a crash window ([procedures.py](../backend/app/services/procedures.py#L467-L477); [search_projection.py](../backend/app/services/search_projection.py#L9-L19)). The target closes that window by making the outbox row local to the canonical shard. AWS’s transactional-outbox guidance makes the same distinction: the data change and intent belong in one local transaction, while consumers must be idempotent because delivery can duplicate ([AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)).

Goal creation is simpler in the target because the canonical Goal write, global name claim, hierarchy revision, and control outbox are all local to K000. Anchor delivery to a data shard is asynchronous and versioned. A Procedure write must not commit if the owner lacks the required anchor version; it may remain in a retryable `pending_anchor` state, but it must not invent a Goal or use a stale private projection to bypass current scope and visibility decisions. Existing Goal and Procedure reads can use a verified cached route and anchor. New global Goal identity writes, new public Procedure writes requiring fresh placement, hierarchy decisions, and rebalancing should queue or fail closed when the control plane cannot validate them.

Cross-shard Procedure references use `procedure_id`, `procedure_row_id`, version, and optionally a content hash. The control route resolves the current home; a local Procedure reference anchor can make an offline display join possible, but it is repairable state. A workflow that updates Procedures on several shards, such as a Goal merge, must be a control-plane state transition followed by idempotent per-shard updates and verification. The current sharded merge already uses per-shard updates and tolerates unavailable shards, which is the right shape ([identity_resolution.py](../backend/app/services/identity_resolution.py#L1012-L1051)).

Cross-database foreign keys and two-phase commit are deliberately excluded. PostgreSQL’s `postgres_fdw` creates remote transactions but documents that multiple remote transactions are committed serially by default and that it cannot prepare a remote transaction for two-phase commit ([postgres_fdw transaction management](https://www.postgresql.org/docs/current/postgres-fdw.html#POSTGRES-FDW-TRANSACTION-MANAGEMENT)). Foreign-table constraints are also not core-enforced remote constraints ([CREATE FOREIGN TABLE](https://www.postgresql.org/docs/current/sql-createforeigntable.html)). The right boundary is therefore a stable-ID directory plus local transactions, outboxes, idempotent consumers, explicit pending states, and repair jobs. FDWs may be useful for controlled migration or read-through work, but not for canonical writes or assumed referential integrity.

The current control-plane association tables expose the migration work this boundary requires. Procedure submissions, usage events, and credit-ledger rows still contain physical references to local Procedure rows or old Goal targets ([102_contribution_economy.sql](../backend/db/102_contribution_economy.sql#L107-L114); [102_contribution_economy.sql](../backend/db/102_contribution_economy.sql#L274-L279); [102_contribution_economy.sql](../backend/db/102_contribution_economy.sql#L330-L342)); migration 110 retargets the Goal columns to `goals(id)` ([110_retarget_goal_id_to_goals.sql](../backend/db/110_retarget_goal_id_to_goals.sql#L71-L92)). Centralizing Goals makes the Goal side of those constraints compatible with the control plane, but Procedure-row references must either move with the Procedure shard or become stable-ID/route-aware records. Execution-plan and durable-run tables should follow the same rule: local physical FKs where co-located, stable references and repairable anchors where not.

## Retrieval stays two-stage and fails safe

The retrieval contract should remain the repository’s current shape: resolve a Goal, generate Procedure candidates, hydrate only the shards holding candidates, apply hard factual constraints to canonical rows, and only then perform semantic judgment and evidence-based selection. The current service explicitly refuses to search Procedures globally when no Goal resolves and records unavailable shards separately from missing rows ([retrieval_service.py](../backend/app/services/retrieval_service.py#L380-L447)). The target adds hierarchy and a second candidate-generation mode without changing the authority boundary.

The recommended launch mode is to retain the full central `procedure_search_index` while it fits measured budgets. The local benchmark covers 100,000 Goal projections and 20,000 Procedure projections on one Windows/PostgreSQL machine: Goal vector top-20 p95 was 6.4 ms, deliberately low-selectivity fused retrieval p95 was 849 ms, and one real projection drainer processed about 127 objects/second ([production_ingestion.md](../docs/production_ingestion.md#L71-L104)). These are explicitly local measurements, not a 100-shard capacity plan. At one million 1,024-dimensional float32 Procedure vectors, the raw vector payload alone is about 4.1 GB before HNSW, TOAST, text, metadata, and MVCC; that arithmetic is a reason to measure, not a claim that a central index must fail. The repository’s configured embedding dimension is 1,024 ([config.py](../backend/app/config.py#L275-L276)).

The hot-path pseudocode is:

```text
search(query, scope, completeness):
    search the centralized Goal FTS/ANN projection with RRF
    semantically resolve at most three Goal candidates
    expand the selected Goal set using only current accepted SPECIALIZES versions
    record relation version IDs and hierarchy_generation

    if launch_mode = central_full_projection and projection_watermark is fresh:
        search central Procedure FTS/ANN with direct Goal filters
        fuse lexical and vector legs with RRF
        candidates = central rows plus procedure_row_id and home_shard_id
    else if compact_mode and route coverage is provably fresh:
        read direct and inherited Procedure postings for the expanded Goal set
        read the aggregate Goal-to-shard route set
        query top-m local FTS/ANN on each selected readable shard concurrently
        globally merge the local candidate lists
    else:
        use the safe fallback below

    group candidate row IDs by home_shard_id
    hydrate one batch per involved shard
    reapply tenant, visibility, scope, status, staleness, and precondition gates
    apply semantic judging and evidence selection to canonical rows
    return the result with freshness, omitted shards, and unavailable shards
```

For a concrete Goal request, the exact direct Goal is always an eligible candidate source. For an abstract Goal request, accepted descendants may be added as candidate Goals, but a generic Procedure linked only to the abstract Goal is not automatically a valid solution for a specific descendant; the existing hard applicability cascade remains non-compensatory. Accepted hierarchy may add candidates, but it cannot compensate for a failed tenant, visibility, status, or precondition constraint. The current service’s hard-check stage is the correct place for that separation ([retrieval_service.py](../backend/app/services/retrieval_service.py#L425-L446)).

In compact mode, if all selected shards use the same embedding model, dimension, and cosine metric, each shard returns at least `m >= k` candidates and the coordinator performs a deterministic distance merge with stable Procedure ID as the tie-break. `m` is larger than `k` when HNSW/IVF approximation, filtering, or uncertainty can discard relevant candidates. Lexical and vector legs should be fused with reciprocal-rank fusion rather than pretending BM25 and cosine are the same quantity:

```text
rrf(procedure) = sum(
    leg_weight / (60 + rank_in_leg)
    for each leg in which the procedure appears
)
```

OpenSearch documents the same rank-fusion rationale and warns that shard count can change per-shard `k` and ranking contributions ([OpenSearch RRF](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/)). Do not independently min-max each shard’s small result set; raw scores are directly comparable only for a homogeneous metric/model. A shard’s health or load can determine probe order and timeout, but it must not multiply user relevance. Distributed vector search generally asks each selected shard for a local candidate set and merges globally; Elasticsearch documents this scatter/gather model ([Elasticsearch kNN](https://www.elastic.co/docs/solutions/search/vector/knn)).

The safe fallback is inclusion-safe. A stale or incomplete hierarchy must not be used to exclude a shard merely because a Procedure is absent from a known posting. The existing `coarse_route_safe_exclusions()` deliberately excludes only leaves proven to belong to another branch and retains unclustered/recent rows; that same principle applies to Goal routes ([hierarchy.py](../backend/app/services/hierarchy.py#L1004-L1030)). If a route revision is behind the canonical revision, a new Procedure has an unapplied local outbox event, an accepted hierarchy is not fully built, or a required anchor is missing, the coordinator should use the fresh central projection when available, add a recent/unrouted overflow route, or fan out to all readable data shards. A completeness-critical audit must use full fanout and strict unavailable-shard behavior; a recommendation path may return a clearly marked partial result rather than silently claiming a global top-k. The response metadata must preserve `routing_mode`, `route_revision`, `hierarchy_generation`, `shards_probed`, `omitted_shards`, `unavailable_shards`, and `fallback_reason`, extending the current retrieval audit fields ([95_control_plane_shards_projections.sql](../backend/db/95_control_plane_shards_projections.sql#L229-L246)).

## Growth and migration require explicit epochs and proof

Growth from one to 100 physical shards should happen by assigning stable buckets, not by changing a Procedure’s semantic key. The first epoch can own all 1,024 buckets on one physical data shard. As shards are added, a new draft epoch computes desired owners from the current weighted catalog, assigns a bounded subset of buckets to each new shard, and migrates one bucket or small bucket group at a time. The current repository’s rollover command stops new placement on a full shard but does not move existing objects ([OPERATIONS_AUTOMATION.md](../docs/OPERATIONS_AUTOMATION.md#L46-L61)); the target adds an explicit migration state machine rather than turning that lever into an implicit rebalancer.

A bucket migration should create the next epoch, mark the bucket `copying`, stream or copy the Procedure families and colocated local state, verify counts and content hashes, publish the new owner as `active`, retain a forwarding record on the old owner, and only then mark the old owner `draining` or retire the old epoch. New writes during the handoff either go to the new owner after cutover or are paused for that bucket; they must not create a second canonical writer. Existing versions and evidence remain on the old home until the migration is complete. Because logical buckets are independent of physical membership, adding a shard does not require a new hash for every Procedure or force unrelated semantic relationships to move.

A failed shard is not automatically a new home. Existing reads return an explicit unavailable/degraded result unless a separately designed replica/failover policy says otherwise; new writes exclude it from eligibility. If no shard remains, placement fails closed. A control-plane outage has three distinct effects: existing Procedure reads can continue from a last verified route, epoch, and Goal-anchor cache; new public writes requiring fresh Goal identity or placement should queue or fail closed; and hierarchy changes, rebalancing, and new global Goal identities pause. If no valid cached placement view exists, the system must not hash against an unknown membership set. This follows the AWS reliability guidance to keep data-plane service operating from previously distributed state during control-plane impairment while caching critical control data ([AWS REL11-BP04](https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_withstand_component_failures_avoid_control_plane.html)). The repository’s existing `ShardPools` backoff and `HydrationResult.unavailable_shards` provide a useful implementation precedent ([shards.py](../backend/app/services/shards.py#L267-L384)).

### Focused migration sequence

The migration order follows the dependency edges rather than changing placement first:

| Phase | Change | Exit condition |
|---:|---|---|
| 1 | Record the product decisions for all-Goals-in-control, `SPECIALIZES` meaning and acceptance authority, bucket count, visibility policy, route SLOs, and rebalancing objective. | A versioned design decision exists; no implementation assumes an unresolved semantic question. |
| 2 | Add control-plane epoch, bucket-owner, anchor, relation-version, posting, intent, inbox/outbox, and audit tables. Add shard-local anchor, link, outbox, inbox, and route-cache tables. | Fresh schemas apply idempotently to control and data shards; existing migrations are not edited. |
| 3 | Make central Goal reads and anchors authoritative, add the local-anchor-aware reference validator, and repair the cross-shard Goal/Procedure boundary. | A K000 Goal can be referenced by a Procedure on a remote shard without a cross-database FK. |
| 4 | Add the data-shard placement operation and dual-read support. New Procedures use the new epoch; existing stored homes are not moved. | Same ID/epoch determinism, protected-data placement, and no-writable behavior are proven. |
| 5 | Add the shard-local outbox consumer, route/posting projection repair, and both search modes behind an explicit deployment mode. | Crash-after-commit, duplicate delivery, out-of-order delivery, and control outage recover without lost routes. |
| 6 | Implement the versioned `SPECIALIZES` adjudication writer and accepted-edge expansion. Add closure only after coverage and latency measurements justify it. | Proposed edges cannot route; accepted edges are auditable and cycle/scope checks pass. |
| 7 | Introduce additional shard roles and grow one to 100 physical stores using bucket epochs. | Forwarding, restore, and rebalance checks pass without split-brain writers. |
| 8 | Cut over the selected retrieval mode and retain rollback to the previous route epoch/projection mode. | A full-fanout oracle and the focused acceptance checks below meet the declared SLO. |

This repository’s hard rule is fresh-start: no implicit legacy backfill and no migration-for-legacy-data. Therefore the target migration should not silently copy existing remote Goals into a second canonical K000 table. A fresh target namespace can be introduced for new writes, or an operator-approved rehome program can explicitly move legacy rows with a separate audit and quarantine policy. Existing remote Goal rows remain readable and quarantined until that decision is made. The current second-database tests prove remote Goals, colocated remote Procedures, evidence locality, private placement, rollover, merge, and targeted hydration, but they do not prove a K000 Goal with a Procedure on a different shard ([test_sharded_writes_e2e.py](../backend/tests/test_sharded_writes_e2e.py#L88-L164); [test_sharded_readers_e2e.py](../backend/tests/test_sharded_readers_e2e.py#L50-L103)). That exact case is a required target check, not an assumption inherited from the current tests.

### Required validation checks, not a full-suite run

The implementation should run only the following focused checks until the design is accepted; the user’s request explicitly excludes running every backend, harness, and packaging test suite:

| Check | Required assertion |
|---|---|
| Deterministic placement | For many fixed Procedure IDs, the same epoch, registry snapshot, and hash version always produce the same bucket and HRW owner; ties resolve by `shard_id`; a retry uses the persisted decision. |
| Eligibility and protected data | Public Procedures select only active `procedure_data` shards; private/org Procedures remain on K000; conflicting hard zones and a fully reserved capacity set fail closed without a canonical write. |
| Central-Goal/remote-Procedure integration | A Goal canonical in K000 is anchored on a remote data shard; a Procedure and its local evidence commit there; control routes, direct postings, search, and hydration resolve without a cross-database FK. |
| Outbox recovery | Kill or fail the worker after the local Procedure commit, replay duplicate and out-of-order events, and verify the route, row route, postings, and projection converge idempotently. |
| Hierarchy semantics | Proposed, accepted, rejected, rescoped, and cyclic relation cases are versioned and auditable; only current accepted edges expand candidates; no hierarchy edge changes a Procedure’s home or creates execution order. |
| Search and fallback | Compare central-full and compact-per-shard modes against a full-fanout oracle for Recall@10/20, judged applicability, p50/p95/p99 latency, bytes, and shards probed; stale routes, recent writes, and unavailable shards produce the documented partial/fallback result. |
| Growth and rebalance | Move a bounded set of buckets from one owner to another, verify forwarding, content hashes, old-owner reads, new writes, and restoration; verify no split-brain writer or lost Procedure. |
| Security and outage | Verify route postings and anchor projections never widen tenant, visibility, scope, or owner access; control outage permits only documented cached reads and queues/fails closed fresh writes. |
| Restore and audit | Restore control and a data shard from independent backups, replay recorded events, and verify every route, anchor version, relation revision, and Procedure reference before reopening writes. |

The current repository’s measured numbers do not replace these checks. In particular, a 100-shard capacity result, a central Procedure-projection curve, hierarchy coverage, and per-shard connection/tail-latency behavior are all still unknown. **Design judgment:** the first implementation gate should be correctness and recoverability on two real databases plus a simulated 100-shard registry, followed by a focused load/soak measurement; it should not be a blanket test-suite run.

### Decisions required before implementation

| Decision | Recommended default | Why the user must choose it |
|---|---|---|
| Centralize all Goals or only abstract Goals | Centralize all Goals in K000 initially | Partial centralization creates remote concrete Goal endpoints and makes association constraints and reader behavior harder for little proven benefit. |
| Meaning of `SPECIALIZES` | Define whether it is formal set containment or a retrieval heuristic; default to accepted-only, acyclic, semantic candidate expansion | The answer determines whether ancestor/descendant inheritance is logically valid or merely a search expansion. |
| Acceptance authority | Named reviewer plus a documented automated policy, with human accountability for acceptance/rejection | The current writer only creates proposed edges; an accepted vocabulary without a writer is not a routing contract. |
| Cross-scope hierarchy | Forbid implicit private-to-public or tenant-to-tenant edges | A hierarchy projection can leak metadata even when canonical rows remain protected. |
| Multiple applicable Goals | Support one primary direct Goal plus explicit additional direct links; inherited hierarchy is never a canonical direct claim | The current singular `achieves_goal_id` and one-row central Procedure projection cannot express the requested multi-Goal case without an additional relation/posting contract. |
| Logical bucket count | Start with 1,024, then measure | A fixed count is a migration-cost and load-balance decision, not a consequence of “100.” |
| Physical topology | 100 independently provisioned clusters/databases | Schemas or partitions in one cluster do not provide independent failure, backup, or capacity domains. |
| Capacity and locality policy | Hard eligibility filters plus epoch weights; explicit, expiring bucket overrides for locality | Dynamic least-loaded placement would make retries and audit dependent on mutable telemetry. |
| Search mode | Full central Procedure projection at launch; compact postings/per-shard search when measured budgets require it | No current benchmark establishes a central Procedure capacity limit at 100 shards. |
| Search budgets | Define `k`, local `m`, shard fanout cap, projection-lag SLO, and partial-result policy before tuning | Route breadth and local candidate depth directly determine recall and latency. |
| Control-plane outage | Cached existing reads; queue/fail closed new identity, fresh placement, hierarchy, and rebalance operations | The product must choose whether stale writes are ever acceptable. |
| Private and organizational data | Keep on the authorized private home; do not copy protected Goal text or anchors to public shards | The current repository already treats K000 as the privacy boundary. |
| Execution placement | Co-locate Procedure-targeted plans, runs, evidence, and claims with the Procedure where feasible; use stable IDs for unavoidable cross-shard references | Existing economy and execution tables contain physical Procedure-row references that cannot remain unchanged under remote-only homes. |
| Rebalancing objective | Minimize moved bytes and peak read/write impact under a declared migration window | “Balanced” is not measurable without a cost objective and a numeric movement budget. |
| Legacy data | Do not silently backfill or duplicate legacy remote Goals; quarantine or explicitly rehome | This follows the repository’s fresh-start rule and avoids two competing Goal truths. |
| Recovery objectives | Specify shard and control RPO/RTO before enabling automatic failover | Replica selection, synchronous standby, and degraded-mode behavior depend on RPO/RTO, not on HRW alone. |

## Conclusion

The durable design boundary is not “abstract Goals on one database and Procedures on many.” It is **one canonical Goal authority, one canonical home per Procedure family, and explicitly rebuildable indexes between them**. Bucketized HRW gives the data plane deterministic, growth-friendly placement; a versioned directory makes capacity and locality decisions visible; semantic DAG revisions explain why a Procedure became a candidate without pretending that hierarchy is execution order.

The implementation should first make the authority and recovery boundaries real—central Goals, local anchors, shard-local outboxes, stable IDs, and idempotent route/posting publication—then change new Procedure placement. Search can evolve from the current central full projection to compact route postings and per-shard search without changing canonical hydration or hard applicability checks. That sequence preserves the repository’s strongest existing property while avoiding the two tempting but incorrect shortcuts: hashing by a hot Goal and using cross-database transactions to make unrelated ownership look atomic.

## Sources and evidence note

This report synthesizes the five notes in `research_notes/Hierarchical goal sharding/` and cross-checks their external claims against primary papers and vendor documentation using parallel Exa research. Claims labeled **Design judgment** are recommendations for this repository, not findings asserted by the cited sources. No product code was modified and no tests were run; the validation section is intentionally a focused implementation gate rather than a request to run every test suite.
