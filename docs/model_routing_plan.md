# Per-Goal model recommender: plan

Status: **plan only, nothing implemented.**

**Purpose.** For every Goal, and later every step of a run.md, recommend the ladder of models that minimises the
expected cost of a verified success. For example: "Gemma → (if the check fails) Opus", or "Opus directly".

The plan is built from three inputs:
- the Perplexity and Gemini research reports;
- the corrections in our design discussion;
- the current codebase.

---

## 0. Fixed decisions

1. **Retrieval semantics do not change.**
   - Procedures stay ranked by correctness.
   - The recommender *attaches* model evidence to each Procedure and never reorders it. The v[π,m] term in §3 carries the Procedure's influence.
   - Using expected cost to success as a tie-break between equally correct Procedures would be a separate, explicit decision. It is not part of this plan.
2. **The ladder is chosen after retrieval and planning.** A model's success depends on which Procedure it follows.
3. **One statistical model, no ad-hoc fallbacks.**
   - When there is no data, the prior decides, and the prior is part of the same model: public benchmark data, the Goal's embedding and its DAG parents.
   - There is never a hard-coded "default model", a "prune models weaker than the one that failed" rule, or an "if no data then X" branch.
   - Every recommendation carries its uncertainty.
4. **"Correct" means passing the Goal's frozen benchmark.** Every other check is a *noisy proxy* with measured error rates: a Procedure's verification step, tests at runtime, an LLM judge, or a host's self-report.
5. **Privacy semantics do not change.** Outcomes on private Goals update only that tenant's parameters (§11).

## 1. What we take from the two reports, and what we reject

| Idea | Source | Decision | Why |
|---|---|---|---|
| Cascades gated by a verifier (FrugalGPT, AutoMix) | both | **adopt** | We have real verifiers, so this is the core of the design |
| IRT: model ability vs item difficulty (IRT-Router) | both | **adopt, extended** | 1-D IRT forces the same model order on every Goal. We need the multidimensional form (MIRT) / a low-rank interaction (§3) |
| Routing unit = model × version × scaffold (× prompt profile) | both | **adopt** | Scaffold swings are as large as model gaps. We decompose the unit additively so new combinations are predictable |
| Hierarchical pooling through the Goal DAG | both | **adopt, but as a joint GLMM** | Gemini's "Beta per node, updates propagate upward" double-counts evidence in diamonds and cannot carry covariates |
| Cascade formula Σ c_k Π(1−p_j) | Gemini | **reject** | It assumes independent failures. A failure means the instance is hard, so later rungs are overrated. Replaced by a latent instance difficulty (§3, §6) |
| "Prune models whose ability ≤ the failed model" | Gemini | **reject** | With Goal-specific specialties, a globally weaker model can be stronger on this Goal. We update the posterior on the failure instead |
| A cheap LLM "meta-verifier" to confirm failures | Gemini | **reject as a rule** | Verifier error is modelled explicitly (§5). A judge may be one of the checks, with its own measured error rates |
| Market spend share (OpenRouter Auto) as a success prior | Gemini | **reject as a prior; keep for discovery** | Popularity is not correctness. It is useful only to decide *which new models to add to the candidate set* |
| Budget constraint via primal-dual shadow price (Bandits with Knapsacks, ParetoBandit) | Gemini | **adopt (per-tenant, optional)** | This is the correct way to enforce an average budget |
| Thompson sampling, logged propensities, doubly robust off-policy evaluation | both | **adopt** | Standard and correct. We add a compliance caveat (§8) |
| Time decay of pseudo-counts | Gemini | **replace** | A random walk on ability (a dynamic IRT, i.e. a state-space model) is the principled version. Price changes are handled by a price table, not by learning (§4) |
| Cache-aware switching cost between steps | Gemini | **adopt (step-level phase)** | Real money: switching models re-sends the context |
| Step-level routing (AgentRouter) | Perplexity | **adopt later, optimising the task-level objective** | Optimising each step greedily ignores what it does to later steps |
| Using a query embedding as a feature | both | **adopt as a prior on Goal parameters** | This gives a sound cold start for Goals without parents |

## 2. Terms

