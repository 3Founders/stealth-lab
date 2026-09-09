# Assumptions — what we believe that we have not proven

Compiled 2026-08-24. Evidence base: Part D of `trial_implementation.md`
(25 mapped items), the phase experiment series (phaseG–O logs and
results.json under `vendor/tau2-bench/data/simulations/`), live
rate-limit header probes against General Compute, market/vendor sweeps,
and an Exa semantic sweep run against these assumptions specifically.

Status markers: **SUPPORTED** (external or internal evidence agrees),
**PARTIAL** (evidence mixed or incomplete), **CHALLENGED** (evidence
pushes against it), **UNTESTED** (no decisive evidence either way).
Every entry names its falsifier — the cheapest observation that would
force a rewrite.

---



## A. Architecture and memory substrate





**A3. Evidence must be first-class and separated from synthetic outcomes.**
Banking-seed verification statistics silently contaminate capability claims
today (Part B, P0).
Status: **SUPPORTED** internally; ASI (arXiv:2504.06821) measures
verification-at-induction as the quality lever (+4.2pp alone); AWM's online
rule (induce only from evaluator-judged successes) is the adoption template.
No contradicting literature found — which is itself weak evidence: this is
an engineering-hygiene belief more than a tested hypothesis.
Falsifier: none expected; the risk is priority drift, not reversal.

**A4. A lazy typed-edge dependency queue can carry ripple propagation.**
Mark-stale lazily, validate-on-touch, instead of eager propagation.
Status: **PARTIAL.** The design avoids unbounded propagation (correct at
scale), but JNO (arXiv:2606.01610) shows propagation and preservation are
*coupled* pressures that want joint planning, and ChainEdit
(arXiv:2507.08427) shows baseline logical-generalization sits near 20%
when updates don't propagate. Our edge index today only knows explicit
`depends_on` relations; implicit logical coupling is undiscovered territory.
Falsifier: audit a batch of real ChangeSet applications; if stale-marking
misses logically-committed neighbors at a rate users would notice, lazy-only
is insufficient and CLaRE-style entanglement discovery becomes required,
not optional.

**A5. Debate stays off for routine claim adjudication at our model class.**
Unguided homogeneous debate buys sycophancy at 2–3× cost below ~32B.
Status: **SUPPORTED** (Cost of Consensus arXiv:2605.00914: conformity to
85.5%, consensus collapse to 32pp oracle gap; MAD ≈ majority vote per two
ICML/ACL studies). `debate_curation` being dormant was accidental wisdom.
Falsifier: GAVEL-shaped debate (evidence-bound subclaims + deterministic
citation validation) outperforming isolated self-correction *on our stack*
at acceptable cost — nobody has shown this at 30B scale yet.

## B. Retrieval strategy

**B1. Hybrid lexical+dense is necessary; dense-only is a liability.**
Status: **SUPPORTED** in principle (BEIR out-of-domain results, Anthropic
contextual-retrieval numbers where BM25 is load-bearing, τ-Knowledge board
where BM25 ≈ dense) — but **PARTIAL** in implementation: our lexical leg is
`ts_rank`, not BM25; the ParadeDB swap is specced, not built.
Falsifier: Doc Recall parity between `ts_rank`-fusion and dense-only on the
gold set would retire the whole hybrid workstream.

**B2. Search/get duality plus intent routing is the right substrate
interface.** Procedures-first for action intents, doc-search for information
intents, exact-fetch to close.
Status: **PARTIAL.** Supported by the routing harness logic and our own
bridge experience; challenged existentially by the Terminal result — grep
over raw docs beats every structured config in 5 of 6 model configurations
in the official paper. Unresolved at our scale: nobody has published
terminal-vs-structured at 30B-open-weight class.
Falsifier (and the single most informative missing experiment): run a
`terminal_kb` arm — shell over the 698 docs — against `stealthlab_procedures`
on gemma. If terminal wins here too, the pitch shifts from retrieval quality
to governance/audit/compliance, full stop.

**B3. Corpus preparation (contextual blurbs, chunk hygiene) is an unused
major lever.** Anthropic reports contextual embeddings+BM25 cut failures
35→49→67%; our procedure nodes still embed bare.
Status: **UNTESTED locally**, high prior from vendor data. Cheapest big
lever on the list; requires re-embed (Gemini chain makes this cheap now).
Falsifier: contextual-blurb re-embed producing <2pp Doc Recall delta on the
gold set.

## C. Scaffold and agent interface

**C1. The step tracker helps gemma without paying the reliability tax.**
PhaseK mean reward rose to 0.417 (from 0.27 baseline) after tracker +
enum footer + truncation.
Status: **PARTIAL — the core product claim rides on this.** Mean reward is
the wrong statistic per arXiv:2603.29231: memory scaffolds raised nothing
and often lowered long-horizon *reliability* via overhead tax. Our tracker
differs structurally (system-rendered, bounded, no agent-initiated writes),
which predicts immunity — but the predicting study measured trial-pair
consistency, and every multi-trial run we own is infra-contaminated
(phaseM/N/O died inside the daily-token wall).
Required check: trial-consistency comparison on clean sharded runs before
any scaffold-effectiveness claim is published.
Falsifier: trial-pair agreement lower than phaseG baseline despite higher
means → slim the render or drop progress lines.

