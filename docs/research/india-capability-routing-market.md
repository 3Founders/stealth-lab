# Capability-based routing in India: market research and where kel could fit

Written 2026-10-13. About 15 searches (extended mode for the first), no full-page reads; nearly everything below is search-summary level. Confidence:
**P** primary/regulator/company filing, **R** reputable reporting, **W** vendor/blog/secondary (lead only). Section 6 is my judgment, not sourced fact.
"Capability-based routing" here means choosing the model (and later the procedure) for a task from measured per-task-type evidence, not from brand or price alone.

## 1. Demand side in India

- **Developers:** GitHub Octoverse 2025: India 21.9M accounts, second to the US (28M); 5.2M added in 2025 (14% of all new accounts); projected to pass the US by 2030. Accounts include students and hobbyists, not jobs (R).
- **AI coding tools:** Cursor says its India base tripled to over 3M developers in a year, India is its third-largest market, Indian developers run the most agent requests per developer, and it launched a ₹649/month India plan in late July 2026 (company claim via press, R/W). Claude Code is reported at ₹2,000-2,399/month and without UPI (W).
- **Enterprise adoption is broad but shallow:** Databricks/Economist Impact: 94% of Indian enterprises use GenAI in at least one function, the highest of 19 countries, but only 24% say applications are production-ready; cost is the top hurdle at 40% across Asia-Pacific (R, vendor-sponsored). EY 2025: 15% have GenAI workloads in production and **only 8% can fully measure and allocate AI costs**. Deloitte India: firms are highly price-sensitive, 71% run more than 10 experiments, most prefer to buy rather than build (R, consulting surveys; definitions differ, do not compare percentages across them).
- **Global capability centres (GCCs):** about 1,700-1,800 centres, about 2M employees, roughly a $60B sector; EY GCC Pulse 2025: 58% investing in agentic AI; 126,600 people in AI-aligned roles in Fortune 500 GCCs (ANSR) (R/W; vendor sources). No source gave GCC developer-tooling budgets.
- **IT services:** Infosys (Topaz: 12,000 AI assets, 150 models), Wipro (200+ agents), HCLTech (multi-vendor partnerships) and TCS (announced Anthropic partnership, details unverified); in January Cognizant, Infosys, HCLTech and Wipro announced deals with Factory, Cursor, Cognition and Typeface for agentic software development (R/W). They are multi-model by necessity; I found no data on their model-routing practice or coding-tool spend.
- **Cost pain is anecdotal:** the evidence is vendor blogs. Founders pay USD pricing in a rupee economy, with about 3% forex, 18% GST on imported services and a 2% TDS on foreign invoices over ₹50K (founder post, W). One unnamed Indian marketing SaaS case: API bill above $12,000/month at 10M emails, cut about 90% by moving drafting to a cheaper model (single vendor anecdote, W). Routing-saves-40-60% claims are vendor figures (W). I found no survey of Indian founders on this.
- **User's own figure:** about $800 per developer per month, from relatives and published commentary, is not independently sourced.

## 2. Supply side: who already routes