- **Routing unit** u = (model m, version, scaffold s). The scaffold id includes the prompt profile, e.g. `claude-code@1.x`, `codex-cli`, `stealth-sandbox`.
- **Instance** i: one concrete task a user brings to Goal g. For example, one SWE-bench task, or one user's bug.
- **Attempt** r: one run of one unit on one instance. A ladder is a sequence of attempts on the same instance.
- **Check** k: the acceptance signal after an attempt. It can be the frozen benchmark, a Procedure step check, runtime tests, a judge or a self-report.

## 3. The success model (one Bayesian GLMM)

For attempt r = (instance i of Goal g, unit u = (m, s), Procedure π):

```
logit P(correct_r) = θ[m,t] + γ[s] + δ[m,s]              ability of model m (at time t), scaffold effect, model×scaffold residual
                   − b[g]                                  Goal difficulty
                   + ⟨a[g], z[m]⟩                          Goal-specific specialty (low-rank, K ≈ 4–8 dims)
                   + ⟨c[π], z[m]⟩                          how much this Procedure helps this model
                   − ε[i] − η[r]                           instance difficulty within g, attempt noise
```

**Priors (all part of the one model):**

```
θ[m,t]  = θ[m,t−1] + N(0, σ_drift²)            weekly random walk: version drift
θ[m',0] ~ N(θ[m_prev,T], σ_new²)               a new version starts from its predecessor, with wider variance
z[m]    ~ N(0, I);   γ[s] ~ N(0, τ_γ²);   δ[m,s] ~ N(0, τ_δ²)
c[π]    ~ N(0, τ_c² I)
ε[i]    ~ N(0, σ_ε[g]²),  log σ_ε[g] pooled through the DAG like b
η[r]    ~ N(0, σ_η²)
```

**Goal parameters pooled through the DAG, with an embedding prior.** Let φ(g) be the Goal's embedding and P(g) its accepted parents:

```
(b[g], a[g]) ~ N( μ(g), Σ )
μ(g) = W·φ(g) + (1/|P(g)|) · Σ_{p ∈ P(g)} ( (b[p], a[p]) − W·φ(p) )
```

- The embedding regression W gives every Goal a baseline.
- Parents contribute their *residuals*: how much harder or easier than predicted they turned out to be.
- A Goal with no parents simply has an empty sum. That is the same formula, not a special case.
- Data enter the likelihood exactly once, so diamonds cannot double-count.
- Children inform parents through the joint posterior, not through heuristic upward updates.

**Why each term is there:**
- **ε** makes failures on the same instance correlated. This is what makes cascade maths correct (§6).
- **a·z** lets the model order differ per Goal: "Gemma beats Opus here" can only happen through this term.
- **c·z** lets "with this run.md, a small model is enough" show up in the data.
- **σ_ε[g]** captures how much the instances of a Goal vary in difficulty. A broad Goal has a large σ_ε, which makes cheap-first ladders riskier there.

**Seeding with public data is not a separate mechanism.** Public results enter as observations in the same likelihood:

- **Per-instance public results.** SWE-bench leaderboard submissions publish per-instance logs (*verify: the SWE-bench `experiments` repo*). When we ingest SWE-bench tasks as leaf Goals, those logs become real (g, m, s) observations from day one, with the submission's scaffold as s.
- **Aggregate-only results** ("78% on X"): a binomial likelihood on the benchmark's mean success, marginalising over its items.

As Goal-specific data accrues, it outweighs the public data automatically. No hand-tuned blending is needed.

## 4. The cost model

- **Learn tokens, not dollars.** For each (g, u, outcome), model tokens_in, tokens_out and cached_in as hierarchical log-normals, pooled the same way as §3.
- **Dollars are computed at decision time** from a live **price table** (model, time, cache tier). A price cut applies instantly, with nothing to relearn.
- **Model the cost of a failed attempt separately** (outcome = fail), because failures usually run longer.
- **Include the check's own cost**, e.g. running a test suite: c_check per check kind.
- **Switching cost (step level):** re-sending the context, i.e. context tokens × (input price − the cache discount you would otherwise get).
- **Latency** is modelled the same way (log-normal). It is used only if the user or tenant sets a latency weight or cap.

