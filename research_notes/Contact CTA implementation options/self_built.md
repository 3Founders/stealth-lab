# Self-built contact-us submission pipeline (Supabase + Turnstile + FastAPI, no CRM)

Research date: 2026-10-28. Coverage is partial: several primary pages (ICO, OWASP header-injection, Postmark/SES DPAs, Supabase RLS/Turnstile-Edge docs) were not retrieved. See Gaps under each section.

## Which design is least work and safest for a 2-5 person team already on Supabase and FastAPI?

### Takeaway
Design C (FastAPI endpoint: verify Turnstile, insert lead, notify) looks like the least new surface and the safest, because the secret key, DB credentials and email API key all stay in one server you already operate. Design B is viable but adds Edge Function and webhook moving parts with no documented delivery guarantee. Design A adds a third runtime with no benefit when FastAPI exists. This ranking is my inference, not a sourced finding.

### Cited Findings
- Server-side Siteverify is mandatory. The widget alone does not protect a form, and only your backend should call the API, since an exposed secret key lets attackers bypass the check. — [Cloudflare Turnstile server-side validation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)
- Turnstile tokens are valid 300 s, single use (replay returns `timeout-or-duplicate`), max 2048 chars. Siteverify returns `hostname`, `action`, `cdata`, `challenge_ts`. Optional `remoteip` and `idempotency_key` (UUID, makes retries safe). Docs advise short timeouts (example: 5 s connect, 10 s total). — [Cloudflare](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)
- Supabase Database Webhooks are a wrapper around triggers using `pg_net`; HTTP is asynchronous so it does not block writes; payload has `type, table, schema, record, old_record`. The page states no delivery guarantee and does not describe retries. Call history can be inspected in the `net` schema. — [Supabase Database Webhooks](https://supabase.com/docs/guides/database/webhooks)
- Webhook secret caveat: with the Vault approach, any role that can log in can read the secret key while the request waits in `net.http_request_queue`; the Dashboard option stores the key in the trigger definition, so it appears in database dumps. — [Supabase Database Webhooks](https://supabase.com/docs/guides/database/webhooks)
- Supabase Edge Functions hosted limits: 256 MB memory, 2 s CPU per request, wall-clock 150 s Free / 400 s paid, 504 on 150 s idle. — [Supabase Edge Function limits](https://supabase.com/docs/guides/functions/limits). A Mintlify-hosted page gives conflicting numbers (10 s Free, 512 MB); treat it as unreliable — [mintlify](https://www.mintlify.com/supabase/supabase/functions/overview).
- Supabase's own Resend example uses `--no-verify-jwt` on deploy and accepts a signed-in user JWT or a secret key; API key is set as an Edge Function secret. — [Supabase send-emails guide](https://supabase.com/docs/guides/functions/examples/send-emails)
- Resend free plan: 100 emails/day, 3,000/month, quota counts sent and received, each To/CC/BCC recipient counts separately, 2 requests/s default. — [Resend quotas](https://resend.com/docs/knowledge-base/account-quotas-and-limits)
- Free-plan behaviour at the cap (sending pauses, no overage) is from a secondary July 2026 review only. — [WPMailSMTP review](https://wpmailsmtp.com/resend-review/)

### Inferences
- C: one deploy target, one secrets store, normal Python tests, structured logs, and rate limiting can use the same code as the rest of the API. Retries/outbox can be done with a `notified_at` column and a retry job.
- B: the anon-insert path needs an Edge Function in front anyway (to verify Turnstile), so B is really "Edge Function + table + webhook", i.e. more parts than C. Webhook with no stated delivery guarantee means a missed notification needs a reconciliation job or an admin list as the source of truth.
- A: needs somewhere to store leads or it loses them if the email provider fails; email-only is least durable.
- Make the DB row the source of truth and email/Slack a best-effort notification in all three designs.

### Gaps
- Could not verify Supabase Edge Function pricing/invocation quotas from an official page (only third-party, mutually inconsistent).
- Could not verify pg_net timeout/retry/concurrency from official docs.
- Did not retrieve Postmark, Brevo or SES pricing/limits; SES sandbox/production-access rules not verified.
- No official guidance found on whether a Supabase Edge Function can safely do Turnstile verification plus insert for anon callers; that is my design reasoning, not a source.

## Common security mistakes in contact forms

### Takeaway
The recurring failures are: skipping or trusting client-side Turnstile, exposing the table to the anon key beyond insert, putting user input in mail headers, and letting the form email arbitrary recipients (open relay).

### Cited Findings
- Table without RLS in the exposed public schema: anyone with the anon key can read or modify it; enabling RLS with no policies denies all API access. — via search summary of [Supabase RLS docs](https://supabase.com/docs/guides/auth/row-level-security) (page itself not fetched)
- Community example of anon insert uses `to anon ... with check (true)`, which tutorials call too permissive for production. — [zenn.dev tutorial](https://zenn.dev/bamtech1904/articles/20251129_supabase-rls-policy)
- Email header injection: if user input goes unvalidated into headers, an attacker can end a header and add Bcc or other recipients, turning the form into a spam/phishing relay. — [Beagle Security](https://beaglesecurity.com/blog/vulnerability/email-header-injection.html); also [DZone](https://dzone.com/articles/what-is-email-header-injection)
- OWASP Input Validation cheat sheet: use allowlist validation, a maintained email-validation library, and note format validation does not prove mailbox access. It does NOT cover CRLF/header injection. — [OWASP Input Validation](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)
- Turnstile: check `success`, and compare `hostname` and `action` yourself; one token per Siteverify call; do not cache a "verified" flag. — [Cloudflare](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/), [Flavio Copes course](https://flaviocopes.com/courses/cloudflare/verify-context-and-prevent-replay/)
- Supabase Edge Functions with `--no-verify-jwt` are publicly callable, so they need their own abuse control. — [Supabase guide](https://supabase.com/docs/guides/functions/examples/send-emails)

### Inferences
Checklist of mistakes (the list is my synthesis):
1. Only rendering the widget; no Siteverify call, or failing open when Siteverify times out or errors.
2. Not checking `hostname`/`action`; reusing a token.
3. Table readable by anon/authenticated (missing RLS, or an overly broad SELECT policy; insert with `returning` needing select).
4. Service-role key or Turnstile secret in browser/NEXT_PUBLIC variables.
5. Putting visitor input in To/From/Subject; correct pattern: fixed From on your verified domain, fixed To, visitor address only in Reply-To, strip CR/LF from Subject and names, use provider JSON API rather than raw SMTP header strings.
6. HTML-injecting message body into the notification email or Slack (escape or send plain text).
7. No per-IP/per-email rate limit and no body size cap; unbounded field lengths.
8. Sending an auto-reply/confirmation to the supplied address (turns the form into a mail bomb relay); avoid or gate heavily.
9. Logging full payloads (PII) into general logs.
10. Webhook endpoint unauthenticated, or secret visible in DB dumps (see Supabase caveat above).

### Gaps
- Did not retrieve OWASP's own page on email header injection (none found in search); the prevention list is general practice backed by secondary sources only.
- Did not verify Cloudflare's current statement on honeypot/disposable-email controls; those are my recommendations (honeypot field, rate limit by IP and email, optional disposable-domain blocklist) with no source fetched.

## Minimum privacy wording and retention policy

### Takeaway
Treat the enquiry as legitimate-interests (or pre-contract) processing, tell the sender at the form what is collected, why, who processes it, and how long you keep it, and set a short fixed retention (my suggestion: delete unconverted enquiries after a stated period such as 12 months or less). The specific number is a decision, not a sourced rule.

### Cited Findings
- Public-sector enquiry notices typically tie retention to resolution of the enquiry rather than a fixed period (East Sussex: until resolved; Highland: depends on issue). — [East Sussex](https://eastsussex.gov.uk/privacy/email-us), [Highland](https://www.highland.gov.uk/privacy-General_enquiry)
- One council mailbox notice keeps enquiries 30 days; another notice keeps business-contact details 2 years after last contact. — [East Sussex mailbox notice](https://healthandcarenews.eastsussex.gov.uk/?p=2643), [Bath & NE Somerset](https://www.bathnes.gov.uk/council-privacy-notices/business-contacts-privacy-notice)
- Lawful basis varies among published notices: consent (Canonical-style), public task (Highland), consent plus legitimate interests (Bath). These are third-party examples, not regulator guidance. — links above
- Resend DPA (page updated 2025-12-31): general authorization for sub-processors with 14 days' notice; EU SCCs (2021/914), UK addendum; stated EU-US Data Privacy Framework compliance; effective when the customer accepts the Terms; executed copy in the Resend dashboard. — [Resend DPA](https://resend.com/legal/dpa)
- AWS offers a GDPR DPA to customers (whitepaper marked historical). — [AWS whitepaper](https://docs.aws.amazon.com/whitepapers/latest/navigating-gdpr-compliance/aws-data-processing-addendum-dpa.html)

### Inferences
Suggested minimum notice text beside the submit button (draft, not legal advice):
"We use your name, email and message only to reply to your enquiry. We process it on the basis of our legitimate interests in responding to you (or to take steps you ask for before a contract). It is stored in our database (Supabase) and notified to our team by [email provider/Slack], who act as our processors. We keep it for [12] months after your last message, then delete it. You can ask us to access, correct or delete it, or object, by emailing [address]; you can complain to your data protection authority. Our privacy policy: [link]."
Retention mechanics: scheduled delete job on `created_at`/`last_contact_at`; deletion on request by id/email; no marketing use unless a separate unticked opt-in. Collect only name, email, message (and optional company); do not store full IP (or store a hash/short TTL) and no user-agent beyond abuse needs. Sign/accept the DPA with each processor (Supabase, email provider, Slack if used, Cloudflare). Slack as a notification channel keeps a copy of the lead in Slack, so retention must cover it too.

### Gaps
- ICO page fetch returned 404; the ICO/EDPB minimum-content list for privacy notices and the regulator's view on legitimate interests for contact forms were not retrieved. Jurisdiction (UK vs EU) of the company is unknown.
- Did not verify Supabase, Postmark, Brevo, SES (service-specific), Slack or Cloudflare DPA terms.
- No source for a recommended numeric retention period.

## Exposing the lead to the team without a CRM

### Takeaway
Cheapest sound option: DB row as source of truth, a plain-text notification to a shared mailbox or Slack channel, and (when volume grows) a small admin list page behind existing Supabase Auth with a role check. No sources specific to this question were retrieved.

### Cited Findings
- Resend free tier (100/day, 3,000/month) is ample for a contact form volume of a small team. — [Resend quotas](https://resend.com/docs/knowledge-base/account-quotas-and-limits)
- Database webhook payload (`record`) can carry the row to a Slack-incoming-webhook-compatible relay, but delivery is not guaranteed by docs. — [Supabase webhooks](https://supabase.com/docs/guides/database/webhooks)

### Inferences
- Notification (email to a team alias or Slack message) contains lead summary only; link to admin page for detail, to reduce PII copies in Slack/email.
- Admin list: Supabase table with RLS permitting SELECT/UPDATE only to users in an admin role/allowlist; columns `status` (new/replied/spam), `assigned_to`, `notified_at`. FastAPI could serve it with service credentials behind existing auth instead.
- Add a daily "unnotified or unreplied leads" digest to catch dropped notifications.

### Gaps
- No sourced guidance on Slack data-retention or Slack DPA; not researched.
- No sourced comparison of shared-inbox tools; not researched.