**C2. Text-first tool presentation; never pair schema constraints with
tool calling on open-weight models.** The enum footer is incidentally the
safe pattern (Constraint Tax arXiv:2606.25605; NLT replication +14.9pp /
−93% critical errors; TSCG format-dominance).
Status: **SUPPORTED** with unusual consistency across five independent
studies. Also flags a future A/B: compiled-text tool descriptions vs JSON
specs for gemma.
Falsifier: a gemma-class model released with RL-tuned native tool calling
that reverses the NLT effect (already true for frontier models — watch for
it propagating down-scale).

**C3. Aggressive render truncation is net positive for this agent class.**
600-char step caps, top-5 detailed procedures.
Status: **SUPPORTED** (length-hurts-despite-perfect-retrieval, EMNLP 2025;
multi-hop ~2× fragility; premature-termination correlation) and
mechanistically consistent with the phaseI→J improvement. Open pricing
question: tracker lines' per-turn token cost has never been isolated.
Falsifier: turns-to-completion flat when progress lines removed.

## D. Evaluation methodology

**D1. Internal phase comparisons are valid; absolute board comparison is
not currently possible.** User sim held constant internally; ±9pp
simulator-choice variance and binary-reward orthogonality (Sim2Real,
Lost in Simulation) poison absolute claims; scaffold-conditional language
mandatory (GAIA scaffold study).
Status: **SUPPORTED.** Standing rule: publish deltas with confounds named;
never bare absolutes against taubench.com numbers.

**D2. Clean full-corpus numbers are achievable via key-sharded serving.**
Two GC keys = two independent 10M-token/day budgets; one tau2 instance each
at concurrency ≤2; `num_retries ≥ 4` so 429s die at call level.
Status: **UNTESTED** — arithmetic plus measured headers, zero operational
runs. Every full-corpus artifact on disk (phaseM/N/O: 0.14 / 0.03 / 0.02
with 60–78% timeout termination) is a budget-death artifact.
Falsifier is simply the first successful sharded run completing with
<5% infra terminations.

**D3. phaseK's 0.417 on dev-12 is our only clean number.** Treat
accordingly: useful for internal regression tracking, not for external
comparison (n=12, one trial, curated subset).

## E. Infrastructure and serving

**E1. Measured GC limits are stable platform facts.** 100 req/min,
1M tokens/min, 10M tokens/day per key; in-flight reservation mechanics
confirmed by error strings.
Status: **SUPPORTED by direct probe** (2026-08-23 headers). Unknown: the
daily-window reset time — a scheduling risk until observed once.

**E2. Google embedding chain (3-key rotation + cache) is solved.**
Burst 134 RPM sustained; warm-cache hits at 0.03ms; Voyage fallback proven.
Rotation pool currently holds keys from an exhausted shared project —
fresh-project keys would decouple the daily caps further.
Status: **SUPPORTED** operationally.

## F. Positioning and market

**F1. Enterprises need governed, auditable knowledge layers under agents;
retrieval failure (not model failure) is the diagnosed production pain.**
Status: **SUPPORTED directionally** (Gartner's retrieval-layer failure
diagnosis; vendor 70–80% vs enterprise-median 41.2% deflection gap;
66% adoption vs 91% exec pressure) — and newly **complicated**: the Exa
sweep found independent teams shipping the same thesis as products
(Quipu governed bitemporal KG store, Evidence-Gated-Memory, SuperLocalMemory
4.0, GapTime). Demand signal and competition signal in one result.
Falsifier: none of these competitors publishing customer-grade audit
results within two quarters would weaken the "governance is bought, not
built" thesis; their absence from τ-Knowledge-board-adjacent evals is
telling either way.

**F2. Small-model enablement is the wedge: scaffolds lift effective
ceilings for 30B-class agents, not just retrieval quality.**
AgentFloor says open-weight matches frontier through coordination tiers;
Gold-ceiling says reasoning binds at the frontier — our claim is that
structure substitutes for some reasoning at small scale.
Status: **PLAUSIBLE, UNTESTED.** The decisive experiment is cheap and
specified: gold-docs+scaffold vs gold-docs-bare on gemma over dev-12.
If scaffold lifts the gold ceiling itself, StealthLab is a small-model
enablement layer; if not, it is a retrieval improvement competing in the
smaller band.
Falsifier: gold-docs arms statistically indistinguishable.

---



## Load-bearing ranking (what hurts most if wrong)

1. **C1** — tracker effectiveness without reliability tax. The product
  claim; blocked only on one clean sharded run.
2. **F1/F2** — the commercial thesis and its wedge. F2 costs one
  afternoon of compute to test; do it before any investor-facing number.
3. **B2** — terminal-vs-substrate at small scale. Existential for the
  retrieval framing; same sharded run can carry the `terminal_kb` arm.
4. **A2** — write-depth thesis. Well supported externally; safe.
5. **A4** — lazy propagation sufficiency. Deferred risk until Phase III;
  audit-before-scale is the guard.



## Immediate cheapest falsifications (ordered by information per hour)

1. Gold-docs+scaffold vs gold-docs-bare on dev-12 (tests F2)
2. Trial-pair consistency from first clean sharded full-corpus run
  (tests C1, retires D2/D3)
3. `terminal_kb` arm on dev-12 (tests B2)
4. Contextual-blurb re-embed + Doc Recall delta (tests B3)
5. One-day wait to observe GC daily reset time (retires E1 residual)

