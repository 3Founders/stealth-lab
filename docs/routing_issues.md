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

## What was not examined
The routing store (`store.py`), the bundled priors (`prior_bundle.py`), `detect_check.mjs`, `locate.mjs`, the executor
runtime under `lib/exec/`, and the real Neon database (over quota during the session). Nothing here was measured on
more than a handful of tasks.
