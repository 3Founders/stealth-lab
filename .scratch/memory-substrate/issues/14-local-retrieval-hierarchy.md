# Local retrieval hierarchy

Type: grilling
Status: resolved
Blocked by: 05, 10

## Question

What is the local-first retrieval design, and how does it sit on top of the retrieval machinery that already exists?

spec.md wants a retrieval hierarchy of structural locality → temporal locality → causal/graph locality → semantic retrieval → reranking. The current task should identify a local epistemic neighbourhood (repository, branch, current files, symbols, dependencies, recent commits, recent failures, related tests, recent procedure executions, relevant claims, artifacts). It explicitly forbids dumping global memory into the model, and requires that as memory grows the relevant model context stays approximately local.

The relevant existing facts — this is the best-developed area of the codebase:

- `HybridRetriever` (`retrieval.py`): pgvector cosine + Postgres FTS, fused by RRF with `RRF_K=60`, then a bounded one-hop graph expansion via `GraphStore.traverse_from`. `PARENT_OF` edges are skipped during expansion; hierarchy-group nodes are excluded from both legs. Vector failure degrades to lexical-only, loudly.
- `hierarchy.py`: beam descent over an HTN tree with internal nodes routing on the mean of children's embeddings, confidence-adaptive beam widening, and a `used_flat_fallback` signal returned to the caller rather than a silent fallback.
- `call_graph.py`: tree-sitter, name-based static reachability, explicitly advisory, bounded at 4000 files / 2 hops / 200 nodes. **This is the repo's only existing structural-locality mechanism.**
- `reuse_detection.py` uses raw cosine with thresholds rather than RRF ranks, deliberately, because reuse needs an absolute number.
- **There is no reranker anywhere** in project code.
- **`retrieval.py` has zero test coverage.** The RRF arithmetic, the `plainto_tsquery` AND→OR rewrite, the hierarchy-group exclusion and the `PARENT_OF` expansion skip are all untested; the one test file that references it monkeypatches `retrieve` away. `graph_store.traverse_from` is likewise untested and marked NOT LOAD TESTED in-source.
- `graph_memory.py` deliberately reports flat retrieval and hierarchical routing **separately and never blends them**.

Decide:

- What defines the local neighbourhood, as an actual query? "Current repository, branch, files, symbols" is a description; the decision is which of those are indexed columns, which are graph traversals, and which are computed per request.
- How do the five tiers compose — a cascade where each tier filters the next, a union with tier-weighted scoring, or a budget allocated across tiers?
- Does reranking land in milestone 1? Nothing exists today, and adding a cross-encoder or LLM reranker is a per-query cost on the hot path.
- What is retrieved *for what*? Retrieving procedures for reuse, claims for context, and episodes for evidence are three different queries with three different relevance criteria — should they share one retriever?

Grill these:

- The strongest thing this repo already has is `call_graph.py`, and it is advisory and name-based. Structural locality in a codebase is the tier spec.md ranks *first*. Is name-based reachability good enough to lead the hierarchy, or does leading with a weak signal poison everything downstream?
- **Context size is a stated metric, and the constraint is that context stays approximately local as memory grows.** What is the actual budget, and what enforces it? Without a hard cap, "local-first" is a preference that degrades silently.
- Is the overlap with ticket 12's "relevant local graph neighbourhood" a duplication? Decide whether applicability and retrieval share one neighbourhood computation or genuinely need two.
- Retrieval is the load-bearing component with zero tests. Does anything here get built on top of it before the existing behaviour is pinned down by tests? Weigh this against the migration/testing fog on the map.

## Research findings (Brief 4 — [answers4.md](../research/answers4.md))

Not an answer; evidence for whoever resolves this. Mostly borrowed from repository-level code
retrieval benchmarks, IR fusion literature and prompt-compression work — **no study ranks these
exact locality signals for LLM-coding-agent retrieval**.

**14.1 — structural beats semantic for code, with a signal ranking.** The consistent finding
across repo-level retrieval benchmarks: structural locality (symbols, dependencies, call-graph
reachability) outperforms embedding similarity for code tasks, and one graph-based system reports
**83% answer quality at ~10× fewer tokens** than file-exploration agents.

| Signal | Precision / recall | Use as |
|---|---|---|
| Relevant symbols (defs, call sites) | high P, high R | **hard filter** |
| Open-file context | high P, moderate R | **hard filter** |
| Dependency-graph context | high R, moderate P | **hard filter** (pairs with open-file for precision) |
| Related tests | high P, moderate R | hard filter for implementation tasks |
| Recent commits | moderate P, low R | **soft ranking / tiebreaker only** |
| Recent failures | moderate P, low R | **soft ranking / tiebreaker only** |

Nuance worth carrying: one benchmark found structural retrieval wins on *budgeted* context yield
while embeddings win on Recall@20 — i.e. structural is better when tokens are scarce, semantic is
better when recall matters more than budget. Combining both beat either alone.

