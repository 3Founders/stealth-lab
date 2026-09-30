# Build prompt: StealthLab local executor layer (external agents + Claude Code subagents)

You are implementing a feature in the StealthLab repository (`github.com/3Founders/stealth-lab`, branch `main`).
Read this whole prompt before writing code. When something here conflicts with what you find in the code, the
code is the truth for *how things work today*. Stop and report the conflict in section 11(f) instead of silently
choosing.

**Your final deliverable is `packaging/npm/EXECUTOR_SUMMARY.md`,** with exactly the eight sections specified in
section 11. Read section 11 before you start, not at the end — the acceptance evidence it demands (an adapter
verification date, a before/after count, a pasted `settings.json` diff) can only be collected while you work.
This is a hard requirement, not a write-up you add afterwards.

---

## 0. Read first (in this order)

1. `CLAUDE.md`: hard rules, commit conventions, code conventions. **Breaking a hard rule invalidates the work.**
   It has **no lane-to-path table** — that is in `.scratch/build-board.md` (SHIP owns `packaging/**`, line 1278;
   commit prefixes, line 2665; claim-your-item, line 2661). Read both.
2. `packaging/npm/bin/stealthlab-mcp.mjs`, `packaging/npm/lib/{clients,config,proxy}.mjs`,
   `packaging/npm/test/*.test.mjs`: the npm installer you are extending. Note its principle: *"Nothing
   StealthLab runs on your machine … `install` writes one entry into each agent's MCP config pointing at the
   hosted endpoint."* This feature deliberately adds the **first local runtime component**, and it must stay opt-in.
3. `backend/app/mcp_server/server.py`: only to learn the **hosted** tools you will call over HTTPS:
   `recommend_models(...)` (line ~3324) and `report_model_run(...)` (line ~3393). Read their docstrings and
   argument lists exactly. **Do not change the hosted server's tool surface** (`V1_TOOLS`, line ~533).
4. `backend/app/mcp_server/prompts.py`: the `plan_and_run` prompt (the `NODE|…|check=…` line format and the
   executor contract "Do node N-3 … Reply with: result, proof, anything you learned"). Your runtime must parse
   that format.
5. `experiments/swebench/generate.py`, functions `checkout()` / `release()`: a proven git-worktree
   create/cleanup pattern (partial clone + `worktree add --detach` + forced removal). Reuse the pattern, not the code.
6. `backend/app/services/trace_redaction.py`: secret-redaction patterns. Summaries you send off-machine must be
   redacted the same way (port the patterns to JS, or shell out to nothing: a pure-JS port with the same test vectors).

## 1. What we are building, and why

Two sources of requirements (summarized; treat them as the spec):

**(A) Drive other coding agents as executors.** Claude Code or Codex is the orchestrator. It asks for a **goal**
(not a free-form prompt) to be achieved. Our local runtime picks an executor (an external agent + model) from
evidence, runs it headlessly in an isolated git worktree with a small precise task, **verifies the result with
the step's own checks independently of what the agent claims**, returns a compact result, and records the
outcome as evidence (`report_model_run`: model + scaffold + accepted). Only verified work may be applied back,
and only on explicit request. Differentiator: *other tools let one agent call another; ours verifies and learns
which one to call.*

**(B) Fit Claude Code's native subagent model.** Subagents: one tool call in, fresh context, one summary out;
`isolation: worktree` for editing; definitions in `~/.claude/agents/*.md` (YAML frontmatter: `tools`, `model`,
`mcpServers`, `isolation`, `hooks`, `maxTurns`, …); `SubagentStart`/`SubagentStop` hooks; plugin agents IGNORE
`hooks`, `mcpServers`, `permissionMode`, so those must be installed into the user's own settings. Native
subagents save **context, not quota**; quota savings come only from delegating to non-Claude executors (A).

## 2. Non-negotiable constraints

1. **The hosted server stays knowledge-only.** No execution, no repo access, no new hosted tools. Everything that
   runs code runs locally, in the new component.
2. **Opt-in.** `npx stealthlab-mcp install` keeps its current behaviour. The executor layer is enabled only by
   `npx stealthlab-mcp install --with-exec` (or `stealthlab-mcp exec enable`). `uninstall` removes every file and
   config entry it added.
3. **Never touch the user's main checkout** except through the explicit `apply_run` tool (section 4), and only
   when the run is verified and the target files are unchanged since the run started.
