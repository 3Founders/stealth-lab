# StealthLab — Grand Vision

## 1. The vision

StealthLab is building the trust and learning layer for autonomous software agents.

Agents do not merely need more memory. They need a way to know which remembered procedures still deserve to influence the next action, under the current task, environment, implementation, and authority conditions. StealthLab turns completed work into an evidence-backed capability system:

```text
experience
  → observations
  → claims and state
  → executable procedures
  → evidence
  → applicability
  → execution
  → capability update
  → safe reuse or explicit refusal
```

The product thesis is:

> **Agents earn the right to remember.**

A trace is not a capability. A model’s confidence is not evidence. A successful past run does not guarantee that the same procedure works in a changed repository, with a changed implementation, or under a different policy. StealthLab makes those distinctions explicit and operational.

## 2. What exists today

The current backend is not a prompt library with a search box. It is a local-first, append-oriented substrate with the following implemented foundations:

- Redacted event collection and ingestion from agent traces.
- Episodes, observations, claims, procedures, evidence, executions, capability records, and ChangeSets.
- Scope and provenance gates on objects entering storage.
- Bi-temporal claims and invalidate-and-append semantics.
- Parameterized procedures with preconditions, exclusions, verification state, and implementation bindings.
- Non-compensatory applicability: a violated precondition disqualifies reuse rather than lowering a similarity score.
- Evidence-based capability using execution outcomes and Wilson lower bounds rather than model-brand confidence.
- Deterministic replay, durable execution, retry/resume, implementation pinning, and terminal execution fencing.
- Local/private procedure storage with explicit publication to a shared commons.
- Product-model surfaces for Problems, Benchmarks, Solutions, Evaluations, and leaderboards derived from real execution lineage.
- An MCP server that exposes the substrate to coding agents and external clients.
- A sharded/projection-based retrieval architecture with local benchmark evidence, while production-scale validation remains incomplete.

The current maturity is a substantial trust substrate and experimental product, not a finished multi-tenant SaaS. The important unfinished claims are equally part of the vision: a fully self-sustaining trace-to-procedure learning loop, converged retrieval surfaces, complete refusal enforcement, public benchmark adoption, and production-scale deployment evidence.

## 3. The product in three layers

### Layer 1 — Private capability substrate

A developer runs StealthLab locally against their own coding-agent traces. The system redacts and structures experience, extracts candidate procedures, checks whether their assumptions still hold, and retrieves only procedures that are applicable to the current goal and environment.

The first product is not “more memory.” It is lower reasoning cost with an auditable reason for every reuse decision.

### Layer 2 — Evidence-backed execution substrate

When an agent executes a procedure, StealthLab records the exact procedure version, implementation binding, inputs, outputs, artifacts, outcome, and evidence. Capability is derived from real executions, including failures and the conditions under which they occurred. A stale or contradicted procedure can be retired or superseded without rewriting history.

This creates a capability record that can be inspected, replayed, compared across models, and used as an input to routing policy.

### Layer 3 — Shared, permissioned capability commons

A user may explicitly publish a trusted private procedure to a global commons. Publication does not transfer trust. A new user’s agent must establish applicability in that user’s environment and generate its own execution evidence before the procedure earns reuse.

The commons is therefore not a public prompt dump. It is a market and governance surface for executable, evidence-bearing methods:

```text
private experience
  → reviewed publication
  → global candidate
  → independent applicability check
  → independent execution evidence
  → earned capability
```

## 4. Why this matters

Most agent memory systems optimize recall, compression, or conversational continuity. Those are useful but incomplete. They do not reliably answer:

- Is this procedure still true?
- Does it apply to this repository, tool version, policy, and task state?
- What evidence supports it?
- How independent is that evidence?
- What happens after a failure changes the environment?
- Can a user inspect why an agent reused or refused a method?
- Can a procedure be replayed without silently resolving to a newer implementation?

StealthLab treats memory as a provenance-bearing object with a lifecycle. The differentiator is not the database. It is the closed loop from experience to evidence to belief to procedure to safe reuse or refusal.

## 5. The moat

The moat is a compounding evidence graph, not a prompt format.

Every execution can add information that a copied codebase does not have:

- a real outcome;
- a failure class;
- a precondition that stopped applying;
- an implementation version;
- a measured cost;
- a human approval or rejection;
- a reusable correction;
- a capability update.

