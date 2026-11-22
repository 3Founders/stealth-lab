# Paying tool creators: what others do, and what to change in our design (research, 2026-11-06)

Companion to `docs/marketplace_payments_design_2026-11-06.md`. Method: 13 Exa searches from different angles (marketplace economics, agent payment protocols, metering, Indian payout rails and tax, fraud, security, ownership proof, spend governance, MCP pricing proposals) plus 6 OpenAlex searches (platform economics, agent micropayments, MCP security, tool marketplaces, creator pricing, tool retrieval), with abstracts read for the closest papers. Vendor pages are marketing and are marked as such. Nothing here is legal or tax advice.

## 1. How the marketplaces pay creators (the market today)

| Platform | Buyer pays | Creator gets | Payout rules | Source |
|---|---|---|---|---|
| Apify Store | pay-per-event (or usage); events set by the creator | 80% of revenue minus the platform usage it caused | invoice each month; minimum $20 (PayPal) or $100 (other); below minimum rolls over, forfeited after 12 months; KYC required before any payout; price changes at most once a month and effective after 14 days; free-plan users do not count toward profit, "promotional users" are paid as if at the top tier | docs.apify.com (official terms) |
| MCPize | subscriptions (Stripe) or per-call USDC (x402) | 80% (85% for early servers) | monthly Stripe Connect payout, $25 minimum (its two pages disagree: $25 and $100) | vendor pages |
| AgentStack | one-time, subscription, usage-based via a gateway that only bills successful calls | 70% | monthly via Stripe; each version security-reviewed | vendor docs |
| BusinessMCP Hub | prepaid balance, price per call $0.001 to $10 set by the creator | 80% | earnings payable 14 days after the call, paid on the 15th monthly at $25 minimum; a call that errors, times out or fails an output scan is refunded and earns nothing; earnings deducted if the funding payment is refunded or lost in a dispute | vendor docs |
| Composio Instant | one prepaid wallet; $2 starting balance; per-call at the provider's price, 0% markup | provider is paid by Composio | failed calls are free; the person's own connected account is used first; **no hard spend cap** (balance checks are asynchronous) | vendor docs |
| Smithery, Glama, mcp.so | n/a | $0 (directories; Smithery charges creators ~$30 a month) | n/a | third-party comparison blog |

Pattern: 70 to 80% to the creator, monthly payouts, a minimum amount, a hold or rollover, KYC before money moves, and failed calls not charged. Per-call pricing is favoured for agent traffic; prepaid balance is how several tools sit behind one wallet.

## 2. Agent payment protocols (an optional second rail, not the first)

- **x402** (Coinbase, now a foundation): a server answers HTTP 402 with the price, the client signs a stablecoin authorization and retries. Default flow is verify, run, then settle, so funds move only after success. There is an `upto` scheme (authorize a maximum, charge actual usage), a `batch-settlement` scheme for micropayments, and an escrow flow for refundable payments. Every payment authorization is single-use. Apify and MCPize already accept it.
- **Weaknesses are documented.** "Five Attacks on x402" (arXiv 2605.11781) shows five practical attacks causing unpaid service or paid-but-denied outcomes across three SDKs; the SoK on agent payments (arXiv 2604.03733) names weak intent binding, misuse under valid authorization, and payment-service decoupling as open problems.
- **AP2 and ACP** (Google, OpenAI/Stripe) are consent artifacts: AP2's mandates carry an allowed payee, an amount range, a total budget, an expiry and a rule against reusing one mandate for several checkouts; ACP scopes a token to one checkout. Both are silent on runtime spend monitoring. The lesson for us is to keep our own caps and ledger regardless of which protocol a buyer uses.
- **MCP itself has no price field.** The paid-tools proposal (modelcontextprotocol issue 3393) was closed "not planned" on 2026-09-28 as a process matter; SEP-2007 (payment support, error `-32402`) is an open pull request; registry discussion 1300 proposes fiscal-safety metadata (`spend_cap`, `charge_evidence`, `revocation_path`). Practical conventions: price under your own `_meta` key, price repeated in the description, a free `quote_*` tool for variable prices, a `max_price` argument. A price shown at list time is a claim by the party being paid: "bait pricing" and a compromised tool choosing its own payee are real failure modes, so enforce the cap at payment time.

## 3. Metering design (what billing engineers converge on)

From Stripe's engineering write-up, Lago, OpenMeter and independent write-ups:

