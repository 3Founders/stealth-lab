# Catastrophic Forgetting — Literature Review

Compiled 2026-10-17. Sources retrieved via Exa; reputation ranked by venue, citation
count, and author track record rather than by recency.

---

## 0. The headline finding that reframes the question

**This repo has no gradient-based training.** Exhaustive search across all source,
all dependency manifests, and full git history (`git log --all -S`) for `peft`,
`LoraConfig`, `get_peft_model`, `SFTTrainer`, `transformers.Trainer`, `DataLoader`,
`loss.backward`, `optimizer.step`, `AdamW`, `torch.nn`, `deepspeed`, `accelerate`,
`unsloth`, `axolotl`, `trl`, `flax`, `keras`, `sklearn.fit` returns **zero hits**. No
weight files exist (`*.safetensors|ckpt|pt|pth|onnx|gguf|pkl` → nothing). `import torch`
appears only as *transcript text* inside agent JSONL and in one since-deleted file.

Therefore the *classic* sense of catastrophic forgetting — "learning task B changed
the weights that encoded task A" (Kirkpatrick et al. 2017) — **cannot occur here and
cannot be observed in any existing log.** Any claim that a run log "shows the LLM
forgetting" would be a category error.

**One correction to the obvious reading.** `backend/app/routing/` *is* a real fitted
model — a NumPyro Bayesian hierarchical recommender (`model.py`, `fit.py`,
`model_update.py`, ~24 modules) fit by NUTS MCMC or normalizing-flow VI over
~39k observations. But it fits **scalar per-model/per-goal success probabilities**, not
network weights. It is posterior inference on a *router* — inference-time model
selection. The LLMs themselves are never modified. Worth knowing so the repo is not
mis-described as "no ML at all," but it does not change the forgetting analysis.

What *can* be observed is a family of adjacent, genuinely measurable phenomena —
retrieval-side forgetting, knowledge invalidation, capability regression across arms,
intra-episode context loss. Those are mapped in §5 and §6.

There is a second, deeper point from the 2025 literature that applies directly here
and is worth internalizing before any experiment is designed: **performance drop ≠
knowledge loss** (§3.1). Any "forgetting" metric built purely on downstream accuracy
will systematically overestimate erasure.

---

## 1. Foundational / canonical

| Source | Venue | Why it matters |
|---|---|---|
| McCloskey & Cohen, *Catastrophic forgetting in neural networks* | Trends Cogn. Sci. 3(3):128–135, 1989 | The original observation (catastrophic interference). Every survey traces back here. |
| French, *Catastrophic forgetting in connectionist networks* | Trends Cogn. Sci. 3(4):128–135, 1999 | Named the phenomenon and framed the biological analogy that still drives the field. |
| Robins, *Catastrophic forgetting, rehearsal and relearning* | Connection Science, 1995/2002 | Established the three-part taxonomy (rehearsal / relearning / regularization) still in use. |
| **Parisi, Kemker, Part, Kanan, Wermter, *Continual Lifelong Learning with Neural Networks: A Review*** | Neural Networks 110:54–71, 2019 · arXiv:1802.07569 · ~14k citations | **The single canonical review.** Best entry point; taxonomy + biological mechanisms. |
| **Kirkpatrick et al., *Overcoming catastrophic forgetting in neural networks*** | PNAS 114(13):3521–3526, 2017 (DeepMind) | **EWC.** Defines the mechanism crisply: "the weights that are important for task A are changed to meet the objectives of task B." Also shows plain L2-to-origin *fails* — it protects all weights equally, so task B is never learnable. |
| De Lange et al., *A continual learning survey: Defying forgetting in classification tasks* | TPAMI, 2021 · arXiv:1909.08383 | 11 methods + 4 baselines, 3 benchmarks. Empirical anchors: **parameter-isolation methods generalize best** to large, dissimilar task sequences; small models forget ~9% more than base; **task ordering has insignificant impact** (this one is under-cited and load-bearing — it kills the "task order is the story" hypothesis). |
| **Wang, Zhang, Su, Zhu, *A Comprehensive Survey of Continual Learning: Theory, Method and Application*** | IEEE TPAMI 46(8), 2024 · arXiv:2302.00487 · ~1.1k citations | The most complete and most current general survey. Formalizes the three CL objectives: **stability–plasticity trade-off**, intra-task generalizability, inter-task transfer, under resource-efficiency constraints. |
| **Dohare, Hernandez-Garcia, Lan, Rahman, Mahmood, Rupam, *Loss of plasticity in deep continual learning*** | **Nature**, 2024 | **Read this one for the distinction everyone conflates:** "Loss of plasticity is *different from* catastrophic forgetting, which concerns poor performance on old examples *even if they are not presented again*." Plasticity loss = can no longer learn; forgetting = lost what was learned. Introduces **continual backpropagation** (reinit <1 unit/step, least-used only). Conclusion is uncomfortable: "methods based on gradient descent are not enough — sustained deep learning requires a random, non-gradient component." |

