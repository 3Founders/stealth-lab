# StealthLab Terms of Service

**STATUS: DRAFT — REQUIRES LEGAL REVIEW. NOT YET PUBLISHED OR IN EFFECT.**

This draft was generated from the current codebase (backend + `frontendv1`) as of 2026-09-09,
to describe what StealthLab actually does today. It is not legal advice and must be reviewed by
qualified counsel before it is shown to a real user or given contractual effect. Every bracketed
placeholder (e.g. `[LEGAL ENTITY NAME]`) must be filled in by the founder(s)/counsel — do not
invent these values.

---

## 1. Who this agreement is with

These Terms are between you and **[LEGAL ENTITY NAME]** ("StealthLab," "we," "us"), a
**[LEGAL ENTITY TYPE, e.g. corporation]** organized in **[JURISDICTION]**. By creating an account
or using StealthLab, you agree to these Terms and to our
[Privacy Policy](./PRIVACY_POLICY.md), [Acceptable Use Policy](./ACCEPTABLE_USE_POLICY.md), and
[Global Commons Terms](./GLOBAL_COMMONS_TERMS.md), each incorporated by reference.

## 2. What StealthLab is

StealthLab is a system for capturing, verifying, and sharing "procedures" — step-by-step
instructions an AI coding agent (or a person) can follow to accomplish a task — along with
execution evidence, provenance, and (optionally) publishing them to a shared public library
called the **Global Commons**. As implemented today, this includes:

- Private, per-user procedures and traces (`backend/app/services/procedures.py`,
  `backend/app/services/personal_contributions.py`).
- Optional, explicit publication of a private procedure into the public Global Commons
  (`backend/app/services/publish.py`, `backend/app/services/publication.py`).
- Automated ingestion of procedures from public internet sources, screened by an admission
  gate before becoming visible (`backend/app/services/ingestion_admission.py`,
  `backend/app/services/skill_ingestion.py`).
- Execution and, for some capabilities, behavioral verification of procedures against evidence
  (`backend/app/execution/behavior_verification.py` and `backend/app/execution/verifiers/`).
- Optional hosted execution against a connected repository, gated by workspace/tenant
  authorization (`backend/app/services/workspace_registry.py`).

The product is under active development. **[FOUNDER: confirm current deployment posture —
local-only vs. hosted — before publishing these Terms; see SECURITY_OVERVIEW.md §"Known
limitations" for why this matters.]**

## 3. Eligibility and accounts

- You must be able to form a binding contract in your jurisdiction to use StealthLab.
- Sign-in is via Supabase Auth (email/password or Google OAuth) — see
  `frontendv1/src/lib/auth.ts` and `frontendv1/src/app/auth/page.tsx`.
- You are responsible for safeguarding your account credentials and for all activity under your
  account.
- **[LEGAL: minimum age requirement — e.g. 18, or 13 with guardian consent — not determined by
  the codebase and must be set by the business.]**

## 4. Your content

"Your Content" means procedures, claims, code, execution traces, and any other material you
submit, whether private or published to the Global Commons.

- **Private content** stays scoped to you (or your organization, once organization-scoped
  access is live) unless and until you take an explicit action to publish it. See
  `backend/app/services/access.py` and `backend/app/services/publish.py` for the enforcement
  mechanism.
- **You keep ownership** of Your Content, subject to the licenses you grant below.
- **License to operate the service:** you grant StealthLab a worldwide, non-exclusive,
  royalty-free license to host, store, process, and display Your Content solely to operate,
  secure, and improve the service for you (and, for published content, for other users — see
  §6).
- You represent that you have the rights necessary to submit Your Content and that it does not
  infringe or misappropriate any third party's rights. See
  [Copyright & Third-Party Sources](./COPYRIGHT_AND_THIRD_PARTY_SOURCES.md).

## 5. Acceptable use

You agree to the [Acceptable Use Policy](./ACCEPTABLE_USE_POLICY.md), which prohibits, among
other things, submitting credential-theft, exfiltration, destructive, or malware-shaped
procedures. StealthLab runs automated admission screening on internet-sourced content before it
becomes visible (`app/services/ingestion_admission.py`), but **this screening is not a
guarantee** — see §9 and the Acceptable Use Policy for its actual scope and limits.

## 6. Global Commons publication

Publishing is an explicit, separate action from creating private content — it is never automatic.
When you publish, additional terms apply: see [Global Commons Terms](./GLOBAL_COMMONS_TERMS.md),
which covers what becomes public, the license you grant on published content, and the
Candidate/Verified status system. **Publishing to the Global Commons is generally irreversible in
effect** even where deletion tooling exists — a published object can be superseded or tombstoned,
but independent copies and derived evidence may persist. Read that document before publishing
anything.

## 7. Candidate vs. Verified — no guarantee of correctness

StealthLab distinguishes procedures that are merely proposed ("Global Candidate") from those with
independent supporting execution evidence ("Global Verified"). See the
[Verification Disclaimer](./VERIFICATION_DISCLAIMER.md) for what that distinction actually means
and does not mean. **Neither status is a warranty.** Following any procedure — Candidate or
Verified — is at your own risk, especially for destructive, security-sensitive, or
production-affecting actions.

## 8. Third-party services

StealthLab's backend calls third-party AI/model providers (for embeddings, model calls, and
optional debate/verification panels) and, if configured, a third-party observability provider.
See [Subprocessors](./SUBPROCESSORS.md) for the current list. Your interactions that flow through
these providers are also subject to those providers' own terms, which StealthLab does not
control.

