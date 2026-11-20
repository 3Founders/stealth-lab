# kell as a coding agent: rating, gaps, and the frontier

**Date:** 2026-11-20
**Scope:** `desktop/` (the agent the person actually uses), the engine it drives, and the parts of `backend/` that
carry its memory, routing and tools.
**Method:** read the agent path in full (`lib/tools.ts`, `kellplugin.ts`, `pooled.ts`, `delegate.ts`, `plan.ts`,
`mcp.ts`, `engine.ts`, `localdb.ts`, `remote.ts`, `procedures-local.ts`, `app/api/prompt/route.ts`), grepped
`app/page.tsx` for every control, read the evaluation docs in `experiments/` and `docs/abtest_followups_2026-10.md`,
then swept the literature and industry record with Exa (22 queries), OpenAlex (12) and arXiv (6 grouped queries).
No benchmark was run for this review; nothing was rebuilt. Where a number is quoted, its source is named.

---

## 1. The rating

kell is not one thing, so one score hides the useful signal. Three scores:

| Lens | Score | One line |
|---|---|---|
| **Capability** — can it do the work unattended? | **2.5 / 5** | Mid-pack. It can edit, run and search; it does not plan, verify or bound its own authority. |
| **Cost-adjusted capability** — quality per dollar | **4 / 5** | Genuinely ahead. Cheap-first, suggest-don't-escalate, and measured 29–45% savings. |
| **Trust** — can you leave it alone? | **2 / 5** | No verification gate, no sandbox, no egress policy, hard-delete history. |
| **Overall** | **3.0 / 5** | A differentiated product with a real thesis, not yet a frontier coding agent. |

The shape of that verdict: kell's **memory and routing** are ahead of the field; its **verification, planning,
security and durability** are behind it. The frontier moved in 2026 from "can the model write the patch" to "can the
harness prove the patch is right, cheaply, without being fooled" — and that is where kell is thinnest.

### Dimension table

| # | Dimension | Score | Evidence in this repo | Frontier reference |
|---|---|---|---|---|
| 1 | Cost routing | 4.0 | `MODEL_LADDER`, `chooseModel` (suggest only), gateway fallback header | RouteLLM (35–85% cuts at 95% quality) |
| 2 | Procedural memory | 4.0 | Goal → Way → Tool, applicability cascade, `.kell` library, survey | Memp, REM, Agentic Memory survey |
| 3 | Agent loop / planning | 2.5 | one ReAct turn; no plan mode, no owned todo artifact, no stuck detection | Claude Code plan mode + TodoWrite; Codex loop |
| 4 | Verification | 2.0 | prompts say "never claim a check you did not run"; nothing enforces it | Verification Horizon, SWE-Bench Pro audit, harness ablation |
| 5 | Context engineering | 3.0 | 95% compaction, summaries, attachments by reference; no repo map, no tool search, no budget meter | Aider repo map, Agent Skills, MCP tool search, context rot |
| 6 | Tool use / MCP | 3.5 | app-proxied MCP, fresh sign-in per call, discover/use_tool, connectors | MCP tool search; capability scoping |
| 7 | Safety / security | 2.0 | full shell in the folder, unrestricted egress, no sandbox, no trust labels on fetched text | CapScope, lethal trifecta, sandbox+egress guidance |
| 8 | Durability / state | 2.0 | `chats.db` + engine store + engine v2 log; hard delete; no trash, no snapshot | SQLite WAL, event-sourced state (OpenHands) |
| 9 | Observability / eval | 2.5 | hooks capture outcomes; A/B harness; no per-turn cost persisted, no OTel, no public benchmark run | OTel GenAI conventions; SWE-bench Pro |
| 10 | Human control / UX | 4.0 | 4 modes, inbox, needs-you, schedules, diff panel, undo, Rooms | Claude Code permission modes; Devin interactive planning |
| 11 | Multi-agent | 3.0 | engine subagents, Rooms read-only forks, delegation brief, worktrees | handoff recaps; worktree-per-worker |
| 12 | Privacy / local-first | 4.0 | no provider key on disk, gateway lanes, local store, redaction chokepoint | local-first coding agents |
| 13 | Reliability / tests | 1.5 | desktop has **no** test suite; backend ~1,340; regressions found by hand | harness-level regression suites |
| 14 | Performance / latency | 3.0 | 3-minute engine start ceiling, one engine per folder, no p50/p95 published | Composer 2 latency targets |

Unweighted mean: **2.93 / 5**.

---

## 2. The topics this agent is built on, and its vocabulary

Read from `desktop/`. These are the names used for the rest of the document.

### 2.1 Topic map

