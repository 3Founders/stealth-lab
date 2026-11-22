# Execution: what actually runs a step

**The path from "someone presses Run" to bytes on disk, and the defects found by running it. 2026-11-23.** Companion
to `pricing_discussions.md` (why any of it is priced) and `docs/steps_sandboxes_filesystems.md` (the sandbox research
this implements).

---

## 0. The chain

```
POST /v1/goals/submissions/{id}/runs
  -> submissions/api.py::start_run
       resolves the submission -> parent procedure row
  -> execution/way_runs.py::start
       licence gate (procedure_billing.check_entitlement)  <- a paid Way cannot run unbought
       bind_purchase (one purchase, one run)
       plan = expand(way)          each sub-Way replaced by its own steps
  -> advance() loop, one step at a time:
       plan_step()        stored step -> {kind, title, prompt, locator, produces, imports}
       _run_by_kell()
         needs: none/fetch/prompt -> call_unit(CallRequest(prompt, max_tokens=budget(step)))
         kind: tool               -> _run_tool (MCP, bounded rounds)
         kind: script             -> submissions/sandbox.run_script(..., produces, imports)
         kind: human              -> pause, wait for a person
       output appended to run["outputs"]; _collect_artifacts writes any declared file
       _save() persists outputs jsonb
  -> view() exposes artifacts + download_all
```

A run's state lives in `way_runs` (plan, outputs, status, next_step). A **step's** artifacts hang off its own entry
in `outputs`, so they survive a page reload without a second table.

---

## 1. The four kinds of step

| kind | what runs | filesystem | cost driver |
|---|---|---|---|
| `prompt` | one completion, no tools | **none** | input tokens (incl. re-read) + output tokens |
| `fetch` | fetch a URL, then a completion | none | the page's tokens + output |
| `tool` | MCP tool-calling loop | none | tokens + **locator cost per tool call** |
| `script` | sandbox (Agent37 default, Modal optional) | `/out` | sandbox seconds + tokens |

**The important line: a `prompt` step has no filesystem.** It is one completion. That is why a step whose prompt says
"write this into literaturereview.md" used to produce a paragraph *describing* a file — there was no
`literaturereview.md` to write to.

---

## 2. What a step declares

```jsonc
{
  "order": 3,
  "title": "Turn the review into LaTeX",
  "prompt": "Read literaturereview.md and write main.tex ...",
  "imports": ["literaturereview.md"],   // files it READS, from earlier steps
  "produces": ["main.tex"],             // files it WRITES (db/222_step_produces.sql)
  "output_cap_tokens": 6000,            // its budget; also the estimate's upper bound
  "needs": "none"                       // none | process | network
}
```

### 2.1 `imports`, and why a missing one is a refusal

kell resolves each imported name against what earlier steps **actually produced**
(`way_runs.imported_bodies`). If nothing produced it:

> *"step 3 needs refs.bib, which no earlier step produced. Name the file on the step that writes it, or drop it from
> this step's imports."*

The alternative — handing the step an empty string — produces a plausible document written from nothing. That is the
worst available outcome, because it looks like success.

### 2.2 `produces`, and why a truncated body is refused

A step's text becomes the file's body. But `max_tokens` cuts mid-sentence, and a `.tex` cut in half does not compile.
`artifacts.looks_truncated()` refuses a body that:

- ends inside an unclosed code fence (the reply was cut off), or
- used ≥95% of its token cap (probably cut off), or
- is empty.

The step still succeeds; the file is reported as **not kept, with the reason**, in the same place a stored file is
shown. Losing a finished step over a truncated document would be the worse outcome.

---

## 3. The two defects that were silently corrupting documents

Both were live in production until this session. Neither raised an error; both produced plausible output.

### 3.1 Every earlier output was cut to 4,000 characters

```python
MAX_OUTPUT_IN_CONTEXT = 4000      # way_runs.py
```

A step asked for "1000–1500 words" (~7,000 characters); the next step received 4,000 and wrote from the cut. The run
reported success. **An invisible corruption of the input is worse than a crash**, because nothing downstream can tell.

Fixed in `execution/step_budget.py`: a file-producing step gets 240,000 characters of context; a prose step keeps
4,000 so existing Ways are unchanged. **A file the step imports is never cut** — it is the input, not a summary.

### 3.2 One step's reply was capped at 2,500 tokens

~1,900 words. A literature review, a paper section, or a `.tex` does not fit. And the truncation guard would then
refuse the file. Fixed by `step_budget.output_cap_tokens(step)`.

### 3.3 The second `_context`

There are **two** `_context` functions — `way_runs.py` and `submissions/runs.py` — with different limits. Fixing the
first left the second silently truncating, and it had **no per-output cap at all** (just a blunt tail cut). Both now
take the step and use the same budget. This was found by running, not by tests.

