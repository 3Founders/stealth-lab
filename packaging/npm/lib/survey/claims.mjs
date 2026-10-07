// Claims pages: `.stealth/claims.md` (repository-level facts) and `.stealth/claims/<slug>.md` (one page per
// unit in a multi-unit repository). Same CLAIM grammar the server parses (backend/app/stealth/local_sync.py
// _parse_claims_md): `CLAIM|<id>|<status>|<topic>|<scope>|<statement>|source=...|version=n[|k=v...]`.
// Extra `k=v` fields are ignored by the server's parser, so `by=scanner|key=...|inherits=...` are safe.
//
// Ownership: lines with `by=scanner` belong to `stealthlab-mcp survey` and are regenerated (same id, same
// line) on every run; every other line belongs to the agent and is only ever re-validated, never rewritten.
import fs from "node:fs";
import path from "node:path";

export const TOPICS = ["purpose", "stack", "runtime", "deps", "build", "test", "lint", "ci", "layout", "conventions",
  "features", "decisions", "env", "issues", "absent"];
const STATUS = new Set(["current", "stale", "unverifiable", "unverified"]);

export function pageFor(unit, multi) {
  return unit.path === "." || !multi ? "claims.md" : `claims/${unit.slug}.md`;
}
export function idPrefix(unit, multi) {
  return unit.path === "." || !multi ? "R-" : `R-${unit.slug}-`;
}
export function scopeFor(unitPath) {
  return unitPath === "." ? "repository" : `unit:${unitPath}`;
}

// `|` separates fields, so a literal pipe inside a value is written as `¦` (U+00A6): `^20 || >=22` stays readable
// as `^20 ¦¦ >=22` instead of silently becoming a different value (`^20 // >=22`). The validator reads `¦` as `|`.
const clean = (s) => String(s ?? "").replace(/[\r\n]+/g, " ").replace(/\|/g, "¦").replace(/\s+/g, " ").trim();

/** Parse one CLAIM line. Repairs a statement that itself contains `|` (everything up to the first k=v field). */
export function parseClaim(line) {
  if (!line.startsWith("CLAIM|")) return null;
  const parts = line.split("|");
  if (parts.length < 6) return null;
  let k = 6;
  while (k < parts.length && !/^[a-z_]+=/.test(parts[k])) k++;
  const statement = parts.slice(5, k).join("¦");
  const kv = [];
  for (const f of parts.slice(k)) {
    const eq = f.indexOf("=");
    if (eq > 0) kv.push([f.slice(0, eq), f.slice(eq + 1)]);
  }
  return { id: parts[1], status: parts[2], topic: parts[3], scope: parts[4], statement, kv, repaired: k > 6 };
}

export const kvGet = (c, k) => { const e = c.kv.find(([a]) => a === k); return e ? e[1] : undefined; };
export function kvSet(c, k, v) {
  const i = c.kv.findIndex(([a]) => a === k);
  if (v === undefined || v === null) { if (i >= 0) c.kv.splice(i, 1); return; }
  if (i >= 0) c.kv[i][1] = String(v); else c.kv.push([k, String(v)]);
}

