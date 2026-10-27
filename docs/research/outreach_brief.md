# Brief for a research agent: how to run small, compliant, deliverable B2B cold outreach (2026-10-27)

You are being asked to research best practices. This brief says who we are, what we are doing, what we already built, what we
do not know, and exactly what we want back. It contains no secrets and no prospect data. Treat anything marked **unverified**
as a claim to check, not a fact.

## 1. Who we are

- A small founding team (three founders), based in India (IST), building a developer tool called StealthLab (product name in
  places: "kel"). It has no revenue and no signed customers yet.
- What the product does: it sits beside coding agents (Claude Code, Cursor and others) and (a) keeps a verified, per-repository
  memory of facts and reusable procedures, with each fact citing a file and line, and (b) can send routine coding tasks to
  cheaper open models first, accepting the result only if checks pass, and falling back to the expensive model otherwise.
- Honest state of the evidence (from our own measurements, `docs/results_index.md`): on benchmark tasks the routing cut cost
  roughly in half with a small loss in solved tasks; on real repository issues the results are **mixed** (one repo cheaper, one
  more expensive), and our biggest known weakness is that the acceptance checks let some wrong cheap fixes through. We are
  therefore **not allowed to claim a savings figure** in outreach.
- Data path (must be stated honestly if asked): prompts and repo facts go to our server; task content goes to model
  providers; there is no SOC 2 report and no penetration test yet.

## 2. What we are doing and why

We want to start conversations with a small number of engineering and AI leaders at software companies (roughly 200 to 2,000
people) who may have a coding-agent cost or quality problem, to learn whether the problem is real and whether they would try the
tool. This is research-led outreach, not volume sales.

- Scale: first batch 20 people; later tens to low hundreds per month. Not thousands.
- Channel: individual, personalised plain-text emails, each built on one public fact with a link, written as a draft, checked by
  a rule check, read and approved by a founder, then sent. No automated sequences, no bulk blasts.
- Regions to start: US, UK, India. We intend to avoid Germany, Austria, Canada and Australia until checked; other EU countries
  only after per-country review.
- Data source: an Apollo account (people and company search, email reveal by credits). API key not yet added. Public facts
  come from official APIs and the person's own public pages. We do not scrape LinkedIn.
- Sending: from a **new domain bought at GoDaddy**, separate from our main brand domain. Mailbox provider not decided
  (Google Workspace on that domain, or the mailbox GoDaddy sells, possibly Microsoft 365 through GoDaddy).

## 3. What is already built (local, tested offline, nothing sent)

- `scripts/outreach/send_mail.py`: Python `smtplib` sender. Dry run by default; sends only drafts marked approved; skips
  suppressed, unsubscribed, bounced or already-sent addresses; rule check (no prices, no percentages, no SOC 2 or zero-egress
  claims, requires an evidence link, no "Re:" subjects, length limit); adds a footer with name, postal address and an opt-out
  line; adds a `List-Unsubscribe` mailto header; per-run cap of 20; 20 to 45 seconds between sends.
- `scripts/outreach/contacts_db.py`: local SQLite contact list with forward-only statuses (new, approved, sent, replied) and
  final statuses (unsubscribed, bounced, do_not_contact); reads the mailbox over IMAP to turn "unsubscribe" replies and
  bounce notices into those statuses.
- `docs/outreach_plan.md`: the plan; `docs/research/iitb-alumni-prospects.md`: a public-source prospect map.
- Not built: Apollo import, the draft writer, any check that SPF/DKIM/DMARC exist, a recipient-country gate, a one-click
  unsubscribe link (we use reply-based opt-out and a mailto header).

## 4. Constraints

- Every message goes through a human approval. Nothing is sent automatically.
- No purchased lists beyond Apollo's own data under Apollo's terms; no guessed or generated addresses; no LinkedIn scraping.
- Opt-outs honored fast and permanently; the do-not-contact record is never deleted.
- Budget is small: prefer free or cheap tooling; paid mailboxes are acceptable.
- We are not lawyers. We want to know what the sources say, where they disagree, and where a lawyer is needed.

## 5. What we want you to find out

For each item: give the recommendation, the reason, the primary source, the date of the source, and how sure you are.

**A. Sending infrastructure**
1. New dedicated domain vs a subdomain of the main domain: reputation isolation, naming, domain age, whether to put a website
   on it, WHOIS privacy, how long a new domain takes to be trusted.
2. Mailbox provider for low-volume cold email: Google Workspace vs Microsoft 365 vs GoDaddy's own mailbox vs a dedicated
   outbound platform. Sending limits, reputation, risk of account suspension for cold outreach, terms of service on
   unsolicited email.