## 9. Disclaimers

STEALTHLAB IS PROVIDED "AS IS" AND "AS AVAILABLE," WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT.
Automated safety screening (admission gate), automated behavioral verification, and any
"Verified" status are **best-effort technical checks, not guarantees** — they can miss real
issues and can be wrong. StealthLab does not guarantee that any procedure, whether Candidate or
Verified, is safe, correct, current, or fit for your environment. You are solely responsible for
reviewing any procedure before executing it, especially in production or security-sensitive
contexts.

## 10. Limitation of liability

**[LEGAL: standard limitation-of-liability language — cap, exclusions for indirect/consequential
damages, carve-outs — to be drafted by counsel per governing law.]** TO THE MAXIMUM EXTENT
PERMITTED BY LAW, STEALTHLAB AND ITS LEGAL ENTITY, OFFICERS, AND EMPLOYEES WILL NOT BE LIABLE FOR
ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR LOSS OF DATA, PROFITS,
OR GOODWILL, ARISING FROM YOUR USE OF THE SERVICE.

## 11. Suspension and termination

We may suspend or terminate access for violation of these Terms, the Acceptable Use Policy, or
applicable law. You may stop using the service and request deletion of your private data at any
time — see the Privacy Policy §"Your rights" and the in-app **Privacy & Data** page
(`frontendv1/src/app/me/privacy/page.tsx`). Deletion of published Global Commons content is
governed by the [Global Commons Terms](./GLOBAL_COMMONS_TERMS.md), not by simple account deletion,
because other users may already rely on published, independently-verified content.

## 12. Changes to these Terms

**[LEGAL/FOUNDER: define notice mechanism and effective-date policy for material changes —
e.g. email notice, in-app banner, minimum notice period.]**

## 13. Governing law and disputes

**[LEGAL: governing law, venue, arbitration clause if any — jurisdiction not determined by the
codebase.]**

## 14. Contact

**[CONTACT EMAIL for legal/Terms questions]**

---
*Generated from repository state on 2026-09-09. Cross-references: `backend/app/services/publish.py`,
`backend/app/services/publication.py`, `backend/app/services/ingestion_admission.py`,
`backend/app/execution/behavior_verification.py`, `SECURITY.md`, `DATA_STATEMENT.md`,
`STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md`.*
