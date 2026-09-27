# DS-1000 pilot: does Kel's knowledge help, beyond routing?

Design, hypotheses and analysis were fixed before any model ran: [PREREGISTRATION.md](PREREGISTRATION.md). The hash of the design and the preregistration is in `runs/design.sha256` (`168171b5…`). Three deviations are listed at the end of that file; none changes an outcome.

Everything ran locally in `kel_ds1000_demo`, a fresh database separate from the BigCodeBench demo, and never in production. Generated code ran in a sandboxed, pinned evaluation environment. Product code was frozen at `c99e614`.

## Setup

- **Problems:** DS-1000, pandas / NumPy / SciPy / scikit-learn. 715 of 732 problems are eligible, meaning their reference solution passes in the pinned environment.
- **Kel learned from 60 fit problems:**
  - 40 originals that have variants, and 20 distractors;
  - 59 Goals; 44 Procedures from 56 verified solutions;
  - the recommender was refit with NUTS: max r-hat 1.014, 0 divergences.
- **Test set, 84 problems, never imported into Kel:**
  - 64 *transfer* problems: variants of fit originals (15 Surface, 29 Semantic, 20 Difficult-Rewrite);
  - 20 *control* problems: originals with no relative in Kel.
- **Models:** gemma-4-31B-it, gpt-oss-120b, deepseek-v3.2 on General Compute, at temperature 0; Claude Sonnet as context-free subagents.
- **Noise:** re-running arm A gave 0 outcome flips in 252 problem–model pairs. The open models are deterministic here, so every difference below is a real paired difference, not sampling noise.

## Results

### 1. Knowledge: Kel's Procedures did not measurably help (primary endpoint)

Transfer problems, gold solve rate; 95% confidence intervals from a family-clustered bootstrap:

| Model | A (no notes) | B (Kel) | B − A | McNemar p (Holm) |
|---|---|---|---|---|
| gemma-4-31B-it | 67.2% | 70.3% | +3.1 [−6.2, +12.9] | 1.0 |
| gpt-oss-120b | 60.9% | 60.9% | 0.0 [−8.7, +8.1] | 1.0 |
| deepseek-v3.2 | 57.8% | 59.4% | +1.6 [−4.8, +8.6] | 1.0 |
| Sonnet | 78.1% | 78.1% | 0.0 [−9.4, +9.8] | 1.0 (unadjusted) |

Pooled over the three open models, each arm against A (192 pairs; exploratory):

| Arm | Change vs A | 95% CI | p |
|---|---|---|---|
| B, Kel | +1.6 | [−3.3, +6.9] | 0.66 |
| C, oracle (the right Procedure, given directly) | −0.5 | [−6.1, +5.3] | 1.0 |
| D, placebo (an unrelated Procedure) | +1.0 | [−3.8, +5.8] | 0.83 |
| **E, plain RAG (similar past problem + its verified code)** | **+8.3** | **[+0.6, +16.9]** | **0.011** |
| B vs E | −6.8 | [−13.7, −0.5] | 0.041 |

**What this means:**
- **Retrieval is not the bottleneck.** Handing models the right Procedure directly (C) helps no more than an unrelated one (D). The Procedure *format* is the problem: steps plus API names, with the code deliberately left out, carry too little for these models.
- **The verified code is what helps.** Plain RAG with the solution code lifts small models. For example, gemma on transfer problems reaches **78.1%, equal to Sonnet without help** (arm E vs A: +10.9 [+3.2, +20.0], p = 0.039). This is exploratory (a secondary analysis, not corrected for multiple comparisons) and needs confirmation on a fresh sample.
- **By variant type** (B − A, pooled): Surface −4.4, Semantic +2.3, Difficult-Rewrite +5.0. All confidence intervals include 0.
- **Retrieval quality:**
  - Kel offered notes on 40 of 64 transfer problems; 28 of them (70%) came from the problem's own family.
  - Kel also offered notes on 6 of 20 controls, all of them false matches.
  - Harm from knowledge was small: across all 84 problems, 3–5 per model were solved without notes but lost with Kel's notes, against 3–5 gained.
