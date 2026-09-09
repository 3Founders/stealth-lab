# Security Overview (public summary)

**STATUS: DRAFT — REQUIRES LEGAL/FOUNDER REVIEW before publishing.**

This is a trimmed, public-facing summary of `SECURITY.md` (the repository's internal/engineering
threat-model document, which remains the authoritative, more detailed reference — link to it
alongside this page if you want to be maximally transparent). This summary avoids exploitable
implementation detail and does not claim any certification StealthLab does not hold.

## Current posture: two modes, and they are very different

StealthLab's codebase supports two deployment shapes, and the security posture depends entirely
on which one you're using:

1. **Local-first (v0.1, as shipped and documented in `SECURITY.md`):** you run the server and
   database yourself, on your own machine, bound to loopback by default. There is no
   StealthLab-hosted service in this mode. A bearer token gates access to the MCP server, but it
   is authentication (proves you have the token), not fine-grained authorization (there is no
   per-tool/per-scope permission model in this mode).
2. **Hosted, multi-user (in progress):** StealthLab operates authentication (Supabase Auth),
   authorization, and the database for you. As of this audit, this mode is **partially built** —
   see "Known limitations" below before relying on it for anything sensitive.

**We will not claim a security posture in the published version of this page that the current
deployment doesn't actually have.** Confirm which mode is live before publishing.

## Authentication

- Sign-in uses Supabase Auth (email/password, Google OAuth), issuing OIDC-shaped JWTs.
- The backend validates every bearer token centrally: JWKS-based signature verification,
  algorithm allow-list (asymmetric signing only — HS256/`none` are rejected), and standard claim
  checks (audience/issuer/expiry/not-before/subject). Expired or malformed tokens are rejected.
- MCP server access (for tool-calling clients) is gated by a separate bearer token, checked with
  constant-time comparison; `stdio` transport has no authentication, by MCP protocol design (if
  you can spawn the process, you already have equivalent access).

## Authorization and tenant isolation

- A centralized access-control layer (`backend/app/services/access.py`) evaluates
  visibility/tenancy predicates before content is returned — not an ad-hoc per-endpoint check.
- Private content is scoped to the owning user (and, where organization scoping is live,
  the owning organization); public Global Commons content is visible to anyone.
- PostgreSQL row-level security acts as an additional backstop layer beneath the application-level
  checks.
- **Known limitation, stated plainly:** as of this audit, organization-level (`org`) visibility
  enforcement was not yet reachable from the live HTTP path in every case, and some
  cross-tenant/IDOR test coverage was still partial per the internal compliance ledger. Treat
  organization-scoped sharing as **still maturing**, not a settled guarantee, until that ledger
  shows it complete.

## Private vs. public boundary

- Nothing becomes public except through an explicit publish action — see
  [Global Commons Terms](./GLOBAL_COMMONS_TERMS.md). Retrieval, embedding, and ingestion never
  promote private content to public on their own.
- Publishing creates an independent public copy; it does not "flip a visibility bit" on your
  private data, and does not carry your private execution history with it.

## Secret handling

- A single redaction choke point (`trace_redaction.py`) runs on traces before they are written to
  disk or transmitted anywhere, catching known secret shapes (cloud/provider API key formats,
  generic bearer tokens, private-key blocks) and excluding tool input/output wholesale when a
  sensitive path was touched.
- The same redaction primitive is reused by both the explicit publish path and the internet
  ingestion admission gate.
- **This is a best-effort floor, not a guarantee** — stated exactly this way in the underlying
  code's own documentation. No fixed pattern set catches every secret shape that will ever exist.
  Do not rely on automated redaction as your only safeguard against submitting a secret.

## Hosted execution

Hosted execution against a connected repository (running a procedure against your own code) is
gated behind workspace/tenant authorization when enabled (`HOSTED_EXECUTION_ENABLED`), and is
**off by default**. When off, the execution path uses a caller-provided path directly — acceptable
only for local/loopback use, not for a shared, hosted deployment. Confirm which mode is active
before treating hosted execution as tenant-isolated.

## Auditability

An append-only audit-event writer exists (`backend/app/services/audit.py`) intended to record
sensitive transitions (publication requested/approved, scope changes, export/deletion requests,
provider-call decisions, etc.). **As of this audit, coverage of this writer across all sensitive
transitions was not fully confirmed** — re-verify before making a completeness claim publicly.

## Vulnerability reporting

**[CONTACT/CHANNEL]** — the current internal document (`SECURITY.md`) points to GitHub's private
vulnerability reporting feature on this repository. Confirm this is the channel you want to
publicize, and add a formal SLA if you intend to commit to one.

## What we do not claim

We do not claim SOC 2, ISO 27001, HIPAA, PCI-DSS, or any other certification. We do not claim
that any automated screening (admission gate, redaction) is a perfect or complete defense — see
[Acceptable Use Policy](./ACCEPTABLE_USE_POLICY.md) §3 and [Privacy Policy](./PRIVACY_POLICY.md)
§9.

---
*Generated from repository state on 2026-09-09. Cross-references: `SECURITY.md`,
`backend/app/services/access.py`, `backend/app/services/audit.py`,
`backend/app/services/trace_redaction.py`, `backend/app/services/workspace_registry.py`,
`docs/launch_compliance_implementation_ledger.md`.*
