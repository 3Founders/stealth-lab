# StealthLab Data Flow and Provenance Specification V1

**Date:** 2026-09-08  
**Product:** StealthLab  
**Repo:** `3Founders/stealth-lab`

> Engineering specification, not legal advice. Counsel must validate controller/processor roles, lawful bases, publication rights, licensing, and jurisdiction-specific deletion/retention rules.

## 1. Core product rule

StealthLab is a permissioned procedural capability and evidence system, not a generic memory dump.

```text
SOURCE → OBSERVATION → CLAIM → PROCEDURE → TASK → IMPLEMENTATION → EXECUTION → VERIFICATION → EVIDENCE
```

Every transition preserves provenance and scope.

## 2. Scopes

```text
PRIVATE
ORGANIZATION
GLOBAL CANDIDATE
GLOBAL VERIFIED
```

Private is user-controlled. Organization is tenant-controlled. Global Candidate is explicitly published but not independently verified. Global Verified requires independent execution evidence.

## 3. Scope transitions

### PRIVATE → ORGANIZATION

Requires authorized actor, organization membership, explicit publication action, privacy/confidentiality checks, and audit event.

### PRIVATE → GLOBAL

Requires authorized actor, explicit publication, source/dependency analysis, privacy/confidentiality checks, IP/license checks, sanitization, publication authorization, fresh Global Candidate, and provenance.

### ORGANIZATION → GLOBAL

Same, plus organization publication policy.

### GLOBAL CANDIDATE → GLOBAL VERIFIED

Requires independent execution evidence. Publisher evidence is not automatically independent global evidence.

## 4. Canonical provenance

Every derived object must be able to identify:

```text
object_id
object_type
scope
creator
organization
created_at
source_refs
parent_refs
dependency_refs
derivation_method
extraction_version
source_license
publication_authorization
sanitization_record
privacy_classification
content_hash
status
```

Reuse the existing graph/provenance substrate rather than creating a second provenance graph.

## 5. Source classes

```text
USER_PRIVATE
ORG_PRIVATE
PUBLIC_WEB
PUBLIC_REPOSITORY
PUBLIC_DOCUMENTATION
PUBLIC_DATASET
STEALTHLAB_GENERATED
STEALTHLAB_EXECUTION
THIRD_PARTY_PROVIDER
```

Source origin constrains what can be derived and published.

## 6. Source vs claim vs procedure vs evidence

Keep these distinct:

```text
SOURCE FACT ≠ CLAIM ≠ EXECUTED RESULT ≠ VERIFIED PROCEDURE
```

Example:

```text
public repository
→ source
→ extracted claim
→ generalized procedure
→ execution
→ tests pass
→ evidence
→ verified procedure
```

Do not encode a source's marketing/documentation claim as StealthLab-verified capability without execution evidence.

## 7. Private flow

```text
repo/chat/trace
→ local collection
→ redaction/exclusion
→ private observation
→ private claim
→ private procedure
→ private execution
→ private evidence
```

Nothing in this path automatically enters the Commons.

## 8. Publication flow

Global publication is a launch feature:

```text
PRIVATE PROCEDURE
→ user clicks Publish
→ identity/authority
→ dependency traversal
→ privacy/confidentiality/IP/license checks
→ sanitization
→ GLOBAL CANDIDATE
→ independent retrieval/applicability
→ independent execution
→ GLOBAL EVIDENCE
→ GLOBAL VERIFIED
```

## 9. Publication must NOT

- copy private traces wholesale;
- publish credentials/secrets;
- publish unnecessary private repository URLs or paths;
- publish private execution logs unnecessarily;
- copy customer-specific code into a generic procedure;
- copy local verification counts into global verification;
- erase provenance;
- bypass source/license checks;
- silently publish after a successful execution.

## 10. Publication sanitizer

Inspect for:

### Secrets
API keys, access tokens, passwords, private keys, cloud credentials, cookies, signed URLs.

### Private environment data
Home directories, local paths, internal hostnames/IPs, private repository URLs, organization identifiers.

### Personal data
Email, phone, names where unnecessary, identifiers, customer data, personal access tokens, conversation content.

