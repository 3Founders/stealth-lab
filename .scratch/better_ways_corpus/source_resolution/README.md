# 60-seed source resolution (spec §17)

Records: `seeds.jsonl` — one JSON object per seed, with the §17 fields plus a
`resolution` verdict and a `family`.

**Method.** Every seed was read from the prompt; the concrete artifacts (GitHub
repos, PyPI packages, arXiv IDs) were checked live where a URL was given. Blog /
vendor-doc / brand-only seeds were classified by whether a verifiable primary
artifact exists, not scraped. Nothing was fetched into any canonical table in this
phase. Run date: 2026-09-03.

## Resolution tally

| verdict | count | meaning |
|---|---|---|
| `RESOLVED_VERIFIED` | **21** | primary artifact confirmed live (repo/paper exists, license read) |
| `RESOLVED_UNVERIFIED` | 15 | artifact almost certainly real but not fully confirmed this pass (repo not fetched, or the specific mechanism needs a code read) |
| `ARTICLE_SECONDARY` | 8 | real source but informational (blog / vendor docs / vendor feature) — no runnable artifact; §59 informational path |
| `MODEL_INFERRED` | 2 | the named technique is **not** in the cited source — must not become a source fact (§20) |
| `UNRESOLVED` | 14 | no locatable primary artifact from the seed (brand-only, or "resolve the real source" with none given) |

`21 RESOLVED_VERIFIED + 15 RESOLVED_UNVERIFIED = 36` seeds have a real artifact to
work from. The other 24 are article-only, inferred, or unresolved.

## Verified primary artifacts (the 21)

| seed(s) | artifact | license | kind |
|---|---|---|---|
| 1, 2, 6, 14 | `github.com/zzet/gortex` — Go symbol-graph engine, CLI+MCP, GCX1 wire format | Apache-2.0 | executable |
| 3 | `github.com/Digital-Threads/token-pilot` — AST-aware read compaction MCP server | MIT | executable |
| 4 | `github.com/NousResearch/hermes-agent` — `/compress` + token-aware memory | MIT | executable |
| 19, 29 | `arXiv:2601.08773` — AST-derived KGs beat vector / LLM-graph for code RAG | arXiv | reproducible method |
| 20 | `arXiv:2601.23254` — "Better Call Grep" / GrepRAG, index-free lexical retrieval | arXiv | reproducible method |
| 22 | `github.com/Muvon/octocode` — Rust tree-sitter symbol graph + embeddings + MCP | Apache-2.0 | executable |
| 23 | `github.com/vitali87/code-graph-rag` — tree-sitter → Memgraph KG, NL→Cypher | MIT | executable (needs Memgraph) |
| 27, 28 | `arXiv:2605.04763` — chunking study; Sliding-Window & cAST on the cost/quality frontier | arXiv | reproducible method |
| 36, 38, 45, 46, 47, 60 | `nicolasbustamante.com/blog/long-running-agent-engineering` (+ `github.com/ghuntley/how-to-ralph-wiggum`) — Ralph Loop, Initializer/Worker/Judge, adversarial judge, invariant smoke test, rollback, failure classification | blog text RESTRICTED; guide INFERRED | abstractable procedure |
| 37 | `arXiv:2601.03204` — InfiAgent, file-centric externalized state | arXiv | reproducible method |
| 50 | `github.com/lm-sys/RouteLLM` + `arXiv:2406.18665` — preference-trained routers, ~2× cost saving | Apache-2.0 | executable framework |

## Normalization into procedure families (spec §16)

The 60 seeds collapse to **9 procedure families**. Within each, most seeds are the
same underlying procedure with a different source / an alternative implementation /
extra evidence — not a new Procedure.

| family | seeds | canonical procedure (one per family) | best executable artifact(s) |
|---|---|---|---|
| **Context efficiency** | 1–10 | *Assemble the minimal sufficient code context for a task* (symbol/AST retrieval → build context → answer → verify token delta) | gortex (1/2/6/14), TokenPilot (3), hermes `/compress` (4/7/8) |
| **Tool efficiency** | 11–18 | *Complete a tool-using task with a large catalog while minimizing tool-definition + tool-output tokens* (lazy/searchable tools + output compression) | gortex GCX1 (14), codegraph-mcp lazy load (12/18), StealthLab deferred tools (native) |
| **Repository intelligence** | 19–28 | *Answer a repo-structure question (callers of X, where is Y) without sending the repo to the model* (build a code KG → query it) | octocode (22), code-graph-rag (23), AST-KG paper (19/29), GrepRAG (20), cAST (27/28) |
| **Deterministic execution** | 29–35 | *Replace an LLM call with a deterministic pass where a compiler / parser / LSP / codemod can prove the result* | AST-KG (29), LSP go-to-def (33), + unresolved codemod/static-check artifacts |
| **Long-running execution** | 36–42 | *Complete a long-horizon task without accumulating conversational history* (fresh sessions + externalized file state + verifiable completion) | Bustamante/Ralph (36/38), InfiAgent (37), StealthLab durable-run (native) |
| **Verification & reliability** | 43–48 | *Independently verify that an execution actually solved the problem* (deterministic checks first, then an independent adversarial judge) | Bustamante adversarial judge (45/46/47), cross-model review (44), StealthLab evidence.py (native) |
| **Model routing** | 49–54 | *Route each step to the cheapest model that can do it, escalating on low confidence* | RouteLLM (50/51), cascade/FrugalGPT class (49/52 — resolve citation), planner/executor split (53) |
| **Parallelism** | 55–58 | *Run parallel coding agents without merge conflicts* (git-worktree isolation + disjoint file sets; quorum on fan-in) | git-worktree (55, StealthLab native), fan-out/fan-in (56), disjoint sets (57) |
| **Failure recovery** | 59–60 | *Detect a stalled/failed run and recover* (stall detection → reset; classify failure → retry / rollback / escalate) | Bustamante failure classification (60), StealthLab RETRYABLE_ERROR_CLASSES + durable retry (native) |

**Consequence for canonical ingest:** ~9 canonical Procedures (one per family), each
carrying multiple source references, Implementations, and Evidence rows — not 60
near-duplicate procedures (spec §16, and the prior wave's own conclusion).

## Ingest / execution eligibility

- **Ingestable now** (real artifact + clear procedure): the 36 `RESOLVED_*` seeds, folded
  into the 9 families.
- **License-safe to abstract + cite** but NOT redistribute text: all `ARTICLE_SECONDARY`
  + the Bustamante blog seeds (§67).
- **Not ingestable as a source fact**: seeds 10, 15 (`MODEL_INFERRED`) — the technique
  isn't in the cited artifact.
- **Blocked on source** pending research: the 14 `UNRESOLVED` seeds — recorded, not
  invented (§77). Several (44, 49, 52, 55, 57, 59) describe real, well-known techniques
  whose *procedure* is abstractable even though the seed's named source doesn't resolve;
  those become procedures with a "needs a firmer citation" note rather than admitted-as-verified.