**14.2 — strict cascade is an antipattern here.** The risk this ticket names is confirmed and
named: **early-stage recall loss**, where a weak early tier permanently discards results no later
tier can recover. Recommendation is **union with tier-weighted scoring**, not a filter chain.
Additional detail: RRF is sensitive to its `k` parameter, and **convex combination (weighted sum)
outperformed RRF in both in-domain and out-of-domain settings and is sample-efficient** (Bruch et
al. 2023) — so the existing `RRF_K=60` in `retrieval.py` is a reasonable default but not
obviously the best available, and a weighted sum is worth benchmarking against it.

> **Cross-ticket tension, resolved — read alongside ticket 12.** Brief 3 concludes the *opposite*
> for applicability: strict, **non-compensatory** filtering is mandatory there, because letting a
> high similarity score outweigh a violated precondition is the "criterion compensation"
> antipattern. Both hold, because they govern different things. **Hard constraints filter; soft
> relevance signals fuse.** A violated precondition is a disqualification, not a weak signal; a
> low structural-locality score is a weak signal, not a disqualification. Do not unify the two
> mechanisms.

**14.3 — reranking: skip it in milestone 1.** Reranking buys ~5–15% NDCG@10 at ~50–200ms p95 in
general IR — but the marginal gain **drops from ~15% to ~5% when the first stage is already
strong**, which describes this repo's existing hybrid + RRF + graph-expansion stage exactly. If
ever added, use a **dedicated cross-encoder reranker, not LLM-as-reranker** (~50ms vs.
~200–2000ms p95). Note the strongest cost/quality figures for dedicated rerankers are
vendor-published; the independent work is more equivocal, finding cross-encoders cheaper/faster
but slightly worse on answer quality than LLM reranking.

**14.4 — separate retrieval paths per type, plus a meta-ranker.** Shared embedding space degrades
when object types carry genuinely different relevance semantics, which is exactly the case here
(procedures need applicability, claims need currency, episodes need provenance). Recommended:
separate paths, with a meta-ranker for cross-type comparison — that cross-type ranking is the one
real thing separate paths cost you. Useful precedent: one system uses separate query *tools*
(symbol search, call-path tracing, impact analysis) over a **single shared graph index**, which
suggests the split can be at the query layer rather than requiring separate stores.

**14.5 — the answer to the filter-vs-feature question.** Name-based matching achieves roughly
**75–85% precision** in well-structured Python; type resolution buys another **~10–15%**. And the
distinction this ticket asked about is answered directly: **imprecise structural signals are worse
as hard filters than as ranking features**, because a filter's false negatives are unrecoverable.
Both cited systems degrade the same way — using precise strategies as filters and falling back to
*ranking* when precision drops.

So `call_graph.py` should contribute a **ranking feature**, not lead as a hard filter — which is a
direct correction to this ticket's framing of structural locality as tier one of a filter cascade.

**14.8 — what characterization tests can actually pin.** They pin **output stability** (regression
detection) and nothing about correctness. The useful addition is **property-based tests over
invariants that hold without knowing the right answer** — for RRF specifically: monotonicity
(higher fused score ⇒ higher rank), idempotence (same inputs ⇒ same ranking), commutativity (input
list order irrelevant under symmetric weights), and stability (small input perturbations don't
reorder the head). That is a concrete, achievable test suite for an untested ranking function;
correctness still needs human relevance judgements, which is out of scope here.

**14.6 — context budget: tokens, priority-fill, and a knee at ~8k.** Denominate in **tokens**
(not items, not FLOPs). Enforce by **priority-ordered fill** — structural > temporal > causal >
semantic — or fixed per-source quotas. The empirical shape of the curve: a **knee at ~8k tokens
for code tasks**, beyond which reported degradation is severe (one source reports 13.9–85%
depending on task, another ~20 percentage points when relevant content moves from the edges to
the middle of the window). That gives this ticket a real, defensible default budget rather than an
arbitrary one, and it is what makes "local-first" enforceable rather than aspirational.

## Answer

**The local neighbourhood is a query split by how precisely each signal was derived — not a
uniform cascade.** Structural locality beats semantic similarity for code tasks (one graph-based
system reports 83% answer quality at ~10× fewer tokens than file-exploration), but "structural"
is not one thing, and its sub-signals differ enough in precision that they belong on opposite
sides of the filter/rank line:

| Signal | Role | Why |
|---|---|---|
| Relevant symbols (defs, call sites) | **filter** | high precision *and* recall |
| Open files / current working set | **filter** | high precision, moderate recall |
| **Import-derived** dependency edges | **filter** | deterministic derivation |
| Related tests | **filter** | high precision for implementation tasks |
| **Name-resolved** call-graph edges | **rank** | see below |
| Recency (recent commits) | **rank** | moderate precision, low recall |
| Recent failures | **rank** | moderate precision, low recall |

