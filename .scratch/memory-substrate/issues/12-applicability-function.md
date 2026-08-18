# Applicability function

Type: grilling
Status: resolved
Blocked by: 05, 10

## Question

How is `applicability(P, S_current)` represented and evaluated?

spec.md is emphatic that reuse must **not** be based solely on semantic similarity. Applicability is a combination of: explicit preconditions, current state, scope, exclusions, temporal validity, environment compatibility, procedure verification status, semantic similarity, and relevant local graph neighbourhood. "Embeddings find analogues. The graph and state determine relevance."

The relevant existing facts:

- Today's entire precondition machinery is `backend/app/services/precondition_gate.py`: preconditions and postconditions are **short lowercase string tags** in JSONB, compared by Jaccard overlap against a 0.25 threshold. Not logical predicates, not evaluated against any world state.
- The gate **passes trivially when either side is empty**. Combined with the next fact, this means it is almost always a no-op.
- **Nothing upstream produces postconditions automatically.** `decomposition.py` says so in-source: there is no LLM-extraction step, so the tags only work when a caller hand-supplies them. The only real producer in the repo is `call_graph.py`-derived `touches:<file>` tags in `symbolic_htn_agent.py`.
- The HTN agent has real precondition/postcondition hooks, but the shipped implementations are: precondition = **advisory only, never fails** (it just writes a `path_hint`), postcondition = a hard gate that runs tree-sitter and refuses completion on syntax errors. Both are useful; neither is a logical predicate over state.
- `reuse_detection.py` uses raw cosine with thresholds (0.90 full, 0.70 partial) deliberately, because reuse needs a number with a threshold rather than a fusion rank.
- `TypedPreconditionHTNAgent` exists as an opt-in strict call-graph gate, and `htn_agent.py` explicitly disclaims having any SMT/Z3 machinery.

Decide:

- What is a precondition, concretely? A string tag, a structured predicate over state fields, executable code, or an LLM judgement? Each has a different cost, a different failure mode, and a different producer problem.
- How do the nine factors **combine**? A weighted score, a hard filter chain, or a filter chain with a scored tail? spec.md's ordering implies hard constraints first and similarity last, but does not say what happens when a hard constraint is simply unknown.
- What are `scope` and `exclusions` in representation terms, and who narrows them? spec.md's failure classification says a scope violation should narrow scope or exclusions — so they must be machine-writable, not just human-authored.

Grill these:

- **The producer problem is the real problem.** The repo already has precondition machinery that is inert because nothing generates the tags. What stops this design from repeating that exactly? Name the producer for every field of the applicability check before designing the check.
- Trivially-passing gates are worse than no gates, because they look like safety. Should an unknown precondition *fail closed* (procedure not applicable) or *fail open* (fall back to similarity)? Fail-closed makes the system useless early, when nothing has preconditions; fail-open makes it silently similarity-only, which is what spec.md forbids.
- What is the honest minimum for milestone 1 — and is a measurable "false reuse rate" (spec.md lists it as a metric) more valuable than a sophisticated applicability model that nobody can validate?
- What does "relevant local graph neighbourhood" mean as an actual query, and is it distinguishable from ticket 14's locality work, or is it the same thing?

## Research findings (Brief 3 — [answers3.md](../research/answers3.md))

Not an answer; evidence for whoever resolves this. Borrowed from CBR, STRIPS/PDDL, MCDA,
conformant planning, version-space learning and transfer learning — **nothing measured for
LLM-coding-agent procedures**.

**12.1 — preconditions are a hybrid, and the current approach is misclassified.** The CBR
literature draws exactly the distinction this ticket needs: **similarity is not applicability**.
Similarity is a cheap a-priori approximation of reusability and is "often wrong"; the
"similar problems have similar solutions" assumption breaks when surface similarity masks deep
structural difference. `precondition_gate.py`'s Jaccard-over-tags is therefore a *similarity*
measure being used as an *applicability* test — a category error, not just an imprecise one.

Recommended layering: tags for **coarse filtering only**; structured predicates (STRIPS/PDDL
style) for **hard constraints**; executable checks for environment validation (cache results —
running them at retrieval time is a real cost); LLM judgement for **soft ranking only, never a
hard constraint**. The number that settles that last point: **LLM-judged conditions reach ~F1 65%
even on simpler classification tasks**, and are prompt-sensitive and non-deterministic.

Known costs of the predicate route, so it goes in with eyes open: authoring cost, and brittleness
to state-schema drift (predicate arity/type changes break stored procedures) — so predicates need
versioning alongside procedure versioning.

