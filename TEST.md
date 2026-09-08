
paste.txt

paste.txt

I’ve read paste.txt. It contains a detailed specification for a primary-source legal/compliance research memo for StealthLab, including India-first analysis, international privacy law, EU AI Act, copyright and open-source ingestion, UGC, model providers, code execution, data governance, technical controls, and a P0–P3 launch plan.

The requested output is too broad to research reliably in a single response—especially because it asks for law current as of September 2026, exact effective dates, and primary-source citations across seven jurisdictions. I can turn it into a rigorous, staged research project rather than produce an unsupported “everything at once” memo.

Recommended research sequence
Scope and assumptions

Confirm incorporation country, operating entities, launch countries, target users, B2B/B2C model, and whether community functionality launches now or later.

Separate the launch product from the long-term product thesis.

India foundation

DPDP Act and Rules implementation status.

IT Act/intermediary obligations where relevant.

Indian copyright, contract, consumer-protection, cybersecurity, tax/payment, and breach-notification issues.

India-specific launch gates and data architecture.

EU and UK

GDPR/UK GDPR territorial scope, controller–processor roles, DPIA/DPO/representative questions, transfers, deletion, cookies, and profiling.

EU AI Act classification and applicable dates.

DSA/UGC exposure if community procedures are enabled.

Copyright, OSS, and ingestion

Public-source ingestion policy.

Repository and license treatment.

Snapshots, embeddings, derived procedures, generated code, attribution, takedown, and license-change handling.

Separate policies for factual knowledge, copied code, and redistributed implementations.

US, Canada, Australia, and Singapore

Applicability thresholds and realistic startup exposure.

State privacy laws rather than an indiscriminate list.

Breach, consumer, UGC, transfer, and AI-specific obligations.

Technical implementation

Data inventory and role matrix.

Scope and authorization model.

Privacy/deletion/export semantics.

Model-provider registry.

Frontend, backend, database, infrastructure, audit, and test requirements.

Launch approval package

P0/P1/P2/P3 launch plan.

Direct answers to the 50 StealthLab-specific questions.

Hostile review.

Primary-source register.

Important limitation
No legal memo can honestly answer “Can StealthLab legally launch today?” without several facts that the prompt leaves open:

Where StealthLab is incorporated and where personnel operate.

Whether the launch is B2B, B2C, or both.

Whether EU/UK users are actively targeted.

Whether private repositories are processed only on customer instructions.

Whether model providers retain or train on submitted content.

Whether public/community procedures are enabled at launch.

Whether StealthLab stores source snapshots or only metadata and extracted procedures.

Whether it executes code against customer repositories or only disposable environments.

Whether it makes decisions affecting people, employment, access, security, or other consequential outcomes.

Expected user count, revenue, and applicable US state thresholds.

Whether StealthLab acts as an intermediary for user-generated content.

The most important immediate design rule is:

Private data must never become global knowledge unless the source owner or authorized organization has granted sufficiently specific permission, the applicable contract and privacy disclosures permit the use, all relevant IP/license restrictions have been checked, the promotion is explicitly approved, and the system records an auditable, reversible publication decision.

That is a product-control requirement, not merely wording for a Terms of Service document. The system should enforce scope isolation, deny cross-scope retrieval by default, require explicit promotion, retain provenance and authorization evidence, and test that private content cannot enter global indexes accidentally.

If you want the memo produced from this prompt, the most reliable next step is to begin with Pass 1: India and launch-scope assumptions, then continue jurisdiction by jurisdiction and maintain a cited source register.

continue

SECTION 1 — EXECUTIVE VERDICT
Can StealthLab legally launch today? → YELLOW (launch possible with strict scope limits and documented gating).

Top 10 blockers/risks right now

India DPDP core obligations are not yet enforceable, but the framework is live — notice/consent, breach reporting, and data-principal rights become enforceable on 13 May 2027, yet you must architect for them now.

EU AI Act transparency duties are active from 2 August 2026 — if you offer an AI-facing product to EU users, Article 50 duties (chatbot disclosure, AI-content marking, deepfake notices) apply immediately to deployers and providers.

GDPR/UK GDPR extraterritorial scope — an India-incorporated SaaS can be in scope simply by “offering services” to individuals in the EU/UK; controller vs processor roles must be mapped per data flow.

