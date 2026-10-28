# Routing review: schemes, benchmarks, run costs (2026-10-28)

Purpose: hand-off for a deep-research pass. Everything here comes from web search and page fetches on
2026-10-28 and has **not** been checked against primary sources. Every benchmark number is vendor-reported or
from an aggregator unless it says otherwise. Aggregators disagree with each other (see "Conflicts"). Treat each
number as a lead to verify.

Context from this repo: `docs/routing_priors_eval.md` (2026-10-08) found that no router, ours included, is
distinguishable from random routing on one strong/weak pair (SWE-bench Verified, 250 items; ours APGR 0.463,
95% CI [0.308, 0.604]). The cold-start prior does help (+0.130 nats/obs). So the first job is a test that *can*
show a difference, which means picking benchmarks where models disagree. Section 4 does that.

## 1. Router schemes to compare

Source for most rows: the 2026 survey [arXiv 2603.04445](https://arxiv.org/html/2603.04445v2) (read up to
character 100,000 of 133,662; the rest is unread). Venues are given only where the survey states them.

| Family | Methods | Mechanism | Notes |
|---|---|---|---|
| Pre-generation, pool routers | **Avengers-Pro** (2025), UniRoute (2026), Eagle (2024), P2L (ICML 2025), IRT-Router (2025), GraphRouter (ICLR 2025), RouterDC (NeurIPS 2024), EmbedLLM (2024), ICL-Router (2026), Zooter (2024), BEST-Route (ICML 2025) | cluster-and-score, Elo, Bradley-Terry per prompt, item-response theory, GNN, contrastive query embeddings, matrix factorization | Avengers-Pro: embed + cluster, pick by performance-efficiency score. UniRoute is a close cousin (K-means, cost-adjusted per-cluster error, new models addable). Our model is closest to IRT-Router (ability/difficulty) |
| Strong/weak binary | RouteLLM (MF, SW ranking, BERT, causal LLM), Hybrid LLM (ICLR 2024) | win-prediction on preference data | we already re-implemented RouteLLM MF and SW |
| Policy / rule | Arch-Router (1.5B model reading user-defined policies), vLLM Semantic Router (ModernBERT intent classifier) | policy match, not predicted quality | the only family aimed at human-stated preferences |
| RL / bandits | Router-R1 (PPO, up to 4 routing rounds), R2-Reasoner (decompose, then allocate per subtask), SCOPE, MetaLLM, MixLLM, PILOT (LinUCB), GreenServ, dueling-feedback routing | learn from reward, online | **Router-R1 and R2-Reasoner are the only multi-step routers the survey lists** |
| Uncertainty | CP-Router (conformal), LLM-as-judge router, probe-based UQ, "LLM Router: Rethinking Routing with Prefill Activations" ([arXiv 2603.20895](https://arxiv.org/pdf/2603.20895)) | use the model's own signals | cheap, needs access to the small model's internals or logits |
| Cascades | FrugalGPT (TMLR 2024), Cascade Routing (ICML 2025), AutoMix (NeurIPS 2024), Self-REF, Firewall Routing, LM-Blender | try cheap first, escalate on a quality estimate | closest to the "try cheap, check, escalate" design that kell already uses |
| Platforms / libraries | [LLMRouter](https://github.com/ulab-uiuc/LLMRouter) (RouterDC, GraphRouter, Router-R1 and more), [Awesome-Routing-LLMs](https://github.com/MilkThink-Lab/Awesome-Routing-LLMs), RouteJudge ([arXiv 2606.18774](https://arxiv.org/pdf/2606.18774)) | reproducible baselines | reuse before re-implementing |

**The gap.** The survey's open problems list multi-stage cascades, RL-trained escalation and multi-objective
(quality, cost, latency) routing as thin. Nearly every method makes one decision per query and is scored on
single-turn QA. I found no published router evaluated on long-horizon agentic coding with real per-task cost.
That is the opening for our claim, and also the reason Avengers-Pro's "dominates" has not been tested there.

## 2. Router benchmarks (where routers are scored)

| Benchmark | What it is | Use |
|---|---|---|
| [LLMRouterBench](https://arxiv.org/pdf/2601.07206) | ~400K query x model outcomes, 33 models, tokens and cost, includes tau2 and SWE-bench. Independent check reports Avengers-Pro Pareto-optimal | free offline head-to-head with Avengers-Pro; license unverified |
| RouterArena (ICLR 2026, [pdf](https://proceedings.iclr.cc/paper_files/paper/2026/file/4987bb24bc53c198785922d1bd9e18cf-Paper-Conference.pdf)) | open platform scoring accuracy, cost, robustness; GraphRouter and kNN do well, RouteLLM cheaper and lower, RouterDC trails (from search snippets only) | a second independent leaderboard |
| [TwinRouterBench](https://arxiv.org/pdf/2605.18859) | static plus live dynamic evaluation for **agentic** routing | most relevant to us; I could not read it (the PDF came back as binary), so read it first |
| RouterBench (withmartian) | 405K outcomes, 11 models, 2024-era models | stale, no license on the card |
| Avengers-Pro paper ([2508.12631](https://arxiv.org/html/2508.12631v2)) | 6 benchmarks, 8 models; +7% accuracy over GPT-5-medium, or equal accuracy at 27% lower cost | single-turn benchmarks only |

## 3. Run costs

Assumptions: list prices in "Prices"; per-task costs are the benchmark owners' published averages where one
exists. "Replay design" means run each pool model **once** on every task, store per-task pass/fail and cost,
then score every router offline from that table. That is how `routing_priors_eval.py` already works and it
avoids paying again for each router. It only covers routers that pick a model per task. A router that switches
models mid-task needs live runs.

### 3a. DeepSWE (113 tasks, mini-swe-agent harness)

Per-task cost x 113, from the [DeepSWE leaderboard and GPT-5.6 update](https://alphasignal.ai/news/datacurve-s-deepswe-shows-gpt-5-6-sol-beats-claude-at-2-5x-lower-cost) and
[deepswe.datacurve.ai](https://deepswe.datacurve.ai/) (snapshot dated 2026-07-01):

| Model (effort) | Pass rate | $/task | One pass over 113 tasks |
|---|---|---|---|
| gpt-5.6-luna (max) | 67% | 3.03 | $342 |
| gpt-5.6-terra (max) | 70% | 4.95 | $559 |
| gpt-5.5 (xhigh) | 67% | 7.23 | $817 |
| gpt-5.6-sol (max) | 73% | 8.39 | $948 |
| claude-opus-4.8 (max) | 59% | 13.22 | $1,494 |
| claude-fable-5 (max) | 70% | 21.63 | $2,444 |
| claude-sonnet-5 (max) | 54% | 26.40 | $2,983 |

Not found: per-task cost for Opus 5.5, Kimi K3.1 Pro, DeepSeek-V4.1-Flash. Rough guess only: Kimi is priced at
$2/$10 per million tokens against Opus 5.5 at $4/$20, so about half per token, but token use differs by up to
10x between agents (Datacurve), so do not budget from price alone.

Pool of four OpenAI-tier models (luna, terra, 5.5, sol): about **$2,700**. Adding fable-5: about **$5,100**.

### 3b. Terminal-Bench 4.0 (66 tasks, 8-hour timeout, 5 trials per task on the official board)

The earlier answer in this session cited Terminal-Bench 2.0 (89 tasks). **4.0 is the current version**
([tbench.ai](https://www.tbench.ai/news/terminal-bench-4-0)). Full-run costs from the
[Snorkel board](https://snorkel.ai/leaderboard/terminal-bench/) (66 tasks x 5 trials = 330 trials):

| Model (agent) | Resolve rate | Full run | Per trial | One pass over 66 tasks |
|---|---|---|---|---|
| gpt-5.6-sol (max, Codex) | 37.3% | $2.5k | $7.6 | ~$500 |
| glm-5.3 (max, Claude Code) | 41.8% | $2.7k | $8.2 | ~$540 |
| claude-opus-5 (max, Claude Code) | 51.8% | $6.0k | $18.2 | ~$1,200 |
| claude-fable-5 (max, Claude Code) | 44.5% | $7.3k | $22.1 | ~$1,460 |

Four-model pool, one pass: about **$3,700**; three-model (without fable): about **$2,240**. Five trials per
task for a leaderboard-grade confidence interval would be 5x that. One pass is enough for a router experiment
if we bootstrap over tasks, but it gives a noisy per-task label (a model that passes 3 of 5 trials is
recorded as pass or fail by luck).

### 3c. LLMRouterBench replay (the Avengers-Pro head-to-head)

Outcomes are pre-computed, so the cost is compute only: embeddings plus fitting routers on a laptop. Roughly
**$0** in API spend. Check the dataset licence before redistributing anything.

### 3d. Totals

| Plan | API spend |
|---|---|
| LLMRouterBench replay only | ~$0 |
| + DeepSWE 4-model pool, one pass | ~$2.7k |
| + Terminal-Bench 4.0 3-model pool, one pass | ~$2.2k |
| Both agentic pools, with fable-5 in each | ~$8.8k |
| Cheaper variant: a 40-task stratified subsample of each | ~35% of the above |

A live validation of the chosen router on each benchmark would cost about one blended arm (roughly the price of
the cheaper models in the pool, since a good router sends most tasks there) and is not included.

Ways to pay less: first look for **public per-task results** (Terminal-Bench publishes per-trial results
for 2.0 as a 40 GB Apache-2.0 dataset on Hugging Face; I did not check that 4.0 is also published, and I found
no per-task DeepSWE data). Rows with any public per-task outcome cost nothing to add to the pool.

### Prices used (per million tokens, secondary sources)

Claude Opus 5.5 $4 in / $20 out ([third-party pricing sites](https://pricepertoken.com/pricing-page/model/anthropic-claude-opus-5.5), verify on Anthropic's page);
Kimi K3.1 Pro $2 / $10, cached input $0.20 ([DataCamp](https://www.datacamp.com/blog/kimi-k3-1-pro)).
GPT-6 Astra, GLM-5.3, DeepSeek-V4.1-Flash prices not collected.

## 4. Benchmarks that separate models

Source for the scores below: Moonshot's launch table as reproduced by
[DataCamp](https://www.datacamp.com/blog/kimi-k3-1-pro) and
[The Decoder](https://the-decoder.com/chinese-ai-model-kimi-k3-1-pro-is-closing-in-on-anthropics-claude-opus-5-5/),
all at maximum reasoning effort. **These are the vendor's own numbers for its own model, not independent.**

### 4a. Contradicting rankings (best for a router: the winner changes with the task)

| Benchmark | Kimi K3.1 Pro | Claude Opus 5.5 | GPT-6 Astra | DeepSeek-V4.1-Flash | Winner | Spread |
|---|---|---|---|---|---|---|
| Terminal-Bench 4.0 | 65.7 | **66.4** | 58.2 | 42.3 | Opus | 24 |
| SWE-Marathon v1.1 | **51.7** | 46.3 | 42.5 | 40.0 | Kimi | 12 |
| DeepSWE v1.1 | 75.5 | 74.2 | 74.1 | **77.0** | DeepSeek | 3 |
| Kimi Code Bench 3.0 long-horizon | 61.2 | 58.5 | **66.5** | 51.5 | GPT-6 | 15 |
| ProgramBench | 31.5 | **65.0** | 50.0 | 29.1 | Opus | 36 |
| Terminal-Bench Science 0.1 | 38.1 | 63.3 | **68.1** | 15.7 | GPT-6 | 52 |
| ExploitBench | 74.7 | 91.0 | **100** | n/a | GPT-6 | 25 |
| BenchCAD (with tools) | 92.7 | **96.2** | 95.9 | 81.6 | Opus | 15 |
| OfficeQA Pro v2 | **64.4** | 62.2 | 54.4 | 40.4 | Kimi | 24 |
| Automation Bench-AA | **73.3** | 69.5 | 68.5 | 66.6 | Kimi | 7 |

Coding pairs that flip the Opus-versus-Kimi order: **Terminal-Bench 4.0 (Opus +0.7) vs SWE-Marathon (Kimi
+5.4) vs ProgramBench (Opus +33.5)**. A router can only beat the best single model where the leader changes
*and* per-task outcomes differ. The widest spreads (ProgramBench, Terminal-Bench Science) are where the top
model is far ahead, so routing helps there only if a cheaper model matches it on a known subset. SWE-Marathon,
DeepSWE and Kimi Code Bench are where the leaders sit within a few points and different models win.
Per-task data decides, not these averages.

### 4b. Headroom (top scores under 50%)

Verified from a leaderboard (not just Kimi's own score):

| Benchmark | Best score found | Source |
|---|---|---|
| Harvey Legal Agent Benchmark (HLAB, v1.1) | 27.1% (all-criteria pass rule) | [Vals](https://www.vals.ai/benchmarks/hlab) |
| PostTrainBench | 23.2% (Opus 4.6, March 2026 paper; Kimi now reports 36.5%) | [arXiv 2603.08640](https://arxiv.org/pdf/2603.08640) |
| SWE-Marathon v1.1 | 51.7% (just above the line) | table above |
| Terminal-Bench 4.0 | ~52-61% depending on board and date | [Snorkel](https://snorkel.ai/leaderboard/terminal-bench/), others |

Kimi's own score is under 50% but the leader's score is **not verified** (look these up): ExploitGym 20.7,
MazeBench 2, CritPt 24.6, GDP.pdf 31.4, AA-AnalystAgent 38.8, Business Operation 37.8, HANDBOOK.md 44.6,
ZeroBench 39.0, MLCR-AA 38.3 ([BenchLM Kimi page](https://benchlm.ai/models/kimi-3-1-pro)).
Agents' Last Exam is contested: BenchLM has GPT-6.1 Terra at 60.0%, another board has Kimi K3.1 Pro at 43.2.

### 4c. Caution on using these

- Moonshot's table picks benchmarks where its model does well, and some are its own (Kimi Hallu Bench, Kimi
  Code Bench). Do not use the vendor's benchmarks to prove a routing claim.
- Low scores are not the same as discriminating ones. A benchmark where every model scores 2% or 25% by
  all-criteria rules (HLAB, MazeBench) has little room to show that one model beats another per task. Prefer
  a benchmark with a mid-range pass rate *and* per-task disagreement between models.
- A benchmark only helps if we can get per-task outcomes. Averages are not enough for a router test.

## 5. Conflicts between sources (do not quote these without resolving)

- Terminal-Bench 4.0 leader: Claude Mythos 5.1 at 60.9% (one index), GPT-6 Astra 59.6% (Artificial Analysis),
  GPT-6 Astra 57.7% (LLM Stats), Claude Opus 5 with Claude Code 51.8% (Snorkel/official). The official board is
  agent + model; the others may be vendor-harness runs.
- Opus naming: Moonshot compares "Claude Opus 5.5"; Snorkel lists "Opus 5". Check these are not different models.
- Kimi's Terminal-Bench 4.0: 65.7 (Moonshot) vs 67.2 (Artificial Analysis harness, per BenchLM).
- DeepSWE: v1 vs v1.1 changed GPT-5.5 from 70% to 67%. The aggregator ranks (GPT 6 Astra 74.1, Gemini 3.8
  Flash 73.8, Opus 5 Max 73.7) differ from the official board snapshot above.
- Cost: DeepSWE and Snorkel costs are list-price estimates at max effort and are not normalized.

## 6. Questions for the deep-research pass

1. TwinRouterBench: tasks, models, baselines, numbers. Does any router beat the best single model on agentic tasks?
2. Is there any router (Avengers-Pro, RouterDC, GraphRouter, Router-R1, cascade) evaluated on SWE-bench-like or
   Terminal-Bench-like tasks with real cost? What did it find?
3. Per-task outcome data: does Terminal-Bench 4.0 publish per-trial results (as 2.0 did)? Does DeepSWE?
   SWE-Marathon, ProgramBench, Terminal-Bench Science, Kimi Code Bench: public per-task data and licences?
4. Which benchmarks have top scores under 50% *and* per-task disagreement between models? Check item-level
   overlap, not just averages (the kind of test `routing_priors_eval.py` does).
5. Verify the leader and score for each unchecked item in 4b.
6. Verify each pricing number in section 3 against the vendor's own page, and get GPT-6 Astra, GLM-5.3,
   DeepSeek-V4.1-Flash prices and token counts per task.
7. Avengers-Pro: exact algorithm (cluster count, the trade-off parameter, score formula), whether code and
   data are public, and whether it uses the benchmark's own training split (leakage risk).
8. Does the router claim need mid-task switching (R2-Reasoner style) to win on long-horizon tasks, or does
   per-task choice already capture most of the gain? A pool's per-task oracle (best model for each task) bounds
   what any per-task router can get, and we can compute it from one pass.

## 7. Harnesses we could build on (checked 2026-10-28, web search only; none run)

| Harness | What it is | Fit |
|---|---|---|
| **mini-swe-agent** | The harness DeepSWE scores are published on (runs through Pier, a Harbor fork). litellm underneath, so any OpenAI-compatible URL works | Best for DeepSWE: our numbers are comparable to the leaderboard. A router sits behind an OpenAI-compatible URL (the hosted server already has `/v1/chat/completions`) but then routes per *step*, not per task |
| **Harbor** | Terminal-Bench 4.0 is "built on the Harbor framework" (`harbor run -d terminal-bench/terminal-bench@4.0.0`). Licence and custom-agent steps not confirmed | Required for Terminal-Bench 4.0 |
| **Pi** (pi.dev, MIT, monorepo moved to Earendil in May 2026) | Four tools (read, write, edit, bash), system prompt under 1,000 tokens, extensions in TypeScript. MCP in core "as of 2026-09-29" is one unverified guide's claim | Best candidate for a second, open executor next to our step agent: small, hackable, no memory of its own. No Terminal-Bench result found |
| **Hermes Agent** (Nous Research, MIT) | A self-improving agent that keeps its own skills and memory; MCP client; model-agnostic | Poor fit as an executor: its learning loop is a second memory next to `.kell`. Could be a *member* of a room instead |
| **DeepSeek-TUI** (Rust, Codex-style, MCP client and server), **Deep Code** (`@vegamo/deepcode-cli`, listed on DeepSeek's own API docs as a community integration), **Reasonix** | Community terminal agents for DeepSeek-V4; none is an official DeepSeek harness (one review's claim of one is unconfirmed) | Not needed for benchmarks: DeepSeek-V4.1-Flash's 77.0 on DeepSWE is on mini-swe-agent. Worth an executor adapter only if a user wants it |

## 8. If the Artificial Analysis Intelligence Index had to be run

What is known (v5, [AA's announcement](https://artificialanalysis.ai/articles/artificial-analysis-intelligence-index-v5)):
nine evaluations in four categories (Knowledge Work: AA-Briefcase, AutomationBench-AA, GDP.pdf; Coding: FrontierCode 2.0 with 132 tasks
run three times, Terminal-Bench 4.0; Science: Terminal-Bench-Science with 70 tasks, CritPt v2 hard subset with 40 questions;
General: AA-Omniscience v1.1, HLE-Diamond with 1,000 questions). The agentic evaluations are 55% of the weight and about 90% of
output tokens. **"For the first time, the majority of the Index is built on private datasets"**: we could not run most of it
ourselves; only AA can. AA benchmarks models it chooses, so a router would have to be exposed as an API model and offered to them.

AA publishes **cost per Intelligence Index task**, not a v5 total. Listed: Claude Fable 5.1 (max) $13.04, Claude Opus 5.5 (max)
$10.04 ($0.98 at low effort, score 35 vs 58), GPT-6 Astra (max) $6.24, GPT-6.1 Sol (max) $1.27, GPT-6.1 Terra (max) $0.96.
Not listed for v5: DeepSeek V4.1 Flash 1017 (score 32), GLM-5.3 (32), Kimi K3.1 Pro (46), Sonnet 5.5, Haiku 5.5.

**Estimated total = cost per task x N**, with N unpublished. Two earlier-version data points imply N of about 2,100 (Opus 4.8: ~$3.7K total /
$1.78 per task) and 2,900 (GPT-5.5: ~$2.9K / $0.99). They disagree by 40% and came from other Index versions, so the range is a guess:

| Model (max effort) | $/task (v5) | Estimated total for N = 2,100 to 2,900 |
|---|---|---|
| Claude Fable 5.1 | 13.04 | $27k to $38k |
| Claude Opus 5.5 | 10.04 | $21k to $29k |
| GPT-6 Astra | 6.24 | $13k to $18k |
| GPT-6.1 Sol | 1.27 | $2.7k to $3.7k |
| GPT-6.1 Terra | 0.96 | $2.0k to $2.8k |
| DeepSeek V4 Pro (max) | 0.04 (an older Index; v5 figure not found) | about $85 to $120 on that Index; not comparable |
| DeepSeek V4.1 Flash, GLM-5.3, Kimi K3.1 Pro | not published | no honest estimate. Kimi lists $2/$10 per million tokens against Opus 5.5's $4/$20, but token use per task decides the cost |

Reference points from earlier versions: Claude Fable 5 about $6.2K, MiniMax-M2.7 about $175, GLM-5 about $547 for a full run, so the
cheap open models are two orders of magnitude below the frontier ones. A router that sends most tasks to a cheap model would land
between its pool's cheapest and dearest rows, weighted by where it sends the agentic tasks, which are 90% of the tokens.
What we can run ourselves is only the public part: Terminal-Bench 4.0 (section 3b) and, if the data is public, HLE-Diamond.