## 5. The check (verifier) model

Every check kind k has a false-accept rate α_k and a false-reject rate β_k, with Beta posteriors:

```
P(accept | correct) = 1 − β_k,     P(accept | wrong) = α_k
```

- **They are estimated by audit.** A random sample of accepted and rejected attempts is re-graded against the frozen benchmark, or by a reviewer where no benchmark exists.
- **The frozen benchmark defines correctness** (§0.4). How reliable it is falls under the benchmark-validation work (fail-before/pass-after, mutation testing), not this model.
- **The likelihood uses noisy labels.** An attempt judged only by check k contributes p(1−β_k) + (1−p)α_k. So host self-reports and judge-only outcomes count with the right weight, with no arbitrary "ignore" or "trust" rule.
- **Reporter reliability:** each reporting host or account gets a reliability random effect, in the style of Dawid–Skene annotator models. This absorbs systematically optimistic reporters and resists gaming.
- **Partial signals improve the belief about ε.** If a check reports a pass fraction (e.g. 3 of 5 FAIL_TO_PASS tests), that fraction enters as a likelihood on ε for the next rung, not just pass or fail.

## 6. The objective and the decision

**Utility of a ladder** for one instance:

```
U = V · P(accepted ∧ correct) − L · P(accepted ∧ wrong) − (1 + λ_B) · E[total cost] − w_lat · E[latency]
```

- **V** is the value of a verified success: what the user or tenant is willing to pay for it.
- **L** is the penalty for delivering a wrong result that passed the check.
- **λ_B** is the budget shadow price (below). It is 0 when no budget is set.

**Hard constraints are applied before optimisation, as filters on the candidate units:** privacy (local or open-weights only), availability, context window, tool support, and max cost per run.

**Reliability constraint (a chance constraint on the posterior):**

```
Pr_posterior( P(accepted ∧ correct) ≥ ρ ) ≥ 1 − δ
```

This is why a Goal with little data automatically gets a stronger or longer ladder: the posterior is wide. It follows from the model; it is not a fallback.

**Computing a ladder's value exactly.** Ladders are sequences of up to K = 3 rungs, and repeating a unit (a retry) is allowed.
- The ε shared by all rungs is integrated by Gauss–Hermite quadrature (about 20 nodes), since it is one-dimensional.
- Attempt noise η is integrated per rung.
- With up to 8 candidate units, that is 8 + 56 + 336 = 400 ladders, each a small integral: milliseconds.

**Sequential re-planning.** After each rejected attempt, update the belief over ε with the check's observation (including the partial pass fraction), then re-solve for the rest of the ladder. The belief state is one-dimensional and K is small, so this is the **exact Bayes-optimal dynamic programme**, not a heuristic.

**Budget (optional, per tenant): primal-dual, as in Bandits with Knapsacks.**

```
λ_B ← max(0, λ_B + η_B · (cost_t − B))
```

B is the tenant's average cost ceiling per run.

## 7. Exploration

- **Thompson sampling:**
  1. draw one sample of all parameters from the posterior;
  2. solve §6 under that sample, keeping the reliability constraint on the full posterior;
  3. recommend the resulting ladder.
- **Propensity:** at decision time, estimate the probability of the chosen ladder from about 200 posterior draws, and log it. Off-policy evaluation needs it.
- **Exploration spending is bounded** by the reliability constraint and the per-run cost cap. No extra ε-greedy knob is needed.

## 8. Learning (inference)

**Deciding and updating have different time budgets.**
- **Deciding** must take milliseconds. It only *reads* stored posterior draws: Thompson sampling picks one draw.
- **Updating** happens after a run finishes. Runs take minutes, so an update may take seconds in a worker.

This split is what makes exact inference affordable.

