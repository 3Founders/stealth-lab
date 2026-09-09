# Legal / Policy / Trust Documentation Audit

**Date:** 2026-09-09
**Scope:** entire repository (`stealth-lab`), user-facing legal/policy/privacy/trust
documentation only — backend authorization/publication/verification *implementation* was read for
accuracy, not modified.
**This audit is not legal advice and does not certify launch readiness.**

---

## 1. Existing legal/policy documents found

None of the following existed anywhere in the repository before this audit: Terms of Service,
Privacy Policy, Acceptable Use Policy, AI/Agent Safety Policy, Global Commons/contribution terms,
copyright/DMCA policy, cookie policy, data-processing terms, subprocessor list, or a public-facing
security page. A repo-wide, case-insensitive search for these terms across `docs/` and
`frontendv1/src` returned matches only in internal specs, never a real document:

- `docs/launch_compliance_implementation_ledger.md` and
  `docs/launch_compliance_impl/FINAL-RELEASE-READINESS.md` — reference the *need* for these
  documents (per the launch-compliance spec's §6 legal-document list); they are not the documents
  themselves.
- `backend/db/46_provider_policy_and_publication.sql` — implementation-level policy tables, not
  user-facing text.

What **did** already exist, and reads as genuinely honest, well-written internal/quasi-public
material this audit deliberately preserved and cross-referenced rather than duplicated:

- `SECURITY.md` (root) — an internal engineering threat-model document for the v0.1
  single-user/local-first posture. Good bones for a public security page, but written for
  engineers and explicitly scoped to a posture (no hosted service) the codebase is now partway
  through moving past (Supabase auth, hosted execution). Not republished as-is; a trimmed public
  version was created instead (`docs/legal/SECURITY_OVERVIEW.md`).
- `DATA_STATEMENT.md` (root) — an honest, plain-language description of v0.1 data handling. Used
  as the primary factual source for the Privacy Policy draft; not itself user-facing-legal in
  format (no jurisdictional coverage, no rights language, no placeholders for the entity that
  would need to answer them).
- `frontendv1/src/app/me/privacy/page.tsx` + `frontendv1/src/lib/api/privacy.ts` — a real, working
  in-app Privacy & Data settings page (export, deletion preview/confirm, public-profile toggle).
  This is a **data-control UI**, not a Privacy Policy document, and it did not link to one before
  this audit (its "Privacy contact" line pointed to a placeholder email with no policy link).

## 2. Missing documents (before this audit)

Terms of Service, Privacy Policy, Acceptable Use Policy, Global Commons/Contribution Terms,
Verification Disclaimer, Copyright & Third-Party Sources policy, Cookies/Tracking disclosure,
Subprocessors list, and a public Security Overview — all nine were entirely absent.

## 3. Documents created/updated (this audit)

All new, under `docs/legal/`, each marked `DRAFT — REQUIRES LEGAL REVIEW` and using bracketed
placeholders (e.g. `[LEGAL ENTITY NAME]`, `[JURISDICTION]`, `[CONTACT EMAIL]`) for anything
requiring founder/legal input:

| File | Covers |
|---|---|
| `docs/legal/TERMS_OF_SERVICE.md` | Account, acceptable use pointer, content/licensing, Global Commons pointer, Candidate/Verified pointer, disclaimers, liability, termination |
| `docs/legal/PRIVACY_POLICY.md` | What's collected (table mapped to actual code), why, private/public boundary, retention (gap noted), export/deletion (real endpoints documented), subprocessors, no-training commitment, security pointer, rights placeholders |
| `docs/legal/ACCEPTABLE_USE_POLICY.md` | Prohibited activities, and a detailed, accurate walkthrough of what `ingestion_admission.py`'s admit/review/reject gate actually checks and does **not** check |
| `docs/legal/GLOBAL_COMMONS_TERMS.md` | What publishing does mechanically (fresh candidate, no inherited evidence, path scrubbing), what becomes public, contributor responsibilities, license placeholder, removal/withdrawal gap called out explicitly |
| `docs/legal/VERIFICATION_DISCLAIMER.md` | Candidate vs. Verified, grounded in `behavior_verification.py`'s actual opt-in, capability-specific, never-launders-a-failure design; staleness/revalidation |
| `docs/legal/COPYRIGHT_AND_THIRD_PARTY_SOURCES.md` | Ingestion-from-public-sources disclosure, "public ≠ free to republish," takedown process (placeholder) |
| `docs/legal/COOKIES_AND_TRACKING.md` | Code-grepped finding: no cookies, no analytics/tracking SDKs found in `frontendv1`; only functional session storage |
| `docs/legal/SUBPROCESSORS.md` | Table of actually-configured providers (Supabase, Anthropic, OpenAI, Fireworks, Google, Voyage AI, optional Sentry/General Compute/Ollama), sourced from `requirements.txt`/`.env.example` |
| `docs/legal/SECURITY_OVERVIEW.md` | Trimmed public version of `SECURITY.md`, updated to reflect the in-progress hosted/Supabase posture, with limitations stated rather than hidden |
| `docs/legal/AUDIT_REPORT.md` | This report |

No existing document was deleted; `SECURITY.md` and `DATA_STATEMENT.md` remain as the more
detailed internal/engineering references, explicitly cited from the new public docs rather than
duplicated wholesale.

## 4. Routes/URLs added

- `frontendv1/src/app/legal/page.tsx` — `/legal` index listing all nine documents.
- `frontendv1/src/app/legal/[slug]/page.tsx` — `/legal/terms`, `/legal/privacy`,
  `/legal/acceptable-use`, `/legal/global-commons`, `/legal/verification`, `/legal/copyright`,
  `/legal/cookies`, `/legal/subprocessors`, `/legal/security`. Each route reads the corresponding
  `docs/legal/*.md` file from disk at request time via `frontendv1/src/lib/legal-docs.ts`
  (`fs.readFileSync` against `path.join(process.cwd(), "..", "docs", "legal", ...)`) and renders
  it as plain preformatted text — there is no markdown-to-HTML pipeline in this repo yet, so this
  is intentionally simple rather than silently mis-rendering markdown syntax.
- No new API routes were added or changed on the backend. No backend authorization, publication,
  deletion, export, or audit logic was modified — this audit only read those files.

**Not yet verified by an actual build**: `frontendv1/node_modules` was not installed in this
environment, so `next build`/`tsc --noEmit` could not be run. The new files were written following
the conventions of existing pages in `frontendv1/src/app` (same `Link`/`"use client"` patterns,
same Tailwind class conventions) and reviewed by hand for import/syntax correctness. **Run
`npm install && npm run build` (or `tsc --noEmit`) in `frontendv1` before shipping** to catch
anything this manual review missed.

## 5. Frontend locations linked

- **Global footer** (`frontendv1/src/app/layout.tsx`): Terms, Privacy, Acceptable Use, Global
  Commons Terms, Security, and "All legal documents" — present on every page, unobtrusive, no
  modal/interstitial.
- **Sign-in/sign-up** (`frontendv1/src/app/auth/page.tsx`): a small "By continuing you agree to
  the Terms of Service and Privacy Policy" line under the auth form. Not a forced checkbox or
  blocking modal, per the task's explicit "no repeated consent modals" instruction.
- **Contribution/publish flow** (`frontendv1/src/app/submit/page.tsx`): after a procedure is
  saved (private), a line linking to Global Commons Terms and the Verification Disclaimer,
  specifically at the point where a user might next consider publishing.
- **Not wired**: the in-app Privacy & Data page (`frontendv1/src/app/me/privacy/page.tsx`)
  still shows the placeholder contact `privacy@stealthlab.example` and was **not** edited to link
  to `/legal/privacy` — this is a one-line follow-up (`<Link href="/legal/privacy">`) intentionally
  left for the founder to do alongside replacing the placeholder contact, since editing that file
  further starts to brush against the "auth-team owns this surface" coordination note in
  `docs/launch_compliance_implementation_ledger.md` §"Coordination."

## 6. Implementation → legal-document mapping

| Feature/policy | Implementation (file/route) | User-facing document | Route | Status |
|---|---|---|---|---|
| Authentication | `backend/app/services/authn.py`, Supabase (`frontendv1/src/lib/auth.ts`) | Security Overview, Privacy Policy | `/legal/security`, `/legal/privacy` | Present (new) |
| Authorization/tenant isolation | `backend/app/services/access.py` | Security Overview | `/legal/security` | Present (new); code itself is PARTIAL per the compliance ledger — document says so |
| Privacy (data inventory) | `backend/app/api/me.py`, `backend/app/services/data_rights.py` | Privacy Policy | `/legal/privacy` | Present (new) |
| Data export | `GET /v1/me/export` | Privacy Policy §6 | `/legal/privacy` | Present (new); real, working endpoint confirmed |
| Data deletion | `POST /v1/me/deletion`, `GET /v1/me/deletion` | Privacy Policy §6 | `/legal/privacy` | Present (new); real, working endpoint confirmed, including legal-hold refusal |
| Legal hold | `data_rights.py` (`plan.legal_hold`) | Privacy Policy §6 (mentioned) | `/legal/privacy` | Present (new); mechanism exists, no dedicated user doc beyond the mention |
| Private/local data | `access.py`, `procedures.py` | Terms of Service §4, Privacy Policy §4 | `/legal/terms`, `/legal/privacy` | Present (new) |
| Public publication | `backend/app/services/publish.py` | Global Commons Terms | `/legal/global-commons` | Present (new) |
| Global Commons | same | Global Commons Terms | `/legal/global-commons` | Present (new) |
| Contributor identity/opt-in profile | `frontendv1/src/app/me/privacy/page.tsx`, `personal_contributions.py` | Global Commons Terms §3, Privacy Policy | `/legal/global-commons`, `/legal/privacy` | Present (new) |
| Provenance | `ingestion_admission.py`, spec LC-003/LC-012 | Copyright & Third-Party Sources, Global Commons Terms | `/legal/copyright`, `/legal/global-commons` | Present (new); underlying field-set completeness unverified — flagged |
| Verification / Candidate vs. Verified | `backend/app/execution/behavior_verification.py`, `verifiers/` | Verification Disclaimer | `/legal/verification` | Present (new) |
| Third-party source ingestion | `skill_ingestion.py`, `ingestion_admission.py` | Acceptable Use Policy §3, Copyright & Third-Party Sources | `/legal/acceptable-use`, `/legal/copyright` | Present (new) |
| User-submitted procedures | `publish.py`, `procedures.py` | Terms of Service §4, Global Commons Terms | `/legal/terms`, `/legal/global-commons` | Present (new) |
| Malicious/dangerous content | `ingestion_admission.py` | Acceptable Use Policy | `/legal/acceptable-use` | Present (new) |
| IP / copyright | n/a (policy, not code) | Copyright & Third-Party Sources | `/legal/copyright` | Present (new); takedown process is a placeholder |
| API/MCP usage | `SECURITY.md`, `mcp_server/server.py` | Security Overview | `/legal/security` | Present (new), high-level only |
| Hosted execution | `workspace_registry.py` | Security Overview | `/legal/security` | Present (new); off-by-default status documented |
| AI-generated content | procedures generally are AI-agent-authored/executed | Terms of Service §2/§9, Verification Disclaimer | `/legal/terms`, `/legal/verification` | Present (new), high-level |
| Data retention | not concretely defined in migrations/config found | Privacy Policy §5 | `/legal/privacy` | **Gap documented, not resolved** — no fixed schedule found in code |
| Subprocessors/providers | `requirements.txt`, `.env.example` | Subprocessors | `/legal/subprocessors` | Present (new) |
| Cookies/tracking | grep of `frontendv1/src` | Cookies & Tracking | `/legal/cookies` | Present (new); confirms none found beyond session storage |

## 7. Items requiring founder decision

- Legal entity name, type, and jurisdiction (used throughout Terms/Privacy).
- Governing law / dispute resolution / arbitration clause.
- Contact addresses: general legal, privacy, security/vulnerability reporting, abuse/takedown
  reporting — currently all placeholders, including the pre-existing
  `privacy@stealthlab.example` placeholder in `frontendv1/src/app/me/privacy/page.tsx`.
  **The Privacy & Data page's contact was not changed by this audit** — it still needs the same
  fix plus a link to `/legal/privacy` (see §5).
  minimum-age policy.
- License model for Global Commons contributions (a specific open license vs. a bespoke platform
  license) — a business decision, not one the codebase implies.
- Retention schedule — no fixed schedule exists in the code; one needs to be decided and then
  implemented, not just documented.
- Whether/when to formalize a DMCA-style safe-harbor process (designated agent, statutory
  compliance) versus a lighter-weight takedown contact.
- Whether the current v0.1 local-only posture or the in-progress hosted posture is what actually
  ships first — several documents (Privacy Policy, Security Overview) explicitly branch on this
  and need a founder call on which is "live" before publishing.

## 8. Items requiring lawyer review

- Every document in `docs/legal/` in full — these are drafts written from code inspection, not
  legal advice, and use placeholder/illustrative language for liability limitation, governing law,
  and rights sections.
- GDPR/CCPA/DPDP/AI-Act applicability determination (`STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md`
  §5 explicitly defers this to counsel; this audit did not attempt it and the Privacy Policy says
  so).
- Copyright/DMCA takedown process design and safe-harbor eligibility.
- The specific license grant language for Global Commons contributions (§7).
- Any claim about subprocessor DPA/SCC status — this audit could only confirm which providers are
  *technically configured*, not what contracts exist with them.

## 9. Legal/product gaps that are technically blocked

These are cases where a document can state intent, but the underlying mechanism doesn't fully
exist yet, per the codebase itself (mainly `docs/launch_compliance_implementation_ledger.md`):

- **Withdrawal/takedown state machine for published Global Commons content** — listed as
  `MISSING` in the compliance ledger (Phase 4). Documented in `GLOBAL_COMMONS_TERMS.md` §8 as an
  explicit gap; until it exists, withdrawal is manual/best-effort only.
- **Pre-publish confirmation UI** (LC-009: "show current scope, destination, what becomes
  visible... before publishing") — not found as a dedicated component in `frontendv1` during this
  audit. `GLOBAL_COMMONS_TERMS.md` §2 notes this and states the document itself is the
  authoritative source of what publishing does until that UI ships.
- **Frontend scope labels PRIVATE/ORGANIZATION/GLOBAL CANDIDATE/GLOBAL VERIFIED (LC-010)** — a
  `ScopeBadge` component exists and is used (`frontendv1/src/app/submit/page.tsx`), but full
  coverage across all four canonical labels everywhere content is shown was not verified end to
  end in this audit.
- **Audit-event coverage** — `backend/app/services/audit.py`'s writer exists but, per the
  compliance ledger, had incomplete caller coverage as of 2026-09-08. `SECURITY_OVERVIEW.md` and
  `PRIVACY_POLICY.md` both flag this rather than claiming full auditability.
- **Organization-scoped visibility enforcement** — per the ledger, the `org` visibility path was
  not fully reachable from the live HTTP path at time of writing. `SECURITY_OVERVIEW.md` states
  this as "still maturing."
- **Provider/data-classification routing (`can_send()` decision point, LC-005)** — not
  implemented; `SUBPROCESSORS.md` states that provider routing is currently governed by API-key
  presence and cost limits only, not a data-classification policy.
- **Formal retention schedule** — no enforcement mechanism found; can't be truthfully documented
  as more than "until you delete it or your account."

## 10. Remaining checklist before public launch

1. Founder/legal fill in every bracketed placeholder across all nine `docs/legal/*.md` files
   (entity name, jurisdiction, contacts, license model, governing law).
2. Lawyer review and sign-off on all nine documents; remove the "DRAFT — REQUIRES LEGAL REVIEW"
   banners only after that review, and only for documents actually being published.
3. Decide and state which deployment posture (local-only v0.1 vs. hosted Supabase) is what real
   users will actually be on at launch; update Privacy Policy/Security Overview accordingly.
4. Replace the placeholder privacy contact in `frontendv1/src/app/me/privacy/page.tsx` and link it
   to `/legal/privacy`.
5. Run `npm install && npm run build` (and `tsc --noEmit`) in `frontendv1` to confirm the new
   `/legal` routes compile and render — not yet verified in this environment (no `node_modules`).
6. Build the pre-publish confirmation UI called for by LC-009, and the withdrawal/takedown
   mechanism called for by the launch-compliance spec's Phase 4 — both are referenced as "not yet
   built" by the new documents, and the documents will need re-review once they land (behavior
   described in `GLOBAL_COMMONS_TERMS.md` will need to match the real UI).
7. Confirm and complete audit-event coverage (`audit.py` callers) before claiming full
   auditability in `SECURITY_OVERVIEW.md`.
8. Confirm organization-visibility enforcement is fully wired before claiming org-scoped isolation
   is settled (currently described as "still maturing").
9. Decide and implement an actual data-retention schedule; update `PRIVACY_POLICY.md` §5 from "no
   fixed schedule found" to a real, enforced number.
10. Stand up real abuse-reporting and copyright-takedown intake channels (currently
    `[CONTACT EMAIL]` placeholders in `ACCEPTABLE_USE_POLICY.md` and
    `COPYRIGHT_AND_THIRD_PARTY_SOURCES.md`).
11. Re-run the Cookies & Tracking grep before launch if any analytics/tracking dependency is added
    between now and then — that document is only accurate as of 2026-09-09.
12. Confirm subprocessor DPA/SCC status with each provider in `SUBPROCESSORS.md` if any
    jurisdiction-specific compliance claim will be made.

**These documents are drafts. They are not legally reviewed, not complete, and are not a
representation that StealthLab is ready for public launch.**

---
*Audit performed by reading (not modifying) backend authorization, publication, verification, and
ingestion-admission code, plus the repository's own internal specs
(`STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md`, `docs/launch_compliance_implementation_ledger.md`,
`SECURITY.md`, `DATA_STATEMENT.md`). No backend logic was changed. Frontend changes were limited
to new `/legal/*` routes and unobtrusive links (footer, sign-in, submit flow) — no access-control,
authentication, or data logic was touched.*
