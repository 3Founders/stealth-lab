# Reasons & Citations

Verification record backing every paper claim in `RESEARCH_INTEGRATION_PLAN.md`,
plus prior-art and market citations used in planning docs. Verified Aug 23–24,
2026 via arXiv abstract fetches, Semantic Scholar, ACL Anthology, and websearch.
Verdicts: ✅ verified · ⚠️ verified-with-caveats · ➕ discovered during research.
Findings live here so `RESEARCH_INTEGRATION_PLAN.md` stays claim-only.

---

## Theme A — Memory as RL component

**MolMem — arXiv:2604.12237 — ✅ strong**
Wang, Wen, Pandey, Liu, Ding. **ACL 2026 Main** (peer-reviewed; anthology
2026.acl-long.2024, pp 43694–43712). Dual-memory confirmed exactly as planned:
Static Exemplar Memory (cold-start grounding via similarity retrieval) +
Evolving Skill Memory (distills successful trajectories into reusable textual
strategies in a skill bank); dense step-wise rewards; memory queried only on
optimization plateau. Results: 90% SR single-property (1.5× best baseline),
52% multi-property @ 500 oracle calls. Code: github.com/REAL-Lab-NU/MolMem.
Caveat we rely on knowing: domain is molecular optimization — the
`method_library.py` mapping is architectural transfer, not direct reuse.

**MemRL — arXiv:2601.03192 — ➕ adjacent discovery**
Utility-gated episodic memory: learned scalar Q-values decide which stored
experiences are retrieved; non-parametric (frozen LLM), stability/plasticity
decoupled via runtime RL on memory usage. Cross-domain memory banks remained
stable behind a semantic filter. Closest prior art found for `_method_score`
as a value function — arguably closer than ADRS. Also surfaced the 2026 wave:
SkillOS, Memento-Skills, MemGen, MemEvolve, MemSkill, Dynamic Procedural
Memory (Apr 2026) — procedural-memory learning is crowded; none of them add
evidence/verification layers.

*Not yet directly verified:* ADRS (2608.03223), SDAR (2605.15155).

## Theme B — Layered world models

**GATS — arXiv:2607.08894 — ⚠️ verified with caveats**
Williams & Nowicki. Three-layer world model confirmed verbatim: L1 exact
symbolic action match, L2 statistics learned from execution logs, L3 LLM
fallback; zero LLM calls during planning (vs 37/task for LATS); 100% success
vs LATS 88.9% / ReAct 23.9% on their 12-scenario stress set; deterministic,
zero-variance plans. Caveats: two-author short paper (17KB submission),
synthetic tasks plus self-built stress benchmark, no peer review yet.
Direction supports the three-tier lookup repo mapping; headline numbers are
not cross-benchmark.

**WorldEvolver — arXiv:2606.30639 — ✅ verified**
Self-evolving world model with frozen LLM, deployment-time context revision
only. Episodic memory (concrete transitions, retrieval-based simulation) +
semantic memory (heuristic rules mined from prediction–observation mismatches)
+ selective foresight (confidence gate drops low-confidence predictions before
they enter agent context). +2.24 pts ALFWorld / +3.33 ScienceWorld on
Gemma-4-26B-A4B. Explicitly framed on Tulving's episodic/semantic split — the
same split as spec v4's Episode vs Observation/Claim layers.

**EnvACE — arXiv:2608.06197 — ✅ verified with caveat**
World rehearsal: one policy alternates acting/environment roles under
role-wise GRPO; test-time private rehearsal N=2 lifts overall 36.7→40.9%,
τ²-bench average 31.4→38.0; evaluated on BFCL-v4, τ²-Bench, VitaBench,
FinMCP-Bench; code: github.com/Within-yao/EnvACE. Caveat (third-party
analysis): rehearsed responses are entirely self-generated and the paper never
quantifies rehearsal-vs-real fidelity; N=3 regressed (context-length cap).
Our bridge hook is stronger than the paper's design because the substrate
holds *real recorded transitions* to ground rehearsal against.