US state privacy laws apply by threshold, not by incorporation — Texas and several others have no consumer-count threshold; California’s revenue threshold is ~$26.6M (2025–26 adjusted), but many states trigger at 35k–100k consumers.

Public-source ingestion is not “free for all” — GitHub ToS, API limits, Stack Overflow’s AI-training prohibition, and CC BY-SA share-alike obligations create concrete constraints on scraping, storing snapshots, and reusing code.

DMCA safe harbor requires a designated agent and notice-and-takedown workflow — if you host or republish user or third-party code/procedures, you need a compliant takedown system and repeat-infringer policy.

Model-provider data-use terms are contractual landmines — prompts/outputs sent to external LLMs may be retained or used for training unless explicitly opted out; this affects privacy claims and enterprise DPAs.

Private → global knowledge leakage is the single biggest product/legal risk — without explicit authorization, provenance checks, and technical isolation, you risk IP, trade-secret, and privacy violations.

Code-execution security is not just “best practice” — sandboxing, network isolation, and secrets isolation are required to credibly claim safety and to satisfy GDPR/DPDP “security safeguards” duties.

Children/minors policy must be explicit — DPDP has specific verifiable parental-consent rules; GDPR and US state laws add constraints. A default “no minors” policy is safest for a developer/AI platform.

SECTION 2 — STEALTHLAB DATA MAP (condensed, launch-relevant subset)
Data category	Personal?	Sensitive?	Typical legal basis (B2C)	Typical legal basis (B2B)	Controller/Processor (StealthLab)	Can enter GLOBAL?	Model-provider exposure?	Retention principle
Account (email, name, org)	Yes	No	Consent / Contract	Contract	Controller	No (by default)	Possible (auth, support)	Until deletion + legal hold
Org membership / roles	Yes	No	Contract	Contract	Controller	No	Possible	As above
Repository metadata (names, URLs)	Possibly	No	Legitimate interests / Contract	Contract	Mixed (often Controller)	Conditional (public only)	Possible	Minimize; align with source
Private repo source code / files	Yes (if linked to person) / Confidential	Potentially (secrets)	Explicit consent + contract	Contract + DPA	Processor for customer data	No unless explicit, license-checked, and authorized	High risk (prompts)	Customer-driven; strict deletion
Public repo code & docs	Possibly	No	Legitimate interests (factual)	N/A	Controller for derived knowledge	Conditional (license-permitted)	Possible	License-dependent; provenance required
Prompts / model inputs	Possibly	Potentially	Consent / Contract	Contract	Joint/Controller depending on flow	No	Yes (by provider)	Provider policy + contractual limits
Model outputs	Possibly	Potentially	Contract	Contract	Controller for stored outputs	Conditional	Provider may retain copies	Align with provider DPA
Tool calls / outputs	Possibly	Potentially	Contract	Contract	Controller/Processor mix	No	Possible	Minimize; security logs separate
Traces / execution logs	Possibly	Potentially	Contract + Legitimate interests (security)	Contract	Controller	No	Possible	Security retention + deletion rights
Embeddings of private code	Possibly	Potentially	Explicit consent + contract	Contract + DPA	Controller	No	Possible	Delete on source deletion unless independently sourced
Source snapshots (public)	Possibly	No	Legitimate interests (research)	N/A	Controller	Conditional (license/ToS)	Possible	License/ToS driven; takedown-ready
Billing / usage analytics	Yes	No	Contract + Legitimate interests	Contract	Controller	No	Possible	Statutory + business need
Support communications	Yes	Potentially	Contract	Contract	Controller	No	Possible	Support lifecycle + legal hold
Key rule: PRIVATE/ORGANIZATION data must not enter GLOBAL knowledge without explicit authorization, license compatibility checks, and an auditable promotion event.

