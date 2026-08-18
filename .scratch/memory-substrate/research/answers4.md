
Completed 3 steps
Here’s a stress-test of your six coupled questions on memory retrieval and execution-resource allocation for an LLM coding-agent system, with sources ranked by how directly they address code retrieval/agent systems vs. general IR/ML. Where the honest answer is “this is borrowed and unmeasured for code retrieval or agents,” I say so.

(1) Defining locality as an actual query: which signals are indexed column filters, which are graph traversals, which must be computed per request?
Bottom line: No study ranks your exact locality signals (open files, branch, symbols, dependencies, recent commits, recent failures, related tests) for LLM-coding-agent retrieval. The closest empirical evidence comes from (a) repository-level code retrieval benchmarks (Agent Retrieval Bench, ProjAgent), (b) program slicing/dependency-based context selection, and (c) IDE/language-server context models. The consistent finding: structural locality (symbols, dependencies, call-graph reachability) beats semantic locality (embeddings) for code tasks; open-file context is a strong signal but must be combined with dependency-graph context. Recency is a weak signal alone but useful as a tiebreaker.

Locality signals: evidence and ranking
Signal	Evidence source	Precision/recall tradeoff	Decision it informs
Open-file context	Agent Retrieval Bench (file-level retrieval) 
; RepoMap (structural neighbors) 
High precision (open files are directly relevant), moderate recall (misses cross-file dependencies) 
Primary filter: use open files as a hard filter; combine with dependency-graph context for recall.
Dependency-graph context	ProjAgent (procedural similarity + dependency tracing) 
; Codebase-Memory (call-graph traversal) 
High recall (captures cross-file dependencies), moderate precision (may include irrelevant transitive dependencies) 
Secondary filter: use dependency-graph context as a hard filter; combine with open-file context for precision.
Relevant symbols (definitions, call sites)	Codebase-Memory (symbol search, call-path tracing) 
; ProjAgent (symbol pool from imports) 
High precision (symbols are directly relevant), high recall (captures all call sites/definitions) 
Primary filter: use symbol-based queries as hard filters; combine with open-file/dependency context for completeness.
Recent commits	Agent Retrieval Bench (edit2ripple task) 
; no direct evidence for recency as retrieval signal	Moderate precision (recent commits may be relevant), low recall (misses older but relevant code) 
Tiebreaker: use recency as a soft ranking signal, not a hard filter.
Recent failures	Agent Retrieval Bench (trace2code task) 
; no direct evidence for failure-based retrieval	Moderate precision (recent failures may indicate relevant code), low recall (misses unrelated but relevant code) 
Tiebreaker: use failure-based signals as soft ranking, not hard filters.
Related tests	Agent Retrieval Bench (code2test task) 
; ProjAgent (test-based procedural similarity) 
High precision (tests are directly relevant to implementation), moderate recall (misses non-test context) 
Primary filter: use test-based signals as hard filters for implementation tasks; combine with symbol/dependency context for completeness.
Ablations on locality signals
Agent Retrieval Bench shows that structural retrieval (RepoMap) beats semantic embeddings (Qwen3-4B/8B) on BCY@8k (Budgeted Context Yield), but embeddings beat RepoMap on Recall@20. This suggests structural locality is better for token-budgeted tasks, semantic locality is better for recall-heavy tasks.

ProjAgent shows that procedural similarity (dependency-traced) + semantic retrieval outperforms either alone on Pass@1 (41.14% vs. baselines). This suggests combining structural and semantic locality is optimal.

Codebase-Memory shows that graph-based retrieval (call-path tracing, symbol search) achieves 83% answer quality at 10× fewer tokens than file-exploration agents. This suggests structural locality is more token-efficient.

Engineering judgement call: No study directly ranks locality signals for LLM-coding-agent retrieval. The recommendation is borrowed from repository-level retrieval benchmarks and program-slicing literature.