| When | Method | Why |
|---|---|---|
| **After each checked attempt** | **Exact local refit.** NUTS on the touched Goal's local block (b, a, σ_ε, and c for the Procedure; about 10 dimensions). Global parameters are drawn from last night's stored global draws (a modular / "cut" update). About 1,000 new draws are stored per Goal | Exact where it matters, and takes seconds. Laplace / ADF are miscalibrated exactly in our regime (few observations, p near 0 or 1). SMC and SVGD are unnecessary: their failure modes (weight degeneracy, variance collapse) are high-dimensional, and ours is not |
| **Nightly** | **Joint refit** of everything, including θ drift, z, γ, δ, W and check error rates. Exact NUTS while the scale allows; beyond that, normalizing-flow VI (NumPyro `AutoBNAFNormal`) validated against NUTS on subsamples | Flows fix Gaussian VI's miscalibration but can still under-cover, so they are always checked against NUTS |
| **New Goal, zero runs** | **No inference.** The belief is the prior from §3 (parents + embedding), computed directly | A cold start is the prior by definition. Amortized neural estimators solve a problem we don't have |
| **New model** | Enters the nightly refit through its public or anchor-sweep observations (§9) | Same model, no special path. Market-share signals only nominate candidates |

**Parameterization rules** (required for both NUTS and VI to work):
- **Non-centered hierarchy.** Write b[g] = μ(g) + τ·ξ[g] with ξ ~ N(0,1), and the same for a[g] and c[π]. This removes the funnel geometry of hierarchical posteriors.
- **Identified low-rank factors.** The skill matrix z is lower-triangular with a positive diagonal (as in Bayesian factor analysis). Otherwise sign flips and rotations of (a, z) make the posterior multimodal.
- **Check error rates (α, β) are anchored by audited gold labels (§5).** Without them, "good model + lying check" and "bad model + honest check" cannot be told apart.

**If hosts don't follow the recommendation:** the host agent, not Stealth, runs the model. Log the recommended ladder, its propensity, and the unit actually used (reported).
- The Bayesian model uses all runs. This is valid under the assumption that the host's choice depends only on logged covariates.
- IPS / doubly robust estimates use only runs where the logged action was the recommended one.

**Kept as options, not used by default:**
- neural-linear heads (Riquelme et al. 2018), only as a learned φ for W·φ if the embedding prior underfits;
- epinets and bootstrapped Thompson sampling;
- expectation propagation;
- SMC for the drift model, only if the nightly θ random walk proves too slow to react.

## 9. Getting data where it matters: active offline sweeps

Running a Goal's frozen benchmark with unit u in the sandbox is how we buy information. We pick what to run by **expected value of information × demand**:

```
score(g, u) = demand(g) · E[ reduction in the regret of the ladder decision for g | observe u on g ]
```

- **demand(g)** is the number of runs plus committed Credits, aggregated over descendants. The economy data already exists.
- The sweep budget is a tenant/ops setting.
- This is adaptive testing from IRT, applied to routing.

## 10. Step-level routing (built)

A run.md node (one Procedure step) can be routed on its own. `plan_and_run` already gives
every node a concrete `check=` and runs nodes as one-line subagent tasks. So each step has
its own pass/fail signal, and switching models between steps costs almost nothing (no long
context to re-send).

- **The unit is (Procedure, step order).** Observations carry `step_order` and a `step_role`
  (plan / edit / verify / other); migrations 122/123.
- **Step terms in the model.** The logit gains `- d[π,k] + <e[π,k], z[m]>`, with
  `d ~ N(mu_role[role], tau_d²)` and `e ~ N(0, tau_e² I)`. A step can be easier or harder
  than the whole task and need different skills. A never-seen step uses its role's prior.
  Stored per Procedure as a `procedure_steps` posterior row.
- **One run = one instance.** All steps of a run share the run's eps: failing step 2 is
  evidence the run is hard, and the next step's recommendation starts stronger. Instances
  are keyed by (reporter, instance_key) across Goals, because sub-Procedure steps belong to
  other Goals.
- **The decision is a one-step rollout (Bertsekas).** The value of a step is the value of
  the whole run. The step's success at each eps node is multiplied by P(every remaining step
  succeeds | eps) under a base policy for those steps: the most reliable ladder over their
  three most reliable units. The reliability target (ρ) and the utility therefore refer to
  the WHOLE run. Each later step is re-solved properly, with the updated belief, when its
  turn comes. Without remaining steps this is exactly the task-level ladder.
