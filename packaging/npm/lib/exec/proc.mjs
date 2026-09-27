// Process handling shared by executor runs and check commands.
//
// - The child env is the parent env minus every STEALTHLAB_* variable (the StealthLab token must never
//   reach a third-party agent or a repo's test command) and minus GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE
//   (so a run launched from inside a git hook cannot be redirected at another repository). PATH, HOME
//   and the agent's own config dirs / provider keys are inherited on purpose: the agent runs with the
//   user's OWN login and keys, on the user's own machine -- nothing is pooled or relayed (ToS boundary).
// - Hang detection: no stdout/stderr byte for hangS -> the tree is killed, reason "hang".
// - Hard timeout: timeoutS -> the tree is killed, reason "timeout".
// - Tree kill: Windows `taskkill /PID <pid> /T /F`; POSIX the child leads its own process group
//   (detached) and the whole group gets SIGTERM then SIGKILL. After a NORMAL exit the POSIX group is
//   also swept, so a grandchild the agent left behind does not outlive the run. HONEST LIMIT: on Windows
//   a grandchild that outlives its parent is orphaned from the /T tree and is not swept (Node exposes no
//   job objects); hang/timeout kills happen while the parent is alive, so those paths are covered.
// - Windows `.cmd`/`.bat` shims: Node >= 18.20.2 refuses to spawn them without a shell, and cmd.exe
//   cannot quote arbitrary text (a task string) safely. So an npm cmd-shim is resolved to the program it
//   wraps and that program is spawned directly with the original argv (no shell). A shim that cannot be
//   resolved is run through cmd.exe ONLY when no argument contains a character cmd.exe would interpret;
//   otherwise the spawn is refused with a clear error rather than risking injection.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const IS_WIN = process.platform === "win32";

export function childEnv(env = process.env, extra = {}) {
  const out = {};
  for (const [k, v] of Object.entries(env)) {
    if (v === undefined) continue;
    if (/^STEALTHLAB_/i.test(k)) continue;
    if (/^(GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE|GIT_OBJECT_DIRECTORY|GIT_COMMON_DIR)$/i.test(k)) continue;
    out[k] = v;
  }
  for (const [k, v] of Object.entries(extra || {})) {
    // An adapter may add its own variables (e.g. the test-only STEALTHLAB_FAKE_SCENARIO), but it can never
    // re-inject StealthLab's credentials or endpoint, even by copying the whole parent env.
    if (/^STEALTHLAB_/i.test(k) && (/TOKEN|SECRET|KEY|PASSWORD|MCP_URL|HOME/i.test(k) || env[k] === v && k !== "STEALTHLAB_FAKE_SCENARIO")) continue;
    if (v === undefined || v === null) delete out[k];
    else out[k] = String(v);
  }
  return out;
}

function envGet(env, name) {
  const key = Object.keys(env).find((k) => k.toUpperCase() === name.toUpperCase());
  return key ? env[key] : undefined;
}

// Resolve a bare command name against PATH (+PATHEXT on Windows). Absolute/relative paths pass through.
export function resolveOnPath(cmd, env = process.env) {
  if (!cmd || cmd.includes("/") || cmd.includes("\\")) return cmd;
  const dirs = String(envGet(env, "PATH") || "").split(path.delimiter).filter(Boolean);
  const exts = IS_WIN
    ? (path.extname(cmd) ? [""] : String(envGet(env, "PATHEXT") || ".COM;.EXE;.BAT;.CMD").split(";").filter(Boolean))
    : [""];
  for (const d of dirs) {
    for (const e of exts) {
      const p = path.join(d, cmd + e.toLowerCase());
      try { if (fs.statSync(p).isFile()) return p; } catch { /* next */ }
    }
  }
  return cmd;
}

// npm cmd-shim: `"%_prog%"  "%dp0%\node_modules\pkg\bin\cli.js" %*` (with _prog = node) or
// `"%dp0%\node_modules\pkg\bin\tool.exe"   %*`. Returns {cmd, prefixArgs} or null.
export function resolveCmdShim(file) {
  let text;
  try { text = fs.readFileSync(file, "utf8"); } catch { return null; }
  const dir = path.dirname(file);
  const m = text.match(/"%(?:~?dp0)%\\?([^"]+)"\s+%\*/i);
  if (!m) return null;
  const target = path.resolve(dir, m[1].replace(/\\/g, path.sep));
  if (!fs.existsSync(target)) return null;
  if (/\.exe$/i.test(target)) return { cmd: target, prefixArgs: [] };
  if (/\.(cmd|bat)$/i.test(target)) return null;
  const usesNode = /%_prog%|node(\.exe)?"?\s/i.test(text) || /\.(m?js|cjs)$/i.test(target);
  if (!usesNode) return null;
  const localNode = path.join(dir, "node.exe");
  return { cmd: fs.existsSync(localNode) ? localNode : process.execPath, prefixArgs: [target] };
}

