# Research Tools & Models — Free vs Paid Catalogue

**Data collected:** 13–14 November 2026. Every price below was read from the vendor page named in the source column at that time. Prices marked ⚠️ were taken from a secondary/summary page and must be re-checked.

**How to read "per 1k" pricing:** most providers sell either *credits* (loosely 1 credit ≈ 1 request) or *dollar credits* (prepaid balance). Where a vendor mixes the two, the second column says what one request actually costs.

---

## 1. Free tier

| Tool | Vendor | What it does | Exact free allowance | Gotchas | Source |
|---|---|---|---|---|---|
| Exa | Exa (exa.ai) | Neural/semantic web search, page fetch, answer, monitors, research agent | **$10 credits on signup, resets to $10 on the 1st of each month** ≈ 2,500 Lite (or ~1,400 Auto) searches. One-off extra **$10 onboarding bonus**. No card | Free balance is dollar-credits, not requests — Deep/Agent calls burn it fast ($12/1k → ~830 deep searches/mo) | https://exa.ai/docs/admin/pricing |
| Exa hosted MCP | Exa | Same search/fetch as an MCP server | **Keyless access at a free IP rate limit**; no sign-in, no key | `agent_run` (deep research) is *not* available keyless; keyless needs direct IPv4 (403 behind proxies); 429 = free limit hit | https://exa.ai/docs/get-started/exa-mcp |
| Tavily | Tavily | Agent-optimised search, extract, crawl, map, research | **1,000 API credits/month**, no card | March-1st reset; advanced search/extract cost >1 credit each; requests fail (not throttle) when credits run out | https://tavily.com/pricing |
| Brave Search API | Brave | Independent index web/news/image/video search + LLM Context + Answers | **$5 free credit every month per plan** ($20 total across Search / Answers / Spellcheck / Autocomplete) | Must show Brave attribution on your site/about page to keep the credit | https://brave.com/search/api/ , https://brave.com/blog/most-powerful-search-api-for-ai/ |
| Serper | Serper | Google SERP scraping API (search, scholar, maps, news, images…) | **2,500 one-time free queries**, no card | Free quota does not reset monthly; paid credits **expire after 6 months** | https://serpprices.com/provider/serper (⚠️ secondary, quotes serper.dev/#pricing) |
| SerpApi | SerpApi | Google + 40 other engines SERP API | **250 searches/month**, 50/hour | Doesn't accumulate; heavy legal-scrutiny/reliability caveats vs native Google access | https://serpapi.com/pricing |
| Parallel | Parallel | Search / Extract / Task / Responses APIs + MCP | **Launch tier: free, no billing** — but *Search MCP only* | Anything beyond MCP needs the Build tier (card) | https://parallel.ai/pricing |
| Firecrawl | Firecrawl | Scrape, crawl, search, browser agent | **1,000 credits/month** (≈500 searches *or* 1,000 pages), 2 concurrent, 10 req/min | Searches and page scrapes draw from the **same** credit pool | https://www.firecrawl.dev/pricing |
| Linkup | Linkup | Web search / fetch / research API | **4,000 free queries** (not monthly — verify on dashboard) | Free pool is one-time-ish; Research calls are $0.25–$2.50/request so they don't fit the free pool well | https://www.linkup.so/pricing |
| Perplexity Search / Agent API | Perplexity | Search API + agentic web tools | **No standing free API credit** — a $5 credit has been offered at signup (⚠️ varies) | Paid-only for practical use; `sonar-reasoning` deprecated 2025-12-15 | https://docs.perplexity.ai/getting-started/pricing |
| You.com Search API | You.com | Web/news search, contents, answers, research modes | **100 searches/day free via MCP**, no key, no card | MCP only; REST API is paid per call | https://you.com/pricing |
| Semantic Scholar | Allen Institute | 200M+ paper search, citations, recommendations | **Fully free**, most endpoints usable without a key | Unauthenticated pool is **1,000 RPS shared by everyone** (contended); key = 1 RPS guaranteed, more on review | https://www.semanticscholar.org/product/api |
| OpenAlex | OurResearch | 250M+ works, authors, institutions, topics; open metadata | **$1 of API usage per day per account**, no card; data itself open | New accounts throttle to $0.10 per 5 min on day 1; 10 req/s and 100k calls/day limits without key | https://docs.openalex.org/access/pricing |
| Crossref | Crossref | DOI metadata, references, funders, licences | **Free, no sign-up**; "polite pool" via `mailto=` | No abstracts for every record; no full text | https://www.crossref.org/documentation/retrieve-metadata/rest-api/ |
| arXiv API | Cornell | Preprint metadata + full-text links (Atom/OAI-PMH) | **Free** | Metadata CC0, PDFs are **not** — redistribution needs author permission; must respect rate limits + acknowledgement text | https://info.arxiv.org/help/api/index.html , https://info.arxiv.org/help/api/tou.html |
| PubMed E-utilities | NCBI/NLM | Entrez search/fetch over PubMed + 37 other DBs | **Free, no key required** | **≤3 requests/second**; large jobs only weekends or 21:00–05:00 ET; abuse = IP block. API key raises limits | https://www.ncbi.nlm.nih.gov/books/NBK25497/ |
| Europe PMC REST | EMBL-EBI | 33M+ publications, 10M full texts, citations, annotations | **Free** (email/registration for higher quota) | Royalty-free use, but no SLA on the public endpoint | https://europepmc.org/RestfulWebService |
| CORE API | CORE (Open University) | 300M+ OA papers, metadata **and full text** | **Free without registration**, subject to rate limits | **1 batch or 5 single requests per 10 s**; registered = faster but often paid; 30-day trial for Institution/Enterprise | https://core.ac.uk/services/api |
| BASE | Bielefeld University Library | 400M+ documents from 12k repositories/journals | **Free**, key by application form (few days) | Needs an API key request describing use case; site is behind a bot challenge for automated access | https://api.base-search.net/ , https://oai.base-search.net/ |
| OpenCitations | University of Bologna | Open citation graph (COCI, OpenCitations Index) | **Free, CC0**, token optional for heavier use | Some legacy endpoints deprecated in favour of Index V2 | https://opencitations.net/ , https://opencitations.net/api/v1/ |
| Dimensions (free app) | Digital Science | Publication/grant/patent/clinical-trial search | **Free web app** for non-commercial use | API is subscription-only | https://www.dimensions.ai/products/ |
| Lens.org | Cambia | Scholarly + patent search workspace | **Anonymous public use, free**; Academic Workspace fee-waived for degree-granting institutions | From 2026, non-academic Professional Workspaces must subscribe (list **US$1,000/yr**) | https://about.lens.org/changes-to-accounts/ |
| Consensus | Consensus | Evidence-based answer engine over papers | **Unlimited paper searches**, 10 Pro messages/mo, 3 Deep reviews/mo, 10 Study Snapshots, **30 API/MCP calls/mo** | The 30 API calls/mo is the only free programmatic access | https://consensus.app/pricing |
| Elicit | Elicit | Literature review agent, extraction tables | **Basic free**: unlimited search across 138M papers, unlimited summaries/chat, **limited** Research Agent & Reports | Limits are per-feature usage caps, not a published number → check dashboard | https://elicit.com/pricing |
| Scite | Scite | Smart Citations (supporting/contrasting), tallies | **Free $0 tier**: public Papers & Tallies endpoints, **no key needed** | No REST key on free; redacted snippets and restricted use even on paid API | https://scite.ai/pricing.md , https://docs.scite.ai/pricing-and-plans |
| SciSpace | SciSpace | Paper Q&A, literature review, paraphraser, citations | **Basic free: 100 agent credits/month** | Agent credits ≠ searches; heavy features are metered per use | https://scispace.com/pricing |
| ResearchRabbit | ResearchRabbit | Citation-graph discovery, alerts, collaboration | **Free Forever** plan | Free caps seed articles and projects (exact number not published) | https://www.researchrabbit.ai/pricing |
| Connected Papers | Connected Papers | Similarity graph per seed paper | **5 graphs/month** | No monthly billing option; yearly/quarterly only on paid | https://www.connectedpapers.com/pricing |
| Litmaps | Litmaps | Citation maps, monitors, discovery | **Free**: basic search ≤20 inputs, 2 Litmaps, 100 articles/map | Free tier has no alerts on custom schedule | https://www.litmaps.com/pricing |
| NotebookLM (Gemini Notebook) | Google | Grounded "chat with your sources", audio/video overviews | **Free with any Gmail account**: 100 notebooks/user, 50 sources/notebook, 500 notes | Compute-based limits; model access is the cheapest tier | https://support.google.com/gemininotebook/answer/16213268 |
| Google Scholar | Google | Broad scholarly search + citation counts | **Free for humans** | **No official API.** Scraping is against the ToS; robots.txt blocks cited-by; bots get CAPTCHAs | (ToS) https://policies.google.com/terms |
| Perplexity (consumer) | Perplexity | Search + Research agent | **3 Pro Searches/day + 1 Research query/month** | Research allowance is extremely tight | https://www.perplexity.ai/hub/pricing |

