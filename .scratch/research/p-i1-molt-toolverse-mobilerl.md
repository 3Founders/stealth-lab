# P-I1 — Molt / ToolVerse / MobileRL maturity

**Ticket:** RESEARCH_INTEGRATION_PLAN.md P-I1 `[papers+web]` · **Date:** 2026-08-25 · **Lane:** research
**Method:** arXiv abstracts + web search (news/vendor/GitHub). Exa unavailable in this worktree (no `backend/.env`; see board log) — built-in websearch used instead.

## 1. Molt (NVIDIA NeMo) — real, fresh, research-grade

- **Paper:** "Molt: A Scalable PyTorch-Native Training Framework for Agentic Reinforcement Learning", arXiv:2607.21653 (submitted 22 Jul 2026) — <https://arxiv.org/html/2607.21653v1>. **Code:** <https://github.com/NVIDIA-NeMo/labs-molt> (Apache 2.0, launch scripts, Slurm configs, prebuilt container).
- **What it is:** PyTorch-native agentic RL trainer composed of Ray (placement/async queues) + vLLM (rollout) + NVIDIA AutoModel/FSDP2; ~8.6K lines of traced RL code vs verl 62K / slime 25K / OpenRLHF 7.2K by the same import-graph measure. Token-exact rollouts (rollout/actor semantics pinned; MoE via rollout routing replay, <https://arxiv.org/abs/2510.11370>). Agents are plain Python (`Env` Gymnasium-style or `ChatAgent` with OpenAI/Anthropic SDK).
- **Maturity read (as of 2026-08-25):** released ~5 weeks ago; NVIDIA explicitly positions it as *research infrastructure, not a production training service*; shipped recipes assume 2×8 H100 nodes split train/rollout (<https://www.marktechpost.com/2026/08/01/nvidia-ai-releases-molt-a-pytorch-native-agentic-reinforcement-learning-framework/>; corroboration: <https://aidailypost.com/news/nvidias-molt-pytorch-framework-agentic>, <https://ainave.com/tech-news/nvidia-molt-brings-a-compact-pytorch-native-agentic-rl-framework-to-researchers>).
- **Relevance to us:** watchlist-only. It is a *trainer*, not an agent runtime or memory system — no overlap with our trust spine. If Band-N work ever fine-tunes small models on our traces (Theme D ActFocus note), Molt or verl would be the candidate harness; Molt's readability pitch is attractive but its hardware floor (16×H100) exceeds our current footprint.

## 2. ToolVerse — verified scale claim; eval-harness potential, not a competitor

- **Paper:** arXiv:2607.15660 ("ToolVerse: Unlocking Massive Environments and Long-Horizon Tasks for Agentic Reinforcement Learning", Zhou et al., 17 Jul 2026) — <https://arxiv.org/abs/2607.15660>.
- **Verified claims:** builds executable agentic-RL training environments from **nearly 400 real-world MCPs containing about 4,500 tools** (plan's "~400 MCPs ≈4,500 tools" is accurate); GUST dataset generated via Dynamic Unlocking Sampling over a tool dependency graph for long-horizon tasks; TARA = Turn-Aware Relative Advantage for fine-grained credit assignment.
- **Maturity read:** paper + dataset (GUST); no product surface found; industry-flavored author list but no vendor page located this session. Treat as future eval-harness option exactly as the plan does — its MCP-as-environment coverage is complementary to our τ-integration (they generate training tasks from live MCP tool graphs; we verify procedural memory against deterministic benchmark state). No evidence anyone has combined it with execution-verified memory.

## 3. MobileRL / ADAGRPO — mature recipe, narrow domain, curriculum idea transfers

- **Paper:** arXiv:2509.18119 v2 ("MobileRL: Online Agentic Reinforcement Learning for Mobile GUI Agents", Xu, Liu, … Dong; Sep 2025, revised Oct 2025) — <https://arxiv.org/abs/2509.18119>.
- **Verified claims:** ADAGRPO = Difficulty-ADAptive GRPO: difficulty-adaptive positive replay + failure curriculum filtering + shortest-path reward reshaping for multi-turn tasks. MOBILERL-9B hits SOTA **AndroidWorld 80.2%** and **AndroidLab 53.6%**; applied to Qwen2.5-VL-7B-Instruct and GLM-4.1V-9B; framework open-sourced (per abstract link).
- **Applicability to procedure learning:** the transferable piece is the *difficulty-curriculum shape* — heavy-tailed task difficulty handled by replay weighting and filtering failures out of the rollout group rather than letting them dominate gradients. Maps onto our credit-assignment step (_method_score as value fn): weight procedure updates by task-difficulty strata instead of uniform trajectory outcomes. Domain itself (mobile GUI VLM agents) is orthogonal to us.

## Board deltas

1. Infra watchlist stands. Nothing here threatens the thesis; nothing here is production-ready either — all three are ≤13 months old and research-framed.
2. Molt: cite as evidence the RL-trainer layer is commoditizing (good for our cost story), not as a component we depend on.
3. ToolVerse: keep as named future eval harness; re-check GUST availability when MEASURE lane freezes baselines.
