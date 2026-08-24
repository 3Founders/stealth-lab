# Research Integration Plan — Papers → StealthLab

Compiled Aug 23, 2026. Workflow: websearch (market/vendor/pain-point) + arXiv/S2/OpenAlex via webfetch → this synthesis.
Thesis: nobody has combined **verified procedural memory** + **layered world models** + **memory-aware RL credit assignment** — the pieces published separately within the last 60 days. Assembling them is product roadmap, paper, and moat at once.

---

## Theme A — Make memory an RL component, not a lookup


| Paper                     | Takeaway                                                                                                                                  | Repo target                                                                                                           |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| MolMem (arXiv:2604.12237) | Dual-memory in RL loop: static exemplars (cold-start grounding) + evolving skill memory distilled from successes; dense step-wise rewards | `method_library.py`: add evolving layer fed by trajectory outcomes                                                    |
| ADRS (arXiv:2608.03223)   | Rescore trajectory tokens conditioned on task-matched procedural skills; return-associated TVA gate                                       | `_method_score` (`htn_agent.py:1487`, currently `NotImplementedError`): implement as return-associated value function |
| SDAR (arXiv:2605.15155)   | Skill-conditioned guidance as *gated* auxiliary objective on GRPO backbone; naive versions destabilize                                    | Gate retrieval-augmented scoring so imperfect retrievals can't poison training                                        |




## Theme B — Layered world models replace LLM-in-the-loop


| Paper                           | Takeaway                                                                                                                                        | Repo target                                                                                                             |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| GATS (arXiv:2607.08894)         | L1 exact symbolic match → L2 stats from execution logs → L3 LLM fallback ⇒ zero-LLM planning, 100% vs ReAct 23.9%                               | `retrieval_mixins.py`: three-tier lookup before generative fallback                                                     |
| WorldEvolver (arXiv:2606.30639) | Episodic memory (real transitions) + semantic memory (rules mined from prediction–observation mismatch) + selective foresight confidence gating | Provenance/success-criteria records double as the mismatch log; gate predictions by confidence before context injection |
| EnvACE (arXiv:2608.06197)       | "World rehearsal": agent plays own environment; test-time private rehearsal pre-execution; validated on τ²-bench                                | `stealthlab_bridge.py`: rehearsal hook — predict DB effect, then commit                                                 |




## Theme C — Scope-aware sharing = contestation layer validated

- FedWorld (arXiv:2608.01561): federates abstract transition rules across agents; classifies each **shared / cluster-specific / private / unresolved** via supporting-vs-contradicting evidence; reduces negative transfer on τ-bench.
- Our differentiation: resolution by *execution* (deterministic benchmarks), not peer vote.
- Product mapping: the evidence registry's scoping semantics = FedWorld's classification, hardened with lifecycle states (active/superseded/retired).



## Theme D — Trajectory hygiene before distillation


| Paper                       | Takeaway                                                                                                                | Repo target                                                                                              |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| CLEANER (arXiv:2601.15141)  | Retrospectively replace failures with successful self-corrections during collection; ⅓ training steps for same accuracy | Procedure-extraction pipeline: purify traces first (fixes RIGHT_FILE_wrong_fix pollution of the library) |
| TRIAL (arXiv:2608.07371)    | Hindsight-conditioned turn-aligned scoring; WebShop 56.4→75.2% on Qwen3-1.7B                                            | Credit assignment for procedure updates                                                                  |
| EFCA (arXiv:2608.08255)     | Multi-timescale credit: immediate-effect + state-history signals from environment feedback                              | Map onto StepTracker event streams                                                                       |
| ActFocus (arXiv:2605.14558) | "Action Bottleneck": gradient mass belongs on action tokens                                                             | Relevant when fine-tuning small models on our traces                                                     |




## Theme E — Semantic lifting (LeCun/JEPA translated to symbolic domains)