SECTION 3 — JURISDICTION MATRIX (high-level applicability)
Jurisdiction	Applicability to StealthLab (India-first startup)	Primary laws (2026)	Main obligations that matter at launch	Trigger conditions	Launch impact
India	High (incorporation/operations)	DPDP Act 2023 + Rules 2025; IT Act (intermediary aspects)	Notice, consent architecture, security safeguards, breach intimation (72h to Board), data-principal rights, grievance redressal, cross-border transfer rules (pending orders)	Processing digital personal data of individuals in India; SDF designation if thresholds met	Must architect for 13 May 2027 enforceability; Board already active; Consent Managers from 13 Nov 2026. 
EU/EEA	Conditional (if offering AI services to individuals in EU)	GDPR; EU AI Act (Reg 2024/1689)	Lawful basis, transparency, DSARs, security, transfers (SCCs), DPIA if high risk; AI Act Art 50 transparency (chatbot disclosure, AI-content marking) from 2 Aug 2026	Art 3(2) GDPR: offering services/monitoring in EU; AI Act: provider/deployer roles	If EU users targeted, Article 50 duties apply now; high-risk AI obligations deferred to 2 Dec 2027 for Annex III standalone systems. 
UK	Conditional (if offering to individuals in UK)	UK GDPR; UK AI framework (no AI Act copy)	Similar to GDPR; transparency, DSARs, security, transfers	Offering services to UK individuals	Treat similarly to EU GDPR; monitor UK AI guidance. 
United States	Conditional (state-by-state thresholds)	CCPA/CPRA (CA), VCDPA (VA), CPA (CO), CTDPA (CT), UCPA (UT), TDPSA (TX), OCPA (OR), MCDPA (MT), DPDPA (DE), etc.	Privacy notice, consumer rights (access/delete/opt-out of sale/share), sensitive-data consent (some states), no selling/share without opt-out, data minimization	Revenue/consumer thresholds; TX has no consumer-count threshold; CT drops to 35k in July 2026	If you cross thresholds in any state, implement rights workflow and notices; many startups start with a unified US privacy notice and rights portal. 
Canada	Conditional	PIPEDA + provincial laws (e.g., Quebec Law 25)	Consent, purpose limitation, safeguards, breach reporting, access/correction	Offering services to Canadians	Implement baseline privacy controls; Quebec has stricter rules.
Australia	Conditional	Privacy Act 1988 + APPs; reforms ongoing	APPs (notice, use/disclosure, security, access/correction), breach notification (NDB)	Carrying on business in Australia	Baseline APP-aligned controls; monitor reforms.
Singapore	Conditional	PDPA	Consent/notification, purpose limitation, protection, access/correction, DND	Carrying on business in SG	Baseline PDPA-aligned controls.
SECTION 4 — LEGAL ROLE MATRIX (selected processing activities)
Processing activity	StealthLab role	Why
Account creation, billing, support	Controller (own service data)	You determine purposes/means for account/service ops. 
Processing customer’s private repo content on their instructions	Processor (for that content)	Customer dictates purposes; you provide the service. 
Ingesting public repos/docs to extract factual procedures	Controller (for derived knowledge)	You decide purposes/means of ingestion and knowledge creation. 
Running AI model calls with user prompts	Controller for your logs/outputs; Processor for customer data sent per their instructions	Mixed roles; must document per flow and provider terms. 
Publishing community procedures (UGC)	Intermediary/Platform (if hosting third-party content)	Potential safe-harbor/intermediary treatment if conditions met; requires takedown system. 
Profiling/recommendation for retrieval ranking	Controller; possible profiling under GDPR	You determine logic; assess Art 22 only if decisions are solely automated and produce legal/significant effects. 
SECTION 5 — PRIVACY REQUIREMENTS (India-first, with EU/US readiness)
India DPDP (as of Sept 2026)

Status: Act + Rules notified 13 Nov 2025; Board established immediately; Consent Managers from 13 Nov 2026; core obligations (notice, consent, security, breach, rights) from 13 May 2027.

What to build now:

Notice & consent flows aligned to Rule 3/Rule 5 (clear purpose, categories, rights, contact).

Security safeguards (Rule 6) and breach runbook with 72-hour intimation to Board + immediate user notice where required.

Data principal rights workflow (access, correction, erasure) ready by May 2027; architect deletion/export now.

Grievance redressal contact and process.

Cross-border transfers: currently permitted subject to future government orders; maintain transfer inventory and SCCs for EU/UK readiness.

GDPR/UK GDPR

Territorial scope: Applies if you offer services to individuals in EU/UK (Art 3(2)).

Core duties: Lawful basis, transparency (Arts 12–14), DSARs (access/erasure/etc.), security (Art 32), processor contracts (Art 28), transfers (SCCs + TIAs), breach notice (72h to SA).

AI-specific: Assess whether retrieval/ranking constitutes profiling; Art 22 only if solely automated decisions with legal/significant effects (often not for dev tools, but document the analysis).

