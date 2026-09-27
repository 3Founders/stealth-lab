# EXECUTOR_SUMMARY: `stealthlab-exec`, the local executor layer

Spec: `.scratch/prompts/executor_layer_build_prompt.md`. Split: `.scratch/prompts/executor_layer_split.md`. Built
2026-09-27/28 by three parallel builders (A runtime, B adapters, C Claude integration + CLI) and integrated in
session 640be25f. Board claim: `.scratch/build-board.md` SHIP item 8. Board questions: Q-EXEC-1..4.
All paths below are relative to `packaging/npm/` unless marked.

## (a) What was built, mapped to each requirement

| Req (spec §) | Status | Files on disk (purpose) | Reason if not `done` |
|---|---|---|---|
| §1A run external agents headlessly on a goal, verify independently, record outcome | done | `lib/exec/runtime.mjs` (ExecRuntime: achieve/status/result/apply/cancel/list/gc, state machine, races, persistence); `lib/exec/server.mjs` (MCP stdio server) | |
| §1B fit Claude Code's subagent model | done | `lib/agents/stealth-executor.md`, `lib/agents/stealth-delegator.md` (templates); `lib/claude_exec.mjs` (install/remove agents + hooks) | |
| §2.1 hosted server stays knowledge-only | done | no backend file changed | |
| §2.2 opt-in; uninstall reverses everything | done | `bin/stealthlab-mcp.mjs` (`install --with-exec`, `uninstall`); `lib/claude_exec.mjs` | `.bak` copies of settings.json are left (by design, a safety copy) |
| §2.3 never touch the main checkout except `apply_run` | done | `lib/exec/worktree.mjs` `applyToCheckout` (blob-hash guard) | |
| §2.4 auto-approve only in a worktree | done | `lib/exec/runtime.mjs` (cwd = detached worktree); adapter flags in `lib/executors/*.mjs` | containers (§7) not built |
| §2.5 ToS: user's own local CLIs/logins; never proxied/pooled | done | `lib/executors/common.mjs` (boundary comment + `scrubEnv`); `lib/exec/proc.mjs` `childEnv`; README section | |
| §2.6 no invented flags; `VERIFIED_WITH`; refuse if unverified | done | `lib/executors/common.mjs` `runnable`/`assertRunnable`; `lib/exec/select.mjs` `refusalReason` | gemini, openhands unverified (not installed) -> refuse |
| §2.7 secrets never logged/sent | done | `lib/exec/redact.mjs` (exact port of `trace_redaction.py` + superset); redaction points listed in (g) | |
| §2.9 `EXECUTOR_SUMMARY.md` | done | this file | |
| §2.10 no new dependencies | done | `package.json`: still no `dependencies`/`devDependencies` | |
| §2.11 runtime code only under `bin/`/`lib/`; `files` unchanged | done | `files` = `["bin","lib","install","README.md"]` (unchanged) | |
| §3 architecture (registry, worktree mgr, verifier, run store, evidence client + outbox) | done | `lib/executors/index.mjs`; `lib/exec/{worktree,verify,store,evidence,hosted,select,proc,task}.mjs` | |
| §3 Node >= 18.17 | partial | all modules | written against 18.17 APIs by review; only Node 24.16 was available locally, so not executed on 18 (see (f)) |
| §4 tools `list_executors`, `achieve`, `run_status`, `run_result`, `apply_run`, `cancel_run` | done | `lib/exec/server.mjs`, `lib/exec/runtime.mjs` | deviations in (c) |
| §4 result contract | done | `lib/exec/runtime.mjs` `attemptView` | extra fields listed in (c) |
| §4 executor selection via `recommend_models` + fallback + race=2 | done | `lib/exec/select.mjs` | |
| §4 evidence via `report_model_run` + offline outbox | done | `lib/exec/evidence.mjs`, `lib/exec/hosted.mjs` | payloads without goal/procedure/model are held (server would refuse) |
| §5 adapters opencode, codex, claude, gemini, openhands, cline, fake | done | `lib/executors/{index,common,opencode,codex,claude,gemini,openhands,cline,fake}.mjs` | |
| §5 process handling: hang detection, hard timeout, process-tree kill (Windows + POSIX) | done | `lib/exec/proc.mjs` | Windows: a grandchild left alive after a *normal* exit is not swept (no job objects in Node) |
| §5 ACP adapter (phase 2) | not done | | phase 2 by spec; build only after phase 1 acceptance |
| §6.1 executor agent | done | `lib/agents/stealth-executor.md` | |
| §6.2 delegator agent (inline MCP) | done | `lib/agents/stealth-delegator.md` | user-level agent only (plugins ignore `mcpServers`) |
| §6.3 SubagentStart/Stop hooks, never slow the user | done | `lib/subagent_hook.mjs`, `bin/stealthlab-mcp.mjs hook ...` | stop hook hands off to a detached worker (returns in ms) |
| §6.4 plugin | not done | | optional/last in spec; hooks + MCP must stay installer-managed anyway |
| §6 register local `stealthlab-exec` for non-Claude clients | partial | README + HELP document manual registration | needs `clients.mjs` writers parameterized by server name (Q-EXEC-3) |
| §6 `stealthlab-mcp exec enable` alias | not done | | optional alternative to `--with-exec` |
| §7 worktrees (detached, base HEAD / working-tree, merge-state refusal, cleanup, 7-day GC) | done | `lib/exec/worktree.mjs`, `lib/exec/runtime.mjs` `gc` | |
| §7 scope enforcement | done | `lib/exec/verify.mjs` `scopeViolations`; artifact exclusions in `lib/exec/worktree.mjs` `ARTIFACT_EXCLUDES` | |
| §7 containers | not done | | phase 2 |
| §8 proving tests | done | `test/exec_{runtime,units,evidence,redact,artifacts}.test.mjs`, `test/executors.test.mjs`, `test/claude_exec.test.mjs`, `test/subagent_hook.test.mjs`, fixtures under `test/fixtures/` | mapping in (d) |
| npm test glob | done | `package.json` `test`/`prepublishOnly` -> `node --test test/*.test.mjs` | fixture scripts were counted as tests (Q-EXEC-4) |
| README | done | `README.md` "Opt-in: the local executor layer" | |

