# Outreach best practices: first-pass research (2026-10-27)

Answers the questions in [outreach_brief.md](outreach_brief.md). **First pass by one agent in about 25 searches and fetches. It is not exhaustive.**
Confidence: **H** = a primary source (regulator, provider or standards body) read in this session; **M** = secondary source or
primary source read only through search; **L** = vendor blog or a single claim. Anything not listed in section 4 was not
researched. Not legal advice.

## 1. Summary: what to do, in order

1. **Fix the data quality problem first.** Independent tests put Apollo's clean-valid email rate at roughly 65 to 76%, with
   a hard-bounce rate that can reach the high teens (vendors in the verification business ran these, so treat the exact figures
   as M/L). In a batch of 20 that is 3 to 4 bounces, which is a bounce rate far above the 2% that vendors call safe. **Verify
   every address with a separate verifier before sending, drop invalid and risky ones, and send nothing to catch-alls you cannot
   justify.** This is the largest risk to a new domain that our research found.
2. **Authenticate the domain before any message:** SPF (one record), DKIM (at least 1024 bits, 2048 preferred), DMARC at least
   `p=none` with a reporting address, valid reverse DNS (the provider handles this), TLS. [H, Google]
3. **Ramp slowly, to people likely to answer:** Google's own words: start "a low sending volume to engaged users", send at a
   consistent rate, avoid bursts, back off on errors. [H] There is no official "warm-up" service; commercial warm-up networks
   are unproven (section 3, item 5).
4. **Keep spam complaints under 0.1% and never reach 0.3%.** [H, Google] At our volume one complaint in 20 emails is 5%, so
   the real defence is sending only to people the message is relevant to, with a visible, honoured opt-out.
5. **Plain text, one link at most, no open-tracking pixel, individually written, short.** [M/L: vendor consensus, no controlled study]
6. **Comply with the law of the recipient's country** (section 2). For the US: CAN-SPAM works (it is opt-out, B2B included).
   For UK corporate addresses: no consent needed under PECR but UK GDPR still applies to named people. For Canada and
   Australia: narrow implied-consent tests. For Germany and Austria: avoid.
7. **Do not use Apollo's data outside its terms:** it forbids uses that break CAN-SPAM, CASL or the TCPA, makes you "solely
   responsible" for legal compliance, forbids training models on its contact data, and forbids API use with LinkedIn
   automation. [H, Apollo terms]
