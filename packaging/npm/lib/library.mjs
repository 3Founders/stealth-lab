// .stealth/library.md, routing.md, index/library.idx, index/terms.idx and SUMMARY.md: this repository's own
// solved problems and model routes, kept on this machine (docs/plan_2026-10_priors_library_survey.md §3).
//
// Why local: the evidence (plan §0) is that a repo's OWN past fixes with their real diffs are what helps
// ("right cause" 27 -> 42 of 96), and they are code -- they stay here. The server only ever sees what
// requestPayload() sends with one find_ways call: library.idx rows (ids, titles, status), routing lines
// (model names and attempt counts) and a hashed repo identity. Never a diff, a step or a check.
//
// The grammar is the same as backend/app/stealth/library.py, which documents it; the shared fixtures in
// test/fixtures/library hold both implementations to the same canonical text, idx and parse.
//
// Layout (book analogy, plan §3.1):
//   SUMMARY.md            back cover: read whole (<= 3 KB), regenerated when a source changes
//   library.md            chapter: problems solved here (committed; .gitattributes merge=union)
//   library/solutions/    appendix: <id>.diff, the code that solved each one
//   index/library.idx     contents: id|status|outcome|unit|g|verified_at|start|end|block_sha|title
//   index/terms.idx       back-of-book index: term|ids (paths, file names, symbols, title words, tags)
//   routing.md            routes from find_ways' model_plan + this machine's OBS counts
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";

export const SEP = "|";
export const LIBRARY_MAX_BYTES = 65536;
export const LIBRARY_ROWS_MAX_BYTES = 65536;
export const ROUTE_OBS_MAX_BYTES = 16384;
export const SUMMARY_MAX_BYTES = 3072;
export const TERMS_MAX_BYTES = 65536;

const ENTRY_ID = /^L-[0-9a-f]{4,16}$/;
const ROUTE_ID = /^R-[0-9a-f]{4,16}$/;
const PROC_ID = /^(L-[0-9a-f]{4,16})\.p([0-9]+)$/;
const STEP_ID = /^(L-[0-9a-f]{4,16})\.p([0-9]+):([0-9]+)$/;
const COUNT = /^[0-9]+$/;
const OUTCOMES = ["pass", "historical", "fail"];
const STATUSES = ["current", "stale"];
const RAW_KEYS = new Set(["touches", "tags", "ladder"]);

export const LIBRARY_HEADER =
  "# library.md -- problems solved in THIS repository, with the diffs that solved them.\n" +
  "# Committed (team knowledge). One entry per id; lines may be in any order (merge=union safe).\n" +
  "# GOAL|<L-id>|<title>|unit=<path or .>|g=<global goal id or ->|outcome=<pass|historical|fail>|" +
  "status=<current|stale>|verified_at=<date>|route=<R-id or ->|tags=<csv or ->\n" +
  "# PROC|<L-id>.p<n>|<name>|p=<global procedure id or ->|solution=<solutions/<L-id>.diff or ->|" +
  "touches=<path#sha=<sha>,...>\n" +
  "# STEP|<L-id>.p<n>:<k>|<action|instruction|subgoal>|<do>|check=<command or ->\n";
export const ROUTING_HEADER =
  "# routing.md -- GENERATED. ROUTE lines come from find_ways' model_plan (replaced on each plan);\n" +
  "# OBS lines are this machine's own attempt counts per route (sent back as find_ways' route_obs).\n" +
  "# ROUTE|<R-id>|goal=<L-id or ->|g=<global goal id>|fit=<fit id or ->|basis=<prior|posterior|->|" +
  "as_of=<date>|step=<*|order>|ladder=<model>::<scaffold>:p=<mean>[<q05>,<q95>]:$<cost> > ...|" +
  "whole=p=<mean>[<q05>,<q95>]:$<cost>\n" +
  "# OBS|<R-id>|<model>|<scaffold>|n=<attempts>|ok=<accepted>|last=<date>\n";
export const IDX_HEADER =
  "# library.idx -- GENERATED from library.md (rebuilt whenever source_sha differs). Do not hand-edit.\n" +
  "# id|status|outcome|unit|g|verified_at|start|end|block_sha|title\n";

// ------------------------------------------------------------------ escaping (== library.py esc/unesc)

export function esc(value) {
  let s = value === null || value === undefined ? "" : String(value);
  s = s.replace(/%(?=[0-9A-Fa-f]{2})/g, "%25");
  return s.replace(/\|/g, "%7C").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n/g, "%0A");
}

export function unesc(value) {
  return String(value).replace(/%([0-9A-Fa-f]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16)));
}

