# Benchmarks & datasets for evaluating / seeding the verified-experience substrate

Research date: 2026-09-27. Research-only; no repo code touched.
Framing: **Research** (Band 3 measurement). Question: which public benchmarks let us test
"verified solved example transfers to unseen relatives" beyond single-shot coding, and which
published per-instance results / trajectories can seed the model recommender.

Verification legend: **[V]** checked directly (LICENSE file, GitHub API `license.spdx_id`, HF API
`license:` tag, or file contents downloaded and counted). **[C]** stated on the official
card/README/paper page but not independently re-derived. **[U]** could not confirm — treat as unknown.

---

## 0. Headline findings

1. **Explicit family structure with automatic state checks exists in agent benchmarks, and it is
   better than anything in single-shot coding.** Best three: AppWorld (250 scenarios x 3 tasks,
   state-based unit tests), tau2-bench (telecom: 2,285 tasks composed from 3 issue families;
   retail/airline ship official train/test splits), WebArena / WebArena-Verified (812 tasks over
   190 intent templates, LLM judge removed in Verified).
2. **Directly on-topic 2026 benchmarks now exist** (SkillEvolBench, AFTER, EvoAgentBench). Their
   early findings echo ours: SkillEvolBench reports "raw-trajectory reuse frequently outperforms
   distilled skills" [C], same direction as our "nearest verified example +8-10 vs procedure +2".
   Licenses are messy (see table) — use for methodology and as a comparison point, not seed data,
   until licenses are clarified.
3. **Per-instance multi-model results for agents are available and permissive**: tau2 leaderboard
   (50 of 70 submissions have trajectories on a public, unauthenticated S3 bucket — verified by
   listing it), Terminal-Bench 2.0 leaderboard (Apache-2.0, 40 GB of per-trial results),
   OSWorld-Verified trajectories (MIT), Toolathlon trajectories (17 models x 3 runs, with cost,
   CC-BY-4.0), DABstep task_scores (1.02M per-task-per-submission rows, CC-BY-4.0),
   LLMRouterBench (33 models, 400K instances with tokens and cost, includes tau2 and SWE-bench).
4. **SWE-bench Verified should not be a primary measurement target.** Epoch rates it "Flawed"
   (2026-09-03) citing OpenAI's Feb-2026 audit: 59.4% of audited tasks had tests that reject
   correct solutions, plus verbatim-solution contamination [C via Epoch review; OpenAI page returned 403].
5. **Text-to-SQL gold labels are badly broken**: VLDB/CIDR 2026 paper measures 52.8% annotation
   errors in BIRD Mini-Dev and 62.8-66.1% in Spider 2.0-Snow [C]. Do not use them to measure small
   transfer effects without a corrected subset.

---

## 1. Ranked shortlist (grouped by need)