| Paper                              | Takeaway                                                                                                                                                         | Repo target                                                                                                       |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| V-JEPA 2 / 2-AC (arXiv:2506.09985) | Predict action-conditioned consequences in latent space; plan = cheap simulation (zero-shot robot pick/place from <62h interaction data)                         | Principle behind B+E: predict effects in representation space, verify before commit                               |
| OCM (arXiv:2607.02846)             | Two executable code bases — object knowledge (Python classes) + procedure knowledge that must import it; online reflection; progressive disclosure of signatures | Adopt invariant: procedures typecheck against a typed domain schema; mirrors existing substrate_search/get design |
| OPINE-World (arXiv:2607.01531)     | Hypothesize object vocabularies online; counterexample-guided program synthesis                                                                                  | Semantic lifter for domains without given schemas                                                                 |
| LeCun position paper / AMI Labs    | JEPA: predict abstractions, ignore unpredictable detail; H-JEPA hierarchy for long horizons                                                                      | Framing for spec v5's semantic layer                                                                              |




## Infra watchlist

Molt (NVIDIA, PyTorch-native agentic RL framework) · ToolVerse (arXiv:2607.15660, ~400 real MCPs ≈4,500 tools as RL environments — future eval harness) · MobileRL/ADAGRPO (arXiv:2509.18119, difficulty-curriculum GRPO) · WAR (arXiv:2607.17299, rollout scheduling).

## Sequenced implementation order

1. **CLEANER-style purification** in extraction pipeline (Theme D) — cheapest, fixes known failure class
2. **Three-tier lookup** (GATS) in retrieval_mixins.py — wall-clock win, aligns with tests-65% bottleneck
3. **Rehearsal hook** (EnvACE) in bridge — trust-story feature
4. `_method_score` **as learned value fn** (ADRS/TRIAL/EFCA) — closes the write-side loop
5. **Object-schema typechecking** (OCM) + scope classification (FedWorld) — registry v1 semantics

Each step is independently shippable and demoable; steps 1–2 land before OSS launch, 3–5 after.

---

## Research Prompts

Methodology tags: `[web]` = built-in websearch (market/vendor/pain-point) · `[papers]` = arXiv / Semantic Scholar / OpenAlex via webfetch · `[exa]` = Exa deep-crawl where plain search underdelivers · synthesis = single report across both, produced in-session, never written to the repo without approval.

### Cross-cutting market prompts
- P-M1 `[web]` — "agent memory RL fine-tuning startups funding 2026 self-improving agents production deployments pain points"
- P-M2 `[web]` — "LLM agent reliability enterprise procurement requirements verification audit trail world model simulation trust"
- P-M3 `[web]` — "tau-bench tau2-bench leaderboard movement 2026 which scaffolds improved most memory components"

### Theme A — Memory as RL component
- P-A1 `[papers]` — Verify MolMem (2604.12237), ADRS (2608.03223), SDAR (2605.15155): fetch abstracts; confirm dual-memory architecture, return-associated skill scoring, gated auxiliary objectives; extract benchmark deltas and model sizes.
- P-A2 `[exa]` — Find any production system already shipping evolving skill memory inside an RL loop (not papers — deployed systems, changelogs, blog posts).

### Theme B — Layered world models
- P-B1 `[papers]` — Verify GATS (2607.08894) three-tier lookup numbers (100% vs ReAct 23.9%), WorldEvolver (2606.30639) mismatch-mining mechanism, EnvACE (2608.06197) τ²-bench rehearsal results.
- P-B2 `[web]` — "world model simulation agent pre-execution validation enterprise trust cost reduction" — who is selling prediction-before-commit?

### Theme C — Scope-aware sharing
- P-C1 `[papers]` — Verify FedWorld (2608.01561): shared/cluster-specific/private/unresolved classification mechanics; negative-transfer reduction size on τ-bench.
- P-C2 `[web]` — "federated agent knowledge sharing negative transfer multi-tenant procedural memory" — competitive scan; anyone productizing scoping semantics?

