# Routing by goal clusters and micro-benchmarks: research and build plan (2026-11-08)

Question from the founder: route the way Avengers-Pro does (cluster, per-cluster model scores), but over abstract goals, with micro-benchmarks. This is research plus a build plan. Nothing here is built.

Confidence tags: **P** paper abstract or section read, **S** secondary summary or search snippet, **R** repo file header read, **U** unverified. I read abstracts and selected sections, not full papers. Every number from a paper is the authors' claim, not replicated by me. Sources found with Exa and OpenAlex searches on 2026-11-08.

## 1. What the evidence says

### 1.1 Avengers-Pro itself
- Method (P, arXiv 2508.12631 method section): embed queries, cluster, per cluster and model measure accuracy and total cost, min-max normalise inside the cluster, `score = a*perf_norm + (1-a)*(1-cost_norm)`, route to the model with the highest score summed over the top-k nearest clusters. No training.
- Reported (S, repo README): +7% accuracy over the strongest single model (GPT-5-medium) and about -27% cost at the best operating point, on 6 benchmarks and 8 models. The authors' numbers.
- Setup details (S): Qwen3-Embedding-8B; about 60 clusters in one summary; another paper used 200 clusters with k-means for its Avengers-Pro baseline.

### 1.2 Independent re-evaluation: LLMRouterBench (ACL 2026 Findings, arXiv 2601.07206)
- 400K+ instances, 21 datasets, 33 models, 10 routing methods (P).
- Model complementarity is real (the premise holds), but "many routing methods exhibit similar performance under unified evaluation, and several recent approaches, including commercial routers, fail to reliably outperform a simple baseline" (P, abstract).
- A large gap to the Oracle remains, "driven primarily by persistent model-recall failures": the router fails to pick the model that would have been right (P).
- **The embedding backbone has limited impact; larger ensembles show diminishing returns against careful model curation** (P).
- Avengers-Pro achieved a Pareto-optimal performance-cost trade-off in that benchmark's performance-cost setting (P, section heading in the HTML). So clustering is a strong baseline, not a dead end.
- Lesson: always compare with the best single model and a size-based or fixed-policy baseline, on held-out data (S, [Proof of Tech](https://proofoftech.org/blog/routers-lose-to-the-baseline/), [Nadir](https://getnadir.com/blog/llmrouterbench-acl-2026-most-routers-fail-baseline/)).

### 1.3 The micro-benchmark idea has a published form: UniRoute (ICLR 2026, arXiv 2502.08773)
- Represent any LLM by its average errors on K pre-defined prompt clusters, measured on a **small validation set of representative prompts**; represent a prompt by its cluster membership; pick the model minimising error plus a cost penalty (P/S).
- It handles **unseen models at test time** and gives an excess-risk bound (P, abstract), tested on more than 30 unseen LLMs.
- This is exactly "cluster the goals, micro-benchmark each model per cluster". It also names the central difficulty: the quality of the per-cluster error vector depends on the size of the validation set.

### 1.4 Small-sample evaluation is a solved problem class: item response theory
- tinyBenchmarks (arXiv 2402.14992, P/S): 100 examples per scenario chosen by clustering IRT embeddings, with an IRT-based estimator correcting the score; reproduces full-benchmark results on several benchmarks.
- IRT-Router (ACL 2025, P/S): routing with IRT, with an online warm-up by semantic similarity; interpretable model ability and query difficulty; 20 LLMs, 12 datasets.
- "Lost in Benchmarks" (AAAI 2026, P) and "Fluid Language Model Benchmarking" (arXiv 2509.11106, S) use IRT to pick informative items. Takeaway: not all items are equally informative; choose anchors that separate models, and estimate with a model, not a raw average.

### 1.5 Cold start and drift
- ParetoBandit (arXiv 2604.00136, P): cost-aware contextual bandit with an online primal-dual **budget pacer** that holds a per-request dollar ceiling, **geometric forgetting** to track price and quality shifts, a hot-swap model registry, and a new model integrated in about 142 steps (on 1,824 prompts, three models: small evidence).
- "Generalising LLM Routing using Past Performance Retrieval: A Few-Shot Router is Sufficient" (S): retrieving past performance of neighbours generalises to new models.
- LLM Bandit (S): new models added by fitting an IRT-style identity vector from few samples.

### 1.6 Embeddings are a weak point out of distribution
- "Embedding Geometry Shapes LLM Routing" (arXiv 2610.19198, P): semantic-embedding routers do well in distribution and fail on unseen tasks, because the features that set a prompt's neighbours are an unreliable proxy for which LLM is best. Using a **deep-layer hidden state of a 7B model** with kNN generalises better and beat every semantic-embedding baseline they tested (authors' claim, on RouterBench).
- ProxRouter (S): proximity weighting for outlier queries.
- This pairs with LLMRouterBench's finding that the backbone matters little in distribution: the choice matters mostly out of distribution.

### 1.7 Agentic tasks: the prompt alone is not enough
- SWE-Router (arXiv 2607.00053, P): task-description routers have a Bayes-error floor in agentic settings (a typo and a multi-module refactor can read alike). Let a cheap model explore a few turns, read the partial trajectory, then continue cheaply or escalate; a theorem says conditioning on the partial trajectory never hurts.
- AgentRouter (arXiv 2609.22951, P): per-step routing with a 12M-parameter classifier; reports 72% cost reduction at 97.3% of frontier quality, and that RouteLLM and FrugalGPT applied per step get far less (authors' claims, one paper, treat with caution).
- TwinRouterBench (S) and LiteLLM's subtask routing (S, 46% less cost, vendor claim) point the same way.
- Router-R1 (S): routing as multi-round reasoning trained by RL.

### 1.8 Guarantees
- Conformal routing papers (S): "Conformal LLM Routing with Distribution-Free Safety Guarantees" (ACL SRW 2026), Conformal Cascade (arXiv 2607.25018), CopRoute, RACER. They bound how often a cheaper model fails. Useful as a safety layer; they need calibration data.
- FrugalGPT-style cascades (P): try cheap first, accept if a scorer says reliable, otherwise escalate. kell's ladder already generalises this.

### 1.9 Clustering caveats
- k-means on embeddings is unstable across restarts (S, clawRxiv permutation test) and clustering has no ground truth (S). The number of clusters and their stability must be measured, not assumed. Option: stability-based selection of K (S, Lord et al.).

## 2. What kell already has (R, headers only; not read in full)

- `backend/app/routing/` is a **hierarchical Bayesian router**, not a lookup table. `fit.py`: joint posterior with NUTS or normalising-flow VI; local refit per goal. `predict.py`: decision-time maths on aligned posterior draws; a new goal or model gets its draws from the model's prior. `ladder.py`: exact ladder of model attempts with the runtime check's false-accept and false-reject rates, a reliability chance constraint, Thompson sampling, and Bayes-optimal continuation after each failed attempt. `cards.py`: priors for unseen models from metadata (family, size, price, date). `probe.py`: a short fixed coding probe against LLMRouterBench anchors, regressing ability on the probe score. `semantic_codes.py`: a two-level code tree over procedure embeddings, used as parents in the goal hierarchy. `plan.py`: the agent-facing plan and per-attempt report flow.
- So kell already has hierarchical pooling (which is what small micro-benchmarks need), model cards, a probe, check error rates and cost-aware choice. Avengers-Pro's table is a simpler special case of that.

## 3. Verdict

1. The cluster-table idea is a **sound baseline**, and the best-supported form of "micro-benchmark per cluster" is UniRoute plus IRT-style estimation.
2. Kell should **not replace** its Bayesian router with an Avengers table. It should (a) add Avengers-Pro and UniRoute as **baselines** to beat, and (b) feed **goal clusters** and **micro-benchmark results** into the existing hierarchy as parents and evidence.
3. The literature's strongest warnings map to concrete design rules: pool small samples (done by the hierarchy), measure against the best single model, expect OOD failures from semantic embeddings, and use trajectory information for agentic tasks.

## 4. Build plan

Principle: every step has a number as its exit criterion (repo rule 5), and nothing changes production routing until an offline replay beats the baselines.

**Step A. Offline replay harness.** Replay recorded outcomes and LLMRouterBench data through candidate routers on held-out goals: (1) best single model, (2) size-based fixed policy, (3) Avengers-Pro as published, (4) UniRoute cluster version, (5) the current ladder. Metrics: pass rate, expected cost, regret against the Oracle, and model-recall failures. Exit: all five run on one held-out split with confidence intervals. Where: `experiments/harness/` (live measurement rig).

**Step B. Goal clustering.** Build the goal-abstraction step (strip project specifics, keep what is to be done and how success is checked), embed, cluster, and reuse or extend the code tree. Measure stability (adjusted Rand index across restarts) and choose K by stability. Compare three embeddings: a standard text embedding, the current one, and a deep-layer hidden-state representation (arXiv 2610.19198) on out-of-distribution held-out goals. Exit: a K and an embedding chosen by held-out routing regret, not by silhouette alone.

**Step C. Micro-benchmark anchors.** Per cluster, pick 5 to 10 graded tasks with machine checks, chosen for informativeness (IRT difficulty and discrimination, as in tinyBenchmarks), not at random. Probe candidate models on them (extend `probe.py`). Exit: for models with full results, the micro-benchmark estimate of each cluster's accuracy correlates with the full result above a threshold set in advance; report the correlation.

**Step D. Estimation.** Treat the micro-benchmark results as evidence in the existing joint model: cluster-level ability with pooling toward the parent cluster and the model's global ability, credible intervals, IRT-style estimator for the small samples. Decision rule: the cheapest model whose lower credible bound clears the pass threshold (the ladder already does this with its chance constraint). Exit: calibration check (simulation-based calibration exists in `fit.py`) on the new evidence type.

**Step E. Agentic extension.** Start cheap, read the partial trajectory, then continue or escalate (SWE-Router idea), as an extra observation in the ladder rather than a separate router. Per-step routing only after the per-task version is measured. Exit: on a multi-turn coding set, cost saved at a fixed pass rate against the per-task router.

**Step F. Operations.** Budget pacing (ParetoBandit idea) as a spend ceiling per account; forgetting (down-weighting old outcomes) so price and quality drift are tracked; a hot-swap path for new models using the card prior then probe then real outcomes. Conformal bound as an optional safety layer once calibration data exists. Exit: a model added mid-run reaches its steady routing share within a stated number of requests in a simulation.

**Step G. Tool retrieval is separate.** A goal with a published tool returns that tool by exact then semantic goal match (see `docs/tool_marketplace_design.md`). The cluster router chooses the model only when no tool matches.

## 5. Risks

- **Small-sample noise** dominates the micro-benchmark idea; min-max scaling amplifies it. Hence the pooling and the lower-bound rule.
- **Contamination:** anchors and public benchmarks may be in a model's training data. Keep a private held-out set; report results on it separately.
- **Distribution shift:** semantic clusters can misplace unseen goals. Add an "unknown goal" fallback: when no cluster is close, use the global posterior and a stronger default.
- **Cost accounting:** cost must include retries, tool calls and checks (the ladder includes check cost). A table of per-call prices misleads.
- **Evidence of benefit is not ours yet.** All published gains are on public benchmarks. The first claim we can make is the replay result on our own outcomes.
- **Gaming:** an open tool marketplace makes outcome records worth manipulating. Weight outcomes by verified checks, not self-reports.

## 6. Open questions for the owners

1. How many verified goals with machine checks exist now (decides which steps are feasible)? A read-only count would answer it.
2. Which candidate models are in scope (BYOK keys, the managed open-model tier)?
3. Is a hidden-state embedding acceptable (it needs a 7B forward pass per goal; cost and hosting)?
4. Budget for the probes (a few hundred graded runs per new model).

## Sources

- Avengers-Pro: https://arxiv.org/abs/2508.12631 , https://github.com/ZhangYiqun018/AvengersPro
- LLMRouterBench: https://arxiv.org/abs/2601.07206 , https://aclanthology.org/2026.findings-acl.1881/
- UniRoute: https://arxiv.org/abs/2502.08773
- tinyBenchmarks: https://arxiv.org/abs/2402.14992
- IRT-Router: https://aclanthology.org/2025.acl-long.761.pdf
- ParetoBandit: https://arxiv.org/abs/2604.00136
- Embedding Geometry Shapes LLM Routing: https://arxiv.org/html/2610.19198
- SWE-Router: https://arxiv.org/html/2607.00053 ; AgentRouter: https://arxiv.org/abs/2609.22951 ; TwinRouterBench: https://arxiv.org/html/2605.18859v3
- RouteLLM: https://arxiv.org/html/2406.18665v3 ; FrugalGPT: https://arxiv.org/abs/2305.05176 ; Router-R1: https://arxiv.org/html/2506.09033
- Conformal routing: https://aclanthology.org/2026.acl-srw.70.pdf , https://arxiv.org/abs/2607.25018
- Routing survey: https://aclanthology.org/2026.findings-emnlp.2072.pdf