**Global gateways and routers**
- **OpenRouter:** claims 200T+ tokens/month, 400+ models, 70+ providers; Sacra: $28M Series A (Menlo, April 2025, $500M valuation). A $113M Series B in May 2026 appears in one blog only (unconfirmed) (R/W).
- **Portkey:** founded by Indian founders (Rohit Agarwal, Ayush Garg); Bengaluru and San Francisco; $3M seed (Lightspeed, Aug 2023); **$15M Series A led by Elevation Capital, February 2026** (Inc42, Entrackr, R). Claims 500B+ tokens and 125M requests/day, $500K+ daily AI spend, 24,000 enterprises; customers named include Postman, Snorkel AI, and earlier Jio Haptik and Springworks. Core gateway is free, paid tiers from $49. One 2026 guide says Palo Alto Networks acquired it in May; I found no second source: **unconfirmed** (W).
- **LiteLLM:** open source, cost- and latency-based routing, custom enterprise tier; a 2026 report of a critical remote-code-execution flaw added to CISA's exploited list, fixed in 1.83.7 (single secondary source, W).
- **Martian** ($9M seed: NEA, Prosus, General Catalyst) reportedly stopped talking about routing and is now interpretability research; **Not Diamond** narrowed to routing for coding agents (one 2026 guide, W). **TrueFoundry** ($19M Series A, Intel Capital, Feb 2025, "north of $1.5M ARR", US-listed HQ; Bengaluru presence not confirmed) sells an AI gateway to Fortune 1000 (R).
- **Evidence for routing itself:** RouteLLM (ICLR 2025) reports a trained complexity router keeping about 95% of GPT-4 performance while sending 14-26% of requests to the strong model (via a vendor blog, W; check the paper).

**Indian model and hosting side**
- **Sarvam:** $1.5B valuation, $234M first close of a $300M Series B (HCLTech press release, R); IndiaAI Mission allotted 4,096 H100s with a ₹98.68 crore subsidy (R). Sarvam 105B priced at $0.80 per million blended tokens versus $4.50 and $9.00 for two named global models; Sarvam-30B at ₹2.5 in / ₹10 out per million tokens (vendor, W). Reports 10M API calls/day on its India-hosted platform (press release). Deployable as cloud, private VPC or air-gapped. **Sarvam Code** (early beta, invite-only): claims about $2 per solved task on Terminal-Bench 2.1 versus $4.10-$27.80 for Claude Code and Codex, billed on completed work with retries not billed; the reported run used Z.ai's GLM-5.2, so the edge is claimed to come from the harness (vendor claim, unverified, R/W).
- **Krutrim** hosts DeepSeek locally from ₹10 per million tokens (Inc42); **Yotta**, **AceCloud**, **E2E**, **Gyde** advertise India-located inference. Government compute at a reported 42% discount; 34,000+ GPUs allocated, ₹111.85 crore of subsidy disbursed; 100% subsidy applies to foundation-model training only, 40% for other workloads (R, secondary).

**What was not found:** any *Indian* product that routes by measured per-task capability (as opposed to price/latency/fallback gateways), and any Indian coding-agent router. Absence in my searches is not proof; search tools here skew to US sources.

## 3. Regulation that creates a reason to route and to record why

- **DPDP Act:** Rules notified 13 Nov 2025, phased. Consent managers about Nov 2026; main obligations, including cross-border transfer rules, from about **May 2027**. India uses a negative list: transfers allowed unless the Central Government restricts a destination. Nothing found on how this applies to LLM API calls (law-firm commentary, W; verify against the Gazette).
- **RBI FREE-AI (Aug 2025):** a committee report, not a rule yet; 7 principles, 26 recommendations; expects a board-approved AI policy, explainability, incident reporting and vendor contracts that flow down obligations (law-firm commentary, W).
- **SEBI (notified Feb 2025):** regulated entities using AI/ML tools, in-house or third-party, are **solely responsible** for investor data privacy, outputs and legal compliance. A broader June 2025 draft covers testing, fallbacks and vendor governance; I could not confirm it is final (W).
- Reading (mine): the rules make the *regulated entity* accountable for a model it did not build, so a record of which model handled which task, on what evidence, and where it ran has a compliance use, not only a cost use.

## 4. Gaps in the market the evidence points to (each with the evidence behind it)

