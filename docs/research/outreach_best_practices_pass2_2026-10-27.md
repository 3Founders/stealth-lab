# Outreach best practices, pass 2 (2026-10-27)

Fills the open list in §5 of [outreach_best_practices.md](outreach_best_practices.md). 9 Exa searches + 4 direct fetches attempted (2 errored, noted below). Same confidence codes as pass 1: **H** primary read this session; **M** secondary or primary-via-search; **L** vendor/single claim. Not legal advice.

## 1. Summary: the deltas new information makes

1. **Mailbox: prefer Google Workspace over Microsoft 365 for sending.** A measured 5-week test (22 domains, Aug–Sep 2026) got **0 human replies from 925 cold emails sent via M365 inboxes vs 15 replies (0.84%) from 1,780 via Google Workspace** (p = 0.002). Single-operator study — treat as M, but it aligns with Microsoft's own posture (Exchange Online "isn't designed for bulk mailing"; Cold mail from SMTP AUTH can be routed to a high-risk pool). Note: GoDaddy-sold M365 sits *inside* GoDaddy's tenant setup — bounces route unblock support through "a GoDaddy Guide", an extra mediation layer we don't control. [Pass-2 M/H]
2. **No warm-up tools — decision now has authority behind it.** M³AAWG's position paper (Nov 2025) classifies "artificially simulate subscriber engagement" as an **abusive, unacceptable practice**, alongside lookalike domains and multi-account masking. No provider document mentions warm-up at all (a checked absence across Google/Yahoo/Microsoft docs). No independent study showing benefit. **Manual-only ramp stays.** [H/M]
3. **Yahoo rules captured (H, primary):** all senders: SPF *or* DKIM, spam <0.3%, forward+reverse DNS, RFC 5321/5322. Bulk (no published threshold; "significant volume"): SPF+DKIM, DMARC ≥`p=none` that passes with aligned From, rua strongly recommended, one-click List-Unsubscribe for marketing mail (**mailto acceptable**, RFC 8058 POST recommended), visible body unsubscribe link, **honor unsubscribes within 2 days** (vs Google's practical expectation), enroll in Yahoo CFL. Enforcement since Feb 2024 (unsubscribe: Jun 2024). Consequence: deferral, spam-folder, rejection; 421s precede blocks (L).
4. **Microsoft numbers captured (H):** Exchange Online per-user: **10,000 recipients/day** (24h sliding), **30 msgs/min**, 500 recipients/msg. Client SMTP submission is intended for low volume; tenants seen as bulk can land in the **high-risk delivery pool** then get restricted. Every cold email is a "non-relationship recipient": Outlook.com consumer caps (5,000/day, 1,000/day non-relationship) show how providers count *first-contact* volume even below bulk thresholds. Sender rules: 5xx NDR → never retransmit to that address; unsubscribe mechanism must be "clearly documented and easy". Known M365 bounce codes and unblock paths documented by GoDaddy help (5.1.8 → GoDaddy Guide; 5.7.708 → sender.office.com appeal).
5. **Blocklist recovery is now a runbook (M/L ops):** stop sending → date the listing → diagnose honestly → fix root cause → request removal **on each list's own portal** (Spamhaus: check.spamhaus.org; CSS self-service + auto-expiry, SBL/DBL written appeal 24–72h · Barracuda form 12–24h · SpamCop ~24h auto-expiry · SORBS weeks) → resume at half volume for a week, watch bounces. Decode bounces: `zen.spamhaus.org` (IP), `dbl.spamhaus.org` (a domain in the message), `b.barracudacentral.org`, `bl.spamcop.net`, SURBL/URIBL (a *link* domain listed), Microsoft `S3150`/`5.7.606` (their own reputation, appeal at sender.office.com). Monitor: MXToolbox weekly for the first month, then monthly at our volume.
6. **France upgraded to H (CNIL, updated 2026-06-10):** B2C needs prior consent; **B2B email is allowed without prior consent when addressed to a professional in relation to their function, with a working opt-out**; the "existing customer, similar products" exception cannot be used where no sale ever happened (CNIL sanction decision reminder). Germany remains vendor-sourced (L): UWG §7(2) treats email ads without express/presumed consent as "unzumutbare Belästigung" — the gesetze-im-internet page 404'd twice; **Germany/Austria stay excluded until read + lawyer-reviewed.**
7. **US state law layer captured (H for UT/IA):** CAN-SPAM has **no private right of action** — plaintiffs use state laws that survived preemption by targeting *deceptive* headers/subjects: **California B&P §17529.5** ($1,000/email, strict liability, catastrophic when class-aggregated; due-diligence defense can cut to $100), **Washington CEMA** (PRA), **Utah Code 13-65-202** ($1,000/email, PRA incl. mailbox providers, rebuttable presumption against you, fees; reducible to $100 with documented preventive practices), **Iowa 716A.6** ($10/msg up to $25,000/day; 5-year SOL). Practical effect: our no-fake-claims/headers rule-check is liability reduction, not etiquette. FTC per-violation figure moves with inflation ($53,088 on the FTC page read in pass 1; other sites show $50,120 — use the FTC page only). [H/M — lawyer to confirm current amounts]
8. **LinkedIn (M, multiple consistent citations of §8.2, version effective 3 Nov 2025):** no automated access; **no scraping "through any means (including... any other technology or manual work)"**; one profile per real person; Professional Community Policies: no promotional messages via invitations to strangers. We do none of this; Apollo terms independently forbid pairing its data with LinkedIn automation. Enforcement is real on LinkedIn's side (hiQ record: ~95M automated scraping attempts blocked/day).
9. **AI-assisted drafting: no law or provider rule requires disclosure** (US CAN-SPAM asks only whether headers are honest; no equivalent found elsewhere) — but the compliance-style playbooks converge on our existing design: human approves every message, and the record keeps template version + approver. Keep doing that.
10. **MTA-STS / TLS-RPT / BIMI: skip for now, backlog receiving-side hardening.** MTA-STS = TXT `_mta-sts` (`v=STSv1; id=YYYYMMDD-NN`) + HTTPS policy file at `mta-sts.<domain>/.well-known/mta-sts.txt` (start `mode: testing`, enforce only after 2 clean weeks per UK gov guidance); TLS-RPT = TXT `_smtp._tls` (`v=TLSRPTv1; rua=…`). Whole stack is about **inbound** mail security to our domain, not our sending reputation. BIMI additionally requires DMARC `p=quarantine/reject` + a paid Verified Mark Certificate — pointless at our stage. GoDaddy DNS quirks documented (agents: type only `_dmarc`, not the full domain; ASCII-only values; single SPF record; Domain Protection may demand an SMS/authenticator code on save).

## 2. Findings by pass-1 open item

**A. Microsoft 365 & GoDaddy mailbox policies** — Exchange Online limits above are hard service limits [H, learn.microsoft.com]. Outlook.com consumer caps: 5,000/day, 1,000/day non-relationship recipients [H]. GoDaddy's M365 page confirms same limits and a bounce/unblock table [H, godaddy.com/help]; GoDaddy's own mailbox product is M365 underneath. Google Workspace AUP already covered (pass 1, H).

**B. Yahoo** — see summary 3 [H]. Yahoo FAQ: bulk-sender definition deliberately unnumbered; one-click requirement is provider policy for marketing mail, judged per sender reputation; spoofed bypass attempts count against you.

**C. MTA-STS/TLS-RPT/BIMI** — see summary 10 [M/L, standards-aligned but vendor-written guides; UK gov page for the testing-then-enforce rule H].

**D. Warm-up pools** — M³AAWG position (H for the working-group's own words): "Any attempts to bypass mail volume limits... **artificially simulate subscriber engagement**, or use other tools or services that exploit loopholes... are particularly egregious and are not acceptable in any manner." Tamozo audit (M): the words "warm up" appear nowhere in Google's sender guidelines/FAQ, Yahoo's best practices or Microsoft's outbound-spam docs — neither permitted nor prohibited; **no independent with/without measurement exists**; placement-measurement tools have returned opposite verdicts on the same domain. One operator's tests "none of them demonstrated meaningful improvements" (L). **Decide: no warm-up network, no engagement automation. Manual ramp 10–25/day/box for ~2 weeks to likely repliers, exactly as pass 1 concluded.**

**E. Cold-email content evidence (what gets replies)** — Three receiver/operator studies this pass:
- Enjyn M365-vs-Gmail test (M): 22 domains, 2,705 prospects, 5 weeks. Reply rates 0.84% (Gmail) vs 0.00% (M365), p = 0.002; auto-reply ("OOO") rate 1.46% vs 0.22%, p < 0.001. **Free placement probe: an out-of-office auto-reply only fires from the inbox, never from junk** — a deliverability signal available in hours from our existing IMAP reader (it already turns OOO into statuses; counting them adds the metric). Limits: tells inbox from junk, not Primary from Promotions.
- GetResponse receiver-side study (M/L): 756 cold emails analyzed over 180 days: 84% name-personalized — many *wrong* (wrong name/tenure/company facts). Personalization from bad data is worse than none: our one-public-fact + link rule (already in the waiver rule-check) is the structural answer.
- M³AAWG NHI study (2020, old ⚠): B2B mail metrics distorted 20–80% by non-human interactions — don't chase open rates; judge by replies and meetings (already our rule).

**F. EU per-country from regulators** — France: CNIL primary (see summary 6) [H]. Germany: vendor sources only; UWG §7(2) not read directly this pass (404); hypothesis unchanged: prior-consent-ish regime with narrow presumed-consent exceptions; **no outreach to DE/AT** stands. Ireland/Netherlands/Italy/Spain: **not closed** (regulator pages still unfetched). EDPB/A.14 notice duty already handled (pass 1).

**G. India** — DPDP Rules timeline unchanged (most duties from 13 May 2027; notice/consent core). TRAI TCCCPR applies to telemarketing calls/SMS; **email exclusion still not verified against the regulation text** — open, low risk at our scale. No anti-spam email statute found (consistent with pass 1).

**H. US states** — see summary 7. Cornell Wex frameworks table (M): most states have *something*; the PRA-deception cluster (CA/WA/UT/VA pattern) is the actionable one. UT and Iowa statutory texts read directly [H].

**I. DUA Act (UK)** — unchanged from pass 1: £17.5m/4% penalty regime for breaches on or after 5 Feb 2026 per two law-firm reports [M]. legislation.gov.uk SI not fetched this pass (kept open). ICO guidance-revision note stands.

**J. Australia** — ACMA "Avoid sending spam" read [H, page dated 2024-11-29]: consent must exist (express or inferred), message must identify sender + carry contact details + easy unsubscribe; inferred consent via a *published address* requires "knowingly and directly given their address"/provably published + role-relevance + no refusal statement. **Unsubscribe timing (5 working days) still not confirmed** — the page fetch truncated before that section; Spam Act s.20 is the anchor to verify.

**K. Blocklist/suspension/regulator-complaint recovery (item 21)** — runbook above [M/L]. Add provider-specific portals: Microsoft sender support (sender.office.com), Google postmaster + mitigation requests require sustained <0.3% for 7 days (M, close.com docs restating Google), Yahoo CFL. For mailbox suspension (Google Workspace AUP / M365): separate domain contains blast radius (pass-1 stance revalidated).

**L. Apollo sending features vs own mailbox (item 10 tail)** — still **no independent data**; Apollo's own guardrails material recommends exactly our stack (SPF/DKIM/DMARC, one-click unsubscribe in *every* sequence, suppression lists, daily caps, DMARC to `p=reject` eventually) [L, vendor]. Decision stands: our own mailboxes, human-gated, for control and evidence trail.

**M. LinkedIn / record-keeping / alternatives (items 18/19/23)** — LinkedIn §8.2 consistent across four independent 2026 sources [M]; record-keeping model: source-of-contact, basis, prompt/template-version/approver, opt-out — matches what Art. 14 + the new state-law due-diligence defenses demand. Alternatives (warm intros/communities) remain GTM-leading (see `institute_alumni_playbook_2026-10-27.md`).

## 3. Deltas to pass-1 §4 (tooling)

1. **Mailbox decision input: Google Workspace first** on the GoDaddy-purchased domain; M365 only if price forces it — and then buy directly from Microsoft, not GoDaddy, to own the unblock path.
2. **contacts_db.py (IMAP reader): count OOO auto-replies separately** as a live inbox-vs-junk probe per batch. One field, no new dependency.
3. **Rule check: add "no tracking pixels/links (unchanged), plus 'no fabricated personalization facts'** — the receiver-side data says wrong-personalization is a hallmark of spam-tier mail.
4. **Add a one-page BLOCKLIST_RUNBOOK.md** (identifying: stop, date, decode bounce, portal links above, half-volume resume; MXToolbox weekly cadence; pause thresholds: hard bounce >2% on a batch, any spam complaint → pause batch and re-verify).
5. **Warm-up: recorded as "manual only"**; the 20-send cap and 20–45s spacing already implement it; no pooling tools ever.
6. **Backlog (not blocking first sends): MTA-STS `testing` + TLS-RPT records;** BIMI: never at our stage; **Do not** preempt `p=none`→`p=reject` migration with BIMI (it requires enforcement and VMC money).
7. **Keep a per-contact record**: source, basis note (already a `basis` column), and if AI drafts, template version + approver id — cheap and matches every record-keeping source found.

## 4. Sources (pass 2)

| # | Source | URL | Date | Conf |
|---|---|---|---|---|
| P1 | Yahoo Sender Hub — best practices | senders.yahooinc.com/best-practices/ | current (Feb/Jun 2024 enforcement) | H |
| P2 | Yahoo Sender Hub — FAQs | senders.yahooinc.com/faqs/ | current | H |
| P3 | uChecker Yahoo 2026 (debunk-correction) | uchecker.net/blog-en/yahoo-sender-requirements-2026 | 2026-06-07 | M |
| P4 | Exchange Online outbound sending limits | learn.microsoft.com/en-us/defender-office-365/outbound-spam-sending-limits-troubleshoot | current | H |
| P5 | Outlook.com sending limits | support.microsoft.com/en-us/outlook/sending-limits-in-outlook-com | current | H |
| P6 | GoDaddy M365 limitations + bounce codes | godaddy.com/help/microsoft-365-email-account-limitations-9003; godaddy.com/help/fix-rejected-email-with-a-bounce-error-40685 | current | H |
| P7 | Microsoft external-sender policies | postmaster.outlook.com/policies.aspx; learn.microsoft.com/.../external-senders-policies-practices-guidelines | current | H |
| P8 | CNIL — commercial prospecting email | cnil.fr/fr/la-prospection-commerciale-par-courrier-electronique-sms-mms-et-automate-dappel | 2026-06-10 update | H |
| P9 | M³AAWG Position on Cold Email v1.0 | m3aawg.org/sites/default/files/doc_files/m3aawg_position_on_cold_email.2025_0.pdf | Nov 2025 | H |
| P10 | M³AAWG NHI study | m3aawg.org (nhi_nov_2020_doc.pdf) | 2020 ⚠ old | M |
| P11 | Tamozo warm-up evidence audit | tamozo.com/blog/email-warmup-tools | 2026-09-04 | M |
| P12 | Enjyn M365-vs-Gmail placement study | enjyn.ai/research/microsoft-365-cold-email | 2026-09-10 | M (single operator) |
| P13 | PostboxServices warm-up field test | postboxservices.com/blogs/post/do-email-warmup-tools-work-in-2025/ | 2025-06-04 ⚠ | L |
| P14 | GetResponse 756-email receiver study | getresponse.com/blog/cold-email-research | 2025-09-16 | M |
| P15 | Blocklist removal playbook | aureliussdr.com/guides/email-blacklist-removal; suped.com/docs/blocklist-monitoring/how-to-get-delisted; bounceclean.com; sender.net | 2025–26 | L (consistent) |
| P16 | ACMA — Avoid sending spam | acma.gov.au/avoid-sending-spam | 2024-11-29 | H |
| P17 | MTA-STS/TLS-RPT/BIMI mechanics | jwatte.com/blog/blog-bimi-mta-sts-cross-host; bigboxhosting.com/guides/email-authentication-2026/; dmarcengine.com/docs; trustyourinbox.com/kb/dns-providers/godaddy; security.gov.uk/policy-and-guidance/set-up-tls-rpt-and-mta-sts/ | 2026 | M (gov: H) |
| P18 | Utah Code 13-65-202 | le.utah.gov/xcode/Title13/Chapter65/C13-65-S202_*.pdf | 2023 | H |
| P19 | Iowa Code §716A.6 | legis.iowa.gov/docs/code/2026/716A.6.pdf | 2026 code | H |
| P20 | CA §17529.5 litigation review | bipc.com/rising-litigation-under-californias-anti-spam-act; compliancepoint.com (2025-05-30); terms.law | 2023–2026 | M |
| P21 | Cornell Wex state frameworks | law.cornell.edu/wex/inbox/state_anti-spam_laws; /anti-spam_state_frameworks_table | current | M |
| P22 | LinkedIn §8.2 + enforcement | perkinsgrowth.com/blog/linkedin-outreach-automation-tools; gtm-api.com/cold-linkedin-message-rules/; linkmate.ghost.io (2026-09-03); imisofts.com/blog/ai-sdr-disclosure (2026-09-24) | 2026-07→09 | M (public agreement versions quoted) |
| P23 | AI-drafting disclosure (absence) | imisofts.com/blog/ai-sdr-disclosure; xengrowth.com/resources-pdf/outbound-sequence-compliance-cookbook.pdf | 2026-09 | M/L |
| P24 | Apollo AI-SDR guardrails (vendor) | apollo.io/insights/what-guardrails-should-i-put-in-place-when-deploying-an-ai-sdr | 2026-04-06 | L |

## 5. Still open (honest)

- UWG §7(2) full text (Germany) — statute page 404'd twice; fetch via a German legal mirror or the institute library when we next touch DE/AT.
- legislation.gov.uk — DUA Act 2025 Commencement No. 6 SI number/text.
- ACMA unsubscribe window (5 working days) against Spam Act s.20.
- TRAI TCCCPR text — confirm email is out of scope for telemarketing rules.
- Ireland/Netherlands/Italy/Spain regulator pages (only France and DE-hypothesis exist for EU per-country).
- Apollo's own sending features (independent deliverability data — none exists).
- Google/Yahoo complaint-rate *measurement* at our tiny volume (Postmaster Tools/CFL need domain verification; below ~5,000/day the dashboards often show no data pass-1 noted — our fallback: bounce + OOO + reply tracking locally).
