# Routing issues found on 2026-10-28

What is wrong, or unproven, in how kell routes work to cheaper models. Written from a debugging session on one
machine, one repository (this one) and a scratch database, so every item says how sure it is:

- **Observed**: seen in a log, a probe reply or a file during that session.
- **Read**: taken from code or comments, not run.
- **Inferred**: follows from the above, not checked.

How routing works, in one paragraph: the prompt hook (`packaging/npm/lib/hook.mjs`) asks a triage judge what kind of
prompt it is, calls `find_ways` with the session's model, the cheap candidates from `~/.stealthlab/exec.json` and this
repo's own evidence (`lib/library.mjs` `requestPayload`: `route_obs` from `.kell/routing.md`, `library_rows`,
`task_features`). The server (`backend/app/routing/`: `service.py` beliefs, `ladder.py` search, `plan.py` the plan)
returns a ladder of models. `lib/dispatch.mjs` runs the cheap rungs in git worktrees, accepts one only if a check
passes, and records the attempt. If none passes, the session's model takes over.

## A. Problems with the design

### 1. One check for every task (Observed; introduced by a local setting)
`.claude/settings.local.json` set `STEALTHLAB_DISPATCH_CHECK=node --test packaging/npm/test/hook.test.mjs`.
`dispatchCheck` (`dispatch.mjs:783`) returns that before the library's own check and before test-command detection, for
every prompt. A cheap answer to a backend or frontend task passes if the hook tests pass, which says nothing about it.
**Fix:** do not set a global check; rely on the matched library entry's check or `detect_check.mjs`; scope an override
to the paths it covers.

### 2. The router cannot tell an easy task from a hard one (Observed)
Plans for a small rename, a hard redesign of the routing posterior and a vague question all came back as
`case: {"kind": "code"}` with cheap-first ladders. Every lookup in the log ended `no_match`, so there is no
task-specific key, and `task_features` are repo-wide medians, not features of the current task. The goal "nearly
everything goes cheap, only extreme reasoning escalates" therefore has no up-front signal for "extreme reasoning";
escalation happens only after a cheap model fails its check.
**Fix:** a difficulty signal on the task (size, files touched, ambiguity) from triage or from the located files.

### 3. The check is a weak verifier (Read; Observed in one plan)
`backend/app/routing/config.py` gives `tests` checks a prior of about 20% false accept, and a comment there cites 6 wrong
answers accepted out of about 79 wrong attempts. One plan from the probes reported `p_wrong_delivered` of about 0.11
for a cheap-first ladder. A passing check is evidence, not proof, so cheap-first delivers a wrong answer roughly once
in ten when the check is only unit tests.

### 4. The data loop starves itself (Observed)
The router learns only from tasks where a cheap model was tried. When the plan puts the session model first, no cheap
data arrives, so it keeps doing so. Until a forced run on 2026-10-28 this repo had four observations, all of the
session model, and none for any cheap model; the log had 160 "no check" skips. The only way out is the 20% random
exploration (`STEALTHLAB_DISPATCH_EXPLORE`), which spends cheap attempts on real work.

### 5. A fixed reliability target makes it worse (Observed)
With `STEALTHLAB_HOOK_RELIABILITY=0.8` and the repo's own evidence sent, the session model was first in 6 of 8 draws
(2 of 8 without the repo evidence). With the default (match the session model within 0.03) the first rung was a cheap
model in 8 of 8 either way. The setting was removed from this repo on 2026-10-28.

### 6. Savings are not established (Read; Inferred)
The session model still sees every prompt ("Done by <model>") and a Sonnet review runs on each accepted answer
(`~/.stealthlab/dispatch_costs.jsonl`, about $0.012 each). One plan estimated about $0.24 expected for a cheap-first
ladder against about $0.40 for the session model alone. CLAUDE.md lists "utility-in-routing" as open in Band 3. No
end-to-end measurement of cost saved at equal correctness exists for real use.

