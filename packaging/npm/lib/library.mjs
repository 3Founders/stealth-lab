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
// Two layers in library.md (2026-10-08):
//   knowledge -- reusable, deduplicated: Goals (with parents), the Ways (procedures) that achieve them, their Steps.
//                G|G-xxxxxxxx|<title>|parent=<G-id or ->|g=<global goal id or ->|unit=<path or .>|tags=<csv or ->
//                W|W-xxxxxxxx|<name>|goal=<G-id>|p=<global procedure id or ->|v=<version>
//                S|W-xxxxxxxx:<k>|<action|instruction|subgoal>|<do>|check=<command or ->
//   solved here -- one entry per problem solved in this repo, with its diff (GOAL/PROC/STEP L- lines, below),
//                linked to the knowledge by goal=<G-id> (on GOAL) and way=<W-id> (on PROC).
// Knowledge ids are content hashes (the normalised title; the Way's step list), so two machines that learn the
// same thing write the same line and a merge=union merge stays clean. Routing keeps outcomes per Way: a plan for
// a matched entry is keyed to its Way, so every problem solved the same way shares one record.
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
const KGOAL_ID = /^G-[0-9a-f]{6,16}$/;
const CODE = /^c[0-9]{2}\.[0-9]+$/;
const WAY_ID = /^W-[0-9a-f]{6,16}$/;
const WAY_STEP_ID = /^(W-[0-9a-f]{6,16}):([0-9]+)$/;
const KGOAL_KEYS = new Set(["parent", "g", "unit", "tags"]);
const WAY_KEYS = new Set(["goal", "p", "v", "code"]);
const OUTCOMES = ["pass", "historical", "fail"];
const STATUSES = ["current", "stale"];
const RAW_KEYS = new Set(["touches", "tags", "ladder"]);
const GOAL_KEYS = new Set(["unit", "g", "outcome", "status", "verified_at", "route", "tags", "goal"]);
const PROC_KEYS = new Set(["p", "solution", "touches", "way"]);
const STEP_KEYS = new Set(["check"]);

// Fields another writer added (the survey's commit=<sha>, diff=truncated, ...): kept verbatim and in order, so
// canonicalising never loses them (== library.py _extra_fields).
function extraFields(fields, known) {
  return fields.filter((f) => {
    const i = f.indexOf("=");
    return f && !(i > 0 && known.has(f.slice(0, i)));
  });
}

export const LIBRARY_HEADER =
  "# library.md -- problems solved in THIS repository, with the diffs that solved them.\n" +
  "# Committed (team knowledge). One entry per id; lines may be in any order (merge=union safe).\n" +
  "# GOAL|<L-id>|<title>|unit=<path or .>|g=<global goal id or ->|outcome=<pass|historical|fail>|" +
  "status=<current|stale>|verified_at=<date>|route=<R-id or ->|tags=<csv or ->\n" +
  "# PROC|<L-id>.p<n>|<name>|p=<global procedure id or ->|solution=<solutions/<L-id>.diff or ->|" +
  "touches=<path#sha=<sha>,...>\n" +
  "# STEP|<L-id>.p<n>:<k>|<action|instruction|subgoal>|<do>|check=<command or ->\n" +
  "# Knowledge (reusable; content-hash ids): G|<G-id>|<title>|parent=|g=|unit=|tags=   " +
  "W|<W-id>|<name>|goal=<G-id>|p=|v=   S|<W-id>:<k>|<kind>|<do>|check=\n" +
  "# An entry links to it with goal=<G-id> on GOAL and way=<W-id> on PROC.\n";
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
    "tags=" + ((e.tags || []).length ? e.tags.map(escItem).join(",") : "-"), ...(e.goal ? [kv("goal", e.goal)] : []),
    ...(e.extra || []),
  ].join(SEP);
}

export function renderProcLine(entryId, p) {
  const touches = (p.touches || []).map((t) => `${escItem(t.path)}#sha=${t.sha}`).join(",") || "-";
  return ["PROC", `${entryId}.p${p.index}`, esc(pyStrip(p.name)), kv("p", p.p), kv("solution", p.solution),
    "touches=" + touches, ...(p.way ? [kv("way", p.way)] : []), ...(p.extra || [])].join(SEP);
}

