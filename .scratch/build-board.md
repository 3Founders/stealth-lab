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
5. `[ ]` **WAVE-2 / HARDENING H1 (assigned) -- Identity tables + tenancy predicate builder**: organizations/users/roles born additively (db/28); convert tenant filtering into the ONE-predicate-builder pattern in services/access.py (03_access.sql's own confession: column existed, no query ever filtered). WAVE GRANT extends ownership to services/access.py + services/authn.py. Extends in-flight authn work. Proving: tenant-filter SQL-content tests + predicate unit tests.

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
Rule: NO new migrations (schema needs route through CORE-A); no edits outside owned paths.

0. `[x]` **WAVE-2 / HARDENING H3 pre-work swap** -- DONE @2026-08-26 by core-b under the HARDENING section item 3 (same task; canonical record there). Rate-limiter collector treatment: in-process token bucket + buffered ledger flush (trace_collector append->drain pattern) so Postgres becomes audit ledger, not enforcement point. CONSTRAINT: preserve fail-closed-on-infra-error; buffered writes need a replay-or-block rule. Retention sweep for rate_limit_events. NOTE: lands in governance.py -- scoped grant to this lane for backend/app/services/governance.py only.

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

### Lane SHIP (owns `packaging/**`) â€” activates after CORE-A merges 1.7
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

### Lane HARDENING (opened by founder referral of Chaitanya-instance audit, 2026-08-25)
Grounded findings from  3_access.sql/ 4_governance.sql/deps.py review. Sequence: after current OIDC tasks land.
1. [ ] **H1 - Identity tables + tenancy predicate builder**: organizations/users/roles born additively; convert tenant filtering into the same ONE-predicate-builder pattern that made visibility flip-on cheap (03_access.sql's own confession: 'column existed, no query ever filtered'). Extends the in-flight OIDC work.
2. [ ] **H2 - RLS backstop on [H] tables**: SET LOCAL app.tenant_id per transaction + row-level security policies at minimum on append-only truth. App-layer stays PRIMARY (single policy source - no drift between two enforcers). asyncpg caveat: transaction-scoped only, or it leaks across pooled connections.
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
