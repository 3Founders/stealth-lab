# Procedure lifecycle and versioning

Type: grilling
Status: resolved
Blocked by: 05

## Question

How do procedure lifecycle states, failure classification and versioning work?

spec.md forbids a simple boolean `verified`. It wants CANDIDATE → VERIFIED → STALE → REVALIDATED → VERIFIED, or RETIRED. Failures must be classified into six kinds — transient/contextual, precondition violation, scope violation, environment/dependency change, structural/procedural, and ambiguous — with explicitly different consequences, and a verified procedure must never be automatically rewritten after one failure. Versioning must never silently overwrite: retain the prior version, the reason for revision, the evidence that triggered it, the source execution, the validity interval and the migration relationship. A changed dependency marks a procedure STALE rather than deleting it.

The relevant existing facts:

- The method library's entire notion of verification is JSONB counters in `success_criteria`: `attempts`, `successes`, `mean_steps`, `times_reused`, `internal_proxy`. `_bump_reuse_count` is a counter, not a reward signal — its own source says so.
- The write path is gated on `subgoals_done > 0 and subgoals_failed == 0`, which is a boolean success test with no failure classification at all.
- `ResearchHTNAgent` item 5 — the Beta-Bernoulli bandit that would turn the reuse counter into a real reliability signal — raises `NotImplementedError`.
- There *is* strong existing precedent for non-destructive versioning: `knowledge_update.py` closes the validity window, appends a new row, and writes a `SUPERSEDES` edge, all in one transaction with `SELECT ... FOR UPDATE`. It never deletes.
- `claims.py` establishes the useful orthogonality that lifecycle can reuse: `t_invalid` (deleted / no longer valid in world time) and `truth_state` (`IN`/`OUT`, no longer believed) are independent axes.
- The `agent_review_state_machine.py` is a working precedent for an explicit transition table with row-locked transitions and an immutable append-only event log.

Decide:

- Does the lifecycle ride the existing bi-temporal + `SUPERSEDES` machinery, or does a procedure need its own version chain and status column?
- Is lifecycle status a column, or is it derived from evidence? Deriving it is more honest (spec.md: justification is canonical, confidence derived) but makes every read a computation.
- What is the transition table, and what triggers each transition? Specifically: what *promotes* CANDIDATE to VERIFIED — a count of successes, a count of distinct contexts, an explicit human action?
- How is a failure classified, and by what? Six categories is a rich taxonomy for something with no classifier. Which are determinable deterministically (precondition violation is checkable; environment change is detectable from state deltas) and which need judgement?

Grill these:

- **What marks a procedure STALE when a dependency changes?** That requires knowing the procedure's dependencies — which is the claim/procedure dependency graph spec.md's TMS-preparation section describes. Is that graph in scope for milestone 1, or does STALE start as a manual/explicit state with the automatic path deferred?
- Ambiguous failure must "not automatically mutate durable memory." What happens to it instead — recorded and ignored, queued for human review, or counted toward a threshold? An unhandled sixth category is where this design quietly leaks.
- A verified procedure that fails repeatedly but never in a classifiable way: what is the escape hatch that stops it being reused forever?
- `agent_review_state_machine.py` is a working precedent in this repo. Should procedure lifecycle reuse that pattern outright, or is procedure state fundamentally different from review state because it is evidence-driven rather than actor-driven?

## Research findings (Brief 3 — [answers3.md](../research/answers3.md))

Not an answer; evidence for whoever resolves this. Borrowed from sequential testing, flaky-test
classification, build-cache invalidation, resilience engineering and speedup learning — **nothing
measured for LLM-coding-agent procedures**.

**13.3 — the verification threshold is much higher than intuition suggests.** Concrete numbers:

- 3 successes / 0 failures gives a Beta(4,1) posterior with a **95% credible interval of
  [0.48, 0.99]** — far too wide to call anything verified.
- The rule of three: with 0 failures in *n* trials the 95% upper bound on failure rate is `3/n`,
  so 3/3 bounds the failure rate at **100%** — i.e. tells you nothing.
- **≥10 successes with 0 failures** gives Beta(11,1) → [0.74, 0.99], which is a defensible floor.
- Raw count is insufficient regardless: require successes across **≥3 distinct contexts**
  (different files, environments, dependency sets), which formalises "verified across distinct
  conditions" rather than "verified ten times identically."
