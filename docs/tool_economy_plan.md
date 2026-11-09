# Tool economy: anyone submits a tool, kell verifies it, creators earn per use (2026-11-09)

A plan, not built. Agreed with the owner on 2026-11-08: any user can be a tool creator; priced tools are paid from the same plan the
user already has; the defaults below (a 10% pool, free tools first and priced tools later) were accepted "for now". Every number marked
"default" is a starting value to revisit with real usage. Cash payouts need the CA's and lawyer's view first (section 7).

## 1. What a creator submits

| Part | What it is | Why |
|---|---|---|
| Tool card | Name, what it does, how to reach it (an MCP server URL or a registry package), the sign-in it needs, price (0 for free) | The router (`backend/app/tool_routing/retrieve.py`) ranks cards; a submission is one more card |
| Goal | The job the tool is for, in the creator's words, matched to a goal in kell's library (or a new goal when none fits) | Tools are ranked per goal; the goal decides which benchmarks apply |
| Evidence | The tests or benchmarks the creator ran: a standard benchmark with its result, or their own cases (input, expected output or a check) | The claim the verifier reruns |
| Ownership proof | A token placed where only the owner can put it: a DNS TXT record, a file in the repo, or a field in the package's metadata | Stops someone claiming royalties on a tool they did not make |

When the creator types the goal, kell suggests benchmarks from its own benchmark library for that goal (section 3). The creator can
accept them, add their own, or both.

## 2. Verification

1. **Rerun.** kell runs the creator's cases and the suggested benchmarks itself, through the gateway (`use_tool`), never on the
   creator's word. The tool must answer from kell's side the same way users will reach it.
2. **Deterministic checks first.** Where a case has an exact answer or a script that checks it (status codes, schemas, test suites),
   that decides it. No model is asked.
3. **LLM judge for the rest.** Open-ended outputs are judged blind and pairwise against the current best tool for the goal: the judge
   sees two answers labelled A and B, not which is the submission. The judge is from a different model family than any model the
   creator could have tuned for, and order is swapped on a second run to cancel position bias.
4. **Hidden half.** Each goal's benchmark set has a public half (shown to creators) and a hidden half (never shown). A tool that does
   well on the public half and badly on the hidden half is flagged as overfitted, not ranked.
5. **Result.** Pass rate, cost and latency per goal are stored with the card. A verified tool gets a ranking prior for that goal
   (the same way `_proven` lifts tools with good outcomes today); real use then moves it up or down through `/tools/outcome`.

Re-verification: on a new tool version, on a drop in real-use success, and on a sample of random dates.

## 3. Benchmark library (does not exist yet)

Per goal: a set of cases, each with an input, a check (exact, script or judge rubric), and whether it is public or hidden. Seeded from
standard benchmarks where they exist and from creators' own cases once verified (a creator's good cases can join the library with
credit). The goal library is narrow today (about 20,600 issue-specific goals, no parent goals), so tool goals need their own general
layer: "error monitoring", "send email", "search the web", and so on, starting from the triage's tool goals.

## 4. Who pays, and how much (defaults)

**Free tools: the pool.**
- 10% of plan revenue each month goes to a creators' pool (default).
- The pool is split by **distinct paying users** who got a verified use of each tool that month, not by raw call count, so one
  user looping a tool cannot drain it.
- A "verified use" is a `use_tool` call that returned a result and was not reported as failed (`report_tools`).

**Priced tools: the same plan wallet.**
- The creator sets a price per call. kell takes 15% (default); the creator gets 85%.
- Paid from the user's existing plan credits; there is no second subscription.
- The price is shown before the call. Calls under about $0.005 run without asking (default); above that the user confirms once per
  tool. A per-call cap of about $0.02 to start (default), raised once there is fraud data.
- A free tool and a priced tool for the same goal are ranked on quality first; price is shown, not hidden in the ranking.

**Abuse rules.** No earnings from the creator's own account or accounts linked to it (same payment method, same device, same org).
Sudden spikes from few users are held for review. Earnings are held for 30 days (default) before they count, so refunds and fraud can
be reversed.

## 5. What a creator sees

A page per tool: verification result per goal, rank, uses this month (distinct users and calls), earnings pending and cleared, and
the cases that failed (public half only). A submission that fails gets the failed public cases and can resubmit.

## 6. Phases

1. **Submission and verification.** The submit form (card, goal, evidence, ownership proof), the rerun through the gateway, the
   deterministic checks and the judge, the ranking prior. No money. This alone grows the catalog with tested tools.
2. **Free tools paid from the pool, in Credits.** Monthly pool split by distinct paying users; earnings arrive as kell Credits
   (usable on the creator's own plan). No cash leaves, so no payout compliance yet.
3. **Priced tools.** Creator prices, the 15% take, the price shown before the call, the caps, the confirm step.
4. **Cash payouts.** Razorpay Route (or equivalent) to verified creators, with KYC, TDS / tax handling and invoices, only after the
   CA's view.

## 7. Open questions before money moves

- Tax and legal: are creator earnings income we must deduct tax on (TDS), and does paying non-Indian creators change that? Is a pool
  share a payment for a service? For the CA and lawyer, with the referral briefing in `unit_economics_2026-10.md`.
- Pool size: 10% is a default; check it against `unit_economics_2026-10.md` margins once real tool-use counts exist.
- Judge cost per verification, and who pays it (kell, or a small submission fee refunded on pass).
- What happens to earnings when a tool is removed for failing re-verification.
- Whether third-party registry tools (not submitted by their makers) can ever earn, and for whom (a claim flow with ownership proof).
