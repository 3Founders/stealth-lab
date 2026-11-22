# Prior art: how developer-infrastructure companies got their first B2B customers, and what that implies for a two-person team selling verified model routing

Written 2026-10-21. Local, not pushed. Not a plan; evidence first, then a synthesis in section 5 that says plainly where evidence is thin.
The lead and the company are not named on purpose ("the lead", "the company").

## 0. How to read this

**Citation convention.** Every URL was fetched on **2026-10-21** unless the tag says otherwise. `wb:YYYY-MM-DD` means a Wayback Machine snapshot; I
read the snapshot date the archive actually served (it can differ from the date I asked for, and I corrected for that), and the link goes to
that exact snapshot. Tags: **[P]** primary = the claimant's own page, filing, repo, docs or founder post. **[P\*]** primary but the claim is
self-reported marketing (primary does not mean true). **[S]** secondary = press, aggregators, competitor pages, alumni pages; used only as a
lead and never alone for a number. HN numbers come from the Hacker News Algolia API ([P], `hn.algolia.com/api/v1/...`). GitHub dates and star
counts come from the GitHub REST API on 2026-10-21 (`repo created_at` is when the repo was created on GitHub, not necessarily the first commit).

**Tool limits you should know.** The page fetcher summarises pages with a small model, so quotes are short and should be re-checked on the
page before external use. Several pages returned 403 (OpenAI, TechCrunch, Infosys.com) and are marked. GitHub star *history* needs authentication
and I did not use anyone's credentials, so I give today's star counts only. Wayback was fetched with `curl`.

**Dates.** Today is 2026-10-21. Many facts below (Stripe-OpenRouter, Cursor-SpaceX, Helicone, Langfuse and Portkey acquisitions, Cursor Router) are
newer than a typical memory, so I relied on fetched pages only. Anything from memory is labelled "unverified (from memory)"; there is very little.

## 0.1 Bottom line (details and citations in sections 1 to 7)

1. In the dossiers below, nearly every company that grew did it on **free, self-serve, one-line adoption by individual engineers**, then added
   enterprise features (SSO, audit logs, self-hosting, SOC 2) only after pull. I found no case of a two-person team landing an enterprise as a
   first customer through outbound.
2. **Individual developers do not pay** is the one failure story told in the founder's own words (Kite, 500k monthly users, no revenue) and it
   names the fix: managers pay for a *discrete new capability*, and "18% faster" did not qualify (section 1.18).
3. **Routers and gateways have been commoditised or absorbed.** Martian and Unify no longer lead with a router; Not Diamond's pricing moved from
   per-request (2024) to a $0.05-per-million-tokens fee (2026); Portkey made its basic gateway free and was bought by a security vendor; Helicone went to maintenance mode; Cursor, GitHub
   Copilot, Gemini CLI and Claude Code ship their own model selection (sections 2.3, 1.8 to 1.11).
4. The most similar competitor is **Not Diamond Code (Aug 2026)**: a local proxy next to coding harnesses, claiming 20%+ savings with no quality loss
   (section 1.10). Its claims are *smaller* than the 60-80% in the earlier chat advice and than ours; it publishes no tests-based verification.
5. **Savings in dollars only exist where tokens are metered.** Claude Code's own docs say usage inside a Team/Enterprise seat allowance "isn't
   metered in dollars"; the same docs give an average of about $13 per developer per active day, $150-250 per developer per month (section 2.3).
6. **IT-services firms appear as late channel partners and investors of already-large vendors** (Infosys with Cursor and Cognition, Wipro with
   Factory, Cognizant and Accenture with Anthropic), not as first customers of small tools. They are also being pushed to outcome pricing and
   to pass productivity gains to clients (section 2.5).
7. **Productivity and savings claims are received sceptically** unless the design is shown: METR's randomised trial moved opinion, then METR itself
   said its follow-up data was unreliable; Cursor's vendor A/B omits arm sizes; OpenAI stopped using SWE-bench Verified (section 2.6).
8. "Free audit/analysis first" is an established offer in cloud cost optimisation (ProsperOps: free analysis "in as little as 24 hours", fee a
   share of realised savings) and Cursor's own guidance for its router is "turn it on for one busy team and watch cost per commit for a week"
   (section 2.7).
9. **The network idea was not part of anyone's early story.** The only close analogue is Composio's July 2025 "shared learning layer" post, which
   came about 17 months after its repo was created and after it had tools customers (sections 2.9 and 5g).
10. A **proxy** is the best-trodden integration, but for Claude Code Anthropic's docs say they do not support routing to non-Claude models through any
    gateway, a gateway sees API requests and not the repo or tests, and sitting in the credential path has produced public incidents (section 3).

## 1. Company dossiers

Fields: (1) first product and first customers; (2) wedge and first channel; (3) pricing and free tier; (4) timing, only if the company states it;
(5) changes and abandoned products; (6) what they say was hard; (7) individual-to-B2B evidence; (8) funding. "Not found" means I looked and did not
find it in a primary source.

### 1.1 Composio (agent tool integrations)

