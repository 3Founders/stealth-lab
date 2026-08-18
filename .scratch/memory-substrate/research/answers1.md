1

1. Projection-by-query viability and failure modes
Bottom line: Computing state at read time over an append-only, bitemporal log is viable until one of these hits: (a) per-entity event counts grow large enough that replay dominates latency, (b) you need full bitemporal materialization (not just as-of-now), or (c) your query shape forces expensive temporal joins without specialized indexes. Practitioners introduce materialization (snapshots or precomputed projections) when replay time exceeds their SLO—commonly ~100ms—or when event counts per aggregate cross ~100–250 events.

What the literature and production writeups say
Source	Type & authority	Key numbers / failure modes	Decision it informs
XTDB blog: “Building a Bitemporal Index (part 2): Bitemporal Resolution” (Henderson, May 2025)	Production engineering writeup from the XTDB team (bitemporal DB creators). Authoritative for real bitemporal query execution.	Shows that naïve bitemporal resolution at query time requires backward replay over event logs plus a “ceiling” data structure; for full materialization this is expensive. As-of-now queries can short-circuit early, but back-in-time or full-history queries explode.	When to avoid pure query-time state: Use pure graph queries only for as-of-now; require materialized views for temporal range joins or full-history materialization.
Event Sourcing Guide: “Snapshots – First Principles” (2024) 
Community-maintained ES/CQRS guidance; widely cited in production event-sourcing practice. Not peer-reviewed but reflects real patterns.	Snapshots reduce aggregate load time from replaying thousands of events to replaying only post-snapshot events. Recommends snapshots when aggregates have “hundreds or thousands of events,” are frequently accessed, or when replay is “already fast enough” you can skip them.	Snapshot trigger heuristic: Materialize when per-entity event count > 100–250 or when load time > your latency budget (e.g., 100ms) 
.
Axon Framework docs: “Event Snapshots” (2026) 
Vendor documentation for a widely used ES framework on the JVM. Authoritative for production ES patterns.	Snapshot creation triggered by event-count threshold, load-time threshold, or time-based. Default threshold in samples is 100 events; production guidance suggests 100–250 depending on aggregate complexity 
.	Concrete threshold: Set snapshot trigger at 100–250 events per aggregate or when aggregate load time > your SLO.
AxonIQ code samples: “Snapshots README” (2020) 
Production code samples from AxonIQ.	“5 is fine for testing, but in real life you would likely use a threshold between 100 and 250… measuring how long it takes to load an Aggregate and basing the count on that will end you up with the most optimal solution.” 
Adaptive triggering: Prefer load-time-based snapshot triggers over fixed event counts once you have production telemetry.
Datomic internals (Tonsky, 2014) 
Unofficial but well-sourced synthesis of Datomic’s design.	as-of queries merge current, in-memory, and history index parts, then filter by time. No older index versions required; queries use most recent current index and deduce previous views.	Index shape implication: Your bitemporal fact graph needs a log index (by transaction time) plus a current index (by entity/attribute) to make as-of queries cheap without full replay.
Failure modes documented:

Projection lag invisible to users (CQRS): writes appear lost because read views haven’t caught up .

Projector failures create growing backlogs; without lag monitoring, stale views go undetected .

Storage overhead: maintaining multiple denormalized views significantly increases storage vs. a single normalized model .

Replay cost: aggregates with long event histories (hundreds/thousands of events) become slow to load without snapshots.

Are there systems that tried pure query-time state and abandoned it? No public post-mortem explicitly says “we abandoned pure query-time state.” However, the universal pattern in ES/CQRS and bitemporal DBs is: start with event-sourced replay, then add snapshots or materialized projections once latency or event counts cross thresholds. Datomic and XTDB both keep full history but rely on specialized indexes (EAVT/AEVT/Log for Datomic; bitemporal resolution with ceiling for XTDB) to avoid naïve replay.

Engineering judgement call: There are no independent benchmarks that say “pure query-time state fails at X million facts.” The numbers you have are practitioner heuristics: snapshot at 100–250 events per aggregate or when load time > 100ms.

