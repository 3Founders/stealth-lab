# researchtechnical.md — Technical Research Queue

**Protocol:** points are researched one at a time on demand. When Chaitanya says
**"next"**, the next unchecked point is researched via **Exa (web)** + **OpenAlex
(scholarly API)**; findings are appended inline under that point (dated), status
flips `[pending]` → `[researched @date]`, and the pointer advances. Findings must
name what changes in StealthLab (system refs S1–S10 per Roadmap1.md), not just
summarize papers.

---

## RQ1 · Verification-gated SLM routing [pending]
*Systems: S3 execution.* How should node selection in an execution graph decide
SLM-vs-frontier per node? Known frame: route SLM iff verification ≪ generation
cost; expected-cost form `p_fail × damage + verify_cost < frontier_cost`; sequence
reliability compounds (gate placement at fan-outs); D1 tiers as per-node thresholds.
**Find:** measured-capability routing literature, cascades/routing papers (FrugalGPT
lineage), verification-cost asymmetry results, compound-reliability gate placement.

## RQ2 · Skill-distillation method landscape [pending]
*Systems: S6.* Deep-dive beyond abstracts: Skill-DisCo PFSM subgraph mining,
Trace2Skill parallel-patch consolidation, SKILL-KD contrastive patches, Memp repo
regimens. **Find:** which consolidation signals prevent skill drift; granularity
evidence (subgoal-level beats task-level?); gold-set evaluation designs we can copy.

## RQ3 · Memory validity & revocation semantics [pending]
*Systems: S4.* TEPA keyed-precedent lifecycle (hypothesis→active→revoked→archive);
Quipu signed denial verdicts; Kumiho AGM correspondence. **Find:** formal treatments
of revocation vs tombstoning; re-promotion rules; conflict-key derivation methods.

## RQ4 · Governed write-gates for agent-written knowledge [pending]
*Systems: S1, S9.* Gate-before-write stores, refusal-driven convergence ("agents
bear strictness's cost"), audit-as-query, signed verdicts. **Find:** gate predicate
languages, convergence measurements, coverage-gap risk (Quipu's residual-risk note),
portable governed-store contracts (GS1–GS6).

## RQ5 · Refusal calibration & evidence sufficiency [pending]
*Systems: S5.* Calibrated sufficiency vs threshold gating (hallucination-proxy
77.8→37.5% result), confidence-budget interfaces (AB-RAG, Know-Before-You-Fetch).
**Find:** calibration methods applicable without reader logits (we refuse on graph
facts, not token probs); selective-prediction metrics (risk–coverage) for
WOULD_REFUSE quality.

## RQ6 · Bi-temporal modeling & provenance lineage [pending]
*Systems: S4, S9.* OpenAlex anchors: Temporal Data Management overview (2018),
TraceGraph (2026). **Find:** bitemporal key design patterns in production DBs,
valid-time vs transaction-time query idioms in Postgres, lineage systems we can
borrow receipt vocabulary from.

## RQ7 · Agentic failure attribution [pending]
*Systems: S7, S10 (explain_failure).* Who&When, SearchAuditBench (~26.6% frontier
localize+repair), SAFARI tool-augmented investigation, AgentTrace causal-graph
tracing, AFANet GNN classifier. **Find:** cheapest deployable attribution pipeline;
classifier training-data requirements; sub-second backward-walk implementations.

## RQ8 · TMS ripple-effect measurement [pending]
*Systems: S4.* RippleEdits 6 criteria → claim-graph adaptation; MQuAKE multi-hop;
CODE epistemic-dissonance (rationale-grounded overwrites). **Find:** propagation %
benchmarks others report; dependency-fan-out cost bounds; scheduling of re-checks.

## RQ9 · Cross-model capability transfer [pending]
*Systems: S6, S3.* AFTER 73.1% cross-model accuracy; Trace2Skill scale transfer;
SKILL-KD frozen-student gains. **Find:** when does pooling help vs hurt; role-
specialization failures; sample-size floors before a procedure is trusted cross-model.

## RQ10 · Embedding migration & drift [pending]
*Systems: S8.* Model-migration stamps exist (migration 21). **Find:** re-embedding
campaign practice, drift detection between stored/current vectors, matryoshka
truncation trade-offs, HNSW rebuild costs at our scale (~700 procedures → 100k+).

## RQ11 · Ingestion & observability at scale [pending]
*Systems: S7.* OTel GenAI semantic conventions, trace-storage retention practice,
backpressure patterns. **Find:** drop-counter/queue telemetry norms, partition/TTL
patterns for event tables, multi-agent session correlation schemes.

## RQ12 · Over-procedure risk & ceremony cost [pending]
*Systems: S6, S5.* SkillTriage top class = Excessive Procedure; phaseO collapse
(0.0155 avg — alarm stands). **Find:** ceremony-cost quantification methods,
refuse-cheaply design patterns, post-mortems of skill-library degradation.

## RQ13 · Deletion mechanics: crypto-shredding [pending]
*Systems: S9 (Band 5, D4-ratified).* Shredding w/ visible shells; GDPR Art.17 in
append-only stores. **Find:** envelope-key per-row/per-scope shredding practice,
key-custody models, interaction with bi-temporal history and receipts.

## RQ14 · Cheapest-capable routing economics [pending]
*Systems: S2, S3.* §23 utility formula; D1 tiers. **Find:** price/perf frontier
data across SLM/frontier tiers, utility-accounting precedents, retirement-window
designs (trailing ≥10 attempts rule sanity-check).

## RQ15 · MCP ecosystem & distribution surface [pending]
*Systems: S10.* SEP-2663 tasks extension, registries (Smithery/Glama/PulseMCP),
NVIDIA verified-skills signing. **Find:** conformance-test suites, security review
norms for MCP servers, registry submission requirements.
