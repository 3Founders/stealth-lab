# P-B1 — GATS / WorldEvolver / EnvACE verification

**Ticket:** RESEARCH_INTEGRATION_PLAN.md P-B1 `[papers]` · **Date:** 2026-08-25 · **Lane:** research
**Method:** arXiv abstract + full-text (HTML v1) fetches. Every number below was read off the primary source this session.

## Verdict: CONFIRMED with corrections

All three papers exist, are recent (Jun–Aug 2026), and say roughly what the plan claims. Two headline numbers in the plan need correction before they appear anywhere customer-facing.

---

## 1. GATS — arXiv:2607.08894

Source: <https://arxiv.org/abs/2607.08894> ("GATS: Graph-Augmented Tree Search with Layered World Models for Efficient Agent Planning", Williams & Nowicki, submitted 9 Jul 2026).

- **Three-layer world model confirmed exactly as planned:** L1 exact symbolic action matching → L2 statistics learned from execution logs → L3 LLM-based prediction for unknown actions.
- **Numbers as published:**
  - Synthetic planning tasks (branching paths, dead ends): GATS **100%**, LATS 92%, ReAct **64%**.
  - 12-scenario stress test (coding workflows, web navigation, long-horizon): GATS **100%**, LATS 88.9%, ReAct **23.9%**.
  - **Zero LLM calls per task during planning vs 37 per task for LATTS**; deterministic plans, zero variance across runs.

### ⚠ Correction to the plan
The plan's line "zero-LLM planning, 100% vs ReAct 23.9%" conflates two tables. The correct pairing for the stress-test 23.9% is GATS 100% / LATS 88.9%; on the synthetic tasks ReAct scores 64%. Also "zero-LLM" holds when L1/L2 coverage suffices — L3 exists precisely as a fallback tier, so the honest phrasing is "LLM-free on covered actions."

### Implication for `retrieval_mixins.py`
Tier structure and the wall-clock argument replicate cleanly; cite both table rows separately. Note the eval domains are synthetic/stress tasks, not τ-bench-style stateful dialogs — our three-tier lookup claim should be framed as architecture transfer, not benchmark transfer.

---

## 2. WorldEvolver — arXiv:2606.30639

Source: <https://arxiv.org/abs/2606.30639>. Actual title: **"Self-Evolving World Models for LLM Agent Planning"** (Zhang, Zhang, Ng, Deng; 29 Jun 2026) — "WorldEvolver" is the system name inside the paper.

- **Mechanism confirmed:** (i) Episodic Memory = retrieval-based simulation over real action transitions; (ii) Semantic Memory = persistent heuristic rules extracted from **prediction–observation mismatches**; (iii) Selective Foresight = low-confidence predictions filtered out before injection into agent context. Agent and all model parameters stay frozen; only deployment-time context evolves.
- **Eval surfaces:** prediction accuracy on Word2World; downstream agent success on AgentBoard; environments ALFWorld + ScienceWorld; three backbones. It does **not** report τ-bench-family results.

### Implication
The mismatch-mining pattern maps onto our provenance/success-criteria records exactly as planned, and confidence-gating-before-context-injection matches our evidence-gating story. Cite it as ALFWorld/ScienceWorld evidence.

---

## 3. EnvACE — arXiv:2608.06197

Source: <https://arxiv.org/abs/2608.06197>, full text <https://arxiv.org/html/2608.06197v1> (Xu et al., SJTU/Tencent/ZJU et al.; 6 Aug 2026). Code: <https://github.com/Within-yao/EnvACE>.

- **Mechanism confirmed:** one shared policy plays both roles — issues a tool call, then *rehearses* the environment response, then conditions on the rehearsed response; role-wise GRPO baselines; jointly optimized on task-success reward. At test time: N private rehearsals (parallel or sequential), summarized into a rehearsal memory that conditions one committed execution against the real environment.
- **τ²-Bench numbers (Qwen3-8B backbone):**
  - Training effect: τ² avg **30.0 → 36.7** vs base Qwen3-8B (Table 1); vs standard GRPO 31.2 → 36.7 (+5.5, Fig. 4). Retail 48.9 / Telecom 17.3 / Airline 44.0.
  - Test-time scaling (Table 3, N=2 parallel rehearsal): overall 36.7 → **40.9** (+4.2); τ² avg 31.4 → 38.0; Retail 42.3 → 54.4. N=3 regresses (context length) — moderate budget matters.
  - Overall across BFCL-v4 + τ² + VitaBench: 32.91, best among environment-scaling baselines compared (EnvScaler-8B 31.92, AWM-14B 32.54).
  - Case study: rehearsal catches an invalid `update_reservation_flights` write pre-commit and substitutes a read-only query — the exact predict-then-commit shape of our planned bridge hook.

### ⚠ Caveat
EnvACE's internalized model lives in fine-tuned parameters; our rehearsal hook predicts from retrieved verified rules instead. Same UX, different trust basis — worth stating explicitly in the spec so nobody assumes we need RL to ship step 3.

---

## Board deltas

1. Fix GATS citation in any external material: 64% (synthetic) vs 23.9% (stress test) are different rows.
2. WorldEvolver: use real title "Self-Evolving World Models for LLM Agent Planning"; benchmarks are ALFWorld/ScienceWorld/AgentBoard.
3. EnvACE supports the rehearsal hook with hard τ² numbers (30.0→36.7 trained; 38.0 with TTS at N=2) and public code.
