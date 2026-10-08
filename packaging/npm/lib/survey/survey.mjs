// `stealthlab-mcp survey [path]` -- the deterministic half of survey_repo. Runs locally, uploads nothing.
//
//   1. list files (git ls-files, or a .gitignore-aware walk)          files.mjs
//   2. resolve units: workspaces, nesting, zones, templates           units.mjs
//   3. repository identity -> .stealth/meta.json                      identity.mjs
//   4. deterministic facts -> claims.md / claims/<unit>.md            facts.mjs, claims.mjs
//   5. validate every fact (scanner's and the agent's)                validate.mjs
//   6. coverage per unit, the agent's worklist, units.idx, root.idx
//   7. library.md from past fix commits (bounded)                     history.mjs
//   8. incremental state: what each fact cites, so a re-run after a one-file change touches only the facts
//      citing that file.
// `--validate` runs only step 5-6 (fast; what the agent runs after writing facts).
const cmpStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { git, listFiles, readText, fileSize } from "./files.mjs";
import { GENERATED_FILE_RE, resolveUnits } from "./units.mjs";
import { computeIdentity } from "./identity.mjs";
import { buildFacts } from "./facts.mjs";
import { claimsOf, idPrefix, kvGet, mergeScannerFacts, pageFor, pageText, readPage, renderClaim, scopeFor, TOPICS, writeAtomic } from "./claims.mjs";
import { makeContext, validatePage } from "./validate.mjs";
import { HISTORY_DEFAULTS, mineHistory } from "./history.mjs";

export const DEFAULTS = {
  maxUnits: 40,              // above this many units, survey lazily: root + touched units only
  unitFiles: 10, unitBytes: 48 * 1024,
  rootFiles: 16, rootBytes: 96 * 1024,
  perFileBytes: 24 * 1024,
  surveyBytes: 640 * 1024,   // total read budget handed to the agent in one survey
};
const ROOT_TOPICS = ["purpose", "stack", "runtime", "build", "test", "lint", "ci", "layout"];
const UNIT_TOPICS = ["purpose", "stack", "build", "test"];
const INHERITED_FROM_ROOT = new Set(["runtime", "lint", "ci", "conventions", "env"]);

const posixDir = (p) => { const d = path.posix.dirname(p); return d === "." ? "." : d; };
const sha1 = (s) => crypto.createHash("sha1").update(s).digest("hex");

/**
 * @param {string} rootArg
 * @param {object} opts {validateOnly, touch: string[], maxUnits, history, historyMax, since, maxCommits,
 *                       verifyCommands, allowPublicName, mirrors, now}
 */
