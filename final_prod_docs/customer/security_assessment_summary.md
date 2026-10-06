# Security Assessment Summary

**[COMPANY LEGAL NAME]** · **[DATE]** · Share under NDA only. Never share the full report.

> **Current status: no independent penetration test has been performed.** Do not send this document with the table below filled in until a test is complete. Everything in "Our own testing" is the company's testing, and must be labelled that way to customers.

## 1. Independent assessment

| Item | Value |
|---|---|
| Assessor | **[firm name]** |
| Type | Web application, API and MCP endpoint assessment (grey box) |
| Dates | **[start]** to **[end]**; retest **[date]** |
| Version tested | **[release / commit]**, environment **[production-equivalent]** |
| Scope | Hosted MCP endpoint and OAuth flow; `call_model` egress and SSRF controls; cross-tenant access through each tool; consent and account pages; prompt injection through stored procedures |
| Out of scope | Third-party services (Supabase, Neon, Google Vertex AI, Cloudflare); denial-of-service; social engineering |
| Method | **[OWASP ASVS / OWASP API Top 10 / OWASP MCP Top 10 / PTES]** |

### Findings

| Severity | Found | Fixed and retested | Accepted risk (with owner and date) |
|---|---|---|---|
| Critical | **[●]** | **[●]** | **[●]** |
| High | **[●]** | **[●]** | **[●]** |
| Medium | **[●]** | **[●]** | **[●]** |
| Low / informational | **[●]** | **[●]** | **[●]** |

Statement to include only if true after the retest: "No critical or high findings remain open as of **[date]**." Otherwise list the open findings and their dates.

Attestation letter from the assessor: **[attached / available on request]**.

## 2. Our own testing (not independent)

- Cross-tenant isolation tests, including one that checks a user cannot report on another's model-plan handle.
- Content-screen tests for links, hidden characters and instruction phrases in retrieved content.
- Tool-description tests and consent-page tests (framing, localhost redirects, unnamed apps).
- Weekly dependency audit and a software bill of materials in CI; patch times in our vulnerability policy.
- MCP-specific scan and prompt-injection test run: **[run on date, result / not yet run]**.

## 3. Disclosure and fixing

Report vulnerabilities to **[security@domain]** or **[advisory URL]**. We acknowledge within 3 business days. Fix or mitigate: critical 7 days, high 30 days, medium 90 days, from confirmation.

## 4. Certifications

SOC 2: **[not started / in progress, with target date / report available]**. ISO 27001: **[same]**. We do not describe either as held until a report exists.