- For deciding as evidence arrives rather than at fixed *n*: **SPRT with α=0.05, β=0.10**.

**13.4 — six failure categories is too many to classify automatically.** Automated failure
classification reaches only **~F1 65%** even on the simpler flaky-test task. Recommended
coarsening, with what is actually automatable:

| Category | Automatable? | Signal |
|---|---|---|
| Transient | **yes** | retry 2–3×; passes on retry |
| Precondition violation | **yes** | re-check preconditions; fails |
| Scope / environment change | **partly** | dependency diffs, version changes, scope-relevant file changes |
| Structural defect / ambiguous | **no** | queue for human review |

Collapsing the spec's six into these four is the honest resolution, and it directly serves ticket
12: scope narrowing may not proceed until a failure is classified, so a classifier that is
confidently wrong is worse than one with a coarse residual bucket.

**13.5 — staleness detection, with accepted over-invalidation rates.** Granularity tradeoff from
build-cache and test-impact-analysis practice: file-level ~85% precision, symbol-level ~95% (with
type resolution), package-version cheapest but coarsest. All of them miss invisible dependencies
(reflection, dynamic dispatch, external services). The load-bearing finding is that **conservative
over-invalidation is established practice, at accepted rates of 10–30%** — so deliberately
marking too much stale is normal engineering, not a design failure. Recommended split:
package-version tracking for most procedures, file-level for high-stakes ones.

**13.7 — escape hatches have concrete published parameters.** Both named patterns transfer:

- **Circuit breaker**: open after ~5 failures, half-open probe after ~60s, close after ~5
  consecutive successes. The half-open probe is what prevents permanently killing something only
  temporarily broken.
- **Flaky-test quarantine**: quarantine at ≥50% failure rate over 7 days; disable after 14 days in
  quarantine.

Both give the ambiguous residual a forced exit, which is the pattern this ticket needs — an
unhandled residual bucket otherwise silently accumulates every hard case.

**13.2 — orthogonal axes confirmed over a single enum.** The 2026 skill-library survey
(*Dynamic Agent Skills*, arXiv 2607.10113) does propose `candidate/verified/stale/revalidated/
retired` as a stored enum — so the taxonomy this ticket inherited is real and independently
arrived at. But the findings side with the orthogonal-axes reading: collapsing multi-concern
status into one enum is a named antipattern ("status enum antipattern" / state explosion), and
independent axes (validity, belief, approval, runnability) allow the concerns to evolve
separately. This repo already does exactly that twice — `claims.py`'s `t_invalid` vs.
`truth_state`, and the Agent Store's `review_state` vs. `runnable`.

**13.x — the utility problem gives this ticket a retirement criterion it didn't have.** Minton's
formula is directly usable as a retention rule:

```
utility(P) = (application_frequency × average_savings) − match_cost
```

Procedures with **negative utility get deleted**, regardless of how well-verified they are — a
procedure can be entirely correct and still be worth removing because matching it costs more than
it saves. That is a retirement trigger orthogonal to every failure-driven one this ticket
currently contemplates, and it requires `verification_stats` (ticket 05) to also record
match cost and realised savings, not just attempts and successes.

## Answer

**Status is three orthogonal axes, not one enum.** The 2026 skill-library survey does propose
`candidate → verified → stale → revalidated → retired` as a single stored enum, and that taxonomy
independently matches spec.md — but a flat enum collapses concerns that are genuinely independent
here:

- `verification_state` — **evidence-driven**: `candidate | verified | retired`
- `staleness` — **dependency-driven**: `fresh | stale | revalidating`
- `availability` — **circuit-breaker-driven**: `active | quarantined | disabled`

A procedure can be simultaneously verified, stale, and quarantined. An enum cannot express that,
and worse, it makes a dependency-driven staleness event *overwrite* hard-won evidence-driven
verification state — so revalidation would have to reconstruct what was already known. Collapsing
multi-concern status into one value is a named antipattern ("status enum antipattern" / state
explosion), and this repo already keeps concerns orthogonal in two places: `claims.py`'s `t_invalid`
(no longer valid) versus `truth_state` (no longer believed), and the Agent Store's `review_state`
versus its separately-tracked `runnable`. The cost is that "is this usable" becomes a predicate
over three fields rather than one comparison; accepted.