export function renderStepLine(entryId, procIndex, s) {
  return ["STEP", `${entryId}.p${procIndex}:${s.order}`, esc(s.kind), esc(pyStrip(s.do)), kv("check", s.check),
    ...(s.extra || [])].join(SEP);
}

export function renderBlock(e) {
  const lines = [renderGoalLine(e)];
  for (const p of [...(e.procs || [])].sort((a, b) => a.index - b.index)) {
    lines.push(renderProcLine(e.id, p));
    for (const s of [...(p.steps || [])].sort((a, b) => a.order - b.order)) lines.push(renderStepLine(e.id, p.index, s));
  }
  return lines;
}

export function renderKGoalLine(g) {
  return ["G", g.id, esc(pyStrip(g.title)), kv("parent", g.parent), kv("g", g.g), kv("unit", g.unit || "."),
    "tags=" + ((g.tags || []).length ? g.tags.map(escItem).join(",") : "-"), ...(g.extra || [])].join(SEP);
}

export function renderWayLine(w) {
  return ["W", w.id, esc(pyStrip(w.name)), kv("goal", w.goal), kv("p", w.p), kv("v", w.v || 1),
    ...(w.code ? [kv("code", w.code)] : []), ...(w.extra || [])].join(SEP);
}

export function renderWayStepLine(wayId, s) {
  return ["S", `${wayId}:${s.order}`, esc(s.kind), esc(pyStrip(s.do)), kv("check", s.check), ...(s.extra || [])].join(SEP);
}

// The knowledge section: each Goal, then the Ways that achieve it with their Steps; Ways whose Goal is not
// declared come last. Sorted by id, so the text is canonical whatever order lines were merged in.
export function renderKnowledge(goals = [], ways = []) {
  const blocks = [];
  const wayBlock = (w) => [renderWayLine(w), ...[...(w.steps || [])].sort((a, b) => a.order - b.order)
    .map((st) => renderWayStepLine(w.id, st))];
  const known = new Set(goals.map((g) => g.id));
  for (const g of [...goals].sort((a, b) => cmp(a.id, b.id))) {
    blocks.push([renderKGoalLine(g), ...ways.filter((w) => w.goal === g.id).sort((a, b) => cmp(a.id, b.id))
      .flatMap(wayBlock)].join("\n"));
  }
  for (const w of ways.filter((x) => !known.has(x.goal)).sort((a, b) => cmp(a.id, b.id))) blocks.push(wayBlock(w).join("\n"));
  return blocks;
}