- **Default value of a run** = value multiplier × the priciest candidate's step cost ×
  (1 + remaining steps).
- **Downstream breakage** (a step passes its own check but breaks a later one) is covered
  by the check's false-accept rate. Attributing a later failure back to an earlier step is
  not modelled.

## 11. Integration with Stealth

**Telemetry (the first thing to build).** Today no execution outcome records which model ran:
- `record_execution_outcome` and `report_execution` store success, context key, steps and criteria;
- `llm_spend` records only Stealth's *own* LLM calls.

Add to `report_execution` and to sandboxed `execution_runs`: model, version, scaffold, tokens (in / out / cached), cost, latency, the check kind and its partial result, the recommended ladder id, and the rung index. Sandboxed runs fill these in automatically; host runs self-report them, which the reporter-reliability term handles.

**Where things live:**

| Data | Where | Notes |
|---|---|---|
| `routing_decisions` log: context, candidates, chosen ladder, propensity, posterior predictions, outcome | **project B** (search/log DB) | **Exempt from `prune-operational`**, or aggregated before pruning. Off-policy evaluation needs history |
| Per-Goal posterior summaries: mean/cov of b, a, σ_ε; token models | **control DB (A)** | Small, one row per Goal × parameter block, read at decision time |
| Global parameters: θ, z, γ, δ, W, check error rates | control DB (A), versioned snapshot | Written by the nightly refit |
| Price table | control DB (A) | Operator-maintained, or imported from provider price APIs |

**API surface:**
- MCP tool **`recommend_models(goal_id, procedure_id?, step?, constraints)`** returns the ladder, each rung's P(success) with credible intervals, the expected cost, U, and "why": the top contributing terms.
- `find_ways` / `find_best_way` results get a **`model_evidence`** field per Procedure. It never changes the order.
- Worker job types: `routing_refit` (nightly), `routing_sweep` (§9).

**Privacy:**
- Private Goals' b, a and c are tenant-scoped parameters.
- Global parameters (θ, z, γ, δ, W) are fit on public data plus tenants that explicitly opt in.
- A tenant's private outcomes never shift what another tenant sees.

**Dependencies:**
- Frozen benchmarks on Goals, from the benchmark pipeline discussed earlier.
- Measured check error rates: the audit sampling in §5.

## 12. Evaluation and safety gates

0. **Inference correctness: simulation-based calibration** (Talts et al. 2018). Simulate data from the model's own prior, fit it, and check that the ranks of the true parameters are uniform. This tests the *computation*; item 1 tests the *model*. Run it on every inference change (local NUTS, nightly NUTS, flow VI).
1. **Calibration.** Reliability diagrams and expected calibration error (ECE) on held-out runs, sliced by model, by DAG depth, and by how much data the Goal has. A phase does not ship without passing it.
2. **Posterior predictive checks:**
   - does the model reproduce the observed rate of "the cheap rung failed, then the next one succeeded"?
   - is the independent-failures model clearly worse at this?
   - if not, σ_ε is mis-specified.
3. **Off-policy evaluation.** Doubly robust and self-normalised IPS (SNIPS) estimates of U for any new policy version, on logged data, before it goes live.
4. **Shadow mode first.** Log recommendations without showing them. Compare their predicted U with what hosts actually did.
5. **Live monitoring:** realised U, cost per verified success, the false-accept rate of delivered results, and drift alarms on θ.

## 13. Phases

| Phase | Scope | Exit criterion |
|---|---|---|
| **P0 Telemetry** | Fields in §11, `routing_decisions` table on B, prune exemption, price table | Every sandboxed run and ≥ X% of host reports carry unit + tokens + check |
| **P1 Offline model** | Ingest public per-instance results (SWE-bench), fit the §3 model + token model, calibration report | ECE below a threshold on held-out public + internal data; posterior predictive checks pass |
| **P2 Task-level ladders** | `recommend_models`, the §5 check model, the §6 solver with DP re-planning, Thompson sampling + propensities, `model_evidence` on `find_ways`; shadow mode, then live | OPE shows higher U than "always the strongest model" at equal or better accuracy; calibration holds live |
| **P3 Continuous learning** | Online updates, nightly refit with drift, reporter reliability, active sweeps (§9), per-tenant budget duals | Weekly calibration stable; sweep EVI measurably shrinks posterior width on high-demand Goals |
| **P4 Step level** | Recursive sub-Goal routing, joint step-role model, switching costs | OPE: step-level U > task-level U without an increase in false-accepts |