3. How many mailboxes and how many emails per mailbox per day are safe at 20, 100 and 300 emails a month.
4. Authentication, with GoDaddy DNS specifics: SPF (single record, the 10-lookup limit, `~all` vs `-all`), DKIM (key length,
   long TXT values in GoDaddy), DMARC (policy progression from `p=none`, alignment, reports and how to read them), and whether
   MTA-STS, TLS-RPT, BIMI or ARC matter at our scale.
5. Warm-up: is warm-up needed for a tiny volume; are automated "warm-up" services useful or harmful (they are said to be
   detectable and against some providers' terms; **unverified**); a safe manual ramp.

**B. Provider rules and filtering**
6. The current Google, Yahoo and Microsoft sender requirements (as of 2026): thresholds for "bulk sender", spam-complaint
   rate limits, one-click unsubscribe (RFC 8058) and when it is required, DMARC requirements. Which apply to us at low volume
   and which are good practice anyway.
7. What makes cold email land in spam in 2026: link count and tracking links, open-tracking pixels, images, HTML vs plain
   text, repeated templates, spam-trap hits, high bounce rates, sudden volume changes. Do AI-written or lightly varied
   templates get filtered?
8. Monitoring: Google Postmaster Tools, Microsoft SNDS/JMRP, blocklist checks, what bounce and complaint thresholds should
   trigger a pause.

**C. List and content quality**
9. Email verification, catch-all domains, role addresses, bounce handling; how accurate Apollo's emails are in independent
   tests; what to do with a hard bounce vs a soft bounce.
10. Apollo specifics: API access by plan, credit costs and caps, terms of service on how the data may be used, where its data
    comes from and what that means for GDPR/UK GDPR notice duties, and the risks of using Apollo's own sending features.
11. Content that gets replies without deception: length, subject lines, a single ask, opt-out wording, how to reference the
    source of the contact details, plain text vs HTML. Evidence from published studies, not vendor blogs, where possible.
12. Reply handling: out-of-office and auto-replies, "unsubscribe" in odd forms, replies in other languages.

**D. Law and regulation (list the law, the article or section, the date, and the regulator's own guidance)**
13. US: CAN-SPAM requirements and penalties; state laws that add to it; the FTC's position on B2B email.
14. UK: PECR rules for corporate vs individual subscribers; UK GDPR legitimate-interest assessment for B2B marketing; the
    Data (Use and Access) Act 2025 changes to PECR fines and other rules; ICO guidance.
15. EU: ePrivacy rules per country for B2B cold email (Germany, Austria, France, Netherlands, Ireland, Italy, Spain at least);
    GDPR Articles 6(1)(f), 14 and 21; CNIL, EDPB and national regulator guidance; what notice a first email must carry when the
    data came from a third party.
16. India: the Digital Personal Data Protection Act 2023 and Rules (status of commencement as of late 2026), the exemption for
    data made public by the person, any email-marketing rules, the TRAI rules (calls and SMS only?).
17. Canada (CASL) and Australia (Spam Act): implied consent for published business addresses; practical risk for a small
    foreign sender.
18. LinkedIn and other platforms: terms on using their data for outreach; Apollo-related litigation or regulatory action, if any.
19. Record-keeping that regulators expect: legitimate-interest assessment, suppression list, source of each contact, retention.
20. Disclosure: is an email drafted with AI assistance required to say so anywhere (law or provider policy)?

**E. Process and risk**
21. What to do if the domain is blocklisted, the mailbox is suspended, or a recipient complains to a regulator.
22. Ethical norms for B2B outreach to founders and executives at companies we may later ask to be pilots.
23. Alternatives that reach the same people with less risk (warm introductions, communities, content, events), and when cold
    email is still worth doing.
24. Anything we have not asked about that experienced practitioners say matters most.

## 6. Out of scope

Bulk or high-volume sending, purchased lists, scraping behind logins, spoofing or rotating sender identities, evading spam
filters, SMS and phone outreach, and any tactic whose purpose is to hide who is sending.

## 7. How to report

- A summary of at most one page: what to do, in order, and what to avoid.
- Then findings by item number above, each with: recommendation, reasoning, source URL, source date, confidence
  (high, medium, low), and whether sources disagree.
- Prefer primary sources: Google, Yahoo, Microsoft and GoDaddy documentation; RFCs; FTC, ICO, CNIL, EDPB, Indian government
  and CRTC/ACMA pages; court or regulator decisions. Mark vendor blogs and marketing sites as such and do not rely on them alone.
- Note the date of each source. Rules in this area changed in 2024 and 2025; flag anything older than 12 months.
- Separate what the law requires from what is only good practice.
- Say plainly where you could not find a primary source or where the answer depends on the country.
- Do not give legal advice; list the points to put to a lawyer.