### Confidential information
Proprietary source code, internal architecture, customer information, unpublished business data, contracts.

Sanitization must be deterministic enough to test.

## 11. Dependency traversal

Before publication:

```text
procedure
→ procedure version
→ implementation(s)
→ task(s)
→ claims
→ observations
→ sources
→ execution/evidence lineage
```

Dependencies must be classified as `PUBLIC`, `PRIVATE`, `ORGANIZATION`, `MIXED`, or `UNKNOWN`.

If a required dependency is private and cannot safely be generalized/sanitized, publication fails closed or enters an explicit review state.

## 12. Mixed-provenance procedures

A procedure can combine public source material with private execution evidence only when private material can be removed without changing the public procedure's meaning and without exposing protected information.

Public provenance stays public. Private evidence remains private unless independently authorized for publication. Private verification does not become global verification merely because the procedure was published.

## 13. Global verification

Independent users execute in genuinely distinct contexts:

```text
Publisher A → Global Candidate
User B → retrieve → applicability → execute → evidence
User C → retrieve → applicability → execute → evidence
```

Context identity must prevent folder renaming or disposable-clone tricks from manufacturing independent evidence.

## 14. Context identity

Keep the repository's existing principle: use meaningful repository identity/commit/environment metadata rather than folder names as the primary identity signal.

## 15. Data classification

Use:

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

Classification drives retrieval, publication, provider routing, storage, export, deletion, and audit.

## 16. Retrieval boundary

Access control must happen before relevance ranking:

```text
query
→ identity
→ tenant
→ scope filter
→ dependency constraints
→ applicability
→ semantic ranking
→ capability ranking
```

Never retrieve broadly and then try to hide unauthorized results afterward.

## 17. Embeddings

Embeddings are derived data. Each embedding must retain or reference source object, scope, tenant, classification, model, model version, and creation time.

A private embedding must never become globally retrievable because it happens to be stored in a shared vector index. Deletion must cover private embeddings/indexes.

## 18. Model-provider boundary

Before an external model call:

```text
object
→ classification
→ provider policy
→ tenant policy
→ region/transfer policy
→ allow/deny
```

Log provider, model, decision, policy version, data classes, tenant, and timestamp.

## 19. Execution data

Separate:

```text
RAW EXECUTION TRACE
DERIVED EXECUTION SUMMARY
VERIFICATION RESULT
EVIDENCE
```

Do not retain raw execution material forever merely because evidence is retained.

## 20. Deletion semantics

Deletion must follow dependency lineage.

Private trace deletion may affect observations, claims, private procedures, embeddings, indexes, and caches.

Global objects require different treatment: independently sourced public knowledge should not necessarily disappear when one user leaves; global objects containing personal/proprietary material must be remediated or deleted as required.

## 21. Tombstone vs physical deletion

Distinguish:

```text
TOMBSTONED
NO LONGER RETRIEVABLE
PHYSICALLY DELETED
RETAINED UNDER LEGAL HOLD
RETAINED AS NON-PERSONAL HISTORICAL EVIDENCE
```

Do not claim “deleted” when the object is merely hidden. Do not destroy immutable historical evidence simply because a current procedure became stale.

## 22. Export semantics

Export should cover account/settings, private procedures/claims, retained private traces, repository connections, user-visible execution/evidence, publication actions, and relevant preferences.

Global public knowledge is not presented as privately owned merely because the user published it; publication history/references are exported instead.

## 23. Organization semantics

Organizations need policy controls such as:

```text
can_publish_global
can_connect_repository
can_execute
can_view_org_knowledge
can_delete_org_data
can_manage_members
can_manage_provider_policy
```

A user cannot publish organization-owned knowledge merely because they personally have an active session.

## 24. Global contribution record

Every publication should produce a durable contribution record containing:

```text
publication_id
source_object_id
published_object_id
actor_user_id
organization_id
destination_scope
authorization_timestamp
sanitization_version
provenance_version
source_license
review_state
withdrawal_state
```

This record is the audit/provenance proof of how the object entered the Commons; it is not the procedure itself.

## 25. Withdrawal

