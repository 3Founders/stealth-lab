// The cheap reviewer (lib/review.mjs) over saved answers: stdin JSONL {key, prompt, patch} -> stdout JSONL {key, verdict,
// why, cost_usd}. Run by acceptance_eval.py, a few at a time; the keys come in its environment, never on a command line.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const npm = process.env.STEALTHLAB_NPM_DIR || path.join(here, "..", "..", "packaging", "npm");
const { reviewPatch } = await import(pathToFileURL(path.join(npm, "lib", "review.mjs")).href);
const items = fs.readFileSync(0, "utf8").split(/\r?\n/).filter(Boolean).map((l) => JSON.parse(l));
const width = Number(process.env.REVIEW_WIDTH || 3);
let next = 0;
async function worker() {
  while (next < items.length) {
    const it = items[next++];
    const r = await reviewPatch({ prompt: it.prompt, diff: it.patch, env: process.env, model: process.env.REVIEW_MODEL || null });
    process.stdout.write(JSON.stringify({ key: it.key, verdict: r?.verdict ?? null, why: r?.why ?? null, cost_usd: r?.cost_usd ?? null }) + "\n");
  }
}
await Promise.all(Array.from({ length: width }, worker));
