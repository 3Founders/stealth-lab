# Competitive sweep — Mem0 / Letta / Zep-Graphiti / HippoRAG / AWM vs the trust spine

**Queue item 2 (board)** · **Date:** 2026-08-25 · **Lane:** research
**Method:** web search across vendor pages, third-party comparisons, primary papers. Exa unavailable in this worktree; built-in search used. Third-party benchmark rows are labeled as such and were not independently rerun.

## Our trust spine (the comparison baseline)

bi-temporal records · typed propositions · evidence independence groups · computed capability · non-compensatory applicability.

## What each ships today

### Mem0
- Ships: fact extraction → vector (+graph +KV) memory layer; managed cloud + Apache-2.0 OSS; ~14M+ downloads, $24M Series A, exclusive memory provider for AWS Agent SDK; first-class LangGraph/LangChain/CrewAI integrations (<https://futurepicker.com/en/ai-agent-memory-systems-letta-mem0-zep-2026-en>, <https://www.agenticwire.news/article/zep-ce-deprecated-migrate-mem0-graphiti-letta>).
- Tracker-reported quality: LongMemEval 49.0% with GPT-4o vs Zep's 63.8% (<https://baeseokjae.github.io/posts/agent-memory-architecture-guide-2026/>); vendor-side claims LoCoMo 91.6 / LongMemEval 93.4 via multi-strategy retrieval (<https://futurepicker.com/en/ai-agent-memory-systems-letta-mem0-zep-2026-en>) — contradictory third-party vs vendor numbers, treat both as marketing until reproduced.
- **Delta vs spine:** episodic/preference recall. No bi-temporal validity windows, no typed propositions, no execution-based verification of stored procedures, no applicability gating beyond similarity. Nothing procedural is *verified* — a recalled workflow is trusted because it was stored.

### Letta (MemGPT)
- Ships: OS-style paging over context (memory blocks + archival), full stateful agent runtime; $10M seed @ $70M valuation; ~22K stars (<https://futurepicker.com/en/ai-agent-memory-systems-letta-mem0-zep-2026-en>).
- **Delta vs spine:** memory management as runtime primitive — orthogonal to truth. No temporal semantics on facts, no evidence model, no non-compensatory gating. It answers "where does state live," not "why should this be believed."

### Zep / Graphiti
- Ships: **bi-temporal knowledge graph** — every edge carries event time + ingestion time; invalidated edges are timestamped, never deleted → point-in-time queries; incremental episode ingestion without graph recomputation (<https://1337skills.com/blog/2026-06-30-ai-agent-memory-2026-cognee-graphiti-mem0-letta/>, <https://callsphere.ai/blog/td30-fw-zep-2-0-graphiti-temporal-knowledge-graph-deep>). Research lineage: arXiv:2501.13956 (94.8% DMR, +18.5% LongMemEval per vendor-tracker summary).
- **Movement signal:** Zep Community Edition deprecated into `legacy/` (no patches) Aug 2026 — migration churn toward Mem0/Graphiti/Letta (<https://www.agenticwire.news/article/zep-ce-deprecated-migrate-mem0-graphiti-letta>). Self-hosted Graphiti needs you to run Neo4j/FalkorDB yourself.
- **Delta vs spine:** this is our **closest neighbor on bi-temporality** — they own "when was this true." They do not own: typed propositions (edges are extracted facts, not schema-typed claims), evidence independence groups (contradiction handling = invalidate-on-newer, not source-aware adjudication), computed capability (graph answers what happened, not what the system can do), or non-compensatory applicability (retrieval is relevance-ranked; no hard gates). Also single-tenant semantics — FedWorld-style scoping (arXiv:2608.01561) has no analogue shipped.

### HippoRAG 2
- Ships: research framework — KG + Personalized PageRank over a hippocampus-inspired index; outperforms standard RAG on factual/sense-making/associative tasks (arXiv:2502.14802, <https://arxiv.org/abs/2502.14802>; code <https://github.com/OSU-NLP-Group/HippoRAG>; PyPI `hipporag` 2.0.0a4 alpha, Jun 2025 <https://pypi.org/project/hipporag/>).
- Third-party maturity read: "Not directly [production-ready], not yet… research framework" (<https://blog.nicolasmeridjen.com/en/blog/2026-04-13-context-engineering-agent-memory-changes-everything/>).
- **Delta vs spine:** associative retrieval quality, zero trust semantics. Not productized; no lifecycle, no verification.

### AWM (Agent Workflow Memory)
- Ships: method, not product — induces reusable workflows from trajectories, recalls them to guide later tasks; ICML 2025; +24.6%/+51.1% relative success on Mind2Web/WebArena (arXiv:2409.07429 <https://arxiv.org/abs/2409.07429>; proceedings <https://proceedings.mlr.press/v267/wang25bx>; code <https://github.com/zorazrw/agent-workflow-memory>).
- The production gap around it is documented: "procedural drift" — stale or wrong workflow recalled because selection is semantic-relevance only; practitioners bolt on freshness windows, TaskCompletion≥0.8 induction filters, quarantine flows via observability tooling (<https://futureagi.com/glossary/agent-workflow-memory/>). ICML reviews flagged underspecified induction and missing management policy (<https://openreview.net/forum?id=NTAhi2JEEE>).
- **Delta vs spine:** AWM is the existence proof that procedural memory pays — and that its failure mode (unverified reuse) is exactly what our evidence-gated, execution-resolved procedures eliminate. Nobody ships AWM-with-applicability-gates as a product.

## Synthesis: where the moat actually sits

| Spine element | Closest competitor | Shipped? |
|---|---|---|
| Bi-temporal | Graphiti edges | Yes — but fact-validity only |
| Typed propositions | none | No |
| Evidence independence groups | none (Graphiti invalidates; FedWorld counts peer evidence, paper only) | No |
| Computed capability | none | No |
| Non-compensatory applicability | observability bolt-ons around AWM | No |

Market context worth keeping: memory layers are consolidating into platform infrastructure (AWS exclusive pick = Mem0; Zep CE sunset), so the wedge must be trust semantics vendors can't retrofit cheaply — execution-based verification and hard applicability gates change the write path, not just the read path.

## Board deltas

1. Positioning line for decks: "Graphiti knows when facts were true. We know whether a procedure still works — proven by execution, gated non-compensatorily."
2. Watch item: Mem0's AWS exclusivity blocks the obvious enterprise channel; our wedge should assume Zep/Graphiti-shaped incumbents at prospects, not vector stores.
3. AWM failure modes ("procedural drift", FutureAGI doc) are citable pain-point evidence for RIGHT_FILE_wrong_fix-class pollution.

## Addendum 2026-08-26 — RUN #1 refresh: is stale-procedure-refusal a published metric?

Re-checked during RUN #1 verification (full analysis: `run1-verification.md`). **Still no
published system reports decision-time refusal of a ground-truth-stale OFFERED PROCEDURE
as a named metric** — but the 2026 literature has converged on the problem from four
adjacent directions and must be cited before any novelty claim:

| Work | What it does | What it measures | Gap vs our §40 metric |
|---|---|---|---|
| STALE (arXiv:2605.06527) | 400 implicit-conflict memory scenarios; Premise Resistance = reject stale-presupposing queries | model-level stale detection over personalization FACTS, QA accuracy (best 55.2%) | facts, not procedures; QA framing, not execution loop |
| TEPA (arXiv:2608.07429) | revocable keyed-precedent memory; validity as explicit state; audit-preserving revocation | post-drift retrieval accuracy (0.950 vs 0.210 append-only) | memory-layer mechanism; no agent-facing refusal metric |
| Library Drift + Ratchet (arXiv:2605.19576 / 2605.22148, AWS, FAGEN@ICML 2026) | skill-library lifecycle governance: contribution scores, outcome-driven retirement, eviction bounds | pass@1 + retirement-statistic reliability | library-side retirement policy, not the facing-agent refusal act |
| AFTER (arXiv:2606.23127) | procedural-memory transfer benchmark, 382 enterprise tasks | cross-task/role/model transfer accuracy | no staleness axis at all |

Positioning line update for decks: cite STALE by name — its Premise Resistance is the
field's nearest framing of this capability — and claim the difference precisely:
procedure-level refusals inside an execution loop, graded against withheld staleness
ground truth, with a substrate-audited evidence trail. Also note the verification finding:
in OUR OWN RUN #1 the substrate gate (not the model) performed every recorded stale
refusal — closing that gap (model-decides tier) is what would make our number comparable
to STALE's probe rather than to nobody's.
