# Build prompt: StealthLab local executor layer (external agents + Claude Code subagents)

You are implementing a feature in the StealthLab repository (`github.com/3Founders/stealth-lab`, branch `main`).
Read this whole prompt before writing code. When something here conflicts with what you find in the code, the
code is the truth for *how things work today*. Stop and report the conflict in your final summary instead of
silently choosing.

---

## 0. Read first (in this order)

1. `CLAUDE.md`: hard rules, lanes, commit conventions, code conventions. **Breaking a hard rule invalidates the work.**
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
8. **Repo rules (CLAUDE.md):** stay in your lane (`packaging/` is the ship lane; commit prefix `ship:`). A backend
   change is allowed ONLY if a hosted tool's contract is actually insufficient: then stop, write the needed
   change as a numbered question in `.scratch/build-board.md`, and continue with what you can. Proving tests ship
   in the same change. Paste test counts into every commit message. Rebase onto `origin/main`; never force-push.
   Never commit `.env`, traces or `node_modules`.

## 3. Architecture

```
orchestrator (Claude Code / Codex / Cursor)
   | MCP (stdio, local)
   v
stealthlab-exec   (NEW: Node >= 20, in packaging/npm; launched as `stealthlab-mcp exec`)
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

The Python packaging suite (`packaging/tests`) and the backend suite must still pass unchanged. Run them with
`DATABASE_URL` unset (offline). Paste all three counts in the commit message.

## 9. Acceptance (numbers, not narrative)

1. All new and existing npm tests pass; the Python packaging and backend offline suites pass (report counts).
2. A **manual end-to-end** on a toy repo with one failing test (`test_add` expects 3 from `add(1, 2)`, and `add` is buggy):
   `achieve(task=…, checks=["python -m pytest -q"], scope=["calc.py"], executor="opencode")`, if OpenCode is
   installed and logged in (else the same with `claude`), returns `verified=true`. The main checkout is unchanged
   until `apply_run`. The outbox or the hosted server has a `report_model_run` payload with `scaffold="opencode"`.
   Paste the result JSON (redacted) into the summary.
3. `install --with-exec` then `uninstall` on a machine with pre-existing `~/.claude/settings.json` hooks leaves
   those hooks intact.

## 10. Out of scope (do not build)

Hosted-server execution or new hosted tools; payments; pooling or relaying anyone's keys; ACP for more than one
agent; automatic merge without `apply_run`; any change to `V1_TOOLS`.

## 11. Deliverables: commits + `SUMMARY.md`

Commits (prefix `ship:`, test counts in each), rebased on `origin/main`, pushed without force.

**Write `packaging/npm/EXECUTOR_SUMMARY.md` as your final deliverable** (committed with the last change). It is
what the reviewer reads first, so it must stand alone:
1. **What was built:** every file added or changed (path + one line), mapped to each requirement in sections 1-7,
   each marked `done` / `partial` / `not done` with the reason.
2. **Verified adapter table:** agent, installed version, exact headless command, JSON output support, the
   auto-approval flag, the source it was verified from (a `--help` excerpt or a doc URL), and the verification date.
   Mark every agent you could not verify, and why.
3. **Tool contracts as built:** the final input/output schema of each `stealthlab-exec` tool, including any
   deviation from section 4 and why.
4. **Tests:** counts for npm, `packaging/tests` and the backend offline suite (before and after), plus the list of
   new tests mapped to section 8's numbers.
5. **Acceptance evidence:** the redacted `run_result` JSON from the manual end-to-end, the outbox or hosted
   `report_model_run` payload, and the before/after `settings.json` diff from the install/uninstall check.
6. **Conflicts** between this prompt and the code, and the board questions you filed (verbatim).
7. **Security review:** every place the runtime spawns a process, writes outside the worktree, or sends data
   off-machine, and what guards it.
8. **Known risks, and the next steps in order** (ACP, containers, the plugin, racing defaults).

Your final chat message: one paragraph and the path to `EXECUTOR_SUMMARY.md`.