| Rank | Candidate | Need(s) served | One-line why for us |
|---|---|---|---|
| 1 | **AppWorld** | 1, 2, 4 | 250 scenarios x 3 tasks with DB-state unit tests: solve one sibling, test on the other two — cleanest "family transfer" design for multi-step API agents. |
| 2 | **tau2-bench** (airline/retail/telecom/banking_knowledge) | 1, 2, 3, 4, 5 | Already vendored locally; official train/test splits, compositional telecom families, policy-bound customer service (non-coding), plus 50 public multi-trial trajectory sets for routing seed. |
| 3 | **WebArena-Verified** (on WebArena) | 1, 2 | 812 tasks / 190 templates, deterministic scoring after LLM-judge removal; template = family. |
| 4 | **WorkArena (L1 -> L2/WorkArena++)** | 1, 2 | 33 parametric atomic task types (19,912 seeded instances) plus 682 compositional L2 tasks: test "atomic verified solutions transfer to compositions". |
| 5 | **AutomationBench** (current next test) | 2 | 600 hand-written business-workflow tasks, strict end-state assertions, MIT; weak family structure (unique tasks) — pair with a family benchmark. |
| 6 | **Terminal-Bench 2.0 / 2.1 / 2-verified** | 2, 3, 4 | Ops/devops with test scripts; Apache-2.0 leaderboard dataset with per-trial results across dozens of agents. No families. |
| 7 | **SWE-rebench V2 + nvidia Open-SWE-Traces + SWE-rebench OpenHands trajectories** | 1, 3, 4 | Fresh, decontaminated SWE tasks (32K+, 20 languages) with multi-attempt resolved/unresolved trajectories; repo = natural family. |
| 8 | **SWE-smith (+ trajectories)** | 1, 4 | 50K+ synthetic bugs across 128 repos — many bugs per repo = family; MIT; resolved labels. |
| 9 | **LLMRouterBench** | 3 | 400K query x model outcomes with tokens and cost incl. tau2 and SWE-bench; direct recommender seed. |
| 10 | **DABstep** | 3, 5 | Non-coding data analysis (payments fees) with 1.02M per-task submission scores; test answers hidden. |
| 11 | **Toolathlon (+ Trajectories)** | 2, 3, 4 | 108 long-horizon (~20 turn) tasks, 32 apps, script-verified; 17 models x 3 runs with cost. |
| 12 | **OSWorld-Verified (+ trajectories)** | 2, 4 | 369 desktop tasks, execution-based checkers; MIT trajectories from many models. GUI-heavy, costly. |
| 13 | **SkillEvolBench / AFTER / EvoAgentBench** | 1 | Built for exactly our question (skill/experience transfer across task families); use as external comparison once licenses are clear. |
| 14 | **BFCL v3/v4** | 2, 3 | Cheap AST/state-checked function calling with categories; good routing features, weak for experience transfer. |
| 15 | **CRMArena-Pro** | 1, 5 | 22 business task types x many instances, exact-match; **CC-BY-NC** — research only. |

Routing-only data (need 3), ranked: LLMRouterBench > tau2/Terminal-Bench/OSWorld/Toolathlon
leaderboards > EmbedLLM (Apache-2.0 correctness matrix) > DABstep task_scores > LiveCodeBench
submissions > HELM raw / Epoch Inspect logs > RouterBench (no license) > SPROUT (no license, LLM-judged).

---

## 2. Detail tables

### 2a. Family transfer + multi-step agents

