# Launch Mechanics Beyond X & Converting Attention to Durable Benefit (Dev Tool / MCP Server, as of Sept 2026)

## Show HN: guidelines, best practices, timing, evidence

### Takeaway
HN's official Show HN rules (news.ycombinator.com/showhn.html) reward runnable, try-without-signup projects and explicitly reject landing pages, newsletters, and low-effort submissions; secondary analyses converge on a plain-description title format and a first-person founder story as the first comment.

### Cited Findings
- Official Show HN scope: "interactive projects users can engage with directly" — runnable software/apps, physical items, hardware (with video/detailed writeup), or sample book chapters qualify — [Show HN guidelines](https://news.ycombinator.com/showhn.html)
- Explicitly off-topic and flaggable: "blog posts, sign-up pages, newsletters, lists, and other reading material," landing pages, fundraisers, minor version-bump updates ("Foo 1.3.1 is out"), and projects not ready for user interaction — [Show HN guidelines](https://news.ycombinator.com/showhn.html)
- Quality bar stated directly: "Don't post quickly-generated one-offs; anybody can do that now" — submissions should show personal investment, and the creator must be available to discuss it — [Show HN guidelines](https://news.ycombinator.com/showhn.html)
- Accessibility requirement: projects should be "easy for users to try your thing out, ideally without barriers such as signups or emails" — [Show HN guidelines](https://news.ycombinator.com/showhn.html)
- Title must begin with "Show HN" to qualify for the Show HN section — [Show HN guidelines](https://news.ycombinator.com/showhn.html)
- Community conduct guidance: commenters should stay respectful, ask genuine questions, offer constructive suggestions — [Show HN guidelines](https://news.ycombinator.com/showhn.html)
- Secondary-source convention (not official HN text, but converges across independent write-ups): title format "Show HN: Name – plain description of what it does," avoiding superlatives (fastest/biggest/first/best) and passive phrasing ("app that offers") in favor of active, modest language; top posts often name a specific use case (e.g., "minimalist scoring tool for board games") — [Syften HN guide](https://syften.com/blog/hacker-news-marketing/), [Favors.dev 2026 guide](https://favors.dev/blog/show-hn-launch-guide)
- First-comment convention: post a comment with the backstory of how the project came to be and what's different about it, written in first person ("I've been working on...") — this seeds discussion in a good direction — [Syften HN guide](https://syften.com/blog/hacker-news-marketing/)
- Opinion/observation from a marketing-focused analysis: founders "usually" write the title and first comment "in a rush minutes before posting," and that under-preparation is treated as the most common mistake (this is an opinion/anecdotal claim from an aggregator, not HN-official) — [Favors.dev 2026 guide](https://favors.dev/blog/show-hn-launch-guide)

### Inferences
- For an MCP server/router, the "try without signup" requirement is a real constraint: a hosted component gated behind auth/onboarding likely needs a local-first or no-signup demo path to be Show-HN-eligible at all.
- Given the explicit ban on "landing pages," the Show HN submission should link directly to a working repo/tool, not a marketing page — a separate site can still exist but shouldn't be the submitted URL.

### Gaps
- No official HN statement found on optimal posting time/day (this is repeatedly claimed in secondary blog posts, e.g., weekday mornings US time, but none of the fetched sources is HN's own FAQ; the researcher.md-mandated primary-source preference means this should be flagged as unconfirmed folklore, not cited as fact).
- Did not fetch the HN FAQ (news.ycombinator.com/newsfaq.html) directly — only showhn.html was retrieved; flagging behavior mechanics (vouching, flag thresholds) are not confirmed from a primary source in this pass.
- No quantitative "successful Show HN" dataset/analysis was fetched (e.g., time-to-front-page distributions) — only qualitative guide content.

---

## Product Hunt in 2026 for dev tools

### Takeaway
Product Hunt remains a viable "concentrated attention moment" for launches in 2026 but is explicitly described (even in industry commentary) as not a standalone traffic engine; for developer tools specifically, complementary devtool-specific launch platforms (e.g., DevHunt) are positioned as additive, not replacements.

### Cited Findings
- Product Hunt's stated advantage is "concentrated attention" — creating a specific launch moment rather than slow organic discovery — [Is launching on Product Hunt still worth it in 2026?](https://www.producthunt.com/p/general/is-launching-on-product-hunt-still-worth-it-in-2026) (opinion/discussion thread, treat as anecdotal)
- Industry commentary (opinion): "Launching products today is harder than ever, with more competition, shorter attention spans, AI products dropping every hour" — [same discussion, opinion]
- Caveat repeated across sources: "Product Hunt shouldn't be treated as a magic traffic machine — a successful launch usually requires preparation and distribution," and "what worked a year ago does not always work today" — [Hackmamba 2026 dev tool PH guide](https://hackmamba.io/developer-marketing/how-to-launch-on-product-hunt/) (opinion/guide, not PH-official)
- DevHunt (a devtool-specific alternative) claims a full week of front-page visibility vs. PH's single day, "a few hundred to a few thousand targeted developer visitors," and a backlink from a tech-focused domain — explicitly framed as a complement to, not replacement for, PH/HN/GitHub/Reddit/SEO/dev communities — [LaunchBuff: Is DevHunt worth it in 2026?](https://launchbuff.com/blog/is-devhunt-worth-it-2026) (vendor-adjacent source; self-interested framing likely)

### Inferences
- Treat Product Hunt as one node in a multi-channel push timed to the same day/week as Show HN and any MCP-registry listing, not a channel to run in isolation — this matches the "multi-channel" framing of the assignment.

### Gaps
- Could not verify with a Product-Hunt-official (first-party) source how PH's 2026 algorithm/ranking works (e.g., current weighting of upvotes vs. comments vs. maker reputation) — all findings above are third-party commentary or a PH community-forum discussion, not PH's own blog/docs. This should be labeled uncertain/opinion in the final report.
- No dev-tool-specific PH case study with hard numbers (installs, signups) was retrieved in this pass.

---

## MCP/agent-specific developer distribution channels

### Takeaway
For an MCP server with a hosted remote component, the two first-party channels are the official MCP Registry (protocol-level) and Anthropic's Connectors Directory (submitted via claude.ai/directory/manage); third-party discovery layers (Glama, Smithery, PulseMCP, mcp.so) each have a distinct mechanism worth targeting — Glama for enterprise/verified credibility, Smithery for actual hosting, PulseMCP for its newsletter/editorial reach, mcp.so for passive indexing.

### Cited Findings
- Anthropic's Connectors Directory submission is done through the developer portal at claude.ai/directory/manage, choosing "MCP connector"; only remote (HTTPS) servers can be submitted as connectors — local/desktop MCP Bundle (MCPB) listings are deprecated and must instead be bundled into a "plugin" — [Anthropic: Submit a connector to the directory](https://claude.com/docs/connectors/building/submission)
- Hard submission requirements: every tool needs a `title` plus a `readOnlyHint` or `destructiveHint` annotation; OAuth 2.0 required if tools act on a user's account; a privacy policy is mandatory for local connectors (missing/incomplete privacy policy = "immediate rejection"); documentation URL, privacy policy URL, support contact, and an icon are required listing materials; a fully-populated reviewer test account must be supplied — [Anthropic: Submit a connector to the directory](https://claude.com/docs/connectors/building/submission)
- Submission flow has 9 portal steps: Connection → Tools → Listing (name ≤100 chars, one-liner ≤200 chars, description ≤2,000 chars, 1–5 categories) → Use cases → Company → Authentication → Data handling → Test & launch → Compliance (7 required policy acknowledgments) → Review and submit — [Anthropic: Submit a connector to the directory](https://claude.com/docs/connectors/building/submission)
- After submission, Anthropic auto-scans for policy compliance and by default lists the server as a "Community connector" with no human action required; some submissions additionally get human review, with variable queue-dependent timing; escalations go to `mcp-review@anthropic.com` — [Anthropic: Submit a connector to the directory](https://claude.com/docs/connectors/building/submission)
- A Community listing can become "Verified" status over time (see linked "Connector verification" doc; not independently fetched here, but referenced as the escalation path from Community → Verified) — [Anthropic: Submit a connector to the directory](https://claude.com/docs/connectors/building/submission)
- Third-party MCP directories, per a 2026 comparison source: Glama tracked "nearly 37,000 servers" as of mid-2026 and splits listings into "Official" (publisher-verified) and "Claimed" (author-verified) tiers; Smithery is distinguished by having real hosting infrastructure attached (its `npx @smithery/cli` can actually run a server, not just link to a repo); PulseMCP pairs its directory with a newsletter and community following, so a listing reaches newsletter readers, and "if your server is genuinely high-quality, editorial curation will surface it"; mcp.so indexes servers automatically and the main action needed is to "claim ownership" — [Tallyfy: How to list on MCP Registry/Smithery/Glama](https://tallyfy.com/how-to-list-mcp-server-registry-smithery-glama-pulsemcp)
- Recommended sequencing from the same source: "if you build MCP servers you want enterprise buyers to find: Glama first. Claim your listing. Pursue the tier system to whichever level your operational maturity supports." — [Tallyfy](https://tallyfy.com/how-to-list-mcp-server-registry-smithery-glama-pulsemcp) (opinion/vendor-comparison framing)

### Inferences
- Given StealthLab's MCP server has a hosted component, it qualifies for the "MCP connector" (remote) submission path rather than the plugin/local path — the privacy-policy and OAuth requirements are non-negotiable gates, not optional polish, and should be built before submission, not after.
- The "Community connector, auto-listed" default means listing is low-friction and fast; "Verified" status is a separate, presumably slower credibility upgrade worth pursuing after initial traction, not before.
- PulseMCP's newsletter pairing makes it functionally a distribution channel, not just a directory — worth treating it like the "newsletter" bucket (TLDR/Latent Space/Ben's Bites) rather than purely a static listing.

### Gaps
- Did not fetch the official MCP Registry (the protocol-level registry distinct from Anthropic's Connectors Directory — likely modelcontextprotocol.io's registry) directly; submission mechanics for that specific registry are not confirmed from a primary source in this pass.
- Did not independently verify Glama's 37,000-server figure or PulseMCP's audience size against a first-party source — both come from a single third-party comparison blog (Tallyfy) and should be treated as a single-source claim.
- No confirmed mechanics for Claude Code plugin marketplaces specifically (distinct from the Connectors Directory) were retrieved — the fetched doc mentions "submit a plugin" as a separate flow but its details weren't fetched.
- No data found on Reddit communities or Discord servers specific to MCP/agents (e.g., r/mcp, official Anthropic Discord) — this sub-question is an outright gap; not researched in this pass due to tool-call budget.
- No data found on awesome-list mechanics (e.g., awesome-mcp-servers PR process) — gap.

---

## Open-source growth tactics (README, stars, good-first-issues, launch weeks)

### Takeaway
Star counts are a weak, gameable proxy for usage (cumulative, non-decaying, hype-sensitive) that developers nonetheless rely on heavily when evaluating projects; "good first issue" labeling has a measured, positive effect on new-contributor counts, and quarterly "Launch Week" cadences (pioneered/popularized by Supabase) have become a dominant devtool-marketing format industry-wide.

### Cited Findings
- Stars are cumulative and don't decay: "an 8k-star repo last touched eighteen months ago still looks impressive next to an 800-star repo shipped every week" — [Rushi's: What GitHub stars measure](https://www.rushis.com/github-stars-what-they-measure-and-what-they-do-not/)
- "75% of surveyed developers consider star counts crucial when evaluating GitHub projects," though people star repos for varied reasons (appreciation, bookmarking, current usage) — [dasroot.net / GitHub star counts meaning](https://dasroot.net/posts/2026/02/github-star-counts-meaning-project-popularity/) (specific survey source not fully traceable from snippet)
- Suggested better/complementary usage signals: pull-request activity, issue-resolution time, documentation quality, and for npm-published projects, actual npm download stats — [same cluster of sources]
- Stronger devtool "buying signal" hierarchy cited: opening an integration/usage issue > forking a repo > submitting a PR > starring multiple competing repos in quick succession — [LeadCognition: GitHub activity buying signals](https://leadcognition.io/blog/github-activity-buying-signal/) (marketing-research framing, treat as industry heuristic not peer-reviewed)
- Quantified good-first-issue effect: projects with ~25% of issues labeled "good first issue" saw 13% more contributors than projects without; projects with ~40% of issues so labeled saw 21% more new contributors — [GitHub README: secrets to onboarding open source contributors](https://github.com/readme/featured/contributor-onboarding)
- Maintainer best practices repeatedly cited: clear CONTRIBUTING.md, fast response times and explicit thanks to first-time contributors, and documentation as "one of the most helpful ways to onboard new contributors" — [opensource.guide: Best Practices for Maintainers](https://opensource.guide/best-practices/), [daily.dev: 10 tips](https://daily.dev/blog/open-source-contributor-onboarding-10-tips)
- Supabase originated the "Launch Week" format: after their first unplanned viral Hacker News launch produced a "ten-fold" overnight increase in hosted databases, a retrospective led to shipping "one major feature or announcement every day for a week" instead of one single-day launch — [Supabase: How we launch](https://supabase.com/blog/supabase-how-we-launch)
- Supabase's Launch Week cadence: every 3 months, each cycle = 3-month build period + one week of daily announcements; features actually ship to production about a week before Launch Week, so the week itself is "full write-ups, marketing copy, and broad top-of-funnel marketing" layered on already-shipped work — [Supabase: How we launch](https://supabase.com/blog/supabase-how-we-launch)
- Supabase assigns marketing-channel ownership per feature to the engineer who built it (e.g., UI-component builders hit design/frontend channels; storage builders targeted Hacker News and technical conferences directly) — [Supabase: How we launch](https://supabase.com/blog/supabase-how-we-launch)
- Supabase recruited "Technical Angel Investors" to amplify launch messaging and ran a dedicated "Community Day" spotlighting complementary open-source projects — [Supabase: How we launch](https://supabase.com/blog/supabase-how-we-launch)
- Supabase's stated growth trajectory: 47% month-on-month database growth sustained over 18 months, attributed to the product-led-growth + Launch Week strategy; between Series A (Sept 2021) and Series B (Aug 2022), developers grew 40K→110K, databases 50K→150K, GitHub stars 19K→36K — [Startup Riders: Supabase Growth Playbook](https://www.startupriders.com/p/supabase-growth-playbook) (secondary aggregator; cross-check against Supabase's own numbers if precision matters)
- The Launch Week format has since spread industry-wide: "126 Launch Weeks were tracked across 94 dev-tool companies in 2024 alone" — [launchweek.dev: Growing to 50K+ GitHub stars](https://launchweek.dev/n/rorstro) (a site specifically tracking this phenomenon; treat as a specialized but not fully independently verified tally)

### Inferences
- A single one-off launch (X + HN + PH) produces a spike; the evidence suggests durable star/contributor growth for devtools comes from a repeatable cadence (quarterly Launch Week-style pushes) layered on top of the initial launch, not the initial launch alone.
- Investing in "good first issue" labeling discipline is a comparatively cheap, evidenced lever for contributor growth and should be part of the pre-launch checklist (have some issues pre-labeled before the traffic spike hits).

### Gaps
- No peer-reviewed academic study was directly fetched (only its listing appeared: NDSS-symposium "An Analysis of GitHub Stars as an Importance Metric" — title retrieved but content not fetched in this pass, so its specific findings are not cited above to avoid fabrication).
- No first-party Resend, Cal.com, or Tailscale launch retrospective was found in this pass — see gap note in the "converting attention" section below.

---

## Measurement: analytics, attribution, retention

### Takeaway
For a CLI/package-distributed dev tool, the field converges on a funnel of install → activated → first_success → d7_retained built from privacy-respecting, explicitly opt-in telemetry, treating npm/PyPI download counts as an external distribution signal rather than the actual user-count denominator; X-native analytics (impressions, engagement, profile visits) were not separately documented by a primary source in this pass.

### Cited Findings
- A stated best-practice funnel for package-based devtools: "install → activated → first_success → d7_retained," with weekly-active treated as a rolling health measure and user feedback as a side channel, not the primary metric — [GitHub: liush2yuxjtu/telemetry — privacy-first opt-in telemetry](https://github.com/liush2yuxjtu/telemetry)
- Explicit warning against overstating opt-in telemetry: "opt-in, best-effort measurements should not be described as total users," and "npm downloads are an external distribution metric, not the funnel denominator" — [same source]
- Privacy-engineering requirements cited for a telemetry pipeline: verify that collectors, load balancers, CDNs, reverse proxies, hosting platforms, and observability/error tooling do not retain IPs, forwarded-IP headers, user agents, or raw request dumps; deduplicate on `event_id`; cap request sizes; define a short retention/deletion policy for pseudonymous events — [same source]
- Dedicated download-stat services exist as the standard way to read npm/PyPI distribution numbers: npm-stat.com and pypistats.org (official PyPI BigQuery-based stats guide is linked from the Python Packaging User Guide) — [npm-stat.com](https://npm-stat.com/), [PyPI Download Stats](https://pypistats.org/), [Python Packaging User Guide: Analyzing PyPI package downloads](https://packaging.python.org/guides/analyzing-pypi-package-downloads/)

### Inferences
- For StealthLab's hosted MCP component, the same install→activated→first_success→d7_retained funnel shape likely transfers directly (install = connector added; activated = first tool call; first_success = first successful task completion; d7_retained = still calling after 7 days), and should be built on explicit opt-in, matching the repo's stated telemetry/hook-wrapper conventions already in use for trace collection.

### Gaps
- Did not find primary-sourced guidance specifically on X/Twitter's own analytics metrics (impressions, engagement rate, profile-visit-to-follow conversion) for launch measurement — this sub-question is effectively unanswered in this research pass; a follow-up search restricted to X's own developer/analytics documentation would be needed.
- Did not find a specific "which metrics predict dev-tool launch success" empirical study — the funnel-shape finding above is best-practice/engineering guidance, not a validated predictive study.
- Did not verify UTM-parameter conventions specifically for correlating social-launch traffic to install events — likely straightforward (standard UTM on doc/README links, attribution keyed at first `activated` event) but not confirmed against a named source.

---

## Converting attention into business value (design partners, community, monetization timing)

### Takeaway
Design-partner programs are a well-defined B2B pattern (5–15 hand-picked early customers trading roadmap influence and preferential pricing for deep engagement), and open-core/dev-tool monetization commonly follows a free/open community phase before a paid layer is introduced — but this pass could not source first-party retrospectives from PostHog, Resend, Cal.com, or Tailscale specifically, which is a notable gap given they were named as target examples.

### Cited Findings
- A design partner program is defined as "a structured early-access arrangement between a B2B startup and 5–15 hand-picked customers who co-build the product in exchange for influence over the roadmap, preferential pricing, and (sometimes) equity warrants" — [Koji: Design Partner Program guide](https://www.koji.so/docs/design-partner-program) (guide/opinion source, not an academic or first-party company source)
- One example structure found: a design-partner program targeting "teams with 10+ engineers actively running coding agents," with a feedback cadence of every 2–3 weeks — [px0.ai Design Partners](https://px0.ai/design-partners/) (a single vendor's own program page, illustrative rather than authoritative guidance)
- PostHog: launched on Hacker News in February 2020 to "an overwhelmingly positive response"; described as having "over 15 thousand stars on GitHub" and "6x revenue growth" in December 2022 "amid a challenging tech market" — [Analytics Insight: Top 10 Open Source Startups](https://www.analyticsinsight.net/startups/top-10-open-source-startups-in-2025) (secondary aggregator, numbers not cross-checked against PostHog's own blog)
- Cal.com: described as used by "over 40,000 teams," positioned as a Calendly competitor — [same aggregator source, unverified against Cal.com's own materials]

### Inferences
- None drawn with confidence — the source base here is too thin (mostly secondary aggregators and vendor self-description) to responsibly generalize a "playbook" for converting attention into design partners/revenue for this category.

### Gaps
- **Significant gap**: no first-party Resend, Cal.com, or Tailscale launch/growth retrospective was located in this research pass — searches surfaced only tangential integration docs (e.g., Tailscale's PostHog data-source integration page) rather than growth writing from Resend, Cal.com, or Tailscale themselves. A dedicated follow-up pass querying each company's own blog directly (e.g., site:resend.com blog, site:tailscale.com blog "open source") is needed.
- No sourced guidance found on waitlists vs. open launch as a deliberate choice (tradeoffs, when each is used) — not covered by any fetched source in this pass.
- No sourced guidance found on ethically collecting testimonials (consent practices) — not covered in this pass; flagged as an outright gap, not fabricated.
- No sourced guidance found on open-core monetization *timing* specifically (e.g., "wait N months/users before introducing a paid tier") — the Koji/px0 sources describe design-partner mechanics but not monetization sequencing.
- Given the tool-call budget for this research pass was exhausted before these could be chased down, this entire section should be treated as the weakest-sourced part of the report and prioritized for a follow-up research pass if the report writer needs it filled in.

---

## Operational: handling a launch-day traffic spike

### Takeaway
Standard guidance is to load-test before the known launch date, rely on auto-scaling/caching/circuit-breakers validated under realistic load, and use a status page as the primary support-deflection mechanism so the support channel isn't overwhelmed by duplicate "is it down" tickets.

### Cited Findings
- "You cannot be confident in your spike handling until you have tested it, [load testing] validates your auto-scaling, caching, and circuit breakers under realistic conditions" — [Zero To Mastery: DevOps Strategies for Handling Traffic Spikes](https://zerotomastery.io/blog/how-to-handle-traffic-spikes/)
- Product launches are called out specifically as a category of "known start time" traffic event that should be pre-planned for, distinct from unpredictable spikes — [PFLB: How to Prepare Your Website for Traffic Spikes](https://pflb.us/blog/how-to-prepare-your-website-for-traffic-spikes/)
- Status page value proposition: "instead of opening a ticket, [customers] check the status page... the information asymmetry disappears, and with it, the ticket storm" — [Flowtriq: Status Pages That Update Themselves](https://flowtriq.com/blog/status-pages-kill-ticket-storm) (vendor blog, but the mechanism described is a widely-used pattern)
- An open-source status-page option exists specifically engineered to stay up during the incidents it's reporting on: "LambStatus is a serverless, open-source status page system whose architecture ensures your status page remains accessible even during high-traffic incidents" — [DEV Community: 15 Free Status Page Tools](https://dev.to/cbartlett/15-free-status-page-tools-in-2025-5elg)
- k6 is cited as a developer-friendly load-testing tool for pre-launch capacity validation — [OneUptime: How to Plan for Traffic Spikes](https://oneuptime.com/blog/post/2026-01-27-capacity-planning-traffic-spikes/view)

### Inferences
- For StealthLab's hosted component specifically, given the repo's own note that the MCP server must run with `--workers 1` because its `TasksExtension` store is in-memory, a real launch-day spike is a concrete operational risk (single-worker process, no horizontal scale-out) — this is a repo fact, not sourced externally, and worth flagging to whoever owns the launch operationally: the current architecture may need either a scale-tested ceiling estimate or an explicit "expect single-worker capacity" plan before a high-traffic public launch.

### Gaps
- No dev-tool-specific (as opposed to generic e-commerce/web) case study of handling an actual HN/PH front-page spike was retrieved — the sourced guidance above is generic DevOps advice, not launch-specific war stories.
- No sourced guidance on issue-triage labeling/staffing specifically for a launch week (e.g., dedicated triage rotation) was found.

---

## Overall cross-cutting gaps for the report writer

- Newsletter *submission mechanics* (how to actually get into TLDR, Latent Space, or Ben's Bites) were not found — only descriptions of what each newsletter is. A follow-up should check each newsletter's own "submit/tips" page directly.
- Reddit/Discord community specifics for MCP/agents were not researched (budget-constrained) — flagged as a clean gap, not guessed at.
- The "converting attention into business value with named dev-tool examples" question is the weakest-sourced section in this file and should be treated as needing a dedicated follow-up pass against each company's own blog before the report writer relies on it.
- No official Hacker News FAQ (newsfaq.html) content was fetched — only showhn.html; if the report needs flagging-threshold mechanics specifically, that primary source should be fetched separately.