---

## 2. LLM-specific empirical findings

### 2.1 Forgetting gets *worse* with scale (the contrarian result)

**Luo, Yang, Meng, Li, Zhou, Zhang — *An Empirical Study of Catastrophic Forgetting in
Large Language Models During Continual Fine-Tuning*** · arXiv:2308.08747 ·
doi:10.1109/TASLPRO.2025.3606231 (2025)

- Forgetting observed across 1B–7B, and in Qwen-2.5-Instruct 3B→14B.
- **Severity *increases* with scale**, opposite to the naive "bigger models forget less"
  assumption. Explanation offered: larger models have higher initial performance, so the
  same fine-tuning shock produces a larger absolute drop.
- Measured forgetting `FG` for BLOOMZ-7.1B: **reading comprehension 26.75% > domain
  knowledge 18.37% > reasoning 13.62%**. Reading comprehension forgets worst.
- BLOOMZ (decoder-only) forgets *less* than mT0 (encoder-decoder) at comparable scale
  → decoder-only architecture retains better.
- **General instruction tuning is protective**: ALLPACA retains more under later
  continual fine-tuning than raw LLAMA.
- Side finding: continual fine-tuning *mitigates* language bias (e.g. stereotype-
  conforming preference 75.0% → 63.88%).

### 2.2 The highest-resolution forgetting data in existence

**Jin & Ren — *Demystifying Language Model Forgetting with Low-rank Example
Associations*** · **NeurIPS 2025** · arXiv:2406.14026 · code/data/stats:
`github.com/AuCson/low-rank-forgetting`

This is the most informative single paper in the entire review. It treats forgetting as
a **matrix** rather than a scalar.

- Fine-tune an LLM on each of **M = 85** new instruction tasks; measure per-example
  forgetting (Δ log-perplexity, or binary exact-match drop) over **N = 140,000**
  upstream examples → an **M × N forgetting association matrix**.
- **These matrices are well-approximated at low rank.** On OLMo-1B and OLMo-7B,
  a **rank-3 approximation achieves R² > 0.69**. Per family at rank 3:
  Pythia/MPT **R² > 0.88**, OLMo-7B ≈ **0.75**, OLMo2 ≈ **0.65**.
- Interpretation: forgetting is driven by **a small number of latent associations**
  between "what was learned" and "what got forgotten" — not by per-example idiosyncrasy.
- **Newer, more capable families need *higher* rank** (i.e. more complicated forgetting),
  and within a family complexity is stable or *increases* with size.
- Predicting forgetting for an **unseen** task = **matrix completion** (collaborative
  filtering). Matrix factorization / KNN beat prior LM-based approaches: **F1 58.16**
  vs **6.4** for random guessing.
- **Mitigation that follows from the analysis**: upweight high-predicted-forgetting
  examples during replay → statistically significant forgetting reduction.
- **Negative results worth knowing**: gradient inner products and perplexity-threshold
  importance (the standard "important example" selectors) **do not beat random
  replay**. MIRT doesn't help at this retrieval-candidate scale.
  *Example importance ≠ example forgettability.* These are different properties.

### 2.3 The single most actionable number

**Béthune, Grangier, Busbridge, Gualdoni, Cuturi, Ablin — *Scaling Laws for Forgetting
during Finetuning with Pretraining Data Injection*** · **ICML 2025** · PMLR
v267:4020–4042

> **Injecting as little as 1% of pretraining data into the finetuning mixture
> prevents the model from forgetting the pretraining set.**

Also derives scaling laws for both forgetting *and* overfitting across target domains,
data quantities, and model scales. If you only remember one practical fact from this
review, it is this one. It is the cheapest forgetting mitigation ever measured, and it
sits comfortably inside every practical data budget.

### 2.4 Safety alignment is the most fragile thing to forget

**Qi et al. — *Fine-tuning Aligned Language Models Compromises Safety, Even When Users
Do Not Intend To*** · arXiv:2310.03693 (2023)

- Jailbroke GPT-3.5 Turbo with **10 examples at < $0.20** via the public API.
- **Three escalating risk tiers**, the third being the important one: even *benign,
  utility-only* datasets (Alpaca, Dolly, LLaVA-Visual-Instruct) degrade safety
  alignment — explicitly attributed to catastrophic forgetting of the initial alignment
  or to an inherent helpfulness/harmlessness tension.
- Practical implication: **you cannot add capability to an aligned model without
  re-verifying alignment.** There is no free lunch and no "small enough" fine-tune.

**Qi et al. — *Safety Alignment Should Be Made More Than Just a Few Tokens Deep***
· arXiv:2406.05946 (2024)