1. **Measurement of cost.** Only 8% of firms can fully allocate AI cost (EY). Gateways give spend logs; none of what I found ties spend to *outcome per task type*.
2. **Rupee, residency and Indian-model blending.** Founders eat forex/GST/TDS friction; Indian-hosted models are far cheaper per token on vendor price lists; regulated buyers want in-country inference. A router that treats Sarvam/Krutrim/Yotta endpoints and global frontier models in one policy (with data-class rules) is not something I found.
3. **Coding agents specifically.** Cursor's India usage is large and cost-sensitive; Sarvam Code's whole pitch is cost per *solved* task. That is the metric capability routing optimises, and Indian incumbents are only now measuring it (vendor claim).
4. **Services firms and GCCs buy for many clients.** They need audit trails and per-client policy; evidence of their routing practice is missing from my searches.
5. **Accountability record.** SEBI's sole-responsibility clause and FREE-AI vendor flow-down create demand for evidence of model choice.

## 5. Risks and counter-evidence

- **Router commoditisation:** Portkey's core is free, LiteLLM is open source, OpenRouter has scale; Martian left routing and Not Diamond narrowed to coding. Plain price/latency routing is not defensible.
- **Portkey is already the Indian-founded incumbent** with Elevation/Lightspeed money, 24,000 claimed enterprises, and an explicit plan to add agent permissions, identity and budget controls. It is the closest competitor, and may be owned by a large security vendor (unconfirmed).
- **Model labs and Indian model makers bundle:** Sarvam's harness-plus-model pitch and Cursor's India plan compete for the same developer budgets.
- **Cost evidence is weak.** Most savings figures are vendor claims; the own measurement so far is public benchmarks, not customer work (this repo's earlier A/B: 114 vs 120 solved at 38% lower cost on 128 public problems, not a customer result).
- **Small paying base:** Indian developers are numerous but price-sensitive (₹649 plan); the dense paying demand may be regulated enterprises, GCCs and services firms.
- **Regulatory dates move:** DPDP cross-border rules are not yet operational; RBI's report is not yet binding; SEBI's draft status unclear.
- **Data quality:** most India-specific market sizing here comes from consulting surveys with different definitions and vendor-sponsored studies.

## 6. How kel could fill the space (my judgment, not sourced)

- **Position:** not "another gateway" but the layer that records *which model succeeded at which kind of task for this organisation, on what evidence*, and routes by that. Gateways answer "where did the request go and what did it cost"; this answers "did it work, and is this model allowed here".
- **Wedge candidates, in order of evidence:**
  1. Coding-agent teams that already pay for several tools and cannot say which task types each handles well (Cursor-scale usage, cost sensitivity). Needs a measured result on *their* repos, which the A/B work has not yet produced.
  2. Regulated Indian fintech/NBFC teams needing in-India inference plus a per-call record of model, data class and outcome (SEBI/RBI/DPDP). Needs the provider-catalog work (Indian-hosted connections with region/ZDR flags) already planned in `providers/`.
  3. IT services firms and GCCs as design partners for per-client policy; one lead exists already (a roughly 2,000-person company).
- **Differentiators that the market scan does not show anyone offering:** per-task-type measured capability from the customer's own outcomes; Indian-hosted and global models under one governed policy with rupee-denominated cost per solved task; an audit record usable for sole-responsibility clauses.
- **What to prove before claiming any of it:** a customer-side result (same tasks, hidden tests, cost per solved task) rather than public benchmarks; one real router comparison against Portkey/OpenRouter-style price-only routing on the same tasks; confirmation of Portkey's ownership and roadmap.
- **Questions to settle by talking to buyers, not searching:** who in an Indian firm owns the AI-coding bill (CTO, platform team, procurement); what fraction already uses more than one provider; whether in-India inference is a hard requirement or a preference; what "evidence of model choice" would need to contain for a SEBI-regulated firm.

## 7. Not found

Indian per-developer AI-coding spend; IT-services and GCC coding-tool budgets; any Indian per-task capability router; Indian founders' routing adoption rate; Portkey revenue and the Palo Alto Networks claim's second source; how DPDP/RBI/SEBI treat prompts sent to foreign APIs; Krutrim's, Yotta's and E2E's current enterprise terms; whether Sarvam Code results replicate.
