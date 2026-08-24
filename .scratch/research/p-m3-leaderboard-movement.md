# P-M3 — τ-bench / τ²-bench leaderboard movement (2026)

**Ticket:** RESEARCH_INTEGRATION_PLAN.md P-M3 `[web]` · **Date:** 2026-08-25 · **Lane:** research
**Method:** web search across official board + third-party trackers. Exa unavailable in this worktree; built-in search used.

## Headline findings

### 1. The official core board is model-driven, not scaffold-driven
Official taubench.com core τ²-bench top rows are raw frontier models, no memory scaffolds visible: Qwen3.5-397B-A17B 87.9%, Gemini 3.0 Pro 85.4%, Claude Opus 4.5 85.3% pass^1 — <http://taubench.com/>. Sierra has extended the family instead of re-scoring scaffolds: τ-voice and τ-knowledge both launched March 2026 (same page; voice paper <https://arxiv.org/abs/2603.13686>, knowledge paper <https://arxiv.org/abs/2603.04370>).

### 2. The telecom dual-control split is saturating
Third-party tracker (Artificial Analysis snapshot mirrored at <https://evals.report/benchmarks/tau2-bench>): Claude Opus 4.6 99.3%, GPT-5.2 98.7%, GLM-5.2 99.1% pass^1 on telecom (unverified vendor rows, Apr–Feb 2026 dates). A year ago this split sat near o3 58.2% / GPT-OSS-120B 65.8% (same table). Movement from ~60→~99% in ~12 months = benchmark exhaustion; it can no longer differentiate agent *systems*, only model families.

### 3. Where movement is still possible: knowledge-heavy surfaces
- τ³-Banking (τ-Knowledge framework) official board tops out at **55.2%** (Qwen 3.8 Max), Opus 5 at 48.7% — <http://taubench.com/leaderboard?benchmark=knowledge>.
- That's consistent with the τ-Knowledge paper's March finding that frontier models with high reasoning budgets averaged only ~25.5% pass^1 (<https://arxiv.org/abs/2603.04370>). Roughly a doubling in five months, still far from saturation.

### 4. Tracker hygiene caveat (matters for our harness)
BenchLM explicitly refuses cross-provider ranking: "144 sourced rows … do not use one guaranteed-common harness"; user-sim model, domain slice, trial counts, and pass^k definitions differ per row — <https://benchlm.ai/benchmarks/tau2-bench>. Steel.dev's methodology notes agree: pass^k punishes intermittent correctness and rows aren't comparable without matched simulators/trial counts — <https://leaderboard.steel.dev/leaderboards/tau-bench>.

## Answer to the ticket question ("which scaffolds improved most / memory components")

**No memory-scaffold entries are moving the official leaderboards.** Observed movement is attributable to base-model releases (Qwen3.x, Gemini 3.x, Claude Opus 4.x/5, GPT-5.x, Grok 4.x) per the dated rows above. Academic scaffolds that cite τ-family gains (EnvACE <https://arxiv.org/html/2608.06197v1>: τ² avg 36.7 at 8B scale; FedWorld <https://arxiv.org/html/2608.01561v1>: task success 0.512→0.624 under fixed controller) operate at small-model scale where headroom exists, not at the saturated frontier. This is actually good for us: the differentiating surface has migrated to exactly the unsaturated, knowledge-and-reliability-dominated regime (τ³-Banking ≤55%) where verified procedural memory and evidence gating are load-bearing.

## Board deltas

1. MEASURE lane: freeze harness baselines on **pass^k with disclosed user-simulator and trial count**; treat any single-source τ² number as non-comparable (cite BenchLM policy).
2. Positioning: don't claim leaderboard wins vs frontier models; claim reliability deltas under fixed controllers (FedWorld/EnvACE pattern) on the still-open knowledge regime.
3. τ²-telecom should be treated as a sanity check, not a headline metric, by the time we ship.
