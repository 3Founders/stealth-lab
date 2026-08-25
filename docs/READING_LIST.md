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

## Added 2026-08-24 — evaluation honesty + small-model clusters

Feeds Part D of `trial_implementation.md`. Ordered by cluster.

### Eval honesty (limitations of every number we produce)
- Sim2Real gap in user simulation: https://arxiv.org/html/2603.11245 — 451
  humans replace the LLM user sim on τ-bench; best simulator USI 76.0 vs
  human 92.9; binary reward orthogonal to human-perceived quality.
- Lost in Simulation (ACL 2026): https://aclanthology.org/2026.acl-long.2192/
  — ±9pp agent success from user-sim choice alone; demographic calibration
  failures (AAVE, Indian English).
- Reliability science framework: https://arxiv.org/html/2603.29231 — memory
  scaffolds never help long-horizon reliability across 10 models (overhead
  tax); variance amplification is a capability signature.
- Knowledge leakage in RAG benchmarks: https://arxiv.org/html/2605.08838v1
  (SeedRG) — parametric-answerable questions collapse retrieval signal.
- Gold ceiling: τ-Knowledge paper Table (arXiv:2603.04370) — gold docs cap at
  39.69% pass^1 for Opus-High; reasoning binds the frontier ceiling.
- Substrate routing harness: https://arxiv.org/html/2608.15008 — no substrate
  dominates; optimal reverses between QA and agentic regimes; trade read
  breadth for write depth.

### Context engineering (why render truncation works)
- Context rot / premature termination: https://arxiv.org/html/2606.29718v2
  (code: github.com/GAIR-NLP/ContextRot) — models give up long before
  exhausting context; management methods are test-time scaling; method
  choice is model-dependent.
- Length alone hurts despite perfect retrieval (EMNLP 2025 Findings):
  https://aclanthology.org/anthology-files/pdf/findings/2025.findings-emnlp.1264.pdf
  — 13.9–85% degradation with distractors masked out of attention entirely.
- Context-length robustness in QA: https://arxiv.org/html/2603.15723 —
  multi-hop degrades ~2× single-hop under equal expansion.
- Chroma context-rot study: https://www.trychroma.com/research/context-rot —
  needle-question similarity modulates degradation rate.
- Long Context vs RAG revisits: https://arxiv.org/html/2501.01880v1 — chunk
  retrieval is consistently the worst option; summarization-based ≈ LC.

### Procedural/workflow memory (procedure-layer validation)
- Agent Workflow Memory (ICML 2025): https://arxiv.org/abs/2409.07429 ·
  code: https://github.com/zorazrw/agent-workflow-memory — induce reusable
  workflows online, supervision-free; +51.1% relative on WebArena.
- Agent Skill Induction: https://arxiv.org/html/2504.06821 — programmatic +
  execution-verified skills beat text skills (+11.3pp over AWM); skills pay
  only in the action space.

### Small-model tool-use reliability (τ² bridge constraints)
- Constraint Tax / Tool Suppression: https://arxiv.org/abs/2606.25605 — JSON
  schema constraints suppress tool calling entirely in open-weight models;
  two-pass execution restores it.
- AgentFloor ladder: https://arxiv.org/html/2605.00334v1 — open-weight ≈
  GPT-5 through coordination tiers; frontier edge survives only in
  long-horizon planning under persistent constraints; decomposition prompts
  regressed every model.
- Scaffold effects on GAIA: https://arxiv.org/html/2606.08529v1 — scaffold
  moves accuracy up to 28pp within one model; family-conditioned;
  single-scaffold numbers are conditional estimates.
- Natural Language Tools replication: https://arxiv.org/html/2607.03953v1 —
  NL tool descriptions beat JSON calling +14.9pp / −93% critical errors,
  largest gains on smaller models.
- TSCG schema compiler: https://arxiv.org/html/2605.04107v1 — compile JSON
  schemas to text; Phi-4 14B 0%→84.4% at 20 tools; format is the mechanism.

### Knowledge lifecycle / ripple effects (TMS + ChangeSet design inputs)
- ChainEdit: https://arxiv.org/pdf/2507.08427 — KG-mined logical rules drive
  chain updates; baseline logical generalization ~20% on RIPPLEEDITS, >30%
  improvement with rule-aligned editing.
- Joint Neighborhood Optimization: https://arxiv.org/abs/2606.01610 —
  propagation and preservation are coupled pressures; pre-execution gate
  abstains from risky edits.
- TRACK (EACL 2026): https://aclanthology.org/2026.eacl-long.273/ —
  conflicting in-context updates *worsen* multi-step reasoning; more
  supplied updates = worse. Argues for tombstone-removal over overlay.