---

## 2. Paid tier

### 2a. Web search / scraping APIs (price per 1,000 requests unless stated)

| Tool | Vendor | Cheap entry point | Metering / price per 1k | What it does | Source |
|---|---|---|---|---|---|
| Exa | Exa | PAYG, no minimum | Lite **$4**, Auto/Fast **$7**; **Deep $12–15**; Contents **$1/1k pages**; Answer **$5**; Monitors **$2**; Agent **$0.012–$1.00/run** (minimal→xhigh; auto ≤$5, ultra ≤$20) | Semantic (embeddings) web search + full-page content | https://exa.ai/docs/admin/pricing |
| Exa Enterprise | Exa | Sales | **$12,000/yr commitment** for ZDR | Custom indexes, SLA, zero retention | https://exa.ai/pricing |
| Tavily | Tavily | **$0.008/credit** PAYG | Project **$30/mo**; startups 100k free credits/mo; students free | Agent search/extract/crawl/research | https://tavily.com/pricing |
| Brave Search | Brave | Prepaid credits, **$5/1k** | Search **$5/1k** (50 qps); Answers **$4/1k + $5/1M tokens** (2 qps); Enterprise = custom | Own index, no Google/Bing fallback | https://brave.com/search/api/ |
| Serper | Serper | **$50 one-time** = 50k credits | **$1.00/1k** → **$0.75** (500k) → **$0.50** (2.5M) → **$0.30/1k** (12.5M); 6-month expiry | Google SERP scrape (search, scholar, maps…) | ⚠️ https://serpprices.com/provider/serper |
| SerpApi | SerpApi | **$25/mo** Starter (1k) | $25/1k → $75/5k ($15) → $150/15k ($10) → $275/30k → $725/100k ($7.25) → $1,475/250k → $2,750/500k ($5.50); cloud $3,750–$106,050/mo | 40+ engines incl. Google, Scholar, Scholar Cite | https://serpapi.com/pricing |
| Parallel | Parallel | **Build** PAYG, $5 free monthly with card (+up to $80 signup) | Search **from $1/1k** (10 results) + $1/1k extra results; Extract **$1/1k URLs**; Task **from $5/1k runs**; Responses **from $10/1k**; Monitor **$3/1k checks**; Scale **$1,000/mo** (= $12,000 credits, or $10,000/yr) | Search/Extract/Task/Responses + Monitor | https://parallel.ai/pricing |
| Firecrawl | Firecrawl | **$19/mo** Hobby (annual $16) | Hobby $19; Standard **$99/mo** (100k credits, annual $83); Growth $399; Scale $749. Prepaid credits: $5/1k → $5/5k by tier | Scrape/crawl/search + structured extraction | https://www.firecrawl.dev/pricing |
| Linkup | Linkup | PAYG | Fetch **$0.001–0.006/req**; Search **$0.005–0.006/req**; Research **$0.25–$2.50/req**; $5,000 startup credits | Deep-search API + fetch | https://www.linkup.so/pricing |
| Perplexity Search API | Perplexity | PAYG | Search **$5/1k requests** (up to 5 queries per request = 1 unit); Fast Search **$1/1k** | Ranked web results with filters | https://docs.perplexity.ai/getting-started/pricing |
| Perplexity Agent API | Perplexity | PAYG | `web_search` **$2.50/1k**, fast **$1/1k**, `fetch_url` $0.0005/call, people/finance search $5/1k, sandbox **$0.03/session** + $0.0025/sdk search | Tool-calling agent stack | same |
| You.com API | You.com | **$5/1k** Standards | Standards $5/1k; Contents **$1/1k pages**; Answer $5/1k; Research Lite **$12/1k**, Standard **$50**, Deep **$100**, Exhaustive **$450**, Frontier **$1,200/1k**; Finance Research Deep $110 / Exhaustive $500 | Search + multi-source research | https://you.com/pricing |