4. **Auto-approve flags only inside a worktree** (optionally a container, section 7). Never run an agent with
   auto-approval in the user's checkout.
5. **Terms of service:** only drive agents the user installed locally, with the user's own logins and keys.
   Never proxy, share, pool or relay sessions or keys across users or machines. Never scrape an agent's
   credentials. Document this in the README and in code comments at the adapter boundary.
6. **No invented CLI flags.** For every adapter, determine the headless invocation from the installed binary's
   own `--help` / `--version` output and its official docs, at implementation time. Record the verified flags
   and the version they were verified against in the adapter file (`VERIFIED_WITH = {version, date, source}`).
   If a flag cannot be verified, the adapter must refuse to run and say so. The flag names in section 5 are
   *starting hypotheses*, not facts.
7. **Secrets:** never log or transmit API keys, tokens, `.env` contents or full transcripts. Summaries and check
   output tails are redacted before leaving the machine or entering a result payload.
8. **Repo rules:** stay in your lane. The lane-to-path map lives in **`.scratch/build-board.md:1278`**, not in
   `CLAUDE.md` (which has no lane table and still describes `packaging/` as a pip-only package): *"Lane SHIP (owns
   `packaging/**`)"* — a recursive glob, so `packaging/npm/` is in scope. Commit prefix `ship:`
   (`.scratch/build-board.md:2665`). **Claim the SHIP lane queue slot before starting** (`- [ ] claimed @ts — name`,
   board line 2661; no slot for this work exists yet, so add one). A backend change is allowed ONLY if a hosted
   tool's contract is actually insufficient: then stop, write the needed change as a numbered question in
   `.scratch/build-board.md`, and continue with what you can. Proving tests ship in the same change. Paste test
   counts into every commit message. Rebase onto `origin/main`; never force-push. Never commit `.env`, traces or
   `node_modules`.
9. **`packaging/npm/EXECUTOR_SUMMARY.md` is a hard deliverable, not an optional write-up.** Its 8-section spec is
   section 11. Work that stops without it is incomplete, and the final chat message is not a substitute for the
   file. Section 11 ends with a completeness checklist — run it and paste the result into the summary itself.
10. **No new runtime dependencies.** `package.json` has **no `dependencies` and no `devDependencies` today**, and
    the README's tested claim is that the suite runs *"offline with no dependencies"*. That is a shipped property,
    not an accident. Every `node:` built-in you need is available. If a task seems to require a package, treat
    that as a finding: solve it with a built-in, or file it as a board question in section 11(f). Do not add one.
11. **`files` is `["bin","lib","install","README.md"]`.** New *runtime* code must therefore live under `bin/` or
    `lib/` or it will not ship. Anything under `test/` or `scripts/` is dev-only. Do **not** add
    `EXECUTOR_SUMMARY.md` to `files` — it is a review artifact, not a published file.

## 3. Architecture

```
orchestrator (Claude Code / Codex / Cursor)
   | MCP (stdio, local)
   v
   stealthlab-exec   (NEW: in packaging/npm; launched as `stealthlab-mcp exec`)
   |-- executor registry --> adapters: opencode | codex | claude | gemini | openhands | cline | fake (tests)
   |-- worktree manager  --> <repo>/.git/worktrees, run dirs under OS temp: stealth-runs/<run_id>
   |-- verifier          --> runs the step's check commands in the worktree; scope enforcement
   |-- run store         --> ~/.stealthlab/runs/<run_id>/{run.json, events.jsonl, stdout.log, stderr.log}
   '-- evidence client   --> HTTPS to the hosted MCP: recommend_models / report_model_run
                              (offline queue: ~/.stealthlab/outbox/*.json, flushed on the next call)
```

Also installed (opt-in, by `--with-exec`) for Claude Code:
- `~/.claude/agents/stealth-executor.md` (section 6.1)
- `~/.claude/agents/stealth-delegator.md` (section 6.2)
- hooks in `~/.claude/settings.json`: `SubagentStart`, `SubagentStop` -> `stealthlab-mcp hook <event>` (section 6.3)

**Two facts about the existing tree that constrain this design.** Both were read on 2026-09-27; re-verify if
you touch them.