## Theme C — Scope-aware sharing

**FedWorld — arXiv:2608.01561 — ✅ strong**
Scope-aware federated world-model protocol exchanging normalized abstract
transition rules. Supporting-vs-contradicting evidence across clients
classifies each rule shared / cluster-specific / private / unresolved;
local-first compiler admits only compatible-scope rules for uncovered cases.
Theorem 1 formalizes when scope filtering wins (prevented overwrites >
withheld transfers). τ-bench: strict-conflict naive union loses −13.8 EM
relative to local-only; FedWorld recovers +16.6 EM and beats local-only by
+2.8; state regressions −44.5%. ALFWorld similar (+25.0 recovery). Code:
github.com/Hik289/fed_world_base. Note: its classification mechanism is
independently our Claim-evidence model applied to transition rules — validates
the design; our differentiation remains resolution-by-execution rather than
peer vote.

## Theme D — Trajectory hygiene & credit assignment

**CLEANER — arXiv:2601.15141 — ✅ verified**
Similarity-Aware Adaptive Rollback (SAAR) retrospectively replaces failed tool
calls with successful self-corrections during collection, producing
self-purified trajectories. Gains: +6/+3/+5 pts AIME24/GPQA/LiveCodeBench;
matches SOTA with one-third training steps. Code:
github.com/Tianshi-Xu/Open-CLEANER; under OpenReview review. Useful negative
result in appendix: using SAAR-identified errors as online-DPO negatives did
not improve over purification alone.

**TRIAL — arXiv:2608.07371 — ✅ strong**
Trajectory-relative hindsight distillation: turn-aligned outcome view of each
decision's realized consequence; signed log-probability gap sets token-level
supervision direction/strength; allocation multipliers normalized (mean one).
Beats GRPO in all 8 backbone×environment×metric combinations; WebShop Qwen3-1.7B
SR 56.4→75.2%, task score 78.7→85.7%. Controlled study isolates the
trajectory-relative profile as the active ingredient (vs uniform and permuted
profiles). Code: github.com/Chihaya-Anon-chan/TRIAL.

**EFCA — arXiv:2608.08255 — ✅ verified**
Environmental Feedback-based Credit Assignment: multi-timescale credit
combining long-term outcome signal with two environment-grounded process
signals — short-term immediate-action feedback and medium-term state-history
signal surfacing ineffective recent patterns. ALFWorld/WebShop improvements
confirmed via independent coverage.

**ASSAY — arXiv:2606.15390 — ➕ discovered during research, high priority**
"Not All Skills Help": randomized skill-masking protocol estimates per-skill
causal effect (difference-in-means estimator) on a dev set; offline library
restructuring plus per-task causal masking at inference. AppWorld
test-challenge: 69.3% task-goal completion with DeepSeek-V3 — new published
SOTA including weight-tuned methods; τ-bench retail +8.7% relative for
GPT-4.1, passing o4-mini/o1/GPT-4.5 without weight changes. Dominant-gain
ablation: per-task masking. Directly composable with our substrate —
`record_execution_outcome()` streams are the masking-trial data ASSAY needs.
Empirically proves the founding thesis: unverified skills measurably hurt.

**Adjacent credit-assignment wave (context, not separately verified):**
HCAPO (hindsight credit assignment), HISR (segmental process rewards; 83.6%
ALFWorld), iStar implicit step rewards, ARPO, GiGPO. Segment/turn-level
environment-grounded credit beating sparse outcome rewards is now consensus —
the algorithm is commodity; typed outcome streams are the differentiator.

## Rigor-loop citations (claim/extraction verification design)

