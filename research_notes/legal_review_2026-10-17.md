# Legal review — research findings (2026-10-17)

**This is research, not legal advice.** It identifies exposures and the questions counsel must answer.
Every finding cites either your own documents or a retrieved third-party source.

**Scope:** all nine `docs/legal/*.md`, `AUDIT_REPORT.md`, `TERMS_OF_SERVICE.md`,
`COPYRIGHT_AND_THIRD_PARTY_SOURCES.md`, `ACCEPTABLE_USE_POLICY.md`, plus external research on GDPR
Art. 28 / SCCs, India's DPDP Act and Rules 2025, the EU AI Act, US copyright registration policy,
§512 safe harbour, FTC AI-claims enforcement, LF contribution-licensing guidance, and web-scraping
liability.

---

## 0. Status of the document set

All nine legal documents carry **"STATUS: DRAFT — REQUIRES LEGAL REVIEW. NOT YET PUBLISHED OR IN
EFFECT."** and use bracketed placeholders: `[LEGAL ENTITY NAME]`, `[JURISDICTION]`, `[CONTACT EMAIL]`.

`AUDIT_REPORT.md` §9 enumerates what the docs themselves flag as **technically blocked**:

| Gap | Ledger status |
|---|---|
| Withdrawal/takedown state machine for published Commons content | **MISSING** (Phase 4) |
| Pre-publish confirmation UI (LC-009) | **Not found** in `frontendv1` |
| Scope labels PRIVATE/ORG/GLOBAL CANDIDATE/GLOBAL VERIFIED (LC-010) | `ScopeBadge` exists, full coverage **not verified end to end** |
| Audit-event coverage (`services/audit.py`) | **Incomplete caller coverage** as of 2026-09-08 |
| Organisation-scoped visibility enforcement | **Not fully reachable** from the live HTTP path |
| Provider/data-classification routing `can_send()` (LC-005) | **Not implemented**; routing governed by API-key presence and cost limits only |

That the audit is this honest is a genuine strength and the basis for everything below being fixable.

---

## 1. RISK 1 (highest) — the Commons republishes scraped content with no copyright clearance

**Your documents, in their own words.** `COPYRIGHT_AND_THIRD_PARTY_SOURCES.md` §1:

> "Part of the Global Commons is populated automatically from public internet sources
> (`backend/app/services/skill_ingestion.py`). … some procedures in the library were not authored by a
> StealthLab user, but extracted and normalized from something publicly published elsewhere."

§3:

> "StealthLab does not make a legal determination about the license status of ingested content at
> ingestion time — the admission gate … screens for safety and structural validity, **not** for
> copyright clearance. Do not assume any given piece of ingested content has been cleared for reuse
> just because it passed admission."

§7:

> "Publicly-sourced content ingested into the Global Commons is not automatically 'StealthLab-owned' by
> virtue of ingestion."

**External law.** Ropes & Gray, *Web Scraping in the Age of AI: Guidance for Data Owners and Scrapers*
(27 May 2026):

> "many web scrapers have faced lawsuits from owners of the scraped data alleging various legal
> theories. The recent emergence and widespread adoption of artificial intelligence (AI) systems has
> made web scraping more prevalent than ever… and **companies that engage in web scraping for this
> purpose may expose themselves to liability if they scrape data impermissibly**. Meanwhile, many
> businesses that publish content online have become concerned about their content being scraped by and
> for AI systems **and are seeking** [action]."

**Assessment.** This is the largest exposure, and it is worse than ordinary scraping risk because of the
next section. StealthLab does not merely scrape — it **republishes into a public, indexed, searchable
Commons** with provenance metadata but no license clearance, and the admission gate has been explicitly
scoped to exclude copyright. Retaining provenance (§2) and publishing a takedown route (§6) are the two
things that meaningfully reduce this; neither is a substitute for either clearance or an exclusion
policy.

**Questions for counsel:** (a) does the specific source mix in `skill_ingestion.py` carry licences that
permit redistribution? (b) is extraction-and-republication fair use / fair dealing in the US and India?
(c) should the launch gate *exclude* known-prohibited sources by default rather than admit and
provenance?

**Recommendation:** add a per-source admissibility rule (allowlist of licence-compatible sources, or an
enforced robots/ToS check) **before** any Commons launch. Provenance retention is not a licence.

---

## 2. RISK 2 (highest) — no takedown process forfeits §512 safe harbour

**Your documents.** `COPYRIGHT_AND_THIRD_PARTY_SOURCES.md` §6:

