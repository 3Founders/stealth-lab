# StealthLab Privacy Policy

**STATUS: DRAFT — REQUIRES LEGAL REVIEW. NOT YET PUBLISHED OR IN EFFECT.**

This draft describes, as accurately as the current codebase allows, what data StealthLab
collects and processes, and what happens to it. It intentionally does **not** claim compliance
with any specific privacy law or certification (GDPR, CCPA, SOC 2, ISO 27001, HIPAA, or
otherwise) — applicability of those regimes is **undetermined** and must be assessed by counsel
per `STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` §5. Every bracketed placeholder must be filled in
by the founder(s)/counsel.

This document supersedes and supersets `DATA_STATEMENT.md` (which describes the earlier,
single-user local-only v0.1 posture) once StealthLab moves to a hosted, multi-user posture. Read
both — the older statement's honesty about limits ("best-effort, not a guarantee") still applies.

## 1. Deployment posture matters

StealthLab can run in two very different modes, and what applies depends on which one you're
using:

- **Local-first (v0.1, as documented in `SECURITY.md`/`DATA_STATEMENT.md`):** you run the backend
  and Postgres yourself; there is no StealthLab-hosted server; the only data that leaves your
  machine is text sent to your own configured embedding/model provider.
- **Hosted (in progress — see `docs/launch_compliance_implementation_ledger.md`):** StealthLab
  operates the backend, database, and authentication (Supabase Auth) for you. This is the mode
  most of this Policy addresses, since it involves StealthLab as a data controller/processor.

**[FOUNDER: state plainly, per deployment you actually ship, which mode a given user is in.]**

## 2. What we collect

Based on the actual implementation:

| Category | What | Source |
|---|---|---|
| Account/auth info | Email, user id, auth provider (email/password or Google), session tokens | Supabase Auth (`frontendv1/src/lib/supabase/client.ts`, `backend/app/services/authn.py`) |
| Profile info | Display name, optional public tagline — **opt-in, off by default** | `frontendv1/src/app/me/privacy/page.tsx` ("Public profile" toggle), `backend/app/services/personal_contributions.py` |
| Procedures | Steps, descriptions, applicability conditions you author or import | `backend/app/services/procedures.py` |
| Execution/evidence data | Records of running a procedure: declared success/failure, artifact-validation results, behavioral-verification results, evidence used to promote Candidate → Verified | `backend/app/execution/behavior_verification.py`, `backend/app/execution/verifiers/` |
| Publication data | What you explicitly publish to the Global Commons, and the publication action itself (a reference/history record) | `backend/app/services/publish.py`, `backend/app/services/publication.py` |
| Provenance data | Source references, derivation method, and (for ingested content) source URL/content hash where available | `backend/app/services/ingestion_admission.py`, provenance columns referenced in the launch-compliance spec |
| Audit events | Sensitive state transitions (where the audit writer is actually wired — see gap note below) | `backend/app/services/audit.py` |
| Technical/logging data | Standard request logs; if `SENTRY_DSN` is configured, error/trace telemetry via Sentry | `backend/app/observability.py`, `backend/requirements.txt` |
| Repository connections | Repository/workspace identifiers for hosted execution, when that feature is enabled | `backend/app/services/workspace_registry.py` |
| API/MCP interaction data | Calls made through the MCP server or API, gated by a bearer token or OIDC identity | `SECURITY.md`, `backend/app/mcp_server/server.py` |

We do **not** collect payment information, government IDs, or biometric data anywhere in the
current codebase.

**Known gap (be honest about this in the real policy):** `backend/app/services/audit.py`'s
`record_audit_event` exists but, as of the compliance ledger dated 2026-09-08, had zero callers
in some code paths — meaning not every sensitive transition may yet produce an audit record.
**[FOUNDER: re-verify current wiring before publishing any claim about audit completeness.]**

## 3. Why we process it

- To operate your account and let you author, execute, and retrieve procedures.
- To run the safety/admission screening described in
  [Acceptable Use Policy](./ACCEPTABLE_USE_POLICY.md) before internet-sourced content becomes
  visible.
- To generate embeddings for search (sent to your configured or StealthLab-configured provider —
  see [Subprocessors](./SUBPROCESSORS.md)).
- To operate the Global Commons: showing what you've explicitly published, and, if you opt in,
  your public profile and contribution counts.
- To maintain security and investigate abuse.

**[LEGAL: map each purpose to a legal basis where a consent/legitimate-interest regime applies —
not determined by the codebase; see spec §5's explicit instruction not to assume every purpose
needs consent.]**

## 4. What is private vs. public

- Procedures and traces are **private by default**. They only leave the private scope through an
  explicit publish action (`publish.py`) — never automatically because retrieval, embedding, or
  ingestion touched them.
