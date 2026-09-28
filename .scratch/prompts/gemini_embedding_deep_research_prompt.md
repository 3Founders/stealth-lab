# Deep research prompt: switching our embeddings to Gemini

Paste into Gemini Deep Research, Perplexity or a research agent. It asks for a sourced report; every number
needs a primary source and a date.

---

**Research question: should a small startup switch its retrieval embeddings from Voyage (voyage-3-large) to
Google's Gemini embeddings (as of September 2026), and if so, how should it migrate safely?**

**Our system (context the answer must fit):**
- A "verified procedural memory" service for AI coding agents. It stores **Goals** (short task names),
  **Procedures** (step-by-step how-tos, 100–2,000 words, often with code), **Claims** (one-sentence facts
  about software, with evidence) and **verified code solutions** (patches). Sources: SKILL.md files, agent
  trajectories, issue→patch→test tasks, CI workflows, codemods, docs.
- **Queries** are short natural-language task descriptions from coding agents ("add a DOCX export",
  "fix failing pytest import error"), sometimes with repository facts.
- Postgres + **pgvector**, columns `VECTOR(1024)`, HNSW indexes, sharded across many Postgres databases.
  Retrieval fuses dense similarity and full-text/BM25 with **Reciprocal Rank Fusion**, then an LLM judge
  checks applicability.
- Currently embedded with **voyage-3-large at 1024 dims**. Our code already supports
  `gemini-embedding-001` via both the **Gemini API (AI Studio keys)** and **Vertex AI `:predict`**, with
  `task_type` RETRIEVAL_QUERY / RETRIEVAL_DOCUMENT and `outputDimensionality=1024`.
- Scale: tens of thousands of items now, heading to 100k–1M+. Budget-sensitive; India-based company with
  US/EU users; some content is customers' private code-derived text.

**Answer each question with sources:**

1. **Models:** which Gemini embedding models exist today (gemini-embedding-001, any successor, multimodal
   variants), their native dimensions, Matryoshka (MRL) support and recommended dims, max input tokens, the
   task types (including `CODE_RETRIEVAL_QUERY`, `RETRIEVAL_QUERY`/`DOCUMENT`, `SEMANTIC_SIMILARITY`), and
   whether truncated outputs must be re-normalized. Cite Google's model cards and docs.
2. **Quality for our content:** how Gemini embeddings compare with voyage-3-large, voyage-code-3, OpenAI
   text-embedding-3-large, Qwen3-Embedding, Nomic/Jina and other strong 2025–2026 models on MTEB (English
   and code), CoIR / code-retrieval benchmarks, and anything on skill/tool/procedure retrieval (e.g. ToolRet,
   skill-retrieval studies). Quantify the loss from 3072→1024 truncation. Separate vendor-reported from
   independent results.
3. **Cost and limits:** per-token pricing and batch discounts on the Gemini API vs Vertex AI; rate limits
   per tier (RPM/TPM/RPD) and how to raise them; regions; latency. Estimate the cost to embed 1M items of
   ~800 tokens, and to re-embed our current corpus.
4. **Data terms (critical):** do the Gemini API free/unpaid tiers use submitted content to improve Google's
   products? What do the paid tier and Vertex AI promise (no training, retention, zero data retention,
   DPA, data residency)? Which tier is acceptable for customers' private content?
5. **Migration:** best practice for switching embedding models on a live pgvector corpus. Vectors from
   different models are not comparable, so cover dual-write, shadow index, backfill ordering, query routing
   during the switch, HNSW rebuild cost, and never falling back across embedding spaces at query time.
   Include how to evaluate before switching (a golden query set, recall@k/nDCG on our own data).
6. **Retrieval design for this domain:** does hybrid dense + BM25 with RRF still beat dense-only for short
   technical queries? Do task-type prompts or instruction prefixes help? Is a reranker (Gemini-based, Voyage
   rerank, Cohere, cross-encoders) worth adding? Cite papers from 2024–2026.
7. **Recommendation:** a go/no-go with conditions, the exact configuration (model, dims, task types, API
   surface, tier), the migration plan with cost and time, and the evaluation gate that must pass first.

**Rules:** primary sources first (Google docs and pricing pages, model cards, arXiv papers, benchmark
leaderboards); date every number; mark anything vendor-reported; say "unknown" rather than guess.