### 7. The ladder is random on purpose (Read; Observed)
`STEALTH_ROUTING_FEASIBILITY` defaults to `draw` (Thompson sampling, `ladder.py:237`): one possible world is drawn and
the cheapest ladder that meets the target in that world is chosen. The same task gets different first rungs on
repeated calls, and `meets_reliability_target: true` can appear with `p_success` of 0.39 because it is true in the
drawn world. This is useful for learning and bad for predictability. `posterior` mode never tries what it is unsure of.

## B. Problems with what gets recorded

### 8. Questions are recorded as successful tasks (Observed)
At 05:28 on 2026-10-28 a prompt that was only a question ("why do we need to run migrations everytime we do this?",
triage `work=answer`) produced library entry `L-097db7` with `outcome=pass` and `route=R-3a1a34`. That route is the one
whose session-model observation reads `n=2 ok=2`. So conversation turns count as passed attempts and raise the session
model's record.

### 9. Library entries capture the whole dirty working tree (Observed)
`L-097db7` lists 126 files and its stored diff is about 45 MB. It includes other lanes' uncommitted work, vendored
skills, lock files and PDFs, none of which the prompt touched. The entry's steps are things like "change meta.json".
This is attributed to one prompt because the tree was dirty when it ended.
**Fix:** record only the files this session's tools edited (`sessionEdits` in `dispatch.mjs` already reads them from the
transcript), and skip prompts triage called answers.

### 10. Checks are recorded as the literal commands that were run (Observed)
The injected "similar solved problem" included a check that was a multi-line heredoc with absolute
`C:/Users/chait/...` paths. It cannot be reused on another machine or after a rename.

### 11. Observations never expire (Inferred)
`OBS|route|model|scaffold|n=|ok=|last=` keeps counts and a last-seen date only. A provider can change a model's weights
or serving stack without notice, and old results keep their full weight.

### 12. Routing codes are not recorded in single-user mode (Observed)
`capture-stop ways coded=0 (server answered 401: sign in first: routing codes need a verified token)` appears after
tasks. The local server runs `DEPLOYMENT_MODE=single_user`, which has no verified identity, so that part of learning
does not run locally.

## C. Problems with the lookup the model sees

### 13. The injected knowledge is large and mostly irrelevant (Observed)
Every prompt in the session got "Solved before in THIS repo (not judged: matched by words only)" followed by up to 97
file lines and the start of a diff, for tasks the entry had nothing to do with. That is several thousand characters added to the
session model's context per prompt, which works against the goal of spending less. The match is by words only, and
the lookups logged on 2026-10-28 ended `no_match`, `refused` or `not_needed`, never a real Goal match.
**Fix:** inject nothing unless the match is judged relevant; cap the size; never inject a diff that is not for the
task's files.

### 14. Triage is often unsure (Observed)
Many logged verdicts end "below confidence 0.6" (`conversation`, `repo_specific`, `knowledge_question`), and one clear
change request came back `unclear_change`. A wrong verdict either blocks dispatch for a change or sends a question to
cheap models.

### 15. Every prompt pays for triage and lookup (Observed)
From the log, triage to lookup result took about 2.6 to 4.4 seconds per prompt, even when nothing was dispatched.

## D. Problems with running the cheap models

### 16. Dispatch blocks the prompt (Read)
Dispatch runs inside the prompt hook. Its time limit is 600 s for the cheap tries together
(`STEALTHLAB_DISPATCH_TIMEOUT_S`), and the benchmark script sets the hook timeout to 1200 s. The user waits with no
output while it runs.

### 17. Provider faults look like model failures (Observed; Read)
During one day: a `HTTP 500 provider_error` from gpt-oss that went away (4 of 4 direct calls then returned 200); one
`400 Invalid base64 image data` from gemma on a plain text prompt (1 of 4); General Compute's day quota reading 0 from
a cache entry that was 12 hours old. The Python path (`call_model`) says an outage is never reported as a failed
attempt. I did not read whether the Claude Code path does the same, so a provider fault may lower a model's record.