export function renderLibrary(lib) {
  const list = Array.isArray(lib) ? lib : lib.entries;
  const knowledge = Array.isArray(lib) ? [] : renderKnowledge(lib.goals || [], lib.ways || []);
  const blocks = [...knowledge, ...[...list].sort((a, b) => cmp(a.id, b.id)).map((e) => renderBlock(e).join("\n"))];
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
  const kgoals = new Map();
  const ways = new Map();
  const wsteps = new Map();
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
    } else if (f[0] === "G" && f.length >= 3 && KGOAL_ID.test(f[1])) {
      keep(kgoals, f[1], f[1], line, f, (ff, l) => l);
    } else if (f[0] === "W" && f.length >= 3 && WAY_ID.test(f[1])) {
      keep(ways, f[1], f[1], line, f, (ff, l) => l);
    } else if (f[0] === "S" && f.length >= 4 && (m = WAY_STEP_ID.exec(f[1]))) {
      keep(wsteps, `${m[1]} ${m[2]}`, m[1], line, f, (ff, l) => l);
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
      ...(KGOAL_ID.test(k.goal || "") ? { goal: k.goal } : {}), extra: extraFields(f.slice(3), GOAL_KEYS),
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
    const p = { index: idx, name: unesc(f[2]), p: dash(k.p), solution: dash(k.solution), touches: parseTouches(k.touches), steps: [],
      ...(WAY_ID.test(k.way || "") ? { way: k.way } : {}), extra: extraFields(f.slice(3), PROC_KEYS) };
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
    p.steps.push({ order, kind: unesc(f[2]), do: unesc(f[3]), check: dash(k.check), extra: extraFields(f.slice(4), STEP_KEYS) });
  }
  const kgoalList = [...kgoals.entries()].sort((a, b) => cmp(a[0], b[0])).map(([id, { fields: f }]) => {
    const k = splitKv(f.slice(3));
    return { id, title: unesc(f[2]), parent: KGOAL_ID.test(k.parent || "") ? k.parent : null, g: dash(k.g),
      unit: dash(k.unit) || ".", tags: parseTags(k.tags), extra: extraFields(f.slice(3), KGOAL_KEYS) };
  });
  const wayMap = new Map();
  for (const [id, { fields: f }] of [...ways.entries()].sort((a, b) => cmp(a[0], b[0]))) {
    const k = splitKv(f.slice(3));
    wayMap.set(id, { id, name: unesc(f[2]), goal: KGOAL_ID.test(k.goal || "") ? k.goal : null, p: dash(k.p),
      v: COUNT.test(k.v || "") ? Number(k.v) : 1, steps: [], extra: extraFields(f.slice(3), WAY_KEYS),
      ...(CODE.test(k.code || "") ? { code: k.code } : {}) });
  }
  for (const [key, { fields: f }] of [...wsteps.entries()].sort((a, b) => cmp(a[0], b[0]))) {
    const [wid, order] = key.split(" ");
    const w = wayMap.get(wid);
    if (!w) {
      problems.push(`orphan: ${wid}:${order} has no W line; skipped`);
      continue;
    }
    const k = splitKv(f.slice(4));
    w.steps.push({ order: Number(order), kind: unesc(f[2]), do: unesc(f[3]), check: dash(k.check),
      extra: extraFields(f.slice(4), STEP_KEYS) });
  }
  for (const w of wayMap.values()) w.steps.sort((a, b) => a.order - b.order);
  return { entries: [...entries.values()].sort((a, b) => cmp(a.id, b.id)), goals: kgoalList, ways: [...wayMap.values()],
    problems };
}

// ------------------------------------------------------------------ knowledge: link entries to Goals and Ways

const norm = (t) => String(t || "").toLowerCase().replace(/\s+/g, " ").trim();
export const goalIdFor = (title) => `G-${shortHash(`goal|${norm(title)}`).slice(0, 8)}`;
export function wayIdFor(name, steps) {
  const sig = (steps || []).length
    ? steps.map((st) => [norm(st.kind), norm(st.do), norm(st.check)].join("|")).join("\n")
    : `name|${norm(name)}`;
  return `W-${shortHash(`way|${sig}`).slice(0, 8)}`;
}

// Give every entry a Goal and every one of its procedures a Way, reusing what exists: the same normalised title is
// the same Goal, the same step list (kind, text, check) is the same Way. Entries already linked are left alone.
// Returns the number of links added; `lib` is changed in place.
export function linkKnowledge(lib) {
  lib.goals = lib.goals || [];
  lib.ways = lib.ways || [];
  const goals = new Map(lib.goals.map((g) => [g.id, g]));
  const ways = new Map(lib.ways.map((w) => [w.id, w]));
  let added = 0;
  for (const e of lib.entries) {
    if (e.outcome === "fail") continue;                 // a failed attempt is not knowledge to reuse
    if (!e.goal) {
      const id = goalIdFor(e.title);
      if (!goals.has(id)) {
        const g = { id, title: e.title, parent: null, g: e.g || null, unit: e.unit || ".", tags: [...(e.tags || [])], extra: [] };
        goals.set(id, g);
        lib.goals.push(g);
      }
      e.goal = id;
      added++;
    }
    for (const p of e.procs || []) {
      if (p.way) continue;
      const steps = (p.steps || []).map((st) => ({ order: st.order, kind: st.kind, do: st.do, check: st.check || null, extra: [] }));
      const id = wayIdFor(p.name, steps);
      if (!ways.has(id)) {
        const w = { id, name: p.name, goal: e.goal, p: p.p || null, v: 1, steps, extra: [] };
        ways.set(id, w);
        lib.ways.push(w);
      }
      p.way = id;
      added++;
    }
  }
  return added;
}

