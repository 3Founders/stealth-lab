# Band 4 entry — scoping note (2026-08-26, CORE-A)

Short note per CLAUDE.md's kickoff: what "ready to enter Band 4" actually
means. Not implementation — just reading spec v4 / schema.md / ROADMAP.md
against the board's current state.

## The headline: Band 4 isn't gated by a technical checklist, it's gated by
## P3, and P3 hasn't passed

ROADMAP.md's ground rules are explicit and non-negotiable by this lane's own
reading: **"No Band 4/5 work before P3's acceptance test passes on a real
workflow"** (ground rule 4), and Band P's own line: *"gates Band 4 onward —
scale work before a working product repeats the exact failure mode this
plan exists to correct."* Band 4's own critical-path diagram shows Band P
feeding Band 4 directly, same as Band 3. So the real entry criterion for
Band 4 isn't "are the Band 1 prerequisites shipped" (they mostly are — see
below) — it's whether P3 has passed, and it hasn't.

P3, verbatim: *"fresh install → one day of real work → ≥1 procedure reused
with visible provenance, and §40 arm-C beating arm-B on the user's own task
mix."* `proj_status.md` tracks it as `⬜` (not done) as of its last update.

**One correction to the kickoff's framing:** CLAUDE.md said Band 3 is
"reported ~85% done elsewhere on the board" — I couldn't find that figure
anywhere (board, proj_status.md, ROADMAP.md). The closest tracked number is
`proj_status.md`'s **Band 3 ~65%**, explicitly still short on repeats, the
error-floor run, and utility-in-routing. I'm flagging the discrepancy rather
than silently using either number — if "~85%" comes from a conversation or
source outside these files, worth reconciling next time someone updates
`proj_status.md`.

## Where P3 actually stands

- **P1** (installable package, `stealthlab-connect`) — shipped, SHIP lane.
- **P2** (status surface) — shipped, SHIP lane.
- **§40 harness + real-model arms** — built, MEASURE lane; RUN #1 executed
  against real models.
- **The "arm-C beats arm-B" half of P3 doesn't have a clean pass yet.** RUN
  #1's raw resolution marginals were B 7/10 (1 false-reuse) vs C 7/10 (0
  false-reuse) — a cleanliness win for C, not a resolution-rate win. The
  stale-refusal result (C 6/6 vs B 0/6) looked like the strong claim, but
  research's independent verification found the p-value is frame-dependent
  ([0.031, 0.0625], not a clean <0.05) and — more importantly — the journal
  proves those six refusals were **substrate-gate-automatic**: the model
  never saw the stale card in five of six cases. That's evidence the gating
  *system* works, not evidence arm-C's model-level decisions beat arm-B's.
  P3 needs the latter.
- **The "fresh install, someone else's real workflow, one day" half** — no
  evidence of this in the board or proj_status.md. `session_corpus.py`
  ingested 2 of the builder's own Claude Code sessions as unscored dry-runs
  (MEASURE's micro-pack, the P4 dogfooding seed) — that's ingestion
  plumbing proven to work, not a scored P3 trial by a fresh install.

So P3 isn't close to a rounding error away from passing — it needs either a
materially cleaner §40 result (more discordant pairs, a model-decides tier
per research's Question, not gate-automatic refusals) or a redesigned
comparison that doesn't lean on the fixture-determined refusal metric, plus
an actual fresh-install trial on a real task mix that isn't the builder's
own.

## What's already true regardless of the P3 gate (Band 1 prerequisites)

Band 4's individual items name specific Band 1 dependencies. Checking each
against what's shipped:

- Item 2 (partition activation) needs **Band 1.3** (scope columns — shipped,
  every table born with `scope_type`/`scope_entity_id`) and **Band 1.10**
  (append-only discipline at birth — shipped: every `[H]` table
  (evidence/executions/change_sets/change_set_operations/failure_routes)
  was born with a freeze trigger, no retrofit needed). **Both satisfied.**
- Item 4 (dedicated ANN cluster, actually a Band 5 item but same shape) and
  the general vector-tier items lean on **Band 1.6** (embedding provenance
  stamps) — shipped at birth per Band 1's own item list.
- Item 8 (**utility-based retirement live in routing**) explicitly "Depends
  on Band 3.4 data" — Band 3.4 (utility accounting: match cost + retrieval
  overhead measured and stored) is one of the three things `proj_status.md`
  lists as still remaining in Band 3. **Not satisfied yet**, independent of
  the P3 gate.
- Items 5 (TMS dependency-queue service), 6 (ClaimFamily LSH blocking), 7
  (ingestion worker fleet), 9 (belief-aggregation v1) don't name a hard
  Band 1 prerequisite beyond the general substrate, which is in place.

## Net: what "ready to enter Band 4" means in practice

1. **Hard blocker, applies to all of Band 4:** P3 passes. That's a founder/
   product-track call (whose task mix, what "beats" means precisely,
   whether the current harness needs a model-decides tier first per
   research's open question) more than a CORE-A schema question — not
   something this lane can unblock by writing code.
2. **Once P3 clears**, items 2/3 (partitioning, read replicas) are the
   cheapest, lowest-risk entry points — their prerequisites are already
   shipped and neither touches product-facing behavior.
3. **Item 8 stays blocked** even after the P3 gate opens, until Band 3.4
   (utility accounting) lands — a MEASURE-lane item, tracked separately.
4. Items 5/6/7/9 are each substantial ([L]/[M–L]) standalone builds with no
   named blocker beyond "the gate is open" — sequencing among them is a
   founder/integrator prioritization call, not something scoping can
   resolve in the abstract.

No schema or app code touched for this note, per this task's instruction.