const escItem = (v) => esc(v).replace(/,/g, "%2C").replace(/#/g, "%23");
const kv = (key, value) => `${key}=${esc(value === null || value === undefined || value === "" ? "-" : value)}`;
const dash = (v) => (v === null || v === undefined || v === "" || v === "-" ? null : v);
const pyStrip = (s) => String(s).trim();

function splitKv(fields) {
  const out = {};
  for (const f of fields) {
    const i = f.indexOf("=");
    if (i <= 0) continue;
    const k = f.slice(0, i);
    if (k in out) continue;
    const v = f.slice(i + 1);
    out[k] = RAW_KEYS.has(k) ? v : unesc(v);
  }
  return out;
}

export function shortHash(text) {
  return crypto.createHash("sha1").update(text, "utf8").digest("hex").slice(0, 12);
}

const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

// ------------------------------------------------------------------ library.md render / parse

export function renderGoalLine(e) {
  return [
    "GOAL", e.id, esc(pyStrip(e.title)), kv("unit", e.unit || "."), kv("g", e.g), kv("outcome", e.outcome || "pass"),
    kv("status", e.status || "current"), kv("verified_at", e.verified_at), kv("route", e.route),
    "tags=" + ((e.tags || []).length ? e.tags.map(escItem).join(",") : "-"),
  ].join(SEP);
}

export function renderProcLine(entryId, p) {
  const touches = (p.touches || []).map((t) => `${escItem(t.path)}#sha=${t.sha}`).join(",") || "-";
  return ["PROC", `${entryId}.p${p.index}`, esc(pyStrip(p.name)), kv("p", p.p), kv("solution", p.solution),
    "touches=" + touches].join(SEP);
}

export function renderStepLine(entryId, procIndex, s) {
  return ["STEP", `${entryId}.p${procIndex}:${s.order}`, esc(s.kind), esc(pyStrip(s.do)), kv("check", s.check)].join(SEP);
}

export function renderBlock(e) {
  const lines = [renderGoalLine(e)];
  for (const p of [...(e.procs || [])].sort((a, b) => a.index - b.index)) {
    lines.push(renderProcLine(e.id, p));
    for (const s of [...(p.steps || [])].sort((a, b) => a.order - b.order)) lines.push(renderStepLine(e.id, p.index, s));
  }
  return lines;
}

export function renderLibrary(entries) {
  const list = Array.isArray(entries) ? entries : entries.entries;
  const blocks = [...list].sort((a, b) => cmp(a.id, b.id)).map((e) => renderBlock(e).join("\n"));
  return LIBRARY_HEADER + (blocks.length ? "\n" + blocks.join("\n\n") + "\n" : "");
}

function parseTouches(raw) {
  if (raw === undefined || raw === "" || raw === "-") return [];
  const out = [];
  for (const item of raw.split(",")) {
    const i = item.lastIndexOf("#sha=");
    if (i < 0) continue;
    const p = item.slice(0, i);
    const sha = item.slice(i + 5).trim();
    if (p && sha) out.push({ path: unesc(p), sha });
  }
  return out;
}

const parseTags = (raw) => (raw === undefined || raw === "" || raw === "-" ? [] : raw.split(",").filter(Boolean).map(unesc));

// Same rules as library.py parse_library: grouped by id, exact duplicates collapse, conflicts resolved the same
// way by every reader (GOAL: later verified_at, then the larger line; PROC/STEP: the larger line), and every
// skipped line or conflict reported in `problems`, in the same order and words.
export function parseLibrary(text) {
  const problems = [];
  const goals = new Map();
  const procs = new Map();
  const steps = new Map();
  const keep = (store, key, label, line, fields, rank) => {
    const old = store.get(key);
    if (!old || old.line === line) {
      store.set(key, { line, fields });
      return;
    }
    problems.push(`conflict: two versions of ${label}; kept one`);
    const a = rank(fields, line);
    const b = rank(old.fields, old.line);
    if (a > b) store.set(key, { line, fields });
  };
  const goalRank = (f, line) => [splitKv(f.slice(3)).verified_at || "", line];
  const rankGt = (r) => r;
  const lines = String(text).split("\n");
  for (let n = 1; n <= lines.length; n++) {
    const line = lines[n - 1].replace(/\r+$/, "");
    if (!line.trim() || line.trimStart().startsWith("#")) continue;
    const f = line.split(SEP);
    let m;
    if (f[0] === "GOAL" && f.length >= 3 && ENTRY_ID.test(f[1])) {
      keep(goals, f[1], f[1], line, f, (ff, l) => { const [d, ln] = goalRank(ff, l); return `${d}\u0000${ln}`; });
    } else if (f[0] === "PROC" && f.length >= 3 && (m = PROC_ID.exec(f[1]))) {
      keep(procs, `${m[1]} ${m[2]}`, m[1], line, f, (ff, l) => l);
    } else if (f[0] === "STEP" && f.length >= 4 && (m = STEP_ID.exec(f[1]))) {
      keep(steps, `${m[1]} ${m[2]} ${m[3]}`, m[1], line, f, (ff, l) => l);
    } else {
      problems.push(`line ${n}: not a library line, skipped`);
    }
  }
  void rankGt;
  const entries = new Map();
  for (const [gid, { fields: f }] of goals) {
    const k = splitKv(f.slice(3));
    entries.set(gid, {
      id: gid, title: unesc(f[2]), unit: dash(k.unit) || ".", g: dash(k.g),
      outcome: OUTCOMES.includes(k.outcome) ? k.outcome : "pass",
      status: STATUSES.includes(k.status) ? k.status : "current",
      verified_at: dash(k.verified_at), route: dash(k.route), tags: parseTags(k.tags), procs: [],
    });
  }
  const keyed = (map, parse) => [...map.entries()].map(([key, v]) => ({ k: parse(key), v }));
  const procList = keyed(procs, (key) => { const [g, i] = key.split(" "); return [g, Number(i)]; })
    .sort((a, b) => cmp(a.k[0], b.k[0]) || a.k[1] - b.k[1]);
  const procObjs = new Map();
  for (const { k: [gid, idx], v: { fields: f } } of procList) {
    if (!entries.has(gid)) {
      problems.push(`orphan: ${gid}.p${idx} has no GOAL line; skipped`);
      continue;
    }
    const k = splitKv(f.slice(3));
    const p = { index: idx, name: unesc(f[2]), p: dash(k.p), solution: dash(k.solution), touches: parseTouches(k.touches), steps: [] };
    entries.get(gid).procs.push(p);
    procObjs.set(`${gid} ${idx}`, p);
  }
  const stepList = keyed(steps, (key) => { const [g, p, o] = key.split(" "); return [g, Number(p), Number(o)]; })
    .sort((a, b) => cmp(a.k[0], b.k[0]) || a.k[1] - b.k[1] || a.k[2] - b.k[2]);
  for (const { k: [gid, pidx, order], v: { fields: f } } of stepList) {
    const p = procObjs.get(`${gid} ${pidx}`);
    if (!p) {
      problems.push(`orphan: ${gid}.p${pidx}:${order} has no PROC line; skipped`);
      continue;
    }
    const k = splitKv(f.slice(4));
    p.steps.push({ order, kind: unesc(f[2]), do: unesc(f[3]), check: dash(k.check) });
  }
  return { entries: [...entries.values()].sort((a, b) => cmp(a.id, b.id)), problems };
}

// ------------------------------------------------------------------ index/library.idx

export function libraryIdxRows(canonical) {
  const lib = parseLibrary(canonical);
  const lines = canonical.split("\n");
  const first = new Map();
  const last = new Map();
  lines.forEach((line, i) => {
    const head = line.split(SEP);
    if (head.length < 2 || !["GOAL", "PROC", "STEP"].includes(head[0])) return;
    const gid = head[1].split(".")[0];
    if (!first.has(gid)) first.set(gid, i + 1);
    last.set(gid, i + 1);
  });
  return lib.entries.map((e) => {
    const s = first.get(e.id);
    const t = last.get(e.id);
    return { id: e.id, status: e.status, outcome: e.outcome, unit: e.unit, g: e.g, verified_at: e.verified_at,
      start: s, end: t, block_sha: shortHash(lines.slice(s - 1, t).join("\n")), title: e.title };
  });
}

const renderIdxRow = (r) => [r.id, r.status, r.outcome, esc(r.unit), r.g || "-", r.verified_at || "-",
  String(r.start), String(r.end), r.block_sha, esc(r.title)].join(SEP);

export function renderLibraryIdx(canonical) {
  return IDX_HEADER + `# source_sha=${shortHash(canonical)}\n` + libraryIdxRows(canonical).map((r) => renderIdxRow(r) + "\n").join("");
}

export function parseLibraryRows(text) {
  const rows = [];
  const seen = new Set();
  for (const raw of String(text).split("\n")) {
    const line = raw.replace(/\r+$/, "");
    if (!line.trim() || line.trimStart().startsWith("#")) continue;
    const f = line.split(SEP);
    if (f.length < 10 || !ENTRY_ID.test(f[0]) || seen.has(f[0])) continue;
    seen.add(f[0]);
    rows.push({ id: f[0], status: STATUSES.includes(f[1]) ? f[1] : "current", outcome: OUTCOMES.includes(f[2]) ? f[2] : "pass",
      unit: unesc(f[3]) || ".", g: dash(f[4]), verified_at: dash(f[5]),
      start: COUNT.test(f[6]) && COUNT.test(f[7]) ? Number(f[6]) : 0, end: COUNT.test(f[6]) && COUNT.test(f[7]) ? Number(f[7]) : 0,
      block_sha: f[8], title: unesc(f.slice(9).join(SEP)) });
  }
  return rows;
}

// ------------------------------------------------------------------ routing.md

const NUMS = /^p=([-0-9.]+|-)\[([-0-9.]+|-),([-0-9.]+|-)\]:\$([-0-9.]+|-)$/;
const RUNG = /^(.+?):p=([-0-9.]+|-)\[([-0-9.]+|-),([-0-9.]+|-)\]:\$([-0-9.]+|-)$/;
const fnum = (v) => {
  if (v === "-") return null;
  if (!/^-?([0-9]+\.?[0-9]*|\.[0-9]+)$/.test(v)) return null;
  const x = Number(v);
  return Number.isFinite(x) ? x : null;
};

function parseRungs(raw) {
  if (raw === undefined || raw === "" || raw === "-") return [];
  const out = [];
  for (const part of raw.split(" > ")) {
    const p = part.trim();
    const m = RUNG.exec(p);
    const unit = m ? m[1] : p;
    const i = unit.indexOf("::");
    if (i <= 0 || i + 2 >= unit.length) continue;
    const model = unesc(unit.slice(0, i));
    const scaffold = unesc(unit.slice(i + 2));
    out.push(m ? { cost: fnum(m[5]), model, p_mean: fnum(m[2]), p_q05: fnum(m[3]), p_q95: fnum(m[4]), scaffold }
      : { cost: null, model, p_mean: null, p_q05: null, p_q95: null, scaffold });
  }
  return out;
}

// Same rules as library.py parse_routing: the last ROUTE line per (route, step) wins, in the position of the
// first; OBS lines for one (route, model, scaffold) are summed; malformed lines and counts are skipped.
export function parseRouting(text) {
  const routes = new Map();
  const obs = new Map();
  for (const raw of String(text).split("\n")) {
    const f = raw.replace(/\r+$/, "").split(SEP);
    if (f[0] === "ROUTE" && f.length >= 3 && ROUTE_ID.test(f[1])) {
      const k = splitKv(f.slice(2));
      const w = NUMS.exec(k.whole ?? "-");
      const r = { as_of: dash(k.as_of), basis: dash(k.basis), fit: dash(k.fit), g: dash(k.g), goal: dash(k.goal), id: f[1],
        ladder: parseRungs(k.ladder), step: k.step || "*",
        whole: w ? { cost: fnum(w[4]), model: "", p_mean: fnum(w[1]), p_q05: fnum(w[2]), p_q95: fnum(w[3]), scaffold: "" } : null };
      routes.set(`${r.id} ${r.step}`, r);
    } else if (f[0] === "OBS" && f.length >= 6 && ROUTE_ID.test(f[1])) {
      const k = splitKv(f.slice(4));
      if (!COUNT.test(k.n ?? "") || !COUNT.test(k.ok ?? "")) continue;
      const n = Number(k.n);
      const ok = Number(k.ok);
      if (ok > n) continue;
      const model = unesc(f[2]);
      const scaffold = unesc(f[3]);
      const key = `${f[1]}\u0000${model}\u0000${scaffold}`;
      const last = dash(k.last);
      const prev = obs.get(key);
      if (!prev) obs.set(key, { last, model, n, ok, route: f[1], scaffold });
      else {
        prev.n += n;
        prev.ok += ok;
        prev.last = [prev.last, last].filter(Boolean).sort().pop() ?? null;
      }
    }
  }
  return { routes: [...routes.values()], obs: [...obs.values()] };
}

export function renderObsLine(o) {
  return ["OBS", o.route, esc(o.model), esc(o.scaffold), `n=${o.n}`, `ok=${o.ok}`, kv("last", o.last)].join(SEP);
}

export function localObsForGoal(routes, obs, goalId) {
  const ids = new Set(routes.filter((r) => r.g === goalId).map((r) => r.id));
  const acc = new Map();
  for (const o of obs) {
    if (!ids.has(o.route)) continue;
    const unit = `${o.model}|${o.scaffold}`;
    const a = acc.get(unit) || [0, 0];
    a[0] += o.n;
    a[1] += o.ok;
    acc.set(unit, a);
  }
  return [...acc.entries()].sort((a, b) => cmp(a[0], b[0])).filter(([, [n]]) => n > 0).map(([unit, [n, ok]]) => ({ n, ok, unit }));
}

// ------------------------------------------------------------------ files

export function stealthDir(root) {
  return path.join(root || process.cwd(), ".stealth");
}

const P = (root, ...parts) => path.join(stealthDir(root), ...parts);
const read = (file) => { try { return fs.readFileSync(file, "utf8"); } catch { return null; } };
const today = (now = new Date()) => now.toISOString().slice(0, 10);

function writeIfChanged(file, text) {
  if (read(file) === text) return false;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, text);
  fs.renameSync(tmp, file);
  return true;
}

