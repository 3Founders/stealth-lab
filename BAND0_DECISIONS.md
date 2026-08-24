# Band 0 Decision Sheet

Five strategic decisions hiding inside Band 0's "paper edits." These are founder-owned:
they set routing thresholds, system personality, product risk posture, and one-way-door
architecture. Everything else in Band 0 (numbering fixes, mutability-class rewrite,
freshness table, replayability invariants) is mechanical and needs only a skim.

**How to use:** answer each ruling line below (`Ruling:` → pick an option or override),
then hand back to the implementing agent. The agent folds rulings into the spec/schema
edits **before** touching any other band. Unanswered items proceed on the recommended
default EXCEPT D4, which blocks until answered (one-way door, per ROADMAP ground rules).

---

## D1 — Capability band boundaries (ROADMAP 0.4)

**Question:** Where do the ladder levels 0–5 sit on the P(outcome | state, procedure,
implementation) scale? These bands become routing thresholds, scoreboard semantics, and
the number defended in due diligence.

| Option | Mapping (level = band of P) | Posture |
|---|---|---|
| A. Conservative | 1: observed (>0) · 2: ≥0.5 · 3: ≥0.75 · 4: ≥0.90 · 5: ≥0.97 + cross-environment reproduction | Slow to trust, strong claims |
| B. Moderate (recommended) | 2: ≥0.5 · 3: ≥0.70 · 4: ≥0.85 · 5: ≥0.95 + reproduction in ≥2 environments | Balanced; routing threshold 0.85–0.90 lands mid-level-4 |
| C. Aggressive | 3: ≥0.60 · 4: ≥0.80 · 5: ≥0.90 | Fast promotion, higher false-reuse exposure |

**Recommendation:** B — aligns with spec §23's example threshold (≥0.90 ≈ top of level 4)
and leaves headroom so "trusted" stays rare enough to mean something.

**Ruling:** _(pick A/B/C or write your own boundaries)_

---

## D2 — Belief-aggregation contract (ROADMAP 0.7)

**Question:** Which invariants bind every belief computation? This is constitutional law
for every score the system ever emits.

Three sub-rulings:

1. **Contradiction dominance:** does one sufficiently strong contradicting evidence row
   cap belief below "supported," regardless of supporting volume?
   - Recommended: **yes** — dominance threshold = evidence strength > 0.8 with valid
     independence group forces status ≤ `disputed` until resolved.
2. **Independence capping:** how do same-group evidence rows count?
   - Recommended: effective-n = number of distinct independence groups; rows within a
     group contribute diminishing weight (√ of count).
3. **Monotonicity:** adding supporting evidence may never lower belief; adding
   contradicting may never raise it.
   - Recommended: **required** (non-negotiable invariant; testable property).

**Rulings:** 1_ 2_ 3_

---

## D3 — Retirement aggressiveness (ROADMAP 0.8)

**Question:** How readily does the system demote procedures it itself learned when their
measured utility goes net-negative?

| Option | Criterion | User-visible feel |
|---|---|---|
| A. Protective | Retire only after utility < 0 across 20+ executions AND match-cost > savings for 14 days | Skills rarely vanish; slower library |
| B. Balanced (recommended) | Utility < 0 over trailing window (≥10 executions) → auto-quarantine (not delete); reinstated on fresh evidence | Existing circuit-breaker semantics (`check_quarantine_and_disable`) generalize |
| C. Ruthless | Any net-negative window → immediate demotion out of candidate sets | Sharpest confrontation of the utility problem; users may watch skills disappear |

**Recommendation:** B — extends the quarantine machinery already in `18_procedures.sql`
rather than inventing new lifecycle states; deletion never automatic, matching §19.

**Ruling:** _

---

## D4 — Deletion mechanism (ROADMAP 0.9) ⛔ BLOCKS UNTIL ANSWERED

**Question:** How is erasure reconciled with append-only history (§19 vs §34)?

| Option | Mechanism | Trade-off |
|---|---|---|
| A. Crypto-shredding (recommended) | Payloads encrypted per-scope key; erasure = destroy that key. History rows survive, content unrecoverable | Cleanest fit with append-only; key management is new critical infrastructure |
| B. Tombstone-eviction | Append a tombstone record; original payload physically purged beyond recovery window | Simpler crypto story; weakens replayability guarantees |
| C. Hybrid | Shred raw payloads/artifacts; keep derived objects' hashes + structure | Middle path; most complex to specify |

**Recommendation:** A, with key hierarchy mirroring scope types (Band 1.3's shard key
becomes the shredding key too — one concept, two jobs). Requires answering: who holds
organization-scope keys?

**Ruling:** _ (**mandatory**) — plus key-custody answer: _

---

## D5 — Scope vocabulary winner (ROADMAP 0.2)

**Question:** §3 lists `branch` but not `entity`; §7 lists `entity` but not `branch`.
Which set is canonical for the shard key?

| Option | Canonical set | Consequence |
|---|---|---|
| A. Superset (recommended) | global, organization, team, project, repository, branch, user, session, task, entity | Both documents were protecting different needs (repos have branches; claims attach to entities); one vocabulary, ten types |
| B. §3 minimal | …task only (drop `entity`) | Entity-attached claims need a workaround via repository/session scope |
| C. §7 claim-only | …entity (drop `branch`) | Breaks repo-lineage scoping that trace ingestion already emits |

**Recommendation:** A — the union costs nothing today (columns are text) and avoids a
painful migration the day entity-scoped claims arrive.

**Ruling:** _

---

## After you rule

Implementing agent: fold each ruling into the target document (spec v4 §§13/16/19/23/34;
schema.md Procedure/Claim/scope preamble), update ROADMAP.md Appendix C rows if test
implications change, commit as `Band 0: decision sheet rulings applied (D1–D5)`, and
note any ruling you believe is wrong next to its line before proceeding — disagreement
is recorded, then executed.