export function runSurvey(rootArg, opts = {}) {
  // An option passed as undefined (an unset CLI flag) must not erase its default.
  const o = { ...DEFAULTS, history: true, ...Object.fromEntries(Object.entries(opts).filter(([, v]) => v !== undefined)) };
  const t0 = performance.now();
  const timing = {};
  const root = path.resolve(rootArg || ".");
  // STEALTH_SURVEY_DEBUG=1: memory per phase (current RSS, heap, and the process's peak so far).
  const mem = (label) => {
    if (!process.env.STEALTH_SURVEY_DEBUG) return;
    const mb = (n) => Math.round(n / 1048576);
    process.stderr.write(`mem ${label}: rss ${mb(process.memoryUsage().rss)} MB heap ${mb(process.memoryUsage().heapUsed)} MB peak ${Math.round(process.resourceUsage().maxRSS / 1024)} MB\n`);
  };
  mem("start");
  const sdir = path.join(root, ".stealth");
  const read = (p) => readText(root, p);

  const listing = listFiles(root);
  listing.fileSet = new Set(listing.files); // one shared set for every stage
  timing.list_ms = Math.round(performance.now() - t0);
  mem("listed");

  const prevState = readJson(path.join(sdir, "index", "survey.state.json")) || {};
  const prevSlugs = readUnitsIdx(path.join(sdir, "index", "units.idx"));
  const t1 = performance.now();
  const resolved = resolveUnits(listing.files, read, { submodules: listing.submodules, previous: prevSlugs, fileSet: listing.fileSet });
  timing.units_ms = Math.round(performance.now() - t1);
  mem("units");
  const units = resolved.units;
  const multi = units.length > 1;
  const unitPaths = new Set(units.map((u) => u.path));
  const unitFiles = resolved.own;

  // ---------------- identity ----------------
  const metaFile = path.join(sdir, "meta.json");
  const meta = readJson(metaFile) || {};
  const identity = computeIdentity(root, { gitRoot: listing.gitRoot, units, meta, mirrors: o.mirrors || meta.survey?.mirrors,
    allowPublicName: o.allowPublicName });

  // ---------------- facts -> pages ----------------
  const t2 = performance.now();
  const pages = new Map(); // page rel path -> page
  const getPage = (rel) => {
    if (!pages.has(rel)) pages.set(rel, readPage(path.join(sdir, rel)));
    return pages.get(rel);
  };
  const nearestUnit = (d) => { for (;;) { if (unitPaths.has(d)) return d; if (d === ".") return "."; d = posixDir(d); } };
  let mergeStats = { added: 0, updated: 0, removed: 0, unchanged: 0 };
  if (!o.validateOnly) {
    const facts = buildFacts({ files: listing.files, fileSet: listing.fileSet, units, workspaces: resolved.workspaces,
      zones: resolved.zones, aux: resolved.aux, zoneOf: resolved.zoneOf, read, unitFiles, unitPaths });
    const byPage = new Map();
    const unitByPath = new Map(units.map((x) => [x.path, x]));
    for (const f of facts) {
      f.unit = nearestUnit(f.unit);
      const u = unitByPath.get(f.unit);
      const rel = pageFor(u, multi);
      if (!byPage.has(rel)) byPage.set(rel, { unit: u, facts: [] });
      byPage.get(rel).facts.push(f);
    }
    for (const u of units) { const rel = pageFor(u, multi); if (!byPage.has(rel)) byPage.set(rel, { unit: u, facts: [] }); }
    // Pages of units that no longer exist: their scanner facts go; the agent's facts stay for review.
    for (const rel of listPages(sdir)) if (!byPage.has(rel)) byPage.set(rel, { unit: null, facts: [] });
    for (const [rel, { unit, facts: pf }] of byPage) {
      const page = getPage(rel);
      const header = unit && unit.path !== "."
        ? `# ${rel} -- facts about unit ${unit.path} (${unit.name}); repository-wide facts are in claims.md. Written by stealthlab-mcp survey (by=scanner lines) and the agent.`
        : "# claims.md -- facts about this repository. Written by stealthlab-mcp survey (by=scanner lines) and the agent (survey_repo).";
      const r = mergeScannerFacts(page, pf, {
        prefix: unit ? idPrefix(unit, multi) : "R-x-",
        header,
        extra: (f) => {
          const e = [];
          if (f.key === "unit" && unit?.inherits) e.push(["inherits", unit.inherits]);
          if (f.key === "no-tests") e.push(["of", "test"]);
          if (f.key === "ci:none") e.push(["of", "ci"]);
          return e;
        },
      });
      for (const k of ["added", "updated", "removed"]) mergeStats[k] += r[k].length;
      mergeStats.unchanged += r.unchanged;
    }
  } else {
    for (const rel of listPages(sdir)) getPage(rel);
  }
  timing.facts_ms = Math.round(performance.now() - t2);
  mem("facts");

  // ---------------- validation (incremental) ----------------
  const t3 = performance.now();
  const fingerprints = fingerprintsFor(root, listing, pages, prevState);
  const changed = new Set();
  if (prevState.fingerprints) {
    for (const [p, fp] of Object.entries(fingerprints)) if (prevState.fingerprints[p] !== fp) changed.add(p);
    for (const p of Object.keys(prevState.fingerprints)) if (!(p in fingerprints)) changed.add(p);
  }
  const incremental = Boolean(prevState.fingerprints) && !o.full;
  const vctx = makeContext(root, listing, units);
  const vcounts = {};
  const rejected = [];
  const ran = [];
  const touchedFacts = [];
  for (const [rel, page] of pages) {
    const before = new Map(claimsOf(page).map((c) => [c.id, renderClaim(c)]));
    const r = validatePage(page, vctx, { verifyCommands: o.verifyCommands, only: incremental ? changed : null });
    for (const [k, v] of Object.entries(r.counts)) vcounts[k] = (vcounts[k] || 0) + v;
    for (const x of r.rejected) rejected.push({ page: rel, ...x });
    ran.push(...r.ran);
    for (const c of claimsOf(page)) if (before.has(c.id) && before.get(c.id) !== renderClaim(c)) touchedFacts.push(c.id);
  }
  timing.validate_ms = Math.round(performance.now() - t3);
  mem("validated");

  // ---------------- coverage + worklist ----------------
  const touchedUnits = new Set((o.touch || []).map((p) => nearestFromPath(normRel(root, p), unitPaths)));
  const lazy = units.length - 1 > o.maxUnits;
  const coverage = new Map();
  const missing = new Map();
  for (const u of units) {
    const page = pages.get(pageFor(u, multi)) || { lines: [] };
    const claims = claimsOf(page).filter((c) => c.status !== "stale" && (u.path === "." || !multi || c.scope === scopeFor(u.path)));
    const have = new Set(claims.map((c) => c.topic));
    for (const c of claims) if (c.topic === "absent" && kvGet(c, "of")) have.add(kvGet(c, "of"));
    const agentWrote = claims.some((c) => kvGet(c, "by") !== "scanner");
    const required = u.path === "." ? ROOT_TOPICS : UNIT_TOPICS;
    let need = required.filter((t) => !have.has(t) && !(u.path !== "." && INHERITED_FROM_ROOT.has(t)));
    if (u.inherits) {
      const repPage = pages.get(pageFor(units.find((x) => x.path === u.inherits), multi)) || { lines: [] };
      const repHave = new Set(claimsOf(repPage).filter((c) => c.status !== "stale").map((c) => c.topic));
      need = need.filter((t) => t === "purpose" || !repHave.has(t));
    }
    missing.set(u.path, need);
    coverage.set(u.path, !need.length ? (agentWrote ? "complete" : "scanner-complete") : agentWrote ? "partial" : "scanner");
  }
  const work = [];
  let budgetLeft = o.surveyBytes;
  const order = [...units].sort((a, b) => rankWork(a, touchedUnits) - rankWork(b, touchedUnits) || cmpStr(a.path, b.path));
  for (const u of order) {
    const cov = coverage.get(u.path);
    const need = missing.get(u.path);
    const stale = claimsOf(pages.get(pageFor(u, multi)) || { lines: [] }).filter((c) => c.status === "stale" && kvGet(c, "by") !== "scanner");
    // The root and a unit a task touches always get one agent pass (features, decisions, issues are never
    // deterministic); other units only when a required topic is still missing.
    const firstPass = (u.path === "." || touchedUnits.has(u.path)) && /^scanner/.test(cov);
    if (!need.length && !stale.length && !firstPass) continue;
    const isRep = !u.inherits;
    const wanted = u.path === "." || touchedUnits.has(u.path) || (!lazy && (isRep || need.includes("purpose")));
    if (!wanted && !stale.length) continue;
    const reads = readList(u, unitFiles.get(u.path) || [], listing, resolved, o, root);
    if (budgetLeft - reads.bytes < 0 && u.path !== "." && !touchedUnits.has(u.path)) { coverage.set(u.path, coverage.get(u.path) + ",deferred"); continue; }
    budgetLeft -= reads.bytes;
    if (reads.dropped.length) coverage.set(u.path, "partial");
    work.push({ unit: u, need, stale, reads, reason: u.path === "." ? "root" : touchedUnits.has(u.path) ? "touched" : u.inherits ? "purpose" : "template-rep" });
  }

  mem("worklist");
  // ---------------- writes ----------------
  const writes = [];
  const w = (rel, text) => { if (!o.dryRun && writeAtomic(path.join(sdir, rel), text)) writes.push(rel); };
  for (const [rel, page] of pages) {
    if (!claimsOf(page).length && rel !== "claims.md") {
      if (!o.dryRun && fs.existsSync(path.join(sdir, rel))) { fs.rmSync(path.join(sdir, rel)); writes.push(`-${rel}`); }
      continue;
    }
    w(rel, pageText(page));
  }
  if (rejected.length || fs.existsSync(path.join(sdir, "survey", "rejected.md"))) {
    w("survey/rejected.md", "# rejected.md -- facts the validator removed from the claims pages (find_ways never sees these). Fix and re-add, or drop.\n" +
      "# REJECTED|<page>|<id>|<reason>|<original CLAIM line>\n" +
      rejected.map((r) => `REJECTED|${r.page}|${r.claim.id}|${r.reason.replace(/\|/g, "/")}|${renderClaim(r.claim)}`).join("\n") + (rejected.length ? "\n" : ""));
  }
  const estTokens = estimateTokens(work);
  w("survey/worklist.md", renderWorklist(work, units, coverage, lazy, estTokens, multi));
  w("index/units.idx", renderUnitsIdx(units, resolved, coverage, identity, lazy, multi));
  const newMeta = { ...meta, repo_identity: identity, survey: { ...(meta.survey || {}), surveyed_at: (o.now || new Date()).toISOString(), units: units.length,
    files: listing.files.length, listing: listing.source } };
  if (!o.validateOnly || !meta.repo_identity) w("meta.json", JSON.stringify(newMeta, null, 2) + "\n");

  // ---------------- history ----------------
  let history = prevState.history || null;
  if (!o.validateOnly && o.history && listing.gitRoot && !o.dryRun) {
    const t4 = performance.now();
    const fileUnit = (f) => nearestFromPath(f, unitPaths);
    const res = mineHistory(root, sdir, {
      since: o.since || HISTORY_DEFAULTS.since,   // null: two years before HEAD (history.mjs) maxCommits: o.maxCommits || HISTORY_DEFAULTS.maxCommits,
      maxEntries: o.historyMax || HISTORY_DEFAULTS.maxEntries, fromHead: prevState.history?.head,
      unitOf: fileUnit, excluded: (f) => Boolean(resolved.zoneOf(posixDir(f))),
    });
    history = { head: res.head, mined_at: new Date().toISOString(), added: res.added, scanned: res.scanned, redacted: res.redacted, truncated: res.truncated };
    if (res.added) writes.push(`library.md (+${res.added})`);
    timing.history_ms = Math.round(performance.now() - t4);
  }
  w("index/root.idx", renderRootIdx(path.join(sdir, "index", "root.idx"), units, multi, fs.existsSync(path.join(sdir, "library.md"))));
  const state = { schema: "stealth-survey-state/1", generated_at: (o.now || new Date()).toISOString(),
    head: listing.gitRoot ? (git(root, ["rev-parse", "HEAD"]) || "").trim() || null : null, fingerprints, history,
    units: Object.fromEntries(units.map((u) => [u.path, { slug: u.slug, template: u.template, coverage: coverage.get(u.path) }])) };
  w("index/survey.state.json", JSON.stringify(state) + "\n");
  timing.total_ms = Math.round(performance.now() - t0);
  mem("written");

  return {
    root, identity, listing: { source: listing.source, files: listing.files.length, sparse: listing.sparse.size, submodules: listing.submodules.length },
    units: units.map((u) => ({ path: u.path, slug: u.slug, kind: u.kind, ecosystems: u.ecosystems, name: u.name, parent: u.parent,
      template: u.template, inherits: u.inherits || null, coverage: coverage.get(u.path), declaredBy: (u.declaredBy || []).map((d) => d.by) })),
    workspaces: resolved.workspaces, zones: resolved.zones, aux: resolved.aux.length, lazy,
    merge: mergeStats, validation: vcounts, rejected: rejected.map((r) => ({ page: r.page, id: r.claim.id, reason: r.reason })),
    ran, changed: [...changed], touchedFacts, incremental,
    worklist: work.map((x) => ({ unit: x.unit.path, need: x.need, stale: x.stale.length, reads: x.reads.files.length, bytes: x.reads.bytes, reason: x.reason })),
    estTokens, history, writes, timing, memory_mb: Math.round(process.resourceUsage().maxRSS / 1024), // peak resident set, MB
  };
}

