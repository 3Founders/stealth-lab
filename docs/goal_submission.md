# Goal submission: a person submits a goal and the Way to do it

Owner's design (Anuj, 2026-11-12). **No website or desktop UI yet** — the owner will decide the interface later. Build
only against the backend described here; the surfaces are an HTTP endpoint, MCP tools and a CLI.

## If you are an agent: start here

1. Pull, then read this whole file. The code is in `backend/app/submissions/` (each module's docstring is its contract),
   the schema in `backend/db/204_goal_submissions.sql`.
2. Claim a row in the status table at the bottom and push the claim **before** writing code (two agents work here).
3. Test on a scratch database only, never production. Model calls go to DeepInfra with `DEEPINFRA_KEY`.
4. Do not change the review ladder or the retrieval rule below without the owner.

## The idea in one paragraph

Anyone can contribute a way to achieve a goal. They give the goal ("ML research") and its steps. A step is either done
by a **person** ("search 'xyz' on abc.com and copy the top 5 links") or **automated** (a prompt to kell, a locator to a
script / website / file / tool, or both). Every step is also stored as **its own goal with its own one-step Way**, so
kell grows granular, reusable pieces. An AI reviewer approves or rejects **each step**; approved Ways enter retrieval
and the rankings marked **"AI approved"**; a rejected step is emailed back to the submitter with the reason, and they
fix just that step.

## What a person submits

```json
{"goal": "ML research",
 "description": "find and summarise recent papers on a topic",
 "steps": [
   {"kind": "human", "description": "Search 'diffusion transformers' on arxiv.org and copy the top 5 links"},
   {"kind": "automated", "title": "Summarise the collected papers",
    "prompt": "For each paper link from the previous step, write its method and main result in two lines",
    "locator": "https://..."}
 ]}
```

- **goal** required. An existing public goal of the same name is reused, never duplicated.
- **human step**: `description` required; no prompt or locator.
- **automated step**: a `prompt`, a `locator`, or both (one is required). A locator is a string (classified as
  `url` / `file` / `script` / `tool`) or `{"type", "value"}`.
- `kind` may be left out: description only → human; prompt or locator → automated. `title` defaults to the first
  sentence.
- **Mode is never asked for.** It is worked out from the steps and shown before submitting
  ("This will be submitted as Human+AI"): **Fully Human** (all human), **Human+AI** (both), **Fully AI** (all automated).
  `intake.preview()` returns that message.

## How it is stored (granularity)

- Each step → its own goal (existing public one by name, else a new goal **private** to the submitter) and a one-step
  Way written by the real writer `capture_procedure`. A human step's Way is bound to `{"kind": "runtime", "runtime":
  "human"}`, so the general plan executor fails loudly on it instead of letting a model stand in for the person.
- While under review, every step Way is `approval_status='submitted'`, `availability='quarantined'`,
  `visibility='private'`. **Retrieval only reads active Ways, so nothing unapproved is ever retrieved.**
- When every step has passed, the **parent Way** is written on the submission's goal: one step per child Way, pointing at
  it with `subprocedure_ref {procedure_id, version}` (the existing composition contract), with
  `domain_payload.mode_label` = Fully Human / Human+AI / Fully AI.
- Tables (db/204): `goal_submissions` (who, goal, mode, status, parent Way), `goal_submission_steps` (each step, its
  goal + Way, status, the reason, the checks and verdict, revision), `goal_submission_runs`.

## Review and approval

The review ladder for Ways is now **submitted < proposed < ai_approved < approved** (rejected is never shown or run);
db/204 adds `ai_approved`.

1. **Checks**, per step, no model: no injection shapes, no credentials in the text; a website locator must be a public
   http(s) address that answers (every redirect screened); a tool locator must be a tool kell knows; a script is never
   run (stored, unscreened). A failed check rejects the step with that reason.
2. **AI reviewer**: DeepSeek V4.1 Flash (`deepseek-ai/DeepSeek-V4.1-Flash-1017` on DeepInfra), GLM-5.3 Flash when it
   cannot answer. One call sees the goal and every step; returns approve/reject + a one-sentence reason per step. It
   judges: would the doer know exactly what to do, is it safe, does it move the goal forward. If no model answers,
   nothing is decided: the steps stay `reviewing` and `review_pending()` retries.
3. **A passed step** → `ai_approved`: its Way goes active + public (into retrieval and the rankings, labelled
   "AI approved"); a goal the submission created is published.
4. **A rejected step** stays out of retrieval with its reason; the submission is `needs_changes` and the submitter is
   **emailed** (Resend; From = `SUBMISSION_NOTIFY_FROM` or `CONTACT_NOTIFY_FROM`; To = the account's `users.email`).
   The email names each rejected step and why, and says the approved steps stay approved.
5. **Fixing**: the submitter replaces only the rejected step (`store.replace_step`); its old Way is retired, the new
   one reviewed. Approved steps are untouched.
6. **All steps passed** → the parent Way is written as `ai_approved`; the submission is `ai_approved`.
7. **A person on our side** can overrule any step (`review.decide`): `approved` (above AI approval), or rejected with a
   reason (the step and the parent Way leave retrieval; the submitter is emailed).

Example from the scratch run (2026-11-12): step 2 first pointed at the general arXiv listing and was rejected —
*"The locator points to the general recent cs.LG listing rather than the 5 links from step 1 … point it at the
collected links"*; after the fix both steps and the parent Way were AI approved.

## Running an approved Way (pauses at human steps)

`runs.start(submission_id, input)` runs automated steps in order. At a **human** step the run is `waiting_human` and
returns `waiting_on {order, title, description}`. `runs.finish_human_step(run_id, note)` records what the person did
(the note is passed to later steps) and continues. An automated step: the prompt goes to DeepSeek V4.1 Flash with the
input and earlier outputs; a website locator is fetched as untrusted data. **A tool or script locator is not run from a
submitted Way yet** — the run fails at that step and says so (open item GS-7).

## Surfaces (all call `app/submissions/api.py`)

**HTTP** (MCP server, `app/mcp_server/submission_routes.py`; signed-in callers, each sees only their own):

| call | body | answer |
|---|---|---|
| `POST /v1/goals/submissions/preview` | `{goal, description, steps}` | `{mode, mode_label, message, steps}`; stores nothing |
| `POST /v1/goals/submissions` | `{goal, description, steps}` | the submission, reviewed (waits up to 90 s; then still `reviewing`) |
| `GET /v1/goals/submissions` | | `{submissions: [...]}` |
| `GET /v1/goals/submissions/{id}` | | `{id, goal_name, mode, mode_label, status, parent_procedure_id, steps: [{order, kind, title, description, prompt, locator, status, reason, reviewer, revision}]}` |
| `POST /v1/goals/submissions/{id}/steps/{n}` | one step | fixes step n, reviewed again |
| `POST /v1/goals/submissions/{id}/runs` | `{input}` | a run: `{id, status, next_step, waiting_on, outputs, error}` |
| `GET /v1/goals/runs/{run_id}` | | the run |
| `POST /v1/goals/runs/{run_id}/done` | `{note}` | the person did the waiting step; the run continues |

Errors: 400 with the sentence to show the person, 404 when it is not the caller's, 401 when not signed in.

**MCP** (in the v1 surface): `submit_goal(goal, steps, description, preview)`; `submit_goal(submission_id)` for the
state; `submit_goal(submission_id, fix_step=n, step={...})` to fix a step; `run_way(submission_id, input)`,
`run_way(run_id, done=true, note=...)`, `run_way(run_id)`.

**CLI** (`backend/scripts/submissions.py`, for us): `preview`, `submit --owner`, `list [--status]`, `show`,
`review <id> | --pending`, `approve <id> <step> --by`, `reject <id> <step> --by --reason`. Pass `--dsn` for a scratch
database; without it the script reads `backend/.env`, which is production.

Limits: the `submit_way` rate limits (10 an hour and 30 a day per person, 300 an hour overall) apply to submits and
fixes, since each costs a reviewer call.

Tests: `tests/test_goal_submission_offline.py` (no database) and `tests/test_goal_submission_e2e.py` (a database with
db/204; the reviewer, email and run model faked).

## Overlap with `submit_way`

The older MCP tool `submit_way` (free-form steps, one automated screen, no per-step goals) is untouched. Whether to
retire it in favour of `submit_goal` is the owner's decision.

## Status

| id | piece | owner | state |
|---|---|---|---|
| GS-1 | schema: submissions, steps, runs; `ai_approved` tier (db/204) | Anuj's agent | done (scratch) |
| GS-2 | intake + store: validate, mode, a goal + Way per step | Anuj's agent | done (scratch) |
| GS-3 | review: checks + AI reviewer, publish / reject, parent Way, person overrule | Anuj's agent | done (scratch) |
| GS-4 | rejection email (Resend) | Anuj's agent | built; not sent for real yet |
| GS-5 | runs that pause at human steps | Anuj's agent | done (scratch) |
| GS-6 | surfaces: HTTP, MCP `submit_goal` + `run_way`, CLI `scripts/submissions.py`, tests | Anuj's agent | done (scratch) |
| GS-7 | run tool and script locators from a submitted Way (through the gate / after screening) | open | not started |
| GS-8 | router label: show "AI approved" for `ai_approved` Ways in `goal_router.py` / route view | open | not started |
| GS-9 | website / desktop UI | owner decides later | do not start |

Not in production: db/204 is not applied and nothing is deployed. That needs the owner's go.