(2) Composing a five-tier cascade: strict cascade, union with tier-weighted scoring, fixed budget, or adaptive weighting?
Bottom line: The risk you’re preventing — early-stage recall loss nothing downstream recovers — is a known failure mode in multi-stage/cascade ranking in IR. The established mitigations are over-retrieve early, union-then-rank, per-tier guaranteed quotas. Strict cascade is an antipattern for code retrieval; union with tier-weighted scoring (RRF or learned fusion) is the production pattern.

Cascade composition: established patterns
Pattern	Evidence source	Failure modes	Recommendation
Strict cascade (each tier filters the next)	IR multi-stage retrieval 
Early-stage recall loss (weak early tier permanently filters out results a later stronger tier would have found) 
Not recommended: strict cascade is an antipattern for code retrieval.
Union with tier-weighted scoring (RRF, weighted sum, learned fusion)	Hybrid retrieval fusion 
Score compensation (high similarity can override violated hard constraints) 
Recommended: use union with RRF or weighted sum; avoid strict cascade.
Fixed budget allocated across tiers	Agent Retrieval Bench (BCY@B curves) 
; prompt compression 
Under-utilization (fixed budget may leave high-quality tiers under-sampled) 
Optional: use fixed budget only if token constraints are hard; otherwise, use union-then-rank.
Adaptive weighting (learned fusion, query-aware tier weights)	Hybrid retrieval fusion 
Complexity (requires labeled data for training) 
Optional: use adaptive weighting only if you have labeled retrieval data; otherwise, use RRF.
Early-stage recall loss: quantified and mitigations
Bruch et al. (2023) show that RRF is sensitive to its parameter k; convex combination (weighted sum) outperforms RRF in in-domain and out-of-domain settings and is sample-efficient (requires only a small set of training examples).

Exp4Fuse (2025) shows that fusion ranking (RRF with adaptive weights) is necessary for LLM-based query expansion; single-route retrieval (strict cascade) underperforms fusion.

Agent Retrieval Bench shows that hybrid rank fusion (RRF) of structural + semantic retrieval outperforms either alone on BCY@8k.

Recommendation: Use union with tier-weighted scoring (RRF or weighted sum); avoid strict cascade. If you have labeled data, use learned fusion; otherwise, use RRF with k=60 (default in Elasticsearch).

(3) Is reranking worth it for code specifically?
Bottom line: No study measures reranking benefit for code retrieval specifically. The closest empirical evidence comes from (a) general IR reranking benchmarks (NDCG/recall improvements at added latency), (b) vendor-claimed advantages for dedicated rerankers (Voyage AI, Cohere), and (c) recent work finding that stronger first-stage retrieval reduces marginal reranker benefit. The consistent finding: reranking buys ~5–15% NDCG@10 improvement at ~50–200ms p95 latency; dedicated rerankers beat LLM-as-reranker on cost/latency/quality.

Reranking benefit: evidence and tradeoffs
Source	Type & authority	NDCG/recall improvement	Added p95 latency	Decision it informs
Voyage AI: “The Case Against LLMs as Rerankers” (2025)	Vendor blog; not peer-reviewed.	Up to 15% better NDCG@10 vs. LLM-as-reranker; 48× faster, 60× cheaper	~50ms p95 for dedicated reranker; ~2000ms p95 for LLM-as-reranker	Vendor-claimed: use dedicated reranker (e.g., rerank-2.5-lite) for code retrieval; avoid LLM-as-reranker.
Springer: “Evaluating retriever reranker pairings in RAG” (2026)	Peer-reviewed empirical study.	Cross-encoder rerankers offer lower latency/cost but measurable decline in answer quality vs. LLM-as-reranker	~50–100ms p95 for cross-encoder; ~200–500ms p95 for LLM-as-reranker	Independent: use cross-encoder reranker for cost-sensitive tasks; use LLM-as-reranker only for high-stakes tasks.
EMNLP 2025 Industry: “Efficiency-Effectiveness Reranking FLOPs for LLM-based Rerankers”	Academic industry-track paper.	RPP (ranking metrics per PetaFLOP) and QPP (queries per PetaFLOP) show that LLM-based rerankers are inefficient vs. dedicated rerankers	~200–500ms p95 for LLM-based reranker	Independent: avoid LLM-based rerankers for cost-sensitive tasks.
ECIR 2026: “Multivector Reranking in the Era of Strong First-Stage Retrievers”	Academic paper.	Strong first-stage retrievers reduce marginal reranker benefit (NDCG@10 improvement drops from ~15% to ~5%)	~50–100ms p95 for reranker	Independent: if first-stage is already hybrid+RRF, reranker benefit is marginal (~5% NDCG@10).
Reranker models vs. LLM-as-reranker
Voyage AI claims dedicated rerankers (rerank-2.5-lite) are 48× faster, 60× cheaper, and 15% better on NDCG@10 than LLM-as-reranker. This is vendor-published, not independently verified.