1. **Raw events are the system of record.** Append-only; totals are derived and rebuildable; a number that cannot be recomputed from events is a number someone typed.
2. **Idempotency key minted at the action, not at send time.** A key minted inside a retry loop is new on every retry and defeats deduplication. Use the call id. At-least-once delivery plus deduplication, not "exactly-once" delivery.
3. **Bill on event time.** Choose and write down a late-event policy (fold in, reject after a horizon, or attribute to now); keep an acceptance window before closing a period.
4. **Money is corrected by appending an adjustment**, never by editing a finalized row.
5. **Reconcile** the event ledger against totals on a schedule.

We already have the right bones: `org_wallet_credits` (append-only), `provider_call_ledger` (reserve then settle), and `tool_gateway_calls` (an audit row per call). The earnings ledger should follow the same pattern.

## 4. Payout rails in India (the weakest part of the plan)

- **Razorpay Route** pays third parties through linked accounts (bank account or UPI; KYC form; a nominal "penny test" transfer before transfers are allowed; 24-hour cooling period; settlements take 2 working days; per-transfer settlement can be held or scheduled).
- **Route works on captured payments.** Transfers come from captured payments or (if Razorpay enables it) direct transfers from balance; the transfer total cannot exceed the captured amount after fees and tax; **no transfer once a refund has been initiated**; transfers can be reversed. This is a payment-first, split-later model; whether "credits bought now, earnings paid next month" fits needs Razorpay's written confirmation.
- **Eligibility.** Under the RBI payment aggregator guidelines of September 2025, Razorpay states that Route requires the business to have domestic turnover above ₹40 lakh (or export turnover above ₹5 lakh) in the current or preceding financial year, and a written "payer-payee transparency" declaration that the third party directly serves the customers. Accounts that did not file by 2025-12-31 had Route disabled. **A pre-revenue company may not qualify.** This needs a direct answer from Razorpay before any build depends on it.
- **Stripe's model, for comparison** (and what MCPize and BusinessMCP use): separate charges and transfers when you must hold funds or the payee is unknown at charge time. The platform is merchant of record and is liable for refunds, chargebacks and negative balances; refunding a charge does not touch earlier transfers, so the platform reconciles by reversing transfers or netting later payouts; a reversal only works if the connected account has balance or a reserve. Whether Stripe Connect is open to an Indian platform was not established here and should not be assumed.

## 5. Indian tax duties if kell collects the money and pays creators

