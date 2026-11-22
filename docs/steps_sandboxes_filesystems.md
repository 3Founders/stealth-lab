# Steps, sandboxes, and filesystems

> **Pricing and the measurement layer now live in `docs/blinkit/`** (2026-11-23). This document remains the authority
> on the sandbox research and the filesystem design; `docs/blinkit/execution.md` describes what was actually built,
> including three bugs that only a real run found.

**How a Way's step actually runs, what it can see, and where its files live. 2026-11-23.** Researched against the
Agent37 API documentation (files, exec, instance lifecycle, S3 mount, SSH) and Modal's Sandbox SDK, then checked
against `backend/app/execution/way_runs.py` and `backend/app/submissions/sandbox.py`.

This document replaces two guesses in earlier notes:

- **"Files travel back over stdout as base64."** True only for small files. `exec` caps `stdout` at **512 KB** and the
  request body at **1 MB**, so a 2 MB PDF cannot come back that way. It is a fallback, not the mechanism.
- **"A fresh sandbox per step."** That was the original design and it is wrong for a Way. A Way's steps are a
  conversation about one body of work; a step that reads `literature.md` must find it on disk.

---

## 0. The chain, as it exists

For a Way page run on kell.org.in:

```
POST /v1/goals/submissions/{id}/runs
  -> app/submissions/api.py::start_run
  -> app/execution/way_runs.py::start  (licence gate at the top)
  -> advance() loop, one step at a time:
       plan_step()  stored step -> {kind, prompt, locator, produces}
       _run_automated()
         kind == "prompt"  -> call_unit(CallRequest(prompt=..., max_tokens=2500))     TEXT ONLY
         kind == "script"  -> submissions/sandbox.run_script(...)                      SANDBOXED
         kind == "tool"    -> _run_tool(...)                                           MCP TOOLS
         kind == "fetch"   -> fetch a URL, then a model call
       output appended to run["outputs"], files collected (submissions/artifacts.py)
  -> _save() persists outputs jsonb; view() exposes `artifacts` + `download_all`
```

**A `prompt` step has no filesystem and no tools.** It is one completion. That is why "write this into
literaturereview.md" produced prose: there was no `literaturereview.md` to write to.

---

## 1. The two defects that matter more than any UI

Both are in the executor and both silently corrupt a document-producing Way.

### 1.1 Every earlier output is cut to 4,000 characters

```python
MAX_OUTPUT_IN_CONTEXT = 4000      # way_runs.py:44
```

Step 3 sees step 2 **truncated**. Step 2 is asked for "1000–1500 words" (~7,000 characters), so step 3 writes the
abstract from a cut-off review. The run does not fail; it produces a plausible document built on a fragment. That is
the worst failure mode available: invisible.

### 1.2 One step's reply is capped at 2,500 tokens

```python
CallRequest(prompt=..., max_tokens=2500)     # way_runs.py:426
```

~1,900 words. A literature review, a paper section, or a `.tex` file does not fit. And because
`artifacts.looks_truncated()` refuses a body that used its whole cap, the step would also lose its file.

**The fix, in one place:** a step's budget is a property of what it produces.

```
budget(step) = 2000                                        if the step produces no file
             = max(2000, chars_needed)                     output cap in tokens, declared or defaulted
context(step) = every earlier output, up to CONTEXT_CAP    raised from 4000 chars to a real window
```

and the declared output cap (`output_cap_tokens`) is what `max_tokens` receives, so the estimator's upper bound
(`docs/pricingestimation.md` §4.3) is the same number the model is actually allowed to use. One number, three uses:
the cap, the cost bound, and the refusal threshold.

---

## 2. What a sandbox must provide

A step needs, in order of how many Ways need it:

| Need | Who needs it | Today |
|---|---|---|
| read the text of earlier steps | every prompt step | ✅ (truncated; §1.1) |
| write a file and hand it back | any step with `produces` | ✅ for model steps |
| read a file an earlier step wrote | `literature.md -> .tex` | ❌ **missing** |
| run code that produces files | `script` steps | ✅ via stdout, ≤512 KB |
| install a package, then run it | research/analysis Ways | ❌ (fresh instance each time) |

