# Experience transfer on AutomationBench: prior results, evaluation hygiene, and a protocol

Date: 2026-09-25. Research only; no repo code was changed. Sources are the papers' arXiv pages or HTML versions,
plus a direct read of the local AutomationBench checkout (`AutomationBench/`, v1.0.6, commit 4a8e106).
When a number comes from a figure or from memory rather than a table, it says so.

---

## 0. Summary

1. **On AutomationBench, exact replay cannot hit held-out tasks.** The 600 scored public tasks have 600 distinct
   `task_name`s, and each is one hand-refined scenario. None are templated instances. If the earlier replay
   result ("worked when it hit") hit on tasks the agent had already seen, it measured memorization, not transfer.
   Any transfer on this benchmark has to come from *cross-task* knowledge: generic heuristics, app/API usage
   patterns, and formatting conventions.
2. **What survives held-out evaluation in the literature is modest and fragile.** Clean held-out benchmarks
   published in 2026 find that automatic experience methods give about 0 to +6 points on average and are
   negative in some cells. EvoAgentBench reports Memento -2.4 to +1.5, ReasoningBank +0.4 to +3.6, GEPA +1.2
   to +5.7, and a worst cell of -36.3. SkillsBench finds that "self-generated" skills give no benefit on average.
   The large reported gains (ALFWorld 73 to 89, Evo-Memory 0.24 to 0.78, AutoManual 97%, Agent KB +18 to +21)
   come from benchmarks with heavy task-type repetition, from streaming over the test set itself, or from
   curated or external knowledge. The Evo-Memory gain correlates with within-dataset task similarity (r = 0.717).
3. **Format.** Distilled, short, retrievable items beat both whole trajectories and one big static checklist.
   The static checklist matches the earlier negative result and the "full history" and context-collapse
   failures. Evidence for this:
   - AWM: abstract workflows beat concrete examples by +5 element accuracy.
   - ReasoningBank: strategy items at k=1.
   - ACE: itemized bullets with helpful/harmful counters.
   - Mem^p: step-level instructions combined with script abstractions.
   - SkillsBench: focused 2-3 module skills beat comprehensive docs.
   - Including *failures* helps only when they are distilled (ReasoningBank 46.5 to 49.7). Raw failures in
     memory degrade simple methods (Evo-Memory).
4. **Protocol in one line.** Use grouped K-fold cross-fitting over all 600 tasks, so every task is held out
   exactly once. Build one shared "train pool" of 3 fresh attempts per task. The no-memory attempts double as
   the baseline arm. Compare about 4 memory arms plus 2 controls, with 3 repeats per test task. The primary test
   is a paired task-level test on strict pass (Wilcoxon or sign test on per-task pass-rate differences, and
   McNemar for single-repeat), with a clustered bootstrap CI. That design has about 95% power at +5 points and
   more than 99% at +10 points. A 200-task, 2-domain pilot detects +10 points at roughly 80%.

---

## 1. Prior results: what was actually measured

Column "Held-out?" answers whether the evaluation tasks were disjoint from the tasks that populated memory.