- **Shallow safety alignment**: alignment adapts the generative distribution primarily
  over only the **first few output tokens**. Hence prefilling attacks work.
- Mechanistically: fine-tuning attacks perturb the initial tokens' distribution *most*,
  because those positions carry the **largest loss values and gradient norms**.
- **ASR went from 1.5% to 87.9% after 6 gradient steps.**
- Consequence: any forgetting-robustness procedure that only preserves later-token
  behavior is preserving the wrong thing.
- Fix shown: a regularized objective constraining updates on initial tokens.

**Mitigating the Alignment Tax of RLHF** · arXiv:2309.06256
- **Plain model averaging (weight interpolation between pre- and post-RLHF) produced
  the strongest alignment-forgetting Pareto front**, beating EWC-style regularization,
  experience replay, LoRA, and reward penalties.
- **Heterogeneous Model Averaging (HMA)** = per-layer averaging ratios, since different
  layers have different task/feature overlap.
- Notable: the simple baseline from the OOD-generalization literature beat methods
  imported from continual learning. A recurring pattern.

### 2.5 Model editing fails differently and worse than expected

**— *Model Editing at Scale leads to Gradual and Catastrophic Forgetting*** ·
arXiv:2401.07453

- ROME and MEMIT forget in **two distinct phases**:
  1. **Gradual, progressive forgetting** — as edits accumulate, the model becomes less
     editable and loses downstream ability.
  2. **Catastrophic forgetting** — a single **"disabling edit"** that cripples the model:
     new edits stop succeeding, all previously edited facts are lost, downstream tasks
     fail.
- MEMIT's catastrophic phase begins around **~1400 sequential edits**; ROME is more
  fragile (MEMIT forgets ~3× fewer previously-edited facts).
- **"New edits consistently bleed into other facts stored in the model"** — edits are
  not as localized as previously believed.
- Method lesson: evaluate on *downstream* tasks, not just edit-specific metrics. The
  collapse is invisible if you only measure edit success.

**WISE** · NeurIPS 2024 · *Rethinking the Knowledge Memory for Lifelong Model Editing*
- Formalizes an **"impossible triangle"** of reliability / generalization / locality:
  editing parametric (long-term) memory conflicts with pretrained and prior-edited
  knowledge; retrieval (working) memory can't generalize.
- Solution: **dual parametric memory** (main + side) with a learned router, plus
  **knowledge sharding** — distinct edits in distinct orthogonal parameter subspaces,
  merged without conflict.
- Introduces **knowledge density** (knowledge bits per parameter) and the editing
  dilemma: few edits → low density → overfit; many edits in a shared subspace → high
  density → conflict → catastrophic forgetting. (Compare: Allen-Zhu & Li's
  **2 bits/parameter** capacity result, ICLR 2025.)

**TOFU: A Task of Fictitious Unlearning for LLMs** · Maini, Feng, Schwarzschild,
Lipton, Kolter · NeurIPS 2024 D&B · arXiv:2401.06121
- 200 **fictitious** author profiles (GPT-4 generated, so definitely not in pretraining),
  20 QA each. Synthetic by design — this isolates forgetting/unlearning to exactly one
  known cause, which is methodologically excellent.
- Three severities: forget 2 / 10 / 20 authors. Compute constrained to O(|forget set|).
- **No baseline achieves real unlearning.** Forget quality is compared statistically
  against the gold-standard "retain" model that never saw the data.
- Finding: **learning and unlearning are entangled** — you cannot depress the forget set
  while preserving the retain set.
- Metric design worth borrowing: probability of generating *true* vs *false* answers on
  the forget set, plus a model-utility aggregate.

---

## 3. The 2025 reframing — most important, most overlooked

### 3.1 Spurious forgetting: performance drop ≠ knowledge loss

**— *Spurious Forgetting in Continual Learning of Language Models*** · **ICLR 2025**
· OpenReview `ScI7IlKGdI`

This paper should change how you measure forgetting. Its claim is that the dominant
failure mode people call "catastrophic forgetting" in LLMs is **not erasure**.

- **The knowledge survives; only the *alignment* to use it collapses.**
- Direct evidence: a model's safety score drops **100% → 0%** after a few harmful
  prompts, then **recovers to ~99%** with only a handful of corrective examples — or
  with *irrelevant* data.
- In a controlled synthetic setting, Task-0 accuracy plummets within the **first 150
  steps** of Task-1 training, driven by **bottom-layer** misalignment.
- On an 8-task continual instruction tuning benchmark, accuracies **fell to zero and
  then rebounded** as later tasks were learned.
- **Resulting numbers:** naive sequential fine-tune retains **~11%** of Task 0.
  Best conventional CL method: **~22%**. Freezing the bottom layers ("Freeze"):
  **~44%** — roughly 2× the best alternative, while updating **less than half the
  parameters**, and **using no old data**.
