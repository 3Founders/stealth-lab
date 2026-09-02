# commLLM.md — Consolidated Reference: Verified Procedural Experience System (StealthLab)

Last updated: 2026-09-02 · Companion to: `verified_procedural_experience_system_ideal_specification_v4.md`, `ARCHITECTURE.md`, `Exec.md`

> Provenance note: the 2026-08-23 QuantAsm↔StealthLab session summary that
> formerly filled this file now lives at
> `.scratch/archive/quantasm-integration-session-2026-08-23.md`. Its Week-1
> "backfill runs/*.jsonl" step is superseded by ROADMAP's fresh-start
> ruling (no backfills; QuantAsm rollouts will enter via `/v1/traces` → V0
> gate with QuantAsm provenance, like any other producer).

**What we're building:** a local-first MCP server that gives coding agents *earned memory* — ingests their work traces, distills them into evidence-backed procedures over a bi-temporal claims graph, and **refuses reuse whose preconditions no longer hold, with provable reasons**. North star: *"Agents that earn the right to remember."* Nobody in academia or the market ships failure→belief-revision→capability-decay→refusal as a closed loop.

---

## 1 · System layers & MCP tool surface (v0.1)

Layers (spec §2): Events/Traces/Episodes → Observations → Claims/State/Graph → Procedures (+verification) → Capability/Applicability → Execution/Planner → Applications.

