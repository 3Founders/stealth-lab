// The agent half of the survey, done by a cheap model: the scanner writes facts it can read off manifests and CI, and a
// worklist (.kell/survey/worklist.md) of what only reading the code tells -- build, layout, conventions: which module
// does what. With only the hooks installed nobody worked that list, so claims.md stayed at the scanner's ~20 facts and
// never said where things live (Modal, jsonargparse: 19 facts, none about the code's layout). This reads each WORK
// line's files and asks one cheap model (gpt-oss-120b by default) for facts citing the line that shows them; the
// survey's own validator then keeps only facts whose anchors really are at the cited line (no fact is trusted because a
// model wrote it). Detached, after the first survey (lib/auto_survey.mjs); STEALTHLAB_SURVEY_FILL=off.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { readExecConfig } from "../exec/store.mjs";
import { profileKey } from "../executors/profile.mjs";
import { costOf, recordSpend } from "../spend.mjs";
import { TOPICS, parseClaim, renderClaim, scopeFor } from "./claims.mjs";

export const fillEnabled = (env = process.env) => (env.STEALTHLAB_SURVEY_FILL || "on").toLowerCase() !== "off";
const READ_MAX = 24000;        // characters of files per WORK line
const MAX_WORK = 3;

export const FILL_SYSTEM = [
  "You write facts about a code repository for other models that will work in it. Reply with only a JSON array of",
  '{"topic": string, "statement": string, "source": "path:line"} objects, at most 12. topic: one of the topics asked for,',
  "or \"layout\" for where things live (which module or directory does what) -- give layout facts for the main modules.",
  "statement: one plain sentence; put the exact identifier, path or command it rests on in backticks. source: the file",
  "and line number (from the numbered text shown) where that backticked text appears. Only facts the shown text proves;",
  "never guess; skip a topic the text does not show.",
].join(" ");

// worklist.md -> [{unit, page, missing: [topics], read: [{path, head}]}]
export function parseWorklist(text) {
  const out = [];
  for (const l of String(text || "").split(/\r?\n/)) {
    if (!l.startsWith("WORK|")) continue;
    const f = l.split("|");
    const kv = Object.fromEntries(f.slice(2).map((x) => [x.slice(0, x.indexOf("=")), x.slice(x.indexOf("=") + 1)]));
    out.push({ unit: f[1], page: kv.page || "claims.md", missing: String(kv.missing || "").split(",").filter(Boolean),
               read: String(kv.read || "").split(",").filter(Boolean).map((r) => ({ path: r.replace(/\(head\)$/, ""), head: /\(head\)$/.test(r) })) });
  }
  return out;
}

function numbered(root, rel, head) {
  try {
    const lines = fs.readFileSync(path.join(root, rel), "utf8").split(/\r?\n/).slice(0, head ? 80 : 400);
    return `--- ${rel} ---\n${lines.map((l, i) => `${i + 1}\t${l}`).join("\n")}`;
  } catch { return ""; }
}