> "**[FOUNDER/LEGAL: stand up an actual takedown process before launch.]** At minimum this should
> include: a designated contact (**[CONTACT EMAIL]**), what information a notice must contain… and a
> defined response process (removal or quarantine pending review, notice to the contributor where
> applicable, and a counter-notice path). If StealthLab intends to rely on a formal safe-harbour regime
> (e.g. DMCA §512 in the US), counsel must confirm eligibility and register a designated agent — **not
> something this codebase audit can determine.**"

And: "the automated withdrawal state machine described in the launch-compliance spec **was not yet
implemented** as of this audit."

**External law.** US Copyright Office, *Section 512 resources*:

> "The safe harbors shield qualifying online service providers from monetary liability for copyright
> infringement **based on the actions of their users**, in exchange for cooperating with copyright owners
> to expeditiously remove infringing content and meeting certain conditions."
> "**Three of the four types of online service providers must comply with the requirements of the
> notice-and-takedown system in order to qualify for the safe harbors.**"

A compliant notice must contain: (i) signature of the copyright owner or authorised agent; (ii)
identification of the copyrighted work; (iii) identification of the infringing material and information
reasonably sufficient to locate it; (iv) a good-faith statement; (v) a statement under penalty of
perjury.

**Assessment — this is the compounding risk.** §512 protects an OSP for **its users'** infringement. The
scraped-and-republished Commons content is **StealthLab's own act**, not a user's upload. Safe harbour
does not reach it. So without both (a) a real takedown process *and* (b) a licence-admissibility policy,
the Commons exposes the company directly, and the missing takedown process removes the shield for the
user-contributed half as well.

**Recommendation:** a single small deliverable unlocks most of this — a designated-agent contact, a
five-field notice form, a quarantine-then-remove state machine, and a counter-notice path. All four are
mechanical. The state machine is already designed in the launch-compliance spec; it needs building, not
designing.

---

## 3. RISK 3 (high) — "Verified" is an accuracy claim the code cannot currently support

**Your documents.** `VERIFICATION_DISCLAIMER.md`:

> **Artifact validation** … "is the edited artifact even valid (e.g. does a `.py` file parse and import).
> **This says nothing about whether the code does what it claims.**"
> **Behavioral verification** … an "**opt-in**," capability-specific check that "only runs for
> capabilities that have a registered verifier" … and "a capability with no registered verifier is
> simply not behaviorally checked at all (**there is no universal, mandatory verification gate**)."

**Your own measurement.** Handoff §12: five cheap answers passed every check and were wrong (DS-1000 ×3,
BIRD ×2).

**External enforcement.** FTC, *Final order against Workado, LLC, which misrepresented the accuracy of
its AI content detection product* (28 Aug 2025):

> "requiring the company to **stop advertising the accuracy or efficacy** of its artificial
> intelligence (AI) content detection products **unless it has competent and reliable evidence** showing
> that those products are as accurate as claimed."

**External drafting guidance.** Bosin, *AI Warranty Disclaimers That Actually Hold Up (2026)*:

> "a disclaimer is not a magic incantation. It holds up when it is **conspicuous**, **specific to how AI
> actually fails**, **consistent with what the company tells the market**, and **paired with a narrow
> express warranty** the vendor genuinely stands behind. It fails when any one of those four legs is
> missing."

**Assessment.** The disclaimer is admirably honest, but honesty is not the test the FTC applies — the
test is **substantiation of the claim**. "Verified" next to a procedure whose only *mandatory* check is
parse-and-import is an accuracy representation without competent and reliable evidence, and the
disclaimer is a separate document the user has to find. Legs 1–3 of Bosin's test are arguably met;
**leg 4 — a narrow express warranty the vendor genuinely stands behind — is not met at all**, because
there is nothing the vendor is willing to stand behind.

**Recommendation, in order of cost:**
1. **Rename the automatic tier.** Something that cannot be mistaken for behavioural correctness —
   "Structurally Validated" or "Parsed" — reserving "Verified" for the behavioural gate. This is a
   product naming change and costs nothing legally.
2. **Make behavioural verification mandatory for the dispatched rung** (the plan's prerequisite from the
   previous audit), so "Verified" has a real referent.
3. Then, and only then, a narrow express warranty is available to write.

---

## 4. RISK 4 (medium-high) — the documents promise mechanisms that do not exist

`GLOBAL_COMMONS_TERMS.md` §2 states the pre-publish UI's content but records that **"As of this audit,
dedicated pre-publish confirmation UI in `frontendv1` was not found"** and says to "treat this document
as the authoritative statement of what publishing does **until that UI exists**." §8 documents withdrawal
that has no implementation.

