# Goal submission: a person submits a goal and the Way to do it

Owner's design (Anuj, 2026-11-12). **A frontend is coming** (website and desktop): this is a product feature, not
only a kell tool. The backend below is complete; the frontend is built on its HTTP API (the table under Surfaces) —
the MCP tools and the CLI are other doors to the same calls. The owner decides the interface; do not start the UI
(GS-9) until they do.

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

- **goal** required. An existing public goal that is the **same goal** is reused, never duplicated (see Matching).
- **human step**: `description` required; no prompt or locator.
- **automated step**: a `prompt`, a `locator`, or both (one is required). A locator is a string (classified as
  `url` / `file` / `script` / `tool`) or `{"type", "value"}`.
- `kind` may be left out: description only → human; prompt or locator → automated. `title` defaults to the first
  sentence of the description or prompt; a step with **only a locator needs a title** saying what it achieves (it
  becomes the step's own goal, which must read as an outcome, not a path).
- A script or a file must be a **link** (a GitHub page link works; it is turned into the raw file): kell cannot open
  a path on the submitter's computer, and the review says so.
- **Mode is never asked for.** It is worked out from the steps and shown before submitting
  ("This will be submitted as Human+AI"): **Fully Human** (all human), **Human+AI** (both), **Fully AI** (all automated).
  `intake.preview()` returns that message.

## How it is stored (granularity)

- Each step → its own goal (an existing public goal judged the same, else a new goal **private** to the submitter) and a one-step
  Way written by the real writer `capture_procedure`. A human step's Way is bound to `{"kind": "runtime", "runtime":
  "human"}`, so the general plan executor fails loudly on it instead of letting a model stand in for the person.
- Global goal names are unique, so a name already held by a goal nobody has had approved yet (the submitter's or
  someone else's) is that goal: whichever Way is approved first makes it public, without the other person's
  description. A fixed step that moves to another goal retires the private goal it left, when nothing else uses it.
- While under review, every step Way is `approval_status='submitted'`, `availability='quarantined'`,
  `visibility='private'`. **Retrieval only reads active Ways, so nothing unapproved is ever retrieved.**
- When every step has passed, the **parent Way** is written on the submission's goal: one step per child Way, pointing at
  it with `subprocedure_ref {procedure_id, version}` (the existing composition contract), with
  `domain_payload.mode_label` = Fully Human / Human+AI / Fully AI.
- Tables (db/204): `goal_submissions` (who, goal, mode, status, parent Way, how the goal was matched),
  `goal_submission_steps` (each step, its goal + Way, status, the reason, the checks and verdict, revision, how its goal
  was matched), `goal_submission_runs` (incl. what the run is waiting on).

## Matching a goal to one kell already has (`app/submissions/matching.py`)

Search finds the candidates, **JEV decides** (owner, 2026-11-13: "just ask JEV if it is duplicate or not"), for the
submission's goal and for every step's goal:

1. **exact**: the same normalized name among public goals → that goal, no model call.
2. **candidates**: kell's goal identity search (`identity_resolution.generate_goal_candidates`: full text + the goal
   vectors, RRF-fused) over **public goals only** (`visibility="public"`), top 6.
3. **JEV**, three typed questions per candidate, in parallel: *is A the same goal as B, narrower, broader, related or
   different?* — name + description vs name + description; name vs name; and the reverse (candidate vs submitted).
4. **vote**: ≥ 2 of the 3 answers "same" at confidence ≥ 0.5 → **duplicate**, that goal is reused. Exactly one
   "same" → **possible duplicate**: a new goal, the candidate recorded in `goal_match.possible_duplicate` so it can be
   merged later. ≥ 2 answers "narrower"/"broader" → recorded in `goal_match.relations` (shown to the submitter; the
   links themselves come from placement, below).

Why a vote and not one answer: JEV's confidence for the same pair drifts between calls (one pair: 0.98, an hour
later 0.54–0.71), and descriptions move it, while its relation label is steady — across every test it never called
two different goals "same". Asking several pairs in one batched call also shifts the answers, so each question is its
own call (~200 ms; a whole goal ≈ 1 s).

Measured 2026-11-13 on 47 labelled pairs, run twice: **18/20 duplicates caught, 0/27 different goals merged**, the
same both runs. Live on scratch with real embeddings: 8/9 right; the miss ("Find security vulnerabilities in npm
dependencies" vs "Audit npm packages for known CVEs") JEV calls broader every time.

- **JEV only.** If it cannot answer (down, not configured, no `identity` in `JEV_CAPABILITIES`), the goal is new and
  the decision is `judge_unavailable`. A duplicate can be merged later; a wrong merge sends people to a Way that does
  something else. Production has `JEV_CAPABILITIES=applicability,identity,claim_relation,triage` (checked 2026-11-13).
- **Nothing before approval.** A match never touches the matched goal; the submitted name becomes its alias only when
  the step (or the whole Way) is approved. A new goal's links are proposed at approval, and the goal re-enters the
  reconcile sweep then, among public goals.
- **No cross-visibility merges.** The reconcile sweep (`reconcile_goals`) now only compares a goal with goals of the
  same visibility (and, for private ones, the same owner): before, a newer public goal could be merged *into* someone's
  private one and vanish from search.

Every decision, the candidates and JEV's reason are stored in `goal_match`.

### Hierarchy: special cases and abstractions (`matching.place_goal`)

Search walks only **accepted** `SPECIALIZES` edges (`docs/search.md` §2, §4), so dedup alone does not make search
better; the hierarchy does. When a goal a submission **created** is approved (it goes public), it is placed at once:

1. kell's existing placement (`ingestion_jobs.handle_goal_abstraction_placement`) gathers up to 20 goals: search hits
   for it plus their accepted parents and children, so a new goal can land under a parent the words alone miss.
2. Its judge is `matching.VotingJEV`: the same three-way JEV vote per candidate. "Narrower" from ≥ 2 answers → the new
   goal is a special case of that goal; "broader" → it is an abstraction of it.
3. Mean confidence ≥ 0.7 → an **accepted** edge (search walks it on the next query). Below that, placement asks its
   second model; with no second answer the edge stays `proposed` (stored, not walked). Placement's own rules still
   apply: no cycles, no edge between a public and a private goal, edges implied by another edge are pruned, and a goal
   with no parent is queued for the abstraction audit.

A goal matched as a duplicate is not placed (it already is). A step is a *part* of the submission's goal, not a
special case of it: nothing links them automatically; a step goal gets an edge to any goal (that one included) only
when JEV judges it a special case or an abstraction.

Placement runs in the server process (production runs no ingestion worker, so a queued placement job would wait
forever), after projecting the goal; it is best-effort with a 90 s cap, and a failure is logged, never blocks approval.

Measured 2026-11-13: 35 labelled pairs, links from ≥ 2 of 3 agreeing answers at ≥ 0.7: **21/22 right, 0 wrong**;
duplicates and merely related pairs never became links. Live on scratch: "Book a flight to Tokyo" → under "Book a
flight"; "Edit an image" → above "Remove the background from a photo"; "Deploy a Next.js app to Vercel" → under
"Deploy a web app"; "Transcribe a podcast episode" → under "Transcribe audio"; "Book a hotel" → no link. 1–16 s per
goal (the first JEV call of a quiet spell is the slow one). Locally the second model (Vertex) refused (billing off on
that Google project), so below-threshold edges stayed proposed.

## Review and approval

The review ladder for Ways is now **submitted < proposed < ai_approved < approved** (rejected is never shown or run);
db/204 adds `ai_approved`.

1. **Checks**, per step, no model: no injection shapes, no credentials in the text; a website or file link must be a
   public http(s) address that answers (every redirect screened); a tool must be one kell's gateway can run (a kell
   builtin, or a live remote server from a verified publisher); a script must be a linked .py / .sh / .js file, is
   fetched, and must hold no credentials. A failed check rejects the step with that reason.
2. **AI reviewer**: DeepSeek V4.1 Flash (`deepseek-ai/DeepSeek-V4.1-Flash-1017` on DeepInfra), GLM-5.3 Flash when it
   cannot answer. One call sees the goal and every step (and a script's source); returns approve/reject + a
   one-sentence reason per step. It judges: would the doer know exactly what to do, is it safe (a script that is
   malicious or obfuscated is rejected), does it move the goal forward. If no model answers,
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

`runs.start(submission_id, input)` runs automated steps in order, by themselves. At a **human** step the run is
`waiting_human` and returns `waiting_on {kind: "step", order, title, description}`. `runs.finish_human_step(run_id,
note)` records what the person did (the note is passed to later steps) and continues.

An automated step, by what it carries:

| step has | what runs |
|---|---|
| a prompt | DeepSeek V4.1 Flash (GLM-5.3 Flash fallback) with the input and earlier outputs |
| a website / file link | fetched (public addresses only, every redirect screened; PDFs read as text), given to the model as untrusted data; with no prompt the text is the output |
| a tool | kell's tool gateway, for the person running the Way, with every use_tool check (gate, consent, secret scan, rate limits, audit). The model picks the tool when only a server is named and writes the arguments from the tool's input schema. A tool that needs the person to sign in pauses the run with `waiting_on {kind: "signin", link}`; marking it done retries the step |
| a script | fetched and run in an isolated **Modal Sandbox** (`app/submissions/sandbox.py`: gVisor, no network, 1 CPU, 1 GB, 120 s), never on the kell server; the run so far on stdin, stdout is the output. Needs `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` on the server |

No model ever stands in for a tool, a script or a person: a step that cannot run fails the run and says why.

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

Limits: 10 submits or fixes an hour and 30 a day per person, 300 an hour overall (each costs a reviewer call).

Tests: `tests/test_goal_submission_offline.py` (no database) and `tests/test_goal_submission_e2e.py` (a database with
db/204; the reviewer, judge, email, gateway, sandbox and run model faked).

## `submit_way` is retired (2026-11-12)

The owner: no duplicate tools. The old MCP tool `submit_way` is removed; `submit_goal` replaces it.
`use_tool("submit_way")` answers with how to call `submit_goal`. The npm package's `kell library share` now drafts a
`submit_goal` call (the published npm version still drafts `submit_way` until the next npm publish). Ways already
submitted through it, and their moderation routes (`/v1/economy/procedure-submissions`), are kept.

## Status

| id | piece | owner | state |
|---|---|---|---|
| GS-1 | schema: submissions, steps, runs; `ai_approved` tier (db/204) | Anuj's agent | done (scratch) |
| GS-2 | intake + store: validate, mode, a goal + Way per step | Anuj's agent | done (scratch) |
| GS-3 | review: checks + AI reviewer, publish / reject, parent Way, person overrule | Anuj's agent | done (scratch) |
| GS-4 | rejection email (Resend) | Anuj's agent | built; not sent for real yet |
| GS-5 | runs that pause at human steps | Anuj's agent | done (scratch) |
| GS-6 | surfaces: HTTP, MCP `submit_goal` + `run_way`, CLI `scripts/submissions.py`, tests | Anuj's agent | done (scratch) |
| GS-7 | tool and script steps run by themselves (gateway; Modal sandbox) | Anuj's agent | done (scripts need Modal keys on the server) |
| GS-8 | router label: show "AI approved" for `ai_approved` Ways in `goal_router.py` / route view | — | done |
| GS-9 | website / desktop UI on the HTTP API | owner decides the interface | do not start yet |
| GS-10 | goal matching: retrieve → rerank → judge | Anuj's agent | replaced by GS-12 |
| GS-11 | retire `submit_way` | Anuj's agent | done |
| GS-12 | goal matching by JEV vote (replaces rerank + DeepSeek judge); no cross-visibility goal merges | Anuj's agent | done, not deployed |
| GS-13 | hierarchy: an approved new goal is placed by JEV vote (special case / abstraction → accepted SPECIALIZES edges) | Anuj's agent | done, not deployed |