The pattern in rows 3–5 is the same: **the workspace must outlive one step.**

---

## 3. Agent37, measured against those needs

From the API documentation (2026-11-23):

| Fact | Consequence for kell |
|---|---|
| **The disk persists** across `stop`, `start`, `restart`, `resize` — "like a VM". Only `update` resets the OS layer, keeping `/home/node`. | A workspace can carry a Way from step to step. This is the key fact. |
| `agent37-base` (our current template) has **no `/v1/files` endpoints** — only `scp` over SSH. | We cannot upload or read a file over the API on the base template. |
| Agent templates (`agent37-hermes`) expose `GET /v1/files`, `GET /v1/files/content`, `PUT /v1/files/content`, `GET /v1/files/archive`. | A full file API, but it requires switching template. |
| `exec` stdout/stderr are each capped at **512 KB**; the request body at **1 MB**. | Base64-over-stdout is a small-file fallback only. |
| Mounting an S3/R2 bucket as a folder needs **`type: vm`** and rclone. On a default instance, copy via `rclone` through exec. | R2 mounting is possible but is a VM-instance decision, not a default. |
| SSH (`scp`/`sftp`) works on **every** template, injected at runtime. | The universal file path, if we need one. |

### 3.1 The decision

**Do not switch the default template, and do not depend on `/v1/files`.** kell should keep using `agent37-base`
and stop treating the sandbox as a place files live. Instead:

**kell owns the workspace; the sandbox is a compute rental.**

- **The workspace is object storage (R2).** It already holds artifacts, it is already the documented home
  (`docs/procedure_outputs.md`), and it works on any provider. A run gets a `runs/{run_id}/` prefix.
- **Before a step runs,** kell writes the files the step needs into the sandbox: the declared `imports`, plus the
  earlier step outputs it references.
- **After a step runs,** kell reads back what it declared and stores it in R2.
- **On Agent37 with the base template,** files move through `exec` (base64, ≤512 KB each, chunked) or `scp` for
  larger ones. Both are transport, not archive.

This keeps one storage system, works identically on Modal, and does not pin kell to an agent template's API surface.

### 3.2 Modal, measured the same way

Modal has the better native filesystem API (`sb.filesystem.write_text`, `snapshot_directory`, `mount_image`), and a
sandbox can be kept alive between calls. That makes Modal the **simpler** implementation of the same model — but the
workspace still lives in R2, so switching provider never changes what a Way can do.

---

## 4. What a step declares

Extend the step record (alongside `produces`, db/222):

```jsonc
{
  "order": 3,
  "title": "Turn the review into LaTeX",
  "prompt": "Read literaturereview.md and write main.tex ...",
  "imports": ["literaturereview.md"],      // files this step READS, from earlier steps
  "produces": ["main.tex"],                // files it WRITES (db/222)
  "output_cap_tokens": 6000,               // its own budget; also the cost bound
  "needs": "process"                       // none | process | network  (docs/sandboxing.md)
}
```

- `imports` names files by the name an earlier step produced. kell resolves the name against the run's workspace
  and materialises it in the sandbox. **A name that was never produced is a refusal, not an empty file** — a step
  silently reading a missing input is how a fabricated result gets shipped.
- `needs: none` (default) runs as a model call — cheapest, no sandbox.
- `needs: process` gives the step a real sandbox with the imports on disk and the produced files read back.
- `needs: network` additionally allows egress (a fetch step).

**This is what makes `literature.md -> .tex` work:** step 2 declares `produces: literaturereview.md`; step 3
declares `imports: [literaturereview.md]` and `produces: main.tex`. kell stores the first, materialises it for the
second, stores the result.

---

## 5. Wiring, by surface

### 5.1 Backend

| Piece | Change |
|---|---|
| `way_runs._run_automated` | pass `imports` + `produces` down; route `needs: process` to the sandbox even for a prompt step |
| `way_runs._context` | raise the cap; never cut a file that a later step imports |
| `way_runs` budget | `max_tokens` from the step's `output_cap_tokens` |
| `submissions/sandbox.py` | `materialise(files)` / `collect(names)` per provider; base64 chunking for exec, `scp` above the cap |
| `submissions/artifacts.py` | `imports` resolution against the run workspace |
| `submissions/api.py` | `download_artifact` already licence-gated; add the run-workspace prefix |

