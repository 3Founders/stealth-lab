# External literature review #2: the seven remaining open questions

Companion to [external-literature-review.md](external-literature-review.md). That one covered
the broad landscape (agent memory, TMS, provenance, procedural memory, retrieval, bitemporal
modeling) and largely *corroborated* the map's existing direction. This one targets the
specific unresolved questions in the 7 open tickets — and unlike #1, it **changes two
in-flight recommendations**.

**Provenance caveat**: gathered via a Perplexity literature search, relayed here. Citations
below are as reported by that search and have **not been independently fetched or verified by
this session** — treat specific arXiv IDs and reported numbers as leads to check, not as
established facts, before any of them load-bear on an implementation choice. The *reasoning*
they support is what's recorded; the numbers are marked where they came from.

## The most important finding: five of seven questions have no directly applicable literature

Explicitly reported as unsupported by prior art for LLM coding agents specifically:

1. **No empirical measurement** of what fraction of useful agent-memory signal is
   deterministically extractable (ticket 04).
2. **No "false reuse rate" metric** exists for procedure retrieval in agent systems — despite
   spec.md listing it as a target metric (ticket 12).
3. **No empirical breakdown** of failure causes (procedure-wrong vs. environment-changed vs.
   transient) in production skill systems (ticket 13).
4. **No code-specific reranker** latency/accuracy study (ticket 14).
5. **No empirical comparison** of retrieval-then-instantiate vs. generative planning for
   coding agents (ticket 15).

This is genuinely useful rather than disappointing: it means these five are **engineering
judgement calls informed by analogous domains**, not decisions with a right answer we could
have looked up. Worth saying so plainly in the affected tickets rather than implying more
grounding than exists.

## 1. State as temporal projection (ticket 10) — REFINES the pending recommendation

Sources: XTDB's bitemporal-index writeups (production, from the bitemporal-DB authors),
CQRS/event-sourcing snapshotting guidance, Azure's Event Sourcing pattern docs, and a reported
bitemporal-operator-algebra preprint.

**The refinement**: computing state by query at read time is fine **for as-of-now queries**,
which short-circuit early and dominate in practice. It degrades badly for (a) long event
streams, (b) full-history materialization, and (c) complex temporal joins without specialized
indexes. Reported practitioner heuristic: **materialize when replay exceeds ~100ms**, or every
N events.