function rankWork(u, touched) {
  if (u.path === ".") return 0;
  if (touched.has(u.path)) return 1;
  if (!u.inherits) return 2;
  return 3;
}

function normRel(root, p) {
  const abs = path.resolve(root, p);
  return path.relative(root, abs).split(path.sep).join("/") || ".";
}
function nearestFromPath(p, unitPaths) {
  let d = p;
  for (;;) { if (unitPaths.has(d)) return d; if (d === "." || !d) return "."; d = posixDir(d); }
}

function readJson(f) { try { return JSON.parse(fs.readFileSync(f, "utf8")); } catch { return null; } }

function listPages(sdir) {
  const out = [];
  if (fs.existsSync(path.join(sdir, "claims.md"))) out.push("claims.md");
  try { for (const f of fs.readdirSync(path.join(sdir, "claims"))) if (f.endsWith(".md")) out.push(`claims/${f}`); } catch { /* none */ }
  return out;
}

function readUnitsIdx(file) {
  const map = new Map();
  let t = "";
  try { t = fs.readFileSync(file, "utf8"); } catch { return map; }
  for (const line of t.split(/\r?\n/)) {
    if (!line.startsWith("UNIT|")) continue;
    const p = line.split("|");
    if (p[1] && p[2]) map.set(p[2], p[1]);
  }
  return map;
}

