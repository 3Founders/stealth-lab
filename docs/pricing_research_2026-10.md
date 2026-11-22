# Pricing research: how kell could charge (2026-10-31)

Status: desk research from web search results, mostly third-party blogs and vendor pages. Nothing here was checked against a vendor's own pricing page, so **re-verify any figure before it goes on the website or into a deck**. Figures marked *(general knowledge)* come from the author's background, not from a search. This is input to a founder decision, not a decision. See `docs/billing.md` for what is built.

## 1. What we are pricing

kell sits between a person's coding tool or assistant and the models. It recommends a method and the cheapest model likely to pass, runs routine steps on lower-cost models, checks the result and records it. Its value is **cost per accepted task goes down, with quality held**. Two things follow:

- It is an **add-on to a tool the buyer already pays for** (Claude Code, Cursor), so a price near the tool's own price is a hard sell.
- Our own measurements are **mixed** (one run saved 32-63%, another cost more than Opus alone), so any price that promises savings has to cope with a run that saves nothing.

## 2. Coding-assistant pricing (the buyer's reference point)

- Entry paid plans cluster at **$10-$20 a month** (Copilot Pro about $10; Cursor Pro, Claude Pro and the Windsurf successor about $20). Top individual plans run **$100-$200** (Copilot Max, Claude Max, Cursor Ultra). Business seats run about **$19-$40 per user**.
- The 2026 shift is to **metered usage on top of a seat**: Copilot moved all paid plans to AI-credit billing (1 credit = $0.01) on 1 June 2026; Cursor uses a credit pool tied to the model chosen; Windsurf moved to daily and weekly quotas. One source puts serious Claude Code API use at roughly $3-$15 per active developer day (an older blog, not official).
- Reading: buyers already accept "seat plus usage". They are also used to being surprised by overage, so a **predictable cap** is itself a selling point.

Our current plans: Pro ₹999 (about $11 at ₹90/$), Teams ₹1,800 per seat (about $20), pilot ₹649 per seat for two weeks. That puts Pro at the Copilot Pro level and Teams at the Copilot Business level, **for an add-on**.

## 3. LLM gateways and routers (our closest category)

- **OpenRouter:** no per-token markup, a **5.5% fee on credit purchases** (one source says a Business plan is 8%; BYOK fees are reported inconsistently, 0.5% to 5%). Min fee about $0.80.
- **Portkey:** free tier, then about **$49 a month** plus logging overage. **LiteLLM:** open source, self-hosted, roughly $50-$200 a month of infrastructure.
- **Martian, Not Diamond:** no usable pricing found.
- One comparison puts the break-even between a percentage fee and a flat fee at about $1,000 a month of spend.
- Reading: the market norm for **moving tokens is a single-digit percentage**. Our placeholder **50% margin on provider cost is roughly ten times that**. It can only be defended if the buyer sees value beyond access (the routing and the measured savings), and a managed open-model tier competes on price per token with DeepInfra, Together and the like.

## 4. Pricing-model trends for AI products

- Pure per-seat is falling (one report: 21% to 15% of SaaS companies in a year); **hybrid (platform fee plus usage) is the most cited winner**, with adoption figures from 37% to 56% depending on the survey. The sources are vendor blogs reporting second-hand numbers.
- Buyer preference (Futurum, 1H 2026 survey, via a press release): **43% prefer consumption-based, 27% outcome-based**.
- **Outcome pricing** is live mainly where a unit of work is clear and countable: per resolved support ticket (Intercom reported at $0.99, Zendesk at $1.50-$2.00, from a vendor-adjacent blog).
- Reading: for kell the countable unit is an **accepted task**. We already record accepted or not, cost and time per task, so the data to bill on outcomes exists. Whether a buyer will trust our own pass/fail is a separate question.

## 5. Adjacent industries

| Industry | How they charge | What it suggests for kell |
|---|---|---|
| **FinOps / cloud cost tools** (ProsperOps, nOps, Usage.ai) | A **share of realised savings** (ProsperOps and Usage.ai); nOps splits into a savings share for automation plus a flat fee for visibility. None publish a standard rate, so ask for quotes. | The closest analogue. A savings share needs a **baseline and a dispute-proof way to measure savings**. Our joint report (cost per solved task with and without kell) is exactly that baseline. |
| **Payment gateways** *(general knowledge)* | A **take rate** on volume (a percentage plus a small fixed fee). | Same shape as the gateway fee above: easy to explain, scales with use, no value claim needed. |
| **Cloud platforms and Snowflake-style warehouses** *(general knowledge)* | **Prepaid commitments and credits**, volume discounts for larger commitments, credits that expire. | Prepaid credits with a discount for larger top-ups are normal. Expiry terms must be disclosed and checked against Indian prepaid-instrument rules. |
| **Telecom prepaid** *(general knowledge)* | Top-ups with a **validity period** and small bonus tiers. | Familiar to Indian buyers, but expiry of paid balances draws regulatory and consumer attention. |
| **Twilio, Stripe-style APIs** *(general knowledge)* | Pay-as-you-go with **automatic volume tiers** and a committed-use discount. | Cheap to start, rewards growth, no seat counting. |
| **Contingency fees (legal recovery, insurance claims, performance marketing)** *(general knowledge)* | Paid only on success, usually a percentage. | Low barrier to try, but a buyer can game the outcome and the vendor carries the risk of a bad run. Needs a floor, a cap and a clear definition of "success". |

## 6. India specifics

