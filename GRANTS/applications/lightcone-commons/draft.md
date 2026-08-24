# Lightcone Commons — Round 1 Draft (plain-language version)

- **Form:** lightconecommons.com/apply · chaitanyad3shkar@gmail.com
- **Deadline:** Aug 23, 11:59pm AoE (= ~Aug 25, 5:30pm IST) — form stays editable after submitting

---

## What are you working on?

> AI coding agents have no real memory. Every session starts from scratch. When an agent finally works out how to do something correctly — after an hour of trial and error — that discovery dies when the session ends. Next time, it makes the same mistakes again, and someone pays for the tokens and the broken code.
>
> Some tools now save what agents learn. Almost none of it is checked. A trick that worked once gets saved as "knowledge," spreads quietly, and keeps failing in places where it never applied. Nobody keeps track of where a piece of agent knowledge came from, whether it still works, or when it stopped working.
>
> We're building memory for AI agents that keeps receipts:
>
> - **Every remembered lesson carries its sources.** Any claim can answer "why do we believe this?" — down to the exact runs that support or contradict it.
> - **Lessons get tested before they're trusted.** A procedure is only promoted after it succeeds on real tasks, repeatedly — not because a model sounded confident.
> - **Knowledge ages honestly.** When the world changes, old lessons get marked stale and re-checked — never silently overwritten, never quietly kept past their expiry.
> - **Your history stays yours.** Raw agent traces contain secrets and private code. Ours is designed to run locally first, with credentials redacted, and nothing sensitive leaves the machine by default.
>
> We've measured this on standard software-repair benchmarks: with our memory, an agent uses roughly three-quarters fewer steps to finish tasks, at the same success rate. It's integrated into tau-bench's banking domain as a live test bed, and we're running a public head-to-head this fall: can a small open model with honest memory beat a frontier model that has none?
>
> Long-term, we think this matters beyond coding assistants. Agents are starting to write and consume operational knowledge — runbooks, procedures, fixes. If nobody tracks where that knowledge came from or whether it still holds, we get a world where machines confidently repeat each other's mistakes. If someone does track it, machine-written knowledge can be checked the way science checks claims: against evidence, with sources attached. That's the part worth building carefully, early, in public.

## What would you do with funding?

> Right now this is two students (IIT Bombay and IISc) building between classes, funded out of pocket. Parallel applications are pending (Emergent Ventures India, YC); we'll report outcomes either way.
>
> Money converts directly into public artifacts:
>
> - **$5K** — compute for the public head-to-head run + releasing the core memory system as open source.
> - **$12K** *(what we actually need)* — all of the above, plus tamper-evident signing so anyone can verify a piece of remembered knowledge hasn't been altered since it was earned ($3K), a published set of paired before/after benchmark results anyone can re-run ($3K), and trips to meet the first five teams who'd actually use this ($2K).
> - **$25K+** — extends the same machinery to a second domain, and buys months of full-time work after graduation at student burn rates.
>
> We deliberately picked deliverables that survive even if the company doesn't: open-source code, reproducible benchmarks, signed records.

## Who is involved?

> **Chaitanya Deshkar** — final-year AI/ML student at IIT Bombay. Built the pipeline end to end: turning raw agent sessions into reusable lessons, the test harnesses that score them, and the integration into tau-bench. CV: [ADD] · GitHub: [ADD]
>
> **Anuj Bhadbhade** — IISc Bangalore. Systems and evaluation; designed how the memory decides what's relevant right now instead of dumping everything into the model. CV: [ADD] · GitHub: [ADD]

## Anything else evaluators should know?

> - **How urgent?** The head-to-head run happens Sept–Oct with or without funding. Funding decides whether the verification and publishing layers ship alongside it or months later.
> - **Restrictions on funders?** None.
> - **One honest limitation:** on our hardest paired tasks, success rates are equal, not better — the win today is cost, not correctness. Fixing correctness is the learning loop we're building next. We measure this openly because inflated baselines are exactly the problem this project exists to fight.
> - **Why apply here specifically?** Our design doc's first principles read like your community's values: provenance over opaque confidence scores, evidence over assertions, replayable decisions, privacy by default. We're not pivoting to say that — it's how the system was specced before we ever wrote an application.

---

## Human-only checklist
- ☐ Paste into live form (restructure freely)
- ☐ Add CV/GitHub links once in MASTER_PROFILE
- ☐ Submit rough tonight — editable after