2. “State is just time-scoped facts”: recognized stance or novel bet?
Bottom line: Treating state as time-indexed facts is a recognized stance in situation calculus (fluents), event calculus, and bitemporal database design. The frame problem literature explicitly argues for representing what hasn’t changed implicitly via frame axioms, which aligns with your “no state table” bet. However, collapsing durable belief and transient world-state into a single fact graph is less common; most systems keep a separation (e.g., Datomic’s immutable datoms vs. application-level “current state” views).

Situation calculus / fluents
Source	Type & authority	Key takeaway	Decision it informs
Stanford Encyclopedia of Philosophy: “The Frame Problem” (2009) 
Authoritative philosophy/AI reference entry.	The frame problem is “the challenge of representing the effects of action in logic without having to represent [all non-changes]” 
. Solutions use frame axioms to implicitly carry forward unchanged fluents.	Implicit non-change: Your design (no explicit “unchanged” records) matches the frame-problem solution: only record changes; state at T is the set of fluents whose validity intervals contain T.
ACM DL: “The frame problem in situation calculus” (Reiter, 1991) 
Classic AI paper; authoritative for situation calculus.	Provides a solution to the frame problem for deterministic actions using successor-state axioms.	Successor-state axioms: Informs your delta computation: state_after = state_before ∪ adds − deletes, without storing state_before explicitly.
Imperial College: “Database Updates in the Event Calculus” (Kowalski et al.) 
Academic paper applying event calculus to databases.	Explicit deletion replaced by implicit deletion via frame axiom: Holds(r, result(a, s)) if Happens(a, s) and r is preserved 
.	Frame axiom as query: Your “state at T = facts whose validity interval contains T” is exactly the event-calculus frame axiom operationalized as a query.
Temporal RDF / bitemporal databases
Source	Type & authority	Key takeaway	Decision it informs
XTDB blog: “Bitemporal Resolution”	Production writeup (see above).	Entities have a timeline of valid-time intervals at each system-time; observers take immutable snapshots of the whole database at a system-time.	State as timeline: Validates your “state = time-scoped facts” for valid-time; adds system-time as a second dimension for auditability.
Datomic tutorial: “History” 
Vendor documentation for Datomic (bitemporal-inspired, unitemporal in practice).	as-of queries return the database “as of” a transaction time or instant, ignoring later transactions 
.	Unitemporal precedent: Datomic treats all data as immutable datoms; “current state” is a derived view, not a stored table.
BDI / agent architectures
No direct source found that argues against unifying belief and world-state in a single fact graph. The BDI literature typically separates beliefs (internal, possibly uncertain) from world state (external, observed), but this is a modeling choice, not a technical constraint.

What’s missing: No paper explicitly warns “don’t collapse belief and world-state into one fact graph” with empirical failure cases. This is an engineering judgement call: the risk is that belief revision (uncertain, defeasible) and world-state updates (deterministic, timestamped) have different lifecycle and provenance needs. If you unify them, you must tag each fact with epistemic status (belief vs. observation) and confidence.

3. Index shape and materialization threshold (PostgreSQL-specific)
Bottom line: For as-of queries over validity ranges in PostgreSQL, the established practice is: use tstzrange (or tsrange) columns with GiST indexes (or SP-GiST for evenly distributed ranges), plus exclusion constraints to prevent overlapping validity intervals per subject. A composite (subject, valid_during) GiST index (via btree_gist extension) is the production pattern; plain btree on (subject, valid_from, valid_to) is inferior for containment/overlap queries.

