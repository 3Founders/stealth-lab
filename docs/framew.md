# What we are building

Written 2026-10-21. A plain account of the framework: what it is, what exists today, what is measured, and what is only
intended. It does not name the company or any prospective customer. Where something is not built or not measured it says so.
Numbers come from `handoff.md` section 11 and the research notes in `docs/research/`; paths are given so they can be checked.

## 1. One paragraph

A **verified procedural memory** for AI agents, with a **routing layer** on top. When an agent solves a problem and a real
check passes (a test, a benchmark, a build), the solution is stored as a reusable *procedure* attached to the *goal* it
achieved, with the evidence that it worked. Later, a similar goal is handled by retrieving that procedure, sending small tasks
to the cheapest model that is likely to pass the check, and accepting an answer only if the check passes. Nothing is deleted:
an update closes the old record's validity window and appends a new one. The long-run intent is that an improvement to one
sub-task, verified somewhere, can benefit every task that contains the same sub-task, and that the records are reproducible
enough to serve as a new kind of research artifact. For now the scope is coding, AI practice, and the agentic literature.

## 2. The primitives

| Primitive | Meaning | Where it lives |
|---|---|---|
| **Goal** | What someone is trying to achieve, at some level of abstraction. Goals form a graph: a goal can specialise a more abstract one | `backend/app/services/goal_abstraction.py`, migration 113 (SPECIALIZES edge); local: `G-` lines with `parent=` in `.stealth/library.md` |
| **Procedure** (a *way*) | An ordered method that achieves a goal. Steps are an action, an instruction, or a *subgoal* | `backend/app/services/procedures.py`; local: `W-` and `S-` lines |
| **Claim** | A statement with a source, a status (current, stale, unverifiable) and evidence. Facts about a repo are claims | `claims.md` with `CLAIM|...|source=path:line#sha`; backend claim tables |
| **Evidence** | A recorded run: who ran what, which check, the verdict. Verified reuse by someone other than the author is the strongest kind | `backend/app/execution/evidence.py`; migration 102 `procedure_usage_events` |
| **Check** | The thing that decides. A project test, a benchmark, a build. Every accepted record points at one | Dispatch verifier (`packaging/npm/lib/exec/verify.mjs`) |

The part-of relation is the important one for the long-run idea: a procedure's step can be a *subgoal*, so a goal can be a
component of many parent procedures. That is the package-manager shape: goal = interface, procedure for the goal =
implementation, parent procedures = dependents. **Status:** the `subgoal` step kind exists in the grammar, but its text is free
text, not a reference to a goal id. The link, and a contract on each bound step (inputs, outputs, preconditions, side effects),
are not built.

## 3. The layers

1. **`.stealth/` per repository (local, no server needed).** A folder of plain files. `claims.md` holds cited facts from a
   survey; `library.md` holds goals, ways and steps plus one entry per problem solved here with its diff in
   `library/solutions/`; `routing.md` holds the repo's own model-outcome counts; `SUMMARY.md` and `index/*` are generated.
   Created by the first prompt in a git repo; grown by a capture hook (a turn that ends with a passing test run and a diff adds
   an entry) and by dispatch (an applied cheap-model answer adds one). Maintained by staleness marking (an entry goes `stale` when
   a file it touched changes; marking runs from `library check`, not automatically), duplicate dropping, and a 64 KB cap that
   archives the oldest. Entries are committed, merged by union, and never silently deleted.
   Code: `packaging/npm/lib/library.mjs`, `capture_hook.mjs`, `library_tidy.mjs`, `survey/*`, `auto_survey.mjs`.
2. **The substrate (server).** The same primitives in Postgres with scope and provenance on every row, a non-compensatory
   applicability cascade (a violated precondition disqualifies; it does not lower a score), bi-temporal invalidate-and-append,
   an append-only audit of changes, and a contribution ledger with credits for verified independent reuse
   (`backend/db/102_contribution_economy.sql`). `.stealth/` was originally a disposable projection of this; the newer local
   library is canonical for a repo's own solved problems.