- Theoretical account: **orthogonal weight updates**.
- The paper's conclusion for the field: *"calls for new continual learning strategies
  focusing on preserving task alignment, not just memorizing knowledge."*

Two implications worth flagging:
1. **A 10% retention score may not mean 90% of knowledge was destroyed.** Most
   forgetting metrics conflate the two.
2. **The cheapest mitigation (freeze the bottom half) beat every sophisticated
   method.** Complexity did not pay here.

### 3.2 Loss-based detection provably fails

**— *RL Fine-Tuning Heals OOD Forgetting in SFT*** · arXiv:2509.12235 (2025)

- Dissects **full-parameter** fine-tuning of LLaMA-3.2-11B and Qwen-2.5-7B at many
  checkpoints (not just endpoints).
- **OOD performance peaks very early in SFT, then declines ("OOD forgetting") while
  training loss AND in-distribution test loss keep falling.** So the best OOD checkpoint
  is **invisible to loss curves**.
- Therefore **"SFT memorizes, RL generalizes" is an artifact** of comparing
  `RL_end > SFT_end`, where `SFT_end` is already badly degraded. RL **restores** SFT's
  OOD peak; it does not surpass it.
- RL recovery has a **hard boundary** — only effective across checkpoint window
  `[420, 1200]`; too little or too much SFT and RL cannot recover.
- **Mechanism:** singular *values* are stable throughout fine-tuning; OOD behavior
  correlates with the **rotation of singular vectors**. SFT hard-aligns crucial
  parameter directions rapidly and greedily (hence fast forgetting); RL re-aligns
  them softly and slowly.

Takeaway for methodology: **your forgetting metric must be measured directly, on the
capability, at multiple checkpoints.** Loss and validation accuracy will not surface it.

---

## 4. Mitigation methods, ranked by evidence strength

1. **Inject ~1% replay of upstream data** — Béthune et al. ICML 2025. Cheapest, best
   evidence. First thing to try.
2. **Freeze bottom layers** — ICLR 2025 spurious forgetting. ~44% vs ~22% vs ~11%, no old
   data, <half the parameters. Remarkably strong for its cost.
3. **Targeted replay using predicted forgetting** — Jin & Ren NeurIPS 2025 (upweight
   high-predicted-forgetting examples; significant improvement over random).
4. **Model averaging / HMA** — arXiv:2309.06256. Best Pareto front on the
   alignment-forgetting trade-off; per-layer ratios matter.
5. **Parameter isolation** (private delta params per task + non-parametric task ID) —
   ACL 2023, *Rehearsal-free Continual Language Learning via Efficient Parameter
   Isolation*. ~40% F1 over rehearsal-free baselines, competitive with rehearsal-based
   methods while using **no cached old data**. Delta params < 0.3% of PLM size.
   Cross-validated by De Lange et al. TPAMI 2021 (parameter isolation generalizes best
   under dissimilar task sequences).
6. **Continual backpropagation** — Nature 2024. For *plasticity*, not forgetting. Use if
   the problem is "the model can't learn anything new," not "the model lost something."
7. **Rehearsal** — Continual-T0 (EMNLP 2022) swept `r ∈ {0, 0.25%, 1%}` and maintained
   ~100% of the original 12-dataset zero-shot suite across 8 new tasks. Small budgets
   suffice.
8. **Knowledge sharding + merging (WISE)** — NeurIPS 2024. For the *update-in-place*
   case (your system edits an existing knowledge base rather than training).
9. **LoRA rank, and whether you merge** — Soutif, Magistri, van de Weijer, Bagdanov,
   CoLLA 2025 (PMLR v274:996–1012). Most CL work stores adapters *without* merging
   into base weights. They **merged**, and found rank materially affects forgetting of
   both the pretraining task and downstream tasks. Also reports an apparently novel
   **"contextual forgetting"** behavior in merged ViT LoRAs, absent in ResNets.
   → Don't assume adapter storage is the safe default.

### Important negative results
- **Gradient / perplexity-based example importance does not predict forgettability** and
  does not beat random replay (Jin & Ren).
- **EWC-style regularization, generative replay, model merging, and gradient-based
  methods all had "limited success"** against spurious forgetting in LMs (ICLR 2025) —
  i.e. techniques that work in classical CV often transfer poorly to LLMs.
- **Degenerate difficulty, not model limits, can manufacture apparent forgetting.**
  Kodysh,-protected Degrees-of-Belief framework (NeurIPS 2021) showed an existing
  lifelong benchmark's catastrophic forgetting was caused by **the experimental
  design** — mixing multi-label and multi-class setups creates misleading gradients. Fix
  the benchmark and the forgetting largely vanishes. **Check your metric's
  confounds before believing your forgetting number.**
  - Same paper: prevalent memory-based methods **failed to beat simple experience
    replay** under a realistic (no task IDs, single pass) setup.
  - Companion finding (Farquhar & Garwicz et al.): existing CL evaluations are
    **biased toward regularization approaches** because they assume access to explicit
    task IDs or unlimited passes over current-task data. Those assumptions don't hold in
    general lifelong learning.

