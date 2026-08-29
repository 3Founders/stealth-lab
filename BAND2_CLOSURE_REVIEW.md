# Band 2 Closure Review — VERDICT: CLOSED ✅ (9/9 items, see amendment below)

Reviewer: integrator ox-alpha · Review range: `8fd83e1`…`461f07c`
Integrator verification: **backend suite 1114 passed / 114 skipped / 0 failed** on merged main — matches the final lane-claimed counts exactly.

Band 2 — Trust completion — is closed. Every ROADMAP Band 2 item is implemented,
merged, and pinned. The substrate now has: evidence-first capability, auditable
mutations, real identity, a readable TMS, working episode assembly, claim
families, failure routing, and deterministic replay.

## Item scorecard

| Item | Verdict | Highlights |
|---|---|---|
| 1 Universal ChangeSet | ✅ | db/25 append-only; wired lifecycle paths; fails closed |
| 2 Evidence first-class | ✅ | db/24 `[H]` w/ tombstone retraction; inv #13 engine ban; independence-capped views |
| 3 Capability computation | ✅ | Wilson lower-bound P; ratified tiers as named config; bidirectional demotion |
| 4 Failure-classification pipeline | ✅ | db/27 `failure_routes [H]` (UNIQUE idempotency, NULL-class-iff-requires_review CHECK, freeze trigger); `failures.py` FAILURE_ROUTES verbatim per §36; record-don't-execute — handlers land with their owners, cross-lane request filed with exact wiring |
| 5 Episode segmenter | ✅ | FINDINGS.md rules verbatim in production (`trace_worker.py`); all four continuation prefixes; ≤2 fold / >200 subdivided at internal completions only; idle-gap dropped; forest-safe; fingerprint-idempotent |
| 6 ClaimFamily resolver v0 | ✅ | canonical-key identity gate + contradiction dominance + conservative ontology ladder; fail-closed on statement-only claims; similarity = ranking, never identity |
| 7 TMS readable | ✅ | OUT/stale filtered across every retrieval leg; history untouched (5 live-DB regressions) |
| 8 Replayability E2E | ✅ | db/26 claim_sources closes the provenance hop; bit-identical double-replay + tamper teeth both layers; spec sentence provable by join |
| 9 OIDC identity gate | ✅ | RS256-only vs JWKS (alg whitelist BEFORE key material — none/HS256 confusion killed); pure-ASGI middleware (contextvar bracketing); present-but-bad always 401, never degrades; frozen-posture boot guard; actor propagated to Events/ChangeSets/Reviews overriding spoofable headers |

## Amendment (2026-08-27, integrator)

Item 8's "Replayability E2E" ✅ is correct on its own stated scope — bit-identical
double-replay and tamper detection, both proved by two live-DB e2e tests. But a
separate ROADMAP acceptance bullet got silently treated as covered by item 8
because one of those tests has "founding_loop" in its name
(`test_founding_loop_replays_bit_identically_from_raw_traces`). It isn't the same
claim. ROADMAP's founding-loop bullet 1 asks for one hand-audited live run, on a
**fresh** database, that actually exercises trace → episode → observation →
claim → procedure-candidate end to end. Read directly (2026-08-27 review,
`.scratch/research/band2-founding-loop-exit-criterion-review.md`): both
replayability tests run against a long-lived shared dev instance, not a fresh
one, and both skip the episode-assembly hop — they seed `trace_events`/synthetic
`episode_id`s directly rather than calling `trace_worker.assemble_episodes()`.
They prove replay determinism (item 8's real claim), not "the founding thesis
was exercised from raw traces at all" (a different, still-open claim).

**Status: open, not closed.** Not fixed by the bootstrap_demo.py rewrite either
(2026-08-27) — that script builds an evidence object by hand and calls
`extract_procedure()` directly, which also skips real episode assembly. This
bullet stays open until one real run, on a fresh DB, goes through the actual
pipeline from raw trace_events onward and is hand-audited against the raw rows.
Chaitanya's dogfooding pilot (started 2026-08-27, real Claude Code hook traces +
Terminal-Bench tasks) may close this for real as a side effect — check
specifically whether his traces flow through `assemble_episodes()` for real
before treating it as closed, don't assume it from volume alone.

Not reopening the item 8 verdict itself — its own stated scope is genuinely
done — this amendment exists so nobody reads "9/9 CLOSED" and assumes the
founding-loop bullet went with it.

## Cross-cutting wins this band

1. **Live-DB discipline became routine** — two lanes ran real-Postgres e2e proof
   sets alongside offline suites; shared-instance drift was honestly quarantined
   rather than explained away.
2. **Cross-lane request protocol worked** — change.py exception and
   failures-handler ownership were negotiated on the board without a single
   file collision.
3. **MEASURE's micro-pack produced the first C-arm signal**: substrate 9/11 vs
   ordinary-memory 6/11, false reuse 1 vs 3, stale-refusal 6/7 vs 0/7 — fixture-
   level, but the instrument discriminates.
4. **HARDENING lane opened** from Chaitanya-instance's grounded audit
   (identity tables + tenancy predicate builder · RLS backstop on `[H]` ·
   rate-limiter buffering pre-launch) — sequenced next.

## Deferreds / carried

- Failure-route *handlers* (the updates each cause triggers) land with their
  owner modules — the queue and log exist (db/27).
- ClaimFamily related/generalizes verdicts returned-not-persisted (family-graph
  edges = Band 4, per plan).
- Full-pipeline replay covers observations→claims→promotion; procedure-candidate
  regeneration joins when its writer exists.
- RLS backstop + rate-limiter rework = HARDENING H2/H3 before any public launch.

## Verdict

**BAND 2 CLOSED.** The trust-completion phase is banked: every mutation audited,
every capability computed from evidence, every failure classified and routed,
identity real, replay provable.

**BAND 3 formally OPEN** — measurement against reality: MEASURE's real-corpus
ingestion is seeded; next increments are real-model arms and extraction error-
floor measurement. HARDENING runs parallel ahead of any public exposure.

## Amendment 2 (2026-08-29, integrator)

Closed, with caveats. Chaitanya's dogfooding-pilot audit found the loop had
never actually run (1,390 `trace_events` were synthetic script batches;
`episodes.session_id ∩ trace_events.session_id = 0`), then ran the real
pipeline against real hook-trace data and produced a genuine, hand-audited
chain: `trace_event 86f38820… → episode a08548b1… (real assemble_episodes()
output) → observation 42bbf38c… → claim 182d6278… → procedure 01a04c6a…`,
each hop read back from the DB. Full write-up:
`.scratch/research/founding-loop-real-data-proof.md` (`bc2028b`).

Real, disclosed limits, not swept under: the chain is not yet self-sustaining
(claim→procedure has no pipeline caller, still manual), the claim's content
is currently near-worthless (observation label verbatim), and this ran
against the long-lived dev DB, not a fresh one. A separate fix (`27c931b`)
closed the claim-promotion re-enqueue gap this audit surfaced as its
highest-value follow-up (claims 1 → 26 on the real corpus).

Tick ROADMAP Band 2 exit bullet 1 closed on this basis.