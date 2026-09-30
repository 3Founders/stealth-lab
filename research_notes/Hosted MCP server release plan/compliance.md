# Launch-stage compliance basics: India-registered startup, hosted MCP server for coding agents (US / EU / India users), as of Sept 2026

> Not legal advice. Researched 2026-09-27 from web sources; several items below are flagged "lawyer needed". Law in force vs pending is marked explicitly.
>
> Service facts assumed (from the brief): Indian entity; remote MCP server; stores coding-agent queries and optional user "discoveries" (free text that may incidentally contain code, file paths, personal data) in Postgres (Neon, US region) on Google Cloud; sends text to LLM providers (Google Vertex AI + third-party OpenAI-compatible APIs) for judging; anonymous reads, account-based writes; optional telemetry.

## India: DPDP Act 2023 + DPDP Rules 2025 — status, commencement, obligations, penalties

### Takeaway
The DPDP Rules were notified 13 Nov 2025 with a phased start; the substantive fiduciary obligations (notice, consent, security, 72-hour breach reporting, erasure, children) are scheduled to bite on 13 May 2027, with a live MeitY proposal (Jan 2026) to pull that forward to Nov 2026 that I could not confirm was finalised. Until then the IT Act/SPDI Rules 2011 and — importantly and already in force — the CERT-In 2022 Directions (6-hour incident reporting, 180-day logs kept in India) apply.