A publisher can request withdrawal. The system must traverse dependencies and determine whether the result is:

```text
WITHDRAWN
WITHDRAWN_FROM_RETRIEVAL
REQUIRES_REMEDIATION
RETAINED_AS_INDEPENDENTLY_SOURCED
```

Do not silently rewrite historical evidence.

## 26. Source/license model

For public sources retain:

```text
source_url
retrieved_at
content_hash
publisher
published_at
license
license_url
attribution
usage_constraints
source_status
```

Useful source states:

```text
RESOLVED_VERIFIED
RESOLVED_UNVERIFIED
SECONDARY
MODEL_INFERRED
UNRESOLVED
```

Only source-supported facts are source facts.

## 27. Frontend information architecture

Procedure page:

```text
Scope
What it does
Applicability
Provenance
Sources
License
Verification
Independent evidence
Implementations
Publish / Withdraw
```

Private procedure: show `PRIVATE`, owner, and `Publish to Global`.

Organization procedure: show organization scope and the organization's publication policy.

## 28. Backend service boundaries

Prefer canonical services such as:

```text
AccessPolicy
PublicationService
ProvenanceService
ClassificationService
DependencyService
ProviderPolicyService
DeletionService
ExportService
AuditService
```

Extend existing access/evidence/procedure services instead of duplicating them.

## 29. Database expectations

Before adding tables, inspect and reuse the existing substrate: `procedures`, `procedure_dependencies`, `procedure_implementations`, `claim_sources`, `observations`, `knowledge_nodes`, `edges`, `executions`, `evidence`, `implementations`, `implementation_tasks`, `ingested_artifacts`, `ingestion_runs`, `organizations`, `org_memberships`, `users`, and `roles`.

Potential additive concepts are `publication_records`, `data_classifications`, `model_provider_policies`, `data_requests`, and `audit_events`, but only if equivalent existing structures cannot be extended.

Do not build a second procedure registry, provenance graph, or audit system.

## 30. Required tests

### Scope leakage
- private procedure absent from global search;
- private embedding absent from global search;
- org procedure absent from another org;
- anonymous sees public only.

### Publication
- unauthorized publish fails;
- private procedure can publish;
- secrets/private paths are blocked or scrubbed;
- provenance/license survives;
- fresh global candidate is created;
- private evidence is not copied into global verification.

### Verification
- publisher cannot manufacture independent contexts;
- same repo/commit does not count as multiple contexts;
- failures reduce capability;
- stale procedures stop being selected.

### Deletion
- private source deletion removes private derived retrieval;
- embeddings/indexes are covered;
- independently sourced global object remains when appropriate;
- mixed-provenance object is remediated when required.

### Provider policy
- prohibited call denied;
- allowed call logged;
- region/policy version recorded.

## 31. Canonical invariants

**INV-01** Private data never becomes global without explicit publication authorization.

**INV-02** Access control is evaluated before relevance ranking.

**INV-03** A global publication is a new Global Candidate, not a copy of private verification status.

**INV-04** Global verification requires independent evidence.

**INV-05** Every derived object has provenance.

**INV-06** Public-source-derived objects retain source/license metadata.

**INV-07** Model calls occur only when data-routing policy allows them.

**INV-08** Deletion follows dependency lineage.

**INV-09** Historical evidence is not rewritten merely because a current procedure becomes stale or withdrawn.

**INV-10** Users can see whether an object is PRIVATE, ORGANIZATION, GLOBAL CANDIDATE, or GLOBAL VERIFIED.

## 32. Final architecture

```text
PUBLIC SOURCES ──→ provenance ──→ claims ──→ procedures ──→ GLOBAL CANDIDATE
                                                         ↑             │
USER/ORG PRIVATE ─→ private claims ─→ private procedures ─┘             │
       │                         │                                       │
       └──── stays private       └── explicit publish ──────────────────┘
                                                                       ↓
                                                        independent execution
                                                                       ↓
                                                             GLOBAL VERIFIED
```

The Global Commons is therefore a first-class launch subsystem whose safety model is:

```text
permission + provenance + sanitization + source/license controls + candidate state + independent evidence
```