| # | Topic | What it means here | Where |
|---|---|---|---|
| T1 | **Turn policy / modes** | Which rules and which pre-work a turn gets | `lib/tools.ts`, `app/page.tsx` (`ProcMode`) |
| T2 | **Delegation** | Turning a task into a Goal/Way/Tool plan and a contract | `lib/delegate.ts`, `lib/plan.ts` |
| T3 | **Procedural memory** | Goals, Ways, steps, approvals, applicability | backend `services/procedures.py`, `retrieval.py` |
| T4 | **Local knowledge** | `.kell/` claims + library, survey, hooks | `lib/kellplugin.ts`, `packaging/` |
| T5 | **Model routing** | Cheap/strong/fallback rungs, ladder, fallbacks | `lib/pooled.ts`, `lib/kellplugin.ts`, `lib/plan.ts` |
| T6 | **Context management** | Compaction, summaries, prior compression, attachments | `pooled.ts`, `app/api/prompt/route.ts` |
| T7 | **Tool surface** | Engine tools + kell MCP tools + connectors | `lib/mcp.ts`, `lib/connectors.ts`, `pooled.ts` |
| T8 | **Authority / permissions** | Who may do what, and when a person is asked | `pooled.ts`, `lib/remote.ts`, `page.tsx` |
| T9 | **State & recovery** | Sessions, messages, snapshots, undo, restore | `lib/localdb.ts`, `lib/snapshots.ts`, `engine.ts` |
| T10 | **Async operation** | Schedules, inbox, needs-you, Rooms asks | `lib/schedules.ts`, `lib/inbox.ts`, `remote.ts` |
| T11 | **Collaboration** | Rooms, forks, read-only answers, owner approval | `lib/remote.ts` |
| T12 | **Cost & accounting** | Wallet, plan, per-step ledger, spend | `pooled.ts`, `page.tsx` |
| T13 | **Evaluation** | A/B arms, hooks capture, SWE-bench design | `experiments/`, `docs/abtest_followups_2026-10.md` |
| T14 | **Packaging / runtime** | Bundled engine, plugin install, RAM flags | `lib/engine.ts`, `scripts/`, `electron/` |

### 2.2 Glossary (the words a review must not blur)

| Term | Meaning |
|---|---|
| **Goal** | The task class the router resolves ("Research how experts do it"). Not the user's sentence. |
| **Way** | A stored, reviewable procedure for a Goal: ordered steps, actors, bindings, a verification state. |
| **Tool** | A callable capability a Way step binds to (MCP server + tool name). |
| **Rung** | Which model tier a turn uses: `cheap`, `strong`, `fallback`. |
| **Ladder** | The ordered model list a turn walks: flash → glm-flash → mimo → v4-pro → kimi. |
| **Mode** | The turn's policy: `direct` (no search), `agent` (model may sweep), `auto` (search + apply best), `ask` (search + show picker). |
| **Delegation** | The plan object: task, Goal, Way, tool, steps, model, rung, why, brief. |
| **Contract** | Text appended to the prompt that forbids redoing a Way's work another way. |
| **Brief** | The least-privilege description a delegated worker may see (no paths, no files, no history). |
| **Prior** | Compressed earlier assistant work handed to the model when the engine session is new/restored. |
| **Compaction** | Engine-side summarisation when a chat reaches 95% of the window. |
| **Snapshot** | The engine's shadow-git before/after tree per turn; the basis of Undo and the diff panel. |
| **Inbox / needs you** | The queue of things waiting on the person (permission, failed or missed schedule). |
| **Hook** | `hook-prompt` (find_ways + survey + tool note), `capture-tool`, `capture-stop` (outcome, library entry). |
| **Survey** | The first-prompt pass that builds `.kell/` for a repo. |
| **Claims / library** | Repo facts and past fixes-with-diffs, kept locally and injected on later prompts. |
| **Lane** | Who pays: `organization`, `personal` (wallet), or `own-key`. |
| **Rooms ask level** | `owner` (own session), `answer` (read-only fork), `approve` (approved full), `full` (full fork). |
| **Refine** | Improve / Specialize / Abstract a Way before submitting it. |

---

## 3. The questions, and what the sweep answered

These are the questions this review is built on. Each answer is the literature/industry position, then what it
implies for kell.

