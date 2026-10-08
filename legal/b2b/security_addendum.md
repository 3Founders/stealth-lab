> **DRAFT TEMPLATE. Not legal advice.** For counsel review. Describes what exists today, and separately what is planned. Do not move an item from "planned" to "in place" until it is verified. Evidence is in `final_prod_docs/`.

# SECURITY ADDENDUM

Attached to the MSA between **[COMPANY LEGAL NAME]** (**Company**) and **[CUSTOMER LEGAL NAME]** (**Customer**). If it conflicts with the MSA on security, this Addendum controls.

## 1. Controls in place

| Area | Control |
|---|---|
| Authentication | OAuth 2.1 sign-in; every request carries a bearer token; the shared operator token is restricted to single-user deployments |
| Authorisation and isolation | One shared access function filters private and organisation content in the application; automated tests that one user cannot read or report on another's data. Row-level security policies are written in the database, but the services do not yet connect with a role they apply to, so isolation rests on the application checks today (see §2) |
| Session handles | A model-plan handle (`instance_key`) works only for the caller it was issued to |
| Untrusted content | Retrieved procedure text is screened (links, hidden characters, instruction phrases), escaped and marked as data, not instructions |
| Model calls (`call_model`) | Customer's own keys by reference; egress policy; server-address (SSRF) checks; worst-case cost reserved before each call; per-organisation and per-user budgets; kill switch; audit ledger of calls (without prompt text) |
| Consent page | Cannot be framed; warns on a localhost redirect or an unnamed app; users can remove an app's access |
| Secrets | Not stored in the repository; model keys not stored in the database; redaction of known secret shapes on stored traces (best effort) |
| Vulnerability handling | Published policy with fix times (critical 7 days, high 30, medium 90); weekly dependency audit and software bill of materials |
| Incident response | Written procedure; customer notice within 48 hours of confirmation; statutory reports |

## 2. Planned, not yet in place

Independent penetration test; SOC 2 or ISO 27001; row-level security in force: the services connecting as the restricted least-privilege database role (created, not yet used by the services), which is what makes the database's row-level security policies apply; more than one running instance and failover; status page and staffed on-call; single sign-on and SCIM for Customer staff; hard-delete and export path; customer-visible audit export; a tested backup restore.

## 3. Customer's responsibilities

Keep its credentials and model keys secure; review the model's output before acting on it; configure Cursor or its agent to ask approval for tool calls; not send source code, secrets or personal data in task descriptions; tell Company of suspected misuse.

## 4. Reviews and evidence

On request and under NDA, Company gives the security whitepaper, the data-flow diagram, a completed security questionnaire and the most recent independent assessment summary **[if any]**. Customer may run its own non-destructive testing of its tenant on **[10]** days' notice and under rules of engagement agreed in writing; testing of shared infrastructure needs Company's written consent.

## 5. Changes

Company may change controls if the overall level of protection is not reduced, and will tell Customer of material changes.