3. **Routing.** A hierarchical Bayesian model of P(a model passes the check) per goal and model, with an exact ladder over
   candidate models, a bundled public prior, and local refits from the repo's own outcomes
   (`backend/app/routing/`, MCP tools `recommend_models` and `report_model_run`). No customer-side result exists yet.
4. **Dispatch (the part customers feel).** Hooks for Claude Code and Cursor read a prompt, build a brief (task, hinted files,
   size), and if it is a small, clear code task, try cheap open models in isolated worktrees. An answer is applied only if the
   project's own check passes; otherwise the session's own model takes over, with a note of what was tried. `undo` reverses the
   last applied change. Code: `packaging/npm/lib/dispatch.mjs`, `brief.mjs`, `cursor_dispatch.mjs`, `exec/*`.

## 4. What is measured, and what is not

Measured (all on benchmark tasks, none on customer data):
- 153 paired tasks: the always-session-model arm solved 140 at $18.05; the routed arm solved 137 at $10.77, about 40% cheaper.
- Live check on 56 hard tasks: 49 (baseline) vs 50 (current routed) solved, current routed about 16% cheaper.
- MultiPL-E JavaScript: 19 of 20 solved, about 61% cheaper.
- Same-repo past fixes with diffs lifted "right cause" from 27 to 42 of 96 (p = 0.023); steps-only knowledge did nothing
  (`docs/plan_2026-10_priors_library_survey.md`).
- Latency is worse: the routed path takes a median of 42-55 s against 15-37 s.

Not measured: a real Node repository; a real Cursor session (hooks were driven with simulated payloads, `communications.md`);
any customer; procedure transfer across repositories or organisations; the false-accept rate of our checks; whether a shared
sub-task network helps anyone. Dollar savings exist only on metered usage; flat-seat plans may show none.

## 5. Where it is going

- **Targeted repair.** Use the task decomposition to aim each attempt at a target (file, symbol, test) and a part-specific
  check, and record out-of-target edits. Motivated by an observed over-edit in the Cursor test. Plan:
  `docs/plan_2026-10_tech_transfer_targeted_repair.md`.
- **Shared sub-tasks.** Add the part-of reference, then test a new implementation of a sub-goal against every parent that uses
  it, staged (offline replay, canary, active), the way a distribution rebuilds reverse dependencies. Needs abstraction of
  procedures (names, paths, internal APIs to typed slots), a "verified where" tag on evidence, a probation period for
  transferred procedures, and a ruling on who owns contributed procedures.
- **A reproducible record format.** A claim, an intervention, a measurement plan, the evidence and the replications in one
  replayable pack, with an append-only review ledger. The honest limit: replay shows reproducibility, not truth or importance,
  and a weak check gives confident wrong records.
- **Domain adapters.** The core stays domain-free; code repair is the first adapter. Other domains are parked.

## 6. Who it is for, and how it is sold

Engineering teams that use coding agents and pay for model usage. The entry offer is a pre-registered audit on the team's own
tasks and tests, then a short randomised pilot, with any fee tied to measured savings. First question for any customer: how is
coding usage billed (metered or flat seats)? Evidence on go-to-market is in `docs/research/devtools-gtm-prior-art.md`.
Routers are being commoditised and platforms ship their own model selection, so the durable asset is the per-repo library of
verified fixes and the check-gated acceptance, not the routing alone.

## 7. What we may and may not claim

May: "On 153 benchmark tasks, check-gated routing solved 137 against 140 at about 40% lower cost; on a hard live subset it was
about 16% cheaper; latency is higher." May: that nothing leaves the machine except the model name, a pass/fail verdict, and
a hashed repo identity (per `library.mjs` and `capture_hook.mjs` privacy notes).
May not: any savings figure for a customer, "60-80%", a network effect, cross-organisation transfer, or "10x".

## 8. Open decisions

1. Who owns contributed procedures, and on what licence (blocks anything involving a second organisation).
2. How the lead is billed for coding usage.
3. Whether to build the part-of reference now or after the audit and pilot.
4. Whether to wire automatic staleness checks and near-duplicate goal merging, which are manual today.