### 5.2 `routing_kell_frontend`

- The step card shows **what it read and what it wrote**, not only its prose: `imports` as inputs, `produces` as the
  files it handed back.
- A refused import ("step 3 wanted literaturereview.md, which step 2 did not produce") appears as a note, in the
  same place `artifacts_refused` already does.
- The `Deliverable` block and the zip stay as written.

### 5.3 `desktop`

- `WayRunCanvas` shows the same imports/produces per step card.
- `app/api/way-artifact` already streams a licence-gated file; unchanged.

### 5.4 Rooms

A Room already has files (`services/room_files.py`). A Way run started from a Room should write its outputs into the
**Room's** workspace rather than a bare run prefix, so a Way's file is the same object the Room's next turn reads.
`room_files.py` documents that it has no signed URLs yet — the same gap `object_storage.py` has (presigned GET). Both
are fixed together or neither is.

---

## 6. Build order

| # | Piece | State |
|---|---|---|
| 1 | **Budget and context** (§1) — `app/execution/step_budget.py` | ✅ done, 15 tests |
| 2 | **`imports` + workspace resolution** — `way_runs.imported_bodies` | ✅ done, refused when nothing produced it |
| 3 | **Sandbox materialise** — Agent37 chunked exec + Modal `filesystem` | ✅ done, 5 tests (byte-exact round trip) |
| 4 | **Steps surface** — read/wrote on each card, both frontends | ✅ done, tsc clean |
| 5 | **Rooms bridge** — `room_files.adopt_way_outputs` | ✅ done, 4 tests; room rules still apply |
| 6 | **A real run** | ✅ `literature.md` (849 B) → `main.tex` (906 B), zip holds both |

Not done: **R2 credentials** (nothing stores to the real bucket), and **`produces` is not yet set on any stored
step** — the machinery is live but no published Way declares a file yet.

---

## 8. Pitfalls

1. **Cutting a file that a later step imports.** The 4,000-character cut is not a display limit; it changes the input
   to a model. `_context` now keeps an imported file whole.
2. **Two `_context` functions.** `way_runs.py` and `submissions/runs.py` each have one, with different limits. Both
   must take the step — a fix in one left the other silently truncating (found by the end-to-end run, not by tests).
3. **A reversed `ObjectStore.put` unpack.** Every store returns `(sha256, locator)`. A fake returning
   `(locator, sha)` let a reversed unpack pass 31 tests while breaking every real download.
4. **Trusting stdout for binary.** 512 KB, and base64 inflates by 4/3. Chunk it, or `scp`.
5. **`agent37-base` file endpoints.** They do not exist. Code must not assume `/v1/files`.
6. **A missing import silently empty.** Refuse and say which step should have produced it.
7. **Serving an artifact `inline`.** Agent37's own docs warn: HTML/SVG runs on your origin. Always
   `Content-Disposition: attachment` — `artifacts.content_disposition` already does.
8. **A fresh sandbox per step.** Correct for isolation, fatal for a workspace. One sandbox per run; isolation is per
   run, not per step.
9. **Pinning a Way to a provider.** The workspace is object storage; Modal and Agent37 are interchangeable transport.

---

## 9. One sentence

**The run's workspace lives in object storage and the sandbox is rented per run, so a step declares what it reads
and writes, kell moves those bytes in and out over whichever transport the provider offers, and the model's context
and token budget are sized by what the step produces rather than by one global constant.**

---

## Sources

- Agent37 files — https://www.agent37.com/docs/agents-api/files
- Agent37 exec (512 KB / 1 MB caps) — https://www.agent37.com/docs/agents-api/exec
- Agent37 instance lifecycle (disk persists) — https://www.agent37.com/docs/agents-api/instance-lifecycle
- Agent37 mount an S3 bucket — https://www.agent37.com/docs/agents-api/mount-s3
- Agent37 SSH (scp on every template) — https://www.agent37.com/docs/agents-api/ssh
- Agent37 core concepts — https://www.agent37.com/docs/agents-api/concepts
- Modal Sandbox filesystem + snapshots — https://modal.com/docs/guide/sandbox-files
