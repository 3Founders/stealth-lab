# Prior art: teams that go into organisations and refine their workflows and procedures

Written 2026-10-13. Replaces the framing of `gig-economy-and-challenge-platforms-prior-art.md` (contest platforms), which was the wrong
analogy: the target is Palantir / compliance-firm style work, where a team enters a customer, learns its real procedures, improves them,
and carries what it learned to the next customer. No ratings, no design here. Confidence: **P** peer-reviewed or filing, **R** reputable
reporting or an insider account, **W** vendor/blog/secondary (lead only). Roughly 20 searches and 1 full read; most search results were
summaries, so abstracts-only items are marked.

## 1. Palantir and the forward-deployed engineer (FDE)

- **How it works (one ex-employee, 2015-2024, read in full, R):** FDEs sit at the customer 3-4 days a week in teams of about 4-5.
  Product-development engineers stay home and *generalise what FDEs built*. FDEs overfit on purpose ("to hell with generalizability").
  Foundry took shape because FDEs did manual cruft work on site and product engineers automated it. Reusable layers named: data
  ingestion, visualisation, app building, and access control / audit in the integration layer. Airbus A350 manufacturing coordination is the
  flagship example. The account says Foundry now drives "50%+" of revenue and calls it a "rare services company -> product company pivot".
- **Economics (same account, R):** gross margin about 80% in 2023 against about 32% for Accenture; below-market salaries; long retention.
- **The costs:** pilots of 8-12 weeks consumed by data-access politics; travel "out of control"; critics call it "a consulting company
  masquerading as software" (R). Pricing is largely fixed-fee with no standard SKUs (W). A former employee notes custom builds decay once their
  builder leaves, which pushed the company to productise (W, single source).
- **The scaling move:** from 2023 Palantir starts the sale with "AIP bootcamps": real workflows on the prospect's own data in days (FY2023 and
  FY2024 10-Ks, P). Commercial revenue Q3 2023: $251M (+23%), 181 commercial customers (+37%) (R). So the embedded team became the *sales
  motion*, not only delivery.
- **Not found:** how Palantir leaves processes with the customer (handover, training); early customer concentration; revenue per customer.
  Sources disagree on when the model began (2003 founding vs early 2010s vs IBM/Oracle earlier). I found nothing on "Echo".
- **Imitation, 2025-26:** OpenAI and Anthropic both built FDE teams; OpenAI's Deployment Company (> $4B, $10B valuation, acquiring Tomoro for
  about 150 FDEs) and an Anthropic-backed entity (about $1.5B) are reported; Tunguz tallies about $9.75B across five companies (R/W; his own tally). Rationale quoted: per $1 of
  software, about $6 of services. Applied-AI postings reportedly up over 800% Jan-Sep 2025 (Indeed via press). No margin data for FDE-heavy startups was found.

## 2. Older transfers of the same idea

- **Toyota supplier support (Dyer & Hatch, *Sloan Management Review* 2004, abstract only, P):** supplier associations, consulting groups doing
  on-site workshops, and learning teams. Reported effects for suppliers: 14% higher output per worker, 25% lower inventories, 50% fewer
  defects, compared with their work for Toyota's rivals (authors' estimate). Toyota's Kentucky support centre opened 1992 and has worked with
  over 300 organisations (R). The knowledge moved through people on site, and it was shared across competing suppliers.
- **Franchise systems:** copying a business template precisely (Winter, Szulanski, Ringov, Jensen, *Organization Science* 2012; abstract only, P)
  and intermediaries who direct people to the template matter when direct transfer is unavailable (a Brazilian language-school franchise
  thesis, single case, W).
- **SEMATECH (1987 consortium of chipmakers; GAO, Berkeley CMR 1993, Irwin & Klenow NBER; P/R):** knowledge moved mainly when engineers went home
  after assignments. Evaluations split: some find sharing happened; Irwin & Klenow argue it subsidised work firms would have done anyway. Members
  fell from 14 to 11 by 1996.
- **Xerox technicians (Orr, *Talking About Machines* 1996; R):** the hardest repairs were solved through "war stories" exchanged between technicians;
  the knowledge was social and tied to specific machines and customers. I found no dollar figure for the later Eureka tool.
- **Surgical safety checklist (Haynes et al. NEJM 2009 and follow-ups; mostly secondary here):** original eight-hospital study reported
  47% lower mortality and 36% fewer complications; later accounts, partly Gawande's own conference remarks, say results depended on team culture and
  that mandate-only roll-outs showed no change (R/W). A South Carolina programme with Ariadne Labs reported fewer deaths (R).