US state privacy laws

Thresholds vary: CA ~$26.6M revenue or 100k consumers; TX no consumer-count threshold; CT 35k from July 2026; others 50k–100k.

Common obligations: Privacy notice, consumer rights (access/delete/opt-out of sale/share), sensitive-data consent (some states), no dark patterns, data minimization.

SECTION 6 — AI ACT / AI LAW REQUIREMENTS
GPAI obligations: Apply to providers of GPAI models from 2 Aug 2025; Commission enforcement from 2 Aug 2026.

Transparency (Art 50): Applies from 2 Aug 2026 to providers and deployers:

Chatbot disclosure (users must know they interact with AI).

Machine-readable marking of AI-generated audio/image/video/text.

Deepfake and certain public-interest text disclosures by deployers.

High-risk AI: Annex III standalone systems deferred to 2 Dec 2027; product-embedded high-risk to 2 Aug 2028.

StealthLab implication: If you deploy generative/interactive AI to EU users, implement chatbot notices and AI-content labeling now; high-risk conformity assessments likely not required yet unless your system fits Annex III.

SECTION 7 — COPYRIGHT / OSS / SOURCE INGESTION
Public GitHub: Respect ToS, API rate limits (unauthenticated 60/hr; authenticated 5k/hr; higher for enterprise).

Stack Overflow: Public content is CC BY-SA, but AI training/fine-tuning is prohibited without separate permission; ShareAlike applies to redistributed derivatives.

DMCA: If you host/republish code or procedures, maintain a designated agent, notice-and-takedown workflow, and repeat-infringer policy to qualify for safe harbor.

License classes:

Permissive (MIT/BSD/Apache): Generally safe for factual ingestion and code reuse with attribution/notice.

Copyleft (GPL/LGPL/AGPL): Safe to learn from; redistribution/derived code triggers source/notice obligations; AGPL especially strict for network use.

SSPL/proprietary: Treat as restricted; avoid automated ingestion without review.

Embeddings & derived procedures: Embeddings of copyrighted code may implicate reproduction/derivative rights; factual knowledge (“technique exists”) is safer than copying code.

Recommended ingestion policy (launch-safe)

Allowlist public sources with clear permissive licenses or explicit AI-use permissions.

Denylist sources prohibiting AI training/scraping (e.g., Stack Overflow for model training).

Store provenance (URL, license, snapshot hash) per artifact; implement takedown and license-change handling.

Do not redistribute code unless license permits; prefer storing factual procedures and citations, not full code blocks.

SECTION 8 — USER CONTENT / COMMUNITY
If you enable community procedure contributions, you function as a UGC platform/intermediary.

Must-haves: Contributor terms (IP license grant, warranties, indemnity), prohibited-content policy, moderation/takedown workflow, repeat-infringer policy, DMCA agent (US), and clear attribution/removal mechanisms.

Liability: Safe harbor depends on notice-and-action compliance; do not exercise editorial control over specific items without corresponding moderation obligations.

SECTION 9 — SECURITY / CODE EXECUTION
Legal baseline: DPDP security safeguards (Rule 6) and GDPR Art 32 require appropriate technical/organizational measures (encryption, isolation, access controls, testing).

Execution sandbox: Network isolation, secrets isolation, filesystem isolation, resource limits, timeouts, and outbound filtering are essential to avoid consequential harm and to support “secure” claims.

Certifications: SOC 2, ISO 27001/27017/27018, ISO 42001, NIST AI RMF/CSF are enterprise expectations/best practices, not legal mandates at launch.

SECTION 10 — MODEL PROVIDERS
Maintain a Model Provider Registry per provider/model with: provider, model, region, data residency, retention, training-use policy (opt-in/opt-out), subprocessors, DPA/SCCs status, security terms, confidentiality, deletion behavior, logging, and breach responsibilities.

Critical fact: Confirm whether prompts/outputs are used for training or retained; obtain written commitments for enterprise customers.

SECTION 11 — DATA RETENTION / DELETION / EXPORT
DPDP: Erasure/correction rights effective 13 May 2027; design cascading deletion, anonymization, and legal holds now.

GDPR: Right to erasure (Art 17), restriction, portability; backup deletion is nuanced (retain for legal/security but exclude from normal processing).