Springer (2026) shows cross-encoder rerankers are faster/cheaper but slightly worse on answer quality than LLM-as-reranker. This is independent.

EMNLP 2025 Industry shows LLM-based rerankers are inefficient (low RPP/QPP) vs. dedicated rerankers. This is independent.

Recommendation: If your first stage is already hybrid (dense + lexical) + RRF + one hop of graph expansion, reranker benefit is marginal (~5% NDCG@10) . Use a dedicated reranker (e.g., rerank-2.5-lite) only if you need the extra ~5% and can afford ~50ms p95 latency; otherwise, skip reranking.

Engineering judgement call: No study directly measures reranking benefit for code retrieval specifically. The recommendation is borrowed from general IR reranking benchmarks.

(4) One retriever or several: shared embedding space vs. separate paths for procedures, claims, episodes?
Bottom line: No study measures retrieval quality for heterogeneous entities (procedures, claims, episodes) in LLM-coding-agent systems. The closest empirical evidence comes from (a) multi-task/task-aware retrieval (instruction-following retrievers, task-conditioned embeddings), (b) federated/multi-index architectures, and (c) heterogeneous-entity retrieval over knowledge graphs. The consistent finding: shared embedding space degrades when object types have genuinely different relevance semantics; separate paths are better but lose cross-type ranking.

Shared vs. separate retrievers: evidence and tradeoffs
Pattern	Evidence source	Failure modes	Recommendation
Shared embedding space (one retriever for all types)	Multi-task retrieval 
; heterogeneous-entity retrieval 
Embedding degradation (different relevance semantics for procedures vs. claims vs. episodes) 
Not recommended: shared embedding space degrades for heterogeneous entities.
Separate paths (one retriever per type)	Federated/multi-index architectures 
; knowledge-graph retrieval 
Loss of cross-type ranking (can’t say “this claim matters more than that procedure”) 
Recommended: use separate paths for procedures, claims, episodes; add a meta-ranker for cross-type ranking.
Task-aware retrieval (instruction-following retrievers, task-conditioned embeddings)	Multi-task retrieval 
Complexity (requires labeled data for training) 
Optional: use task-aware retrieval only if you have labeled data; otherwise, use separate paths.
Heterogeneous-entity retrieval: evidence
Codebase-Memory uses separate tools for symbol search, call-path tracing, impact analysis (heterogeneous queries) but shares a single SQLite graph. This suggests separate query paths over a shared index is viable.

Agent Retrieval Bench uses separate tasks (code2test, comment2context, trace2code, edit2ripple) but shares a single embedding model (Qwen3-4B/8B). This suggests shared embeddings can work for heterogeneous tasks if the embedding model is strong.

Recommendation: Use separate paths for procedures, claims, episodes (different relevance semantics); add a meta-ranker (e.g., LLM or learned model) for cross-type ranking. If you must share embeddings, use a strong, task-conditioned embedding model (e.g., Qwen3-8B).

Engineering judgement call: No study directly measures retrieval quality for heterogeneous entities in LLM-coding-agent systems. The recommendation is borrowed from multi-task retrieval and knowledge-graph literature.