- **Leakage audit:** the notes name about 32–39% of the reference solution's calls in arms B, C and E, and 5% in the placebo. Arms C and E name a similar share of the right APIs, so E's advantage is the concrete code, not API names.

### 2. Routing: the reliable win, again

All 84 test problems. Costs use **placeholder** open-model prices and **estimated** Sonnet tokens. "Wrong delivered" counts problems where the delivered answer failed gold. A single model with no check delivers every unsolved problem, so its wrong-delivered count equals its unsolved count.

| Setup | Solved | Cost (USD) | vs Sonnet | Wrong delivered | First model chosen |
|---|---|---|---|---|---|
| Sonnet alone | 69 (82.1%) | 0.0937 | — | 15 | — |
| Routing only (F0), target 0.5 | 69 (82.1%) | 0.0409 | **−56%** | 14 | gpt-oss 63, gemma 14, deepseek 7 |
| Knowledge + routing (F), target 0.5 | 69 (82.1%) | 0.0511 | −45% | 13 | mostly gpt-oss |
| Routing only, target 0.6 | 71 (84.5%) | 0.0348 | −63% | 12 | |
| Routing only, target 0.9 | 75 (89.3%) | 0.0965 | +3% | 8 | Sonnet first, then cheaper models after a failed check |
| Knowledge + routing, target 0.9 | 76 (90.5%) | 0.1224 | +31% | 6 | Sonnet first |

- **At the same quality as Sonnet**, routing costs about half.
- **Above Sonnet's quality:** at target 0.9, the escalation ladder (try Sonnet, check, fall back to another model) solves 7–8 more problems than Sonnet alone, at almost the same cost for routing only. This is a Pareto improvement over the frontier model.
- **Knowledge makes routing slightly more expensive**, because the notes add prompt tokens and add no accuracy. This matches section 1.

## What should change in the product

- **Store and return the verified code with Procedures.** For example, keep it as the step's implementation, or as an attached artifact that `find_ways` returns.
  - Today the code-solution extractor deliberately drops the code.
  - This pilot suggests that exactly this code is what transfers.
  - This is a product decision (it touches the "no Implementation object" design) and should be confirmed first with a preregistered replication on a fresh sample.
- **Fewer false matches on controls:** 6 of 20 controls got notes from unrelated Goals through "ways on candidate". This path could require a higher applicability score.
- **Extraction reliability:** 12 of 56 verified solutions never became Procedures, even after 5 attempts, because of the groundedness refusal on very short solutions.

## Limits (read before quoting any number)

- **Small sample:** 64 transfer problems. Effects smaller than about 10 points can't be ruled out. The null primary result means "no evidence of a large effect", not "no effect".
- **The RAG and oracle findings are exploratory.** Confirm them on a fresh sample before relying on them.
- **Contamination:** DS-1000 has been public since 2022, so the models may have seen it. Baselines are high (Sonnet 82%), which leaves less room for any help.
- **Single-shot problems only.** Knowledge effects in multi-step agent work (fewer turns, less exploration) are not measured here.
- **Costs are indicative only:** open-model prices are placeholders; Sonnet ran as Claude Code subagents in batches of up to 8, and its tokens are estimated.
- **Goal names** were generated by gemma, standing in for ingestion. One pair of fit problems merged into a single Goal.
- **Scope:** Matplotlib, PyTorch and TensorFlow are excluded.

## Reproduce

In order, from `experiments/ds1000` with the backend virtualenv:

1. `check_references.py`
2. `design.py`
3. `run_models.py fit_raw`, and `sonnet_io.py prepare|record fit_raw`
4. `kel_setup.py`: `name`, `import`, `extract` (up to 5 passes), `notes_val`
5. `run_models.py fit_val`
6. `kel_setup.py`: `evidence`, `observe`, `refit`
7. `retrieve.py`, then `arms.py`
8. `run_models.py`: `A`, `A2`, `B`, `C`, `D`, `E`, plus Sonnet arms A and B
9. `analyze.py`