### 2b. Scholarly / literature tools

| Tool | Vendor | Cheap entry point | Prices | What it does | Source |
|---|---|---|---|---|---|
| OpenAlex | OurResearch | PAYG in **$1 increments** | Member **$5,000/yr** ($20/day budget), Member+ **$10,000/yr** ($100/day), Partner **from $20,000/yr** | Priority budgets, sync, support | https://docs.openalex.org/access/pricing |
| Semantic Scholar | Allen Institute | — | Free; paid = data licensing via AI2 (custom) | Bulk datasets free | https://www.semanticscholar.org/product/api |
| CORE | CORE | — | Free tier; paid = faster rate / VIP quote; Institution & Enterprise with 30-day trial | Full-text OA corpus | https://core.ac.uk/services/api |
| Dimensions | Digital Science | Institutional licence only | Quote only (API 30 req/min) | Publications + grants + patents + trials | https://docs.dimensions.ai/dsl/api.html |
| Lens.org | Cambia | **US$1,000/yr** professional workspace | Institutional / Enterprise Toolkit = custom | Scholarly + patent workspace, APIs in Toolkit | https://about.lens.org/changes-to-accounts/ |
| Elicit | Elicit | **Industry Pro $49/mo** (annual $588) or $75/mo | Scale **$229/mo** annual ($2,748/yr) or $375/mo; academic tiers ⚠️ Plus $11 / Pro $39 / Scale $89 per year (annual) | Research agent, data extraction | https://elicit.com/pricing |
| Consensus | Consensus | **Pro $20/mo** or $144/yr | Deep **$65/mo** or $540/yr; Teams custom | Evidence answers + Deep reviews | https://consensus.app/pricing |
| Scite | Scite | **Basic $20/mo** or $144/yr | Pro **$50/mo** or $480/yr (2,500 MCP credits, API key); Team $50/seat; 7-day trial | Supporting/contrasting citations | https://scite.ai/pricing.md |
| SciSpace | SciSpace | **Premium $20/mo** ($12/mo annual = $144/yr) | Advanced $90/mo ($70 annual); Max $200/mo ($160 annual) | Paper chat, literature review | https://scispace.com/pricing |
| ResearchRabbit | ResearchRabbit | RR+ (price on site) | Institution = custom (LibKey integration) | Graph discovery at scale | https://www.researchrabbit.ai/pricing |
| Connected Papers | Connected Papers | **Academic $6/mo** ⚠️ | Business $20/mo ⚠️ (billed yearly, US list) | Unlimited similarity graphs | ⚠️ https://www.oceanofpapers.com/compare/connected-papers |
| Litmaps | Litmaps | **Pro Education $10/mo** (annual) / $12.50 monthly ⚠️ | Commercial $40/mo annual / $50 monthly ⚠️ | Citation maps + alerts | ⚠️ https://toolmango.com/tools/litmaps/pricing (page itself shows no numbers) |