const CMD_UNSAFE = /[%!"^&|<>()\r\n]/;

export function planSpawn(cmd, args, env) {
  const resolved = resolveOnPath(cmd, env);
  if (IS_WIN && /\.(cmd|bat)$/i.test(resolved)) {
    const shim = resolveCmdShim(resolved);
    if (shim) return { cmd: shim.cmd, args: [...shim.prefixArgs, ...args], shell: false };
    const bad = args.find((a) => CMD_UNSAFE.test(String(a)));
    if (bad !== undefined) {
      throw new Error(`refusing to run ${path.basename(resolved)} through cmd.exe: an argument contains a character ` +
        "cmd.exe would interpret (%, !, \", ^, &, |, <, >, parentheses or a newline) and the shim could not be resolved " +
        "to the program it wraps. Pass the task on stdin in the adapter instead.");
    }
    const line = [resolved, ...args].map((a) => `"${a}"`).join(" ");
    return { cmd: process.env.ComSpec || "cmd.exe", args: ["/d", "/s", "/c", `"${line}"`], shell: false, verbatim: true };
  }
  return { cmd: resolved, args, shell: false };
}

export function killTree(pid, { signal = "SIGKILL" } = {}) {
  if (!pid) return;
  if (IS_WIN) {
    spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
    return;
  }
  try { process.kill(-pid, signal); } catch { try { process.kill(pid, signal); } catch { /* gone */ } }
}

export function isAlive(pid) {
  try { process.kill(pid, 0); return true; } catch (err) { return err.code === "EPERM"; }
}

// spawnManaged -> { pid, done: Promise<{exitCode, signal, reason, error}>, kill(reason) }
// reason: "exit" | "hang" | "timeout" | "cancelled" | "spawn_error"
export function spawnManaged({ cmd, args = [], cwd, env, stdinText, shellCommand, hangS, timeoutS, onStdout, onStderr }) {
  let plan;
  if (shellCommand !== undefined) {
    // A check command: run verbatim through the platform shell BY DESIGN (it is the step's own check,
    // written by the orchestrator). This is an injection surface and is documented as one.
    plan = { cmd: shellCommand, args: [], shell: true };
  } else {
    plan = planSpawn(cmd, args, env || process.env);
  }
  let child;
  try {
    child = spawn(plan.cmd, plan.args, {
      cwd, env, shell: plan.shell, windowsHide: true, detached: !IS_WIN,
      windowsVerbatimArguments: !!plan.verbatim,
      stdio: [stdinText !== undefined && stdinText !== null ? "pipe" : "ignore", "pipe", "pipe"],
    });
  } catch (err) {
    return { pid: undefined, kill() {}, done: Promise.resolve({ exitCode: null, signal: null, reason: "spawn_error", error: err.message }) };
  }
  let reason = null;
  let hangTimer = null;
  let hardTimer = null;
  const pid = child.pid;

  const kill = (why) => {
    if (reason) return;
    reason = why;
    killTree(pid, { signal: "SIGTERM" });
    if (!IS_WIN) setTimeout(() => killTree(pid, { signal: "SIGKILL" }), 1500).unref();
  };
  const armHang = () => {
    if (!hangS) return;
    clearTimeout(hangTimer);
    hangTimer = setTimeout(() => kill("hang"), hangS * 1000);
    hangTimer.unref?.();
  };
  if (timeoutS) {
    hardTimer = setTimeout(() => kill("timeout"), timeoutS * 1000);
    hardTimer.unref?.();
  }
  armHang();
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (d) => { armHang(); onStdout?.(d); });
  child.stderr.on("data", (d) => { armHang(); onStderr?.(d); });
  if (child.stdin) {
    child.stdin.on("error", () => {});
    child.stdin.end(String(stdinText));
  }

  const done = new Promise((resolve) => {
    let settled = false;
    const finish = (res) => {
      if (settled) return;
      settled = true;
      clearTimeout(hangTimer);
      clearTimeout(hardTimer);
      resolve(res);
    };
    child.on("error", (err) => finish({ exitCode: null, signal: null, reason: "spawn_error", error: err.message }));
    // "exit", not "close": a grandchild holding the stdout pipe open must not keep the run alive.
    child.on("exit", (code, sig) => {
      clearTimeout(hangTimer);
      clearTimeout(hardTimer);
      if (!IS_WIN) killTree(pid, { signal: "SIGKILL" }); // sweep the group (stragglers)
      const res = { exitCode: code, signal: sig, reason: reason || "exit" };
      let closed = false;
      child.on("close", () => { closed = true; finish(res); });
      setTimeout(() => {
        if (closed) return;
        child.stdout.destroy();
        child.stderr.destroy();
        finish(res);
      }, 1000).unref();
    });
  });
  return { pid, done, kill };
}