Outputs go to `runs/` (gitignored). `report.json` and `routing_traces.json` hold every number above.

---

# Follow-up: does returning the verified code help? (round-1 addendum + round-2 confirmation)

Question: Kel's Procedures (steps plus API names) did not help. Would returning the **verified code** with the Procedure help?

- **Stage 1** (same sample, exploratory; `PREREGISTRATION.md`, Addendum 1): Kel + code (Bc) − A, pooled, was +3.1 [−2.6, +10.1].
- **Stage 2** (fresh sample, preregistered before any run; [PREREGISTRATION_2.md](PREREGISTRATION_2.md), hash `2a0a4196…`):
  - 60 new families that Kel learned from; round 1's 60 fit problems stayed in the library.
  - Test set: 104 transfer variants and 20 controls, never imported.
  - 4 deviations are logged there; none touches the test arms.

## Round 2 results (open models pooled; 312 task-model pairs; family-clustered 95% CI)

| Arm vs A (no notes) | Change | 95% CI | p |
|---|---|---|---|
| B: Kel Procedure only | +2.2 | [−0.3, +5.3] | 0.12 |
| **Bc: Kel Procedure + verified code (primary)** | **+4.5** | **[+1.2, +8.3]** | **0.007** |
| Bw: Kel's retrieval, shown as a worked example (post hoc) | +4.2 | [+0.6, +8.1] | 0.024 |
| Cc: oracle Procedure + code | +6.1 | [+1.4, +11.2] | 0.003 |
| **E: plain RAG, similar past problem + its code** | **+9.9** | **[+4.6, +15.4]** | **0.0001** |

- **Primary endpoint, per model (Bc − A):**
  - gemma +3.8, gpt-oss +1.0, deepseek +8.7;
  - after Holm correction the best is p = 0.068;
  - so **under the preregistered rule the result is not confirmed**, even though the pooled effect is positive and significant.
- **Head-to-head comparisons:**
  - **Bc vs E: −5.4 [−10.2, −1.0]**. Kel + code is worse than plain RAG.
  - **Bw vs Bc: −0.3.** Showing Kel's retrieval as a worked example changes nothing, so the *format* is not the gap.
- **Why Kel falls short: retrieval coverage.**
  - Kel offered notes on 49 of 104 related tasks (40 from the right family).
  - RAG always shows its nearest neighbour: 104 of 104, 72 from the right family. Even neighbours from other families help, with the same library idioms.
  - Kel also gave notes on 10 of 20 controls, all wrong matches.
  - Harm from knowledge is small: across all tasks, 2–4 tasks lost per model against 4–12 gained.
- **Sonnet:** Bc − A = +6.7 [0, +14], p = 0.09.
- **Plain RAG replicates.** Round 1 gave +8.3 (exploratory); round 2 gives +9.9 on a fresh sample.

## Routing, round 2 (124 tasks; placeholder open-model prices)

| Setup | Solved | Cost (USD) | vs Sonnet |
|---|---|---|---|
| Sonnet alone | 87 (70.2%) | 0.1444 | — |
| gemma alone | 87 (70.2%) | 0.0104 | −93% |
| Routing only, target 0.5 | 100 (80.6%) | 0.0553 | −62%, +10 pts |
| Kel + code + routing, target 0.5 | 103 (83.1%) | 0.0627 | −57%, +13 pts |
| Kel + code + routing, target 0.8 | 107 (86.3%) | 0.0906 | −37%, +16 pts |

## What this means for the product

1. **Returning the verified code is a small, real improvement** (+4.5 points pooled), but it failed our strict per-model bar. It is cheap, so it is worth doing, though it is not the main lever.
2. **The big lever is recall.**
   - Plain retrieval of the nearest verified solved example roughly doubles the gain (+9.9).
   - It gets that gain because it always answers, whereas `find_ways` answers `ambiguous` or `no_match` on about half of related tasks.
   - Candidate change: when `find_ways` doesn't resolve, still return the nearest verified solved example (problem plus code), labelled as "similar, not verified to apply". Then test it again with this protocol (round 3).
