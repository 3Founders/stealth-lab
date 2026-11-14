# Way executor

Status: built 2026-11-14 (`app/execution/way_runs.py`, `db/206_way_runs.sql`, the MCP tool `run_way`). Not deployed.

A person's prompt -> `find_ways` retrieves the goal and its Way -> `run_way` loads that Way into a run and executes its
steps in order, feeding every earlier step's output into the next.

## The flow (session mode, the default)

```
run_way(way_id=<procedure_id or row id>, input=<the task>)
  -> {notice, status, steps: [{n, title, by}], your_turn | waiting_on | result | error}
```

1. **Load.** The Way's steps are copied into the run's `plan`. A step that is a sub-Way (`subprocedure_ref`) is replaced
   by that Way's own steps (at most 3 deep). A cycle, or a sub-Way that is not active and runnable, refuses the run.
   A Way someone edits mid-run cannot change a run already going.
2. **Notice.** The session tells the person, word for word:
   - no human step: **"kell will autonomously handle it, you can leave this running"** (plus "a tool may ask you to
     sign in once" when there is a tool step);
   - any human step: **"This will require your inputs at some steps along the way, stay tuned"**.
3. **Steps.** kell runs every step it can by itself and stops at the first one the session or a person must do:

| Step | Who | How |
|---|---|---|
| prompt | the session's model | `your_turn.do` + `earlier` (input and every earlier output) -> `run_way(run_id, output=...)` |
| prompt pinned to a model | the session, through `call_model(model=X)` | strict: a refusal fails the step; not strict: the session may do it, saying so |
| website / file, no prompt | kell | the page text is the output |
| website / file + prompt | the session | the page is in `your_turn.page`, wrapped as untrusted data |
| tool | the session, through kell's gateway | `discover_tools(server)` then `use_tool`; never the same work another way. A sign-in pauses |
| script | kell's sandbox | the run so far on stdin; no model ever stands in |
| human | the person | `waiting_on` -> `run_way(run_id, done=true, note=...)` |

4. **End.** `done` -> `result` is the last output. `failed` -> `error`, and the session asks before trying the next Way
   find_ways listed. `run_way(run_id, cancel=true)` stops a run; `run_way(run_id)` shows it.

Every turn tells the session to ask the person before a step sends, posts, pays, buys or deletes anything. Earlier
outputs are cut to 4000 characters each in what the next step sees; the run keeps them in full.

## Headless mode

`way_runs.start(..., mode="headless")`: no session, so kell does every automated step itself with its own model
(app/submissions/runs.py's helpers: DeepSeek V4.1 Flash, the tool-argument writer, the sandbox). For a web frontend.
A pinned model is not honoured in headless mode yet.

## Which Ways run

Active Ways, public or the caller's own. Every stored step shape maps to a kind (`way_runs.plan_step`): submitted steps
(`binding` model / tool / command / http_api / runtime human), coding Ways (`{goal, order}` -> a prompt step), sub-Way
references. `run_way(submission_id=...)` runs a submission's approved parent Way the same way. A run id from the older
submission runner (`goal_submission_runs`) still continues there.

## Wiring (2026-11-14)

- **find_ways -> run_way:** a reply that chose a Way carries `run` (`way_runs.with_run`): the `run_way` call for the
  first Way and what to do with its replies.
- **Desktop:** the delegation for a picked Way (`desktop/lib/plan.ts` `contractOf`) tells the turn to run it with
  `kell_run_way`, falling back to the older one-tool instruction only when the executor refuses the Way.
- **Outcomes:** a done or failed run is recorded on the Way through `procedures.record_execution_outcome` as a self-report
  (`execution_verified=False`): evidence ranking reads, never a promotion or a quarantine by itself. Cancelled runs say
  nothing.
- **One runner:** the website's submission run routes start headless runs here; a run id from the older runner
  (`goal_submission_runs`) still continues there. Stored actors are `human` / `ai` / `session`.
- **Headless pinned models:** a step pinned to a model runs on it through kell's providers; strict fails when it cannot,
  otherwise kell's own model does it.
- **Thin coding steps:** `your_turn` carries the step's check and note and, in `part_of`, what the whole Way achieves.

## Not done yet

- Not yet exercised against production data.
- The desktop change reaches people only in the next desktop release.

Tests: `tests/test_way_runs_e2e.py` (11, on a real database).