### Cited Findings
- DPDP Act and DPDP Rules notified 13 Nov 2025, phased: Phase I immediate (Data Protection Board setup), Phase II 13 Nov 2026 (Consent Manager provisions), Phase III 13 May 2027 (all substantive compliance obligations). Until Phase III, IT Act 2000 and the 2011 Privacy (SPDI) Rules remain the governing law — [DLA Piper Data Protection Laws of the World: India](https://www.dlapiperdataprotection.com/?t=law&c=IN); same 18-month / 13 May 2027 date — [Scrut](https://www.scrut.io/post/dpdp-rules), [PIB notification doc](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf). (Minor date discrepancy: some sources say notified 14 Nov 2025 with milestones 14 Nov 2026 / 14 May 2027 — [India Briefing](https://www.india-briefing.com/news/india-dpdp-compliance-timeline-enforcement-2026-27-44740.html/), [Shardul Amarchand Mangaldas](https://www.amsshardul.com/insight/enforcement-of-the-dpdp-act-and-notification-of-the-dpdp-rules/).)
- PENDING: On 23 Jan 2026 MeitY proposed cutting the compliance window from 18 to 12 months (last date would become 13 Nov 2026), enforcing cross-border restriction provisions immediately, and implementing the government's power to call for information immediately; comments were due 4 Feb 2026 — [Mondaq / S.S. Rana](https://www.mondaq.com/india/data-protection/1773554/meity-plans-to-cut-short-dpdp-compliance-timeline-and-notify-cross-border-restrictions-for-sdfs), [Business Standard](https://www.business-standard.com/technology/tech-news/meity-may-cut-compliance-timeline-for-key-dpdp-rules-to-12-months-126012201293_1.html). Reporting says the proposal was aimed primarily at big tech, large BFSI firms and large social media intermediaries — [same Mondaq piece](https://www.mondaq.com/india/data-protection/1773554/meity-plans-to-cut-short-dpdp-compliance-timeline-and-notify-cross-border-restrictions-for-sdfs). Later 2026 sources still cite 13 May 2027 as the final deadline — [India Briefing](https://www.india-briefing.com/news/india-dpdp-compliance-timeline-enforcement-2026-27-44740.html/).
- Notice: must itemise the personal data and specified purpose, in clear and plain language, describe the goods/services, give links to withdraw consent and exercise rights, and explain how to complain to the Board. Consent must be "free, specific, informed, unconditional, and unambiguous" via "clear affirmative action" — [DLA Piper](https://www.dlapiperdataprotection.com/?t=law&c=IN).
- Security safeguards (Rules): encryption/obfuscation/masking/virtual token mapping; access controls; visibility via "appropriate logs, monitoring and review"; retain logs and personal data for at least one year for detection/investigation; backups/continuity — [DLA Piper](https://www.dlapiperdataprotection.com/?t=law&c=IN).
- Breach: notify affected Data Principals (nature, consequences, mitigation) and the Board without delay, then a detailed report to the Board within 72 hours of becoming aware — [DLA Piper](https://www.dlapiperdataprotection.com/?t=law&c=IN), [Scrut](https://www.scrut.io/post/dpdp-rules).
- Erasure: stop retaining when purpose no longer served, consent withdrawn, or erasure requested (subject to other laws) — [DLA Piper](https://www.dlapiperdataprotection.com/?t=law&c=IN).
- Children = under 18; verifiable parental consent required; no tracking, behavioural monitoring or targeted advertising of children — [DLA Piper](https://www.dlapiperdataprotection.com/?t=law&c=IN).
- Cross-border: negative-list model — "A Data Fiduciary may transfer personal data outside India except where the Central Government restricts such transfer" (Rule 15); SDFs may be required to keep specified data in India (Rule 13) — [K&S / RuleExpert summary via search; Rule 15 text at DPDPA.com](https://www.dpdpa.com/dpdparules/rule15.html), [DLA Piper](https://www.dlapiperdataprotection.com/?t=law&c=IN).
- Contact point: every Data Fiduciary designates a contact person for Data Principal queries; Significant Data Fiduciaries must appoint an India-based DPO, an independent data auditor, and run a DPIA/audit once every 12 months — [DLA Piper](https://www.dlapiperdataprotection.com/?t=law&c=IN).
- Penalties: up to INR 250 crore for failing to take reasonable security safeguards (the highest item in the Schedule); other items in the INR 50–250 crore range; per-contravention, can stack — [DLA Piper](https://www.dlapiperdataprotection.com/?t=law&c=IN), [iPleaders](https://blog.ipleaders.in/dpdp-rules-2025-operational-compliance-guide-for-indian-businesses/).
- Startup relief: the Act lets the Government notify exemptions for classes of fiduciaries including startups (e.g., from notice, accuracy, some rights), but this requires a notification — [DLA Piper](https://www.dlapiperdataprotection.com/?t=law&c=IN).
- IN FORCE NOW (since 27 Jun 2022): CERT-In Directions require service providers, intermediaries, data centres and body corporates to report specified cyber incidents within 6 hours of becoming aware, and to keep logs of all ICT systems for a rolling 180 days within Indian jurisdiction; commentators read this to reach cloud/app-layer providers — [CERT-In Directions PDF](https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf), [Trilegal](https://trilegal.com/wp-content/uploads/2022/05/2022-CERT-In-Directions-on-Reporting-Cyber-Incidents-1.pdf), [UpGuard](https://www.upguard.com/blog/indias-6-hour-data-breach-reporting-rule).

### Inferences
- A 2–4 person dev-tool startup is very unlikely to be designated a Significant Data Fiduciary (designation is by government notification, based on volume/sensitivity/risk); plan as an ordinary Data Fiduciary.
- DPDP's extraterritorial reach aside, the company is an Indian entity, so DPDP applies to its processing of data of Indian users collected digitally regardless of server location; US-hosted storage is fine under the negative-list model unless the US is ever restricted.
- Because full DPDP obligations are ≤8 months away (or possibly already due if the 12-month compression was adopted), it is cheaper to build to DPDP now than to retrofit: plain-language itemised notice, consent capture at account creation, withdrawal link, grievance contact, erasure path, 1-year log retention, 72-hour breach playbook.
- The one-year DPDP log minimum and the "delete when purpose served" rule pull in opposite directions for the stored "discoveries" text — define separate retention for security logs vs user content.
- CERT-In's 180-day in-India log requirement is the most likely current gap for a US-hosted stack; mitigation could be shipping a copy of security/access logs to a GCP India region (asia-south1) bucket. Lawyer needed.
- Children: a developer tool should state 18+ in ToS (DPDP defines child as <18, stricter than GDPR/COPPA) and avoid needing verifiable parental consent.

### Gaps
- Could not confirm whether the Jan 2026 MeitY proposal (12-month compression; immediate cross-border enforcement) was ever formally notified. Check eGazette / MeitY before relying on May 2027. **Lawyer needed.**
- Could not find whether any startup exemption under s.17(3) has actually been notified.
- The PIB summary document I fetched returned an unreliable machine summary (penalty figures inconsistent with the Act); figures above rely on DLA Piper instead. Exact Schedule line items should be read from the gazette text.
- CERT-In FAQs (2022) may have clarified whether foreign-hosted logs satisfy the rule; not verified here.

## GDPR for a non-EU company serving EU developers

### Takeaway
GDPR almost certainly applies via Art. 3(2)(a) (offering a service to people in the EU) once EU developers are targeted; the practical consequences are a GDPR-grade privacy notice, a lawful basis per purpose, an Art. 27 EU representative (the "occasional processing" exemption is read narrowly and a continuous SaaS is unlikely to fit), records of processing, a 72-hour breach process, processor DPAs with Neon/Google/LLM vendors, and a DPA offered to business customers. The EU–US DPF is valid law in 2026 but under appeal at the CJEU.

### Cited Findings
- Art. 3(2): GDPR applies to a controller/processor not established in the EU where processing relates to offering goods/services to data subjects in the EU or monitoring their behaviour in the EU — [EDPB Guidelines 3/2018 on territorial scope](https://www.edpb.europa.eu/sites/default/files/files/file1/edpb_guidelines_3_2018_territorial_scope_after_public_consultation_en_1.pdf).
- Art. 27 requires Art. 3(2) controllers to designate an EU representative in writing unless processing is "occasional, does not include, on a large scale, processing of special categories … or criminal data, and is unlikely to result in a risk to the rights and freedoms of natural persons" — [EDPB 3/2018](https://www.edpb.europa.eu/sites/default/files/files/file1/edpb_guidelines_3_2018_territorial_scope_after_public_consultation_en_1.pdf), [Legiscope](https://www.legiscope.com/blog/gdpr-article-27-non-eu-representatives.html).
- EDPB: "occasional" only if "not carried out regularly, and occurs outside the regular course of business or activity"; exemption interpreted narrowly — [DataRep summary of EDPB 3/2018](https://www.datarep.com/2019/11/15/edpb-finalises-guidelines-for-gdpr-article-27-eu-representative/), [GDPR Local](https://gdprlocal.com/what-is-an-article-27-representative/).
- Transfers: when an EU data subject discloses data directly to a non-EU controller, there is no "exporter" and hence no Chapter V "transfer"; but Chapter V does apply when an Art. 3(2) controller onward-transfers to a processor/controller in the same or another third country — [EDPB Guidelines 05/2021 (v2, final Feb 2023)](https://www.edpb.europa.eu/system/files/2023-02/edpb_guidelines_05-2021_interplay_between_the_application_of_art3-chapter_v_of_the_gdpr_v2_en_0.pdf), [DLA Piper Privacy Matters](https://privacymatters.dlapiper.com/2023/03/eu-final-version-of-the-edpb-guidelines-05-2021-on-the-interplay-between-the-application-of-art-3-and-the-provisions-on-international-transfers-as-per-chapter-v-of-the-gdpr/), [Goodwin](https://www.goodwinprivacyblog.com/2021/12/02/edpb-defines-a-transfer-under-the-gdpr/).
- DPF status: General Court dismissed Latombe's annulment action on 3 Sep 2025 (T-553/23), upholding adequacy decision 2023/1795; appeal lodged 31 Oct 2025 (C-703/25 P); as of mid-2026 no hearing date; DPF remains valid — [WilmerHale](https://www.wilmerhale.com/en/insights/blogs/wilmerhale-privacy-and-cybersecurity-law/20251201-european-court-of-justice-to-review-challenge-to-eu-us-data-privacy-framework), [EuropeanMartech 2026 status](https://europeanmartech.eu/blog/eu-us-data-privacy-framework-2026-status), [European Law Blog](https://europeanlawblog.eu/20d2hhrr/).
- Neon provides a DPA embedded in its terms (separately signable), with a platform-specific subprocessor list at neon.com/subprocessors — [Neon DPA](https://neon.com/dpa), [Neon GDPR blog](https://neon.com/blog/gdpr-compliance-and-neon).
- GDPR core texts (Art. 6 lawful basis, Art. 28 processor contracts, Art. 30 records, Art. 33 72-hour breach notification to the supervisory authority, Art. 34 notification to data subjects when high risk) — [GDPR on EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj).

### Inferences
- Controller vs processor: for individual developers using the hosted service, the startup is a **controller**. For business/team accounts that submit their employees' data or their code, the startup is likely a **processor** for that content and needs an Art. 28-compliant DPA (with subprocessor list and change-notice mechanism). Most B2B dev tools publish a click-through DPA.
- Lawful basis sketch: contract (Art. 6(1)(b)) for account and service delivery; legitimate interests (6(1)(f), with a documented LIA) for security logging, abuse prevention and anonymous-read rate limiting; consent (6(1)(a)) for optional telemetry and any non-essential cookies; if submitted "discoveries" are used to improve a shared corpus visible to others, disclose that clearly and consider consent or an explicit ToS licence plus opt-out. **Lawyer needed** on shared-corpus reuse.
- Transfers: EU users sending data directly to an Indian company is not itself a transfer (EDPB 05/2021), but the company's onward sharing to Neon (US), Google and US LLM APIs is Chapter V; rely on each vendor's DPF certification and/or SCCs in their DPA, and keep a short transfer risk note. If the DPF falls at the CJEU, SCCs are the fallback — choose vendors whose DPAs already include SCCs.
- Art. 27 representative: outsourced representative services exist at low annual cost; a small team launching a regular SaaS to EU users should assume it is needed at public beta (arguably at alpha if EU users are onboarded). Art. 30 records: the <250-employee exemption does not apply to non-occasional processing, so keep a simple RoPA spreadsheet.
- The "incidental personal data in free text" issue: stored discoveries may contain names/emails/file paths (e.g., `/Users/<name>/`). A redaction pass before storage and before sending to LLM judges is the strongest proportionate mitigation (the repo already has a redaction chokepoint on trace paths).

### Gaps
- Did not verify current fees for EU representative services, nor whether the EU Digital Omnibus changes to GDPR (e.g., breach threshold/timing) have been adopted; check before finalising.
- Did not verify Neon's underlying cloud (the brief says Google Cloud; Neon has historically run on AWS/Azure) — confirm which subprocessors actually appear in Neon's list.
- UK GDPR (separate Art. 27 UK representative) not researched.

## US: CCPA/CPRA and state laws, FTC expectations on AI claims

### Takeaway
CCPA almost certainly does not apply at launch (revenue threshold now $26.625M, or 100k+ California consumers/households, or 50%+ revenue from selling/sharing data); other state laws have similar volume thresholds. The live US risk is the FTC: benchmark/accuracy claims must be substantiated at the time made, per the 2025 Workado order.

### Cited Findings
- CCPA gross-revenue threshold for 2025–2026 is $26,625,000 (inflation-adjusted from $25M; global revenue, not California-only) — [Clym](https://www.clym.io/blog/ccpa-applicability-guide), [SecurePrivacy](https://secureprivacy.ai/blog/ccpa-requirements-2026-complete-compliance-guide).
- New CCPA regulations (cybersecurity audits, risk assessments, ADMT) took effect 1 Jan 2026 for covered businesses — [Ropes & Gray](https://www.ropesgray.com/en/insights/alerts/2026/01/californias-ccpa-cybersecurity-audit-rule-takes-effect-what-businesses-need-to-know), [Jackson Lewis](https://www.jacksonlewis.com/insights/navigating-california-consumer-privacy-act-30-essential-faqs-covered-businesses-including-clarifying-regulations-effective-1126).
- FTC final order against Workado (28 Aug 2025): advertised "98 percent accurate" AI detection; FTC testing found ~53% on general text; order bars efficacy claims unless backed by "competent and reliable evidence" at the time the claim is made and requires retaining that evidence — [FTC press release (final order)](https://www.ftc.gov/news-events/news/press-releases/2025/08/ftc-approves-final-order-against-workado-llc-which-misrepresented-accuracy-its-artificial), [FTC proposed order release](https://www.ftc.gov/news-events/news/press-releases/2025/04/ftc-order-requires-workado-back-artificial-intelligence-detection-claims).
- "Operation AI Comply" enforcement has continued under the current administration — [Benesch](https://www.beneschlaw.com/insight/one-year-in-ftcs-operation-ai-comply-continues-under-new-administration-signaling-enduring-enforcement-focus/).

### Inferences
- Marketing numbers like "X% improvement on SWE-bench" must be reproducible from retained artefacts (the harness run records), with the benchmark, model, sample size and conditions stated; avoid generalising a benchmark result to "makes your agent X% better". The repo's own rule "exit criteria are numbers" maps well onto FTC substantiation: keep the run logs that back every public claim.
- Even without CCPA, the FTC Act s.5 applies to the privacy policy itself: say only what you do (e.g., don't claim "we never store your code" if discoveries may contain code).

### Gaps
- Did not survey the individual state comprehensive privacy laws (~20 states) thresholds; they generally use consumer-count thresholds (commonly 100k) that a launch-stage tool would not meet, but this was not verified per state.
- COPPA not researched (mitigated by an 18+ ToS).

## EU AI Act: obligations for a dev-tool MCP service using third-party LLMs

### Takeaway
The startup is not a GPAI *model* provider (it uses third-party models), so the Aug 2025 GPAI obligations sit with Google/OpenAI-compatible vendors. It may be the *provider of an AI system* (its own branded service), which pulls in Article 50 transparency duties from 2 Aug 2026 — but the chatbot-disclosure duty has an "obvious from context" exception that almost certainly covers a tool used by coding agents, and the synthetic-content marking duty (Art. 50(2)) has an "assistive / doesn't substantially alter input" exception. Nothing high-risk. The AI literacy duty (Art. 4, softened by the Omnibus) is the only generally applicable item.

### Cited Findings
- Timeline: in force 1 Aug 2024; prohibitions + AI literacy from 2 Feb 2025; GPAI model obligations from 2 Aug 2025; Article 50 transparency from 2 Aug 2026 — [Gibson Dunn](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/).
- Digital Omnibus on AI is Regulation (EU) 2026/1744, in force since 27 Jul 2026; high-risk deadlines moved to 2 Dec 2027 (Annex III) and 2 Aug 2028 (Annex I); Article 50 largely unchanged, with an Art. 50(2) watermarking deadline of 2 Dec 2026 for systems already on the market — [Usercentrics](https://usercentrics.com/knowledge-hub/eu-ai-act-high-risk-delay-article-50-transparency-consent/), [Mayer Brown (Jul 2026)](https://www.mayerbrown.com/en/insights/publications/2026/07/eu-ai-act-news-digital-omnibus-on-ai-new-guidance-on-risk-classification-gpai-and-transparency-obligations), [Gibson Dunn](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/).
- Omnibus softened Art. 4: providers/deployers must "support the development of AI literacy" rather than ensure a specific level — [Gibson Dunn](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/).
- Art. 50(1) (providers): AI systems intended to interact directly with natural persons must inform them they are interacting with AI, unless "obvious from the point of view of a natural person who is reasonably well-informed, observant and circumspect". Art. 50(2) (providers of systems generating synthetic text etc.): machine-readable marking, except where the system performs "an assistive function for standard editing" or does "not substantially alter the input data" — [Article 50 text](https://artificialintelligenceact.eu/article/50/).
- A startup that builds a branded system on a third-party GPAI model can be the *provider* of that AI system, not just a deployer — [Praxikon](https://www.praxikon.com/en/posts/article-50-provider-deployer-transparency-eu-ai-act), [Stephenson Harwood](https://www.stephensonharwood.com/insights/the-roles-of-the-provider-and-deployer-in-ai-systems-and-models/). Commission has issued guidelines on Art. 50 transparency obligations — [European Commission](https://digital-strategy.ec.europa.eu/en/policies/guidelines-ai-transparency-obligations).

### Inferences
- Proportionate response: (1) say plainly in docs/ToS that outputs/judgments are AI-generated and which provider classes are used; (2) if the service returns generated text to humans (not just agents), add a machine-readable marker field (e.g., `"generated_by": "ai"` in MCP tool results) — cheap, and a defensible Art. 50(2) posture by 2 Dec 2026; (3) a one-page internal AI-literacy note for the team.
- LLM-judge scoring of user "discoveries" is not an Annex III use (not employment, credit, education, etc.), so high-risk obligations don't attach.

### Gaps
- Did not read the Commission's final Art. 50 guidelines/Code of Practice on marking text; whether machine-readable marking of text is expected for API outputs consumed by agents is unclear. **Lawyer needed if EU revenue becomes material.**

## Contracts and policies; LLM provider data terms to reflect

### Takeaway
Before any external user: ToS (with AUP), Privacy Policy, and a subprocessor list; add a DPA template before business customers. The privacy policy must accurately describe LLM-provider handling: on Vertex AI, zero data retention requires disabling the default 24-hour in-memory cache and getting an abuse-monitoring/prompt-logging exception; third-party OpenAI-compatible APIs vary and must be checked one by one.

### Cited Findings
- Vertex AI: cached contents stored up to 24 hours in the serving data centre by default; ZDR requires disabling data caching (per project) and opting out of abuse monitoring (per project or billing account) via a request form or invoiced billing; Google may log prompts to detect abuse of its AUP/Prohibited Use Policy — [Google Cloud: Generative AI and zero data retention](https://cloud.google.com/vertex-ai/generative-ai/docs/data-governance?authuser=0), [Medium walkthrough](https://goabego.medium.com/no-data-left-behind-how-to-setup-zdr-with-gemini-a9ff5caf1c71). Gemini Developer API (AI Studio) has a separate ZDR regime — [Google AI for Developers](https://ai.google.dev/gemini-api/docs/zdr).
- Neon DPA and subprocessor list available — [Neon DPA](https://neon.com/dpa).

### Inferences
- Minimum document set and what each must say:
  - **ToS**: 18+; licence the user grants for submitted discoveries (esp. if shown to others / used to build shared procedures); no warranty on AI output; liability cap; governing law (India) with arbitration; suspension for abuse; anonymous-read terms.
  - **AUP**: no secrets/credentials/regulated data in discoveries; no malware, scraping abuse, attempts to exfiltrate other users' data; comply with LLM providers' use policies (flow-down).
  - **Privacy Policy** (one document satisfying DPDP notice + GDPR Art. 13): data categories (account, queries, discoveries, telemetry, logs), purposes and lawful bases, recipients/subprocessors (Neon, Google Cloud, Vertex AI, each third-party LLM API), transfer mechanism, retention per category, rights + how to exercise, grievance contact (DPDP), EU representative contact, Board/SA complaint rights, children (18+).
  - **Subprocessor page** with a change-notification mechanism (email/RSS).
  - **DPA template** (Art. 28 + SCCs module 2/3 references) when the first team/business customer asks.
  - **Cookie consent**: only if the marketing site/dashboard sets non-essential cookies or analytics; using cookieless analytics avoids an EU banner.
- Third-party OpenAI-compatible providers are the weakest link: many free/cheap endpoints reserve rights to log or train on inputs. For each, record retention, training-use, region, and DPA availability; route only redacted text, or exclude providers without a DPA from EU/business traffic.

### Gaps
- Did not verify training-use terms of specific third-party OpenAI-compatible providers the project uses (not named in the brief).
- Could not fetch the full Vertex data-governance page body (the fetch returned navigation only); the caching/abuse-monitoring details come from the search snippet of Google's page and a secondary walkthrough — re-verify on the live page.

## SOC 2 / ISO 27001 readiness at launch stage

### Takeaway
Nobody legally requires SOC 2; it becomes a sales blocker when mid-market/enterprise buyers send security questionnaires (typically when selling team plans). Type I costs roughly $28–58k all-in for a 10–50 person company (auditor alone $5–12k), with automation platforms at ~$6–15k/yr; a 2–4 person team should do the cheap groundwork now and defer the audit until a deal needs it.

### Cited Findings
- Typical SOC 2 Type I for a 10–50 person startup in 2026: $28k–58k all-in, 14–22 weeks, 240–380 internal hours; audit fee $5k–12k (boutique ~$14k vs Big-Four-adjacent ~$42k for the same Type I) — [Atlant Security (14 engagements)](https://atlantsecurity.com/blog/soc-2-type-1-timeline-cost-startup-2026).
- Platform pricing: Vanta ~ $10–12k/yr for sub-50-employee single framework; Drata Foundation ~$7.5–15k/yr; Sprinto ~$6–8k/yr for small simple-cloud startups, most $8–10k — [soc2auditors.org](https://soc2auditors.org/insights/vanta-alternatives/), [Sprinto cost page](https://sprinto.com/soc-2/certification-cost/) (vendor-published, treat as marketing). Drata's own cost guide — [Drata](https://drata.com/learn/soc-2/cost).
- Automation platforms speed evidence collection/gap analysis but do not shorten the audit itself — [Atlant Security](https://atlantsecurity.com/blog/soc-2-type-1-timeline-cost-startup-2026).

### Inferences
- Type I = design of controls at a point in time; Type II = operating effectiveness over a window (commonly 3–12 months). Buyers increasingly want Type II, so the "clock" matters: starting logging/access-review habits early lets a Type II window begin as soon as the team decides.
- Do now (near-zero cost, satisfies DPDP Rule security items too): SSO + MFA everywhere (Google Workspace, GitHub, GCP, Neon); least-privilege IAM and a quarterly access review recorded in a doc; centralised audit logs retained ≥1 year (DPDP) with a 180-day copy in India (CERT-In); encryption at rest/in transit (defaults on Neon/GCP); branch protection + code review; dependency scanning; backups with a tested restore; written incident-response plan covering 6h (CERT-In) / 72h (GDPR, DPDP) clocks; vendor inventory (doubles as subprocessor list and RoPA); short policies (infosec, access control, incident response, data retention, vendor management).
- ISO 27001 is the more common ask from EU and Indian enterprise buyers and is a certification rather than an attestation; Sprinto (India-based) and the others support both. Choose based on where the first large customers are.

### Gaps
- No primary AICPA source fetched on Type I vs Type II definitions; definitions above are standard but not cited.
- No verified ISO 27001 cost figures for a 2–4 person company.

## Proportionate checklist: private alpha / public beta / later

### Takeaway
Legally required now is small (accurate notice, security safeguards, incident reporting, honest marketing); most of the list is "strongly recommended" because DPDP's full obligations land by May 2027 (possibly earlier) and GDPR applies from the first targeted EU user.

### Cited Findings
- Deadlines driving the sequence: CERT-In 6h/180-day logs in force since 2022 — [CERT-In](https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf); DPDP substantive duties 13 May 2027 (possible earlier) — [DLA Piper](https://www.dlapiperdataprotection.com/?t=law&c=IN), [Business Standard](https://www.business-standard.com/technology/tech-news/meity-may-cut-compliance-timeline-for-key-dpdp-rules-to-12-months-126012201293_1.html); AI Act Art. 50 from 2 Aug 2026, Art. 50(2) marking by 2 Dec 2026 for existing systems — [Usercentrics](https://usercentrics.com/knowledge-hub/eu-ai-act-high-risk-delay-article-50-transparency-consent/); FTC substantiation — [FTC](https://www.ftc.gov/news-events/news/press-releases/2025/08/ftc-approves-final-order-against-workado-llc-which-misrepresented-accuracy-its-artificial).

### Inferences
**Before private alpha (invite-only)**
- Required/near-required: ToS + AUP (18+), Privacy Policy accurate to actual data flows, grievance/contact email; incident-response note with CERT-In 6h path; reasonable security (MFA, least privilege, encryption, logs); substantiated or no public performance claims.
- Strongly recommended: redact discoveries before storage and before LLM calls; enable Vertex ZDR (disable cache, request abuse-monitoring exception) or disclose the 24h cache/abuse logging; vendor inventory = subprocessor list = RoPA; sign/accept Neon and Google Cloud DPAs; decide retention periods per data category; telemetry opt-in (off by default).
- If EU alpha testers: lawful-basis table and GDPR rights wording in the privacy policy.

**Before public beta**
- Appoint EU Art. 27 representative (outsourced) if EU users are targeted; publish subprocessor page with change notice; publish DPA template (Art. 28 + SCCs); consent capture/withdrawal UI satisfying DPDP notice format; erasure/export flow; 72-hour GDPR/DPDP breach runbook; India copy of 180-day logs (confirm with counsel); AI-generated marker in tool outputs; cookie consent only if non-essential cookies; one-page AI-literacy note; lawyer review of ToS/Privacy/DPA (one-time, highest-leverage legal spend).
- Marketing: a claims file linking every published number to its run artefact.

**Later (on customer pull or scale)**
- SOC 2 Type I → Type II (or ISO 27001) when a deal requires it; pen test; DPIA if shared-corpus reuse of user content expands; re-check DPF after CJEU ruling in C-703/25 P; re-check US state thresholds and CCPA when revenue/users grow; DPDP SDF self-assessment if user numbers become large.

### Gaps
- Whether a lawyer is needed at alpha is a judgement call; the cheapest meaningful spend is a one-time review of ToS/Privacy/DPA before public beta, plus a specific opinion on CERT-In log localisation and on reusing user-submitted discoveries across users.