**Assessment.** A terms document that describes a mechanism the product lacks is a representation about
the product. It is also self-defeating: the honesty that makes these documents valuable is undercut by
promises the code does not keep.

**Recommendation:** every bracketed gap either gets a "not yet available" banner on the public page, or
the corresponding claim is struck. Do not ship a term whose mechanism is a MISSING ledger item.

---

## 5. RISK 5 (medium) — processor agreements and international transfers

**Your documents.** `AUDIT_REPORT.md:167`: "Any claim about subprocessor DPA/SCC status — this audit could
only confirm which providers…" and `:225`: "Confirm subprocessor DPA/SCC status with each provider in
`SUBPROCESSORS.md` if any". `SUBPROCESSORS.md` lists Supabase, Anthropic, OpenAI, Fireworks, Google,
Voyage AI, optional Sentry / General Compute / Ollama.

**External law — GDPR.** European Commission, *New Standard Contractual Clauses — Q&A*:

> "SCCs for the relationship between controllers and processors fulfil the requirements in Article
> 28(3) and (4) of [GDPR]… The use of the SCCs… **requires that the parties enter into a legally binding
> agreement** to abide by them. To this end, the parties need to **fill in the annexes to the SCCs and
> sign Annex I**, which form an integral part of the clauses."

**External law — transfers.** The EU-US Data Privacy Framework adequacy decision covers **US** recipients
only. **An India↔EEA flow is not covered by it.** India has no EU adequacy decision; SCCs (or another
Article 46 tool) are required for EEA personal data reaching an India-based processor.

**External law — India.** The **DPDP Act 2023 and DPDP Rules 2025 were notified on 13 November 2025**, the
**Digital Data Protection Board has been notified**, and an **enforcement timeline** was published
alongside. MeitY has additionally issued a **simplified compliance framework for start-ups and certain
data fiduciaries** (8 Dec 2025).

**Assessment.** Two distinct workstreams: (a) StealthLab as a **processor** for pilot customers — needs
Art. 28 clauses and a public DPA; (b) StealthLab's own **inbound** transfers. The India simplified
framework is worth reading before designing anything bespoke.

**Recommendation:** produce a standard customer DPA with SCC annexes. It is a template, not bespoke work,
and it is the single most-requested document in enterprise procurement. Also complete the subprocessor
DPA/SCC status check the audit already identified.

---

## 6. RISK 6 (medium) — contributors may not be able to make the representation the terms require

**Your documents.** `GLOBAL_COMMONS_TERMS.md` §4: *"You must own or control the content you publish."*

**External law.** US Copyright Office, *Copyright Registration Guidance: Works Containing Material
Generated by Artificial Intelligence*:

> "copyright can protect only material that is the product of human creativity… the term 'authorship'
> implies that, for a work to be copyrightable, it must owe its origin to a human being… **will not
> register works produced by a machine** or [other non-human creator]."

**Assessment.** If a user publishes a **machine-extracted** procedure, they may hold no copyright in it
and cannot truthfully make §4's representation. The terms currently assume every contributor is a human
author. This is a gap in the drafting, not a gap in the product.

**Adjacent:** the EU AI Act requires GPAI providers to establish copyright policies and publish training
content summaries. StealthLab is not a GPAI provider, so the obligation does not attach directly — but
**the ingestion pipeline is the analogous act and deserves an equivalent written policy**, particularly
given Risk 1.

**Recommendation:** split the contributor warranty by provenance class — human-authored, org-contributed
under an agreement, and machine-ingested. Only the first two can carry an ownership warranty.

---

## 7. RISK 7 (medium) — inbound contribution mechanism is undecided, and three contributor types need different ones

**External law.** Linux Foundation, *Contributions to Projects: DCO and CLAs — Policy and Best Practices*:

> "it is important to understand its inbound license(s) and contribution mechanisms and, **even if you are
> not contributing to a project, you may still want to understand how it handles its inbound contribution
> processes.**"

**Assessment.** `GLOBAL_COMMONS_TERMS.md` §5 is a **license placeholder**. The Commons has three distinct
inbound populations — (a) users authoring procedures, (b) ingestion pipelines extracting from public
sources, (c) organisations contributing under an agreement. A single inbound mechanism does not fit all
three: **a DCO is meaningless for machine ingestion**, and a CLA adds legal friction you may not want on
individual contributions.

**Recommendation:** decide per class. Human-authored contributions can ride on the Terms of Service
representation with a licence grant; org contributions need an explicit agreement; machine-ingested
content needs a source-admissibility policy instead (Risk 1). This reframes the plan's blocking item 1
from "one ruling" into three short instruments.