### 2c. Bulk Google access & "cheapest Google results"

| Route | Price | Reality check |
|---|---|---|
| Serper | **$1/1k** at $50 prepay, down to $0.30/1k | Cheapest headline; unofficial scrape of Google; 6-month credit expiry; no per-endpoint multipliers published |
| SerpApi | **$25/1k** entry, $7.25/1k at 100k | Official-ish, many engines, highest per-call cost |
| Google Custom Search JSON API | Not listed above — **closed to new customers** in 2026 per Brave's account | Do not build on it ⚠️ |
| Bing Search API | **Retired** by Microsoft (2026) | Dead |
| Brave Search | $5/1k | Not Google results, but own index — the only one here that can't be shut off |

---

## 3. Research-capable LLM models / answer engines

| Product | What it actually is | Access & price | Free tier | Source |
|---|---|---|---|---|
| **Perplexity Sonar** | Small search-grounded model, chat-completion API | $1/1M in, $1/1M out **+ request fee $5/1k (low), $8/1k (medium), $12/1k (high)** context | — | https://docs.perplexity.ai/getting-started/pricing |
| **Sonar Pro** | Larger search model | $3/1M in, $15/1M out + $6/$10/$14 per 1k requests (low/med/high) | — | same |
| **Sonar Reasoning Pro** | Reasoning + search | $2/1M in, $8/1M out + $6/$10/$14 per 1k | — | same |
| **Sonar Deep Research** | Multi-step agent-as-model | $2/1M in, $8/1M out + reasoning tokens $3/1M + search $5/1k + citation tokens $2/1M | — | same |
| Perplexity consumer (Max/Pro) | Product: search + Research agent | Pro **$17/mo billed annually** ⚠️ (monthly rate not captured); Max **$167/mo** annual; Education Pro $10/mo | 3 Pro Searches/day, 1 Research/mo | https://www.perplexity.ai/hub/pricing |
| **OpenAI o3-deep-research** | Model (Responses API) | $10/1M in, $40/1M out, $2.50 cached | — | https://platform.openai.com/docs/pricing |
| **OpenAI o4-mini-deep-research** | Cheaper model | $2/1M in, $8/1M out, $0.50 cached | — | same |
| ⚠️ **Both deep-research models were SHUT DOWN on 2026-07-23.** | Replacement path is the flagship chat models (gpt-6-astra, gpt-6.1-sol/terra, gpt-6-luna, gpt-5.6-*) with the built-in web-search tool / "run longer web research" mode | Chat-side research is included in ChatGPT plans; API = token cost + web-search tool charges | ChatGPT free tier has limited research | https://platform.openai.com/docs/deprecations |
| OpenAI consumer Deep Research | **Product** (ChatGPT agent mode) | In ChatGPT Plus/Pro/Business | Limited runs on free | https://chatgpt.com/pricing |
| **Google Gemini Deep Research** | **Agent** in Gemini app / AI Studio, backed by Gemini 3.1 Pro | Token rates $2/1M in, $12/1M out (batched $1/$6) **+ Google Search grounding $14/1k requests** (Gemini 3+); Google's own estimate ~$1–$3 per standard task, $3–$7 per Max task ⚠️ | Grounding 5,000 free requests/month then $14/1k; AI Studio default 1,500 RPD per project | https://aistudio.google.com/docs/pricing ⚠️ (page times out on direct fetch; figures from cached pricing docs) |
| Gemini Deep Research (consumer) | Product | Included in **Google AI Pro $19.99/mo** / Ultra; expanded limits | ✅ in free tier | https://one.google.com/about/google-ai-plans/ |
| **Anthropic Claude + web_search** | **Model + server tool**, not an agent | Tool **$10/1k searches** + normal token rates (Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50 per 1M) | — | https://docs.claude.com/en/docs/about-claude/pricing , https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool |
| Claude consumer Research | Product feature | In Claude Pro/Max plans | Limited | https://www.anthropic.com/pricing |
| **xAI Grok DeepSearch** | **Product** on top of grok-4.7 | API: grok-4.7 $2/$6 per 1M (<200k ctx), $4/$12 (≥200k); **`web_search` tool $5/1k calls**, `x_search` $5/1k posts + $10/1k profiles | ✅ limited DeepSearch on free X/Grok tier | https://docs.x.ai/developers/pricing |
| **Exa Agent** | **Agent API** (async deep research, list building, enrichment) | **$0.012/run (minimal) → $0.025 (low) → $0.10 (medium) → $0.50 (high) → $1.00 (xhigh)**; `auto` up to $5, `ultra` up to $20 per run | MCP keyless has **no** agent access | https://exa.ai/docs/admin/pricing |
| Exa Deep Search (`deep`, `deep-reasoning`) | Search mode with synthesis + citations | $12/1k (deep-lite, deep), $15/1k (deep-reasoning) | Draws from the $10 monthly credit | same |
| Exa Answer | Grounded answer endpoint | $5/1k | same | same |
| **You.com Research** | **Research agent API** | Lite **$12/1k**, Standard **$50**, Deep **$100**, Exhaustive **$450**, Frontier **$1,200/1k** calls | Web Search MCP only (100/day) | https://you.com/pricing |
| Parallel Task API | Deep-research agent API | **from $5/1k runs**; Responses API from $10/1k | Launch = MCP only | https://parallel.ai/pricing |
| Linkup Research | Deep-research agent | **$0.25–$2.50 per request** | 4,000 free queries (search-level) | https://www.linkup.so/pricing |
| NotebookLM | **Product** (grounded RAG over *your* sources) | Included in Google AI Plus $4.99 / Pro $19.99 / Ultra $99.99–$199.99 | ✅ 100 notebooks, 50 sources each | https://support.google.com/gemininotebook/answer/16213268 |