---

## 4. The sandbox

### 4.1 Provider facts (researched 2026-11-23)

| Fact | Consequence |
|---|---|
| Agent37 **disk persists** across stop/start/restart | a workspace *could* outlive a step |
| `agent37-base` has **no `/v1/files` endpoints** (only `scp` over SSH) | cannot upload/read over the API on the base template |
| Agent templates (`agent37-hermes`) expose `GET/PUT /v1/files` | a full file API, but requires switching template |
| `exec` stdout/stderr capped at **512 KB**; request body **1 MB** | base64-over-stdout is a small-file fallback only |
| Mounting S3/R2 needs **`type: vm`** + rclone | not a default-instance option |
| SSH/scp works on **every** template | the universal path, if needed |
| Modal has `filesystem.write_text`, `snapshot_directory`, `mount_image` | the better native API |

### 4.2 The decision: kell owns the workspace; the sandbox is rented

**The workspace lives in object storage. The sandbox is compute.**

- Before a step runs, kell writes its `imports` into the sandbox.
- After it runs, kell reads back its `produces` and stores them.
- Transport on Agent37: a chunked base64 `printf | base64 -d` prefix in the same `exec`, under the 1 MB body cap.
  On Modal: the native `filesystem` API.

Why not one persistent instance per run, despite the disk surviving? Because then a Way is pinned to Agent37's base
template's transport quirks, Modal and Agent37 stop being interchangeable, and the workspace dies with the instance.
Object storage works identically on both and already exists (`docs/procedure_outputs.md`).

**Isolation is per step, not per run.** One sandbox per step means two steps never see each other's files except the
ones explicitly imported. That is a feature.

---

## 5. Rooms

A Room keeps files in Postgres (`room_files`, `storage='db'`); a run keeps them in object storage. They are different
stores, so the bridge is explicit, not a merge:

`room_files.adopt_way_outputs(pool, room_id, uploader, run_id, files, store)` reads the run's bytes from the bucket
and hands them to the room's **own upload path** — so the room keeps every safety rule it has: the type is sniffed
from the bytes, programs are refused, size is capped, expiry is set. A Way's author is a stranger too, so the room's
rules are not relaxed for them. One refused file never loses the rest.

---

## 6. Bugs found by running, not by tests

Three, and they are the argument for the end-to-end run existing at all.

### 6.1 The reversed `ObjectStore.put` unpack

```python
locator, sha = await store.put(...)     # WRONG
sha, locator = await store.put(...)     # every store returns (sha256, locator)
```

The object key became a bare hex digest. Every file was written and **every later download failed** with
"object not found" on a file that existed. It passed 31 tests because the test's fake store had the *same* reversal.
Both fixed; a test now pins the order and asserts the key resolves.

### 6.2 The second `_context` (§3.3)

Surfaced as `TypeError: _context() takes 1 positional argument but 2 were given` on step 2 of a real two-step run.

### 6.3 A test asserting the wrong property

A filename test asserted `safe_name("<script>alert(1)</script>.md") == "script-alert-1-script.md"`. The real
behaviour is `script.md`, because `</script>` contains a `/`, so `basename` keeps only the last segment. The *code*
was right and safe; the test used a magic string. It now asserts the security property (no `<`, `>`, or `"` reaches a
header) rather than one exact output.

---

## 7. The verified end-to-end run

Real model calls, real executor, local object store (R2 credentials absent):

```
step 1  cap=8000   literature.md   849 B   ← written by the model, stored, read back
        ↓ imported into step 2's prompt
step 2  cap=8000   main.tex        906 B   ← \documentclass{article}\begin{document}…
run artifacts: ['literature.md', 'main.tex']
zip holds:     ['way-output/literature.md', 'way-output/main.tex']
```

Suite: **6207 passed**, 16 pre-existing failures (`route_goal` tool classification, auth pins, provider rungs — none
touching this work). Website and desktop `tsc` clean.

---

## 8. What execution does not yet do

| Gap | Consequence |
|---|---|
| **No `run_id`/`step_order` in `llm_spend`** | a run's cost is unattributable — the whole measurement layer is blocked |
| **No cost on `way_runs`** | nowhere to put the measured total |
| **`needs: process` routing for a prompt step** | a prompt step cannot yet be given a sandbox even if it declares one |
| **`n > 1` tool rounds are budgeted by a constant** | a long tool loop is bounded by rounds, not by cost |
| **No per-run spend ceiling** | a runaway run is bounded by step count only |
| **`produces` is set on no published step** | the machinery is live; no Way declares a file yet |

---

## 9. One sentence

**A prompt step is a completion, a script step is rented compute, and everything a step reads or writes moves through
kell's own object storage — so the executor's job is to size each step's budget to what it produces, refuse to
silently pass a truncated document on, and keep the evidence that a run happened.**