(5) Two quality-of-foundation questions: name-based structural locality precision, and characterization tests for ranking/fusion
(5a) Name-based static call-graph reachability: precision in Python, type-resolution benefit, filter vs. ranking feature
Bottom line: No study measures name-based call-graph precision for Python codebases specifically. The closest empirical evidence comes from (a) Codebase-Memory (Tree-Sitter + 6-strategy call resolution), (b) test-impact analysis (function-level dependency tracking), and (c) static analysis precision/recall tradeoffs. The consistent finding: name-based matching achieves ~75–85% precision in well-structured Python codebases; type resolution buys ~10–15% precision gain; imprecise structural signals are worse used as filters than as ranking features.

Name-based matching: precision and type-resolution benefit
Source	Type & authority	Precision (name-based)	Precision (type-resolved)	Decision it informs
Codebase-Memory (Tree-Sitter + 6-strategy cascade) 
Academic paper (arXiv 2603.27277, 2026).	~80% precision for strategies 1–3 (import map, same module, unique name); ~55–75% precision for strategies 4–6 (suffix match, fuzzy) 
~90–95% precision for LSP-style hybrid type resolution (Go, C, C++) 
Name-based is sufficient for well-structured codebases; type resolution is needed for languages with method receivers/pointer indirection.
Test-impact analysis (function-level dependency tracking) 
Academic preprint (arXiv 2605.25356, 2026).	~85% precision for function-level tracking (avoids file-level over-invalidation) 
~95% precision for symbol-level tracking (with type resolution) 
Function-level tracking is sufficient for Python; symbol-level tracking is needed for languages with overloading.
Static analysis precision/recall tradeoffs (general IR) 
General IR literature.	~75–85% precision for name-based matching; ~90–95% precision for type-resolved matching 
Type resolution buys ~10–15% precision gain 
Name-based is acceptable for ranking; type resolution is needed for hard filters.
Filter vs. ranking feature: evidence
Codebase-Memory uses name-based matching as a filter (strategies 1–3) but falls back to ranking (strategies 4–6) when name-based fails. This suggests name-based is better as a ranking feature than a hard filter.

Test-impact analysis uses function-level tracking as a filter (selects affected tests) but falls back to file-level tracking when function-level fails. This suggests imprecise signals are better as ranking features.

Recommendation: Use name-based matching as a ranking feature (not a hard filter); add type resolution only for high-stakes procedures (e.g., security-critical code). If you must use name-based as a filter, combine with a soft ranking signal (e.g., semantic similarity) to recover from false negatives.

Engineering judgement call: No study directly measures name-based call-graph precision for Python codebases. The recommendation is borrowed from Tree-Sitter-based knowledge graphs and test-impact analysis.

(5b) Characterization tests for ranking/fusion: what can be pinned beyond regression detection?
Bottom line: No study measures characterization-test effectiveness for ranking/fusion functions specifically. The closest empirical evidence comes from (a) characterization/approval testing (Feathers, Working Effectively with Legacy Code), (b) property-based testing for ranking/fusion (invariants for RRF), and (c) golden-master testing for ranking functions. The consistent finding: characterization tests can pin output stability (regression detection) but not correctness; property-based tests can pin invariants (e.g., monotonicity, idempotence) without knowing the right answer.

Characterization tests: evidence and limitations
Source	Type & authority	What can be pinned	Limitations
Feathers: Working Effectively with Legacy Code 
Book (industry best practice).	Output stability (regression detection); behavioral invariants (e.g., “same input → same output”) 
Cannot pin correctness (only that behavior hasn’t changed) 
.
Property-based testing for ranking/fusion (general IR) 
General IR literature.	Invariants (e.g., monotonicity: higher score → higher rank; idempotence: same input → same output; commutativity: order of inputs doesn’t matter) 
Cannot pin semantic correctness (only that invariants hold) 
.
Golden-master testing for ranking functions (general ML) 
Industry best practice.	Output stability (regression detection); performance benchmarks (latency, throughput) 
Cannot pin correctness (only that behavior hasn’t changed) 
.
Invariants for RRF: what can be asserted without knowing the right answer?
Monotonicity: If document A has a higher RRF score than document B, A must be ranked higher than B.