// .stealth/.gitattributes: library.md merges by union (both sides' lines kept; ids keep entries apart).
// .stealth/.gitignore: the generated files, rebuilt from library.md / the server. library.md, library/solutions
// and SUMMARY's sources stay committable -- whether .stealth/ is committed at all is the repo owner's choice.
const GITATTRIBUTES = "library.md merge=union\n";
const GITIGNORE_LINES = ["index/library.idx", "index/terms.idx", "SUMMARY.md", "routing.md"];

export function ensureGitFiles(root) {
  const attr = P(root, ".gitattributes");
  const cur = read(attr) || "";
  if (!cur.split(/\r?\n/).includes("library.md merge=union")) writeIfChanged(attr, cur + (cur && !cur.endsWith("\n") ? "\n" : "") + GITATTRIBUTES);
  const ign = P(root, ".gitignore");
  const lines = (read(ign) || "").split(/\r?\n/).filter(Boolean);
  const missing = GITIGNORE_LINES.filter((l) => !lines.includes(l));
  if (missing.length) writeIfChanged(ign, [...lines, ...missing].join("\n") + "\n");
}

export function loadLibrary(root) {
  return parseLibrary(read(P(root, "library.md")) || "");
}

function newId(prefix, taken) {
  for (;;) {
    const id = `${prefix}-${crypto.randomBytes(3).toString("hex")}`;
    if (!taken.has(id)) return id;
  }
}

