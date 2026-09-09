# Global Commons Terms

**STATUS: DRAFT — REQUIRES LEGAL REVIEW. NOT YET PUBLISHED OR IN EFFECT.**

## 1. What the Global Commons is

The Global Commons is StealthLab's shared, public library of procedures — step-by-step
instructions, contributed either by users explicitly publishing their own private work, or
ingested from public internet sources through an automated admission gate. Anyone can read and
search it; you do not need an account to view it. See §0 of
`STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md`: the Commons is a launch feature, not an add-on, and
is deliberately **not** an anonymous, instant-trust, open-upload system — publication requires an
authenticated, identified actor.

## 2. Publishing is explicit — nothing becomes public by accident

Creating, editing, retrieving, or embedding a private procedure never makes it public. The only
way your content enters the Global Commons is an explicit publish action
(`POST /v1/procedures/{id}/publish`, `backend/app/services/publish.py`). Before you publish, you
should understand exactly what that action does:

- It creates a **brand-new, independent global candidate row** for your procedure. It does not
  turn your existing private row into a public one — your private row and its history remain
  where they were.
- Your private execution/verification history is **not copied** into the new global candidate.
  The published candidate starts with zero global verification evidence, deliberately, even if
  you'd run and verified it many times privately. Global trust has to be re-earned independently
  — see the [Verification Disclaimer](./VERIFICATION_DISCLAIMER.md).
- Paths and other locally-identifying values in the procedure text are scrubbed before
  publication (`_scrub_paths` / `_scrub_value` in `publish.py`).
- Publishing again after a later private edit creates **another** fresh, independent global
  candidate — republishing does not silently mutate a previously-published global row.
- Publication requires a verified, non-anonymous actor identity — `publish_local_procedure`
  explicitly refuses to run without one.
- A `global_candidate_created` action is recorded.

**Frontend note:** the launch-compliance spec (LC-009) calls for pre-publish UI that shows
current scope, destination, what becomes visible, and confirms the private-history guarantee
above before you commit. As of this audit, dedicated pre-publish confirmation UI in
`frontendv1` was not found as a distinct component — see `docs/legal/AUDIT_REPORT.md` §9 for
this gap. Treat this document as the authoritative statement of what publishing does until that
UI exists.

## 3. What becomes public

When you publish a procedure: its name, description, applicability conditions, steps, and
(scrubbed) content become visible to anyone browsing the Global Commons. Provenance metadata
(creator/source reference, derivation method) travels with it. Your private execution traces,
raw evidence, and anything not part of the procedure text itself do **not** become public.

If you additionally opt in to a **public profile** (off by default — see
`frontendv1/src/app/me/privacy/page.tsx`), your display name, tagline, and aggregate contribution
counts (procedures authored, verified procedures, claims, Commons publications) also become
visible on a profile page, in people search, and on the contributor leaderboard. This is a
separate, independent opt-in from publishing a procedure.

## 4. Your responsibilities as a contributor

- You must own or control the content you publish, or have the rights necessary to share it
  under this license (see §5 and [Copyright & Third-Party Sources](./COPYRIGHT_AND_THIRD_PARTY_SOURCES.md)).
  Don't publish someone else's proprietary or copyrighted material without the right to do so.
- Don't publish secrets, credentials, personal data about yourself or others, or organization
  confidential material — even though scrubbing/redaction runs automatically, it is a best-effort
  safety net, not a substitute for your own review (see [Acceptable Use Policy](./ACCEPTABLE_USE_POLICY.md)).
- Follow the [Acceptable Use Policy](./ACCEPTABLE_USE_POLICY.md) — no malicious, destructive, or
  credential-theft procedures.
- Represent your procedure honestly — don't misrepresent scope, risk, or applicability.

## 5. License you grant on published content

