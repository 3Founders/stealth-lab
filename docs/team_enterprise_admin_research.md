# Team and Enterprise admin pages: what is standard, what keळ should build

Date: 2026-10-31. Method: web search plus one primary-doc fetch (Cursor). Most of the search tool's answers came from summaries of vendor help pages and third-party guides, not full reads of primary pages. Every claim carries a source URL. Where only a third-party source was found, or sources disagree, it is flagged "unverified" or "sources disagree". Nothing here is legal or tax advice; the GST section must be confirmed with a chartered accountant.

## 1. Summary

1. Across vendors, SSO/SAML, SCIM, audit logs, and IP restrictions are mostly gated to Enterprise tiers. Basic MFA enforcement, roles, invites, and billing pages are available to small teams (Slack, GitHub, Cursor, Linear below).
2. Slack lets admins require 2FA on Free, Pro, Business+ and Enterprise Grid; with SSO the identity provider handles MFA (https://slack.com/help/articles/212221668).
3. GitHub removes members without 2FA when an owner turns on the requirement, so a grace/preview step is worth copying (https://help.github.com/articles/requiring-two-factor-authentication-in-your-organization).
4. Cursor splits as: SSO on Teams and Enterprise; SCIM and audit logs Enterprise only (https://cursor.com/docs/enterprise). This is the closest comparable to keळ and the likeliest split buyers will expect.
5. Google and Okta treat admin accounts as the highest-risk accounts: enforced 2SV, multiple security keys, a second super admin for recovery, and short sessions (sources in section 5).
6. Critics call gating SSO/SCIM the "SSO tax" and call these baseline security (https://1password.com/blog/explaining-the-backlash-to-the-sso-tax); others call it ordinary segmentation (https://alexgaynor.net/2025/feb/07/sso-tax-smart-business-bad-security/). For a 3-10 developer team the pragmatic answer is: MFA on for everyone, SSO as a paid add-on later.
7. keळ's biggest gaps for paying teams, in order: MFA for admin login, API/service-account keys with scopes, GST/INR invoices, retention settings, plan page. SSO/SCIM/domain verification/IP allowlist are Enterprise work that should wait for a named buyer.
8. The e-invoicing threshold is Rs 5 crore aggregate turnover since 1 Aug 2023 per Notification 10/2023-Central Tax (https://gstcouncil.gov.in/node/4365). Likely not binding on keळ at its current size, but invoice fields must still follow Rule 46 (section 6).
9. Sources are thin or conflicting on: Anthropic and Vercel plan gating, Atlassian MFA/retention, GitHub audit retention, SAC code mapping, NIST session numbers. These are marked below.
10. Do not claim SOC 2 until an auditor's report exists; publish an honest "in progress / not certified" trust page instead (section 8).

## 2. What each product's admin console contains (evidence)

### Slack
- Require 2FA for members and guests on Free, Pro, Business+, Enterprise Grid: https://slack.com/help/articles/212221668
- Enterprise Grid: 2FA runs alongside SAML via the IdP: same URL. Per Salesforce Trailhead, SSO is optional on Business+ and mandatory on Enterprise Grid; Slack's own MFA applies to guests and owners while the IdP covers full members: https://trailhead.salesforce.com/content/learn/modules/identity-management-in-slack/explore-access-and-security-controls
- SCIM deprovisioning for Business+ and Enterprise Grid: https://slack.com/intl/en-gb/help/articles/115004155306-Security-tips-to-protect-your-workspace . SCIM can manage IdP groups on Enterprise Grid (Trailhead link above).

### GitHub
- Org owners can require 2FA; non-compliant members, outside collaborators, and billing managers are removed and lose access: https://help.github.com/articles/requiring-two-factor-authentication-in-your-organization
- Audit-log search for removal reason `reason:two_factor_requirement_non_compliance`: https://docs.github.com/en/enterprise-server@2.22/admin/user-management/managing-organizations-in-your-enterprise/requiring-two-factor-authentication-for-an-organization
- Enterprise Cloud audit log: most events kept for a configurable period of up to six months, Git events seven days; streaming to Azure Event Hubs, Datadog, Splunk, S3, Azure Blob, GCS: https://resources.github.com/learn/pathways/administration-governance/essentials/access-capture-consume-audit-logs . Enterprise Server differs (indefinite by default): https://docs.github.com/en/enterprise-server@3.8/admin/monitoring-activity-in-your-enterprise/reviewing-audit-logs-for-your-enterprise/about-the-audit-log-for-your-enterprise . A "180 days" figure appeared only on a third-party blog (https://www.decryptiondigest.com/blog/github-organization-security-hardening-checklist) and conflicts with "up to six months"; treat as unverified.

### Cursor (closest comparable)
Fetched primary page https://cursor.com/docs/enterprise:
- SSO (SAML/OIDC): Teams and Enterprise. SCIM: Enterprise only. Audit logs: Enterprise only.
- Privacy Mode enforceable org-wide on both.
- Usage: Teams get an analytics dashboard; Enterprise adds AI Code Tracking API and Conversation Insights.
- Spend: Teams have team limits; Enterprise has pooled usage with admin-only controls.
- Admins can disable users' own API keys (BYOK).
- Page does not cover roles, IP allowlist, domain verification, or API-key management; the Network configuration docs mention IP allowlisting (unconfirmed which tier).
- Audit log event types per a search summary of Cursor docs: authentication, membership, permissions, API key actions, settings changes; SIEM streaming; OpenTelemetry export Enterprise only: https://cursor.com/docs/account/teams/enterprise-settings (summary only, not read in full). A BAA claim differs between the official page and third-party skill pages (https://claudeskills.info/skills/jeremylongshore/claude-code-plugins-plus-skills/cursor-compliance-audit/); official page wins.

### Linear
- SCIM requires SAML first; once SCIM is on, users cannot be managed inside Linear: https://linear.app/docs/scim
- Docs say SAML and IP restrictions are Enterprise only; a feature page says Plus plan has SAML, so sources disagree: https://linear.app/docs/saml-and-access-control , https://Linear.app/features/secure
- Audit log keeps about 3 months and is limited to workspace owners; on Enterprise the old admin role splits into owner and a limited admin: https://linear.app/security , https://linear.app/docs/changes-to-user-roles-when-upgrading-to-enterprise
- Guests only on Business and Enterprise: same.

### Notion (third-party sources only; unverified)
- SAML SSO on Business and Enterprise; SCIM, audit logs Enterprise only; domain verification Business and Enterprise; owners can still log in with email when SSO is enforced (recovery path); in-product audit log reportedly 90 days: https://howtoharden.com/guides/notion/ , https://www.resumelens.org/blog/notion/notion-enterprise-sso-scim
- Business pricing differed between sources, so no price is quoted here.

### Vercel
- Official docs list team roles Owner, Member, Developer, Security, Billing, Pro Viewer, Enterprise Viewer, Contributor; only Owners can assign roles: https://vercel.com/docs/accounts/team-members-and-roles/access-roles . A third-party guide lists a shorter set; official page is more current.
- SAML, SCIM, audit-log plan gating found only on third-party pages (unverified): https://www.stitchflow.com/user-management/vercel/manual

### OpenAI platform
- Org roles Owner and Reader; project roles Owner and Member; non-owners only see projects they belong to; only org owners see all projects, members, API keys: https://help.openai.com/en/articles/9186755
- Service accounts: created only by org/project owners, scoped to one project, key shown once, default read/write on project resources and permissions editable afterwards: same URL.
- Audit logs and zero-data-retention: not found in my searches.

### Anthropic (Claude Console / claude.ai)
- Domain Capture proves domain ownership and forces matching emails through SSO; section appears only after working with Sales; verification via WorkOS: https://support.claude.com/en/articles/10280258-setting-up-single-sign-on-on-the-api-console
- Console org-role count is unclear (one Anthropic page says five, a third-party guide lists six): https://platform.claude.com/docs/en/manage-claude/overview , https://howtoharden.com/guides/anthropic-claude/
- Compliance/audit logs cover auth, API-key management, user admin, SSO/SCIM sync actions; Admin API keys only for admin role: https://docs.datadoghq.com/integrations/anthropic_compliance_logs/
- Plan gating sources disagree (one says Team lacks SCIM, audit logs, custom roles, domain capture; another says Team has SSO and domain verification): https://www.fast.io/resources/claude-teams-plan-guide.md , https://wotai.co/blog/claude-sso-admin-settings . Unresolved.

### Atlassian Admin
- Org admin audit log on Guard Standard, Cloud Enterprise, Guard Premium; full app logs and SIEM integration on Premium; IP allowlists (Compass example) need Premium: https://www.resolution.de/?p=82137 , https://support.atlassian.com/security-and-access-policies/docs/accessing-audit-log-activities/
- 2FA enforcement and log retention: not found. Don't assume.

### Google Workspace Admin
- Admins must keep more than one security key enrolled; a second super admin can generate backup codes; in security-key-only mode users cannot self-generate backup codes; use a grace period when enforcing; Google enforces 2SV on admins and the only remedy for a non-enrolling admin is removing admin rights: https://knowledge.workspace.google.com/admin/security/recover-an-account-protected-by-2-step-verification , https://knowledge.workspace.google.com/admin/security/about-2sv-enforcement-for-admins
- Session length: not found.

### Okta
- MFA for Admin Console on by default; recommends a top-of-order policy forcing MFA re-auth on every Admin Console access; max session not exceeding 12 hours; cites NIST idle maximum of 15 minutes for admins: https://support.okta.com/help/s/article/best-practices-for-securing-okta-workforce-identity-cloud-admin-accounts
- Break-glass: each admin who may need the emergency password enrolls their own security key; service accounts that sign in to the console must also do MFA: https://support.okta.com/help/s/article/frequently-asked-questions-on-mfa-enforcement-for-the-admin-console

### The "SSO tax" debate
- Stitchflow's own analysis (vendor dataset, treat as advocacy) says 57% of 721 apps offer no SCIM and 1.2% include it on the base tier: https://www.stitchflow.com/blog/scim-vs-sso-tax-manual-provisioning
- SCIM is the part that matters most for offboarding: "SAML gets users in, SCIM gets them out safely" (same page, paraphrase).

## 3. Feature table by tier

Legend for "keळ today": Yes / Partial / No (from the list you gave; I did not inspect the code).
Tiering is my recommendation, informed by the evidence above, not a claim about any vendor's rule.

| Feature | Team: table stakes | Team: nice to have | Enterprise | keळ has it today? |
|---|---|---|---|---|
| **Sign-in security** | | | | |
| TOTP MFA for admins, enforced | X | | | No |
| Recovery codes (one-time, hashed) | X | | | No |
| Passkeys / WebAuthn security keys | | X (add key-only enforcement option) | X | No |
| Org-wide "require MFA for all members" with grace period | | X | X | No |
| Session length cap and idle timeout for admin console | X | | configurable | Unknown (not in list) |
| Step-up re-auth for sensitive actions | X (limits, keys, ownership) | | | No |
| Break-glass / second owner | X (2 owners rule) | | documented break-glass | No |
| SSO SAML/OIDC | | | X (or paid add-on) | No |
| SCIM provisioning | | | X | No |
| Domain verification / domain capture | | | X | No |
| IP allowlist | | | X | No |
| **Roles** | | | | |
| Owner / admin / billing / member | X | | | Partial (members exist; role set unconfirmed) |
| Viewer (read-only) | | X | X | Unknown |
| Billing role separated from technical admin | X | | | Unknown |
| Custom roles | | | X | No |
| **Member lifecycle** | | | | |
| Invite, resend, revoke | X | | | Yes |
| Deactivate, remove with key/session revocation | X | | | Partial (members yes; revocation of sessions/keys unconfirmed) |
| Transfer ownership | X | | | Unknown |
| Approval of join requests | | X | X | Unknown |
| Offboarding checklist (reassign keys, export) | | X | X | No |
| **Audit** | | | | |
| Audit log viewer (who/what/when) | X | | | Yes |
| CSV/JSON export | | X | X | Unknown |
| Retention setting for audit log | | | X | No |
| Streaming to SIEM/webhook | | | X | No |
| **Billing** | | | | |
| Plan, seat count, usage-based credits | X | | | Partial (usage and cost exist; plan/seats page no) |
| Invoices (INR, GST-compliant) download | X | | | No |
| Payment method, billing email | X | | | No |
| Tax IDs (GSTIN, place of supply) | X | | | No |
| Spend limits, alerts, kill switch | X | | | Yes |
| Purchase orders / invoicing by contract | | | X | No |
| **Data controls** | | | | |
| Data-retention settings | | X | X | No |
| Zero-data-retention option | | | X | No |
| DPA download, subprocessor list | X (links) | | X (signed) | Unknown |
| Export and deletion on request | X | | | Yes (legal holds, erasure requests) |
| Legal hold | | | X | Yes |
| **API / service accounts** | | | | |
| Org API keys: create, revoke, last-used, show once | X | | | Partial (provider keys; keळ-issued keys unconfirmed) |
| Service accounts with scopes, per-project | | X | X | No |
| Key expiry/rotation | | X | X | No |
| **Analytics and cost** | | | | |
| Usage and cost by person/model/day | X | | | Yes |
| Spending policy and limits | X | | | Yes |
| Denials / performance views | | X | X | Yes |
| **Compliance/support** | | | | |
| Trust page with honest compliance status | X | | | No |
| Security contact, incident process | X | | | No |
| Team-vs-Enterprise plan comparison page | X | | | No |
| Contributor credits | | X | | Yes |
| Provider keys (BYOK) management | X | | | Yes |

## 4. Detail per category

### (1) Sign-in security
- Standard: MFA enforceable by owners on small plans (Slack, GitHub above). Phishing-resistant keys are the strongest option; Google recommends two keys per admin.
- NIST SP 800-63B-4: sources disagree whether a phishing-resistant option is required at AAL2 or only strongly recommended; one blog gives 12h max session and 30 min idle for AAL2 but I could not verify against NIST text (https://www.wwpass.com/blog/phishing-resistant-mfa-in-2025-buyer-s-guide-to-nist-sp-800-63-4-omb-m-22-09/ , https://presencis.com/regulations/nist-800-63/article-22/). Okta's 12h/15 min figures (link above) are a safer cited anchor.
- Break-glass: a documented procedure, not a shared password (Okta link). For a small team the equivalent is a mandatory second owner plus printed recovery codes.
- SSO/SCIM/domain verification: Enterprise or paid add-on; Anthropic's domain capture is sales-gated (support.claude.com link above). Slack and Notion keep an owner email fallback when SSO is enforced (Notion via howtoharden; Slack via Trailhead's owner-MFA note).

### (2) Roles
- Observed role sets: OpenAI owner/reader (org), owner/member (project); Vercel eight team roles; Linear owner vs limited admin on Enterprise; GitHub has billing managers as a separate class (GitHub 2FA page).
- Recommendation: Owner, Admin, Billing, Member, Viewer. Billing cannot read prompts or traces; Admin cannot change billing or transfer ownership; only Owner assigns Owner/Admin (mirrors Vercel's owner-only role assignment).
- Custom roles stay Enterprise (Anthropic third-party source says Team lacks them; unverified).

### (3) Member lifecycle
- Invite, deactivate, transfer ownership are universal. Offboarding in vendors that support SCIM is IdP-driven (Slack, Vercel third-party source). Without SCIM, keळ must make deactivation revoke sessions and personal API keys atomically and write an audit event.
- No source found describing a standard "join request approval" UX; treat as optional.

### (4) Audit logs
- Retention seen: Linear about 3 months (linear.app/security), GitHub up to six months configurable (resources.github.com above), Notion about 90 days (third-party). Export and SIEM streaming are Enterprise (GitHub, Cursor, Atlassian Premium).
- Given keळ's bi-temporal "never delete" design, a longer default retention than competitors is a differentiator; make export (CSV/JSON) Team-level and streaming Enterprise.

### (5) Billing
- Cursor splits team limits vs pooled admin controls (cursor.com/docs/enterprise).
- India GST, with source conflicts noted:
  - Invoice content is governed by Rule 46 CGST Rules 2017; one guide says 21 mandatory fields, another 16, so check the rule text: https://www.mastersindia.co/blog/gst-invoice-e-invoice-credit-note-debit-note-guide/ , https://www.incorpx.io/blog/gst-invoice-rules-format-mandatory-fields
  - Place of supply and supplier/recipient GSTIN are required where applicable (same sources).
  - E-invoicing: Rs 5 crore threshold effective 1 Aug 2023 (https://gstcouncil.gov.in/node/4365). Other sources saying Rs 10 crore are older; I found no official notice of a change after 2023. Re-check at launch.
  - SAC code: sources conflict. One guide lists 998313 as cloud-hosted software/SaaS at 18%, 998314 as IT consulting, 998315 as infrastructure; this contradicts the brief's framing of 998314 as hosting. Another suggests foreign OIDAR invoices show 9983. Confirm the SAC with a CA and the official SAC list before printing it: https://www.mygstrefund.com/blog/gst-for-saas-companies , https://taxguru.in/goods-and-service-tax/oidar-services-taxability.html
  - 18% is the default rate in those sources. Whether keळ charges CGST+SGST or IGST depends on place of supply; I found no source in these searches, so that is an open question for the CA.
  - Required UX: collect legal name, GSTIN (validate format and checksum), billing address/state at checkout; show tax breakup; sequential invoice numbers; credit notes for refunds.

### (6) Security and data controls
- IP allowlist: Enterprise (Linear docs, Atlassian Premium, Cursor network docs mention it).
- Privacy Mode enforcement is a Team-level control at Cursor, which suggests keळ's equivalent (what trace data is retained) is a Team-level setting.
- DPA, subprocessor list, retention, ZDR: I found no primary-source survey in this pass. Treat the "Team links, Enterprise signed" split in the table as my recommendation, not a measured norm.

### (7) API keys and service accounts
- OpenAI pattern worth copying: service accounts are project-scoped, creation restricted to owners, secret shown once, permissions editable afterwards (help.openai.com link). Cursor lets admins disable members' personal BYOK keys.
- Audit events for key creation, use, and revocation are logged by Cursor and Anthropic (sources above).

### (8) Usage analytics and cost
- keळ already matches or exceeds the Team tier here. Gap: alerts at threshold percentages and a forecast; the spending kill switch is already a strength.

### (9) Support and compliance pages
- I found no authoritative source in this pass on how small vendors word SOC 2 status. My recommendation (unsourced opinion): state exactly what exists (policies, pen test, DPA) and what does not; use "not certified" until a report exists; never show a SOC 2 badge without one. This also matches the repo rule against unbacked claims (see b2b-pilot-copy-rules in memory).

## 5. Prioritized build order (first 10)

Effort: S = days, M = 1-2 weeks, L = 3+ weeks.

| # | Item | Effort | Why now |
|---|---|---|---|
| 1 | TOTP MFA for admin login + hashed recovery codes + enforce for Owner/Admin | M | Admin accounts hold kill switch, keys, billing. Google/Okta treat admin MFA as non-optional. |
| 2 | Session policy: absolute max (<=12h), idle timeout (15 min for admin console), step-up re-auth for sensitive actions | S | Anchored to Okta guidance; cheap once MFA exists. |
| 3 | Role set: Owner/Admin/Billing/Member/Viewer, two-owner rule, ownership transfer | M | Separation of billing from technical admin; recovery without break-glass password. |
| 4 | Plan page (Team vs Enterprise, seats, current usage, upgrade contact) | S | Cursor shows the shape; also anchors what is paid later. |
| 5 | keळ API keys / service accounts: scopes, show-once, last-used, expiry, revoke, audit events | M | Teams need CI keys; OpenAI pattern. |
| 6 | GST/INR billing: GSTIN capture, place of supply, invoice PDF per Rule 46, credit notes | L | Needs CA sign-off on SAC and CGST/SGST vs IGST first. Start e-invoice readiness only when near Rs 5 crore. |
| 7 | Audit log export (CSV/JSON) and stated retention; org-wide MFA requirement with grace period | S/M | Export is Team table stakes in my table; GitHub-style removal needs a grace step. |
| 8 | Data-retention setting (trace retention days) with legal hold interaction | M | Conflicts with the "nothing deleted" model; needs a design decision, see questions. |
| 9 | Passkeys/WebAuthn as MFA factor; key-only enforcement option | M | Strongest option; Google's guidance requires multiple keys per admin, so build recovery with it. |
| 10 | Trust page: subprocessors, DPA, security contact, honest compliance status | S | Needed in every enterprise questionnaire. |

Deferred until a named Enterprise buyer: SSO (SAML/OIDC), SCIM, domain verification, IP allowlist, SIEM streaming, custom roles, ZDR. Domain verification is a prerequisite for SSO enforcement (Anthropic domain-capture page).

## 6. Open questions for the founder

1. Retention vs "nothing is deleted": does a retention setting purge trace payloads while keeping ChangeSet audit rows? (My suggestion; no vendor source.)
2. Does keळ sell as an Indian entity charging 18% GST, and does it have a GSTIN? CA must settle SAC and tax type.
3. Will SSO be an add-on price or Enterprise only? The SSO-tax debate links in section 2 give the arguments both ways.

## 7. Standard admin login: step by step

This is a synthesis of the Google, Okta, GitHub and Slack sources above, not a single vendor's flow. Numbers marked with a source are cited; others are my recommendation.

1. Admin enters email; server returns the same response whether or not the account exists (no enumeration).
2. First factor: password (or email magic link, or SSO if the domain is enforced). If SSO is enforced, the IdP does MFA, and one owner keeps an emergency email path (Slack/Notion pattern; Notion via howtoharden).
3. Second factor: TOTP code or a security key/passkey. Rate-limit attempts and lock out on repeated failures.
4. If no second factor enrolled and the account is Owner/Admin: force enrollment before reaching the console. Show recovery codes once; store only hashes. Require enrolling a second factor or key (Google: more than one key: https://knowledge.workspace.google.com/admin/security/recover-an-account-protected-by-2-step-verification ).
5. Issue a session: absolute cap <=12 hours for admins and an idle timeout (Okta cites 12h max and 15 min idle: support.okta.com best-practices link). Bind to cookie flags HttpOnly/Secure/SameSite.
6. Admin console always re-authenticates with MFA on entry, as Okta recommends for its Admin Console.
7. Step-up (fresh MFA within a few minutes) before: changing spending policy or kill switch, creating/revoking API keys, changing roles, transferring ownership, editing billing or tax details, disabling MFA, exporting data, erasure requests.
8. New-device or new-IP sign-in sends an email to the user and writes an audit event.
9. Lost factor: a different Owner can issue one-time backup codes (Google's second super admin pattern, same Google link). Solo owner: documented identity-proofing path handled by keळ staff, logged, with delay and email notice. This is the break-glass equivalent; Okta Support can grant temporary password-only console access that expires automatically (support.okta.com FAQ link above).
10. Enforcement rollout: warn, grace period, then enforce. GitHub removes non-compliant members immediately (help.github.com link), so show a preview of who would be affected first.
11. Every step writes to the audit log with actor, IP, user agent, factor used, outcome.

## 8. Source quality notes

- Strong: cursor.com/docs/enterprise (fetched), gstcouncil.gov.in/node/4365 (found in search results, not fetched in full), help.openai.com article 9186755, Okta and Google support pages (search summaries of the primary pages).
- Weak or secondary: howtoharden.com, resumelens.org, stitchflow.com, claudeskills.info, and the GST blogs.
- Not found at all in this pass: Slack/GitHub audit-log retention for non-Enterprise tiers, Atlassian 2FA enforcement, OpenAI audit logs and ZDR, Vercel SSO/audit gating from primary docs, Google session-length settings, any SOC 2 wording survey.
- Re-verify before shipping: e-invoice threshold, SAC code, Rule 46 field list.