- **AblationBench** — arXiv:2507.08038: best LM recovers only ~38% of human-designed ablations → machine intuition may schedule tests but must not design or judge them alone.
- **AbGen / AbGen-Eval** — arXiv:2507.13300, ACL 2025 (1,500 expert-annotated examples, 807 NLP papers): automated evaluation of ablation designs correlates poorly with human assessment → verdicts must be grounded in execution outcomes.
- **LLM-as-judge reliability**: cross-family juries reduce bias 30–40%; position-swap and label neutralization standard mitigations; 200–500 human-labeled cases validate a judge at ≥0.85 agreement with quarterly recalibration; small distilled judges (MiniCheck/Lynx class) give ~$0.001 entailment checks at 100–200 ms for bulk screening. Self-correction helps only with external grounding (execution results, retrieved documents) — CRITIC line of work.
- **Claim Verification survey** — ACL 2026 SRW (arXiv-listed anthology 2026.acl-srw.2): atomic decomposition + retrieval grounding + entailment checking is the standard pipeline shape; VISTA extends to per-turn sequential verification.
- **DSPy Assert/Suggest** — hard/soft constraint tiers in pipelines; up to +164% rule adherence.

## Prior art & market citations (planning docs)

- Agent-memory market ~$6.3B (2025) → $28.5B (2030), 35% CAGR — AgentMarketCap, Apr 2026.
- Enterprise KG market $1.84B (2026) → $4.37B (2030), 24% CAGR — TBRC; graph DB $4.97B → $11.47B, 23% CAGR — TBRC/Technavio 2026; GraphRAG named top adoption driver.
- Vendor land-grab: Mem0 $24M Series A + exclusive AWS AgentSDK memory provider; Letta $10M seed; Microsoft/Oracle/AWS shipped native memory — consolidation risk documented across multiple landscape surveys.
- Zep/Graphiti — arXiv:2501.13956: bi-temporal edges (`invalid_at`, no deletion), hybrid BM25+vector+graph retrieval; DMR 94.8%, LongMemEval +18.5%, latency −90%.
- Mem0 — arXiv:2504.19413 (ECAI 2025): selective extraction pipeline; +26% relative accuracy vs OpenAI baseline, 91% p95 latency reduction, >90% token savings on LOCOMO.
- ACE — arXiv:2510.04618 (ICLR 2026): evolving playbooks with incremental delta updates; +10.6% agents, +8.6% finance; AppWorld leaderboard parity with IBM-CUGA using open weights; identifies brevity bias and context collapse failure modes. Dynamic Cheatsheet — arXiv:2504.07952.
- OEP poisoning — arXiv:2605.18930: self-evolving agents attackable via locally-correct but non-transferable experiences — the security argument for evidence-gated procedure libraries.
- AWM — arXiv:2409.07429 (ICML 2025): workflow induction from trajectories, Mind2Web SR 2.0→4.8; OpenReview critiques (granularity control unspecified, "repackaging" concern) are precisely what our validators/slot-binders address.
- ClawHavoc supply-chain attack (Feb 2026): 824 malicious skills ≈ 20–26% of registry, 9,000+ compromised installs; Palo Alto Unit42 disclosure — the demonstrated trust vacuum behind M3's marketplace wedge.
- τ-Knowledge — arXiv:2603.04370 + Sierra blog + taubench.com: best frontier ~25.5% pass¹ standardized (GPT-5.2 high); GPT-5.5 xhigh leads at 37.4%; gold-documents ceiling ~39.7% (Claude-4.5-Opus) — reasoning, not retrieval, is the residual bottleneck; paired-bootstrap significance protocol is the comparability standard.
- Corpus economics: S2ORC-Full 14.5M full-text papers (ODC-BY, HuggingFace, ~1.4TB Parquet); Stack Overflow dump ~12M accepted answers (CC BY-SA, quarterly); GHALogs 116K workflows / 513K runs / 2.3M logged steps with full logs (Zenodo, 143GB).
- Scale references: Google KG 500B facts/5B entities (2020); Wikidata ~115M items/~1.7B statements with documented query-service strain; Meituan LDBC benchmark (2.6B entities/17.7B edges) selecting NebulaGraph after testing 30 databases; NebulaGraph trillion-edge production claims (Snapchat/Binance), p99 <50ms, 3B edges/hour bulk load.
