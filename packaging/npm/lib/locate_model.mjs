// The second half of locating, after the search (lib/locate.mjs): one cheap model call narrows it, the way hierarchical
// localisation (Agentless) does -- the issue, the repository's source files, and the outline (top-level classes and
// functions, with lines) of the search's candidates; the model picks the files and functions the fix is in. Every pick is
// checked: a file must exist and be source code, a function must be defined in that file; anything else is dropped.
//
// Why: on 47 real issues from 47 repositories the search alone put a file of the real fix in its top three for 62%, and
// found nothing for 17% -- issues written in plain words ("double pipe is not concatenation in SQL Server") give it no
// names to search for, and the right file is often a sibling of the one the words match (pipeline survey, 2026-10-16).
// STEALTHLAB_LOCATE_MODEL names the model (an exec.json profile; default gpt-oss-120b); "off" keeps the search alone.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { readExecConfig } from "./exec/store.mjs";
import { profileKey } from "./executors/profile.mjs";
import { costOf, recordSpend } from "./spend.mjs";
import { enclosingBlock, isSource } from "./locate.mjs";

export const NARROW_SYSTEM = [
  "You find where in a code repository a reported problem must be fixed. You get the report, the repository's source",
  "files, and the outline of some candidate files (their top-level classes and functions with line numbers). Reply with",
  'only a JSON object: {"files": [up to 4 paths, most likely first], "functions": [{"file": path, "name": name} up to 6],',
  '"why": one sentence}. Use only paths from the list and names from the outlines or that the report names. Pick the',
  "code that implements the behaviour the report is about -- not tests, docs, examples or build scripts.",
].join(" ");

const read = (f) => { try { return fs.readFileSync(f, "utf8").split(/\r?\n/); } catch { return null; } };

export function outlineOf(root, file, max = 40) {
  const lines = read(path.join(root, file)) || [];
  const out = [];
  for (let i = 0; i < lines.length && out.length < max; i++) {
    const m = /^(\s{0,4})(?:export\s+)?(?:async\s+)?(?:def|class|function|func|fn|pub\s+fn|interface|type)\s+([A-Za-z_]\w*)/.exec(lines[i]);
    if (m) out.push(`  ${i + 1}: ${lines[i].trim().slice(0, 100)}`);
  }
  return `${file}\n${out.join("\n")}`;
}

function sourceFiles(root) {
  const r = spawnSync("git", ["-C", root, "ls-files"], { encoding: "utf8", windowsHide: true, timeout: 15000, maxBuffer: 32 * 1024 * 1024 });
  return String(r.stdout || "").split(/\r?\n/).filter((f) => f && isSource(f));
}

// the model's reply -> {files, functions} checked against the repository
export function checkPicks(text, root, known) {
  const s = String(text || "");
  const a = s.indexOf("{"), b = s.lastIndexOf("}");
  if (a < 0 || b <= a) return null;
  let o;
  try { o = JSON.parse(s.slice(a, b + 1)); } catch { return null; }
  const files = (Array.isArray(o.files) ? o.files : []).map(String).filter((f) => known.has(f)).slice(0, 4);
  const functions = [];
  for (const fn of Array.isArray(o.functions) ? o.functions : []) {
    const file = String(fn?.file || ""), name = String(fn?.name || "").replace(/[^\w]/g, "");
    if (!known.has(file) || !name) continue;
    const lines = read(path.join(root, file)) || [];
    const at = lines.findIndex((l) => new RegExp(`^\\s*(?:export\\s+)?(?:async\\s+)?(?:def|class|function|func|fn)\\s+${name}\\b`).test(l));
    if (at >= 0) functions.push({ file, name, line: at + 1 });
  }
  return { files, functions: functions.slice(0, 6), why: String(o.why || "").slice(0, 200) };
}

