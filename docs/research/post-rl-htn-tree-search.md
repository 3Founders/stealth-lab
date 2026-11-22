# Post-RL, HTN, tree search: how to think about it for our system

Written 2026-10-14. Question from the founder: "we want to turn our framework into something post RL HTN tree search thing, how can we
think about that?" This is a thinking aid plus a small experiment plan, not a design to build. Confidence tags: **P** peer-reviewed or
venue page, **A** arXiv abstract read (results are the authors' own, not replicated by me), **R** repo file read in full or in the cited
range, **U** unverified (said from memory or a secondary source; do not rely on it). I read the abstract pages of about 25 papers, not the
full texts, except where a repo file is cited. Any number from a paper is the authors' claim.

## 0. The reading of the question I tested, and the correction

My reading: "post-RL" = after the era of improving agents by training policy weights with RL; "HTN tree search" = at inference time, search
over hierarchical decompositions (methods with preconditions, primitive tasks), with a library of methods as the action space and
tests/verifiers as the reward.

Correction after reading the repo: **we already run a degenerate version of this search.** The dispatch path is a depth-limited search
whose branching is "which model x scaffold next", whose leaf reward is the project's check, and whose value function is a Bayesian
posterior (section 1). What is missing is not search as such. Three things are missing: (i) the action space has no "which procedure /
which decomposition" axis at run time, (ii) the old HTN engine is gone, so nothing decomposes, (iii) nothing measures whether more search
(width or depth) pays at our verifier quality. The honest framing is therefore "extend a model-routing search to also choose methods and
subgoals, then measure", not "replace RL-trained agents with a new paradigm".

## 1. What exists in the repo, and what does not

**HTN agent: deleted.** `experiments/swebench_pro/htn_agent.py` (1,884 lines, `HTNAgent` -> `AugmentedHTNAgent` -> `ResearchHTNAgent`, no
DB access) is gone from this checkout: `experiments/swebench_pro/` does not exist, and `backend/app/execution/graph_executor.py:1-27` says
HTNAgent was "the only code that ever walked a multi-node dependency graph" and "was deleted". `ROADMAP.md:85` still lists
`backend/app/execution/htn_agent.py` as the DAG engine; that line is stale (R). Ticket 15 records what the engine did well and what
should survive a rebuild: per-node fresh context, validated DAG parsing, `deps` vs `requires` (failure cascades along `requires` only),
localized replanning, a lock-guarded budget, the "discard patch when no subgoal finished" safety valve
(`.scratch/memory-substrate/issues/15-htn-relocation-and-hyperparameters.md:15-21`, R). It also records that no HTN code was reachable from
any endpoint or MCP tool (line 21) and that the eight hyperparameters were measured on SWE-bench and are suspect elsewhere (lines 23, 78-88).

**Scheduler for a plan graph: exists, no search.** `execute_task_graph` (`graph_executor.py:101-145`) runs nodes in dependency order and
blocks only transitive dependents of a failed node; outcomes are success / needs_rework / failure. It takes `run_node` as an injected
callback (lines 11-19), so any executor, including a search policy, can be plugged in. `PlanNode` has `deps`, `binding`,
`verification_gate`, `cost_budget` (`backend/app/models/plan.py:65-84`, R). `ProcedureStep` is deliberately planner-neutral, with no
`deps` field, so the HTN-specific binding was meant to be produced at instantiation time
(`backend/app/services/procedure_extraction/schema.py:60-80`, R).

**Method library: written, not called.** `backend/app/services/method_library.py` stores one row per HTN subgoal decomposition
(`CREATED_BY = "htn_method_library"`, line 42; header lines 2-31) and has a precondition-style gate on touch tags (lines 54-69). Its own
docstring says `persist_plan()` stores only successes (survivorship bias; `failure_capture.py:3-4`). I found no caller of
`find_reusable_plan` / `persist_plan` in `backend/app`, `experiments`, or `packaging/src` (grep, 2026-10-14), so treat it as dormant (R).

**Applicability = the HTN precondition check, as a hard filter.** `backend/app/services/applicability.py:1-20`: a non-compensatory cascade
(preconditions, state, scope, exclusions, temporal validity, verification status, staleness), similarity ranks only survivors; "a violated
precondition is a disqualification, not a low score". Executable environment checks are deferred (lines 27-29); automatic scope/exclusion
narrowing is not built (lines 33-36) (R). Retrieval is the opposite on purpose, RRF fusion (CLAUDE.md "Code conventions").

**Routing as a search value function: built, and more than a heuristic.** `backend/app/routing/ladder.py:1-47` (R):
- reach / P(ok) / P(wrong) / E[cost] / U = V P(ok) - L P(wrong) - E[cost] for an ordered ladder of (model x scaffold) units;
- the check has false-accept alpha and false-reject beta, per posterior draw;
- a shared latent instance difficulty eps correlates rungs, integrated at the end (this is the part ordinary independent-attempt math gets wrong);
- after a rejection the belief over eps is updated exactly, so the re-solved ladder is the Bayes-optimal continuation;
- choice is Thompson sampling with an exact propensity, under a chance constraint Pr(P(ok) >= rho) >= confidence;
- step-level mode multiplies each step by P(all later steps succeed | eps) under a base policy: a one-step rollout in the Bertsekas sense.
`docs/model_routing_plan.md:215-240` (section 10) describes step-level routing as built, keyed on (Procedure, step order); line 295 gives
its proving test: off-policy evaluation, "step-level U > task-level U without an increase in false-accepts" (R). Whether that test has been
run, I did not find.

**Dispatch: the runtime search loop.** `packaging/npm/lib/dispatch.mjs:1-14, 35-60`: the plan's cheap rungs run before the session model,
in a worktree, verified by the project check (no check, no dispatch); a pass is applied and recorded, a fail escalates; policy knobs
include `maxRungs` (default 3), `parallel` (cheap rungs at once, first pass wins), `explore` (0.2, the exploration rate), test-first
generated tests, a strict cross-check, per-model time caps (R). Every attempt is reported to the plan and counted, so the posterior learns.

**Measured so far.** `docs/HANDOFF_abtest_2026-10-11.md` section 11 (table near lines 768-774, R): on 153 paired tasks, always-session-model
(A) solved 140 for $18.05; dispatch (C) solved 137 for $10.77 (-40% cost) after 56 re-runs; MultiPL-E JS 19/20 = A at -61% cost. Latency:
C median 42-55 s vs A 15-37 s. `docs/findings.md:15, 52-53` (DS-1000): the ladder with a perfect check was a simulation upper bound
(89-93% vs 82%), and with no check it collapses to the cheap model (66%). `docs/routing_priors_eval.md` (verdict paragraph, R): on one strong/weak
pair, no router, ours included, was distinguishable from random routing on APGR (ours 0.463, CI [0.308, 0.604]). That is a warning for
anything that claims "learned value guides search" without a pair whose models fail on different items.

**Summary table**

| Piece of an HTN-search system | In the repo? | Where |
|---|---|---|
| Methods with preconditions, steps, invariants | Yes (as Procedures) | `procedure_extraction/schema.py`, `procedures.py` |
| Precondition filter (action masking) | Yes | `applicability.py` |
| Executable preconditions | No (deferred) | `applicability.py:27-29` |
| Decomposition at run time | No (engine deleted) | `graph_executor.py:1-27` |
| Plan-graph scheduler with failure containment | Yes | `graph_executor.py` |
| Stored decompositions reused | Dormant | `method_library.py` |
| Value of a model choice, exact, Bayesian | Yes | `routing/ladder.py` |
| Step-level lookahead (one-step rollout) | Built; proving OPE not found | `model_routing_plan.md:215-240, 295` |
| Verifier-in-the-loop with alpha / beta | Yes in the model; alpha/beta on our real checks not measured (I found no number) | `ladder.py:1-47` |
| Search over procedures at run time | No | n/a |
| Negative-transfer / utility-problem instrumentation | Called for, not measured | ticket 15, lines 53-77 |

## 2. Primary sources

### 2.1 Classical HTN planning

- **Erol, Hendler, Nau, "HTN Planning: Complexity and Expressivity", AAAI 1994, pp. 1123-1128** (P). Abstract: most practical planning work
  of the prior fifteen years used HTN decomposition, with little analysis; the paper "describes how the complexity of HTN planning varies
  with various conditions on the task networks". https://mlanthology.org/aaai/1994/erol1994aaai-htn/ . A companion UMD report states HTN
  planning is strictly more expressive than STRIPS-style planning under several expressivity definitions
  (https://drum.lib.umd.edu/items/5f23ccd8-7881-4df9-96b9-1927a78dde2e/full , via search snippet, A). **The specific complexity classes
  (for example undecidability with unrestricted recursion) I could not read from an abstract page: unverified.** What matters here, if
  that is right (U): method libraries with recursion are not just "more search"; restricting their shape is what keeps planning tractable.
- **Nau et al., "SHOP2: An HTN Planning System", JAIR 20:379-404, 2003** (P): a domain-independent HTN planner that earned one of the top
  awards at the 2002 International Planning Competition; methods are written by hand by domain experts. https://jair.org/index.php/jair/article/view/10362 (listing via search).
- **SHOP3**: open source HTN planner in Common Lisp, MPL licensed. https://github.com/shop-planner/shop3 (README; it says nothing about SHOP2).
- **HDDL**, Hoeller et al., AAAI 2020, pp. 9883-9891: a PDDL extension for hierarchical problems, used as the input language of the
  hierarchical track at IPC 2020 (P). https://ojs.aaai.org/index.php/AAAI/article/view/6542 .
- What classical HTN gives that LLM planners lack: the methods are *given* and *sound*; search is over which method applies and in what
  order, and a returned plan is correct by construction. What it costs: someone writes the methods. Our procedures are the learned,
  evidence-scored version of that method library, and our applicability cascade is the precondition check.

### 2.2 LLMs with HTN and hierarchical planning

- **ChatHTN**, Munoz-Avila, Aha, Rizzo, arXiv:2505.11814 (May 2025) (A). Interleaves symbolic HTN decomposition with ChatGPT-generated
  decompositions; abstract: "provably sound; any plan it generates correctly achieves the input tasks". This is the cleanest template
  for us: the LLM proposes methods, the symbolic layer (our applicability + checks) decides what is allowed. https://arxiv.org/abs/2505.11814
- **Online Learning of HTN Methods for integrated LLM-HTN Planning**, Xu and Munoz-Avila, arXiv:2511.12901 (Nov 2025, ACS 2025) (A). Learns
  methods from LLM decompositions; "reduces the number of calls to ChatGPT" while solving at least as many problems; two domains, no
  numbers in the abstract. https://arxiv.org/abs/2511.12901 . Same idea as `method_library.py`, with a measurement we do not have.
- **HTN Planning with LLM-Generated Heuristics**, Meneguzzi et al., arXiv:2605.07707 (May 2026; submitted to NeurIPS 2026) (A). Six total-order
  HTN domains, nine LLMs; LLM-written heuristics "nearly match the coverage of the best available HTN planner" and cut search effort on
  83% of shared problems. Relevant as: the LLM as prior over search, search itself still symbolic. https://arxiv.org/abs/2605.07707
- **ADaPT**, Prasad et al., NAACL 2024 Findings, arXiv:2311.05772 (A). Decompose "as needed", only when the executor fails; up to +28.3%
  ALFWorld, +27% WebShop, +33% TextCraft. Matches the repo's inversion in ticket 15 (planner as fallback) and the dispatch ladder (try cheap, decompose/escalate on failure). https://arxiv.org/abs/2311.05772
- **Kambhampati et al., "LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks", ICML 2024** (P/A). Position: autoregressive LLMs
  "cannot, by themselves, do planning or self-verification"; pair them with external model-based verifiers. https://arxiv.org/abs/2402.01817 . It is a position paper, not an experiment.

### 2.3 Tree search for LLM agents and code

- **LATS**, Zhou et al., arXiv:2310.04406 (A): MCTS + LM value function + self-reflection + environment feedback; HumanEval pass@1 92.7% (GPT-4),
  WebShop 75.9 (GPT-3.5) without gradient updates. https://arxiv.org/abs/2310.04406
- **RAP**, Hao et al., EMNLP 2023, arXiv:2305.14992 (A): LLM as world model and agent with MCTS; LLaMA-33B with RAP beats GPT-4 CoT by 33% relative on plan generation. https://arxiv.org/abs/2305.14992
- **SWE-Search**, Antoniades et al., arXiv:2410.20285 (A): MCTS with a hybrid LLM value function for SWE agents; "23% relative improvement
  ... across five models"; more inference compute (deeper search) helps. https://arxiv.org/abs/2410.20285 . Value function is LLM-judged, not a test result; the verifier-quality caveat in 2.5 applies.
- **PlanSearch**, Wang et al., arXiv:2409.03733 (A): search over *natural-language plans* before code; Claude 3.5 Sonnet LiveCodeBench pass@200
  77.0% vs 60.6% for plain repeated sampling and 41.4% pass@1 without search. Evidence that searching over plans (a decomposition-like axis) beats searching over code samples. https://arxiv.org/abs/2409.03733
- **AFlow**, Zhang et al., arXiv:2410.10762 (A): MCTS over code-represented workflows; +5.7% average over baselines on six benchmarks, and smaller models beating GPT-4o on specific tasks at 4.55% of its cost. This is search over *procedures*, offline, which is the closest published analogue to "search over our library". https://arxiv.org/abs/2410.10762
- **Agentless**, Xia et al., arXiv:2407.01489 (A): a fixed three-phase pipeline (localize, repair, validate), no agent choosing actions, 32.00% SWE-bench Lite at $0.70. A reminder that the baseline for "complex search" is a simple pipeline. https://arxiv.org/abs/2407.01489
- **Reflexion**, arXiv:2303.11366 (A): verbal self-reflection from feedback; HumanEval pass@1 91%. https://arxiv.org/abs/2303.11366

### 2.4 Skill and procedure libraries

- **Voyager**, arXiv:2305.16291 (A): code-as-skills library in Minecraft via black-box GPT-4, no fine-tuning; 3.3x unique items, 15.3x faster tech-tree milestones; the library transfers to a new world. https://arxiv.org/abs/2305.16291
- **ExpeL**, AAAI 2024, arXiv:2308.10144 (A): extracts natural-language insights from experience, recalls them at inference; no numbers in the abstract. https://arxiv.org/abs/2308.10144
- **Agent Workflow Memory**, Wang et al., arXiv:2409.07429 (A): induced workflows; +24.6% (Mind2Web) and +51.1% (WebArena) relative; online AWM's advantage grows from 8.9 to 14.0 points as train/test distributions diverge. https://arxiv.org/abs/2409.07429
- **SkillWeaver**, Zheng et al., arXiv:2504.07079 (A): web agents synthesize skills as APIs; +31.8% WebArena, +39.8% real sites; skills from strong agents help weaker ones by up to 54.3%. https://arxiv.org/abs/2504.07079 . Strong-to-weak transfer is exactly our cheap-model story.
- **ADAS**, Hu, Lu, Clune, arXiv:2408.08435 (A): a meta-agent writes agents in code and searches an archive; discovered agents beat hand-designed ones in coding, science, math; no numbers on the abstract page. https://arxiv.org/abs/2408.08435
- **Surveys (2026):** "A Comprehensive Survey on Agent Skills" arXiv:2605.07358 (listed only; contents not read); "Dynamic Agent Skills: A
  Lifecycle Survey and Taxonomy of Evolving Skill Libraries", Li, arXiv:2607.10113, TMLR 2026 (A): 124 papers, eight-stage lifecycle, notes
  "flat retrieval can degrade as libraries grow" and that benchmarks "under-report" usage-utility gaps; the terms "negative transfer" and
  "utility problem" do **not** appear in the abstract (I did not read the full text, so this does not confirm the repo's claim that the whole survey omits them). https://arxiv.org/abs/2607.10113
  **SkillSmith**, arXiv:2606.01314 (A): models skills as interacting, some interfering; "Skills compete for context"; up to +10.2 / +9.8 points over EvoSkill on OfficeQA / SealQA with Qwen3.5. https://arxiv.org/abs/2606.01314

### 2.5 Test-time compute, verifiers, and their limits

- **Let's Verify Step by Step**, Lightman et al., arXiv:2305.20050 (A): process supervision beats outcome supervision on MATH (78% on a subset); releases PRM800K. Math, human step labels. https://arxiv.org/abs/2305.20050
- **Snell et al., "Scaling LLM Test-Time Compute Optimally ...", arXiv:2408.03314** (A): effectiveness of verifier-guided search vs adaptive revision depends on prompt difficulty; compute-optimal allocation improves efficiency over best-of-N by more than 4x; on problems where a small model has non-trivial success, test-time compute can beat a 14x larger model. https://arxiv.org/abs/2408.03314
- **Large Language Monkeys**, Brown et al., arXiv:2407.21787 (A): coverage grows log-linearly with samples; SWE-bench Lite 15.9% (1 sample) to 56% (250 samples) with DeepSeek-Coder-V2; with automatic verifiers coverage turns into end performance, without them majority vote and reward-model selection plateau. https://arxiv.org/abs/2407.21787
- **"The Limits of Inference Scaling Through Resampling"**, Stroebl, Kapoor, Narayanan, arXiv:2411.17501 (v3 March 2026) (A): imperfect verifiers (unit tests with false positives) put a ceiling on resampling that more compute cannot remove; optimal attempts "often fewer than 10"; a weaker model cannot catch up by resampling. https://arxiv.org/abs/2411.17501 . **This is the most important paper for us**: our alpha is this false-positive rate.
- **SWE-Gym**, Pan et al., ICML 2025, arXiv:2412.21139 (P/A): 2,438 Python tasks; fine-tuning gives up to +19 points on SWE-Bench Verified/Lite; trained verifiers at inference reach 32.0% Verified / 26.0% Lite. https://arxiv.org/abs/2412.21139
- **R2E-Gym**, Jain et al., NeurIPS 2025, arXiv:2504.07164 (A, via search summary; abstract page itself not fetched): test-based and execution-free verifiers each saturate near 42-43%; hybrid reaches 51% on SWE-Bench Verified. https://arxiv.org/abs/2504.07164 . Evidence that tests alone have "low distinguishability" for selection.

### 2.6 The "post-RL" angle: weights vs memory, search, test-time learning

- **Yue et al., "Does RL Really Incentivize Reasoning Capacity in LLMs Beyond the Base Model?", NeurIPS 2025, arXiv:2504.13837** (A): at large k,
  base models have *higher* pass@k than RLVR-trained models; RLVR helps at small k; reasoning patterns "originate from and are bounded by the base model". Supports "sample + verify" as competitive in coverage; it is a statement about math/code reasoning pass@k, not about agents with tools. https://arxiv.org/abs/2504.13837
- **Training-Free GRPO**, Cai et al., arXiv:2510.08191 (A): learn "experiential knowledge as a token prior" with group-relative semantic advantages, no parameter updates; claims to beat fine-tuned small LLMs with "a few dozen training samples" on DeepSeek-V3.1-Terminus math and web search. No numbers on the abstract page. https://arxiv.org/abs/2510.08191
- **Agentic Context Engineering (ACE)**, Zhang et al., ICLR 2026, arXiv:2510.04618 (A, via search summary): evolving "playbooks" in context; +10.6% on agent benchmarks, +8.6% finance; matches the top AppWorld production agent with a smaller open model; names *brevity bias* and *context collapse* as failure modes of memory rewriting. No independent replication found. https://arxiv.org/abs/2510.04618
- **ReasoningBank**, Ouyang et al., ICLR 2026, arXiv:2509.25140 (A, via search summary): distills strategies from successes *and failures*; memory-aware test-time scaling spends extra compute to produce better memory. https://arxiv.org/abs/2509.25140 . Failures-as-data matches `failure_capture.py`.
- **Memento**, Zhou et al., arXiv:2508.16153 (A): case-based memory, no LLM fine-tuning; GAIA validation 87.88% Pass@3, test 79.40%; case memory adds 4.7-9.6 points out of distribution. https://arxiv.org/abs/2508.16153
- **Agent Learning via Early Experience**, Meta/OSU, arXiv:2510.08558 (A, via search summary): train on the agent's own action outcomes without reward; may be a better start for RL. A reminder that weight updates and memory are being combined, not replaced. https://arxiv.org/abs/2510.08558
- **TTRL**, Zuo et al., arXiv:2504.16084 (A): RL at test time from unlabeled data using majority-vote rewards; ~211% pass@1 gain on AIME 2024 for Qwen-2.5-Math-7B. This is test-time *weight* update, so not what we do, but it shows the boundary is not "RL vs no RL". https://arxiv.org/abs/2504.16084

**What the sources do and do not support.** They support: (a) memory/skill libraries improve agents without weight updates on web, GAIA, AppWorld
tasks (author-reported); (b) search over plans/workflows helps on code benchmarks; (c) verifier quality caps the benefit of more attempts.
They do **not** show that library + search beats RL-trained agents on repository-level SWE tasks at equal cost. I found no head-to-head, and
most memory results are web/math/GAIA. "Post-RL" is a direction in the literature (rhetorically and in a few results), not a demonstrated
replacement. The two approaches also compose: SWE-Gym and R2E-Gym train weights *and* use inference-time verifiers.

## 3. How to think about it for our product

### (a) State, action, transition, reward, if procedures are HTN methods

- **State** s = (task text; repo snapshot / worktree hash; set of subgoals verified so far; belief over difficulty eps and over unit/method
  parameters, i.e. the posterior draws and node weights already in `ladder.py`; attempt history on this instance; remaining budget in $
  and seconds). The belief is the part we already have and published search systems do not (LATS, SWE-Search use an LLM-written value
  string).
- **Action** a = (method, unit, mode). *Method* = a Procedure that passes the applicability cascade (hard-masked: violated precondition
  means the action does not exist), or "fresh plan" (the planner fallback of ticket 15). *Unit* = model x scaffold, as in the ladder.
  *Mode* = attempt the whole goal / decompose into the procedure's steps / escalate / stop and hand to the session model. Applicability
  being non-compensatory is the right design here for the same reason action masking is right in planning: it prunes before value is
  computed (`applicability.py:1-20`). RRF similarity only orders the survivors.
- **Transition** = run the unit in a worktree (as `dispatch.mjs` does), observe the check outcome. Stochastic, and the observation is
  *noisy*: the check accepts wrong answers with probability alpha. So this is a POMDP over eps with a noisy observation, which the ladder
  already treats exactly for depth up to `maxRungs` (`ladder.py:1-47`).
- **Reward** = the ladder's utility, U = V P(ok) - L P(wrong) - E[cost] (`ladder.py:7-14`), where P(wrong) is the cost of a silently
  accepted bad change. This is the point where we differ from RL-with-tests: a bare "tests pass" reward ignores alpha.
- **Terminal** = check passes and cross-check does not object (strict mode, `dispatch.mjs:44-46`), budget out, or escalation to the session
  model.

Subgoals add one structural requirement: each decomposed step needs its own check (`PlanNode.verification_gate`, `plan.py:65-84`) or the
decomposition cannot be searched, because there is no per-subgoal reward. Where a step has no check, treat it as not decomposable.

### (b) Where the search happens

Two levels, and I would do only the outer one first.

1. **Outer, across units at dispatch time (exists).** Branch on model x scaffold x method, evaluate with the exact ladder, run sequentially or
   in parallel (`policy.parallel`). Cheap leaf runs (hundreds of tokens to tens of thousands, `findings.md`) make wide search affordable, and
   a pass is verified before it is applied. This is Large Language Monkeys-style coverage (2.5) bought with *heterogeneous* models, which the
   repo's evidence says fail on different items (12 of 15 of Sonnet's failures solved by some open model, `findings.md:20-22` region, R).
2. **Inner, within one agent run (does not exist; skip for now).** LATS / SWE-Search put the tree inside a single long run, with an
   LLM value function. For us that means a stateful worktree tree, expensive rollbacks, and an LLM-judged value in place of a test result.
   It also reintroduces what ticket 15 warns about: hyperparameters tuned on one domain. Only reach for it if the outer level shows
   headroom in experiment 1 below.

Decomposition (HTN proper) sits between the two: a procedure's steps become sub-tasks, each dispatched with its own ladder. That is what
step-level routing (`model_routing_plan.md:215-240`) was built for, and it is untested as far as I found.

### (c) How the exact ladder / Bayesian routing becomes the value and prior

- **Value function:** U of the best continuation ladder under the current belief is already a value function for a node in the search tree;
  the "continuation" term (P all later steps succeed | eps) is the rollout value of the rest of the plan. Extending the ladder's unit
  set to (method, unit) pairs makes the method a new coordinate in the same model; no new machinery in principle (a Procedure is already a
  Goal with a posterior, `model_routing_plan.md` section 10).
- **Prior over actions:** the cold-start cards and bundled prior give a model's ability before it has run on this repo
  (`docs/routing_priors.md`; cards beat the old prior by +0.130 nats/obs [0.066, 0.195] on unseen models, `routing_priors_eval.md`). For
  methods, the analogue is the procedure's evidence record (attempts/successes by unit), shrunk toward its Goal's parents. Ticket 15
  names the right estimator: empirical-Bayes shrinkage, needing ~30-50 executions per procedure type before per-procedure beats a global default
  (lines 90-97). So method-level priors will be weak for a long time on a new customer. Say so.
- **Exploration:** Thompson sampling with exact propensity is already there; propensities make off-policy evaluation of a *different*
  search policy possible on logged runs without re-running. That is the cheapest way to test deeper search (see experiment 1).
- **Warning:** `routing_priors_eval.md` shows our router not separable from random on the one tested pair. A value function that does not rank
  items better than chance adds nothing to a search. The value must be shown to discriminate on *this* task mix first.

### (d) What verified procedures give that RL does not, and where RL or search wins

What we have that a weight update does not:
- **Auditability.** A procedure carries source, preconditions, evidence record and the ChangeSet that created it; a policy change is a
  tensor diff. Matches the "peer review for agent procedures" framing in `SOLUTION.md`.
- **Bi-temporal invalidation, nothing deleted.** A stale procedure closes its validity window; you cannot un-train a weight.
- **Per-customer data.** Procedures learned from a customer's traces stay scoped to them (`CLAUDE.md` hard rule 2); fine-tuning on customer
  traces raises data-governance questions we avoid.
- **No GPU loop; works with API-only and open models alike.** Same argument ExpeL, Voyager and Training-Free GRPO make for black-box models
  (2.4, 2.6).
- **A check that carries alpha/beta**, so a bad accept is a priced risk, not an invisible one.

Where RL or search would beat us (be honest):
- **Internalised skill, zero retrieval cost.** SWE-Gym reports up to +19 points from fine-tuning on SWE-Bench Verified/Lite (2.5, A). A procedure
  costs context tokens and match cost; the repo's own data shows a confident neighbouring snippet *broke* 5 of Sonnet's successes while
  fixing 6 (`findings.md:50-53`, R). RL does not have that failure mode in that form.
- **Capability the library cannot hold.** A library stores what has been done and verified. Novel multi-file reasoning is not a lookup. Yue et
  al. say RLVR mostly sharpens what the base model could already do; that does not mean libraries create new capability either.
- **Scale of search data.** Hundreds of thousands of rollouts train a policy; our library sees what a customer's agents happen to do.
- **Latency.** Dispatch median 42-55 s vs 15-37 s (`HANDOFF_abtest_2026-10-11.md` section 11). Tree search adds more.
- **Where no test exists,** neither approach works well, but RL with a learned reward at least trains for it; our dispatch refuses without a check.

### (e) The smallest experiments, with numbers

All use data we have (153 paired tasks, per-rung attempt logs under `experiments/abtest/.local/results`, MultiPL-E, the dispatch path).
Write the success rule down before running; the sample is small, so use paired tests.

1. **Headroom and verifier quality, offline, $0.** From the recorded attempts on the 153 tasks compute: (i) union solve count over all cheap
   models and sessions (pass@k with heterogeneous units), (ii) alpha and beta of the check actually used (test-first tests, strict
   cross-check) against ground-truth grading, (iii) the oracle ladder's cost and solves. *Success numbers:* union >= 145 solved means
   there is search headroom above C's 137 and A's 140; alpha <= 0.10 means search will not mostly buy false accepts (Stroebl et al. say the
   cap is set by this). *Falsifier:* union <= 141 or alpha >= 0.25: more width cannot pay on this set; stop and fix the check first.
   Also run OPE of a deeper/wider policy using the logged Thompson propensities.
2. **Procedure as method vs fresh plan, matched control.** On the subset of the 153 tasks where the applicability cascade returns a verified
   procedure, run the same dispatch with and without the procedure in the brief; same models, same seeds, paired. Record wins, losses, and
   negative transfers separately (ticket 15 asks for exactly this control, lines 65-69). *Success:* net >= +3 solves on that subset with
   losses <= half of wins, at <= +10% cost. *Falsifier:* net <= 0, or losses >= wins (this is what `findings.md:52` already shows for one hook on
   Sonnet).
3. **Width vs depth in the outer search.** Hold models fixed. Compare sequential ladder (`parallel=off`, `maxRungs=3`) vs parallel width 2
   and 3 vs `maxRungs=5`, with the shared-fail stop on, on the 153 tasks (and MultiPL-E as a quick check). *Success:* >= 139 solved
   (A is 140, C 137) at <= $10.77, and a McNemar-type paired test not worse than C. *Falsifier:* extra rungs/width add <= 2 solves for
   >= +20% cost (the "fewer than 10 attempts is optimal" regime).
4. **Decomposition vs flat (needs new tasks).** Step-level routing with the continuation term vs task-level ladder, on tasks that really
   have separable, individually checkable steps. MultiPL-E single functions will not test this; the long-horizon harness is listed as untested
   (`HANDOFF_abtest_2026-10-11.md` section 11). *Success:* the OPE rule already written at `model_routing_plan.md:295`.
5. **Value function discrimination.** Does the posterior's P(ok) for the *first* rung rank held-out tasks better than chance (AUC) on the 153
   tasks, per unit? *Success:* AUC >= 0.65 per unit with a CI that excludes 0.5. This is the guard against the `routing_priors_eval.md`
   result repeating.

Order: 1, then 2, then 3. Experiment 1 costs nothing and decides whether 3 is worth the money.

### (f) Honest risks and what would falsify the idea

- **Verifier-bound.** Everything above is gated by alpha (Stroebl et al.; R2E-Gym finds tests alone have "low distinguishability"). If our
  check's false-accept rate is high, search produces confident wrong patches faster. Falsifier: experiment 1 (ii).
- **Small n.** 153 tasks; the gap A - C is 3 solves. Differences of one to three solves are not distinguishable from noise without
  repeats (the repo records that 12 of 82 Sonnet outcomes flipped between identical runs, `findings.md`, R).
- **Decomposition may have nothing to decompose.** Most of what dispatch handles are small, single-function-class tasks; the old HTN engine's
  SWE-bench constants were measured on a different workload (ticket 15). If experiment 4 cannot find tasks with separable checkable steps,
  HTN structure adds overhead (context for plans, per-step checks) with no gain.
- **Utility / negative transfer.** More methods in the library can lower performance (ticket 15, lines 53-77); the 2026 survey abstract does not
  name it (2.4). Instrument, do not assume.
- **Methods are text, not symbolic.** ChatHTN's soundness holds because preconditions stay symbolic. Ours are claim-based predicates under a
  closed-world assumption, and executable preconditions are deferred (`applicability.py:27-29`). Without them, "HTN" is a label for
  "retrieve a procedure and run its steps".
- **Cost of adding search.** Latency already trails always-session-model; users notice 40 s.
- **The label.** "Post-RL" is not shown to be better than RL-trained agents on repo-level tasks by anything I could verify. Presenting it as
  a replacement would be a claim we cannot back. The defensible claim is narrower: for a customer whose data cannot be trained on, an
  auditable method library plus checked, cost-aware search gets part of the benefit without weight updates, and we can measure how much.
- **What would falsify the whole direction:** experiments 1 and 3 both flat (union near C, extra width/depth no gain), and experiment 2 net
  <= 0. Then the product value is routing + check, not search over methods.

## 4. What I could not verify

- Specific complexity results of Erol/Hendler/Nau (for example undecidability under recursion, EXPSPACE for the totally ordered case): the
  abstract page gives none and I did not open the PDF. Anything said about them here is marked U.
- Full texts: I read abstract pages (or search-engine summaries, flagged) only. The numbers for LATS, SWE-Search, AFlow, AWM, SkillWeaver,
  ACE, ReasoningBank, Memento, Training-Free GRPO, TTRL, R2E-Gym are the authors' claims and I did not check replication. ACE, ReasoningBank,
  Early Experience and R2E-Gym were read through search summaries plus venue listings, not their own abstract pages.
- Whether any paper compares library + search against RL-trained agents on repository-level SWE tasks at equal cost: I found none; absence
  of evidence from a limited search, not proof.
- The contents of the two 2026 skill surveys beyond the abstract (so whether the whole text omits negative transfer is not established).
- "Mind2Web / WebArena" and similar web results transfer to coding agents: unverified.
- Repo side: whether the step-level OPE proving test (`model_routing_plan.md:295`) was run; the alpha/beta of our real checks; the
  per-rung attempt logs (local-only results, I did not open them); callers of `method_library.py` outside `backend/app`, `experiments`,
  `packaging/src`.
- Nothing was executed; no model, database or server was started for this note.