- (1) No founder document on first customers found. The homepage on [wb:2023-06-05](https://web.archive.org/web/20230605111037/https://composio.dev/) read
  "Coming Soon / Automate and Personalize API integrations using AI" [P]; on [wb:2023-12-08](https://web.archive.org/web/20231208150142/https://composio.dev/)
  "Building the best GPT wrappers" [P]; on [wb:2024-04-19](https://web.archive.org/web/20240419083219/https://composio.dev/) "Connect 90+ tools to your AI
  agents ... Schedule a call" [P].
- (2) Open-source SDK: repo created 2024-02-23, MIT, 30,665 stars today ([P](https://api.github.com/repos/ComposioHQ/composio)). A free tier plus an
  "Announcing composio startups program" banner on the [wb:2024-07-16](https://web.archive.org/web/20240716231202/https://composio.dev/pricing) pricing page [P]. A
  public benchmark through a partner: LangChain's case study (Nov 11, 2024) says Composio's SWE-Kit agent solved 243 of 500 SWE-bench Verified
  issues, 48.60%, "fourth overall and second in the open-source category" ([P\*, LangChain-authored](https://www.langchain.com/blog/composio-swekit)); its Hacker News
  submission got 9 points and 0 comments ([P](https://hn.algolia.com/api/v1/search?query=Composio&tags=story)).
- (3) [wb:2024-07-16](https://web.archive.org/web/20240716231202/https://composio.dev/pricing): Individual free (100 connected accounts, 10k executions/month),
  Starter $39/month, Growth custom with RBAC, SSO, audit logs, on-prem by request [P]. Now: Hobby $0 (100,000 tool calls, no card), Pro $29/month, Enterprise
  custom with SSO/SCIM and customer-managed keys ([P](https://composio.dev/pricing)).
- (4) Repo created Feb 2024; Series A announced Jul 22, 2025 ([P](https://composio.dev/blog/series-a)). Time to first revenue: not found.
- (5) Positioning moved: API-integration automation, then GPT wrappers, then agent tools, then an open-source coding-agent kit, then (2025) "skills that evolve".
  The Series A post says the goal is a shared learning layer where "one agent's improvements benefit all agents" ([P\*](https://composio.dev/blog/series-a)).
  May 2026: agents can create anonymous, claimable accounts ([P](https://composio.dev/blog/your-agents-can-signup-for-composio)).
- (6) Their own list of open problems is about capturing tacit knowledge from interactions ([P](https://composio.dev/blog/series-a)). A security researcher
  (Cyera, Aug 13, 2026) reports that a leaked Composio API key returned live Gmail, GitHub and CircleCI tokens in a lab test and that rotating the key does not revoke
  downstream grants ([S, vendor research, unverified by me](https://www.cyera.com/research/the-hidden-attack-surface-of-agentic-ai-securing-ai-agent-integration-platforms)).
- (7) Enterprise features (SSO/SCIM, customer-managed keys, BAA, white-labeling) are paid add-ons on the pricing page ([P](https://composio.dev/pricing)). No conversion data.
- (8) Series A: "$29M" in the title but "$25M" in the page metadata; no lead named ([P](https://composio.dev/blog/series-a)). An IIT Bombay alumni page says $25M led by
  Lightspeed and that both founders hold a 2017 B.Tech in computer science from IIT Bombay ([S](https://acr.iitbombay.org/?p=314263)); an aggregator lists a different lead
  ([S](https://seedtable.com/companies/composio/funding-rounds/series-a-2025-07)). Lead investor unresolved.

### 1.2 Portkey (AI gateway)

- (1) Seed post (Aug 2023): over 50 million requests, 100+ companies worked with, one named early customer handling 10K questions a day, a claim of about 20% cost
  reduction and 20x faster answers from semantic caching ([P\*](https://portkey.ai/blog/building-a-full-stack-llmops-platform/)).
- (2) One-line proxy: change the OpenAI client base URL; Discord and Twitter community; planned partnerships with model providers and vector databases ([P](https://portkey.ai/blog/building-a-full-stack-llmops-platform/)).
  Open-source gateway repo created 2023-08-23, MIT, 13,243 stars ([P](https://api.github.com/repos/Portkey-AI/gateway)); Show HN "A lightweight AI gateway to 100+ models, in TS" on
  2024-01-08: 37 points, 13 comments ([P](https://hn.algolia.com/api/v1/items/38911677)). Content marketing on spend: "We Tracked $93M in LLM Spends" (Jan 2026) and a Claude Code limits explainer
  ([P](https://portkey.ai/blog)).
- (3) [wb:2023-12-04](https://web.archive.org/web/20231204172447/https://www.portkey.ai/pricing): Developer $0 (10k requests/month), Business $49 (a $99 figure also shown) up to 1M
  requests, Enterprise custom with SOC 2/GDPR/ISO and PII anonymiser [P]. February 2026: "making its basic enterprise gateway free" because governance "shouldn't be gated by budget"
  ([P\*](https://portkey.ai/blog/series-a-funding/)).
- (4) Seed Aug 2023 to Series A Feb 19, 2026, about 30 months ([P](https://portkey.ai/blog/series-a-funding/)). First revenue: not found.
- (5) From LLMOps stack to "control plane" for enterprise AI and agent governance; acquired. Palo Alto Networks closed the acquisition on May 29, 2026; no price disclosed
  ([P](https://paloaltonetworks.com/company/press/2026/palo-alto-networks-completes-acquisition-of-portkey-to-secure-ai-agents)).
- (6) Not stated beyond reliability themes. Note what they sell: governance and visibility of spend, not a saving.
- (7) Their pitch to buyers is "Engineering gets reliability. Finance gets accountability. Leadership gets answers"; claimed scale 500B+ tokens/day, 120M requests/day, 24,000+ organisations,
  "$180M+ annualized spend under management" (spend passing through, not revenue); Fortune 500 customers unnamed ([P\*](https://portkey.ai/blog/series-a-funding/)).
- (8) $3M seed led by Lightspeed India (Dev Khare, Manjot Pahwa) in Aug 2023 ([P](https://portkey.ai/blog/building-a-full-stack-llmops-platform/)); $15M Series A led by Elevation with Lightspeed on Feb 19, 2026
  ([P](https://portkey.ai/blog/series-a-funding/)).

### 1.3 Helicone (LLM observability proxy, YC W23)

- (1) Homepage [wb:2023-02-02](https://web.archive.org/web/20230202035833/https://www.helicone.ai/): "Observe your GPT-3 logs ... with one line of code", "Backed by Y Combinator", proxy via a base-path change [P].
- (2) Proxy + open source (repo created 2023-01-31, Apache-2.0, 6,230 stars, [P](https://api.github.com/repos/Helicone/helicone)). Product Hunt #1 Product of the Day (Aug 2024): nearly 800 upvotes; signups rose from 10-15 a day to 50-60 on launch day
  and the day after; free-to-paid conversion tripled; most support came from social media, cold outreach and email rather than the demo video ([P](https://www.helicone.ai/blog/product-hunt-experience)). Show HN (2025-01-23): 29 points, 7 comments
  ([P](https://hn.algolia.com/api/v1/items/42806254)).
- (3) [wb:2023-12-10](https://web.archive.org/web/20231210174312/https://www.helicone.ai/pricing): Free 100,000 requests/month, Pro $25/month, Enterprise (SOC 2, self-deployment) [P].
- (4) The founders' Show HN post says "22 months ago" for launch, with 2.1B requests and 2.6T tokens processed ([P](https://hn.algolia.com/api/v1/items/42806254)). First revenue: not found.
- (5) Moved from logging to a Log, Evaluate, Experiment, Review, Release workflow because "logging alone wasn't enough" ([P](https://www.helicone.ai/blog/introducing-helicone-v2)); then an AI gateway (Jun 2025) ([P](https://www.helicone.ai/blog)). Mintlify acquired Helicone
  on Mar 3, 2026; Helicone "will continue operating in maintenance mode" and Mintlify "will work closely with every customer to support a smooth migration to another platform" ([P](https://www.mintlify.com/blog/mintlify-acquires-helicone.md)). On 2026-10-21 Helicone's own site shows a banner
  "Helicone is no longer part of Mintlify. Details to come soon." and offers a 7-day free trial ([P](https://www.helicone.ai/)); I could not reconcile the two.
- (6) First architecture failed under load; query times went "from milliseconds to minutes" until they rebuilt on Kafka and ClickHouse ([P](https://www.helicone.ai/blog/introducing-helicone-v2)).
- (7) Self-hosting was added because security and compliance teams at regulated customers could not send data to a third party (banks, healthcare, government named as segments, no customers named) ([P](https://www.helicone.ai/blog/self-hosting-launch)).
- (8) Funding amounts: not found in a primary source. YC lists the team as 5 and the status as acquired ([P](https://www.ycombinator.com/companies/helicone)).

### 1.4 E2B (agent sandboxes)

- (1) First product was not a sandbox: [wb:2023-04-04](https://web.archive.org/web/20230404202859/https://e2b.dev/) "Open-Source AI Powered IDE That Does The Work For You"; by [wb:2023-11-08](https://web.archive.org/web/20231108210537/https://e2b.dev/) "Sandbox Runtime for LLMs" [P].
  Later named customers: Hugging Face, LMArena, Perplexity, Lindy, Groq, Manus ([P\*](https://e2b.dev/blog/series-a)).
- (2) Open source (repo created 2023-03-04, Apache-2.0, 14,488 stars, [P](https://api.github.com/repos/e2b-dev/E2B)); developer SDKs (Python, JS), Code Interpreter.
- (3) [wb:2024-11-09](https://web.archive.org/web/20241109015309/https://e2b.dev/pricing): Hobby free with a one-time $100 usage credit and no card; Pro $150/month; Custom; and a banner "we just closed a new funding round of $11.5M led by Decibel" [P].
- (4) Timeline from their own page: 2023 SDK, 2024 SDK 1.0, 2025 Series A, 1B sandbox starts in June 2026, 2B in October 2026 ([P\*](https://e2b.dev/about)). First revenue: not found.
- (5) "They shifted their focus from the agent itself to the reusable machine underneath it" ([P](https://e2b.dev/about)). That is a pivot from an agent to infrastructure, driven by what was reusable.
- (6) Fortune 100 customers pushed it "to meet the highest bars for security, compliance, and reliability"; enterprise modules (secrets vault, observability, shared context) are listed as planned ([P](https://e2b.dev/blog/series-a)).
- (7) "88% of the Fortune 100" had *signed up* at Series A (later "94 of the Fortune 100", "100,000+ teams have run at least one sandbox"); signup is not paid usage and the page does not say how many pay ([P\*](https://e2b.dev/blog/series-a), [P\*](https://e2b.dev/about)).
  Deploy "on your infrastructure of choice" including a customer VPC is the enterprise path.
- (8) $11.5M led by Decibel (banner above); $21M Series A led by Insight Partners with Decibel, Sunflower, Kaya ([P](https://www.insightpartners.com/ideas/e2b-raises-a-21m-series-a-to-offer-cloud-for-ai-agents-to-fortune-100/)).

### 1.5 LangChain / LangSmith

- (1) `langchain` began as a side project in fall 2022; the company was founded Feb 2023; LangSmith went into closed beta in July 2023 as a separate commercial product ([P](https://www.langchain.com/blog/three-years-langchain)).
- (2) Open-source framework as the funnel (repo created 2022-10-17, MIT, 148,202 stars, [P](https://api.github.com/repos/langchain-ai/langchain)); LangSmith sold to the same developers.
- (3) [wb:2024-02-24](https://web.archive.org/web/20240224151352/https://www.langchain.com/pricing): Developer free (1 user, 3,000 traces/month), Plus $39/user/month, Enterprise custom (SSO, self-hosted options), with the note "We will not enforce pricing on the Plus plan until April 2024" [P] (about nine months after the July 2023 beta).
- (4) GA on Feb 15, 2024 with "over 80K signups", "over 5K monthly active teams", customers named Rakuten, Elastic, Moody's, Retool ([P\*, wb:2024-02-28](https://web.archive.org/web/20240228141009/https://blog.langchain.dev/langsmith-ga/)).
- (5) Separated LangSmith from `langchain` to be framework-neutral; built LangGraph for control; rebuilt `langchain` 1.0 (Oct 20, 2025) after complaints about "breaking changes, hidden prompts, package bloat" ([P](https://www.langchain.com/blog/three-years-langchain)).
- (6) Unreliable model output and loss of control in the high-level interfaces (same post).
- (7) Enterprise features listed as future work at GA ("administration and security needs for our largest customers"); the open library generated the audience, the closed platform the revenue ([P](https://www.langchain.com/blog/langsmith-ga)).
- (8) Series A $25M led by Sequoia with the GA post (Feb 15, 2024); $125M at $1.25B in Oct 2025 ([P](https://www.langchain.com/blog/three-years-langchain)); the $10M Benchmark seed (Apr 2023) is from search summaries of press ([S](https://techcrunch.com/2025/10/21/open-source-agentic-startup-langchain-hits-1-25b-valuation), not opened).

### 1.6 Langfuse (open-source LLM observability, YC W23)

- (1) Their own timeline: the three founders first built a usage-based billing product (2022), joined YC W23, tried several AI ideas for under two weeks each in Jan-Apr 2023, then in May-Aug 2023 pivoted to open-source tooling for LLM developers; "early YC customers spent a lot of
  time in-product and referred other teams" ([P](https://langfuse.com/handbook/chapters/story)).
- (2) Show HN on 2023-08-29: 143 points, 35 comments; the founder wrote "We're borrowing our OSS business model from Posthog/Supabase" and that all code was MIT ([P](https://hn.algolia.com/api/v1/items/37310070)). Launched on Product Hunt in August and was Product of the Day ([P](https://langfuse.com/blog/announcing-our-seed-round)).
- (3) At launch, pricing was by storage ("picked storage as the first metric to price on", co-founder comment, [P](https://hn.algolia.com/api/v1/items/37310070)); cloud trial without a card. Sep 2025: Core plan cut from $59 to $29/month, graduated usage pricing, "self-serve Enterprise, avoiding mandatory sales calls"
  ([P](https://langfuse.com/handbook/chapters/story)). Jun 2025: all product features open-sourced under MIT; only enterprise security and platform features stay commercial (same page).
- (4) 1,000 GitHub stars in Oct 2023, 10,000 in Apr 2025, SDK installs above 2M/month in Sep 2024 (same page); 36,137 stars today ([P](https://api.github.com/repos/langfuse/langfuse)). Seed to acquisition: about 26 months.
- (5) Repositioned from "product analytics for LLM apps" to "the AI engineering platform" (Apr 2024); moved the data layer from Postgres to ClickHouse (v3, Dec 2024) because volume outgrew it (same page).
- (6) "Building LLM apps is easy to demo and hard to run in production"; data volume outgrew Postgres ([P](https://langfuse.com/blog/joining-clickhouse)).
- (7) Their seed post says enterprise traction "came from individual contributors who self-host to move fast while keeping production data under their control" ([P\*](https://langfuse.com/blog/announcing-our-seed-round)). Sales handbook: "We prioritize inbound", "we avoid broad cold outbound", segments by contract value
  (self-serve 0-10k; sales-assisted 10-50k with Enterprise from about 3k/month on a 12-month term; sales-led 50k+), "Make security reviews as self-servable as possible" with a public security centre, and "We don't have a self-hosted commercial offering" ([P](https://langfuse.com/handbook/sales-and-cs/sales.md)).
- (8) $4M seed from Lightspeed, La Famiglia and Y Combinator, post dated Nov 7, 2023 ([P](https://langfuse.com/blog/announcing-our-seed-round)); the handbook says Sep 2023 (a conflict inside their own pages). ClickHouse acquired Langfuse on Jan 16, 2026; "we did not plan to sell" and they "had term sheets for a Series A"
  ([P](https://langfuse.com/handbook/chapters/story), [P](https://langfuse.com/blog/joining-clickhouse)).

### 1.7 Braintrust (evals and observability; closed source)

- (1) The founder says Zapier "was our first user" and pushed back ("I know this is a problem, you seem smart, but I'm not convinced of the solution"); Coda and Airtable followed, and the hybrid deployment model was "a big reason" because "there was just no chance they would be able to use the
  product unless the data stayed in their cloud" ([P\*, founder interview](https://latent.space/p/braintrust)). Seed post names Zapier, Coda, Airtable, Instacart ([P](https://www.braintrust.dev/blog/seed-round)).
- (2) Show HN on 2023-09-28: 8 points, 2 comments; first post states on-prem and private-VPC support ([P](https://hn.algolia.com/api/v1/items/37692239)). Evals as a shared way to settle debates; angels include the heads of Hugging Face, OpenAI, Airtable, Vercel and Datadog ([P](https://www.braintrust.dev/blog/seed-round)).
- (3) Free trial then Pro; a 2026 Starter plan ([P](https://www.braintrust.dev/blog)); startup programme: six to twelve months of Pro for companies with at least $100k of funding, twelve months if funded by named partner VCs ([P](https://www.braintrust.dev/blog/braintrust-for-startups)).
- (4) Seed Dec 13, 2023; Series A Oct 8, 2024; Series B Feb 17, 2026; SOC 2 Type II Jul 15, 2024 ([P](https://www.braintrust.dev/blog)).
- (5) Built its own database (Brainstore) because traces reached "hundreds of megabytes per interaction" ([P](https://www.braintrust.dev/blog/announcing-series-b)).
- (6) The founder's earlier company taught him that "if you don't have a very deep, intuitive understanding of your customer, everything becomes harder" (same interview).
- (7) Self-serve entry plus demo requests; Series A names Notion, Stripe, Vercel, Airtable, Instacart, Zapier, Coda, The Browser Company ([P\*](https://www.braintrust.dev/blog/announcing-series-a)).
- (8) $5.1M seed led by Greylock; $36M Series A led by a16z; $80M Series B led by ICONIQ ([P](https://www.braintrust.dev/blog/seed-round), [P](https://www.braintrust.dev/blog/announcing-series-a), [P](https://www.braintrust.dev/blog/announcing-series-b)). A reported May 2026 breach telling customers to rotate keys is known to me only from a headline ([S](https://hn.algolia.com/api/v1/items/48042494); page 403).

### 1.8 OpenRouter (model marketplace and gateway)

- (1) May 2023 homepage was a list of "Trending Apps" by tokens ([P wb:2023-05-25](https://web.archive.org/web/20230525161713/https://openrouter.ai/)); June 2023 docs: "Create self-funded & model-agnostic AI apps ... Users can pay for what they use, instead of developers", "Pricing is free for developers"
  ([P wb:2023-06-29](https://web.archive.org/web/20230629230218/https://openrouter.ai/docs)). The company calls itself "the first LLM marketplace" that began in early 2023 ([P](https://openrouter.ai/about)).
- (2) Marketplace and public usage rankings; one API for many models (HN: "Show HN: Ranking LLMs by Usage over Time", 2023-11-25, 3 points, [P](https://hn.algolia.com/api/v1/search_by_date?query=openrouter.ai&tags=story)). Not open source.
- (3) FAQ [wb:2025-03-24](https://web.archive.org/web/20250324042112/https://openrouter.ai/docs/faq): "there is no markup on inference pricing (however we do charge a fee when purchasing credits)" [P]. Today: Free (50 requests/day), Standard 5.5% fee on credits, Business 8%, bring-your-own-key free for the first $25,000/month of list-price
  inference then 5%, Enterprise "fee discounts available" ([P](https://openrouter.ai/pricing)).
- (4) Series B ($113M, led by CapitalG): "8M+ developers", weekly tokens 5T to 25T in six months ([P\*](https://openrouter.ai/blog/announcements/series-b/)); "10+ trillion tokens per day" and "at least 10x growth in inference volume every year" in the Aug 19, 2026 Stripe announcement ([P\*](https://openrouter.ai/blog/announcements/openrouter-is-joining-stripe/)).
- (5) Added routing features late: Auto Router; an Oct 2, 2026 router benchmark that weights quality 60%, time 20%, cost 20%, concedes that switching models rebuilds caches and adds latency, and notes that blended routers "don't disclose the models used or how often switching happens" ([P](https://openrouter.ai/blog/announcements/model-router-benchmarks/)).
- (6) Not stated.
- (7) Enterprise: contract pricing, SSO/SAML, SLAs, Private Endpoints "available by request on the Enterprise plan" ([P](https://openrouter.ai/pricing), [P](https://openrouter.ai/announcements)).
- (8) Stripe acquisition announced Aug 19, 2026 with no terms; Bloomberg, relayed by TechCrunch, reports "$7B+" and Stripe did not comment ([P](https://openrouter.ai/blog/announcements/openrouter-is-joining-stripe/), [S](https://techcrunch.com/2026/08/16/stripe-will-reportedly-acquire-ai-gateway-startup-openrouter-for-7b/)). **Revenue, take rate and margin are not disclosed in any primary source I found.**

### 1.9 Martian (model router)

- (1) Homepage [wb:2024-01-19](https://web.archive.org/web/20240119154136/https://withmartian.com/): "We invented the first LLM router ... beat GPT-4 on performance, reduce costs by 20%-97%" [P\*]. Accenture Ventures invested (Sep 17, 2024), citing "300+ companies, from Amazon to Zapier" whose engineers "have used Martian"; terms undisclosed
  ([P\*](https://newsroom.accenture.com/news/2024/accenture-invests-in-martian-to-bring-dynamic-routing-of-large-language-queries-and-more-effective-ai-systems-to-clients)).
- (2) Drop-in API replacement ("a few lines") plus consulting on custom routers ([P wb:2024-01-19](https://web.archive.org/web/20240119154136/https://withmartian.com/)).
- (3) [wb:2024-01-21](https://web.archive.org/web/20240121071314/https://withmartian.com/pricing): Developer plan "20% of AI Spend"; Enterprise custom; VPC deployment [P]. This is the one explicit router take rate I found.
- (5) On 2026-10-21 the homepage describes "a team of researchers who've left the big labs to focus on understanding machine intelligence", names no router, and the docs list Gateway (200+ models), ARES (an RL framework for training and evaluating coding agents) and K-Steering ([P](https://www.withmartian.com/), [P](https://docs.withmartian.com)).
- (8) A $9M seed from NEA, Prosus, Carya and General Catalyst is in search summaries only ([S](https://neuronfeed.com/startups/martian-router), unverified).

### 1.10 Not Diamond (model router) — nearest competitor

- (3) [wb:2024-08-03](https://web.archive.org/web/20240803102239/https://www.notdiamond.ai/pricing): free up to 100K routing requests/month, $100/month plus $0.001 per request after 100K, Enterprise with VPC [P]. Today: pay-as-you-go "$0.05 per million tokens routed" (apply for access), Enterprise "contact us" ([P](https://www.notdiamond.ai/pricing)).
- (1,7) Logos include Hugging Face, Dropbox, IBM, DoorDash, Rootly, American Express and OpenRouter; testimonials from OpenRouter's CEO, Replicated and Rootly ([P\*](https://www.notdiamond.ai/)). The page's headline says "Cost savings 50%+" while its own calculator example models 25% lower cost "at equivalent output quality", unreconciled (same page).
- (5) "the world's most powerful intelligent model router for coding agents" (same page). **Not Diamond Code (Aug 4, 2026):** a router "for long-running coding agents", run as a **local proxy** beside the harness (not a hook); claims "reducing costs by 20%+ without any degradation in quality"; on a
  Poly-SWE-bench Verified run with Claude Code as the harness it approximated Opus 4.8 XHigh at 39% lower cost; early access, waitlist, SOC 2 and ISO 27001; company-run benchmarks; notes that it may keep a pricier model to preserve a warm cache ([P\*](https://www.notdiamond.ai/blog/not-diamond-code-intelligent-model-routing-for-coding-agents)).
- (6) Their own buyer-guide post says classifier routers fail on long sessions because model switches force cache rewrites that can cost more than the saving, cites Anthropic's $250 per developer per month average, and asks buyers to demand "at least 10-20%" savings ([P\*](https://www.notdiamond.ai/blog/how-to-control-coding-agent-costs-with-intelligent-model-routing)).
- (8) Funding: not found in a primary source.

### 1.11 Unify (router, then something else)

- (3) [wb:2024-09-22](https://web.archive.org/web/20240922050517/https://unify.ai/pricing): Developer free (10K queries/month), Professional $40/user/month "LLM Routing" included, Enterprise on-prem [P].
- (5) [wb:2025-05-26](https://web.archive.org/web/20250526154254/https://unify.ai/): "Hire AI, Not APIs ... Assign Tasks by Call, E-mail, or Message", "Excited to Announce Our $8M Funding Round" [P]. On 2026-10-21: "Continual learning for AI teams ... Models that keep learning ... Every user interaction trains the model driving your agent", no routing, no pricing, no customers
  named ([P](https://unify.ai/)). Three different positioning statements within about two years; the router product I could find is the 2024 one. The router's 2024 Product Hunt launch (307 upvotes) is from search summaries ([S](https://www.producthunt.com/p/unify-5/unify-router)). TechCrunch's profile returned 403.

### 1.12 Modal (serverless compute)

- (1,2) Closed platform with an open client (repo created 2022-10-18, Apache-2.0, [P](https://api.github.com/repos/modal-labs/modal-client)). [wb:2022-11-03](https://web.archive.org/web/20221103020227/https://modal.com/pricing): pay per use (CPU $0.32/core-hour, GPU $2.10/hour), "All Modal users get $30/month of free credits" [P].
- (4) GA with no waitlist on Oct 10, 2023, "after more than two years of deep hacking"; named Ramp, Substack, SphinxBio; $16M Series A led by Redpoint with Amplify, $23M total; money to "attract more enterprise customers" ([P](https://modal.com/blog/general-availability-and-series-a-press-release), [P](https://modal.com/blog/general-availability-and-series-a)).
- (6) "we would have to build almost all of it ourselves" (file system, container runtime) ([P](https://modal.com/blog/general-availability-and-series-a)).
- (8) Series D on Oct 14, 2026: $750M at $15.75B led by Accel, "$1B in annualized run rate", revenue "more than 15x year-over-year", sandboxes a "$300M run rate product on its own"; customers include Cognition, Ramp, DoorDash ([P\*](https://modal.com/blog/1-billion-annualized-run-rate-series-d)). Note the growth arrived when coding agents needed sandboxes.

### 1.13 Supabase (open-source backend, YC S20)

- (1,2) Hacker News: "Supabase (YC S20) - An open source Firebase alternative" on 2020-05-27: 1,120 points, 366 comments (posted by a third party) ([P](https://hn.algolia.com/api/v1/search_by_date?query=supabase&tags=story&numericFilters=created_at_i<1606780800)). The founders' postmortem: about 100 signups before launch, 600 on day one, 1,400 in seven days,
  GitHub stars 300 to 1,300 overnight, over 1,000 customer databases, hit provider limits within hours ([P](https://supabase.com/blog/alpha-launch-postmortem)).
- (3) Series A post (Sep 2021): over 50,000 databases, over 40,000 developer signups, "Our growth is all organic" ([P\*](https://supabase.com/blog/supabase-series-a)). Free tier at the Nov 2021 [wb](https://web.archive.org/web/20211124085109/https://supabase.com/pricing): $0 per project, unlimited API requests ([P](https://web.archive.org/web/20211124085109/https://supabase.com/pricing)).
- (7) Early named customers: two (Xendit, Mobbin) at the Series A ([P\*](https://supabase.com/blog/supabase-series-a)); "We loaded our Seed round with developers, technical founders, and developer advocates" ([P](https://supabase.com/blog/angels-of-supabase)).
- (8) $5MM seed filed with the SEC (HN link to Form D, Nov 2020, [P](https://hn.algolia.com/api/v1/search_by_date?query=supabase&tags=story&numericFilters=created_at_i<1606780800)); $30M Series A (Sep 2021); $500M Series F at $10B pre-money; "Nearly 10 million developers"; over 60% of new databases launched through an AI tool; "Growth is accelerating since January as Claude Code and Codex expand the number of
  people who can build" ([P\*](https://supabase.com/blog/supabase-series-f)). ARR and customer counts: not disclosed.

### 1.14 Vercel (Next.js)

- Next.js repo created 2016-10-05, MIT, 143,119 stars ([P](https://api.github.com/repos/vercel/next.js)). Series F (Sep 30, 2025): $300M at $9.3B; "over 500 million" Next.js downloads in 12 months; AI SDK weekly downloads from 446,012 to 3,209,817 in a year; open source called "a powerful flywheel"; ARR and customer counts not disclosed ([P\*](https://vercel.com/blog/series-f)).
  I did not find Vercel's own account of first customers or time to first revenue.

### 1.15 Sourcegraph / Cody

- Closed new Cody Free/Pro sign-ups on Jun 25, 2025 and ended both plans on Jul 23, 2025 (four weeks), also removed Cody from Enterprise Starter; Cody Enterprise "remains fully supported"; users were pointed to a separate product, Amp, with $10-$40 credits ([P](https://sourcegraph.com/blog/changes-to-cody-free-pro-and-enterprise-starter-plans)). The stated reason was to "better align with how our products are being used today". Amp is reported to have become
  a separate company in Dec 2025 ([S](https://www.promptquorum.com/power-local-llm/sourcegraph-cody-review)), and the main repo to have gone private in Aug 2024 ([S](https://devclass.com/2024/08/21/sourcegraph-makes-core-repository-private-co-founder-complains-open-source-means-extra-work-and-risk)).
  Reading: a coding assistant with consumer plans was cut back to enterprise-only.

### 1.16 Codeium / Windsurf / Cognition (Devin)

- Codeium [wb:2022-12-06](https://web.archive.org/web/20221206193306/https://codeium.com/pricing): "Early users get free forever access"; sold by the company then called Exafunction ([P](https://web.archive.org/web/20221206193306/https://codeium.com/pricing)). Origin as a GPU-virtualisation company and the later rename to Windsurf are from press ([S](https://www.unite.ai/varun-mohan-co-founder-ceo-of-codeium-interview-series/)).
- Cognition's own note (Jul 14, 2025) on acquiring Windsurf: "$82M of ARR", "enterprise ARR doubling quarter-over-quarter", "350+ enterprise customers" ([P\*](https://cognition.com/blog/windsurf)). The $2.4B Google licensing/hiring deal and OpenAI's collapsed purchase come from press ([S](https://news.bloomberglaw.com/ip-law/openais-3-billion-deal-to-buy-ai-startup-windsurf-falls-apart)).
- Devin pricing page [wb:2024-10-05](https://web.archive.org/web/20241005100409/https://devin.ai/pricing): Personal and Team "Request access for pricing"; Enterprise with VPC deployment and custom fine-tuned agents [P]. The later drop of the entry plan from $500 to $20 is from press ([S](https://fast.io/resources/devin-ai-core-plan/)).
- Cognition's "Devin's 2025 performance review": "67% of its PRs are now merged vs 34% last year", Goldman Sachs, Santander, Nubank named, no methodology; its own limits: ambiguous tasks and "usually performs worse when you keep telling it more after it starts the task" ([P\*](https://cognition.com/blog/devin-annual-performance-review-2025)).

### 1.17 Cursor (Anysphere)

- (1,3) [wb:2023-05-23](https://web.archive.org/web/20230523155256/https://cursor.sh/pricing): Free (100 prompts/month), Pro $20/month (150K free GPT-4 tokens then $0.06/1K), and "OpenAI Key: use Cursor completely at-cost"; "Why isn't Cursor completely free? Large-language models cost quite a bit of money to run"; support via "DM @truell20"; an "Enterprise" link already in the nav [P].
- (2) Hacker News "Cursor - The AI-First Code Editor" 2023-10-15: 40 points, 57 comments; a founder wrote "We forked VSCode because we wanted to change the UI in ways Microsoft wouldn't allow" ([P](https://hn.algolia.com/api/v1/items/37888477)).
- (4) Self-reported: "over 40,000 customers" (Aug 22, 2024, Series A $60M) ([P\*](https://cursor.com/blog/series-a)); "exceeded $100m in recurring revenue" (Jan 16, 2025, $105M) ([P\*](https://cursor.com/blog/series-b)); "over $500 million in ARR" and "used by over half of the Fortune 500, including NVIDIA, Uber, and Adobe" (Jun 2025) ([P\*](https://cursor.com/blog/series-c));
  "$1B in annualized revenue", $2.3B at $29.3B (Nov 13, 2025) ([P\*](https://cursor.com/blog/series-d)).
- (5) Pricing changes cost trust: Pro moved from request counts to usage-based API pricing on June 16, 2025 ("New models can spend more tokens per request on longer-horizon tasks"); on July 4 CEO apology and refunds for unexpected usage ([P](https://cursor.com/blog/june-2025-pricing)). Bought Graphite (Dec 19, 2025) because "reviewing changes, merging them safely ... have increasingly become the
  bottlenecks" ([P](https://cursor.com/blog/graphite)); became part of SpaceX on Aug 14, 2026 citing GPU access and "more capable models at lower cost" ([P](https://cursor.com/blog/joining-spacex)).
- (7) In Infosys's filing with the SEC, Cursor's CEO is quoted as looking forward to Infosys enabling "over 100,000 software engineers" with agentic coding platforms; the same filing lists a separate Infosys-Cognition collaboration ([P](https://www.sec.gov/Archives/edgar/data/0001067491/000106749126000018/exv99w02.htm)).
- Cursor Router (Jul 22, 2026): see 2.3.

### 1.18 Other evidence that is better than the list you gave

- **Kite (failure, founder's own words, Nov 16, 2022).** "We grew our user base to 500,000 monthly-active developers, with almost zero marketing spend. Then, our product failed to generate revenue. Our 500k developers would not pay to use it. Our diagnosis is that individual developers do not pay for tools.
  Their manager might, but engineering managers only want to pay for discrete new capabilities, i.e. making their developers 18% faster when writing code did not resonate strongly enough." Also: no product-market fit until 2019, five years in; "10+ years too early" ([P wb:2022-11-22](https://web.archive.org/web/20221122101021/https://www.kite.com/blog/product/kite-is-saying-farewell/)). The HN thread got 1,083 points ([P](https://hn.algolia.com/api/v1/items/33685209)).
- **LiteLLM (open-source proxy, YC W23).** Show HN Aug 12, 2023: 140 points, 34 comments ([P](https://hn.algolia.com/api/v1/items/37095542)). Open-source gateway; paid enterprise licence for SSO/SCIM, audit logs, support SLAs; "SSO is free for up to 5 users"; 30-day trial ([P](https://docs.litellm.ai/docs/enterprise)). Anthropic's docs say "several large enterprises reported using LiteLLM" to track spend
  ([P](https://code.claude.com/docs/en/costs)). A seed of $1.6M is in YC job posts via search ([S](https://www.ycombinator.com/companies/litellm/jobs/6uvoBp3-founding-backend-engineer)). Supply-chain compromise of its PyPI package on Mar 24, 2026 (versions 1.82.7 and 1.82.8, credential-stealing) is reported by several security outlets ([S](https://bleepingcomputer.com/news/security/popular-litellm-pypi-package-compromised-in-teampcp-supply-chain-attack)).
- **Mem0 (agent memory; nearest "memory layer" company).** $24M seed plus Series A (Oct 28, 2025): "41,000 GitHub stars", "14 million Python package downloads", API calls from 35M (Q1) to 186M (Q3); native integrations with CrewAI, Flowise, Langflow; "AWS selected us as the exclusive memory provider for their new Agent SDK" (their claim). The post never says "open source" but the traction cited is stars, downloads and framework integrations ([P\*](https://mem0.ai/series-a)).
- **Humanloop (shut down, [S]).** Anthropic hired the co-founders and most of the team in Aug 2025 but, per its spokesperson, acquired no assets; the platform went offline Sep 8, 2025 ([S](https://techcrunch.com/2025/08/13/anthropic-nabs-humanloop-team-as-competition-for-enterprise-ai-talent-heats-up) via search; competitor notice [S](https://wandb.ai/wandb_fc/product-announcements-fc/reports/Humanloop-is-sunsetting-Migrate-to-Weights-Biases-as-an-alternative--VmlldzoxMzk4ODc1Nw)).
- **Verification and quality sellers.** CodeRabbit: Series B $60M at $550M led by Scale Venture Partners (Sep 2025) ([P](https://www.coderabbit.ai/newsroom/coderabbit-series-b-60-million)); "over 8,000 paying customers" and "$15M ARR" come from press ([S](https://finance.yahoo.com/news/coderabbit-raises-60m-valuing-2-190000807.html), via search). Graphite acquired by Cursor ([P](https://cursor.com/blog/graphite)). Qodo (formerly CodiumAI) rebranded around a "code integrity" platform
  with a $40M Series A in Sep 2024 ([S](https://www.unite.ai/qodo-raises-40m-to-enhance-ai-driven-code-integrity-and-developer-efficiency/)).
- **Outcome pricing for AI.** Intercom's Fin is "$0.99 per outcome"; an outcome counts when "A customer confirms their issue is resolved" or "They don't ask for more help after Fin responds" or Fin "completes a workflow (Procedure)" ([P](https://www.intercom.com/pricing)). Note the verifier is the customer's behaviour, not a test.
- **Cloud cost optimisation (savings-share precedent).** ProsperOps: "takes a small percentage of the realized savings"; "Free Savings Analysis ... 100% free ... as little as 24 hours"; its customer list includes a "Top 3 Global Systems Integrator" with $1,767,430 lifetime savings, the smallest on the list ([P\*](https://www.prosperops.com/pricing/)). Cast AI publishes no price and no free audit on its pricing page ([P](https://cast.ai/pricing)).
  Spot.io and Usage.ai describe pay-as-you-save tiers ([S](https://www.usage.ai/blogs/finops/savings-based-cloud-cost-optimization-pricing/)).

### 1.19 Summary table (facts repeat the cited lines above)

| Company | First wedge | Open source | First price shape | Where it ended up (2026-10-21) |
|---|---|---|---|---|
| Composio | SDK + tool catalogue, startups programme, partner benchmark (1.1) | Yes (MIT) | free, then $39/month | independent; Series A 2025 |
| Portkey | one-line proxy, OSS gateway (1.2) | gateway yes | free, $49/month | bought by a security vendor |
| Helicone | one-line proxy, PH/HN (1.3) | Yes (Apache) | free 100k, $25/month | maintenance mode |
| E2B | OSS sandbox SDK (1.4) | Yes (Apache) | free + $100 credit, $150/month | independent; Series A |
| LangChain | OSS library then paid platform (1.5) | library yes | free, $39/user | independent; $1.25B |
| Langfuse | OSS + HN + PH (1.6) | Yes (MIT) | storage-based, then $59, $29 | bought by ClickHouse |
| Braintrust | high-touch first user, closed (1.7) | No | free trial, Pro | independent; Series B |
| OpenRouter | marketplace, rankings (1.8) | No | fee on credits | joining Stripe |
| Martian | router API (1.9) | No | 20% of AI spend | research lab; no router on homepage |
| Not Diamond | router API (1.10) | partial | 100K free, $0.001 per request | coding-agent router, $0.05/M tokens |
| Unify | router (1.11) | No | free, $40/user | different product |
| Modal | serverless compute (1.12) | client | pay per use, $30 free | $1B run rate (claimed) |
| Supabase | OSS + HN (1.13) | Yes | free tier, then $25 | $10B pre-money Series F |

## 2. Cross-cutting findings

### 2.1 Which wedges worked for a two-to-three-person team with little money

Observed in the dossiers (evidence quality varies; none is a controlled comparison):

- **A public launch where developers already look, with a one-line or one-command start**: Supabase's Hacker News launch (1,120 points; about 100 to 1,400 signups in a week, 1.2), Helicone's base-URL change and later Product Hunt day, Langfuse's Show HN (143 points) and Product Hunt, Portkey's Show HN (37 points), Cursor's HN (40 points).
  The spread of Show HN outcomes is wide: Braintrust 8 points, Helicone 29, Portkey 37, Langfuse 143, Supabase 1,120 (all from the HN API links above). A launch is a lottery with a long tail, not a plan.
- **Open source as trust and distribution** (see 2.2).
- **A public benchmark delivered through a partner**: Composio's SWE-bench result inside a LangChain case study (1.1). Benchmarks age (2.6).
- **Programmes for startups and free tiers**: Composio's startups programme, Braintrust's six-to-twelve-month Pro offer, E2B's $100 credit, Modal's $30 a month ([P] links in 1.1, 1.4, 1.7, 1.12).
- **Very high-touch work with one demanding first user, then the neighbours**: Braintrust's Zapier then Coda and Airtable (1.7); Langfuse's early YC customers referring others (1.6).
- **Funding followed public traction within months**, which is how "little money" teams were funded: Langfuse launched Aug 2023 and announced a seed in Nov 2023; Braintrust Show HN Sep 2023 and seed Dec 2023; Supabase's HN launch May 2020 and an SEC-filed seed in Nov 2020 (1.6, 1.7, 1.13). Survivorship caveat: I only see the ones that did.

Not found: any case where a first enterprise customer came from cold outbound by a two-person company. Langfuse's handbook says it "avoids broad cold outbound" and prioritises inbound (1.6); Helicone's PH day credits cold outreach and email as well as social media (1.3).

### 2.2 Open source versus closed for infrastructure sold to engineers

- Open-source-led in the dossiers: Supabase, Langfuse, Helicone, E2B, Portkey's gateway, LiteLLM, Composio's SDK, Next.js, Mem0 (stars and downloads as traction). Closed: Braintrust, OpenRouter, Cursor, Martian, Unify, Not Diamond (with occasional open base-router releases, per its pricing FAQ in the 2024 snapshot).
- Outcomes do not sort by licence: closed-source Braintrust ($80M Series B), OpenRouter (reported $7B+), Cursor, and open-source Supabase ($10B pre-money), Vercel ($9.3B), Langfuse and Helicone (acquired, the latter into maintenance mode).
- What open source bought was **trust with security-sensitive buyers and self-hosting as the enterprise path**: Helicone built self-hosting because regulated customers could not send data out (1.3); Langfuse's enterprise users self-host (1.6); Braintrust, closed, needed a hybrid deployment where "the data stayed in their cloud" to win Coda and Airtable (1.7); E2B offers customer-VPC deployment (1.4).
- What open source cost: Sourcegraph moved its core repository private in 2024 [S, 1.15]; Langfuse in 2025 open-sourced *all* product features and kept only security and platform features commercial (1.6), i.e. open source was the strategy and security/administration the product.
- For a tool that touches source code and tests, the data path is the sales conversation. Both open and closed companies ended up offering "your data stays in your environment".

### 2.3 Companies that sell COST SAVINGS, and the structural problem

**What the router companies say about their own business**

- Martian: "reduce costs by 20%-97%" (Jan 2024) and a developer price of "20% of AI Spend" (1.9); by 2026 the homepage presents a research group and the docs list a gateway, an RL framework for coding agents and a steering toolkit.
- Not Diamond: per-request pricing (2024) became "$0.05 per million tokens routed" (2026); its newest product claims 20%+ savings (1.10).
- Unify: router with $40/user/month (2024), then an assistant, then continual learning (1.11).
- OpenRouter: sells access, not savings: "no markup on inference pricing", a 5.5% or 8% fee on credits, 5% on bring-your-own-key beyond $25,000/month; its own router benchmark says benchmarks "are only representative of general tasks rather than your own work" (1.8).
- Portkey: sells governance and visibility ("$180M+ annualized spend under management"); basic gateway made free (1.2).
- Take rates: the only router take rate disclosed anywhere I looked is OpenRouter's 5.5/8% fee on credits, Martian's 20% of spend (early 2024), and Not Diamond's $0.05 per million tokens. Margins and revenue are **not disclosed** for any of them.

**Providers and tools ship their own selection**

- Cursor Router (Jul 22, 2026, Teams and Enterprise): classifies each request and routes it; "In online A/B tests across millions of requests ... frontier-quality performance at 60% savings"; early access: "three high-volume accounts with thousands of users saved 30%-50% ... versus routing everything to Opus 4.8"; cost per commit $4.63 (Balance) and $6.76 (Intelligence) vs $7.34 for all-Opus; includes cache-miss costs; no arm sizes, confidence intervals or exact baseline for the 60% figure
  ([P\*](https://cursor.com/blog/router)); modes Cost/Balance/Intelligence, admins choose which are allowed ([P](https://cursor.com/docs/cursor-router)). The docs do not say whether routing uses tests or verification (same page).
- GitHub Copilot "Auto": GA in VS Code Dec 10, 2025, with "a 10% discount on the model multiplier", routing "based on real-time availability" ([P](https://github.blog/changelog/2025-12-10-auto-model-selection-is-generally-available-in-github-copilot-in-visual-studio-code/)); later JetBrains and CLI ([S](https://github.blog/changelog/2026-04-17-github-copilot-cli-now-supports-copilot-auto-model-selection), via search).
- Gemini CLI: default model `auto`; fallback policies; an experimental local-Gemma router "can reduce hosted-model costs" ([P](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/model-routing.md)).
- Claude Code: `opusplan` (Opus in plan mode, Sonnet to execute), effort levels, per-subagent `model: haiku`, and a docs page on reducing cost ([P](https://code.claude.com/docs/en/model-config), [P](https://code.claude.com/docs/en/costs)). I found no automatic cross-model router in the parts of the Claude Code docs I read (the model-config page was read only to its first 100,000 characters).
- OpenAI's GPT-5 launched with a router between a fast and a thinking model; the router "wasn't working properly" at rollout according to the CEO, and users complained about lack of control ([S](https://fortune.com/2025/08/12/openai-gpt-5-model-router-backlash-ai-future), via search; OpenAI's own pages returned 403).

**Prices fall.** a16z, Nov 12, 2024: "For an LLM of equivalent performance, the cost is decreasing by 10x every year" ([P\*, VC essay](https://a16z.com/llmflation-llm-inference-cost/)). Counter-trend in agent spend: Not Diamond says spend is rising because of longer, parallel sessions ([P\*](https://www.notdiamond.ai/blog/how-to-control-coding-agent-costs-with-intelligent-model-routing)); Cursor moved to usage pricing because newer models "can spend more tokens per request" (1.17).
Net: the *absolute* dollars in coding-agent spend are rising even if the price per fixed capability falls, but a saving defined against the buyer's current frontier model gets re-based every model release.

**Open-source routers exist.** RouteLLM (LMSYS, Jul 2024): "cost reductions of over 85% on MT Bench, 45% on MMLU, and 35% on GSM8K" against GPT-4-only at 95% of GPT-4 quality, with caveats on out-of-distribution data; it says its routers match commercial ones "while being over 40% cheaper" ([P](https://www.lmsys.org/blog/2024-07-01-routellm/)).

**What the buyer's bill looks like.** Claude Code's docs: "the average cost is around $13 per developer per active day and $150-250 per developer per month, with costs remaining below $30 per active day for 90% of users"; and "Usage inside the seat allowance isn't metered in dollars" on Team/Enterprise seats ([P](https://code.claude.com/docs/en/costs)).
Arithmetic from those averages (mine, an upper bound that assumes every task is routable): a 6-developer pilot spends about $900-1,500 a month at API rates, so 16%-40% is about $144-600 a month; a 20% share of that is $29-120 a month. A 1,000-developer deployment is $150,000-250,000 a month, so 16%-40% is up to $24,000-100,000 a month.
Implication: the pilot proves a claim, it does not make revenue; the money is at hundreds of metered developers.

### 2.4 Companies that sell VERIFICATION or QUALITY

- Strongest signals are **attached to a workflow chokepoint**, not a standalone score: code review (CodeRabbit's $60M round, Cursor buying Graphite because review became "the bottleneck"), evals as a shared decision tool (Braintrust: "it gives you a clear set of tools ... to get people to agree"), and Cursor reporting "keep rate" (how much agent code stays) and a satisfaction proxy for its router (1.7, 1.17, 1.18, 2.3).
- A quality claim sold on a benchmark is weaker than one sold on the buyer's own checks: Intercom charges per outcome verified by customer behaviour (1.18); CodeRabbit and Graphite verify by review, not by execution. None of the dossiers sells test-gated acceptance ("accept cheap-model output only if the project's own tests pass") as the product.
  That is either a gap or a sign that the buyer will not pay separately for it; the evidence cannot distinguish.
- Reception of the quality worry: the Google-affiliated DORA 2025 report is summarised as "AI does not automatically improve software delivery performance" and as an amplifier of existing conditions ([S](https://infoq.com/news/2026/03/ai-dora-report/), via search; I did not retrieve the report).

### 2.5 IT-services companies as first customers of dev tools

- **No case found** of a services company being the *first* customer of a small dev tool. What I found is services firms as partners, deployers and investors of large vendors:
  - Infosys: a 6-K to the SEC (filed with its Apr 2026 results) lists collaborations with Cursor (Cursor's CEO is quoted: "as they enable over 100,000 software engineers at Infosys with agentic coding platforms"), Cognition and others ([P](https://www.sec.gov/Archives/edgar/data/0001067491/000106749126000018/exv99w02.htm)); Infosys FY26 revenue $20,158M, IFRS operating margin 20.3% ([P](https://www.sec.gov/Archives/edgar/data/0001067491/000106749126000018/exv99w01.htm)).
  - Wipro and Factory (Jan 28, 2026): rollout "across tens of thousands of engineers", Wipro Ventures took part in Factory's funding round, no financial terms, no outcome metrics ([P](https://www.wipro.com/newsroom/press-releases/2026/wipro-and-factory-partner-to-accelerate-agent-native-software-development-for-enterprises-globally/)).
  - Cognizant and Anthropic (Nov 4, 2025): Claude to "up to 350,000 employees", including Claude Code; no terms, no quantified productivity ([P](https://www.anthropic.com/news/cognizant-partnership)).
- **Their economics are being squeezed.** Reuters (via The Star, Aug 21, 2026): the TCS CEO says about 80% of contracts in its finance, HR and other business services segment are tied to outcome measures; clients want "the same work for 25% to 30% less" (Persistent's CEO); Cognizant's reported Daimler Truck deal splits AI-related savings; HCLTech's E.ON deal pays nothing in year one ([S](https://www.thestar.com.my/tech/tech-news/2026/08/21/ai-reshapes-india039s-it-services-sector-contracts-as-clients-demand-more-for-less)).
- **My inference (not a sourced fact):** a services buyer cares about token cost in proportion to how much of it lands on its own margin; under outcome or fixed-price work the firm keeps the saving, under time-and-materials it may be passed through. Ask the lead which applies before pricing anything.
- Another cost-saving vendor, ProsperOps, lists a global systems integrator as a customer on its pricing page, with the smallest lifetime savings of its list (1.18). That is a hint that integrators buy savings, not evidence about first customers.

### 2.6 How productivity and cost claims were published and received

- **Randomised trials with disclosed design are what moved opinion.** GitHub Copilot study: "completed the task 55.8% faster" on an HTTP-server task, preprint Feb 2023 ([P](https://arxiv.org/abs/2302.06590)). Field experiments with 4,867 developers at Microsoft, Accenture and a Fortune 100 firm: a 26.08% increase in completed tasks, with a standard error of 10.3%, published in Management Science
  ([S](https://pubsonline.informs.org/doi/fpi/10.1287/mnsc.2025.00535), via search). METR (Jul 10, 2025): 16 experienced developers, 246 tasks on their own repositories, tasks took "19% longer" with AI, developers expected 24% faster and still believed 20% faster afterwards; the authors list their limits
  ([P](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/)). METR's Feb 24, 2026 update says its follow-up (57 developers, 800+ tasks) is unreliable because developers refused to work without AI, pay fell from $150 to $50 an hour, and time tracking fails with parallel agents; it is "working on changes to the design" ([P](https://metr.org/blog/2026-02-24-uplift-update/)).
  Lesson: credibility came from design and self-criticism, and measurement is getting harder, not easier.
- **Vendor numbers are received as marketing unless the design is shown.** Cursor Router reports "millions of requests" with no arm sizes, CI or exact baseline (2.3); Cognition's review lists merge rates and customer anecdotes with no method (1.16); Not Diamond's homepage headline (50%+) and calculator (25%) disagree (1.10); Kite's founder says an 18% speed-up "did not resonate" with the people who pay (1.18).
- **Public benchmarks expire.** OpenAI's post (dated Feb 23, 2026, per a search summary; page returned 403) says it no longer reports SWE-bench Verified, citing flawed tests (59.4% of 138 audited hard tasks) and contamination ([S](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)); its recommended replacement, SWE-bench Pro, was built with private commercial repositories to resist contamination
  ([P](https://arxiv.org/abs/2509.16941)) and itself has audit disputes in later reports (single sources, unverified). A buyer-run replay on the buyer's own tests is harder to dismiss than a leaderboard.
- **What buyers are told to measure.** Cursor's guide: "Track cost per unit of real work rather than cost per request", use keep rate and satisfaction, "bring [cost per commit] to budget discussions", ask whether savings are cache-aware, and "Turn routing on for one busy team, watch cost per commit for a week, and expand" ([P\*](https://cursor.com/guides/model-routing)).

### 2.7 Audit, free-analysis and replay offers

- **Free analysis, fee on realised savings** (cloud cost): ProsperOps, 1.18. Closest precedent for savings-share; the fee base is "realized savings, as determined by your provider's billing system", i.e. an external meter both sides trust. Usage.ai's own guidance is to ask which savings dollars the fee applies to ([S](https://www.usage.ai/blogs/finops/savings-based-cloud-cost-optimization-pricing/)).
- **One team, one week, one metric** (Cursor's guide, above).
- **Free trials without a card** are standard in the dossiers (Langfuse Cloud, Helicone 7-day, LiteLLM 30-day enterprise trial, Braintrust startups programme).
- **Replay on your own data**: Helicone's experiments let teams "test prompt variants against historical conversations before deploying" ([P](https://www.helicone.ai/blog/introducing-helicone-v2)); Braintrust's pitch is the same loop. I found **no source with a measured conversion rate** from a free audit, trial or replay to a contract.
- **Forward-deployed bootcamps** (Palantir's AIP bootcamps) are covered in `docs/research/embedded-workflow-refinement-prior-art.md` section 1; I did not re-verify them.

### 2.8 Failures, pivots and counter-evidence in one place

- Kite: 500k monthly users, no revenue (1.18). Humanloop: shut down after a team-only hire (1.18). Helicone: maintenance mode (1.3). Sourcegraph: consumer plans ended with four weeks' notice (1.15). Cursor: a pricing change that needed an apology and refunds (1.17). Unify and Martian: router first, something else later (1.9, 1.11). Windsurf: sold in pieces (1.16, S). Composio: four positionings in 18 months (1.1).
- Counter-evidence to "pivots are failure": Langfuse pivoted in the first three months after YC and became the category's most-starred open-source tool (1.6); E2B pivoted from an agent to its sandbox (1.4). Pivots that went toward the reusable infrastructure underneath worked; pivots away from a saving-centred router are where the router companies went.
- Counter-evidence to "open source wins": see 2.2. Counter-evidence to "enterprise first customer must be a big name": Braintrust's first user was a single team at one company that pushed back (1.7).
- **Security is a recurring failure channel for anything in the credential or traffic path**: LiteLLM's PyPI compromise (S), Braintrust's reported breach (S, headline only), Cyera's Composio findings (S). Each is reported by someone other than the company and I could not confirm remediation.

### 2.9 Was a shared, cross-customer network part of anyone's early story?

- Early stories were single-tenant: observability and evaluation tools (Helicone, Langfuse, Braintrust, Portkey), compute (Modal, E2B), a backend (Supabase), a model marketplace (OpenRouter). None of the early pages I read promised that one customer's improvement benefits another.
- Closest analogue: Composio's Series A post (Jul 22, 2025): "one agent's improvements benefit all agents", "a shared learning layer" ([P\*](https://composio.dev/blog/series-a)), written after the SDK, free tier and partner benchmark. Unify's 2026 page ("Models that keep learning ... you own the flywheel") is per-customer learning, not a network (1.11).
- Platform vendors are building the sharing layer themselves: Anthropic's Agent Skills ("folders that include instructions, scripts, and resources", Oct 16, 2025; organisation-wide management added Dec 18, 2025; "working toward" enterprise-wide distribution) ([P](https://claude.com/blog/skills)); Windsurf documents auto-generated per-workspace memories ([S](https://docs.windsurf.com/windsurf/memories), via search). The Cursor "Memories" feature was reportedly removed in late 2025 (sources conflict, [S](https://forum.cursor.com/t/saved-memories-in-cursor-and-their-viability/128968)).
- Data-sharing across customers collides with the evidence on data-path objections (2.2): Cursor's zero-data-retention policy does not apply to bring-your-own-key use ([P](https://cursor.com/docs/settings/api-keys)); regulated buyers pulled Helicone to self-hosting.

## 3. The proxy idea versus a hook/MCP approach

What the primary sources say:

- **A gateway is the most common integration in the dossiers**: Helicone and Portkey (one-line base URL change), LiteLLM, OpenRouter, Martian ("Switching takes just a few lines"), and Not Diamond Code as a *local* proxy ([P] links in 1.2, 1.3, 1.9, 1.10, 1.18).
- **Claude Code supports gateways but not this use.** The docs list what a gateway is for (credentials, usage tracking, cost controls, audit logging, provider switching) and say "Anthropic doesn't endorse, maintain, or audit third-party gateway products, and doesn't support routing Claude Code to non-Claude models through any gateway" ([P](https://code.claude.com/docs/en/llm-gateway)). A gateway credential bills per token to whoever owns the credential and
  bypasses subscription limits (same page). So a routing proxy for Claude Code is unsupported by the vendor and, on seats, saves no metered dollars.
- **Cursor**: its API-key page covers bring-your-own-key for chat models only and says nothing about a custom base URL or Agent ([P](https://cursor.com/docs/settings/api-keys)); I could not confirm that an OpenAI-compatible proxy can sit under Cursor's agent.
- **What a hook sees**: Claude Code hooks receive `cwd`, `transcript_path`, `tool_name` and `tool_input`, can run any command, can allow, deny or rewrite tool calls, and can be distributed as managed organisation policy that users cannot disable ([P](https://code.claude.com/docs/en/hooks)). Cursor hooks receive `workspace_roots`, shell commands and MCP tool inputs, can block or modify some events, and can be distributed by MDM or the team dashboard on Enterprise ([P](https://cursor.com/docs/agent/hooks)).
  A request-level proxy sees model API traffic; to run the project's tests it needs the working directory, which a hook has and a remote gateway does not (inference from the two docs).
- **Credential path liability**: gateways hold or forward provider keys; incidents in 2.8 and the Helicone and Langfuse security work (1.3, 1.6) show buyers' security teams treat them as high risk. A hook that runs locally and sends only task text avoids holding the customer's provider key, but the repo's data-path question still has to be answered (the pilot plan lists it as a gap).
- **Thin evidence**: I found no source comparing hook-based and proxy-based distribution for coding agents, no conversion data for either, and no public data on how many developers use gateways with Claude Code. Not Diamond Code's choice of a local proxy is one competitor's bet, not evidence it works.

## 4. What the lead and the pilot look like against this evidence

(Internal documents, not external evidence.) `docs/pilot_plan_lead_2026-10.md` says the lead's headcount is about 2,000 (not developers), that they have Cursor seats "so a flat seat, probably", used mostly for presentations and question answering, all second-hand and unverified; that the dollar saving "can be zero even when routing works" inside included usage;
that four of the router's misses were wrong cheap answers that passed visible checks; and that the hold time is 45-57 s per dispatched prompt. `docs/HANDOFF_abtest_2026-10-11.md` section 11 has the 153-task table (A 140 solved at $18.05; C 137 at $10.77, -40%) and the 56-hard-task live check (A 49, new C 50, $8.06 vs $6.75, -16%).
`docs/plan_2026-10_tech_transfer_targeted_repair.md` says transfer of procedures across organisations is "not yet shown". The tasks are function-level benchmark suites, not multi-file repository work; say so whenever the number is quoted.

## 5. Synthesis for a two-person, pre-incorporation team with this evidence level

### (a) The three best-supported go-to-market moves

1. **Make adoption free, local and one command, and launch it where developers look; measure retained use, not installs.** Support: Supabase, Langfuse, Helicone, E2B, Composio, Mem0 (1.13, 1.6, 1.3, 1.4, 1.1, 1.18). Qualification: Kite had 500k monthly users and no revenue; a launch can return 8 points or 1,120 (2.1). Keep the model-cost burden on the user (bring your own key
   is the stated default of the company's plan) the way Cursor's first pricing page offered "at-cost" use of the user's own key (1.17).
2. **Sell to the person who owns the AI budget with a short, pre-registered audit and pilot on their own spend and their own tests, and turn the first result into a second customer.** Support: ProsperOps' free analysis plus fee on realised savings (1.18), Cursor's "one team, one week, cost per commit" (2.6), Braintrust's first user pushing back and iterating until it worked, then neighbours (1.7), Kite's finding that managers
   pay for discrete capabilities (1.18). Evidence on conversion rates is absent (2.7).
3. **Choose the first buyers by how they are billed: metered API, Bedrock, Vertex or Foundry coding teams, not flat seats.** Support: Claude Code's docs on seat allowances not being metered in dollars and on the average spend (2.3), the arithmetic that only hundreds of metered developers make a saving worth a contract (2.3), Cursor's own move to usage pricing (1.17).
   This is the weakest-supported of the three because it rests on one vendor's docs and on the pilot plan's second-hand note about the lead.

### (b) What to avoid, each with an example

- Building for individual developers and expecting payment: Kite (1.18).
- A headline saving without the design: Cursor's undisclosed arm sizes (2.3), Not Diamond's 50% headline vs 25% calculator (1.10), Martian's "20%-97%" from 2024 now absent from its homepage (1.9).
- A standalone router positioned against the platforms' own routers: Cursor Router, Copilot Auto, Gemini CLI auto (2.3); Unify and Martian moved off the router (1.9, 1.11).
- Sitting in the customer's credential path before there is a security story: LiteLLM, Braintrust, Composio incidents (2.8, all secondary).
- Depending on a behaviour the platform says it does not support: Anthropic's gateway statement (3).
- Surprise pricing changes with short notice: Cursor's June 2025 apology and refunds (1.17); Sourcegraph's four-week sunset (1.15).
- Leading with the network or a "shared flywheel" before a single-customer result exists: nobody's early story did (2.9); Composio waited until Series A (1.1).
- Relying on a public leaderboard as the proof: SWE-bench Verified was retired by OpenAI's own evaluations (2.6).

### (c) Proxy versus hook/MCP (summary of section 3)

Hook/MCP is better matched to *your* mechanism, since the verification step needs the repo and the test command and both vendors' docs show hooks receive the working directory and can run commands. A proxy is the better-trodden distribution path and the one the best-funded competitor (Not Diamond Code) chose, but it is unsupported by Anthropic for non-Claude models, sees only API traffic, and puts you in the credential path.
Evidence is thin on conversion for either approach. A reasonable reading: hooks for verification and dispatch, an optional local proxy only if a buyer asks for spend attribution (an adjacency that LiteLLM, Portkey and Claude's own gateway already cover).

### (d) Turning the one lead and the 4-week pilot into a reference others will believe

Evidence-based rules:

1. **Pre-register** the primary metric, the arm sizes, the stop rules and the readout meeting before data (the pilot plan already fixes rules in week 0). METR's credibility came from a disclosed design and from publishing its own invalidation (2.6).
2. **Use the buyer's metric: cost per accepted change** (Cursor's own guide), on dispatched tasks only, with the share of tasks that were dispatchable shown next to it. Do not extrapolate to total spend: the pilot plan's own variance estimate says a 6-person, 4-week pilot sees about 25% or more, not 10%.
3. **Publish raw counts and intervals, the failures and the latency**: solved vs. attempted, silent wrong accepts, hold time, opt-outs, how many users kept dispatch on. Cursor's vendor numbers were marked down for omitting these (2.3).
4. **Randomise at the user-and-task level with an off switch, and log opt-outs**; METR found developers refusing to work without AI, which biases results (2.6).
5. **Executable artefact**: a replay script and the verifier configuration that a second team can run on its own repo; this fits the planned reproducible format and sidesteps benchmark expiry.
6. **Permission and anonymity**: services firms publicly announce partnerships only with large vendors (2.5). Ask for permission to describe "a services company of about 2,000 people" plus a quote from the budget owner, in exchange for the pilot terms, and keep the name out until written consent.
7. **Get the second reference fast** (Braintrust: Zapier, then Coda, then Airtable within one seed post; 1.7), because a single reference is an anecdote.

### (e) Claims you may and may not make from the current numbers

| May say (with the denominators) | Must not say |
|---|---|
| On 153 paired benchmark tasks, the routed arm solved 137 vs 140 for Opus-alone at about 40% lower measured API cost ($10.77 vs $18.05; one run; the tasks are function-level benchmark suites). | "Same quality" or "no quality loss" in general; 3 fewer solves is not a test of non-inferiority. |
| On a 56-task hard subset (live check) it solved 50 vs 49 at about 16% lower cost. | "40% savings" for any real team; the larger number is on easier suites. |
| Per dispatched prompt the cheap path holds the user for about 45-57 s; the median run is slower than the baseline. | Anything about latency being acceptable or fixed until re-measured. |
| Accepting a cheap answer only when the project's own test passes reduces the chance of a wrong accept; four known silent wrong answers passed the visible check. | "Verified correct"; the check is only as good as the project's tests. |
| A saving of X% applies to *metered API spend on dispatchable tasks*. | Any dollar saving on flat seats; "60-80% savings" (no source in this note supports it, and comparable vendors' headlines are 20%+ to 60% with undisclosed or vendor-favourable baselines, 2.3). |
| The mechanism is a per-repository library of verified fixes. | That fixes transfer across customers (the repo's own plan says "not yet shown"), or any network effect. |
| Nothing about the lead until written consent. | Any customer name, logo or "enterprise customer" language. |

### (f) Smallest experiments, with numbers

These thresholds are design choices for your situation, not findings; where a reference class exists I cite it. Run in order; each takes one to three weeks.

| # | Experiment | N | Success | Falsification | Reference/why |
|---|---|---|---|---|---|
| 1 | Buyer-billing survey: 15-minute calls with engineering leads on how coding agents are billed and who owns the budget | 12 calls | at least 6 of 12 teams on metered API/cloud billing with a named budget owner | 3 or fewer of 12 metered, or no named owner in 8 or more | 2.3: seat plans have no dollar meter |
| 2 | Spend-audit offer (no install; we analyse their exported usage; deliver in 48 hours) by personalised messages to heads of engineering or platform | 40 messages to teams of at least 10 developers | at least 6 replies (15%), 3 exports shared, 1 team with addressable savings of at least 15% | 2 or fewer replies, or 0 exports with a ceiling of 15% or more | ProsperOps free analysis (1.18); the pilot plan's go gate of 15%. Cold outbound has no base rate in the sources, so 40 is deliberately small |
| 3 | Public release of the local hook plus library under a permissive licence, one Show HN, one post with raw numbers and failures | one launch | at least 40 HN points (above Portkey's 37), at least 150 installs in 14 days, and at least 25 repos with the hook still active on day 7 (opt-in count) | fewer than 10 points (Braintrust got 8) and fewer than 30 installs, or fewer than 10% of installers active on day 7 | HN spread 8 to 1,120 (2.1); Kite shows installs are not revenue, so the retained-repo number is the one that counts |
| 4 | Design-partner replay on the partner's last tasks with their test commands, dispatch off vs on | 3 teams, at least 40 tasks each | in 2 of 3 teams: pass rate within 5 points of baseline and cost per accepted task at least 25% lower on dispatched tasks, silent wrong accepts at most 2% | in 2 of 3 teams: saving under 10%, or silent wrong accepts above 5% | The pilot plan's 15% floor; Not Diamond's own checklist asks for "at least 10-20%" (1.10) |
| 5 | Paid-intent test after experiment 4: a one-page pilot with a savings share, cap and floor, signed by the budget owner | the 3 teams | 2 of 3 sign, or give a dated PO commitment, within 30 days | 0 of 3 within 30 days | Kite: individuals do not pay, managers pay for discrete capability (1.18) |
| 6 | In-pilot retention (the lead): users keep dispatch on during the last two weeks | 5-10 users | at least 4 of 6 (the pilot plan's rule) and median hold under 15 s or dispatch made async | 2 or fewer of 6, or more than half switch it off within 3 days | Retention as revealed preference (pilot plan) |
| 7 | Reference conversion: after the readout, ask for a written case study with named permissions or an anonymised one, and for one introduction | the lead | one signed anonymised case study and one introduction within 30 days of the readout | no written permission within 45 days | 2.5, 5(d): services firms announce only with large vendors |

### (g) When to talk about the long-run network idea

Nothing in the dossiers put a cross-customer network in the first story, and the one close analogue (Composio) put it in a Series A post after a tools business existed (2.9). Platform vendors are already shipping shared procedures inside one organisation (Anthropic Skills), which reduces what a small company can claim as novel there.
Implication: lead with a single-customer, measured result (cost per accepted change with a test gate), keep the network as a research direction and a roadmap sentence, and raise it only when (i) one customer's library measurably helps a second customer's repository (not shown yet) and (ii) the data-sharing terms have been agreed with a buyer's security team.
For investors or research audiences the network can be the vision; for buyers in the first 12 months it is a risk to the data-path answer.

## 6. What I could not verify

- **Founder accounts of first customers and time to first revenue** for Composio, Portkey, Helicone, E2B, OpenRouter, Martian, Not Diamond, Unify: not found in primary sources. Podcast and interview searches returned nothing usable for Portkey and Composio.
- **Funding**: Composio's lead and any seed; Helicone's amounts; Portkey's figures beyond its own posts; LangChain's seed and Series A amounts as they appear in secondary press; Martian's $9M seed; Not Diamond and Unify rounds (Unify's own banner says $8M, [P wb:2025-05-26] in 1.11); Braintrust's valuation (Axios, paywalled); E2B's total ($32M subtitle vs other trackers).
- **OpenRouter, Martian, Not Diamond, Portkey revenue, take rate, margin**: not disclosed. The Stripe price ($7B+) is one report by Bloomberg relayed by TechCrunch; Stripe declined to comment.
- **Pages that returned 403 or could not be read**: OpenAI (GPT-5 router, SWE-bench Verified retirement), TechCrunch (Unify, Braintrust breach), Infosys.com (I used the SEC 6-K instead), the Anthropic-Accenture release on BusinessWire. The GPT-5 router and the SWE-bench retirement rest on secondary summaries.
- **Helicone's current status**: Mintlify says maintenance mode (Mar 3, 2026); Helicone's own site on 2026-10-21 says it is "no longer part of Mintlify". I could not reconcile these.
- **Cursor Memories removal**, **LiteLLM and Braintrust security incidents**, **Windsurf-Google-OpenAI deal terms**, **Humanloop's shutdown details**, **CodeRabbit's ARR**: secondary only.
- **DORA 2025** figures: only secondary summaries; the report was not retrieved.
- **GitHub star history** (needs authentication): only today's counts.
- **Whether Cursor's agent works through an OpenAI-compatible proxy**: docs do not say.
- **How the lead is billed and what they use the tools for**: second-hand in the pilot plan. This is the biggest gap for the synthesis.
- **Claims from memory**: none that I relied on. Wayback was the source for early pricing; its CDX listing and my corrected snapshot dates are recorded in the tags.

## 7. Where the earlier chat advice was right or wrong

(The advice as relayed: ship an OpenAI-compatible proxy; claim 60-80% savings; target Series A-B startups.)

- **"Ship an OpenAI-compatible proxy" — partly right, partly wrong for you.**
  Right: a base-URL change is the dominant low-friction integration (1.2, 1.3), and the nearest competitor ships a local proxy (1.10).
  Wrong or unproven for your mechanism: your verification step needs the repository and its tests, which a gateway does not see (3); Anthropic says it does not support routing Claude Code to non-Claude models through gateways (3); in 2026 gateways were being given away, bought or put in maintenance mode (1.2, 1.3, 1.6); and holding traffic and keys invites the incidents in 2.8. The advice also assumes metered agent products rather than coding-team seats.
- **"Claim 60-80% savings" — not supported.** Your measured numbers are about 40% on easier benchmark suites and 16% on a hard live subset, with worse latency (section 4). Comparable published claims: 20%+ (Not Diamond Code, with 39% on one benchmark), 30-50% on three accounts and "60%" in an A/B with an undisclosed baseline (Cursor), 20-97% (Martian 2024, since dropped from its homepage) (1.9, 1.10, 2.3).
  A claim above what you can reproduce also turns the sale into an argument about the number (2.6). Say what you measured, against which baseline, on which task type.
- **"Target Series A-B startups" — right about speed, wrong about fit and size.**
  Right: the early customers named by Braintrust, Composio, E2B and Langfuse were fast-moving AI-native companies (1.7, 1.1, 1.4, 1.6), and startup programmes are a common wedge.
  Wrong or unproven: a 10-person coding team spends roughly $1,500-2,500 a month at the documented averages, so savings are small in dollars (2.3); many startups are on seats, where there is no dollar meter; the products those companies sell are not your evidence (you measured coding tasks, not metered agent products); and Kite's lesson is that small buyers' managers pay for capability, not a percentage. Start with metered coding teams of any size that have a named budget owner, and test the segment in experiment 1.
- **What the advice missed.** The platform vendors' own routers and the commoditisation of gateways (2.3), the data-path and security questions (2.2, 2.8), and the need for a pre-registered, intervals-reported design to be believed (2.6).