/** Fingerprint of every file a fact cites, plus manifests: the index blob when clean, else a content hash. */
function fingerprintsFor(root, listing, pages, prevState) {
  const cited = new Set();
  for (const page of pages.values()) for (const c of claimsOf(page)) {
    const m = (kvGet(c, "source") || "").match(/^(.+?):\d+/);
    if (m && !m[1].startsWith("search")) cited.add(m[1]);
  }
  for (const p of Object.keys(prevState.fingerprints || {})) cited.add(p);
  // Content hash of each cited file (CRLF-normalised), so worktree edits count; a sparse (absent) file is
  // its own state. Only cited files and manifests are hashed -- never the whole repository.
  const fp = {};
  for (const p of cited) {
    if (listing.sparse.has(p)) { fp[p] = "sparse"; continue; }
    const t = readText(root, p, 8 * 1024 * 1024);
    fp[p] = t == null ? "missing" : sha1(t.replace(/\r\n/g, "\n")).slice(0, 16);
  }
  return fp;
}

// ---------------------------------------------------------------------------------------------
// What the agent should read for a unit: highest value first, within budget.
// ---------------------------------------------------------------------------------------------
const READ_PRIORITY = [
  /^README(\.[\w-]+)?$/i, /^(package\.json|pyproject\.toml|setup\.py|setup\.cfg|Cargo\.toml|go\.mod|pom\.xml|build\.gradle(\.kts)?|settings\.gradle(\.kts)?|[^/]+\.(cs|fs|vb)proj|[^/]+\.sln|mix\.exs|pubspec\.yaml|Package\.swift|composer\.json|deno\.jsonc?|Gemfile|[^/]+\.gemspec|CMakeLists\.txt|meson\.build|MODULE\.bazel|WORKSPACE|BUILD(\.bazel)?|project\.json)$/,
  /^(AGENTS|CLAUDE)\.md$|^CONTRIBUTING(\.\w+)?$|^\.github\/copilot-instructions\.md$/i,
  /^(Makefile|justfile|Dockerfile|(docker-)?compose\.ya?ml|tox\.ini|pytest\.ini|noxfile\.py|tsconfig\.json|vite\.config\.\w+|vitest\.config\.\w+|jest\.config\.\w+|turbo\.json|nx\.json|pnpm-workspace\.yaml|\.env\.example|mkdocs\.ya?ml)$/,
  /^\.github\/workflows\/[^/]+\.ya?ml$|^\.gitlab-ci\.yml$|^\.circleci\/config\.ya?ml$|^azure-pipelines\.ya?ml$|^Jenkinsfile$/,
  /^(src\/)?(index|main|app|server|cli|lib|mod)\.(ts|tsx|js|mjs|py|go|rs)$|^src\/(lib|main)\.rs$|^cmd\/[^/]+\/main\.go$|^manage\.py$|^[a-z_]+\/__init__\.py$|^src\/[a-z_]+\/__init__\.py$/,
  /^(CHANGELOG|HISTORY)(\.\w+)?$|^docs\/(index|README|getting-started|architecture|development)\.md$/i,
];
const GENERATED_HEADER = /(DO NOT EDIT|@generated|auto-generated|autogenerated|generated by)/i;

