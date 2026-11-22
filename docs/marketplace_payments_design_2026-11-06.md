# Paying tool creators through kell: design (2026-11-06, draft for decision)

Founder direction: payments for tools made by creators pass through kell, and kell pays the creators who add tools to the platform. This note
says what exists, what is missing, a build order in which no real money moves until the numbers are proven, and the decisions only the founders can
make. Nothing here is built yet. Nothing in it is legal advice; the money-handling questions below need a lawyer and Razorpay's written answer.

## What exists today (and is reused)

- **A person's balance.** `org_wallet_credits` (money in, append-only) and `provider_call_ledger` (spend, held then settled). The balance is credits minus spend, so it cannot drift. Shown to people as kell credits.
- **Every outside tool call is already recorded.** `tool_gateway_calls` (server, tool, person, outcome, time) is written by the gateway (`backend/app/tool_routing/gateway.py`, `use_tool` in `server.py`).
- **The gateway runs hosted (remote) MCP servers only**, verified and filtered (`gateway_allowed`, `signin_allowed`), with a per-user and per-server rate limit and a block on tools that act for the user.
- **Razorpay is wired for receiving money** (orders, webhook, plans, packs, free-credit codes). It is not wired for paying anyone.

## What is missing

1. **A creator and a listing.** Who owns a server, proof that they do (a DNS or file challenge on the server's domain), the price they set, the tools it exposes, and a review before it is live.
2. **A price on a call.** Today a tool call is free to the person. A paid tool needs a price per call (or per result) shown before it is used, and a hold on the person's balance like a model call has.
3. **An earnings ledger.** One append-only row per settled paid call: creator, tool, amount, the platform's share, the person's share of GST. Same pattern as `org_wallet_credits`; nothing edited, a refund is a negative row.
4. **Payouts.** Moving money to a creator's bank account.
5. **Controls.** A per-tool spending limit for the person, confirmation before a tool that acts for them, refunds, disputes, and a way to take a listing down and stop paying out on it.
6. **Tax and terms.** GST on the platform fee and on the creator's supply, TDS where it applies to marketplace payouts, creator terms, invoices.

## The money path (recommended)

- A person buys kell credits as now. Using a paid tool debits those credits at the tool's price.
- kell does not hold creator money in its own account to hand out later. The recommended rail is **Razorpay Route** (linked accounts): the creator completes KYC with Razorpay, and kell instructs transfers from settled funds to their linked account. This keeps kell out of operating its own payment system. Whether Route fits this exact flow (credits bought first, split later, payouts on a schedule) and how refunds and holds work must be confirmed with Razorpay in writing before anything is built on it.
- A holding period before earnings become payable (for refunds and chargebacks) and a minimum payout amount keep the number of tiny transfers down.

## Build order (no real money moves until step 4)

1. **Listings, free.** Creators register a server, prove ownership, and set a (not yet charged) price; review pipeline; the tool shows as "by <creator>". Extends the router and gateway.
2. **Metering.** Record what each tool call would have earned, in the earnings ledger, without charging anyone. Creators see their numbers on a dashboard. This proves the metering and the split on real use.
3. **Charging.** Turn on the debit from the person's kell credits for paid tools, with the price shown first, spending limits and confirmation for acting tools. Earnings accrue but are not paid out.
4. **Payouts.** Razorpay Route linked accounts, KYC, hold period, minimum, monthly transfer, invoices and tax lines. Only after a lawyer and Razorpay have signed off the flow.

Each step ships with its proving tests and a number to pass (per the repo's rule that exit criteria are numbers).

## Decisions only the founders can make

1. **The split.** What share does kell keep? A flat percentage, or a fee per call?
2. **Pricing shape.** Per call, per result, or a monthly subscription to a tool? Can a creator choose, or is there one model?
3. **Who may list.** Anyone with a verified domain, or invited creators first?
4. **Payout rules.** Hold period, minimum payout, payout schedule, and who bears GST and TDS.
5. **Refunds.** Who funds a refund of a bad call: the creator's future earnings, or kell?
6. **Legal.** The RBI rules on payment aggregators and on holding funds for a third party, and GST and TDS treatment of marketplace payouts: an Indian payments lawyer's view before step 4.
7. **Safety bar for paid tools.** A paid tool sees the person's request: which data classes may it receive, and does a paid tool get a stricter review than a free one?

## What I can start now, without any of those answers

Step 1 (free listings with domain-ownership proof) and step 2 (the earnings ledger and metering, no charging), because they move no money and the
design choices above change numbers, not structure. Say go and I will start with the migration and the metering tests.