// `library link`: link every unlinked entry, write library.md if anything changed, rebuild the indexes.
export function linkLibrary(root) {
  const lib = loadLibrary(root);
  const added = linkKnowledge(lib);
  if (added) {
    // the knowledge lines take room too: keep library.md within its budget by archiving the oldest entries and
    // dropping Goals / Ways no remaining entry uses (their ids are content hashes, so they come back if needed)
    const moved = [];
    while (lib.entries.length > 1 && Buffer.byteLength(renderLibrary(lib)) > LIBRARY_MAX_BYTES) {
      lib.entries.sort((a, b) => cmp(b.verified_at || "", a.verified_at || "") || cmp(a.id, b.id));
      moved.push(lib.entries.pop());
      pruneKnowledge(lib);
    }
    // mined history goes where the survey looks for it (so a re-run never mines it again); the rest by month
    const groups = new Map();
    for (const e of moved) {
      const name = e.outcome === "historical" ? "archive-historical.md" : `archive-${new Date().toISOString().slice(0, 7)}.md`;
      groups.set(name, [...(groups.get(name) || []), e]);
    }
    for (const [name, es] of groups) {
      const file = P(root, "library", name);
      writeIfChanged(file, renderLibrary([...parseLibrary(read(file) || "").entries, ...es]));
    }
    writeIfChanged(P(root, "library.md"), renderLibrary(lib));
  }
  const built = buildIndex(root);
  return { linked: added, goals: lib.goals.length, ways: lib.ways.length, entries: lib.entries.length, changed: built.changed };
}

// Goals and Ways that no entry in library.md links to any more (their entries were archived). A Goal another kept
// Goal names as its parent stays.
function pruneKnowledge(lib) {
  const usedWays = new Set(lib.entries.flatMap((e) => (e.procs || []).map((p) => p.way)).filter(Boolean));
  lib.ways = (lib.ways || []).filter((w) => usedWays.has(w.id));
  const used = new Set([...lib.entries.map((e) => e.goal), ...lib.ways.map((w) => w.goal)].filter(Boolean));
  for (let grew = true; grew;) {
    grew = false;
    for (const g of lib.goals || []) if (used.has(g.id) && g.parent && !used.has(g.parent)) { used.add(g.parent); grew = true; }
  }
  lib.goals = (lib.goals || []).filter((g) => used.has(g.id));
}

// ---- semantic codes: what kind of work a Way is (the server's codebook; backend/app/routing/semantic_codes.py)

// The text a Way is embedded as: its name and its steps, the way the server's procedures are described.
export function wayText(w) {
  return [w.name, ...(w.steps || []).map((st) => `${st.order}. ${st.do}${st.check ? ` (check: ${st.check})` : ""}`)]
    .join("\n").slice(0, 4000);
}

const VECTORS = ["index", "way_vectors.json"];        // kept locally (gitignored): re-coding never resends the text

// Ask the server for the codes of the Ways that have none (POST <server>/routing/codes, one call, at most 50 Ways),
// write them onto the W lines and keep the vectors in index/way_vectors.json. Needs a signed-in token; any
// failure leaves the Ways uncoded (routing then uses fix size alone) and is reported, never thrown.
export async function codeWays(root, { url, token, fetchImpl = globalThis.fetch, userAgent = "stealthlab-mcp" } = {}) {
  const lib = loadLibrary(root);
  const todo = (lib.ways || []).filter((w) => !w.code).slice(0, 50);
  if (!todo.length) return { coded: 0 };
  if (!url || !token) return { coded: 0, skipped: "not signed in (stealthlab-mcp login): Ways stay uncoded" };
  let target;
  try {
    const u = new URL(url);
    u.pathname = u.pathname.replace(/\/mcp\/?$/, "").replace(/\/$/, "") + "/routing/codes";
    target = u.toString();
  } catch {
    return { coded: 0, skipped: "bad server URL" };
  }
  let body;
  try {
    const res = await fetchImpl(target, {
      method: "POST", signal: AbortSignal.timeout(20000),
      headers: { "content-type": "application/json", authorization: `Bearer ${token}`, "user-agent": userAgent },
      body: JSON.stringify({ items: todo.map((w) => ({ id: w.id, text: wayText(w) })) }),
    });
    body = await res.json().catch(() => ({}));
    if (!res.ok) return { coded: 0, skipped: `server answered ${res.status}${body.error ? `: ${body.error}` : ""}` };
  } catch (err) {
    return { coded: 0, skipped: `server unreachable (${err.name || "error"})` };
  }
  let coded = 0;
  for (const w of lib.ways) {
    const c = body.codes?.[w.id];
    if (typeof c === "string" && CODE.test(c)) { w.code = c; coded++; }
  }
  if (coded) {
    writeIfChanged(P(root, "library.md"), renderLibrary(lib));
    const file = P(root, ...VECTORS);
    let kept = {};
    try { kept = JSON.parse(read(file) || "{}"); } catch { kept = {}; }
    kept.version = body.version;
    kept.embedding_model_id = body.embedding_model_id;
    kept.vectors = { ...(kept.vectors || {}), ...(body.vectors || {}) };
    writeIfChanged(file, JSON.stringify(kept));
    buildIndex(root);
  }
  return { coded, version: body.version };
}

