// The validator: no fact is trusted because someone wrote it. Every CLAIM is checked against the file it
// cites, deterministically:
//   * `source=path:line` must exist (posix path; case fixed on case-insensitive file systems; a symlink is
//     resolved once to its target). Under a sparse checkout a tracked-but-absent file is `unverifiable`.
//   * `#sha=` is the first 7 hex of sha1(the cited line, CRLF->LF, trailing whitespace stripped). A first
//     validation stamps it. Later, a mismatch is re-anchored if the same line moved (unique match), else
//     the fact goes `stale`. A legacy whole-file `git hash-object` sha is accepted once and re-stamped.
//   * For agent-written facts, the statement's checkable anchors (backticked spans, quoted text, version
//     numbers) must appear at the cited line (±3), elsewhere in the cited file (the line is re-anchored),
//     or be an existing path, or a command the manifests/CI confirm. Otherwise the fact is rejected.
//   * Commands in build/test/lint/ci facts are cross-checked against manifests, Makefiles and CI; one that
//     contradicts them (`npm run build` with no build script) is rejected.
// Rejected facts leave the page (find_ways never sees them) and go to .stealth/survey/rejected.md.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { claimsOf, kvGet, kvSet, TOPICS, validStatus } from "./claims.mjs";
import { makeTargets, parseJsonLoose, parseToml, tomlGet } from "./parse.mjs";
import { readText } from "./files.mjs";

export const normLine = (s) => s.replace(/\r$/, "").replace(/[ \t]+$/, "");
export const lineHash = (s) => crypto.createHash("sha1").update(normLine(s)).digest("hex").slice(0, 7);
const gitBlobSha = (buf) => crypto.createHash("sha1").update(`blob ${buf.length}\0`).update(buf).digest("hex");

const SECRET_RES = [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, /\bAKIA[0-9A-Z]{16}\b/, /\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b/,
  /\bgithub_pat_[A-Za-z0-9_]{60,}\b/, /\bsk-(proj-|ant-)?[A-Za-z0-9_-]{20,}/, /\bxox[baprs]-[A-Za-z0-9-]{10,}/, /\bAIza[0-9A-Za-z_-]{35}\b/,
  /\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp):\/\/[^\s:@/]+:[^\s@/]+@/i, /\bnpg_[A-Za-z0-9]{12,}/];
export const hasSecret = (s) => SECRET_RES.some((re) => re.test(s));

/** Build the shared validation context once per run. */
export function makeContext(root, listing, units) {
  const fileSet = listing.fileSet || new Set(listing.files);
  // Built only on first use: a case-insensitive lookup is needed only for a fact citing a wrongly-cased path,
  // and the directory set only for a fact that names a directory.
  let lower = null;
  let dirs = null;
  const trace = (what) => {
    if (process.env.STEALTH_SURVEY_DEBUG) process.stderr.write(`lazy ${what} built by: ${(new Error().stack || "").split(/\r?\n/)[3] || "?"}\n`);
  };
  const lazy = {
    get lower() {
      if (!lower) { trace("lower"); lower = new Map(); for (const f of listing.files) { const k = f.toLowerCase(); if (!lower.has(k)) lower.set(k, f); } }
      return lower;
    },
    get dirSet() {
      if (!dirs) {
        trace("dirSet");
        dirs = new Set();
        for (const f of listing.files) { let i = f.lastIndexOf("/"); while (i > 0) { const d = f.slice(0, i); if (dirs.has(d)) break; dirs.add(d); i = d.lastIndexOf("/"); } }
      }
      return dirs;
    },
  };
  const cache = new Map();
  const read = (p) => { if (!cache.has(p)) cache.set(p, readText(root, p, 2 * 1024 * 1024)); return cache.get(p); };
  return { root, listing, fileSet, get lower() { return lazy.lower; }, get dirSet() { return lazy.dirSet; }, units, read, unitByPath: new Map(units.map((u) => [u.path, u])) };
}