// ---- terms.idx: the back-of-book index (grep -i '^term|' .stealth/index/terms.idx -> ids)

const STOP = new Set(("the a an and or of to in on for with by from is are be this that it as at fix add use when " +
  "into not no its can should make makes made").split(" "));
const SYMBOL = /\b(?:def|function|class|func|fn|interface|struct|enum|type|module|sub|method)\s+([A-Za-z_][A-Za-z0-9_]*)/g;

export function termsFor(entry, diffText = "") {
  const terms = new Set();
  for (const w of String(entry.title).toLowerCase().match(/[a-z0-9_]+/g) || []) if (w.length > 2 && !STOP.has(w)) terms.add(w);
  for (const t of entry.tags || []) terms.add(String(t).toLowerCase());
  if (entry.unit && entry.unit !== ".") terms.add(entry.unit.toLowerCase());
  for (const p of entry.procs || []) {
    for (const t of p.touches || []) {
      terms.add(t.path.toLowerCase());
      terms.add(path.posix.basename(t.path).toLowerCase());
    }
  }
  for (const line of String(diffText).split("\n")) {
    if (!/^(@@|[+-])/.test(line) || /^(\+\+\+|---) /.test(line)) continue;
    for (const m of line.matchAll(SYMBOL)) terms.add(m[1].toLowerCase());
  }
  for (const t of [...terms]) if (!t || t.includes("|") || t.includes("\n")) terms.delete(t);
  return terms;
}

