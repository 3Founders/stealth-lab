// The first survey of a repo, started by the prompt hook itself.
//
// `kell survey` writes .kell/claims.md (cited facts about the repo) and a worklist; until it has run the
// lookups have no repo facts to break ties with, and a user who installed with `install --with-exec` never runs it by
// hand. So the first prompt in a git repo starts it, once, DETACHED: the prompt never waits for it and a failure never
// shows (the hook exits 0 whatever happens here).
//
// Once per repo = until .kell/claims.md exists or the attempt marker .kell/.auto-survey does (written before the
// survey starts, so a survey that crashes is not retried on every prompt; delete the marker to try again). The survey
// itself is local and uploads nothing. STEALTHLAB_AUTO_SURVEY=off turns this off.
import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "kell.mjs");

// under `node --test` an unset switch is off: a test that runs the prompt hook in a temp repo must not start a survey
export const autoSurveyEnabled = (env = process.env) =>
  String(env.STEALTHLAB_AUTO_SURVEY || (process.env.NODE_TEST_CONTEXT ? "off" : "on")).toLowerCase() !== "off";

// the git work tree the prompt runs in, or null (not a repo: nothing to survey)
export function repoRoot(cwd) {
  if (!cwd) return null;
  try {
    const r = spawnSync("git", ["-C", cwd, "rev-parse", "--show-toplevel"], { encoding: "utf8", timeout: 3000, windowsHide: true });
    return r.status === 0 && r.stdout.trim() ? path.resolve(r.stdout.trim()) : null;
  } catch {
    return null;
  }
}

function detached(root, env) {
  // --fill: then a cheap model works the survey's worklist (lib/survey/fill.mjs), still detached
  const fill = (env.STEALTHLAB_SURVEY_FILL || "on").toLowerCase() !== "off" ? ["--fill"] : [];
  const child = spawn(process.execPath, [BIN, "survey", root, ...fill], { cwd: root, detached: true, stdio: "ignore", windowsHide: true, env });
  child.on("error", () => {});
  child.unref();
}

// -> {started: bool, reason?: string, root?: string}. Never throws.
export function maybeStartSurvey({ cwd, env = process.env, repoRootImpl = repoRoot, start = detached } = {}) {
  try {
    if (!autoSurveyEnabled(env)) return { started: false, reason: "off" };
    const root = repoRootImpl(cwd);
    if (!root) return { started: false, reason: "not a git repo" };
    const dir = path.join(root, ".kell");
    if (fs.existsSync(path.join(dir, "claims.md"))) return { started: false, reason: "already surveyed", root };
    const marker = path.join(dir, ".auto-survey");
    if (fs.existsSync(marker)) return { started: false, reason: "already attempted", root };
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(marker, `${new Date().toISOString()}\n`);
    start(root, env);
    return { started: true, root };
  } catch {
    return { started: false, reason: "error" };
  }
}
