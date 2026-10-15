// For the pipeline survey, stage 3: one cheap model on one issue, through the real executor (worktree, agent, limits), with
// the task text dispatch would send (the request + the "likely place" line). No check gates it here: the survey judges the
// answer afterwards, both by the tests StealthLab wrote and by the real hidden tests.
// argv: <root> <prompt file> <where file> <executor: stealth|claude> <model>. Prints {state, patch, files, secs, cost_usd, tokens}.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [root, promptFile, whereFile, executor, model] = process.argv.slice(2);
const npm = process.env.STEALTHLAB_NPM_DIR;
const { ExecRuntime } = await import(pathToFileURL(path.join(npm, "lib", "exec", "runtime.mjs")).href);
const { withWhere } = await import(pathToFileURL(path.join(npm, "lib", "dispatch.mjs")).href);
const prompt = fs.readFileSync(promptFile, "utf8").trim();
const where = fs.existsSync(whereFile) ? fs.readFileSync(whereFile, "utf8").trim() : "";
const task = withWhere(prompt.length > 1900 ? `${prompt.slice(0, 1880)} [...]` : prompt, where);
const rt = new ExecRuntime({ env: process.env });
const t0 = Date.now();
let r;
try {
  const st = await rt.achieve({ repo_path: root, task, checks: ["python -c \"print('done')\""], scope: ["*", "**/*"], executor, model,
                                base: "working-tree", timeout_s: Number(process.env.SURVEY_AGENT_TIMEOUT_S || 600) });
  do { r = await rt.runResult(st.run_id, 55); } while (!["verified", "failed", "timed_out", "cancelled", "error"].includes(r?.state));
} catch (err) {
  process.stdout.write(JSON.stringify({ state: "error", error: String(err.message).slice(0, 300) }));
  process.exit(0);
}
let patch = "";
try { if (r.diff?.patch_path) patch = fs.readFileSync(r.diff.patch_path, "utf8"); } catch { /* none */ }
process.stdout.write(JSON.stringify({ state: r.state, patch, files: r.diff?.files || [], secs: Math.round((Date.now() - t0) / 1000),
  cost_usd: r.cost_usd ?? null, tokens: r.tokens || null, error: r.error || null }));
process.exit(0);
