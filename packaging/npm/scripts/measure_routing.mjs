// Measures how long routing takes (docs/latencycheck.md): the real prompt hook against a running kell server, with
// STEALTHLAB_ROUTING_TIMING=1, over three kinds of prompt. Everything up to the first cheap model is real: the /triage
// judge, find_ways and its model plan, dispatch's checks (with its quota lookup), the brief and the locate step. The one
// stand-in is the cheap model's run itself: it fails at once, so a prompt ends right after routing and no cheap model
// works on (or is billed for) the task. Prompt text is never written; the timing file holds lengths and hashes.
//
//   node scripts/measure_routing.mjs --url http://127.0.0.1:8765/mcp --token-file <file> --out <dir> [--label warm]
//        [--kinds change,question,nostealth] [--repeat 1] [--limit n] [--keys backend/.env]
//
// Model keys (General Compute, z.ai) are read from --keys for dispatch's quota lookup and are never printed.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { runPromptHook } from "../lib/hook.mjs";
import { dispatch } from "../lib/dispatch.mjs";
import { routingTimingFile } from "../lib/routing_timing.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const arg = (name, dflt) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : dflt; };

const url = arg("url", "http://127.0.0.1:8765/mcp");
const token = fs.readFileSync(arg("token-file"), "utf8").trim();
const out = path.resolve(arg("out", fs.mkdtempSync(path.join(os.tmpdir(), "routing-timing-"))));
const label = arg("label", "warm");
const kinds = arg("kinds", "change,question,nostealth").split(",");
const repeat = Number(arg("repeat", "1"));
const limit = Number(arg("limit", "100000"));         // stop after this many prompts (a cold run: 1)

// Prompts (their text stays in this file; the timing rows hold only length and hash)
export const PROMPTS = {
  change: [
    "Fix add() in calc.py: it subtracts instead of adding.",
    "calc.py's divide() crashes on zero; make it raise ValueError with a clear message instead.",
    "Add a multiply(a, b) function to calc.py with a test for it.",
    "The mean() in stats.py returns the wrong value for an empty list; it should return 0.",
    "Rename the helper _sq in calc.py to square and update every caller.",
    "stats.py median() is wrong for lists with an even number of items. Fix it.",
    "Make clamp() in calc.py accept low > high by swapping them.",
    "Add type hints to every function in stats.py.",
    "When parse_int in calc.py gets ' 42 ' with spaces it fails; strip whitespace first.",
    "Add a percentile(values, p) function to stats.py, with tests.",
    "calc.power() returns a float for integer inputs; return an int when both inputs are ints.",
    "Write a test that covers calc.divide with negative numbers and fix anything it finds.",
  ],
  question: [
    "What does stats.py do?",
    "Why would someone use a tuple instead of a list in Python?",
    "thanks, that looks good",
    "Can you explain how median() works here?",
    "ok, continue",
    "Which function in calc.py is the most complex?",
    "Is it better to raise or return None on bad input?",
    "What test framework does this project use?",
    "great, nice work",
    "How would you structure a bigger version of this project?",
  ],
  nostealth: [
    "Fix add() in calc.py: it subtracts instead of adding.",
    "Add a multiply(a, b) function to calc.py with a test for it.",
    "The mean() in stats.py returns the wrong value for an empty list; it should return 0.",
    "Make clamp() in calc.py accept low > high by swapping them.",
    "Add type hints to every function in stats.py.",
    "Add a percentile(values, p) function to stats.py, with tests.",
    "stats.py median() is wrong for lists with an even number of items. Fix it.",
    "calc.power() returns a float for integer inputs; return an int when both inputs are ints.",
  ],
};

const CALC = `def add(a, b):\n    return a - b\n\n\ndef divide(a, b):\n    return a / b\n\n\ndef _sq(x):\n    return x * x\n\n\ndef clamp(x, low, high):\n    return max(low, min(x, high))\n\n\ndef parse_int(s):\n    return int(s)\n\n\ndef power(a, b):\n    return float(a ** b)\n`;
const STATS = `def mean(values):\n    return sum(values) / len(values)\n\n\ndef median(values):\n    v = sorted(values)\n    return v[len(v) // 2]\n`;
const TESTS = `from calc import add, divide\n\n\ndef test_divide():\n    assert divide(6, 3) == 2\n`;