---

## 5. Public artifacts that literally show what a model forgot

Ranked by how directly they answer "show me the forgetting."

### A. Highest resolution — per-example forgetting matrices

**`github.com/AuCson/low-rank-forgetting`** (Jin & Ren, NeurIPS 2025)
- Fine-tuning scripts + checkpoints for **9 configurations**: OLMo-1B, OLMo-7B,
  OLMo-7B-Instruct, OLMo2-7B, OLMo2-13B, MPT-7B, Pythia-1B, Pythia-6.9B, Pythia-12B.
- **Pre-computed forgetting statistics downloadable from public S3** —
  `low-rank-forgetting.s3.us-east-1.amazonaws.com/stats.zip`. Each evaluation run saves
  **an array of log-perplexity per upstream example**; these aggregate to the M×N
  matrices in Figs. 2/6/8.
- Datasets (Dolma subsample, Tulu subsample) also on S3 (`data.zip`).
- Includes **mitigation** scripts: random replay, replay with offline additive / KNN / MF,
  and online variants — so you can compare *predicted-targeting* vs *random* replay
  directly, with the data already in hand.
- `viz_stats.ipynb` visualizes the forgetting-association matrix;
  `correlation_analysis` notebook relates forgetting to similarity measures.
- **This is the only public resource that gives you example-level forgetting ground
  truth across a 1B–13B model range. Start here.**

### B. Full training telemetry — Weights & Biases logs

**`github.com/tml-tuebingen/forgetting-contamination`** (Bordt, Srinivas, Boreiko,
von Luxburg — ICML 2025, "How much can we forget about Data Contamination?")
- **Publishes actual W&B training logs for OLMo-1B and OLMo-7B.**
- `forgetting_curves.ipynb` — dedicated forgetting-curve generation.
- Model checkpoints for 124M / 350M / 774M / 1.6B and OLMo-1B / OLMo-7B, plus
  per-checkpoint `evals/` folders.
- Design is a clean holdout-vs-exposed measurement: 44,000 benchmark questions
  deduplicated by fuzzy matching, split by `split-id` with id 0 held out. Absolute
  accuracies published per split, so you can read degradation directly. Example
  (Fig 1b, over-training): at **15× Chinchilla**, holdout **46.45%** vs 144× exposure
  **67.10%** — the model retained baseline accuracy while gaining ~21 points of
  exposed-set accuracy.
- Deduplicated benchmark variants published on HF under `sbordt/*`.
- Useful as a template: this is a minimal viable design for *measuring* forgetting
  rather than assuming it.

### C. Base-pretraining telemetry
**`huggingface.co/croissantllm/CroissantLLM_training_logs`** — tensorboard logs for a
1.3B bilingual model at 190k steps / 2.99T tokens, with dozens of checkpoints across
model sizes, data distributions, and training steps.

### D. Practitioner post-mortems (symptom catalogue, not statistics)
- **HuggingFace forum, Nov 2025** — "Fine-tuned Model Shows Severe Quality Degradation
  (Long-Form + Formula Reasoning Task)." Useful mainly as a real-world *symptom*
  list: logical inconsistency, reasoning errors, **long-form generation collapsing in
  the second half**, **random mid-generation language switching** (Chinese→English)
  that the base model never does, and mode collapse/repetition. Notably the same data
  and pipeline on *other* models behaved fine — model-specific sensitivity is real.
- **"Fine-Tuning or Fine-Failing? Debunking Performance Myths in LLMs"**
  (arXiv:2406.11201) — fine-tuning Mistral/LLaMA2 at 200/500/1000 examples **decreased**
  accuracy and completeness inside RAG pipelines versus the base models, contradicting
  OpenAI's published guidance. A clean example of the forgetting-mitigation
  (fine-tune) actively harming the downstream system.

---

## 6. Mapping the literature onto this repo — and what is already solved

### 6.1 The core premise is externally validated

"Fine-Tuning or Retrieval?" (arXiv:2312.05934) found RAG **consistently beat
fine-tuning** for knowledge injection — for both existing and entirely new knowledge
— and attributed part of the gap to fine-tuning's catastrophic interference.
Fine-tuning also struggled to learn new facts at all; showing many paraphrases of the
same fact helped. StealthLab is a retrieval/memory architecture. That is the right
call, and this is the strongest external support for it.

### 6.2 Good news: the strongest mitigation in the literature is already implemented

Reviewing the memory layer against the literature, the repo has **already adopted the
design that the literature converges on** — and in a stronger form than most published
systems:

