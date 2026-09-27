# Executor layer: work split for 3 parallel builders (read with executor_layer_build_prompt.md)

The spec is `.scratch/prompts/executor_layer_build_prompt.md` (the working-tree version is authoritative). Three
builders work **at the same time in the same checkout**, so file ownership is strict. **Do not commit, stage or
run any git command that writes** (no add/commit/stash/checkout/reset). The integrator commits. Read-only git is
fine. Never edit a file another builder owns; if you need a change there, say so in your final report.

Constraints from the spec that everyone must respect: Node **>= 18.17** APIs only, **no dependencies** (only
`node:` built-ins), runtime code only under `packaging/npm/bin` or `packaging/npm/lib`, tests under
`packaging/npm/test` run with `node --test`, Windows + macOS + Linux (use `path`, handle `.cmd` shims, kill process
trees with `taskkill /T /F` on Windows and process groups elsewhere). Backend untouched.

## Ownership

| Builder | Owns (create/edit only these) |
|---|---|
| **A: runtime** | `packaging/npm/lib/exec/**` (runstore, worktree, verify, evidence/outbox, redact, select, server), `packaging/npm/test/exec_*.test.mjs`, `packaging/npm/test/fixtures/exec/**` |
| **B: adapters** | `packaging/npm/lib/executors/**`, `packaging/npm/test/executors*.test.mjs`, `packaging/npm/test/fixtures/fake_agent.mjs`, `packaging/npm/test/fixtures/executors/**` |
| **C: Claude integration + CLI** | `packaging/npm/lib/claude_exec.mjs`, `packaging/npm/lib/subagent_hook.mjs`, `packaging/npm/lib/agents/**` (agent definition templates), `packaging/npm/bin/stealthlab-mcp.mjs` (wiring only), `packaging/npm/lib/clients.mjs` (only if install needs it; it has fresh changes from 2363b26, so preserve them), `packaging/npm/README.md`, `packaging/npm/test/claude_exec*.test.mjs`, `packaging/npm/test/subagent_hook*.test.mjs` |
| Integrator | `packaging/npm/EXECUTOR_SUMMARY.md`, `package.json`, commits, board |

## Interfaces (fixed: build against these exactly)

**Adapters (B provides, A consumes)**: `packaging/npm/lib/executors/index.mjs`
```js
export const ADAPTERS;                       // { opencode, codex, claude, gemini, openhands, cline, fake }
export function getAdapter(id, { env } = {}) // throws for unknown id; "fake" only when env.STEALTHLAB_EXEC_ALLOW_FAKE === "1"
```
Each adapter (default export of `lib/executors/<id>.mjs`), per spec section 5:
```js
{ id,
  VERIFIED_WITH,                               // { version, date, source } or null => the adapter refuses to run
  async detect({ env }) -> { installed, version, bin },   // bin = resolved executable path (Windows .cmd aware)
  buildCommand({ task, model, worktree, timeoutS, env }) -> { cmd, args, env, stdinText },
  parseOutput({ stdout, stderr, exitCode }) -> { finalMessage, learned: string[], tokens: {in,out}|null, costUsd|null },
  async health({ env }) -> { healthy, detail } }
```
`lib/executors/fake.mjs` runs `node test/fixtures/fake_agent.mjs` with a scenario given in env
`STEALTHLAB_FAKE_SCENARIO` (JSON: `{ "edits": [{"path","content"}], "sleepMs", "silentMs", "exitCode",
"finalMessage", "spawnGrandchild": bool, "printSecret": bool }`), so A's tests can drive every behaviour.

**Runtime (A provides, C consumes)**
```js
// lib/exec/server.mjs
export async function runExecServer({ stdin = process.stdin, stdout = process.stdout, env = process.env,
                                      adapters = undefined /* injectable for tests */ });
// MCP stdio server (JSON-RPC 2.0, newline-delimited, protocol per proxy.mjs conventions) exposing
// list_executors, achieve, run_status, run_result, apply_run, cancel_run (spec section 4 contracts).
// lib/exec/evidence.mjs
export async function reportModelRun(payload, { env } = {});   // hosted report_model_run, else queue to outbox
export async function flushOutbox({ env } = {});
// lib/exec/redact.mjs
export function redact(text);                                    // same patterns as backend trace_redaction.py
// lib/exec/verify.mjs
export async function runChecks({ cwd, checks, timeoutS, env }) -> [{ cmd, exit, seconds, tail }];
```
State dirs: `~/.stealthlab/runs/<run_id>/`, `~/.stealthlab/outbox/`, `~/.stealthlab/exec.json` (config), all
overridable with env `STEALTHLAB_HOME` (tests MUST set it to a temp dir).

**CLI (C wires in bin)**: `stealthlab-mcp exec` -> `runExecServer()`; `stealthlab-mcp hook subagent-start|subagent-stop`
-> `lib/subagent_hook.mjs`; `install --with-exec` / `uninstall` -> `lib/claude_exec.mjs` (writes/removes
`~/.claude/agents/stealth-executor.md`, `~/.claude/agents/stealth-delegator.md`, the hooks entries in
`~/.claude/settings.json` (merge, never clobber) and the local `stealthlab-exec` MCP entry). Honour env
`CLAUDE_CONFIG_DIR` / a `home` override for tests; never touch the real `~/.claude` in tests.

## Final report (each builder)

Return: (1) files created/changed; (2) your slice of spec sections mapped to done/partial/not done; (3) exact test
command and counts (before/after for the whole `node --test` suite in packaging/npm); (4) conflicts with the spec
or code; (5) for B: the verified adapter table (agent, installed version or "not installed", exact headless
command, JSON support, auto-approve flag, source + date); for C: the before/after settings.json diff from the
install/uninstall test; for A: the security review items (every spawn, every write outside a worktree, every
network egress, and its guard).
