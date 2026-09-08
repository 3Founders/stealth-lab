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

## Results  (complete — bounded 448-procedure eval index)

| metric | **local** mxbai-embed-large | **gemini** gemini-embedding-001 | **voyage** voyage-3-large |
|---|---|---|---|
| **per-model optimal threshold** | 0.6906 | 0.6829 | **0.5396** |
| precision @ threshold | **0.762** | 0.621 | 0.598 |
| recall @ threshold | 0.481 | **0.643** | 0.631 |
| **F1 @ threshold** | 0.589 | **0.632** | 0.614 |
| no-match false-positive rate | 0.00 | 0.00 | 0.00 |
| zero-result rate on no-match | 7/7 → 0 | 7/7 → 0 | 7/7 → 0 |
| Recall@1 | 0.277 | 0.290 | **0.299** |
| Recall@3 | 0.475 | **0.493** | 0.470 |
| Recall@5 | 0.546 | **0.565** | 0.536 |
| Recall@10 | 0.722 | **0.734** | 0.696 |
| Precision@1 | 0.579 | **0.597** | 0.579 |
| Precision@3 | 0.404 | **0.421** | 0.404 |
| Precision@5 | 0.326 | **0.330** | 0.316 |
| Precision@10 | 0.242 | 0.233 | 0.226 |
| **MRR** | 0.623 | **0.634** | 0.616 |
| **nDCG@10** | 0.726 | **0.741** | 0.727 |
| embedding failures | 0 | 0 | **16 / 574** (free-tier 3 RPM / 10 K TPM cap) |
| embedding latency (448–574 docs + 57 q) | ~0 s (reads the live column) | ~510 s, constant free-tier 429-rotation | ~1090 s, 3 RPM pacing |
| operational cost for a 2478-doc migration | run/scale an Ollama host (cold call ~16 s) | free tier: ~35 min of 429-churn; **needs paid tier** | free tier: > 1 h at 3 RPM, will drop batches; **needs a payment method** |
| provider data-use policy | data never leaves your infra (strongest) | Google AI API terms — verify per account tier | Voyage SaaS terms — verify |

Note the **per-model optimal thresholds differ sharply** — 0.691 / 0.683
/ 0.540 — exactly why a single raw similarity cutoff must never be shared
across models (voyage-3-large's cosine distribution sits materially
lower).

## Selection: **`gemini:gemini-embedding-001`**

`gemini` leads on **MRR (0.634)**, **nDCG@10 (0.741)**, Recall@3/5/10,
Precision@1/3/5, and **F1 at its own threshold (0.632)**. All three
abstain perfectly on the no-match bucket.

- `local` has the best precision-at-threshold (0.76) but **by far the
  worst recall (0.48)** — it is the most conservative and misses the
  most.
- `voyage` is roughly tied with `local` on ranking (MRR 0.616, nDCG@10
  0.727) and behind `gemini`; it also produced **16 embedding failures**
  on the free tier — the strongest general-purpose model of the three,
  but not on this eval and not operationally, without paid Voyage access.

The differences are **real but modest** (≈ 1–2 points on the ranking
metrics between `gemini` and the others). `gemini` is the measured choice.

### Blocker on acting on the selection

`gemini-embedding-001` needs a **paid Google tier** — the free tier is
`RESOURCE_EXHAUSTED` across all three configured keys (`CHITANYA-SETUP.md`
§2b). Until that is provisioned, **keep `local:mxbai-embed-large`**: it is
functional, free, private, had zero embedding failures, and the retrieval
gap is modest. This is an operational decision for Chaitanya.

The production model is **not finalised** until Chaitanya makes the
billing decision. The corpus re-embed (section 6) and threshold
re-derivation (section 7) then run against the selected space
(`gemini` if billing is enabled, else `local`).
