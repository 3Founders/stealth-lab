# Opening kell to normal (individual) users: what is required first

Written 2026-10-28. Web sources are advisory blogs and law-firm notes, not primary text; every legal line needs a lawyer or the official text before it is relied on. Repo facts were read from the code, not run.

## Where we stand

Built: sign-in (Google, GitHub), auto-enrolment into the capped pilot org, governed `call_model` and `/v1/chat/completions` on the hosted MCP server, per-person $1/day and org $30/month caps, a cost ledger, an org admin API, delegator relay, legal drafts in `docs/legal/` (Terms, Privacy, AUP, Subprocessors, Cookies).

Not built or not verified: a signed-in end-to-end call, payments, self-serve account deletion, per-IP abuse limits on the hosted routes, a status/support path, provider capacity numbers.

## 1. Legal and compliance (blocking)

1. **Provider terms.** Z.ai pay-as-you-go API terms allow downstream apps for end users; one clause on resale (section XIII.2) is ambiguous. Get a written answer from Z.ai before charging anyone. General Compute terms have not been read: read them. Third-party reports say Z.ai does not train on API content unless you agree; confirm in the contract, not a blog.
2. **No data-processing agreement** with either provider. Until there is one, pooled models stay public-data-only (enforced by policy today). The site must say so where a person types.
3. **India DPDP Act 2023 and Rules (notified 13 Nov 2025).** Consent, notice and breach provisions take effect about 18 months after notification (roughly May 2027) per law-firm summaries. Plan to comply from day one: a standalone plain-language notice listing data and purposes, no bundled consent, breach reporting to the Board without delay with a detailed report within 72 hours, reasonable security safeguards (penalties up to INR 200-250 crore are quoted). No small-company exemption was found; confirm. Sources: Mondaq, Khaitan, Hogan Lovells (see links below).
4. **Our own legal pages** exist as drafts in `docs/legal/`. They must match what the product does now: the models used, the providers that receive prompts, retention, the ledger. Review with counsel before launch. Users must accept Terms and Privacy at first sign-in (not built).
5. **Account deletion and data export** (DPDP principal rights; also expected by Google/GitHub login review): no self-serve route found in the backend API. Needed.
6. **Minors.** Decide the age gate (DPDP treats under-18 data specially, with verifiable parental consent). Simplest: 18+ in the Terms and a checkbox.
7. **Anthropic and OpenAI terms.** Never pool or resell Claude/Codex subscriptions. Fine as is: we pool only Z.ai and General Compute API keys.

## 2. Money (blocking if we charge)

1. **Payments.** None in the code. India: Razorpay (INR, UPI, cards, subscriptions with RBI e-mandate rules) or Stripe (needs India eligibility). Prefer prepaid credits, which avoid recurring-mandate friction.
2. **GST.** Software/online services are taxed at 18%. If kell is operated by an India-registered business, normal GST registration and invoices apply; a foreign operator selling to Indian consumers would need OIDAR registration with no threshold. Confirm which entity sells.
3. **Free tier economics.** At glm-5.3-flash prices ($0.15 in / $0.50 out per million tokens) $1/day is millions of tokens, so cost per free user is small; the risk is volume of accounts, not heavy users. The $30/month org cap is a hard stop for the whole pilot.
4. **Prices and refunds policy** written before launch; no prices appear in B2B pilot copy (see memory b2b-pilot-copy-rules).

## 3. Abuse and capacity (blocking at any scale)

1. **Sybil free accounts.** One person can make many Google/GitHub accounts to multiply the $1/day. Options: require verified email plus GitHub account age, a phone or card check for the free tier, a global daily ceiling, per-IP limits. The org monthly cap already bounds worst-case spend.
2. **Per-IP / per-user rate limits** on `/v1/chat/completions` and `/v1/models` on the MCP host: not present (the full API app has rate limits, the MCP host's new routes do not). Add before public launch.
3. **Provider limits.** Pooled keys share one concurrency ceiling. Z.ai's published numbers for paid use were not found (third parties report 1 concurrent for the free flash; unverified). Measure with a load test; keep General Compute as automatic failover; add a third provider.
4. **Reasoning model quirk.** glm-5.3-flash spends tokens on reasoning first; a low max_tokens returns empty content. Defaults and prompts now ask for 2000+.
5. **Content abuse.** The AUP exists; there is no moderation of prompts. Decide whether to log prompts (privacy cost) and how to handle reports.
6. **Backups, migrations, secrets.** Neon plan quota was exceeded once; keep a paid plan. Rotate the two provider keys; one stray password line remains in backend/.env (local).

## 4. Product and support

1. A failing-call experience: clear errors for cap reached, wrong data class, provider down (partly done).
2. Account page showing usage today and remaining allowance (the ledger supports it; no user-facing route found).
3. Onboarding that gets a person from sign-up to a first delegated call (connector at https://mcp.kell.org.in/mcp, preference prompt).
4. Status page, support email, and an owner who answers it. Incident process for a provider outage or key leak.
5. Analytics consent (cookies) per `COOKIES_AND_TRACKING.md`.

## 5. Order of work

1. Verify end to end with a real signed-in call (cap, ledger, refusal text).
2. Rate limits and a global ceiling on the hosted routes; sign-up friction against sybils.
3. Terms/Privacy acceptance at sign-in; delete-my-account and export routes; age gate.
4. Written answers from Z.ai (resale, DPA, limits) and a read of General Compute terms.
5. Payments (prepaid credits), GST decision, invoices.
6. Lawyer review of the legal pages against actual data flows; DPDP notice.
7. Load test, status page, support path; then open sign-up.

## Sources

- DPDP Rules 2025 notified: https://www.mondaq.com/india/data-protection/1708164/digital-personal-data-protection-rules-2025-notified
- Khaitan note (15 Nov 2025): https://www.khaitanco.com/sites/default/files/2025-11/ERGO%20-%20Digital%20Personal%20%20Data%20Protection%20Rules%20-%2015%20November%202025.pdf
- Hogan Lovells overview: https://www.hoganlovells.com/en/publications/indias-digital-personal-data-protection-act-2023-brought-into-force-
- OIDAR / GST for digital services: https://www.india-briefing.com/news/oidar-compliance-india-gst-registration-ntor-gstr5a-digital-tax-43951.html/ and https://stripe.com/resources/more/india-vat-tax
- Z.ai pricing and limits (third party, unverified): https://developer.puter.com/tutorials/zai-glm-api-pricing/