| System | Benchmarks | Memory format | Headline effect (as reported) | Held-out? / caveats |
|---|---|---|---|---|
| **Reflexion** (Shinn 2023) [1] | ALFWorld, HotpotQA, HumanEval | verbal self-reflection | ALFWorld 130/134; HumanEval pass@1 91% | **No.** Retries on the *same* task. It is a within-task learning result and says nothing about cross-task transfer. |
| **ExpeL** (Zhao 2023) [2] | HotpotQA, ALFWorld, WebShop, FEVER | extracted insights + retrieved successful trajectories | HotpotQA 28.0 to 39.0; ALFWorld 40 to 59; WebShop ~31 to ~40 (figure) | **Yes.** Four-fold validation, about half train and half eval. Ablations: insights-only 36 / retrieve-only 31 (HotpotQA); ALFWorld retrieve-only 55 / insights-only 50. **Random retrieval gave only 42.5 vs 59 for similarity retrieval.** Insights written from reflections *hurt* (29 vs 39). Transfer HotpotQA to FEVER: 63 to 70. ALFWorld has only 6 task types, so similarity is high. |
| **Synapse** (Zheng 2023) [3] | MiniWoB++, Mind2Web | state abstraction + full trajectory exemplars, similarity-retrieved | MiniWoB++ 99.2% (64 tasks from 48 demos); Mind2Web +56% rel. step SR | Exemplars were **human** demonstrations, not the agent's own experience. MiniWoB++ is highly templated. |
| **Voyager** (Wang 2023) [4] | Minecraft | executable code skills | 3.3x unique items, up to 15.3x faster tech tree; skill library helps in a new world | Open-ended exploration, not a pass/fail held-out suite. Code skills work because the action space is code. |
| **AWM** (Wang 2024) [5] | WebArena, Mind2Web | induced *abstract* workflows (sub-routines with variables) | WebArena 23.5 to 35.5 (+12.0 abs, +51.1% rel); Mind2Web cross-task step SR 45.1 (GPT-4) | Offline AWM induces from Mind2Web *training* data, which is held-out. **Online AWM induces from the test stream using an LLM evaluator** (no ground truth), so test queries feed memory. Cross-website/domain gains of +8.9 to +14.0 abs. Abstract beats concrete examples by +5.0 elem acc; text vs code was a wash. |
| **AutoManual** (Chen 2024) [6] | ALFWorld, MiniWoB++ | typed rules ("Success Process", "Corrected Error", "Unsolved Error", ...) compiled into a manual | ALFWorld 97.4% (GPT-4-turbo), from 36 building tasks | Held-out, but ALFWorld (6 types) and MiniWoB++ are template-heavy. Online rule updating went from 90.7 to 97.4. The paper names the "path dependency" problem: blindly replaying earlier paths. |
| **Self-generated in-context examples** (Sarukkai 2025) [7] | ALFWorld, Wordcraft, InterCode-SQL | raw successful self-trajectories as exemplars + curation | ALFWorld 73 to 89 (93 with curation); Wordcraft 55 to 64; SQL 75 to 79 | The closest analogue to the planned design (own successes as exemplars). The gain shrinks as task diversity rises (SQL +4). |
| **Dynamic Cheatsheet** (Suzgun 2025) [8] | AIME, GPQA-D, Game of 24, MMLU-Pro | curated cheatsheet of strategies and code snippets | Game of 24 (GPT-4o) 10 to 99; AIME'24 (Sonnet 3.5) 23 to 50 | Test-time streaming over the eval set. Huge gains on *homogeneous* puzzles, where one reusable code snippet solves all of them. **Smaller models gained little or regressed.** Full-history appending underperformed. GPT-4o on GPQA dipped. |
| **Agent KB** (2025) [9] | GAIA, SWE-bench Lite, HLE, GPQA | ~9.5k structured experiences from *other* datasets; hybrid BM25 + embedding; reason-retrieve-refine | GAIA 55.2 to 73.9 pass@3; SWE-bench Lite 24.3 to 38.0 | Knowledge comes from external corpora (BrowseComp, SWE-Gym ...), not the agent's own past. The metrics are pass@3 and 50-iteration numbers. Hybrid retrieval beat either alone; top-k=3 was best; gains plateau at about 500 items. |
| **SkillWeaver** (2025) [10] | WebArena, 44 live sites | Playwright Python API skills from 160 exploration iterations per site | WebArena (GPT-4o) 12.3 to 22.6; 4o-mini 9.2 to 14.1 | Skills are per-website and exploration-derived. Skills from a strong model help a weak one. |
| **Memento** (2025) [11] | GAIA, DeepResearcher, SimpleQA, HLE | case bank of (state, plan, reward); K=4 retrieved | Case memory adds +3.7 to 6.7 F1 on DeepResearcher; claims +4.7 to 9.6 on OOD | The case bank grows over the evaluation iterations. In EvoAgentBench (below), Memento is **-2.4 to +1.5 on average, and -36.3 in one cell**. |
| **ReasoningBank** (2025) [12] | WebArena, Mind2Web, SWE-bench-Verified | memory items {title, description, content}: distilled strategies from successes **and failures**; k=1 | WebArena (Gemini-2.5-flash) 40.5 to 48.8; AWM 44.1; Synapse 42.1; Claude-3.7: 41.7 to 46.3 (AWM 40.8, *below* no-memory) | Streaming on the test set with an LLM judge (no ground truth). Successes-only 46.5 vs +failures 49.7. Saves up to 1.6 steps. In EvoAgentBench's held-out setup: **+0.4 to +3.6**. |
| **ACE** (Zhang 2025) [13] | AppWorld, FiNER, Formula | "playbook" of itemized bullets with helpful/harmful counters; incremental delta updates, dedup | AppWorld (DeepSeek-V3.1) test-normal TGC 63.7 to 76.2 (offline, labels); online 69.6 | AppWorld is the closest *published* analogue to AutomationBench (API-driven app tasks), with separate train and test splits. The authors say it degrades when the feedback signal is weak. It names "context collapse" (iterative rewrites erode detail) and "brevity bias". |
| **Mem^p** (2025) [14] | TravelPlanner, ALFWorld | procedural memory: step-level instructions + script abstractions ("proceduralization") | Gains across GPT-4o, Claude, Qwen; fewer steps and tokens | Combined format beats either alone. Query-based retrieval beats random. Reflection-based *update* is best over time. Retrieving too many memories plateaus or hurts. |
| **Memory management / experience-following** (Xiong 2025) [15] | EHRAgent, AgentDriver, CIC-IoT, RegAgent | stored (input, output) records | **Add-all hurts:** EHRAgent 16.75 to 13.05; AgentDriver 40.1 to 32.3. Strict-evaluator selective add: 38.5 and 51.0 | Agents copy the retrieved record when inputs look similar ("experience-following"). Errors propagate. Quality gating on what enters memory is the whole game. **This paper most directly supports StealthLab's "verified" framing.** |
| **Evo-Memory** (Wei 2025) [16] | ALFWorld, BabyAI, ScienceWorld, PDDL, AIME, GPQA, MMLU-Pro, ToolBench | 10+ memory modules, ExpRAG (plain retrieval), ReMem | Claude-3.7 multi-turn success 0.24 to ExpRAG 0.63 / ReMem 0.78 / AWM 0.49 / Mem0 0.50 | Streaming over each dataset. **Simple ExpRAG beats most complex designs.** The gain **correlates with within-dataset task similarity (r = 0.717)**. Mixing failures into memory degrades the simple methods. |
| **Mem0 / A-MEM / MemGPT** [17][18][19] | LoCoMo, MSC/DMR (conversational recall) | facts / notes / paged context | Mem0 +26% rel. LLM-judge vs OpenAI memory on LoCoMo | **Irrelevant** to procedural transfer. These measure recall of facts from long dialogues (also LongMemEval [20]). Do not cite them as evidence that "memory improves agents at tasks". |
| **SkillsBench** (2026) [21] | 86 tasks, 11 domains, 7,308 trajectories | curated vs self-generated Agent Skills | Curated: +16.2 pp average (range +4.5 to +51.9); **16 of 84 tasks negative**. Self-generated: **no average benefit** | Their "self-generated" skills are written by the model, not distilled from verified execution, so it is a different condition from ours. Focused 2-3 module skills beat comprehensive docs. |
| **SkillFlow** (2026) [22] | 166 tasks in 20 families, lifelong | agent-maintained skill library | Opus 4.6: 62.65 to 71.08; Kimi K2.5 +0.6 despite 67% skill usage; Qwen-Coder-Next regressed | **High usage does not mean gain.** Log uptake separately from benefit. |
| **EvoAgentBench** (2026) [23] | 528 train / 267 test across web research, algorithms, SWE, knowledge work | methods compared: Memento, ReasoningBank, GEPA, curated "Anchor" abilities | Automatic methods: -2.4 to +5.7 average; each has at least one negative domain cell (Memento -36.3); curated Anchor +5.8 to +10.5, positive in all 24 cells | **The best held-out template to copy.** The split uses a procedural-overlap graph: every test task shares at least one reusable ability with a train task, but no instance overlaps. It diagnoses extraction vs routing vs uptake failures separately. |
| **TAME** (2026) [24] | Trust-Memevo | executor-evaluator memory | Names "memory misevolution": trustworthiness declines as memory evolves on benign tasks | A reminder to track side effects (over-action) as well as pass rate. |
| **Agentic Plan Caching** (2025) [25] | several agent apps | keyword-matched plan templates adapted by a small LM | -50% cost, -27% latency, accuracy "maintained" | This is the efficiency case for replay. It works only when requests recur, which the AutomationBench public set does not have. |

