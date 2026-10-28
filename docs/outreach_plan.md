# Outreach plan: draft-only, evidence-grounded (2026-10-25)

Status: draft for the founders. Local, not committed. Nothing here sends a message. Apollo details marked **verify** are from
memory of Apollo's public docs and must be checked against the account's plan before relying on them.

## 1. What this is

A small pipeline that turns a target list into short, personalised email drafts, each grounded in one public fact with a
link, checked against our copy rules, and approved by a person before anything is sent. It is the useful part of the
"GTM engineering" roadmap at our stage (signal + personalisation), not the whole roadmap.

Out of scope for now: usage-based pricing, churn models, multi-touch attribution, automated QBR decks, marketplace
integrations. They need customers and volume we do not have.

## 2. Pipeline

| Step | What happens | Who/what does it |
|---|---|---|
| 1. Targets | A file of companies and roles (`outreach/targets.csv`), or an Apollo search | You, or Apollo |
| 2. People | Find the right people at each company (title, company, size) | Apollo |
| 3. Contact | Reveal a work email only for people we will actually write to | Apollo (spends credits) |
| 4. Evidence | One public, linkable fact per person (see section 3) | APIs first; a person for gaps |
| 5. Draft | One short email from that fact and our offer | A model, with the facts passed in as data |
| 6. Check | Rule check on every draft (section 5); failures are fixed or dropped | Code |
| 7. Approve | You read, edit, approve each draft | You |
| 8. Send | Copy and paste from your own mailbox | You |
| 9. Log | Who, when, which fact, reply or not (`outreach/log.csv`) | Code and you |

## 3. Do we have to scrape and collect?

Mostly no. What we need and where it comes from:

| Need | Source | Scraping? |
|---|---|---|
| Name, title, company, size, industry, location | Apollo people/company search | No (API) **verify plan allows API** |
| Work email | Apollo enrichment | No (API, uses credits) |
| Company facts: headcount, funding, tech used, open roles | Apollo company fields | No |
| Engineering/AI evidence: public repos, commits, issues | GitHub's official API | No (API) |
| Engineering/AI evidence: blog posts, talks, podcasts | RSS feeds and the pages the person themselves published | Light fetching of public pages only |
| Anything from LinkedIn | Not collected by us. Their terms forbid scraping | You paste it by hand if you want to use it |

Rules for the little fetching that remains:
- public pages only, respect `robots.txt`, low rate, no logins, no bypassing blocks;
- store the URL and the sentence used, not whole pages;
- no personal data beyond business role, work email and the public fact; no health, family, political or similar details;
- if a fact cannot be found for a person, the draft is not written (no invented personalisation).

## 4. What a draft may and may not say

Built from the copy rules in the project memory (`b2b-pilot-copy-rules`) and `docs/results_index.md`:
- never name the pilot lead or imply a pilot is planned;
- no prices;
- no savings headline we cannot back; results are mixed (one repo cheaper, one dearer), and benchmark numbers need
  their caveats (small samples, automatic graders, cost against API-priced Claude Code);
- say honestly where data goes (prompt and repo claims to our server, task and files to model providers, no SOC 2 or pen
  test yet);
- no claim about the person that the fact does not support;
- one clear ask, short, plain.

## 5. The rule check (code, runs before you see a draft)

Fails a draft if it: names the lead, contains a price or currency amount, states a percentage saving without a linked
source line, says "zero egress" or "SOC 2", quotes a fact with no URL, exceeds the length limit, lacks the sender identity
or an opt-out line, or personalises with a fact not in the evidence file.

## 6. Compliance and safety

- Cold email needs a real sender identity, a working opt-out and honest subject lines (CAN-SPAM). For EU/UK recipients,
  GDPR/PECR apply; for India, the DPDP Act. Business contacts are not exempt. Get advice before emailing at scale.
- No purchased or scraped lists beyond what Apollo provides under its terms.
- Keep the log; remove anyone who opts out from every future run.
- Keys: the Apollo key goes in `backend/.env` (gitignored) and is read from there; never pasted in chat, never in an
  argument, a log or a commit.
- Prospect names and emails are local-only (`outreach/` is gitignored), like `FundingGrants/`.

## 7. Cost

- Apollo: credits for email reveal. A per-run cap is set in code; first batch of 20 people.
- Model calls for drafting: small (about 20 short drafts). Use a cheap model; the rule check, not the model, is the gate.
- People time: reading every draft in the first batch.

## 8. Measuring whether it works