**`call_graph.py` contributes a ranking feature, not a filter** — a direct correction to this
ticket's framing of structural locality as tier one of a filter chain. Name-based matching reaches
roughly **75–85% precision** in well-structured Python (type resolution buys another ~10–15%),
and the cited systems degrade the same way: precise strategies filter, imprecise ones fall back to
*ranking*. The asymmetry is what decides it — **a filter's false negatives are unrecoverable**,
while a ranking feature's errors only misorder. At 75–85% precision, leading a filter cascade
would permanently discard 15–25% of relevant results with nothing downstream able to recover them.

This also resolves the apparent tension in the row above: dependency context derived from
**imports** is deterministic and can filter; dependency context derived from **name resolution**
ranks.

**Tiers compose by union with weighted fusion, not a strict cascade.** The failure this ticket
anticipated is real and named — **early-stage recall loss**, where a weak early tier permanently
discards what no later tier can recover — and strict cascade is described as an antipattern for
code retrieval specifically. So: retrieve per tier, fuse, then rank.

Keeping `RRF_K = 60` in `retrieval.py` as the default. The research reports that convex
combination (weighted sum) outperformed RRF both in- and out-of-domain and is sample-efficient,
which makes it a genuine benchmark candidate — but **switching on that basis alone would trade a
working default for an unvalidated one**, and this repo cannot currently measure the difference.
Recorded as a measurement task, not adopted.

> **This is deliberately the opposite of ticket 12's conclusion, and both are correct.** Ticket 12
> mandates a strict **non-compensatory** filter, because letting similarity outweigh a violated
> precondition is the criterion-compensation antipattern. The difference is the nature of the
> signal: a violated precondition is a **disqualification**; a low locality score is a **weak
> signal**. Hard constraints filter; soft signals fuse. The two mechanisms must stay separate.

**Reranking is out of milestone 1.** In general IR it buys ~5–15% NDCG@10 at ~50–200ms p95 — but
the marginal gain **falls from ~15% to ~5% when the first stage is already strong**, and this
repo's first stage is already hybrid dense+lexical, RRF-fused, with one hop of graph expansion.
Paying ~50ms on a hot path for ~5% is not a milestone-1 trade. If added later: a **dedicated
cross-encoder, never LLM-as-reranker** (~50ms vs. ~200–2000ms p95). Noting honestly that the
strongest dedicated-reranker figures are vendor-published; the independent work is more equivocal.

**Three retrieval paths over one shared index, and no meta-ranker yet.** A shared embedding space
degrades when object types carry genuinely different relevance semantics — and they do here:
procedures need *applicability*, claims need *currency*, episodes need *provenance*. But separate
*stores* would be overreach; the useful precedent is a system exposing separate query tools
(symbol search, call-path tracing, impact analysis) over a **single shared graph index**. So the
split lives at the query layer.

The one thing separate paths cost is cross-type ranking — the ability to say "this claim matters
more than that procedure right now." **Deferred, because nothing in milestone 1 asks that
question**: an applicability check wants procedures, context assembly wants claims, and each caller
knows which. A meta-ranker without a consumer is speculative machinery; to fog.

**Context budget: tokens, hard cap, 8k default, priority-ordered fill.** Denominated in **tokens**
— item counts do not bound context, and FLOPs are not measurable here. Filled in priority order
**structural > temporal > causal > semantic**, truncating at the cap rather than degrading
silently.

The default is **8k tokens**, which is where the reported knee sits for code tasks; beyond it
degradation is severe (one source reports 13.9–85% depending on task; another ~20 percentage
points when relevant content moves from the window edges to the middle). A hard cap enforced at
assembly time is what makes spec.md's "context stays approximately local as memory grows" an
enforced property rather than an aspiration. Borrowed constant, so it is configuration.

**Property-based tests land before any extension of `retrieval.py`.** The component is
load-bearing with zero coverage — its RRF arithmetic, its OR-rewriting query fix, and its
exclusion rules are all untested, and `graph_store.traverse_from` is annotated in-source as not
load tested. Characterization tests pin **output stability** (regression detection) and nothing
about correctness, which for a *ranking* function is a real limit — there is no obviously right
ranking to assert.

What is assertable without knowing the right answer are **invariants**, and for RRF they are
concrete: monotonicity (higher fused score ⇒ higher rank), idempotence (same inputs ⇒ same
ranking), commutativity (input-list order irrelevant under symmetric weights), and stability
(small perturbations do not reorder the head). That is a cheap, achievable suite. Correctness
still needs human relevance judgements, which are out of scope here and stated as such rather than
quietly implied.

**Provenance of this answer.** Literature-grounded: structural-beats-semantic for code, the
early-stage-recall-loss antipattern, reranker marginal-gain decay, shared-embedding degradation
for heterogeneous types, name-based precision figures and the filter-versus-feature asymmetry, the
~8k knee, and the RRF invariants. Judgement calls: splitting dependency context by derivation
method (import vs. name-resolved), and deferring the meta-ranker on consumer grounds. Flagged
absent by the research: **no study ranks these locality signals for LLM-coding-agent retrieval,
none measures reranking for code specifically, and none measures name-based call-graph precision
for Python** — the numbers above are transferred and should be treated as calibration, not
constants.
