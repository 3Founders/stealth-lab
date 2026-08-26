# Ingesting a 10M-Paper Corpus into StealthLab — Plan (OpenAlex bulk + Exa enrichment)

Researched 2026-08-26 against live vendor docs. Companion to `commLLM.md` §3B and the
extraction pipeline (`procedure_extraction/evidence.py`).

## Source-of-truth decision

| Role | Tool | Why |
|---|---|---|
| **Bulk truth (all 10M)** | **OpenAlex S3 snapshot** (`s3://openalex/data/parquet/works`) | Free, anonymous, CC0; ~649M records total, partitioned by `updated_date` in ≤400k-row parts; carries `deleted_ids.csv` (tombstones!) |
| **Incremental updates** | OpenAlex `updated_date=` partitions re-synced | Snapshot partitions make deltas cheap; no cursor-paging 10M rows through the metered API (100k credits/day free, 100 req/s cap — API is for lookups, never bulk) |
| **Enrichment (subset only)** | **Exa** `/contents` @100 QPS, `$1/1k pages/type` | Never bulk: fill missing abstracts + fetch landing/pages for top-K subset. Budget-capped; `/search` ($7/1k, 10 QPS) reserved for discovery queries, not corpus walks |

## What a paper becomes in OUR graph (born-correct, fresh-start compliant)

1. **knowledge_node** (`node_type='publication'`): title, venue, year, authors, topics,
   cited_by_count, OA status. Natural keys: openalex_id + doi (`ON CONFLICT DO NOTHING`).
   `provenance='public_generated'`, `scope_type='entity'`,
   `scope_entity_id=<doi>`, `extractor_version='openalex-snapshot@<date>'`.
2. **Deterministic SPO claims — no LLM in the bulk path.** Each metadata field becomes a
   claim: `(W123, published_in, venue) as_of pub_year`, `(W123, cites, W456)` from
   `referenced_works`, `(W123, has_topic, topic)`. These are `extract_deterministic_
   observations()`-class facts: cheap, verifiable, bi-temporal
   (`t_valid = publication_year`, `t_created = ingest time`).
3. **LLM claim extraction is LAZY.** Running the model pipeline over 10M abstracts is an
   economics bug (~$10k+ and weeks). Instead: abstract stored on the node; claim-level
   extraction runs on-demand for retrieval-hit subsets (top-K reranked candidates),
   memoized. The substrate already separates candidate extractions from accepted
   knowledge (invariant #15) — lazy claims enter as candidates pending review.
4. **Tombstones**: apply `deleted_ids.csv` as `t_invalid` stamps. Retractions/deletions
   are exactly our supersede semantics — history stays queryable.

## Pipeline (stages, each resumable)

```
S1 sync      aws s3 sync s3://openalex/data/parquet/works ... --no-sign-request
             (+ authors/sources/topics lookup tables for FK resolution)
S2 stage     pyarrow/duckdb stream parquet → COPY into staging tables (raw_openalex_works)
             ~10M rows ≈ 30–90 min local disk-bound; no indexes yet
S3 resolve   reconstruct abstracts from inverted index (SQL/duckdb join position arrays);
             dedup vs existing nodes on doi/openalex_id
S4 emit      transform → COPY into corpus_works (dedicated table, own HNSW — keeps the
             core knowledge_nodes table lean) + edges + deterministic claims
S5 embed     batch embedding of title+abstract (~250 tok avg ⇒ ~2.5B tokens)
             — paid batch tier ONLY (free-tier 25k TPM bucket would take years):
             ≈ $300–600, ≈ 6–15 wall-clock hours at provider batch limits
             store as halfvec(1024) (fp16: 10M × 2KB ≈ 20 GB vs 40 GB fp32)
S6 index     CREATE INDEX HNSW AFTER bulk load (build during load = disaster);
             expect 1–3 h build, m/hnsw ef params tuned for recall@10 ≥0.95 on holdout
S7 tombstone apply deleted_ids.csv; S8 smoke: run retrieval leave-one-out sanity
```

## Scale math (the honest numbers)

| Item | Value |
|---|---|
| Works rows | 10M × ~1 KB ≈ 10–15 GB heap |
| Vectors (halfvec 1024d) | ~20 GB + HNSW graph overhead ≈ 25–35 GB |
| Embedding cost | ~2.5B tokens ⇒ **$300–600 one-time** (batch pricing) |
| Load wall-clock | S1 dominates (hundreds of GB over network); DB stages ≈ hours; embedding ≈ overnight |
| Hot-path impact | ZERO if corpus lives in its own table + its own retriever leg; core OLTP paths untouched |

## StealthLab-specific integration points (files that change)

- NEW: `backend/app/services/corpus_ingest.py` (staging→emit, V0-gated) +
  `db/NN_corpus_works.sql` (table + HNSW + deleted-log application)
- EXTEND (never rewrite): `HybridRetriever` gains a corpus leg feeding RRF fusion;
  `retrieve_precedent` optionally includes publications when query intent is factual
- Lazy extractor: reuse `GroundedHybridExtractor` on reranked hits; results land as
  candidate claims w/ `extractor_version`, promoted via existing review path
- Exa enricher: queue-driven (ingestion_jobs pattern) for top-K subset; hard monthly
  credit ceiling in config; every fetched page lands as Evidence w/ URL + fetch date

## Risks & mitigations

- **Embedding cost creep** → embed titles-only first (cheap pass), abstracts second;
  skip non-English until needed (language field exists)
- **HNSW recall degradation at 10M** → measure recall@10 on 1k-holdout before declaring
  done; fall back to ivfflat + exact rerank over topic-partitioned subsets
- **OpenAlex abstract gaps** (~share without inverted index) → Exa contents for top-K
  cited subset only; node stays metadata-complete regardless
- **Snapshot drift between releases** → pin RELEASE date; incremental via updated_date
  partitions + deleted_ids; never mix release dates in one load