### 18. A dirty or conflicted working tree blocks or complicates dispatch (Observed)
`dispatch gpt... refusing: <repo> has unmerged paths` stopped a run until the conflict was resolved. A verified answer
is applied onto a tree that may hold other uncommitted work, and `kell undo` refuses if those files changed since.

### 19. Code and conversation go to outside providers (Read)
`conversationPrompt` (`brief.mjs`) sends earlier turns and located file excerpts to the cheap models' providers
(General Compute, z.ai) with the user's keys, and the hosted server receives up to 4000 characters of every prompt
(raised from 1500 for the lookup and triage on 2026-10-28). This needs to match what the product says about the data path.

## E. Problems with the setup

### 20. One server, one database, no visible failure (Observed)
From 04:17 to 05:06 on 2026-10-28 every prompt logged `triage gave no verdict` and `no knowledge added (fetch failed)`.
The server could not start because the Neon account was over quota (`InsufficientResourcesError`), and the autostart
hook kept relaunching a server that exited. The session kept working, so routing failed open, but nothing told the user.

### 21. Two routing paths keep separate books (Inferred)
The Claude Code path records in `.kell/routing.md` and via `report_result`; the Python path (`call_model` with
`model="auto"`, then `report_result`) records on the server. The older `recommend_models` and `report_model_run` tools
are still present. These can disagree about what a model has done.

### 22. Claude on the web has no repo, no tests and no hooks (Read)
The plan there uses public priors only, nothing can be checked, and a cheaper model can only run through the server's
`call_model` with the deployment's keys. Cheap-first with verification does not carry over. The Python path was not run
end to end in this session.

## F. What keeps it from being a near-perfect router

The items above are why routing misbehaves today. These are why it could not be near-perfect even after they are fixed.

### 23. The router only sees the ladder it ran (Read; Inferred)
If a cheap model passes the check, nothing says whether an even cheaper one would have; if it fails, the only lesson
is "this one failed here". The data is censored by the choices made. A near-perfect router needs to know, for each task,
the cheapest model that would have passed. That takes deliberate counterfactual runs (for example shadow runs on a
sample), with a stated cost and a measured regret. No replay of real traffic against a labelled "best model" exists in
what I read.

### 24. No features of the task itself (Observed)
`taskFeatures(root)` in `library.mjs` sends medians over past diffs for the whole repo or for a way. Nothing describes
the current prompt: how many files the located region spans, which languages, how large the edit is likely to be, how
well specified it is, whether tests cover that code. Without a per-task feature vector, probabilities cannot differ
between a rename and a redesign (see item 2), however much data arrives.

### 25. The check's strength is assumed, not measured per task (Read)
Routing uses one prior per check kind (`tests`: about 20% false accept). The real strength depends on whether any test
executes the changed lines. A pass from a suite that never touches the edited code is close to no evidence, but is
modelled the same as a pass from a covering test. Near-perfect needs a per-task coverage or impact signal, and a
stronger acceptance route (independent review, extra tests) when coverage is thin.

### 26. The reviewer is the session's own model family (Observed)
Accepted answers carry "strong review" from `claude-sonnet-5-5` (`dispatch: ... accepted by the rule (E Rs)`). A reviewer
from the same family as the model being replaced shares its blind spots, and every accepted answer costs a Sonnet call.
Independence and cost both argue against it as the main safety net.

### 27. Wall-clock time is not in the objective (Read; Inferred)
The ladder optimises success, wrong-delivery and dollar cost. Dispatch has a 600 s limit for all cheap tries together,
and tries run before the session model starts. A ladder that saves cents and costs minutes on every failure is a loss
for the person waiting. There is no latency budget in the plan, and no measure of time-to-correct-answer by route.

### 28. Hand-off loses context (Read)
The session model sees the whole conversation. A cheap model gets the prompt, up to 12000 characters, plus about 4 KB of
repository context and some earlier turns (`brief.mjs`, `task_context.mjs`). Prompts that lean on earlier decisions or
on files nobody named will fail for lack of context, not capability. I did not read what the session model is told
about a cheap attempt that failed, so I do not know if escalation starts from scratch.