**12.2 — the pathology has a name.** High similarity compensating for a violated hard constraint
is **criterion compensation** (a.k.a. score compensation) in multi-criteria decision analysis.
The established guard is **non-compensatory MCDA** — lexicographic ordering or conjunctive models
— where hard constraints simply cannot be outscored. Weighted linear scoring across mixed
hard/soft criteria is the named antipattern; a learned ranker is out (no labelled data, and it
would overfit spurious correlates like procedure ID or timestamp).

So: **strict filter cascade on hard constraints** (preconditions, scope, exclusions, environment,
verification status), similarity ranks only the survivors.

> **Cross-ticket tension, resolved — read before ticket 14.** Brief 4 concludes the *opposite*
> for retrieval: strict cascade is an **antipattern** there, because a weak early tier
> permanently discards results no later tier can recover. Both are correct, because they govern
> different things. **Hard constraints filter (non-compensatory); soft relevance signals fuse
> (union + weighted/RRF).** A violated precondition is not a weak signal to be outweighed — it is
> a disqualification. A low structural-locality score *is* a weak signal. Ticket 12 owns the
> first, ticket 14 owns the second, and they must not be collapsed into one mechanism.

**12.5 — unknown preconditions, with a real cold-start answer.** Fail-closed is principled but
impractical at cold start; fail-open silently degrades to similarity-only, which is the failure
this whole design exists to avoid. Recommended middle: **three-valued (true/false/unknown) with
explicit belief-state tracking**, avoiding SQL-style NULL semantics.

The cold-start strategy is the actionable part, borrowed from conformant planning: **disable
procedure retrieval entirely while evidence is thin and fall back to generative planning**, then
enable retrieval once enough preconditions are actually recorded. Note this is *phase-dependent*
and couples directly to ticket 15 — the planner is the **default** early and becomes the
**fallback** later, rather than being one or the other permanently.

Also note the interaction with ticket 10's closed-world decision: under CWA absence is
determinate, so "no claim found" and "precondition unsatisfied" coincide. Adopting three-valued
logic here would partially reopen that — worth deciding deliberately rather than by drift.

**12.3 — version-space learning confirmed, with all three dangers documented.** Mitchell's
version-space learning (general/specific boundaries, narrow from negative examples) and ILP
theory revision are the right frames. Mitigations for the three dangers this ticket names:

- *Overfitting to one failure*: require **≥3 failures in similar contexts** before narrowing; use
  minimum description length to penalise over-specific scopes.
- *Oscillation*: **hysteresis** — different thresholds each way (e.g. narrow at 50% failure rate,
  widen at 20% success rate), plus an EMA of failure rates rather than instantaneous values.
- *Ordering dependency*: **confirmed real** — misclassified failures produce wrong
  specialisations. Failure cause must be classified before scope is narrowed, which makes ticket
  13's classifier a hard prerequisite for this ticket's automation, not a nice-to-have.

Also worth adopting: CBR stores adaptation-failure conditions as **separate failure cases**, so a
procedure isn't reapplied in a context already known to break it.

**12.6 — "false reuse rate" is negative transfer, and measuring it needs a control arm.** The
standard framing is **negative transfer**, with a formal condition: transfer is negative when
`RPT(A(S,T)) > RPT(A(∅,T))` — performance with the source worse than without it. The consequence
is a real experimental-design constraint: **measurement requires a matched no-transfer baseline**
(solve-from-scratch control); single-arm proxies are insufficient. Anything claiming to measure
false reuse without that control is measuring something else.

## Answer

**A precondition is a structured predicate over the claim graph — and today's tag matching is a
category error, not merely an imprecise one.** The CBR literature draws exactly the distinction
this ticket needs: *similarity* is a cheap a-priori approximation of reusability and is often
wrong; *applicability* is a different question. `precondition_gate.py`'s Jaccard-over-tags is a
similarity measure being used as an applicability test, so extending it is not the path.

Layering, scoped by what actually has a **producer** (this ticket's own 12.4 constraint, which
kills most otherwise-attractive designs):

- **Hard constraints — structured predicates over the claim graph.** The producer exists because
  of ticket 10: a procedure's preconditions are derived deterministically from what its source
  episode's `state_before` projection actually contained. Nothing has to be hand-authored for a
  procedure to have real preconditions.
- **Coarse filtering — existing tags.** Retained, because the data exists and cheap filtering
  before predicate evaluation is worth having. Never load-bearing on its own.
- **Soft ranking — an LLM, and only ranking.** LLM-judged conditions reach roughly **F1 65%** on
  simpler classification tasks, are prompt-sensitive and non-deterministic. That is adequate for
  ordering candidates and disqualifying for a hard gate.
- Executable checks are the natural fourth layer for environment validation, but deferred: they
  need sandboxing and result caching, and nothing in milestone 1 forces them.