| Field | AppWorld | tau2-bench | WebArena-Verified / WebArena | WorkArena / WorkArena++ |
|---|---|---|---|---|
| Links | [paper 2407.18901](https://arxiv.org/abs/2407.18901) · [repo](https://github.com/StonyBrookNLP/appworld) · [site](https://appworld.dev/appworld) | [repo](https://github.com/sierra-research/tau2-bench) · [leaderboard](https://taubench.com) · local: `c:\Users\chait\Prog\3Found\vendor\tau2-bench` | [WebArena repo](https://github.com/web-arena-x/webarena) · [Verified repo](https://github.com/ServiceNow/webarena-verified) · [docs](https://servicenow.github.io/webarena-verified/) | [repo](https://github.com/ServiceNow/WorkArena) |
| Year / maintainer / active | 2024 (ACL'24 best resource) / Stony Brook / active, pushed 2026-09-04 [V] | 2025 / Sierra / active, local copy dated 2026-08-26, v1.0.1 [V] | 2023 CMU; Verified 2025 ServiceNow, pushed 2026-03-08 [V] | 2024 / ServiceNow / active (instance pool updated 2026-09-25 [V]) |
| Tasks | 750 = 250 scenarios x 3 tasks; train 105, dev 60, test_normal 168, test_challenge 417 [C] | airline 50 (train 30/test 20), retail 114 (74/40), telecom 114 base / 2,285 full / 20 small, banking_knowledge 97 (698 docs) [V, counted] | 812 tasks, **190 distinct intent_template_id** (119 templates have exactly 5 tasks) [V, counted]; Verified hard subset 258 [C] | L1: 33 task types, 19,912 seeded instances; L2: 682 compositional tasks [C] |
| Family structure | Explicit: scenario = family of 3 tasks; Test-C uses unseen apps (Amazon, Gmail) — cross-app transfer split built in | Explicit: telecom IDs are `[issue_family]subtask_combo[PERSONA]` — 3 families (mms 1,984 / mobile_data 254 / service 47) [V]; retail/airline official train/test | Explicit templates (e.g. "top-N best-selling products in {period}") | Explicit parametric types (seeds) + L2 composes L1 atoms |
| Check | DB-state unit tests incl. collateral-damage checks; TGC and SGC (scenario goal completion) [C] | DB final-state compare + required actions/communicated info; user is an LLM simulator (source of variance) [V code] | Original: program_html 411, string_match 335, url_match 205, **LLM fuzzy_match 118** [V counted]. Verified: LLM judge and substring matching removed, every task/evaluator reviewed [C] | Programmatic validators + oracle "cheat" functions [C] |
| Check reliability | Strong; state-based. No public audit found [U] | v1.0.1 fixed 75+ tasks; results <1.0.1 not comparable [C]. User-sim nondeterminism → run 4 trials (leaderboard requires 4+) | Original had many reported evaluator bugs; Verified is the fix | Good; deterministic validators [C] |
| Per-instance results | Baseline outputs downloadable; leaderboard [C] | **Yes**: 70 submissions in repo, 50 with trajectories on public S3 `s3://sierra-tau-bench-public/submissions/` (unauthenticated listing verified) | Zeno / execution traces released for original [C] | Via AgentLab/BrowserGym leaderboard [C] |
| Trajectories | Some released, more promised [C] | Full JSON trajectories, pass^1..pass^4 per task [V] | Human trajectories ~170 tasks + agent traces [C] | Not found [U] |
| License | Apache-2.0 [V LICENSE]; protected task data must be redistributed **encrypted only** [C README] | MIT (c) 2025 Sierra Research [V]; S3 trajectory files carry no separate license [U] | Apache-2.0 both [V GitHub API] | Apache-2.0 [V LICENSE]; ServiceNow instances under ServiceNow developer terms [U] |
| Cost to run | `pip install appworld`, local Python server, no Docker. ~20-50 steps; est. $0.2-1/task on mid-tier models; train+dev 165 tasks ≈ $50-165 | Pure Python. ~15-40 turns incl. user sim. retail test 40 x 4 trials ≈ $40-120 | 6 self-hosted Docker sites (large disk; AMI available) or Verified's replay; ~30 steps; $0.5-1.5/task | Needs a ServiceNow instance (free developer PDI or gated HF pool); browser; short L1 episodes (~$0.1-0.3), L2 longer |
| Contamination | Task data encrypted to prevent scraping — low | Public since 2024 (tau-bench), medium; telecom/banking newer | Public since 2023; medium-high; Verified offers hard subset | Low-medium (seeded generation) |
| Why us | **Family transfer on multi-step API agents, scenario-split by design** | Families + train/test + routing seed + trajectories, already vendored | Template-split family transfer on web | Atomic -> compositional transfer |

| Field | AutomationBench | Terminal-Bench 2.x | Toolathlon | OSWorld-Verified |
|---|---|---|---|---|
| Links | [repo](https://github.com/zapier/AutomationBench) | [2.0 repo](https://github.com/harbor-framework/terminal-bench-2) · [HF 2.0](https://huggingface.co/datasets/harborframework/terminal-bench-2.0) · [leaderboard data](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard) · [zai 2-verified](https://huggingface.co/datasets/zai-org/terminal-bench-2-verified) | [paper 2510.25726](https://arxiv.org/abs/2510.25726) · [repo](https://github.com/hkust-nlp/Toolathlon) · [trajectories](https://huggingface.co/datasets/hkust-nlp/Toolathlon-Trajectories) | [repo](https://github.com/xlang-ai/OSWorld) · [trajs](https://huggingface.co/datasets/xlangai/ubuntu_osworld_verified_trajs) · [blog](https://xlang.ai/blog/osworld-verified) |
| Year / maint. | 2026 / Zapier / pushed 2026-08-04 [V] | 2025-26 / Stanford + Laude + Harbor / active; site now shows v4.0 [C] | 2025 (ICLR'26) / HKUST / pushed 2026-08-18 [V] | 2024, Verified Jul-2025 / XLANG / trajs updated 2026-08-07 [V] |
| Tasks / families | 600 public (6 domains x 100) + 200 "simple"; separate private held-out leaderboard set [C]. Families: domain only; tasks unique | 89 unique tasks; no families | 108 tasks, 32 apps, 604 tools; no families | 369 (361 without Google Drive) [C]; app domains only |
| Check | End-state assertions; strict all-pass + partial credit [C] | Test scripts per task; 2-verified fixed 4 grading, 11 instruction, 89 env issues [C] | Per-task evaluation scripts [C] | Execution-based checker scripts [C] |
| Per-instance / trajs | Visualizer of own runs; public per-model per-task not found [U] | Leaderboard dataset, Apache-2.0, 40 GB, per-trial result.json + artifacts [V license, C content] | 17 models x 3 runs, pass/fail, tokens and approximate cost [C]; gated=auto | Many models, 15/50/100-step runs, MIT [V card] |
| License | MIT (c) 2026 Zapier [V]; LICENSE carves out derived third-party API schemas (not claimed) [V] | Apache-2.0 [V] | Trajectories CC-BY-4.0 [V HF tag]; **repo has no LICENSE file** [V GitHub API]; task dataset card has no license tag [V] | Apache-2.0 repo [V], trajectories MIT [V] |
| Cost | Simulated SaaS, cheap to host | Docker per task; long tasks, $1-5/task frontier | Many real SaaS accounts, or their public eval server; up to 90 min/task | VMs (Docker+KVM/AWS); screenshots; $1-3/task |
| Why us | Our planned multi-step test; pair with a family benchmark | Ops domain + routing seed | Trajectories with cost across 17 models | GUI trajectories |

### 2b. Coding and SWE (families via repo / perturbation)

| Field | DS-1000 | BigCodeBench | SWE-bench Verified | SWE-rebench / V2 | SWE-smith | SWE-Gym | LiveCodeBench | Aider polyglot |
|---|---|---|---|---|---|---|---|---|
| Links | [HF](https://huggingface.co/datasets/xlangai/DS-1000) · [paper](https://arxiv.org/abs/2211.11501) | [HF](https://huggingface.co/datasets/bigcode/bigcodebench) | [HF](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified) · [experiments](https://github.com/SWE-bench/experiments) | [HF v1](https://huggingface.co/datasets/nebius/SWE-rebench) · [HF V2](https://huggingface.co/datasets/nebius/SWE-rebench-V2) · [leaderboard](https://swe-rebench.com/) | [HF](https://huggingface.co/datasets/SWE-bench/SWE-smith) | [HF](https://huggingface.co/datasets/SWE-Gym/SWE-Gym) | [repo](https://github.com/LiveCodeBench/LiveCodeBench) · [submissions](https://github.com/LiveCodeBench/submissions) | [leaderboard](https://aider.chat/docs/leaderboards/) |
| Tasks / families | 1,000; perturbation families (Origin/Surface/Semantic/Difficult-Rewrite) link variants to an origin problem [C] | 1,140; 7 domains [C] | 500 | v1 21K+ (27,878 rows); V2 32K+, 20 languages [C] | 59,136 / 128 repos [C] | 2,438 / 11 repos [C] | v6: 1,055 problems, dated [C] | 225 Exercism |
| Check | Tests + surface constraints; 1.8% false-pass in paper [C] | ~5.6 tests/task; authors admit some over-specific tests [C] | Tests; **59.4% of audited tasks flawed** [C Epoch] | Tests in Docker (7,500 images) [C] | Tests | Tests | Hidden tests | Tests |
| Per-instance results | No | Leaderboard aggregate [C] | Yes: resolved IDs + trajs per submission [C] | Leaderboard: cost + tokens per problem, pass@5 [C]; per-task download [U] | — | — | Yes, ~80 models, MIT [C] | Aggregate only, with cost |
| Trajectories | — | — | Per submission (submitter-hosted) | [OpenHands trajs](https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories): 67,074 (32,161 resolved), Qwen3-Coder-480B, multi-attempt, CC-BY-4.0 [V license]; [nvidia Open-SWE-Traces](https://huggingface.co/datasets/nvidia/Open-SWE-Traces): 207,489 trajs, 9 languages, resolved labels incl. failures, multiple scaffolds, CC-BY-4.0 [V] | [trajectories](https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories) 76K rows, resolved bool, MIT [V] | OpenHands sampled trajs, no license tag [V] | — | — |
| License | CC-BY-SA-4.0 [V] | Apache-2.0 [C]; **repo archived** [V] | Card: none [V]; SWE-bench repo MIT [V]; **experiments repo has no license** [V] | CC-BY-4.0 + per-instance upstream repo license field [C] | MIT [V] | MIT [V] | Repo MIT; HF tag just "cc" [V]; problems scraped from LeetCode/AtCoder/Codeforces [C] | Apache-2.0 code [V] |
| Contamination | High (StackOverflow) | Medium | **Severe** | Low (time-windowed) | Low-medium | Medium | Low if date-filtered | Medium; leaderboard stale (top is GPT-5) [C] |
| Why us | Already used; perturbation families | Already used | Avoid as target | Repo families + trajectories + fresh eval | Many bugs per repo = family; trajectories | Small repo families | Routing seed | Skip |

### 2c. Data / SQL / business non-coding

| Field | DABstep | Spider 2.0 | BIRD | SpreadsheetBench | CRMArena-Pro | GSM-Symbolic |
|---|---|---|---|---|---|---|
| Links | [HF](https://huggingface.co/datasets/adyen/DABstep) | [repo](https://github.com/xlang-ai/Spider2) | [site](https://bird-bench.github.io/) · [mini-dev](https://huggingface.co/datasets/birdsql/bird_mini_dev) | [repo](https://github.com/RUCKBReasoning/SpreadsheetBench) | [HF](https://huggingface.co/datasets/Salesforce/CRMArenaPro) · [repo](https://github.com/SalesforceAIResearch/CRMArena) | [HF](https://huggingface.co/datasets/apple/GSM-Symbolic) |
| Tasks / families | 450 + 10 dev, easy/hard; many templated fee questions (cluster needed) [C] | Snow 547, Lite 547, DBT 68 [C] | 12,751 pairs / 95 DBs; dev 1,534; mini-dev 500 [C]; DB = family | 912 instructions x ~3 test sheets (2,729) [C]; Verified 400 subset (Dec-2025) [C] | 22 task types, 8,614 rows, B2B/B2C [C] | 100 templates x 50 instances; p1, p2 difficulty variants [C] |
| Check | Exact-match normalized; **test answers hidden** (answer column is one constant value across 450 rows) [V] → only 10 dev tasks self-checkable | Execution-result match; partial gold SQL [C] | Execution accuracy | OJ-style multi-test-case | Exact match [C] | Exact numeric |
| Check reliability | Unknown | **62.8-66.1% annotation errors (Snow)** [C, arXiv 2601.08778] | **52.8% errors (Mini-Dev)** [C] | Better (multiple test cases) | Unknown | High |
| Per-instance results | **task_scores: 1.02M rows** (submission_id, task_id, score, agent_answer) [C] | Leaderboard only | Leaderboard only | — | — | — |
| License | CC-BY-4.0 [V] | MIT [V]; Lite needs BigQuery (some cost); Snow free [C] | CC-BY-SA-4.0 [V] | README says CC-BY-SA-4.0; **no LICENSE file** [V]; forum-sourced content [C] | **CC-BY-NC-4.0** [V LICENSE.txt] | **CC-BY-NC-ND-4.0** [C card] |
| Why us | Non-coding + routing seed | Avoid until corrected | Avoid until corrected | Spreadsheet domain with verifiable tests | Business families, research-only | Math families, research-only |

### 2d. Experience-transfer benchmarks (2025-2026, methodologically closest)

| Field | SkillEvolBench | AFTER | EvoAgentBench | Evo-Memory |
|---|---|---|---|---|
| Links | [paper 2605.24117](https://arxiv.org/html/2605.24117v1) · [site](https://skillevolbench.github.io/) · [repo](https://github.com/AIoT-MLSys-Lab/SkillEvolBench) | [paper 2606.23127](https://arxiv.org/html/2606.23127v1) · [HF](https://huggingface.co/datasets/DavydenkoGr/AFTER) | [paper 2607.05202](https://arxiv.org/html/2607.05202v1) · [HF](https://huggingface.co/datasets/EverMind-AI/EvoAgentBench) | [paper 2511.20857](https://arxiv.org/pdf/2511.20857) (Google DeepMind) |
| Design | 180 tasks, 6 envs x 5 families x 6 roles (canonical, enriched, variant, context-shift, adversarial, composition) [C] | 382 tasks, 6 roles, 22 skills; cross-task / cross-role / cross-model splits [C] | 2,605 tasks from BrowseComp-Plus, SWE-bench Verified, LCB v6, GDPval; ability-aware 528/267 split [C] | Streams 10 existing datasets; ExpRAG baseline [C] |
| Check | Deterministic public + hidden tests + process verifiers [C] | pytest [C] | Mixed; GDPval part is judge-graded [C] | Inherits source datasets |
| Finding | "raw-trajectory reuse frequently outperforms distilled skills" [C] | +3.7-6.7 from skills; multi-model traces +13.7 cross-model; cross-role -4.8 to -7.5 [C] | Curator-verified skills +7.5-10.5; automatic methods brittle [C] | — |
| License | Paper says CC-BY-4.0; **repo has no LICENSE** [V] | Paper says CC-BY-4.0; **HF tag says apache-2.0** [V] — conflicting | Paper says CC-BY-SA-4.0; **HF tag apache-2.0** [V] — conflicting; inherits SWE-bench Verified contamination | Code release not confirmed [U] |
| Why us | Direct external replication target for "example vs procedure" | Cross-model trace finding supports multi-model substrate | Ability graph ≈ our procedure families | Streaming protocol idea |

### 2e. Routing / per-instance result corpora

| Dataset | Size | Fields | Checking | License | Notes |
|---|---|---|---|---|---|
| [LLMRouterBench](https://github.com/ynulihao/LLMRouterBench) / [HF](https://huggingface.co/datasets/NPULH/LLMRouterBench) | 33 models, 21 datasets, 400K+ instances, ~1.8B tokens [C] | prompt, prediction, ground_truth, score, prompt/completion tokens, cost [C] | Mostly exact/test-based; ArenaHard judge-based | README badge MIT but **no LICENSE file** and no HF license tag [V] | Includes tau2-bench and SWE-bench in the cost pool; flagship models (GPT-5, Claude 4, Gemini 2.5 Pro). Finding: routers ≈ dataset-level oracle. |
| [EmbedLLM](https://huggingface.co/datasets/RZ412/EmbedLLM) | correctness matrix (prompt x model), 34 GB [C] | prompt_id, model_id, category, label [C] | Exact match | HF apache-2.0 [V]; repo no license [V] | Older open models (2024). |
| [RouterBench](https://huggingface.co/datasets/withmartian/routerbench) | 30K+ prompts x 11 models (405K outcomes) [C] | response, cost, score | Mixed | **No license on card** [V]; repo MIT [C] | 2024 models (GPT-4, Claude-2, Llama-2). Stale. |
| [RouteLLM gpt4_dataset](https://huggingface.co/datasets/routellm/gpt4_dataset) | 119K | GPT-4 vs Mixtral, `mixtral_score` 1-5 | **LLM judge** | Apache-2.0 [V] | Two models only; judge-labelled. |
| [SPROUT](https://huggingface.co/datasets/CARROT-LLM-Routing/SPROUT) | 44K x 13 models | tokens in/out | **LLM judge** | **No license** [V] | Avoid for "verified". |
| [DABstep task_scores](https://huggingface.co/datasets/adyen/DABstep) | 1.02M rows | submission, task, score, answer | Exact | CC-BY-4.0 [V] | Agent systems rather than raw models. |
| tau2 S3 trajectories | 50 submissions x 4 domains x 4+ trials | full trajectory, reward, cost where reported | DB state | MIT repo [V]; files unlicensed [U] | `aws s3 sync s3://sierra-tau-bench-public/submissions/... --no-sign-request` [V] |
| [Terminal-Bench 2 leaderboard](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard) | 40 GB | per-trial result.json + run artifacts | tests | Apache-2.0 [V] | |
| [Toolathlon-Trajectories](https://huggingface.co/datasets/hkust-nlp/Toolathlon-Trajectories) | 17 models x 3 runs x 108 | pass/fail, tokens, approx cost | scripts | CC-BY-4.0 [V], gated auto | |
| [OSWorld-Verified trajs](https://huggingface.co/datasets/xlangai/ubuntu_osworld_verified_trajs) | 1000+ episodes, 15+ models [C] | screenshots, actions, results | scripts | MIT [V] | |
| [LiveCodeBench submissions](https://github.com/LiveCodeBench/submissions) | ~80 models [C] | per-problem results | hidden tests | MIT [C] | Date-filter for contamination. |
| [HELM raw](https://crfm-helm.readthedocs.io/en/latest/downloading_raw_results/) | hundreds of GB per project | per-instance prompts, outputs, metrics | mixed | code Apache-2.0; data license [U] | Public GCS bucket `crfm-helm-public`. |
| [Epoch Benchmarking Hub](https://epoch.ai/benchmarks/use-this-data) | per-run Inspect logs | per-question prompt/response/score | mixed | CC-BY for Epoch data; external sources keep their licenses [C] | Frontier models, maintained. |
| [HAL](https://hal.cs.princeton.edu/) | 9 benchmarks incl. tau-bench airline, GAIA, SWE-bench Verified Mini | full Weave traces + cost | per-benchmark | License not stated [U]; traces encrypted; **harness archived 2026-07-01** [V] | Good historical cost data. |
| [Open LLM Leaderboard details](https://huggingface.co/collections/OpenEvals/archived-open-llm-leaderboard-2024-2025) | thousands of open models | per-sample details | exact | varies [U] | **Retired Mar-2025**; open-weight models only; low relevance. |

---

## 3. Recommended next three to run

### 1) tau2-bench — retail + airline official train/test, then telecom families
- **Why first:** zero setup (already vendored at `c:\Users\chait\Prog\3Found\vendor\tau2-bench`,
  MIT), official train/test split, non-coding policy-bound domain (need 5), DB-state checking,
  and 50 public multi-trial trajectory sets to seed the recommender *and* supply
  "verified solved example" candidates without running anything.
- **Design:** arms solo / ordinary memory / substrate. Seed from *train* tasks (our own solved
  runs, or leaderboard trajectories where reward=1), evaluate on *test* (retail 40, airline 20).
  Second pass: telecom — hold out subtask combinations within each issue family.
- **Pilot cost:** 60 test tasks x 3 arms x 4 trials ≈ 720 episodes. At ~$0.05-0.15/episode on a
  mid-tier agent model plus user-sim ≈ **$40-110**.
- **Caveat:** LLM user simulator adds variance; use pass^k with 4 trials, as the leaderboard does.
  Keep version >= 1.0.1.

### 2) AppWorld — scenario-split family transfer
- **Why:** the cleanest family design found: 250 scenarios x 3 tasks, strong state-based tests
  with collateral-damage checks, Test-C adds unseen apps. Apache-2.0, pip-installable, no Docker.
- **Design:** within each scenario, give the agent 1 verified sibling solution (code + test
  that proved it) and test on the other 2; compare with a procedure-only arm. This mirrors our
  DS-1000 result directly on multi-step agents.
- **Pilot:** train+dev (165 tasks, 55 scenarios) x 3 arms ≈ 500 episodes x $0.2-0.5 ≈ **$100-250**.
- **Caveat:** protected task data must stay encrypted if redistributed — do not commit
  decrypted tasks to the tracked repo or ship them in `stealthlab-connect`.

### 3) WorkArena L1 -> L2 (alternative: WebArena-Verified hard subset)
- **Why:** tests a question AppWorld does not — do verified *atomic* solutions transfer to
  *compositions* (L2 = 682 tasks built from L1 atoms). Deterministic validators, Apache-2.0,
  web/GUI modality (need 2).
- **Pilot:** 33 L1 types x 3 seeds for seeding (cheap, short) + ~100 L2 tasks x 3 arms ≈
  **$100-250**, plus a free ServiceNow developer instance (or gated HF pool).
- **If ServiceNow setup blocks:** WebArena-Verified hard subset (258 tasks, template-split,
  deterministic) at ~$0.5-1.5/task ≈ $150-400 for 1 arm pair, plus heavy Docker setup.

Not in the top three but run cheaply alongside: AutomationBench (already planned) for absolute
multi-step numbers; LLMRouterBench + tau2/Terminal-Bench leaderboard data to backtest the
recommender offline at ~$0.

---

## 4. Avoid / use with caution (with evidence)

| Item | Problem | Evidence |
|---|---|---|
| SWE-bench Verified as a measurement target | Flawed tests + contamination | Epoch "Flawed" verdict 2026-09-03 citing OpenAI audit: 59.4% of audited tasks reject correct solutions; frontier models reproduce solutions from task IDs ([Epoch review](https://epoch.ai/benchmarks/swe-bench-verified/review)). A secondary claim that OpenAI later withdrew its SWE-Bench Pro recommendation (~30% broken) came from a search summary only — **[U]**. |
| BIRD / Spider 2.0 gold labels | Annotation errors swamp small effects | 52.8% (BIRD Mini-Dev), 62.8-66.1% (Spider 2.0-Snow); rank correlation to corrected labels only 0.32 ([arXiv 2601.08778](https://arxiv.org/abs/2601.08778)). |
| GSM-Symbolic, CRMArena-Pro | Non-commercial licenses | CC-BY-NC-ND-4.0 [C card] and CC-BY-NC-4.0 [V LICENSE.txt]. Research use only; never ship derived examples. |
| ToolSandbox (Apple) | Proprietary license | Apple Software License, "personal, non-exclusive" [V LICENSE]. |
| GAIA | Gated; no resharing; test answers hidden | Card terms: do not reshare outside gated/private repo [C]. |
| RouteLLM gpt4_dataset, SPROUT, GDPval-based sets | LLM-judge labels | `mixtral_score` 1-5 judge; SPROUT `judge_response` [C]. Not "verified". |
| RouterBench, SPROUT, SWE-bench/experiments, Toolathlon repo, SkillEvolBench repo, LLMRouterBench | Missing LICENSE | Verified via HF/GitHub API — no license means no default right to redistribute; OK to read for research, confirm before seeding shipped data. |
| DS-1000, BIRD, SpreadsheetBench (CC-BY-SA) | Share-alike | Verified examples derived from them that we redistribute (e.g. in a public substrate) would need to carry CC-BY-SA. |
| SWE-rebench / Open-SWE-Traces content | Upstream repo licenses | Card: respect each instance's `license_name`; Open-SWE-Traces restricted to MIT/Apache/BSD repos [C]. |
| LiveCodeBench problem text | Scraped from LeetCode/AtCoder/Codeforces | HF license tag is only "cc" [V]; upstream ToS may restrict redistribution [U]. |
| Aider polyglot | Stale leaderboard, aggregate results only | Top entry still GPT-5 [C]. |
| Original WebArena scoring | 118 of 812 tasks use LLM `fuzzy_match` [V counted] | Use WebArena-Verified instead. |
| Open LLM Leaderboard, HAL | Retired/archived | Retired Mar-2025 [C]; hal-harness archived 2026-07-01 [V]. |
| BigCodeBench | Repo archived 2026 [V] | Still usable as a frozen set. |
| Terminal-Bench 2.0 original | Environment/grading bugs | zai-org 2-verified fixes 106 tasks (4 grading, 11 instruction, 89 environment) [C]; prefer 2.1 or 2-verified. |

---

## 5. Open items / couldn't confirm

- AppWorld split sizes (105/60/168/417) are from secondary sources and the paper summary, not a count.
- tau2 S3 trajectory files: accessible, but no explicit data license beyond the repo's MIT.
- WorkArena instance cost/terms under ServiceNow developer program not checked.
- DABstep family clustering (templated fee questions) is inferred, not counted.
- Per-task downloadability for SWE-rebench leaderboard and BFCL per-model score files not confirmed.
- HELM raw data license not confirmed.
- License conflicts for AFTER and EvoAgentBench (paper vs HF tag) — ask authors before use in shipped data.
