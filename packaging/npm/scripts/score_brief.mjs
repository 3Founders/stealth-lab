#!/usr/bin/env node
// Scores the task brief's kind (lib/brief.mjs) on test/fixtures/brief_prompts.json with the REAL model. The brief no
// longer gates dispatch (the server's judge does, POST /triage); this measures the brief model alone. A hand-run probe: it
// makes one live call per prompt (STEALTHLAB_BRIEF_MODEL, default gpt-oss-120b on the user's own key), so it
// is not part of `npm test`.
//
//   node scripts/score_brief.mjs            # prints the table and the three numbers
//
// The bar (handoff 6.2 A): kind right on >= 90% of the prompts, and NO question or chat prompt is called code (the
// brief alone). Exit code 1 when the bar is missed.
import fs from "node:fs";
import { makeBrief } from "../lib/brief.mjs";

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
  if (kind === "code" && p.kind !== "code") wronglyDispatched.push(p.prompt);
  if (kind !== "code" && p.kind === "code") missedCode.push(p.prompt);
  console.log(`${kind === p.kind ? "ok  " : "MISS"} want ${p.kind.padEnd(8)} got ${String(kind).padEnd(10)} ${p.prompt.slice(0, 70)}`);
}
const pct = Math.round((100 * kindRight) / prompts.length);
console.log(`\nkind right: ${kindRight}/${prompts.length} (${pct}%)   no brief (not dispatched): ${noBrief}`);
console.log(`question or chat called code: ${wronglyDispatched.length}${wronglyDispatched.length ? "\n  " + wronglyDispatched.join("\n  ") : ""}`);
console.log(`code tasks called something else: ${missedCode.length}${missedCode.length ? "\n  " + missedCode.join("\n  ") : ""}`);
process.exit(pct >= 90 && wronglyDispatched.length === 0 ? 0 : 1);