## (b) Verified adapter table

Verification = `--version` + `--help` of the **installed** binary on 2026-09-27; no agent was run on a task
during verification. Help excerpts are quoted in each adapter file and saved under `test/fixtures/executors/`.

| Agent | Installed version | Headless command as built | JSON output | Auto-approve | `VERIFIED_WITH` source | Date | Verified |
|---|---|---|---|---|---|---|---|
| opencode | 1.18.32 (npm `.cmd` shim unwrapped to `opencode.exe`) | `opencode.exe run --format json --auto --dir <wt> [--model <provider/model>] "<task>"` | yes: JSONL `{type,timestamp,sessionID,part}` (`text`, `step_start`, `step_finish` with tokens/cost, `tool_use`, `error`); shape read from the binary | `--auto` | `opencode run --help` (1.18.32); https://opencode.ai/docs/cli/ | 2026-09-27 | yes |
| codex | codex-cli 0.153.4 (`.cmd` -> `node codex.js`) | `node codex.js exec --json --color never --sandbox workspace-write --cd <wt> [--model <m>] -` (task on stdin) | yes: JSONL `thread.started`, `item.completed(agent_message)`, `turn.completed(usage)`, `turn.failed` (docs example; names confirmed in the binary) | `--sandbox workspace-write` (never `--dangerously-bypass-approvals-and-sandbox`) | `codex exec --help` (0.153.4); https://learn.chatgpt.com/docs/non-interactive-mode | 2026-09-27 | yes |
| claude | 2.1.283 (`claude.exe`) | `claude.exe -p "<task>" --output-format json --no-session-persistence --permission-mode acceptEdits --permission-prompts none [--model <m>] --allowedTools Read,Edit,Write,Bash,Glob,Grep` | yes: one object `{result,is_error,total_cost_usd,usage}` (docs) | `--permission-mode acceptEdits` + `--allowedTools` (never `bypassPermissions`/`--dangerously-skip-permissions`) | `claude --help` (2.1.283); https://code.claude.com/docs/en/headless | 2026-09-27 | yes |
| cline | 3.0.61 (`.cmd` -> `node bin/cline`) | `node cline --json --auto-approve true --cwd <wt> [--model <m>] [--timeout <s>] "<task>"` | yes: NDJSON `agent_event` (content_end, usage, done) from the binary; the docs' `say/ask` shape is also parsed (the docs are wrong for 3.0.61) | `--auto-approve true` | `cline --help` (3.0.61); https://docs.cline.bot/cli/cli-reference | 2026-09-27 | yes |
| gemini | **not installed** | `gemini --output-format json --approval-mode yolo [--model <m>] --prompt "<task>"` (from docs) | partial: documented `{response,stats,error}`; stats layout undocumented | `--approval-mode yolo` (docs) | https://geminicli.com/docs/cli/cli-reference/ ; https://geminicli.com/docs/cli/headless/ | 2026-09-27 | **no**: `VERIFIED_WITH=null`, **the adapter refuses to run** |
| openhands | **not installed** | `openhands --headless --json [--override-with-envs + env LLM_MODEL=<m>] --task "<task>"` (from docs) | partial: JSONL action/observation events documented; no final-message event documented | implicit (docs: headless always auto-approves) | https://docs.openhands.dev/openhands/usage/cli/headless ; .../cli/command-reference | 2026-09-27 | **no**: `VERIFIED_WITH=null`, **the adapter refuses to run** |
| fake | fixture 1.0.0 | `node test/fixtures/fake_agent.mjs [--model <m>]`, task on stdin, env `STEALTHLAB_FAKE_SCENARIO` | yes: final `{"type":"final",...}` line | none | test fixture | - | yes; only when `STEALTHLAB_EXEC_ALLOW_FAKE=1`; not shipped (`test/` not in `files`) |

