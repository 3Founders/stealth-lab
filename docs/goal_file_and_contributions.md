# The goal file, the run feed, and contributions (2026-10-28)

Three things that make delegation work the way the product says it does, and one that lets a person add to the library.
The unit is a **goal**, not a "task": what is to be done, how success is checked, and the context that decides how.

## 1. The goal file: context by pointer, not by prompt

`.kell/goals/<goal id>.md`, written by dispatch (`packaging/npm/lib/goal_file.mjs`) before it starts the cheap models. Lines are
`KIND|...` so `rg '^CLAIM\|' .kell/goals/<id>.md` works:

```
GOAL|<id>|<title>             OBJECTIVE|<the request>          DONE_WHEN|<the project's check, or "no check: needs review">
TEST|<the located module's own tests>                          WHERE|<file>:<start>-<end>|<symbol>
CLAIM|R-014|...|source=<file>:<line>#sha=...                   LIBRARY|<an earlier fix find_ways judged to be this goal>
```

The executor's task text is then the request, the location line and one pointer line. What made it work:

- `.kell/*` is gitignored in most repos, so a worktree made from a commit had **no `.kell` at all**. `createWorktree` now copies
  the greppable parts (`goals/`, `claims.md`, `claims/`, `procedures.md`, `run.md`, `SUMMARY.md`; never `library/solutions` or
  routing logs) read-only, and `.kell` is excluded from the run's diff so a model editing it is not "a change".
- The open-model step agent already has `read_file` and `search`, and preloads files its task names, so naming the goal file also
  puts it in the first turn.
- A hosted model has no filesystem. `call_model(..., context=<the lines it needs>)` adds those lines to `system` as reference data
  (6,000 characters, redacted). The session model copies the lines from the goal file; `excerpt()` in `goal_file.mjs` cuts them.
- `STEALTHLAB_DISPATCH_GOALFILE=off` restores the old behaviour (context inlined into the task text).

Not covered: the spec tests dispatch writes while the cheap models work do not exist yet when the file is written, so `DONE_WHEN`
names the project's check and `TEST` the module's existing tests only.

## 2. The run feed: console and rooms from one log

`packaging/npm/lib/run_events.mjs` turns a run's `events.jsonl`, `a<N>.transcript.jsonl` and `run.json` into one ordered, redacted
list of events (`state`, `tool` with the paths it touched, `final` with checks and files). The console's `/runs/stream` snapshot
carries it as `events`.

To follow a run in a room: set `KELL_ROOM=<room id>` and `KELL_ROOM_TOKEN=<a website-session token>`. Dispatch then POSTs the events
to `/v1/rooms/{id}/events` as the backend takes them (`backend/app/services/rooms.py::_validate`): `id` a UUID made from the run
event's id (a repost is a duplicate), `kind` `note` or `task_update`, `sender_kind: "agent"`, `sender_handle: "@kell-run"`, plain-text
`body` with a `Files:` line, `depth: 1`. At most 50 per request; a "slow down" rejection leaves the event for the next flush.

Limits: that route takes the person's **website-session** token and refuses an MCP token, which a local runner normally does not
have; a 401/403 stops the follower and says so in the log. An agent posting with its MCP token needs the `room_post` tool (rooms
design P5), which is not built. The local runner's own token is therefore not enough today.

## 3. Contributions: a person or an agent adds a tool, script, prompt or tip

One service, `backend/app/services/contributions.py`, two callers, so a suggestion is stored the same way whoever makes it:

| Caller | Entry | Identity |
|---|---|---|
| an agent in the person's session | MCP tool `submit(kind, title, content, why, goal_id | goal+goal_objective, ...)` | the connector token |
| the person, from the website | `POST /v1/contributions` (`backend/app/mcp_server/contribution_routes.py`) | the website-session token |

Body: `{kind: "tool" | "script" | "prompt" | "tip", title, content, why, goal_id | goal + goal_objective, procedure_id?, step_order?, repo?}`.
`suggested_by` is `user` on the website route, whatever the body says; the tool takes `user` or `agent`.

Answers: `201 {outcome: "stored", claim_id, goal, ...}`; `200 {outcome: "goal_ambiguous", candidates}` or
`{outcome: "rejected_by_screen", screen}` (nothing written); `400 {error}`; `429 {error}`; `401/403/503` as the other website routes.

Stored as a **private candidate claim** owned by the submitter and linked to the goal (`subject goal:<id>`, `predicate suggested_<kind>`),
like `report_discovery`. An automated screen rejects malicious or NSFW text and any link and fails closed. Secrets are redacted.
20 an hour and 60 a day per person. Nothing is published, verified or paid (v2).

Not built: listing your own contributions back (`GET`), editing or deleting one, publishing one to other people, and the website
form itself (the frontend owns that; the contract above is what it calls).