### 29. Decisions are made once per prompt (Read)
The hook decides at the start of a prompt. `ladder.py` supports step-level routing, but the hook uses the task-level
plan. A long task whose first half is easy and second half is hard cannot be re-routed part-way, and follow-up prompts
on the same task each start a new decision with partial state.

### 30. Cheap executors may lack what the task needs (Inferred)
Triage says `work=change`, not what the change needs. A task that needs a running service, network access, a browser or
a database cannot be done in an isolated worktree. Nothing screens for that before a cheap attempt is spent.

### 31. Knowledge from outside can steer the models (Read)
Library entries and the ways pulled from the shared commons are injected into prompts. `submit_way` entries are screened
by an automated check and "never verified on submission". Local library entries are also injected as text. Both reach
the session model and the cheap executors, which can write files. CLAUDE.md already says to treat this as untrusted
data, but nothing in the injected text separates instructions from facts.

### 32. Several sessions share one set of files (Observed)
On 2026-10-28 other sessions' lines interleaved with mine in `~/.stealthlab/hooks.log`. `.kell/routing.md`,
`.kell/library.md` and `provider_quota.json` are shared by every session on the machine. Concurrent writers can lose
counts or show a quota another session already spent.

### 33. No per-decision record (Observed)
`hooks.log` is free text, and `dispatch_costs.jsonl` held only review costs. There is no row per prompt tying together
triage verdict, plan, ladder, what ran, check result, cost, time and the final outcome. Without it, the router's precision
(how often cheap-first was right), regret and savings cannot be computed, so "near-perfect" cannot be demonstrated.

## What was not examined
The routing store (`store.py`), the bundled priors (`prior_bundle.py`), `detect_check.mjs`, `locate.mjs`, the executor
runtime under `lib/exec/`, and the real Neon database (over quota during the session). Nothing here was measured on
more than a handful of tasks.

## Review, 2026-10-28 (AnujB's session): which items are real

Each item above was checked against the code on main and, where possible, against the A/B replays run the same day
(replay-2/3: gpt-oss-120b and glm-5.3-flash on 6 jsonargparse issues, graded on the hidden tests). **Status** says what
is true now; "fixed locally" means the change exists in AnujB's working tree, is tested, and is not pushed yet.

| # | Verdict | Evidence / status |
|---|---------|-------------------|
| 1 | A local setting, not a product bug | `dispatchCheck` (dispatch.mjs) does prefer `STEALTHLAB_DISPATCH_CHECK` over everything; nothing in the repo sets it. Scoping an override is still worth doing. |
| 2 | Real (design) | Agreed: no up-front difficulty signal. |
| 3 | Real | Seen in the series: sqlglot-4067's wrong cheap answer was accepted by the Sonnet reviewer. The reviewer now sees the changed functions and a stricter prompt (fixed locally): wrong accepted 2/7 -> 1/7 on 15 graded answers, right still 7/8 (small sample; recorded as a deviation in docs/acceptance_rules_preregistered_2026-10-16.md). |
| 4, 5, 7 | Real (design) | Agreed. 5's setting is gone from this repo. |
| 6 | Real, and confirmed by measurement | cache-full-2, 9 common issues: Opus alone 8/9 for $4.43; kell 7/9 for $5.78. Cheap models solved 1/9. No saving is established yet. Part of kell's extra cost was the Opus fallback following a wrong "likely place" pointer (+$0.22-0.35 on 508/518/531); the hand-off now passes the pointer only when strong evidence backs it (fixed locally). |
| 8 | Real (code) | `recordLibraryEntry` (capture_hook.mjs) never looks at the triage verdict. Open. |
| 9 | Real (code) | `diffFromGit` (library.mjs) takes the whole working tree against HEAD plus every untracked file, not the session's edits. Open. |
| 10, 11 | Real | Open. |
| 12 | Real | Seen again today on a fresh local install: readiness reports "token MISSING", outcomes are not reported. |
| 13 | Real (code) | The session hook defaults to `STEALTHLAB_HOOK_MODE=full`, which injects unjudged ("matched by words only") library matches with file lists and diffs. Open. |
| 14, 15 | Real | Open. |
| 16 | Real, and now longer | The cheap tries' limit is 900 s by default (raised from 600 s; fixed locally): GLM had found the root cause of jsonargparse-560 and ran out one edit short, and 2 of the survey's 16 cheap solves took longer than 600 s. The prompt waits that long before Opus takes over. Needs a non-blocking design or a visible progress line. |
| 17 | Real, fixed locally | `PROVIDER_LIMIT` (exec/runtime.mjs) excused only 429 and out-of-credit replies; a 5xx or a provider_error counted as the model failing. It now excuses 5xx and provider_error too. |
| 18, 19, 21, 22 | Real | Open. |
| 20 | Real, seen again | Today the local scratch Postgres stopped accepting connections ("could not reserve shared memory region", a Windows Postgres fault after days of uptime) and the MCP server failed to start; nothing told the user. The console page showed "MCP CLOSED (unreachable (Invalid URL))" until a server URL was saved. |

