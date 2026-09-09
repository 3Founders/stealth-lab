# StealthLab Launch Compliance Specification V1

**Date:** 2026-09-08  
**Product:** StealthLab  
**Repo:** `3Founders/stealth-lab`

> Engineering/compliance specification, not legal advice. Counsel must review final legal documents and jurisdictional applicability.

## 0. Product decision: Global Commons is a launch feature

StealthLab **will let users explicitly publish procedures, implementations, claims, and related knowledge into the Global Commons at launch.** This is not a post-release feature.

Canonical flow:

```text
PRIVATE / ORGANIZATION
        ↓ explicit publish
publication policy gate
        ↓
privacy + confidentiality + IP/license + dependency checks
        ↓
sanitation
        ↓
GLOBAL CANDIDATE
        ↓ independent executions
GLOBAL VERIFIED
```

Private data must never become global merely because ingestion, extraction, embeddings, retrieval, or learning touched it.

## 1. Existing controls to preserve

The repository already has substantial foundations: local/private storage, explicit private→global semantics, centralized visibility/tenancy predicates, PostgreSQL RLS, OIDC support, provenance, evidence-backed verification, version-pinned evaluations, gated graph mutation, trace privacy/redaction work, secret-free execution descriptors, artifact validation, and staleness/applicability controls. Extend these; do not create parallel authorization/provenance systems.

## 2. P0 launch blockers

### LC-001 — Hosted repository authorization

The current code documents `find_best_way`'s `repo_path` as caller-controlled. That is acceptable only for constrained local/loopback use, not as the final hosted private-repository SaaS model.

Hosted execution must resolve:

```text
authenticated principal → organization → repository_id → authorized workspace → sandbox
```

Do not accept an arbitrary host filesystem path as the security boundary.

Required: repository membership checks, canonical path resolution, traversal/symlink protection, disposable workspaces, network/resource limits, secret isolation, auditability, and fail-closed authorization.

Acceptance tests: cross-user/org access denied; traversal/symlink escape denied; deleted/disconnected repository denied; unauthorized execution creates no execution; execution records principal/org/repository.

### LC-002 — Explicit private→global publication gate

Create one canonical publication operation. It must verify actor identity/authority, traverse dependencies, classify data, check privacy/confidentiality and source/license/IP policy, sanitize, record authorization, create a fresh Global Candidate, preserve provenance, and emit an audit event.

**Never copy private verification counts into global verification.**

### LC-003 — Provenance/dependency completeness

Every global candidate must identify: creator, organization, source refs, parent/dependency refs, derivation method, extraction version, source/license metadata, publication authorization, sanitization record, classification, content hash, and current status.

### LC-004 — Canonical data classification

Use one vocabulary:

```text
PUBLIC_SOURCE
PUBLIC_DERIVED
GLOBAL_PROCEDURE
ORG_PRIVATE
USER_PRIVATE
EXECUTION_SECRET
PERSONAL_DATA
CONFIDENTIAL_DATA
SECURITY_DATA
AUDIT_DATA
```

Classification must drive storage, retrieval, publication, provider routing, export, deletion, and audit policy.

### LC-005 — Model-provider policy enforcement

Create a provider/model registry containing at least:

```text
provider, model, endpoint, region, data_residency,
retention_policy, training_or_improvement_use,
subprocessors, DPA_available, transfer_mechanism,
delection_semantics, allowed_data_classes, effective_from,
effective_until
```

Implement a single decision point such as:

```python
can_send(data_classification, provider, model, tenant_policy) -> Decision
```

Deny calls when routing policy does not allow the data to leave its trust boundary. API-key existence is not authorization.

### LC-006 — Data deletion engine

Use one dependency-aware deletion service. It must handle private traces, observations, claims, procedures, implementations, embeddings, indexes/caches, exports, provider deletion requests where applicable, backups, and legal holds.

Do not blindly delete independently sourced public/global knowledge when a user leaves. Conversely, do not preserve a global object that materially contains private personal/proprietary data simply by relabeling it public.

### LC-007 — Export

Provide a machine-readable export workflow for account/profile, private procedures, retained traces, repository connections, execution/evidence available to the user, publication actions, and relevant preferences. Global public knowledge is not presented as privately owned; publication history/references are exported instead.

## 3. P1 launch requirements

### LC-008 — Privacy/Data UI

Settings → Privacy & Data:
- What StealthLab stores
- Export my data
- Delete my data
- Connected repositories / disconnect
- AI/model-provider settings
- Privacy contact

### LC-009 — Publication UI

Before publishing, show current scope, destination, what becomes visible, what private execution history stays private, provenance/source/license, and confirmation. Example: “Your private execution history will NOT become public. This creates a new Global Candidate.”

### LC-010 — Scope labels

Use explicit labels: `PRIVATE`, `ORGANIZATION`, `GLOBAL CANDIDATE`, `GLOBAL VERIFIED`.

### LC-011 — Audit events

At minimum: repository connected/disconnected; private object created/deleted; publication requested/approved/rejected; global candidate created; global procedure verified; scope changed; export/deletion requested/completed; provider call allowed/denied; incident opened/closed.

### LC-012 — Source/license gate

Public-source-derived global objects retain source URL, retrieval time, content hash, publisher/author where known, publication date, license, license source, attribution requirements, provenance, and derivation method. Keep separate states for source fact, extracted claim, StealthLab interpretation, and verified procedure.

## 4. Security requirements

