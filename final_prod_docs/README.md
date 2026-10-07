# final_prod_docs — security and architecture documents for customers

**Status: drafts for founder review. Not reviewed by counsel or an auditor. Nothing here has been sent to anyone.**

These documents answer a CISO's questionnaire. Each statement is tagged by how well it is supported:

- **[code]** — checked against the repository (file named).
- **[config]** — true only if the deployment is set up as documented; confirm on the live service.
- **[unverified]** — believed but not checked, or depends on a contract we have not read. Do not send it to a customer as fact.

## Contents

| File | What it answers |
|---|---|
| `01_security_and_data_flow.md` | The one-page whitepaper: what we store, how it is protected, where it runs. Replaces the "zero-retention guarantee" wording with what is true |
| `02_data_inventory_and_retention.md` | Every kind of data, where it lives, who can see it, how long it is kept, how it is deleted |
| `03_data_flow_diagram.md` | Diagram of the flows and trust boundaries, per tool |
| `04_service_levels_and_failure_behavior.md` | Targets (not guarantees), added latency, what happens when a model provider is down |
| `05_assessments_and_attestations.md` | Penetration test, SOC 2 / ISO 27001, questionnaires: status and what we may say |
| `06_claims_register.md` | Claims customers ask for that we **cannot make yet**, and what unlocks each |

`customer/` holds the three documents written to be sent to a customer once the bracketed blanks are filled: `security_whitepaper.md` (one page), `sla_policy.md`, `security_assessment_summary.md` (a template: no independent test exists yet). The numbered files are internal evidence behind them.

Related, already in the repo: `docs/legal/SECURITY_OVERVIEW.md`, `docs/legal/SUBPROCESSORS.md` (updated 2026-10-07; confirm the blanks), `docs/security/vulnerability_policy.md`, `docs/security_runbook.md`, `docs/provider_connections.md`, `legal/` (MNDA, MSA).

## How this differs from the checklist we were given

The checklist assumed an inline proxy that sits between a coding agent and Anthropic/OpenAI. This product is an MCP server. Its default path does **not** carry the customer's prompts or code: `find_ways` receives a task description and returns procedures. Only `call_model`, which is off until a connection is configured, sends customer text to a model provider. Writing the documents around the real shape avoids promising a proxy's guarantees (for example "under 30 ms added") that the product does not have.
