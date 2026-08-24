# EXEC — Sentry-for-Agents: Vendor Landscape, Legal/Ethical Playbook, Research Base

Date: 2026-08-24 · Method: websearch (market/vendor/pain) + arXiv API (credibility checks), synthesized.

---

## 1. Context

StealthLab MCP releases an "earned memory" layer for coding agents: ingest traces → distill evidence-backed procedures → refuse stale reuse provably. The monitoring ask ("Sentry-level tracking of agentic failures") needs (a) a vendor map of who already plays here, (b) how their architecture works, (c) the legal/ethical rules of the telemetry road, (d) the research that de-risks our classifier/attribution design.

## 2. Vendor landscape (the space is real, funded, consolidating)

Market size: LLM observability ≈ $1.97B (2025) → $6.8B by 2029 (36.5% CAGR). 89% of agent teams already run observability, yet 1-in-3 still name quality their #1 production blocker — the category records *what happened*, not *whether it was okay*.

| Vendor | Status/funding | Model | License / self-host |
|---|---|---|---|
| Braintrust | $120M raised, ~$800M val | Eval-first: datasets/scorers/experiments | Closed |
| LangSmith (LangChain) | ~$125M | Managed polish, LangGraph-native tracing | Closed |
| Langfuse | Acquired by ClickHouse (Jan 2026) | OSS traces + prompts + evals | MIT-ish core, self-hostable |
| Helicone | ~$10M, YC; maintenance mode post Mintlify-acq (Mar 2026) | Lossless LLM gateway/proxy logging | Apache-2.0 |
| AgentOps | $2.6M seed | **Agent-first**, session replay, cost tracking | **MIT full stack** (SDK+dashboard+API backend) |
| Arize Phoenix | part of Arize (~$100M+) | OTel + OpenInference tracing/evals | ELv2 source-available |
| Galileo | ~$45M; Cisco acq. announced Apr 2026 → Splunk AI Agent Monitoring | Luna-2 fine-tuned judge evals (40× cheaper than GPT-judge) | Closed, VPC/on-prem enterprise |
| OpenLLMetry / Langtrace / SigNoz | OSS | OTel span emitters into any APM | Apache-2.0 / AGPL / MIT |
| Datadog LLM Obs, Portkey | big-co | Gateway/APM-attached LLM tracing | Closed |

Signals: two acquisitions in two months (Galileo→Cisco, Helicone→Mintlify) = consolidation; deep instrumentation costs ≈12% latency; common serious-team pattern = cheap always-on proxy + selective deep platform. Nobody ships: real-time per-turn verdicts, or failure→memory feedback loops.

**Gap we occupy:** all twelve record and replay. None (i) attribute failure back into a verified knowledge graph, (ii) degrade capability scores on evidence, (iii) make the agent refuse reuse afterward. Our monitoring is not a dashboard bolt-on — it is the learning input (spec §36).

## 3. How they work (mechanics worth copying)