Over time, StealthLab can distinguish methods that merely look plausible from methods that repeatedly work under stated conditions. That is a different asset from a static library of skills or a vector database of conversations.

The open protocol and registry components should remain open. The defensible value is the operating discipline around evidence, evaluation, adjudication, and safe deployment.

## 6. Research and product flywheel

StealthLab is both a product and a measurement program:

1. Agents execute real work.
2. Traces become structured episodes and observations.
3. Procedures are extracted and versioned.
4. Applicability and evidence determine reuse.
5. Execution produces outcomes and cost measurements.
6. Failures classify boundaries and update capability.
7. Public benchmarks compare solo, ordinary memory, and verified substrate arms.
8. Results improve extraction, routing, retirement, and safety policy.
9. Better procedures create more useful agent execution, which creates better evidence.

The scientific claim is deliberately narrow until more data exists: evidence-gated procedure reuse may preserve accuracy while reducing token/tool cost and may reduce unsafe reuse of stale procedures. The current 74.4% token reduction with flat accuracy is a promising directional result, not proof of universal superiority. The next research milestone is repeated, error-floor-aware, multi-domain measurement.

## 7. Grand product vision

### For developers

A private, inspectable capability layer for coding agents that reduces repeated reasoning, prevents stale methods from being reused, and makes every recommendation explainable.

### For agent platforms

A standard substrate for importing, evaluating, approving, executing, and retiring procedures across models, frameworks, and repositories without surrendering local control.

### For organizations

A governed record of how autonomous work was performed, which methods were permitted, what evidence supported them, where failures occurred, and who or what was accountable.

### For the public commons

A permissioned exchange of executable methods where trust is earned through independent applicability and execution rather than asserted by authorship, model identity, or popularity.

### For AI safety researchers

A reproducible testbed for studying stale memory, provenance poisoning, procedure reuse, capability estimation, multi-agent coordination, and the gap between empirical trust and formal guarantees.

## 8. What StealthLab should not become

- It should not become a closed vendor memory silo that makes portability impossible.
- It should not treat model reputation as a substitute for evidence.
- It should not publish procedures without preserving provenance, scope, and revalidation conditions.
- It should not optimize benchmark scores while hiding failure classes or inconvenient results.
- It should not claim that audit-mode refusal equals enforcement.
- It should not grow a shared corpus faster than it can establish consent, deletion, security, and independent evaluation.
- It should not use “AI agent” as a substitute for a precise user, workflow, authority boundary, and measurable outcome.

## 9. Three-year horizon

### Now — prove the loop

- Publish a reproducible evidence pack from the existing tau2-bench and SWE-bench-style harnesses.
- Complete the trace → procedure → applicability → refusal demo.
- Establish one canonical retrieval path and one honest public benchmark.
- Ship a local installation and MCP integration another developer can run.
- Submit to evaluation, open-source, provenance, and agent-safety funders.

### Next — make learning self-sustaining

- Close the claim-to-procedure quality gap.
- Automate multi-episode synthesis with stronger compatibility checks.
- Tie every extracted procedure to a reproducible implementation and evaluation.
- Add public benchmark submissions and external users.
- Measure the write-side learning loop against ordinary memory.

### Later — become shared infrastructure

- Add a permissioned public procedure registry.
- Add independent reviewer and adjudication workflows.
- Add portable procedure artifacts and interoperability with MCP/A2A-compatible systems.
- Build organizational governance, policy enforcement, and compliance evidence.
- Support multi-agent and cross-organization capability exchange without copying private evidence.

### At scale

StealthLab becomes the trust-and-learning plane for autonomous software: the place where experience becomes executable capability, capability becomes measurable, and uncertainty remains visible.

## 10. One-sentence version

> **StealthLab turns agent traces into a living, evidence-backed capability system—and makes an agent earn the right to reuse what it remembers.**

## 11. Grounding and honesty

This vision is grounded in the current repository architecture and documented semantics, especially:

- `verified_procedural_experience_system_ideal_specification_v4.md`
- `schema.md`
- `docs/final-v1.md`
- `docs/retrieval_architecture.md`
- `docs/production_ingestion.md`
- `docs/production_acceptance_matrix.md`
- `backend/app/services/v0_gate.py`
- `backend/app/services/applicability.py`
- `backend/app/execution/evidence.py`
- `backend/app/mcp_server/server.py`

The repository’s current acceptance evidence is substantial but not a production guarantee. The public story should always distinguish implemented foundations, measured results, directional findings, and future work.