By publishing content to the Global Commons, you grant StealthLab and other users a worldwide,
non-exclusive, royalty-free, sublicensable license to host, display, execute, adapt, and
redistribute that content as part of the shared library — including allowing other users to run,
fork, or build derivative procedures from it. **[LEGAL: select and confirm the actual license
model — e.g. a specific open license (CC-BY-SA, MIT-equivalent for procedure text) vs. a bespoke
platform license — this is a business decision the codebase does not make. State clearly whether
you retain ownership (recommended) subject to this license.]**

## 6. Third-party and internet-sourced content

Some Global Commons content is ingested automatically from public sources rather than published
by a user (see `backend/app/services/skill_ingestion.py` and
`backend/app/services/ingestion_admission.py`). This content:

- Passes through the same automated admission/safety screening described in the
  [Acceptable Use Policy](./ACCEPTABLE_USE_POLICY.md) §3.
- Retains provenance (source reference) where available.
- Is **not** vetted for license compliance with the original source by a legal review — see
  [Copyright & Third-Party Sources](./COPYRIGHT_AND_THIRD_PARTY_SOURCES.md). Content being
  publicly accessible on the internet does not mean it is free to republish, and StealthLab does
  not represent that ingested content is cleared for reuse.
- Starts as a Global Candidate, never Verified — ingestion admission is a safety decision, never
  a correctness or licensing decision.

## 7. Candidate vs. Verified

See the [Verification Disclaimer](./VERIFICATION_DISCLAIMER.md) for the full explanation. In
short: **Candidate** means proposed, not independently established. **Verified** means
independently tested under defined conditions with supporting evidence — it does **not** mean
guaranteed-correct in every environment, and previously-verified procedures can go stale.

## 8. Removal, correction, and takedown

- **Reporting a problem with published content:** **[FOUNDER: define a real reporting channel —
  e.g. an in-app "report" action or CONTACT EMAIL — not currently found as a dedicated UI
  surface in `frontendv1`.]**
- **Copyright/IP takedown:** see [Copyright & Third-Party Sources](./COPYRIGHT_AND_THIRD_PARTY_SOURCES.md)
  §"Takedown and correction."
- **Correcting your own published content:** publish an updated version, which creates a new,
  independent global candidate (see §2) — the old version is not silently overwritten, preserving
  history for anyone who relied on it.
- **Withdrawal:** the launch-compliance spec calls for a withdrawal state machine
  (`STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` §"LC-002"/Phase 4) allowing a contributor to request
  a published object be withdrawn. As of this audit, that state machine was listed as **not yet
  implemented** in `docs/launch_compliance_implementation_ledger.md` (Phase 4: "withdrawal state
  machine — MISSING"). Until it exists, treat publication as **effectively irreversible** in
  practice: you can supersede a procedure with a corrected version, but cannot yet fully retract
  the original through in-app tooling. Ask **[CONTACT EMAIL]** for manual removal requests in the
  meantime.
- Deleting your account does **not** delete content you've already published to the Global
  Commons — see [Privacy Policy](./PRIVACY_POLICY.md) §6 ("independently-sourced/verified
  Commons objects are preserved") and `backend/app/api/me.py`'s deletion endpoint.

## 9. What happens to derived evidence

If other users execute a published Verified procedure and generate their own evidence, that
evidence belongs to their own execution record, not to you as the original contributor. Removing
or superseding a published procedure does not retroactively delete evidence other users already
generated from it, consistent with the append-only, evidence-preserving design described in
`docs/launch_compliance_implementation_ledger.md`.

## 10. Versioning

Each publish action creates a new, independently-evidenced candidate, as described in §2.
StealthLab does not currently present an explicit semantic-version number for published
procedures in the reviewed code paths — versioning today is effectively "each publish is a new,
separately-tracked object with its own provenance." **[FOUNDER: confirm whether a formal version
scheme is planned.]**

---
*Generated from repository state on 2026-09-09. Cross-references: `backend/app/services/publish.py`,
`backend/app/services/publication.py`, `backend/app/services/ingestion_admission.py`,
`STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md`, `docs/launch_compliance_implementation_ledger.md`.*
