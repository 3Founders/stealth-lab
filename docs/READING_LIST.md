# Reading List — Building the StealthLab Retrieval/Memory Stack

Compiled via Exa semantic search + web research (2026-08-24). Ordered by
leverage against our known gaps: dense-only retrieval, `ts_rank`≠BM25,
no reranker, no contextual ingest, no ANN index, fixed top-k search shape.

**Our stack today**: Postgres+pgvector (`<=>` cosine, Gemini 1024-dim) +
OR-expanded `to_tsvector`/`ts_rank`, fused RRF k=60 in
`backend/app/services/agent_search.py` and `retrieval.py`. Benchmark side:
`stealthlab_procedures` variant over 698 procedures.

---

## Tier 1 — Direct hits on current gaps

### 1. Contextual Retrieval (Anthropic) — biggest single lever for our 698-procedure corpus
- https://www.anthropic.com/engineering/contextual-retrieval
- Cookbook walkthrough: https://github.com/anthropics/claude-cookbooks/blob/main/capabilities/contextual-embeddings/guide.ipynb
- Why: LLM-written context blurb prepended to each chunk before embedding +
  BM25 indexing cut top-20 retrieval failures **35% alone → 49% with
  contextual BM25 → 67% with reranking**. One-time ingest cost, prompt-cache
  cheap (~$1.02/M doc tokens). Directly applicable: our procedure nodes embed
  bare; a "which section/company context" blurb per node is a re-embed away.
- First action: prototype contextual blurbs in `Onboarder.seed()` path,
  re-embed, rerun phaseN scoring.

### 2. Real BM25 in Postgres (ParadeDB pg_search) — replace ts_rank
- https://www.paradedb.com/learn/search-in-postgresql/bm25 (why built-in FTS falls short)
- https://vineeth.fyi/blog/pg-vs-pg-search/ (PG17 head-to-head: 401ms vanilla vs 92ms ParadeDB, 11 indexes → 1)
- https://neon.com/blog/postgres-full-text-search-vs-elasticsearch (ts_rank degradation at scale; GIN not relevance-tuned)
- https://www.paradedb.com/blog/introducing-search (pg_search overview)
- Why: our lexical leg uses `ts_rank` with OR-expanded tsquery — no IDF, no
  length saturation, no term-frequency curve. The τ-Knowledge board scores
  BM25 at 16.8 mean pass^1 vs dense-only 16.5; hybrid is strictly better than
  either. pg_search gives real BM25 without leaving Postgres.
- First action: spike pg_search extension locally; swap `_lexical_search`
  internals behind the same interface.

### 3. Cross-encoder rerankers — precision stage we don't have
- Landscape comparisons (2026): https://newsifyall.com/llm-reranking-2026-cohere-vs-bge-vs-voyage-compared/ · https://particula.tech/blog/reranker-models-compared-cohere-voyage-jina-bge-latency-ndcg · https://topaitracker.com/rankings/2026-06-14-best-ai-reranker-models-for-rag-ranked-by-retrieval-quality-and-cost/
- Key numbers: Azure AI Search benchmark — hybrid alone +10% NDCG vs vector-only; **hybrid+rerank +37%**. Anthropic: rerank took failures 2.9%→1.9%.
- Candidates: Voyage rerank-2.5 ($0.05/M tok, 200M free/mo, instruction-following) · Cohere Rerank 4 Pro (~$2/1k searches, ELO 1629) · self-host Jina reranker-v3 (61.94 nDCG@10 BEIR, ~188ms, listwise, CC-BY-NC) · BGE v2-m3 (Apache 2.0 baseline).
- Why: our RRF fuses two weak signals into a final order with zero
  cross-attention. Reranking fused-top-N→top-K is the standard fix.
- First action: wire reranker behind env flag after pg_search lands; measure
  Doc Recall delta on banking_knowledge tasks before committing.

