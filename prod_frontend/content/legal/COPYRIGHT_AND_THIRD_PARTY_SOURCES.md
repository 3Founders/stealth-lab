# Copyright & Third-Party Sources Policy

**STATUS: DRAFT — REQUIRES LEGAL REVIEW. NOT YET PUBLISHED OR IN EFFECT.**

This document has no legal conclusions about any specific license or source. It states policy and
process only. **[LEGAL: review before publication — this is exactly the kind of document that
needs counsel's sign-off given DMCA/copyright-law specifics.]**

## 1. StealthLab ingests from public sources

Part of the Global Commons is populated automatically from public internet sources
(`backend/app/services/skill_ingestion.py`). This is disclosed plainly: some procedures in the
library were not authored by a StealthLab user, but extracted and normalized from something
publicly published elsewhere.

## 2. Provenance is retained, not discarded

Ingested content keeps a provenance reference (source, derivation method) rather than being
presented as if it originated with StealthLab or an anonymous user — see
`STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` LC-003/LC-012 for the intended provenance field set
(source URL, retrieval time, content hash, publisher/author where known, publication date,
license, license source, attribution requirements). **[FOUNDER: confirm current coverage of this
field set in production data — the compliance ledger notes provenance completeness as partial as
of this audit.]**

## 3. Public does not mean free to republish

**Content being publicly accessible on the internet does not automatically mean it is free of
copyright, free to republish, or licensed for reuse.** StealthLab does not make a legal
determination about the license status of ingested content at ingestion time — the admission gate
(see [Acceptable Use Policy](./ACCEPTABLE_USE_POLICY.md) §3) screens for safety and structural
validity, **not** for copyright clearance. Do not assume any given piece of ingested content has
been cleared for reuse just because it passed admission.

## 4. Your obligations when submitting content

When you author or publish content to StealthLab — private or public — you must have the rights
necessary to do so. Do not submit:

- Content copied from a source whose license or terms prohibit republication or redistribution.
- Proprietary or confidential material belonging to an employer, client, or third party, without
  authorization.
- Content that infringes another party's copyright, trademark, patent, or other intellectual
  property right.

You are responsible for the content you submit. See [Terms of Service](./TERMS_OF_SERVICE.md) §4
for the representations you make when submitting content.

## 5. Attribution and license terms must be respected

Where a source's license requires attribution or imposes other conditions (e.g. share-alike,
non-commercial), StealthLab's retention of provenance metadata is intended to preserve the
information needed to honor those conditions — but StealthLab does not independently verify that
every condition is being met for every ingested item. If you know a specific piece of content
carries license conditions StealthLab is not honoring, please report it (§6).

## 6. Takedown and correction

If you believe content on StealthLab infringes your copyright or other rights, or misattributes a
source:

**[FOUNDER/LEGAL: stand up an actual takedown process before launch.]** At minimum this should
include: a designated contact (**[CONTACT EMAIL]**), what information a notice must contain
(identification of the work, the allegedly infringing content's location, your contact info, a
good-faith statement), and a defined response process (removal or quarantine pending review,
notice to the contributor where applicable, and a counter-notice path). If StealthLab intends to
rely on a formal safe-harbor regime (e.g. DMCA §512 in the US), counsel must confirm eligibility
and register a designated agent — **not something this codebase audit can determine.**

Pending a formal process, the fastest interim remedy is: republish a corrected/removed version
(see [Global Commons Terms](./GLOBAL_COMMONS_TERMS.md) §8) or contact **[CONTACT EMAIL]** for
manual review, since the automated withdrawal state machine described in the launch-compliance
spec was not yet implemented as of this audit.

## 7. What StealthLab owns vs. what it hosts

Publicly-sourced content ingested into the Global Commons is not automatically "StealthLab-owned"
by virtue of ingestion — StealthLab hosts and indexes it, retaining provenance to the original
source. Content you author yourself is governed by the license you grant under
[Global Commons Terms](./GLOBAL_COMMONS_TERMS.md) §5 upon publication, or remains private and
unlicensed to anyone else while it stays private.

---
*Generated from repository state on 2026-09-09. Cross-references:
`backend/app/services/skill_ingestion.py`, `backend/app/services/ingestion_admission.py`,
`STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` (LC-003, LC-012).*
