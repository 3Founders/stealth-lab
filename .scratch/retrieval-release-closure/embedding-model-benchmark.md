# Embedding-model benchmark (release closure section 1)

Harness: `backend/scripts/benchmark_embedding_models.py`. Raw JSON:
`.scratch/retrieval-release-closure/embedding-model-benchmark.json`.

## Method

**Held constant** (verified in the harness): the procedure set, the
canonical retrieval representation (`procdoc_v1`), the lexical retrieval
leg (`applicability._PROC_LEXICAL_SQL`, model-independent, run once per
query and reused), RRF fusion (`retrieval.fuse_rrf`), the browse-mode
applicability filter (`_CANDIDATE_BASE_WHERE`), access filtering
(unrestricted — the eval set has no private rows), the ranking logic, the
57 evaluation queries, the 855 labels, and the scoring code.

**Varied:** the embedding model only — one of the three the codebase
actually supports (`local:mxbai-embed-large`, `voyage:voyage-3-large`,
`gemini:gemini-embedding-001`). No unsupported providers were introduced.

**Index scope:** the 448 unique procedures named by the labelled
candidates (the same set for every model). A full-2478-corpus re-embed
per model was not feasible here because Voyage's free tier without a
payment method is **3 RPM / 10 K TPM**. This is a bounded, fair comparison
— identical procedures, queries and labels; only the model changes — and
it is the same reduction `eval_representation_before_after.py` used.
Documented as a limitation.

**Threshold:** derived INDEPENDENTLY per model by the documented rule —
sweep the cosine cutoff over the model's observed range in 40 steps; pick
the highest-F1 cutoff among those whose no-match bucket returns zero
results in ≥ 90 % of its 7 no-match queries; ties → higher cutoff.

`Recall@K` / `Precision@K` / `MRR` / `nDCG@10` are on the **raw fused
ranking** (ranker quality, threshold-independent). `optimal F1` /
`no-match FP rate` / `zero-result rate` are at each model's **own optimal
threshold**.

## Results

| metric | **local** mxbai-embed-large | **gemini** gemini-embedding-001 | **voyage** voyage-3-large |
|---|---|---|---|
| per-model optimal threshold | 0.6906 | 0.6829 | _measurement running (bcmpyqvc0)_ |
| precision @ threshold | 0.762 | 0.621 | _pending_ |
| recall @ threshold | 0.481 | 0.643 | _pending_ |
| **F1 @ threshold** | 0.589 | **0.632** | _pending_ |
| no-match false-positive rate | 0.00 | 0.00 | _pending_ |
| zero-result rate | (7/7 no-match → 0 results) | (7/7) | _pending_ |
| Recall@1 | 0.277 | 0.290 | _pending_ |
| Recall@3 | 0.475 | 0.493 | _pending_ |
| Recall@5 | 0.546 | 0.565 | _pending_ |
| Recall@10 | 0.722 | **0.734** | _pending_ |
| Precision@1 | 0.579 | 0.597 | _pending_ |
| Precision@3 | 0.404 | 0.421 | _pending_ |
| Precision@5 | 0.326 | 0.330 | _pending_ |
| Precision@10 | 0.242 | 0.233 | _pending_ |
| MRR | 0.623 | **0.634** | _pending_ |
| nDCG@10 | 0.726 | **0.741** | _pending_ |
| embedding failures | 0 | 0 | _pending_ |
| embedding latency (448 docs + 57 q) | ~0 s (reads live column) | ~510 s, constant free-tier 429-rotation | _pending — 3 RPM pacing_ |
| operational cost for a 2478-doc migration | run/scale an Ollama host (cold call ~16 s) | free tier: ~35 min of 429-churn; **needs paid tier** | free tier: ~1 h at 3 RPM; **needs a payment method** |
| provider data-use policy | data never leaves your infra (strongest) | Google AI API terms — verify per account tier | Voyage SaaS terms — verify |

## Selection

**Ranking quality:** `gemini` > `local` on every K (MRR 0.634 vs 0.623,
nDCG@10 0.741 vs 0.726, Recall@10 0.734 vs 0.722). `gemini` also wins F1
at its own threshold (0.632 vs 0.589) by trading precision (0.62 vs 0.76)
for recall (0.64 vs 0.48). Both abstain perfectly on the no-match bucket.
The gap between `local` and `gemini` is **real but modest** on this
bounded index (≈ 1–2 points on the ranking metrics).

**`voyage` measurement is still running** and is the tie-breaker: Voyage-3
is generally the strongest of the three for retrieval, but its free tier
is operationally unusable at scale without a payment method.

**Decision (interim, pending the voyage row):**

- If `voyage` measurably beats `gemini` on MRR/nDCG/Recall@K AND
  Chaitanya adds a Voyage payment method (§2a of `CHITANYA-SETUP.md`) →
  **migrate to `voyage:voyage-3-large`**.
- Else, if Chaitanya enables billing on the Gemini project → **migrate to
  `gemini:gemini-embedding-001`** (measured better than the current local
  model, an API provider is operationally simpler than self-hosting
  Ollama at production scale).
- If neither paid option is provisioned → **keep `local:mxbai-embed-large`**.
  It is not the best of the three but it is fully functional, free,
  private, has zero embedding failures, and the retrieval quality
  difference vs the alternatives is modest on this eval. This is an
  operational decision for Chaitanya, not a quality-only one.

The production model is **not finalised** until the voyage row lands and
Chaitanya makes the billing decision. The corpus re-embed (section 6) and
the threshold re-derivation (section 7) then run against that final model.