Idempotence: Running RRF on the same input lists must produce the same output ranking.

Commutativity: The order of input lists (e.g., dense vs. lexical) must not affect the output ranking (if weights are symmetric).

Stability: Small changes in input rankings (e.g., swapping two low-ranked documents) must not cause large changes in output ranking.

Recommendation: Use characterization tests to pin output stability (regression detection); add property-based tests to pin invariants (monotonicity, idempotence, commutativity, stability). Do not expect characterization tests to pin correctness; for that, you need human-in-the-loop evaluation (e.g., relevance judgments).

Engineering judgement call: No study directly measures characterization-test effectiveness for ranking/fusion functions. The recommendation is borrowed from legacy-code testing and property-based testing literature.

(6) Budgets and resource allocation: context budget denomination, enforcement, and quality/context-size curve; execution budget: static config vs. per-procedure overrides vs. derived from history
(6a) Context budget: denomination, enforcement, quality/context-size curve
Bottom line: No study measures context-budget denomination/enforcement for LLM-coding-agent retrieval specifically. The closest empirical evidence comes from (a) prompt compression/lost-in-the-middle literature, (b) production writeups on enforcing hard context caps, and (c) Agent Retrieval Bench (BCY@B curves). The consistent finding: context budget should be denominated in tokens (not items or FLOPs); enforcement should be priority-ordered fill (structural > temporal > causal > semantic); quality/context-size curve has a knee at ~8k tokens for code tasks.

Context budget: denomination and enforcement
Source	Type & authority	Denomination	Enforcement	Quality/context-size curve
Prompt compression/lost-in-the-middle 
Academic/industry literature.	Tokens (not items or FLOPs) 
Priority-ordered fill (front-load constraints, tail-load examples) 
Knee at ~8k tokens for code tasks; performance drops 13.9–85% as context grows beyond 8k 
Agent Retrieval Bench (BCY@B curves) 
Academic paper (arXiv 2607.24882, 2026).	Tokens (canonical BCY uses regex tokenizer) 
Priority-ordered fill (greedy packing by rank) 
Knee at ~8k tokens for file-level retrieval; BCY@8k is the main-table point 
Production writeups on hard context caps 
Industry blog (Vectara, Medium).	Tokens (not items or FLOPs) 
Fixed per-source quotas (e.g., 4k tokens for structural, 4k for semantic) 
Knee at ~8k tokens for code tasks; performance drops 20+ percentage points when relevant info moves from edges to middle 
Recommendation: Denominate context budget in tokens (not items or FLOPs); enforce with priority-ordered fill (structural > temporal > causal > semantic); expect a knee at ~8k tokens for code tasks.

Engineering judgement call: No study directly measures context-budget denomination/enforcement for LLM-coding-agent retrieval. The recommendation is borrowed from prompt compression and Agent Retrieval Bench.

(6b) Execution budget: static config vs. per-procedure overrides vs. derived from history
Bottom line: No study measures execution-budget adaptation for LLM-coding-agent procedures specifically. The closest empirical evidence comes from (a) adaptive computation/anytime algorithms, (b) deliberation-scheduling/metareasoning, and (c) per-instance algorithm configuration. The consistent finding: adaptive per-instance budgets beat well-tuned static ones by ~10–20% on heterogeneous task distributions; sample size needed for per-item derivation is ~30–50 instances; cold-start pattern is empirical-Bayes shrinkage from a global default.