// `library goal <G-id> --parent <G-id>`: set (or clear with "-") a Goal's parent. A cycle is refused.
export function setGoalParent(root, id, parent) {
  const lib = loadLibrary(root);
  const g = lib.goals.find((x) => x.id === id);
  if (!g) throw new Error(`no knowledge goal ${id}`);
  const target = parent === "-" ? null : parent;
  if (target) {
    const byId = new Map(lib.goals.map((x) => [x.id, x]));
    if (!byId.has(target)) throw new Error(`no knowledge goal ${target}`);
    for (let cur = target; cur; cur = byId.get(cur)?.parent) {
      if (cur === id) throw new Error(`${target} is under ${id}: a goal cannot be its own ancestor`);
    }
  }
  g.parent = target;
  writeIfChanged(P(root, "library.md"), renderLibrary(lib));
  buildIndex(root);
  return g;
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

// The directory holding .stealth/ at or above `cwd` (the agent may run in a sub-directory), or null.
export function findStealthRoot(cwd = process.cwd()) {
  let dir = path.resolve(cwd);
  for (;;) {
    if (fs.existsSync(path.join(dir, ".stealth"))) return dir;
    const up = path.dirname(dir);
    if (up === dir) return null;
    dir = up;
  }
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
const GITIGNORE_LINES = ["index/library.idx", "index/terms.idx", "SUMMARY.md", "routing.md", "index/way_vectors.json"];

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
  return text.split("\n").map((l) => l.replace(/\r$/, "")).filter((l) => l && !l.startsWith("#"))
    .map((l) => { const f = l.split(SEP); return f[0] === "UNIT" ? f[2] : f[0]; }).filter(Boolean);
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
  const kg = library.goals || [];
  const kw = library.ways || [];
  const knowLine = `Knowledge: ${kg.length} goals${kg.some((g) => g.parent) ? ` (${kg.filter((g) => g.parent).length} under a parent)` : ""}, ` +
    `${kw.length} ways${library.entries.some((e) => !e.goal) ? " (some entries unlinked: run `stealthlab-mcp library link`)" : ""}`;
  const tried = obs.reduce((s, o) => s + o.n, 0);
  const passed = obs.reduce((s, o) => s + o.ok, 0);
  const routeLine = `Routes: ${new Set(routes.map((r) => r.id)).size} (local attempts ${tried}, accepted ${passed})`;
  const newest = [...library.entries].sort((a, b) => cmp(b.verified_at || "", a.verified_at || "") || cmp(a.id, b.id))
    .map((e) => `  ${e.id} ${e.title}${e.unit !== "." ? ` [${e.unit}]` : ""}${e.status === "stale" ? " (stale)" : ""}`);
  const tail = [
    "Look up:",
    "  rg -i '^<file, symbol or word>\\|' .stealth/index/terms.idx   -> library ids",
    "  rg '^(GOAL|PROC|STEP)\\|<L-id>' .stealth/library.md           -> the entry; its diff: .stealth/library/solutions/<L-id>.diff",
    "  rg '^(W|S)\\|<W-id>' .stealth/library.md                       -> a reusable way and its steps",
    "  rg '^CLAIM\\|[^|]*\\|[^|]*\\|<topic>\\|' .stealth/claims.md        -> facts on a topic",
  ];
  const build = (n, withUnits) => [...head, withUnits ? unitLine : `Units: ${units.length}`, libLine, knowLine,
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
    if (["GOAL", "PROC", "STEP"].includes(f[0])) return (f[1] || "").split(".")[0] === id;
    if (f[0] === "G") return f[1] === id;
    if (f[0] === "W" || f[0] === "S") return (f[1] || "").split(":")[0] === id;
    return false;
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
      if (!t.sha || t.sha === "-") unverifiable.push(`${t.path} (no recorded sha)`);
      else if (!sha) (sparse ? unverifiable : changed).push(`${t.path} (missing)`);
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
    route: opts.route || null, tags: opts.tags || [], goal: opts.goal || null,
    procs: [{ index: 1, name: String(opts.name || title), p: opts.p || null, solution, way: opts.way || null,
      touches: touched.filter((t) => shas.get(t)).map((t) => ({ path: t, sha: shas.get(t).slice(0, 12) })), steps }],
  };
  if (diff) writeIfChanged(P(root, "library", solution), diff);
  const next = { ...lib, entries: [...lib.entries, entry] };
  linkKnowledge(next);                                  // reuse the Goal / Way this problem shares, or add them
  const text = renderLibrary(next);
  if (Buffer.byteLength(text) > LIBRARY_MAX_BYTES) archiveOldest(root, next, entry);
  else writeIfChanged(P(root, "library.md"), text);
  buildIndex(root);
  return entry;
}

// library.md over its cap: the oldest-verified entries move to library/archive-<yyyy-mm>.md (same grammar, still
// greppable), newest stay, until the chapter fits again.
// The knowledge section always stays in library.md: only solved entries are archived.
function archiveOldest(root, lib, entry) {
  void entry;
  const keep = [...lib.entries].sort((a, b) => cmp(b.verified_at || "", a.verified_at || ""));
  const moved = [];
  const size = (es) => Buffer.byteLength(renderLibrary({ ...lib, entries: es }));
  while (keep.length > 1 && size(keep) > LIBRARY_MAX_BYTES) moved.push(keep.pop());
  const file = P(root, "library", `archive-${new Date().toISOString().slice(0, 7)}.md`);
  const old = parseLibrary(read(file) || "").entries;
  writeIfChanged(file, renderLibrary([...old, ...moved]));
  writeIfChanged(P(root, "library.md"), renderLibrary({ ...lib, entries: keep }));
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

// ---- task_features: the fix size of each solved entry, and of this repository's typical fix (numbers only).
// The routing model learned how a fix's size moves a task's difficulty (backend/app/routing/goal_features.py); a
// plan keyed to a library entry or to this repository uses these to set its prior. Same counting as patch_stats.
const LANG = {
  py: "python", pyi: "python", js: "js", jsx: "js", mjs: "js", ts: "ts", tsx: "ts", go: "go", rs: "rust",
  java: "java", kt: "kotlin", scala: "scala", c: "c", h: "c", cc: "cpp", cpp: "cpp", hpp: "cpp", cs: "csharp",
  rb: "ruby", php: "php", swift: "swift", m: "objc", sh: "shell", sql: "sql", yml: "yaml", yaml: "yaml",
  toml: "toml", json: "json", md: "docs", rst: "docs", txt: "docs", cfg: "config", ini: "config", html: "html",
  css: "css",
};

export function patchStats(patch) {
  const files = [...String(patch || "").matchAll(/^diff --git a\/(\S+) b\/(\S+)/gm)].map((m) => m[2]);
  let hunks = 0, added = 0, removed = 0;
  for (const line of String(patch || "").split("\n")) {
    if (line.startsWith("@@")) hunks++;
    else if (line.startsWith("+") && !line.startsWith("+++")) added++;
    else if (line.startsWith("-") && !line.startsWith("---")) removed++;
  }
  const langs = new Set(files.filter((f) => f.split("/").pop().includes("."))
    .map((f) => LANG[f.split(".").pop().toLowerCase()] || "other"));
  const tops = new Set(files.map((f) => (f.includes("/") ? f.split("/")[0] : ".")));
  return { files: files.length, hunks, lines_added: added, lines_removed: removed, languages: langs.size,
           packages: tops.size };
}

const MAX_FEATURE_ENTRIES = 200;

export function taskFeatures(root) {
  const entries = {};
  let n = 0;
  for (const e of loadLibrary(root).entries) {
    if (n >= MAX_FEATURE_ENTRIES) break;
    if (e.outcome === "fail") continue;
    const sol = e.procs.map((p) => p.solution).find(Boolean);
    const diff = sol ? read(P(root, sol.startsWith("library/") ? sol : `library/${sol}`)) : null;
    if (!diff) continue;
    const st = patchStats(diff);
    if (!st.files) continue;
    const way = e.procs.map((p) => p.way).find(Boolean);
    entries[e.id] = { ...st, ...(e.goal ? { goal: e.goal } : {}), ...(way ? { way } : {}) };
    n++;
  }
  const all = Object.values(entries);
  if (!all.length) return null;
  const median = (k) => {
    const v = all.map((x) => x[k]).sort((a, b) => a - b);
    return v[Math.floor((v.length - 1) / 2)];
  };
  const FIELDS = ["files", "hunks", "lines_added", "lines_removed", "languages", "packages"];
  const repo = Object.fromEntries(FIELDS.map((k) => [k, median(k)]));
  const byWay = new Map();
  for (const st of all) if (st.way) byWay.set(st.way, [...(byWay.get(st.way) || []), st]);
  const codeOf = new Map((loadLibrary(root).ways || []).filter((w) => w.code).map((w) => [w.id, w.code]));
  const ways = Object.fromEntries([...byWay.entries()].map(([w, rows]) => [w, {
    ...Object.fromEntries(FIELDS.map((k) => {
      const v = rows.map((r) => r[k]).sort((a, b) => a - b);
      return [k, v[Math.floor((v.length - 1) / 2)]];
    })),
    ...(codeOf.has(w) ? { code: codeOf.get(w) } : {}),
  }]));
  return { entries, repo, ...(byWay.size ? { ways } : {}) };
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
  try {
    const tf = taskFeatures(root);
    if (tf) out.task_features = tf;
  } catch { /* no diffs: the plan uses the population prior */ }
  return out;
}

// ---- share: draft the submit_way call that offers one entry to everyone (plan_and_run step 8)
// Only what a stranger can use without this repository: the problem, the way's name and its steps with their
// checks. Never the diff, touched paths, unit path or route -- those describe this repository's code. The agent
// fills the three judgement fields (rationale, preconditions, expected outcome) and calls submit_way itself,
// so nothing is sent from here.
export function shareDraft(root, id, { procIndex = 0 } = {}) {
  const e = loadLibrary(root).entries.find((x) => x.id === id);
  if (!e) throw new Error(`no library entry ${id}`);
  if (e.outcome === "fail") throw new Error(`${id} records a failed attempt; only a way that passed can be shared`);
  const p = e.procs[procIndex];
  if (!p || !p.steps.length) throw new Error(`${id} has no steps to share (add them with --step when recording it)`);
  const steps = p.steps.map((st) => (st.check ? `${st.do} (check: ${st.check})` : st.do));
  const args = {
    name: p.name || e.title,
    steps_json: JSON.stringify(steps),
    rationale: "<why this works, in plain sentences>",
    preconditions_json: JSON.stringify(["<what must be true first, e.g. the stack and versions it needs>"]),
    expected_outcome_json: JSON.stringify(["<what is true when it worked>"]),
    ...(e.g ? { goal_id: e.g } : { goal: e.title, goal_objective: "<what counts as done>" }),
  };
  return {
    entry: id, status: e.status, call: "submit_way", arguments: args,
    left_out: ["the diff", "touched paths", "unit", "route"],
    next: "Replace every <...> placeholder, remove anything specific to this repository (names, paths, secrets), " +
      "then call submit_way with these arguments (use_tool(\"submit_way\", ...) if it is not in your tool list). " +
      "It is screened and then public.",
  };
}


// ------------------------------------------------------------------ tidy: drop a duplicate, merge two Goals
//
// `library tidy` (lib/library_tidy.mjs) finds what to clean; these two are the only edits it asks the agent to make,
// so library.md is never hand-edited. Both are idempotent, and both are honest about `merge=union`: a union merge
// keeps every line from both sides, so a deletion made on one branch comes back when the other branch's copy is
// merged in. Nothing breaks when that happens -- the entry shows up as a duplicate again and the same command
// removes it again -- but a clean state is only clean until the next merge.

export const SUPERSEDED_ARCHIVE = "archive-superseded.md";

// Move one entry out of library.md into library/archive-superseded.md (still greppable, same grammar, out of the
// index so find_ways never offers it). `supersededBy` names the entry that replaces it and is recorded on the
// archived GOAL line, with the date (unknown fields survive canonicalising). The diff stays in library/solutions/.
export function dropEntry(root, id, { supersededBy = null, now = new Date() } = {}) {
  const lib = loadLibrary(root);
  const e = lib.entries.find((x) => x.id === id);
  if (!e) throw new Error(`no library entry ${id}`);
  if (supersededBy) {
    if (supersededBy === id) throw new Error("an entry cannot supersede itself");
    if (!lib.entries.some((x) => x.id === supersededBy)) throw new Error(`no library entry ${supersededBy} to supersede ${id}`);
  }
  const archived = {
    ...e, extra: [...(e.extra || []).filter((f) => !/^(superseded_by|dropped_at)=/.test(f)),
      ...(supersededBy ? [kv("superseded_by", supersededBy)] : []), kv("dropped_at", today(now))],
  };
  const file = P(root, "library", SUPERSEDED_ARCHIVE);
  const old = parseLibrary(read(file) || "").entries.filter((x) => x.id !== id);
  writeIfChanged(file, renderLibrary([...old, archived]));
  lib.entries = lib.entries.filter((x) => x.id !== id);
  pruneKnowledge(lib);
  writeIfChanged(P(root, "library.md"), renderLibrary(lib));
  buildIndex(root);
  return { dropped: id, superseded_by: supersededBy, archived_in: `library/${SUPERSEDED_ARCHIVE}`, entries: lib.entries.length };
}

// Two knowledge Goals that are the same problem under different titles: every entry, Way and child Goal that named
// `dropId` names `keepId` instead, the dropped Goal's global link and tags are carried over where the kept Goal has
// none, and the dropped Goal's line goes. A merge that would make a Goal its own ancestor is refused.
export function mergeGoals(root, keepId, dropId) {
  if (keepId === dropId) throw new Error("a goal cannot be merged into itself");
  const lib = loadLibrary(root);
  const byId = new Map(lib.goals.map((g) => [g.id, g]));
  const keep = byId.get(keepId);
  const drop = byId.get(dropId);
  if (!keep) throw new Error(`no knowledge goal ${keepId}`);
  if (!drop) throw new Error(`no knowledge goal ${dropId}`);
  for (let cur = keep.parent; cur; cur = byId.get(cur)?.parent) {
    if (cur === dropId) throw new Error(`${keepId} is under ${dropId}: merging would make a goal its own ancestor`);
  }
  let entries = 0;
  let ways = 0;
  let children = 0;
  for (const e of lib.entries) if (e.goal === dropId) { e.goal = keepId; entries++; }
  for (const w of lib.ways) if (w.goal === dropId) { w.goal = keepId; ways++; }
  for (const g of lib.goals) if (g.parent === dropId) { g.parent = keep.parent === g.id ? null : keepId; children++; }
  if (!keep.g && drop.g) keep.g = drop.g;
  keep.tags = [...new Set([...(keep.tags || []), ...(drop.tags || [])])];
  lib.goals = lib.goals.filter((g) => g.id !== dropId);
  writeIfChanged(P(root, "library.md"), renderLibrary(lib));
  buildIndex(root);
  return { kept: keepId, merged: dropId, entries_moved: entries, ways_moved: ways, children_moved: children };
}