### What held up on held-out tasks vs only on seen tasks

- **Held up (held-out split, own experience):**
  - ExpeL four-fold (+11 HotpotQA, +19 ALFWorld).
  - AWM offline on Mind2Web (cross-website and cross-domain).
  - Sarukkai (ALFWorld +16, SQL +4).
  - ACE offline on AppWorld test splits.
  - EvoAgentBench's automatic methods: small and inconsistent, +0 to +6.

  Every large held-out gain is on low-diversity task families (ALFWorld's 6 types, MiniWoB++).
- **Only measured in streaming/test-stream mode** (the memory is built from the evaluation tasks themselves, with
  an LLM judge): online AWM, ReasoningBank, Dynamic Cheatsheet, Evo-Memory, Memento. These are legitimate
  "test-time learning" numbers. They are not held-out transfer numbers, and they partly reward seeing
  near-identical items earlier in the stream.
- **External or curated knowledge:** Agent KB, SkillsBench curated, the EvoAgentBench Anchor. Large gains, but
  the knowledge did not come from the agent's own experience.

**Implication for AutomationBench:** the 600 tasks are bespoke and diverse (median 3 apps, 7-32 assertions
depending on domain). By the Evo-Memory similarity correlation and the EvoAgentBench results, a realistic
prior for held-out gain from own-experience memory is **+0 to +6 points strict pass**, and it may be negative
for some domains. Budget for detecting about 5 points, not 10.

### Which format worked best

- **Distilled, itemized, retrievable strategy units** beat raw trajectories on diverse tasks: ReasoningBank >
  Synapse and AWM; ACE bullets; AutoManual typed rules; the Mem^p combination.
- **Raw successful trajectories** win when tasks are near-duplicates (Sarukkai on ALFWorld; Evo-Memory ExpRAG)
  and lose when inputs are only superficially similar, because of experience-following [15].
- **Whole-manual / full-history injection underperforms** (DC full history; SkillsBench comprehensive docs;
  ACE's context collapse; StealthLab's own earlier static checklist).
- **Failures help only when distilled into a "why / what to check" lesson** (ReasoningBank, AutoManual's
  "Corrected Error" rules). Raw failure trajectories in memory hurt [16], and failure-derived insights from
  hallucinated reflections hurt (ExpeL 39 to 29).
- **Code skills** win when the action space is code or there is one reusable algorithm (Voyager, SkillWeaver,
  Game of 24). AutomationBench's tools are API calls, so code skills are a poor fit unless we let the agent call
  a macro tool.

---

## 2. Leakage and evaluation hygiene

### 2.1 AutomationBench-specific leakage findings (measured locally)

I measured these with a script over `automationbench/domains/*/tasks.py`:

- **One instance per task.** 600 unique `task_name`s (100 per domain) and 200 simple tasks. There are no
  seeds, parameterized templates, or instance variants.
  - Therefore "instance-level vs template-level split" collapses to a task-level split.
  - But **near-duplicate task pairs exist**, e.g. `hr.visa_expiry_tracking` and `hr.visa_expiration_monitoring`.
    The finance journal cluster is another: `monthend_journal_entries`, `qb_recurring_journal`,
    `closing_journal_automation`, `depreciation_schedule` have prompt-token Jaccard up to 0.66 and identical
    tool sets.
- **Shared named fixtures.** 28 spreadsheet or email fixtures (e.g. "FX Rates", "Escalation Policy",
  "Employee Directory" in 6 tasks) are shared by 2-6 tasks, touching **about 60 tasks**. A stored procedure
  that hard-codes a value from such a fixture could "transfer" by coincidence. These tasks must be kept in the
  same fold.
- **A shared noise pool.** About 20 noise email subjects appear in about 70 tasks each (injected by `_noise.py`,
  seeded by example_id). A memory could learn "ignore these specific emails". That is a benchmark artifact that
  would not transfer to the private set or to real deployments. It should be flagged in memory audits and
  ideally scrubbed: reject memory items that quote noise-pool strings.
- **42% of assertions are negative** (3,995 of 9,612: `*_not_sent`, `*_not_exists`, `*_not_updated`). Over-action
  fails tasks. "Don't do this" records are well matched to this failure mode.
- **Policy lives in the environment.**
  - About 404 of 600 initial states contain policy/guideline/SOP text.
  - About 146 contain superseded or updated policies.
  - 246 prompts say "per our policy".

  This is the most plausible source of *genuine cross-task transfer*: a generic procedure such as "search inbox
  and drive for the latest policy; newer supersedes older; follow override priority". The same holds for "check
  every list item before summarizing" and "preserve source values verbatim", which the paper's failure analysis
  names: false-confidence completion, shallow search, incomplete list processing, paraphrasing requirements [26].
- **The grader is exact-string.** For example `body_contains: ['$156,000', 'Enterprise']`. Formatting conventions
  are learnable across tasks, and memory must not contain train-task assertion text (see below).

### 2.2 Split method

- **Grouped K-fold cross-fitting**, as in ExpeL's four-fold validation and Miller's clustered errors [27], with
  groups defined as follows:
  1. Embed each task's user prompt, using the same embedder StealthLab uses. Link tasks with cosine similarity
     at or above a threshold (choose it so that obvious pairs like the visa pair link; start at about 0.85)
     **or** a shared named fixture from the list above.
  2. Take connected components as groups.
  3. Assign whole groups to folds, stratified by domain.
- **Report held-out results stratified by the nearest train neighbour's similarity** (terciles). This is the
  honest version of EvoAgentBench's overlap graph. You would expect gains concentrated in the top tercile. If
  gain appears *only* there, you are measuring near-duplicate reuse.
- **Do not use the `simple` domain as train for the scored domains unless it is its own arm.** It is a different
  distribution, and it is also a cheap place to smoke-test the pipeline.
- **Freeze the memory store per fold before evaluating that fold.** No writes during test (no streaming),
  otherwise the result becomes a test-time-learning number. Streaming can be a *separate, labelled* secondary
  experiment.
- **Label hygiene.**
  - Using `task_completed_correctly` and `partial_credit` on *train* tasks to decide "verified success" is
    legitimate. It is the train-set label, as in ACE offline "with labels".
  - Report a second variant with an **LLM judge instead of the grader** (the ReasoningBank/AWM-online setting),
    because a real deployment will not have assertions.
  - **Never let assertion JSON, or the grader's failure messages quoted verbatim, enter memory content.** That
    is rubric leakage. It teaches "the grader checks for 'Deal Closed Notification'".
  - Keep an automated check: fail the run if any memory item contains a string that appears in a *test* task's
    assertions but not in that test task's prompt or initial state.

### 2.3 Sample size, variance, tests

- **Variance is real.** The paper claims run-to-run variance "typically within 1%" of the aggregate [26]. That
  is aggregate stability, not per-task stability. In my simulation, per-task discordance between two single runs
  is about 30% with a realistic mix of task difficulties. τ-bench's pass^k shows the same per-task instability
  [28].
- **Primary test (paired, task-level):**
  - With 1 repeat per arm: **McNemar exact test** (binomial on discordant pairs).
  - With R > 1 repeats: compute per-task mean pass for each arm, then use a **Wilcoxon signed-rank** (or paired
    permutation test) on the per-task differences.
  - CI: **paired bootstrap over tasks, clustered by the similarity group** (resample groups, not tasks).
  - Also fit a logistic GLMM, `pass ~ arm + (1|task)`, as a robustness check.
- **Power (simulated):** 2,000 simulations per cell (600 for some), task difficulty drawn from Beta(0.6, ·) with
  mean 0.35, and a uniform logit shift giving the target average gain. α = 0.05, two-sided.

  | Held-out tasks | Repeats/arm | Power @ +10 pts | Power @ +5 pts |
  |---|---|---|---|
  | 100 | 1 | 0.34 | — |
  | 100 | 3 | 0.83 | — |
  | 180 (30% split) | 1 | 0.65 | — |
  | 180 | 3 | 0.99 | — |
  | 300 | 1 | 0.89 | 0.30 |
  | 300 | 3 | 1.00 | 0.78 |
  | 600 (cross-fit) | 1 | 1.00 | 0.60 |
  | 600 | 3 | 1.00 | 0.95 |

  The analytic McNemar check agrees: discordance ψ = 0.3 and δ = 0.10 need n ≈ 233 for 80% power.
  **Conclusion:** a single 70/30 split with one run is under-powered even for +10 points (65%). Cross-fitting
  over all 600 tasks with 3 repeats is powered for +5 points.
- **Multiple arms:** apply Holm correction across arm-vs-baseline comparisons. Pre-register one primary arm and
  one primary metric (strict pass).
- **Reporting standards** (drawing on ABC checklist [29], "AI Agents That Matter" [30], HAL [31], Miller [27]):
  - Report per-arm pass with CI and the paired difference with CI.
  - Report per-domain results, cost and tokens per task, and the cost-accuracy Pareto point.
  - Report the number of runs, model id with provider route and date, temperature, and reasoning effort.
  - Report the benchmark version (v1.0.6), commit, toolset, and max steps.
  - Report every task that errored or hit infrastructure faults. Count them as fail and report them separately.
  - Publish per-task results and the memory store snapshot per fold.

---

## 3. Retrieval and injection at test time

**What the literature supports:**

- **Top-k small** (k=1 ReasoningBank; 3 Agent KB and DC-RS; 4 Memento). Performance plateaus or drops with more
  (Mem^p, Memento, Agent KB).
- **Similarity-retrieved, not random:** ExpeL random retrieval 42.5 vs 59.
- **Hybrid lexical + dense, fused**, which beats either alone (Agent KB). This matches StealthLab's RRF.
- **Items shaped as {title, when-applicable, steps/lesson, pitfalls}**, not transcripts.
- **Two stages beat one:** plan-time retrieval, then retrieval again on failure or error signals. This is Agent
  KB's retrieve-then-refine; removing Refine cost 6 points. ReMem refines memory during the episode.
- **An applicability/uptake gate is valuable.** Agent KB uses a disagreement gate (β = 0.8), and experience-following
  [15] shows that superficially similar items get copied. StealthLab's non-compensatory applicability cascade
  is the right shape here. The experiment should *measure* it: the gate on vs off.

**Known failure modes of memory:**

- Negative transfer and experience-following: the agent copies entity names, recipients, or amounts from a
  retrieved record.
- Distraction and context bloat: full history, comprehensive docs, the earlier static checklist.
- Context collapse after repeated rewriting (ACE).
- Weak models failing to use or seed memory (DC, SkillFlow).
- Memory learning benchmark artifacts (the noise pool).
- Misevolution: side effects creeping up (TAME).

**Presentation options to compare:**

1. **Start-of-task injection:** top-k distilled items appended to the *user* message (or a system addendum)
   under a clear header, "Past experience (may not apply; verify against current policy)". Cap it at about 600
   tokens.
2. **Recall as a tool:** `recall_experience(query)` is registered via the runner's `tools=` hook
   (`AutomationBenchEnv.__init__(..., tools=...)` in `automationbench/runner.py`). The agent decides when to
   call it. The cost is lower when the agent doesn't call it, but uptake is uncertain, so log call rate.
3. **Per-step/failure-triggered:** re-query when a tool returns empty or error results, or before the final
   message ("verify checklist"). This is more engineering; do it as a secondary arm.

**Standard controls and ablations** (make them token-matched where noted):

- **No memory**: the baseline.
- **Random memory, token-matched**: random items from the same fold's store, same k and token budget. This
  separates "relevant content" from "any extra context / longer prompt". ExpeL used a random-retrieval control.
- **Wrong-domain memory**: retrieve only from other domains' stores. This tests domain specificity and matches
  Agent KB's cross-domain asymmetry analysis.
- **Generic-instructions placebo**: a fixed hand-written paragraph of generic advice ("find the latest policy;
  process every item; don't over-act") of the same length. This is critical on AutomationBench because much of
  the plausible gain is generic.
- **Oracle memory (upper bound)**: memory built from *the test task's own* successful train-pool attempt,
  retrieved by exact goal. This is the replay ceiling. It is only interpretable as an upper bound and must never
  be reported as transfer.
- **Successes-only vs successes+failures**, as ReasoningBank did.
- **Applicability gate on/off.**

---

## 4. AutomationBench specifics

From the paper [26], the README, and a local code read.

- **Construction:** synthetic, "generated based on use cases from real customers" (workflow shapes only),
  drafted with frontier models, and manually refined through many iterations.
  - Hardening: irrelevant data, key info behind tool calls, ambiguity about where info lives, similarly-named
    wrong entities, and strict business policy with overriding priorities.
  - 47 apps, about 500 endpoints.
  - Each task has trigger data (a single user message; the agent may not ask questions), an initial world state,
    and an assertion-based rubric evaluated on the final state.
- **Size:** 600 public (100 per domain) plus 200 `simple` tasks (excluded from the score). There are 600+
  private tasks, and the official leaderboard is scored only on the private set, which is "purposely harder".
  - This means experiments on the public set do not contaminate official scores.
  - It also means public-set gains are not leaderboard claims.
  - The README says public improvements are "likely (but not guaranteed)" to carry over.
- **Rules on use:** the repo is MIT-licensed. I found no rule against using public tasks for training or memory.
  The README describes `partial_credit` as the "environment reward signal for denser training", and the
  benchmark ships as a Prime Intellect RL environment, which implies training use is anticipated. The only
  norm is the one above: do not report public-set results as official.
- **Versioning matters.**
  - Scores rose from under 10% for every frontier model in the paper (Opus 4.7 at 9.9%) to 26-50% in the
    current README (Opus 5 max at 50.3%), after fairness fixes in 1.0.5/1.0.6. For example, v1.0.6 added Drive
    access where a sheet ID was otherwise undiscoverable, and relaxed over-strict formatting.
  - Pin v1.0.6 or the current commit (`4a8e106`).
  - Also note that many historical agent failures were benchmark bugs. A memory that learned workarounds for
    since-fixed bugs is stale.
- **Toolsets:** `api` (the default; raw API schemas, all tools exposed), `zapier` (search/execute meta-tools),
  `limited_zapier` (only task-relevant tools). For Gemini 3.1 Pro these scored 9.6 / 12.8 / 14.3% in the paper.
  The toolset is a large confound: fix one, preferably the default `api`.
- **Transfer outlook:**
  - Against: the tasks are bespoke, with unique entities and policies, so specific procedures rarely apply and
    exact replay never hits held-out tasks.
  - For: (a) recurring apps (Google Sheets in 528 of 600 tasks, Gmail 415, Slack 343, Salesforce 111), so there
    is API-usage know-how, including how to find a spreadsheet ID via Drive and query semantics; (b) recurring
    policy-discovery and supersession patterns; (c) recurring failure modes (false completion, partial list
    processing, over-action on the 42% negative assertions, paraphrasing values); (d) some near-duplicate
    clusters, especially in finance and HR.
  - Expect gains mostly from (a) and (c), which a generic placebo may partly capture. That is why the placebo
    control is essential.

---

## 5. Cost realism

- **Paper cost per task** (frontier models, early version): Opus 4.7 $1.80; GPT-5.4 $1.93; Sonnet 4.6 $1.81;
  Gemini 3.1 Pro $0.54; Haiku 4.5 $0.18.
- **Steps per task:** Opus 12.6 steps / 29.8 tool calls; Gemini 21.8 / 35.4; GPT-5.4 15.4 / 43.9. The max is
  50 steps and rarely hit.
- **The runner records usage** (`_extract_usage_and_debug` in `runner.py`, `usage.py`, `pricing.py`) and
  supports `--input-cost/--output-cost` for OpenRouter pricing.
- **Budget model.** Let c be the dollar cost per run. Memory injection adds less than 1k tokens per run; recall
  as a tool adds more.
  - Train pool: 600 × 3 = 1,800 runs. This is shared across folds and doubles as the baseline arm.
  - Evaluation: 600 × (A arms) × 3 repeats. With A = 4 memory arms + 2 controls, that is 10,800 runs.
  - Distillation LLM calls: about 1,800 short calls. This is small.
  - **Total about 12.6k runs.**
    - At c = $0.15 (cheap OpenRouter model): about $1.9k.
    - At c = $0.50: about $6.3k.
    - At frontier c ≈ $1.8: about $23k.
- **Pilot (detects +10 at about 80%):**
  - Tasks: 2 domains, 200 tasks, grouped 2-fold.
  - Train pool: 3 attempts = 600 runs.
  - Evaluation: 2 arms (best memory format + random token-matched control) × 200 × 2 repeats = 800 runs.
  - About 1.4k runs, roughly $200-700 with a mid-priced model.
  - A 100-task / 1-repeat pilot has only about 34% power at +10 and is not worth running as a decision test.
- **Prompt caching:** v1.0.6 enabled prompt caching. Memory text placed in the system prompt will break cache
  prefixes unless it is appended *after* the stable prefix. Put it at the end of the user message, and report
  cached vs uncached tokens.

---

## 6. Recommended protocol (implementable)

### 6.1 Fixed settings

- AutomationBench v1.0.6 at commit `4a8e106`, `--toolset api`, `--max-steps 50`.
- One OpenRouter model with a pinned route and provider. Fix temperature and reasoning effort.
- Record the date. Run all arms of a fold interleaved in time, to limit provider drift.

### 6.2 Domains

- Use **all 6 scored domains** (600 tasks), because power needs the tasks.
- If the budget forces a subset, use **finance + HR** for the pilot. They have the most visible near-duplicate
  clusters and the cheapest prompts (median 7 and 9 assertions), so they give the best chance of seeing a
  signal first. Then report whether support and marketing (32 and 18 assertions) behave differently.
- Keep `simple` out of scoring. Use it for pipeline smoke tests.

### 6.3 Split

- **Grouped, domain-stratified 3-fold cross-fitting** (§2.2). Groups are prompt-embedding components (threshold
  about 0.85) unioned with shared-named-fixture components.
- For each fold f, memory M_f is built only from train-pool runs of tasks outside f, then **frozen**.
- Every task is evaluated once as held-out.

### 6.4 Train pool

- **3 independent no-memory attempts per task** (all 600, run once).
  - With a pass rate around 35%, about 60% of tasks yield at least one success.
  - Tasks with 0/3 yield failure lessons only.
  - More attempts give diminishing returns; ExpeL and AutoManual used 1-3.
- Attempt #1 (or all 3) on fold-f tasks **is the baseline arm** for fold f. It is valid because M_f never saw
  those runs.
- Record per task: pass, partial credit, tokens, steps, tool calls, and failing assertion *types*. Keep assertion
  contents out of memory.

### 6.5 Memory construction (per fold; one extractor version, logged)

- **P (verified procedures):** from passing runs only, via StealthLab's normal ingestion.
  - Content: goal abstraction, app sequence, parameterized steps with entity values replaced by variables
    (AWM style), and preconditions.
  - Rejection filter: drop items containing any noise-pool string or any literal ID, email, or amount from the
    source task.
- **L (lessons):** distilled {title, when-applicable, lesson} items from successes *and* failures
  (ReasoningBank/ACE-bullet style). Failures are contrasted with a success of the same task when one exists
  (ExpeL-style comparison).
  - "Don't do this" items must name the observable trigger, e.g. "an updated policy email exists",
    "excluded items".
- **T (raw trajectories):** compressed successful tool-call traces (Synapse/Sarukkai style). This is the
  "ordinary memory" comparison.

### 6.6 Arms on held-out tasks (3 repeats each)

| Arm | What the agent gets |
|---|---|
| A0 | no memory (from the train pool; or fresh runs if you want strict time matching) |
| A1 | T: top-3 raw trajectories, injected at start |
| A2 | P: top-3 verified procedures + applicability gate, injected at start (**StealthLab primary**) |
| A3 | L: top-5 lessons (successes + failures), injected at start |
| A4 | P+L as a `recall_experience` tool (on demand), with the gate |
| C1 | random items from M_f, token-matched to A2 |
| C2 | generic placebo paragraph, token-matched |
| Optional C3 | wrong-domain memory |
| Optional C4 | oracle (own-task successful trace), upper bound only |
| Optional | A2 with the gate off |

- Injection cap: about 800 tokens, appended to the end of the user message under a "may not apply" header.

### 6.7 Metrics

- **Primary:** strict pass (`task_completed_correctly`), paired difference A2 − A0.
- **Secondary:**
  - partial credit;
  - input and output tokens and $ per task, plus $ per *passed* task;
  - steps and tool calls;
  - negative-assertion violation rate (over-action);
  - retrieval hit rate and uptake. Uptake means the agent's actions reference the retrieved steps; for A4 it
    also means the tool-call rate.
  - per-domain results and per-similarity-tercile results.

### 6.8 Statistics

- Wilcoxon signed-rank on per-task mean-pass differences (McNemar if R = 1).
- 95% clustered-bootstrap CI (10k resamples over similarity groups).
- Holm correction across arms vs A0.
- Pre-register A2 vs A0 as the primary comparison, and A2 vs C1/C2 as the "is it the content" comparison.
- With 600 tasks × 3 repeats, power is about 0.95 at +5 points.

### 6.9 Decision rule

Claim "substrate helps" only if:

- A2 > A0 is significant **and** A2 > C2 (placebo) is significant or at least positive with a CI excluding
  large negatives;
- no domain shows a significant regression;
- cost per passed task does not rise by more than X% (set X beforehand).

---

## 7. Most likely ways the result could mislead

1. **Near-duplicate reuse mistaken for transfer.** The finance and HR clusters and shared fixtures could produce
   the whole gain. Check the similarity-tercile breakdown and the grouped split.
2. **The placebo explains it.** Generic "find latest policy / process every item / don't over-act" advice may
   deliver most of the gain. Without C2 you would credit the substrate.
3. **Context-length or attention effects.** Longer prompts alone change behaviour. C1 (random, token-matched)
   addresses this.
4. **Rubric leakage.** Assertion strings or grader messages end up in memory. Guard with the automated check in
   §2.2.
5. **Benchmark-artifact learning.** Memory about noise-pool emails or since-fixed v1.0.5 bugs inflates the
   public-set gain and won't carry to the private set or real use.
6. **Selection on train successes.** If only tasks the model already solves produce procedures, memory may just
   make easy tasks slightly more reliable. Report gain by baseline difficulty (per-task A0 pass rate: 0/3, 1-2/3,
   3/3).
7. **Non-stationary provider.** OpenRouter routes to different backends and quantizations over time. Pin the
   provider and interleave arms.
8. **Aggregate-only reporting.** A +3 average can hide +10 in one domain and -8 in another (EvoAgentBench,
   SkillsBench: 16 of 84 negative). Report per-domain results and per-task win/loss counts.
9. **Streaming contamination.** If memory writes during evaluation, the number becomes test-time learning
   (ReasoningBank/DC style), not held-out transfer. Freeze per fold.
10. **Weak-model floor.** If the chosen model passes under 15% on train, there are too few verified successes to
    build P, and DC/SkillFlow show weak models can't use memory anyway. Check train-pool yield before running
    arms.
11. **Label mismatch with deployment.** Grader-verified success is stronger than anything production has. Report
    the LLM-judge-labelled variant as well.
12. **Cost story reversed.** Memory can cut steps (ReasoningBank -1.6 steps, APC -50% cost) or add tokens (the
    earlier static checklist). Report $ per passed task, not only pass rate.

---

## References

1. Reflexion — https://arxiv.org/abs/2303.11366
2. ExpeL — https://arxiv.org/abs/2308.10144 (numbers from https://arxiv.org/html/2308.10144)
3. Synapse — https://arxiv.org/abs/2306.07863
4. Voyager — https://arxiv.org/abs/2305.16291
5. Agent Workflow Memory — https://arxiv.org/abs/2409.07429 (https://arxiv.org/html/2409.07429)
6. AutoManual — https://arxiv.org/abs/2405.16247
7. Self-Generated In-Context Examples Improve LLM Agents — https://arxiv.org/abs/2505.00234
8. Dynamic Cheatsheet — https://arxiv.org/abs/2504.07952
9. Agent KB — https://arxiv.org/abs/2507.06229
10. SkillWeaver — https://arxiv.org/abs/2504.07079
11. Memento — https://arxiv.org/abs/2508.16153
12. ReasoningBank — https://arxiv.org/abs/2509.25140 (https://arxiv.org/html/2509.25140)
13. Agentic Context Engineering (ACE) — https://arxiv.org/abs/2510.04618
14. Mem^p — https://arxiv.org/abs/2508.06433
15. How Memory Management Impacts LLM Agents (experience-following) — https://arxiv.org/abs/2505.16067
16. Evo-Memory — https://arxiv.org/abs/2511.20857
17. Mem0 — https://arxiv.org/abs/2504.19413
18. A-MEM — https://arxiv.org/abs/2502.12110
19. MemGPT — https://arxiv.org/abs/2310.08560
20. LongMemEval — https://arxiv.org/abs/2410.10813 ; LoCoMo — https://arxiv.org/abs/2402.17753
21. SkillsBench — https://arxiv.org/abs/2602.12670
22. SkillFlow — https://arxiv.org/abs/2604.17308
23. EvoAgentBench — https://arxiv.org/abs/2607.05202 (https://arxiv.org/html/2607.05202)
24. TAME — https://arxiv.org/abs/2602.03224
25. Agentic Plan Caching — https://arxiv.org/abs/2506.14852
26. AutomationBench paper — https://arxiv.org/abs/2604.18934 (https://arxiv.org/html/2604.18934); repo https://github.com/zapier/AutomationBench
27. Miller, "Adding Error Bars to Evals" — https://arxiv.org/abs/2411.00640
28. τ-bench (pass^k) — https://arxiv.org/abs/2406.12045
29. Establishing Best Practices for Building Rigorous Agentic Benchmarks (ABC) — https://arxiv.org/abs/2507.02825
30. AI Agents That Matter — https://arxiv.org/abs/2407.01502
31. Holistic Agent Leaderboard (HAL) — https://arxiv.org/abs/2510.11977

Notes on provenance:
- Reflexion, Voyager, Mem0, A-MEM, MemGPT, LoCoMo/LongMemEval, τ-bench, ABC, AI Agents That Matter and HAL
  figures are from their abstracts or well-known headline results. They were not re-verified against tables in
  this session.
- ExpeL WebShop values were read from a figure and are approximate.
- The EvoAgentBench, ACE and Memento ablation numbers were extracted from the HTML papers by a summarizing
  fetch. Spot-check the tables before quoting them externally.
- The local analysis scripts were throwaway files in /tmp. They are not saved in the repo.