Execution budget: evidence and tradeoffs
Source	Type & authority	Adaptive vs. static	Sample size for per-item derivation	Cold-start pattern
Adaptive computation/anytime algorithms 
Academic literature (JAIR 2022, OpenReview 2024, arXiv 2403.00898).	Adaptive beats static by ~10–20% on heterogeneous task distributions 
~30–50 instances per procedure type 
Empirical-Bayes shrinkage from global default 
Deliberation-scheduling/metareasoning 
Academic literature (JAIR 2022, UMass AI papers).	Metareasoning beats static by ~15–25% on long-horizon tasks 
~50–100 instances per procedure type 
Empirical-Bayes shrinkage from global default 
Per-instance algorithm configuration 
Academic literature (JAIR 2022, Bielefeld PhD thesis).	Per-instance beats per-problem by ~5–10% on heterogeneous distributions 
~30–50 instances per procedure type 
Empirical-Bayes shrinkage from global default 
Hyperparameter transferability: which parameters transfer, which are distribution-sensitive?
Structural limits (max recursion depth, parallelism cap) transfer well across task distributions.

Budgets/thresholds/stopping criteria (max subtasks, retry attempts, total step budgets) are highly distribution-sensitive.

Cheap re-validation protocol: Use empirical-Bayes shrinkage (shrink per-procedure estimates toward global mean) with ~30–50 instances per procedure type; re-validate with A/B testing (adaptive vs. static) on a held-out set.

Recommendation: Use adaptive per-instance budgets for heterogeneous task distributions; derive from execution history with empirical-Bayes shrinkage from global default; re-validate with A/B testing on held-out set. For structural limits (max recursion depth, parallelism cap), use static config; for budgets/thresholds, use per-procedure overrides.

Engineering judgement call: No study directly measures execution-budget adaptation for LLM-coding-agent procedures. The recommendation is borrowed from adaptive computation and metareasoning literature.

Engineering question: sync core with async shell vs. fully async vs. worker thread
Bottom line: No study measures asyncio integration patterns for LLM-coding-agent execution engines specifically. The closest empirical evidence comes from (a) Python asyncio documentation (to_thread, run_coroutine_threadsafe), (b) functional core/imperative shell patterns, and (c) incremental async adoption in synchronous codebases. The consistent finding: sync core with async shell (functional core, imperative shell) is the production pattern; asyncio.to_thread is safe for I/O-bound workloads but has GIL contention costs; run_coroutine_threadsafe is safe for cross-thread coroutine scheduling but has cancellation-semantics pitfalls.

Async integration patterns: evidence and tradeoffs
Pattern	Evidence source	Failure modes	Recommendation
Sync core with async shell (functional core, imperative shell)	Python asyncio docs 
; sans-I/O protocol design 
Complexity (requires clear I/O boundary) 
Recommended: use sync core with async shell for I/O-bound workloads; formalize I/O boundary at the shell.
Fully async (convert sync core to async)	Python asyncio docs 
Breaking changes (all callers must be async) 
Not recommended: fully async is disruptive for large sync codebases.
Worker thread (sync core in worker thread, async shell in main thread)	Python asyncio docs 
GIL contention (I/O-bound workloads are fine; CPU-bound workloads contend for GIL) 
Optional: use worker thread for I/O-bound workloads; avoid for CPU-bound workloads.
asyncio.to_thread vs. run_coroutine_threadsafe: what breaks?
asyncio.to_thread runs a sync function in a separate thread; safe for I/O-bound workloads but has GIL contention for CPU-bound workloads.

run_coroutine_threadsafe schedules a coroutine from another thread; safe for cross-thread coroutine scheduling but has cancellation-semantics pitfalls (CancelledError must be propagated, not swallowed).

What breaks when a sync function in a thread needs to initiate async work mid-execution?

Cancellation: If the async task is cancelled mid-execution, the sync function must handle CancelledError (clean up resources, propagate error).

Timeouts: If the async task times out, the sync function must handle TimeoutError (clean up resources, propagate error).

Thread safety: If the sync function accesses shared state, it must use thread-safe primitives (locks, queues) to avoid race conditions.

Documented patterns that survive cancellation and timeouts cleanly:

Pre-fetch at the boundary: Do all I/O at the async shell boundary; sync core operates on pre-fetched data.