**Model-vs-product cheat sheet**

- *Model you call*: Sonar / Sonar Pro / Sonar Reasoning Pro / Sonar Deep Research, o3-deep-research (retired), o4-mini-deep-research (retired), grok-4.7, Claude + `web_search` tool, Gemini 3.1 Pro + grounding.
- *Agent endpoint*: Exa Agent, Exa Deep Search, Perplexity Agent API, You.com Research, Parallel Task/Responses, Linkup Research, Gemini Deep Research (API), Consensus Deep review.
- *Consumer product only*: ChatGPT Deep Research, Grok DeepSearch, Claude Research, Perplexity Pro/Max, NotebookLM.

---

## 4. Recommendation matrix

| If you want… | Use | Why / entry cost |
|---|---|---|
| **Semantic / RAG web search** | **Exa** (`auto` $7/1k, embeddings-native) → fall back to **Brave LLM Context** ($5/1k, own index) | Exa returns token-efficient content with the result; Brave is cheapest at scale with no Google dependency |
| **Cheapest Google results** | **Serper** at $50 prepay ($1/1k → $0.30/1k) | Nothing else is close; accept unofficial scraping, 6-month credit life, and no per-endpoint multipliers. If you need official/stable, **SerpApi** from $25/mo |
| **Academic literature** | **OpenAlex** (free, $1/day API budget) as the base index + **Semantic Scholar** (free, 1 RPS keyed) for recommendations/citations | Both free; OpenAlex for breadth/metadata, S2 for graph features. Add **Crossref** (free) for DOI truth and **arXiv** / **Europe PMC** / **PubMed** for subject depth |
| **Cited answer engine** | **Exa Answer** ($5/1k) or **Perplexity Search API** ($5/1k, 5 queries per request) at the API level; **Consensus** ($20/mo) for science claims; **Perplexity Pro** ($17/mo annual) for humans | Perplexity returns synthesised answers with citations out of the box; Consensus returns supporting/contradicting evidence per paper |
| **Bulk paper screening** (title/abstract triage at scale) | **OpenAlex + Crossref + Semantic Scholar** bulk endpoints (free) with batch calls; use **Elicit** ($49/mo Pro) only when you need extraction tables | Only the free scholarly APIs can handle 10k+ records without per-request pricing. Elicit/SciSpace/Consensus free tiers are per-message, not per-paper |
| **Full-page scraping** | **Firecrawl** ($19/mo Hobby, 1k credits) or **Exa Contents** ($1/1k pages) | Firecrawl is the cheapest flat-rate crawler; Exa Contents wins if you already pay Exa for search |
| **Deep-research agent run** | **Exa Agent** $0.012–$1.00/run (fixed effort) | By far the cheapest predictable per-run cost; You.com Frontier ($1.20/run) and Linkup Research ($0.25–$2.50) are pricier |
| **Free "research something properly" for a person** | **Gemini Deep Research** (free tier) or **Perplexity** (3 Pro/day) or **NotebookLM** (unlimited-ish, your own sources) | Zero-cost, no API key |
| **Citing a model inside another app** | **Claude Sonnet 5.5 + `web_search`** ($2/$10 per 1M + $10/1k searches) or **grok-4.7 + `web_search`** ($2/$6 per 1M + $5/1k calls) | Grok's search tool is half of Anthropic's and the model is cheaper than Sonnet |
| **Citation graph / discovery** | **ResearchRabbit** (free) or **Connected Papers** (5 graphs/month free) or **Litmaps** (free 2 maps) | All free at entry; pay only when you need unlimited maps |
| **Open-access full text at volume** | **CORE** (free, 5 req/10 s) + **Europe PMC** (free) | CORE is the only free one that hands you the *full text*, not just metadata |