**What this means for ticket 10's answer**: the "no state table, always compute" recommendation
holds as the *default*, but needs an explicit escape hatch rather than being absolute —
otherwise the design has a known failure mode with no documented response. Concretely:
- Pure claim-graph projection for as-of-now queries (the common case: `S_current` for ticket
  12's applicability check).
- A materialization threshold for back-in-time/full-history queries, rather than pretending
  they're the same cost.
- Index on `(subject, t_valid, t_invalid)` — reported as the shape that makes as-of selectivity
  drive performance. Note this is a *different* index from the `properties->>'subject'`
  expression index ticket 03 already specified; both may be needed.
- Monitorables worth naming now: p95 projection latency, rebuild-time window, projector
  idempotency.

## 2. Deterministic vs. LLM extraction (ticket 04)

Sources: process-mining literature — *Foundations of Process Event Data* (Springer textbook,
canonical), case-ID detection papers (Springer LNCS 2024), interaction-log case-identifier
work.

**The transferable frame**: process mining's canonical deterministic triad is **case ID +
activity label + timestamp**. Everything outside that triad is a candidate for model-based
extraction. Mapped onto this repo: file edits, test runs and outcomes, commands executed,
commits made are the deterministic core (all already present in `trace_events` per ticket 06);
semantic labels (intent, task type, dependency rationale) are where a model earns its place.

**On extraction versioning** (ticket 04's explicit sub-question): version by **code hash +
model ID**, not prompt text alone — a compound key `(extraction_code_version, model_id,
prompt_hash)`. This matches what ticket 05 already decided for `parameter_schema`
(`extraction_method` + `extractor_version`), so the two stay consistent.

**Still a judgement call**: the deterministic/LLM split percentage is unmeasured. Nobody knows
how much value the LLM layer adds — which is an argument for making it optional and measurable
rather than assumed, same conclusion ticket 05 reached for its two extractors.

## 3. Episode segmentation (ticket 11) — gives the prototype an evaluation method

Sources: dialogue topic segmentation (granularity-aware evaluation preprint; Interspeech 2023
unsupervised segmentation), process-mining case segmentation (ANNALS of CSIS 2024,
ScienceDirect 2026).

**Two directly usable findings for the prototype**:
1. **Composition rule**: use deterministic boundaries as *hard* segment edges (prompt
   delimiters, commit boundaries, test-run completion, subagent nesting), and apply semantic
   segmentation only *within* those windows. This is exactly the "deterministic now, semantic
   later" seam spec.md asks for, and it resolves ticket 11's "how do rules compose when they
   conflict" — they don't compete; deterministic wins and semantic subdivides.
2. **Evaluation without ground truth**: use **purity / coverage / windowed-F1**, plus
   boundary-density sweeps — *not* strict boundary F1, which is meaningless without gold
   boundaries. Standard metrics where labels do exist: Pk, WindowDiff. This gives ticket 11's
   prototype a real yardstick instead of pure eyeballing.

## 4. Preconditions and the unknown-precondition problem (ticket 12) — ANSWERS the sharpest question

Sources: conformant/conditional planning literature (three-valued belief states), STRIPS
precondition representation (canonical planning material), open-world-assumption lecture
material on unknown fluents.

**The answer ticket 12 was missing**: classical planning is unambiguous — an action requires
its preconditions to be **must-be-true**; an *unknown* precondition blocks applicability. So
the principled default is **fail closed**, with three-valued belief states as the explicit
mechanism if you want to reason under uncertainty rather than just refuse.

This matters because ticket 12 frames fail-closed as "makes the system useless early, when
nothing has preconditions." The literature's response is effectively: that's the correct
behavior, and the fix is not fail-open (which silently becomes similarity-only, the thing
spec.md forbids) — it's either modelling uncertainty explicitly (three-valued) or falling back
to *conditional* planning, not to blind similarity.

**Representation**: structured predicates over state (STRIPS-style conjunction of literals),
**not** string tags — which is a direct verdict against extending `precondition_gate.py`'s
current Jaccard-tag approach as the long-term design.

## 5. Procedure lifecycle (ticket 13) — near-exact prior art exists

Sources: a reported 2026 survey, *Dynamic Agent Skills: A Lifecycle Survey and Taxonomy of
Evolving Skill Libraries*; industry skill-lifecycle writeups (Axiom Studio, SuperML, iflytek's
SkillHub).

**Direct validation**: the survey reportedly proposes **candidate → verified → stale →
revalidated → retired** — the same lifecycle spec.md specifies, independently arrived at. Worth
fetching properly before ticket 13 is worked; it's the closest thing to a reference model.

**The structurally useful finding** (SkillHub): decompose skill status into **orthogonal
dimensions** — container lifecycle, version lifecycle, review workflow, visibility — rather
than one status column. This maps onto something this repo already does well: `claims.py`'s
`t_invalid` (no longer valid) vs. `truth_state` (no longer believed) orthogonality, and the
Agent Store's `review_state` vs. separately-tracked `runnable` boolean. Ticket 13's "is
lifecycle a column or derived from evidence" may be a false dichotomy — it's likely several
independent axes.

**Failure classification and staleness**: no empirical grounding. Reported practice is
heuristic — classify by error-pattern consistency (consistent → procedure wrong; dependency/API
breakage → environment; retryable → transient), and trigger revalidation on dependency-version
change. Confirms ticket 13's own suspicion that the automatic-STALE path needs a real
dependency graph, and that starting with explicit/manual STALE is defensible.

## 6. Retrieval tiers and reranking (ticket 14)

Sources: retriever-reranker pairing study (Springer 2026), Voyage AI's reranker writeup
(industry, with numbers), multivector-reranking paper (ECIR 2026), reranker-FLOPs paper
(EMNLP 2025 industry track).

**Reranker verdict, two parts**:
1. If reranking at all, use a **dedicated reranker, not LLM-as-reranker** — reported figures
   (Voyage AI, vendor-published so treat with appropriate skepticism): ~60x cheaper, ~48x
   faster, ~15% better NDCG@10.
2. **Strong first-stage retrieval reduces reranker benefit** (ECIR 2026). Since this repo's
   first stage is already RRF-fused hybrid + graph expansion, that argues for **deferring
   reranking out of milestone 1** — which matches ticket 14's own instinct about per-query hot-
   path cost.

**Budget enforcement** (ticket 14's "what enforces the budget" question): allocate per-query
**FLOPs**, not just token count; the reported RPP/QPP metrics exist for comparing tiers on that
basis. Practical note for code specifically: structural (AST/symbol graph) and temporal
(recency) tiers dominate — consistent with spec.md ranking structural locality first.

## 7. Planner as fallback (ticket 15) — supports the inversion

Sources: STRIPS action representation (canonical), execution-monitoring/replanning lecture
material, case-based planning (theoretical only).

**Supports ticket 15's proposed inversion**: if a procedure's preconditions are fully satisfied
and its effects are deterministic, the planner LLM has nothing to do — **skip decomposition
entirely**. The planner becomes the fallback for (a) novel situations with no matching
procedure, and (b) situations where the belief state is incomplete (which connects directly to
ticket 12's fail-closed decision: an unknown precondition is exactly when you *do* need the
planner). Execution monitoring adds the third trigger: replan when preconditions fail *at
execution time*, not just at selection time.

**Unvalidated for LLM agents**: no empirical comparison of retrieval-then-instantiate vs.
generative planning for coding tasks. Case-based planning supports retrieval-then-adapt in
principle, but nobody has measured planner skip rates in this setting.

## Net effect on the map

- **Ticket 10** (in flight): recommendation refined — keep projection-by-query as the default,
  add an explicit materialization threshold and index shape rather than treating on-demand
  computation as unconditionally free.
- **Ticket 12**: its sharpest open question (fail-open vs. fail-closed) now has a principled
  answer — fail closed, with three-valued belief state as the escape hatch, and structured
  predicates rather than string tags.
- **Ticket 13**: a near-matching lifecycle taxonomy reportedly exists and should be fetched;
  and "one status column vs. derived" is likely a false dichotomy — use orthogonal axes, as
  `claims.py` and the Agent Store already do.
- **Ticket 14**: defer reranking out of milestone 1, on the grounds that strong first-stage
  retrieval already exists here.
- **Ticket 15**: the planner-as-fallback inversion is supported in principle, unmeasured in
  practice.
- **Tickets 04, 11**: process mining supplies both the deterministic-core frame and (for 11) a
  real evaluation methodology — purity/coverage/W-F1, not strict boundary F1.
