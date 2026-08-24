# QuantAsm ↔ StealthLab Integration — Session Summary (2026-08-23)

## What QuantAsm is
An anti-cheat benchmark where LLMs write x86-64 assembly trying to beat
gcc -O3 -march=native on 16 quant-finance kernels. Repo: Prog/LLMAssemb
(v0 committed, `caca2d0`). Every candidate passes: poisoned output buffers,
nm symbol gate (sole defined global = kern), import denylist,
.init_array block, implausible-cycle floor, fresh-seed re-verification,
64-byte placement symmetry, A/B/B/A TSC medians. Calibrated 16/16 at ~1.0x.

## The decision
QuantAsm rollout traces + procedures will be stored in StealthLab's VPES
backend (direct asyncpg writes, no HTTP dependency). This gives VPES its
second domain (asm optimization alongside tau3bench) — the first live test
of cross-domain `capability_statement` retrieval (spec §10 / migration 20).
HF dataset export later becomes a view over this store.

## Why now (primary-source verified)
- SuperCoder arXiv:2505.11480v4 (full text): owns asm superoptimization
  (8,072 programs, 1.46× over -O3); its Limitations call for better
  assembly verification. Nobody has built that layer.
- KernelBench-Verified arXiv:2607.16241 (full text): honest eval deflates
  GPT-5.5 from 1.43×→0.88×; Limitations admit fixed hidden suites are
  anticipatable → our unbounded-seed + range-contract design answers it.
- Terminal Wrench / Trace-and-Amplify / Auditing Reward Hackability:
  failure-trajectory datasets get shipped AND used; unprompted hack
  trajectories are scarcer and more valuable than elicited ones.
- How2Bench ICML'26: 672 benchmarks surveyed — rigor is decaying
  (38% skip prompts, 66% never repeat evals).

## Evidence-forced protocol amendments (judge literature)
LLM-judge reliability papers (541K-judgment study; Coin Flip Judge):
AB/BA position randomization mandatory · pointwise-first judging ·
judge_id + prompt_hash stored per audit row · disagreement → manual queue.

Procedure extraction amendments (2608.20274, AFTER, PolySkill):
subtask/strategy-level induction ONLY (task-level skills harm agents);
multi-model trace pooling is empirically optimal (73.1% vs 36–59%
cross-model accuracy); capability_statement ≈ polymorphic abstract class.

## Week-1 build sequence
0. Local Postgres → migrate.py → emitter creates 16 kernel task nodes;
   backfill runs/*.jsonl via memory/stealth_emit.py (asyncpg, idempotent)
1. Zero-budget campaign: Gemini Flash free (1,500 RPD) primary, Groq
   (≤14.4K RPD), NVIDIA NIM, Ollama floor (qwen2.5-coder-7b = SuperCoder's
   own base model). ~300–1,500 attempts across ≥3 models.
2. verify() fresh seeds on every >1.02× claim; O0/O2/O3/fast-math × 16 =
   64 calibration anchor rows
3. taxonomy.py labels failures {assemble-fail, gate-hit, wrong-answer,
   timeout, cheat-suspect}; dual-judge audits on winners per amended protocol
4. Procedures via migration-20 extractor registry (strategy-level prompts);
   HF unlisted draft + How2Bench-compliant card
5. git tag harness-v0.1; blog: "the benchmark that passes its own audit"

## API-key reality
Storage = zero keys (pure DB writes). Embeddings: Gemini free keys with
rotation (gemini_api_keys pattern already in config) or Ollama
mxbai-embed-large (1024-dim matches schema). Extraction dogfooding:
USE_LOCAL_MODELS=true. Governance budgets already cap spend ($10/day).

## Division of labor
- StealthLab: receives task nodes + traces; extractor registry mines
  procedures; capability_statement gets its cross-domain test
- QuantAsm: emits episodes/traces per rollout; owns kernel specs, gates,
  timing oracle, HF corpus export

## Open items
- Postgres confirmed local-available; DATABASE_URL wiring pending
- Procedure seeding: registry-extraction vs hand-seeded capability
  statements (discussion parked)
- HF account/token needed Day 4 of corpus week