3. **Routing remains the dependable win.** On this sample it beats Sonnet on both cost and accuracy at every target.

---

# Re-measure: round 2 with production-like knowledge (`runs2pl`, database `kel_ds1000_pl`)

Three experiment gaps were closed, and the same 124 round-2 test problems were re-run with the same models and grading:
- **Embeddings:** every Goal is embedded.
- **Identity and hierarchy:** production identity resolution (`judge_mode="model"`) and the real ingestion `Worker` running `goal_abstraction_placement`; no hard-coded library edges.
- **Queries:** an agent-written request (one fixed writer model) alongside the raw problem text.

Kel re-learned all 120 fit problems through this path: 118 Goals, 81 Procedures, 243 evidence outcomes.

**Hierarchy:** production placement accepted **1** parent/child edge among 118 Goals; 4 more were proposed.
- The judge compared each Goal with its real nearest neighbours and correctly called them *distinct* (they are sibling tasks).
- Nothing in the product creates broader parent Goals, and edges below 0.9 confidence only become *proposed*.
- The judge's batch calls partly fail (JEV 400, Gemini malformed JSON, Gemma key rejected), but that affected only 3 of 118 jobs.

| Retrieval | Notes on related tasks (104) | From the right family | Notes on controls (20) |
|---|---|---|---|
| Round 2 as run | 49 | 40 | 10 |
| Production-like, raw query | 50 | 42 | **3** |
| Production-like, agent query | 52 | 39 | 5 |

Solve rate vs no notes, 3 open models pooled, transfer tasks:

| Arm | Change | 95% CI | p |
|---|---|---|---|
| Kel + code, round 2 as run | +4.5 | [+1.2, +8.3] | 0.007 |
| Kel + code, production-like, raw query | +3.8 | [+0.6, +7.5] | 0.023 |
| Kel + code, production-like, agent query | +2.2 | [−1.0, +5.9] | 0.25 |
| Kel Procedure only, production-like, agent query | +1.6 | [−1.6, +4.8] | 0.42 |
| Plain RAG | **+9.9** | [+4.6, +15.4] | 0.0001 |

- Kel + code with the agent query is **7.7 points below plain RAG** [−12.5, −3.3], p = 0.0009.

**Conclusion:** the experiment's gaps did **not** hold Kel back.
- With production-like machinery the knowledge gain stays at about +2 to +4 points, and plain RAG stays about twice as good.
- Closing the gaps mainly cut **false matches on controls** (10 → 3–5).
- The constraint is the design itself: knowledge passes only an applicability gate, so close variants are rejected, and there is no channel for "similar, adapt it".

---

# Round 3: knowledge-side improvements, confirmation

> **Code location:** round 3 exercises changes 1–5 (`KNOWLEDGE_*` flags, migration 124), which live on the branch `knowledge-examples-experimental`, not on `main`. `retrieve_k.py` and round 3's knowledge base need that branch. Rounds 1–2 and the re-measure run on `main`. (preregistered, `runs3`, database `kel_ds1000_r3`)

**Setup**
- **Sample:** 30 new families: 82 transfer variants and 7 controls.
- **Knowledge base:** production-like, with the improvements ON, built from all 150 fit problems; 100 Procedures, all with a stored verified example.
- **Queries:** agent-written.

**Verdict: not confirmed.** Kel with the improvements (K) vs no notes: +3.3 [−3.4, +10.2], p = 0.27.
- K equals plain RAG (0.0) and today's product (+0.4).
- Plain RAG's own gain shrank to +3.3 on this sample (it was +9.9 in round 2).
- Sonnet with K: −3.7.
- Routing again beat Sonnet alone at about 57% lower cost.

Details and the full secondary list are in [PREREGISTRATION_3.md](PREREGISTRATION_3.md).
