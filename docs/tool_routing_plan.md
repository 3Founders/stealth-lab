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
- P1 and P2, 2026-10-30:
  - Catalog: the MCP Registry snapshot (52,230 servers). Every remote server was probed (`initialize` + `tools/list`):
    20,437 live, 9,695 need a sign-in, 4,089 dead (dropped), 305 templated URLs. 48,124 cards remain; 20,372 carry
    their real tool lists. GitHub stars for 28,108 linked repositories (`scripts/fetch_repo_stars.py`). A "when to use"
    expansion per card from Qwen3-235B (`scripts/expand_tool_cards.py`, 9.6M tokens). Vectors from
    Qwen3-Embedding-8B at 1024 dimensions (`scripts/build_tool_vectors.py`; 89 MB, not committed). Committed data:
    `scripts/pack_tool_data.py` writes the `.json.gz` files.
  - Ranking (`app/tool_routing/retrieve.py`): BM25 + embeddings (RRF) -> Qwen3-Reranker-8B over the first 40, the
    caller's installed tools and the goal's 15 most used servers among those related by meaning. The reranker is
    asked "which MCP server gives a coding agent the ability to <goal>? The agent's task: <prompt>"; asked plainly,
    it ranked by topic (a rate-limiting server for "search the web for how teams handle rate limiting"). Among the
    cards it calls relevant (>= 0.5), its order and the order by use (stars, liveness, a verified publisher) are
    fused by RRF; relevant installed tools come first; an official server named in the prompt is lifted.
  - Claude Code: the triage (JEV, the same call) answers a fourth question, the tool goal or none (14 of 14 right on
    a hand set, `none` for a rename, a repository question, thanks and a plain change). When it names one, the hook
    asks `POST /tools` alongside the lookup and adds a few lines: installed servers first, then one to add
    (`packaging/npm/lib/tool_note.mjs`). Only server names and a package name or host leave the machine.
  - Coding set (45 requests, top 5): keywords alone 93% hit, MRR 0.81; with meaning and the reranker, goal guessed,
    93% and 0.90; with the triage's goal, as the hook runs, 98% and 0.89 (the one miss: a stock price got general
    scrapers). ToolRet (520 queries, 44k tools): nDCG@10 BM25 22.1, dense 27.8, hybrid 30.7, + reranker 40.2 (the
    paper's best retriever: 33.8).
  - Storage: the catalog lives in the control database (db/160_tool_cards.sql: tool_cards, tool_card_vectors,
    tool_terms), filled by the server itself from the snapshot in its image (app/tool_routing/store.py sync_catalog:
    advisory lock, only changed cards written, only changed text re-embedded). A first load takes about 6 minutes
    (vectors from a local file) or about 35 (embedded on DeepInfra); a restart with nothing changed writes nothing.
    Per request: rare-word full-text search and an exact scan of 256-dimension vectors in a narrow table (0.3 s warm),
    then the full-vector similarity of those ~570 candidates and the unchanged ranking. A whole /tools call takes 3-5 s,
    most of it the embedding and reranker APIs. Measured and rejected: an HNSW index (3-18 s a query once its 400 MB no
    longer fit the buffers) and an OR of all the request's words (22k matches, 4.6 s to rank). Without the tables (an
    older database) the server falls back to loading the files.
  - Open: Brave, Exa and Tavily do not surface for web search (more general scrapers do).
- P3, outcomes (2026-10-31): which tools a task was offered and used, and how it ended, go to `tool_observations`
  (db/162). Claude Code: the capture hooks watch MCP tools and web search/fetch (PostToolUse matcher), keep a record per
  prompt (packaging/npm/lib/tool_outcomes.mjs), and send it at Stop with the test verdict (POST /tools/outcome); the
  library entry for the prompt gets `tool:<ref>` tags. claude.ai: the listed `report_tools` tool, which find_ways'
  tools field asks for at the end of the task. A server is named by its package or URL host, never the user's own name
  for it or a URL path. Ranking: a tool's smoothed pass rate on the goal's tasks moves it by up to +/-0.1, weighted by
  how many outcomes there are (app/tool_routing/retrieve.py _proven).