## (c) Tool contracts as built

Transport: MCP over stdio, newline-delimited JSON-RPC 2.0 (`initialize`, `ping`, `tools/list`, `tools/call`);
requests handled concurrently; client disconnect cancels active runs. Launched as `stealthlab-mcp exec`.

| Tool | Input (required*) | Output |
|---|---|---|
| `list_executors` | none | `[{id, installed, version, verified_flags, healthy, last_health_check, notes, runnable, models}]` |
| `achieve` | `repo_path`*, `task`* (<= 2,000 chars), `scope`* (non-empty globs), `checks` (array; defaults to the NODE lines' `check=`), `goal_id`, `procedure_id`, `step_order`, `executor`, `model`, `timeout_s` (default 900, max 3600), `hang_s` (default 180), `check_timeout_s` (default 600), `race` (1/2), `base` (`HEAD` default / `working-tree`) | `{run_id, state:"queued", executor, model, started_at, worktree}`, immediately |
| `run_status` | `run_id`* | `{state, elapsed_s, last_event, executor, model}` |
| `run_result` | `run_id`*, `wait_s` (<= 55) | the result contract below |
| `apply_run` | `run_id`* | files applied to the checkout, or a refusal |
| `cancel_run` | `run_id`* | kills the process tree, removes worktree(s), state `cancelled` |

Result contract as built (acceptance run, (e)):
`{run_id, state, verified, checks:[{cmd,exit,seconds,tail}], scope_violations, summary, learned,
diff:{files,stat,worktree,patch_path}, executor, model, attempt, duration_s, tokens:{in,out}, cost_usd,
evidence:{reported,queued,held?,outbox_file?,instance_key}, executor_exit, error?, stop_reason?, race?,
selection:{source,...}, applied?}`

**Deviations from spec §4, each with its reason:**
1. `achieve` returns `executor`/`model` = `null` when the executor is chosen from evidence. Selection needs a hosted call plus agent `--version` probes, which can break the 2 s budget; the choice appears in `run_status`/`run_result`. `state:"queued"` is added.
2. Added inputs `hang_s` and `check_timeout_s`, so the spec's fixed defaults can be tuned per call.
3. `scope` is required and non-empty, and at least one check is required (from `checks` or a NODE `check=`). With no check, "verified" would be vacuous; with an empty scope, every edit would be a violation.
4. `list_executors` adds `runnable` and `models`, and errors if the adapter registry can't load.
5. The result adds `executor_exit`, `stop_reason`, `error`, `race[]`, `selection`, `applied`, and `evidence.held/outbox_file`.
6. `evidence` is **held** (kept in `outbox/held/`, never sent) when there is no `goal_id`/`procedure_id` or no model: the hosted `report_model_run` refuses those (`backend/app/mcp_server/server.py:3437`). Reports also need a saved token; without one they are queued with no network call.
7. `apply_run` copies files instead of running `git apply`. It first checks blob hashes (`hash-object` of the checkout file vs `rev-parse <baseTree>:<path>`); once they match, copying is equivalent and robust to autocrlf and binary files. It refuses symlinks and paths escaping the repo.
8. `cancel_run` on a finished run removes its worktrees and marks it `cancelled`; it refuses an applied run.
9. Scope globs without a slash match the repo root only (stricter than .gitignore).
10. Generated artifacts (`**/__pycache__/**`, `*.pyc/.pyo`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.tox`, `.coverage`, `node_modules/.cache`) are excluded from the diff, the scope check, the patch and `apply_run`. The acceptance run found this: a correct fix failed its scope check because the agent's own pytest run left `__pycache__/*.pyc` (proved by `test/exec_artifacts.test.mjs`).
11. `base=working-tree` copies tracked changes (`git diff HEAD`) only; untracked files are not copied.
12. A race's cancelled loser is reported as `accepted=false`: `report_model_run` has no "cancelled" outcome (Q-EXEC-1).

## (d) Test counts before and after

Real runs, 2026-09-27/28. "Before" = a clean worktree of `HEAD` (`e0a3566`); "after" = the same plus this change.
`DATABASE_URL` unset.

| Suite | Before | After | Delta |
|---|---|---|---|
| `node --test` (packaging/npm) | 17 passed (spec's "10" was stale: `2363b26` added hook tests) | **93 tests: 90 passed, 0 failed, 3 skipped** | +73 passing; 0 new failures |
| `python -m pytest tests -q` (packaging) | 62 passed / 33 failed | 62 passed / 33 failed | 0 (identical) |
| `python -m pytest tests -q` (backend, offline) | 3880 passed / 14 failed / 678 skipped; **HEAD re-run twice: 3879 / 15 / 678 both times, identical failure list** | 3879 passed / 15 failed / 678 skipped | 0 vs HEAD's re-runs (the first run's 14 was one pre-existing test passing intermittently) |

Backend note: the change touches only `packaging/npm` (JavaScript), which the Python suite never imports. The one-count
difference between the first `before` run (14 failed) and `after` (15) is run-to-run flakiness in `HEAD` itself: two more
`HEAD` runs gave 15 failed with the **same 15 test ids** both times. That matches the spec's recorded baseline of 15. The 15
pre-existing failures are in: test_adapters_e2e, test_auth_enforcement_offline, test_auth_hardening_offline,
test_bypass_closure_offline (x3), test_claim_graph_api_offline, test_economy_hardening_offline (x3),
test_migration_upgrade_e2e, test_phase1_security_boundaries_offline, test_phase2_authorization_offline,
test_procdoc_v2_pipeline_offline, test_retrieval_identity_offline. Not one of them was introduced here.

The 3 skips are platform-specific (POSIX-only path/detect/process-group tests skipped on Windows, each with a
skip message). Delta rule: npm is up; no new Python failures (see the counts above).

New tests mapped to spec §8 (all written):
1. Async lifecycle queued -> running -> verifying -> verified, checkout untouched: `exec_runtime.test.mjs`
2. Failing check -> tail present and redacted (planted `sk-test-…` + the saved token absent from result, run store and outbox): `exec_runtime.test.mjs`
3. Scope violation: `exec_runtime.test.mjs`; artifact exclusion: `exec_artifacts.test.mjs`
4. Hang -> `timed_out` within hang_s + 5 s, process gone: `exec_runtime.test.mjs`
5. Hard timeout kills the tree incl. a grandchild: `exec_runtime.test.mjs`
6. `apply_run` refuses unverified / changed target; applies otherwise (new files, deletions): `exec_runtime.test.mjs`
7. Race: the faster second rung wins, the loser is cancelled, both reported with shared instance_key/recommendation_id: `exec_runtime.test.mjs`
8. Evidence: HTTP 500, `relation … does not exist`, `UndefinedTableError`, JSON-RPC error -> queued; `REFUSED` -> rejected/; not logged in; no goal -> held/; flush: `exec_evidence.test.mjs`
9. Selection via a mocked `recommend_models` ladder; fallback on 500/not_ready; wide interval turns racing on; explicit race=1 respected: `exec_runtime.test.mjs`/`exec_units.test.mjs`
10. Adapter refusal (missing `VERIFIED_WITH`, major-version mismatch; the agent never spawns): `exec_units.test.mjs`, `executors.test.mjs`
11. `install --with-exec` then `uninstall` restores settings.json (semantic and byte equality): `claude_exec.test.mjs`
12. SubagentStop payload -> check runs + report queued; malformed payload -> exit 0 + log line: `subagent_hook.test.mjs`
13. Windows path cases (`.cmd` shims, backslashes; POSIX counterparts skip on win32): `executors.test.mjs`, `exec_units.test.mjs`, `claude_exec.test.mjs`
Also: redaction parity (13 vectors generated from the Python module), NODE-line parsing, MCP stdio protocol,
reload after restart, GC at 6 days vs 8 days, and integration against the real fake adapter.

## (e) Acceptance evidence

**9.2, manual end-to-end.** Toy repo: `calc.py` with `return a - b`; `test_calc.py::test_add` expects 3. Driven
through the real `stealthlab-mcp exec` server over stdio: `achieve(task=…, checks=["python -m pytest -q"],
scope=["calc.py"], executor="opencode", goal_id=<test id>, step_order=1)`, then polling `run_result`. Paths are
redacted to `<tmp>`.

First run: `state:"failed"`. OpenCode fixed `calc.py` correctly and the check passed, but its own pytest run left
`__pycache__/*.pyc`, which counted as scope violations. That was a runtime bug, fixed by deviation 10. Second run:

```json
{
 "run_id": "r-20260927184904-d03e98",
 "state": "verified",
 "verified": true,
 "checks": [{"cmd": "python -m pytest -q", "exit": 0, "seconds": 2, "tail": "<pytest output, redacted>"}],
 "scope_violations": [],
 "summary": "Fixed `calc.py:2` (`a - b` → `a + b`); test passes.",
 "learned": [],
 "diff": {"files": ["calc.py"], "stat": " calc.py | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)",
          "worktree": "<tmp>\\stealth-runs\\r-20260927184904-d03e98-a1", "patch_path": "<tmp>\\home\\runs\\r-20260927184904-d03e98\\a1.patch"},
 "executor": "opencode", "model": null, "attempt": 1, "duration_s": 33.1,
 "tokens": {"in": 19976, "out": 324}, "cost_usd": 0,
 "evidence": {"reported": false, "queued": false, "held": "no model is known for this run (configure models in exec.json)",
              "outbox_file": "<tmp>\\home\\outbox\\held\\1790534977603-….json", "instance_key": "stealth-r-20260927184904-d03e98"},
 "executor_exit": 0, "selection": {"source": "explicit"}
}
```
Main checkout unchanged before `apply_run`: **true** (`calc.py` byte-compared).

Outbox payload (`outbox/held/…json`). It is held rather than sent, because no model was configured for OpenCode
and the server requires one (deviation 6):
```json
{"tool": "report_model_run",
 "args": {"scaffold": "opencode", "accepted": true, "instance_key": "stealth-r-20260927184904-d03e98",
          "goal_id": "00000000-0000-7000-8000-00000000e2e0", "check_kind": "tests", "attempt_index": 0,
          "tokens_in": 19976, "tokens_out": 324, "cost_usd": 0, "latency_ms": 33120, "step_order": 1},
 "held": "no model is known for this run (configure models in exec.json)", "queued_at": "2026-09-27T18:49:37.602Z"}
```

**9.3, settings.json survives `install --with-exec` then `uninstall`** (`claude_exec.test.mjs`, real CLI on a
temp home, `STEALTHLAB_PRINT_SETTINGS_DIFF=1`). Before (the user's own hooks):
```json
{"model":"opus","permissions":{"allow":["Bash(npm test:*)"]},"hooks":{
 "UserPromptSubmit":[{"hooks":[{"type":"command","command":"my-prompt-logger","timeout":5}]}],
 "SubagentStop":[{"matcher":"db-agent","hooks":[{"type":"command","command":"./scripts/cleanup-db-connection.sh"}]}],
 "PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"guard.sh"}]}]}}
```
After `install --with-exec`, only appended (paths shortened):
```
UserPromptSubmit[1] = {"hooks":[{"type":"command","command":"\"…\\node.exe\" …\\bin\\stealthlab-mcp.mjs hook-prompt","timeout":30}]}
SubagentStop[1]     = {"hooks":[{"type":"command","command":"\"…\\node.exe\" …\\bin\\stealthlab-mcp.mjs hook subagent-stop","timeout":15}]}
SubagentStart       = [{"hooks":[{"type":"command","command":"\"…\\node.exe\" …\\bin\\stealthlab-mcp.mjs hook subagent-start","timeout":15}]}]
```
After `uninstall`, identical to "before" (asserted `deepEqual`; byte-identical in the run above).

**9.1:** see (d). **9.4:** this file, sections (a)-(h) plus the checklist.

## (f) Conflicts and board questions

**1. Conflicts (code vs prompt, and what was done):**
- **"`~/.claude/` is never touched by this package."** The code already did touch it: since `2363b26`, `install` writes a `UserPromptSubmit` hook into `~/.claude/settings.json`. Done: that hook is kept as-is, and a test proves both hook sets are added and removed independently.
- **Node `engines` (§3).** `engines.node` stays `>=18.17`. Everything is written to Node 18.17 APIs, but only Node 24.16 exists on this machine, so it was not executed on 18. The new test glob `node --test test/*.test.mjs` relies on the shell (POSIX) or on Node >= 21 to expand it. On Windows cmd with Node 18/20 it would fail (Q-EXEC-4).
- **§8.2 "plant `sk-test-XXXX…` and assert it is redacted".** The backend's `trace_redaction.py` patterns do **not** redact `sk-test-…` (checked by running the module). `lib/exec/redact.mjs` = the exact port (pinned by 13 generated vectors) plus a superset (generic `sk-`, `*_KEY/TOKEN/SECRET/PASSWORD=value`, a truncated private-key block, and the literal saved token) (Q-EXEC-2).
- **The SubagentStop payload has no task prompt, model or worktree path** (per the hooks docs). The node comes from a `STEALTH_RESULT node=… cwd=…` line the executor agent must end with, or from what SubagentStart stashed per `agent_id`. The model is reported as `"claude"`. The claimed cwd is used only if it is a worktree of the same repo (same `--git-common-dir`); otherwise the check runs in the session directory.
- **Claude Code removes a subagent's worktree when nothing changed, and bases it on the default branch.** So the hook reads `.stealth/run.md`/`procedures.md` from the session directory first.
- **`report_model_run` refuses without `goal_id`/`procedure_id` (and a model)**, so such reports are held, not sent (deviation 6).
- **`verify.mjs` said checks "never run in the user's checkout".** That is untrue for the SubagentStop fallback (§6.3 falls back to the checkout). The comment should be read as "never for `achieve` runs".
- **Claude-side hook timeout is 15 s, not 60.** The hook returns in milliseconds; the 60 s limit is enforced inside the detached worker.
- **Race losers are reported `accepted=false`** (deviation 12, Q-EXEC-1).
- **§8 "fixtures are dev-only" vs `node --test`.** `node --test` counted fixture scripts under `test/` as tests (3 phantom passes), so the test script now uses the `test/*.test.mjs` glob.

**2. Board questions** (`.scratch/build-board.md`, Log, verbatim headings):
- line 4777: **Q-EXEC-1 (non-blocking): race losers reported as failures.** Proposed default: don't report cancelled losers until the backend has a "cancelled" outcome.
- line 4783: **Q-EXEC-2 (non-blocking): backend redaction misses keys.** Proposed default: CORE adds the same superset to `trace_redaction.py`.
- line 4787: **Q-EXEC-3 (non-blocking): register the local executor server for non-Claude clients.** Proposed default: a follow-up SHIP change to parameterize `clients.mjs` writers by server name.
- line 4791: **Q-EXEC-4 (non-blocking): test glob on Windows + Node < 21.** Confirm CI is POSIX.

**Pre-existing issues (§9), confirmed on 2026-09-28, not fixed:**
- `node scripts/check-publish.mjs` fails with `package.json "stealthlab.defaultMcpUrl" is empty`, so the package is unpublishable as committed.
- `"license": "UNLICENSED"` while `.github/workflows/publish-mcp-client.yml:32` runs `npm publish --access public`.
- The `description` contains U+0933 (reading it through a cp1252 console raises `UnicodeEncodeError`). Not touched.
- `check-publish.mjs` regexes are exact-format anchored (not re-tested; installer lines untouched).
- `STEALTHLAB_CLIENTS` is honoured in `install/install.ps1:31` only, not in `install.sh`.

## (g) Security review: every process spawn and every network egress

### Process spawns, before this change (the whole `packaging/npm/` tree, re-grepped)

| Site | Binary/command | Args from | `shell`? | Guard | Reachable from |
|---|---|---|---|---|---|
| `lib/clients.mjs:33` | `where` / `which` | hardcoded bin list | no | `stdio: "ignore"` | install/uninstall |
| `lib/clients.mjs:184` (spec said :125; line moved in `2363b26`) | `claude`, `code` | hardcoded + quoted | **yes, on Windows** | per-arg quoting | install/uninstall |
| `install/install.sh:41` | `npx` (bash `exec`) | `$PKG`, `$URL` | n/a | URL from the precedence chain | installer |
| `install/install.ps1:42` | `npx` | `$Pkg`, `$Url` | n/a | same | installer |

### Process spawns, after (the four rows above unchanged, plus)

| Site | Spawns | Untrusted input reaches it? | `shell` | Env | cwd | Timeout / kill |
|---|---|---|---|---|---|---|
| `lib/exec/proc.mjs:128` `spawnManaged` (from `runtime.mjs`) | the executor (adapter `buildCommand`) | **yes**: task string and model, each passed as a single argv element; a task starting with `-` gets a `Task: ` prefix (`safeTaskArg`) | no | `childEnv`: strips all `STEALTHLAB_*` and `GIT_DIR`-family; PATH/HOME and the agent's own config/keys inherited | detached worktree | `hang_s` silence and `timeout_s` hard, both kill the tree |
| `lib/exec/proc.mjs:97` (Windows fallback) | `cmd.exe /d /s /c` | only for an unresolvable `.cmd` shim, and only if no argument contains `%!"^&|<>()` or a newline; otherwise refused | via cmd.exe | as above | worktree | as above |
| `lib/exec/proc.mjs:122` / `verify.mjs` `runChecks` | **check commands, run verbatim through a shell by design** | **yes: an injection surface.** Whoever writes `checks`/`check=` decides what runs (the orchestrator's trust level) | **yes** | scrubbed as above | the worktree (`achieve`); the session dir for the SubagentStop fallback | per-check timeout + tree kill |
| `lib/exec/worktree.mjs:22` | `git` via `execFile` | repo filenames (after `--`) and the worktree path | no | `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` stripped | repo / worktree | 120 s; 64 MB buffer |
| `lib/exec/proc.mjs:105` | `taskkill /PID <n> /T /F` | numeric pid only | no | - | - | - |
| POSIX `process.kill(-pgid)` | signal only | - | - | - | - | - |
| `lib/executors/common.mjs:127` | `<agent> --version` (detect/health) | no | no | scrubbed | - | 15 s |
| `lib/subagent_hook.mjs:176` | `git -C <dir> rev-parse --git-common-dir` | `<dir>` may be the model-claimed cwd, passed only as an argument | no | inherited | - | 10 s |
| `lib/subagent_hook.mjs:277` | `process.execPath <bin> hook subagent-stop` (detached worker) | no (fixed args; the job file path goes via env) | no | inherited | - | worker enforces 60 s |

### Network egress, before (unchanged)

| Site | Method | Destination | What is sent | Auth | Guard |
|---|---|---|---|---|---|
| `lib/proxy.mjs:101` | POST | the `url` | one JSON-RPC line verbatim | bearer | loopback-only `http://` rule |
| `lib/proxy.mjs:173` | GET | the `url` | none | bearer | 405/404 treated as absent |
| `lib/proxy.mjs:196` | DELETE | the `url` | none | bearer | 2 s abort, session-only |
| `bin/stealthlab-mcp.mjs:164` (was :117) | POST | the `url` | probe payload | bearer | 15 s timeout |
| `bin/stealthlab-mcp.mjs:178` (was :131) | DELETE | the `url` | none | bearer | - |
| `lib/hook.mjs:60`, `:78` (from `2363b26`; not in the spec's table) | POST / DELETE | the `url` | the `find_ways` query from the user's prompt | bearer | fail-open, timeout |

### Network egress, after (new rows)

| Site | Method | Destination | What is sent | Auth | Guard / on failure |
|---|---|---|---|---|---|
| `lib/exec/hosted.mjs:61` | POST (initialize, initialized, tools/call) | the same `url` via `config.mjs` `resolveSettings` | `recommend_models`: `candidates` (`model\|executor` from exec.json), `goal_id`/`procedure_id`, `step_order`, `check_kind`. `report_model_run`: only `evidence.mjs` `REPORT_FIELDS`, strings passed through `redact` | bearer (saved token); recommend anonymous without one | 10 s abort (8 s for recommend); `http://` refused for non-loopback; failure -> outbox (report) or `default_order` (selection) |
| `lib/exec/hosted.mjs:93` | DELETE | same | none | bearer | best-effort |

**Never sent:** task text, summaries, diffs, check output. **No hardcoded host** in the code of `lib/`, `bin/` or
`scripts/` (re-grepped). The only URLs are record strings in the adapters' `VERIFIED_WITH.source`/`DOCS_SOURCE`
and comments in `lib/agents/*.md`; nothing fetches them.

**Paths written outside a worktree** (mode; reversed by `uninstall`?):
- `~/.claude/agents/stealth-executor.md`, `stealth-delegator.md`: 0600, with a `# stealthlab-mcp:managed` marker; a same-named file without the marker is refused. Yes, removed.
- `~/.claude/settings.json`: existing mode kept (new file 0600); `.bak` written before each change. Yes, entries removed; the `.bak` stays.
- `$STEALTHLAB_HOME` (default `~/.stealthlab`), dirs 0700 / files 0600. Not removed by `uninstall` (user data):
  - `runs/<id>/`: `run.json`, `events.jsonl`, `aN.std{out,err}.log`, all redacted per line; `aN.patch` is raw (it is the product).
  - `outbox/{,held/,rejected/}`: whitelisted, redacted fields only.
  - `hooks.log` (rotated at 1 MB), `hooks/subagents/*.json` (node id + line; deleted after 24 h), `hooks/jobs/*.json`.
- `os.tmpdir()/stealth-runs/<id>-aN` worktrees (0700) plus `.git/worktrees/<name>` admin data. Removed when cancelled or failed-without-diff; verified runs kept 7 days, then garbage-collected. Loose git objects from `add -A`/`write-tree` stay unreachable until the user's normal `git gc`.

**Unverified adapters:** `select.mjs` `refusalReason` runs inside `probe()`. `selectUnits` throws for an explicit
unrunnable executor and never makes one a candidate, and `runtime.runAttempt` re-checks before creating a
worktree or spawning. Tests assert the agent's pid file is never created. Gemini and OpenHands are refused today.

**Secrets:** `redact()` runs on check commands and tails, summary (<= 1,200 chars), `learned` (<= 5), stdout/stderr
logs, `events.jsonl`, the task/checks in `run.json`, error strings, and outbox/hosted payloads. The saved token is
also redacted as a literal. `childEnv` keeps StealthLab credentials out of every agent's environment.

**ToS boundary, enforced in code:**
- Agents are spawned only as local child processes of the user's own binaries (`common.mjs` `resolveBin` on the user's PATH). There is no session relay anywhere.
- `childEnv`/`scrubEnv` pass the user's own agent keys only to the user's own agent.
- The only egress is the user's own StealthLab endpoint.
- The README states the rule: only your own locally installed agents and logins; never proxied, shared, pooled or relayed.

## (h) Next steps, in order

1. **Configure models so evidence is sent, not held.** Seed `exec.json` `models` per executor, and read the model back from each agent's JSON where it reports one. Until then every `report_model_run` from `achieve` stays in `outbox/held/`, and the recommender never learns.
2. **Resolve Q-EXEC-1** (don't report cancelled race losers) before racing is used for real, or the recommender learns against slower-but-correct executors. Racing stays opt-in (`race=2`) or automatic only on a wide interval.
3. **Q-EXEC-3: register `stealthlab-exec` for Cursor/Codex/Cline.** It is a small `clients.mjs` change and extends the layer beyond Claude Code.
4. **Run the suite on Node 18.17 in CI (POSIX)** to confirm the engines claim, and settle Q-EXEC-4 for Windows.
5. **Containers (phase 2):** required before enabling OpenHands (always auto-approve) and for untrusted repos.
6. **ACP adapter (phase 2):** only after 1-4, starting with OpenCode (`opencode acp`), behind the same adapter interface.
7. **Plugin manifest:** last. It can only carry agent definitions and skills; hooks and MCP stay installer-managed.

Newly possible: measuring **agent + model** success per Goal (`scaffold` = executor) through the same
`report_model_run` table the recommender already fits. Newly urgent: the production routing tables. Migrations
120-124 are pending in production, so hosted reports would currently be queued, not stored.

**With one more day:** do step 1 and wire the SWE-bench KP arm to call `achieve` with OpenCode on a free
provider, to get the first real per-goal executor evidence.

### Completeness checklist

- [x] (a) Every requirement from §§1-7 has a row, with a reason for each `partial`/`not done`: §3 Node 18 run, §5 ACP, §6 non-Claude registration, `exec enable`, §6.4 plugin, §7 containers.
- [x] (b) One row per adapter (7). Gemini and OpenHands are marked unverified (not installed) and refuse to run. Every flag has a `--help` or doc URL source.
- [x] (c) All 6 tools' schemas as dumped from the live server's `tools/list`; 12 deviations listed.
- [x] (d) All three suites, before/after, from real runs: npm 17 -> 93 (90 pass / 0 fail / 3 skip); packaging 62/33 -> 62/33; backend 15 failed at HEAD (twice, same ids) -> 15 after. The delta rule holds: no new failures. New tests are mapped to §8 items 1-13.
- [x] (e) Artifacts pasted: the run_result JSON (verified), the outbox payload `scaffold="opencode"`, and the settings.json before/after.
- [x] (f) Conflicts plus 4 board questions with line numbers 4777/4783/4787/4791; 5 pre-existing issues confirmed.
- [x] (g) Both tables cover every spawn and egress site in `lib/`, `bin/`, `install/` and `scripts/` (re-grepped 2026-09-28), old and new.
- [x] (h) Ordered next steps.
- [x] Pre-existing §9 issues confirmed, not silently fixed.
- [x] No new runtime dependency; `files` unchanged (verified in `package.json`).
- [x] Every count is a real run (npm 93/90/0/3; packaging 62/33 before and after; backend 3880/14 then 3879/15 x2 at HEAD, 3879/15 after).
