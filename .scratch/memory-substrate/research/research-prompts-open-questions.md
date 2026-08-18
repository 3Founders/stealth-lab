# Research prompts for the open tickets: 4 consolidated tradeoff briefs

Companion to [external-literature-review.md](external-literature-review.md) (broad landscape)
and [external-literature-review-2-open-questions.md](external-literature-review-2-open-questions.md)
(per-ticket, lumped). This file exists because review #2 asked one question per *ticket*, and a
ticket bundles 5–8 distinct decisions — so it reported "no applicable literature" for 5 of 7
tickets while strong prior art existed for individual sub-questions inside them.

**The miss that motivated this file**: ticket 15 asks whether a fully-specified procedure should
demote the planner to a fallback. Review #2 correctly said no LLM-agent study measures this —
but missed **the utility problem** (Minton, macro-operator learning / case-based planning): a
named classical result that adding stored plans can make a system *net slower*, because match
and retrieval cost grows faster than the planning effort saved. That is a direct threat to this
map's entire premise, and ticket-level searching never surfaced it.

All 51 decisions across the 7 open tickets were triaged (full table in the session plan): **31
are externally researchable**, 17 are answerable from this repo alone, and 3 need this repo's own
data. The 31 are consolidated below into **4 briefs by tradeoff family** — each brief is one
pasteable prompt covering a coherent cluster of coupled decisions, with the specific alternatives
and the named prior-art leads preserved. Sub-question ids are noted per brief so answers can be
routed back to tickets.

---

## Brief 1 — Temporal fact representation: derived-on-read vs. materialized

*Covers 10.1, 10.4, 10.7, 10.8, 10.9.*

> I'm designing the state representation for an agent-memory system backed by PostgreSQL. The
> core bet I want stress-tested: rather than storing `state_before`/`state_after` snapshots per
> episode, I plan to make state a **read-time projection over a bitemporal fact graph** — state
> at time T = the set of facts whose validity interval contains T, filtered to a subject scope,
> with deltas computed as set differences between two such projections and never stored. Under
> this design there is no state table at all; "current state" is just a query, and state becomes
> indistinguishable from short-lived beliefs.
>
> Please cover five coupled questions.
>
> **(1) Is projection-by-query viable, and when does it break?** Alternatives are: store
> snapshots, store deltas and reconstruct, store all three, or my query-only approach. I want the
> documented *performance* failure modes of computing state at read time — at what data volumes
> they bite, and what practitioners report as the trigger for introducing materialization. Draw on
> event-sourcing/CQRS projection and snapshotting practice, Datomic and XTDB as-of query
> performance, and temporal-database literature. Are there systems that tried pure query-time
> state and abandoned it?
>
> **(2) Is "state is just time-scoped facts" a recognized stance or a novel bet?** I'm collapsing
> the distinction between durable belief and transient world-state. Does the situation
> calculus/fluent tradition, temporal RDF, or BDI agent architecture literature support treating
> state as time-indexed facts — and has anyone argued *against* unifying them, with concrete
> problems that resulted? Also, what does the frame problem literature imply for a design that
> must represent what *hasn't* changed only implicitly?
>
> **(3) Index shape and materialization threshold.** What index designs do temporal databases
> actually use for as-of queries — is a `(subject, valid_from, valid_to)` composite btree right,
> or do production systems need interval trees or GiST range indexes? For PostgreSQL specifically,
> what's established practice for temporal validity ranges (btree on bounds vs. range types with
> GiST)? And are there *published numbers* for when a temporal query must be backed by a
> materialized projection rather than computed live — in event counts or latency budgets?
>
> **(4) Representing unknown vs. false vs. partial.** My plan is that absence is simply an empty
> query result, with no explicit null sentinel. Is that sufficient, or does prior art show you
> need "unknown" explicitly distinguishable from "false"? Cover the open-world vs. closed-world
> tradeoff, three/four-valued logics (Kleene, Belnap) as applied to real systems, SQL NULL
> semantics as a cautionary tale, and whether any system treats "partial knowledge about an
> entity" as first-class rather than as absence.
>
> **(5) Immutable artifact references.** Some artifacts I reference are natively content-addressed
> (git commits, git blobs); others aren't (test output, build logs). Options: git SHAs plus
> blob-store URIs for the rest, content-hash everything into a CAS, or a typed reference union
> naming its addressing scheme. Draw on the git object model's rationale, content-addressable
> storage design (CAS, Merkle DAGs), and build-system artifact addressing (Bazel action cache, Nix
> store paths). What goes wrong when one scheme mixes addressing modes, and how do production
> systems handle artifacts too large or streamed to content-address cheaply?
>
> For each of the five, if the honest answer is that this is an unstudied engineering tradeoff
> rather than a settled question, say so plainly rather than fitting a loosely-related source to
> it. Prefer sources with real measurements over architectural advice, and flag which numbers come
> from vendor material versus independent evaluation.