| Tool | Status | Purpose |
|---|---|---|
| `retrieve_precedent` | built | prior solved patterns (hybrid RRF + graph expansion) |
| `ingest_trace` | wrap `/v1/traces` | agent-agnostic capture (Claude Code hooks live) |
| `check_procedure` | built | applicability + preconditions → allow/**structured refusal** |
| `explain_decision` | **new** | provenance receipt from graph |
| `explain_failure` | **new** | execution-graph backward tracing → cause chain w/ beliefs. **Hard dependency: Band 1.7 plan/execution persistence** (migration 23 — executions bound to exact frozen plans); nothing to walk until that lands |
| `detect_conflict_trigger`, `propose_synthesis`, `submit_approval` | built | debate-gated knowledge change loop |
| `find_best_way` | built | retrieval-grounded coding agent (RepoSandbox) |
| ~~`apply_change_set` ungated~~ | gate behind opt-in flag | raw write primitive must not ship public |

Transport: stdio + Streamable HTTP (`mcp==2.0.0` `MCPServer`, hand-built SEP-2663 Tasks extension for long-running tools). Bearer token = authentication only; threat model documented; loopback-first posture.

**Final-V1 update (2026-09-03):** the MCP surface is **28 registered tools**. Added since the note below: the six read-only product-model tools (`find_problem`, `inspect_problem`, `list_problem_solutions`, `compare_solutions`, `inspect_evaluation`, `find_best_solution`) and `get_claim_graph`. `Problem / Benchmark / Solution / Evaluation` (spec §11–§19) is now **shipped** — migration 35, `app/services/product_model.py`, `/v1` REST in `app/api/problems.py`, MCP tools above — not a future layer; a Problem's current-best Solution is derived on read from a Wilson lower bound over completed-Evaluation lineage, never stored. `find_best_way` tier-2 and `reproduce_procedure` now execute on a **durable execution run** (`execution_runs` / `execution_run_nodes`, migrations 36–37) and **resume after a crash** (`find_best_way(resume_run_id=…)`) — completed nodes are not re-run, terminal state is fenced, concurrent resume is refused. Execution now has a canonical **execution descriptor** (`GET /v1/implementations/{id}/descriptor`; MCP `inspect_implementation` / `resolve_implementation`). Ingestion hardening: ChatGPT-export branch-tree reconstruction (abandoned siblings no longer contaminate evidence, §28) and untrusted-document-as-data injection defense in `skill_ingestion.py` (§29). Full account: `docs/final-v1.md`. The V1 product UI is `frontendv1/` (Next.js 16, now tracked, benchmark-first pages + 13-tool WebMCP bridge) — the older `frontend/` is not the V1 surface.

**Verified 2026-09-01 against `backend/app/mcp_server/server.py`'s live registry** (`packaging/tests/test_server_offline.py::test_all_registered_tools`): the table above is the original v0.1 slice and is no longer the full surface. 20 tools are registered today. Landed since, not reflected above: `check_applicability`, `decide_procedure`, `get_procedure`, `report_execution`, `reproduce_procedure`, `search_procedures`, `submit_procedure`, and a new **Implementation Registry** group (`get_implementation_capability`, `inspect_implementation`, `list_task_implementations`, `resolve_implementation` — durable implementation identity, migration 33). A companion REST layer also now exists outside the MCP surface, wired in `backend/app/main.py`: `/v1/claims`, `/v1/procedures`, `/v1/solutions`, `/v1/repositories`, `/v1/projects`, `/v1/tasks`, `/v1/me`, `/v1/search`, `/v1/implementations` (read-only), plus one write endpoint, `POST /v1/admin/failure-routes/process` (2026-09-02, `app/api/admin.py`) -- a real production consumer for `fetch_route_queue()`, not read-only. Landed the same pass, not new tools/endpoints but real wiring fixes: the Implementation Registry (`resolve_implementation` et al.) now resolves+binds inside `find_best_way`/`reproduce_procedure`'s actual hot path rather than sitting only as standalone reads; multi-episode generalization (`synthesize_procedure`) has a real production caller via ingestion auto-discovery, no longer requiring a caller to hand-pick `episode_ids`; the publish-time privacy scrub covers `preconditions`/`scope`/`exclusions` and absolute filesystem paths, not just `name`/`goal`/`steps`; and an unscoped IDOR on `GET /v1/agent-store/{agent_id}` is fixed.

## 2 · Frameworks & standards

- **Model Context Protocol**: spec 2026-06-28 era, Python SDK `mcp==2.0.0` (`MCPServer`, constructor-injected `on_list_tools`/`on_call_tool`; bundled FastMCP removed). Official registry + Smithery/Glama/PulseMCP directories for distribution. *Pins are informational; `backend/requirements.txt` is the source of truth — re-verify SDK/SEP pins at every release cut.*
- **OpenTelemetry GenAI semantic conventions**: span types for LLM call / tool call / retrieval / agent step — vendor-neutral export into Foundry/Datadog/Langfuse sinks.
- **agentskills.io open standard** (30+ adopters) + **NVIDIA verified-skills** pipeline (SkillSpector risk scanning, detached crypto signatures, machine-readable SKILLCARD.yaml) — trust-layer rails we ride, not rebuild.
- **Claude Code hooks** (hook_wrapper.py) — primary ingestion source; Zep/Codex/Cursor plugins prove multi-client demand.
- Stack: FastAPI, Postgres + pgvector (HNSW cosine), bi-temporal columns (`t_valid/t_invalid`) enabling index-backed leave-one-out evals, uvx-runnable package, docker-compose up.

## 3 · Research base (papers that de-risk each layer)

### A · Failure attribution & agentic observability
- **Who&When** — first MAS failure-attribution benchmark (faulty agent + step); our classifier's calibration set.
- **SearchAuditBench / SearchAuditor** (arXiv:2608.05212) — 1,243 expert-annotated failed long-horizon trajectories; frontier models only ≈26.6% end-to-end localize+repair ⇒ attribution is genuinely hard; standing eval.
- **SAFARI** (2606.24626, ICML AIWILD'26) — tool-augmented investigation beats full-context diagnosis (+20% Who&When; works 5× beyond context window) ⇒ search the graph, never dump it.
- **AFANet** (2608.18575) — lightweight GNN on step-level semantics matches LLM-based attribution at near-zero cost ⇒ local classifier viable without GPU.
- **AgentDebugX** (2607.18754) — Detect→Attribute→Recover→Rerun toolkit; best strict attribution on Who&When; opt-in "Error Hub" shares *scrubbed* diagnosis bundles as debugging memory (privacy precedent).
- **Stalled/Biased/Confused** (FORGE '26, doi:10.1145/3793655.3793732) — 48k scenarios; 16-class reasoning-failure taxonomy; Location/Type/Hypothesis accuracy metrics.
- **AgentTrace** (2603.14688, ICLR'26 AIWILD) — causal-graph tracing from execution logs; backward tracing + structural/positional ranking, sub-second, no debug-time LLM; beats heuristic AND LLM baselines ⇒ blueprint for `explain_failure`.
- **Error Trace Regression** (ICML '26) — statistically principled root-cause-step estimator.
- **Graph of Trace** (ACL '26 demo) — live DAG visualization of agent runs; experts prefer graphs over linear traces.
- **causetrace** (OSS GitHub) — coding-agent causal-tree observability; demand validation + prior-art map.

### B · Memory verification & truth maintenance
- **RippleEdits** (arXiv:2307.12976; TACL 2024) — 5K edits × 6 ripple-effect criteria; parametric editors fail consistency ⇒ adapted to claim-graph edits = our headline receipt.
- **MQuAKE-CF/T** (2305.14795, EMNLP'23) — multi-hop consequences of edits; external memory (MeLLo) catastrophically outperforms weight editing ⇒ externalist thesis has benchmark-shaped proof.
- **CODE / Epistemic Dissonance** (2605.28303) — naive fact overwrite → 95.6% self-refutation; causal-narrative grounding collapses it to ~2–7% ⇒ changesets must carry rationale.
- **ForgetBench** (2607.26455) — retention decay under sequential editing ⇒ motivates revalidation scheduling (spec §37).
- **Ground Truth First / Veracium** (2607.21962) — longitudinal memory eval; rankings invert at 9 weeks; **provenance-typed graphs rise to 90% while curated stores fall 96→72%**; weak writes fail 24% vs 2%; injection resistance tracked provenance-boundary survival ⇒ provenance is a security control too.
- **D²ACCI** (2608.17756) — diagnostic-gated iteration for memory pipelines; DCR localizability metric (98–100% vs 0% result-only) ⇒ adopt DCR internally; 90.93% LongMemEval reference point.
- Surveys: **From Storage to Experience** (2605.06716, ACL'26 Findings; Storage→Reflection→Experience evolution), rate–distortion compaction (2607.08032), privacy of agents (2606.26627; information-flow control covers compositional leakage), license/sustainability risk (2606.24896; 46% single-vendor VC-backed infra had adverse events vs 2.5% foundation-governed ⇒ governance posture matters).

### C · Procedural memory & skills
- **AFTER** (2606.23127) — 382 enterprise tasks/22 skills; one refinement round = +3.7–6.7pt; skills evolved from **multi-model traces hit 73.1% cross-model accuracy**, beating any single-model source; some skills role-specialize and fail transfer ⇒ capability boundaries are real (§16).
- **Agent Skills Can Be Harmful / SkillTriage** (2608.11888) — differential analysis attributes failures/cost regressions to specific skills; top class = **Excessive Procedure** (over-verification as mandatory work) ⇒ refuse cheaply, add no ceremony.
- **SkillLens Visual Skill Cards** (2608.10775) — independently converges on our triad: procedures bound to **applicability cues + evidence + verification signals**.
- Adjacent substrates: SIGA adapters (2606.09774), Neural Procedural Memory steering (2606.29824), PMD distillation (2607.01480), SESA co-evolution (2607.29468), DuoMem on-device (2606.29961).

### D · Root-cause analysis & grounding (adjacent market proof)
- **OpenRCA 2.0 / PAVE** (2606.27154) — process-level causal-path grading; frontier agents: exact root-cause 20.7%, correct-service 76%, grounded-path only 61.5% ⇒ "ungrounded diagnosis" is THE failure mode.
- **ORCA-bench** (2607.28545) — production-fidelity oncall testbed; best frontier agent 25.3% medium / 10% hard; hallucinated causes up to 40%.
- **GALA+** (2608.08968, ASE'26) — graph-guided investigation +25pp AC@1; SURE-Score human-aligned eval.
- **eIRWR** (2608.08073) — random-walk localization MRR 0.94, <25ms @17K nodes ⇒ online graph RCA is cheap.
- **Log-Insight** (2607.08529, Huawei production) — neuro-symbolic triage, MRR .79; adoption driven by its **Forensic Evidence section** ("opaque oracle → investigative assistant") ⇒ receipts drive adoption.
- **EvoCause/TeleRCA** (2607.27290) — LLM-offline-refined causal graphs, deterministic transparent prediction at test time (our architecture philosophy).
- **GSAR** (2604.23366) — four-way claim typology (grounded/ungrounded/contradicted/complementary) + evidence-typed weighting for narrative verification.
- **Exathlon** (VLDB'21, PMLR-free ref: proc. VLDB 14(11)) — ED metrics: conciseness, consistency entropy, accuracy-as-predictor.

### E · Governance & legal-tech
- **Mandato** (2608.14074) — signed mandates + hash-chained audit logs on MCP actions; mapped to EU AI Act Art. 12/14, GDPR accountability, NIS2, eIDAS-2 ⇒ borrow vocabulary: receipts as delegation-of-authority artifacts legible to auditors.
- Frameworks in play: EU AI Act, NIST AI RMF, ISO 42001; SOC 2 = baseline, AI-governance layer = differentiator (96% of enterprises rank explainability as #1 provider criterion — HFS Jul 2026, vendor-reported).

## 4 · Benchmarks & datasets (all free)

| Benchmark | Use | Access |
|---|---|---|
| RippleEdits (5K edits) | TMS propagation receipt | github.com/edenbiran/rippleedits |
| MQuAKE-CF/T (9K+1.8K) | multi-hop/temporal propagation | github.com/princeton-nlp/MQuAKE |
| LongMemEval-S (+cleaned) | retrieval, abstention=refusal metric, knowledge-update | HF xiaowu0162/longmemeval-cleaned |
| LoCoMo / MemoryAgentBench | long-dialog memory; conflict resolution | snap-research.github.io/locomo · HF ai-hyz |
| AFTER | procedure transfer & capability boundaries | paper release (2606.23127) |
| Who&When · SearchAuditBench · StateMAS | attribution calibration & replayable failure corpora | via papers above |
| Veracium corpus generator | labeled claims-with-validity for extraction quality | github.com/veracium-ai/Veracium |
| AgentTrace benchmark | causal-graph localization baseline comparison | github (2603.14688) |
| In-repo | SWE-bench Pro 731 + own hook traces + synthetic trajectory library | already integrated |

**Integrity stance:** gold-run gating before any arm spends tokens ([T-11]) and bi-temporal holdout ([T-23]) — our harness hygiene directly answers OpenAI's SWE-bench-Pro audit (~30% broken tasks, endorsement retracted). Never headline numbers from unaudited public splits alone.

## 5 · Competitive landscape (condensed; full detail in Exec.md)

- Market $1.97B→$6.8B by 2029. 89% of agent teams run observability; ⅓ still blocked on quality ⇒ everyone records *what*, nobody verifies *belief*.
- Tracing/graphs-for-debugging is converging fast: Microsoft Foundry (tracing+evals GA spring'26; continuous evaluation of sampled live traffic; Agent Optimizer w/ lineage+rollback; Control Plane fleet view; ROI dashboards), Galileo→Cisco/Splunk, Helicone→Mintlify, Langfuse→ClickHouse, causetrace OSS. Foundry validates vocabulary ("observe→evaluate→optimize") but its loop ends at prompt/config rollback — no belief revision, no capability decay, no refusal.
- Positioning line: **"Foundry shows you which step broke. We show you which belief broke — and make sure it never misleads the agent again."** Our OTel spans can feed Foundry/App-Insights as a downstream sink while we own the verification layer.
- Gartner: >40% of agentic projects canceled by 2027; inadequate debugging infra a primary cause.

## 6 · Legal & ethical policy defaults (locked)

1. Local-first storage; hosted telemetry strictly opt-in. 2. Redact-before-persist (`trace_redaction` as single choke point; GDPR Art. 25 pattern). 3. Publish Data Manifesto + `--debug-telemetry` transparency flag + subprocessor/AI-policy page at release. 4. Sentry's five AI commitments adopted verbatim (deletion propagates to training data; PII scrub pre-training; output isolation; in-house/trusted models only). 5. Apache-2.0 + SECURITY.md + plain-language threat model (bearer = authN not authZ). 6. Never train on user traces without explicit consent. 7. Shared failure bundles only scrubbed + consented (AgentDebugX Error-Hub precedent).

## 7 · Failure-telemetry architecture (Sentry-level, closed-loop)

```
capture   MCPServer middleware wraps every tool call → immutable Event rows
          (args-shape, latency, outcome, tokens); hook traces join same pipeline
redact    trace_redaction BEFORE persistence
classify  v0 rules over ONE primary taxonomy — spec §36 six causes + false-reuse.
          SkillTriage class + fingerprint family are stored as SECONDARY
          attributes on the same rows (mapping table ships with T1; the
          classifier never emits free-form or dual-primary labels).
          Fingerprints: hash(tool_id, step_kind, arg-shape, env) → failure families
attribute explain_failure(execution_id): backward walk over execution graph
          (AgentTrace/eIRWR style, sub-second, no hot-path LLM) joined with
          fingerprint family + capability history → ranked cause chain WITH beliefs:
          "reused procedure P relying on claim C whose precondition died at t"
act       failure → Evidence(contradicts) → belief/capability drop → dependents
          re-checked via TMS → next check_procedure refuses citing degradation
export    OTLP GenAI spans (any APM incl. Foundry); optional literal-Sentry sink;
          shipped Grafana dashboard JSON
alerts    capability<threshold · new-family spike · stale-procedure attempt · DCR drop
```

## 8 · Evaluation receipts stack (release blockers)

Ripple-lite (100-instance propagation %) · MQuAKE-T multi-hop consistency · LongMemEval-S 100Q stratified incl. abstention · Who&When calibration of classifier · retrieval leave-one-out sanity (pattern validated n=400, p=.0066 [T-24]) · AFTER smoke → full if green · p95 tool-call latency · multi-client conformance matrix (Claude Code/opencode/Inspector).

## 9 · Roadmap gates

Sprint tracks (T1 ripple ⭐ … T6 positioning) → JSON contracts → integration + refusal demo video → decision memo (propagation-dominant ⇒ infrastructure wedge; capability-dominant ⇒ procedures wedge; both ⇒ open-core).
Day-7 gate: ≥1 public asset live. Day-30 gate: {500 installs ∨ 1 enterprise conversation ∨ workshop submission}. Launch: L0 assets week (receipts page, video, manifesto, friends-circle 48h) → L1 Show-HN Tue–Thu ET + registry/directory submissions same afternoon → L2 weekly changelog, design partners from loudest reporters, "failure family of the week" content. Pivot triggers pre-committed (HN cold ⇒ video-first relaunch; numbers challenged ⇒ publish reproducible harness; incumbent ships refusal ⇒ compete on TMS depth + receipts-format standard).

## 10 · Primary sources
arXiv: 2603.14688 · 2604.23366 · 2605.06716 · 2605.28303 · 2606.01053 · 2606.23127 · 2606.24626 · 2606.26627 · 2606.27154 · 2606.29824 · 2606.29961 · 2607.01480 · 2607.08032 · 2607.08529 · 2607.12180(≠TRAIL-bench) · 2607.13548 · 2607.18754 · 2607.21962 · 2607.26455 · 2607.27290 · 2607.28545 · 2607.29055 · 2608.05212 · 2608.08073 · 2608.08968 · 2608.10775 · 2608.11888 · 2608.14074 · 2608.17756 · 2608.18575 · 2305.14795 · 2307.12976 · 2411.00278(KAN-AD, sibling project)
Web: learn.microsoft.com/foundry observability + Build'26 BRK252 · galileo.ai RCA-tools roundup · morphllm 12-platform comparison · presenc.ai funding matrix · sentry.io security/privacy/AI-TOS blog · sota.io GDPR analysis · research.swtch.com/telemetry · linuxfoundation.org telemetry guidance · code.visualstudio.com telemetry · docs.sentry.io scrubbing · Gartner agentic-cancellation PR · agentskills.io · developer.nvidia.com verified skills.