export function renderTermsIdx(entries, diffs = {}) {
  const map = new Map();
  for (const e of entries) for (const t of termsFor(e, diffs[e.id] || "")) map.set(t, [...(map.get(t) || []), e.id]);
  let rows = [...map.entries()].sort((a, b) => cmp(a[0], b[0]));
  const head = "# terms.idx -- GENERATED inverted index: term|library ids. Look up: rg -i '^<term>\\|' .stealth/index/terms.idx\n";
  const size = (rs) => Buffer.byteLength(head + rs.map(([t, ids]) => `${t}|${ids.join(",")}\n`).join(""));
  // Over the cap: drop the least specific terms (the ones naming the most entries) first.
  if (size(rows) > TERMS_MAX_BYTES) {
    const bySpread = [...rows].sort((a, b) => b[1].length - a[1].length || cmp(a[0], b[0]));
    const drop = new Set();
    while (size(rows.filter(([t]) => !drop.has(t))) > TERMS_MAX_BYTES && bySpread.length) drop.add(bySpread.shift()[0]);
    rows = rows.filter(([t]) => !drop.has(t));
  }
  return head + rows.map(([t, ids]) => `${t}|${ids.join(",")}\n`).join("");
}

// ---- SUMMARY.md: the "dynamic summary" (Codex memories pattern: one small file read whole, details by grep)

function readMeta(root) {
  try { return JSON.parse(read(P(root, "meta.json")) || "{}"); } catch { return {}; }
}

function claimsStats(text) {
  const topics = new Map();
  let stale = 0;
  let n = 0;
  for (const line of String(text || "").split("\n")) {
    if (!line.startsWith("CLAIM|")) continue;
    const f = line.split(SEP);
    n++;
    if (f[2] === "stale") stale++;
    topics.set(f[3], (topics.get(f[3]) || 0) + 1);
  }
  return { n, stale, topics };
}

function unitPaths(root) {
  const text = read(P(root, "index", "units.idx"));
  if (!text) return [];
  return text.split("\n").map((l) => l.replace(/\r$/, "")).filter((l) => l && !l.startsWith("#")).map((l) => l.split(SEP)[0]);
}

export function renderSummary(root, { lib, routing, now = new Date() } = {}) {
  const meta = readMeta(root);
  const ident = meta.repo_identity || {};
  const library = lib || loadLibrary(root);
  const { routes, obs } = routing || parseRouting(read(P(root, "routing.md")) || "");
  const claims = claimsStats(read(P(root, "claims.md")));
  const units = unitPaths(root);
  const sources = ["claims.md", "library.md", "routing.md", "index/units.idx", "meta.json"]
    .map((f) => `${f}=${shortHash(read(P(root, f)) || "")}`).join(" ");
  const counts = (key) => library.entries.reduce((m, e) => m.set(e[key], (m.get(e[key]) || 0) + 1), new Map());
  const fmt = (m) => [...m.entries()].sort((a, b) => b[1] - a[1] || cmp(a[0], b[0])).map(([k, v]) => `${k} ${v}`).join(", ");
  const head = [
    "# SUMMARY.md -- GENERATED by `stealthlab-mcp library index`. Read it whole; look details up by grep.",
    `# sources: ${sources}`,
    "",
    `Repo: ${ident.public_name || ident.repo_id || "unknown (run the survey: it writes .stealth/meta.json repo_identity)"}` +
      (ident.repo_id ? ` (identity ${ident.strength === "weak" || String(ident.repo_id).startsWith("p:") ? "weak" : "strong"})` : ""),
    `Facts: claims.md ${claims.n}${claims.n ? ` (${fmt(claims.topics)})` : ""}${claims.stale ? `; ${claims.stale} stale` : ""}`,
  ];
  const groups = new Map();
  for (const u of units) {
    const top = u === "." ? "." : u.split("/")[0] + (u.includes("/") ? "/" : "");
    groups.set(top, (groups.get(top) || 0) + 1);
  }
  const unitLine = !units.length ? "Units: not surveyed (index/units.idx missing)"
    : units.length <= 12 ? `Units: ${units.length}: ${units.join(", ")}` : `Units: ${units.length} in ${groups.size} groups: ${fmt(groups)}`;
  const stale = library.entries.filter((e) => e.status === "stale").length;
  const libLine = `Library: ${library.entries.length} solved here` +
    (library.entries.length ? ` (${fmt(counts("outcome"))}${stale ? `; ${stale} stale -- re-check before reuse` : ""})` : "");
  const tried = obs.reduce((s, o) => s + o.n, 0);
  const passed = obs.reduce((s, o) => s + o.ok, 0);
  const routeLine = `Routes: ${new Set(routes.map((r) => r.id)).size} (local attempts ${tried}, accepted ${passed})`;
  const newest = [...library.entries].sort((a, b) => cmp(b.verified_at || "", a.verified_at || "") || cmp(a.id, b.id))
    .map((e) => `  ${e.id} ${e.title}${e.unit !== "." ? ` [${e.unit}]` : ""}${e.status === "stale" ? " (stale)" : ""}`);
  const tail = [
    "Look up:",
    "  rg -i '^<file, symbol or word>\\|' .stealth/index/terms.idx   -> library ids",
    "  rg '^(GOAL|PROC|STEP)\\|<L-id>' .stealth/library.md           -> the entry; its diff: .stealth/library/solutions/<L-id>.diff",
    "  rg '^CLAIM\\|[^|]*\\|[^|]*\\|<topic>\\|' .stealth/claims.md        -> facts on a topic",
  ];
  const build = (n, withUnits) => [...head, withUnits ? unitLine : `Units: ${units.length}`, libLine,
    ...(n ? ["Newest:", ...newest.slice(0, n)] : []), routeLine, "", ...tail].join("\n") + "\n";
  for (const withUnits of [true, false]) {
    for (let n = Math.min(8, newest.length); n >= 0; n--) {
      const text = build(n, withUnits);
      if (Buffer.byteLength(text) <= SUMMARY_MAX_BYTES) return text;
    }
  }
  return build(0, false).slice(0, SUMMARY_MAX_BYTES);
}