---

## 8. RISK 8 (low now, non-zero) — EU AI Act and the procurement checkpoint

**External law.** European Commission, *The enforcement framework of the AI Act*:

> "Other breaches, including of the obligations for GPAI models, may result in fines of up to **€15
> million or 3% of total worldwide annual turnover**, whichever is higher. Infringements involving
> **prohibited AI practices** are subject to the highest penalties, of up to **€35 million or 7%**…"
> The AI Office can issue RFIs; "**If a simple RFI's reply is incorrect or misleading, fines can be
> imposed.**"

**Assessment.** StealthLab is an integrator/deployer, not a GPAI provider, so the GPAI obligations do not
attach. "AI washing" — overstating what an AI system does — is a live prohibited-practice area and is
directly relevant to Risk 3.

**Commercially more important than legally.** ISO/IEC 42001:2023 "has moved from a vendor marketing claim
to an enterprise AI procurement checkpoint through 2025-2026, appearing as a stated or preferred
requirement in regulated-sector and EU AI RFPs." Note the caution in the same source: the certificate
is **necessary-not-sufficient** and buyers are being taught to demand "the evidence behind it (scope
statement, Statement of Applicability, certification body and accreditation, validity dates,
product-level AI risk assessment)."

**Assessment.** StealthLab's entire pitch is an evidence layer — an ISO 42001-shaped claim without the
certificate. That is an opportunity, not just a gap: the procurement discipline described (evidence
behind the certificate, control-baseline mapping to NIST AI RMF) is precisely what a replay/verify system
produces.

---

## 9. RISK 9 (medium, India-specific) — consumer protection and liability caps

**External law.** Consumer Protection Act, 2019 (statutory text): "consumer" includes a person who
"**hires or avails of any service for a consideration**"; **"defect" means any fault, deficiency or
shortcoming**.

**Assessment.** A B2B SaaS liability cap drafted on a US template should not be assumed to bind an
Indian consumer forum, and the deficiency-of-service route exists independently of contract. This is the
point where Indian counsel is genuinely needed rather than a template — I did not retrieve a source
resolving cap enforceability under the CPA, and I am not going to assert one.

**Question for counsel:** for a paid pilot in India, what is the enforceability of a liability cap and a
"no warranty of output accuracy" clause against a CPA deficiency claim, and does the answer differ
between a B2B pilot and the self-serve product?

---

## 10. Ranked action list

| # | Action | Risk closed | Cost | Owner |
|---|---|---|---|---|
| 1 | **Per-source admissibility policy for `skill_ingestion.py`** (allowlist, or enforced ToS/robots check) | 1 | Days | Engineering + counsel |
| 2 | **Takedown: designated agent, 5-field notice form, quarantine-then-remove, counter-notice** | 2 | Days | Ops + counsel |
| 3 | **Rename the automatic verification tier**; reserve "Verified" for behavioural | 3 | Hours | Product |
| 4 | **Make behavioural verification mandatory for the dispatched rung** | 3 | Weeks | Engineering |
| 5 | **Strike or banner every claim whose mechanism is a MISSING ledger item** | 4 | Hours | Founder |
| 6 | **Standard customer DPA + SCC annexes; complete subprocessor DPA/SCC status check** | 5 | Days | Founder + counsel |
| 7 | **Split the contributor warranty by provenance class** (human / org / machine) | 6 | Hours | Counsel |
| 8 | **Three inbound instruments instead of one placeholder** | 7 | Days | Counsel |
| 9 | **Indian counsel: CPA cap enforceability + deficiency route** | 9 | 1 call | Founder |
| 10 | **Read the ISO 42001 evidence-behind-the-certificate standard** and treat it as a product spec | 8 (commercial) | Hours | Product |

## 11. What I did not determine

- **Licence status of the actual ingestion source mix.** Requires reading `skill_ingestion.py`'s source
  list and checking each licence — the single highest-value legal check available and not done here.
- **Whether Indian copyright law permits the claims US fair use might not**, for extraction +
  republication. Needs Indian counsel.
- **Whether the CPA permits a liability cap** in a B2B context (Risk 9).
- **Whether the pilot lead's sector imposes sectoral rules** — the pilot is an analytics-firm-shaped
  customer, and if financial-sector customers follow, RBI's AI governance framework becomes a live
  constraint on the verification claims.
- **Privacy-policy retention.** `AUDIT_REPORT` notes a retention gap; I did not assess it.