// -> a location in lib/locate.mjs's shape, the model's picks first, the search's after them; the search's own result when
//    the model is off, unknown, slow or unusable
export async function narrowLocation({ prompt, root, loc, env = process.env, fetchImpl = globalThis.fetch, log = () => {}, timeoutMs = 45000 }) {
  const name = String(env.STEALTHLAB_LOCATE_MODEL || "gpt-oss-120b").trim();
  if (!root || name.toLowerCase() === "off") return loc;
  try {
    const profile = readExecConfig(env).profiles?.[name];
    if (!profile?.openai_base_url) return loc;
    const all = sourceFiles(root);
    if (!all.length) return loc;
    const known = new Set(all);
    // the file list: whole when it fits, else the candidates' folders first and the rest by depth
    const cand = (loc?.files || []).slice(0, 6);
    let list = all;
    if (all.join("\n").length > 24000) {
      const dirs = new Set(cand.map((f) => path.posix.dirname(f)));
      list = [...all.filter((f) => dirs.has(path.posix.dirname(f))), ...all.filter((f) => !dirs.has(path.posix.dirname(f)))
        .sort((x, y) => x.split("/").length - y.split("/").length)];
      const out = [];
      let used = 0;
      for (const f of list) { if (used + f.length + 1 > 24000) break; out.push(f); used += f.length + 1; }
      list = out.sort();
    }
    const outlines = cand.map((f) => outlineOf(root, f)).join("\n\n");
    const user = `The report:\n${String(prompt || "").slice(0, 5000)}\n\nSource files:\n${list.join("\n")}\n\n` +
      (outlines ? `Outlines of the files a text search matched:\n${outlines}` : "A text search matched no file.");
    const messages = profile.no_system_role ? [{ role: "user", content: `${NARROW_SYSTEM}\n\n${user}` }]
      : [{ role: "system", content: NARROW_SYSTEM }, { role: "user", content: user }];
    const res = await fetchImpl(`${profile.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(timeoutMs),
      headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, env)}` },
      body: JSON.stringify({ model: profile.model_id, messages, max_tokens: 3000, temperature: 0, ...(profile.request_extras || {}) }),
    });
    if (!res.ok) { log(`locate: narrowing HTTP ${res.status}`); return loc; }
    const body = await res.json();
    recordSpend(env, { what: "locate", model: profile.name, cost_usd: costOf(profile, body?.usage) });
    const picks = checkPicks(body?.choices?.[0]?.message?.content, root, known);
    if (!picks || (!picks.files.length && !picks.functions.length)) { log("locate: narrowing gave nothing usable"); return loc; }
    // files: the model's, then its functions' files, then the search's; spots: its functions, then the search's spots
    const files = [...new Set([...picks.files, ...picks.functions.map((f) => f.file), ...(loc?.files || [])])].slice(0, 3);
    const spots = [];
    for (const fn of picks.functions) {
      const lines = read(path.join(root, fn.file)) || [];
      const b = enclosingBlock(lines, fn.line);
      if (!spots.some((s) => s.file === fn.file && !(b.end < s.start || b.start > s.end))) spots.push({ file: fn.file, start: b.start, end: Math.min(b.end, b.start + 200), symbol: b.symbol || fn.name });
    }
    for (const s of loc?.spots || []) if (spots.length < 4 && files.includes(s.file) && !spots.some((x) => x.file === s.file && !(s.end < x.start || s.start > x.end))) spots.push(s);
    for (const f of files) if (!spots.some((s) => s.file === f) && spots.length < 4) {
      const n = (read(path.join(root, f)) || []).length;
      spots.push({ file: f, start: 1, end: Math.min(n, 80), symbol: null });
    }
    log(`locate: narrowed to ${files.join(", ")}${picks.functions.length ? ` (${picks.functions.map((f) => f.name).join(", ")})` : ""}`);
    return { ...loc, files, spots: spots.slice(0, 4), narrowed: picks };
  } catch (err) {
    log(`locate: narrowing ${err.name === "TimeoutError" ? "timed out" : err.message}`);
    return loc;
  }
}