// ---- index: canonical library.md + library.idx + terms.idx + SUMMARY.md (only what changed is rewritten)

export function buildIndex(root, { now = new Date() } = {}) {
  ensureGitFiles(root);
  const raw = read(P(root, "library.md"));
  const lib = parseLibrary(raw || "");
  const canonical = renderLibrary(lib);
  const changed = [];
  if (raw !== null && raw !== canonical && writeIfChanged(P(root, "library.md"), canonical)) changed.push("library.md");
  if (writeIfChanged(P(root, "index", "library.idx"), renderLibraryIdx(canonical))) changed.push("index/library.idx");
  const diffs = {};
  for (const e of lib.entries) {
    const sol = e.procs.map((p) => p.solution).find(Boolean);
    if (sol) diffs[e.id] = read(P(root, "library", sol)) || "";
  }
  if (writeIfChanged(P(root, "index", "terms.idx"), renderTermsIdx(lib.entries, diffs))) changed.push("index/terms.idx");
  if (writeIfChanged(P(root, "SUMMARY.md"), renderSummary(root, { lib, now }))) changed.push("SUMMARY.md");
  return { entries: lib.entries.length, problems: lib.problems, changed };
}

// The idx is trusted only while its source_sha matches library.md; otherwise it is rebuilt (self-healing).
export function ensureIndex(root) {
  const lib = read(P(root, "library.md"));
  const idx = read(P(root, "index", "library.idx"));
  if (lib === null) return { rebuilt: false, idx: idx || "" };
  const sha = /# source_sha=([0-9a-f]+)/.exec(idx || "")?.[1];
  if (idx !== null && sha === shortHash(lib)) return { rebuilt: false, idx };
  buildIndex(root);
  return { rebuilt: true, idx: read(P(root, "index", "library.idx")) || "" };
}

// One entry by id: the idx range, checked against its block hash; a stale range rebuilds the idx and retries;
// as a last resort the entry is found by its id prefix (ids are authoritative, ranges are disposable).
export function readEntry(root, id) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const { idx } = ensureIndex(root);
    const row = parseLibraryRows(idx).find((r) => r.id === id);
    const lines = (read(P(root, "library.md")) || "").split("\n");
    if (row && row.start > 0) {
      const block = lines.slice(row.start - 1, row.end);
      if (shortHash(block.join("\n")) === row.block_sha) return { id, lines: block, via: attempt ? "rebuilt-idx" : "idx" };
    }
    if (attempt === 0) buildIndex(root);
  }
  const lines = (read(P(root, "library.md")) || "").split("\n").filter((l) => {
    const f = l.split(SEP);
    return ["GOAL", "PROC", "STEP"].includes(f[0]) && (f[1] || "").split(".")[0] === id;
  });
  return lines.length ? { id, lines, via: "scan" } : null;
}

// ---- staleness: touches=path#sha against `git hash-object` now

function git(root, args, input) {
  return execFileSync("git", ["-C", root, ...args], { encoding: "utf8", input, stdio: ["pipe", "pipe", "ignore"], maxBuffer: 64 << 20 });
}

export function currentShas(root, paths, { gitImpl = git } = {}) {
  const out = new Map();
  const existing = paths.filter((p) => fs.existsSync(path.join(root, p)));
  if (existing.length) {
    const shas = gitImpl(root, ["hash-object", "--stdin-paths"], existing.join("\n") + "\n").trim().split(/\r?\n/);
    existing.forEach((p, i) => out.set(p, shas[i] || null));
  }
  return out;
}

function sparseCheckout(root, gitImpl) {
  try { return gitImpl(root, ["config", "--bool", "core.sparseCheckout"]).trim() === "true"; } catch { return false; }
}