function readList(u, files, listing, resolved, o, root) {
  const isRoot = u.path === ".";
  const maxFiles = isRoot ? o.rootFiles : o.unitFiles;
  const maxBytes = isRoot ? o.rootBytes : o.unitBytes;
  const prefix = isRoot ? "" : u.path + "/";
  const ranked = [];
  for (const f of files) {
    if (resolved.zoneOf(posixDir(f)) || GENERATED_FILE_RE.test(f) || listing.sparse.has(f)) continue;
    const rel = f.slice(prefix.length);
    const pr = READ_PRIORITY.findIndex((re) => re.test(rel));
    if (pr < 0) continue;
    if (!isRoot && pr === 4) continue; // CI lives at the root
    ranked.push({ f, pr, depth: rel.split("/").length });
  }
  ranked.sort((a, b) => a.pr - b.pr || a.depth - b.depth || cmpStr(a.f, b.f));
  const picked = [];
  const dropped = [];
  let bytes = 0;
  for (const r of ranked) {
    if (r.pr === 5) {
      const head = readText(root, r.f, 512) || "";
      if (GENERATED_HEADER.test(head)) continue;
    }
    const size = Math.min(Math.max(fileSize(root, r.f), 0), o.perFileBytes);
    if (picked.length >= maxFiles || bytes + size > maxBytes) { dropped.push(r.f); continue; }
    picked.push(fileSize(root, r.f) > o.perFileBytes ? `${r.f}(head)` : r.f);
    bytes += size;
  }
  return { files: picked, bytes, dropped };
}