| # | Question | Answer (source) | Implication |
|---|---|---|---|
| Q1 | Is "cheap model first" still a defensible thesis in late 2026? | Yes, more than in 2024. GLM 5.3 Flash scores 92.0% on SWE-bench Verified at **$0.02/test**; DeepSeek V4 Flash 88.8% at $0.01 (Vals AI, Sep 2026). | The thesis holds, but the moat is thinning: the cheap model alone is now good. kell must win on *selection* and *verification*, not on model choice. |
| Q2 | Which benchmark actually measures a coding agent now? | SWE-bench Verified is saturated (97%) and contaminated; OpenAI stopped reporting it and recommends **SWE-bench Pro**. Terminal-Bench 2/4.1 is the live frontier (DeepSeek V4.1 Flash 90.6%, Nov 2026). | kell's preregistered SWE-bench run is aimed at a saturated target. Add SWE-bench Pro and Terminal-Bench 2.1 subsets. |
| Q3 | Does harness design matter as much as the model? | Yes: a controlled ablation (arXiv 2609.20804) over 176 settings finds context management, planning and action space each move cost/accuracy independently of the model. | kell's harness is the product. It should be ablated, not just shipped. |
| Q4 | What does planning actually buy? | For stronger models, planning is mostly a **cost saver**, not an accuracy gain; for weaker models it is an accuracy scaffold (same study). | kell's cheap-first default needs *cheap* planning (a todo artifact), not an expensive plan phase. |
| Q5 | Is more context better? | No: performance degrades non-uniformly with input length ("context rot", Chroma, 18 models). | kell's compaction is right in principle; its trigger (95%) is late, and it has no budget meter. |
| Q6 | How should tools scale past ~50? | Tool search: 50 tools cost 10–20K tokens and selection accuracy degrades past 30–50 loaded (Claude Agent SDK, LiteLLM). | `codemode: false` plus a manual discover→use two-step is the right idea with the wrong ergonomics. |
| Q7 | Does procedural memory work? | Yes, if the *lifecycle* is managed. REM: 68.7% vs 55.6% match@1, 27.6% vs 24.9% resolution, 17% lower cost — by deciding what to store, when to recall, what to forget (arXiv 2610.27377). Memp and the Agentic Memory survey agree. | kell stores and retrieves well; it has no forgetting, no decay, no in-loop recall gate. |
| Q8 | Is retrieval quality the bottleneck for memory? | No — REM's result is that **storage and governance** matter more than retrieval fidelity. | kell's RRF + applicability work is necessary, not sufficient. |
| Q9 | Is RRF safe as a fusion rule? | Yes, with caveats: rank-only fusion avoids scale mismatch, but shallow lists lose the agreement it exists to reward; a 2026 paper names a "1/k ceiling" for zero-overlap lists. | kell fetches `top_k * 2` per lane. Too shallow. |
| Q10 | Do learned routers beat static ladders? | RouteLLM: 85% / 45% / 35% cost cuts at ~95% quality on MT-Bench/MMLU/GSM8K; cascades and multi-turn routers improve further (MTRouter 2026). | kell's `NEEDS_STRONG` regex and ladder position are a cold-start heuristic, not a router. |
| Q11 | Is silent escalation acceptable? | No. A fallback is a behaviour change (tools, structured output, context length, residency), not transparent failover. | kell's "suggest, never escalate" is *better* than the industry default and should stay. |
| Q12 | What is the main security failure mode for coding agents? | Indirect prompt injection through repo files, AGENTS.md, skills, source comments and tool output — the "lethal trifecta" of private data + untrusted content + egress. | kell has all three, with no trust labels and no egress policy. |
| Q13 | Can prompt-level defenses fix injection? | Not reliably. CapScope (arXiv 2609.08371) cuts injected effects from 33–47/75 to **3/75** by capability-scoping tool calls outside the model's context, at no completion cost. | kell needs harness-level authority, not more prompt text. |
| Q14 | Do humans catch silent defects? | No: N=36 study, participants were confident while missing silent defects, did generic checks and misread evidence (arXiv 2610.27801). | "The person reviews the diff" is not a verification strategy. |
| Q15 | What does verification cost? | Test-first rewards invite reward hacking (hardcoding, editing tests, reading the gold patch); SWE-Bench Pro needed a verified variant for this reason. | kell's "never claim a check you did not run" is unenforceable without a check record. |
| Q16 | Should the agent own a todo list? | Yes for long-horizon work: externalised constraint tracking beats in-head state; plan mode is a permission mode, not a subsystem. | kell has no owned plan artifact; the engine's `todowrite` is optional and invisible to the app. |
| Q17 | Do subagents help? | Yes for context isolation and cost, if each gets a narrow brief and its own context; the failure is duplicated work and lost handoffs. | kell's brief is good; there is no handoff recap and no parallel worktree orchestration. |
| Q18 | What should a coding agent's state model be? | Event-sourced and append-only, with one canonical log and derived projections (OpenHands V1). | kell has three conversation stores and a hard delete. |
| Q19 | What breaks long-running agents in production? | Context overflow, stream disconnects, lost sessions, non-durable task state, per-process limits. | kell has each of these; the backend review (`docs/backendfix`) documents four. |
| Q20 | Is local-first a real differentiator? | Yes: no key on disk, no vendor seeing the code, offline-capable peers exist and sell exactly this. | kell's key handling is strong; "local-first" is undermined by shipping every turn to a cloud model. |
| Q21 | Does the scaffold or the model explain benchmark gains? | Scaffold: the same model scores differently under different harnesses (Terminal-Bench authors; Composer 2 trained *in* its harness). | kell should publish harness-level numbers, not model-level ones. |
| Q22 | What is the right evaluation unit? | Cost per **resolved** task, with pass rate, at a stated budget — not cost per token or raw pass rate. | kell's A/B reports cost and solved; the desktop does not record cost per turn. |
| Q23 | Does memory transfer across repos? | Weakly. The local-eval preregistration bets on same-repo history (27→42 right-cause, p=0.023, internal n=96); enterprise/global tiers are the open question. | kell's L1/L2/L3 design is the right experiment; it is unrun. |
| Q24 | What do users actually do with an agent? | Long, interruptible sessions with steering; the human stays in the loop (Codex/Claude Code docs). | kell's Stop, inbox and modes fit this better than most. |
| Q25 | Is a repo map still worth it? | Yes at ~1K tokens: graph-ranked signatures give whole-repo awareness cheaply (Aider). | kell relies on glob/grep; no map, so orientation is expensive. |
| Q26 | Do skills/progressive disclosure beat big prompts? | Yes: metadata always loaded, body on trigger, resources on demand (Anthropic Agent Skills). | kell's `.kell` is close, but Ways are retrieved by the server, not disclosed by the agent. |
| Q27 | What observability is standard now? | OTel GenAI spans (`gen_ai.operation.name`, agent id/name/version, tool spans). | kell has hooks and logs, no spans, no replay. |
| Q28 | Can a small model be the whole agent? | Increasingly yes on narrow tasks; distillation and SLM issue-resolution studies show viability, with energy/latency tradeoffs. | Supports kell's ladder; raises the bar on verification, since the cheap model errs more quietly. |
| Q29 | What is the failure mode of "review the diff"? | Automation bias and complacency; oversight degrades with extended use. | The product needs machine checks and *targeted* human attention, not a bigger diff pane. |
| Q30 | Is there a defensible niche left? | Yes: verified procedural reuse + cost governance + owner-controlled collaboration is not what Claude Code, Codex, Cursor or Devin sell. | kell should compete on *trust per dollar*, not on raw agent capability. |