---

## Brief 2 — Extracting structure from raw traces: what's deterministic, what needs a model, and can you trust it

*Covers 04.2, 04.3, 04.4, 04.5, 04.6, 11.1, 11.2, 11.4, 11.5.*

> I'm building a pipeline that turns raw LLM-coding-agent execution traces (file edits, tool
> calls, test runs, commits, shell commands, subagent invocations) into structured memory: first
> segmenting continuous sessions into coherent task episodes, then extracting semantic
> "observations" from events, then deriving durable claims. Guiding constraint: prefer
> deterministic local processing, and don't let every tool call trigger an LLM call.
>
> Please cover five coupled questions.
>
> **(1) Segmenting sessions into episodes.** Available deterministic signals: user prompt
> submissions, session start/end, subagent nesting, git commits, test-run completions,
> working-directory changes, context-compaction events, idle gaps. Which are reliable task
> boundaries, and is there evidence ranking them by precision vs. recall? I'm specifically
> suspicious of idle-time gaps — the session-identification literature in web analytics has
> criticized the "30-minute timeout" convention, and I'd like that critique. Then: **how do
> multiple signals compose when they conflict** (a commit lands mid-prompt; one prompt spans many
> commits; a subagent's work is both its own unit and part of its parent's)? Options are strict
> precedence, voting, hierarchical nesting, union, or intersection. Draw on process-mining case-ID
> discovery, dialogue/topic segmentation, hierarchical segmentation models, and multi-signal
> change-point detection.
>
> **(2) Is "episode" even the right unit?** My downstream goal is mining *reusable procedures*, so
> maybe the useful unit isn't a task episode but "the span from which a reusable procedure could
> be extracted" — possibly finer than a session, coarser than a tool call, and not aligned to any
> transcript boundary. Cover option discovery and temporal abstraction in hierarchical RL (how are
> option boundaries chosen — subgoal discovery, bottleneck states, change-point methods?), skill
> segmentation from demonstration trajectories in robotics/imitation learning, macro-operator
> extraction from plan traces, and process mining's "case notion" selection problem. The question:
> does optimizing for *coherent description* produce different boundaries than optimizing for
> *extractability of reusable structure*?
>
> **(3) Evaluating segmentation with no ground truth.** No gold boundaries exist for my corpus and
> labeling at scale isn't feasible. Beyond Pk and WindowDiff (which need labels, and whose known
> biases I'd like covered): what's legitimate — purity/coverage, stability under perturbation,
> inter-rule agreement, downstream-task evaluation, or small-sample human adjudication? What
> sample size is considered adequate to validate a segmenter, and what inter-annotator agreement
> do people actually achieve on task-boundary tasks? If agreement is low, that itself bounds how
> much precision is worth chasing.
>
> **(4) The deterministic/model split, and whether the semantic layer earns its place.** What
> fraction of useful higher-level signal is recoverable from structured execution logs by
> deterministic rules alone, versus needing a model? **Event abstraction in process mining**
> (low-level event → high-level activity) is decades of work on exactly this problem — does that
> literature *demonstrate* the intermediate abstraction layer provides measurable benefit, or
> assume it? I'm also weighing skipping the observation layer entirely and having a claim
> extractor read events directly, so cover the pipeline-vs-end-to-end / error-propagation
> tradeoff. I especially want studies reporting a rule-based *baseline* before adding a model. If
> nobody has published that comparison, say so — knowing it's unmeasured argues for making the
> model layer optional and measurable rather than assumed.
>
> **(5) Versioning extraction, and confidence at the first interpretive step.** Every derived fact
> must be attributable to the logic that produced it ("this claim came from extractor version X
> over trace Y"). What should the version identifier consist of — code hash, prompt-template hash,
> model ID, decoding parameters, or a compound key? How do systems handle a hosted model changing
> behavior without changing its advertised ID? Draw on ML lineage/experiment-tracking practice
> (MLflow, DVC, OpenLineage) and LLM reproducibility work. Separately: observations are the *first*
> interpretive step, so their only evidence is the raw events — what can confidence legitimately
> be derived from? Candidates: token/sequence probabilities, verbalized self-confidence,
> self-consistency across runs, deterministic-rule/model agreement, coverage of source events, or
> refusing to assign confidence at this layer. Cover LLM calibration findings, selective
> prediction/abstention, and conformal prediction for LLM outputs. Critically: is *any* cheap
> LLM confidence signal calibrated enough to be worth storing? If the literature says these are
> unreliable, say that plainly rather than offering a menu.
>
> Throughout: say explicitly where nothing applies to agent traces specifically and you're
> transferring findings from an adjacent domain. I'd rather know which conclusions are borrowed.

---

## Brief 3 — Reuse economics: does stored knowledge apply, when is it trusted, and does reuse even pay?

*Covers 12.1, 12.2, 12.3, 12.5, 12.6, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 15.7.*

> I'm building a procedural-memory system for LLM coding agents: parameterized procedures are
> mined from past execution traces, verified through repeated successful use, retrieved when
> applicable, and versioned/retired as evidence changes. Reuse must not be based on embedding
> similarity alone. Please cover six coupled questions — question (6) is the one that could
> invalidate the whole premise, so weight it accordingly.
>
> **(1) What a precondition concretely is.** Options: short string tags compared by set overlap
> (what my codebase does today — Jaccard against a threshold), structured logical predicates over
> typed state (STRIPS/PDDL style), executable check functions, natural-language conditions judged
> by an LLM, or a hybrid. I want the *failure modes* of each, not just the menu: what goes wrong
> with tag matching in practice, what's the authoring cost of formal predicates, and is there
> measurement of how often an LLM misjudges a stated precondition? Cover STRIPS/PDDL preconditions
> and their practical critiques, case-based reasoning's similarity-vs-applicability distinction,
> and business-rules/feature-flag targeting systems as a large-scale practical analogue.
>
> **(2) Combining heterogeneous applicability factors.** I must combine nine factors: explicit
> preconditions, current state, declared scope, exclusions, temporal validity, environment
> compatibility, verification status, semantic similarity, and graph-neighborhood relevance.
> Options: strict filter cascade (hard constraints first, similarity only ranks survivors),
> weighted linear score over everything, learned ranker, or filter-then-score. The pathology I must
> prevent: a high similarity score compensating for a *violated* hard constraint. Does that
> score-compensation failure have a name, and what's the established guard? Cover multi-criteria
> decision analysis on mixing hard constraints with soft preferences, soft-constraint CSP, and IR's
> filtering-versus-ranking distinction.
>
> **(3) Unknown preconditions.** Some precondition will be *unknown* rather than true or false —
> nothing has recorded the relevant fact yet. Three responses: fail closed (unusable early, when
> little is recorded), fail open (falls back to similarity, which silently becomes the
> similarity-only behavior I'm trying to avoid), or represent uncertainty explicitly. What's the
> principled answer, and what do practical systems do? Cover classical planning's treatment of
> incomplete initial state, conformant/contingent planning, three-valued belief states, and
> fail-safe versus fail-operational defaults in safety engineering. Then the practical part: is
> there a documented **cold-start** strategy for a system that must fail closed in principle but
> starts with almost nothing recorded?
>
> **(4) Machine-writable scope narrowing.** When a procedure fails because it was applied outside
> its real scope, the system should narrow its scope or add an exclusion *automatically* — so these
> fields must be machine-writable. My strongest lead is **version-space learning** and concept
> refinement (Mitchell): maintaining general/specific boundaries and tightening from negative
> examples. Confirm or refute that framing, and cover inductive logic programming theory revision,
> rule specialization, and CBR work on learning adaptation-failure conditions. Address three
> dangers: overfitting scope to a single failure until the procedure is uselessly narrow;
> oscillation as scope narrows and re-widens; and the ordering dependency that you must classify
> *why* it failed before narrowing — does the literature treat that dependency?
>
> **(5) Verification, failure classification, staleness, and escape hatches.** Four tightly linked
> lifecycle questions:
>
> - *How much evidence promotes a candidate to verified?* I want statistical rigor, not a magic
>   number: cover the sequential probability ratio test and anytime/sequential testing (I decide as
>   evidence arrives, not at fixed n), Beta-Bernoulli posteriors and credible intervals for tiny
>   samples, best-arm identification, and small-sample bounds for zero-failure observations
>   (the "rule of three"). Concretely: with 3 successes and 0 failures, what can honestly be
>   claimed? And does the literature support requiring *diversity of contexts* rather than raw
>   count — a formal treatment of "verified across distinct conditions"?
> - *Classifying a failure's cause* into: transient noise, precondition violation, scope violation,
>   environment/dependency change, structural defect, or genuinely ambiguous. My strongest
>   industrial analogue is **flaky-test classification** — distinguishing "test is broken" from
>   "code is broken" from "nondeterministic," which large engineering orgs have published on with
>   real numbers. Which of my six categories are determinable from deterministic signals (retry
>   behavior, dependency diffs, precondition re-checks) and which need judgement? What's the
>   reported accuracy ceiling for automated failure classification — if low, that argues for fewer,
>   coarser categories.
> - *Detecting staleness from dependency change.* Requires knowing a procedure's dependencies. My
>   leads: **build-cache invalidation** (Bazel action cache, Nix input-addressed derivations) and
>   **test-impact analysis** (which tests to rerun after a change — published with real numbers by
>   several large orgs). What dependency granularity is worth it (file / symbol / package-version),
>   what precision-recall tradeoffs are reported for coarse vs. fine tracking, what do these systems
>   do about invisible dependencies (reflection, dynamic dispatch, external services), and is
>   deliberate conservative over-invalidation established practice with accepted rates?
> - *Escape hatches for the ambiguous residual.* Ambiguous failures must not automatically mutate
>   durable memory — but then what happens to them (recorded and ignored, accumulated toward a
>   threshold, queued for human review, or confidence lowered without status change)? And what
>   stops a verified procedure that fails repeatedly-but-unclassifiably from being retried forever?
>   Cover **circuit breakers** (including half-open probe states) and **flaky-test quarantine**
>   policies with their actual thresholds, plus bandit arm elimination. How do these avoid
>   permanently killing something only temporarily broken, and how is oscillation prevented?
>
> Separately on lifecycle representation: is status a stored column, derived from evidence on read,
> or **several orthogonal axes**? I lean toward orthogonal, because my own system already has two
> precedents (a fact can be "still valid" yet "no longer believed"; an agent can be "approved" yet
> not "runnable"). Is collapsing multi-concern status into one enum a named antipattern? Also, I've
> heard of a 2026 survey on evolving skill libraries for LLM agents proposing exactly
> candidate/verified/stale/revalidated/retired — please locate and assess it properly.
>
> **(6) Does reuse pay at all? The utility problem.** This is the question I most want stress-
> tested, because it threatens the premise. In macro-operator learning and case-based planning,
> **the utility problem** (Minton, and the speedup-learning literature) is the named result that
> adding stored plans can make a system *net slower* — match and retrieval cost grows faster than
> the planning effort saved. Please cover: how it was characterized and measured, what mitigations
> were developed (utility-based retention, selective forgetting, match-cost-aware indexing), and
> whether it's considered solved or merely managed. Beyond speed, is there a *quality* analogue —
> retrieved plans constraining the system into a worse solution than fresh planning would have
> found? Then the related measurement question: I need to detect when reuse actively hurt versus
> solving from scratch. I've been calling it "false reuse rate" but suspect the standard framing is
> **negative transfer** in transfer learning — a named, measured phenomenon with established
> measurement relative to a no-transfer baseline. Is that the right frame, and does measuring it
> *require* a matched no-reuse control arm, or are there single-arm proxies? Also cover
> retrieval-augmented-generation evaluation practice for "retrieval made the answer worse." Finally:
> has the LLM-agent literature confronted the utility problem at all? If it hasn't, say so
> explicitly — that absence is itself an important finding for me.
>
> Throughout: distinguish what's empirically established from what's theoretically proposed but
> unused in practice, and say plainly where nothing applies rather than stretching an analogue.

---

## Brief 4 — Retrieval locality, budgets, and resource allocation

*Covers 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.8, 15.2, 15.4, 15.6.*

> I'm designing memory retrieval and execution-resource allocation for an LLM coding-agent system.
> Retrieval must be local-first: scoped to a "local epistemic neighborhood" (current repository,
> branch, open files, relevant symbols, their dependencies, recent commits, recent failures,
> related tests) rather than dumping global history, and retrieved context must stay approximately
> constant in size as the memory corpus grows. Please cover six questions.
>
> **(1) Defining locality as an actual query.** I need to turn that description into a query plan:
> which signals are indexed column filters, which are graph traversals, which must be computed per
> request. Is there evidence *ranking* locality signals by value — does open-file context beat
> dependency-graph context, does recency beat structural proximity? Cover repository-level code
> retrieval and repo-context construction for coding agents, program slicing and dependency-based
> context selection, IDE/language-server context models, and any ablations on which locality
> signals most improve coding-agent task success. Prefer papers with ablations over architecture
> descriptions.
>
> **(2) Composing a five-tier cascade.** My tiers are structural locality → temporal locality →
> causal/graph locality → semantic retrieval → reranking. Options: strict cascade where each tier
> filters the next, union with tier-weighted scoring, fixed budget allocated across tiers, or
> adaptive weighting. The risk: an early weak tier permanently filtering out results a later
> stronger tier would have found — early-stage recall loss nothing downstream recovers. Is that
> quantified, and what are the established mitigations (over-retrieve early, union-then-rank,
> per-tier guaranteed quotas)? Cover multi-stage/cascade ranking in IR, hybrid retrieval fusion
> comparisons (reciprocal rank fusion vs. weighted score fusion vs. learned fusion — is there
> consensus?), and whether adaptive tier weighting beats fixed composition in practice.
>
> **(3) Is reranking worth it for code specifically?** My first stage is already hybrid (dense +
> lexical, fused by reciprocal rank fusion) plus one hop of graph expansion; reranking adds
> per-query hot-path latency. What NDCG/recall improvement does reranking typically buy on *code*
> retrieval benchmarks, at what added p95 latency, and does the gain hold when the first stage is
> already hybrid+RRF? I believe there's recent work finding that stronger first-stage retrieval
> reduces marginal reranker benefit — please locate and assess it. Also compare dedicated reranker
> models against LLM-as-reranker on cost/latency/quality; I've seen large vendor-claimed advantages
> for dedicated rerankers and want independent corroboration, so be explicit about which numbers
> are vendor-published.
>
> **(4) One retriever or several.** I retrieve three quite different things with different relevance
> criteria: procedures (needs applicability, not just similarity), claims (needs currency and truth
> status), episodes (needs provenance and completeness). Share one configurable retriever, or
> separate paths? Cover multi-task and task-aware retrieval (instruction-following retrievers,
> task-conditioned embeddings), federated/multi-index architectures, and heterogeneous-entity
> retrieval over knowledge graphs. Is there evidence a shared embedding space degrades when object
> types have genuinely different relevance semantics? And what's lost with separate paths — just
> maintenance, or also cross-type ranking (saying "this claim matters more than that procedure right
> now")?
>
> **(5) Two quality-of-foundation questions.** First: my only existing structural-locality
> mechanism is **name-based** static call-graph reachability (tree-sitter, matching call sites to
> definitions by identifier name, no type resolution — so same-named functions in different files
> collide). Structural locality is the tier I want to rank *first*, so a weak leading signal could
> poison everything downstream. What precision does name-based matching actually achieve in Python
> codebases, how much does type resolution buy, and — the distinction that matters most to me — is
> there evidence about whether an imprecise structural signal is worse used as a *filter* (loses
> results permanently) than as a *ranking feature* (just misranks)? Second: this retrieval component
> is load-bearing with **zero test coverage** (its rank-fusion arithmetic, a query-rewriting fix,
> and several exclusion rules are all untested). My lead is **characterization tests** (Feathers,
> *Working Effectively with Legacy Code*) — pinning current behavior before extending. Is that the
> right frame, are there empirical studies on whether test-first-on-legacy reduces defect rates
> versus proceeding carefully, and for a *ranking* function where correctness is subjective, what
> can characterization tests usefully pin beyond regression detection? Cover approval/golden-master
> testing and property-based testing for ranking/fusion (what invariants can you assert about RRF
> without knowing the right answer?).
>
> **(6) Budgets and resource allocation.** Two linked parts. *Context budget:* what should it be
> denominated in — tokens, retrieved items, or compute/FLOPs? What enforces it when multiple
> sources compete (fixed per-source quotas, priority-ordered fill, learned allocator)? And is there
> evidence about the *shape* of the quality/context-size curve for code tasks — a documented knee
> beyond which more retrieved context stops helping or starts hurting? Cover prompt compression,
> the "lost in the middle" long-context degradation findings, and production writeups on enforcing
> hard context caps. *Execution budget:* I have eight tuned constants (max subtasks per
> decomposition, max recursion depth, retry attempts, per-subtask and total step budgets, minimum
> viable budget, parallelism cap, planner context size). Should they be static config, per-procedure
> overrides, or derived from each procedure's own execution history? Cover adaptive computation,
> anytime algorithms and deliberation-scheduling/metareasoning (deciding how much effort to spend
> per instance is exactly my question), and per-instance algorithm configuration. Is there evidence
> adaptive per-instance budgets beat well-tuned static ones, by how much, and what sample size is
> needed before per-item derivation wins — with a recognized cold-start pattern like empirical-Bayes
> shrinkage from a global default? **And critically**: each of my eight constants carries an
> in-source comment citing a measurement from a benchmark domain that has now been scoped *out* of
> my project. What does the literature say about hyperparameter transferability across task
> distributions — which parameter kinds transfer (structural limits) versus which are highly
> distribution-sensitive (budgets, thresholds, stopping criteria)? Is there a cheap re-validation
> protocol to check whether an inherited value is still near-optimal without a full sweep?
>
> One engineering question alongside these: my execution engine is synchronous (~1900 lines, no I/O
> of its own) and must integrate into an async Python service. Its synchronicity is load-bearing —
> called from an async context, awaiting DB calls inside it raises "cannot be called from a running
> event loop," and the current workaround splits an async pre-step before the sync run. Options:
> keep it sync in a worker thread, convert fully to async, or formalize a sync core with an async
> shell doing all I/O at the boundary. Cover "functional core, imperative shell" and sans-I/O
> protocol design, `asyncio.to_thread`/executor costs (thread-pool exhaustion, GIL contention,
> cancellation semantics — my workload is I/O-bound LLM calls), and incremental async adoption in
> synchronous codebases. Specifically: what breaks when a sync function in a thread needs to
> *initiate* async work mid-execution, what are the documented patterns (pre-fetch at the boundary,
> `run_coroutine_threadsafe`, queue-based request/response), and which survive cancellation and
> timeouts cleanly?
>
> Throughout: prefer independent evaluation over vendor material and flag which is which; say
> plainly where nothing applies to code retrieval or agent systems specifically and you're
> transferring from general IR or ML.

---

## Named prior-art leads embedded above

Each of these is named inside a brief so the search confirms or refutes rather than
rediscovers — they're the leads a generic search reliably misses:

| Lead | Brief | Bears on |
|---|---|---|
| The utility problem (Minton; macro-operator learning, CBR) | 3 | Whether stored procedures net-slow the system — threatens the premise |
| Negative transfer (transfer learning) | 3 | The measurable analogue of spec.md's "false reuse rate" |
| Flaky-test classification and quarantine | 3 | Failure-cause classification + the escape hatch |
| Test-impact analysis / build-cache invalidation (Bazel, Nix) | 3 | Dependency-driven staleness at scale |
| Version-space learning / concept refinement (Mitchell) | 3 | Machine-writable scope narrowing from failures |
| SPRT, Beta-Bernoulli, best-arm identification | 3 | How much evidence promotes candidate → verified |
| Event abstraction in process mining | 2 | Whether the observation layer earns its place |
| Process-mining case-notion selection | 2 | Whether "episode" is the right unit at all |
| Characterization tests (Feathers) | 4 | Building on an untested load-bearing component |
| Name-based vs. type-resolved static analysis precision | 4 | Whether call-graph reachability can lead the hierarchy |
| Situation calculus / fluents; temporal RDF | 1 | Whether "state is time-scoped claims" is recognized |
| Content-addressable storage (git object model, CAS, Nix) | 1 | Immutable artifact reference format |

## Not covered here (and why)

**17 decisions are answerable from this repo plus already-resolved tickets**, so no external
search applies: where the engine lives and its interface (15.1), the per-run-state refactor
(15.3), whether to restructure the class hierarchy (15.5), whether memory reaches node executors
(15.8), naming a producer for each applicability field (12.4), overlap between tickets 12 and 14
(12.7, 14.7), observation representation given ticket 03's test (04.1), whether the LLM extractor
ships in milestone 1 (04.7, follows from 04.3), state granularity and scoping (10.2, 10.3),
whether a world-state model is needed at all (10.5), domain-neutrality cost (10.6, settled by
ticket 02), the version-chain question (13.1, settled by ticket 05), reusing the existing review
state machine (13.8), and the semantic-segmentation seam (11.3).

**3 decisions need this repo's own data**: which deterministic boundary signals actually fire on
real sessions and how often candidate rules disagree (11.6, and the empirical half of 11.1) —
precisely why ticket 11 is typed `prototype` rather than `grilling`.