// the source files of a unit, for layout facts: their paths (the validator accepts an existing path as an anchor)
function sourceList(root, unit) {
  const r = spawnSync("git", ["-C", root, "ls-files", "--", unit === "." ? "." : unit], { encoding: "utf8", windowsHide: true, timeout: 15000 });
  return String(r.stdout || "").split(/\r?\n/).filter((f) => /\.(py|m?[jt]sx?|go|rs|java|rb|kt|cs|php)$/.test(f) && !/(^|\/)(tests?|vendor|node_modules)\//.test(f)).slice(0, 80);
}

// an outline of the main source files: their top-level classes and functions with line numbers (what a layout fact cites)
export function outline(root, files, { maxFiles = 20, perFile = 8, max = 9000 } = {}) {
  const sized = files.map((f) => { try { return [f, fs.statSync(path.join(root, f)).size]; } catch { return [f, 0]; } })
    .sort((a, b) => b[1] - a[1]).slice(0, maxFiles).map(([f]) => f).sort();
  const parts = [];
  let used = 0;
  for (const f of sized) {
    let lines;
    try { lines = fs.readFileSync(path.join(root, f), "utf8").split(/\r?\n/); } catch { continue; }
    const defs = [];
    for (let i = 0; i < lines.length && defs.length < perFile; i++) {
      const m = /^(?:export\s+)?(?:async\s+)?(?:def|class|function|func|fn|pub\s+fn|type|interface)\s+([A-Za-z_]\w*)/.exec(lines[i]);
      if (m) defs.push(`  ${i + 1}: ${lines[i].trim().slice(0, 90)}`);
    }
    const doc = lines.slice(0, 6).find((l) => /^\s*("""|'''|\/\/|#|\/\*)/.test(l) && l.trim().length > 6);
    const text = `${f}${doc ? `  (${doc.trim().slice(0, 100)})` : ""}\n${defs.join("\n")}`;
    if (used + text.length > max) break;
    parts.push(text);
    used += text.length;
  }
  return parts.join("\n");
}

// the model's reply -> CLAIM lines (new ids after the page's highest; topics checked; the validator does the rest)
export function claimsFromReply(text, { pageText, unit, topics }) {
  const s = String(text || "");
  const a = s.indexOf("["), b = s.lastIndexOf("]");
  if (a < 0 || b <= a) return [];
  let arr;
  try { arr = JSON.parse(s.slice(a, b + 1)); } catch { return []; }
  const ids = String(pageText || "").split(/\r?\n/).map(parseClaim).filter(Boolean).map((c) => c.id);
  const prefix = (ids[0] || "R-001").replace(/\d+$/, "");
  let n = Math.max(0, ...ids.map((id) => Number((/(\d+)$/.exec(id) || [])[1] || 0)));
  const allowed = new Set([...topics, "layout"].filter((t) => TOPICS.includes(t)));
  const out = [];
  for (const f of (Array.isArray(arr) ? arr : []).slice(0, 12)) {
    if (!f || !allowed.has(f.topic) || typeof f.statement !== "string" || !/^[^:\s]+:\d+$/.test(String(f.source || ""))) continue;
    n++;
    out.push(renderClaim({ id: `${prefix}${String(n).padStart(3, "0")}`, status: "current", topic: f.topic, scope: scopeFor(unit),
                           statement: f.statement.slice(0, 300), kv: [["source", String(f.source)], ["version", "1"]] }));
  }
  return out;
}

// -> {asked, added} (facts appended; the caller validates the page right after)
export async function fillWorklist({ root, env = process.env, fetchImpl = globalThis.fetch, log = () => {} }) {
  const res = { asked: 0, added: 0 };
  if (!fillEnabled(env)) return res;
  const sdir = path.join(root, ".kell");
  let work;
  try { work = parseWorklist(fs.readFileSync(path.join(sdir, "survey", "worklist.md"), "utf8")).slice(0, MAX_WORK); } catch { return res; }
  const name = String(env.STEALTHLAB_SURVEY_FILL_MODEL || "gpt-oss-120b").trim();
  const profile = readExecConfig(env).profiles?.[name];
  if (!profile?.openai_base_url || !work.length) return res;
  for (const w of work) {
    let shown = "";
    for (const r of w.read) { const t = numbered(root, r.path, r.head); if (shown.length + t.length < READ_MAX) shown += `${t}\n\n`; }
    const files = sourceList(root, w.unit);
    const user = `Topics to cover: ${[...w.missing, "layout"].join(", ")}.\n\nThe source files of this ${w.unit === "." ? "repository" : `unit (${w.unit})`}:\n` +
      `${files.join("\n")}\n\nOutline of the main source files (top-level classes and functions, with their line numbers; ` +
      `cite these lines for layout facts, e.g. which module handles what):\n${outline(root, files)}\n\nFiles (numbered lines):\n${shown}`;
    const messages = profile.no_system_role ? [{ role: "user", content: `${FILL_SYSTEM}\n\n${user}` }]
      : [{ role: "system", content: FILL_SYSTEM }, { role: "user", content: user }];
    try {
      res.asked++;
      const r = await fetchImpl(`${profile.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
        method: "POST", redirect: "error", signal: AbortSignal.timeout(90000),
        headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, env)}` },
        body: JSON.stringify({ model: profile.model_id, messages, max_tokens: 4000, temperature: 0, ...(profile.request_extras || {}) }),
      });
      if (!r.ok) { log(`survey fill: HTTP ${r.status}`); continue; }
      const body = await r.json();
      recordSpend(env, { what: "survey-fill", model: profile.name, cost_usd: costOf(profile, body?.usage) });
      const pagePath = path.join(sdir, w.page);
      const pageText = fs.existsSync(pagePath) ? fs.readFileSync(pagePath, "utf8") : "";
      const lines = claimsFromReply(body?.choices?.[0]?.message?.content, { pageText, unit: w.unit, topics: w.missing });
      if (lines.length) {
        fs.writeFileSync(pagePath, pageText + (pageText.endsWith("\n") || !pageText ? "" : "\n") + lines.join("\n") + "\n");
        res.added += lines.length;
      }
    } catch (err) {
      log(`survey fill: ${err.message}`);
    }
  }
  return res;
}