### Theme D — Trajectory hygiene
- P-D1 `[papers]` — Verify CLEANER (2601.15141) ⅓-steps claim and its failure-replacement criterion; TRIAL (2608.07371) hindsight scoring on WebShop; EFCA multi-timescale credit.
- P-D2 `[web]` — "agent trajectory data quality cleaning pipeline commercial tooling" — is hygiene being sold as a product yet?

### Theme E — Semantic lifting
- P-E1 `[papers]` — Verify OCM (2607.02846) typed-schema/procedure-import invariant; OPINE-World hypothesis-synthesis loop; V-JEPA 2 action-conditioned latent planning.
- P-E2 `[exa]` — Locate JEPA-inspired *symbolic* domain work beyond robotics (code agents, API agents) published after V-JEPA 2.

### Infra watchlist
- P-I1 `[papers+web]` — Molt framework maturity; ToolVerse MCP-as-environment coverage vs our τ-integration; MobileRL curriculum applicability to procedure learning.

Execution order: P-A1/P-B1/P-C1/P-D1/P-E1 first (credibility gates everything else); market prompts run in parallel pairs; exa prompts only if [web] results are thin.

---

## Verification log (research lane)

- **2026-08-25 — P-B1 CONFIRMED w/ corrections** (`.scratch/research/p-b1-gats-worldevolver-envace.md`): GATS three tiers + zero-LLM-on-covered-actions verified; ⚠ "100% vs ReAct 23.9%" conflates tables — synthetic ReAct = 64%, 23.9% is stress-test-only. WorldEvolver real title "Self-Evolving World Models for LLM Agent Planning" (ALFWorld/ScienceWorld/AgentBoard evals, not τ). EnvACE verified: τ² avg 30.0→36.7 (Qwen3-8B), TTS N=2 → 38.0; case study = invalid-write caught pre-commit; code public.
- **2026-08-25 — P-C1 CONFIRMED** (`.scratch/research/p-c1-fedworld.md`): M1–M4 mechanics as planned + Theorem 1 protection–transfer trade-off; strict-conflict recovery +16.6 (τ) / +25.0 (ALFWorld) EM pts over naive pooling; online task success 0.512→0.624 / 0.447→0.593 under fixed controller; limitations section leaves execution-based resolution open (our wedge).
- **2026-08-25 — P-M3 ANSWERED** (`.scratch/research/p-m3-leaderboard-movement.md`): leaderboard movement is model-driven; no memory scaffolds on official boards. τ² telecom saturating (~99% tracker rows); τ³-Banking still open (top 55.2%). Harness implication: report pass^k + disclosed user-sim; treat τ²-telecom as sanity check only.
- **2026-08-25 — P-I1 ANSWERED** (`.scratch/research/p-i1-molt-toolverse-mobilerl.md`): Molt = real (arXiv:2607.21653, Apache-2.0, ~5 wks old, research-infra framing, 16×H100 floor) — watchlist only. ToolVerse scale claim (~400 MCPs/~4,500 tools) accurate. MobileRL ADAGRPO curriculum transfers to procedure-learning credit assignment; domains otherwise orthogonal.
- **2026-08-25 — τ-Knowledge re-check** (`.scratch/research/tau-knowledge-ceiling-recheck.md`): paper's 25.5% pass^1 frontier ceiling is stale; official board now tops at 55.2% (Qwen 3.8 Max) — still least-saturated τ surface → keep as headroom benchmark; harness should ship pass^1+pass^3 from day one.
- **2026-08-25 — Competitive sweep** (`.scratch/research/competitive-sweep-mem0-letta-zep-hipporag-awm.md`): Graphiti owns bi-temporal fact validity only; no vendor ships typed propositions, evidence independence groups, computed capability, or non-compensatory applicability. AWM's documented "procedural drift" failure mode = citable pain-point evidence. Zep CE deprecated Aug 2026 (migration churn).
- **Tooling note:** `EXA_API_KEY` absent in this worktree (`backend/.env` is gitignored and didn't propagate); built-in websearch used per protocol. Key needs manual placement for future `[exa]` tickets.



