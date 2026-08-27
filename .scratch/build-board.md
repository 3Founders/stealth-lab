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

7. `[x]` done @2026-08-26 — branch `lane/core-b` **WAVE-3 / Debate panel OpenRouter wiring**: app/debate/panel.py + config.py (scoped
   grant) - add OpenRouter as a provider so scan->debate->approve runs locally on the founder key; reuse openrouter_arms backoff
   pattern; prove with offline FakePool tests + one gated live smoke.
   *(Shipped: config.py fourth provider posture `use_openrouter` +
   OPENROUTER_API_KEY/base_url/panel_models/judge_model — defaults are the
   CHEAP four-family roster PROBED LIVE on the founder account 2026-08-26:
   ox-alpha | openai/gpt-4o-mini | anthropic/claude-haiku-4.5 panel +
   google/gemini-2.5-flash judge. panel.py gains OpenRouterAgent [PanelAgent
   dataclass]: arms-pattern survival ported wholesale — full-jitter
   exponential backoff uniform in [0, min(cap, base·2^attempt)) on
   RETRYABLE_STATUSES {408,409,429,500,502,503,504} AND network errors,
   immediate non-retryable-4xx fallthrough to fallback chain models,
   AllModelsFailedError carrying the per-attempt trail; injectable
   transport/sleep/rng proven offline at zero wall-clock; TURN_BUDGET_S=110
   deliberately under gather_responses' outer 120s wait_for so an exhausted
   seat raises WITH its trail instead of being cancelled into an anonymous
   failure. `manages_own_retries` marker makes _call_with_retry STAND DOWN
   for these seats — stacking both retry ladders = ~4x worst-case sleeps
   hammering a saturated pool; failure isolation unchanged. THREE live-run
   findings baked in as named machinery: (1) json_mode=True at the
   factories [response_format json_object] — real frontier models ramble
   past completion budgets in prose and get truncated before JSON starts;
   (2) REASONING_BUDGET_CAPS {ox-alpha: reasoning.max_tokens=400} — ox-alpha
   burned ANY plain budget on invisible reasoning and returned empty
   content; cap verified fixing it in one probe; (3) stale-default guard:
   claude-3-5-haiku had been RETIRED upstream [first smoke: clean 404 trail]
   and catalog listing ≠ endpoint availability [deepseek-chat-v3.2 listed
   but rejected] — hence probe-then-pin defaults + a construction-time
   roster tripwire test. Factories wired into default_panel/default_judge/
   default_chat_agent/default_layer2_agent; provider-flag conflicts now
   checked pairwise across all three hosted flags. PROOF: 36 offline tests
   tests/test_debate_openrouter_offline.py incl. THE FakePool lifecycle —
   real TriggerDetector.scan+record → LoopOrchestrator.run over three
   scripted-completion OpenRouterAgent seats + judge against a recording
   FakePool with STATEFUL debate-state FOR UPDATE reads → scorecard passed /
   groundedness 1.0 / PENDING_APPROVAL → DebateStateMachine APPROVED, whole
   legal ladder OPEN→IN_DEBATE→PENDING_EVAL→PENDING_APPROVAL→APPROVED pinned
   and an illegal jump refused; zero spend, zero sleeps asserted. LIVE SMOKE
   gated behind SL_DEBATE_LIVE_SMOKE=1 [CI never sets it; module-level
   skipif]: PASSED end-to-end vs real endpoint — 3 seats × 2 rounds, 6 turns
   all engine-parsed VADA JSON, 0 transport failures, ~14s wall. Total live
   spend this item ≈ $0.02 incl. diagnosis probes. Full suite: **1243
   passed / 115 skipped / 0 failed** [= prior baseline + this item's tests,
   the +1 skip being the gated smoke]. DISCLOSED out-of-grant touches:
   tests/test_general_compute.py ONE line [new third flag must read False in
   the nothing-configured fallback test — truthy MagicMock routed otherwise];
   requirements.txt untouched by grant but NOTE for its owner: httpx is now
   load-bearing at first OpenRouter use [lazy import; present transitively
   today, v0.28.1].)*

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
10. `[x]` done @2026-08-27 — lane/core-b **check_procedure MCP tool** (this
    wave's kickoff item, direct chat instruction): demo.md's C5 row and its
    pinned §3 payload contract described a tool that didn't exist in
    `backend/app/mcp_server/server.py` — 8 tools, no `check_procedure`.
    Added it as tool #9, audit-mode only (informs, never blocks).
    *(Shipped: ALL decision logic lives in the OWNED file
    `app/services/applicability.py` — `check_procedure_reuse()` +
    `ProcedureVerdict`/`ProcedureNotFound` — with `server.py`'s
    `check_procedure` a genuinely thin wrapper (json.dumps the verdict,
    turn `ProcedureNotFound` into a `"REFUSED: ..."` string), same
    discipline as `retrieve_precedent`/`apply_change_set`. Reuses, does not
    reinvent: `check_hard_constraints()` (this module, unchanged) is the
    ALLOW/WOULD_REFUSE cascade itself, called with
    `require_verified=False` — ticket 13's own named exception ("a
    candidate procedure remains explicitly invocable"), since naming
    `procedure_id` IS explicit invocation, not automatic selection; and
    `procedure_extraction/failure_handlers.capability_for_stream()`
    (imported lazily — module-level would be a real import cycle, that
    package's `__init__.py` itself imports `_scope_matches` from this
    module) for `capability_note`, over the SAME `target_type='procedure'`
    evidence rows `procedure_evidence_stats` (db/24) counts. NOT reused:
    `precondition_gate.py`'s postcondition Jaccard gate — it needs
    STRUCTURED tags on both sides, `query` is free text with none
    extracted, so calling it would be dead code (`postconditions_compatible
    (tags, None)` trivially True); stays real at its actual call site
    (hierarchy.py). CORRECTION to the kickoff brief: it named `state.py`
    as one of the three pieces implementing "capability-decay tracking" —
    that logic actually lives in `procedure_extraction/capability.py`
    (Band 1.9b), not `state.py`; `state.py` contributes only via the
    EXISTING `project_state()` call inside `check_hard_constraints`'s own
    precondition loop, untouched here. Real reason-building, not a
    canned string: a failed precondition triggers a direct
    `knowledge_nodes`/`edges` lookup (read-only, no new write logic) to
    name the actual current claim and, when truth_state=OUT, the actual
    claim that superseded it via the real SUPERSEDES edge
    (`claims.py::relate_claims`'s own mechanism) — proves demo.md §3's
    exact headline shape ("precondition claim cl_17 ... superseded by
    cl_23") against a real (fake-pool) edge lookup, not fabricated.
    HONEST GAP disclosed in the module docstring: `evidence` cites real
    claim ids, not changeset ids, for a claim supersession — 1.9c's
    universal ChangeSet coverage explicitly scoped knowledge_node
    supersession OUT of v1 (db/25's own header), and `relate_claims()`
    writes the SUPERSEDES edge with no change_sets row, so there is
    honestly no changeset id to cite yet. 15 new offline proving tests:
    12 in `tests/test_check_procedure_reuse_offline.py` (not-found/bad-
    uuid, ALLOW incl. explicit-invocation-bypasses-verification, every
    WOULD_REFUSE branch incl. all three precondition sub-cases, capability
    equality vs `capability_for_stream` directly, exact SQL-content pin on
    the evidence-stream query) + 3 in
    `tests/test_mcp_check_procedure_offline.py` (thin-wrapper JSON-shape
    pin against demo.md §3's exact contract, procedure_id pass-through,
    REFUSED-string mapping) — first offline tests `mcp_server/` has ever
    had; scoped file grant for server.py only, otherwise unowned,
    integrator-approved per kickoff. REAL BUG FOUND AND FIXED EN ROUTE:
    the MCP wiring test file's own import of `app.mcp_server.server`
    triggers that module's `load_dotenv()`, a process-wide `os.environ`
    mutation that set `DATABASE_URL` for the first time in an offline
    run — every `*_e2e.py` module collected alphabetically afterward saw
    it and stopped skipping, attempting real (unreachable) connections:
    69 failures on the first full-suite run, gone (env snapshot/restore
    immediately after the import, at collection time) on the second. Full
    suite after the fix: **1368 passed / 115 skipped / 0 failed** (zero
    regressions; the jump from the WAVE-3 baseline of 1243/115 is other
    lanes' already-landed work on this branch, not this item alone).)*
11. `[x]` done @2026-08-27 — lane/core-b **bootstrap_demo.py real two-phase
    story** (this wave's kickoff item, direct chat instruction, scoped
    exception on `backend/scripts/bootstrap_demo.py` — unowned by any
    lane, integrator-approved for this wave): the checked-in script was
    the OLD debate-seeder (raw SQL trace rows tripping `_DEMO_RULES`'
    bottleneck threshold) — unrelated to this pipeline, produces zero
    procedures, and demo.md doesn't reference that behavior at all.
    Replaced outright, per the kickoff's own stated default (nobody
    flagged a dependency on the old behavior).
    *(Shipped: Phase A builds an `AgentRunEvidenceSource` by hand — same
    shape `server.py`'s `solve_task()` builds at its own
    `extract_procedure()` call site — over a REAL probe of this repo's
    own `backend/` checkout (`environment_probe.assert_environment_claims`,
    zero fixtures: real `pyproject.toml`/`requirements.txt`/`tests/` ->
    real `language`/`has_test_runner`/`package_manager` claims), then
    calls `extract_procedure(pool, evidence_source, client=None, ...)` —
    deterministic path, zero API calls. Phase B picks one real derived
    precondition, genuinely invalidates the claim behind it via a real,
    separately-captured claim + `claims.relate_claims()`'s real
    SUPERSEDES edge (deliberately a DIFFERENT predicate than the one
    being invalidated — sharing it would make `_precondition_narrative`'s
    own newest-claim lookup find the NEW claim instead of the superseded
    OLD one, hiding the exact "superseded by" narrative), then proves
    `check_procedure_reuse()` flips ALLOW -> WOULD_REFUSE citing BOTH
    real claim ids, demo.md §3's exact pinned shape.

    REAL BUG FOUND AND FIXED EN ROUTE (in-scope, `procedure_extraction/
    __init__.py`): `extract_procedure()` never passed `provenance` or
    `scope_type` to `capture_procedure()`, which unconditionally requires
    both (`v0_gate.py`'s V0 gate) — every real call would raise
    `V0Violation` before reaching the INSERT, on EVERY caller including
    `solve_task()` itself, never caught because the offline suite
    monkeypatches `capture_procedure` entirely (see
    `test_procedure_extraction_init_offline.py`'s own docstring) and no
    worktree has had a working `DATABASE_URL` until this wave. Fixed:
    `provenance="system_pending_review"` (this codebase's existing
    convention for a system-derived, not-yet-approved object —
    `claim_family.py`/`knowledge_conflict.py` use the same value,
    matching the follow-up UPDATE that stamps `approval_status='proposed'`
    on the same row) + `scope_type="project"`/`scope_entity_id=
    evidence.project_id` when known, `"global"` otherwise. VALIDATED
    LIVE: the existing (pre-existing, self-cleaning, already-scoped)
    `test_procedure_extraction_init_e2e.py` — previously never actually
    exercised — now passes 3/3 against the real Supabase DATABASE_URL in
    `backend/.env`.

    HONEST FINDING, numbered per house rules (non-blocking — proceeded on
    the stated default, kept moving): the kickoff text says Phase B
    should "call `retrieve_precedent` (should surface it)" before
    breaking the precondition. Traced `retrieve_precedent`'s real
    implementation (`reuse_detection._vector_candidates`): it only ever
    queries `task_nodes`/`knowledge_nodes` — it CANNOT return a
    `procedures` row, regardless of verification state (a real, disclosed
    drift from demo.md C4's own description of that tool — separate
    pre-existing gap, not touched here). Even `find_applicable_procedures`
    (the function that DOES retrieve procedures) is cold-start-gated OFF
    (`should_disable_procedure_retrieval`) whenever fewer than
    `MIN_VERIFIED_PROCEDURES_TO_ENABLE_RETRIEVAL` verified procedures
    exist system-wide — true by construction for ANY freshly-extracted
    procedure on a fresh DB, no matter which retrieval function is
    called. **Question #7 (non-blocking, default applied):** "reuse
    you can see" for a not-yet-verified procedure is therefore
    necessarily EXPLICIT invocation (`check_procedure_reuse`, which
    deliberately bypasses both gates per ticket 13's own named
    exception), not automatic retrieval. Default applied: the script
    calls `check_procedure_reuse` for a real pre-break ALLOW proof, AND
    calls `find_applicable_procedures` for real, printing its honest
    (empty, cold-start-gated) result rather than claiming it "surfaced"
    something it structurally cannot yet. Options if this reading is
    wrong: (a) keep as shipped (proposed default — matches how
    `check_procedure`'s own docstring already frames explicit-invocation
    as the answer for this exact situation), (b) mark the procedure
    verified+approved before Phase B to force automatic retrieval to
    fire too (rejected by me: fabricates evidence demo.md §2 item 4
    explicitly forbids — "no backfills... a fresh install sees exactly
    what the schema births"), (c) treat this as a real product gap
    (`retrieve_precedent` should search `procedures` too) and file it as
    separate follow-on work, out of scope for this wave.

    PROOF STATUS, same standard as the migration-chain item — not
    claiming more than actually run: offline coverage is solid (13
    `test_procedure_extraction_init_offline.py` tests unaffected by the
    fix + 45 across the check_procedure/derive/bootstrap_demo cluster,
    all green; new `tests/test_bootstrap_demo_offline.py` — 10 tests —
    covers the script's own new pure logic: `select_target_precondition`,
    `verdict_cites_both_claims`, the OBSERVATIONS fixture's own
    load-bearing shape, phase function signatures). Full offline suite
    unaffected: **1368 passed / 115 skipped / 0 failed**, unchanged
    (the fix only adds kwargs an already-monkeypatched call site doesn't
    assert on). The script itself has NOT been run live yet — this
    worktree has no Docker (confirmed: `docker`/`docker compose` both
    "command not found"), but DOES have a real, populated
    `backend/.env` pointing at a live (shared, NOT fresh/disposable)
    Supabase `DATABASE_URL` — used above only to validate the V0-gate fix
    via the pre-existing, self-cleaning, narrowly-scoped e2e test file,
    not to run the new script itself. Per the kickoff's own instruction
    ("flag it and I'll route the live fresh-DB run to Chaitanya... or
    point you at the shared dev DB, whichever's faster") — flagging here
    rather than unilaterally running a brand-new data-writing script
    against shared infra. Script is ready to run as-is the moment either
    path is confirmed.)*
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

5. `[x]` done @2026-08-26 â€” branch `lane/measure` **real-arms sweeps #2 and
   #3** (fresh --out per run, --auto-resume; all 11 tasks valid in BOTH
   files).
   *(RUN#2: $0.2586 main pass [89 attempts / 45 billed] + two resume
   passes [$0.0793 + $0.0890] = **$0.4269**, 113 attempts total. Final
   scoreboard n=11: A 9/11 Â· B 7/11 w/ 2 false-reuse Â· C 6/11 w/ 0;
   stale-refusal column C 7/7 vs B 0/7 [surface behavior only â€" see Log
   correction: gate-mechanical for C, no refusal path in B]; no pairwise
   significant [B-vs-C 1 discordant p=1.0; A-vs-C 3 discordant p=0.25].
   RUN#3: $0.2839 main [43/37] + one-task resume retry $0.0350 =
   **$0.3189**, 50 attempts.
    n=11: A 8/11 Â· B 7/11 w/ 1 false-reuse Â· C 9/11 w/ 0; refusal column
   repeats the same fixture-determined pattern; B-vs-C 2 discordant both
   C-favoring p=0.5; nothing significant. SESSION SPEND $0.8732 total
   incl. item 6's extraction â€" under the ~$1 cap. INSTRUMENT FINDING +
   FIX: run2's ledger showed billed calls pinned at exactly tokens_out=
   700 returning unparseable-after-repair JSON â€" MAX_COMPLETION_TOKENS
   was shearing long replies mid-object. Raised 700â†'1400 as a named
   constant with regression test [test_openrouter_arms.TestCompletionCap]
   ; fix applied AFTER run2's main pass, so its two broken tasks were
   completed under the fixed cap via resume; ALL of run3 + the error-
   floor pass ran at 1400. Comparability note for RUN#1: it ran entirely
   at cap 700 [10/10 valid then â€" sampling luck, not robustness].)*
6. `[x]` done @2026-08-26 â€” branch `lane/measure` **error-floor FIRST LIVE
   extraction pass** over the 42 labeled fixtures using the OpenRouter-
   backed observation extractor.
   *(Shipped: `live_extractor.py` inside experiments/harness only â€"
   OpenRouterClient reused wholesale [backoff/chain/SpendLog/backend-.env
   key]; arm tag EX in the spend ledger; one repair round-trip per
   excerpt mirroring RealAgentBase; prompt encodes ticket 04's published
   TAXONOMY not the demo mirror's prefix rules + the NONE contract for
   the semantic layer + hard-negative silence; dict-shaped output passes
   through to the grader UNVALIDATED so malformed predictions cost
   precision as the rubric intends [only non-dict array items dropped,
   counted in row meta]; unparseable rows recorded as empty+flagged and
   RETRIED by --auto-resume like run_real_arms. 12 offline tests
   tests/test_live_extractor.py [fake client, zero network]. LIVE RUN:
   42/42 excerpts extracted, 0 adapter errors, 0 unparseable; 53
   attempts / 42 billed / **$0.1274**. GRADED FLOOR [error_floor_live_
   results.jsonl + _detail.json, consumable via scoreboard
   --error-floor-results]: overall P 25/44 (0.568) Â· R 25/30 (0.833) Â·
   F1 0.676 [golds=30/preds=44]. Per type: commit_made 5/5Â·5/5,
   test_run 7/7Â·7/7, file_touched P 8/10 R 8/8, command_executed P 5/5
   R 5/6, semantic_label P 0/17 R 0/4. FINDINGS: (a) the SEMANTIC layer
   is the whole error story â€" the model answers verbose detailed
   sentences where golds are terse ('authentication implementation was
   modified'), token-Jaccard < 0.5 everywhere, and it pads labels onto
   hard-negative events; Band-3 model extractor needs terse-label
   discipline in-prompt or a founder ruling on whether the rubric's
   threshold should grade information coverage rather than brevity;
   (b) NONE contract overreach: bare `ls src` got no mechanical gold
   answer either [ef-sem-005 FN]; (c) one malformed empty observation
   [ef-file-010]; (d) `rm -rf dist/` typed as file_touched [ef-sem-002].
   Live vs demo-mirror baseline: P .880â†'.568, R .733â†'.833, F1 .800â†'.
   676.)*

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
5. `[x]` done @2026-08-26 — research lane (this worktree) **Model-decides
   stale-procedure task tier design** (CLAUDE.md kickoff, Claude Code takeover):
   design (not implement) a fixture/task spec where staleness is present in
   the procedure card but NOT pre-filtered by `mcp_surface.StubSurface`'s
   gate — the model must decide from context whether to reuse or refuse —
   fixing the gap `run1-verification.md` found (all 6 of RUN #1's C stale
   refusals were substrate-gate-automatic, not model decisions).
   *(Report: `.scratch/research/model-decides-tier-design.md`. KEY FINDING:
   the mechanism already exists and is switched off — `mcp_surface.py`'s
   `bypasses_gate` context flag (unconditional `verdict=True`) plus
   `openrouter_arms.py`'s `RealProcedureAgent` gate-threading already support
   a model-decides episode end to end; it was used exactly once
   (`mic-refund-003`, an unrelated poisoned-gate honest-negative slot) and
   returned unparseable. DESIGN: single-offer trap tasks (stale procedure
   offered, correct=refuse) + control tasks (current procedure offered via
   the same bypass mechanism, correct=reuse — the missing specificity half;
   nothing today tests whether C just refuses everything) across the
   existing 4 domains, reusing all 4 existing procedure pairs and rag blobs —
   ZERO harness code changes needed for wave 1 (mcp_surface.py,
   openrouter_arms.py, AND scoring.py all support it as-is; the two new
   metrics — trap-avoidance paired B-vs-C and C's own false-refusal rate —
   are both derivable from existing episode fields + procedures.json ground
   truth, report-layer only, same pattern as run1_verify.py). SIZING: 24
   single-offer tasks (3 trap + 3 control × 4 domains) recommended for wave
   1, reasoned from mcnemar_power.py's own required_n()/binomial-floor math
   since NO run2/run3 data exists to reason from despite the kickoff text
   assuming it would (flagged explicitly, not papered over) — table shows
   ~67-100% chance of clearing MIN_PUBLIC_DISCORDANT_N=6 at n=16-20 valid
   episodes under d=0.4-0.7 discordant-rate assumptions, but likely
   UNDERPOWERED for significance unless the true effect is large (q>=0.85);
   recommends wave-1-then-sized-wave-2 via required_n(q_observed), not a
   single-shot collection. Cross-lane request to MEASURE filed in the report
   §7: fixture-content-only ask (new tasks.json/scenarios.json rows), zero
   required code changes, one open question flagged (dual-offer serial-
   position-bias risk, deliberately NOT used in wave 1's design) rather than
   silently assumed away.)*
6. `[x]` done @2026-08-27 — research lane (this worktree) **Independent
   verification of MEASURE's model-decides live sweep** (founder request,
   same discipline as `run1-verification.md`). Recomputed sensitivity/
   specificity from raw `model_decides_results.jsonl` (sl-measure worktree,
   read-only, gitignored) with an independent classifier + hand-read every
   `C_journal` entry for genuine model-decision evidence.
   *(Report: `.scratch/research/model-decides-verification.md` + rerunnable
   `model_decides_verify.py`. HEADLINE NUMBERS CONFIRMED EXACTLY: sensitivity
   11 discordant pairs (B-only 1, C-only 10), p=0.01171875; specificity 0/12
   false refusals — both bit-for-bit against the shipped
   `model_decides_report.json`. JOURNAL CONFIRMS GENUINELY MODEL-DECIDED:
   every `check_applicability` verdict=True (bypass working), zero refusals
   carry the mechanical gate string, all 11 refusal reasons are
   situation-specific model prose (e.g. "purchase age exceeds the maximum
   allowed of 90 days") — exactly the missing evidence run1-verification.md
   asked for. NEW FINDING, more consequential than any single number: the
   ENTIRE sweep ran on `openai/gpt-4o-mini`, not `ox-alpha` — every one of
   72 `ox-alpha` attempts returned HTTP 404 (non-retryable, immediate
   fallthrough), zero 429s anywhere in the spend log; the board's own
   write-up describes these as "72 absorbed 429s/network retries", which is
   factually wrong (counts match, failure KIND doesn't) — this sweep is not
   comparable to RUN #1/#2/#3 on the model axis (those were majority
   ox-alpha successes) and needs MEASURE/founder to confirm whether
   ox-alpha's OpenRouter model id is still valid before any second sweep.
   Secondary findings: (a) the board's "already exceeds n-for-80%-power@
   this-ratio=9" line is misleading, not wrong — required_n() is not a
   monotone floor for this exact test (power dips to 0.74 at n=11 despite
   clearing 80% at n=9), the correctly-caveated number is already printed
   earlier on the same board line; (b) the one B-only discordant task
   (dec-pdf-103) shows C's own reasoning correctly named the staleness in
   prose but never became a structured refusal — a schema-capture gap that
   understates C's true detection rate, not a detection failure, opposite
   in direction from RUN #1's gate-inflation caveat; (c) three control-task
   episodes show correct reasoning without a structured reuse credit — same
   gap, doesn't corrupt the shipped 0/12 specificity number. No files
   outside lane paths touched; results/spend read from sl-measure's
   worktree read-only (gitignored machine-local artifacts).
   **RUN2 ADDENDUM (same session, landed mid-verification):** MEASURE's
   run2 (board entry above, this section) also independently re-verified —
   sensitivity 10 discordant (B-only 1, C-only 9), p=0.02148438, CONFIRMED
   exactly; specificity 0/12, CONFIRMED exactly. Journal check repeats
   clean: every check_applicability verdict=True, zero mechanical-gate
   refusal strings, all 11 refusal reasons situation-specific model prose.
   THE OX-ALPHA 404 ISSUE PERSISTS IN RUN2 UNCHANGED: 72/72 ox-alpha
   attempts 404, zero 429s, same as run1 — not a one-off (>=2h42m span,
   two separate sweeps), and run2's board entry gives no per-model
   breakdown at all, so the substitution is now twice-undisclosed. The one
   B-only discordant task (dec-pdf-103) and one control-abstain task
   (dec-dep-105) repeat IDENTICALLY in run2 — model's own reasoning correct
   both times, never structured into the credited field both times — a
   real, repeated instrument gap (understates C, doesn't overstate it),
   not sampling noise, confirming the board's own "not a fluke" read.
   Full detail + updated verdict table in
   `.scratch/research/model-decides-verification.md` (updated in place, not
   a second file). Standing recommendation for whoever runs a third sweep
   of anything on this OpenRouter chain: confirm ox-alpha's current model
   id first.)*
7. `[x]` done @2026-08-27 — research lane (this worktree) **Observation/event
   labeling technique brief for MEASURE** (founder request): read STALE,
   TEPA, Library-Drift+Ratchet, AFTER full text specifically for
   observation/event labeling methodology (not abstracts, not just
   citation) — brief on any real technique worth trying.
   *(Report: `.scratch/research/observation-labeling-technique-brief.md`.
   HEADLINE RECOMMENDATION: swap `error_floor.py`'s literal token-Jaccard
   `semantic_label` match rule for an LLM-judge adjudication pass on
   Jaccard-FAILING pairs only (Jaccard stays the free first pass) — this is
   STALE's own move ("LLM judge... rather than against synthetic reference
   strings", validated at 95.8% human agreement, Appendix E.3), applied
   directly against MEASURE's OWN diagnosed failure ("CI workflow
   configuration updated" vs gold "continuous integration pipeline
   configuration added" = Jaccard 0.125, a correct label scored wrong by
   vocabulary choice, per the fourth-wave board note). Ratchet's
   false-positive/false-negative asymmetry finding folded in as a
   deployment caution: validate the judge's false-positive rate
   specifically before trusting it, majority-vote-of-3 if it ever backs a
   public number. SECOND recommendation, buildable on infrastructure
   MEASURE already has: AFTER's Collect-Diagnose-Revise-Promote refinement
   loop — group error_floor's own typed FP/FN reason codes (already
   computed), one reflector call proposes ONE additive prompt change,
   promote only if the floor improves without regressing other types
   (systematizes the terse-label ruling MEASURE already did once by hand
   into a repeatable, audited loop). THIRD idea flagged as speculative, not
   a quick win: TEPA's key/value extraction split (canonical closed-key +
   free value) — paper itself admits this is "a harder problem for
   open-ended memories," so scoped as a later-wave taxonomy idea, not
   next-sprint. Library-Drift/Ratchet's skill-retirement governance
   correctly identified as a DIFFERENT problem (library lifecycle, not
   single-observation labeling) — cite-only beyond the false-positive
   caution. Source table in the report distinguishes what was confirmed
   from full paper text vs what the papers genuinely don't show (e.g. none
   of the four publish their actual judge/reflector prompt text — checked
   directly, not assumed). No live model calls; pure literature work.)*
8. `[x]` claimed+done @2026-08-27 — research lane (this worktree)
   **Outside-eye pass on demo.md/README for first-time-user/investor
   clarity** (founder request, direct chat, repo-root docs, not board-queued
   before this wave).
   *(Report: `.scratch/research/outside-eye-demo-readme-pass.md`. TOP
   FINDING: repo-root `README.md` (untouched since pre-pivot commit
   `2ce3c7e`) describes a completely different, superseded product —
   "Task Graph + Ontology" workflow-debate platform, zero mention of MCP/
   traces/procedures/refusal — while `demo.md` defines the current v0.1
   earned-memory MCP product; a first-time reader or investor gets two
   unreconciled stories with no link between them. `backend/README.md` +
   `backend/README_MCP_SERVER.md` carry the SAME old-product description
   but are accurate to the code that runs today — grepped every
   `@server.tool()` in `backend/app/mcp_server/server.py`: the 8 real tools
   ARE the old workflow-debate surface (retrieve_precedent/apply_change_set/
   propose_synthesis/solve_task/detect_conflict_trigger/decompose_task/
   decide_decomposition/submit_approval), matching those two docs and
   `packaging/README.md` (SHIP, accurate) exactly. SECOND FINDING:
   `demo.md`'s own headline differentiator C5 (`check_procedure` ->
   `ALLOW`/`WOULD_REFUSE`) has zero code matches for either identifier
   anywhere in `backend/` — the mechanism is real and independently
   verified (this lane's own model-decides-verification.md, a genuine model
   refusing with situation-specific reasons) but runs inside
   `experiments/harness/mcp_surface.py`'s stub, not the production server;
   `commLLM.md`'s own tool table already marks check_procedure "**new**"
   but demo.md's C1-C5 table doesn't carry that distinction forward. Same
   pattern smaller: C1's `docker compose up -d` has no docker-compose.yml
   anywhere in the repo (checklist's own `[ ]` already honest about this;
   the C1 row wasn't). THIRD: demo.md claims Apache-2.0 at ship, no LICENSE
   file exists anywhere — flagged, not fabricated (founder/legal call).
   BONUS: commLLM.md (demo.md's own positioning companion link) was saved
   as UTF-16LE, rendering as unreadable spaced garbage in any UTF-8 reader
   — fixed the encoding (content otherwise untouched); a separate, older
   layer of mojibake on em-dashes/arrows/section-marks survives (~12 distinct
   garbled sequences, couldn't confidently map all back without guessing) —
   flagged for the owner to regenerate rather than guessed at. CHANGES MADE
   DIRECTLY: commLLM.md encoding fix; demo.md — one non-restructuring note
   added under the C1-C5 table naming both gaps, table itself untouched;
   README.md — full rewrite, two-layer honest structure (what's runnable
   today = the real 8-tool server + packaging CLI, both verified against
   server.py; what it's becoming = demo.md's v0.1 story, explicitly labeled
   as such), links out to backend/README_MCP_SERVER.md + packaging/README.md
   rather than re-deriving their setup detail. NOT committed to lane/research
   — repo-root docs, no lane owns README.md today (same posture as CORE-A's
   SECURITY.md/DATA_STATEMENT.md landing), flagging for founder to route
   rather than assuming this lane's normal commit path applies.)*
9. `[x]` claimed+done @2026-08-27 — research lane (this worktree) **Verdict on
   Chaitanya's infra-report knock-on claim** ("Band 2's founding-loop exit
   criterion is still unexercised on a fresh DB", from `bootstrap_demo.py`'s
   gap — founder request, direct chat).
   *(Report: `.scratch/research/band2-founding-loop-exit-criterion-review.md`.
   VERDICT: genuine gap, narrowly scoped. `ROADMAP.md`'s Band 2 exit-criteria
   list has exactly 4 bullets; bullets 3 (TMS readability) and 4
   (replayability) map cleanly onto `BAND2_CLOSURE_REVIEW.md`'s items 7 and 8
   — those are correctly closed, no issue found. Bullet 1 ("founding loop
   executed once end-to-end on real data... database currently contains zero
   inhabitants... until this runs once, the substrate's founding thesis is
   unexercised") has NO corresponding scorecard item — grepped the review for
   "founding"/"real data"/"hand-audited"/"end-to-end"/"inhabitant": zero
   matches, not even in its own honest "Deferreds / carried" section where
   other known gaps are listed. Yet the review's summary verdict ("Every
   ROADMAP Band 2 item is implemented" / "BAND 2 CLOSED") reads as covering
   it. Checked whether item 8's cited live e2e tests
   (`test_founding_loop_replays_bit_identically_from_raw_traces` — the name
   is why this likely looked closed at a glance —
   `test_procedure_candidate_replays_bit_identically_from_raw_traces`) secretly
   satisfy bullet 1 anyway: they don't. Read directly: (a) both run against
   "the long-lived shared dev instance" per the test file's own comment, the
   opposite of a fresh zero-inhabitant DB; (b) both skip the episode-assembly
   hop entirely — raw `trace_events` seeded directly into
   `process_pending_jobs()`, and the procedure-candidate test's `episode_id`
   is a bare `uuid.uuid4()`, never a row from `trace_worker.
   assemble_episodes()`; (c) their actual proof target is replay
   *determinism* (run once, replay twice, byte-compare, catch tampering) —
   bullet 4's claim, correctly cited there, not bullet 1's "one hand-audited
   live run" ask. Causality note: bullet 1 was unexercised BEFORE this
   infra-report run too — `bootstrap_demo.py` didn't newly break an
   already-closed criterion, it's simply the vehicle that would have closed
   it and turned out not to attempt it; the closure review silently never
   covered it either way. Recommendation (not a cross-lane code request — no
   harness/backend paths implicated, pure documentation/closure-bookkeeping
   finding): (1) amend `BAND2_CLOSURE_REVIEW.md` to explicitly carry bullet 1
   as open/deferred rather than silently absorbed into "CLOSED 9/9"; (2) real
   close-out is exactly what `PRODUCTION_READINESS.md`'s current #1 priority
   — rewriting `bootstrap_demo.py` to run the real two-phase story
   hand-audited on a fresh DB — would already deliver; flagging for whoever
   picks that item up (CORE-B/product-owned, not this lane) to explicitly
   tick this ROADMAP box when it lands.)*

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

4. `[x]` done @2026-08-26 — branch `lane/ship` **First real public scoreboard
   regeneration (CLAUDE.md kickoff task)**: ran `stealthlab-public-board`
   against all three real sweeps that exist so far — RUN #1
   (integrator checkout, `C:\Users\user\stealth-lab\experiments\harness\`)
   and MEASURE's run2/run3 (`C:\Users\user\sl-measure\experiments\harness\`,
   gitignored, read-only).
   *(Read `.scratch/research/run1-verification.md` (research lane) first per
   instructions — caveat 2: C's stale-refusal headline was gate-mechanical
   (StubSurface.check_applicability decided every refusal before the model
   saw the card, or blocked a proposed reuse), not model-level detection.
   ANSWERING the kickoff's open question directly: yes, the generator needed
   a second caveat type distinct from MIN_PUBLIC_DISCORDANT_N — that one is
   about SAMPLE SIZE, this one about CONSTRUCT VALIDITY (a comparison can
   clear the n-floor and still not mean what the raw numbers suggest). Not
   raised as a blocking founder question: the research report's code-level
   evidence (openrouter_arms.py's own `record_refusal` reason strings) made
   this an engineering call, not a real ambiguity — flagged here non-blocking
   for founder override on phrasing/threshold.
   SHIPPED: `stale_refusal_attribution()` in scoreboard_gen.py classifies
   each arm's stale-refusal credit as gate-mechanical / model-initiated /
   unattributed, counted PER TASK (mirrors scoring.classify's own boolean
   semantics — a task refusing two stale ids still counts once) and scoped
   to ground-truth-stale procedure ids only, via the `<arm>_journal` reason
   strings the harness already writes. New "Stale-refusal attribution" table
   on both pages (research recommendation #4); a new interpretive-validity
   caveat renders in "Read first" only when an arm's ENTIRE stale_refusal
   credit is gate-mechanical with zero model-initiated cases. TWO real bugs
   caught and fixed while proving this against all three real sweeps (not
   synthetic fixtures) before shipping:
     1. counting raw journal EVENTS instead of per-task booleans overcounted
        whenever one task refused more than one stale id;
     2. matching usable rows by task_id STRING instead of by which specific
        classified entry is in the usable set: run3's real data has TWO
        `mic-pdf-003` rows (an earlier invalid attempt left beside its
        --auto-resume retry) sharing one task_id — string matching pulled
        the excluded row's journal entry back in. Fixed via object-identity
        pairing between raw rows and their classified entries (both lists
        are 1:1 in order). Both bugs were invisible against the offline
        fixtures (which never has richer multi-stale-id tasks or duplicate
        rows) and only surfaced by running against real data — recording
        here as a reminder that "23/80/85 tests green" does not substitute
        for a live-data run before calling a generator done.
     8 new offline proving tests cover the attribution feature end-to-end
     plus both fixes specifically (non-stale-procedure exclusion, excluded-
     row exclusion, and the exact run3 duplicate-task-id shape). Packaging
     suite: **88 passed / 0 failed** [= 80 prior + 8 new, zero regressions].
   REAL RESULT, confirmed after both fixes (every arm's attribution total
   now foots exactly to its Arms-table stale_refusal count on all three
   runs): the interpretive-validity caveat fires on RUN #1 (C 5/5, all
   gate-mechanical, 0 model-initiated) but NOT on run2 or run3 (C 6
   gate-mechanical + 1 model-initiated each) — because in both later
   sweeps the model actually returned a parseable, self-reasoned refusal on
   the one poisoned-gate task (mic-refund-003) where research's report says
   "the model itself faced the staleness decision"; RUN #1's attempt at
   that same task was unparseable (0 model-initiated credit there). The
   caveat is therefore precise, not blanket — it tracks whether THIS sweep
   produced any genuine model-level evidence, self-documenting the exact
   distinction research's report asked for. Separately: the shipped
   generator's own McNemar comparisons never reproduced the old "p~0.031"
   figure on any of the three runs — pass/fail only, always under the
   small-n floor (RUN #1: A-B/A-C p=1.0 n=1, B-C p=N/A n=0; run2/run3
   similar) — that number was always an out-of-band computation outside
   this instrument. Output: `.scratch/ship/{run1,run2,run3}/
   public_scoreboard.{md,html}` (evidence trail, not the packaged artifact
   itself — that's generated on demand per README). Backend diff: ZERO
   files.)*

5. `[x]` done @2026-08-27 — branch `lane/ship` **Model-decides tier section
   (founder task)**: add MEASURE's model-decides tier results
   (`experiments/harness/model_decides_report*.json`, committed) to the
   public scoreboard as a clearly labeled new section — the project's
   first genuinely model-decided stale-detection evidence. Kept the
   existing run1/run2/run3 arms-based evidence untouched; regenerated a
   NEW demonstration page once MEASURE's second sweep and RESEARCH's
   independent verification both landed mid-session.
   *(SHIPPED: `discover_model_decides_reports()` globs every committed
   `model_decides_report*.json` beside the arms data (run1 = bare name,
   run2+ = `_run<N>` suffix, MEASURE's own convention — sorts correctly
   because `.` < `_`), so a future sweep needs no generator change to
   appear. `build_model_decides_block()` reads each report as DATA (no
   re-implementation — `model_decides.render_report()` imported verbatim
   for the canonical block, same discipline as the arms table's harness
   imports), rendering one sub-section per sweep plus a combined caveat
   whose wording depends on count: one report → single-sweep hedge; two+
   → a "confirmed across N sweeps" caveat. New section sits right after
   the "Read first" caveats and before "## Arms", ahead of the arms
   tables — it is now the headline, they are the system-level baseline.
   TIMING, this session: started with only run1's report committed →
   shipped single-report support → MEASURE's run2 report landed
   mid-session (commit `1c8b8f2`) → generalized to N reports before
   shipping → RESEARCH's independent verification landed immediately
   after (commits `ad209aa`/`1a0939e`) → caveat text updated to the
   CONFIRMED finding rather than a hedge (see below). Not raised as a
   blocking question at any point — each new fact was either a strict
   confirmation or a straightforward "cite it" call.
   CRITICAL FINDING SURFACED BY THIS WORK: `model_decides_report.json`'s
   schema has NO `served_by_model` field (unlike the arms A/B/C results),
   so the generator cannot detect on its own which model a sweep actually
   used — flagged as a caveat before I knew the answer. RESEARCH's
   verification then confirmed the concrete fact: **both existing
   model-decides sweeps ran entirely on `openai/gpt-4o-mini`, not
   `ox-alpha`** — every `ox-alpha` call returned a non-retryable HTTP 404
   and the chain fell through silently, spanning >=2h42m across two
   separate sweeps, undisclosed in either sweep's own board entry (which
   described the 72 non-billed attempts as "absorbed 429s" — they are
   100% 404s, a permanent-per-call failure, not transient rate-limiting).
   The headline numbers themselves are independently reproduced EXACTLY
   (p=0.0117 and p=0.0215) and the refusals ARE genuinely model-decided
   (C_journal audit: zero mechanical gate strings, every reason is
   situation-specific model prose) — the finding is real, just not yet
   about the project's primary model. The shipped caveat text states this
   plainly and by name (dated 2026-08-27, hand-written from the merged
   verification report, not derived from a schema field that doesn't
   exist) rather than hedging generically — **recommend confirming
   ox-alpha's current OpenRouter model id before the next model-decides
   sweep**, or this evidence base stays about the fallback chain
   indefinitely.
   7 new offline proving tests (report loading/discovery/labeling,
   present/absent rendering, single- vs multi-sweep caveat wording,
   data-not-recomputed proof). Packaging suite: **95 passed / 0 failed**
   [= 88 prior + 7 new, zero regressions]. Demonstration output (not the committed
   run1/2/3 evidence, which is untouched):
   `.scratch/ship/model_decides/public_scoreboard.{md,html}`, built from
   run3's arms data + both model-decides reports via auto-discovery.
   Backend diff: ZERO files.)*

6. `[ ]` **HELD @2026-08-27 — branch `lane/ship`, blocked on external access.**
   Task: dry-run the real README quickstart end-to-end — `docker compose up`
   → add MCP server → `solve_task` twice, second citing precedent (the last
   genuinely unverified checklist row; independent of CORE-B's
   `bootstrap_demo.py` work). Blocked before starting: this machine (the
   `sl-ship` worktree's host) has no Docker anywhere — no `docker` binary, no
   Docker Desktop install, no Docker Windows service, no WSL distro installed
   (`wsl -l -v` reports none). Confirmed via `Get-Service *docker*` (empty),
   `Get-Command docker` (not found), and a filesystem check for `Docker
   Desktop.exe` (absent) — not just a `docker --version` PATH miss.
   Correction to Lane INFRA's entry above: that entry's "Docker 29.7.2 +
   Compose v5.3.1 are installed ... only the Desktop daemon was stopped"
   finding is true on *whichever machine INFRA ran on* (Chaitanya's, per
   `proj_status.md`'s "who's who"), not on this one — both worktrees happen
   to sit under a `C:\Users\user\...` path so the two are easy to conflate;
   this lane's own filesystem/service checks above are specific to the
   `sl-ship` host and found nothing.
   Founder asked directly (AskUserQuestion, not guessed): get access to
   Chaitanya's Docker machine, install Docker locally here, or dry-run the
   MCP-server/solve_task half only against the real (non-Docker) credentials
   already sitting in `backend/.env` (populated `DATABASE_URL` +
   `STEALTHLAB_MCP_TOKEN` on this host, likely hosted Postgres per
   `backend/README.md`'s Supabase note) and flag `docker compose up` as
   still unverified from this lane. Founder chose: **hold** — do not proceed
   on any of those paths yet, wait for coordination with Chaitanya.
   Resuming this: needs either (a) remote/SSH access or credentials to
   Chaitanya's Docker-equipped machine, or (b) an explicit founder go-ahead
   to install Docker on this host, or (c) a founder go-ahead to dry-run only
   the non-Docker half and report the compose gap as still Chaitanya-only-
   verified.

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

7. `[x]` done @2026-08-27 — branch `lane/ship` **doc-accuracy sweep** (direct
   kickoff, three mechanical fixes, all independently verified before commit):
   (a) commLLM.md cp437/UTF-8 mojibake — `text.encode('cp437').decode('utf-8')`
   on the whole file; git diff shows exactly 78 changed lines, every one a
   plain dash/arrow/middle-dot/other real punctuation, nothing else moved;
   post-fix grep for 'Γ'/'┬'/'├' returns zero matches. (b) root README.md:
   "8 tools" → "9" (confirmed against
   packaging/tests/test_server_offline.py::test_all_nine_tools_registered's
   9-name list), added a `check_procedure` table row (description matches
   demo.md C5's language), reworded "What it's becoming" so only
   explain_decision/explain_failure are described as forthcoming —
   check_procedure folded into "what runs today". (c) commLLM.md §1 table:
   check_procedure Status "**new**" → "built" (explain_decision/
   explain_failure untouched, still "**new**"); "Last updated" header bumped
   2026-08-25 → 2026-08-27. Scoped grant for this task only: root `README.md`
   + `commLLM.md` (both otherwise unowned). Full offline suite:
   **1368 passed / 115 skipped / 0 failed** — identical to the pre-existing
   baseline (CORE-B's check_procedure item above), confirming zero
   regressions from a pure doc/text change. No blocking questions hit —
   everything in the kickoff held up exactly as described.
   POST-PUSH FINDING (not a SHIP regression — this commit touches zero
   backend/** files, mathematically cannot affect Python runtime behavior):
   two subsequent full-suite runs (post-rebase, same command, same tree)
   BOTH reproduced identically — 1461 passed / 19 skipped / **15 failed**
   (test_procedures_e2e.py x14 + test_schema_drift.py's real-DB-enum check),
   not the clean 1368/115/0 baseline. Same failing set both times, so this
   is a deterministic order-dependent leak on the CURRENT main state, not
   network flakiness. Root cause: `app/mcp_server/server.py`'s module-level
   `load_dotenv()` sets a real DATABASE_URL (this worktree's Supabase
   credential) process-wide on import; `tests/test_mcp_check_procedure_offline.py`
   carries a documented env-snapshot/restore guard specifically to stop this
   leaking into later-collected e2e modules (see that file's own docstring,
   check_procedure item above — fixed a 69-failure version of this exact
   bug once already) but it is evidently NOT fully effective: confirmed the
   leaked DATABASE_URL survives into test_procedures_e2e.py/test_schema_drift.py
   collection two full runs running. Isolated re-run of one affected test
   skips cleanly (no leak outside full-suite collection order), consistent
   with an incomplete restore rather than a standalone bug in either failing
   file. Out of this lane's scoped grant to fix (owns README.md/commLLM.md
   only this task; the guard lives in backend/tests, CORE-B/integrator
   territory) — flagging here for whoever owns that guard next, since it's
   reproducible now on main and will surface in every full-suite CI-style
   run, not just occasionally.

### Lane INFRA - Docker boot test (opened 2026-08-27, scoped grant for this task)

Scoped ownership for this task only: `docker-compose.yml`, `backend/Dockerfile`,
`.dockerignore`, `backend/scripts/docker_healthcheck.py` (otherwise-unowned files).
No edits outside those four. Commit prefix `infra:`.

1. `[x]` **DONE @2026-08-27** - `docker compose up -d` boot test. The compose
   stack was written and schema-validated last wave but never booted (see
   `PRODUCTION_READINESS.md` "Install path - now real, but never boot-tested").
   Fix whatever breaks.
   FINDING (pre-work, corrects a doc claim): `PRODUCTION_READINESS.md` states
   "Docker is not installed anywhere in this build" and the v0.1 checklist in
   `proj_status.md` blames Docker access for two open items. That is stale as of
   today on this machine - Docker 29.7.2 + Compose v5.3.1 are installed; only the
   Desktop daemon was stopped. Both docs need a one-line correction; not doing it
   here because neither file is in this lane's grant (see the correction note at the end of this section).
   RESULT: booted clean on the FIRST attempt - zero fixes required to
   `docker-compose.yml`, `backend/Dockerfile`, `.dockerignore`, or
   `docker_healthcheck.py`. The repo-root build context reasoning held: the
   `experiments/swebench_pro/` sibling import resolved and the server did not
   crash-loop. Image `stealthlab-backend` built in ~59s (pip layer). Both
   containers report `(healthy)`; `stealthlab-db-1` on 127.0.0.1:5433,
   `stealthlab-backend-1` on 127.0.0.1:8765.
   Auth gate verified from the HOST, not just the container healthcheck:
   `curl -X POST http://127.0.0.1:8765/mcp` -> **HTTP 401**, which is the
   documented "healthy" signal (serving AND auth enforced).
2. `[x]` **DONE @2026-08-27** - Migration chain 01->30 on the disposable
   compose DB. This closes the item CORE-A queue 2 has carried as "BLOCKED on
   external - awaiting Chaitanya's Docker" since Band 1. CORE-A's entry says
   01->23; main has 30. Not editing CORE-A's queue - integrator can close it
   from the engine output below.
   ENGINE OUTPUT (`python scripts/migrate.py --status`, in-container, against
   a volume created seconds earlier):
   all 30 files report `applied`, 0 pending, 0 errors -
   01_ontology, 02_loop, 03_access, 04_governance, 05_decomposition,
   06_generated_files, 07_agents, 08a_graph_workflow_execution_type,
   08b_graph_workflow_execution_rest, 09_seed_internal_agents,
   10_code_sourced_agents, 11_fix_embedding_joint_drift,
   12_trace_ingestion_pipeline, 13_claim_subject_index, 14_observations,
   16_state_projection_index, 17_episode_project_columns, 18_procedures,
   19_procedures_embedding, 20_procedure_extraction, 21_band1_contracts,
   22_band1_review_fixes, 23_plan_persistence, 24_evidence,
   25_universal_changesets, 26_replayability, 27_failure_routing,
   28_identity, 29_rls_backstop, 30_verified_requires_evidence.
   (Numbering skips 15 on purpose; 30 files, not 31.)
   Idempotency proven: a second bare `migrate.py` apply against the same DB is
   a silent no-op, exit 0. Resulting schema: 41 base tables, extensions
   `vector 0.8.6` + `btree_gist 1.7` + `pgcrypto 1.3`. Migration 01's
   `CREATE EXTENSION vector` succeeded, confirming the pgvector image pin is
   doing real work.
3. `[x]` **RUN @2026-08-27, but it does NOT test what the brief assumed** -
   `python scripts/bootstrap_demo.py` completed clean, **exit 0, no error, no
   stop**. It did not reach a WOULD_REFUSE step because the script contains no
   such step. See blocking question #1.
   What it actually did: `Seeded 'example_generic_pipeline': 3 tasks, 4 edges`
   + `Inserted 10 traces ... 80% error rate`. Post-run row counts on the fresh
   DB: traces 10, task_nodes 3, knowledge_nodes 2, and **episodes 0,
   observations 0, procedures 0, evidence 0, agent_traces 0, trace_events 0**.
   So it seeds the debate/bottleneck product demo and leaves every
   substrate/knowledge-layer table empty.

**BLOCKING QUESTION #1 (INFRA -> integrator/founder). Proposed default: treat
`demo.md` checklist line 62 as UNMET rather than met-by-exit-0.**
`demo.md:62` specifies `bootstrap_demo.py` should run "the scripted two-phase
story: phase A produces traces->procedures; phase B retrieves precedent AND
triggers a `WOULD_REFUSE` after the fixture breaks a precondition claim." The
script on main (82 lines, `backend/scripts/bootstrap_demo.py`) is the older
debate-era seeder: it writes a workflow + 10 traces to cross `_DEMO_RULES`'
error-rate threshold, and grep finds no occurrence of `refus`, `WOULD_REFUSE`,
`check_procedure`, or `procedure` anywhere in it. Phase A and phase B are not
implemented, not merely blocked on CORE-B's `check_procedure` wiring.
Consequence if defaulted the wrong way: this item could be marked green on an
exit code that proves nothing about the capability demo.md is selling. Note the
row counts above also mean Band 2's founding-loop exit criterion (trace ->
episode -> observation -> claim -> procedure, hand-audited) remains unexercised
on a fresh DB - the substrate tables are born empty and nothing on main fills
them in one command.

**Correction to two integrator-owned docs (flagged, not edited - not this
lane's grant).** `PRODUCTION_READINESS.md` says "Docker is not installed
anywhere in this build" and lists the migration-chain item as "blocked on
Docker access"; `proj_status.md`'s v0.1 checklist says the same and marks the
compose files "never boot-tested, no Docker installed anywhere in the fleet
yet." All of that is now false: Docker 29.7.2 + Compose v5.3.1 are installed on
this machine (only the Desktop daemon was stopped), and both items are now
verified green. Suggested: flip both checklist rows and drop the
Docker-availability caveat.

Lane INFRA status: all three assigned items executed. No fix was needed to
any of the four granted files - the boot test passed as written.

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
3. `[x]` done @2026-08-27 -- lane/core-b **CORE-B -> whoever owns/next touches
   `backend/app/services/observations.py`** (unowned file, outside every
   lane's granted path list -- same file CORE-A took a scoped disclosed
   exception on for Band 2.8's `promote_observation_to_claim`): apply terse-
   label discipline to `_SEMANTIC_LABEL_SYSTEM_PROMPT` (consumed by
   `extract_model_observation`), mirroring MEASURE queue item 6's live
   error-floor finding.
   *(RESOLVED: integrator granted CORE-B a scoped exception for this one
   prompt. Shipped verbatim-mirrored wording from MEASURE's already-proved-
   out `live_extractor.py` fix (fourth wave, above): "Aim for 3-6 words,
   never more than 8: subject + past-tense verb, nothing else" + the same
   four good-example labels + an explicit ban on quoting file
   paths/commands/hashes and on parentheticals/explanations "those pad the
   label without changing its meaning and are wrong even when true." NONE
   contract and the rest of the surrounding function untouched. Regression
   test `test_prompt_enforces_terse_gold_style_labels` added to
   test_observations.py, pinning the wording the same way MEASURE pinned
   theirs. 16/16 test_observations.py green. Full offline suite: 1268
   passed / 2 skipped / 111 failed -- the 111 is the same pre-existing
   drifted-DB e2e gap this board already has on record [H2's entry above:
   "1192 passed / 1 skipped / 111 failed, the 111 IDENTICAL per-file to a
   stashed clean tree"; reproduced here even with DATABASE_URL explicitly
   cleared before invocation, confirming it's env-level (.env loading
   mid-run) not this change -- zero regressions in the non-DB 1268].
   Production and eval now share one terse-label contract; no drift.)*
## Founder dependencies (blocking nothing currently)

| Ruling | Blocks | State |
|---|---|---|
| D1 capability bands | nothing (default live in Â§16, tagged) | open |
| D4 deletion mechanism | Band 5.6 only | open |

## OpenRouter budget wall (2026-08-27, MEASURE) - READ BEFORE ANY LIVE SWEEP

Two SEPARATE things died, both confirmed this session via zero-cost read-only
checks (`GET /api/v1/models` needs no key; `GET /api/v1/key` is account-status,
not a completion call - neither line item below cost anything):

1. **`ox-alpha` (the harness's primary model, `DEFAULT_MODEL_CHAIN[0]` in
   `openrouter_arms.py`) is GONE from OpenRouter's public catalog as of
   today** - `GET /api/v1/models` returns 417 models total and zero of them
   match `ox` or `alpha` in id or name. CORRECTED TIMELINE (per RESEARCH's
   independent verification of this lane's own model-decides sweeps,
   `.scratch/research/model-decides-verification.md` - this lane's own prior
   board text got this wrong, see the correction Log entry below): `ox-alpha`
   served real billed calls normally as recently as sweep #3 (commit
   `40121b6`, 18:17 yesterday - 41 successes / 9 retried-429s / zero 404s),
   but had ALREADY gone to HTTP 404 on 100% of attempts by the model-decides
   run1 sweep (23:47) and stayed that way through run2 (02:29) - two
   sweeps, 144 attempts, zero successes, zero 429s, all 404 (model not
   found), immediately falling through to `openai/gpt-4o-mini` every time.
   Today's catalog check (this entry) independently corroborates that dead
   window from the other direction: whatever `ox-alpha` was, it is not
   resolvable AT ALL right now, hours after the last observed 404. Not
   investigated further (not this lane's account to administer), just
   recorded so the fallback chain's first entry isn't silently assumed to
   still work.
2. **The account itself has no purchased credits** (`GET /api/v1/key` ->
   `"is_free_tier": true`, `"usage_daily": ~$0.067`, no `limit`/
   `limit_remaining` set at the key level - there is simply no balance to
   spend against). This matches the founder's own report of "no spendable
   balance right now." `openai/gpt-4o-mini` and `anthropic/claude-3-5-haiku`
   (the fallback chain's other two entries) are BOTH real paid models with
   per-token pricing - every call this session's sweeps made to them was
   billed against whatever small balance existed, and that balance is now
   what's gone. Nothing about this is a code bug; it's an account state.

**Genuinely `:free`-tagged models DO exist** (20 found in today's catalog,
verified by `pricing.prompt == pricing.completion == "0"` AND id suffix
`:free` together - `pricing == "0"` alone is NOT sufficient, see the
`google/lyria-3-*-preview` trap below) - but this account's free-tier status
caps them at **OpenRouter's documented free-tier rate limit: 20 requests/
minute, 50 requests/day** (confirmed against OpenRouter's own docs this
session; the >=$10-lifetime-credit tier gets 1000/day instead - this account
qualifies for neither escape hatch since it has $0 lifetime purchased). That
50/day ceiling is the real constraint on any $0 testing path, not model
availability.

**Quality-usable candidates for OUR shape (text->text or text+image->text,
JSON-structured extraction/decision output)**, from today's catalog, with the
lightest/most-on-topic ones marked:

| model id | notably | for our task |
|---|---|---|
| `liquid/lfm-2.5-2.6b:free` | 2.6B, own description says "suited for... data extraction" | closest fit, cheapest to run fast |
| `z-ai/glm-5.2:free` | 1M ctx, `structured_outputs`+`response_format` supported | strong JSON-mode candidate |
| `nvidia/nemotron-3-super-120b-a12b:free` | 120B/12B-active, `structured_outputs` supported | bigger, still free |
| `nvidia/nemotron-3.5-lightning:free` | 3B active, "high-throughput agentic workloads" | fast/cheap-quality tradeoff |
| `cohere/north-mini-code:free` | 30B/3B-active, first-party AGENTIC CODING model | plausible for trace-event reasoning |
| `poolside/laguna-s-2.1:free` / `laguna-xs-2.1:free` | coding-agent models | untested here, plausible |
| `minimax/minimax-m2.7:free` | text->text, "autonomous... productivity" | plausible general candidate |
| `openrouter/free` | meta-router, randomly picks a free model per call | convenient but NON-deterministic served-model per call - breaks `served_by_model` attribution in our spend ledger, use named models above instead |

**NOT usable despite showing `0`/`0` pricing - a silent-cost trap**:
`google/lyria-3-clip-preview` and `google/lyria-3-pro-preview` are MUSIC
GENERATION models (`output_modalities: [text, audio]`); their own
descriptions say "$0.04 per clip" / "$0.08 per song" - billed on a
non-token dimension the `pricing.prompt`/`pricing.completion` fields don't
capture at all. A filter on zero token-pricing alone would have picked
these up as "free"; they are not. Also excluded on relevance, not cost:
`nvidia/nemotron-3.5-content-safety:free` (a moderation/guardrail
classifier, wrong tool for extraction) and the omni/multimodal-input-only
entries where text is just one of several input modalities we don't need.

**Feasibility math against the 50/day cap**: `live_extractor.py`'s
single-arm, one-call-per-excerpt shape over the 42 error-floor fixtures
(occasional +1 repair round) fits in ONE day per prompt variant - the
prompt-variant work above (4 candidates) would need roughly 4-5 days of
free-tier quota to run all of them, one variant per day, if paid funds
never return. `run_real_arms.py`'s 3-arm x 24-task model-decides sweep
(72+ episodes/day minimum even with zero repairs) does NOT fit in one
day under this cap at all - would need `--auto-resume` splitting across
at least 2 days, or a `--task-ids` subset per day.

**IMPORTANT CAVEAT if a free model is ever substituted in**: swapping the
extraction/decision model away from `ox-alpha` breaks apples-to-apples
comparability with every committed baseline this lane has produced so far
(the v1/v2 semantic_label error-floor reports, RUN #1/model-decides
run1/run2) - a free-model run is a DIFFERENT model's performance, useful
only as a directional signal about PROMPT shape (does few_shot/
vocab_discipline/strict_noun_phrase move the needle AT ALL, on ANY model),
never as a continuation of the same ox-alpha-anchored series. Any such run
should be labeled with its actual served model in the results, never
folded into the same comparison table as the ox-alpha baselines without
that label.

**Ready-to-run when authorized** (zero code changes needed - both CLIs
already expose `--models` as an override; this is exactly the same
readiness posture as the prompt variants above - prepared, not executed,
no spend without explicit confirmation first):

    python live_extractor.py --prompt-variant few_shot \
        --models liquid/lfm-2.5-2.6b:free \
        --out live_extractor_preds_few_shot_free.jsonl \
        --spend-log live_extractor_spend_few_shot_free.jsonl

(swap the `--models` value for any id in the table above; swap
`--prompt-variant` per `semantic_label_prompt_variants.PROMPT_VARIANTS`).

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
- **SHIP (2026-08-26, second wave): first real public scoreboard run** — full
  record in SHIP queue item 4. Read RESEARCH's `run1-verification.md` first
  per the kickoff instructions and answered its open question: yes, the
  generator needed a second, non-n caveat type for INTERPRETIVE validity
  (construct validity of the stale_refusal metric, not sample size) —
  shipped as `stale_refusal_attribution()`, which reads openrouter_arms.py's
  own `record_refusal` reason strings to split gate-mechanical from
  model-initiated stale-refusal credit, plus a "Stale-refusal attribution"
  table on both pages and a caveat that fires only when an arm's entire
  stale_refusal credit is gate-mechanical. Not raised as a blocking
  question — the research report's code-level evidence made this a clear
  engineering call; flagged non-blocking for founder override on
  phrasing/threshold. Ran against all three real sweeps that exist
  (RUN #1 + MEASURE's run2/run3, gitignored/machine-local) instead of only
  synthetic fixtures, which caught two real correctness bugs before ship
  (raw-event vs per-task counting; task_id-string vs object-identity row
  matching — the latter hit for real on run3's duplicated `mic-pdf-003`
  resume artifact). Confirmed after both fixes: every arm's attribution
  total foots exactly to its stale_refusal count on all three runs; the
  interpretive caveat fires on RUN #1 only (C's one model-facing task,
  mic-refund-003, came back unparseable there) and correctly does NOT fire
  on run2/run3 (same task returned a genuine self-reasoned model refusal in
  both later sweeps) — the caveat tracks real evidence per sweep, not a
  RUN #1-specific rule. The shipped generator's own McNemar comparisons
  never reproduced the old "p~0.031" figure on any run (pass/fail only,
  always under the small-n floor). Packaging suite 88/88 (+8 offline
  tests, zero regressions). Output: `.scratch/ship/{run1,run2,run3}/
  public_scoreboard.{md,html}`.
- **SHIP (2026-08-27): model-decides tier section** — full record in SHIP
  queue item 5. Landed the founder's ask to surface MEASURE's
  model-decides tier (genuinely model-decided stale-refusal evidence, not
  gate-mechanical) as a clearly labeled new section, kept separate from
  the untouched run1/run2/run3 arms evidence. Built to auto-discover every
  committed `model_decides_report*.json` (no generator change needed for
  future sweeps) — useful sooner than expected: started with 1 report,
  MEASURE's run2 landed mid-session, RESEARCH's independent verification
  landed right after. **Headline finding surfaced along the way**: the
  report schema has no `served_by_model` field, and RESEARCH's
  verification (merged, `.scratch/research/model-decides-verification.md`)
  confirmed BOTH existing sweeps actually ran on `openai/gpt-4o-mini`, not
  `ox-alpha` — every `ox-alpha` call 404'd (non-retryable) across >=2h42m,
  a fact undisclosed in either sweep's own board entry. The numbers
  themselves are solid (p=0.0117/p=0.0215 independently reproduced exactly,
  refusals confirmed genuinely model-decided via journal audit) — this is
  evidence about `gpt-4o-mini`'s detection behavior specifically, not yet
  ox-alpha's. Shipped caveat states this by name and by date rather than
  hedging generically, since it's now a confirmed, merged fact, not a
  guess. **Recommend confirming ox-alpha's current OpenRouter model id
  before the next model-decides sweep.** Packaging suite 95/95 (+7 offline
  tests, zero regressions). Demo output (run1/2/3 untouched):
  `.scratch/ship/model_decides/public_scoreboard.{md,html}`.
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
- MEASURE (2026-08-26, third wave): sweeps #2/#3 + live error-floor pass
  done on `lane/measure` — full records in MEASURE queue items 5/6.
  1. SPEND LEDGER SUMMARY for the treasurer: run2 $0.4269 [113 attempts],
     run3 $0.3189 [50 attempts], extraction $0.1274 [53 attempts] —
     **$0.8732 total**, under the ~$1 lane cap. Per-attempt rows in
     real_spend_run2.jsonl / real_spend_run3.jsonl /
     live_extractor_spend.jsonl beside the results files; every 429 and
     network error is a row.
  2. STALE-REFUSAL COLUMN = SURFACE BEHAVIOR ONLY (corrected per
     RESEARCH's run1 verification before pushing): C's 7/7 refusals in
     BOTH new sweeps are substrate-gate-automatic by construction [a
     gate-blocked offer becomes a recorded refusal without the card ever
     reaching the model] and real-B has NO refusal path at all — so
     7/7-vs-0/7 repeats run1's fixture-determined pattern and is NOT
     model staleness detection. The model-decided numbers stand
     separately: resolution run3 C 9/11 vs A 8/11 vs B 7/11, false-reuse
     B-only [2 then 1]; nothing individually significant this session;
     no public phrasing yet.
  3. Instrument correction disclosed in queue item 5: completion-cap 700
     was shearing JSON mid-reply [run2 ledger evidence: billed rows at
     exactly tokens_out=700 unparseable-after-repair]; raised to 1400
     with regression test. RUN#1 comparability caveat recorded there too.
  4. Error-floor LIVE baseline recorded [queue item 6]: semantic_label
     layer P 0/17 R 0/4 is the dominant cost — verbosity vs terse golds
     under Jaccard>=0.5. Needs a founder/integrator ruling before Band 3:
     (a) prompt-side terse-label discipline [proposed default], (b)
     rubric recalibration to grade information coverage over brevity,
     (c) accept as the honest floor and gate model-extractor work on it.
  5. Harness suite 176/176 green [163 prior + 13 new]; zero backend edits
     [git diff origin/main -- backend/ empty]. Rebased onto origin/main;
     fast-forwarded main per OVERNIGHT MODE after suite green.

- CORE-B (2026-08-26): **WAVE-3 debate-panel OpenRouter wiring** done on
  `lane/core-b` -- full record in queue item 7. Notes:
  1. `OpenRouterAgent` reuses the arms-pattern full-jitter backoff wholesale
     but stands down `_call_with_retry`'s generic wrapper via a
     `manages_own_retries` marker -- stacking both retry ladders would be
     ~4x worst-case sleeps against an already-saturated shared pool.
  2. Three live-run findings pinned as named machinery: `json_mode=True`
     at the factories (frontier models ramble past completion budgets in
     prose before JSON starts); `REASONING_BUDGET_CAPS` capping ox-alpha's
     reasoning tokens (it was burning the entire plain completion budget
     on invisible chain-of-thought and returning empty content -- verified
     fixed in one live probe); a stale-default guard (claude-3-5-haiku is
     RETIRED upstream -- clean 404 on first smoke -- and catalog listing
     does not imply endpoint availability, deepseek-chat-v3.2 was listed
     but rejected) -- hence probe-then-pin defaults plus a construction-time
     roster tripwire test. Roster: ox-alpha / gpt-4o-mini / claude-haiku-4.5
     panel + gemini-2.5-flash judge, all live-probed on the founder account
     2026-08-26.
  3. Full suite: **1243 passed / 115 skipped / 0 failed** (prior baseline +
     36 offline tests in tests/test_debate_openrouter_offline.py, the +1
     skip being the gated live smoke). Live smoke PASSED end-to-end against
     the real endpoint: 3 seats x 2 rounds, 6 turns, all engine-parsed VADA
     JSON, 0 transport failures, ~14s wall, ~$0.02 total spend incl.
     diagnosis probes. One disclosed out-of-grant touch:
     tests/test_general_compute.py, one line (the new third provider flag
     needed to read False in the nothing-configured fallback test -- a
     truthy MagicMock was routing otherwise).
  4. **Recovery note:** this item was finished and written up locally
     (code + tests + this board entry) but never committed -- the
     session's free-model access (`x-preview-f-free`) died before CORE-B
     could run `git add`/`commit`/`push`. Recovered from the uncommitted
     sl-core-b worktree state by the integrator (Claude, picking up from
     the dead opencode/ox-alpha session) on 2026-08-26 evening; code and
     this write-up are exactly as CORE-B left them, unedited. Founder:
     please re-run the offline suite once in sl-core-b before pushing, as
     a sanity check on the recovery -- the numbers above are CORE-B's own
     report, not independently re-verified by the integrator this pass.

- MEASURE (2026-08-26, fourth wave): **CLAUDE.md Task 1 — semantic-label
  terse-prompt ruling implemented** on `lane/measure`. Per the board's own
  recommended default (option a), tightened `live_extractor.py`'s
  `semantic_label` instruction: 3-6 word subject+past-tense-verb labels in
  the gold house style, explicit examples from the actual golds, and an
  explicit ban on quoting file paths/commands/hashes or adding
  parentheticals/explanations — those pad meaning without changing it and
  are wrong even when true. Re-ran the LIVE extractor over the same 42
  fixtures fresh [`live_extractor_preds_v2.jsonl`, 42/42 parsed, 0
  unparseable, $0.0601 / 84 attempts] and re-graded
  [`error_floor_results_v2.jsonl` + `_detail.json` — v1's committed
  baseline files left untouched, this is a parallel artifact, not an
  overwrite]. **DELTA**: semantic_label P 0/17→2/13 (0.0→0.154), R
  0/4→2/4 (0.0→0.500), F1 0.0→0.236; overall P 25/44→28/46 (.568→.609), R
  25/30→28/30 (.833→.933), F1 .676→.7368. The needle moved — recall
  doubled from zero — so no rubric-recalibration escalation needed.
  Residual analysis (not further prompt-tuned without a ruling, per the
  house instruction not to guess at rubric changes unilaterally):
  (a) one clean win — ef-sem-004 "database container started" now matches
  its gold VERBATIM; ef-sem-001 clears Jaccard 2/4=0.5 exactly (terse
  synonym overlap) — terseness alone fixed these two; (b) one vocabulary
  miss survives terseness — ef-sem-003 gold "continuous integration
  pipeline configuration added" vs terse pred "CI workflow configuration
  updated" is still Jaccard 1/8=0.125 [synonym/abbreviation choice, not
  verbosity — terse-prompt discipline cannot fix vocabulary divergence];
  (c) NONE-contract now UNDER-fires once — ef-sem-002 (`rm -rf dist/`)
  got no label at all this run [FN missing_no_candidate], the mirror image
  of over-labeling; (d) precision on the type stays low (2/13) mostly
  because the model now emits confident terse labels on the FILE family
  fixtures (ef-file-001/002/003/007/008/009), which the fixture authors
  never gave semantic golds — an over-application issue orthogonal to
  wording, flagged here rather than patched into the prompt unasked.
  CONFOUND DISCLOSED: command_executed also moved P 5/5→6/11 this run
  [5 test-run commands got a duplicate command_executed observation
  alongside their test_run one] — inspected the raw predictions, this is
  live-model sampling variance across a fresh LLM run, NOT caused by the
  semantic_label prompt edit (that section of the prompt is untouched);
  reported for honesty, not attributed to this change.
  SPEND: $0.0601 this pass; **session-to-date total $0.9333**, still under
  the informal ~$1 mark but tight — flagging for the integrator/founder
  before any further live-model passes this session. One regression test
  added (`test_prompt_enforces_terse_gold_style_labels`) pinning the
  terse-label wording so a future edit can't silently relax it back to
  free-form prose. Harness suite 177/177 green [176 prior + 1 new]; zero
  backend edits. Checked for RESEARCH's model-decides-tier design doc
  (CLAUDE.md Task 2) — not yet landed in `.scratch/research/`; not
  blocking, will check again next wave per the kickoff instructions.

- CORE-A (2026-08-26, ninth wave): **Founder-rulings write-up** (kickoff
  task, not a numbered queue item — docs only, no schema/app edits). Wrote
  `.scratch/core-a/founder-rulings-needed.md` covering the three
  accumulated founder-only calls. Finding: D1 and D4 were NOT actually
  open — both were ratified by founder quiz 2026-08-25 (commit
  3c1de7b/ddb3894) and are already folded into spec v4 (§16, §34b); D1 is
  already implemented in CORE-B's capability.py. The board's Founder
  dependencies table simply never got updated after ratification —
  corrected above. Only the third item (tenant_id on
  procedures/agents/observations, this lane's own WAVE-3 "honest
  exclusion") is a genuine open founder call; full tradeoffs + my
  recommended default (Option A: full tenant_id, siloed per-org, same
  db/28/29 pattern) are in the doc. No suite to run (docs-only).

- CORE-A (2026-08-26, tenth wave): **Band 4 entry scoping note** (kickoff
  task, remainder of the wave — docs only). Wrote
  `.scratch/core-a/band4-entry-scoping.md`. Headline finding: Band 4 entry
  is gated by ROADMAP's own ground rule 4 + Band P's exit criteria — P3's
  acceptance test ("fresh install → 1 day real work → ≥1 procedure reused
  with visible provenance + §40 arm-C beating arm-B") — not by a Band 1
  technical checklist. P3 is `⬜` in proj_status.md and, per RUN #1's
  independent verification, doesn't have a clean pass available yet either
  (the arm-C-beats-arm-B evidence was frame-dependent and gate-automatic,
  not model-level). Band 1 prerequisites Band 4 items actually name (1.3
  scope columns, 1.10 append-only-at-birth, 1.6 embedding stamps) are all
  already shipped, so once the P3 gate opens, items 2/3 (partitioning, read
  replicas) are the cheapest entry points; item 8 (utility-based
  retirement) stays separately blocked on Band 3.4 (utility accounting,
  still open per proj_status.md) regardless of the gate. Also flagged: I
  could not find the "~85% Band 3 done" figure cited in this wave's kickoff
  anywhere in the repo — closest tracked number is proj_status.md's ~65%,
  worth reconciling. No suite to run (docs-only).

- CORE-B (2026-08-26, WAVE-3 terse-label task): **assessed, no CORE-B code
  change this item** -- read MEASURE queue item 6 in full, then checked
  `backend/app/services/procedure_extraction/**` (my owned grant) for an
  equivalent semantic-label prompt. It has none of the kind MEASURE tested:
  the only prompt in-grant is `strategies.py`'s `_ABSTRACTION_SYSTEM_PROMPT`,
  a different task (procedure CAPABILITY/STEPS generalization, not graded by
  the error-floor fixtures, already terse by construction -- one abstract
  sentence + short generalized phrases) -- left untouched, no invented work.
  The real production counterpart to MEASURE's finding is
  `_SEMANTIC_LABEL_SYSTEM_PROMPT` in `backend/app/services/observations.py`
  (`extract_model_observation`) -- an unowned file outside every lane's
  granted path list, so out of scope for a CORE-B edit under house rules.
  Filed as cross-lane request #3 above rather than touching it. No other
  open CORE-B queue item exists this wave (all queue + HARDENING items above
  are `[x]`) -- nothing else picked up.
  **POST-REBASE UPDATE**: MEASURE's fourth-wave entry above (landed after
  this was written, seen only on rebase) already tightened
  `live_extractor.py`'s harness-side prompt to 3-6 word terse labels;
  cross-lane request #3's "not yet landed" clause is now stale for the
  harness half specifically -- only the production half
  (`observations.py`'s `_SEMANTIC_LABEL_SYSTEM_PROMPT`, still unowned) is
  outstanding. Request #3 left as-is otherwise; its owner should match
  MEASURE's now-landed wording (3-6 word subject+past-tense-verb, no
  quoted paths/commands/parentheticals) rather than reinvent it.
- RESEARCH (2026-08-26, model-decides tier design): done - see Lane RESEARCH
  item 5 and `.scratch/research/model-decides-tier-design.md`. One-line summary
  for the integrator: RUN #1's stale-refusal split was gate-decided, not model
  evidence (this lane's own prior finding); the fix - `substrate_bypasses_gate`
  - already exists in the harness and was used exactly once, for an unrelated
  purpose (mic-refund-003, unparseable). This design proposes a 24-task
  single-offer trap+control tier reusing that exact mechanism across all 4
  existing domains, needing ZERO changes to mcp_surface.py, openrouter_arms.py,
  or scoring.py - purely fixture content (cross-lane request to MEASURE, not an
  edit) plus a report-layer analysis script this lane can write once real rows
  exist. Sizing is reasoned from mcnemar_power.py's own math since no run2/run3
  data exists yet (CLAUDE.md's kickoff assumed it would - flagged, not
  fabricated). No files outside lane paths touched.

- MEASURE (2026-08-26, fifth wave): **model-decides stale-procedure tier
  implemented and swept live** on `lane/measure` (CLAUDE.md Task 2,
  implementing RESEARCH's `.scratch/research/model-decides-tier-design.md`).
  Design's own §2/§5 claim held: zero changes needed to mcp_surface.py or
  scoring.py — `substrate_bypasses_gate` already makes the model's own
  reuse/refuse choice, not the gate, decide the outcome. ONE small,
  disclosed exception to "purely fixture content": `openrouter_arms.
  situation_text()` gained a fallback to a task-embedded `situation` field
  (checked AFTER the existing scenario lookup, so mic-* behavior is
  byte-identical) — necessary because this tier's 24 tasks would blow past
  the micro pack's mandated 8-12 scenario count (test_micro_fixtures.py's
  `test_scenario_count_within_mandate` / `test_every_task_has_a_scenario_
  and_vice_versa`) if they shared fixtures/micro/scenarios.json. Kept the
  whole tier in a NEW `fixtures/model_decides/` directory instead — its own
  tasks.json (24 tasks, no scenarios.json at all) plus content-identical,
  drift-tripwire-tested copies of procedures.json/rag_corpus.json — so
  fixtures/micro/** is untouched (verified: `git diff` there is empty).
  SHAPE: 3 trap (stale_offer = the domain's stale:true procedure, correct
  =refuse) + 3 control (applicable_procedure = the domain's stale:false
  procedure, correct=reuse — deliberately NOT authored via `stale_offer`,
  which the micro pack's own validator requires to be ground-truth stale;
  `applicable_procedure` is offered identically from the model's POV and
  needs no gate bypass to begin with, so it's the semantically correct
  field) per domain x 4 domains, situational specifics varied per
  replicate (near-boundary dollar amounts/days, pip/pdfplumber/python
  versions) so the model must check the stated assumption rather than
  pattern-match an extreme number. `model_decides.py`: pure analysis layer
  reusing mcnemar_power.discordant_counts/format_pair VERBATIM (zero
  changes there either) for the paired B-vs-C sensitivity metric over trap
  tasks, plus a C-only false-refusal specificity guardrail over control
  tasks — both computed from raw episodes + procedures.json ground truth,
  never from task-schema role alone (defense-in-depth mirroring
  scoring.classify's own discipline). `run_model_decides.py` CLI
  (--validate-only / --results). 34 offline proving tests
  (tests/test_model_decides.py): fixture contract + breakage detection
  (dual-offer rejected, wrong prefix rejected, non-stale stale_offer
  rejected, missing bypass/situation rejected), hand-computed row-level
  and analyze()-level verdicts incl. a hand-verified McNemar scenario,
  zero-denominator honesty, cross-tier row skipping. Harness suite
  213/213 green [177 prior + 2 situation_text-fallback pins + 34 new].
  **LIVE SWEEP** (all 24 tasks, single pass, no resume needed — 24/24
  valid first try): **$0.0725** (144 attempts, 72 billed, 72 absorbed
  429s/network retries under the existing backoff). RESULT:
  sensitivity — B vs C on the 12 trap tasks: **11 discordant pairs (C-only
  avoided 10, B-only avoided 1), exact p=0.0117** (significant at α=0.05,
  q_observed=0.909, already exceeds n-for-80%-power@this-ratio=9).
  Specificity — C's false-refusal rate on the 12 control tasks: **0/12
  (0.000)** — C never refused a procedure it should have reused, so "C
  refuses everything" is ruled out as the explanation for its trap
  performance. Hand-verified every one of the 24 raw episodes against the
  report's discordant-pair accounting before trusting the number (detailed
  in this session's own audit, not just the script's say-so): the single
  B-only-avoided case is `dec-pdf-103`, where C neither refused nor reused
  the offered stale card at all (silently routed around it) while B's
  memory-following happened not to cause that particular failure — a real,
  disclosed edge case the strict refusal-based metric scores as "C did not
  avoid," which is the metric working as designed (design §5), not a bug.
  Committed: `model_decides_report.json` (the `analyze()` output, same
  "graded results tracked" precedent as error_floor's committed grading
  output); raw `model_decides_results.jsonl` / `model_decides_spend.jsonl`
  stay local/gitignored like every other run-output file, same as
  real_arms_results.jsonl.
  CAVEATS before any public phrasing (same discipline run1-verification.md
  demanded of the original tier): (1) this is a SINGLE sweep, n=1 per
  task — no repeat-sweep variance data for THIS tier yet; a second sweep
  (~$0.07 at this rate) before any headline claim is cheap and
  recommended, not yet run this session; (2) unlike the original RUN #1
  tier, every one of these 12 trap refusals is genuinely model-decided
  (bypass verified on, journal/episode fields hand-checked above) — this
  is the real thing the design set out to produce, not another
  gate-mechanical artifact, but it is still ONE sweep of 12 discordant
  pairs, not a settled result.
  SPEND: $0.0725 this pass; **session-to-date total $1.0058** — over the
  informal ~$1 mark now (was $0.9333 after Task 1); flagging plainly
  rather than burying it, per the same spend-discipline house rule as
  every prior entry. Rebase-then-push pending; harness suite 213/213
  green throughout; zero backend edits; zero edits to fixtures/micro/**
  or any other lane's owned paths.

- MEASURE (2026-08-27): **model-decides tier, second sweep** — founder-
  approved confirmation pass on the same 24 `fixtures/model_decides/`
  tasks, requested to check the first run's result before any public
  claim (same discipline `run1-verification.md` applied to the original
  tier). Fresh output files (`model_decides_results_run2.jsonl` +
  `_spend_run2.jsonl`, run1's untouched), analyzed with the unchanged
  `model_decides.py` / `run_model_decides.py`.

  | | run1 (2026-08-26) | run2 (2026-08-27) |
  |---|---|---|
  | trap discordant pairs | 11 (C-only 10, B-only 1) | 10 (C-only 9, B-only 1) |
  | exact p (B vs C) | 0.0117 | 0.0215 |
  | q observed | 0.909 | 0.900 |
  | control false-refusal | 0/12 (0.000) | 0/12 (0.000) |
  | spend | $0.0725 | $0.0722 |

  Both runs SIGNIFICANT at α=0.05, same direction, comparable magnitude —
  a repeat of run1's result, not a fluke of one sampling draw. Every one
  of run2's 24 raw episodes hand-verified against the report's discordant-
  pair accounting (same audit discipline as run1): the domain-level
  pattern is IDENTICAL in kind to run1 — all refund/dep/env trap triples
  fully C-refused vs fully B-failed (9 of the 10 discordant pairs), pdf
  triples mostly concordant with exactly one B-only-avoided case
  (`dec-pdf-103` again — C neither refused nor reused the offered card
  either run, a real and now-repeated model behavior on that specific
  replicate, not sampling noise on the metric side). Control tasks:
  false-refusal stayed at a clean 0/12 both times; `dec-dep-105` again
  saw C touch neither refuse nor reuse on that one replicate in both
  runs (resolved=False both times) — a candidate for follow-up if this
  tier gets a wave 2, not investigated further this session.
  Both runs' committed report artifacts: `model_decides_report.json`
  (run1) + `model_decides_report_run2.json` (run2); raw results/spend
  JSONL stay local/untracked (same convention as run2/run3 of the
  original tier). Harness suite unchanged, still 213/213 green (no code
  touched this pass, sweep + analysis only).
  SPEND: $0.0722 this pass; **session-to-date total $1.0780**.

- CORE-A (2026-08-27, eleventh wave): **SECURITY.md + DATA_STATEMENT.md**
  (founder-requested, repo root — docs only). Closes `demo.md` §3's last
  unchecked ship-checklist line; box checked there with a pointer.
  Threat model grounded in real code, not aspirational: bearer token gates
  WHO reaches the MCP server, not WHAT they can do once in — named the two
  concrete gaps this actually means (`apply_change_set` ungated write,
  `solve_task`'s caller-controlled `repo_path`) straight from
  `server.py`'s own docstrings, plus stdio's by-design no-auth. Redaction
  section states the module's own honesty verbatim: best-effort known-token
  + sensitive-path floor, not exhaustive — never softened for the public
  doc. Data statement's one real disclosure: embedding generation sends
  already-redacted text to a third-party provider (Voyage/Gemini, caller's
  own key) — the one network egress point v0.1's shipped path has;
  distinguished explicitly from telemetry-to-us, of which there is none
  (no phone-home code path exists at all in v0.1, not even opt-in — so
  "never train on user traces" is trivially true today and stated as a
  durable forward commitment, not a feature description). Vulnerability
  reporting defaults to GitHub private security advisories rather than
  publishing a personal email — flagging that choice here in case the
  founder wants a dedicated security@ address instead later, one-line
  change. No schema/app code touched.

- RESEARCH (2026-08-27, model-decides sweep verification): done - see Lane
  RESEARCH item 6 and .scratch/research/model-decides-verification.md.
  One-line summary for the integrator: the two headline numbers (sensitivity
  p=0.0117, specificity 0/12) are confirmed exactly, and the refusals are
  genuinely model-decided this time (journal-verified) - the design's core
  goal is met. But the sweep ran entirely on openai/gpt-4o-mini, not
  ox-alpha - every ox-alpha attempt 404'd (not 429, contra the board's own
  description) - so it is not comparable to RUN #1/#2/#3 on the model axis;
  MEASURE/founder should confirm ox-alpha's OpenRouter model id before any
  second sweep. Two smaller nuances also filed: a power-presentation
  gloss (required_n isn't a monotone power floor for this exact test) and
  a schema-capture gap on dec-pdf-103 that understates, not inflates, C's
  true detection rate. No second sweep exists yet to check variance.

- RESEARCH (2026-08-27, run2 addendum): the run2 sweep landed mid-
  verification (see MEASURE's entry above and Lane RESEARCH item 6's
  addendum) and has now also been independently re-verified with the same
  script - sensitivity p=0.02148438 (10 discordant, 1 B-only/9 C-only) and
  specificity 0/12 both CONFIRMED exactly, journal confirms genuinely
  model-decided refusals again. The ox-alpha-404/gpt-4o-mini-fallback issue
  flagged for run1 persists UNCHANGED in run2 (72/72 404, zero 429) and is
  still not disclosed in run2's own board entry - two sweeps now, same
  undisclosed model substitution both times. dec-pdf-103 and dec-dep-105
  repeat identically across both runs: correct model reasoning, never
  captured as a structured decision - a real, repeated instrument gap.
  model-decides-verification.md updated in place with the full run2
  detail rather than a second file.
- CORE-B (2026-08-27): **cross-lane request #3 closed** on `lane/core-b` --
  full record in the CORE-A/whoever-owns-observations queue item above
  (now `[x]`). Integrator granted a one-file scoped exception for
  `_SEMANTIC_LABEL_SYSTEM_PROMPT` in the otherwise-unowned
  `observations.py`; wording mirrors MEASURE's already-landed
  `live_extractor.py` fix verbatim (3-6 words, subject+past-tense-verb, no
  quoted paths/commands/hashes, no parentheticals) rather than reinventing
  it, per the note left on this board after the fourth-wave rebase.
  Regression test added, 16/16 test_observations.py green. Full offline
  suite 1268 passed / 2 skipped / 111 failed -- the 111 is the
  pre-existing drifted-DB e2e gap on record at H2's entry above [identical
  count, reproduced even with DATABASE_URL cleared before invocation, so
  it's env-level (.env loading mid-run) not this change]. No other file
  touched outside the granted exception.

- RESEARCH (2026-08-27, observation-labeling brief): done - see Lane
  RESEARCH item 7 and
  .scratch/research/observation-labeling-technique-brief.md. One-line
  summary for MEASURE: swap error_floor.py's literal-Jaccard semantic_label
  match for an LLM-judge second pass on Jaccard-failing pairs only - this is
  STALE's own validated move (95.8% human agreement), aimed directly at the
  vocabulary-divergence failure MEASURE already diagnosed on ef-sem-003.
  Second, buildable-now idea: AFTER's diagnose-revise-promote loop over
  error_floor's own typed FP/FN reason codes, to systematize prompt tuning
  instead of repeating ad hoc edits each wave. TEPA's key/value split noted
  as speculative (paper admits it's unsolved for open-ended labels);
  Library-Drift/Ratchet correctly scoped as skill-retirement governance, a
  different problem from observation labeling - only Ratchet's
  false-positive-asymmetry finding carried over as a judge-validation
  caution. Full text read via WebFetch on arXiv HTML, methodology sections
  specifically, not abstracts. Pure literature work, no live calls.

- CORE-A (2026-08-27, twelfth wave): **docker-compose.yml** (founder-
  requested, repo root) closing the remainder of demo.md C1's install
  claim — `db` (pgvector/pgvector:pg15, healthchecked) + `backend` (new
  `backend/Dockerfile`, migrates then serves the MCP server). Real,
  non-obvious finding along the way: `app/mcp_server/server.py` imports
  `Agent`/`RepoSandbox` from `experiments/swebench_pro/` as a real SIBLING
  of `backend/` at MODULE IMPORT TIME (`README_MCP_SERVER.md` setup step
  3's own documented requirement) — a backend-only build context would
  have crash-looped the container on boot. Fixed by building with
  `context: .` (repo root) instead of `context: ./backend`, copying both
  trees preserving the sibling relationship; `.dockerignore` written
  accordingly (repo-root-scoped, excludes `backend/.venv`,
  `backend/conflict_candidates.json` [~60MB stray result file],
  `backend/.env` [never bake secrets into a layer], `.scratch`/`.claude`/
  `frontend`/`plat_v1`/`docs`). Loopback posture preserved end-to-end
  per SECURITY.md: the container process binds `0.0.0.0` internally
  (unavoidable — a container's own loopback interface isn't reachable
  through Docker's port mapping at all) but the compose `ports:` publish
  spec is `127.0.0.1:8765:8765` on the HOST side, which is the actual
  enforcement boundary and keeps the server unreachable from any other
  machine, same threat model as bare-metal. `STEALTHLAB_MCP_TOKEN` reaches
  the container via `env_file: backend/.env` with `required: false` (so
  `docker compose up -d` still brings up `db` on a fresh clone before
  `.env` exists) — the container itself still hard-fails with its own
  existing clear error if the token is missing, nothing silently
  papered over. `DATABASE_URL` is overridden explicitly in compose
  (`db:5432`, the service name) since whatever's in `backend/.env` is
  host-side (`127.0.0.1`). Added `backend/scripts/docker_healthcheck.py`
  (POSTs to `/mcp` unauthenticated, treats 401 as healthy — same signal
  `packaging/README.md`'s own smoke test uses; a bare 2xx with no token
  is treated as UNHEALTHY on purpose, so a passing healthcheck can never
  paper over an auth-gate regression). Filled a real, small, adjacent gap
  found while tracing the boot path: `backend/.env.example` never had
  `STEALTHLAB_MCP_TOKEN` even though the server hard-requires it — added
  with the same generation instruction `README_MCP_SERVER.md` already
  gives.
  **VALIDATION, honestly scoped**: Docker is NOT installed in this
  environment (`docker`/`docker compose` both absent — checked both Bash
  and PowerShell) — same gap that originally blocked CORE-A's queue item
  2 (real-DB migration chain verification), and it is NOT resolved by
  authoring this file; it still needs an environment that actually has
  Docker to execute. **I did not, and could not, boot-test this stack.**
  What I did verify statically: `docker-compose.yml` parses as valid YAML
  and validates clean against the official compose-spec JSON schema
  (fetched live from compose-spec/compose-spec, checked with `jsonschema`
  in a real Python 3.14 interpreter found on this machine outside the
  project venv); every eager (module-top-level) import on the
  `app.mcp_server.server` import path was traced by hand (`app.config.
  Settings` — all fields `Optional`, no required env var beyond what's
  already handled; `app.debate.panel.default_panel`/`default_judge` are
  functions, not called at import time, so no panel API key is needed to
  boot either) to confirm nothing beyond `DATABASE_URL` +
  `STEALTHLAB_MCP_TOKEN` is required just to come up, matching the
  no-live-model-calls scope of this task. **Still unverified**: whether
  the image actually builds (a `pip install` failure on some pinned
  package, a wheel unavailable for the build platform, etc. would only
  surface at real build time). Whoever next has Docker available should
  run `docker compose up -d` then `docker compose exec backend python
  scripts/migrate.py --status` (expect all 30 applied — NOTE: demo.md's
  own C1 evidence line still says "all 23 applied", stale since the
  WAVE-2/3 hardening waves added db/24–30; not fixed here, flagging only)
  and the `curl -X POST .../mcp` → 401 smoke test from the compose
  file's own header comment as the real proof this task couldn't
  produce. No schema/app code touched beyond the two new Docker-only
  files and the `.env.example` addition.

- MEASURE (2026-08-27, second wave): **no live calls this wave** (founder:
  account has no spendable balance right now) - see the new "OpenRouter
  budget wall" section above for the full account-state writeup. Three
  things done, zero completions/generations against any model:
  1. `semantic_label_prompt_variants.py` - 4 candidate semantic_label
     prompt variants (few_shot, vocab_discipline, strict_noun_phrase,
     combined) prepared as ready-to-run code alongside the shipped
     terse_v2 baseline, each targeting one of the two residual failure
     modes the v2 live pass left visible (vocabulary mismatch on
     ef-sem-003; one NONE-contract under-fire on ef-sem-002).
     `live_extractor.py` gained a `--prompt-variant` flag (default
     `terse_v2`, byte-identical to today's shipped behavior) and a
     `system_prompt` param threaded through `build_messages`/
     `LiveExtractor` - deferred import avoids a circular dependency
     between the two modules. 23 new offline tests total [7 in
     test_live_extractor.py's new TestPromptVariants class + 16 in the
     new tests/test_semantic_label_prompt_variants.py]: registry
     integrity, a mechanical-layer-unchanged-across-variants tripwire,
     few_shot's exemplar strings verified verbatim against the actual
     golds, vocab_discipline's CI-ban targeting ef-sem-003 specifically,
     strict_noun_phrase's tighter ceiling, CLI wiring, and the
     file-family out-of-scope limitation disclosed in the module
     docstring so no variant quietly overclaims fixing something a
     prompt can't fix. Harness suite 236/236 green [213 prior + 23 new].
  2. OpenRouter catalog + account-status check (both zero-cost reads,
     no key needed for the catalog, no completion call for the key
     endpoint): confirmed `ox-alpha` is entirely absent from the current
     417-model catalog despite billing successfully as recently as
     yesterday; confirmed the account is `is_free_tier: true` (no
     lifetime credits purchased); confirmed via OpenRouter's own docs
     the free-tier rate limit (20/min, 50/day under $10 lifetime credits,
     1000/day at/above it) that governs any `:free`-model path forward.
     Catalogued 20 `:free`-tagged models, flagged which are genuinely
     usable for JSON-structured extraction vs. two that show `0`/`0`
     token pricing but are actually billed per-clip/per-song on a
     different dimension (`google/lyria-3-*-preview`, music generation -
     a real trap for anyone filtering on price fields alone) vs. one
     that's a moderation classifier, wrong tool for this task.
  3. Board budget-wall section written (see above) - both dead
     free-tiers (ox-alpha's own, and the account's) now documented in
     one place with the exact numbers, so the next session doesn't
     rediscover this by watching a sweep fail.
  No spend this wave. No live calls of any kind - only catalog/account-
  status GETs (see above) and offline test runs. Awaiting explicit
  confirmation before running anything against a `:free` model (per
  instruction) or before any paid resumption.

- **MEASURE CORRECTION (2026-08-27)**, after reading RESEARCH's independent
  verification of the model-decides sweeps
  (`.scratch/research/model-decides-verification.md`): this lane's own
  fifth-wave board entry (commit `5929904`) mischaracterized run1's 72
  non-billed attempts as **"72 absorbed 429s/network retries"** - they were
  actually **100% HTTP 404 on `ox-alpha`** (model not found, non-retryable),
  **zero 429s**, and the same is true of run2 (never disclosed there at all -
  the run2 board entry gave no per-model breakdown). Corrected plainly:
  1. **Neither model-decides sweep is evidence about `ox-alpha`** - both ran
     100% on the fallback, `openai/gpt-4o-mini`. The two sweeps are two
     samples of gpt-4o-mini's behavior at whatever temperature/sampling
     variance it has, not "two independent confirmations of the target
     model's behavior" - a narrower claim than this lane's original framing
     implied. The sensitivity result itself (p=0.0117 run1, p=0.0215 run2,
     both 0/12 false-refusal) is still real and correctly computed; it says
     "gpt-4o-mini detects staleness better through the verified surface than
     through prose RAG," not "ox-alpha does."
  2. **Power-framing correction**: this lane's run1 write-up said run1
     "already exceeds n-for-80%-power@this-ratio=9" (n=11 > 9) - true as a
     bare number comparison, but exact-test power is NOT monotonic in n
     (`power(q=0.909)`: n=9 -> 0.806, n=10 -> 0.771, n=11 -> 0.736, n=12 ->
     0.911). At n=11 this sweep does NOT actually have 80% power against its
     own observed effect size (0.736 < 0.8), even though it already clears
     significance (p=0.0117 < 0.05) - those are two different claims and only
     the significance one is unambiguously true here.
  3. **`dec-pdf-103` (the one discordant loss, both runs) and `dec-dep-105`
     (the one control abstain, both runs) both show CORRECT model reasoning
     in free-text `decision_notes`** ("the assumptions... do not hold due to
     the version mismatch" / "...do not align with the current environment"
     for pdf-103; "the assumptions... hold true, as pip 24.2 is installed..."
     for dep-105) that never became a structured `refuse[]`/`reuse[]` entry -
     a repeatable SCHEMA-CAPTURE gap, not a detection failure. This means
     C's true detection rate is understated by the strict metric, not
     inflated - the opposite bias from RUN #1's Caveat 2 (where the
     mechanical gate made C's refusal rate look inflated relative to genuine
     model reasoning). Not a defect in the metric (it's doing exactly what
     design §5 specified); flagged as a possible future refinement (scoring
     whether `decision_notes` names the violated assumption as a secondary
     signal), not acted on this session.
  4. Every other headline number (11/10 discordant pairs, exact p-values,
     0/12 false-refusal both runs, genuinely-model-decided journal check)
     was recomputed independently and **CONFIRMED EXACTLY** - this is a
     characterization/comparability correction, not a retraction of the
     finding itself.
  **Standing recommendation adopted**: any future sweep on this chain
  confirms which model actually served each arm from the spend log's
  per-attempt `model`/`status` fields before reporting a result against
  `ox-alpha` by name - "appears in the models: summary line" is NOT the same
  claim as "served a billed call," the exact conflation that caused this
  correction.
- CORE-B (2026-08-27, second wave): **offline coverage-gap closure** across
  every owned module (no live calls, no schema changes; full record in the
  commit message). applicability.py/invariants.py/precondition_gate.py/
  state.py went to 100% offline-covered (were 72/83/95/81%);
  procedure_extraction/registry.py 26%->82% and evidence.py 62%->100%. Six
  new/extended offline test files, all FakePool or pure-function, no DB:
  test_applicability_hard_constraints_offline.py (new, 24 tests -- every
  disqualification branch in check_hard_constraints plus the pure
  _scope_matches/_excluded helpers and find_applicable_procedures' two
  early-return paths, all previously proven ONLY against the shared,
  repeatedly-drifted Postgres instance test_applicability_e2e.py needs),
  test_invariants.py (+15 -- every whitelisted operator, non-numeric-
  constant/chained-comparison/disallowed-operator refusals, the
  z3-unavailable degradation path via both a real forced ImportError and a
  monkeypatched check, and the genuine solver-unknown outcome mocked at
  Solver.check to stay deterministic), test_precondition_gate.py (+1),
  test_state_offline.py (new -- state_delta had zero offline coverage
  before this), test_registry_offline.py (new -- select_extractor's
  version-sort/kind-tiebreak/scope-filter logic, extractor_stats' div-by-
  zero guards), test_evidence_offline.py (new -- both EvidenceSource
  implementations). DEFERRED, disclosed rather than silently skipped:
  procedure_extraction/__init__.py (21%), strategies.py (50%, its pure
  _parse_abstraction_response half already 100%), derive.py's remaining
  pool-touching branches (88%, untouched) -- each needs a multi-query
  FakePool harness (strategies.py additionally the scripted-client LLM
  pattern) sized more like its own queue item than a quick close-now pass.
  Full suite: **1327 passed / 2 skipped / 111 failed** -- the 111 is the
  same pre-existing drifted-DB gap on record at H2's entry (identical
  count), +59 over the prior 1268-passed baseline matching every test
  added, zero regressions.
  **Standing by on MEASURE's semantic_label prompt-variant prep** per this
  session's kickoff instruction: read `semantic_label_prompt_variants.py`
  (four candidates -- few_shot/vocab_discipline/strict_noun_phrase/
  combined, targeting the two residual failure modes terse_v2 left open)
  and the OpenRouter budget-wall entry above in full. None of the four are
  live-tested yet (MEASURE is blocked on both ox-alpha's disappearance from
  the catalog and the account's zero spendable balance, explicitly awaiting
  confirmation before any run). Correctly nothing to mirror into
  observations.py's `_SEMANTIC_LABEL_SYSTEM_PROMPT` yet -- last wave's rule
  (mirror the ALREADY-PROVED-OUT wording, never a hypothesis) applies the
  same way to these four candidates. Will pick whichever variant MEASURE's
  live run actually validates, once one exists, the same way terse_v2 was
  mirrored verbatim rather than reinvented.

- MEASURE (2026-08-27, third wave): **4 prompt-variant candidates run live
  against a `:free` model** (founder: test the 4 prepared variants against
  a real free-tier model, pick the best-suited one from the catalog audit,
  stay mindful of the 50/day cap, prefer a semantic-focused subset first).
  Used `liquid/lfm-2.5-2.6b:free` (the catalog audit's own top pick - "own
  description says suited for... data extraction"). Scoped to the 8
  `ef-sem-*` excerpts (`fixtures/error_floor/semantic.json`) - confirmed by
  grep that this is the ONLY fixture family in the 42-excerpt corpus with
  any `semantic_label` gold at all, so it is the correct minimal subset for
  a semantic-label prompt comparison, not an arbitrary cut. Two new CLI
  flags to support this without weakening the fixture contract:
  `live_extractor.py --excerpt-ids` and `run_error_floor.py --excerpt-ids`
  (comma-separated) - both still load+validate the FULL 42-excerpt corpus
  first (fixture contract unchanged), only the network calls / grading
  scope narrow to the named subset. 4 new offline tests (2 per script:
  subset restriction + unknown-id hard error), plus 1 more from the pricing
  fix below (5 new total this wave). Ran all five prompts
  (terse_v2 as a same-model baseline anchor, plus the four candidates) on
  the identical 8-excerpt subset - **RESULTS TABLE, FREE-TIER MODEL, NOT
  ox-alpha, DIRECTIONAL SIGNAL ONLY** (full caveat + per-variant discussion
  now lives as a comment block in `semantic_label_prompt_variants.py`,
  right next to the variants it grades, so this is discoverable by anyone
  reading the code, not just this log entry):

  | variant | sem P | sem R | sem F1 | overall P/R/F1 |
  |---|---|---|---|---|
  | terse_v2 (baseline) | 1/8=0.125 | 1/4=0.25 | 0.167 | 0.375/0.667/0.48 |
  | few_shot | 2/6=0.333 | 2/4=0.5 | 0.4 | 0.462/0.667/0.545 |
  | vocab_discipline | 2/6=0.333 | 2/4=0.5 | 0.4 | 0.333/0.444/0.381 |
  | strict_noun_phrase | 1/7=0.143 | 1/4=0.25 | 0.182 | 0.4/0.667/0.5 |
  | combined | 2/5=0.4 | 2/4=0.5 | 0.444 | 0.636/0.778/0.7 |

  `combined` wins on both semantic_label F1 and overall F1 on this model -
  the module's own hypothesis ("the single most promising blend if the
  first two each move the needle independently") held up here. Real
  caution surfaced too: `vocab_discipline` alone COLLAPSED command_executed
  to 0/3 TP on this model - a mechanical-layer regression no variant showed
  on ox-alpha's earlier live pass, plausibly this cheaper model bleeding
  the added vocabulary instructions into unrelated observation types.
  New model-specific failure modes also appeared that ox-alpha's pass never
  showed (ef-sem-006/007 - a file read and a glob - spuriously earned
  observations under every variant), reinforcing that this is a DIFFERENT
  model's behavior, not a preview of what any variant does on the
  production model.
  **BUG FOUND + FIXED en route**: `openrouter_arms.SpendLog.record` priced
  every model by `PRICE_PER_MTOK.get(model.split("/",1)[0], ...["default"])`
  - `liquid` isn't a keyed provider, so it silently fell through to the
  DEFAULT PAID rate ($2.50/$10.00 per Mtok), logging a fictitious $0.0743
  for the first (terse_v2) run even though the model is genuinely free.
  Fixed: any `model` ending `:free` now prices at $0 regardless of provider
  prefix, provider-agnostic per OpenRouter's own suffix convention (their
  provider-keyed pricing table only ever covered paid entries anyway). 1
  new regression test (`TestSpendLedger.
  test_free_suffix_prices_zero_regardless_of_provider`). The one already-
  written terse_v2 spend ledger was corrected in place (its `observations`
  predictions were unaffected by the bug, only cost accounting was) rather
  than re-spending 8 more requests to regenerate it.
  **BUDGET**: 50/50 free-tier requests used today, zero 429s, zero non-
  billed attempts - ended EXACTLY at the daily cap (8 terse_v2 + 9 few_shot
  + 12 vocab_discipline + 12 strict_noun_phrase + 9 combined [4+5, split
  across two `--auto-resume` passes to stay inside budget before
  completing the comparable 8-excerpt set]). RESEARCH's LLM-judge second-
  pass idea (observation-labeling-technique-brief.md) was NOT attempted
  this wave - deferred purely because the daily allotment was gone by the
  time the four variants + baseline finished, not a decision against the
  idea; next session can pick it up once the free-tier day resets. All
  result JSONL/detail files stay LOCAL/untracked (`live_extractor_preds_
  free_<variant>.jsonl` etc.), same convention as every other run-output
  file this lane produces - the numbers above plus the full narrative live
  in this log entry and in `semantic_label_prompt_variants.py`'s docstring,
  not in a committed data file. $0.00 real spend (free-tier model, correctly
  priced after the fix above); session-to-date PAID total unchanged at
  $1.0780. Harness suite 241/241 green [236 prior + 5 new]. Zero backend
  edits.

- RESEARCH (2026-08-27, outside-eye pass on demo.md/README): done - see
  Lane RESEARCH item 8 and
  .scratch/research/outside-eye-demo-readme-pass.md. One-line summary for
  the founder: repo-root README.md described a completely different,
  pre-pivot product with no link to demo.md's current earned-memory
  positioning (last touched pre-pivot, commit 2ce3c7e) - rewritten in place
  with an honest two-layer structure (what's actually runnable today = the
  real 8-tool workflow-debate MCP server, verified against server.py's own
  @server.tool() decorators, vs. what demo.md's v0.1 slice is building
  toward). Second finding: demo.md's own C5 differentiator claim
  (check_procedure / WOULD_REFUSE) has zero code matches anywhere in
  backend/ - real and independently verified in the harness
  (model-decides-verification.md) but not yet wired into the production MCP
  server; added one non-restructuring note under demo.md's table naming
  this plus the C1 docker-compose gap, left the table itself untouched
  since which fix is right (build the wrapper vs. relabel the claim) is a
  product call, not mine. Also: no LICENSE file despite demo.md's
  Apache-2.0 claim (flagged, not fabricated); commLLM.md was saved as
  UTF-16LE and unreadable - fixed the encoding, left a separate older layer
  of punctuation mojibake (~12 sequences) unfixed rather than guess. Changes
  made directly: commLLM.md (encoding), demo.md (one added note),
  README.md (full rewrite) - none committed to lane/research since no lane
  owns README.md today; routing/commit is the founder's call, same posture
  as CORE-A's SECURITY.md/DATA_STATEMENT.md landing.

- CORE-B (2026-08-27, third wave): **closed the three deferred coverage
  gaps from the second wave** -- procedure_extraction/__init__.py (21%),
  strategies.py (50%), derive.py's remaining pool-touching branches (88%)
  all now at **100%**. Turned out smaller than the second wave's own
  sizing note feared: each of derive.py's two pool-touching functions
  (derive_preconditions, derive_scope) issues exactly ONE project_state()
  call, so the same single-purpose FakePool test_state_offline.py already
  established covers both -- no "several real queries at once" harness was
  actually needed there. And since strategies.py's/init.py's own test
  evidence carries no project_id, derive_preconditions/derive_scope
  short-circuit before ever reaching the pool (proven directly in the new
  derive suite), so THEIR tests needed no FakePool at all, only a
  pool-that-raises-if-touched sentinel -- reserving capture_procedure() as
  the one thing actually monkeypatched (same precedent registry.py's own
  entry set: fake the persistence BOUNDARY, never the INSERT/UPDATE SQL
  itself). Three new files, 28 tests: test_derive_offline.py (7 --
  derive_preconditions/derive_scope's real project_state() round trip both
  ways, plus two small pure sub-branches no existing test had hit: a
  test_run observation whose own command string also feeds the package-
  manager/build/dev-server regexes, and a command_executed observation
  with a genuine nonzero exit code); test_strategies_offline.py (8 --
  DeterministicExtractor's literal output, GroundedHybridExtractor's full
  fallback ladder [no client / empty skeleton / client exception /
  malformed response / step-count mismatch] and its real abstraction path
  incl. model/temperature threading); test_procedure_extraction_init_offline.py
  (13 -- extract_procedure's V5 pre-check both ways, a validation-failure
  report that never reaches capture_procedure, dry_run's persistence skip,
  the real persist-plus-migration-20-UPDATE path, _select_strategy's full
  registry-driven branch matrix incl. the no-client-even-for-an-llm-row
  case, evaluate_extractor's unknown-id/skip-episodes/per-rule-failure-
  counting/llm-strategy paths). REAL BUG SURFACED, documented not fixed
  (out of scope for a coverage-only pass): evidence with real observations
  but an empty tool_sequence is possible per evidence.py's shape (the two
  lists are populated independently) and would raise an uncaught pydantic
  ValidationError all the way up through extract_procedure -- schema.py's
  ExtractedProcedure refuses zero steps unconditionally, so BOTH extractors
  crash identically on it rather than degrading; extract_procedure's own
  V5 pre-check only inspects has_observations(), never tool_sequence, so
  nothing upstream catches this today. Pinned as a documented
  pytest.raises in test_strategies_offline.py rather than silently
  avoided. procedure_extraction package-wide offline coverage (excl. e2e):
  483 stmts / 10 missed / 98% -- the 10 remaining are registry.py's
  disclosed pure-DB-write plumbing (7, deferred at the second wave for the
  same reason capture_procedure is monkeypatched here, not faked) plus two
  pre-existing minor gaps in schema.py/validators.py never in scope. Local
  package suite (offline + e2e, no regressions): 86 passed / 19 skipped /
  0 failed. Full backend suite confirms it: **1353 passed / 115 skipped /
  0 failed** (`pytest -m "not e2e" tests/`, 13m19s) -- zero production code
  changed this wave (only new test files plus one stale docstring pointer
  fixed in test_procedure_extraction.py), so this is exactly the expected
  clean result, not a surprise.

- CORE-A (2026-08-27, thirteenth wave): **Band 6 hygiene sweep** (founder-
  requested, docs only) — four known-stale references closed:
  (1) `demo.md` C1's evidence line — flagged as stale in the twelfth-wave
  entry above ("all 23 applied") — now says 30, matching the real
  `db/` migration count (01–30, WAVE-2/3 hardening added 24–30).
  (2) `0xAlphaplan.md`'s file-ownership line pointed at
  `vendor/tau2-bench/...`, a path that doesn't exist in this tree;
  `git ls-files -s` shows the τ²-bench code is actually a gitlink
  (mode 160000) at `experiments/tau3_bench/_tau2_bench_src` — updated
  the anchor to match (submodule isn't initialized in this worktree so
  the internal `src/tau2/domains/...` subpath past the anchor is
  unverified, carried forward as-is). (3) `backend/README.md` opened
  with "# Workflow Debate Platform" and framed the debate/decompose
  system as the whole product — stale since the pivot to StealthLab
  (confirmed against `commLLM.md` + root `README.md`/`demo.md`): the
  debate loop is real and still lives at `app/debate/` (still wired
  into the MCP tool surface as `detect_conflict_trigger`/
  `propose_synthesis`/`submit_approval` per `commLLM.md`'s tool table),
  it just isn't the top-level product anymore. Re-titled to "Backend —
  debate & decomposition subsystem" with a pointer to root
  `README.md`/`demo.md` for current framing; left the rest of the doc
  (setup steps, API table, protocol writeup) untouched — auditing every
  technical claim in it is a separate, non-zero-cost task, not this one.
  (4) Root `README.md` had three leftover `backend_v2/backend_v2` /
  `frontend_v2/frontend_v2` nested-path references (two `cd` lines, one
  deploy note) from before the repo flattened to plain `backend/` /
  `frontend/` (confirmed: `backend_v2`/`frontend_v2` don't exist at
  root) — all three now point at the real paths.
  **Scope notes (both superseded by this second rebase)**: this entry
  originally also flagged `backend/README.md` line 49's "frontend_v2
  folder" wording as the same stale pattern, left out of scope — the
  first rebase (onto the SHIP/CORE-B/MEASURE batch) showed another lane
  had already fixed that line concurrently. This second rebase (onto
  RESEARCH's outside-eye pass, entry directly above) goes further: item
  (4) itself is now moot — RESEARCH fully rewrote root `README.md` in
  place, and the rewrite has no `backend_v2`/`frontend_v2` references
  left anywhere to fix. Net effect after both rebases: items (1)-(3)
  stand as landed by this wave; item (4)'s specific line-level fix was
  overtaken by a superseding rewrite before it could matter, which is a
  fine outcome, not a conflict. No schema/app code touched.

- RESEARCH (2026-08-27, Band 2 founding-loop exit-criterion verdict): done -
  see Lane RESEARCH item 9 and
  .scratch/research/band2-founding-loop-exit-criterion-review.md. Answering
  Chaitanya's infra-report knock-on claim (bootstrap_demo.py's fresh-DB run
  leaves episodes/observations/procedures/evidence at 0 rows, therefore
  "Band 2's founding-loop exit criterion is still unexercised on a fresh
  DB"): genuine gap, but narrowly scoped - not a false claim
  BAND2_CLOSURE_REVIEW.md made, since it never actually engaged this
  specific ROADMAP bullet at all (grepped for founding/real
  data/hand-audited/end-to-end/inhabitant - zero matches, not even in its
  own Deferreds section). The two live e2e tests under the review's item 8
  that could be mistaken for covering it
  (test_founding_loop_replays_bit_identically_from_raw_traces and its
  procedure-candidate sibling) run on the shared dev instance, not a fresh
  DB, and skip the episode-assembly hop entirely (fabricated
  uuid.uuid4() episode_id, no assemble_episodes() call) - they prove replay
  determinism (ROADMAP bullet 4, correctly cited there), not "one
  hand-audited live run from zero" (ROADMAP bullet 1, the actual founding-
  loop criterion). So the review's summary verdict ("Every ROADMAP Band 2
  item is implemented" / "BAND 2 CLOSED") overclaims relative to what its
  own scorecard checked - one of ROADMAP's four named Band-2 exit-criteria
  bullets was silently never covered, positive or negative. This predates
  bootstrap_demo.py's gap; that script didn't break an already-closed
  criterion, it's the vehicle that would have closed it and turns out not
  to attempt it. Recommendation boarded, not executed (outside this lane's
  paths): amend BAND2_CLOSURE_REVIEW.md to carry the bullet as open, and
  note that PRODUCTION_READINESS.md's current #1 priority (rewriting
  bootstrap_demo.py's real two-phase story) already closes it as a side
  effect once landed. Item 2 of the founder's two-thread request (outside-
  eye pass on CORE-B's new bootstrap_demo.py once it lands) queued, not
  started - nothing to review yet.

- MEASURE (2026-08-27, fourth wave): **free-tier cap confirmed still NOT
  reset - prep-only work this wave, zero live extraction calls** (founder
  instruction: check the cap directly rather than assume a reset just
  because a day's passed; if not reset, stand by / prep only).
  **CAP CHECK (live, zero-cost as far as billing goes - one real request,
  correctly rejected before generating any tokens)**: `GET
  https://openrouter.ai/api/v1/key` showed `usage_daily: 0` but carries no
  free-tier request-count field, so it can't answer the question by itself;
  a real probe call to `liquid/lfm-2.5-2.6b:free` (1-token "reply OK"
  prompt) came back **HTTP 429** with `X-RateLimit-Limit: 50`,
  `X-RateLimit-Remaining: 0`, `X-RateLimit-Reset: 1787875200000` ->
  **2026-08-28T00:00:00Z**, i.e. still ~11h45m away from this check
  (12:14 UTC same day). Error body confirms the specific limiter:
  `"Rate limit exceeded: free-models-per-day"`. **Cap has NOT reset** -
  boarding this either way per instruction, not just on a reset.
  Per instruction, did NOT proceed to option (a) or (b) live - did
  prep-only work instead, picking (b)'s design (RESEARCH's LLM-judge
  second-pass idea, `.scratch/research/observation-labeling-technique-
  brief.md`) since it has a concrete, ungated adoption shape ready to code
  (option (a), a second free-model cross-validation of `combined`, needs
  no new code at all - it's a `--models` swap on the existing live_
  extractor.py CLI, already "ready-to-run when authorized" per the prior
  wave's own board note - so there was nothing to *prepare* for it beyond
  what already exists; picking a second-wave-worthy free model for it is a
  quick catalog lookup, not a coding task, and is better done live so the
  choice can be sanity-checked against that day's actual catalog rather
  than staged now and going stale).
  **Shipped (offline only, zero network calls beyond the one cap-check
  probe above)**: `semantic_judge.py` (new) - implements the brief's exact
  §1 adoption shape: `error_floor.py`'s Jaccard rule stays the free
  deterministic first pass, unchanged; a Jaccard-*failing* semantic_label
  pair of the same observation_type can now be routed to ONE adjudicating
  LLM-judge call (`{"match": true|false}`, closed yes/no, not open
  scoring) via a `judge` param threaded through `error_floor.semantic_
  match`/`observations_match`/`grade_excerpt` (`judge=None` default is
  BYTE-IDENTICAL to every prior grading run - regression-tested). Ratchet's
  false-positive/false-negative asymmetry caution (cited in the brief) is
  encoded directly: an unparseable judge reply defaults to NO MATCH, never
  match-by-default, since a false positive silently corrupts the metric
  while a false negative just costs recoverable recall.
  `run_error_floor.py` gained `--judge-model` (optional; off by default,
  deferred-imports `openrouter_arms`/`semantic_judge` only when set, so the
  CLI's default path stays exactly as pure as before) + `--judge-spend-log`,
  wired through a real `SemanticJudge` built on the SAME `OpenRouterClient`
  backoff/chain/SpendLog machinery every other live component here uses -
  no new HTTP code. 13 new offline tests (`tests/test_semantic_judge.py`):
  verdict parsing (JSON/code-fence/bare-yes-no/unparseable), message
  construction, the unparseable-defaults-to-NO-MATCH policy, the `JUDGE`
  arm tag, and four `error_floor.py` integration cases proving the
  composition end-to-end with a fake client - a Jaccard-failing synonym
  pair (`ef-sem-003`'s real gold/pred strings) becomes a TP under a
  matching judge, stays FP+FN with `judge=None`, the judge is NEVER called
  when Jaccard already matches (asserted via a fake client with zero
  queued replies, that would IndexError if called), and a genuine
  mismatch stays correctly refused even with a judge wired in. Harness
  suite **254/254 green** [241 prior + 13 new].
  **NOT done this wave, staying correctly undone**: no live judge calls
  (cap exhausted, confirmed above); brief's step 3 validation (judge-vs-
  human agreement on a hand sample) therefore also not done - this module
  is unvalidated code, not yet a trusted grading path, and its own
  docstring says so; the brief's step 4 (3-call majority vote before this
  backs any headline number) intentionally NOT implemented, single-call
  only. Nothing mirrored into `observations.py`'s live
  `_SEMANTIC_LABEL_SYSTEM_PROMPT` (unrelated to this module, and that bar
  was never about the judge anyway) and nothing mirrored into
  `error_floor.py`'s DEFAULT grading behavior (`judge=None` unless a
  caller opts in with `--judge-model`). $0.00 spend this wave (the one
  cap-check probe was rejected by OpenRouter before any tokens were
  generated - a 429 does not bill). Next session with quota: run
  `run_error_floor.py --judge-model <a :free id> --predictions live_
  extractor_preds_free_combined.jsonl --excerpt-ids <the semantic subset>`
  (or wire it into a fresh `live_extractor.py` pass) and report judge-vs-
  Jaccard delta P/R/F1, plus do the brief's step-3 validation before
  trusting the number.

- CORE-A (2026-08-27, parallel assist for CORE-B's bootstrap_demo.py phase
  B): **confirmed — nothing about the current schema blocks a genuine
  claim-supersession write; no new migration needed.** Checked directly
  against `app/services/claims.py`'s `relate_claims()` (the function CORE-B
  plans to call): it inserts one `edges` row with `edge_type='SUPERSEDES'`
  (already a real value in the `edge_type` enum, `backend/db/01_ontology.sql`
  lines 12-15 — no ALTER TYPE needed) and does one
  `UPDATE knowledge_nodes SET properties = properties ||
  '{"truth_state":"OUT"}'::jsonb` on the target claim. `knowledge_nodes` has
  no freeze trigger (append-only enforcement per the board's own [H] list is
  evidence/executions/change_sets/change_set_operations/failure_routes only
  — knowledge_nodes was never one of them), so that UPDATE is a normal
  write, not a fight with a trigger. `truth_state` lives in the existing
  `properties` JSONB column, not a typed column, so there's no DDL surface
  to migrate in the first place. This isn't a fresh read either:
  `test_claims.py` lines 283-317 already offline-test this exact call shape
  (SUPERSEDES flipping target truth_state to OUT, CONTRADICTS riding the
  SUPERSEDES enum bucket via `custom_edge_type` same as FAILURE_MODE does
  elsewhere) against a fake pool, and `state.py`'s `project_state()`
  (`backend/db/16_state_projection_index.sql`) already filters live belief
  on `properties->>'truth_state' = 'IN'` — so a flipped claim already stops
  counting as currently-believed for any downstream consumer today, no
  schema change needed on that side either.
  **One adjacent gap, not a blocker for this specific ask:** `bootstrap_demo.py`
  as it exists right now (read in full) is the older single-phase debate/
  decompose seeder (seeds `example_workflow.json`, inserts 10 traces to trip
  `_DEMO_RULES`'s error-rate threshold) — no phase A/B structure, no
  `relate_claims`/`check_procedure` references anywhere in it yet, so CORE-B
  is building this fresh rather than extending an existing phase B. Separately,
  RESEARCH's outside-eye pass (entry above) already flagged that
  `check_procedure`/`WOULD_REFUSE` have zero matches in `backend/` today —
  real in `experiments/harness/`, not yet wired into
  `app/mcp_server/server.py`. That's a real gap CORE-B's phase B will run
  into if it expects a callable `check_procedure` MCP tool, but it's an
  MCP-server wiring question, not a schema one — doesn't change the answer
  to what was actually asked here. No schema/app code touched for this
  confirmation.

- CORE-B (2026-08-27, next wave): **bootstrap_demo.py real two-phase
  story** — full detail in CORE-B queue item 11 above. Summary: replaced
  the old debate-seeder script outright (unrelated pipeline, zero
  procedures, demo.md silent on it — kickoff's own default, nobody
  flagged a dependency); new script's Phase A extracts a real procedure
  via `extract_procedure(client=None)` from a real probe of this repo's
  own `backend/` checkout; Phase B genuinely supersedes the claim behind
  one real precondition via `claims.relate_claims()` and proves
  `check_procedure_reuse()` flips ALLOW -> WOULD_REFUSE citing both real
  claim ids. Found and fixed a real, previously-unexercised bug in owned
  code: `extract_procedure()` never passed `provenance`/`scope_type` to
  `capture_procedure()`, so every real (non-monkeypatched) call raised
  `V0Violation` — invisible to the offline suite, never caught because no
  worktree had a working `DATABASE_URL` until this wave. Fixed
  (`provenance="system_pending_review"`, `scope_type` from
  `evidence.project_id`) and validated LIVE against the real Supabase
  `DATABASE_URL` via the pre-existing, self-cleaning
  `test_procedure_extraction_init_e2e.py` (3/3 passed — previously never
  actually run). **Question #7 (non-blocking, default applied):** kickoff
  text assumed `retrieve_precedent` would "surface" the fresh procedure
  before Phase B breaks it; traced its real implementation
  (`reuse_detection._vector_candidates`) and found it structurally cannot
  return a `procedures` row at all (task_nodes/knowledge_nodes only —
  separate, pre-existing drift from demo.md C4's description, not fixed
  here), and separately that `find_applicable_procedures` is cold-start-
  gated off for any freshly-extracted procedure regardless. Default
  applied: script proves the "reuse you can see" half via
  `check_procedure_reuse`'s explicit-invocation ALLOW instead, and prints
  `find_applicable_procedures`'s honest empty result rather than
  overclaiming. Full detail + options in the queue item.
  PROOF STATUS (same standard as the migration-chain item): offline
  suite green throughout (**1368 passed / 115 skipped / 0 failed**,
  unchanged — the V0-gate fix only adds kwargs an already-monkeypatched
  test doesn't assert on) plus 10 new offline tests for the script's own
  pure logic (`tests/test_bootstrap_demo_offline.py`). The script itself
  has NOT been run live: no Docker in this worktree (confirmed), and
  `backend/.env`'s real `DATABASE_URL` points at a SHARED (not fresh/
  disposable) Supabase instance — used above only to validate the V0-gate
  fix via a pre-existing, self-cleaning, narrowly-scoped test, not to run
  this new data-writing script. Flagging per the kickoff's own routing
  instruction rather than unilaterally running it against shared infra —
  ready to run the moment either the disposable-DB path or "go ahead on
  the shared DB" is confirmed. **Note re: RESEARCH's entry directly
  above (Band 2 founding-loop exit-criterion) — resolved by this item**:
  Chaitanya's infra report (cited there) already ran the OLD
  bootstrap_demo.py against a real disposable DB and found it left
  episodes/observations/procedures/evidence at 0 rows — exactly the gap
  this rewrite closes. That same disposable-DB pipeline is the natural
  next step for this item's own flagged live run: it would simultaneously
  produce demo.md §3's proof AND the "one hand-audited live run from
  zero" evidence ROADMAP bullet 1 asks for.
