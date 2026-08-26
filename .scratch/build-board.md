# Build Coordination Board â€” multi-lane parallel execution (worktree edition)

**Run mode: one git worktree per lane, one agent instance per worktree, this main
checkout = integrator/reviewer.** Lanes push `lane/<name>` branches; the integrator
runs the full suite on main and merges green branches. File ownership is absolute â€”
a lane that edits outside its paths gets its commit reverted, no discussion.

## Worktrees

```powershell
git worktree add ..\sl-core-a  -b lane/core-a  origin/main
git worktree add ..\sl-core-b  -b lane/core-b  origin/main
git worktree add ..\sl-measure -b lane/measure origin/main
git worktree add ..\sl-research -b lane/research origin/main
# per worktree needing pytest: python -m venv backend\.venv; pip install -r requirements.txt
```

## Lanes

### Lane CORE-A â€” storage & plans (owns `backend/db/**`, `backend/app/execution/**`, `backend/app/models/**`)
1. `[x]` **1.7** done @2026-08-25 â€” branch `lane/core-a`. Persist ExecutionPlan/TaskGraph `[Dâ†’frozen]`; bind executions to exact
   plan versions (Appendix C #1/#2/#17 proving tests in same change). One-way door.
   *(Prior claim by Chaitanya withdrawn by founder 2026-08-24 â€” lane reassigned to
   local worktree agent.)*
   Shipped: db/23_plan_persistence.sql (execution_plans + task_graphs + executions,
   frozen by trigger; composite FK procedures(procedure_id,version)); app/execution/plans.py
   (compile/hash/rebind/binding boundary); app/models/plan.py; 36 proving tests in
   tests/test_band1_7_plans.py. Full suite: 885 passed, 106 skipped.
2. `[ ]` **BLOCKED on external** â€” Real-DB migration chain verification (01â†’23) on
   disposable Postgres; awaiting Chaitanya's Docker (founder has none locally).
   Paste engine output when run.
3. `[x]` sweep performed @2026-08-25 (lane/core-a) â€” Band 1 exit criteria audited,
   result in Log entry below; formal integrator review requested, full pass pending
   queue 2's real-DB chain run.
4. `[x] done @2026-08-25 â€” branch lane/core-a` **NEXT WAVE â€” 1.9a Evidence
   table**: typed rows with independence groups; procedure verification stats become
   views over evidence. Proving tests: Appendix C #3/#12/#13.
   Shipped: db/24_evidence.sql (evidence [H] table â€” evidence_kind enum with Â§11's
   nine types, strength {score, method} NOT NULL, independence_group capping columns,
   context_key, Â§36 seven-value failure_class (Band 0 #0.5's structural home),
   named CHECKs incl. evidence_success_criteria_chk banning bare model-asserted
   success at the engine (#13 teeth), tg_evidence_append_only freeze with the
   t_invalid retraction tombstone as the ONE legal update (#19 teeth),
   procedure_evidence_stats view replacing the JSONB counter blob with
   independent_* counts via DISTINCT COALESCE(independence_group, id::text));
   app/execution/evidence.py boundary (validate_evidence / outcome_to_evidence /
   assert_verified_requires_evidence â€” #3's contract-level gate; pure, offline-
   provable, same pattern as plans.py); app/models/evidence.py shapes; 33 proving
   tests in tests/test_band1_9a_evidence.py. Full suite: 947 passed / 106 skipped /
   0 failed. Sequencing note: the verified-requires-evidence ENGINE trigger lands in
    the SAME change that wires evidence writes into the lifecycle path (cross-lane
    request below) â€” gate and writer together, never a half-gate.
5. `[x]` done @2026-08-25 â€” branch `lane/core-a` **Band 2.7 TMS readability**
   (integrator-approved 2026-08-25): `truth_state` is write-only
   today â€” OUT/stale claims still surface in retrieval. Filter
   `properties->>'truth_state'='OUT'` out of EVERY retrieval path
   (retrieval.py: vector/lexical/hydrate/graph-expansion;
   local_retrieval.py: structural/temporal tiers) WITHOUT touching the
   bi-temporal columns â€” an invalidated claim vanishes from results while its
   history stays queryable. Regression tests prove both halves. Scoped file grant
   for this item only: backend/app/services/retrieval.py +
   backend/app/services/local_retrieval.py (both otherwise unowned; integrator
   approval recorded here per OVERNIGHT MODE). No migration: read-time filter
   over existing JSONB properties, fresh-start compliant, nothing backfilled.
   *(Shipped: NOT_TRUTH_STATE_OUT predicate â€” `IS DISTINCT FROM 'OUT'`, so
   rows without the key stay visible; bare `<>` would have blanked every
   non-claim node. Applied to all four HybridRetriever stages incl.
   expansion re-hydration â€” the SUPERSEDES edge itself was the leak path
   back to the OUT claim â€” plus local_retrieval's structural/temporal legs;
   knowledge_nodes legs only, task_nodes has no properties column. 8 offline
   tests tests/test_tms_readability_offline.py (query-content + write-side
   preservation pins); 5 live-DB regression tests
   tests/test_tms_readability_e2e.py â€” vanish from hybrid/lexical-only/
   expansion/structural-tier + history-stays-queryable (raw row survives,
   subject history read sees both generations, project_state sees only IN).
    LIVE RUN: this worktree NOW HAS backend/.env (Supabase URL; earlier log
    notes said absent â€” changed since) and the schema is fully migrated:
    all 5 e2e proofs PASSED against the real DB. Full standard suite (no env):
    989 passed / 111 skipped / 0 failed. Full suite WITH DATABASE_URL exported:
    1059 passed / 40 failed / 1 skipped â€” the same 40 fail identically on a
    STASHED CLEAN TREE (applicability/environment-probe/procedure-extraction/
    procedures/state e2e), i.e. pre-existing shared-instance drift, exactly
    what queue item 2's disposable-DB chain run must sort out; NOT a 2.7
    regression. tms-e2e fixtures self-clean by name prefix; zero rows left.*
6. `[x]` done @2026-08-25 â€” branch `lane/core-a` **Band 2.8 end-to-end
   replayability**: observations/claims/procedure-candidates regenerate
   deterministically from raw traces, extractor versions stamped.
   *(Shipped: db/26_replayability.sql â€” claim_sources join table closing the
   ONE broken provenance hop (claimâ†’observation), FKs both ways, reverse
   index, fresh-start compliant; app/execution/replay.py boundary â€”
   fingerprint/extractor_stamps registry pinned to the constants that
   govern each write path / regenerate_observations pure re-run /
   expected_claim_shape mirror of promotion / replay_session verifier over
   all three layers; promotion now writes claim_sources +
   properties.promoted_by="claim_promotion@1". SCOPED EDIT DISCLOSED:
   app/services/observations.py promote_observation_to_claim only (unowned
   file, writer-and-table-in-one-change per 1.9a's half-gate rule; ratify
   or revert-with-replacement). 15 offline proving tests + 2 live e2e in
   tests/test_band2_8_replayability.py: founding loop replays
   bit-identically twice from raw traces, tamper-detection teeth both
   layers, spec sentence "claimâ†extractor Xâ†trace E" proven by join.
   Suite: 1004 passed / 113 skipped / 0 failed (no env); WITH DATABASE_URL:
   1075 passed / 40 failed / 2 skipped â€” identical 40 to the 2.7 baseline
   drift set, zero new. db/26 applied to the shared instance (additive,
   idempotent) so the live proof could run.
   FINDINGS filed in Log: extract_procedure V0 gap (#4) + shared-instance
   migration state (queue-2 input).)*
7. `[x]` done @2026-08-25 — branch `lane/core-a` **Band 2.4 failure-classification
   pipeline**: outcomes classify into §36's causes via Evidence.failure_class;
   each cause routed to its mandated update.
   *(Shipped: db/27_failure_routing.sql — failure_routes [H] append-only log
   [evidence_id FK, class copy, route, typed payload, routed_by stamp;
   UNIQUE(evidence_id,route) idempotency; engine teeth incl. the §36 pairing
   rule as a CHECK — NULL class iff requires_review — plus db/24-style
   tombstone-only freeze trigger]; app/execution/failures.py boundary —
   FAILURE_ROUTES table verbatim per assignment [procedure_wrong→
   procedure_version_candidate; implementation_wrong→capability_demotion;
   environment_changed→dependency_queue; input_abnormal→applicability_narrowing;
   verification_wrong→plan_revision; external_failure→no_op], unclassified
   NULL→requires_review, false_reuse→applicability_narrowing as the ONE named
   judgment call [spec assigns it none; the reuse gate admitted a failure —
   narrow what may match; monkeypatch-retunable constant]; classify_failure /
   build_payload / record_routing / classify_and_route one-call API for the
   evidence writer + fetch_unrouted_failures sweeper / fetch_route_queue
   consumers. DESIGN: record-don't-execute — mandated updates live in other
   owners' code; CORE-A owes the durable auditable queue, handlers land with
   their owners [cross-lane request #1 extended: wire classify_and_route()
   next to the evidence INSERT]. 24 offline proving tests +
   schema-probe-gated e2e [classify/idempotence/queues/tamper-teeth/
   tombstone-cleanup] in tests/test_band2_4_failures.py. Suite: 1084 passed /
   114 skipped / 0 failed standard [includes CORE-B's latest landed files];
   e2e skips on shared instance BY PROBE — db/27 FKs evidence (db/24) which
   the drifted instance lacks; deliberately NOT piecemeal-applied there
   [queue item 2 owns the chain].)*
5. `[x]` done @2026-08-26 â€” branch `lane/core-a` **WAVE-2 / HARDENING H1 (assigned) -- Identity tables + tenancy predicate builder**
   *(Shipped: db/28_identity.sql â€” organizations/users/roles/org_memberships born
   additively, idempotent, fresh-start compliant; identity key is (issuer,
   external_subject) not bare sub; roles are catalog ROWS not an enum so adding a
   role never needs a migration; seeded Commons organization REUSES V0's default
   tenant uuid '00000000-...-0001' so every existing tenant_id value resolves to a
   real organization row without touching existing data. services/access.py gains
   the second axis: TenantScope + tenant_predicate() mirroring visibility's contract
   exactly [never-empty, unrestricted = visible literal TRUE, alias threading,
   positional param threading, ::uuid cast explicit in SQL text] + scope_predicates()
   building BOTH axes with sequenced params in one call [tenant arg deliberately has
   NO default â€” omitting it is a TypeError, not a silently unscoped query].
   services/authn.py EXTENDED additively [grant this wave; CORE-B's acquisition
   surface untouched, their 30 tests green]: Actor -> ensure_user [get-or-create,
   conflict-safe, IdentityInactive fails closed on deactivated/expired rows] ->
   resolve_memberships -> tenant_scope_for [0 memberships -> Commons posture, >1 ->
   AmbiguousTenant naming every candidate, never an implicit pick] -> one-call
   tenant_scope_for_actor seam for future deps resolvers. ADOPTED QUERY PATH within
   grant: execution/replay.py claims-layer read over knowledge_nodes now ALWAYS
   carries the fragment [TRUE when unrestricted]. Enforcement teeth: repo-wide
   hygiene scan test bans hand-written WHERE/AND tenant filters outside access.py;
   migration static checks; predicate unit proofs; replay SQL-content proofs. 33
   proving tests in tests/test_hardening_h1_identity_tenancy.py. Full suite: 1147
   passed / 114 skipped / 0 failed [= main baseline 1114 + 33, zero regressions].
   Remaining [V]/[H] query-path adoption outside grant paths -> Question #5 +
   cross-lane request #2.)*

7. `[ ]` **WAVE-3 / Debate panel OpenRouter wiring**: app/debate/panel.py + config.py (scoped grant) - add OpenRouter as a provider so scan->debate->approve runs locally on the founder key; reuse openrouter_arms backoff pattern; prove with offline FakePool tests + one gated live smoke.

6. `[x]` done @2026-08-26 — branch `lane/core-a` **WAVE-3 / HARDENING adoption
   sweep**: tenant_transaction() callers wired per cross-lane request #1
   (evidence-writer first) + unowned query-path sweep per request #2 /
   Question #5; hygiene scan green repo-wide.
   *(Shipped: db/30_verified_requires_evidence.sql — db/24's promised ENGINE
   trigger, landed in the SAME change as its writer per the half-gate rule:
   BEFORE UPDATE on procedures, arms ONLY on the transition INTO 'verified'
   (OLD IS DISTINCT FROM NEW), requires procedure_evidence_stats.
   independent_supporting_required >= 1 — independence-capped, view-based,
   sees the writer's same-transaction INSERT so gate+writer are atomic.
   services/procedures.py::record_execution_outcome REWIRED (cross-lane
   request #1 + its Band-2.4 extension, both landed): now the FIRST
   tenant_transaction() caller — app.tenant_id bound as first statement
   (default Commons; explicit scope threads through; unrestricted = plain-
   txn hatch), one outcome_to_evidence() execution_result row per outcome
   INSERTed BEFORE the counters UPDATE (db/30's trigger must count THIS
   run's evidence), failures routed via classify_and_route(conn, ...) IN
   THE SAME TRANSACTION — successes skipped by construction. Named judgment
   calls: successes synthesize explicit success_criteria from the call's own
   measurements when the caller passes none (#13 bans bare model-asserted
   success; criteria=None on failures is validate_evidence's contract);
   failure_class param added (None -> requires_review queue, honest); rows
   stamped created_by="record_execution_outcome@1", tenant_id bound to the
   active scope explicitly. execution/failures.py: record_routing/
   classify_and_route now accept a transaction-bound Connection alongside
   Pool (atomicity requirement; SQL unchanged, source-pin tests still green).
   SWEEP (request #2): scope_predicates() adopted at EVERY remaining
   visibility_predicate() site over TENANT-BEARING tables — graph_store.py
   (neighbors/traverse-incl-recursion/node_exists), retrieval.py (vector/
   lexical/hydrate/expansion), local_retrieval.py (structural/temporal
   legs), state.py (project_state + state_delta threading), dedup.py (base +
   aliased a/b pair query, params bound once), reuse_detection.py (vector+
   lexical), hierarchy.py (_fetch_roots + 3 public entry points),
   knowledge_conflict.py, claim_family.py (subject/blocking/hub-at-$4),
   api/graph.py (GraphStore + hydrate). Default posture everywhere =
   TenantScope.unrestricted() -> visible literal TRUE, zero new bindings,
   behavior byte-identical; every function gained an optional
   tenant_scope= param for the cutover. HONEST EXCLUSIONS: applicability.py
   (counts over `procedures`), agent_search.py (`agents`), observations.py
   (not in request list) — those tables carry NO tenant_id column today;
   adding one is schema work needing a founder ruling, disclosed here.
   api/graph endpoint resolves unrestricted internally (a dataclass kwarg on
   a GET route would be misread as a query param); becomes a resolved
   TenantScope when deps grow the seam. Tests: 22 offline proving tests in
   tests/test_wave3_tenancy_adoption.py [writer: setting-first binding,
   exact-version targets, criteria teeth, routing-in-transaction ordering
   evidence->route->counters, unclassified->requires_review, rollback
   atomicity, unknown-id writes nothing, db/30 static checks; sweep:
   dual-axis fragment + correctly sequenced params at ten sites incl.
   aliased/recursion forms]. One mechanical pin updated in CORE-B's
   test_claim_family_offline.py (unrestricted fragment is now the pair
   "(TRUE) AND (TRUE)" — semantics preserved, disclosed per file ownership).
   Full offline suite: 1229 passed / 114 skipped / 0 failed (= post-H2
   baseline 1207 + 22, zero regressions).)*

### Lane CORE-B â€” extraction & gating (owns `backend/app/services/procedure_extraction/**`, `invariants.py`, `applicability.py`, `precondition_gate.py`, `state.py`)
1. `[x] done 2026-08-25 â€” lane/core-b` **1.8a** Precondition relevance filter (derive gates only load-bearing facts).
2. `[x] done 2026-08-25 â€” lane/core-b` **1.8b** V6 authoring-time invariant validator + z3 off event loop w/ timeout.
3. `[x] done 2026-08-25 â€” lane/core-b` **1.8c** Memoized `project_state()` in applicability cascade; tenant-scoped
   cold-start gate.
4. `[x]` done 2026-08-25 â€” lane/core-b **NEXT WAVE â€” 1.9b capability computation**: levels-as-banded-P implementing
   the RATIFIED D1 thresholds (spec Â§16; routing tiers 0.90/0.70 as named config,
   never magic numbers); bidirectional demotion on failure. Proving tests:
   Appendix C #5/#10/#12.
   *(Shipped: procedure_extraction/capability.py â€” CapabilityScope rejects blank
   context fields [#5]; Wilson-lower-bound P estimate, D1 bands 0.50/0.70/0.85/0.95,
   per-level gates [independence groups â‰¥2 for L2, verification plan for L3,
   â‰¥2 envs holding successes for L4/L5, completed review for L5]; routing reads P
   only, never the label; trajectory API proves failure-drops-level-then-recovers
   [#10] and brand-metadata never enters computation [#12]. 25 tests in
   tests/test_capability_bands.py. Suite: 939 passed / 106 skipped / 0 failed
   (= origin/main baseline + CORE-A's 36 plan tests + these 25). Placement note +
   numbered question #2 in Log.)*
5. `[x] done 2026-08-25 â€” by integrator (commit 2505705)` **NEXT WAVE â€” 1.9c universal ChangeSet coverage**: every `[V]` mutation
   produces a ChangeSet record (extend `models/change.py` reach to observations,
   procedures, implementations, applicability rules, states). Proving test:
   Appendix C #7.
6. `[x]` done @2026-08-25 â€” lane/core-b **Band 2 â€” promote episode segmenter to production**
   in `backend/app/services/trace_worker.py`, using the empirically-validated rules from
   `experiments/episode_assembly/FINDINGS.md`: Rule-A genuine-prompt boundaries as the only
   primary signal; merge rule folding â‰¤2-event episodes into successors; subdivision of
   >200-event episodes at internal commit/test sub-boundaries (metadata role per findings â€”
   never a top-level cut); nested subagent episodes joined via `sourceToolAssistantUUID` â†’
   parent assistant `uuid` (sibling-file join); idle-gap signal DROPPED (bimodality does not
   exist â€” no temporal threshold anywhere).
   *(Shipped: trace_worker.py episode-assembly section â€” pure `assemble_episodes()`
   [reference prompt predicate verbatim incl. the four auto-continuation prefixes;
   O(n) merge sweep equivalent to fold-until-stable w/ trailing-trivial folding backward;
   completing commit/test event CLOSES its sub-episode; oversize-without-internal-signal
   stays whole and flagged `oversize_unsubdivided`, no invented cuts; forest-safe â€” file
   order across all parentUuid:null roots, never a chain walk; tolerant of all 16 line
   types + intra-file drift and missing timestamps]; `write_session_episodes()` into the
   EXISTING episodes table via migration-17 columns, NO new migration, idempotent by
   per-episode shape fingerprint in metadata JSONB [child links to existing parent row id
   after crash-between-inserts]; `process_transcript_session()` full path + sibling-file
   discovery. content_ref is a locator, never message text. Thresholds are named module
   constants consulted at call time, monkeypatch-proven retunable. 29 proving tests in
   tests/test_episode_segmentation.py vs synthetic real-schema fixtures [16 line types,
   timestampless boundary lines, two-root forest, torn writes]. Suite: 1018 passed /
   111 skipped / 0 failed (= origin/main baseline 989 + these 29, zero regressions).
   Integrator-approved scoped files for this item only: services/trace_worker.py +
   tests/test_episode_segmentation.py.)*
7. `[x]` done @2026-08-25 â€” lane/core-b **Band 2.6 â€” ClaimFamily resolver v0**: project-scoped blocking +
   proposition match per spec Â§10 (similarity is candidate generation, not
   identity); cross-project families deferred to Band 4. Entities managed:
   ClaimFamily `[V]`. New module `backend/app/services/claim_family.py` +
   proving tests.
   *(Shipped: pure decision core â€” normalized subject|predicate|object|type
   canonical-key as THE identity gate [fails closed on statement-only claims];
   dominant contradiction check [CONTRADICTS edges + negation flips are distinct
   at ANY similarity â€” the spec-10 tooth]; condition matching splits
   same_family vs related_family; conservative ontology-overlap ladder
   [generalizes/specializes only on strictly-nested token sets per Â§10's own
   hierarchy example, >=2-of-3 shared slots for related]. Blocking = hard
   project-scope-pair filter [nothing implicit-global; a global-scoped twin is
   NOT a v0 candidate], similarity only ranks/caps survivors [BLOCK_LIMIT named
   constant]. DB boundary rides EXISTING generic tables, NO migration:
   node_type='claim_family' hub [provenance='system_pending_review' +
   RESOLVER_VERSION extractor stamp per V0 derived-object rule] + OWNS/
   FAMILY_MEMBER membership edges, fully idempotent re-resolution;
   truth_state='OUT' claims neither anchor nor join families. Related/
   generalizes verdicts returned but NOT persisted in v0 â€” family-graph edges
   between hubs are Band 4's LSH wave. Outcome matching honestly absent: claims
   carry no outcome field yet. 27 offline proving tests in
   tests/test_claim_family_offline.py [max-similarity contradicted twin stays
   distinct; SQL-content proofs for hard scope terms/TMS exclusion/visibility
   fragment/LIMIT; write-behavior proofs for stamps/idempotency/no-winner-no-
   writes]. Suite: 1045 passed / 111 skipped / 0 failed (= prior lane baseline
   1018 + 27, zero regressions). Placement note mirrors Question #2: module sits
   at services/ top level under the founder's explicit scoped grant recorded in
   the claim entry above; relocation later is a one-line import change.)*
8. `[x]` done @2026-08-26 — lane/core-b **Band 2.9 — Identity gate**: OIDC-only authN — real identity before
   multi-user exposure (ROADMAP: single-tenant posture frozen in code until
   enabled, cannot silently slip to Band 5). Token validation middleware,
   actor_id propagation into Events/ChangeSets/Reviews. Scope minimal:
   validation + propagation ONLY, no user management UI.
   *(Shipped: services/authn.py — RS256-only OIDC validation vs JWKS [alg
   whitelist enforced BEFORE key material so none/HS256 confusion dies
   unexamined; iss/aud/exp/nbf/sub required; TTL-cached JWKS fetched OFF the
   event loop via asyncio.to_thread with one refresh on unknown kid for IdP
   rotation]; pure-ASGI actor middleware [same-task contextvar bracketing —
   deliberately NOT BaseHTTPMiddleware, whose downstream task-split makes
   attribution a gamble]. Teeth: a PRESENT-but-bad token is always 401 and
   never degrades to anonymous; missing token is anonymous only in public
   posture, 401 everywhere except /health+/docs once private visibility is on;
   assert_boot_posture refuses boot on multi_user_exposure_enabled or
   real_auth_enabled WITHOUT OIDC configured — every half-enabled posture has
   a named refusal. Propagation at all three surfaces, validated identity
   overriding self-asserted: ingest payload actor_id [events], ChangeSet
   author omission resolves from contextvar or raises "unattributed"
   [explicit authors incl procedures.py's quarantine timer untouched],
   agent_review_events.actor falls back to contextvar [scope-key callers
   unchanged]; get_scope prefers validated subject over X-Viewer-Id. Legacy
   deps.require_trustworthy_identity kept intact — test_access pins its
   message. No user management, no migrations; pyjwt[crypto] added to
   requirements.txt [installed in this worktree's venv]. HARDENING H1 seam:
   authn keeps identity ACQUISITION separate from tenancy FILTERING so H1's
   predicate builder can consume Actors uncoupled. 30 offline proving tests in
   tests/test_authn_offline.py [locally-generated RSA vs static JWKS,
   pure-ASGI harness, fake-pool SQL-content proofs of override + fallback +
   explicit-wins at each surface]. FINAL suite on rebased origin/main
   (incl. core-a 2.4): 1114 passed / 114 skipped /
   0 failed (zero regressions; an earlier 1090/113 reading predates the
   last main rebase). Rebased onto origin/main mid-item [board conflict resolved by taking
   main's board wholesale + re-inserting this item].)*
9. `[x]` done @2026-08-26 — lane/core-b **Band 2.4 completion — failure-route handlers wiring**
   (founder referral f00915a): consume app/execution/failures.py's
   fetch_route_queue / fetch_unrouted_failures (READ-import granted;
   failures.py itself stays untouched) and land the four mandated-update
   handlers: capability-demotion calls capability.py's demotion trajectory;
   applicability-narrowing edits procedure rule detail for input_abnormal;
   dependency-queue enqueuer flags derived claims for environment_changed;
   requires_review flagger stamps claim_status for unclassified. Proving
   tests: FakePool SQL capture, idempotent re-consume = one update,
   unrouted failures never trigger handlers.
   *(Shipped: services/procedure_extraction/failure_handlers.py —
   HANDLED_ROUTES = {capability_demotion, applicability_narrowing,
   dependency_queue, requires_review} + run_failure_handlers() dispatcher +
   unclassified_backlog() read-only visibility sweep [the ONLY consumer of
   fetch_unrouted_failures, provably never wired to any handler].
   IDEMPOTENCY LEDGER: every handler gates its write on ONE query —
   change_sets.reason = "failure_route:<route>:<fr_id>" — db/25 is [H]
   append-only so a fired mandate can never be erased; failure_routes rows
   stay untouched [no processed column exists; tombstone means RETRACTED,
   not executed]. Per-handler mandates: demotion recomputes the
   implementation's verdict from its cumulative evidence stream via
   compute_capability/capability_for_stream [same attempt discipline as
   db/24's procedure_evidence_stats view: direction='supports',
   execution_result|reproduction, t_invalid IS NULL] and records it as a
   ChangeSet status_change op on the TRIGGERING evidence row [no
   implementations table exists; capability storage stays CORE-A's deferred
   [D]]; narrowing appends ONE structured exclusion entry {key: context_key,
   values: [failed_context_key], _source_route_id, _failure_class} to
   procedures.exclusions — proven to disqualify through applicability._excluded
   UNCHANGED [ticket-12 machine-writable rule detail]; serves input_abnormal
   AND false_reuse [both route there]; skips non-procedure targets and
   contextless payloads honestly [no invented blank bans]; dependency_queue
   flags derived claims [claim_sources ← observations where
   properties->>'context_key' = failed_context_key — the one derivation
   index that exists; richer §20 indexing is Band 4] as
   claim_status='stale' + properties.revalidation marker [OUT/t_invalid
   claims skipped; zero dependents found leaves the mandate queued];
   requires_review stamps target claims claim_status='uncertain' +
   properties.review marker [prior status preserved in ChangeSet detail;
   non-claim targets skip — the routing row stays the human worklist].
   Every [V] mutation goes through changeset_record.record_change_set with
   author="failure_handlers@1" [invariant #7]. NO migration, NO edits
   outside owned paths, failures.py untouched [read-import only]. Named
   judgment calls documented in module header: ledger mechanism, dependents
   resolution, 'uncertain' stamp value, ChangeSet persistence shape. 17
   offline proving tests in tests/test_band2_4_handlers.py [FakePool SQL-
   content pins incl. stream filters/::uuid casts/DISTINCT OUT exclusion/
   LIMIT param; exact-update proofs per handler; idempotency for all four +
   dispatcher rerun; unrouted-sweep tripwire proves handlers can never see
   unclassified rows; claim_status vocabulary statically pinned to db/21's
   kn_claim_status_chk]. Full suite: **1185 passed / 114 skipped /
   0 failed** [= pre-existing baseline + 17, zero regressions].)*
Rule: NO new migrations (schema needs route through CORE-A); no edits outside owned paths.

0. `[x]` **WAVE-2 / HARDENING H3 pre-work swap** -- DONE @2026-08-26 by core-b under the HARDENING section item 3 (same task; canonical record there). Rate-limiter collector treatment: in-process token bucket + buffered ledger flush (trace_collector append->drain pattern) so Postgres becomes audit ledger, not enforcement point. CONSTRAINT: preserve fail-closed-on-infra-error; buffered writes need a replay-or-block rule. Retention sweep for rate_limit_events. NOTE: lands in governance.py -- scoped grant to this lane for backend/app/services/governance.py only.

### Lane MEASURE-WAVE (real arms - founder go 2026-08-26, key in backend/.env OPENROUTER_API_KEY)
0. `[x]` done @2026-08-26 — branch `lane/measure` **Real-model arms**: replace scripted_arms decision logic with live model calls via OpenRouter (OpenAI-compatible, key from env). REQUIRED: exponential backoff+jitter on 429 (upstream shared pool saturates - verified live); model fallback chain (ox-alpha primary; document alternates); resumable sweeps (--auto-resume pattern); spend log per run. Arm A = solo frontier call per step; B/C consume memory surface identically to scripted versions.
   *(Shipped: `openrouter_arms.py` + `run_real_arms.py` inside experiments/harness/** only. AgentAdapter contract preserved - episodes drop into the UNCHANGED scoring/scoreboard/micro_pack stack. DECISION CONTRACT: strict-JSON {resolved, reuse[], refuse[], notes}, one repair round-trip then invalid episode [resume retries it]; Arm A = situation-only solo call; B = same rag blob as scripted; C = SAME surface dance [search -> upfront gate consult -> cards] with model deciding reuse/refuse over OFFERED ids only, reuse credited ONLY after a fresh check_applicability verdict - model proposes, gate disposes; refusals recorded either way. GROUND-TRUTH-FREE AGENTS: `stale`/`rag=misleading`/`solo_outcome` never read by real arms [the scripted arms read them - that leak is what a real arm must not have]; attribution mechanical: reuse_caused_failure := leaned-on-memory-or-reuse AND unresolved. BACKOFF: full-jitter exponential ceiling BASE_S=1.5 doubling to CAP_S=60, injectable sleep/rng proven offline; network errors retry like 429s; non-retryable 4xx falls to next model immediately; exhaustion raises AllModelsFailedError with the per-attempt trail. CHAIN: DEFAULT_MODEL_CHAIN=(ox-alpha, openai/gpt-4o-mini, anthropic/claude-3-5-haiku) named + --models override. RESUME: existing results file refuses without --auto-resume [paid history never clobbered]; load_done skips valid all-arm rows, error AND unparseable rows retry; --max-tasks caps fresh spend. SPEND LOG: one JSONL row PER ATTEMPT beside results [successes carry usage/cost, failures carry status], totals print with scoreboard. FINDING fixed en route: scenario narratives leaked scripted verdicts into prompts [mic-dep-003 'Honest outcome: everyone falls back and fails'] - situation_text now strips everything from an 'Honest outcome:' marker; SOFTER framing hints remain in fixture prose, flagged below for ruling before headline data collection. LIVE SMOKE vs real endpoint PASSED end-to-end: mic-dep-003 all three arms valid, scoreboard+power footer rendered from real rows, 429 saturation observed and absorbed [3 of 6 attempts failed, chain held, $0.014 total]. Harness suite 163/163 green [126 prior + 37 new]. Zero backend edits.)*

### Lane MEASURE (owns `experiments/harness/**`)
1. `[x] done 2026-08-25 â€” lane/measure` Â§40 harness skeleton adapted from `experiments/swebench_pro/run_graph_experiment.py`;
   arms A/B/C; synthetic fixtures only until CORE-A lands 1.7.
2. `[x] done 2026-08-25 â€” lane/measure` Scoreboard script: pass-rate/cost/false-reuse/stale-refusal + power-analysis
3. [x] done @2026-08-25 â€” branch `lane/measure` **NEXT WAVE â€” micro-experiment pack** (founder mandate 2026-08-25): 8â€“12 tiny real-life scenarios as fixtures â€” adversarial refund-policy rule violations, dependency-conflict debug, PDF-to-sheet pipeline steps, env-drift staleness case â€” each run through the harness against the MCP surface; plus ingest this project's own Claude Code sessions as first real corpus. Output: per-scenario pass/fail + evidence-trail assertions. Doubles as the P4 dogfooding seed.
   footer (discordant pairs beside every p-value).
   *(Shipped: fixtures/micro/ = 11 scenarios [refund-policy violation Ã—3,
   dependency-conflict Ã—3, PDF-to-sheet pipeline Ã—3, env-drift staleness Ã—2]
   with arm-independent success criteria + per-scenario evidence_requirements;
   micro_pack.py grading/validation; run_micro_pack.py CLI printing
   per-scenario verdicts WITH failing requirement ids + scoreboard + power
   footer; mcp_surface.StubSurface now journals every tool call so evidence
   assertions check the trail, not self-report; session_corpus.py ingests
   ~/.claude/projects/**.jsonl into a LOCATOR-ONLY manifest â€” 2 sessions /
   11 real prompts on this machine, zero message text stored â€” flowing
   through all three arms as unscored dry-runs, the P4 dogfooding seed.
   SKELETON BUG FOUND+FIXED en route: SoloFrontierAgent cleared
   stale_offered for all arms via inheritance, so Â§40's stale-refusal
   denominator read "no offers" forever in real sweeps; B/C now keep the
   offer (fixtures' own contract), pinned by test at B 0/7 Â· C 6/7(missed 1)
    on the micro pack. Hand-derived verdict matrix asserted e2e: A 5/11,
    B 6/11 (3 false-reuse), C 9/11 (1 false-reuse = the deliberate poisoned-
    gate honest-negative). Harness suite 78/78 green.)*
4. `[x]` done @2026-08-26 â€” branch `lane/measure` **Band 3 prep â€”
   extraction error-floor instrument**.
   *(Shipped: `fixtures/error_floor/` = 42 hand-gold trace excerpts in four
   themed files [files/commands/semantic/negatives] covering all five
   observation types, each excerpt carrying a notes defense; ambiguous
   readings excluded by authorship rule. `_rubric.md` IS the grading
   contract â€” typed canonical-key equality after documented normalization
   [path separators, whitespace collapse], token-Jaccard >= 0.5 for
   free-text semantic labels, one-to-one greedy matching in gold order,
   reason codes on every FP/FN [type_mismatch_vs_gold /
   type_mismatch_in_predictions / key_mismatch_same_type /
   missing_no_candidate / extra_no_gold]. `error_floor.py` pure grader +
   fixture-validation teeth [closed type set, unique ef- ids, notes
   mandatory, 30â€“50 count band, per-type coverage]. Scoreboard wiring:
   `scoreboard.format_error_floor()` prints overall + per-type rates WITH
   numerator/denominator [zero-denominator renders '-', never a fake
   0.0/1.0], appendable to any Â§40 run via `--error-floor-results`;
   `run_error_floor.py` CLI takes `--adapter module:function` resolved in
   the CALLER's environment [harness never imports backend/** â€” lane
   rule] or `--predictions` JSONL from an out-of-process extractor run;
   exit 1 iff the adapter errored anywhere â€” a partial run never
   masquerades as a floor. `demo_extractor.py` mirrors deterministic_v1's
   published rules INCLUDING quirks so the instrument reads non-trivially
   fully offline. LANDING BASELINE [demo mirror]: P 22/25 (0.880) Â·
   R 22/30 (0.733) Â· F1 0.800 over golds=30/preds=25 â five known v1
   quirks visible AS NUMBERS [compound `cd x && git commit` + flagged
   `git -c â€¦ commit` mislabeled command_executed; `pip install pytest-cov`
   substring false test_run; NotebookEdit whitelist miss; semantic layer
   absent Ã—4 FN]; hand-derived confusion matrix asserted e2e EXACTLY,
   incl. reason codes per discrepancy. Design note carried to Band 3: the
   model extractor's NONE contract governs only the SEMANTIC layer â
   `ls src` keeps its mechanical command_executed gold while warranting no
   label [excerpts carry both layers wherever both are agreed].
   Harness suite 126/126 green [78 prior + 48 new]. Zero backend edits;
   no model calls this session.)*

### Lane RESEARCH (owns `.scratch/research/**`, updates to `RESEARCH_INTEGRATION_PLAN.md`)
Tooling: `research_exa.py` at repo root (key lives in `backend/.env` as EXA_API_KEY â€”
never committed). Protocol per founder: market/vendor/pain-point evidence via Exa web
search; technical credibility checks via arXiv / Semantic Scholar / OpenAlex (webfetch);
single synthesized reports into `.scratch/research/`.
1. `[x]` done @2026-08-25 â€” research lane (this worktree)
   Execute open verification tickets in RESEARCH_INTEGRATION_PLAN.md
   (P-M3 leaderboard movement Â· P-B1 GATS/WorldEvolver/EnvACE numbers Â· P-C1 FedWorld
   mechanics Â· P-I1 Molt/ToolVerse/MobileRL maturity).
   *(All four verified/answered; reports in `.scratch/research/p-*.md`; log appended to
   RESEARCH_INTEGRATION_PLAN.md. GATS citation corrected â€” 23.9% is stress-test-only.)*
2. `[x] done @2026-08-25 - research lane (file: competitive-sweep-mem0-letta-zep-hipporag-awm.md; tick missed before session died)` Competitive sweep: Mem0 / Letta / Zep-Graphiti / HippoRAG / AWM â€” what they
   ship vs our trust spine; file deltas as board notes.
3. `[ ]` Ï„-Knowledge ceiling re-check (arXiv:2603.04370) before harness baselines freeze.
4. `[x]` done @2026-08-26 — RUN #1 independent verification complete; report at
   `.scratch/research/run1-verification.md` (+ script `run1_verify.py`, competitive
   addendum in the sweep file).
   *(VERDICTS: spend \$0.2659/95 calls CONFIRMED exactly; marginals A 6/10, B 7/10+1FR,
   C 7/10 CONFIRMED under per-arm-valid denominators [different task sets per arm —
   shipped scoreboard default prints 9-task frame A5/B6/C6]. Stale-refusal "C 6/6 vs
   B 0/6, p≈0.031" REPRODUCES ARITHMETICALLY (p=0.03125) but only under an
   invalid-counts-as-non-refusal frame that charges B's unparseable mic-pdf-003 decision
   as a missed refusal; validity-restricted frames give 5 pairs, p=0.0625 > α — report
   p∈[0.031,0.062] or drop the inferential claim. BIGGER FINDING: C's journal proves ALL
   SIX correct stale refusals were recorded mechanically by the StubSurface gate before
   the model saw any card [mcp_surface verdict = not stale, deterministic]; on the ONE
   model-decides task [refund-003 poisoned gate] real-C returned unparseable, and arm B's
   prompt contains no procedure ids so it structurally cannot refuse. The 6-vs-0 outcome
   is fixture-determined surface design, not model behavior — no inferential reading
   survives, and it is NOT evidence ox-alpha detects staleness. Competitive refresh: NO
   published system reports decision-time stale-procedure-refusal as a metric, but STALE
   arXiv:2605.06527 / TEPA arXiv:2608.07429 / Library-Drift+Ratchet arXiv:2605.19576 +
   2605.22148 / AFTER arXiv:2606.23127 converge from adjacent directions — cite STALE's
   Premise Resistance in any novelty claim; URLs in the report.)*

### Lane SHIP (owns `packaging/**`) â€” activates after CORE-A merges 1.7
2. `[x]` done @2026-08-26 â€” branch `lane/ship` **P2 - Minimal status surface**:
   single-page read-only view served from packaging/, listing episodes -> claims ->
   procedures with capability scores and evidence-trail links.
   *(Shipped: `packaging/src/stealthlab_connect/status_page.html` — THE one file of
   HTML/JS/CSS [expandable cards, capability badges `L<level> <label> · P̂=<Wilson
   lower bound>`, routing verdicts, claim provenance chains, lazy evidence-trail
   tables, graph deep-links] + `status_server.py` — a tiny FastAPI app [`/`,
   `/health`, `/api/meta`, `/api/overview?limit=`, `/api/evidence/{claim|procedure}/{id}`]
   + `status_entry.py` console script `stealthlab-status-page` [loopback 8766
   default]. READ-ONLY both senses: every SQL statement is a SELECT; backend code
   is imported as shipped, zero re-implementation — access.py builders for all
   scoping [scope_predicates on the tenant-bearing core tables knowledge_nodes/
   task_nodes/episodes; visibility_predicate alone on procedures/evidence/
   observations, which have NO tenant_id column — the live run caught me assuming
   otherwise], authn.py's install_actor_middleware + assert_boot_posture +
   deps.get_scope wired exactly like app/main.py, pool via db/session.create_pool,
   and capability scores computed by CORE-B's REAL engine
   procedure_extraction/capability.py::compute_capability over each procedure's
   outcome evidence [same population as procedure_evidence_stats: supports-direction
   execution_result/reproduction]; verification-plan/completed-review gates reported
   unclaimed until anything stores them, so trust tiers above reproduced cannot
   appear from statistics alone. Pre-migration-24 databases [the documented shared-
   instance drift] degrade honestly — named banner + level-0 scores, never fake
   numbers, other DB errors still raise loudly. 27 offline tests in
   tests/test_status_offline.py + test_status_capability_offline.py: FakePool SQL-
   content proofs [SELECT-only teeth, builder fragments present per table class,
   parameterized LIMITs, X-Viewer-Id threading], hand-computed Wilson expectations
   vs the real engine [n=1 s=1 -> 0.2065/L1/refuse; 95/100 3-group 2-env -> L4/
   offer; single-env cap -> L2; ungrouped perfect record -> L1 despite auto-route
   P; review gate blocks L5], drift degradation, entry preflight. LIVE SMOKE against
   the real shared DATABASE_URL passed [meta/overview/trail/400 paths]. Packaging
   suite: 55 passed [= 28 prior + 27 new]. Backend diff vs origin/main: ZERO files.)
   Note: no new auth surface — loopback bind, no write endpoints exist to gate.*

3. `[x]` done @2026-08-26 — branch `lane/ship` **P5 groundwork - public
   scoreboard generator**: generator in packaging/ reading
   experiments/harness/real_arms_results.jsonl + real_spend.jsonl and emitting
   a static scoreboard page (markdown + HTML) with the power-analysis footer -
   discordant pairs beside every p-value, spend line, generated-timestamp.
   Offline tests for the transformation logic. No backend edits.
   *(Shipped: `packaging/src/stealthlab_connect/scoreboard_gen.py` — CLI
   console script `stealthlab-public-board` [pyproject] producing static
   `public_scoreboard.md` + `.html`. ZERO re-implementation: classification,
   arm aggregation, exact-McNemar p-values/power analysis and spend
   aggregation are imported from experiments/harness as shipped
   [scoring.py / scoreboard.py / mcnemar_power.py / openrouter_arms.SpendLog;
   stdlib-only chain], so the public page cannot drift from the terminal
   scoreboard. Harness-root discovery mirrors _bootstrap.py: --harness-root >
   $STEALTHLAB_HARNESS_ROOT > walk-from-package/cwd, loud failure naming
   candidates. STRUCTURAL GUARANTEES, each test-pinned: comparison lines are
   rendered ONLY by mcnemar_power.format_pair whose signature makes a bare
   p-value unrepresentable [test asserts every exact-p= line on BOTH pages
   carries "discordant pairs" — 9 renderings/pair-set: bullets + footer +
   embedded canonical terminal block]; POWER-ANALYSIS FOOTER section on both
   pages; SPEND section via SpendLog.summarize/render semantics verbatim +
   429 count + per-arm billed cost; missing ledger renders an honest-absence
   note, never a fabricated $0.0000 run; Generated UTC timestamp on both +
   meta tag + source-file provenance w/ row counts; unusable tasks counted &
   disclosed in a caveat, never silently dropped; comparisons under
   MIN_PUBLIC_DISCORDANT_N=6 [RUN #1's k>=6 public-phrasing floor, codified
   as a named constant] carry a small-n caveat blocking headline phrasing.
   Spend-ledger resolution order matches the wild: <results stem>_spend.jsonl
   [run_real_arms code default] > real_arms_spend.jsonl [harness .gitignore]
   > real_spend.jsonl [RUN #1 log name]. CLI refuses exit-2 on missing/empty
   results — no page from absent data. HTML escapes all model-controlled
   text. 23 offline proving tests in
   packaging/tests/test_public_board_offline.py [hand-computed matrices incl.
   an 11-task scenario proving exact p=0.01171875/0.001953125, torn-line
   tolerance, error-row disclosure, spend math vs runner semantics, escaping,
   CLI e2e + refusal paths]. Packaging suite: **80 passed / 0 failed** [= 57
   prior + 23 new, zero regressions]. Backend diff vs origin/main: ZERO
   files. Live smoke on synthetic sweep data rendered all guarantees; real
   RUN #1 artifacts are machine-local to their worktree [gitignored] so the
   generator ships proven against the formats, first real regeneration is
   one command wherever the ledger lives.)*

1. `[x]` done @2026-08-25 â€” branch `lane/ship` Installable package wrapping
   `trace_collector` + `mcp_server`.
   Shipped: `packaging/` = installable **stealthlab-connect** (pyproject,
   console scripts `stealthlab-mcp-server` [HTTP loopback default / --stdio]
   and `stealthlab-trace-hook` [Claude Code hook CLI, always exit-0 fail-safe]),
   import-only wrapping of backend (no vendored logic, no backend edits, no
   migrations). Backend-root discovery w/ loud explicit-path failures; loads
   backend/.env; preflight for STEALTHLAB_MCP_TOKEN. 30 offline tests (no DB):
   bootstrap resolution, collector round-trip/redaction/dedup/drop-count,
   hook CLI fail-safes, server module imported DB-less (8-tool roster, token
   verifier). README: install + 3 smoke tests. Console scripts verified in a
   bare scratch venv (found+fixed a real order-dependent bug there:
   collect_payload wasn't bootstrapping the path). Suite delta vs pristine
   origin/main = ZERO: identical 103 failed / 974 passed / 1 skipped on both â€”
   same pre-existing fresh-worktree gap MEASURE logged (no local Postgres;
   e2e failures only under full-run ordering; they skip cleanly when run
   individually). Not a SHIP regression. Rebased onto origin/main before push
   per OVERNIGHT MODE.

## Integrator (= reviewer instance, main checkout)
- Watches for `lane/*` branch pushes; rebases lane onto origin/main when stale.
- Runs full suite on the merge candidate; merges green, rejects red with notes here.
- Sole writer of ROADMAP.md checkbox updates and review files.

## Shared rules

**OVERNIGHT MODE (2026-08-24 night): no integrator on duty.** Lanes may push their
`lane/*` branch and then fast-forward main themselves (`git fetch origin; git rebase
origin/main; git push origin HEAD:main`) ONLY after the full offline suite passes in
their worktree. File ownership is the safety net. No force-pushes, no destructive git
commands, no edits outside owned paths ever. On ambiguity: stop, leave a numbered
blocking question in the Log, continue with the next queue item.

- **Claims**: claim your task line (`- [ ] claimed @ts â€” name`) before starting; mark
  `[x] done @ts â€” branch` after. One claimant per task.
- **Branches**: lanes commit to their `lane/*` branch only; rebase onto origin/main
  before signaling done. Never push to main directly from a worktree. Never force-push.
- **Commit prefix**: `core-a:` / `core-b:` / `measure:` / `research:` / `ship:`.
- **Migrations**: CORE-A exclusively. Others needing schema â†’ request below.
- **Docs**: spec v4 / schema.md frozen post-Band-0 (board notes only).
  BAND0_DECISIONS.md founder-owned. ROADMAP checkboxes = integrator.
- **Session end**: merged/claimed state updated here, or blocking question in Log with
  numbered options + proposed default.

## Cross-lane requests

1. **CORE-A â†’ whoever owns/next touches `backend/app/services/procedures.py`**
   (unowned file â€” outside every lane's path list, hence this request instead of an
   edit): wire evidence-row writes into `record_execution_outcome()`'s transaction
   (one `outcome_to_evidence(...)` + INSERT per outcome, fields documented in
   app/execution/evidence.py), then land db/24's verified-requires-evidence engine
   trigger in the same change. Until then the JSONB counters remain the working
   promotion path and Appendix C #3 is proven at contract level only. Proposed
   default: assign services/procedures.py to CORE-A next wave (it is procedure-
   lifecycle storage, squarely CORE-A's "storage & plans" charter).
    **EXTENDED by Band 2.4:** in that same change, failures get routed too — call
    `app/execution/failures.py::classify_and_route(pool, evidence_row)` for every
    failure row right after its INSERT (one-call API, idempotent); successes raise
    NotClassifiable and must be skipped by the caller. Consumers then read their
    queues via `fetch_route_queue(pool, route)` — capability demotion consumers,
    applicability narrowing, plan revision each land with their owner; until then
    decisions stay durably queued and auditable in failure_routes.
2. **CORE-A â†’ owners of every remaining [V]/[H] query path over tenant-bearing
   tables** (`app/db/graph_store.py`, `services/retrieval.py`,
   `services/local_retrieval.py`, `services/state.py`, `services/dedup.py`,
   `services/reuse_detection.py`, `services/hierarchy.py`,
   `services/knowledge_conflict.py`, `services/claim_family.py`,
   `services/applicability.py`, `services/agent_search.py`, `api/graph.py` â€” all
   outside every lane's granted path list): thread tenancy through
   `access.scope_predicates(access_scope, tenant_scope, alias, param_index)`
   exactly where `visibility_predicate()` is already AND-ed in â€” one call
   supplies both fragments with correctly sequenced params; pass
   `TenantScope.unrestricted()` to keep today's permissive-in-effect posture
   visible as literal TRUE rather than absent. The hygiene scan test
   (test_hardening_h1_identity_tenancy.py) bans hand-written `tenant_id =`
   filters repo-wide from now on, so adoption must go through the builder.
   Proposed default: founder grants CORE-A a scoped next-wave sweep of these
   files so the mechanical change lands uniformly from the pattern's owner
   (Question #5 below); alternative is per-owner adoption via this request.
## Founder dependencies (blocking nothing currently)

| Ruling | Blocks | State |
|---|---|---|
| D1 capability bands | nothing (default live in Â§16, tagged) | open |
| D4 deletion mechanism | Band 5.6 only | open |

## Log

- Board rewritten for worktree multi-lane mode (4 lanes + integrator).
- 2026-08-25 research lane: queue items 2 (competitive sweep â†’ `.scratch/research/competitive-sweep-mem0-letta-zep-hipporag-awm.md`) and 3 (Ï„-Knowledge re-check â†’ `.scratch/research/tau-knowledge-ceiling-recheck.md`) also done same session.
- **Blocking question #1 (non-blocking for current work):** `EXA_API_KEY` is not present in any worktree â€” `backend/.env` is gitignored so it never propagated from the original checkout. Options: (a) founder pastes key into each worktree's `backend/.env` (proposed default), (b) lane falls back to built-in websearch permanently (worked fine today), (c) commit a template only.
- MEASURE (2026-08-25): harness skeleton + scoreboard landed on `lane/measure`
  (43/43 harness tests green; smoke sweep of all 10 fixture tasks passes
  end-to-end offline). Backend suite delta vs pristine origin/main = ZERO:
  identical 103 failed / 851 passed / 1 skipped on both â€” pre-existing gap in
  fresh worktrees (backend/.env absent: STEALTHLAB_MCP_TOKEN collection
  errors when unset; trace_ingestion e2e failures under full-run ordering).
  Not a MEASURE regression; needs an integrator decision on worktree env setup.
  Rebased onto origin/main before push per OVERNIGHT MODE.
- CORE-B (2026-08-25): all three queue items done on `lane/core-b`, one commit.
  - **1.8a** `derive.filter_load_bearing_claims` + `load_bearing_predicates`:
    gates only on behaviorally load-bearing claims (ran tests / invoked pkg
    manager / built / served / touched source), intersected with the probe
    vocabulary; `has_framework` honestly never gated (no deterministic
    signal). E2E contract updated from "equals project_state" to "grounded
    load-bearing subset"; supersession test given build-command evidence so
    it stays non-vacuous.
  - **1.8b** `invariants.authoring_problems` (parse whitelist + per-expr
    satisfiability) behind new validators rule **V6**; `ExtractedProcedure.
    invariants` field now persists via capture_procedure's existing param;
    every z3 Solver bounded by `DEFAULT_SOLVER_TIMEOUT_MS=5000`, `unknown`
    â†’ `undecidable` (never violation); retrieval path moved off the event
    loop via `check_invariants_async`/`asyncio.to_thread`.
  - **1.8c** cold-start gate counts through `visibility_predicate()`
    (`access_scope` param, default unrestricted preserves old behavior);
    per-cascade `_state_cache` dedupes `project_state()` fetches across
    candidates AND repeat subjects within one procedure; `as_of` pinned per
    cascade so the memo key is stable. Offline proof: fake-pool call-count +
    SQL-content tests in `tests/test_applicability_cascade_offline.py`.
  - Suite in this worktree (has backend/.env, unlike MEASURE's): baseline
    **849 passed / 106 skipped / 0 failed** â†’ after **878 passed / 106
    skipped / 0 failed** (+29 proving tests, zero regressions). Rebased onto
    origin/main before push per OVERNIGHT MODE.
- Board hygiene note (CORE-B): origin/main's board file carried a stray
  conflict marker (`>>>>>>> 35670e7 ...`) at the end of this Log â€” leftover
  debris from the measure commit itself. Removed here; integrator please
  sanity-check future board merges.
- CORE-B (2026-08-25, second wave): **1.9b capability computation** done on
  `lane/core-b`. Pure module `procedure_extraction/capability.py` â€” no DB, no
  migration, no edits outside owned paths; nothing existing changes behavior
  until a caller adopts it (procedures.py's ticket-13 SPRT lifecycle stays
  authoritative; wiring P into retrieval call sites is deliberately a separate
  change). Named config only: ROUTE_AUTO_THRESHOLD/ROUTE_OFFER_THRESHOLD and all
  four band boundaries + gate minima are module constants, proven retunable by
  monkeypatch tests (an inlined literal would fail them).
  - **Question #2 (non-blocking, placement):** capability.py lives under
    `procedure_extraction/` because that is this lane's only wholesale-owned
    path (`**`); a new top-level `services/capability.py` would be outside
    every owned path and file ownership is absolute. Options: (a) keep as-is
    until Band-2 routing integration (proposed default â€” relocation is a
    one-line import change), (b) integrator grants CORE-B
    `services/capability.py` and relocates in a follow-up, (c) fold into
    applicability.py (rejected by me: bloats a focused cascade module).
  - Suite in this worktree: **939 passed / 106 skipped / 0 failed**
    (+25 proving tests vs the post-CORE-A baseline; zero regressions).
    Rebased onto origin/main before push per OVERNIGHT MODE.
  - **Question #3 (BLOCKS queue item 5 / 1.9c):** that item's own text says
    "extend `models/change.py` reach", but `backend/app/models/**` is CORE-A
    property and file ownership is absolute â€” as written, 1.9c cannot be
    started by this lane without guaranteed revert. Options: (a) reassign
    1.9c to CORE-A alongside its models/db ownership (proposed default),
**RESOLVED by integrator 2026-08-25: CORE-B is granted a scoped exception for ackend/app/models/change.py alone (1.9c only, this wave). models/** remains CORE-A otherwise. Proceed with 1.9c.**
    (b) grant CORE-B an explicit exception path list for the change-set
    coverage work (models/change.py + the [V] mutation service files),
    (c) split: CORE-A extends the model, CORE-B writes the Appendix C #7
    proving tests against it from owned test files. CORE-B idle on new
    items until answered; no further queue entries exist for this lane.
- CORE-A (2026-08-25): queue items 3 (sweep) and 4 (1.9a evidence) done on
  `lane/core-a`.
  - **Band 1 exit-criteria sweep** (item 3): zero scope-less writes accepted â€”
    V0 gate live since 1.2/1.3, tests green. V1â€“V6 green on every ingested row â€”
    V6 shipped via CORE-B's 1.8b. Plan persistence end-to-end â€” proven at
    contract level by 1.7 (36 tests); REAL end-to-end still gated on queue 2
    (disposable-Postgres chain run). Replay determinism â€” rebind determinism
    pinned by test_band1_7_plans (#1/#17); full raw-trace regeneration is Band
    2.8's own item per ROADMAP. Verdict: every criterion that can go green
    without a live DB is green; Band 1 formally exits when queue 2 runs.
    Integrator review requested.
  - **1.9a** as detailed in the queue item above: evidence substrate + views +
    boundary + 33 proving tests, Appendix C #3/#12/#13 covered (the #12
    capability-trajectory half belongs to CORE-B's 1.9b, which now inherits a
    brand-blind, independence-aware stats view to consume).
  - Suite in this worktree: **947 passed / 106 skipped / 0 failed**
    (main baseline 914 + 33 new, zero regressions). No backend/.env here, so
    live-DB e2e tests skip â€” same env caveat MEASURE recorded; queue 2 remains
    the real-DB gate.
  -    Cross-lane request #1 filed above (services/procedures.py wiring).

- CORE-A (2026-08-25, third wave): **Band 2.7 TMS readability** done on
  `lane/core-a` â€” first Band 2 item; ROADMAP exit criterion "OUT/stale claims
  provably absent from retrieval results" is now proven by test at four
  retrieval surfaces plus the write-side history guarantee. Details in queue
  item 5 above. Integrator notes: (a) this worktree's backend/.env appeared
  since the earlier "absent" log entries â€” with DATABASE_URL exported the
  live-DB suites run here, and my 5 new e2e proofs pass live; (b) 40
  pre-existing failures across six OTHER e2e files reproduce identically on a
  clean checkout of origin/main against the same shared instance â€” recorded in
  queue item 5 as input to queue item 2's disposable-DB chain verification;
  (c) experiments/swebench_pro/graph_memory.py consumes HybridRetriever and
  inherits the fix unchanged (YC plan Step 2.3 satisfied for both named sites);
  (d) knowledge_conflict.py's pair-scan still considers OUT claims â€” that is
  conflict DETECTION over history, not retrieval-for-context, left untouched
  deliberately.

- CORE-A (2026-08-25, fourth wave): **Band 2.8 replayability** done â€” see
  queue item 6. Two findings for the integrator/other lanes:
  - **Question #4 (non-blocking for 2.8, blocks the founding loop's last
    hop):** `extract_procedure()` (CORE-B path) calls `capture_procedure()`
    without `provenance` or `scope_type`/`scope_entity_id`, but Band 1.3's V0
    gate rejects both when absent â€” on any FULLY migrated DB,
    extract_procedure raises V0Violation at persist time. Its capstone e2e
    passes today only where migration 21 is missing (the failure surfaces
    earlier, as UndefinedTable/UndefinedColumn). Static read of both files;
    not exercised end-to-end anywhere green. Options: (a) route to CORE-B to
    pass provenance="public_generated" + scope through extract_procedure
    (proposed default â€” their owned pipeline), (b) CORE-A takes it with the
    procedures.py storage boundary, (c) relax capture_procedure defaults
    (rejected: reopens the Band 1.3 gate).
  - **Shared-instance drift inventory (queue-2 input):** the long-lived dev
    DATABASE_URL has migrations through ~18 only: NO procedure_extractors
    (20), no scope columns on procedures (21), hence no executions/evidence/
    changesets either. db/26 applied by me (additive, idempotent). The 40-fail
    env'd baseline is fully explained by this; nothing newer than 18 should be
    assumed present in any e2e until queue 2 runs the chain on a clean DB.
  - Candidate-replay e2e schema-probes and SKIPs (documented reason) on the
    drifted instance instead of adding red; it asserts full three-layer
    equality wherever 20â€“22 are properly applied (CI / post-queue-2 DB).
- MEASURE (2026-08-25, second wave): micro-experiment pack done on
  `lane/measure` (details in queue item 3). Two env notes for the
  integrator: (a) THIS worktree now HAS backend/.env (contradicts the older
  "absent" entries) â€” with it present and no DATABASE_URL exported, the
  standard backend run here is 108 failed / 1020 passed / 1 skipped; a
  pristine origin/main (17fd338) checkout sharing this venv and .env fails
  IDENTICALLY in class (110 failed / 1062 passed â€” the +42 passes are
  exactly core-b's 27 ClaimFamily + core-a's 15 replay tests that postdate
  this lane's base). MEASURE's diff touches zero backend files, so delta =
  zero; the failure set is the same fresh-worktree/env gap recorded last
  session, now aggravated by .env presence turning skip-into-run for
  token-gated e2e. Needs the integrator's disposable-DB decision, not a
  measure fix. (b) Rebased onto origin/main before push per OVERNIGHT MODE.

- CORE-A (2026-08-25, fifth wave): **Band 2.4 failure-classification pipeline**
  done — see queue item 7. Notes: (a) false_reuse's route is the one value
  neither §36's routing paragraph nor the assignment names — routed to
  applicability_narrowing as FALSE_REUSE_ROUTE, a named monkeypatch-retunable
  constant with the reasoning in its docstring; a future founder ruling
  changes one line. (b) The routing layer is record-don't-execute BY DESIGN:
  db/27 is the durable auditable idempotent queue; executing procedure
  revisions / narrowing / plan revisions belongs to extraction / applicability /
  plans owners respectively — cross-lane request #1 extended with the exact
  one-call wiring (`classify_and_route`) for whoever lands the evidence writer.
   (c) db/27 NOT applied to the shared instance: it FKs evidence (db/24) which
   is absent there; piecemeal-applying 24+27 would deepen exactly the drift
   queue item 2 exists to sort. E2E probes and skips with that reason.

- CORE-A (2026-08-26, sixth wave): **HARDENING H1 â€” identity tables + tenancy
  predicate builder** done on `lane/core-a` â€” see queue item 5 / HARDENING item 1.
  Notes: (a) db/28 is additive + idempotent and NOT yet applied to the shared
  drifted instance â€” H1's proofs are all offline per house style, so nothing
  needed a live DB; applying it is safe whenever convenient but queue item 2's
  chain run should remain the canonical application point. (b) The authn.py
  change is strictly APPENDED (new tenancy-resolution section); CORE-B's token-
  acquisition surface is untouched and their test_authn_offline.py stays green
  inside the full suite. (c) A repo-wide hygiene scan test now bans hand-written
  `WHERE/AND ... tenant_id =` filters outside services/access.py â€” tenancy may
  only enter SQL through the builder from this commit forward.
  - **Question #5 (non-blocking for H1 itself, blocks FULL retirement of
    decorative tenancy):** H1's grant covered access.py + authn.py (+ my usual
    paths), so the builder is adopted at ONE real query path (replay.py's
    knowledge_nodes claims read, owned by me). The remaining [V]/[H] query
    paths over tenant-bearing tables live in files NO lane owns
    (graph_store.py + the retrieval/state/dedup/reuse/hierarchy/conflict/
    claim_family/applicability/agent_search family + api/graph.py) â€”
    enumerated in cross-lane request #2. Options: (a) founder grants CORE-A a
    scoped next-wave sweep of those files so the uniform mechanical adoption
    lands from the pattern's owner (proposed default; mirrors how the V2
    visibility threading was one dedicated wave), (b) each future owner adopts
    via cross-lane request #2 as they touch each file (risk: half-adopted state
    persists for months), (c) defer enforcement entirely to H2's RLS/SET LOCAL
    (rejected by H2's own constraint: app-layer stays PRIMARY, single policy
    source). Until answered, every new query path is still forced through the
    builder by the hygiene tooth â only the EXISTING unowned paths predate it.
- CORE-B (2026-08-26): **HARDENING H3** done on `lane/core-b` — see the
  HARDENING section item 3 for the full record. Two notes:
  1. The proving suite caught two real implementation bugs pre-ship
     (ledger rows written in wrong parameter order; degraded-retry
     throttle defeated by the reachability clause hammering a down
     Postgres on every denial) — both fixed and regression-pinned.
     Recording them because that's the SQL-content/FakePool discipline
     paying for itself.
  2. **Note #5 (non-blocking):** `backend/integration_check_v2_governance.py`
     (unowned root script, not pytest-collected) asserts real-DB
     row-counts immediately after check_and_record — true under V2's
     write-through, stale under H3's buffered ledger. Nobody owns it.
     Options: (a) its next user adds an explicit drain/flush call before
     asserting (proposed default — one line), (b) assign it to whoever
     picks up HARDENING follow-ups, (c) leave as-is; it fails loudly,
     not misleadingly. Enforcement semantics in api/deps.py are
     UNCHANGED at the call site — verified by reading deps.py; no edit
      was needed or made there.
- MEASURE (2026-08-26): error-floor instrument landed on `lane/measure`
  and fast-forwarded to main per OVERNIGHT MODE (commit f18313c; full
  record in queue item 4). One observation for the next full-suite runner,
  NOT a MEASURE regression (my diff touches zero backend files â verified
  `git diff origin/main -- backend/` empty): post-rebase backend run here
  = **1167 passed / 114 skipped / 1 failed**, the failure being
  `test_authn_offline.py::test_tampered_signature_rejected` under FULL-RUN
   ordering only; it PASSES in isolation. Ordering-sensitive crypto test on
   main's baseline â€” H1 owners may want to pin it (e.g. clear module-level
   JWKS/key cache state) before someone burns a merge cycle on it.
- MEASURE (2026-08-26, second wave): MEASURE-WAVE item 0 (real-model arms)
- **RUN #1 LOGGED (2026-08-26, integrator)**: first genuine SS40 sweep on ox-alpha via openrouter_arms (95 calls, \$ .2659, auto-resume ledger 
eal_spend.jsonl). Results on 10 valid tasks: A 6/10 resolved, B 7/10 w/ 1 false-reuse, C 7/10 w/ 0. **Stale-refusal C 6/6 vs B 0/6 -> McNemar p~0.031** (at the k>=6 significance floor - more n required before any public phrasing). Resolution B-vs-C not significant (1-1 discordant: C lost poisoned-gate refund-003 by design, gained pdf-003 adversarial blend). Universal failures dep-003/env-001 = library coverage gaps feeding extraction targets. Repeat sweeps queued (fresh --out per run); Question #6 verdict-leak framing audit assigned to integrator.
- **RUN #1 CORRECTION (2026-08-26, post independent verification - research lane ee2913f)**: (1) headline p is FRAME-DEPENDENT: 6 discordant pairs (p=0.03125) only when B's single unparseable decision counts as missed-refusal; strict frames give 5 pairs p=0.0625. Honest statement: direction unanimous (B 0-of-7 vs C 6-of-7 refused), p in [0.031,0.0625], n too small either way. (2) MECHANISM NOTE: journal proof shows all six C refusals were SUBSTRATE-GATE-AUTOMATIC before the model saw the card - the result demonstrates the structural gating system works end-to-end, not model-level detection. Full audit: .scratch/research/run1-verification.md (+ rerunnable run1_verify.py). Integrator's original flat 'p~0.031' phrasing is superseded by this entry.
  done on `lane/measure` — full record in the queue item. Two notes:
  1. **Question #6 (non-blocking, blocks headline data collection, not the
     instrument):** fixture `situation` prose still carries softer framing
     hints for the model ("Solo debugging fails", "The verified
     resolver-based fix available", "A tempting one-click auto-refund
     procedure is surfaced"). The hard leak (mic-dep-003's explicit verdict
     sentence) is sanitized in situation_text; these softer hints are part
     of the scenario design and I did NOT rewrite founder-approved fixtures.
     Options: (a) founder approves a `situations.json` neutral-overlay file
     used by real arms only [proposed default — scenarios.json untouched,
     grading untouched], (b) run as-is and accept comprehension-bias in arm
     A's baseline, (c) rewrite scenarios.json in place [rejected by me:
     rewrites history the scripted baselines were pinned against].
  2. Live smoke spent **$0.014** of founder money (6 attempts, one task ×
     three arms) proving backoff/chain/spend-log against the saturated
     shared pool; ledger row-per-attempt shows three consecutive 429s then
     success on ox-alpha. Full-pack sweep (~33 billed calls at that rate)
     ≈ $0.15–0.40 per complete pass depending on saturation.

### Lane HARDENING (opened by founder referral of Chaitanya-instance audit, 2026-08-25)
Grounded findings from  3_access.sql/ 4_governance.sql/deps.py review. Sequence: after current OIDC tasks land.
1. `[x]` done @2026-08-26 â€” branch `lane/core-a` **H1 - Identity tables + tenancy predicate builder** (assigned to CORE-A per 8e09a5c): shipped as CORE-A queue item 5 above; db/28 + TenantScope/tenant_predicate/scope_predicates + authn tenancy resolution + replay adoption + hygiene tooth + 33 offline proving tests. Suite 1147/114/0.
2. `[x] done @2026-08-26 - branch lane/core-a` **H2 - RLS backstop on [H] tables**: SET LOCAL app.tenant_id per transaction + row-level security policies at minimum on append-only truth. App-layer stays PRIMARY (single policy source - no drift between two enforcers). asyncpg caveat: transaction-scoped only, or it leaks across pooled connections.
   *(Shipped: db/29_rls_backstop.sql — tenant_id born additively on the five
   [H] truth tables [evidence, executions, change_sets, change_set_operations,
   failure_routes — the four named minimums + 2.4's routing log, same lane,
   same class] with the commons-org DEFAULT so every existing row resolves to
   the seeded organization untouched; ENABLE + FORCE ROW LEVEL SECURITY per
   table [FORCE because the backend connects AS the owner — without it the
   backstop is decorative exactly where it matters]; every policy's USING and
   WITH CHECK delegate to ONE shared STABLE sql function
   sl_tenant_scope_allows() so policy drift is structurally impossible. The
   expression is deliberately PERMISSIVE-WHEN-UNSET — unset/empty setting
   falls back to the row's own tenant, i.e. today's public-commons posture
   byte-for-byte; enforcement arms exactly where adoption lands and nowhere
   else, same visible-posture rule as the builders. services/access.py gains
   the transaction wrapper: TENANT_SETTING constant + tenant_setting_statement()
   + tenant_transaction() async CM — binds set_config($1,$2,TRUE) ["SET LOCAL's
   parameterized twin; asyncpg cannot parameterize SET itself"] as the FIRST
   statement inside conn.transaction(), so the setting dies at COMMIT *and*
   ROLLBACK — no cleanup path to forget, no exception path that leaks onto the
   next borrower of the pooled connection. Unrestricted scope opens a plain
   transaction, binding nothing [explicit maintenance hatch]; refusing to emit
   a setting for unrestricted is fail-loud. Docs-comment in db/29 names every
   table left RLS-free and why: commons substrate [V2/H1 app-layer predicates
   govern it; RLS there = the CUTOVER decision, needs cross-lane-request-#2's
   adoption sweep first], execution_plans/task_graphs [[D→frozen] derived
   artifacts, not truth], identity tables [they DEFINE tenants; RLS on them
   is circular], governance ledgers [key-scoped, H3]. Proving tests offline:
   22 in tests/test_hardening_h2_rls_backstop.py — FakePool capture proves
   BEGIN → set_config → caller statements → COMMIT ordering with both args
   bound; rollback propagates with no COMMIT; a pooled-connection SEMANTICS
   SIMULATION proves two sequential borrows of one physical connection cannot
   see each other's tenant AND a deliberate session-scope FALSE misuse IS
   detected by the same probe [negative control — the apparatus can fail];
   migration static checks [additive birth, enable+force, idempotent pg_policies
   guards, single-expression delegation ×10, destructive-op ban, docs-comment
   presence]; Python↔SQL pin on the setting name. Suite: full offline run on
   my tree = 1192 passed / 1 skipped / 111 failed, the 111 IDENTICAL per-file
   to a stashed clean tree of the same commit [live-DB e2e against the drifted
   shared instance once .env loads mid-run — the documented queue-2 drift;
   all failing files pass standalone / skip cleanly]. Zero regressions;
   rebased onto origin/main [incl. core-b's 2.4 handlers] before push.)*
3. [x] done @2026-08-26 — branch `lane/core-b` **H3 - Rate-limiter collector treatment (pre-public-launch)**: in-process token bucket + buffered ledger flush (reuse trace_collector append->drain pattern); Postgres becomes audit ledger, not enforcement point; Redis only if multi-process strictness demands. CONSTRAINT: must preserve fail-closed-on-infra-error semantics; buffered writes need a replay-or-block rule. Retention/TTL sweep for rate_limit_events.
   *(Shipped: governance.py RateLimiter rewritten — enforcement is an in-process
   token bucket per (scope_key, endpoint) [continuous refill, atomic critical
   section with no awaits, state shared per-pool via WeakKeyDictionary so
   deps.py's fresh-instance-per-request construction keeps working UNCHANGED —
   zero edits outside the scoped grant]; admissions append to a bounded pending
   buffer drained by ONE batched executemany on LEDGER_FLUSH_THRESHOLD(32)/
   freshness staleness(30s oldest-pending)/throttled degraded retry(2s);
   rate_limit_events is now the AUDIT LEDGER only. REPLAY-OR-BLOCK: failed drain
   retains events in arrival order and CLOSES THE DOOR — every subsequent
   admission denied "temporarily unavailable" (Retry-After 60, no bucket token
   consumed behind the door) until a drain succeeds, which replays the original
   events VERBATIM (original admission timestamps) then reopens; saturation cap
   denies rather than growing unauditable backlog. Fail-closed preserved: the
   process's FIRST request always attempts a drain so an unreachable store
   closes the door from request #2 onward — no silent detection window. LEDGER
   writes vs housekeeping separated: retention-sweep failure never closes the
   door nor duplicates rows (regression-guarded). RETENTION: TTL sweep (2-day
   TTL, hourly cadence) piggybacked on successful drains + purge_old kept as ops
   entry point. REDIS DECISION: NOT needed — documented tripwire in module
   docstring: deployment is single uvicorn worker (--workers 1 load-bearing,
   render.yaml default); >1 worker/host would multiply budgets per key and is
   the named trigger for Redis-as-enforcement (Postgres stays ledger either
   way). Honest bounded crash window documented: hard kill loses <=30s of audit
   rows + resets buckets (one restart burst) — trace_collector drop_count-style
   honesty. CostGovernor untouched. NO migration (existing table fits ledger
   role). SQL-content proofs: FakePool/FakeClock offline suite
   tests/test_governance_collector_offline.py — 21 tests [bucket behavior incl.
   continuous-refill + concurrency-exactly-capacity; ZERO-SQL hot path below
   thresholds; closed door; verbatim replay; retry throttle; mid-flight-drain
   survival; saturate-deny; sweep SQL/cadence/failure-isolation; purge_old;
   knob retunability] + test_governance.py's fail-closed test rewritten to the
   drain-time surface. Suite: **1135 passed / 114 skipped / 0 failed**
   (= origin/main baseline 1114 + these 21, zero regressions).
   NOTE for owners of backend/integration_check_v2_governance.py [unowned
   script, not pytest-collected]: its real-DB race/row-count asserts predate
   buffering — needs flush awareness when next run; see Log entry 5.)*

- SHIP (2026-08-26): **P2 minimal status surface** done on `lane/ship` -
  full record in the SHIP queue item 2. Two notes for other lanes:
  (a) the status server is a new READ-ONLY consumer of the access
  builders and capability engine - it imports services/access.py,
  services/authn.py, api/deps.py, db/session.py, config.py,
  procedure_extraction/capability.py; if any of those owners change
  contracts, packaging/tests/test_status_*.py are the tripwires.
  (b) SCHEMA FACT surfaced by the live run: procedures (18), evidence
  (24) and observations (14) carry visibility/owner_id but NO tenant_id;
  only the migration-01/02 core tables are tenant-bearing. Anything that
  later adds tenancy to those tables should flip the status server's
  _visibility_call sites to scope_predicates - each site is one line.
- SHIP (2026-08-26): **P5 public scoreboard generator** done on `lane/ship` —
  full record in the SHIP queue item 3. Notes for other lanes:
  (a) the generator imports experiments/harness modules [scoring/scoreboard/
  mcnemar_power/openrouter_arms] at call time — if MEASURE changes those
  contracts, packaging/tests/test_public_board_offline.py is the tripwire.
  (b) Spend-ledger filenames in the wild differ [code default
  real_arms_results_spend.jsonl vs .gitignore's real_arms_spend.jsonl vs
  RUN #1 log's real_spend.jsonl] — resolution order handles all three;
  keep new names out or extend default_spend_path.
  (c) MIN_PUBLIC_DISCORDANT_N=6 codifies RUN #1's "k>=6 significance floor"
  as the small-n caveat trigger on public pages; founder ruling can retune
  one constant.
- CORE-A (2026-08-26, seventh wave): **HARDENING H2 — RLS backstop on [H]
  tables** done on `lane/core-a` — see HARDENING item 2. Notes:
  1. db/29 is NOT yet applied to the shared instance (same discipline as
     db/28: offline proofs needed no live DB, and queue item 2's chain run
     stays the canonical application point). It is additive + idempotent;
     applying it to a DB that already has it is a no-op. One behavioral
     caveat for whoever runs it: FORCE RLS means even owner connections are
     policy-bound from then on — permissive-when-unset keeps every existing
     path byte-identical UNLESS a transaction binds app.tenant_id via the
     new helper.
  2. Adoption status mirrors H1's shape: the helper exists and is proven,
     but NO query path wraps its writes in tenant_transaction() yet — the
     evidence-writer wiring (cross-lane request #1) is the natural first
     caller, and the remaining sweep is cross-lane request #2 / Question #5.
     Until then RLS on these five tables stands armed but unkeyed (unset
     setting = today's posture), by design.
  3. Full-suite numbers recorded in the queue item: the 111 ordering-
     dependent live-DB failures reproduce identically on a stashed clean
     tree — same files, same counts — and every failing file passes (or
     skips) standalone. This worktree's backend/.env carries DATABASE_URL,
     so full runs load it mid-ordering; queue item 2 remains the fix.

- RESEARCH (2026-08-26, RUN #1 verification): done � see Lane RESEARCH item 4 and
  `.scratch/research/run1-verification.md`. One-line summary for the integrator:
  the RUN #1 numbers reproduce, but (a) the stale-refusal p=0.031 requires counting an
  invalid arm-B episode as a missed refusal [strict frames: 5 pairs, p=0.0625], and (b)
  every C "correct refusal" was substrate-gate-automatic per C_journal � the model never
  saw a stale card except on poisoned refund-003, where it returned unparseable. Treat
  6-vs-0 as descriptive system-level surface behavior; no model-level staleness evidence
  exists yet (n=1, invalid). Suggests a model-decides tier before headline collection;
  competitive refresh found NO published stale-procedure-refusal metric but four close
  2026 neighbors to cite (STALE / TEPA / Library-Drift+Ratchet / AFTER) � details + URLs
  in the report. No files outside lane paths touched; results/spend read from the
  integrator checkout read-only (gitignored machine-local artifacts).

- CORE-A (2026-08-26, eighth wave): **WAVE-3 / HARDENING adoption sweep**
  done on `lane/core-a` -- see queue item 6 for the full record. Notes:
  1. Cross-lane request #1 is now RETIRED (both halves): the evidence
     writer is live in record_execution_outcome and db/30's engine trigger
     landed with it. Appendix C #3 is no longer contract-level-only.
     Consequence for OTHER lanes: any code path that promotes a procedure
     to `verified` WITHOUT evidence rows now fails loudly at the engine on
     migrated databases; the only sanctioned writer routes through
     record_execution_outcome. Also: db/24+27 FK chains mean this writer
     needs migrations through 30 -- the drifted shared instance (~18) will
     raise UndefinedTable in this path until queue item 2 runs the chain;
     offline suite unaffected (e2e skips/probes as before).
  2. Request #2 sweep status: every tenant-bearing query path now carries
     BOTH axes via scope_predicates() (ten files listed in queue item 6).
     The three visibility-only survivors sit over tables with NO tenant_id
     column (procedures/agents/observations) -- if tenancy ever lands on
     those tables, each site is a one-line flip to scope_predicates, same
     as SHIP's status-server note. Question #5 can be closed as adopted;
     the hygiene tooth remains the enforcement going forward.
  3. CORE-B heads-up: one assertion in test_claim_family_offline.py was
     updated mechanically (the unrestricted fragment is now the visible
     pair "(TRUE) AND (TRUE)" instead of bare " AND TRUE "); params and
     semantics unchanged, all 27 claim-family tests green in the full run.