function resolvePath(ctx, p) {
  p = p.replace(/\\/g, "/").replace(/^\.\//, "").replace(/^\/+/, "");
  if (ctx.fileSet.has(p)) return { path: p };
  const ci = ctx.lower.get(p.toLowerCase());
  if (ci) return { path: ci, caseFixed: true };
  return null;
}

function resolveSymlink(ctx, p) {
  try {
    const abs = path.join(ctx.root, p);
    if (!fs.lstatSync(abs).isSymbolicLink()) return p;
    const real = fs.realpathSync(abs);
    const rel = path.relative(fs.realpathSync(ctx.root), real).split(path.sep).join("/");
    if (!rel.startsWith("..") && !path.isAbsolute(rel) && ctx.fileSet.has(rel)) return rel;
  } catch { /* not a symlink or broken */ }
  return p;
}

const SOURCE_RE = /^(.+?):(\d+)(?:-(\d+))?(?:#sha=([0-9a-fA-F]{7,40}))?$/;

/** Checkable anchors in a statement. */
export function anchorsOf(statement) {
  const out = [];
  for (const m of statement.matchAll(/`([^`]+)`/g)) if (m[1].trim().length >= 2) out.push({ text: m[1].trim(), kind: "code" });
  for (const m of statement.replace(/`[^`]*`/g, " ").matchAll(/"([^"]{3,80})"/g)) out.push({ text: m[1].trim(), kind: "quote" });
  const bare = statement.replace(/`[^`]*`/g, " ");
  for (const m of bare.matchAll(/(?<![\w.])v?(\d+\.\d+(?:\.\d+)*)(?![\w.])/g)) out.push({ text: m[1], kind: "version" });
  // A bare major version right after a name ("Postgres 16", "Node 20", "Java 21") is checkable too.
  for (const m of bare.matchAll(/\b[A-Z][\w.+#-]*\s+v?(\d{1,3})(?![\w.%])/g)) out.push({ text: m[1], kind: "version" });
  return out;
}

const STOP = new Set(["this", "that", "with", "from", "into", "uses", "used", "using", "have", "has", "been", "which", "when", "where",
  "there", "their", "they", "them", "repository", "repo", "file", "files", "code", "only", "also", "must", "should", "will", "does",
  "each", "every", "other", "than", "then", "about", "after", "before", "here", "readme", "project", "package"]);
const squash = (s) => s.toLowerCase().replace(/¦/g, "|").replace(/\s+/g, " ").trim();

/**
 * Validate every claim on a page in place. Returns {counts, rejected:[{line, reason}], ran:[...]}.
 * @param {{lines:any[]}} page
 * @param {object} ctx from makeContext
 * @param {{verifyCommands?: boolean, only?: Set<string>}} opts only: re-check just these cited paths (incremental)
 */
export function validatePage(page, ctx, opts = {}) {
  const counts = { ok: 0, stamped: 0, relocated: 0, stale: 0, unverifiable: 0, rejected: 0, case_fixed: 0, repaired: 0 };
  const rejected = [];
  const ran = [];
  const keep = [];
  for (const l of page.lines) {
    if (!l.claim) { keep.push(l); continue; }
    const c = l.claim;
    const r = validateClaim(c, ctx, opts);
    if (c.repaired) { counts.repaired++; c.repaired = false; }
    if (r.ran) ran.push({ id: c.id, ...r.ran });
    if (r.action === "reject") {
      counts.rejected++;
      rejected.push({ claim: c, reason: r.reason });
      continue;
    }
    counts[r.action] = (counts[r.action] || 0) + 1;
    if (r.caseFixed) counts.case_fixed++;
    keep.push(l);
  }
  page.lines = keep;
  return { counts, rejected, ran };
}

export function validateClaim(c, ctx, opts = {}) {
  const scanner = kvGet(c, "by") === "scanner";
  if (hasSecret(c.statement)) return { action: "reject", reason: "the statement contains what looks like a secret value" };
  if (!TOPICS.includes(c.topic)) return { action: "reject", reason: `unknown topic ${c.topic} (one of: ${TOPICS.join(", ")})` };
  if (!validStatus(c.status)) c.status = "current";
  const source = kvGet(c, "source") || "";
  if (source.startsWith("search:")) {
    if (!scanner && c.topic !== "absent" && !/^(layout|stack)$/.test(c.topic)) {
      return { action: "reject", reason: "only absence facts may cite search:; cite the file and line that shows it" };
    }
    return { action: "ok" };
  }
  const m = source.match(SOURCE_RE);
  if (!m) return { action: "reject", reason: `source must be path:line (got ${source || "nothing"})` };
  let [, rawPath, lineStr, , sha] = m;
  // Incremental: a stamped fact whose file did not change needs no re-check. New facts are always checked.
  if (opts.only && sha && c.status === "current" && !opts.only.has(rawPath)) return { action: "ok" };
  const res = resolvePath(ctx, rawPath);
  if (!res) {
    if (ctx.listing.source === "walk") return { action: "reject", reason: `${rawPath} does not exist` };
    return { action: "reject", reason: `${rawPath} is not a file in this repository` };
  }
  let p = resolveSymlink(ctx, res.path);
  if (ctx.listing.sparse.has(p) || !fs.existsSync(path.join(ctx.root, p))) {
    c.status = "unverifiable";
    kvSet(c, "source", `${p}:${lineStr}${sha ? `#sha=${sha.slice(0, 7)}` : ""}`);
    return { action: "unverifiable" };
  }
  const textRaw = ctx.read(p);
  if (textRaw == null) return { action: "reject", reason: `${p} is binary or unreadable` };
  const lines = textRaw.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  let line = Number(lineStr);
  const caseFixed = Boolean(res.caseFixed) || p !== rawPath;

  // Already-stamped fact: does the line still say the same thing?
  if (sha) {
    sha = sha.toLowerCase();
    if (line >= 1 && line <= lines.length && lineHash(lines[line - 1]) === sha.slice(0, 7)) {
      if (caseFixed) kvSet(c, "source", `${p}:${line}#sha=${sha.slice(0, 7)}`);
      if (c.status === "stale" || c.status === "unverifiable") c.status = "current";
      return { action: "ok", caseFixed };
    }
    if (sha.length === 7 || sha.length === 40) {
      // Legacy: the whole-file `git hash-object` sha the old survey prompt asked for.
      const blob = ctx.listing.blob.get(p);
      const fileSha = blob || gitBlobSha(Buffer.from(textRaw.replace(/\r\n/g, "\n")));
      if (fileSha.startsWith(sha) && line >= 1 && line <= lines.length) {
        kvSet(c, "source", `${p}:${line}#sha=${lineHash(lines[line - 1])}`);
        return { action: "stamped", caseFixed };
      }
    }
    const hits = [];
    for (let i = 0; i < lines.length && hits.length < 2; i++) if (lineHash(lines[i]) === sha.slice(0, 7)) hits.push(i + 1);
    if (hits.length === 1) {
      kvSet(c, "source", `${p}:${hits[0]}#sha=${sha.slice(0, 7)}`);
      if (c.status !== "current") c.status = "current";
      return { action: "relocated", caseFixed };
    }
    c.status = "stale";
    if (line < 1 || line > lines.length) line = Math.min(Math.max(line, 1), lines.length || 1);
    return { action: "stale", caseFixed };
  }

  // First validation: the line must exist and, for agent facts, support the statement.
  if (line < 1 || line > lines.length) return { action: "reject", reason: `${p} has ${lines.length} lines; line ${line} does not exist` };
  let ranResult = null;
  if (!scanner) {
    const window = squash(lines.slice(Math.max(0, line - 4), line + 3).join("\n"));
    const whole = squash(lines.join("\n"));
    let relocateTo = 0;
    for (const a of anchorsOf(c.statement)) {
      const t = squash(a.text);
      if (window.includes(t)) continue;
      if (a.kind === "code" && (ctx.fileSet.has(a.text.replace(/^\.\//, "").replace(/\/$/, "")) || ctx.dirSet.has(a.text.replace(/^\.\//, "").replace(/\/$/, "")))) continue;
      if (a.kind === "code" && looksLikeCommand(a.text)) {
        const chk = checkCommandIn(a.text, c, ctx);
        if (chk.status === "verified") continue;
        if (chk.status === "contradicted") return { action: "reject", reason: `command ${a.text}: ${chk.why}` };
      }
      if (whole.includes(t)) {
        if (!relocateTo) {
          const i = lines.findIndex((x) => squash(x).includes(t));
          if (i >= 0) relocateTo = i + 1;
        }
        continue;
      }
      return { action: "reject", reason: `"${a.text}" is not shown at ${p}:${line} (or anywhere in that file)` };
    }
    if (relocateTo && relocateTo !== line) line = relocateTo;
    // A citation must point at something: never a blank line, and a statement with nothing checkable (no code,
    // quote or version) must still share a content word with the cited lines -- else it cites the wrong place.
    if (!lines[line - 1].trim()) return { action: "reject", reason: `${p}:${line} is a blank line; cite the line that shows the fact` };
    if (!anchorsOf(c.statement).length) {
      const words = (s) => new Set((s.toLowerCase().match(/[a-z][a-z0-9.+#-]{3,}/g) || []).filter((w) => !STOP.has(w)));
      const said = words(c.statement);
      const shown = words(lines.slice(Math.max(0, line - 4), line + 3).join(" "));
      if (said.size && ![...said].some((w) => shown.has(w))) return { action: "reject", reason: `nothing in the statement appears at ${p}:${line}` };
    }
  }
  if (!scanner && /^(build|test|lint|ci)$/.test(c.topic)) {
    for (const a of anchorsOf(c.statement)) {
      if (a.kind !== "code" || !looksLikeCommand(a.text)) continue;
      const chk = checkCommandIn(a.text, c, ctx);
      if (chk.status === "contradicted") return { action: "reject", reason: `command ${a.text}: ${chk.why}` };
      if (opts.verifyCommands && chk.status === "verified" && !ranResult) ranResult = dryRun(a.text, unitOf(c), ctx);
    }
  }
  if (ranResult) kvSet(c, "ran", ranResult.result);
  kvSet(c, "source", `${p}:${line}#sha=${lineHash(lines[line - 1])}`);
  if (c.status === "stale" || c.status === "unverifiable") c.status = "current";
  return { action: "stamped", caseFixed, ran: ranResult };
}

const unitOf = (c) => (c.scope?.startsWith("unit:") ? c.scope.slice(5) : ".");

/** A command is checked where the fact says it runs: its unit, or a unit directory the statement names
 *  ("CI runs `npm test` in packaging/npm"). Contradicted only if it fails everywhere it could run. */
function checkCommandIn(cmd, c, ctx) {
  const first = checkCommand(cmd, unitOf(c), ctx);
  if (first.status !== "contradicted") return first;
  for (const u of ctx.units) {
    if (u.path === "." || u.path === unitOf(c)) continue;
    if (c.statement.includes(u.path)) {
      const r = checkCommand(cmd, u.path, ctx);
      if (r.status !== "contradicted") return r;
    }
  }
  return first;
}

// ---------------------------------------------------------------------------------------------
// Command cross-check
// ---------------------------------------------------------------------------------------------
const CMD_HEAD = /^(?:[A-Z_][A-Z0-9_]*=\S+\s+)*(npm|pnpm|yarn|bun|bunx|npx|make|just|pytest|py\.test|python3?|uv|poetry|pdm|hatch|tox|nox|cargo|go|mvn|\.\/mvnw|gradle|\.\/gradlew|gradlew|dotnet|bazel|bazelisk|buck2?|pants|mix|flutter|dart|swift|bundle|rake|rspec|composer|deno|docker|docker-compose|cmake|ctest|meson|ninja|tsc|ruff|black|mypy|flake8|eslint|prettier|vitest|jest|playwright|turbo|nx|lerna|rush)(\s|$)/;
export const looksLikeCommand = (s) => CMD_HEAD.test(s.trim());

const PNPM_BUILTIN = new Set(["install", "i", "add", "remove", "rm", "uninstall", "update", "up", "upgrade", "exec", "dlx", "why", "list", "ls",
  "outdated", "publish", "pack", "link", "unlink", "store", "setup", "env", "init", "create", "audit", "rebuild", "prune", "fetch", "patch",
  "deploy", "import", "server", "root", "bin", "config", "c", "help", "licenses", "approve-builds", "self-update", "dedupe", "install-test", "it", "ci"]);
const YARN_BUILTIN = new Set(["install", "add", "remove", "up", "upgrade", "dlx", "exec", "why", "info", "init", "config", "set", "plugin", "node",
  "npm", "pack", "publish", "version", "workspaces", "constraints", "dedupe", "cache", "link", "unlink", "bin", "explain", "patch", "rebuild"]);

function readJson(ctx, p) { const t = ctx.fileSet.has(p) ? ctx.read(p) : null; return t ? parseJsonLoose(t) : null; }
const at = (dir, base) => (dir === "." ? base : `${dir}/${base}`);
const parentDir = (d) => { const i = d.lastIndexOf("/"); return i < 0 ? "." : d.slice(0, i); };
function upward(dir) { const out = []; let d = dir; for (;;) { out.push(d); if (d === ".") return out; d = parentDir(d); } }

function scriptsFor(ctx, dir) {
  // The unit's own package.json first, then each ancestor's (a command at the root runs the root scripts).
  const found = [];
  for (const d of upward(dir)) { const pj = readJson(ctx, at(d, "package.json")); if (pj) found.push({ dir: d, pj }); }
  return found;
}
function allPackages(ctx) {
  ctx._pkgs ??= ctx.listing.files.filter((f) => (f === "package.json" || f.endsWith("/package.json")) && !f.includes("node_modules/"))
    .map((f) => ({ file: f, pj: readJson(ctx, f) })).filter((x) => x.pj);
  return ctx._pkgs;
}
function hasDep(pj, name) {
  return [pj?.dependencies, pj?.devDependencies, pj?.peerDependencies, pj?.optionalDependencies].some((s) => s && name in s);
}
function documented(ctx, cmd) {
  ctx._docs ??= ctx.listing.files.filter((f) => /^\.github\/workflows\/|^\.gitlab-ci\.yml$|^\.circleci\/|(^|\/)(Makefile|justfile|README(\.\w+)?|CONTRIBUTING(\.\w+)?|tox\.ini|noxfile\.py|azure-pipelines\.ya?ml|Jenkinsfile|AGENTS\.md|CLAUDE\.md)$/i.test(f))
    .slice(0, 200).map((f) => ({ f, t: squash(ctx.read(f) || "") }));
  const needle = squash(cmd);
  return ctx._docs.find((d) => d.t.includes(needle))?.f || null;
}
const anyFile = (ctx, re) => ctx.listing.files.some((f) => re.test(f));
const nearest = (ctx, dir, bases) => { for (const d of upward(dir)) for (const b of bases) if (ctx.fileSet.has(at(d, b))) return at(d, b); return null; };

/**
 * @returns {{status: "verified"|"contradicted"|"unknown", why: string}}
 */
export function checkCommand(cmd, unitPath, ctx) {
  const parts = cmd.split(/\s*(?:&&|;)\s*/).filter(Boolean);
  let dir = unitPath;
  let best = { status: "unknown", why: "no rule for this command" };
  for (const part of parts) {
    const cd = part.match(/^cd\s+(\S+)$/);
    if (cd) { dir = normDir(dir, cd[1]); continue; }
    const r = checkOne(part.trim(), dir, ctx);
    if (r.status === "contradicted") return r;
    if (r.status === "verified") best = r;
  }
  if (best.status !== "verified") {
    const doc = documented(ctx, cmd);
    if (doc) return { status: "verified", why: `documented in ${doc}` };
  }
  return best;
}
function normDir(dir, rel) { const j = path.posix.normalize(dir === "." ? rel : `${dir}/${rel}`).replace(/\/$/, ""); return j === "" || j === "." ? "." : j; }

function checkOne(cmd, dir, ctx) {
  let toks = cmd.split(/\s+/);
  while (toks.length && /^[A-Z_][A-Z0-9_]*=/.test(toks[0])) toks.shift();
  const tool = toks[0];
  const V = (why) => ({ status: "verified", why });
  const X = (why) => ({ status: "contradicted", why });
  const U = (why) => ({ status: "unknown", why });
  const pmScript = (script, strict, scopeDir, recursive, filterName) => {
    if (filterName) {
      const pkg = allPackages(ctx).find((x) => x.pj.name === filterName || x.file === at(filterName.replace(/^\.\//, ""), "package.json"));
      if (!pkg) return X(`no workspace package named ${filterName}`);
      return pkg.pj.scripts?.[script] ? V(`script ${script} in ${pkg.file}`) : X(`${pkg.file} has no script ${script}`);
    }
    if (recursive) {
      const any = allPackages(ctx).find((x) => x.pj.scripts?.[script]);
      return any ? V(`script ${script} in ${any.file}`) : X(`no package has a script ${script}`);
    }
    const found = scriptsFor(ctx, scopeDir);
    const hit = found.find((x) => x.pj.scripts?.[script]);
    if (hit) return V(`script ${script} in ${at(hit.dir, "package.json")}`);
    if (!found.length) return X("no package.json here");
    return strict ? X(`no script ${script} in ${found.map((x) => at(x.dir, "package.json")).join(", ")}`) : U(`no script ${script}`);
  };
  const flags = (from) => {
    const o = { rest: [], recursive: false, filter: null, dir: null };
    for (let i = from; i < toks.length; i++) {
      const t = toks[i];
      if (t === "-r" || t === "--recursive" || t === "-ws" || t === "--workspaces") o.recursive = true;
      else if (t === "--filter" || t === "-F" || t === "-w" || t === "--workspace") { if (t === "-w" && toks[0] === "pnpm") continue; o.filter = toks[++i]; }
      else if (t.startsWith("--filter=") || t.startsWith("--workspace=")) o.filter = t.split("=")[1];
      else if (t === "-C" || t === "--dir" || t === "--prefix" || t === "--cwd") o.dir = toks[++i];
      else if (t === "--") break;
      else o.rest.push(t);
    }
    return o;
  };
  switch (tool) {
    case "npm": {
      const o = flags(1);
      const scope = o.dir ? normDir(dir, o.dir) : dir;
      const [sub, arg] = o.rest;
      if (["test", "t", "start", "stop", "restart"].includes(sub)) return pmScript(sub === "t" ? "test" : sub, true, scope, o.recursive, o.filter);
      if (sub === "run" || sub === "run-script" || sub === "rum" || sub === "urn") return arg ? pmScript(arg, true, scope, o.recursive, o.filter) : U("bare npm run");
      if (["ci", "install", "i"].includes(sub)) return nearest(ctx, scope, ["package.json"]) ? V("package.json present") : X("no package.json");
      if (sub === "exec") return U("npm exec");
      return U(`npm ${sub}`);
    }
    case "pnpm": case "yarn": {
      const o = flags(1);
      const scope = o.dir ? normDir(dir, o.dir) : dir;
      let [sub, arg, arg2] = o.rest;
      if (tool === "yarn" && sub === "workspace") return arg2 ? pmScript(arg2 === "run" ? o.rest[3] : arg2, true, scope, false, arg) : U("yarn workspace");
      if (tool === "yarn" && sub === "workspaces" && arg === "foreach") { const i = o.rest.indexOf("run"); return i > 0 ? pmScript(o.rest[i + 1], true, scope, true) : U("yarn workspaces foreach"); }
      if (!sub) return nearest(ctx, scope, ["package.json"]) ? V("install") : X("no package.json");
      if (sub === "run") return arg ? pmScript(arg, true, scope, o.recursive, o.filter) : U("bare run");
      if (["install", "i", "add"].includes(sub)) return nearest(ctx, scope, ["package.json"]) ? V("package.json present") : X("no package.json");
      if ((tool === "pnpm" ? PNPM_BUILTIN : YARN_BUILTIN).has(sub)) return U(`${tool} ${sub}`);
      if (sub === "test" || sub === "start") return pmScript(sub, true, scope, o.recursive, o.filter);
      return pmScript(sub, false, scope, o.recursive, o.filter);
    }
    case "bun": {
      const o = flags(1);
      const [sub, arg] = o.rest;
      if (sub === "test") return V("bun's built-in test runner");
      if (sub === "run") return arg ? pmScript(arg, false, dir, false, o.filter) : U("bare bun run");
      return U(`bun ${sub}`);
    }
    case "npx": case "bunx": {
      const bin = toks.slice(1).find((t) => !t.startsWith("-"));
      if (!bin) return U("npx");
      const pkgs = scriptsFor(ctx, dir);
      return pkgs.some((x) => hasDep(x.pj, bin) || hasDep(x.pj, `@${bin}/cli`)) ? V(`${bin} is a dependency`) : U(`${bin} not a declared dependency`);
    }
    case "make": case "just": {
      let mdir = dir;
      let target = null;
      for (let i = 1; i < toks.length; i++) {
        if (toks[i] === "-C" || toks[i] === "-f" || toks[i] === "--directory") { if (toks[i] !== "-f") mdir = normDir(dir, toks[i + 1]); i++; continue; }
        if (toks[i].startsWith("-") || toks[i].includes("=")) continue;
        target = toks[i];
        break;
      }
      const names = tool === "make" ? ["Makefile", "makefile", "GNUmakefile"] : ["justfile", "Justfile", ".justfile"];
      const file = names.map((b) => at(mdir, b)).find((p) => ctx.fileSet.has(p)) || (mdir === dir ? nearest(ctx, dir, names) : null);
      if (!file) return X(`no ${names[0]} in ${mdir === "." ? "the repository root" : mdir}`);
      if (!target) return V(`${file} default target`);
      const t = ctx.read(file) || "";
      const targets = tool === "make" ? makeTargets(t).map((x) => x.target) : [...t.matchAll(/^@?([A-Za-z0-9_-]+)(?:\s+[^:=\n]*)?:(?!=)/gm)].map((x) => x[1]);
      if (targets.includes(target)) return V(`${tool} target ${target} in ${file}`);
      if (/^\s*(-?include|import)\s/m.test(t) || /^%:/m.test(t)) return U(`${file} includes other files`);
      return X(`${file} has no target ${target}`);
    }
    case "pytest": case "py.test": return pythonTest(ctx, dir, V, U);
    case "python": case "python3": {
      if (toks[1] === "-m" && toks[2] === "pytest") return pythonTest(ctx, dir, V, U);
      if (toks[1] === "-m" && (toks[2] === "tox" || toks[2] === "nox")) return checkOne(toks.slice(2).join(" "), dir, ctx);
      if (toks[1] === "manage.py") return nearest(ctx, dir, ["manage.py"]) ? V("manage.py present") : X("no manage.py");
      if (toks[1] && !toks[1].startsWith("-")) { const f = normDir(dir, toks[1]); return ctx.fileSet.has(f) || ctx.fileSet.has(toks[1]) ? V(`${toks[1]} exists`) : X(`${toks[1]} does not exist`); }
      return U("python");
    }
    case "uv": case "poetry": case "pdm": case "hatch": {
      if (toks[1] === "run" && toks[2]) {
        if (tool === "hatch") return U("hatch run <env:script>");
        return checkOne(toks.slice(2).join(" "), dir, ctx);
      }
      return U(`${tool} ${toks[1] || ""}`);
    }
    case "tox": return nearest(ctx, dir, ["tox.ini"]) || pyprojectHas(ctx, dir, "tool.tox") ? V("tox configured") : X("no tox.ini or [tool.tox]");
    case "nox": return nearest(ctx, dir, ["noxfile.py"]) ? V("noxfile.py present") : X("no noxfile.py");
    case "cargo": {
      const man = nearest(ctx, dir, ["Cargo.toml"]);
      if (!man) return X("no Cargo.toml");
      const pi = toks.findIndex((t) => t === "-p" || t === "--package");
      if (pi > 0 && toks[pi + 1]) {
        const crate = toks[pi + 1];
        const crates = ctx.listing.files.filter((f) => f.endsWith("Cargo.toml")).map((f) => tomlGet(parseToml(ctx.read(f) || "").data, "package.name"));
        return crates.includes(crate) ? V(`crate ${crate} exists`) : X(`no crate named ${crate}`);
      }
      return V(`${man} present`);
    }
    case "go": return nearest(ctx, dir, ["go.mod", "go.work"]) ? V("go module present") : X("no go.mod");
    case "mvn": return nearest(ctx, dir, ["pom.xml"]) ? V("pom.xml present") : X("no pom.xml");
    case "./mvnw": return ctx.fileSet.has(at(dir, "mvnw")) || ctx.fileSet.has("mvnw") ? V("mvnw present") : X("no mvnw wrapper");
    case "gradle": return nearest(ctx, dir, ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"]) ? V("Gradle build present") : X("no Gradle build");
    case "./gradlew": case "gradlew": return ctx.fileSet.has(at(dir, "gradlew")) || ctx.fileSet.has("gradlew") ? V("gradlew present") : X("no gradlew wrapper");
    case "dotnet": return anyFile(ctx, /\.(sln|csproj|fsproj|vbproj)$/) ? V(".NET project present") : X("no .NET project");
    case "bazel": case "bazelisk": return ["WORKSPACE", "WORKSPACE.bazel", "MODULE.bazel"].some((f) => ctx.fileSet.has(f)) ? V("Bazel workspace") : X("no Bazel workspace");
    case "buck": case "buck2": return ctx.fileSet.has(".buckconfig") ? V("Buck project") : X("no .buckconfig");
    case "pants": return ctx.fileSet.has("pants.toml") ? V("Pants project") : X("no pants.toml");
    case "mix": return nearest(ctx, dir, ["mix.exs"]) ? V("mix.exs present") : X("no mix.exs");
    case "flutter": case "dart": return nearest(ctx, dir, ["pubspec.yaml"]) ? V("pubspec.yaml present") : X("no pubspec.yaml");
    case "swift": return nearest(ctx, dir, ["Package.swift"]) || anyFile(ctx, /\.xcodeproj\//) ? V("Swift package") : X("no Package.swift");
    case "bundle": case "rake": case "rspec": return nearest(ctx, dir, ["Gemfile", "Rakefile"]) ? V("Ruby project") : X("no Gemfile");
    case "composer": {
      const cj = (() => { const f = nearest(ctx, dir, ["composer.json"]); return f ? parseJsonLoose(ctx.read(f) || "") : null; })();
      if (!cj) return X("no composer.json");
      const sub = toks[1];
      if (sub === "run-script" || sub === "run") return cj.scripts?.[toks[2]] ? V("composer script") : X(`no composer script ${toks[2]}`);
      if (sub && cj.scripts?.[sub]) return V("composer script");
      return U(`composer ${sub}`);
    }
    case "deno": {
      if (toks[1] === "task") {
        const f = nearest(ctx, dir, ["deno.json", "deno.jsonc"]);
        const dj = f ? parseJsonLoose(ctx.read(f) || "") : null;
        if (!dj) return X("no deno.json");
        return dj.tasks?.[toks[2]] ? V(`deno task ${toks[2]}`) : X(`no deno task ${toks[2]}`);
      }
      return U("deno");
    }
    case "docker": if (toks[1] !== "compose") return U("docker");
    // falls through
    case "docker-compose": return anyFile(ctx, /(^|\/)(docker-)?compose(\.[\w-]+)?\.ya?ml$/) ? V("compose file present") : X("no compose file");
    case "cmake": case "ctest": return nearest(ctx, dir, ["CMakeLists.txt"]) ? V("CMake project") : X("no CMakeLists.txt");
    case "meson": case "ninja": return nearest(ctx, dir, ["meson.build", "build.ninja", "CMakeLists.txt"]) ? V("build files present") : U(tool);
    case "tsc": return nearest(ctx, dir, ["tsconfig.json"]) ? V("tsconfig present") : U("tsc without tsconfig");
    default: {
      const pkgs = scriptsFor(ctx, dir);
      const depName = { vitest: "vitest", jest: "jest", eslint: "eslint", prettier: "prettier", playwright: "@playwright/test", turbo: "turbo", nx: "nx", lerna: "lerna" }[tool];
      if (depName) return pkgs.some((x) => hasDep(x.pj, depName)) ? V(`${depName} is a dependency`) : U(`${depName} not declared`);
      if (["ruff", "black", "mypy", "flake8"].includes(tool)) return pyprojectHas(ctx, dir, `tool.${tool}`) || nearest(ctx, dir, [`.${tool}`, `${tool}.toml`, ".flake8", "setup.cfg", ".pre-commit-config.yaml"]) ? V(`${tool} configured`) : U(`${tool} not configured`);
      if (tool === "rush") return ctx.fileSet.has("rush.json") ? V("rush.json") : X("no rush.json");
      return U(tool);
    }
  }
}

function pyprojectHas(ctx, dir, key) {
  for (const d of upward(dir)) {
    const f = at(d, "pyproject.toml");
    if (ctx.fileSet.has(f) && tomlGet(parseToml(ctx.read(f) || "").data, key)) return true;
  }
  return false;
}
function pythonTest(ctx, dir, V, U) {
  if (pyprojectHas(ctx, dir, "tool.pytest")) return V("pytest configured in pyproject.toml");
  const f = nearest(ctx, dir, ["pytest.ini", "conftest.py", "tox.ini", "setup.cfg"]);
  if (f) return V(`pytest configuration (${f})`);
  if (ctx.listing.files.some((p) => /(^|\/)(test_[^/]+|[^/]+_test)\.py$/.test(p))) return V("pytest-style test files exist");
  return U("no pytest configuration found");
}

// ---------------------------------------------------------------------------------------------
// --verify-commands: opt-in dry runs of commands we already verified statically. Only fixed, safe forms
// (collect / list / plan, never "run the tests") and never the raw command string as a shell line.
// ---------------------------------------------------------------------------------------------
function dryRun(cmd, unitPath, ctx) {
  const cwd = path.join(ctx.root, unitPath === "." ? "" : unitPath);
  const run = (bin, args) => {
    const r = spawnSync(bin, args, { cwd, encoding: "utf8", timeout: 60_000, windowsHide: true, shell: process.platform === "win32" });
    if (r.error && r.error.code === "ENOENT") return { result: "skipped", detail: `${bin} not installed` };
    return { result: r.status === 0 ? "ok" : "fail", detail: `${bin} ${args.join(" ")} -> exit ${r.status}` };
  };
  const m = cmd.trim();
  let mm;
  if ((mm = m.match(/^(?:(uv|poetry|pdm) run\s+)?(?:python3? -m )?pytest((?:\s+[\w./:-]+)*)$/))) {
    const args = ["-m", "pytest", "--collect-only", "-q", ...mm[2].trim().split(/\s+/).filter(Boolean)];
    return mm[1] ? run(mm[1], ["run", "python", ...args]) : run("python", args);
  }
  if ((mm = m.match(/^make\s+([\w.-]+)$/))) return run("make", ["-n", mm[1]]);
  if (/^cargo\s/.test(m)) return run("cargo", ["metadata", "--no-deps", "--format-version", "1"]);
  if (/^go (test|build|vet)\b/.test(m)) return run("go", ["list", "./..."]);
  if ((mm = m.match(/^(npm|pnpm|yarn) (?:run )?([\w:.-]+)$/))) return { result: "static", detail: `script ${mm[2]} checked statically` };
  return { result: "skipped", detail: "no safe dry-run form" };
}