- **Node version.** `package.json` declares `engines: { node: ">=18.17" }` and CI tests Node 18 and 22. So
  `stealthlab-exec` must run on **18.17**, or the engine must be raised as a deliberate, recorded decision
  (state it in section 11(f) and say which Node APIs you needed). Do not silently write Node-20-only APIs.
- **`~/.claude/` is currently never touched by this package.** Grepping the whole of `packaging/npm/` for
  `.claude` returns zero hits: Claude Code registration is delegated to the `claude` CLI, which owns its own
  store. Section 6 therefore introduces a **brand-new external write surface** — two agent files plus a
  `settings.json` merge — and it is the highest-consequence thing in this whole prompt. Follow the two
  existing disciplines exactly: the `.bak`-then-write-then-abort-on-unparseable pattern from
  `lib/clients.mjs:56-60`, and the `0700` dir / `0600` file modes from `lib/config.mjs:39-42`. `uninstall`
  must restore `settings.json` to semantic equality, not merely delete its own keys.

## 4. The local MCP tool surface (stealthlab-exec)

Async by design: runs take minutes and MCP clients time out. Every tool returns in < 2 s except `run_result`
with `wait_s`.

| Tool | Input | Output |
|---|---|---|
| `list_executors()` | none | `[{id, installed, version, verified_flags: bool, healthy, last_health_check, notes}]` |
| `achieve(...)` | `repo_path`, `task` (<= 2,000 chars: objective + the NODE/STEP lines + claim lines), `checks` (list of shell commands, each must exit 0), `scope` (globs the run may modify), optional `goal_id`, `procedure_id`, `step_order`, `executor`, `model`, `timeout_s` (default 900, max 3600), `race` (1 or 2, default 1), `base` ("HEAD" default; "working-tree" copies the uncommitted diff into the worktree) | `{run_id, executor, model, started_at, worktree}`, immediately |
| `run_status(run_id)` | | `{state: queued\|running\|verifying\|verified\|failed\|timed_out\|cancelled, elapsed_s, last_event, executor, model}` |
| `run_result(run_id, wait_s?)` | wait up to `wait_s` (<= 55) for a terminal state | the **result contract**, below |
| `apply_run(run_id)` | | applies the verified diff to the main checkout: refuses unless `verified`, and refuses if any target file changed since `achieve` started (compare blob hashes); returns the files applied |
| `cancel_run(run_id)` | | kills the process tree, removes the worktree |