8. **Keep records:** the source of each contact, why you contacted them, when, and every opt-out (needed for GDPR/UK GDPR and
   for CAN-SPAM's ten-business-day rule).
9. **Know the account risk:** Google Workspace's acceptable use policy prohibits unsolicited bulk commercial email and allows
   suspension. [H] Individual, low-volume, relevant messages are not "bulk" in the usual sense, but a spam-report spike can
   still get a mailbox limited. Use a separate domain so a problem cannot reach the main one.

## 2. Law, by place (what the sources say)

| Place | What applies to a small B2B cold email | Source and confidence |
|---|---|---|
| **US** | CAN-SPAM covers business email ("no exception for business-to-business email"). Accurate headers, honest subject, clearly identify as an advertisement, valid physical postal address, opt-out explained and honoured within 10 business days (mechanism must work for at least 30 days). Up to **$53,088 per email**. Harvesting addresses or generating them by dictionary attack is a criminal offense. The company that is promoted is liable even if a vendor sends. | [FTC guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business), H. Penalty figure as stated on that page; it is inflation-adjusted yearly |
| **UK** | PECR's email rule "does not apply to corporate subscribers" (companies, LLPs); sole traders and some partnerships are individuals and need consent or the soft opt-in. A work address that identifies a person is personal data, so UK GDPR applies; legitimate interests is the likely basis but needs the three-part test; the right to object to direct marketing is absolute. Do not disguise identity; give a valid opt-out address. The ICO page says its guidance is under review because of the Data (Use and Access) Act. | [ICO B2B marketing](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/), H |
| **UK, penalties** | PECR fines moved to the UK GDPR level (higher of £17.5m or 4% of turnover) for breaches on or after **5 February 2026** (Commencement No. 6 Regulations 2026). | Law-firm reports, M: [Kennedys](https://www.kennedyslaw.com/en/thought-leadership/article/2026/the-data-use-and-access-act-2025-commencement-dates-and-planned-guidance-for-2026), [A&O Shearman](https://www.aoshearman.com/en/insights/ao-shearman-on-data/key-data-protection-changes-under-the-data-use-and-access-act-2025-comes-into-force). Check legislation.gov.uk |
| **EU, GDPR** | When data came from a third party (Apollo), Article 14 requires telling the person who you are, why, the legal basis, the legitimate interest, their rights and **the source**, "no later than the first contact" if you use the data to contact them. Right to object (Art. 21) is absolute for direct marketing. | [Art. 14 text](https://gdpr-info.eu/art-14-gdpr/), H |
| **EU, ePrivacy by country** | Germany: prior consent generally needed even for B2B (UWG section 7); enforcement often by competitor lawsuits. France: B2B allowed if the message relates to the person's role and has an opt-out. Netherlands and Ireland: allowed with conditions and opt-out; sources differ on the legal basis. | Vendor blogs, **L**: [Overloop](https://overloop.com/blog/b2b-cold-email-germany-gdpr-compliance), [Tomba](https://tomba.io/blog/gdpr-cold-email-b2b). Needs regulator sources and a lawyer |
| **India** | DPDP Rules notified 13 Nov 2025; **most core duties (notice, consent, rights, safeguards) apply from 13 May 2027**; consent-manager duties from 13 Nov 2026. The Board's operational status is disputed between sources. No email-specific anti-spam statute found; TRAI rules cover calls and SMS (not verified in this pass). | [AZB](https://www.azbpartners.com/bank/indias-digital-personal-data-protection-act-phased-rollout-and-key-compliance-milestones/), [Conventus](https://conventuslaw.com/featured-content/building-indias-digital-privacy-framework-an-overview-of-the-dpdp-rules-2025/), M |
| **Canada, CASL** | Consent is required; implied consent for a "conspicuously published" address needs all three: published, no "do not contact" statement, and the message relevant to the person's role. Addresses scraped or sold by a third party do not count. | [Bennett Jones on CRTC 2016-428](https://www.bennettjones.com/insights/blogs/2016/11/crtc-assesses-conspicuous-publication-basis-of-implied-consent-in-casl-enforcement-decision), [BLG on the 2020 appeal](https://www.blg.com/en/insights/2020/07/federal-court-of-appeal-rules-casl-constitutionally-valid-and-provides-interpretive-guidance), M. The CRTC page returned an error; penalties not re-verified |
| **Australia** | Inferred consent via a published address needs: public, no statement refusing commercial messages, and subject directly related to the person's role. Harvested addresses are banned. The 5-working-day unsubscribe time was **not** confirmed. | University and vendor pages, M/L; read the Spam Act on legislation.gov.au |

## 3. Findings by brief item

**1. Dedicated domain.** No primary source tested. Consensus (L): use a separate domain, not the main one, so reputation damage is
contained. Google's own page warns about volume spikes "if you do not have a history of sending large volumes", which describes
every new domain. Confidence in "use a separate domain": M.

**2. Mailbox provider.** Google Workspace's [acceptable use policy](https://workspace.google.com/terms/use_policy.html) prohibits
bulk unsolicited commercial email and allows suspension (H, read in a Hindi rendering; verify the English text). Nothing found
on Microsoft 365 or GoDaddy mailboxes' policies or SMTP settings: **open**.

**3. Volume.** Vendor guides disagree: 30 to 50, 50 to 100 and 80 per mailbox per day, all L. At our 20-per-batch scale
every figure is far above need; the practical limit is quality, not quota.

**4. Authentication.** Google (H, [sender guidelines](https://support.google.com/a/answer/81126)): all senders need SPF or DKIM,
valid forward and reverse DNS, TLS, RFC 5322 format; DKIM keys at least 1024 bits, 2048 recommended; bulk senders (more than
5,000 a day to Gmail) need SPF, DKIM and DMARC with the From domain aligned. DMARC may be `p=none`. For GoDaddy DNS (vendor
guides, L): paste the raw TXT value without quotes or line breaks (GoDaddy adds quotes and splits long values), enter only the
selector part of the host (`google._domainkey`, not the full domain), keep to one SPF record and under 10 lookups, verify with
`dig` or `nslookup`, allow up to an hour or more to propagate. MTA-STS, TLS-RPT, BIMI: **not researched**.

**5. Warm-up.** Google's guidance is the only primary one: low volume to engaged users, consistent rate, back off on errors
(error 4.7.28: stop for at least 10 minutes). Paid warm-up "pools" are vendor-promoted; no independent evidence was found that
they work or that Google penalises them. Treat as unproven. A safe manual ramp (L, vendor): 10 to 25 a day for the first two
weeks, to the people most likely to reply.

**6. Provider rules.**
- Google (H): the bulk-sender duties above apply at more than 5,000 messages a day; but "Always give recipients an easy way to
  unsubscribe" is addressed to all senders; one-click unsubscribe (RFC 8058 headers) is required for marketing mail from bulk
  senders; spam rate target below 0.1%, never 0.3%.
- [RFC 8058](https://www.rfc-editor.org/rfc/rfc8058) (H): needs an HTTPS URI in `List-Unsubscribe`, a
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click` header, and a DKIM signature covering both headers. Our `mailto:` header
  is not one-click. At our volume Google does not require it; it is good practice.
- Microsoft (M): domains sending more than 5,000 a day to Outlook.com must pass SPF, DKIM and DMARC (effective 5 May 2025);
  non-compliant mail goes to junk or is rejected with `550 5.7.515`. Sources disagree on junk vs reject. See
  [Microsoft postmaster policies](https://substrate.office.com/ip-domain-management-snds/postmaster/Policies).
- Yahoo: **not read in this pass.**

**7. What lands in spam.** Vendor consensus (L): plain text; zero to one link; no images or attachments; no open-tracking
pixel (judge by replies and meetings); authenticated; verified list; stable volume. No controlled study found. Whether
AI-written or lightly varied templates are filtered: **no evidence found**.

**8. Monitoring.** Postmaster Tools exists and shows spam rate, but small senders often see "no data" (L, Suped and a university
page); Google gives no exact threshold. Practical consequence: **do not rely on Postmaster Tools at 20 emails a month**;
watch bounces and replies by hand. Microsoft SNDS/JMRP and blocklist checks: not researched.

**9. List quality.** See summary item 1. Apollo claims 91% accuracy and a credit refund for bounces within 30 days; independent
vendor tests report roughly 65 to 76% clean-valid and 12 to 18% invalid or bounce, with 18% catch-all (microsoft 365 domains
often look catch-all). Sources: [BounceZero study](https://bouncezero.io/apollo-email-accuracy-study-2026), [Apollo's own page](https://knowledge.apollo.io/hc/en-us/articles/10826699994381). L (sellers of verification).
No neutral source for a "pause" bounce threshold; vendors say 2% (throttling) and 15 to 20% (suspension), both L.

**10. Apollo.** [Terms](https://www.apollo.io/terms) (H, read in this session): may use contact data to communicate about the
person's profession or business; barred from uses that violate "CAN-SPAM, CASL, or the TCPA"; "You must independently analyze
and ensure" privacy-law compliance; no representation of accuracy; no scraping or bots; no circumventing API limits; no
multiple self-serve accounts to get around credits; no use of contact data to train AI models; no API use with LinkedIn
automation. API: all customers have basic access, advanced depends on plan; people and organisation search and enrichment
consume credits ([docs](https://docs.apollo.io/docs/api-pricing)). Apollo's GDPR badge is a vendor claim. Its data sources: **not found**.

**11 to 12. Content, replies.** Not researched beyond vendor consensus (short, one ask, plain text). Auto-replies and
odd-form unsubscribes: not researched.

**13 to 20. Law.** See section 2. Not covered: US state laws, EDPB guidance on legitimate interest, CNIL's own text, the
Indian TRAI position, LinkedIn terms, any litigation against Apollo, regulator expectations on record-keeping beyond Art. 14,
and any rule requiring disclosure of AI-assisted drafting.

**21 to 24.** Blocklist and suspension recovery, ethics, and alternatives (warm introductions, communities, content) were not
researched.

## 4. What changes in our tool because of this

1. **Add an address-verification step** before any send, and a gate: only addresses marked valid go to `approved`. (Summary 1.)
2. **A recipient-country gate** with a short allow-list (US, UK, India first), and a note in the log of which rule was
   applied. (Section 2.)
3. **Footer wording:** keep the postal address and opt-out; say the message is an advertisement or cold introduction (CAN-SPAM);
   for UK and EU recipients add the source of the data and the legal basis (Art. 14). Our footer says the contact came from
   "public sources and a business-data provider", which partly covers the source but not the basis or rights.
4. **Authentication check:** a command that confirms SPF, DKIM and DMARC records exist for the sending domain before `--really`.
5. **Optional one-click unsubscribe** later (needs an HTTPS endpoint and DKIM covering the headers). Not required at our volume.
6. **No open tracking, at most one link.** Add to the rule check.
7. **Keep the legitimate-interest note per contact** (already a `basis` column in the contact list).
8. **Do not store or use Apollo contact data to train or fine-tune any model**; drafting with a model per email is a use to
   check against the terms (section 3(e)(5)) with Apollo or a lawyer.

## 5. Open questions for the second research agent

Microsoft 365 and GoDaddy mailbox SMTP settings and policies; Yahoo's rules; MTA-STS, TLS-RPT, BIMI; independent evidence on
warm-up pools; controlled data on cold-email content; EU per-country rules from regulators, not vendors; the Indian
position on commercial email; US state laws; whether Apollo's own sending features are safer or riskier than our own
mailbox; DUA Act commencement dates from legislation.gov.uk; Spam Act unsubscribe timing; blocklist recovery.

## 6. Source reliability notes

Primary and read directly: Google's sender guidelines, the FTC CAN-SPAM guide, the ICO B2B page, GDPR Article 14, RFC 8058,
Apollo's terms. Read via search results only: Microsoft postmaster policy, Google Workspace AUP (via a translated page),
DPDP timeline, DUA Act commencement, CASL and Australian tests. Vendor marketing (Instantly, Smartlead, Clay, BounceZero,
Overloop, Tomba and others) is marked L and should not be relied on alone.