---

## 4. Advantages (do not regress these)

1. **Cost-first with honest escalation.** `chooseModel` returns the cheap rung and a *suggestion*; it never silently
   spends more (`lib/plan.ts:59`). This is better than the industry default and matches the evidence that fallbacks
   are behaviour changes.
2. **Measured savings, not claimed savings.** Four A/B batches against plain Claude Code: −45%, −44%, −29%, −43%
   cost, with 0–4 fewer tasks solved out of 30–45 (`docs/abtest_followups_2026-10.md`). Very few agent products
   publish this.
3. **Procedural memory with provenance.** Goal → Way → Tool, `verification_state`, approval gates, an applicability
   cascade that disqualifies rather than down-ranks (`services/applicability.py`). This is the real differentiator.
4. **Local knowledge that compounds.** `.kell/` claims + library + survey + outcome capture, in Claude Code's hook
   format (`lib/kellplugin.ts`). The agent gets better on *this* repo without a training run.
5. **No provider key on the machine.** Both lanes send the person's kell sign-in to the gateway; the engine gets an
   env var, never a stored key (`lib/pooled.ts`).
6. **A durable local chat store independent of the engine.** `chats.db` survives engine upgrades and a deleted
   engine DB (`lib/localdb.ts`).
7. **Human control that is actually built.** Four explicit modes, Stop that interrupts the engine, inbox, needs-you
   notifications, schedules, diff panel, Undo, model menu.
8. **Owner-controlled collaboration.** Rooms asks run in a fork; `answer` level is deny-all-then-read-only; a
   permission goes back to the owner (`lib/remote.ts`). This is a better collaboration model than shared sessions.
9. **Least-privilege delegation brief.** `briefOf` gives a worker no paths, no files, no history, and requires it to
   say what it could not check (`lib/plan.ts:79`).
10. **A culture of honest scope limits.** Docstrings and prompts repeatedly refuse to claim capability they lack.
    That is rare and worth keeping.

---

## 5. Problems, what is missing, and how to make it up

Severity: **P0** = can lose data, mislead the person, or be exploited; **P1** = wrong behaviour under real load;
**P2** = quality and operations.

### P0-1. No verification gate: the agent can say "done" without a check

**What is missing.** There is no test/check phase in the desktop turn. `app/api/prompt/route.ts` streams the model's
text and writes it to history; nothing runs the project's tests, typecheck or lint, and nothing records that a check
ran. The rules say "never claim a check you did not run" (`lib/plan.ts:92`), but that is an instruction, not an
invariant. The A/B test-first loop that produced the savings lives in the **Claude Code** arm
(`experiments/abtest`), not in the desktop.

**Why it matters.** Silent defects are exactly what people miss: in the N=36 oversight study, participants were
confident while missing them and misread the evidence they did collect. Test-passing rewards are also hackable
(editing tests, hardcoding, reading the gold patch), which is why SWE-Bench Pro needed a verified variant.

**Make it up.**
1. Detect the project's check command once per repo (package.json scripts, pyproject, Makefile, Cargo, go test) and
   store it in `projects` metadata.
