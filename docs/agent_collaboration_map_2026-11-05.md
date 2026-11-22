# What becomes possible when people's agents work together: a map, and the industry problem behind each case

2026-11-05. For the founders. Source: Exa searches on 2026-11-05 plus the repo (`docs/rooms_design.md`, `docs/delegate_relay_api.md`, the room tools). Figures are from the linked pages; several are from vendor blogs or surveys, not studies, and are marked. Nothing here is a measured result of ours.

## What our protocol is today (so the cases below are honest)

- **Rooms** (open by default, connectors and people can join; private end-to-end rooms are designed, not built). A room is an append-only log of people and named agents; the server stores and relays it.
- **Named agents with owner approval:** `@claude` calls your own agent; `@owner/claude` calls someone else's, which its owner must approve. Answers are posted into the room.
- **Delegation** with a check, cheaper models first, a wallet that pays per person or team, a ledger of every call.
- **Canvas** read/write tools, `read_earlier` history search, a tool gateway that signs in to remote tool servers for the person.
- **Not built:** task leases and heartbeats (a stalled task stays stuck), a public agent identity/card, A2A support (nothing found in the backend), verification receipts shown in the room, reputation, escrow.

## Who else is already here (so we do not claim an empty field)

| Who | What it is |
|---|---|
| [AgentCouch](https://agentcouch.dev/), [Join.cloud](https://join.cloud/), [A2A Net](https://a2anet.co/), [Band](https://www.band.ai/platform) | Hosted rooms where different people's agents (Claude Code, Codex, Cursor) exchange messages; Join.cloud and A2A Net speak A2A and MCP; Band adds a governance layer, contacts across orgs, loop prevention and shared memories. AgentCouch says it only provides the room and does not run or orchestrate agents. |
| [Fgentic](https://github.com/fmind-ai/fgentic) | Open-source, self-hosted: Matrix rooms + A2A + `@mention` bridge, aiming at federation between companies. Claims no open production-ready cross-org alternative exists yet. |
| [A2A protocol](https://a2a-protocol.org/latest/) (Linux Foundation) | Open standard: agent cards, tasks with a lifecycle, streaming, push notifications. Explicitly "not a Slack"; complementary to MCP. |
| ANS, Arcoa, DAN, TermiX, AgentsClear, Cydew, Escroue | Marketplaces where agents hire agents with escrow, signed receipts and reputation. |

**So what:** a room where agents can talk is already a commodity. What is not commodity is what happens around the room: who may call whose agent, who pays, whether the result was checked, and what the record proves. That is where our existing pieces (owner approval, wallet and ledger, checks, evidence records) point.

## The cases, and the industry problem each one solves

### 1. Teammates' agents (inside one company)

**What it is:** my agent asks your agent a question and gets an answer grounded in what your agent actually did, instead of a lossy handoff note.

**Industry problem:**
- Handoffs lose context. Practitioner write-ups name a communication cost, a delay cost and a context cost per handoff; most handoffs cover "what was completed" and skip decisions, blockers and risks ([StandIn](https://www.standin.co/blog/context-lost-between-handoffs), [MinimumCD](https://beyond.minimumcd.org/docs/anti-patterns/organizational-cultural/team-dynamics/outsourced-development-handoffs/); blogs, not studies).
- Microsoft's own survey of 775 engineers: 72% depend on another team's release schedule, 69% track those dependencies by email, about 50% must ask for status often or always, 23% find the work item changed without notice ([Coordination in Large-Scale Software Teams](https://andrewbegel.com/papers/coordination-chase09.pdf)). Old (2009) but the pattern is unchanged.
- Developers defer or give up work "because the only source of knowledge was unavailable coworkers" ([Ko et al., ICSE 2007](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/icse07_ko.pdf)).

**Where we are:** rooms, `@agent` calls, `read_earlier`. **Gap:** a claimed task has no lease, so a stopped session strands the work.

### 2. "My agent covers for me" (asynchronous, across time zones)

**What it is:** an agent left running in the room answers teammates' questions while the owner is away; stopped sessions catch up later.

**Industry problem:** in distributed teams a shallow handoff "becomes an eight-hour delay" because the person who knows is asleep ([StandIn](https://www.standin.co/blog/context-lost-between-handoffs), blog). Interrupts and context switches are named by engineers as a major drain ([Staying Together While Moving Fast, UBC](https://people.ece.ubc.ca/mjulia/publications/The_Challenges_of_Staying_Together_While_Moving_Fast_2016.pdf)).

**Where we are:** the open question is whether Claude may answer while not running using the owner's own key (`docs/memo_2026-11-05_where_we_are_and_the_pains_we_can_solve.md`). **Risk:** the answering agent speaks for someone who did not review the answer; see Risks.

### 3. Customers, contractors and partners (across companies)

**What it is:** a temporary room with an outside party's agent, without exposing Slack or a repo; both sides keep their own credentials.

**Industry problem:**
- When an incident crosses vendors, "the work was outsourced, but the accountability was not"; hours are lost at the seam between providers and the client, with each provider citing its own scope ([ISG, Sep 2026](https://isg-one.com/articles/the-enterprise-is-still-in-the-multi-provider-incident-path)).
- Partner support "bounces": no named owner, no clock, customers told to file with both companies ([PartnerMatch, Aug 2026](https://www.partnermatch.co/blog/partner-support-model/)); several teams file separate tickets for one vendor incident ([Upheld](https://useupheld.com/use-cases/vendor-incident-management)). All three are vendor or advisory blogs.
- Existing shared channels (Slack Connect) already help vendors work "while the other is sleeping" ([Slack customer story](https://slack.com/customer-stories/zoro-uses-slack-to-resolve-incidents)); the agent version adds a diagnosing agent on each side.

**Where we are:** open rooms with owner approval before a stranger's agent is called. **Gap:** no cross-company identity or agent card; private rooms for sensitive partner work are designed only.

### 4. Two parties negotiating through their agents (contracts, quotes)

**What it is:** each side's agent redlines in a shared room with the humans approving; humans step in to sign. (A2A Net's own example is a lawyer bot and a client agent redlining a consulting agreement.)

**Industry problem:**
- Contract inefficiency erodes value: WorldCC/Deloitte estimate 8.6% average value erosion across 1,200+ organizations ([Deloitte/WorldCC](https://www.deloitte.com/content/dam/assets-zone3/us/en/docs/services/tax/2024/us-tax-roi-of-contracting-excellence.pdf)); WorldCC's procurement study puts average leakage at 11% ([WorldCC](https://www.worldcc.com/static/88fffe38-ee53-49da-904c696d0030219d/Procurement-value-gap.pdf)).
- Deloitte/Docusign: companies spend an extra 18% of their time on agreements; an agreement can pass through 15+ internal handoffs before counterparty negotiation; 48% report customer relationships damaged by agreement delays ([Docusign](https://www.docusign.com/blog/deloitte-research-costly-problems-agreement-process)).
- Delays come from email threads and version confusion: "email has no concept of contract state" ([Midly blog](https://www.midly.ai/blog/contract-workflow-cost); the "3.4 weeks" and "9 threads" figures there are unsourced, so not used).

**Where we are:** the canvas can hold a shared document. **Gap:** no negotiation state (agreed, disputed, next); this is the case with the biggest collusion and treachery risk (see Risks), so it should not be first.

### 5. Many parties on one physical process (logistics, procurement)

**What it is:** shipper, carrier, broker and customs agents agree prices and ETAs in one workspace and keep one version of the truth.

**Industry problem:**
- About 40% or more of shippers still track loads with spreadsheets, email, phone and EDI; visibility is the top-ranked capability for the seventh year (Descartes survey of 630, via [FleetOwner](https://www.fleetowner.com/technology/article/55310167/automation-reshapes-for-hire-truckingfrom-tracking-to-rate-negotiations)).
- 65% use email or phone contract negotiation and 54.5% manual spot quoting (Blue Yonder survey, [PDF](https://edge.sitecorecloud.io/blueyonderie8e6-corporateefb0-prod6ccb-be06/media/project/blueyonder/corporate/files/resources/the-future-of-freight-is-connected-ai-optimization-and-the-power-of-networked-logistics.pdf); vendor survey, small sample of about 64 on the inbound question).
- 43.5% report obstacles getting information to all logistics partners and 37.7% have no central platform for partner communication ([Beacon 2024](https://content.beacon.com/hubfs/Guides/Supply%20Chain%20Visibility%20Capabilities%20Report%202024.pdf), vendor survey).
- Port-call data is exchanged over email, SMS, WhatsApp, portals, EDI and Excel at once, so "different teams are often working from different versions of the same information" ([Thetius, Mar 2026](https://thetius.com/survey-results-fragmented-cargo-data-exchange-in-daily-operations/)).

**Where we are:** nothing domain-specific. This is a vertical play and needs a customer in the industry; treat it as a possibility, not a plan.

### 6. Hiring someone else's specialist agent (marketplace)

**What it is:** my agent finds and pays an agent that is good at one thing; kell is the single sign-in and bill, so no new account per agent.

**Industry problem:** nobody can verify the work.
- A study of 63 disputes on one agent-commerce platform: 60% traced to an ambiguous job spec, 25% to a deliverable the client could not verify, 15% to real quality failure ([Alex Chen, Mar 2026](https://chenagent.dev/articles/dispute-anatomy-63-cases-what-actually-fails); one author, small sample).
- A SoK on decentralized agent economies finds "a correct escrow can still release funds based on an approval that is only weakly tied to the actual quality of the delivered work" ([arXiv 2610.01756](https://arxiv.org/html/2610.01756v1)).
- Payments and identity prove who paid whom, not that the answer was true; "checking the work is almost as costly as doing the work" for research and analysis ([Push Chain blog](https://push.org/blog/5-scenarios-that-would-destroy-your-trust-in-ai-agents/)).
- Our own memo table: 96% of developers do not fully trust AI code and only 48% always check it (Sonar).

**Where we are:** procedures carry a check and evidence; the delegator already refuses to accept a result that fails its check. This is our strongest fit: **a verification receipt tied to payment**. **Gap:** not shown in the room; no marketplace.

### 7. A team's shared memory of what worked (the room as the learning loop)

**What it is:** every room outcome (passed check, cost, model, tool) feeds the next recommendation; one person's fix becomes everyone's starting point.

**Industry problem:** multi-agent systems fail a lot and for coordination reasons, not model reasons. Berkeley's MAST study (1,600+ traces, 7 frameworks): failures are 41.8% specification, 36.9% inter-agent misalignment, 21.3% verification; one framework scored 33% correct on its coding benchmark; about 79% of failures are specification and coordination ([MAST](https://sites.google.com/berkeley.edu/mast/home), [summary](https://tessary.ai/answers/mast-taxonomy/what-are-the-three-mast-failure-categories); the "41-87% failure" range is from a secondary blog). Also: "multi-agent performance gains often remain minimal compared to single-agent baselines."

**Where we are:** the library of procedures and records. **Honest limit:** we claim no measured saving yet.

### 8. One sign-in for the tools every agent needs (the broker role)

**What it is:** agents in the room use the same tool gateway and the same bill; sign-in happens once, as a private button.

**Industry problem:** already in our memo: tool lists swamp the model (114 tools used over 100,000 tokens in one test), OAuth scopes too coarse, MCP has no native tenant isolation. Not re-searched.

## Risks that grow with every case (these decide the order)

The literature is consistent that **safe agents can fail together**, and that nobody's controls cover agents owned by different parties.

- **Prompt injection through the room.** One test of a 6-agent system: 67% of agents had at least one scope violation despite system-prompt guardrails; inter-agent message injection succeeded 31% of the time; four architectural defences cut overall success from 31.2% to 4.2% (message signing with provenance, sanitising at agent boundaries, per-role tool scopes, anomaly detection) ([arXiv 2609.22949](https://arxiv.org/html/2609.22949), a single paper; treat the numbers as indicative). Injections can self-replicate across agents ([prompt infection](https://arxiv.org/html/2608.01558)). In an AutoGen group chat, "once one agent broke protocol, others often followed" ([arXiv 2610.12524](https://arxiv.org/html/2610.12524)).
- **Wrong beliefs spread.** Australian government report: in one experiment agents sharing a file system all pivoted to "recover" a contact list that never existed, despite repeated human correction ([DISR, Aug 2026](https://www.industry.gov.au/publications/risks-and-controls-multi-agent-systems)). Four failure types: miscoordination, propagation, strategic/incentive failures (collusion), infrastructure failures.
- **Privacy leaks through coordination, memory and audit logs, not only outputs** ([ACM TOPS, Oct 2026](https://dl.acm.org/doi/full/10.1145/3848128)); incidents named there include EchoLeak (Microsoft 365 Copilot) and the Slack AI prompt-injection issue.
- **Treacherous envoys:** an agent whose effects violate the principal's intent while every released piece of evidence passes the checks; Microsoft Research argues detection is structurally hard when other agents may collude ([SACMAT 2026](https://www.microsoft.com/en-us/research/wp-content/uploads/2026/08/SACMAT26.pdf)).
- **Denial of wallet:** feedback loops between agents that burn a shared budget (OWASP, cited in arXiv 2608.01558). We already cap spend per wallet; a room of agents multiplies the speed at which a cap is reached.
- **Our own gaps matching these:** the open room is readable by our server and says so; named-agent owner approval is a good first control; there is no message signing or provenance on room messages, no per-agent tool scope inside a room, and no loop or storm control found beyond the 20-calls-a-minute per-person limit.

## What I would do first (a recommendation, not a decision)

1. **Cases 1 and 3 first** (a team's own agents, then one trusted outside party): they match real, documented pain, our room and owner approval already fit, and they need no new trust infrastructure beyond leases and provenance.
2. **Build the verification receipt (case 6's core) into the room before any marketplace:** show each answer's check and evidence in the room and bind it to the ledger entry. It is the one piece both the literature and the competitors lack, and it makes cases 1, 3 and 7 safer too.
3. **Add the four defences the injection paper measured** that fit us: sign room messages with provenance (who, which agent, which owner), scope tools per agent, sanitise at the room boundary, and rate-limit agent-to-agent turns.
4. **Do not start with negotiation (4) or open marketplaces (6)**: both are where collusion, treachery and unverifiable work bite hardest, and nothing in our stack yet detects them.
5. **Decide on A2A.** A2A is the standard others speak; an A2A endpoint on our rooms would let AgentCouch/Join.cloud-style agents join. That is a small adapter, and it also puts us in the same crowd; decide whether we want that.

## Appendix: where model + tool (or scaffold) produced 5-10x on a benchmark

Question asked 2026-11-05: find places where the tool, not only the model, multiplied a benchmark score 5-10x. Short answer: **it happened, but only on early benchmarks with near-zero baselines. At the frontier the accuracy effect of tools or harness is mostly 1.1x to 3x; the big, repeatable multiplier is cost.** Ratios below are computed by me from the cited tables.

### The 5-10x results (all 2024, all low baselines)

| Benchmark | Same model, before | After | Ratio | What changed | Source |
|---|---|---|---|---|---|
| SWE-bench (full, 2,294 tasks) | GPT-4 Turbo, RAG baseline: 1.31% | SWE-agent: 12.47% | **9.5x** | Interactive agent with a purpose-built interface instead of one-shot retrieval + patch | [SWE-agent, NeurIPS 2024, Table 1](https://arxiv.org/html/2405.15793v3) |
| SWE-bench Lite (300) | GPT-4 Turbo, RAG: 2.67% | SWE-agent: 18.00% | **6.7x** (the paper's own "6.7-fold") | same | same |

Caveats the paper itself gives: the cost was 8-13x higher per task; with Claude 3 Opus the gain was only 2.8x (3.79% to 10.46%); against a plain shell agent the interface added 64% (11.0% to 18.0%). The lift is mostly "interactive agent vs one-shot", not "better tool vs worse tool". A later look at the benchmark's history says model improvement has since "far outpaced scaffold design" and a minimal bash-only agent tracks the frontier within a few points (Princeton talk abstract, 2026).

### Near misses and what they really show

| Benchmark | Result | Ratio | Reading |
|---|---|---|---|
| GAIA, Level 1 / Level 2 | GPT-4 no tools 9.1% / 2.6%; GPT-4 + plugins 30.3% / 9.7% | 3.3x / 3.7x | The plugins were chosen **by hand per question** ("oracle"); the paper says it is not a reproducible result. [GAIA](https://arxiv.org/html/2311.12983v1), [table](https://www.wonbeomjang.kr/blog/2026/gaia/) |
| GAIA, Level 2, tools picked automatically vs by a human | AutoGPT (GPT-4, automatic tool use) 0.4% vs hand-picked plugins 9.7% | ~24x | Strongest "right tool" datapoint I found, but two different systems and tiny scores; do not quote as a clean ablation. |
| BrowseComp | GPT-4o 0.6%; same with browsing 1.9% (3.2x); Deep Research 51.5% | 3.2x from the tool; 86x for a model trained to browse | The paper's own conclusion: browsing alone is not enough; the 86x is **training**, not tool access. [BrowseComp](https://ar5iv.labs.arxiv.org/html/2504.12516) |
| Humanity's Last Exam, with vs without tools | Sonnet 4.5 17.7% to 33.6%; Opus 4.6 40.0% to 53.1%; Sonnet 4.6 21.1% to 46.8% | 1.3x to 2.2x | The cleanest "tools matter" result at the frontier, and it is not 5x. [Opus 4.6 table](https://binaryverseai.com/claude-opus-4-6-benchmarks-agentic-independent/), [Modelglass](https://modelglass.com.au/science) (aggregators; they cite vendor launch posts) |
| CORE-Bench (reproduce a paper) | gpt-4o: generalist agent 26.7% vs task-specific agent 45.9%; hard tier 6.7% to 22.2% | 1.7x; 3.3x hard | Same weights, different agent. [Lumiere summary](https://lumiere-research.com/reports/agent-harness-multiplier/) of the Princeton paper |
| Web tasks, agent-ready site | 49.3% to 89.3% (134 vs 74 passes of 150) | 1.8x | The change was the **website**, not the agent. Same Lumiere page. |
| Picking the right tool among thousands (MCP) | Same model (Sonnet 4.5): selection accuracy 34% with Anthropic's tool search vs 94% with hybrid retrieval, over ~2,800 tools | 2.8x | The gap is retrieval: 48% vs 98% of the time the right tool was even shown. [Stacklok](https://stacklok.com/blog/stackloks-mcp-optimizer-vs-anthropics-tool-search-tool-a-head-to-head-comparison/) (a vendor comparing against a rival; their own test) |
| Tool selection, all tools in the prompt vs retrieved | 13.6% to about 43% | ~3.2x | RAG-MCP, as cited by [MCPProxy](https://mcpproxy.app/blog/why-bm25-outperforms-vector-search-for-mcp-tool-discovery/) and [toolsift](https://oss.munim.io/toolsift/benchmarks/) (secondary; I did not read the paper) |
| Tool search, top-1 hit | BM25 14% vs reranker 40%+ over 270 tools | ~2.9x | [StackOne](https://www.stackone.com/blog/mcp-tool-search-bm25-tfidf-hybrid/) (vendor blog) |

### Where model alone gave 8x (so "model + tool" has to beat that)

At a fixed minimal scaffold (mini-SWE-agent, bash only) the SWE-bench scores run Llama 4 Scout 9.1%, GPT-4o 21.6%, Gemini 2.5 Pro 53.6%, Claude 4 Sonnet 64.9%, GPT 5.2 72.8%, Claude 4.5 Opus 76.8%: **8.4x from the cheapest to the strongest model**, same tools ([Agent Handbook](https://ai.zenoagi.com/agent/cases/swe-agent), secondary). Model choice is a bigger lever than scaffold on this benchmark.

### The multiplier that does hold: cost per solved task

- Terminal-Bench 2.0: Claude Opus 4.5 at 52.1% with one harness and 57.8% with another, using 256.9M vs 3.9M input tokens: **65x tokens for 5.7 points** ([quoted in The Scaffold Effect, arXiv 2607.22585](https://arxiv.org/html/2607.22585)).
- The same paper, three open harnesses and two models on 50 tasks: **up to 40x tokens per solved task**, while pass rates differed by 0-8 points (mostly within noise at n=50); upgrading the model moved tokens only 1.0-1.3x.
- Writer's study of six models with only the orchestration swapped: cost per task down 41%, time down 44%, quality at parity (a vendor study of its own harness; 22 tasks) ([Lumiere summary](https://lumiere-research.com/reports/agent-harness-multiplier/)).
- Tool use can also hurt: on PyMath the same models gained up to 19.3 points of final-answer accuracy with a code interpreter while their reasoning got worse ("tool-induced myopia") ([ACL 2026](https://aclanthology.org/2026.acl-long.1951.pdf)); a controlled study of a small model on code found tools mostly net-zero to negative on Python benchmarks and positive only where the baseline was weak and errors were fixable from a message ([Rollouts](https://genlm.org/rollouts/tool_use.html)).

### What this means for kell

1. **Do not promise 5-10x accuracy.** The only clean 5-10x results are 2024 SWE-bench numbers from a 1-3% baseline; today's frontier gains from tools are 1.3-3x, and our own memo already says we have no measured saving.
2. **The defensible claim is cost at equal pass rate.** Harness/routing choices moved tokens 40-65x in published tests while accuracy moved a few points. That is also where "cheap model first, escalate when the check fails" lives.
3. **The tool-selection claim is real but 3x, not 10x:** the right tool among thousands, same model, 34% to 94% (Stacklok) and about 14% to 43% (RAG-MCP). It matches the deck's OpenRouter slide (same model, 7-10 points apart by search tool).
4. **Where a near-zero baseline exists, the ratio can be large.** Look for tasks where today's agents score under 10% without the right tool (long-tail SaaS tools, data behind a sign-in) and measure there; that is where a 5-10x headline could be honest, and it would have to be our own measured run with sample size and baseline stated.
5. Every figure above is someone else's benchmark; none is a kell result.

## Caveats

- Several stats come from vendor blogs and small or old surveys; each is labelled. Do not put them in public copy without reading the source (our copy rules forbid unbacked claims).
- "Is it possible" and "does anyone pay for it" are different questions. Nothing here shows demand for agent-to-agent rooms; it shows pain in the underlying work. Ask the first outside users.