### 4. Hybrid retrieval patterns — validate/refine our RRF
- https://docs.weaviate.io/weaviate/concepts/search/hybrid-search
- TREC 2025 RAG track systems (sparse-dense fusion + LLM rerank): https://trec.nist.gov/pubs/trec34/papers/UTokyo.rag.pdf
- SemEval-2026 Task 8 multi-turn hybrid systems: https://aclanthology.org/2026.semeval-1.32.pdf · https://aclanthology.org/2026.semeval-1.345.pdf
- SciRet compute-aware retrieval/rerank study: https://arxiv.org/html/2608.03860v1
- Why: confirms RRF k=60 choice but shows weighted fusion (80/20
  semantic/lexical in Anthropic's cookbook) and query rewriting/HyDE as the
  next knobs. SciRet is the template for compute-quality tradeoff tables.
- First action: read Weaviate doc; note weighted-fusion option for later A/B.

## Tier 2 — Architecture direction

### 5. Agentic search beats fixed top-k (the Terminal result)
- https://arxiv.org/html/2605.15184v1 — "Is Grep All You Need?" (harness reshapes agentic search)
- https://arxiv.org/html/2605.29307 — GrepSeek: training search agents for direct corpus interaction
- https://arxiv.org/pdf/2605.05242 — critique of fixed similarity-interface retrieval
- τ-Knowledge evidence (already benchmarked against): arXiv:2603.04370 — Terminal config mean 20.1 pass^1 > Qwen3-emb 17.7 > BM25 16.8 > dense 16.5; Sierra-standardized BM25+dense+shell → GPT-5.5 xhigh 37.4%.
- Why: the board says multi-step tool-driven corpus access beats one-shot
  embedding lookup even for frontier models. Our substrate exposes search as
  a tool already — this cluster tells us how agents should drive it
  (iterative grep-style refinement, not single query).
- First action: read the Grep paper; consider an iterative-search loop mode
  in the stealthlab_procedures scaffold.

### 6. Agent memory architectures — where the substrate sits in the field
- https://aclanthology.org/2026.findings-acl.2069/ — From Storage to Experience: survey of LLM agent memory mechanisms (ACL 2026)
- https://arxiv.org/html/2604.01707v3 — Memory in the LLM Era: modular architectures, unified framework incl. A-MEM taxonomy table
- https://arxiv.org/html/2602.19320v1 — Anatomy of Agentic Memory: taxonomy + empirical limits of evals/systems
- https://arxiv.org/html/2603.07670 — Memory for Autonomous LLM Agents: mechanisms, evaluation, frontiers
- Why: positions knowledge_nodes/hierarchy/provenance work against MemGPT/
  Letta, Mem0, Zep/Graphiti temporal KGs, HippoRAG, A-Mem. Useful before any
  next structural change so we adopt field-standard vocabulary and avoid
  rebuilding known designs.
- First action: skim the ACL survey's taxonomy; map our components onto it.

### 7. pgvector production tuning — we have no ANN index yet
- https://nerdleveltech.com/pgvector-hnsw-postgres-18-production-tuning-tutorial (PG18 + pgvector 0.8.2: six statements, three GUCs, quantization)
- https://dev.to/philip_mcclarence_2ef9475/pgvector-in-production-hnsw-filtering-and-tuning-46lj (start with HNSW, filtering patterns)
- https://queryplane.com/blog/pgvector-hnsw-tuning-guide/ (ef_search/m/ef_construction recall-speed tradeoffs)
- https://github.com/pgvector/pgvector — canonical reference (HNSW vs IVFFlat, halfvec)
- Why: 698 docs seq-scan fine today, but hierarchy/dedup/conflict scans fan
  out fast and the corpus grows. HNSW + halfvec is the standard graduation.
- First action: add HNSW index migration when corpus >10k nodes or latency
  budget tightens.

## Tier 3 — Eval rigor

### 8. LongMemEval (ICLR 2025) — long-term memory eval methodology
- https://arxiv.org/html/2410.10813 · https://github.com/xiaowu0162/LongMemEval · https://xiaowu0162.github.io/long-mem-eval/
- Why: complementary to τ-Knowledge's Doc Recall/Action Recall — tests
  session-spanning memory abilities our substrate claims but hasn't measured.

### 9. τ-Knowledge protocol (already our scoreboard — read the full paper)
- https://arxiv.org/html/2603.04370v1 — pass^k metrics, paired bootstrap at
  (model, task) level, ≥k trials per config, Action Recall + Doc Recall.
- Why: our phaseN numbers only count if computed per this protocol.

## Appendix — Free agentic search tooling (zero-key / self-hosted)

Found via Exa + web sweep while hunting Tavily/Brave alternatives. All run
without paid API keys; maturity varies (many are young repos).

### Self-hosted metasearch + MCP servers
- **agent-search** (MIT, ~71★) — https://github.com/brcrusoe72/agent-search —
  FastAPI+SearXNG, 17 endpoints, strategy modes (`general`/`code`/`academic`/
  `news`/...), cross-engine dedup, prompt-injection scrubbing, and *direct
  no-key academic providers* (arXiv, Crossref, OpenAlex, Semantic Scholar) —
  covers exactly the academic-API gaps we hit earlier this session.
- **searxng-mcp** (MIT) — https://github.com/millerjes37/searxng-mcp —
  SearXNG MCP exposing 7 tools (web/news/video/code search + URL fetch),
  aggregates 89+ engines, zero keys.
- **free-search-mcp** — https://github.com/sweetcornna/free-search-mcp —
  local-first multi-engine (DDG/Mojeek/Bing defaults) with **RRF-fused
  results** (same fusion we use internally), FTS5 page cache, PDF/DOCX
  reader, one-shot `research()` tool, Playwright rescue fallback.
- (Skipped: `cuba-search` — archived + CC-BY-NC.)

### Keyless search-and-fetch stacks (lightest weight)
- **Jina Reader OSS** (Apache-2.0) — https://github.com/jina-ai/reader —
  `r.jina.ai/<url>` → markdown, `s.jina.ai/<query>` → top-5 pages fully
  fetched and converted; self-hostable docker image; free hosted tier.
- **web-forager** (MIT, on PyPI) — https://pypi.org/project/web-forager/ —
  DuckDuckGo (ddgs) + Jina fetch as an MCP server *and* standalone Agent
  Skills; graceful fallback chain DDG→Exa→Jina→manual fetch.
- **websearch-skill** — https://github.com/hec-ovi/websearch-skill — keyless
  uv-native skill: ddgs multi-engine search with de-correlated rank fusion +
  Trafilatura extraction + keyless arXiv access.
- **god-search**, **zero-api-key-web-search**, **local-search**
  (https://www.lsearch.dev/, drives a local Chrome) — further zero-key
  options if the above get rate-limited.

### Deep-research orchestration
- **GPT Researcher** — https://github.com/assafelovic/gpt-researcher ·
  https://gptr.dev — the standard open-source autonomous deep-research agent;
  pluggable retrievers, ships an MCP server. Pair with any backend above.

### Local-corpus agentic search (pattern source for our substrate)
- **xgrep** — https://github.com/momokun7/xgrep/ — indexed code search,
  faster than ripgrep on repeated queries, built-in MCP server.
- **grepplus** — https://github.com/Mixpeal/grepplus/ — hybrid CLI: raw grep
  speed by default + meaning-aware semantic layer when needed.
- **agentgrep** — https://github.com/1jehuang/agentgrep — Rust CLI-first
  retrieval designed to replace an agent's file-search tools.
- **TriSeek** — https://sagart-cactus.github.io/TriSeek/ — shared local
  context substrate (find → remember-read → reuse search context across
  sessions). Architecturally closest cousin to what StealthLab does for
  knowledge nodes.

**Practical picks**: for *my* tooling, web-forager or websearch-skill gives
keyless coverage today; for durable infra, agent-search (its academic modes
replace the S2-429/OpenAlex workarounds). Caveat: scraping-based engines
(Google/Startpage) break often — treat SearXNG instance health as ops debt.

---

*Search provenance: Exa `/search` (semantic) for tiers 2–3 clusters and BM25
cluster; session websearch for reranker landscape + contextual retrieval.
Raw results cached at `C:\Users\chait\AppData\Local\Temp\opencode\exa_reading_results.json`.*