2. After any edit, run the check in the turn, capture exit code + first failing lines, and attach a
   `verification` record to the assistant message.
3. Block the "done" summary on `verification.ran == true` when the turn edited code; if it could not run, the reply
   must say so, and the UI shows **Unverified**.
4. Add a test-writer step for checkable tasks (the abtest `SPEC_SYSTEM` already exists) and run cheap models against
   the project check plus those tests in a worktree.
5. Show **Verified / Unverified / Failed** in the transcript and in the inbox; never a green check without a record.

**Measure:** share of edit turns with a check; pass rate; "claimed but unverified" count (target 0).

### P0-2. No sandbox and no egress policy: the lethal trifecta is open

**What is missing.** The engine runs with a full shell in the person's folder (`lib/engine.ts`), `config.websearch`
is off but `AGENT_RULES` explicitly tells the model to fetch pages through kell's tools, and nothing labels fetched
text as untrusted. There is no network allowlist, no container, no filesystem boundary beyond the project, and no
capability scoping. The only policy is `permissions = [{ action: "question", ... deny }]` plus whatever the engine
asks about.

**Why it matters.** CapScope's evaluation: with ambient authority, an injected instruction executes in 33–47 of 75
runs; with typed capabilities checked outside the model's context, 3 of 75 — with no loss of completed repairs.
Prompt text does not fix this.

**Make it up.**
1. Put fetched web/repo content in an explicit `untrusted` envelope, and strip instruction-like lines before it
   enters the prompt.
2. Default-deny egress for shell and tools; allowlist hosts per project; route through the app's proxy so the policy
   is enforced, not requested.
3. Introduce capability tokens per turn (read path, write path, exec, net host) minted by the app, checked at the
   tool wrapper — the CapScope shape, stored outside the model's context.
4. Keep `READ_ONLY_RULES` for `answer` forks, and extend the same policy language to local turns.
5. Add a dry-run/approval step for destructive commands (`rm -rf`, `git reset --hard`, force-push, credential reads).

**Measure:** injected-effect rate on a 20-case suite; blocked-egress count; false-block rate.

### P0-3. Conversation durability is still three stores and a hard delete

**What is missing.** `deleteSession` deletes messages, attachments, summaries and the row in one transaction
(`lib/localdb.ts:230`); the route then removes the engine session. No `deleted_at`, no trash, no export, no
snapshot before delete. The engine has a third copy (`session_v2`/`session_message`) that the app does not read.
A null `directory` hides a row from every project list.

**Why it matters.** This is the GenRec incident: one cleanup script deleted "the newest session" and the chat
vanished from the sidebar while its v2 log survived by luck. SQLite WAL means a copy without the WAL is a different
database; there was no snapshot to restore from.

**Make it up.**
1. `deleted_at` + a Trash view + restore; delete becomes a tombstone.
2. Export the chat (engine `experimental.session.export`) before any permanent delete.
3. Make `chats.db` canonical; treat the engine session as an execution detail (the same-id recreation in
   `app/api/prompt/route.ts` is the right start) and rebuild from local on demand.
4. Show null-directory chats under **Unfiled**.
5. Snapshot on a timer and before migrations with `VACUUM INTO`; keep the last N.

**Measure:** delete→restore round-trip test on a real SQLite file; zero chats unreachable by directory.

### P0-4. The desktop has no tests

**What is missing.** No `*.test.ts`, no Playwright/Electron smoke suite, no route-level tests. The regressions in
the recent log (terminal repaint, session vanishing, stop not ending the turn, mode sizes) were all found by hand.

**Why it matters.** The agent's correctness lives in glue code: event mapping, session lifecycle, cost ledger,
permission replies. That is exactly what unit tests catch and manual clicking does not.

**Make it up.**
1. Route tests with a fake engine client: prompt → events → local rows; stop; restored session; failed turn.
2. SQLite tests against a temp file for delete/restore, import, FTS, filing.
3. One Electron smoke test: launch, open a folder, send a prompt against a stub engine, assert a bubble and a row.
4. Golden transcripts for `rulesFor`, `contractOf`, `briefOf` so prompt changes are visible in review.

**Measure:** the desktop suite runs in CI in under 2 minutes; every P0 path has a test.

### P1-1. No planning artifact and no stuck detection

**What is missing.** One ReAct turn. No plan mode (writes forbidden while planning), no persisted todo owned by the
app, no dependency graph, no detector for "same tool call three times" or "no diff after N steps". The engine's
`todowrite` exists but the app neither requires nor displays it.

**Why it matters.** The harness ablation finds planning is a cost saver even when accuracy is flat, and that
externalised constraint tracking is what keeps long tasks from drifting. Without a stop rule, a cheap model can
loop and bill.

**Make it up.**
1. Plan mode as a permission rule (deny write/edit/shell-mutate), with an explicit "approve plan" transition.
2. An app-owned `plan` row per session, rendered as a checklist, updated by a `update_plan` tool.
3. Stuck detection: repeated identical tool call, no file diff over K steps, same error twice → ask the person or
   escalate the rung.