- **Bi-temporal, invalidate-and-append. Never a silent overwrite.**
  `supersede_procedure()` (`services/procedures.py:564-711`) writes a new row at
  `version = prior.version + 1`, sets `t_invalid`/`t_expired` on the prior
  (*"tombstone, never delete, this table's own idiom"*), writes a `SUPERSEDES` edge,
  and records a ChangeSet.
- **Claims carry an explicit non-belief state.** `supersede_claim()`
  (`services/claims.py:485-598`) flips the superseded claim's `truth_state` to `'OUT'`
  — documented as *"known superseded/contradicted, no longer believed"* — and retrieval
  filters on it (`claims.py:841`, `applicability.py:182`).
- **Dedup preserves the evidence.** `merge_duplicate_procedures()`
  (`procedures.py:1367-1500`) tombstones losers, unions their `evidence_refs` and
  `source_episode_ids` into the survivor, and writes a `DUPLICATE_OF` edge.
  *"Losers are NEVER hard-deleted."*
- **Walk-back is supported.** `get_claim_version_chain()` exposes the full history.

This is materially better than the model-editing literature it maps onto. WISE
(NeurIPS 2024) has to invent knowledge sharding and orthogonal subspaces because
edits in weights conflict irrecoverably. StealthLab gets the same property for free
from append-only storage. **The retrieval architecture has structurally eliminated a
whole category of catastrophic forgetting that the parametric architecture cannot
escape.** That is a genuine argument for the design and it should be made explicitly.

### 6.3 The four places forgetting *is* observable in existing artifacts

| Artifact | What it measures | Actual numbers |
|---|---|---|
| `backend/tests/data/retrieval_eval_v1.before_after.json` | Retrieval representation change, old vs new text formula, same embedding model | **precision +0.184** (0.469→0.653), **recall −0.125** (0.670→0.545), F1 +0.043, cutoff +0.024. A real recorded **regression traded for a precision gain** |
| `experiments/abtest` arms **C vs C0** | Persistent `.stealth/` vs `wipe_stealth()` before every task (`runner.py:122-129`) | The cleanest retrieval-side forgetting analogue in the repo. `report.py --curve` scores it |
| `experiments/swebench/analyze.py` | Preregistered per-repo regression detection: exact McNemar on discordant pairs, explicit `gained`/`lost` counts, repo-stratified bootstrap CI, and a **`noise_A0r_minus_A0` run-to-run noise floor** | Rule 3 is literally *"no scored repo with a significant regression (McNemar p < 0.05, K < A0)"* |
| `experiments/ds1000/README.md` | Gains shrinking as the pipeline gets more production-realistic | **+4.5 → +3.8 → +2.2**; plain RAG's own gain **+9.9 (round 2) → +3.3 (round 3)**; final: Kel+code **7.7 points below plain RAG** [−12.5, −3.3], p=0.0009 |

**Note on the abtest design** — it is the one experiment that is *actually* a
continual-learning measurement: `C` accumulates knowledge across the series, `C0`
is reset before every task, and the difference is the value of persistence.
`report.py:77-104` already computes the learning curve and states the logic:
*"What C has over C0 is what a persistent `.stealth` is worth; the late positions are
where it should show."* That is a forgetting curve, in everything but name.

**A caveat worth flagging:** `analyze.py` is sound but its output is **not on disk** —
`experiments/*/runs/` is gitignored, and `experiments/swebench_pro/` (the 3-arm
graph/HTN study) **no longer exists**. Its results survive only as prose in
`Claude-Chat handoff and file preparation-20260815-1316.md:179-282`
(`no_memory 2/15`, `graph_memory 1/15`, `htn_memory 0/15`), and `check_results.py:29`
still points at the now-missing `experiments/swebench_pro` directory. Same for DS-1000,
whose `runs/` is gitignored. **Any forgetting claim built on this repo's historical
results is currently unsourceable.** That is the single most urgent gap here, and it
is an artifact-retention problem, not a research problem.

### 6.4 The four genuine remaining forgetting risks

**1. Unreachable-but-live knowledge (index staleness / recipe drift).** This is the
closest true analogue to "the model knows it but can't get at it" — and it is the one
that maps onto the ICLR 2025 *spurious forgetting* result (§3.1), where the knowledge
survived but became inaccessible.

`backend/app/services/index_freshness.py` already instruments it —
`get_index_lag()` returns `lag_count`, `recipe_drift_count`, `total_stale`, exposed at
`GET /admin/index-lag`. **But no committed snapshot of this metric exists anywhere.**
Knowledge that is live in Postgres but no longer reachable under the current retrieval
recipe is invisible to every other test in the repo. *This is the highest-value,
lowest-effort thing to fix: snapshot `index-lag` on every eval run, the same way
`evaluation-results/` snapshots pass/fail.*