PostgreSQL temporal indexing practice
Source	Type & authority	Key numbers / patterns	Decision it informs
Cybertec: “Implementing ‘AS OF’-queries in PostgreSQL” (2024) 
Commercial PostgreSQL specialist; practical, production-oriented guidance.	Uses tstzrange with EXCLUDE USING gist (id WITH =, valid WITH &&) to enforce non-overlapping validity per id. Creates views for “recent” and “historic” state using current_timestamp <@ valid or a GUC for as-of time 
.	Index shape: GiST on valid range + exclusion constraint; btree on scalar columns for filtering.
Redgate Simple-Talk: “PostgreSQL Range Overlap Queries: GiST Indexes” (2024) 
Professional PostgreSQL education; widely read by DBAs.	GiST is the standard index for range overlap queries (&&, @>, etc.). SP-GiST can give 30% speedup for evenly distributed ranges (5.2s → 3.7s on 1M rows test) 
. Composite GiST with btree_gist extension for (location_id, tsrange(enter, leave)) outperforms separate indexes 
.	Composite GiST: Use btree_gist to combine equality filters (subject id) with range containment in one index.
Cursa: “Indexing for Temporal Queries and Stream Reads” (2026) 
Ebook on event-driven PostgreSQL modeling.	B-tree for temporal access (ordered scans), GiST for range containment/overlaps. Recommends GiST on valid_during plus btree on entity_id 
.	Dual-index strategy: GiST for as-of containment, btree for entity lookup.
DevTechTools: “PostgreSQL Bi-temporal Modeling with Range Types & GIST” (2025) 
Advanced PostgreSQL guide.	“This model’s performance hinges entirely on the GIST indexes. A B-Tree index… cannot efficiently index the multi-dimensional nature of ranges” 
. With GiST on valid_time, as-of lookups are “typically excellent… 10–50ms” 
.	Performance expectation: As-of queries with GiST should be 10–50ms on large datasets; btree alone is insufficient.
DevTechTools: “Bi-Temporal Data Models in PostgreSQL with Range Types and GIN” (2025) 
Same as above.	GiST is the superior default for bi-temporal workloads; GIN can be faster for single-point containment but less efficient for overlaps 
.	GiST vs. GIN: Prefer GiST for general temporal queries; consider GIN only if you mostly do point-in-time lookups.
PostgreSQL Wiki: “SQL2011Temporal” (2025) 
Community documentation of SQL:2011 temporal features in Postgres.	Temporal primary keys use exclusion constraints with GiST indexes; requires btree_gist extension for scalar types 
.	Extension requirement: Install btree_gist to combine equality and range in one GiST index.
Materialization threshold numbers: No PostgreSQL-specific paper gives a hard “materialize after X rows” number. The guidance is qualitative: use materialized views when as-of queries become “expensive” (implicitly, > 100ms) or when you need to pre-aggregate for reporting. The ES/CQRS snapshot thresholds (100–250 events per aggregate) are the closest quantitative guidance.

Recommendation for your design:

Index shape: CREATE INDEX ON facts USING gist (subject_id, valid_during) with btree_gist extension, plus exclusion constraint EXCLUDE USING gist (subject_id WITH =, valid_during WITH &&) to enforce non-overlap.

Materialization trigger: Monitor as-of query latency; introduce materialized projections when p95 > 100ms or when per-subject event counts exceed ~200.

4. Representing unknown vs. false vs. partial
Bottom line: Absence = empty query result is sufficient if you adopt a closed-world assumption (CWA) for your fact graph. If you need open-world reasoning (unknown ≠ false), you must represent “unknown” explicitly (e.g., three-valued logic or explicit null sentinels). SQL’s NULL semantics are a cautionary tale: three-valued logic (TRUE/FALSE/UNKNOWN) leads to unintuitive query results and programmer mistakes.

