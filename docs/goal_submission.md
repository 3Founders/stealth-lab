# Goal submission: a person submits a goal and the Way to do it

Owner's design, 2026-11-12. No website yet: an HTTP endpoint, an MCP tool and a CLI.

## What a person submits

- A **goal** ("ML research"). An existing goal with the same identity is reused, never duplicated.
- An ordered list of **steps**. Each step is one of:
  - **human**: a person acts. Needs a `description` ("search 'xyz' on abc.com and copy the top 5 links").
  - **automated**: kell acts. Needs a `prompt` to kell, a `locator` (a script, a website, a file, or a tool), or both.
- The **mode** is worked out from the steps and shown before submitting, never asked for:
  **Fully Human** (all human), **Human+AI** (both), **Fully AI** (all automated).

## Granularity: every step is its own goal and Way

Each step is stored as its own goal with a one-step Way. The parent goal gets a Way whose steps point at those
child Ways (`subprocedure_ref`, the existing composition contract). A step whose goal already exists attaches to it
as one more Way. So a step approved once can be reused inside other Ways.

## Review and approval

1. **Checks** (deterministic, per step): the required fields are there, the locator has a known shape and a website is
   reachable, the injection scanner finds nothing, a script is never run (it stays unscreened).
2. **AI reviewer** (per step): DeepSeek V4.1 Flash (`deepseek-ai/DeepSeek-V4.1-Flash-1017` on DeepInfra), GLM-5.3 Flash
   when it cannot answer. It judges whether the step is clear enough to act on and moves the goal forward, and gives
   the reason for a rejection.
3. A step that passes both is **ai_approved**: its Way goes active and into the rankings, labelled "AI approved". A
   person on our side can later promote it to **approved** (`scripts/review_procedures.py`) or reject it.
4. The parent Way is ai_approved only when **every** step is. Until then it and its steps stay out of retrieval
   (quarantined, private to the submitter), who can see each step's state.
5. A rejected step: the submitter gets an **email** naming the step and the reason. They fix that step and resubmit;
   steps already approved stay approved.

## Running an approved Way

Automated steps run (prompt to the model, a website locator is fetched as untrusted context; a script locator is not
run until screened). At a human step the run **pauses** with the step's description and waits for the person to mark
it done (with an optional note), then continues.

## Status

| id | piece | owner | state |
|---|---|---|---|
| GS-1 | schema: submissions, steps, runs; `ai_approved` tier (db/204) | Anuj's agent | in progress |
| GS-2 | submit: validate, mode, goals + Ways per step, parent Way | Anuj's agent | in progress |
| GS-3 | review: checks + AI reviewer, activation, rejection | Anuj's agent | in progress |
| GS-4 | rejection email (Resend) | Anuj's agent | in progress |
| GS-5 | runs that pause at human steps | Anuj's agent | in progress |
| GS-6 | surfaces: HTTP, MCP `submit_goal`, CLI | Anuj's agent | in progress |

Files: `backend/app/submissions/*`, `backend/db/204_goal_submissions.sql`, `backend/scripts/submissions.py`,
`backend/app/mcp_server/submission_routes.py`, the new tools in `server.py`. The router's label for `ai_approved`
touches `goal_router.py` in one place.