4. A per-turn step budget with a visible counter.

**Measure:** loop rate per 100 turns; cost per resolved task on long tasks.

### P1-2. Context is managed late and blindly

**What is missing.** Compaction only at 95% of the window, LLM-summarised (lossy, blocking); no repo map; no
tool-schema search; no context-budget meter; prior work compressed to 3,000 chars only for imported/restored chats.
`codemode: false` means every kell tool schema is loaded, and discovery is a manual two-step.

**Why it matters.** Context rot is real and non-uniform; tool definitions alone can eat 10–20K tokens at 50 tools;
Aider gets whole-repo awareness for ~1K tokens.

**Make it up.**
1. Stage rule-based elision (drop tool output bodies, keep heads/tails) *before* LLM summarisation — the ablation
   found this the strongest strategy.
2. Add a repo map: tree-sitter signatures, graph-ranked, ~1K token budget.
3. Turn on semantic tool search with top-5 schemas and cache loaded tools per session.
4. Show a context meter (system / tools / history / files) and warn at 70%, not 95%.

**Measure:** overflow failures per 100 turns; tokens per turn at equal task success.

### P1-3. Routing is a regex, not a router

**What is missing.** `NEEDS_STRONG` is a word list; `modelPlan` maps by ladder position; there is no learned
difficulty estimate, no escalation after a failed check, and no per-step routing in practice (step models are
suggested, not enforced). The ladder is static.

**Why it matters.** RouteLLM and cascades show large gains from learned routing; MTRouter shows multi-turn routing
matters more than per-prompt routing. kell has the outcome data (hooks, A/B) but does not learn from it.

**Make it up.**
1. Log `(task features, rung, outcome, cost)` per turn; train a small difficulty classifier offline; ship it as a
   prior, not a hard rule.
2. Escalate only on evidence: failed check, repeated error, explicit "this is hard" signal — and show the reason.
3. Route per step inside a Way (cheap for mechanical steps, strong for design/review), with a per-Way budget.
4. Keep "suggest, never silently escalate" as the product contract.

**Measure:** cost per resolved task vs the static ladder at equal success.

### P1-4. Memory has no lifecycle

**What is missing.** No forgetting, no decay, no dedup, no negative evidence, no in-loop recall gate. `find_ways`
runs on every prompt regardless of whether the agent is struggling; stored Ways never age out; a bad Way can be
retrieved forever.

**Why it matters.** REM's gains come from *when* to recall and *what to forget*, not retrieval alone. A memory that
only grows becomes noise and a liability.

**Make it up.**
1. Write path: consolidate a finished turn into a small typed record (files, cause, fix, check) — not a transcript.
2. Recall gate: inject the compact record up front; inject the detailed record only when a check fails or the agent
   repeats itself.
3. Forget: decay unused Ways, retire those with repeated failures, cap per Goal, and record why.
4. Negative evidence: a Way that failed under stated conditions lowers its applicability, not just its score.

**Measure:** match@1 and resolution on a held-out repo set; retrieval precision at k.

### P1-5. Cost is displayed but not recorded

**What is missing.** `session.step.ended` updates a live ledger and `spend` in the UI, but the assistant row written
by the prompt route carries no tokens, model or cost (`appendMessage` gets role/text/summary only). There is no
cost per session, per Way, or per resolved task.

**Why it matters.** The product's claim is cost. Without persisted cost you cannot audit it, route on it, or publish
it. The A/B had to instrument outside the app.

**Make it up.**
1. Persist `model`, `tokens_in/out`, `cost_usd` on every assistant row from the step events.
2. Aggregate per session, per Way, per project; show "$ per resolved task".
3. Emit a `turn.finished` record with outcome and verification status for the offline learner.

**Measure:** 100% of assistant rows priced; dashboard reconciles with the gateway invoice.

### P1-6. Tool surface is broad, unranked and unlabelled

**What is missing.** `discover_tools` → `use_tool` is manual; `codemode: false`; connectors are merged into the
engine config; no per-tool risk label, no schema validation at the app boundary, no result-size cap.

**Why it matters.** Tool selection accuracy degrades past 30–50 tools; unlabelled results are an injection surface;
oversized results blow context.

**Make it up.**
1. Semantic search over tool cards, top-5 schemas, cached.
2. Risk tier per tool (read / write / spend / external) and a default policy per tier.
3. Validate arguments and truncate results at the wrapper, with a "full result" fetch on demand.
4. Label every external result as data, never instructions.

### P1-7. Multi-agent without merge discipline

**What is missing.** Subagents exist (engine), and Rooms forks exist, but there is no worktree-per-worker, no patch
merge/conflict handling, no handoff recap when a different agent continues a thread, and no shared task board.