(From CBIC's e-commerce FAQ, section 52 of the CGST Act, Khaitan & Co's note on the CBDT circular, and a CA's 2026 guide. Confirm with a CA.)

- **GST TCS (section 52):** an e-commerce operator that collects the consideration collects tax at source on the net value of taxable supplies made through it by other suppliers: 0.5% since 10 July 2024 (0.25% CGST plus 0.25% SGST, or 0.5% IGST), reported on GSTR-8 by the 10th of the next month, remitted within 10 days of month end, net of returns.
- **TDS 194-O:** 1% on the gross amount (5% if no PAN); the CBDT treats commissions and fees the platform charges as part of that gross amount and says they are not subject to another TDS; the ₹5 lakh yearly threshold applies to individual or HUF sellers who give a PAN or Aadhaar.
- **GST on kell's own commission** is a separate supply with its own invoice.
- The practical consequence: creator onboarding must collect PAN and GSTIN (if registered), and monthly statements must show gross, commission, GST, TCS and TDS.

## 6. Fraud: creators paying themselves

The documented controls across payouts platforms (Posty, PlayHouse, Edgaze, affiliate-fraud guides, a marketplace-fraud taxonomy):

- **Time is the strongest control.** Hold earnings before they are payable (examples: 7 days for base payouts and 30 for bonuses; 14 days at BusinessMCP), hold a first payout longer, and restart the hold after a chargeback. Most fraud shows itself in the window; paying instantly removes the best detection chance.
- **Clawback as a ledger entry:** deduct from an unreleased payout or record a negative adjustment netted from the next one.
- **No self-dealing:** nobody can buy their own listing; link buyer and creator accounts by device, identity, payment method and payout destination; watch for a creator's volume coming from a few new linked accounts; a new creator paired with new buyers is the risky combination.
- **Payout details change = hold and re-verify; payout only to an account in the verified creator's own name.**
- **Free or promotional credit:** Apify pays creators at the top tier on promotional usage and absorbs the cost itself. We must decide whether trial credits (the free codes) can be spent on paid tools, and who funds the creator's share if they can.

## 7. Security of a marketplace of third-party tools

- OWASP's MCP cheat sheet lists tool poisoning (instructions hidden in descriptions, schemas or results), rug pulls (definitions changed after approval), tool shadowing, confused deputy, data exfiltration, over-scoped tokens and supply-chain attacks. Mitigations: **hash the whole tool definition including `inputSchema`, `outputSchema` and `annotations`** and require review when it changes (a flipped `readOnlyHint` changes approval behaviour with no visible text change; the hash does not detect behaviour changes behind an unchanged definition); strict schemas with no extra properties; never auto-approve financial operations; show full parameters; allowlists; log every call.
- Evidence that review matters: a TSE 2026 study uploaded malicious servers to three MCP aggregation platforms successfully ("current audit mechanisms are insufficient"); a registry study of 67,057 servers found 833 vulnerable and 18 with suspicious descriptions; a study of 7,508 MCP repositories documents widespread tool cloning, which matters for attribution and for who is paid.
- **Ownership proof exists and is cheap.** The official MCP Registry proves namespace control by GitHub, by a DNS TXT record on the domain apex (`v=MCPv1; k=ed25519; p=<key>`), or by an HTTPS file at `/.well-known/mcp-registry-auth`. It proves control of a namespace, not that the code is safe. Our gateway already treats a GitHub-namespace server as weaker (the stars test); paid listings should require the domain proofs.

## 8. What the academic papers add (and do not)

- *Revenue-Sharing as Infrastructure* (arXiv 2603.20533, 2026): proposes platforms take a share of developer revenue instead of charging for access; conceptual, no measurements, little for our design.
- *402Pilot* (arXiv 2608.01341, 2026): a buyer-side policy for choosing among payable providers under a limited wallet (contextual Thompson sampling; spends 39 to 43% of the wallet at competitive quality in a replay benchmark). Relevant to the router: once tools have prices, ranking should be price-aware under the person's budget.
- *Content Creators Between Platform Control and User Autonomy* (BISE 2023): revenue-sharing platforms create tension when the platform's ranking controls creators' income. **Implication: if the router ranks tools, creators will care how; do not let price or revenue silently change ranking, and say what ranking uses.**
- *Pro-Rata versus User-Centric* revenue allocation (Management Science 2020): about splitting a subscription pool; not needed while we price per call.
- OpenAlex had no abstract for some hits (for example the 2026 platform-commission papers), so I did not draw on them.

## 9. What this changes in our design

1. **Keep prepaid kell credits as the only buyer-facing money** at first (BusinessMCP and Composio do this). Treat x402 as a later optional rail for external agents with no account.
2. **Earnings ledger:** append-only, one row per settled paid call, idempotency key = the gateway's call id minted when the call is made; reserve then settle; failed, timed-out and output-scan-failed calls earn nothing and are not charged; adjustments and clawbacks are negative rows. Define the late-event and dispute window in writing.
3. **Price shown before first use**, in tool metadata under our own `_meta` key and in the description; variable prices get a `quote_*` tool and a `max_price` argument; the cap is enforced when charging; price changes at most monthly with 14 days' notice (Apify's rule).
4. **Payout rules (to decide):** monthly, a minimum, a 14-day hold, a longer first hold, hold on payout-detail change, KYC before any money moves, an abandonment rule only with legal sign-off.
5. **Fraud rules:** block earning from your own usage and from linked accounts; cap earnings from brand-new accounts and from trial credit; decide who funds creator earnings on trial usage.
6. **Security rules:** domain proof (DNS or HTTP) for paid listings; hash-pin the full tool definition and re-review on change; review per version; strict schemas; confirmation for tools that act for the person.
7. **Rail:** before building, get Razorpay's written answer on Route eligibility and the credits-first flow; if Route is unavailable, plan manual bank payouts against creator invoices with TCS, TDS and GST handled by a CA; verify whether any Stripe-based option is open to an Indian platform.
8. **Ranking neutrality:** keep ranking independent of price and revenue, or disclose how it is used.

## 10. Confidence

Strong: Apify's published terms, Razorpay Route documentation, Stripe Connect documentation, the OWASP guidance, the registry proof methods, the metering practices (several independent sources agree). Medium: vendor-reported numbers (MCPize, AgentStack, BusinessMCP, Composio: marketing pages, one of which contradicts itself on the minimum payout); the third-party comparison table of marketplaces. Weak or missing: whether Route fits our credits-first flow and whether kell qualifies; whether Stripe Connect is available to us; any outcome data on creator fraud in tool marketplaces specifically (the evidence is from adjacent marketplaces); the legal characterisation of kell under RBI payment aggregator rules and as an e-commerce operator.