- Your **profile visibility is opt-in and off by default**; see the toggle in
  `frontendv1/src/app/me/privacy/page.tsx`. When off, no name or contribution history is shown
  publicly.
- Private execution history is never made public by a publish action — the publish flow creates a
  **fresh** Global Candidate; local verification counts are not copied into global verification
  (see `docs/launch_compliance_implementation_ledger.md`, Phase 4, and the
  [Verification Disclaimer](./VERIFICATION_DISCLAIMER.md)).

## 5. Retention

The codebase does not currently define fixed retention periods (e.g. "logs kept for 90 days") in
a way this audit could confirm from migrations or config. What we can confirm:

- Private data persists until you delete it or your account, subject to §6.
- Published Global Commons objects persist as part of the shared library, potentially beyond your
  account's lifetime — see §6 and the Global Commons Terms.
- **[FOUNDER/LEGAL: define and publish an actual retention schedule; the compliance spec calls
  this out as an internal-document gap (`STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` §6, "Retention
  schedule").]**

## 6. Export and deletion

Real, working endpoints exist today:

- **Export:** `GET /v1/me/export` (`backend/app/api/me.py`, `backend/app/services/data_rights.py`)
  returns a machine-readable JSON bundle of your data. Global Commons knowledge you didn't
  personally author appears only as a publication-action reference, not as owned data.
- **Deletion preview:** `GET /v1/me/deletion` shows what a deletion request would do, without
  mutating anything.
- **Deletion:** `POST /v1/me/deletion` executes it. Per the implementation and the in-app copy:
  - Private procedures with no published copy are **physically deleted**, including search
    vectors.
  - A procedure you published is **tombstoned**: kept as immutable history, but no longer
    retrievable as active content.
  - Independently-sourced/independently-verified Global Commons objects are **preserved** — they
    are not deleted just because you leave, matching the "do not blindly delete independently
    sourced public/global knowledge" rule in the launch-compliance spec.
  - Deletion is **refused if your account is under legal hold**.

Both are reachable from the in-app **Privacy & Data** page
(`frontendv1/src/app/me/privacy/page.tsx`).

We have not been able to confirm, from the code alone, that every downstream store (backups,
third-party provider copies, caches) is covered by this deletion path — the compliance ledger
lists a "dependency-aware DeletionService" covering backups/provider deletion as historically
**not fully verified**. **[FOUNDER: confirm current coverage before making a completeness claim
in the published policy.]**

## 7. Third-party providers ("subprocessors")

See [Subprocessors](./SUBPROCESSORS.md) for the full, code-derived list. In short: AI/model
providers (for embeddings and model calls), Supabase (auth and, in hosted mode, the database),
and optionally Sentry (error observability) and a self-hosted Ollama endpoint for local
development. We do not claim any of these have signed a DPA/SCC with us unless and until that is
actually true — **[LEGAL/FOUNDER: confirm and update.]**

## 8. AI training

We do not use your private data to train models. This mirrors the durable commitment in
`DATA_STATEMENT.md`: any future opt-in training use would ship as a separately disclosed,
opt-in feature, never a silent default change.

## 9. Security measures (high level)

See [Security Overview](./SECURITY_OVERVIEW.md) for a truthful, non-exploitable summary of
authentication, tenant isolation, and secret handling. We do not claim any security certification
we do not hold.

## 10. Your rights

**[LEGAL: state actual rights available per applicable law — access, correction, deletion,
portability, objection, restriction, appeal — and how to exercise them beyond the self-service
export/deletion tools above. Applicability of GDPR/CCPA/DPDP/other regimes is undetermined; do
not assert compliance without counsel's review of `STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` §5.]**

## 11. Children's privacy

**[LEGAL/FOUNDER: state minimum age and policy; not determined by the codebase.]**

## 12. International transfers

**[LEGAL: not determined by the codebase — depends on where you host Supabase/Postgres and which
model providers you route to. See Subprocessors for what's actually configured.]**

## 13. Changes to this Policy

**[FOUNDER/LEGAL: define notice mechanism for material changes.]**

## 14. Contact

Privacy questions: **[CONTACT EMAIL]** (the current in-app placeholder,
`privacy@stealthlab.example`, in `frontendv1/src/app/me/privacy/page.tsx`, must be replaced with
a real, monitored address before launch).

---
*Generated from repository state on 2026-09-09. Cross-references: `DATA_STATEMENT.md`,
`SECURITY.md`, `backend/app/api/me.py`, `backend/app/services/data_rights.py`,
`backend/app/services/access.py`, `docs/launch_compliance_implementation_ledger.md`.*