## 3. What the research says about moving procedures between organisations

- **Szulanski, *Strategic Management Journal* 1996 (122 transfers, 8 firms, P):** the main barriers to moving best practice were
  *knowledge-related* (recipient's absorptive capacity, causal ambiguity, a difficult source-recipient relationship), not motivation. Later:
  adapting practices across borders raised stickiness (Jensen & Szulanski 2004); front-loading transfer methods helps when causal ambiguity is
  high but hurts when the relationship is arduous (Szulanski, Ringov, Jensen 2016). Abstract-level reading only.
- **Reuse inside consulting is hard (Waisberg & Nelson, *Organization Science* 2018, one ethnographic case, P):** junior consultants could not reuse solutions
  because they were embedded in particular contexts; they produced new insights instead. Consultancies' own knowledge-management systems are described
  by the firms' own clients as a failure in places (Bain-hosted opinion piece, dated, W); a 1999 McKinsey 40-company survey found a gap between the vision and the shop floor (book, W).
- **Where encoded practice demonstrably helped (Brynjolfsson, Li, Raymond, NBER 2023 / QJE 2025, 5,172 agents, P):** an AI assistant raised
  support productivity about 14-15%, with the largest gains for novices (34% in the working paper); the authors suggest it spreads the best agents' practices
  (suggestive, not proven). Experienced staff gained little.
- **Frameworks and standards (ITIL, COBIT, ISO):** effectiveness evidence is thin. A 2025 review of 36 hospital-IT papers credits ITIL with 25% faster incident
  resolution; a Leiden thesis says COBIT's effectiveness is unclear; a 2008 practitioner survey (n about 200) rated all frameworks low. Barriers: change resistance and
  complexity (P/W). Nothing on ISO 9001 was found.
- **Audit firms (indirect, P/W):** Big Four firms apply house working rules across clients, giving a recognisable audit style and consistency within a firm; several
  national studies find no audit-quality difference between the Big Four. None measures procedure transfer across clients directly.

## 4. Adjacent businesses that productised the pattern

- **Compliance automation (Vanta, Drata):** estimates, not filings: Vanta about $300M ARR in April 2026 (+69%), $220M by mid-2025; Drata about $98M in January 2025 (Sacra, W). The visible
  moat is the auditor relationship and being the system where trust documents live. Cross-customer evidence reuse is *not* documented; treat as unverified.
- **Process mining (Celonis):** 1,500+ customers (2023 release) or 5,000+ deployments (job post); "$18B value identified" vs "$10B delivered" in company claims (W). It uses benchmarks across
  customers in sales, but no independent description of how they are built or validated exists.
- **RPA (UiPath-era bots):** brittle to UI change. A "30-50% of initial implementations fail" figure is attributed to EY but traced to commentary, not a study; maintenance
  estimates range 30-60% of program cost (vendors selling alternatives, W). One hospital case: 18 months to automate two narrow workflows (arXiv 2405.03710, single case).
- **ERP/CRM implementation partners:** integrator ecosystems (Accenture, Deloitte, KPMG lead Forrester's Workday and Salesforce waves); failure folklore ("about 50% of CRM
  implementations miss objectives", secondary) and Revlon's SAP failure (stock -6.9%, investor suit). No revenue-share data found.
- **Consulting under AI (Brynjolfsson & Petropoulos, Stanford, Aug 2026, W/P unverified):** push toward hybrid and asset-based fees; Deloitte reportedly offers libraries of pre-built agents.
  Outcome pricing shifts risk onto the consultant, and most engagements lack the repetition to make it profitable (commentary).

## 5. Patterns (observations, not recommendations)

1. **Embedding is how tacit procedure is learned; the product is what remains after generalising it.** Palantir (FDE builds, product team generalises), Toyota (on-site learning teams)
   and Xerox (war stories) all moved knowledge through people on site. Szulanski's result explains why: the barrier is causal ambiguity and absorptive capacity, not willingness.
2. **The same team can be delivery, sales and R&D, but only while a separate generalising function exists.** Without Palantir's product-engineering half, the FDE model is consulting
   with consulting margins (Accenture about 32%); with it, reported margins were about 80%. The pattern is two functions with a hand-off, and the hand-off is the hard part.
3. **Verbatim reuse fails; reuse with adaptation works only when the context differences are modelled.** Waisberg & Nelson; Jensen & Szulanski on adaptation raising stickiness; franchising
   works with precise templates in near-identical settings. Cross-client procedure transfer is easiest where settings are alike (checklists, franchises) and hardest across contexts.
4. **Results depend on the receiving organisation's practice, not only on the artefact.** Checklists with mandates alone showed no change; frameworks fail on resistance. A procedure
   delivered without the team and authority to use it does not behave like a procedure.
5. **Encoded practice benefits novices most** (support study). Experts gain little and may lose a little quality.
6. **Cross-customer learning is claimed far more often than evidenced.** Celonis benchmarks, Vanta templates, consulting knowledge bases: all asserted, none independently audited in what I found.
7. **Automation of procedures rots when the environment changes** (RPA). Whatever is codified needs a way to notice that it no longer fits.
8. **Competitors will share when a third party mediates and the scope is non-core** (Toyota supplier networks, SEMATECH), but evaluation of the net benefit is contested, and membership shrinks.
9. **Capital and attention are flowing to this model right now** (OpenAI, Anthropic, Tunguz tally), which is evidence of demand and also of competition from the model labs themselves.

## 6. Failure modes in the record

- Becoming a consultancy: bespoke work, per-engagement margins, no compounding (the critique of Palantir; the Accenture margin gap).
- Custom builds decaying after the builder leaves.
- Pilots lost to data-access politics (Palantir).
- Knowledge bases nobody reuses (consulting KM).
- Mandated procedures without team change (checklists).
- Brittle automation (RPA).
- Integrator-led roll-outs that fail on adoption (ERP/CRM).
- Outcome pricing without repeatable scope.

## 7. Not found

Palantir early concentration and per-customer revenue; its handover practice; independent evidence that cross-customer benchmarks (Celonis, Vanta) improve outcomes; any measured
reuse rate of consulting playbooks; data on how much of a compliance engagement is reusable across clients; margins of AI-era FDE startups; ISO 9001 effect studies; Eureka's dollar value.
Many academic items were read at abstract level only; the Szulanski, Dyer & Hatch and checklist numbers should be checked against the originals before quoting.

## 8. Addendum: how ordinary people and firms became this group themselves (added the same day)

The question changed: not "a platform employs the embedded team" but "make the people and companies on the platform into that team". Precedents for
turning outsiders into practitioners of a vendor's or a method's work, about 8 searches, mostly vendor-commissioned or opinion sources.

- **Partner ecosystems (Salesforce, Shopify, Atlassian):** the vendor trains and lists third parties who then do the embedded work. Salesforce-commissioned IDC forecasts say
  partners earn $6.19 per $1 Salesforce earns by 2026 (forecast, not audited, W). Shopify's partner counts range from about 100,000 (2024) to over 700,000 (2026) depending on source, with
  over $12.5B a year of partner revenue; revenue share can stop at any time (W). Atlassian partners tier Silver/Gold/Platinum and package services into repeatable offers (vendor-commissioned IDC, W).
  *Pattern:* the platform's own product is the shared substrate the practitioners build on; I found no data on what individual practitioners earn.
- **Method certification (Six Sigma, Scrum):** GE tied pay and promotion to belts, the method spread to most large firms and the US military, and cost over $1B in training at GE (Quartz, R).
  Critics say four-week belt courses produced inexperienced practitioners who failed to deliver and discredited the method (IndustryWeek 2004, W). Scrum's certified master was described by critics as showing
  mainly that the fee was paid, revenue flows to trainers, and the "Certified" word is disputed even by insiders who say it helped Scrum spread (W; 2008-2010 blogs). *Pattern:* certification spreads a method
  fast and dilutes it fast; I found no controlled study of either claim. This is a note on credentials, not a proposal for ratings.
- **Shared codebase + services (Red Hat, WordPress):** Red Hat sells subscriptions and support around a free distribution (about $2.9B revenue in 2018, most from subscriptions; split uncertain, W);
  WordPress's agency and plugin economy sits mostly outside Automattic (low-quality source, W).
- **Reuse threshold for turning a client's work into something shared:** one practitioner source proposes requiring two independent deliveries across two customers before generalising, and
  testing "commonality" on role, task structure, inputs, rules and constraints, not just on using the same model (single unvetted source, W). It matches the Waisberg & Nelson and Szulanski findings above.
- **Firms becoming the team for other firms:** Toyota's support centre and a lean-consulting group staffed by former Toyota supplier-support people are the clearest examples found; no source on companies commercialising
  internal playbooks at Intuit, Walmart or AWS (search returned nothing relevant). Consulting itself is being repriced by AI toward hybrid and asset-based fees (Stanford, Aug 2026, as summarised).
- **Not found:** practitioner earnings in any partner ecosystem; any controlled study of productized or playbook consulting improving outcomes or margins; evidence that certification improves delivery quality.