// Marks an entry `stale` when any file it touched changed or vanished (it is re-checked before reuse, never
// deleted). Under a sparse checkout a missing file is `unverifiable`, not a change.
export function checkStaleness(root, { gitImpl = git, write = true } = {}) {
  const lib = loadLibrary(root);
  const all = [...new Set(lib.entries.flatMap((e) => e.procs.flatMap((p) => p.touches.map((t) => t.path))))];
  const now = all.length ? currentShas(root, all, { gitImpl }) : new Map();
  const sparse = all.some((p) => !now.has(p)) && sparseCheckout(root, gitImpl);
  const report = [];
  for (const e of lib.entries) {
    const changed = [];
    const unverifiable = [];
    for (const t of e.procs.flatMap((p) => p.touches)) {
      const sha = now.get(t.path);
      if (!sha) (sparse ? unverifiable : changed).push(`${t.path} (missing)`);
      else if (!sha.startsWith(t.sha)) changed.push(t.path);
    }
    if (changed.length && e.status !== "stale") {
      e.status = "stale";
      report.push({ id: e.id, status: "stale", changed });
    } else if (unverifiable.length) {
      report.push({ id: e.id, status: e.status, unverifiable });
    }
  }
  if (write && report.some((r) => r.status === "stale")) {
    writeIfChanged(P(root, "library.md"), renderLibrary(lib));
    buildIndex(root);
  }
  return report;
}

// After an entry was re-checked and still holds: current shas, status current, verified today.
export function refreshEntry(root, id, { gitImpl = git, now = new Date() } = {}) {
  const lib = loadLibrary(root);
  const e = lib.entries.find((x) => x.id === id);
  if (!e) throw new Error(`no library entry ${id}`);
  const shas = currentShas(root, e.procs.flatMap((p) => p.touches.map((t) => t.path)), { gitImpl });
  for (const p of e.procs) p.touches = p.touches.filter((t) => shas.get(t.path)).map((t) => ({ path: t.path, sha: shas.get(t.path).slice(0, 12) }));
  e.status = "current";
  e.verified_at = today(now);
  writeIfChanged(P(root, "library.md"), renderLibrary(lib));
  buildIndex(root);
  return e;
}

// ---- write-back: a new entry after its check passed

export function diffPaths(diffText) {
  const out = [];
  for (const line of String(diffText).split("\n")) {
    const m = /^\+\+\+ (?:b\/)?(.+?)\s*$/.exec(line);
    if (m && m[1] !== "/dev/null" && !out.includes(m[1])) out.push(m[1]);
  }
  return out;
}

export function diffFromGit(root, base = "HEAD", { gitImpl = git } = {}) {
  let text = gitImpl(root, ["diff", "--no-color", base, "--"]);
  const untracked = gitImpl(root, ["ls-files", "--others", "--exclude-standard"]).split(/\r?\n/)
    .filter((f) => f && !f.startsWith(".stealth/"));
  for (const f of untracked) {
    try {
      text += gitImpl(root, ["diff", "--no-color", "--no-index", "--", "/dev/null", f]);
    } catch (err) {
      text += err.stdout || "";   // --no-index exits 1 when the files differ, which they always do here
    }
  }
  return text;
}

export function runCheck(root, command) {
  const r = spawnSync(command, { cwd: root, shell: true, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 30 * 60 * 1000 });
  return { ok: r.status === 0, status: r.status, tail: `${r.stdout || ""}${r.stderr || ""}`.slice(-2000) };
}

// opts: {title, unit, g, p, name, outcome, tags, steps: [{kind, do, check}], diff (text), check, verify, route}
export function addEntry(root, opts, { gitImpl = git, now = new Date(), check = runCheck } = {}) {
  const title = String(opts.title || "").trim();
  if (!title) throw new Error("an entry needs a title (the problem it solved, as a goal)");
  const outcome = opts.outcome || "pass";
  if (!OUTCOMES.includes(outcome)) throw new Error(`outcome must be one of ${OUTCOMES.join(", ")}`);
  let verifiedAt = null;
  if (opts.check && opts.verify !== false) {
    const r = check(root, opts.check);
    if (!r.ok && outcome === "pass") throw new Error(`the check failed (exit ${r.status}); nothing written:\n${r.tail}`);
    if (r.ok) verifiedAt = today(now);
  } else if (outcome === "pass" && opts.verify !== false) {
    throw new Error("a passing entry needs --check <command> (run to prove it); use --no-verify to record it unverified");
  }
  const lib = loadLibrary(root);
  const id = newId("L", new Set(lib.entries.map((e) => e.id)));
  const diff = opts.diff || "";
  const touched = diffPaths(diff);
  const shas = touched.length ? currentShas(root, touched, { gitImpl }) : new Map();
  const solution = diff ? `solutions/${id}.diff` : null;
  const steps = (opts.steps || []).map((s, i) => ({ order: i + 1, kind: s.kind || "instruction", do: String(s.do || ""), check: s.check || null }));
  if (opts.check && !steps.some((s) => s.check)) steps.push({ order: steps.length + 1, kind: "action", do: "Run the check", check: opts.check });
  const entry = {
    id, title, unit: opts.unit || ".", g: opts.g || null, outcome, status: "current", verified_at: verifiedAt,
    route: opts.route || null, tags: opts.tags || [],
    procs: [{ index: 1, name: String(opts.name || title), p: opts.p || null, solution,
      touches: touched.filter((t) => shas.get(t)).map((t) => ({ path: t, sha: shas.get(t).slice(0, 12) })), steps }],
  };
  if (diff) writeIfChanged(P(root, "library", solution), diff);
  const text = renderLibrary([...lib.entries, entry]);
  if (Buffer.byteLength(text) > LIBRARY_MAX_BYTES) archiveOldest(root, lib, entry);
  else writeIfChanged(P(root, "library.md"), text);
  buildIndex(root);
  return entry;
}

