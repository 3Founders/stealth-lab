# Solution Statement

## One paragraph (all-encompassing, plain language)

We give AI agents a shared library of how-to knowledge that comes with proof it
works. Everything an agent does and learns gets recorded; approaches that keep
succeeding are written up as clear step-by-step procedures and tested on real
tasks before entering the library; every reuse updates a public track record;
procedures that stop working lose trust and get retired automatically; and every
entry carries its receipts — source, applicability conditions, what superseded
it, outcome history — so agents see current truth instead of stale leftovers,
compare conflicting lessons on evidence instead of guessing, trace any failure
to the exact knowledge behind it, and survive model or vendor changes because
everything lives in open formats any agent already speaks.

## Three-layer representation

```
Layer 1 — CLAIMS      atomic statements with provenance, sources,
                      bi-temporal validity (stated-at / valid-at),
                      truth states (IN / OUT / superseded)
Layer 2 — PROCEDURES  executable skills compiled from verified claims:
                      preconditions, typed tool bindings, numeric
                      invariants, termination conditions
Layer 3 — EVIDENCE    immutable outcome records per procedure
                      (attempts/successes/model-mix/last-failure),
                      statistical verification gate, lifecycle engine
                      (promote → supersede → retire)
```

Distributed through existing standards (`SKILL.md` over MCP); verified through
the layer nobody has built (Layer 3).

## Compressed framings

- Primary: "Peer review + a public journal for what AI agents learn — tested
  before publication, cited when reused, retracted when stale."
- Ultra-short: "A peer-reviewed journal for agent procedures — globally indexed."
- Technical: "npm + peer review for agent skills."
- Why-now pairing: "The printing press for agent knowledge shipped last fall.
  Nobody has built the peer review yet."

## Design choices with evidence (all fetched/read)

| Choice | Evidence |
|---|---|
| Structured graphs > flat chunks | Zep temporal KG beats MemGPT 94.8 v 93.4 DMR, +18.5% LongMemEval (arXiv:2501.13956) |
| Supersession/temporal edges | KG beats vector RAG by 70% on CFR; solves temporal hallucination (arXiv:2604.14220) |
| Hybrid symbolic+text grounding | Ontology-KG 90% vs vector-RAG 60% (arXiv:2511.05991); vector RAG 0% on schema-bound queries (FalkorDB/Diffbot) |
| Verification gate before reuse | Skill-Pro PPO Gate (arXiv:2602.01869, ICML'26); MACLA Bayesian reliability (arXiv:2512.18950) |
| Lifecycle/pruning, not append-only | ReMe passive-accumulation critique (arXiv:2512.10696); stability-plasticity resurfaces at memory level (arXiv:2604.27003) |
| Multi-source synthesis | Multi-model-trace skills hit 73.1% cross-model accuracy (AFTER, arXiv:2606.23127) |
| Retention as core question | Weighted Memory Tree: "deciding which information remains active" (arXiv:2608.20631) |
| Ride existing rails | agentskills.io (~40 clients); MCP everywhere |

## Build / borrow / ride toolchain

- **Ride:** SKILL.md import-export, MCP transport, OpenTelemetry telemetry, Postgres/pgvector
- **Borrow:** Sigstore/cosign signing (tamper-evident evidence), OCI-style content-addressed registry, progressive-disclosure patterns
- **Build (the product):** Procedure CI (generalize our two benchmark harnesses), outcome-telemetry SDK (step-tracker prototype exists), evidence-record schema, lifecycle engine, public read API

## Pre-empted objection

"Isn't this the Semantic Web again?" — Prior failures (Semantic Web, Cyc,
expert systems) died because humans had to write, populate, and maintain
knowledge forever, and no reader came. All three conditions reversed since
2024: agents generate content as a byproduct of work, agents are the consumer,
and upkeep is statistical automation. An agent writing to an uncorrectable
model would repeat the 2005 mistake — which is why human-reviewable
provenance, approval gates, and editable entries are structural, not optional.