**2. `mark_procedure_stale()` is one-directional.** `procedures.py:1218-1269` only
allows `fresh → stale`, never auto-reverts, and `applicability.py::check_hard_constraints()`
then excludes stale rows from retrieval entirely. This is deliberate ("do not build
the complete TMS yet") and defensible, but it means a single contradicted invariant
permanently removes a procedure from the retrieval set with no measured
self-correction rate. **Nothing in the repo measures how often staleness was
wrong.** Under the literature's terms this is a ratchet with an unknown error rate.

**3. Quarantine → disable is time-driven and irreversible in practice.**
`check_quarantine_and_disable()` (`procedures.py:1098-1146`) disables a procedure after
14 days in quarantine, plus an outcome-driven failure-streak circuit breaker above it.
Time-based, so a slow-arriving verification cannot save it. Again deliberate; again
unmeasured.

**4. The one real hard DELETE — and it is unmeasured.**
`backend/app/services/data_rights.py:164`:
```sql
DELETE FROM procedures WHERE id = $1::uuid
```
The GDPR path deliberately distinguishes non-published procedures (physical delete)
from published ones (tombstone: keep history, clear vector, stop retrieval). That
distinction is exactly right, and it is the **one place in the repo where TOFU's
central question applies**: does the deleted-then-rebuilt system behave as if the
record had never existed, or does it leave traces? The file returns counts including
`"tombstoned": N`, but **no test compares post-deletion retrieval behavior against a
never-existed gold standard.** TOFU (NeurIPS 2024 D&B) exists precisely because nobody
was checking, and its finding was that *no baseline achieves true unlearning*.

### 6.5 Methodological guidance carried over from the literature

1. **Measure directly, at multiple points, never via a proxy.** §3.2 showed loss and
   in-distribution accuracy stay flat while the capability decays. The
   `retrieval_eval_v1.before_after.json` methodology — re-embed, re-score all queries,
   sweep the cutoff, report precision/recall/F1 separately — is the right shape. The
   recall −0.125 only became visible because precision and recall were reported
   separately rather than collapsed to one score.

2. **Keep a noise floor.** `analyze.py`'s `A0r` arm (a time-matched repeat of `A0`,
   run concurrently) with an explicit `flip_rate` is unusually good practice and is
   exactly what the CL field lacks. De Lange et al. (TPAMI 2021) make the complementary
   point that **task ordering has insignificant impact** — so a single A/B difference is
   not a forgetting curve either.

3. **Report at least two of {direct capability, recovery rate,
   alignment-vs-knowledge decomposition}.** §3.1: a 10% retention score does not mean
   90% of knowledge was destroyed. For this repo the analogue is concrete and testable:
   if a stale/retired/superseded procedure were force-injected into the prompt, does
   the agent still *act* on it? If yes, the knowledge was only unreachable (spurious);
   if no, it was actually lost. **That single experiment would give this repo its own
   version of the ICLR 2025 result**, and it needs no fine-tuning — just a bypass flag
   on the retrieval filters at `claims.py:841` and
   `applicability.py::check_hard_constraints()`.

4. **Mind the store-growth effect.** LifelongAgentBench finds conventional experience
   replay has **limited effectiveness for LLM agents** specifically because of
   irrelevant-information and context-length problems. `.stealth/claims.md` is already
   **51.9 KB** locally and `procedures.py` is **76 KB** of service code. The DS-1000
   numbers (§6.3) — where gains *shrank* as realism increased, and where Kel+code ended
   up 7.7 points *below* plain RAG — are consistent with exactly this. Worth measuring
   explicitly as a function of store size rather than assuming monotonic returns.

5. **The cheapest wins are structural, not algorithmic.** Across the literature:
   1% upstream replay (ICML 2025) beats sophisticated mitigations; freezing the bottom
   half of the network (ICLR 2025) doubled the best classic method; plain weight
   averaging beat EWC and replay on the alignment-forgetting trade-off. In an
   append-only retrieval system the structural equivalent — **preserve everything,
   version everything, never mutate in place** — is already done. That instinct was right.

---

## 7. Full source list

