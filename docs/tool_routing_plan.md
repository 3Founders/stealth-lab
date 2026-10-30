# Tool routing: choosing the right tool for a task, as kell chooses the model

Status: approved 2026-10-30; P1 in progress.

## Why

kell routes each task to the cheapest model likely to do it (`docs/routing_priors.md`). The same question exists for
tools: thousands of MCP servers and APIs exist, an agent (the session's or a cheap one) usually has a few installed and
does not know which fits the task, and listing every tool in the context costs tokens on every turn and lowers
selection accuracy. kell already has the shape: `discover_tools(need)` + `use_tool(name, arguments)` over kell's own
nine hidden tools. Tool routing makes that search the real catalog and ranks it per kind of goal, learning from
outcomes the way the model ranking does.

## What the research says (2026-10-30)

- **Monid** (monid.ai; `monid-ai/monid`, MIT): three meta-tools, `discover` / `inspect` / `run`, over hosted API
  endpoints billed per call. Its ranking ("by what the job is", with price, live health, p50/p95 latency) is hosted and
  not in the repo. The open catalog is 726 endpoints from 35 providers, mostly SEO, scraping and data (DataForSEO alone
  is 216); 64 flat leaf categories. Worth copying: descriptions written for agents, saying what the endpoint does, what
  it will not do, and which endpoint to use instead. Not a source of coding tools.
- **Tool-routing practice**: decide whether a tool is needed, retrieve a short list (BM25 + embeddings), load only the
  chosen tools' schemas, search again as the plan changes (MCP-Zero), learn from use. Retrieval is hard (ToolRet: the
  best retriever 33.8 nDCG@10). Nobody publishes ranking tools per goal from task success.
- **Avengers-Pro** (arXiv 2508.12631): query clusters + an accuracy/cost weight for models; no goal/procedure
  primitives. kell's goal -> procedure model is the skill-library line (Voyager, Agent KB).

## The design

1. **Is a tool needed, and for what?** The triage (JEV) gets a question: none, or which tool goal(s).
2. **Tool goals** (`backend/app/tool_routing/goals.py`): 15 abstract goals for coding work, each with a description the
   retriever matches against.
3. **Catalog of tool cards** (`backend/app/tool_routing/catalog.py`): kell's own tools; the user's installed MCP
   servers and their tools (sent by the client); the official MCP Registry (a snapshot,
   `scripts/fetch_mcp_registry.py`). Monid later, for outside data: it is a paid proxy.
4. **Retrieval** (`backend/app/tool_routing/retrieve.py`): BM25 over name, title and description, plus embedding
   similarity, fused by reciprocal rank; a match to the request's goal adds to the score; installed tools rank above
   tools the user would have to install.
5. **Ranking per goal** (P3): a prior from the card, then outcomes per goal (did the call work, did the task pass its
   check and review), in the same Bayesian form as the model ranking.
6. **Delivery**: `discover_tools(need, goal?, installed?)` returns kell's tools as before plus a ranked short list of
   external tools with when to use each.

## Phases

| Phase | What | Status |
|---|---|---|
| P1 | catalog + goals + hybrid retrieval behind `discover_tools`; retrieval scored on a coding-tool set and ToolRet; hosted embeddings fixed | in progress |
| P2 | the triage flags "tool needed, goal X"; the hook names the matching installed tools to the session's model | |
| P3 | outcome logging per (tool, goal); the ranking learns | |
| P4 | cheap agents call tools (an MCP client in the step agent, read-only and allow-listed first) | |

## Measuring

- Retrieval: ToolRet (nDCG@10, recall@k) and a hand-labelled set of coding requests -> the right server.
- Whole tasks: LiveMCPBench (527 tools), MCPToolBench++; then kell's own outcomes.

## Done so far

- Hosted embeddings: Railway had `EMBEDDING_PROVIDER_CHAIN=openrouter` and no `OPENROUTER_API_KEY`, so `find_ways`
  ran on keywords only ("embedding provider unavailable"). The key is set (2026-10-30, without a deploy); it takes
  effect on the next deploy.
