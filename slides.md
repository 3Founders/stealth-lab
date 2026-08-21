Slide 1 — Title

Teaching AI Agents to Remember What Works
Chaitanya Deshkar — Final Year, AI/ML
Centre for Multidisciplinary Education, IIT Bombay
IITB–Groww INV.ENT

Slide 2 — The Problem, in Plain Terms

AI agents (think: an AI that writes code, or handles a customer support ticket) have a strange flaw — they forget everything the moment a task ends.
Ask it the same type of question tomorrow, and it starts from zero. Same mistakes, same wasted time, same wasted cost — every single time.
The current fix everyone uses is "just give it more information upfront" — dump the whole manual, the whole history, into its instructions every time.
That fix doesn't actually work — and we can prove it, not just claim it.

Slide 3 — The Experiment That Started This

We tested a simple customer-refund rule: money must go back to the original payment method, not wherever the customer asks.
We gave a large, expensive AI the entire rulebook, in full, every time. It got the rule wrong 100% of the time.
We gave a much smaller, cheaper AI the same rule, but organized as structured knowledge instead of a wall of text. It got it right half the time — using less than half the resources.
The mistake wasn't confusion. The AI knew the rule was in front of it — it still gave the customer what they asked for instead of what the rule said. Having information available isn't the same as actually applying it.
This is a small first test, not a final result — but it's the finding that convinced us this problem is real and solvable.

Slide 4 — What We've Actually Built

This isn't a slide-deck idea. It's a real, working system — roughly the scale of a small production product.
~22,500 lines of working code, with over 500 automated checks that verify it behaves correctly — comparable to what a funded startup would have at Series A, not a college project.
What's already running: a structured memory graph that stores knowledge with evidence and history · a search system that finds relevant knowledge quickly · a planning engine that breaks big tasks into steps and adjusts when something goes wrong · a review pipeline so untrusted AI-generated work is checked before being trusted.
What's not built yet — and what this funding is for: the part that turns raw AI activity into that trustworthy, reusable knowledge in the first place.

Slide 5 — Proof It Actually Saves Money

We ran a real, independent test — the same coding tasks, given to the AI twice: once with no memory, once with our system.
	Without our system	With our system
Resources used (tokens/cost)	Baseline	74% less
Number of actions taken	Baseline	34% fewer
That's the honest headline: it makes AI dramatically cheaper to run on repeat work.
The honest catch: it didn't yet make the AI more accurate at solving the task — that's still a work in progress, and we're saying so upfront rather than hiding it.
What we did find: the AI makes the same kind of mistake repeatedly across different projects — meaning if we can teach it to fix that one pattern, the fix should transfer, not be a one-off.

Slide 6 — Why You Can Trust These Numbers

A lot of AI demos are quietly rigged — built and tested by the same people, on examples chosen to look good.
We deliberately avoided that: our results come from independent, third-party test sets that we didn't write and can't cheat on.
We checked our own system for the ways it could accidentally "cheat" — like memorizing the answer instead of learning the pattern — and measured that risk directly rather than assuming it away. It came back low.
We report results honestly even when they're not flattering — including the accuracy result on the previous slide, which most teams would have quietly left out.

Slide 7 — Where We're Honest About What's Missing

What's proven: major cost savings on real tasks, a real repeating error pattern we can target, and a testing process we trust.
What's not proven yet:
The system remembers things, but doesn't yet update itself when a stored solution turns out to be wrong or outdated.
It doesn't yet track which remembered solution led to which result — so it can't yet reward good memories and retire bad ones.
This is exactly the gap between "interesting research prototype" and "trustworthy production system" — and it's exactly what the next phase of work closes.

Slide 8 — What This Funding Actually Builds

Stage	What it delivers	How we'll know it worked
1. Close the loop	The system starts learning from real outcomes, not just storing them	A stored solution's "track record" visibly improves or worsens based on real results
2. Prove reuse works	Test whether a learned fix actually transfers to new, unseen problems	Measured improvement on problems the system has never seen before
3. A second domain	Prove this isn't just a coding-specific trick	Same approach tested on a completely different task type (e.g. banking/customer service)
4. Full system + transfer	Test whether a lesson learned in one domain helps in a totally different one	Does knowledge from coding tasks measurably help with, say, support tickets?
Every stage has a clear pass/fail test attached — including the possibility of a "no" at any stage. That's intentional: we'd rather find out early than oversell.
Who benefits first: any team building AI agents on repeat work — coding assistants today, customer support and operations tools next.

Slide 9 — Where This Goes If It Works

(This is our vision, not a promise — the rest of this deck earns the right to say it by being honest about what isn't proven yet.)
Near term: any team running AI coding agents on real repositories — today's most expensive, highest-frequency AI use case — gets agents that get cheaper and more reliable the more they're used, instead of staying flat.
Next: the same substrate extends beyond coding — to customer support, operations, and any workflow where an AI agent repeats similar tasks and could learn from what worked before.
The bigger bet: today, every AI agent product is quietly rebuilding its own memory from scratch. We think "durable, verifiable agent memory" becomes infrastructure — the layer other agent products build on, not a feature any one of them owns.
We're not claiming this today. We're saying: if the milestones on the previous slide hold up, this is the direction the evidence points.

Slide 10 — Value Proposition

For developers and teams using AI coding agents: stop paying, in time and money, for your AI re-learning the same lessons every single day.
What we give them, concretely:
Lower cost — 74% fewer tokens spent on repeat-pattern work, measured on real coding tasks, not a demo.
Less repeated failure — the agent stops retrying approaches that have already failed.
Trust, not just speed — every reused solution carries its track record with it, so teams can see why the agent is doing something, not just that it did it.
One line: We don't make the AI smarter. We make it stop forgetting what already worked.

Slide 11 — Market Size

The AI agent market overall: valued at roughly $8–12 billion in 2026, projected by multiple independent research firms to reach $50–180 billion by the early 2030s — one of the fastest-growing markets in tech right now.
The specific layer we're building — agent orchestration and memory systems: sized at roughly $6 billion in 2025, projected to reach $28–69 billion by the early 2030s, depending on the research firm.
The narrowest, most specific category — AI agent memory infrastructure itself: valued at just $1.2 billion in 2025, but projected to grow 62% per year, reaching ~$19 billion by 2034 — the fastest-growing slice of the market, because it's the newest and least solved.
Why that matters for us: we're not entering a mature, crowded market late — we're building in the youngest, fastest-growing layer of it, before it has a clear winner.
(These are third-party market research estimates, not our own projections — sourced and available on request if asked.)

Slide 12 — Thank You

Our belief: AI agents don't need to be smarter — they need a way to remember what actually works, and forget what doesn't.
Every number in this deck is backed by a real file we can show you, not a projection.
Chaitanya Deshkar — [email / contact]
Questions & discussion