**Result contract** (mirrors Claude's subagent contract: compact, no transcript):
```json
{"run_id": "...", "state": "verified|failed|timed_out|cancelled",
 "verified": true,
 "checks": [{"cmd": "pytest -q tests/test_x.py", "exit": 0, "seconds": 12.3, "tail": "<= 40 redacted lines"}],
 "scope_violations": ["src/other.py"],
 "summary": "<= 1,200 chars, redacted: what the executor did (its own final message, trimmed)",
 "learned": ["<= 5 short items the executor reported as surprises/fixes, redacted"],
 "diff": {"files": ["..."], "stat": "git diff --stat output", "worktree": "<path>", "patch_path": "<path to .patch>"},
 "executor": "opencode", "model": "provider/model", "attempt": 1,
 "duration_s": 312, "tokens": {"in": null, "out": null}, "cost_usd": null,
 "evidence": {"reported": true, "queued": false, "instance_key": "..."}}
```
`verified` = every check exited 0 AND there are no scope violations AND the diff is non-empty (unless the task says
"no change expected"). What the agent says about its own success never sets `verified`.

**Executor selection** (when `executor` is not given):
1. Candidates = installed, healthy, flag-verified executors x the models each can use (from config
   `~/.stealthlab/exec.json`: `{"executors": {"opencode": {"models": ["..."]}, ...}}`; no guessing).
2. Call hosted `recommend_models(candidates=["model|scaffold", ...], goal_id, procedure_id, step_order, instance_key)`
   and take the first rung. If the hosted call fails, fall back to the config's `default_order`.
3. `race=2`: run the top two rungs in separate worktrees; the first to become `verified` wins, the other is
   cancelled; **both outcomes are reported**. Enable racing automatically only when the recommender's
   interval for the top rung is wide (use the field `recommend_models` actually returns; read the code) or the
   caller asks.

**Evidence:** on every terminal state, call hosted `report_model_run(model, scaffold=<executor id>,
accepted=<verified>, instance_key, goal_id, procedure_id, step_order, check_kind="tests" if checks ran else
"self_report", latency_ms, tokens_*, cost_usd, recommendation_id, attempt_index)`. Needs the user's saved token
(`stealthlab-mcp login`). If offline, not logged in, or the hosted call errors, write to the outbox and flush later.
**Known production gap:** the hosted routing tables may not exist yet (pending migrations). Treat a
server-side "undefined table" style error as "queue and retry later", never as a failed run.

## 5. Executor adapters

One file per adapter under `packaging/npm/lib/executors/`, same interface:
```js
export default {
  id: "opencode",
  detect(): Promise<{installed, version}>,
  VERIFIED_WITH: {version: "x.y.z", date: "YYYY-MM-DD", source: "--help output / docs URL"},
  buildCommand({task, model, worktree, timeoutS}): {cmd, args, env, stdinText?},
  parseOutput({stdout, stderr, exitCode}): {finalMessage, learned[], tokens?, cost?},
  health(): Promise<{healthy, detail}>
}
```
Starting hypotheses to **verify, not trust** (check each against the installed binary's `--help` and its docs,
then record what you found):

| Agent | Headless hypothesis | JSON output hypothesis | Notes |
|---|---|---|---|
| OpenCode | `opencode run "<task>"` (model flag?) | ? | The paste also mentions ACP (`opencode acp`); ACP is phase 2 |
| Codex CLI | `codex exec "<task>"` | `--json`? | Also `codex mcp-server`; approval/sandbox flags to verify |
| Claude Code | `claude -p "<task>" --output-format json` | yes (verify) | Uses the user's Claude plan: *no* quota saving; still valid as a scaffold |
| Gemini CLI | `gemini -p "<task>"` | ? | |
| OpenHands | `openhands --headless -t "<task>"` | `--json` (JSONL)? | Headless is always auto-approve, so worktree or container is mandatory |
| Cline CLI | `-y` / `--json` | ? | A reported hang on Linux ARM64: health check + hang timeout |
| `fake` | a Node script in `test/fixtures` that edits files per a scenario file | yes | Used by every test; never shipped as selectable in production config |

Process handling (shared):
- spawn with `cwd = worktree`, a minimal env (inherit PATH/HOME and the agent's own config dirs; strip
  `STEALTHLAB_*` tokens), stdin closed unless the adapter needs it;
- **hang detection:** no stdout/stderr bytes for `hang_s` (default 180) -> `timed_out`;
- hard timeout -> kill the whole process tree (Windows: `taskkill /T /F`; POSIX: process group);
- the platforms we run on are **Windows, macOS and Linux**; test path handling on Windows (backslashes,
  `.cmd` shims such as `opencode.cmd`).

**ACP (phase 2, same interface):** one `acp` adapter implementing the Agent Client Protocol client side, tried
first with OpenCode. Build it only after phase 1 passes acceptance, and only from the official ACP spec (cite
the version).

## 6. Claude Code integration (installed by `--with-exec`)

### 6.1 `~/.claude/agents/stealth-executor.md`
- Frontmatter: `name`, `description` (when to use: "carry out one NODE from .stealth/run.md and prove it with its
  check"), `tools` (minimal: Read, Edit, Write, Bash, Grep, Glob), `isolation: worktree`, `maxTurns` (e.g. 40).
- Body: the one-line executor contract from `plan_and_run`: read the NODE line and its claims, do only that,
  run the `check=`, reply with *result, proof (diff stat + check output tail), anything learned*.
- Verify every frontmatter key against the current Claude Code docs for custom subagents before writing it.

### 6.2 `~/.claude/agents/stealth-delegator.md`
- Carries the `stealthlab-exec` MCP server **inline** (`mcpServers`), so the main conversation never loads
  those tool descriptions. Its job: take a goal/NODE, call `achieve`, poll `run_result`, return the result
  contract's `summary` + `verified` + `diff.stat`, and never apply the diff itself.
- This works only as a user-level agent (plugins ignore `mcpServers`). The installer writes it; `uninstall` removes it.

### 6.3 Hooks: measure Claude's own subagents
- `SubagentStart` / `SubagentStop` entries in `~/.claude/settings.json` running `stealthlab-mcp hook subagent-start|subagent-stop`.
- Read the hook's JSON from stdin. **Verify the payload fields in the current docs**; do not assume field names.
- `subagent-stop`: find the NODE the subagent handled (from its task text or `.stealth/run.md`), run that NODE's
  `check=` in the subagent's worktree (or the checkout if none), and send `report_model_run(scaffold="claude-code-subagent",
  model=<subagent model if the payload has it, else "claude">, accepted=<check passed>, check_kind="procedure_check")`
  through the same outbox.
- It must never block or slow the user: hard timeout 60 s, always exit 0, log failures to `~/.stealthlab/hooks.log`.
- Merge into the existing settings JSON without clobbering the user's other hooks (the installer already merges
  MCP config: follow `config.mjs`).

### 6.4 Plugin (optional, last)
A Claude Code plugin manifest carrying the two agent definitions and a skill that explains `achieve`. Hooks
and MCP config stay installer-managed (plugins ignore them). Skip it if the manifest format can't be verified.

## 7. Worktrees, scope, containers

- Worktree: `git -C <repo> worktree add --detach <tmp>/stealth-runs/<run_id> <base-commit>`. For
  `base="working-tree"`, apply `git diff HEAD` (binary-safe) into the worktree first. Refuse if the repo has
  unresolved merge state. Never create branches in the user's repo namespace without the prefix `stealth/run-<id>`.
- Cleanup: remove the worktree when the run is cancelled, failed with no diff, or applied; keep verified,
  unapplied runs for 7 days (then GC). Always `git worktree prune` afterwards.
- Scope: after the run, `git -C <worktree> diff --name-only <base>` must match `scope` globs; otherwise the result
  lists `scope_violations` and `verified=false`.
- Optional `container: true` (phase 2): run the executor inside `docker run --rm -v <worktree>:/work` with the
  agent's binary; required default for OpenHands if Docker is present.

## 8. Tests (proving tests ship with the code)

`node --test` in `packaging/npm/test/` (follow the existing test style), using the `fake` executor and a
throwaway git repo created per test:
1. `achieve` returns immediately; `run_status` goes queued -> running -> verifying -> verified.
2. A check that fails -> `verified=false`, with the check tail present and redacted (plant a fake token like
   `sk-test-XXXX…` in output and assert it is redacted).
3. Scope violation -> `verified=false`, `scope_violations` listed.
4. Hang (fake emits nothing) -> `timed_out` within `hang_s` + 5 s; the process tree is gone.
5. Hard timeout kills the tree (spawn a child that spawns a grandchild).
6. `apply_run` refuses an unverified run; refuses when a target file changed after start; succeeds otherwise;
   the main checkout is untouched before `apply_run`.
7. Race: two fakes, one verifies faster -> the winner is returned, the loser cancelled, both reported (outbox).
8. Evidence: the hosted call fails (mock HTTP 500 / "relation does not exist") -> queued; the next call flushes.
9. Executor selection uses a mocked `recommend_models` ladder; falls back to `default_order` on error.
10. Adapter refuses to run when `VERIFIED_WITH` is missing or the installed major version differs.
11. Installer: `install --with-exec` writes the agents, hooks and MCP entry; `uninstall` restores the original
    `settings.json` byte-for-byte (except formatting it already normalizes: assert semantic equality).
12. Hook: a sample `SubagentStop` payload (from the docs) -> the check runs and `report_model_run` is queued; a
    malformed payload -> exit 0 and a log line.
13. Windows path cases (skip on non-Windows with a clear skip message, and vice versa).
14. Coverage gaps that exist in the tree you are extending, which the new work makes worse unless closed:
    `clients()` is **never invoked by any current test** (only the free `upsert*`/`remove*` functions are), and the
    `STEALTHLAB_TEST_HOME` seam at `lib/clients.mjs:38` is dead capacity. Test the real client table, including
    its `detect()` branches, before you add `~/.claude` writing to that same path.
15. `--help` completeness: the `config` subcommand exists in the dispatch (`bin/stealthlab-mcp.mjs:175`) but is
    **absent from the `HELP` text**. Add every subcommand you introduce (`exec`, `hook`, …) to `HELP`, and assert
    dispatch↔help parity so the gap cannot reopen.

### Baseline (recorded 2026-09-27 — node v24.16.0, Python 3.13.0, `DATABASE_URL` unset)

"Still passing" is not measurable against a moving tree, so here is the tree you start from. **Reproduce these
before you change anything, and if you cannot, say so in section 11(f) rather than proceeding on a guess.**

| Suite | Command | Baseline result |
|---|---|---|
| npm | `node --test` in `packaging/npm` | **10 passed, 0 failed, 0 skipped** (~0.32 s) |
| `packaging/tests` | `python -m pytest tests -q` in `packaging` | **62 passed, 33 FAILED, 0 skipped** (7.18 s) |
| `backend/tests` | `python -m pytest tests -q` in `backend` | **3866 passed, 15 FAILED, 677 skipped** (351 s); 4556 collected |

**Two pre-existing red suites — neither is yours, and neither is a licence to add to them.**

- The 33 `packaging/tests` failures have **one root cause**: this checkout is missing `experiments/harness` and
  `experiments/swebench_pro`, so `HarnessRootNotFound` (28 tests) and `BackendRootNotFound` (4 tests) fire. One
  further failure (`test_status_offline.py::test_viewer_header_threads_owner_parameter`) is separate. Do not
  "fix" these — they are missing sibling trees, not defects in your change.
- The 15 `backend/tests` failures are stable and pre-existing (reproduced identically on two consecutive runs).
  They include `test_auth_hardening_offline.py::test_supabase_service_role_key_is_never_referenced_by_the_backend_or_frontends`,
  which is a **secrets-scanning guard** — read it and make sure your change does not trip it.

**The delta rule, which is the only thing that actually matters here:** after your change, npm tests must be
**> 10** (you added tests, and section 8's list requires it), and `packaging/tests` + `backend/tests` must show
**exactly the same 33 + 15 failures and not one more**. A new failure anywhere is your bug, regardless of how
innocent it looks. Note for the record: backend collection reports 4556 but the run's outcomes sum to 4558; that
discrepancy is pre-existing and unreconciled — report the verbatim summary line, do not chase it.

Paste all three before-and-after counts in the commit message and again in section 11(d).

## 9. Acceptance (numbers, not narrative)

1. All new and existing npm tests pass; the Python packaging and backend offline suites show **no new failures
   against the section 8 baseline** (i.e. the same 33 + 15 pre-existing failures, and not one more). Report
   all three counts.
2. A **manual end-to-end** on a toy repo with one failing test (`test_add` expects 3 from `add(1, 2)`, and `add` is buggy):
   `achieve(task=…, checks=["python -m pytest -q"], scope=["calc.py"], executor="opencode")`, if OpenCode is
   installed and logged in (else the same with `claude`), returns `verified=true`. The main checkout is unchanged
   until `apply_run`. The outbox or the hosted server has a `report_model_run` payload with `scaffold="opencode"`.
   Paste the result JSON (redacted) into the summary.
3. `install --with-exec` then `uninstall` on a machine with pre-existing `~/.claude/settings.json` hooks leaves
   those hooks intact.
4. **`packaging/npm/EXECUTOR_SUMMARY.md` exists, and all eight sections (a)–(h) are present.** Not "the work
   happened" — the file, with every section, and the section 11 completeness checklist copied into it with its
   answers. A missing section fails acceptance even if every test is green. This is the deliverable the reviewer
   reads first, so its accuracy is the acceptance criterion, not its polish.

### Pre-existing issues you will notice — do not attribute them to yourself, do report them in 11(f)

All read on 2026-09-27 and none caused by this work:

- `scripts/check-publish.mjs` **currently fails**: `stealthlab.defaultMcpUrl` is `""`, so the package is
  unpublishable as committed and `npm publish` would refuse. The three places that must agree are
  `package.json:24`, `install/install.sh:16`, `install/install.ps1:21`.
- `package.json` is `"license": "UNLICENSED"` while the release workflow runs `npm publish --access public`. Worth
  a board question.
- The `description` field contains `keळ` (U+0933, standing in for a U+096C danda) — valid UTF-8, but it renders as
  mojibase on npm. Free to fix if you touch the manifest; say so in 11(f) either way.
- The gate's regexes in `check-publish.mjs:11-12` are exact-format anchored, so reformatting either installer
  line silently makes the capture return `null`. It fails closed, but with a confusing message.
- `install.ps1` honours `$env:STEALTHLAB_CLIENTS` (`install.ps1:31`); `install.sh` has no equivalent handling.
  Intentional per `README.md:25`.

## 10. Out of scope (do not build)

Hosted-server execution or new hosted tools; payments; pooling or relaying anyone's keys; ACP for more than one
agent; automatic merge without `apply_run`; any change to `V1_TOOLS`.

## 11. Deliverables: commits + `packaging/npm/EXECUTOR_SUMMARY.md`

Commits (prefix `ship:`, test counts in each), rebased on `origin/main`, pushed without force.

**`packaging/npm/EXECUTOR_SUMMARY.md` is the final deliverable.** Write it as the last change and commit it. It is
what the reviewer reads first, so it must stand alone: someone who has not read this prompt should be able to
audit the work from that file plus the diff. Exactly eight sections, lettered, in this order, all eight present —
a missing section fails acceptance even when every test is green.

### (a) What was built, mapped to each requirement

A table, one row per requirement from sections 1–7, each `done` / `partial` / `not done`. For `partial` and
`not done`, the reason — never a bare status. The "files" column lists every file added or changed with a
one-line purpose, so the section doubles as the change inventory. Do not describe intent; describe what is on
disk, verifiable against the diff.

### (b) Verified adapter table

One row per adapter in section 5: agent, **installed version**, the exact headless command as built, JSON-output
support (yes / no / partial, and how you know), the auto-approval flag, the `VERIFIED_WITH` source — a `--help`
excerpt or a doc URL, not "the docs" — and the verification date. **Mark every agent you could not verify, and
say why.** An unverified row is an acceptable outcome; an invented flag is not. If a flag could not be
confirmed, state that the adapter refuses to run.

### (c) Tool contracts as built

The final input/output schema of each `stealthlab-exec` tool from section 4, with the actual JSON shape, not the
proposed one. **List every deviation from section 4 and give the reason for each** — a renamed field, a different
default, a dropped input, a tool you added or removed. Silence here reads as "matched the spec", so deviation
must be explicit even when the spec was wrong.

### (d) Test counts before and after

The three suites, before and after, as numbers:

| Suite | Before | After | Delta |
|---|---|---|---|
| `node --test` (packaging/npm) | 10 passed | | |
| `python -m pytest tests -q` (packaging) | 62 passed / 33 failed | | |
| `python -m pytest tests -q` (backend, offline) | 3866 passed / 15 failed / 677 skipped | | |

Then the list of new tests mapped to section 8's numbered items, marking any of 1–15 you did not write. Apply
the delta rule from section 8: npm must be up, and the 33 + 15 pre-existing failures must be unchanged and not
one more. If that is not what you observe, say so plainly — this section is where it gets reported.

### (e) Acceptance evidence

The redacted `run_result` JSON from the manual end-to-end; the outbox file or hosted `report_model_run` payload
showing `scaffold="opencode"`; and the before/after `~/.claude/settings.json` diff proving the pre-existing
hooks survived `install --with-exec` → `uninstall`. Paste the artifacts, redacted, not a description of them.
If an acceptance item could not be run, it is `not done` with the reason — do not narrate around it.

### (f) Conflicts and board questions

Two parts, both verbatim.

1. **Conflicts** between this prompt and the code, per the instruction at the top of this file: what the code
   says, what the prompt said, and what you did. Include the Node `engines` question from section 3, and any
   section-8 requirement you believe is wrong.
2. **Board questions** filed in `.scratch/build-board.md`, quoted exactly as written, each with its line number
   and whether it is blocking. Also list the pre-existing issues from section 9 that you confirmed but did not
   fix.

### (g) Security review: every process spawn and every network egress

**Enumerate, do not summarize.** Two tables, "before" and "after", covering the **whole** `packaging/npm/` tree —
not just the files you added. The "before" column below is the measured state of the tree you are starting from;
if your reading of the tree disagrees with it, trust your grep and say so.

**Process spawns.** Every call site, including ones you added and ones you did not touch:

| Site (file:line) | Binary/command | Args from | `shell`? | Guard | Reachable from |
|---|---|---|---|---|---|
| `lib/clients.mjs:33` | `where` / `which` | hardcoded bin list | no | `stdio: "ignore"` | `install`/`uninstall` |
| `lib/clients.mjs:125` | `claude`, `code` | hardcoded + quoted | **yes, on Windows** | per-arg quoting | `install`/`uninstall` |
| `install/install.sh:41` | `npx` (bash `exec`) | `$PKG`, `$URL` | n/a | URL from precedence chain | installer |
| `install/install.ps1:42` | `npx` | `$Pkg`, `$Url` | n/a | same | installer |

The new code adds process spawns by design — the executor adapters, the check runner, the git worktree calls, and
`taskkill` / POSIX process-group teardown. Each needs a row stating: what is spawned, **whether the command or
any argument can be influenced by untrusted input (a task string, a model name, check output, a repo filename)**
, whether `shell: true` is used and why, the env scrubbing applied (`STEALTHLAB_*` tokens stripped; PATH/HOME and
the agent's own config dirs inherited), the cwd, and the timeout and kill path. State plainly which of these are
injection surfaces. If the answer is "a check command is run verbatim by design", say that too — it is a real
property of the design, and hiding it is worse than naming it.

**Network egress.** Every outbound call, same standard:

| Site (file:line) | Method | Destination | What is sent | Auth | Guard |
|---|---|---|---|---|---|
| `lib/proxy.mjs:101` | POST | the `url` | one JSON-RPC line verbatim | bearer | loopback-only `http://` rule |
| `lib/proxy.mjs:173` | GET | the `url` | none | bearer | 405/404 treated as absent |
| `lib/proxy.mjs:196` | DELETE | the `url` | none | bearer | 2 s abort, session-only |
| `bin/stealthlab-mcp.mjs:117` | POST | the `url` | probe payload | bearer | 15 s timeout |
| `bin/stealthlab-mcp.mjs:131` | DELETE | the `url` | none | bearer | 15 s timeout |

The property to preserve and to assert: **there is no hardcoded host anywhere in `lib/`, `bin/` or `scripts/`.**
Egress resolves entirely at runtime through the four-source precedence chain in `lib/config.mjs:64` (`--url` →
`STEALTHLAB_MCP_URL` → `~/.stealthlab/config.json` → `package.json`'s `defaultMcpUrl`). For each new egress —
`recommend_models`, `report_model_run`, and anything the hooks send — state the destination, the exact payload
fields, what redaction runs before it leaves (`trace_redaction.py`'s patterns, ported with the same test
vectors), and what happens on failure (the outbox at `~/.stealthlab/outbox/`). If you add a hardcoded host or
allow a non-HTTPS destination, that is a finding to report in this section and in 11(f), not a detail.

**Also required in (g), in prose:**
- every path written **outside** the worktree (`~/.claude/agents/*.md`, `~/.claude/settings.json`,
  `~/.stealthlab/**`, OS temp run dirs), with the permission mode and whether `uninstall` reverses it;
- what an unverified adapter is prevented from doing, and how `VERIFIED_WITH` gates execution;
- how secrets are kept out of logs, result payloads, the run store, and the outbox, and where redaction is
  applied on the way out;
- the ToS boundary from constraint 5: user's own agents, user's own logins, never pooled, proxied or relayed —
  name where in the code that is enforced, not just where it is commented.

### (h) Next steps, in order

The follow-on work, sequenced, with the reason for the order and an explicit call on each phase-2 item: ACP
adapter, containers, the plugin manifest, racing defaults. Then anything this change made newly possible or newly
urgent. End with the one thing you would do next if you had one more day.

### Completeness checklist — copy this into the summary and answer it

The file is not finished until every line is checked. Answer with evidence, not ticks.

- [ ] (a) every requirement from sections 1–7 has a row, with a reason for each `partial` / `not done`
- [ ] (b) one row per adapter; unverified ones marked with the reason; no flag stated without a source
- [ ] (c) every tool's final schema, with every deviation from section 4 listed
- [ ] (d) before/after counts for all three suites; new tests mapped to section 8; delta rule applied
- [ ] (e) pasted artifacts for all three acceptance items, or an explicit `not done` with a reason
- [ ] (f) conflicts and board questions verbatim, with line numbers
- [ ] (g) **both** tables complete — every spawn site and every egress site, old and new, none summarized away
- [ ] (h) ordered next steps
- [ ] pre-existing issues from section 9 confirmed or corrected, not silently fixed
- [ ] no new runtime dependency in `package.json`; `files` unchanged
- [ ] every count in this file is a real run, not an estimate

Your final chat message: one paragraph and the path to `EXECUTOR_SUMMARY.md`. Nothing else.
