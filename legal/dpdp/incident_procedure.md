> **DRAFT.** An internal procedure that backs the MSA's 48-hour incident clause and the DPA breach clause. Fill the names and numbers. Check CERT-In and DPDP deadlines with counsel; they change.

# Security incident procedure

## 1. What counts

Any event that exposes, alters or loses customer data or credentials, or lets someone act outside their rights: a leaked key or token, cross-tenant access, an exposed database, malware, a compromised account, a model provider breach that touches our data.

## 2. Roles

| Role | Person |
|---|---|
| Incident lead | **[name, phone]** |
| Technical lead | **[name]** |
| Communications and customer contact | **[name]** |
| Legal contact | **[counsel]** |

## 3. Severity

| Level | Meaning | First action within |
|---|---|---|
| 1 | Confirmed exposure of customer data, or active attack | 1 hour |
| 2 | Credible suspicion, or exposure of internal data | 4 hours |
| 3 | Defect or near miss | 1 business day |

## 4. Steps

1. **Detect and record.** Open an incident record: time noticed, who, what. The "time noticed" starts the statutory clock.
2. **Contain.** Revoke the leaked credential (`service_identity revoke` or `disable`, `docs/security_runbook.md`), deactivate the account (`users.is_active=false`), rotate the database role, turn on the organisation kill switch for `call_model` if a model path is involved. Keep evidence: do not delete logs.
3. **Assess.** Which tenants, which data, how many people, whether personal data is involved, how long.
4. **Report to authorities.** A reportable cyber incident goes to **CERT-In within 6 hours of noticing**. Counsel decides whether the incident is a personal data breach that must be reported to the Data Protection Board and to affected people, as the DPDP Act and its rules provide.
5. **Tell customers.** Affected customers get written notice within **48 hours of confirmation** (the MSA and DPA figure), earlier if the customer's contract says so. The notice states what happened, what data, what we have done, what the customer should do, and a contact. Update every **[24]** hours until closed.
6. **Eradicate and recover.** Fix the cause, restore from a known good state, add a test that would have caught it.
7. **Review within 5 business days.** Write a post-incident report: timeline, cause, effect, fix, changes. Keep it with the incident record. Share a summary with affected customers on request.

## 5. Logs

Keep security logs for at least 180 days, in India if the law requires it for our setup (counsel to confirm). Logs never contain tokens, keys or prompt text.

## 6. Practice

Run a tabletop exercise once a year. Test one restore from backup once a year and record the result.

## 7. What we do not have yet

A staffed on-call, a status page, and an alerting setup. Until they exist, the 1-hour response time is the founder's commitment, not a service.