Open-world vs. closed-world tradeoff
Source	Type & authority	Key takeaway	Decision it informs
Manchester CS: “The Open World Assumption” (Drummond, n.d.) 
University lecture slides; authoritative for OWA/CWA distinction.	OWA: unless we have a statement “pigs can/cannot fly,” we return “don’t know” 
. CWA: anything not provable true is false.	Assumption choice: If you want “unknown” to be first-class, adopt OWA and represent unknown explicitly; otherwise, CWA with absence = false is simpler.
DE Ontology Handbook: “Open World Assumption” 
Ontology engineering reference.	OWA: anything not explicitly stated is unknown, not false 
.	Explicit unknown: In OWA, you need a sentinel (e.g., unknown fact) to distinguish from absence.
C2 Wiki: “Open World Assumption” 
Community wiki; summarizes OWA/CWA.	OWA: propositions not derivable are unknown; CWA: not provable true → false 
.	Design implication: Your “absence = empty result” is CWA; if you later need OWA, you’ll regret not having an explicit unknown marker.
SQL NULL / three-valued logic cautionary tales
Source	Type & authority	Key takeaway	Decision it informs
VLDB Endowment: “Troubles with Nulls, Views from the Users” (2022) 
Peer-reviewed database research.	NULL values in SQL have over 20 semantic combinations; SQL’s three-valued logic filters out too many results, increasing user disagreement with queries involving negation 
.	Avoid SQL-style NULL: Don’t introduce a NULL-like sentinel without a clear semantics; it will confuse queries with negation.
ACM TODS: “Nulls, three-valued logic, and ambiguity in SQL” (2008) 
Peer-reviewed database theory paper.	SQL’s 3VL introduces “startling complexity” into seemingly straightforward queries; Date’s critique is flawed but the general conclusion stands 
.	Complexity warning: Three-valued logic makes query reasoning hard; prefer two-valued logic with explicit “unknown” facts if needed.
arXiv: “SQL Nulls and Two-Valued Logic” (2020) 
Academic preprint.	SQL’s 3VL adds UNKNOWN to handle nulls; often criticized for unintuitive behavior and programmer mistakes 
.	Programmer mistakes: If you introduce unknown, provide clear APIs to avoid 3VL pitfalls.
Simple-Talk: “SQL and the Snare of Three-Valued Logic” (2009) 
Professional SQL education.	NULLs propagate; comparisons with NULL yield UNKNOWN; this is a source of many application errors 
.	Propagation risk: Unknown sentinels will poison boolean expressions unless you handle them explicitly.
PipeCode: “SQL NULL Semantics & Three-Valued Logic” (2026) 
Data engineering blog.	“NULL in SQL is not a value — it is the ABSENCE of a value… a single unhandled NULL can silently corrupt a report, a join, a constraint, or an index” 
.	Absence vs. unknown: Your current plan (absence = empty result) avoids SQL’s NULL pitfalls; if you need unknown, make it a first-class fact type, not a NULL.
Three/four-valued logics in real systems
Source	Type & authority	Key takeaway	Decision it informs
Unimib: “BORDERLINE VS. UNKNOWN” (n.d.) 
Academic paper on Kleene/Belnap logics.	Kleene logic: 1/2 = borderline; Belnap: four values (true, false, both, neither) 
.	Four-valued option: If you need to represent contradictory or partial knowledge, Belnap’s four-valued logic is a formal basis.
Edinburgh: “SQL’s Three-Valued Logic and Certain Answers” (2016) 
Peer-reviewed database theory paper.	SQL’s 3VL leads to well-known paradoxes (e.g., x <= 0 OR x > 0 returns nothing when x is NULL) 
.	Paradox example: Avoid 3VL unless you need it; design queries to avoid UNKNOWN propagation.
Recommendation:

If you adopt CWA: Absence = false is fine; no explicit unknown needed. This matches your current plan and avoids SQL NULL pitfalls.

If you need OWA: Represent “unknown” as an explicit fact type (e.g., Fact(subject, predicate, status='unknown', valid_during)), not a NULL sentinel. This keeps boolean expressions two-valued and avoids 3VL complexity.

Engineering judgement call: No production agent-memory system is documented as using four-valued Belnap logic for partial knowledge. This is an engineering judgement call: if you anticipate frequent partial knowledge (e.g., “we know the file exists but not its contents”), model it as an explicit “partial” fact type rather than a NULL.