function estimateTokens(work) {
  // Input: what the agent reads (≈4 bytes/token) + a fixed per-unit overhead for the prompt and the facts it writes.
  return work.reduce((s, x) => s + Math.round(x.reads.bytes / 4) + 1500, 0);
}

function renderWorklist(work, units, coverage, lazy, estTokens, multi) {
  const lines = [
    "# worklist.md -- what the agent still has to survey (generated by stealthlab-mcp survey; do not hand-edit).",
    "# For each WORK line: read ONLY the files in read=, then write facts for the missing= topics on page= (or an",
    "# `absent` fact with of=<topic>), then re-check each STALE fact. Then run `npx -y stealthlab-mcp survey --validate`.",
    `# units=${units.length} lazy=${lazy ? "yes (only the root and units a task touches are surveyed now)" : "no"} est_tokens=${estTokens}`,
    "",
  ];
  for (const x of work) {
    const u = x.unit;
    lines.push(`WORK|${u.path}|page=${pageFor(u, multi)}|missing=${x.need.join(",") || "-"}|reason=${x.reason}|${u.inherits ? `inherits=${u.inherits}|` : ""}read=${x.reads.files.join(",") || "-"}${x.reads.dropped.length ? `|over_budget=${x.reads.dropped.slice(0, 8).join(",")}${x.reads.dropped.length > 8 ? ",…" : ""}` : ""}`);
    for (const c of x.stale) lines.push(`STALE|${u.path}|${c.id}|${kvGet(c, "source")}|${c.statement}`);
  }
  if (!work.length) lines.push("(nothing to do: every unit that needs surveying now is covered)");
  const deferred = units.filter((u) => /scanner|deferred/.test(coverage.get(u.path)) && !work.some((x) => x.unit === u));
  if (deferred.length) lines.push("", `# ${deferred.length} unit(s) keep scanner-only facts until a task touches them (coverage=scanner): survey one with \`npx -y stealthlab-mcp survey --touch <path>\`.`);
  return lines.join("\n") + "\n";
}

