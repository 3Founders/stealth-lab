// Test-only stand-in for a coding agent. Behaviour comes from env EXEC_STUB_SCENARIO (JSON):
//   edits: [{path, content}]   files to write (relative to cwd = the worktree)
//   pidFile: path              append "agent <pid>" (and "grandchild <pid>") lines here
//   spawnGrandchild: bool      spawn a node child that sleeps 120 s (tests tree kill)
//   printSecret: bool          print a fake secret (tests redaction)
//   chatterMs: n               print a line every 100 ms for n ms (busy, never "hung")
//   silentMs: n                print one line, then stay silent for n ms (tests hang detection)
//   sleepMs: n                 plain wait before finishing
//   finalMessage, learned[]    reported in the last stdout line as JSON
//   exitCode: n
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";

const sc = JSON.parse(process.env.EXEC_STUB_SCENARIO || "{}");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const note = (line) => { if (sc.pidFile) fs.appendFileSync(sc.pidFile, line + "\n"); };

note(`agent ${process.pid}`);
console.log("stub agent starting");
if (sc.printSecret) console.log("using key sk-test-ABCDEFGHIJKLMNOPQRSTUVWX for the call");
for (const e of sc.edits || []) {
  const p = path.join(process.cwd(), e.path);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, e.content);
}
if (sc.spawnGrandchild) {
  const gc = spawn(process.execPath, ["-e", "setTimeout(() => {}, 120000)"], { stdio: "ignore" });
  note(`grandchild ${gc.pid}`);
}
if (sc.chatterMs) {
  const end = Date.now() + sc.chatterMs;
  while (Date.now() < end) { console.log(`working ${Date.now()}`); await wait(100); }
}
if (sc.silentMs) await wait(sc.silentMs);
if (sc.sleepMs) await wait(sc.sleepMs);
console.log(JSON.stringify({ final: sc.finalMessage || "done", learned: sc.learned || [] }));
process.exit(sc.exitCode || 0);