US states: Deletion rights vary; implement a unified DSAR portal with role-based export/delete.

StealthLab model:

Account/org deletion → cascade to private knowledge; preserve independently sourced global knowledge.

Repository disconnect → detach and delete private artifacts; retain audit logs under legal hold.

Embeddings of private data → delete or irreversibly unlink on source deletion unless independently sourced and license-permitted.

SECTION 12 — REQUIRED LEGAL DOCUMENTS
Document	Required?	Why	Jurisdiction	Must contain	Where surfaced	Backend dependency
Privacy Policy	Strongly recommended (legally required if in scope)	Transparency, lawful basis, rights	India/EU/US/etc.	Data categories, purposes, rights, contact, transfers	Public site + app	DSAR workflow, retention schedule
Terms of Service / AI Terms	Contractual requirement	Define scope, acceptable use, IP, disclaimers	Global	Service description, license grants, UGC rules, liability limits	Signup + public	Scope enforcement, deletion logic
DPA (for B2B)	Contractual requirement	Processor obligations, SCCs	EU/UK/India	Processing details, security, subprocessors, breach	Enterprise contracts	Subprocessor registry, transfer records
Cookie/Tracking Notice	Conditional	Consent for non-essential cookies	EU/UK/India (children)	Categories, consent management	Site banner/settings	Consent manager integration
DMCA/Takedown Policy	Conditional (if UGC/hosting)	Safe harbor	US	Agent contact, notice requirements, repeat-infringer	Public site	Takedown workflow, audit logs
Subprocessors List	Strongly recommended	Transparency (GDPR/enterprise)	EU/UK	Provider names, purposes, locations	Public or DPA	Vendor risk records
Security Policy / Vulnerability Disclosure	Best practice (enterprise expectation)	Security transparency	Global	Controls, reporting channel	Public site	Incident response process
SECTIONS 13–16 — FRONTEND / BACKEND / DATABASE / INFRASTRUCTURE (launch-critical subset)
Frontend

Privacy/Scope controls: explicit toggles for PRIVATE/TEAM/ORG/GLOBAL; confirmation before publishing knowledge globally.

DSAR portal: export/delete account, repository disconnect, data-sharing preferences, AI-improvement opt-outs.

Chatbot/AI disclosure banner (EU AI Act Art 50) if EU users are targeted.

Backend/API

Scope enforcement middleware: deny cross-scope retrieval; require explicit promotion event for PRIVATE→GLOBAL.

Provenance & license checks on ingestion; block redistribution where license forbids.

Model-provider call wrapper: log provider, region, training policy; enforce opt-outs.

Database

Add fields: data_scope, owner_id, organization_id, source_license, attribution_required, deletion_requested_at, deleted_at, legal_hold, provider_region, provider_training_policy.

Audit tables for publication events, takedowns, DSARs, and breach incidents.

Infrastructure

Sandboxed execution environments with network/secrets isolation; strict egress filtering.

Encryption at rest/in transit; role-based access; support access logging.

SECTIONS 17–19 — OPERATIONS, TESTS, LAUNCH PLAN (P0–P3)
P0 (block launch if missing)

Scope isolation + PRIVATE→GLOBAL authorization gate with audit.

Privacy Policy + Terms (AI/UGC clauses) + DPA template for B2B.

Model Provider Registry + training-use opt-outs documented.

Security baseline: sandboxing, encryption, access controls, incident runbook (72h DPDP/GDPR alignment).

EU AI Act Art 50 transparency (chatbot notice, AI-content labeling) if EU users targeted.

P1 (before beta/pilot)

DSAR workflow (export/delete), repository disconnect semantics.

Takedown system + repeat-infringer policy (if UGC).

Ingestion allowlist/denylist + provenance storage.

P2 (before scale)

Unified US privacy notice + rights portal (threshold monitoring).

SOC 2/ISO roadmap (enterprise readiness).

P3 (later)

Consent Manager integration (India, post-Nov 2026).

Advanced DPIA/audit automation.

SECTION 20 — HOSTILE REVIEW (“What are we missing?”)
Embedding leakage: Semantic retrieval across tenants via shared vector indexes.

Logs/traces containing another tenant’s code or secrets.

License drift: Source changes license after ingestion; no revalidation.

Model-provider retention: Prompts containing private code stored by provider despite your policy.