// library.md over its cap: the oldest-verified entries move to library/archive-<yyyy-mm>.md (same grammar, still
// greppable), newest stay, until the chapter fits again.
function archiveOldest(root, lib, entry) {
  const keep = [...lib.entries, entry].sort((a, b) => cmp(b.verified_at || "", a.verified_at || ""));
  const moved = [];
  while (keep.length > 1 && Buffer.byteLength(renderLibrary(keep)) > LIBRARY_MAX_BYTES) moved.push(keep.pop());
  const file = P(root, "library", `archive-${new Date().toISOString().slice(0, 7)}.md`);
  const old = parseLibrary(read(file) || "").entries;
  writeIfChanged(file, renderLibrary([...old, ...moved]));
  writeIfChanged(P(root, "library.md"), renderLibrary(keep));
}

// ---- routing.md upkeep

// Server-rendered ROUTE lines replace older ones for the same (route, step); OBS lines are kept.
export function upsertRoutes(root, routeLines) {
  const file = P(root, "routing.md");
  const lines = (read(file) || ROUTING_HEADER).split("\n").filter((l, i, a) => l || i < a.length - 1);
  const keyOf = (line) => {
    const f = line.split(SEP);
    if (f[0] !== "ROUTE" || !ROUTE_ID.test(f[1] || "")) return null;
    return `${f[1]} ${splitKv(f.slice(2)).step || "*"}`;
  };
  const incoming = routeLines.filter((l) => keyOf(l));
  const replaced = new Set(incoming.map(keyOf));
  const kept = lines.filter((l) => !replaced.has(keyOf(l)));
  const body = [...kept.filter((l) => l.startsWith("#")), ...incoming, ...kept.filter((l) => l && !l.startsWith("#"))];
  writeIfChanged(file, body.join("\n") + "\n");
  return incoming.length;
}

// A route anchored to a global Goal with no plan yet: OBS need an anchor whose g= says which Goal they are about.
export function ensureRoute(root, goalId, { now = new Date() } = {}) {
  const { routes } = parseRouting(read(P(root, "routing.md")) || "");
  const existing = routes.find((r) => r.g === goalId);
  if (existing) return existing.id;
  const id = newId("R", new Set(routes.map((r) => r.id)));
  upsertRoutes(root, [["ROUTE", id, "goal=-", kv("g", goalId), "fit=-", "basis=-", kv("as_of", today(now)), "step=*",
    "ladder=-", "whole=-"].join(SEP)]);
  return id;
}

// One attempt's outcome on a route: the OBS line for (route, model, scaffold) is incremented in place.
export function recordObs(root, routeId, model, scaffold, accepted, { now = new Date() } = {}) {
  if (!ROUTE_ID.test(routeId)) throw new Error(`not a route id: ${routeId}`);
  const file = P(root, "routing.md");
  const lines = (read(file) || ROUTING_HEADER).split("\n");
  let hit = false;
  const out = lines.map((line) => {
    const f = line.split(SEP);
    if (hit || f[0] !== "OBS" || f[1] !== routeId || unesc(f[2] || "") !== model || unesc(f[3] || "") !== scaffold) return line;
    const k = splitKv(f.slice(4));
    if (!COUNT.test(k.n ?? "") || !COUNT.test(k.ok ?? "")) return line;
    hit = true;
    return renderObsLine({ route: routeId, model, scaffold, n: Number(k.n) + 1, ok: Number(k.ok) + (accepted ? 1 : 0), last: today(now) });
  });
  if (!hit) {
    while (out.length && out[out.length - 1] === "") out.pop();
    out.push(renderObsLine({ route: routeId, model, scaffold, n: 1, ok: accepted ? 1 : 0, last: today(now) }));
  }
  writeIfChanged(file, out.join("\n").replace(/\n*$/, "\n"));
}

// ---- what find_ways gets (plan §5.3). public_name only when the user allowed it (meta.json
// repo_identity.share_public_name, or STEALTHLAB_SHARE_REPO_NAME=1).

export function repoIdentityPayload(root, env = process.env) {
  const ident = readMeta(root).repo_identity;
  if (!ident || typeof ident !== "object" || !/^[rcp]:[0-9a-f]{16}$/.test(String(ident.repo_id || ""))) return null;
  const share = ident.share_public_name === true || env.STEALTHLAB_SHARE_REPO_NAME === "1";
  return {
    repo_id: ident.repo_id,
    ...(share && ident.public_name ? { public_name: String(ident.public_name) } : {}),
    ...(ident.strength === "weak" ? { strength: "weak" } : {}),
  };
}

function capBytes(text, max) {
  if (Buffer.byteLength(text) <= max) return text;
  const cut = Buffer.from(text).subarray(0, max).toString("utf8");
  return cut.slice(0, cut.lastIndexOf("\n") + 1);
}

export function requestPayload(root, { env = process.env } = {}) {
  const out = {};
  try {
    const { idx } = ensureIndex(root);
    const rows = idx.split("\n").filter((l) => l && !l.startsWith("#")).join("\n");
    if (rows) out.library_rows = capBytes(rows + "\n", LIBRARY_ROWS_MAX_BYTES);
  } catch { /* no library: nothing to send */ }
  const routing = read(P(root, "routing.md"));
  if (routing) {
    const lines = routing.split("\n").filter((l) => l.startsWith("ROUTE|") || l.startsWith("OBS|")).join("\n");
    if (lines) out.route_obs = capBytes(lines + "\n", ROUTE_OBS_MAX_BYTES);
  }
  const ident = repoIdentityPayload(root, env);
  if (ident) out.repo_identity = ident;
  return out;
}