### New problems found the same day

- **Benchmark contamination (harness).** In Claude Code, GLM-5.3-flash ran `pip download jsonargparse==<later version>` on most tries and copied the upstream fix (replay-2g: 11 of 15 Claude Code tries fetched; most of its "solves" were these). The step agent blocks installs; Claude Code did not. The series transcripts (arms A and C) were scanned: none fetched. Fixed in the harness: no package index for any arm's agents, no network for replay agents, and the peek detector flags fetches.
- **Cost accounting ignored cached tokens.** z.ai bills cached input at a fifth (GLM-5.3-flash $0.15 vs $0.03 per M) and agents resend their whole conversation every turn, so GLM costs were overstated about 3x. Fixed locally (`cached_input_per_mtok`, one `costOf` for every call). General Compute reports no cached tokens.
- **gpt-oss under Claude Code dies on "Failed to parse tool call from GPT OSS output"** (General Compute, HTTP 400 provider_error, always at the same character). Retrying with sampling variation (fixed locally) did not cure it; still open, under investigation. It is no longer counted against the model (#17).
- **Early-stop rules cut models short.** Rule e (48 calls with no edit) stopped all 10 of GLM's step-agent tries at 2-6 minutes of 10; the step budget was 80 calls (GLM's first edit came at call 69 on a solved issue). Fixed locally: e counts only repeated calls (24) with a 144-call ceiling, the budget is 200 calls, and the replays now run with no early-stop rules so they can be judged on the transcripts (`experiments/abtest/stall_audit.mjs`).
- **Throttling measured as model failure.** Six agents on one General Compute key took 12-21 s per call and timed out with no edit. The replay now runs at most 2 per key, as the product does.
- **Locate pointed at the wrong file on 4 of 6 jsonargparse issues.** On 87 real issues from 24 repositories: top-1 32% -> 38%, top-3 49% -> 59% after matching quoted error messages to where they are raised, traceback frames, and a bug-fix-history prior (fixed locally).
- **Console/setup gaps.** `console/scripts/start.mjs` starts the MCP with `backend/scripts/start_mcp_local.ps1`, which does not exist; the console page still says prompts are sent "up to 1,500 characters" (now 4,000, see #19).

### Where this leaves routing
Cheap models do not yet save money on real issues. With the fair settings (20 minutes, no early-stop rules, no network)
the clean replay so far is: gpt-oss solves 531 and 518 and fails 508 and 527; GLM's step agent solves none of 508, 518 and
527 in 20 minutes; GLM under Claude Code solves 518. At today's prices the break-even is roughly 45% of issues solved by
a cheap model. Next: finish the clean replay, then rerun the full series (kell vs Opus) with the settings it picks.
