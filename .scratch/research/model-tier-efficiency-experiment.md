# Design brief: does a cheap model + StealthLab match/beat a frontier model alone?

_Status: design only, not authorized to run live. Zero cost until someone approves
model access. Written 2026-08-27 by the integrator, at the founder's request._
_Companion docs: `demo.md` (what ships), `commLLM.md` §5 (competitive landscape),
`.scratch/build-board.md` RUN #1 entry (the existing architecture-ablation result
this experiment extends), `experiments/harness/openrouter_arms.py` (the code this
would run through)._

## 1 · The claim, stated precisely

Two distinct claims get talked about together but need separate evidence:

**Claim 1 — the substrate does something new.** A verified-procedural-memory agent
resolves tasks at least as well as a solo frontier agent, while adding near-zero
false reuse and catching stale-precondition reuse the other approaches miss. This
is an **architecture** comparison, same model across arms. **Already has a first
result** (RUN #1, 2026-08-26, 10 tasks, ox-alpha): Arm A (solo) resolved 6/10,
Arm C (verified substrate) resolved 7/10 — matched or beat solo, not just
"kept up" — while adding 0 false reuse and catching 6/6 stale-reuse cases
neither other arm caught. Next step for this claim is just **more
repeats of the same design** — no new engineering.

**Claim 2 — the substrate lets a cheaper model do a frontier model's job.** A
*cheap/small* model, paired with the verified substrate, resolves tasks at a rate
that matches or beats a *frontier* model working alone (no substrate). This is a
**model-tier × architecture** comparison — two dimensions crossed, not one. This
is the new claim this brief is about. **No result exists yet, and the current
harness cannot produce one without either a small code change or a specific
two-sweep run design (both described below).**

If Claim 2 holds even partially, it's a genuinely strong, differentiated
positioning line — "you don't need the expensive model if you have real earned
experience" is a cost-efficiency argument nobody in the competitive landscape
(commLLM.md §5: Foundry, Galileo, Helicone, Langfuse) is making, because none of
them have a substrate that could make it true.

## 2 · What the harness supports today (checked directly, 2026-08-27)

`experiments/harness/run_real_arms.py` takes one `--models` flag: a single
fallback chain shared across **all three arms** (A/B/C) in a run. There is no
per-arm model override. `openrouter_arms.py`'s `OpenRouterClient` walks the same
chain regardless of which arm is calling it, falling back to the next model in
the chain only on a non-retryable error (e.g. a 429). Each episode's row already
records `served_by_model` — which model actually answered — so post-hoc auditing
of what really ran is possible and already proven useful: RESEARCH's independent
verification (`.scratch/research/model-decides-verification.md`) caught both
prior "ox-alpha" sweeps having silently fallen back to `gpt-4o-mini` mid-run.

**Implication:** you cannot currently run one sweep where Arm A gets a frontier
model and Arm C gets a cheap model. Two options:

- **Option (a) — two separate sweeps, no new code.** Sweep 1: `--models` chain
  contains ONLY the frontier candidate, run all three arms. Sweep 2: `--models`
  chain contains ONLY the cheap candidate, run all three arms. Compare Arm A of
  sweep 1 (frontier, solo) against Arm C of sweep 2 (cheap, verified substrate).
  Cheapest to build, but doubles the task-set exposure needed (two full sweeps
  instead of one) and the comparison is across runs, not within one — any
  non-model confound between sweeps (task order, timing) is a real risk to
  disclose.
- **Option (b) — per-arm model pinning, small code change.** Add a per-arm
  model override to `build_agents`/`OpenRouterClient` construction (e.g.
  `--model-a`, `--model-c`, falling back to `--models` if unset). One sweep,
  cleaner comparison, same spend guard/resume machinery untouched. Estimated
  a half-day of focused work plus offline tests, following the same discipline
  every other harness change here has used (regression tests against a fake
  client, `served_by_model` still the ground truth for what actually ran).

**Recommendation: build option (b).** It's a small, well-scoped, testable change
that removes a whole category of "which sweep confound" disclosure burden from
every future run of this design, not just this one.

## 3 · The model-access gate (the real blocker, not engineering)

Free-tier OpenRouter models cluster in a similar small/cheap capability band.
Comparing one free model against another free model tests "cheap vs.
slightly-less-cheap," not "cheap vs. frontier" — it would not support Claim 2
credibly even if the harness fully supported per-arm pinning. Two ways to get a
real frontier anchor:

1. A small paid-tier top-up (even a few real dollars buys a meaningful number of
   frontier-tier calls at typical per-task token counts for this harness's
   fixture sizes — see §5 for the exact spend estimate).
2. A free-tier model that's genuinely, meaningfully larger/more capable than the
   cheap candidate — worth a fresh catalog check at run time (OpenRouter's free
   catalog changes; `ox-alpha` itself disappeared entirely between waves), not
   assumed from what was available before.

This brief takes no position on which — that's the same "confirm before
credential/spend use" call already standing for every other live-model item in
this project. It's flagged here so the decision, when made, can point at this
document instead of re-deriving the tradeoff from scratch.

## 4 · Proposed design

| | Arm A′ (frontier, solo) | Arm C′ (cheap, verified substrate) |
|---|---|---|
| Model | one frontier-tier candidate (TBD at run time — see §3) | one cheap/free candidate (TBD at run time) |
| Architecture | solo agent, no memory | full verified substrate (same Arm C logic as RUN #1) |
| Task set | same fixture pack RUN #1 used, or a fresh one of equal or larger n | same as Arm A′, same tasks, same order |

Primary outcome: resolution rate, Arm A′ vs Arm C′. Secondary: false-reuse rate,
stale-refusal catch rate (Arm C′ only — Arm A′ has no memory to misuse). Keep
Arms B (ordinary memory) in the run if budget allows, for a fuller three-way
picture, but the headline comparison this brief is chasing is strictly A′ vs C′.

**Sample size honesty:** RUN #1 was n=10 and explicitly below the k≥6
significance floor for the *stale-refusal* metric specifically (per
`proj_status.md`'s own McNemar note). Whatever n this runs at, report the same
floor discipline — don't headline a resolution-rate comparison as decisive below
a defensible n, and say so plainly if it's still directional-only.

## 5 · Rough spend estimate (so the model-access decision has a real number)

Using this harness's own fixture pack size (RUN #1: 10 tasks, ~95 billed calls,
$0.27 total on a mid-tier paid model) as the reference point: a frontier-tier
model typically runs 3-10x the per-token price of a mid-tier one. For a
comparable n=10-20 task run on Arm A′ alone (Arm C′ stays free if the cheap
candidate is genuinely `:free`), a reasonable budget ceiling is **$5-15** for a
first pass, scaling with however many repeats the significance floor above ends
up needing. This is a planning number, not a quote — re-derive from the actual
chosen model's real per-token pricing before spending anything.

## 6 · What this brief does NOT do

No code has been written. No sweep has been run. No model has been chosen. This
is the same "ready to run, zero spend until authorized" posture MEASURE's
LLM-judge design brief (`.scratch/research/observation-labeling-technique-brief.md`)
used successfully — the next step, when authorized, is either handing §2's
option (b) to a lane as a real kickoff, or running §2's option (a) directly if
the founder would rather not wait on the code change.

## 7 · Open questions for the founder (numbered, defaults proposed per house rule)

1. **Paid top-up vs. free-tier frontier proxy for Arm A′?** Default: hold, revisit
   once the current free-tier README-quickstart decision is settled — same
   underlying budget conversation, no reason to split it into two decisions.
2. **Option (a) two-sweep vs. option (b) per-arm pinning?** Default: (b), for the
   reasons in §2 — but (a) is a legitimate fallback if engineering time is the
   tighter constraint than spend clarity.
3. **Reuse RUN #1's exact fixture pack, or a fresh/larger one?** Default: reuse
   RUN #1's pack for the first pass (keeps this comparable to the existing
   result), consider a larger pack only once the significance floor demands it.