Accepted cost, stated so it is not a surprise later: predicates are brittle to state-schema drift
(arity or type changes break stored procedures), so **the predicate schema is versioned alongside
the procedure**, using ticket 05's existing versioning rather than a new mechanism.

**The nine factors combine as a non-compensatory filter cascade, with similarity ranking only the
survivors.** The pathology this ticket set out to prevent has a name: **criterion compensation**
in multi-criteria decision analysis — a high score on one criterion outweighing a violated hard
constraint. The established guard is non-compensatory MCDA (lexicographic or conjunctive), where
hard constraints simply cannot be outscored. Weighted linear scoring across mixed hard and soft
criteria is the named antipattern; a learned ranker is out on separate grounds (no labelled data,
and it would overfit spurious correlates like procedure id or timestamp).

Hard, filtering: preconditions, current state, scope, exclusions, temporal validity, environment
compatibility, verification status. Soft, ranking survivors: semantic similarity, local graph
neighbourhood.

> **This is deliberately the opposite of ticket 14's conclusion, and both are correct.** Ticket 14
> finds strict cascades an *antipattern* for retrieval, because a weak early tier permanently
> discards results nothing downstream recovers. The difference is what the signals are: a violated
> precondition is a **disqualification**, a low locality score is a **weak signal**. Hard
> constraints filter; soft signals fuse. These two mechanisms must not be unified — doing so
> either reintroduces criterion compensation here or early-recall-loss there.

**Unknown preconditions fail closed — and cold start is solved by disabling reuse, not by
weakening the gate.** Fail-open is rejected outright: it silently degrades into similarity-only
selection, the exact failure this design exists to prevent. Brief 3 recommends three-valued logic
(true/false/unknown) with explicit belief-state tracking; **that recommendation is rejected here**,
because ticket 10 adopted the closed-world assumption, under which absence is determinate — "no
claim found" and "precondition unsatisfied" are the same answer, so fail-closed comes for free and
three-valued query semantics would partially reopen a settled decision for no gain milestone 1 can
use.

That leaves the real objection: fail-closed makes nothing applicable early, when little is
recorded. The answer, borrowed from conformant planning, is not to loosen the gate but to
**disable procedure retrieval entirely while evidence is thin and fall back to generative
planning**, enabling retrieval once preconditions are actually recorded. This is phase-dependent
and couples directly to ticket 15: the planner is the **default** early and becomes the
**fallback** later, rather than being permanently one or the other. Ticket 10's escape hatch — an
explicit `unknown` fact type, never a NULL — remains available if fail-closed proves too strict in
practice.

**Scope and exclusions: record now, automate narrowing later.** Version-space learning (Mitchell)
and ILP theory revision are confirmed as the right frame for machine-writable narrowing, and all
three dangers this ticket anticipated are documented with mitigations: require **≥3 failures in
similar contexts** before narrowing (against overfitting to one failure), plus MDL to penalise
over-specific scopes; **hysteresis** with different thresholds each way plus an EMA of failure
rates (against oscillation); and classify the failure cause *before* narrowing (the ordering
dependency — confirmed real, since misclassified failures produce wrong specialisations).

That last point makes ticket 13's failure classifier a **hard prerequisite** for automated
narrowing, not a nice-to-have. So milestone 1 **records the evidence** — adaptation-failure
conditions stored as separate failure cases, the CBR pattern, so a procedure is not reapplied in a
context already known to break it — and **defers the automation** to fog. Recording is cheap and
non-lossy; automating on top of a classifier that tops out near F1 65% is not.

**Measuring bad reuse: the standard framing is negative transfer, and it needs a control arm.**
"False reuse rate" is not a standard term; the measured phenomenon is **negative transfer**, with
a formal condition — transfer is negative when performance *with* the source is worse than
without it. The consequence is an experimental-design constraint, not a coding one: **measurement
requires a matched no-transfer baseline**; single-arm proxies measure something else. Milestone 1
instruments for it (record that a procedure was used, and the outcome); the control arm is an
obligation on the A/B harness spec.md already contemplates, and this ticket does not pretend
otherwise.

**Provenance of this answer.** Literature-grounded: the similarity/applicability distinction (CBR),
criterion compensation and non-compensatory MCDA, version-space learning and its three documented
dangers, negative transfer's formal condition, the LLM-judgement F1 figure. Judgement calls:
deriving preconditions from ticket 10's `state_before` projection (this repo's own affordance, not
from any source), and rejecting three-valued logic to preserve ticket 10. Flagged absent by the
research itself: **nothing here is measured for LLM-coding-agent procedures** — every finding is
transferred from CBR, planning, MCDA or transfer learning.