Hosted execution requires disposable workspace, no Docker socket, controlled filesystem/network, CPU/memory/time/process limits, explicit secret injection, no ambient cloud credentials, artifact validation, cleanup, and audit trail.

Shared deployments require per-caller identity (OIDC or equivalent). A shared bearer token is not a substitute for per-user authorization.

Tenant tests must prove:

```text
User A → A private       ALLOW
User A → B private       DENY
Org A  → Org B           DENY
Anonymous → public       ALLOW
Anonymous → private      DENY
```

## 5. Legal/operational mapping

### India — DPDP

India's DPDP Rules 2025 were notified in November 2025 and use an 18-month phased implementation approach. Government material emphasizes purpose limitation, minimization, security safeguards, transparency, access/correction/removal rights, and breach notification.

Engineering: data inventory, purpose metadata, clear notices, authorization/consent records where consent is the applicable basis, rights workflow, security safeguards, breach workflow, retention/deletion, and a privacy contact.

Do not assume every processing purpose requires consent; counsel must map the appropriate legal basis.

### EU — GDPR

Where GDPR applies, document controller/processor roles per processing activity, rights workflows, security, processor contracts where applicable, and international-transfer mechanisms.

Engineering: data inventory/RoPA, DPA/subprocessor register, access/export/delete/restriction workflows, provider-region metadata, transfer register, incident handling, and DPIA assessment where required.

### EU — AI Act

Article 50 transparency obligations apply from 2 August 2026. Do not label every AI-generated artifact identically. Map each StealthLab feature to the actual applicable Article 50 obligation and document the decision.

### UK

Map UK GDPR controller/processor roles, processor contracts, individual rights, security, and restricted international transfers. Contractual deletion/return requirements must be reflected in the data lifecycle.

### US

Create a state-law applicability matrix. Engineering must support identity-verified requests, deletion, correction/access where applicable, appeals where applicable, and service-provider/processor contractual controls. Do not assume every state law applies to every user or deployment.

## 6. Legal/product documents

### Public
- Terms of Service
- Privacy Policy
- AI/Product Terms
- Security page
- Subprocessor list
- Copyright/IP and takedown contact
- Acceptable Use Policy

### B2B
- MSA
- DPA
- Security Addendum
- Subprocessor terms
- SLA if promised

### Internal
- Data inventory/RoPA
- Data-flow map
- Retention schedule
- Deletion SOP
- Rights-request SOP
- Incident-response SOP
- Model-provider register
- Source/license register
- Vendor-risk register
- Access-control policy
- Backup/deletion policy
- Publication policy

## 7. Global Commons policy

The Commons is a launch subsystem, not a later add-on. Initial model:

```text
permissioned contribution
+ provenance
+ sanitization
+ source/license controls
+ candidate state
+ independent evidence
```

A user may publish a procedure they own/control when the publication gate passes. An organization may publish according to organization policy. Private secrets, proprietary details, personal data, and private execution material must not leak. Anonymous instant-trust uploads are outside initial scope.

## 8. Release acceptance suite

### Privacy
- private procedures/embeddings absent from global retrieval;
- deleted private data is no longer retrievable;
- derived private stores are covered by deletion;
- backups obey documented lifecycle;
- export contains expected personal data.

### Publication
- unauthorized publication fails;
- publication creates a fresh global candidate;
- local evidence is not copied into global verification;
- secrets/private paths are scrubbed;
- provenance/license metadata survives;
- rejected publication leaves no global object.

### Provider routing
- forbidden data/provider combinations denied;
- permitted calls logged;
- region and policy version recorded.

### Execution
- arbitrary host paths rejected;
- traversal/symlink escape rejected;
- unauthorized repositories rejected;
- sandbox escape tests fail closed;
- ambient credentials unavailable.

### Audit
Every sensitive transition creates an attributable audit record.

## 9. Launch gates

### P0 MUST PASS
LC-001, LC-002, LC-003, LC-004, LC-005, LC-006, LC-007, execution isolation, tenant isolation.

### P1 MUST PASS before broad public launch
LC-008 through LC-012, shared authentication/authorization, legal-document review, incident-response rehearsal.

### P2 later
Open community uploads, public comments, marketplace, monetization, reputation economy, anonymous publication.

## 10. Current primary sources

- India PIB — DPDP Rules 2025: https://www.pib.gov.in/PressReleasePage.aspx?PRID=2190655
- India PIB — DPDP notification: https://www.pib.gov.in/PressReleasePage.aspx?PRID=2190014
- EU GDPR — EUR-Lex: https://eur-lex.europa.eu/eli/reg/2016/679/
- EU AI Act transparency guidance — European Commission: https://digital-strategy.ec.europa.eu/en/library/guidelines-transparency-obligations-providers-and-deployers-ai-systems
- UK ICO controllers/processors: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/controllers-and-processors/
- UK ICO processor contracts: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/contracts-and-liabilities-between-controllers-and-processors-multi/what-needs-to-be-included-in-the-contract/
- UK ICO international transfers: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/international-transfers/a-guide-to-international-transfers/
- California CPPA 2026 CCPA statute: https://cppa.ca.gov/regulations/pdf/ccpa_statute_eff_20260101.pdf

## 11. Final engineering rule

A policy is not implemented because a policy document says it.

```text
policy promise
→ data model
→ backend enforcement
→ frontend control
→ audit event
→ automated test
→ release evidence
```

If one is missing, mark the requirement incomplete.