Per batch: drafts written, drafts approved, sent, replies, meetings. Report counts, not percentages, until a batch has
well over 100 sends. Compare only like with like (same role, same offer, same week). Keep the fact type with each send so
we can later see which kind of evidence drew replies.

## 9. First batch

- 20 people: heads of engineering or AI at software firms of 200 to 2,000 people, plus names from
  `docs/research/iitb-alumni-prospects.md` Tier A where a fact exists.
- Everything reviewed by hand; nothing sent without your approval.
- Stop and report if Apollo's plan does not allow the API calls, credits would pass the cap, or a draft cannot pass the
  rule check without weakening the rule.

## 10. Needed from you

1. The Apollo API key in `backend/.env` (tell me when it is there; do not paste it).
2. Which Apollo plan, and the credit cap per run.
3. Target roles and company size (default above), and any names to include or exclude.
4. The sender name, address and opt-out line to use.
5. Whether to use your own mailbox (default) or Apollo sequences.

## 11. Decisions after research passes 1 and 2 (2026-10-27)

- **Mailbox:** GoDaddy Professional Email (Titan) on a separate domain; GoDaddy's terms have been checked by the founders. **Open:** pass 2
  tested Microsoft 365 against Google Workspace (0 human replies from 925 cold emails on M365 vs 15 from 1,780 on Workspace, one operator, 5
  weeks, medium confidence) but nothing was found on Titan's cold-email reputation or limits. Watch the out-of-office count and replies in the
  first batch; if mail seems to land in junk, move to Google Workspace on the same domain.
- **Warm-up:** manual only (10 to 25 a day for about two weeks, to likely repliers). No warm-up networks or engagement automation: M3AAWG calls
  simulated engagement abusive.
- **Every draft record carries** `evidence_url`, `evidence_fact` (the one sentence taken from the source page), `approved_by` and
  `template_version`; the rule check rejects drafts without them, with more than one link, with HTML, or with tracking parameters.
- **Auto-replies** are counted by `sync-inbox` as a free inbox-versus-junk signal (an out-of-office never comes from junk).
- **Countries:** US, UK, India first. France only for role-relevant messages with an opt-out (CNIL, 2026-06). Germany and Austria excluded.
- **State laws:** the US has state anti-spam laws with large per-email damages for deceptive headers or subjects (California, Washington, Utah,
  Iowa); honest headers and subjects are liability reduction, not etiquette.
- **Backlog, not blocking:** MTA-STS and TLS-RPT records (inbound security), BIMI never at this stage.
- **Runbook:** `docs/outreach_blocklist_runbook.md`.

## 12. Unsubscribe link, gates and draft tools (2026-10-28)

- **One-click unsubscribe:** mail carries `List-Unsubscribe` (link + mailto) and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`; the footer has the
  same link. The link goes to the Supabase Edge Function in `scripts/outreach/supabase_functions/unsubscribe` (POST marks the address unsubscribed;
  GET shows a page with one button, so link scanners cannot unsubscribe anyone). The token is an HMAC of the address with `UNSUBSCRIBE_SECRET`.
  Deploy: `supabase functions deploy unsubscribe --no-verify-jwt`; `supabase secrets set UNSUBSCRIBE_SECRET=...`; then put `UNSUBSCRIBE_URL` and the
  same `UNSUBSCRIBE_SECRET` in `backend/.env`. **Limit:** RFC 8058 wants the DKIM signature to cover both List-Unsubscribe headers; GoDaddy signs our mail and
  we cannot see which headers it covers, so some mailbox providers may not show their own unsubscribe button. The footer link and reply path still work.
- **Country allow-list:** `OUTREACH_COUNTRIES` (default `US,UK,IN`); a draft whose country (draft field, else the contact's) is missing or not listed is skipped.
- **Footer:** every mail says it is a commercial cold introduction, who/where, where the details came from; recipients outside the US and India also get the
  legal basis, what is held and the right to object (UK GDPR / GDPR Article 14).
- **DNS check:** `send_mail.py auth-check` (MX, one SPF, DMARC, DKIM if `OUTREACH_DKIM_SELECTOR` is set); `send --really` runs it first.
- **Bounce pause:** only after 20 addresses mailed and at least 2 bounces, above 2%.
- **Drafts without a model:** `drafts.py init|make|show|approve` (versioned template + a facts file you fill); `evidence.py find URL --word ...` lists candidate
  sentences from one public page (robots.txt respected) for you to choose from.