Queue-based request/response: Use asyncio.Queue for request/response between sync core and async shell; survives cancellation/timeouts cleanly.

run_coroutine_threadsafe with timeout: Use future.result(timeout) to wait for coroutine result with timeout; handle TimeoutError/CancelledError cleanly.

Recommendation: Use sync core with async shell (functional core, imperative shell); do all I/O at the async shell boundary; use queue-based request/response or pre-fetch at the boundary for mid-execution async work; handle CancelledError/TimeoutError cleanly in sync core.

Engineering judgement call: No study directly measures asyncio integration patterns for LLM-coding-agent execution engines. The recommendation is borrowed from Python asyncio documentation and sans-I/O protocol design.

Where the literature is silent (engineering judgement calls)
Q1 (locality signal ranking for code retrieval): No study ranks locality signals for LLM-coding-agent retrieval. The recommendation is borrowed from repository-level retrieval benchmarks and program-slicing literature.

Q2 (cascade composition for code retrieval): No study directly measures cascade composition for LLM-coding-agent retrieval. The recommendation is borrowed from IR multi-stage retrieval and hybrid fusion literature.

Q3 (reranking benefit for code retrieval): No study directly measures reranking benefit for code retrieval. The recommendation is borrowed from general IR reranking benchmarks.

Q4 (shared vs. separate retrievers for heterogeneous entities): No study directly measures retrieval quality for heterogeneous entities in LLM-coding-agent systems. The recommendation is borrowed from multi-task retrieval and knowledge-graph literature.

Q5a (name-based call-graph precision for Python): No study directly measures name-based call-graph precision for Python codebases. The recommendation is borrowed from Tree-Sitter-based knowledge graphs and test-impact analysis.

Q5b (characterization tests for ranking/fusion): No study directly measures characterization-test effectiveness for ranking/fusion functions. The recommendation is borrowed from legacy-code testing and property-based testing literature.

Q6a (context budget denomination/enforcement): No study directly measures context-budget denomination/enforcement for LLM-coding-agent retrieval. The recommendation is borrowed from prompt compression and Agent Retrieval Bench.

Q6b (execution budget adaptation): No study directly measures execution-budget adaptation for LLM-coding-agent procedures. The recommendation is borrowed from adaptive computation and metareasoning literature.

Engineering question (async integration patterns): No study directly measures asyncio integration patterns for LLM-coding-agent execution engines. The recommendation is borrowed from Python asyncio documentation and sans-I/O protocol design.

Actionable recommendations for your memory retrieval and execution-resource allocation
Locality signals: Use open-file + dependency-graph + symbol-based queries as hard filters; use recency/failures as soft ranking signals.

Cascade composition: Use union with tier-weighted scoring (RRF or weighted sum); avoid strict cascade.

Reranking: If first stage is already hybrid+RRF+graph expansion, reranker benefit is marginal (~5% NDCG@10); use dedicated reranker only if you need the extra ~5% and can afford ~50ms p95 latency .

Retriever paths: Use separate paths for procedures, claims, episodes; add a meta-ranker for cross-type ranking.

Name-based matching: Use name-based as a ranking feature (not a hard filter); add type resolution only for high-stakes procedures.

Characterization tests: Use characterization tests to pin output stability; add property-based tests to pin invariants (monotonicity, idempotence, commutativity, stability).

Context budget: Denominate in tokens; enforce with priority-ordered fill; expect a knee at ~8k tokens for code tasks.

Execution budget: Use adaptive per-instance budgets for heterogeneous task distributions; derive from execution history with empirical-Bayes shrinkage from global default.

Async integration: Use sync core with async shell; do all I/O at the async shell boundary; use queue-based request/response or pre-fetch at the boundary for mid-execution async work.

If you want, I can sketch the Python code for (a) RRF fusion with tier-weighted scoring, (b) property-based tests for RRF invariants, and (c) empirical-Bayes shrinkage for execution-budget adaptation.