1. **OTel-native spans**: GenAI semantic conventions (LLM call / tool call / retrieval / agent-step spans with parents). Adopt — vendor-neutral export keeps us out of lock-in fights and satisfies procurement.
2. **Session replay** (AgentOps' differentiator): reconstruct full agent run from stored events — we get this free from the immutable trace/event tables (spec §4).
3. **Issue grouping**: Sentry-style fingerprints; nobody in the LLM cohort does semantic grouping well — our claim-family machinery is the novel edge ("failure families").
4. **Evals-as-pipeline** (Braintrust/Galileo): versioned scorers over sampled production traffic. Maps to our Layer-2 empirical replay eval (currently unwired).
5. **Fine-tuned small judges** (Galileo Luna-2): ~40× cheaper, ~21× faster than GPT-as-judge at comparable accuracy — validates our plan for rule-based classifier v0 → distilled SLM judge later.
6. **Day-one instrumentation checklist** (industry consensus): every LLM call, tool call, retrieval, user-visible error/rejection, feedback signal.

## 4. Legal learnings (mostly from Sentry's paper trail)

- **GDPR exposure is structural**: error/failure states are where PII leaks (stack traces, request bodies, local variables). Transfers to US processors trigger Art. 44; DPF certification doesn't neutralize CLOUD Act risk (Schrems III pending). Buyers increasingly want EU-sovereign/self-host options — our local-first Postgres default is a compliance feature; say so explicitly.
- **Art. 25 data-protection-by-design**: scrubbing must be *before capture/transmission* (`beforeSend` pattern), not just server-side. Our `trace_redaction` must run inside ingestion, before persistence — and be documented as such.
- **Subprocessor hygiene**: DPAs, RoPA (Art. 30), privacy-policy disclosure lists. If we ever host, publish subprocessor list day one.
- **Retention minimization**: offer 7/30-day tiers rather than 90-day defaults; deletion must propagate everywhere including derived stores.
- **The Sentry AI-TOS moment (Jan 2024)**: backlash forced them to publish five explicit commitments — (1) encourage pre-send scrubbing, (2) deletion rules apply to training data too, (3) PII scrubbed before training, (4) outputs only expose the customer's own data, (5) in-house/trusted-subprocessor models only. **Copy this verbatim as our AI-data policy** before anyone asks.
- **Precedent for MCP governance**: Mandato (arXiv 2608.14074) maps signed mandates + hash-chained audit logs on MCP actions onto EU AI Act Art. 12/14, NIS2, eIDAS-2 — legal-institution framing (delegation-of-authority) makes artifacts legible to auditors/lawyers. Our receipts should borrow this vocabulary (who authorized, what scope, what evidence, hash-chained).
- NIS2 treats monitoring vendors as supply-chain security surface — expect security questionnaires early; SOC 2 is table stakes, AI-governance documentation is the differentiator (matches earlier 96%-explainability procurement finding).

## 5. Ethical playbook for telemetry in OSS (the community has settled much of this)

Settled norms (Go's transparent-telemetry saga ended opt-IN Feb 2023; LF best-practice guidance; HN/reddit consensus; VS Code/Next.js docs):

1. **Local-first by default; hosted anything strictly opt-in.** Our architecture already enforces this — state it as principle #1.
2. **Publish a Data Manifesto** in README: what's collected, what's never collected (IPs, usernames, paths, file contents, user content, unique IDs), retention, no-sale commitment, one-command opt-out, source-link for verification.
3. **`--debug-telemetry` transparency flag**: print exactly what would be sent. Transparency as a feature, not shame.
4. **Never train on user data without explicit consent** — and if ever offered, mirror Sentry's five commitments.
5. **Aggregate/batch client-side; consider differential-privacy noise on sensitive counts; fail silently when the telemetry path breaks** (never break the product to phone home).
6. **Redaction-before-export** (already built: `trace_redaction`) is both ethics and law — wire it as the single choke point.
7. Shared-failure corpora (à la AgentDebugX's opt-in "Error Hub") are legitimate and valuable **only** as scrubbed, consented bundles. Their design: share diagnosis-repair bundles, never raw traces.

## 6. Research literature de-risking our build (all 2026, arXiv)

| Paper | Finding we exploit |
|---|---|
| **MAST-lineage + Who&When benchmark** | Failure attribution task formalized: given failed trajectory → faulty agent + step + error type. Use Who&When to calibrate our classifier. |
| **AFANet** (2608.18575) | Lightweight GNN on step-level semantics matches LLM-based attribution at near-zero cost → our fingerprint/classifier can be tiny + local. |
| **SearchAuditBench / SearchAuditor** (2608.05212) | 1,243 expert-annotated failed trajectories (critical step, root cause, repair rubric); frontier models only ~26.6% end-to-end → attribution is hard; benchmark available for our evals. |
| **SAFARI** (2606.24626) | Tool-augmented investigation beats context-stuffing on long traces (+20% Who&When) → our diagnosis tools should search the graph, not dump it into a prompt. |
| **AgentDebugX** (2607.18754) | Open-source Detect→Attribute→Recover→Rerun loop; opt-in Error Hub shares *scrubbed* diagnosis bundles as debugging memory — direct architectural precedent + prior art to cite. |
| **SkillTriage / Skills-can-be-harmful** (2608.11888) | Differential analysis attributes failures/cost-regressions to specific skills; biggest class = "Excessive Procedure" (over-verification turned into mandatory work) → validates capability decay + refusal design; gives taxonomy for procedure-caused failures. |
| **StateMAS/MARS** (2607.29055) | 1,310 replayable multi-agent failure trajectories; taxonomy-guided repair evaluation → replay-based regression tests for our TMS. |
| **FORGE'26 16-class taxonomy** (earlier wave) | Fine-grained reasoning-failure labels for classifier v1. |
| **Veracium** (earlier wave) | Provenance-typed graphs win long-horizon recall + injection resistance → provenance fields are also a *security* control, not just bookkeeping. |

## 7. Design implications — StealthLab failure-telemetry v1

```
capture:   MCPServer middleware wraps every tool call → immutable Event rows
           (args-shape, latency, outcome, tokens); hook-ingested external traces join same pipeline
redact:    trace_redaction runs BEFORE persistence (single choke point, Art. 25 style)
classify:  v0 rules → spec §36 six causes + false-reuse + skill-induced classes
           (SkillTriage taxonomy); fingerprints = hash(tool_id, step_kind,
           normalized-arg-shape, env) grouped into failure families (claim-family reuse)
attribute: graph-search diagnostic loop (SAFARI-style), never full-context dumps;
           GNN/lightweight scorer viable long-term (AFANet)
act:       failure → Evidence(contradicts) → belief/capability drop → dependents re-checked
           → next check_procedure refuses with cited degradation history
export:    OTLP GenAI semconv spans (any APM); optional literal-Sentry sink; Grafana JSON shipped
alerts:    capability<threshold · new-family spike · stale-procedure attempt · propagation-lag growth
policy:    Data Manifesto + --debug-telemetry + opt-in-only hosted mode + Sentry-style five commitments
```

## 8. Locked decision defaults

1. Local-first storage; hosted telemetry opt-in only. 2. Redact-before-persist. 3. Publish Data Manifesto + subprocessor/AI-policy page at release. 4. Apache-2.0 + SECURITY.md + threat-model doc. 5. Attribution benchmarks (Who&When, SearchAuditBench subset) adopted as standing evals alongside ripple/MQuAKE receipts. 6. Never train on user traces; if product changes, publish Sentry-style commitments first.

## Sources (abridged)
morphllm.com 12-platform comparison (Jul 2026) · presenc.ai startup matrix (May 2026) · galileo.ai platform guides · web3aiblog June-2026 scoreboard · sentry.io/security, /privacy, blog "AI, Privacy and ToS Updates" (Jan 2024) · sota.io GDPR/CLOUD-Act analysis (May 2026) · complydog.com Sentry-GDPR · research.swtch.com/telemetry (Go) · linuxfoundation.org telemetry guidance · code.visualstudio.com telemetry docs · notesnook.com opt-in post · 1984.vc OSS-telemetry handbook · news.ycombinator.com item 39431943 · arXiv: 2608.14074, 2608.18575, 2608.11888, 2608.05212, 2607.29055, 2607.18754, 2606.24626.