---

## 5. Verification notes / traps

1. **OpenAI deep-research models are gone.** `o3-deep-research` and `o4-mini-deep-research` were retired 2026-07-23. Any quote for them (including the $10/$40 and $2/$8 per 1M figures above) is historical.
2. **Perplexity's Sonar Chat Completions API was superseded by the Agent API (Sept 2026)**; existing callers are grandfathered. `sonar-reasoning` was deprecated 2025-12-15. Budget on the Search + Agent API prices, not Sonar's.
3. **Perplexity bills a per-request fee *plus* tokens** — the "per 1k requests" figure alone understates cost.
4. **Exa and SerpApi both charge extra per result above 10**; Exa's "$7/1k" assumes ≤10 results.
5. **Firecrawl searches and page scrapes share one credit balance** — 1,000 free credits ≈ 500 searches *or* 1,000 pages, never both.
6. **Serper credits expire after 6 months**, so a cheap big prepay can be a loss if you don't consume it.
7. **Parallel's free Launch tier is MCP-only** and has no billing — it will not work for a REST pipeline.
8. **Consensus's free tier includes only 30 API/MCP calls/month**; the unlimited searches are for the web UI.
9. **Scite's free tier has no REST key** — only public Papers/Tallies endpoints.
10. **Google Scholar has no API** and its ToS forbids automated querying; SerpApi/Serper "Google Scholar" endpoints are unofficial scrapers that may break or get blocked.
11. **Brave requires attribution** to keep the $5/month credit.
12. **PubMed E-utilities: ≤3 req/s**, and bulk jobs only at night/weekends — this is a policy limit, not a throttle.
13. **Google Gemini grounding**: 5,000 free grounding requests/month, then **$14/1k** on Gemini 3+ — often more than the tokens themselves.
14. ⚠️ Unverified in this pass (page did not render prices): Brave plan grid beyond blog figures, Serper's own page, Connected Papers personal plan prices, Litmaps monthly prices, Dimensions licence price, Perplexity Pro monthly (non-annual) rate, Google AI Studio pricing page. Re-check before quoting.
