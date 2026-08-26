# Model-Decides Stale-Procedure Task Tier — Design

**Lane:** research · **Date:** 2026-08-26 · **Task:** board Lane RESEARCH item 5
(CLAUDE.md kickoff). **Status: design only — no implementation, no fixture files
edited.** Everything referenced under `experiments/harness/**` is described, not
touched (that tree is Lane MEASURE's exclusively).

## 1. The gap this closes

`run1-verification.md` (this lane, 2026-08-26) found that RUN #1's headline —
"C 6/6 vs B 0/6 stale-refusal, McNemar p≈0.031" — is not evidence any model
detects staleness. Every one of C's six refusals was decided by
`mcp_surface.StubSurface.check_applicability`'s deterministic gate
(`verdict = not p["stale"]`, mcp_surface.py:88) **before the model saw the
card**. The one task that reached the model, `mic-refund-003`
(`substrate_bypasses_gate: true`), returned an unparseable decision — n=1,
invalid. Arm B never receives procedure ids at all (openrouter_arms.py:496–510),
so it cannot structurally refuse anything, at any n.

The company's differentiator claim is that the verified substrate causes the
**model** to refuse a stale procedure it would otherwise reuse. Today's harness
cannot produce that evidence — it can only show that a pre-gate blocks stale
offers, which is a true and useful property, but a different (and weaker) claim.

## 2. The mechanism already exists — it's just switched off

This is the load-bearing discovery of this design: **no harness code needs to
change.** The bypass path is already built and already proven live:

- `mcp_surface.py:85-86` — `if context.get("bypasses_gate"): verdict = True`.
  Unconditional: it returns `True` regardless of the procedure's true `stale`
  value.
- `openrouter_arms.py:552-553` — `RealProcedureAgent.arun` builds
  `gate_ctx = {"bypasses_gate": True} if task.get("substrate_bypasses_gate")
  else {}` and threads it into **both** the pre-offer check (line 558) and the
  post-decision reuse-credit check (line 588). With bypass on, the card is
  always offered, and if the model proposes reuse it is always credited — the
  model's choice is what determines the outcome, not the gate.
- The one existing task using it, `mic-refund-003`, is documented in
  `scenarios.json` as "the gate has been poisoned to fail open" — i.e. it was
  authored as a deliberate **honest-negative slot for the false-reuse metric**,
  not as a model-decides staleness probe. Its scenario notes say so explicitly:
  "arm C is EXPECTED to trip here." That framing is fine for its original
  purpose; it just means it's the wrong (and only) data point for this claim.

So a model-decides tier is **entirely a fixture-content addition**: new rows in
`tasks.json` with `substrate_bypasses_gate: true`, reusing the exact code path
already exercised once. No changes to `mcp_surface.py`, `openrouter_arms.py`,
or — see §5 — `scoring.py` are required to build it.

## 3. Design requirement the current single task misses: specificity

`mic-refund-003` only tests one direction — offer a stale card, see if the
model refuses. A tier built only from that shape cannot distinguish "the model
detected staleness" from "the model refuses everything offered to it." Both
strategies would look identical on trap-only tasks. **The tier needs a control
condition:** tasks that offer a *currently-valid* (non-stale) procedure under
the same bypassed-gate mechanism, where the correct behavior is to reuse it,
not refuse it. This is the missing half of the instrument.

Convenient fact: the mechanism doesn't care which procedure_id you put in
`stale_offer`, or what its ground-truth `stale` value is — it just gates and
offers whatever's named there. So a control task is authored by pointing
`stale_offer` at a `stale: false` procedure (e.g. `refund-review-v2` instead
of `refund-auto-v1`) with `substrate_bypasses_gate: true`. `scoring.py`'s
`_is_stale` lookup (scoring.py:63-65) already reads ground truth per-id, so a
correct accept of a control card is never miscounted as a missed stale
refusal — that bucketing is free.

Note: bypass is a no-op for control tasks specifically (`check_applicability`
without bypass already returns `not stale` = `True` for a non-stale procedure)
— but setting it uniformly across the whole tier is still the right call for
protocol cleanliness: every episode in the tier is then legible as
"model-decides" from the flag alone, with no need to reconstruct per-task
whether the gate or the model was actually in control when someone reads the
journal later.

## 4. Task schema — two shapes, single-offer (recommended for wave 1)

Both shapes reuse the **existing** four procedure pairs in `procedures.json`
(refunds, deps, pdf-etl, env — each already has one `stale:true` and one
`stale:false` record) and the existing `rag_corpus.json` blob per domain. No
new procedure cards need authoring for wave 1.

**Trap task** (sensitivity — correct behavior: refuse):
```json
{
  "task_id": "dec-refund-101",
  "domain": "refunds",
  "unseen": false,
  "solo_outcome": "pass",
  "rag": "misleading",
  "applicable_procedure": null,
  "stale_offer": "refund-auto-v1",
  "substrate_bypasses_gate": true
}
```
Situation text varies the surface details per replicate (different dollar
amount / days-past-purchase each time — e.g. "$540, 95 days" / "$610, 140
days" / "$505, 91 days" — near-boundary values are useful precisely because
they force the model to actually check the stated assumption
`"amount < $500"` / `"purchase age <= 90 days"` rather than pattern-match on
an obviously huge number) while keeping the same procedure card and the same
underlying assumption-violation logic. `rag: misleading` points B at the same
domain blob already authored (`rag-refunds-1`), which recommends the identical
trap in prose form — B's parallel decision is present in its channel too.

**Control task** (specificity — correct behavior: reuse):
```json
{
  "task_id": "dec-refund-102",
  "domain": "refunds",
  "unseen": false,
  "solo_outcome": "fail",
  "rag": "helpful",
  "applicable_procedure": null,
  "stale_offer": "refund-review-v2",
  "substrate_bypasses_gate": true
}
```
Situation text states facts that *satisfy* the offered procedure's assumptions
("support ticket exists", "customer identity verified") so a correct accept is
achievable and a refusal here is genuinely wrong, not merely untested.

An optional wave-2 **dual-offer** shape (`stale_offer` + `applicable_procedure`
both set, bypass on, mirroring the existing `mic-pdf-002/003` pattern) tests
sensitivity and specificity in one episode and roughly halves the task count
needed for the same statistical power. It's not the wave-1 recommendation
because `offered[]` is built in a fixed order — `stale_offer` first, then
`applicable_procedure` (openrouter_arms.py:555-572) — and an LLM serial-position
bias could masquerade as detection skill. Single-offer tasks have no ordering
confound at all. If MEASURE wants the efficiency win later, the clean fix is
randomizing offer order per episode (a real code change, MEASURE's call, not
proposed here) — flagged as an open question in §6, not assumed.

## 5. Scoring — zero code changes needed

Every statistic this tier needs is already derivable from **existing** episode
fields plus `procedures.json` ground truth, exactly the way
`.scratch/research/run1_verify.py` independently recomputed RUN #1 without
importing harness scoring:

- **Sensitivity (paired, McNemar-eligible B vs C)**: per trap task,
  `avoided_stale_trap` :=
  - C: `stale_refusal_correct` (scoring.py:82, unmodified — refused a
    genuinely stale offered procedure).
  - B: `not reuse_caused_failure` restricted to that task (B has no
    procedure-id-level refusal — see run1-verification.md's own point that
    arm B "structurally cannot refuse"; its parallel signal is behavioral:
    did following the misleading blob cause the failure, or did the model
    route around it). This is the correct apples-to-apples framing per the
    CLAUDE.md brief's "arms A and B stay scoreable on the same decision" — the
    decision being compared is *trap avoidance*, not *literal id refusal*,
    because only C's surface has ids to refuse in the first place. That
    asymmetry is real and structural, not a scoring gap to paper over.
- **Specificity (C-only descriptive guardrail, not McNemar-paired)**:
  `false_refusal` := `pid in refused_procedure_ids` and
  `procedures_by_id[pid]["stale"] is False` — the inverse of the existing
  `_is_stale` check (scoring.py:63-65), computable from raw episodes +
  `procedures.json` with no new field. This is **not** paired against B
  (B cannot refuse a named id at all, so there is nothing to pair against);
  it's reported as C's own rate, the way RUN #1 already reports marginals.
  Its job is to prevent "C refuses everything" from masquerading as "C
  detects staleness" — a real risk once sensitivity is the only number
  reported.
- **Arm A** stays the zero-memory control it already is — it never sees
  offers (`sees_offers = False`), so it contributes no per-task decision here,
  only the solo-failure baseline the existing tier also reports.

If MEASURE later wants these as first-class `classify()` output columns for
the shipped scoreboard (rather than a report-layer recomputation), that's a
genuinely optional convenience addition to `scoring.py` — noted in the
cross-lane request below as optional, not required to collect or interpret
wave-1 data.

## 6. Sizing — how many tasks to plausibly clear `MIN_PUBLIC_DISCORDANT_N=6`

**Honesty check first:** CLAUDE.md's kickoff text says to reason from "RUN #1 +
run2 + run3's variance." Only RUN #1 exists — `real_arms_results.jsonl` /
`real_spend.jsonl` have one run's worth of rows, and the board's RUN #1 log
entry says repeat sweeps are "queued," not done. There is no run2/run3 data in
this repo. Sizing below is reasoned from RUN #1's single data point plus
`mcnemar_power.py`'s own math — not from variance that doesn't exist yet. Flag
this explicitly rather than imply a variance estimate this lane doesn't have.

Two different bars matter and they're easy to conflate:

1. **The floor** — `MIN_PUBLIC_DISCORDANT_N=6` (packaging's own small-n caveat
   trigger) — just needs ≥6 discordant pairs to exist, regardless of which way
   they lean.
2. **Significance** — needs enough discordant pairs *and* a large enough split
   between them to clear α=0.05 (this is exactly Caveat 1 of
   `run1-verification.md`: 5 discordant pairs at an even-ish split was p=0.0625,
   not significant; 6 pairs with a bigger split was p=0.03125).

For the floor, model the trap series as n independent Bernoulli trials with
unknown "discordant rate" d (fraction of trap tasks where B and C land on
different avoided/not-avoided outcomes) — RUN #1 gives no clean anchor for d
under real model-decides conditions (its only such episode was invalid), so
treat d as unknown across a plausible 0.4–0.7 range and check P(≥6 discordant
pairs | n tasks):

| n valid tasks | d=0.4 | d=0.5 | d=0.6 | d=0.7 |
|---|---|---|---|---|
| 12 | 0.33 | 0.61 | 0.84 | 0.96 |
| 16 | 0.67 | 0.89 | 0.98 | 1.00 |
| 20 | 0.87 | 0.98 | 1.00 | 1.00 |

(computed via `math.comb` binomial tail, same discipline as
`mcnemar_power.py`; script inline below for rerun.)

For significance itself, `mcnemar_power.required_n()` (run against the live
module) gives, for a given true win-ratio q (probability C lands on the
correct side of a discordant pair):

| q (C's share of discordant pairs) | required_n for 80% power |
|---|---|
| 0.70 | 49 |
| 0.75 | 30 |
| 0.80 | 20 |
| 0.85 | 15 |
| 0.90 | 12 |

Read together: **a first wave sized to clear the floor will very likely be
directionally suggestive but underpowered for significance unless the true
effect is large (q≥0.85).** That's not a flaw in the design — it's the same
honest shape RUN #1 already had, and the fix is the same one
`mcnemar_power`'s own footer already prescribes: report the observed q after
wave 1, then use `required_n(q_observed)` to size wave 2. This lane isn't
recommending a single-shot "collect N and declare victory" — it's recommending
staged collection with the sizing formula already in the repo.

**Recommendation:** author **24 single-offer model-decides tasks** — 3 trap +
3 control per domain × 4 domains — for wave 1. Budget for invalid-episode
attrition: RUN #1 saw roughly 1/11 (~9%) invalid episodes on C under ordinary
conditions, and every task in this tier is model-decides (higher JSON-decision
load than the mostly-gate-decided RUN #1 mix), so expect attrition somewhat
above that baseline. 24 authored → a plausible 18–22 valid per series lands
solidly in the n=16–20 rows of the table above (67–100% chance of clearing the
floor even under a pessimistic d=0.4). If wave 1's observed q is ≥0.85, the
same 24 tasks likely already carry a significant result; if q is in the
0.70–0.80 range (plausible, and still a real, useful signal), plan a sized
wave 2 using `required_n(q_observed)` before any public phrasing — exactly
the discipline `run1-verification.md` recommended for the existing tier.

```python
# rerunnable sizing check (stdlib only, mirrors mcnemar_power.py's discipline)
import math
def p_at_least_6(n, d):
    return sum(math.comb(n, i) * d**i * (1 - d)**(n - i) for i in range(6, n + 1))
for n in (12, 16, 20, 24):
    print(n, [round(p_at_least_6(n, d), 2) for d in (0.4, 0.5, 0.6, 0.7)])
```

## 7. Cross-lane request → MEASURE (owner of `experiments/harness/**`)

This lane owns `.scratch/research/**` only; everything below is a request, not
an edit, mirroring the board's existing "Cross-lane requests" format.

1. **Fixture content (required for wave 1):** add ~24 new task rows to
   `experiments/harness/fixtures/micro/tasks.json` per the single-offer shape
   in §4 (task_id prefix suggestion: `dec-<domain>-<n>`, kept distinct from
   the existing `mic-*` set so RUN #1's data and this tier's data are never
   accidentally pooled), plus matching `scenarios.json` entries (situation
   text + `success_criteria`) for each new task_id, following the existing
   authoring pattern. All 4 procedure pairs and all 4 domain rag blobs already
   exist and need no changes. Vary situational specifics per replicate (§4)
   rather than repeating identical prose against the same procedure card.
2. **No code changes required** to ship wave 1: `mcp_surface.py`'s bypass path
   and `openrouter_arms.py`'s `RealProcedureAgent` already support this
   exactly as built (§2); `scoring.py` needs no new fields to *collect or
   analyze* the data (§5) — a rerunnable report script in
   `.scratch/research/` (this lane's usual pattern, e.g. a follow-on to
   `run1_verify.py`) can compute both series from raw episodes +
   `procedures.json` once real rows exist.
3. **Optional convenience (not blocking):** if MEASURE wants
   `avoided_stale_trap` / `false_refusal` as first-class `classify()` /
   scoreboard columns rather than a report-layer computation, that's a small,
   independent `scoring.py` addition MEASURE can take at its discretion — the
   design in §5 works either way.
4. **Open question, not assumed (§4 dual-offer note):** whether to randomize
   `offered[]` order in `RealProcedureAgent.arun` to make a dual-offer
   compound-task format safe from serial-position bias. Wave-1 as designed
   here doesn't need this (single-offer only); flagging it now so it isn't
   rediscovered independently if a wave-2 efficiency push reaches for the
   dual-offer shape.

## 8. Summary for the board Log

RUN #1's 6-vs-0 stale-refusal split was gate-determined, not model-evidence
(this lane's prior finding). The fix already lives in the harness —
`substrate_bypasses_gate` — used exactly once, for an unrelated purpose, and
returned unparseable. This design proposes reusing that exact mechanism for a
dedicated tier: single-offer trap + control tasks across the existing 4
domains, needing zero harness code changes, sized (24 tasks, ~18–22
survivors) to plausibly clear the public `MIN_PUBLIC_DISCORDANT_N=6` floor
while being honest that significance likely needs a sized wave 2. No run2/run3
data exists yet to refine this further — that's a fact worth the board
knowing, not an assumption to paper over.