- UPI AutoPay is the main recurring rail. In Paddle's implementation it applies to INR-priced recurring items where **each renewal is under ₹15,000**; the customer pre-approves a maximum charge shown in their UPI app (one source suggests keeping the ceiling within about 30% of the first charge).
- Several sources say successful Indian subscription products price **entry plans below roughly $5 a month**, and that conversion starts dropping near $10. Vendor-sourced and unverified.
- Purchasing-power pricing: guides suggest starting at about a **30% discount** for India and measuring before going deeper; JetBrains-style regional discounts of up to 50% and a 2023 Basecamp India price of about $4 a month are cited. Our plans are already priced in rupees, but ₹999 is **above** the $5 threshold these sources cite.
- Local checkout (rupees, UPI) is reported to raise conversion; the 2-3x figure comes from a vendor and is unverified.

## 7. Options for kell

| Option | How it works | For | Against |
|---|---|---|---|
| **A. Seat plus prepaid usage** (current plan) | ₹999 / ₹1,800 per seat; extra usage as prepaid credits at provider cost plus a margin | Matches how buyers expect AI tools to bill; predictable | Priced like the main tool; the add-on is a hard sell; margin is the sticking point |
| **B. Take rate on routed spend** | e.g. a single-digit percentage of the spend kell routes, no seat | Lowest friction, scales with use, matches gateway norms | Small revenue per customer; nothing to charge for people who bring their own key and low volume |
| **C. Share of verified savings** | A percentage of the measured saving per accepted task, compared with the buyer's baseline; zero when there is no saving | Charges only when we deliver; strong story for a pilot | Needs a trusted baseline, measurement disputes, lumpy revenue, risk of a run that saves nothing |
| **D. Hybrid (recommended to test)** | A small platform fee per seat (covers recommendations and the record) **plus** usage at provider cost and a modest margin; **offer C as an enterprise option** once a joint report exists | Matches the market trend; keeps C for buyers who want proof first | More to explain |

## 8. Suggestions (judgement, not findings)

1. **Cut the managed-inference margin well below 50%.** Gateways charge about 5-8% to move tokens. Start in the **10-25%** range for a managed open-model tier and keep bring-your-own-key at no fee. Revisit when we know our provider costs.
2. **Price the pilot as a paid trial of the savings claim.** ₹649 per seat for two weeks is reasonable. Make the joint report the product: cost per accepted task with and without kell.
3. **Test a lower entry price for individuals** (below the current ₹999) or a free tier with a small cap, because India-focused sources put the conversion drop near $10. This needs our own test, not a guess.
4. **Keep Pro and Teams as seat-plus-usage, but add a hard spending cap per user and per month**, which the product already has. Predictable bills are what the usage-billing backlash is about.
5. **Hold off on a savings-share offer until we have a repeat measurement we trust.** Our own results are mixed, and a contingency model needs a floor (minimum fee), a cap, and a clear definition of "accepted".
6. **Do not show a savings number or compare with the Claude plan price** until a real-session study backs it (FTC substantiation, see the pilot copy rules).
7. **Disclose credit expiry and refunds clearly** and confirm them with counsel before launch.

## 9. Unknowns that would change the answer

- Our real provider cost per accepted task (needed to set any margin).
- What a buyer's current spend per developer is, and how much of it kell can move.
- Whether the first buyers are individuals (low price, card or UPI) or companies (invoice, GST, procurement).
- Whether foreign customers matter in the first year (changes currency and PPP).

## Sources

Search results of 2026-10-31; third-party and vendor pages unless stated.

- Coding assistants: [Scrimba](https://scrimba.com/articles/best-ai-coding-assistants-2026/), [Lushbinary](https://lushbinary.com/blog/ai-coding-agents-comparison-cursor-windsurf-claude-copilot-kiro-2026/), [Creatr](https://getcreatr.com/ai-coding-assistant-pricing-2026), [Tech Insider](https://tech-insider.org/windsurf-vs-cursor-vs-github-copilot-2026/)
- Gateways: [LLM Gateway](https://llmgateway.io/blog/ai-gateway-fees-compared), [CloudZero](https://www.cloudzero.com/blog/llm-gateways/), [TheRouter](https://therouter.ai/blog/ai-model-router-pricing-cost-comparison-2026/), [PocketLantern](https://pocketlantern.dev/briefs/llm-gateway-litellm-vs-portkey-vs-openrouter-pricing-and-routing-2026), [OpenRouter blog](https://openrouter.ai/blog/insights/openrouter-vs-litellm/)
- Pricing models: [Lago](https://getlago.com/blog/ai-pricing-models), [Growth Unhinged](https://www.growthunhinged.com/p/the-state-of-b2b-monetization-in-2026), [Futurum](https://futurumgroup.com/press-release/are-outcome-based-and-hybrid-ai-pricing-models-rewriting-the-vendor-playbook/), [DEV Community](https://dev.to/korix/ai-pricing-models-per-seat-vs-per-use-vs-outcome-2026-32ep), [Pickaxe](https://pickaxe.co/post/ai-agent-pricing-models)
- Savings-share: [ProsperOps pricing](https://prosperops.com/pricing), [ProsperOps outcome-based pricing](https://www.prosperops.com/blog/prosperops-plus-outcome-based-pricing-for-the-flexera-cloud-finops-suite/), [Usage.ai](https://www.usage.ai/blogs/finops/savings-based-cloud-cost-optimization-pricing/), [nOps](https://toolradar.com/tools/nops/pricing)
- India: [Paddle UPI AutoPay](https://developer.paddle.com/changelog/2026/upi-autopay/), [Razorpay Subscriptions](https://razorpay.com/subscriptions/), [Fungies PPP](https://fungies.io/purchasing-power-parity-saas-pricing-2026), [EximPe](https://eximpe.com/saas-subscriptions-india), [TechCrunch on Basecamp India](https://techcrunch.com/2023/07/18/basecamp-india-special-pricing/amp/)
