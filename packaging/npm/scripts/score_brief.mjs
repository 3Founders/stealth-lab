#!/usr/bin/env node
// Scores the task brief (lib/brief.mjs) on test/fixtures/brief_prompts.json with the REAL model. A hand-run probe: it
// makes one live call per prompt on the user's own provider key (exec.json profile for STEALTHLAB_BRIEF_MODEL, default
// gpt-oss-120b), so it is not part of `npm test`.
//
//   node scripts/score_brief.mjs            # prints the table and the three numbers
//
// The bar (handoff 6.2 A): kind right on >= 90% of the prompts, and NO question or chat prompt ends up dispatched (the
// gate, with the guard, as dispatch applies it). Exit code 1 when the bar is missed.
import fs from "node:fs";
import { briefGate, guardKind, makeBrief } from "../lib/brief.mjs";

const prompts = JSON.parse(fs.readFileSync(new URL("../test/fixtures/brief_prompts.json", import.meta.url), "utf8")).prompts;
let kindRight = 0;
let noBrief = 0;
const wronglyDispatched = [];
const missedCode = [];
for (const p of prompts) {
  const brief = await makeBrief({ prompt: p.prompt, log: (m) => process.stderr.write(`  ${m}\n`) });
  const kind = guardKind(p.prompt) || brief?.kind || "(no brief)";
  if (!brief) noBrief++;
  if (kind === p.kind) kindRight++;
  const gate = briefGate(brief, p.prompt);
  if (gate.ok && p.kind !== "code") wronglyDispatched.push(p.prompt);
  if (!gate.ok && p.kind === "code" && p.clarity === "clear" && p.size !== "large") missedCode.push(`${p.prompt}  (${gate.reason})`);
  console.log(`${kind === p.kind ? "ok  " : "MISS"} want ${p.kind.padEnd(8)} got ${String(kind).padEnd(10)} ${gate.ok ? "dispatch" : "no      "} ${p.prompt.slice(0, 70)}`);
}
const pct = Math.round((100 * kindRight) / prompts.length);
console.log(`\nkind right: ${kindRight}/${prompts.length} (${pct}%)   no brief (fell back): ${noBrief}`);
console.log(`question or chat dispatched: ${wronglyDispatched.length}${wronglyDispatched.length ? "\n  " + wronglyDispatched.join("\n  ") : ""}`);
console.log(`clear code tasks the gate would skip: ${missedCode.length}${missedCode.length ? "\n  " + missedCode.join("\n  ") : ""}`);
process.exit(pct >= 90 && wronglyDispatched.length === 0 ? 0 : 1);