**Promotion to verified needs far more evidence than intuition suggests — but the bar gates
automatic reuse, not all reuse.** The numbers are unambiguous. Three successes and zero failures
gives a Beta(4,1) posterior with a **95% credible interval of [0.48, 0.99]**; by the rule of three,
zero failures in three trials bounds the failure rate at **100%** — the claim is unsupported. Ten
successes with zero failures gives Beta(11,1) → [0.74, 0.99], which is defensible.

So: **≥10 successes, 0 failures, across ≥3 distinct contexts** (different files, environments,
dependency sets) for `verified`, with **SPRT (α=0.05, β=0.10)** as the mechanism for deciding as
evidence arrives rather than at a fixed sample size. Context diversity is required separately from
count, because ten identical repetitions evidence far less than ten varied ones.

The obvious objection is that this makes verification nearly unreachable early, compounding the
cold-start problem ticket 12 already has. The resolution is to be precise about what the bar
gates: **`verified` gates automatic retrieval; a `candidate` procedure remains explicitly
invocable.** That preserves the statistics without making the library inert — and it is consistent
with ticket 12's decision to disable *automatic* reuse during cold start anyway.

**Failure classification collapses to four automated categories, with the finer distinction kept
as evidence.** Automated failure classification tops out near **F1 65%** even on the simpler
flaky-test task, so spec.md's six categories cannot be assigned confidently by machine — and
confident misclassification is actively harmful, because ticket 12's scope narrowing consumes this
output and a wrong classification produces a wrong specialisation.

| Category | Automated? | Signal |
|---|---|---|
| Transient | yes | retry 2–3×, passes on retry |
| Precondition violation | yes | re-check preconditions, fails |
| Scope / environment change | partly | dependency diffs, version changes |
| Structural / ambiguous | no | residual → human review |

The finer six-way distinction is **retained as recorded evidence** on the failure record rather
than discarded, so a human or a better classifier later loses nothing. This is the honest position:
classify coarsely and reliably, record richly.

**Staleness uses package-version granularity, accepting 20–30% over-invalidation.** Finer tracking
is more precise (file-level ~85%, symbol-level ~95% with type resolution) and more expensive, and
*all* granularities miss invisible dependencies — reflection, dynamic dispatch, external services.
Conservative over-invalidation at 10–30% is established practice in build-cache and test-impact
systems, not a design failure.

The decisive argument is asymmetry: **over-invalidation costs revalidation work; under-invalidation
means a stale procedure is reused as though verified.** Those costs are not comparable, so bias
conservative. File-level tracking for high-stakes procedures is available later without redesign.

**The ambiguous residual gets a forced exit; both escape-hatch patterns are adopted.** An unhandled
residual bucket is where every hard case silently accumulates, so:

- **Circuit breaker** — open after 5 failures, half-open probe after 60s, close after 5 consecutive
  successes. The half-open probe is precisely what prevents permanently killing something that was
  only temporarily broken.
- **Quarantine** — at ≥50% failure rate over 7 days; disable after 14 days in quarantine.

These constants are borrowed from resilience engineering and flaky-test practice and are
**unvalidated in this domain**, so they are configuration, not literals in source.

**Utility-based retirement — a retirement trigger orthogonal to failure.** Minton's result gives
this ticket a criterion it did not previously have:

```
utility(P) = (application_frequency × average_savings) − match_cost
```

A procedure with negative utility is deleted **regardless of how well-verified it is** — it can be
entirely correct and still be net-negative, because matching it costs more than it saves. Nothing
in the failure-driven machinery above would ever catch that.

**This amends ticket 05**: `verification_stats` must record **match cost and realised savings**,
not just attempts, successes, mean steps and times-reused. Same amendment pattern ticket 10 used
on ticket 03 — extending a field list the resolved ticket defined, not reopening its decision.

**Provenance of this answer.** Literature-grounded: the Beta-Bernoulli and rule-of-three figures,
SPRT, the F1 ~65% classification ceiling, the 10–30% over-invalidation norm, circuit-breaker and
quarantine parameters, and Minton's utility formula. Judgement calls: the three-axis decomposition
(reasoned from this repo's own precedents), and gating automatic-only reuse on `verified`.
Explicitly absent from the literature and flagged by the research: **no empirical breakdown exists
of failure causes in production skill systems**, and **no LLM-agent work has confronted the utility
problem at all** — including the 2026 survey. Being early to it is a real position, and it means
the utility criterion must be instrumented and observed rather than assumed to bite.