**Why it matters.** The failure mode of multi-agent work is duplicated or conflicting edits; the handoff recap
pattern (what the next agent missed, as data) is what makes heterogeneous agents usable.

**Make it up.**
1. Worktree per delegated worker; return a patch + a check result; merge only on green.
2. A `<conversation_handoff>` block for cross-agent continuation, explicitly marked as context, not instructions.
3. A per-session task board with owners and states.

### P1-8. Observability is logs and hooks, not traces

**What is missing.** No OTel GenAI spans, no trajectory store (tool calls, diffs, checks), no replay, no per-turn
latency percentiles. Debugging relies on `next.log` and the engine DB.

**Why it matters.** You cannot improve routing or verification without a trajectory you can replay; OTel GenAI is
now the standard shape for exactly this.

**Make it up.**
1. Emit `gen_ai.*` spans for turn, model call, tool call, check, with agent id/version.
2. Store the trajectory (tool, args hash, result size, duration, outcome) next to the message.
3. Build a replay harness that re-runs a recorded turn against a new prompt/rung.

### P1-9. Evaluation targets a saturated benchmark and is unrun

**What is missing.** The SWE-bench design is preregistered but has produced no scored results; the local-eval
L1/L2/L3 experiment is preregistered and unrun; the A/B is function-level (DS-1000, BigCodeBench, MBPP+) and
predates the desktop.

**Why it matters.** SWE-bench Verified is saturated and contaminated; OpenAI recommends SWE-bench Pro. Publishing
a number on the wrong benchmark is worse than publishing none.

**Make it up.**
1. Run a **SWE-bench Pro** and **Terminal-Bench 2.1** subset (50–100 tasks) with cost per resolved task, on
   post-cutoff tasks only.
2. Report harness-level results: same model, kell harness vs a bash-only harness.
3. Finish the local-eval L1/L2/L3 arm; it is the actual product claim.
4. Publish "on checkable tasks, X% saved at Y% quality" — never "saves X% on your work".

### P2-1. Startup, supervision and resource behaviour

Engine start can take 3 minutes after an upgrade; one engine per app; no health surface; `OPENCODE_*` flags trim
RAM but nothing measures it. Add a supervisor with crash-loop backoff, a warm start per recent project, and a
health endpoint reporting engine state and last successful turn.

### P2-2. Prompt-injection surface in the rules themselves

`AGENT_RULES` tells the model to run several sweeps and "say what you searched", but does not tell it that page
content is untrusted, nor forbid following instructions found in it. Add an explicit trust paragraph and a
"quote, do not obey" rule; pair it with P0-2's enforcement.

### P2-3. The person is the only reviewer

Automation bias means a diff pane is not oversight. Add targeted re-engagement: ask the person only when a check
failed, a risky capability was used, or confidence is low — and say *what* to look at, with the failing output.

---

## 6. What to do first (sequenced)

**0–30 days — trust floor**
1. Persist cost/model/tokens per assistant row (P1-5).
2. Verification record + Verified/Unverified badge + block unverified "done" (P0-1).
3. Soft delete + Trash + `VACUUM INTO` snapshot (P0-3).
4. Desktop route tests + one Electron smoke (P0-4).
5. Untrusted envelope for fetched text + egress allowlist (P0-2, first half).

**30–90 days — capability**
6. Plan mode + app-owned todo + stuck detection (P1-1).
7. Repo map + staged elision + context meter + tool search (P1-2, P1-6).
8. Capability tokens for tool calls (P0-2, second half).
9. Memory lifecycle: consolidate, gate, decay (P1-4).
10. Cost-aware router trained on logged outcomes (P1-3).

**90+ days — proof**
11. SWE-bench Pro / Terminal-Bench 2.1 subset with cost per resolved task (P1-9).
12. OTel spans + trajectory replay (P1-8).
13. Worktree-per-worker merge discipline (P1-7).
14. Publish the harness ablation: same model, kell vs bash-only.

---

## 7. Sources

**Industry / benchmarks (Exa)**
- SWE-bench leaderboards: https://www.swebench.com/ · Verified: https://www.swebench.com/verified.html
- Vals AI SWE-bench Verified (Sep 2026): https://www.vals.ai/benchmarks/swebench
- OpenAI, "Why SWE-bench Verified no longer measures frontier coding capabilities":
  https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/
- Terminal-Bench 2 leaderboard (Nov 2026): https://aicost.tools/benchmarks/terminal-bench-2/ ·
  https://www.tbench.ai/
- Claude Code, "How Claude Code works": https://code.claude.com/docs/en/how-claude-code-works
- Claude Code subagents: https://code.claude.com/docs/en/sub-agents
- Claude Agent SDK tool search: https://code.claude.com/docs/en/agent-sdk/tool-search
- Anthropic, "Effective context engineering for AI agents":
  https://www.anthropic.com/research/effective-context-engineering-for-ai-agents
