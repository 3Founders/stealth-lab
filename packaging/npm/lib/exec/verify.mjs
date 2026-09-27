// The verifier: the step's own checks, run in the run's worktree, independently of anything the executor
// claimed. A check is a shell command that must exit 0. It is run VERBATIM through the platform shell by
// design (it is the orchestrator's check, e.g. `python -m pytest -q`): that is an injection surface in the
// sense that whoever writes `checks` chooses what runs -- the same trust as the orchestrator itself, which
// can already run shell commands. It never runs in the user's checkout, only in the worktree.
import { childEnv, spawnManaged } from "./proc.mjs";
import { redact } from "./redact.mjs";

const TAIL_LINES = 40;
const KEEP_BYTES = 256 * 1024;

export function tailLines(text, n = TAIL_LINES) {
  const lines = String(text).replace(/\r\n/g, "\n").replace(/\n+$/, "").split("\n");
  return lines.slice(-n).join("\n");
}

export async function runChecks({ cwd, checks, timeoutS = 600, env = process.env, secrets = [], onEvent, onSpawn,
                                  shouldStop } = {}) {
  const results = [];
  for (const cmd of checks || []) {
    if (shouldStop?.()) break;
    const started = Date.now();
    let out = "";
    const keep = (d) => {
      out += d;
      if (out.length > KEEP_BYTES * 2) out = out.slice(-KEEP_BYTES);
    };
    onEvent?.({ event: "check_start", cmd: redact(cmd, { secrets }) });
    const h = spawnManaged({ shellCommand: cmd, cwd, env: childEnv(env), timeoutS, onStdout: keep, onStderr: keep });
    onSpawn?.(h);
    const r = await h.done;
    onSpawn?.(null);
    if (r.reason === "cancelled") out += "\n[stealthlab: check killed: run cancelled]";
    let exit = r.exitCode;
    if (r.reason === "timeout") {
      exit = exit === 0 || exit === null ? 124 : exit;
      out += `\n[stealthlab: check killed after ${timeoutS}s timeout]`;
    }
    if (r.reason === "spawn_error") {
      exit = 127;
      out += `\n[stealthlab: could not start check: ${r.error}]`;
    }
    if (exit === null) exit = 128;
    const res = {
      cmd: redact(cmd, { secrets }),
      exit,
      seconds: Math.round((Date.now() - started) / 100) / 10,
      tail: redact(tailLines(out), { secrets }),
    };
    results.push(res);
    onEvent?.({ event: "check_done", cmd: res.cmd, exit: res.exit, seconds: res.seconds });
  }
  return results;
}

// Scope globs: matched against repo-relative, forward-slash paths, anchored at the repo root.
//   `*`  any run of characters except "/"      `**` any run including "/" (`**/` also matches zero dirs)
//   `?`  one character except "/"              a trailing "/" means "everything under this directory"
// A pattern without a slash is NOT matched at every depth (unlike .gitignore): "calc.py" means the root
// file only. Scope is a safety boundary, so the narrow reading is the default.
export function globToRegExp(glob) {
  let g = String(glob).trim().replace(/\\/g, "/").replace(/^\.\//, "");
  if (g.endsWith("/")) g += "**";
  let re = "";
  for (let i = 0; i < g.length; i++) {
    const c = g[i];
    if (c === "*") {
      if (g[i + 1] === "*") {
        const slash = g[i + 2] === "/";
        re += slash ? "(?:.*/)?" : ".*";
        i += slash ? 2 : 1;
      } else {
        re += "[^/]*";
      }
    } else if (c === "?") {
      re += "[^/]";
    } else {
      re += c.replace(/[.+^${}()|[\]\\]/g, "\\$&");
    }
  }
  return new RegExp(`^${re}$`, process.platform === "win32" ? "i" : "");
}

export function scopeViolations(files, scope) {
  const res = (scope || []).map(globToRegExp);
  return (files || []).filter((f) => !res.some((re) => re.test(String(f).replace(/\\/g, "/"))));
}