export function renderClaim(c) {
  const order = ["source", "version"];
  const kv = [...c.kv].sort((a, b) => {
    const ia = order.indexOf(a[0]);
    const ib = order.indexOf(b[0]);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  return ["CLAIM", c.id, c.status, c.topic, c.scope, clean(c.statement), ...kv.map(([k, v]) => `${k}=${clean(v)}`)].join("|");
}

export function readPage(file) {
  let text = "";
  try { text = fs.readFileSync(file, "utf8"); } catch { return { file, exists: false, lines: [] }; }
  const lines = text.split(/\r?\n/);
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  return { file, exists: true, lines: lines.map((l) => { const c = parseClaim(l); return c ? { claim: c } : { text: l }; }) };
}

export function pageText(page) {
  return page.lines.map((l) => (l.claim ? renderClaim(l.claim) : l.text)).join("\n") + "\n";
}

export function claimsOf(page) {
  return page.lines.filter((l) => l.claim).map((l) => l.claim);
}

/** Next free id number for a prefix on a page. */
function nextId(page, prefix) {
  let max = 0;
  for (const c of claimsOf(page)) {
    if (!c.id.startsWith(prefix)) continue;
    const n = Number(c.id.slice(prefix.length));
    if (Number.isInteger(n) && n > max) max = n;
  }
  return max + 1;
}
const fmtId = (prefix, n) => `${prefix}${String(n).padStart(3, "0")}`;

/** Insert a claim at the end of its topic block, or where its topic belongs in TOPICS order. */
function insertClaim(page, claim) {
  const rank = (t) => { const i = TOPICS.indexOf(t); return i < 0 ? TOPICS.length : i; };
  let lastSame = -1;
  let firstAfter = -1;
  page.lines.forEach((l, i) => {
    if (!l.claim) return;
    if (l.claim.topic === claim.topic) lastSame = i;
    else if (firstAfter < 0 && rank(l.claim.topic) > rank(claim.topic)) firstAfter = i;
  });
  const at = lastSame >= 0 ? lastSame + 1 : firstAfter >= 0 ? firstAfter : page.lines.length;
  page.lines.splice(at, 0, { claim });
}

/**
 * Merge scanner facts into a page. Returns {added, updated, removed} (claim ids).
 * @param {{lines:any[]}} page
 * @param {{topic,key,statement,source,unit}[]} facts facts for this page
 * @param {{prefix:string, header:string, extra?:(fact)=>[string,string][]}} opts
 */
export function mergeScannerFacts(page, facts, opts) {
  if (!page.lines.some((l) => l.text?.startsWith("#"))) page.lines.unshift({ text: opts.header });
  const existing = new Map();
  for (const c of claimsOf(page)) if (kvGet(c, "by") === "scanner") existing.set(`${c.scope}#${kvGet(c, "key")}`, c);
  const result = { added: [], updated: [], removed: [], unchanged: 0 };
  const seen = new Set();
  let n = nextId(page, opts.prefix);
  // Topic order first, so a new page numbers its ids straight down the file (one contiguous range per topic).
  const rank = (t) => { const i = TOPICS.indexOf(t); return i < 0 ? TOPICS.length : i; };
  facts = [...facts].sort((a, b) => rank(a.topic) - rank(b.topic));
  for (const f of facts) {
    const scope = scopeFor(f.unit);
    const k = `${scope}#${f.key}`;
    if (seen.has(k)) continue;
    seen.add(k);
    const old = existing.get(k);
    const extra = opts.extra ? opts.extra(f) : [];
    if (old) {
      const oldSrc = (kvGet(old, "source") || "").replace(/#sha=.*$/, "");
      const changed = clean(old.statement) !== clean(f.statement) || oldSrc !== f.source || old.topic !== f.topic;
      if (changed) {
        if (clean(old.statement) !== clean(f.statement)) kvSet(old, "version", Number(kvGet(old, "version") || 1) + 1);
        old.statement = f.statement;
        old.topic = f.topic;
        old.status = "current";
        kvSet(old, "source", f.source);
        result.updated.push(old.id);
      } else {
        if (old.status !== "current" && old.status !== "unverifiable") { old.status = "current"; result.updated.push(old.id); } else result.unchanged++;
      }
      for (const [ek, ev] of extra) kvSet(old, ek, ev);
      continue;
    }
    const claim = { id: fmtId(opts.prefix, n++), status: "current", topic: f.topic, scope, statement: f.statement,
      kv: [["source", f.source], ["version", "1"], ["by", "scanner"], ["key", f.key], ...extra] };
    insertClaim(page, claim);
    result.added.push(claim.id);
  }
  for (const [k, c] of existing) {
    if (seen.has(k)) continue;
    const i = page.lines.findIndex((l) => l.claim === c);
    if (i >= 0) page.lines.splice(i, 1);
    result.removed.push(c.id);
  }
  return result;
}

export function writeAtomic(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  try { if (fs.readFileSync(file, "utf8") === text) return false; } catch { /* new file */ }
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, text);
  fs.renameSync(tmp, file);
  return true;
}

export function validStatus(s) { return STATUS.has(s); }
