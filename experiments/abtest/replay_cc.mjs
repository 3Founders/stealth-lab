// One cheap try driven by lean Claude Code through kell's bridge (lib/executors/claude.mjs buildBridged), for
// replay_cheap.py's "cc" variants: stdin {task, profile_name, profiles, timeout_s} -> runs in the current directory ->
// stdout one JSON line {stop, steps, costUsd, message, tokens}. The provider key comes in the environment (the profile's
// key_env), never on a command line; the Claude Code child gets only the bridge's per-process secret.
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const npm = process.env.STEALTHLAB_NPM_DIR || path.join(here, "..", "..", "packaging", "npm");
const lib = (p) => import(pathToFileURL(path.join(npm, "lib", p)).href);
const { default: claude } = await lib("executors/claude.mjs");
const { normaliseProfiles } = await lib("executors/profile.mjs");
const { takeBridgeUsage, closeBridge } = await lib("anthropic_bridge.mjs");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const profile = normaliseProfiles(input.profiles)[input.profile_name];
const tag = `replay-${Date.now()}`;
const cmd = await claude.buildCommand({ task: input.task, worktree: process.cwd(), env: process.env, profile, runTag: tag });
const t0 = Date.now();
const child = spawn(cmd.cmd, cmd.args, { cwd: process.cwd(), env: Object.fromEntries(Object.entries(cmd.env).filter(([, v]) => v !== undefined)),
                                         stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
let out = "", err = "";
child.stdout.on("data", (d) => { out += d; });
child.stderr.on("data", (d) => { err += d; });
let timedOut = false;
const timer = setTimeout(() => { timedOut = true; child.kill("SIGKILL"); }, (input.timeout_s || 600) * 1000);
const code = await new Promise((r) => child.on("close", r));
clearTimeout(timer);
if (input.transcript) fs.writeFileSync(input.transcript, out);
const parsed = claude.parseOutput({ stdout: out, stderr: err });
const usage = takeBridgeUsage(tag);
const p = profile;
const cost = usage ? ((usage.in || 0) * (p.input_per_mtok || 0) + (usage.out || 0) * (p.output_per_mtok || 0)) / 1e6 : null;
const steps = (out.match(/"type":"tool_use"/g) || []).length;
process.stdout.write(JSON.stringify({ type: "final", stop: timedOut ? "timed_out" : code === 0 ? "finished" : `exit ${code}`, steps,
  costUsd: cost, message: String(parsed.finalMessage || err.slice(-300)), tokens: usage, seconds: (Date.now() - t0) / 1000 }) + "\n");
closeBridge();
