# Band 2 founding-loop exit criterion vs BAND2_CLOSURE_REVIEW.md — verdict

**Question posed (Chaitanya infra report, `755d8af`, 2026-08-27):** is "Band 2's
founding-loop exit criterion is still unexercised on a fresh DB" — a knock-on effect
of `bootstrap_demo.py`'s gap — a genuine crack in Band 2's already-reviewed closure,
or a separate criterion Band 2 never claimed to cover?

## Verdict: genuine gap, narrowly scoped — the closure review never actually checked
this criterion off, but its summary verdict reads as if it had.

## The four exit criteria, and what closed against what

`ROADMAP.md` §"Band 2 exit criteria *(completing the milestone-M1 gate)*" lists
exactly four bullets:

1. **Founding loop executed once end-to-end on real data** — "a live session flows
   trace → episode → observation → claim → procedure candidate, hand-audited at each
   hop. The database currently contains zero inhabitants of every knowledge-layer
   entity; until this runs once, the substrate's founding thesis is unexercised."
2. Outcome→capability traceability (Band 3.5's test).
3. TMS readability — OUT/stale claims absent from retrieval (2.7).
4. Replayability — derived objects regenerate deterministically from raw traces (2.8).

`BAND2_CLOSURE_REVIEW.md`'s 9-item scorecard maps cleanly onto bullets 3 and 4:
item 7 "TMS readable" ✅ ↔ bullet 3; item 8 "Replayability E2E" ✅ ↔ bullet 4. Bullet
2 is out of scope for this check (it's a Band 3 test, not reviewed here). **Bullet 1
has no corresponding scorecard item at all.** A direct grep of
`BAND2_CLOSURE_REVIEW.md` for "founding", "real data", "hand-audited", "end-to-end",
or "inhabitant" returns zero matches — the review never engages with this specific
criterion, positively or negatively, not even in its "Deferreds / carried" section
where other known gaps (failure-route handlers, ClaimFamily edges, RLS/rate-limiter
work) are honestly listed.

Yet the review's summary verdict says: *"Band 2 — Trust completion — is closed.
Every ROADMAP Band 2 item is implemented, merged, and pinned"* and **"BAND 2
CLOSED."** That phrasing claims full coverage of ROADMAP's Band 2 section, which
includes this exit-criteria list by name. It is an overclaim relative to what the
scorecard actually checked.

## Why item 8's live e2e tests don't secretly cover bullet 1

Item 8 cites `tests/test_band2_8_replayability.py`'s two live e2e tests
(`test_founding_loop_replays_bit_identically_from_raw_traces` and
`test_procedure_candidate_replays_bit_identically_from_raw_traces` — note the first
one's *name* literally contains "founding_loop", which is almost certainly why this
looked closed at a glance). Read directly, neither satisfies bullet 1:

- **Not a fresh DB.** Both run against what the test file's own comment (line 405-408)
  calls "the long-lived shared dev instance" — the opposite of ROADMAP's explicit
  "database currently contains zero inhabitants" framing. One of the two tests even
  has to `pytest.skip` there when the shared instance's schema predates Band-1
  columns.
- **Skips the episode hop.** ROADMAP's chain is trace → **episode** → observation →
  claim → procedure candidate. Both tests seed `trace_events` rows directly and jump
  straight to `process_pending_jobs()` → observations; no call to
  `trace_worker.assemble_episodes()` appears anywhere. The second test's `episode_id`
  is a bare `str(uuid.uuid4())` — a fabricated id, never a row produced by the real
  episode-assembly pipeline that Band 2 item 5 (episode segmenter) shipped.
- **Different claim, same words.** What these tests actually prove is *replay
  determinism*: run the pipeline once, replay it twice, assert byte-identical output,
  assert tampering is caught. That's ROADMAP bullet 4 (Replayability), which is
  exactly what item 8 is titled and where these tests are cited. It is not "one
  hand-audited live run establishing the founding thesis is exercised at all,"
  which is what bullet 1 asks for.

## Relationship to bootstrap_demo.py

Chaitanya's report is correct on the substance: the fresh-DB run left
`episodes`/`observations`/`procedures`/`evidence` at 0 rows, so bullet 1 is
unexercised as of that run. But it's important to state the causality precisely —
**bullet 1 was never exercised even before this run.** `bootstrap_demo.py` didn't
newly break an already-closed criterion; it's simply the vehicle that would have
naturally closed it (a fresh-DB, hand-auditable, trace→...→procedure-candidate run
is exactly bullet 1's ask) and turned out, on inspection, not to attempt it at all
(it's the older debate-era seeder, per the infra lane's finding). The gap is not new
information about Band 2 — it was always there, silently uncovered by the closure
review — but the infra report is what surfaced it, correctly.

## Recommendation

Don't reopen Band 2's other 8 items — they're independently, correctly closed and
this review found no issue with any of them. Two options for this one bullet, not
mutually exclusive:

1. **Amend `BAND2_CLOSURE_REVIEW.md`** to explicitly carry bullet 1 as open/deferred
   (matching its own honest "Deferreds / carried" pattern) rather than silently
   absorbed into "CLOSED 9/9" — a documentation-accuracy fix, cheap, should happen
   regardless of when/whether item 2 below lands.
2. **Close it for real** — this is exactly what `PRODUCTION_READINESS.md`'s current
   #1 priority (rewriting `bootstrap_demo.py` to run the actual two-phase story:
   ingest → episode-assembly → extraction → procedure → deliberately broken
   precondition → `check_procedure` → real `WOULD_REFUSE`, on a fresh DB, hand-
   audited) would close in one shot. That work is CORE-B/product-owned, not
   RESEARCH's — flagging here for whoever picks up that item to also tick this
   ROADMAP box explicitly when it lands, rather than leaving it implicit.

Not a cross-lane code request (no harness/backend paths owned by this lane are
implicated) — this is a documentation/closure-bookkeeping finding, boarded per the
founder's direct request.