- CLaRE (ACL 2026 Findings): https://aclanthology.org/2026.findings-acl.1469/
  — forward-activation entanglement graphs predict ripple targets; cheap
  dependency discovery for audit trails.

### Debate & verification (governance layer guardrails)
- Multiagent debate (Du et al., ICML 2024):
  https://proceedings.mlr.press/v235/du24e.html — founding positive result.
- Should we be going MAD? (ICML 2024):
  https://proceedings.mlr.press/v235/smit24a.html — MAD does not reliably
  beat self-consistency; hyperparameter-sensitive.
- Demystifying MAD (ACL 2026 Findings):
  https://aclanthology.org/2026.findings-acl.1694/ — homogeneous+uniform
  debate ≈ majority vote; fixes are diversity-aware init and calibrated
  confidence updates.
- The Cost of Consensus: https://arxiv.org/html/2605.00914v1 — unguided
  homogeneous debate at 7–8B: sycophancy up to 85.5%, consensus collapse to
  32pp oracle gap, 2.1–3.4× token cost for equal/worse accuracy.
- GAVEL (ACL 2026 Findings): https://aclanthology.org/2026.findings-acl.1789/
  — Evidence Contract (subclaims bound to evidence units) + mechanized
  deterministic citation validation; the shape to copy if debate is ever
  enabled.

### Assumption stress-test sweep (Exa, 2026-08-24 — feeds `assumptions.md`)
- Prompt-Induced Waste in Coding Agents: https://arxiv.org/html/2608.01347
- Scaffold Effect as Hidden Variable (coding agents): https://arxiv.org/html/2607.22585
- Cross-Component Interference in Agent Scaffolding: https://arxiv.org/html/2605.05716
- Hidden Cost of Structure / constrained decoding (RANLP 2025): https://aclanthology.org/2025.ranlp-1.124/
- Skill-optimisation real-cost decomposition: https://arxiv.org/html/2607.03048
- **Quipu — governed bitemporal KG store (competitive/validation signal):** https://arxiv.org/html/2608.16813
- Evidence-Gated-Memory (OSS, lineage+gating+audit): https://github.com/yushui2022/Evidence-Gated-Memory
- SuperLocalMemory 4.0 governed memory OS: https://arxiv.org/html/2608.08253v1
- GapTime bi-temporal KG memory: https://github.com/davccalcante/gaptime · Bi-temporal edges audit essay: https://javatask.dev/blog/bitemporal-edges-agent-memory/
- What Deserves Memory (ACL 2026 adaptive distillation): https://aclanthology.org/2026.acl-long.1607.pdf
- LazyMem retrieve-broadly/construct-selectively: https://arxiv.org/html/2607.22690
- WhenLoss write vs retrieval bottlenecks: https://arxiv.org/html/2605.24579
- Retrieval vs utilization bottleneck diagnosis: https://arxiv.org/abs/2603.02473

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

## Tier 0 — Primary sources (read before writing anything up)

- **τ-bench** (Sierra): https://arxiv.org/abs/2406.12045 — the base protocol our harness descends from.
- **τ-Knowledge appendix**: https://arxiv.org/html/2603.04370v1 — exact pass^k definitions, Action/Doc Recall, paired bootstrap at (model, task) level. Our numbers only count if computed this way.
- **MemGPT**: https://arxiv.org/abs/2310.08560 — virtual-context memory management; foundation of Letta.
- **HippoRAG 2**: https://arxiv.org/abs/2502.14802 — hippocampal-index retrieval; nearest academic cousin of knowledge_nodes+hierarchy.
- **Zep/Graphiti**: https://arxiv.org/abs/2501.13956 — temporal knowledge graphs for agent memory; compare against our t_valid/t_invalid provenance model.
- **A-MEM**: https://arxiv.org/abs/2502.12110 — agentic memory with Zettelkasten-style note linking.
- **RRF original**: Cormack et al. 2009 — why k=60, why rank-fusion beats score-fusion.
- **ColBERT**: https://arxiv.org/abs/2004.12832 — late interaction; the middle ground between bi- and cross-encoders.
- **Lost in the Middle**: https://arxiv.org/abs/2307.03172 — positional attention decay; directly relevant to how we order rendered procedures in prompts.
- **Anthropic — Building Effective Agents**: https://www.anthropic.com/engineering/building-effective-agents — workflow-vs-agent decision framing.

---

*Search provenance: Exa `/search` (semantic) for tiers 2–3 clusters and BM25
cluster; session websearch for reranker landscape + contextual retrieval.
Raw results cached at `C:\Users\chait\AppData\Local\Temp\opencode\exa_reading_results.json`.*