Children access: No age gate; DPDP parental-consent obligations triggered.

Overclaiming: Marketing says “verified/safe/private” without technical enforcement or evidence.

Cross-border transfers: No SCCs/TIAs while processing EU data.

SECTION 21 — PRIMARY SOURCE REGISTER (selected)
India DPDP Act 2023 + Rules 2025 — MeitY notifications; phased dates: Board immediate, Consent Managers 13 Nov 2026, core obligations 13 May 2027.

EU AI Act — Regulation (EU) 2024/1689; Art 50 transparency from 2 Aug 2026; GPAI obligations from 2 Aug 2025; high-risk deferred to 2 Dec 2027 / 2 Aug 2028.

GDPR — Articles 3, 5, 12–14, 17, 28, 32, 33, 35, 44–49.

US state privacy laws — CCPA/CPRA thresholds; TX no consumer threshold; CT 35k from July 2026.

DMCA — 17 U.S.C. §512(c)(3) notice elements; designated agent requirement.

GitHub ToS / API limits — Rate limits and API terms.

Stack Overflow — CC BY-SA licensing; AI-training prohibition in ToS.

SECTION 30 — DIRECT ANSWERS TO KEY STEALTHLAB QUESTIONS (condensed)
Store private GitHub contents? → CONDITIONAL — only as processor under customer contract/DPA; never as global knowledge.

Send private contents to LLM? → CONDITIONAL — only with explicit customer permission and provider terms allowing it; document retention/training policy.

3–4. Improve responses for same user/org? → CONDITIONAL — contract + explicit consent; isolate by scope.

Improve for other customers? → NO by default; requires explicit, specific authorization and license checks.
6–8. Abstract procedure / embedding / retrieval from private code? → CONDITIONAL — treat as derived data; default NO for global use; require authorization and deletion alignment.

9–10. Global procedure from private content? → NO unless explicit owner/org approval, license-compatible, and auditable promotion.
11–15. Public repos: ingest, snapshot, extract, store/generated, redistribute? → CONDITIONAL — allowed for factual knowledge; redistribution depends on license; respect ToS/rate limits.

Redistribute GPL-derived code? → CONDITIONAL — must comply with GPL (source, notice); often avoid automated redistribution.

17–20. License change / deletion requests / global procedure retention? → Implement takedown, revalidation, and deletion semantics; retain independently sourced global knowledge only if legally defensible.

21–24. Embeddings/backups/traces/model copies after deletion? → Delete or irreversibly unlink private-derived artifacts; retain backups under legal hold but exclude from normal processing.

25–26. Consent for AI improvement/training? → YES for private data; separate opt-in for model training is strongly recommended.

Prohibit minors? → RECOMMENDED for developer/AI platform; DPDP requires verifiable parental consent if allowing children.

Cookie banner? → CONDITIONAL — required in EU/UK for non-essential cookies; India children rules may require consent mechanisms.

DPO? → CONDITIONAL — GDPR DPO if core activities involve large-scale systematic monitoring or sensitive data; DPDP requires a contact/grievance officer.

DPIA? → CONDITIONAL — GDPR DPIA if high risk; DPDP SDF DPIAs from May 2027.

31–36. EU representative / India contact / US notice / DPA / SCCs / subprocessors page? → CONDITIONAL — depends on targeting and data flows; enterprise customers will expect all.

37–40. Takedown / DMCA agent / intermediary status / moderation? → YES if hosting third-party content; implement compliant workflow.

41–46. OSS license DB / provenance tracking / audit logs / explicit confirmation before PRIVATE→GLOBAL? → YES — technical controls required to make promises defensible.

Promises not to make unless enforced? → “Private,” “never used for training,” “verified,” “secure,” “isolated.”

Enterprise review fail triggers? → No DPA/SCCs, no sandbox isolation, no deletion/export, no subprocessor transparency.

Five most dangerous mistakes? → Private→global leakage, unapproved model training use, license-violating redistribution, missing takedown system, overbroad privacy claims.

Outside-counsel launch conditions? → Enforced scope isolation, documented model-provider terms, privacy/ToS/DPA suite, takedown workflow, security baseline, EU AI Act transparency (if EU users).

If you want, I can expand any single section (e.g., India DPDP build plan, EU AI Act transparency checklist, or the ingestion allowlist/denylist with concrete sources) into a deeper, source-linked appendix.