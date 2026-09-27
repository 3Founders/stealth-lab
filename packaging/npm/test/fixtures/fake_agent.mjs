#!/usr/bin/env node
// Fake coding agent for the executor tests. Reads the task on stdin and acts out
// the scenario in env STEALTHLAB_FAKE_SCENARIO (JSON, or "@<path>" to a JSON file):
//   edits            [{path, content}]  files written relative to cwd (dirs created)
//   sleepMs          number   busy period, printing a progress line every 200 ms
//   silentMs         number   period with NO output at all (drives hang detection)
//   exitCode         number   process exit code (default 0)
//   finalMessage     string   the agent's final message (default "done")
//   learned          string[] reported in the final JSON line
//   tokens           {in,out} reported in the final JSON line
//   spawnGrandchild  bool     spawn child -> grandchild that stay alive (tree-kill tests)
//   childLifeMs      number   upper bound on the descendants' life (default 120000)
//   pidFile          string   append "role pid" lines here (in addition to stdout)
//   printSecret      bool     print a fake token (redaction tests)
// Order: start line, descendants, sleepMs, silentMs, edits, final line, exit.
// Output format: free text, then {"type":"final","message","learned","tokens"}.
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";

export const FAKE_SECRET = "sk-test-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

const role = process.argv[2] === "--role" ? process.argv[3] : "agent";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function loadScenario() {
  const raw = process.env.STEALTHLAB_FAKE_SCENARIO || "{}";
  const text = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(text);
}

function notePid(s, who, pid) {
  process.stdout.write(`FAKE_PID ${who}=${pid}\n`);
  if (s.pidFile) fs.appendFileSync(s.pidFile, `${who} ${pid}\n`);
}

async function descendant(s) {
  // child spawns the grandchild; both just stay alive until killed (bounded).
  notePid(s, role, process.pid);
  if (role === "child") {
    spawn(process.execPath, [process.argv[1], "--role", "grandchild"], { stdio: "inherit", env: process.env });
  }
  setTimeout(() => process.exit(0), s.childLifeMs || 120000);
  setInterval(() => {}, 1 << 30);
}

async function readStdin() {
  if (process.stdin.isTTY) return "";
  let buf = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) buf += chunk;
  return buf;
}

async function main() {
  // `node --test` discovers every .mjs under test/, including this fixture. When
  // it is loaded that way (no scenario, not a descendant), do nothing.
  if (process.env.STEALTHLAB_FAKE_SCENARIO === undefined && role === "agent") return;
  const s = loadScenario();
  if (role !== "agent") return descendant(s);
  const task = await readStdin();
  const mi = process.argv.indexOf("--model");
  const model = mi > 0 ? process.argv[mi + 1] : "fake/default";
  process.stdout.write(`fake agent start model=${model} task_chars=${task.length}\n`);
  notePid(s, "agent", process.pid);
  if (s.spawnGrandchild) spawn(process.execPath, [process.argv[1], "--role", "child"], { stdio: "inherit", env: process.env });
  if (s.printSecret) process.stdout.write(`using token ${FAKE_SECRET}\n`);
  for (let t = 0; t < (s.sleepMs || 0); t += 200) {
    await sleep(Math.min(200, s.sleepMs - t));
    process.stdout.write(`working ${t}\n`);
  }
  if (s.silentMs) await sleep(s.silentMs);
  for (const e of s.edits || []) {
    const p = path.resolve(process.cwd(), e.path);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, e.content ?? "");
    process.stdout.write(`edited ${e.path}\n`);
  }
  const message = (s.finalMessage ?? "done") + (s.printSecret ? ` (key ${FAKE_SECRET})` : "");
  process.stdout.write(JSON.stringify({ type: "final", message, learned: s.learned || [], tokens: s.tokens || null }) + "\n");
  process.exitCode = s.exitCode || 0;
  if (!s.spawnGrandchild) return;
  // With descendants alive, keep the agent alive too until it is killed or they time out.
  await sleep(s.childLifeMs || 120000);
}

main().catch((err) => {
  process.stderr.write(`fake agent error: ${err.message}\n`);
  process.exit(3);
});