5. Immutable artifact references: content-addressed vs. typed unions
Bottom line: Content-addressed storage (CAS) via git SHAs or Merkle DAGs is the production standard for immutable artifacts; for large or streamed artifacts, systems use a hybrid: content-hash small artifacts, store large artifacts in blob storage with a CAS-like URI (e.g., s3://bucket/sha256-…), and maintain a typed reference union that names the addressing scheme. Mixing addressing modes without a typed union leads to cache-invalidation bugs and broken reproducibility.

Git object model rationale
Source	Type & authority	Key takeaway	Decision it informs
Git documentation (implicit in many sources)	Git’s design is well-documented in books and talks.	Git uses SHA-1 (now SHA-256) content-addressed blobs, trees, and commits; this ensures immutability and deduplication.	Content-hash small artifacts: Use git SHAs for code, configs, and small build outputs.
Content-addressable storage (CAS, Merkle DAGs)
Source	Type & authority	Key takeaway	Decision it informs
Nix Reference Manual: “Content-address” (2024) 
Official Nix documentation; authoritative for CAS in build systems.	Nix store paths are content-addressed: /nix/store/<hash>-name; files and symlinks are hashed as git blobs/trees 
.	CAS for build artifacts: Use content-hash for reproducible builds; Nix’s approach is a production precedent.
Build-system artifact addressing (Bazel, Nix)
Source	Type & authority	Key takeaway	Decision it informs
Bazel action cache (implicit in Bazel docs)	Bazel’s design is well-documented.	Bazel action cache keys are content-addressed (input files’ hashes + command line); outputs are stored in a CAS.	Action cache precedent: Use content-hash for action inputs/outputs; large outputs go to remote cache with CAS-like URIs.
Nix (see above) 
Same as above.	Large artifacts are still content-addressed but stored in binary caches (e.g., cache.nixos.org) with CAS-like URIs.	Hybrid for large artifacts: Content-hash the metadata, store the blob in object storage.
What goes wrong when mixing addressing modes
Cache invalidation bugs: If some artifacts are path-addressed and others content-addressed, updates to path-addressed artifacts break reproducibility (the same content-hash may point to different versions).

Broken provenance: Without a typed reference union, you can’t tell whether a reference is a git SHA, S3 URI, or database ID, leading to incorrect resolution logic.

Large/streamed artifacts: Content-hashing large artifacts is expensive; systems like Bazel and Nix use a hybrid: content-hash small artifacts, store large artifacts in blob storage with a CAS-like URI.

Recommendation:

Typed reference union: Define a reference type like ArtifactRef = GitSha(string) | BlobUri(string) | DbId(int) that names the addressing scheme. This avoids ambiguity and enables correct resolution logic.

Hybrid for large artifacts: Content-hash small artifacts (code, configs, test results < 10MB); for large/streamed artifacts (build logs, videos), store in blob storage with a CAS-like URI (e.g., s3://bucket/sha256-…) and record the hash in your fact graph.

Precedent: Nix and Bazel both use content-addressed storage for reproducibility; large artifacts are stored in binary caches with CAS-like URIs.

Engineering judgement call: No empirical study compares “git SHAs + blob-store URIs” vs. “content-hash everything” vs. “typed reference union” for agent-memory artifacts. This is an engineering judgement call: the typed union is the safest design to avoid mixing addressing modes incorrectly.

Where the literature is silent (engineering judgement calls)
Q2 (collapsing belief and world-state): No empirical study warns against unifying durable belief and transient world-state in a single fact graph. This is an engineering judgement call: tag facts with epistemic status if you unify them.

Q4 (partial knowledge as first-class): No production system is documented as using four-valued Belnap logic for partial knowledge in agent memory. This is an engineering judgement call: model partial knowledge as an explicit fact type, not a NULL.

Q5 (typed reference union for artifacts): No empirical study compares artifact addressing schemes for agent-memory systems. This is an engineering judgement call: use a typed union to avoid mixing addressing modes incorrectly.

Actionable recommendations for your PostgreSQL-backed design
Index shape: Use tstzrange for validity intervals, GiST indexes (with btree_gist extension) for (subject_id, valid_during), and exclusion constraints to prevent overlapping validity per subject.

Materialization trigger: Monitor as-of query latency; introduce materialized projections when p95 > 100ms or when per-subject event counts exceed ~200.

Unknown vs. false: Adopt CWA (absence = false) for simplicity; if you need OWA, represent “unknown” as an explicit fact type, not a NULL sentinel.

Artifact references: Use a typed reference union (GitSha | BlobUri | DbId); content-hash small artifacts, store large artifacts in blob storage with CAS-like URIs 
.

If you want, I can sketch the PostgreSQL schema (tables, indexes, exclusion constraints) and the SQL for as-of queries and state deltas under this design.