function renderUnitsIdx(units, resolved, coverage, identity, lazy, multi) {
  const bySlug = new Map(units.map((u) => [u.path, u.slug]));
  const lines = [
    "# units.idx -- one row per unit (package) of this repository; generated by stealthlab-mcp survey. Grep a path to find its claims page.",
    "# UNIT|<slug>|<path>|kind=|eco=|name=|parent=|template=|inherits=|page=|coverage=|declared=",
    `REPO|repo_id=${identity.repo_id}|strength=${identity.strength}|units=${units.length}|lazy=${lazy ? "yes" : "no"}`,
  ];
  const c = (s) => String(s ?? "-").replace(/[|\r\n]/g, "/") || "-";
  for (const u of units) {
    const d = u.declaredBy?.[0];
    lines.push(["UNIT", u.slug, u.path, `kind=${u.kind}${u.tags?.length ? "+" + u.tags.join("+") : ""}`, `eco=${u.ecosystems.join(",") || "-"}`, `name=${c(u.name)}`,
      `parent=${u.parent ? bySlug.get(u.parent) : "-"}`, `template=${u.template || "-"}`, `inherits=${u.inherits || "-"}`, `page=${pageFor(u, multi)}`,
      `coverage=${coverage.get(u.path)}`, `declared=${d ? `${d.by}:${d.file}` : "-"}`].join("|"));
  }
  for (const z of resolved.zones) lines.push(`ZONE|${z.path}|kind=${z.kind}`);
  for (const a of resolved.aux.slice(0, 100)) lines.push(`AUX|${a.path}|reason=${a.reason}`);
  if (resolved.aux.length > 100) lines.push(`# … ${resolved.aux.length - 100} more AUX directories`);
  return lines.join("\n") + "\n";
}

function renderRootIdx(file, units, multi, hasLibrary) {
  let existing = [];
  try { existing = fs.readFileSync(file, "utf8").split(/\r?\n/).filter(Boolean); } catch { /* new */ }
  const ours = (name) => /^(claims|claims:.+|units|worklist)$/.test(name);
  const keep = existing.filter((l) => l.startsWith("#") ? false : !ours(l.split("|")[0]));
  const rows = [
    "claims|claims.md|repository-wide facts: read first",
    "units|index/units.idx|one row per unit: grep a path to find its claims page",
    "worklist|survey/worklist.md|units and facts the agent still has to survey",
  ];
  if (hasLibrary && !keep.some((l) => l.startsWith("library|"))) rows.push("library|library.md|this repository's solved problems (past fixes with diffs)");
  const unitRows = multi && units.length <= 31 ? units.filter((u) => u.path !== ".").map((u) => `claims:${u.slug}|claims/${u.slug}.md|${u.path}`) : [];
  const head = "# root.idx -- name|target|hint. The router: read this first.";
  let out = [head, ...rows, ...keep, ...unitRows];
  while (Buffer.byteLength(out.join("\n") + "\n") > 4096 && unitRows.length) { unitRows.pop(); out = [head, ...rows, ...keep, ...unitRows]; }
  return out.join("\n") + "\n";
}