- Anthropic Agent Skills: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Martin Fowler, "Context Engineering for Coding Agents":
  https://martinfowler.com/articles/exploring-gen-ai/context-engineering-coding-agents.html
- OpenAI, "Unrolling the Codex agent loop": https://openai.com/index/unrolling-the-codex-agent-loop/
- Cognition, Devin 2.0: https://cognition.com/blog/devin-2
- Aider repo map: https://aider.chat/docs/repomap.html
- OpenHands SDK architecture: https://arxiv.org/html/2511.03690
- LiteLLM MCP tool search: https://docs.litellm.ai/docs/mcp_tool_search
- RouteLLM: https://www.lmsys.org/blog/2024-07-01-routellm/
- LangChain, coding agent sandboxes: https://www.langchain.com/resources/coding-agent-sandboxes
- Simon Willison, external sandboxes: https://blog.primeradiant.com/external-sandboxes-for-coding-agents/
- NVIDIA, sandboxing agentic workflows:
  https://developer.nvidia.com/blog/practical-security-guidance-for-sandboxing-agentic-workflows-and-managing-execution-risk/
- OTel GenAI agent spans:
  https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md
- Chroma, "Context Rot": https://www.trychroma.com/research/context-rot
- Harness engineering, planning: https://ghdaru.github.io/harness_engineering/en/09-planning.html

**Papers (arXiv / OpenAlex)**
- REM: Reusable Experience Memory — https://arxiv.org/abs/2610.27377
- Memp: Exploring Agent Procedural Memory — https://arxiv.org/abs/2508.06433
- A Survey on the Memory Mechanism of LLM-based Agents — https://doi.org/10.1145/3748302
- Agentic Memory (ACL 2026) — https://doi.org/10.18653/v1/2026.acl-long.981
- An Empirical Study of Harness Design for Coding Agents — https://arxiv.org/html/2609.20804v1
- The Verification Horizon: No Silver Bullet for Coding Agent Rewards — https://arxiv.org/html/2606.26300v1
- SWE-Bench Pro Verified — https://arxiv.org/abs/2609.08149v3
- SWE-Bench ProMax — https://arxiv.org/abs/2608.09802v2
- EvilGenie: A Reward Hacking Benchmark — https://arxiv.org/abs/2511.21654v2
- Saving SWE-Bench (benchmark mutation) — https://arxiv.org/abs/2510.08996v4
- Authority Is Not a String (CapScope) — https://arxiv.org/html/2609.08371v1
- Evaluating Indirect Prompt Injection Defenses in Tool-Using LLM Agents —
  https://doi.org/10.3390/computers15090570
- What If Prompt Injection Never Left? (cross-session stored injection) — https://arxiv.org/abs/2606.04425v2
- Looks Fine to Me: Understanding Oversight in Agentic Interactions — https://arxiv.org/html/2610.27801
- AI Agents Push Humans Out of the Loop — https://arxiv.org/abs/2608.23642
- From Delegation to Action Approval — https://arxiv.org/html/2611.10058v1
- Software Delegation Contracts — https://arxiv.org/abs/2606.17099v1
- RACE-Bench: repository-level code agents — https://arxiv.org/abs/2603.26337v3
- SWE-Trail: history-guided trajectory collection — https://arxiv.org/abs/2611.02074v1
- MTRouter: cost-aware multi-turn LLM routing — https://arxiv.org/abs/2604.23530v1
- Hybrid LLM: cost-efficient query routing — https://doi.org/10.48550/arxiv.2404.14618
- A Unified Approach to Routing and Cascading — https://doi.org/10.48550/arxiv.2410.10347
- The Routing Plateau — https://doi.org/10.48550/arxiv.2606.07587
- Parallel Context Compaction for Long-Horizon Agent Serving — https://arxiv.org/abs/2605.23296v1
- The 1/k Ceiling in Reciprocal Rank Fusion — https://doi.org/10.5281/zenodo.23331138
- Composer 2 Technical Report — https://arxiv.org/html/2603.24477v2
- SWE-agent: Agent-Computer Interfaces — https://arxiv.org/abs/2405.15793
- MapCoder-Lite: distilling multi-agent coding into a small LLM —
  https://aclanthology.org/2026.findings-eacl.346/
- Oracle Agent Memory as an Enterprise Memory Substrate — https://arxiv.org/abs/2607.13157v1

**This repo**
- `docs/abtest_followups_2026-10.md` (four A/B batches)
- `experiments/local_eval/PREREGISTRATION.md` (L1/L2/L3 design)
- `experiments/swebench/DEVIATIONS.md` (grading path, no results)
- `docs/backendfix/README.md` (backend findings)
- `docs/desktop_requirements_2026-11.md` (feature baseline)

---

*Limits of this review: no benchmark was run; no click-through of the built app; the desktop has no test suite to
read. Scores are judgement against the cited frontier evidence, not measurements. The one measurement quoted from
this repo (the A/B batches) is function-level and predates the desktop.*