- McCloskey & Cohen. Catastrophic forgetting in neural networks. *TiCS* 3(3):128–135, 1989.
- French. Catastrophic forgetting in connectionist networks. *TiCS* 3(4):128–135, 1999.
- Robins. Catastrophic forgetting, rehearsal and relearning. *Connection Science*, 1995/2002.
- Parisi, Kemker, Part, Kanan, Wermter. Continual Lifelong Learning with Neural Networks: A Review. *Neural Networks* 110:54–71, 2019. arXiv:1802.07569. doi:10.1016/j.neunet.2019.01.012
- Kirkpatrick, Pascanu, Rabinowitz, Veness, Desjardins, Rusu, et al. Overcoming catastrophic forgetting in neural networks. *PNAS* 114(13):3521–3526, 2017. doi:10.1073/pnas.1611835114
- De Lange, Aljundi, Masana, Parisot, Jia, Leonardis, Slabaugh, Tuytelaars. A continual learning survey: Defying forgetting in classification tasks. *TPAMI*, 2021. arXiv:1909.08383
- Wang, Zhang, Su, Zhu. A Comprehensive Survey of Continual Learning: Theory, Method and Application. *IEEE TPAMI* 46(8), 2024. arXiv:2302.00487. doi:10.1109/TPAMI.2024.3367329
- Dohare, Hernandez-Garcia, Lan, Rahman, Mahmood, Rupam. Loss of plasticity in deep continual learning. *Nature*, 2024. doi:10.1038/s41586-024-07711-7
- Luo, Yang, Meng, Li, Zhou, Zhang. An Empirical Study of Catastrophic Forgetting in LLMs During Continual Fine-Tuning. arXiv:2308.08747. doi:10.1109/TASLPRO.2025.3606231
- Shi, Xu, Wang, Qin, Wang. Continual Learning of Large Language Models: A Comprehensive Survey. *ACM Computing Surveys*, 2025. doi:10.1145/3735633
- Jin & Ren. Demystifying Language Model Forgetting with Low-rank Example Associations. *NeurIPS 2025*. arXiv:2406.14026. code: github.com/AuCson/low-rank-forgetting
- Béthune, Grangier, Busbridge, Gualdoni, Cuturi, Ablin. Scaling Laws for Forgetting during Finetuning with Pretraining Data Injection. *ICML 2025*, PMLR v267:4020–4042.
- Qi, Zeng, et al. Fine-tuning Aligned Language Models Compromises Safety, Even When Users Do Not Intend To. arXiv:2310.03693
- Qi, et al. Safety Alignment Should Be Made More Than Just a Few Tokens Deep. arXiv:2406.05946
- Mitigating the Alignment Tax of RLHF. arXiv:2309.06256
- Model Editing at Scale leads to Gradual and Catastrophic Forgetting. arXiv:2401.07453
- WISE: Rethinking the Knowledge Memory for Lifelong Model Editing of LLMs. *NeurIPS 2024*.
- Wang, Zhu, Liu, Zheng, Chen. Knowledge Editing for Large Language Models: A Survey. *ACM Computing Surveys*, 2024. doi:10.1145/3698590
- Maini, Feng, Schwarzschild, Lipton, Kolter. TOFU: A Task of Fictitious Unlearning for LLMs. *NeurIPS 2024 D&B*. arXiv:2401.06121
- Spurious Forgetting in Continual Learning of Language Models. *ICLR 2025*. OpenReview ScI7IlKGdI
- RL Fine-Tuning Heals OOD Forgetting in SFT. arXiv:2509.12235
- Soutif, Magistri, van de Weijer, Bagdanov. An Empirical Analysis of Forgetting in Pre-trained Models with Incremental Low-Rank Updates. *CoLLA 2025*, PMLR v274:996–1012.
- Ostapenko, Lesort, Rodriguez, Arefin, Douillard, Rish, Charlin. Continual Learning with Foundation Models: An Empirical Study of Latent Replay. *CLLAs 2022*, PMLR v199:60–91.
- Rehearsal-free Continual Language Learning via Efficient Parameter Isolation. *ACL 2023*, pp. 10933.
- Fine-tuned Language Models are Continual Learners (Continual-T0). *EMNLP 2022*, pp. 6107.
- Kodysh et al. Towards a robust experimental framework and benchmark for lifelong language learning. *NeurIPS 2021 D&B*.
- Zheng, Cai, Li, Zhang, Li, Zhang, Song, Ma. LifelongAgentBench: Evaluating LLM Agents as Lifelong Learners. arXiv:2505.11942
- Garg, Farajtabar, Pouransari, Vemulapalli, Mehta. TiC-CLIP: Continual Training of CLIP Models. *ICLR 2024*.
- Wang, Yu, et al. MIRIX: Multi-Agent Memory System for LLM-Based Agents. arXiv:2507.07957
- Fine-Tuning or Retrieval? Comparing Knowledge Injection in LLMs. arXiv:2312.05934
- Fine-Tuning or Fine-Failing? Debunking Performance Myths in LLMs. arXiv:2406.11201
- Allen-Zhu & Li. Physics of Language Models: Part 3.3, Knowledge Capacity Scaling Laws. *ICLR 2025*. arXiv:2404.05405
- Bordt, Srinivas, Boreiko, von Luxburg. How much can we forget about Data Contamination? *ICML 2025*. code: github.com/tml-tuebingen/forgetting-contamination
- Catastrophic Forgetting in Deep Learning: A Comprehensive Taxonomy. arXiv:2312.10549