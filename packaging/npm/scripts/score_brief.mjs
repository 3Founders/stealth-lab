#!/usr/bin/env node
// Scores the task brief (lib/brief.mjs) on test/fixtures/brief_prompts.json with the REAL model. A hand-run probe: it
// makes one live call per prompt (STEALTHLAB_BRIEF_MODEL, default claude-sonnet-5-5 under the user's own login), so it
// is not part of `npm test`.
//
//   node scripts/score_brief.mjs            # prints the table and the three numbers
//
// The bar (handoff 6.2 A): kind right on >= 90% of the prompts, and NO question or chat prompt ends up dispatched (the
// gate as dispatch applies it: the model's judgment alone). Exit code 1 when the bar is missed.
import fs from "node:fs";
import { briefGate, makeBrief } from "../lib/brief.mjs";

const prompts = JSON.parse(fs.readFileSync(new URL("../test/fixtures/brief_prompts.json", import.meta.url), "utf8")).prompts;
let kindRight = 0;
let noBrief = 0;
const wronglyDispatched = [];
const missedCode = [];
for (const p of prompts) {
  const brief = await makeBrief({ prompt: p.prompt, log: (m) => process.stderr.write(`  ${m}\n`) });
  const kind = brief?.kind || "(no brief)";
  if (!brief) noBrief++;
  if (kind === p.kind) kindRight++;
  const gate = briefGate(brief);
  if (gate.ok && p.kind !== "code") wronglyDispatched.push(p.prompt);
  if (!gate.ok && p.kind === "code" && p.clarity === "clear") missedCode.push(`${p.prompt}  (${gate.reason})`);
  console.log(`${kind === p.kind ? "ok  " : "MISS"} want ${p.kind.padEnd(8)} got ${String(kind).padEnd(10)} ${gate.ok ? "dispatch" : "no      "} ${p.prompt.slice(0, 70)}`);
}
const pct = Math.round((100 * kindRight) / prompts.length);
console.log(`\nkind right: ${kindRight}/${prompts.length} (${pct}%)   no brief (not dispatched): ${noBrief}`);
console.log(`question or chat dispatched: ${wronglyDispatched.length}${wronglyDispatched.length ? "\n  " + wronglyDispatched.join("\n  ") : ""}`);
console.log(`clear code tasks the gate would skip: ${missedCode.length}${missedCode.length ? "\n  " + missedCode.join("\n  ") : ""}`);
process.exit(pct >= 90 && wronglyDispatched.length === 0 ? 0 : 1);