function project(dir, { git }) {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "calc.py"), CALC);
  fs.writeFileSync(path.join(dir, "stats.py"), STATS);
  fs.writeFileSync(path.join(dir, "test_calc.py"), TESTS);
  if (git) {
    const g = (...a) => execFileSync("git", ["-C", dir, ...a], { stdio: "pipe" });
    g("init", "-q"); g("config", "user.email", "t@e.invalid"); g("config", "user.name", "t");
    fs.mkdirSync(path.join(dir, ".kell"), { recursive: true });
    fs.writeFileSync(path.join(dir, ".kell", ".keep"), "");
    g("add", "-A"); g("commit", "-q", "-m", "init");
  }
  return dir;
}

function keysFrom(file) {
  const env = {};
  try {
    for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Za-z0-9_]+)\s*=\s*(.*)\s*$/);
      if (m) env[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  } catch { /* no keys: dispatch's quota lookup skips the providers */ }
  return { ...(env.GENERAL_COMPUTE_API_KEY ? { GENERAL_COMPUTE_API_KEY: env.GENERAL_COMPUTE_API_KEY } : {}),
           ...(env.Z_AI_KEY ? { Z_AI_KEY: env.Z_AI_KEY } : {}) };
}

// the cheap model's run: fails at once, so routing is all a prompt measures
const failFast = {
  async achieve() { return { run_id: `stub-${Math.random().toString(36).slice(2, 8)}` }; },
  async runResult(id) { return { run_id: id, state: "failed", cost_usd: 0, checks: [{ tail: "stub: not run (routing measurement)" }] }; },
  async applyRun() { throw new Error("stub: never applied"); },
  async cancelRun() {},
  async finishRun() {},
};

const home = path.join(out, "home");
fs.mkdirSync(home, { recursive: true });
const execCfg = JSON.parse(fs.readFileSync(path.join(REPO, "experiments", "abtest", "exec.json"), "utf8"));
fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify(execCfg, null, 2));
fs.writeFileSync(path.join(home, "config.json"), JSON.stringify({ dispatch: true }));
const units = Object.entries(execCfg.executors).flatMap(([ex, s]) => s.models.map((m) => `${m}|${ex}`));

const env = {
  ...process.env, ...keysFrom(path.resolve(REPO, arg("keys", "backend/.env"))),
  // the session's model, as the A/B harness sets it: the hook asks for a model plan only when it knows it
  ANTHROPIC_MODEL: process.env.ANTHROPIC_MODEL || "claude-opus-5-5",
  STEALTHLAB_HOME: home, STEALTHLAB_ROUTING_TIMING: "1", STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q",
  STEALTHLAB_HOOK_CANDIDATES: [...units, "claude-opus-5-5|claude-code"].join(","),
  STEALTHLAB_EXEC_WORKTREE_ROOT: path.join(out, "worktrees"), STEALTHLAB_AUTO_SURVEY: "off",
};
const withStealth = project(path.join(out, "repo"), { git: true });
const without = project(path.join(out, "plain"), { git: false });     // not a git repo: no .kell/ can be made

let n = 0;
for (let r = 0; r < repeat; r++) {
  for (const kind of kinds) {
    for (const prompt of PROMPTS[kind] || []) {
      if (n >= limit) break;
      const cwd = kind === "nostealth" ? without : withStealth;
      const payload = { prompt, cwd, session_id: `measure-${label}-${kind}-${r}-${n++}`, hook_event_name: "UserPromptSubmit" };
      const before = fs.existsSync(routingTimingFile(env)) ? fs.readFileSync(routingTimingFile(env), "utf8").split("\n").length : 1;
      await runPromptHook({
        stdinText: JSON.stringify(payload), settings: { url, token }, userAgent: "stealthlab-measure", env,
        write: () => {}, log: () => {},
        dispatchImpl: (a) => dispatch({ ...a, runtime: failFast }),
      });
      // tag the row just written with the prompt's kind and this run's label (no prompt text)
      const file = routingTimingFile(env);
      const lines = fs.readFileSync(file, "utf8").split("\n");
      if (lines.length > before) {
        const i = lines.length - 2;
        lines[i] = JSON.stringify({ ...JSON.parse(lines[i]), kind, label });
        fs.writeFileSync(file, lines.join("\n"));
      }
      process.stdout.write(".");
    }
  }
}
process.stdout.write(`\n${n} prompts; rows in ${routingTimingFile(env)}\n`);
