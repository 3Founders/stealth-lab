// Would the early-stop rules (packaging/npm/lib/agent/stall.mjs) have stopped a try that went on to solve its issue?
// Replays each step-agent transcript of a replay run (run without the rules) through the StallDetector, call by call.
//   node stall_audit.mjs <dir with <issue>/<variant>.json and .transcript.jsonl>...
// The worktree's fingerprint is approximated: it changes on a successful edit or write (a shell command that changed files is
// not seen, so rules d/e may fire here a little earlier than they would in a real run -- the audit errs towards "would cut").
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const { StallDetector } = await import(pathToFileURL(path.join(here, "..", "..", "packaging", "npm", "lib", "agent", "stall.mjs")).href);
const rows = [];
for (const dir of process.argv.slice(2)) {
  for (const issue of fs.readdirSync(dir)) {
    const d = path.join(dir, issue);
    if (!fs.statSync(d).isDirectory()) continue;
    for (const f of fs.readdirSync(d).filter((x) => x.endsWith(".transcript.jsonl"))) {
      const variant = f.replace(".transcript.jsonl", "");
      if (variant.includes("cc")) continue;                     // Claude Code runs have no stall rules
      let res = {};
      try { res = JSON.parse(fs.readFileSync(path.join(d, `${variant}.json`), "utf8")); } catch { continue; }
      const det = new StallDetector();
      det.start("fp0");
      let fp = 0, n = 0, fired = null, lastEdit = 0;
      for (const line of fs.readFileSync(path.join(d, f), "utf8").split(/\r?\n/)) {
        let j;
        try { j = JSON.parse(line); } catch { continue; }
        if (!j.tool) continue;
        n++;
        const ok = !/^error:/.test(String(j.result || ""));
        const edit = (j.tool === "edit_file" || j.tool === "write_file") && ok;
        if (edit) { fp++; lastEdit = n; }
        const r = det.observe({ name: j.tool, args: j.args || {}, result: String(j.result || ""), hash: edit ? `h${fp}` : null,
                                fingerprint: ["edit_file", "write_file", "run_shell"].includes(j.tool) ? `fp${fp}` : undefined });
        if (r && !fired) fired = { at: n, ...r };
      }
      rows.push({ issue: issue.slice(-3), model: res.model, variant, solved: !!res.resolved && !(res.fetched || []).length, calls: n, fired, lastEdit });
    }
  }
}
for (const r of rows.sort((a, b) => `${a.model}${a.issue}${a.variant}`.localeCompare(`${b.model}${b.issue}${b.variant}`))) {
  console.log(`${String(r.model).slice(0, 7)} ${r.issue} ${r.variant.padEnd(18)} ${r.solved ? "SOLVED" : "no    "} ${String(r.calls).padStart(4)} calls  ` +
    (r.fired ? `rule ${r.fired.rule} at call ${r.fired.at}${r.solved && r.fired.at < r.lastEdit ? `   <-- WOULD HAVE CUT A SOLVE (last edit at ${r.lastEdit})` : r.solved ? " (after the last edit: harmless)" : ""}` : "no rule fires"));
}
const cut = rows.filter((r) => r.solved && r.fired && r.fired.at < r.lastEdit);
console.log(`\n${rows.filter((r) => r.solved).length} solved tries; the rules would have cut ${cut.length} of them. ` +
  `${rows.filter((r) => !r.solved && r.fired).length} of ${rows.filter((r) => !r.solved).length} failed tries would have been stopped early.`);
