# Homepage hero CTAs: "Get started" + "Contact now" + admin sign-in (B2B dev tools)

Method note: WebFetch returns text-converted pages, often agent-oriented summaries without the real header/footer markup. Competitor findings are therefore partial and flagged. Searches returned mostly vendor blogs; no NN/g or CXL page on this topic was retrieved.

## (1) Button pairing, hierarchy and wording

### Takeaway
Use one filled primary ("Get started") and one visually lower-weight secondary for contact, primary on the left. Prefer a specific label ("Contact sales" / "Talk to sales") over "Contact now". The evidence on wording is vendor data only and not independently verified.

### Cited Findings
- Design systems agree on one primary-style button per visible section, with the secondary styled less prominently, and the primary on the left when two buttons sit together (BlackRock). Morningstar says same-type buttons should not sit adjacent. — [Morningstar](https://design.morningstar.com/systems/marketing/business-website/components/buttons), [BlackRock](https://www.blackrock.com/design/nested-components/buttons), [SaaSframe](https://www.saasframe.io/best-practices/cta-hierarchy)
- Docket (vendor) reports demo-intent labels converting 13.1% vs 4.8% for generic "Contact Us"/"Book a Meeting". Own dataset, unverified. — [Docket](https://www.docket.io/blog/increase-demo-requests-on-your-b2b-website)
- A single unverified example: "Contact us" 5.1% vs "Get a free demo" 12.4%. — [aitom.cz](https://www.aitom.cz/en/slovnik/call-to-action-cta-vyzva-ktera-v-b2b-rozhoduje-o-konverzi)
- Opposing view (vendor, unsourced): "Contact Us" suits visitors not ready for a demo. Blend B2B says "talk to sales" is clearer about what happens next than "contact us", and that the CTA must deliver what it promises (a "Book a demo" label should land on a booking flow). — [Blend B2B](https://www.blendb2b.com/websites-decoded/improve-website-conversion-rate), [Knock](https://blog.knock-ai.com/generate-pipeline-from-navigation-bar)
- NN/g heuristics (match with real world, consistency) give no button-label guidance specifically. — [NN/g](https://www.nngroup.com/articles/ten-usability-heuristics/)

### Inferences
- "Contact now" is vague and implies urgency without saying who you reach. Peers use "Talk to sales", "Contact sales", "Request a demo" (see section 3). "Contact sales" or "Talk to us" is the safer match.
- Pick the label by what the destination does: form -> "Contact sales"; calendar -> "Book a demo".

### Gaps
- No independent (NN/g, CXL, Baymard) study on "Contact now" or on contact-vs-demo wording found. The numbers above are vendor claims.

## (2) Contact destination for developer buyers

### Takeaway
Keep the form to the fewest fields and state what happens next. I found no peer that publishes a response-time promise on the pages I could read.

### Cited Findings
- Baymard (checkout, not B2B contact) finds field count affects usability more than step count; average checkout 11.3 fields (2024), most sites need only 8. Latest verified Baymard figures are 2024; third-party 2025 numbers conflict. — [Baymard](https://baymard.com/blog/checkout-flow-average-form-fields)
- Supabase sales form: First Name, Last Name, Company Email, "What are you interested in?", plus a privacy-policy consent line. No response-time promise, no embedded calendar; "Request a demo" form is the main route, support routed elsewhere. Required-ness not visible. — [Supabase](https://supabase.com/contact/sales)
- Vercel's /contact/sales fetch returned no form fields, response promise or booking tool (page content was not the real form). — [Vercel](https://vercel.com/contact/sales)

### Inferences
- A minimal form: work email, name, one free-text "what do you need" field (optional company). Four fields or fewer matches the Supabase peer.
- Add a one-line promise of the next step (for example reply time), since the peers I could read omitted it. The specific time must be one the team can actually meet.

### Gaps
- No evidence found on form-vs-booking conversion for developer buyers, nor on gating friction. Response-time promises of peers not verified (pages did not expose them).

## (3) How well-known dev-tool sites lay out hero and contact CTAs

### Takeaway
Peers pair a "start" action with a sales action in the hero or header; sales wording is "Talk to sales", "Contact sales", "Request a demo", or "Talk to a human".

### Cited Findings
- Vercel hero: "Deploy now" and "Talk to sales". — [vercel.com](https://vercel.com)
- Cursor hero: "Download for macOS", "Get started", "Request a demo"; header: "Sign in", "Contact sales", "Download". — [cursor.com](https://cursor.com)
- PostHog hero: "Get started – free"; side menu has "Talk to a human" and "Demo". — [posthog.com](https://posthog.com)
- Linear: no hero button; header has "Docs", "Open app", "Log in", "Sign up", plus a "Contact" nav link; "Contact sales" appears in the closing section next to "Get started". — [linear.app](https://linear.app)

### Inferences
- Two of four verified peers put a sales CTA next to the start CTA in the hero; Cursor places "Sign in" in the header away from the hero.

### Gaps
- Supabase, Sentry, Cloudflare, Neon, Resend: fetches returned no usable button labels (summarised or doc-only content). Not verified; check live in a browser.

## (4) Operator/admin login vs customer sign-in

### Takeaway
I found no peer that shows a separate "administrator" link on its marketing site. Customer sign-in sits in the header ("Sign in" / "Log in").

### Cited Findings
- Cursor header "Sign in"; Linear header "Log in" and "Open app". Neither shows a distinct admin/operator login in the fetched content. — [cursor.com](https://cursor.com), [linear.app](https://linear.app)

### Inferences
- Treat "Sign in as administrator" as a low-emphasis text link in the footer or header, never styled as a button next to the hero CTAs, so it is not confused with "Get started". Wording of "Administrator sign in" (already used on /sign-in and footer per repo log) is consistent with that.

### Gaps
- Whether peers hide operator consoles behind separate subdomains or the same login: not found. Treat the above as design judgement, not research.

## (5) Accessibility and mobile for adjacent CTAs

### Takeaway
Meet WCAG 2.2 AA 24x24 CSS px minimum; aim for 44x44 as best practice, and keep spacing between the two buttons.

### Cited Findings
- WCAG 2.2 SC 2.5.8 (AA): pointer targets at least 24x24 CSS px, or undersized targets pass if a 24px circle centred on each does not intersect another target. — [W3C](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)
- SC 2.5.5 (AAA): 44x44 CSS px, with exceptions including inline links and an equivalent target elsewhere. — [W3C](https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced.html)

### Inferences
- Stack the buttons full-width on narrow screens (primary on top), each >= 44px tall, with a visible gap. Labels must differ in text, not just colour; the admin link, if small inline text, relies on the inline or spacing exception, so keep it clear of the buttons.

### Gaps
- Mobile-specific conversion research for paired CTAs was not retrieved; focus order and contrast rules not researched (apply WCAG 1.4.3 and 2.4.7 by default, not verified here).

## Direct answers
- Wording: "Get started" (primary, filled, left/top); "Contact sales" or "Talk to us" (secondary, outlined) instead of "Contact now"; "Administrator sign in" as a small footer/header text link.
- Contact page: ask work email, name, and what they need (optional company); promise a concrete next step and reply time only if you can honour it. Unverified: any peer's published response time.