## 14. Decisions needed from you

1. **Who sets V and L?** Default per tenant, override per request? V can simply be "the cost of the strongest model's attempt × k".
2. **Default reliability target ρ and confidence 1 − δ** (e.g. ρ = 0.9, δ = 0.1).
3. **Which scaffolds to support first:** e.g. Claude Code, Codex CLI, Aider, the Stealth sandbox.
4. **Should self-reported host outcomes enter the likelihood from P1?** Weighted by the check model and reporter reliability. Or only from P3?
5. **Ladder length K** (3 proposed) and whether same-model retries are allowed (proposed: yes).

## 15. Claims to verify before building on them

- the IRT-Router (ACL 2025) results;
- the Bayesian routers from the inference research round (BayesRouter, the ICLR 2026 Bayesian Thompson-sampling router) and FlowVAT. These are baselines to compare against;
- Gemini's claims about "UniformMesh-N" and linear-cost expectation propagation (looked dubious);
- HiBayES ("< 20 observations");
- ParetoBandit;
- the two September 2026 arXiv papers (the scaffold-variance audit, AgentRouter);
- the OpenRouter Auto mechanism: spend share vs Not Diamond;
- that SWE-bench publishes per-instance results per submission;
- RouterBench vs LLMRouterBench.

The numbers quoted in both reports mostly come from secondary blogs. Check each against its primary paper.

---

## 16. Implementation status (first build)

Built: P0–P2 plus the P3 learning loop. Code is in `backend/app/routing/`. Tests:
- `test_routing_ladder_offline.py`;
- `test_routing_fit_offline.py`;
- `test_routing_e2e.py`;
- `test_routing_mcp_offline.py`.

**Corrections to this plan, found while implementing and testing:**
1. **The attempt-noise term η is removed.** For binary outcomes the Bernoulli-logit draw *is* the per-attempt randomness. An extra Gaussian η only rescales the logit, so it is not identifiable, and the sampler diverged on that ridge.
   - The instance difficulty ε stays. It is identified by instances attempted more than once: ladders, retries, and public results where several models attempt the same benchmark instance.
2. **Skill dimensions are capped at min(K, models − 1).** A Goal × model interaction beyond the main effects has at most that rank.
3. **The likelihood is computed in log space in float64** (log-sigmoid + logaddexp, with a "safe log" for exact zeros). In float32 the naive form underflows to log(0), which made every transition divergent.
4. **The local refit marginalizes the global draws as a J-component mixture inside NUTS.** It then does *defensive importance resampling* to one local draw per global draw, so every stored draw set stays row-aligned with the global draws. The per-draw weights are bounded by construction.

**Where things are:**
- **Decision (API, numpy only):** `predict.py` + `ladder.py`, task level and step level (§10). The ladder uses the exact Bayes-optimal continuation after rejections. The Thompson-sampling propensity is exact over the stored draws.
- **Inference (worker, JAX/NumPyro):** `model.py` + `fit.py`.
- **MCP:** two new tools, `recommend_models` and `report_model_run`, on both the v1 and v2 surfaces. No existing tool changed.
- **Admin commands:** `routing-status`, `routing-refit`, `routing-local-refit`, `routing-price`, `routing-model`, `routing-import`, `routing-audit`, `routing-sbc`.

**Not built yet:**
- the active offline sweeps (§9, EVI × demand), which need a sandbox that runs arbitrary models;
- per-tenant budget duals (no tenant budget setting exists yet);
- the latency weight;
- using the partial pass fraction as extra evidence (it is stored now);
- the flow-VI path validated against NUTS on subsamples (the path exists, and NUTS is used below `STEALTH_ROUTING_NUTS_MAX_LATENTS`).
