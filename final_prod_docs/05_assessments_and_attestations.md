# Assessments and attestations

**DRAFT.**

## Penetration test (VAPT)

**Status: none done.** We cannot show a summary with "no high or critical findings", and we must not imply one. What we have instead, to be labelled as our own work:

- Cross-tenant isolation tests (`backend/tests/test_routing_isolation_e2e.py`, other `*_e2e.py` suites; most need a live database and have been run on a local scratch database only).
- Content-screen, tool-description and consent-page tests (`backend/tests/test_content_screen_injection_offline.py`, `test_mcp_tool_descriptions_offline.py`, `prod_frontend/tests/consent-security.test.ts`).
- A weekly dependency audit and SBOM in CI (`docs/security/vulnerability_policy.md`).
- An MCP-specific scan (`mcp-scan`) and a prompt-injection test run (`promptfoo`): **not yet run**, because they need a deployed server.

**Plan.** Commission one external test once the service is deployed and stable, before the first paid pilot if the customer asks for it. Scope:

| In scope | Out of scope |
|---|---|
| The hosted MCP endpoint and OAuth flow | Third-party services (Supabase, Neon, Vertex, R2); their own reports apply |
| `call_model` egress and SSRF checks | Denial-of-service load testing |
| Cross-tenant access through each tool | Social engineering |
| The consent page and account pages | |
| Prompt injection through stored procedures | |

Ask the tester for: a letter of attestation, an executive summary, a list of findings by severity, and a retest after fixes. Share the summary under NDA only, never the full report.

## Certifications

| Standard | Status | What we may say |
|---|---|---|
| SOC 2 Type I / II | Not started | "We are building toward SOC 2." Never "compliant" or "certified" |
| ISO 27001 | Not started | Same |
| DPDP Act, 2023 | Controls partly built; DPA drafted in `legal/` is pending | "We are designing for the Act." State the role (processor for customer content) once the DPA is signed |
| CERT-In directions | Log retention and 6-hour reporting not yet implemented | Do not claim |

A SOC 2 report takes roughly 6 to 12 months for Type II after controls exist. Start with a readiness assessment and a compliance-automation tool when the first enterprise contract needs it, not before.

## Questionnaires

Customers will send CAIQ, SIG-lite or their own spreadsheet. Build one answer bank from documents 01 to 06, with each answer tagged [code], [config] or [unverified]. Answer only from the first two tags. Mark the rest "in progress" with a date.

## Customer-side controls we can describe today

- Cursor and similar clients ask the user to approve each tool call by default. Tool descriptions name each tool's effect (read, write, spends money) **[code]**.
- Users can revoke an app's access under Account → Connected apps.
- An org admin can set an `allowed_data_classes` list, monthly and per-user budgets, and a kill switch for `call_model` **[code, migration 136]**.
- Not available: single sign-on and SCIM for customer staff, customer-managed encryption keys, customer-specific data regions, IP allow-listing.
