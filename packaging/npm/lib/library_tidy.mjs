// `stealthlab-mcp library lint` and `library tidy`: keep library.md from rotting. Local, deterministic, read-only.
//
// A library that only ever grows turns into near-duplicates and dangling references: the same fix recorded twice
// from two sessions, a Goal whose parent was dropped, a Way pointing at a Goal that was merged away. Retrieval then
// judges the same thing twice, and the 64 KB cap archives by age rather than by redundancy. Agent-memory systems that
// stay useful (supermemoryai/memoryrepo's periodic "dream") do one thing on a schedule: merge duplicates, drop what
// is outdated, repair broken links, keep the entry file short. This is the deterministic half of that for our
// library. It FINDS the work, with the evidence and the exact command for each item; the agent (the user's own
// model, no server and no extra spend) makes the judgement calls and runs `library drop` / `library merge-goals` /
// `library goal`, then `library lint` must come back clean. Nothing here edits library.md.
//
// What it will not do: decide that two entries are the same. A similarity score orders the candidates; the diff and
// the check are the evidence, and "the most recent verified entry wins unless its diff shows the older one is right".
import fs from "node:fs";
import path from "node:path";
import * as L from "./library.mjs";

export const TIDY_MAX_PER_SECTION = 25;
export const NEAR_BUDGET = 0.9;         // lint warns from here; tidy suggests archiving from 75%
export const ARCHIVE_SUGGEST_AT = 0.75;

// the same stop words termsFor() uses, so "similar" means what retrieval means by it
const STOP = new Set(("the a an and or of to in on for with by from is are be this that it as at fix add use when " +
  "into not no its can should make makes made").split(" "));

// Light suffix stripping so "value" / "values" and "timeout" / "timeouts" are one term. Deliberately crude and
// deterministic (a stemmer library would be a dependency for a threshold that is only a candidate filter).
export function stem(word) {
  let w = word;
  if (w.length > 4 && w.endsWith("ies")) return `${w.slice(0, -3)}y`;
  if (w.length > 5 && w.endsWith("ing")) w = w.slice(0, -3);
  else if (w.length > 4 && w.endsWith("ed")) w = w.slice(0, -2);
  else if (w.length > 4 && w.endsWith("es") && /(?:s|x|z|ch|sh)es$/.test(w)) w = w.slice(0, -2);
  else if (w.length > 3 && w.endsWith("s") && !w.endsWith("ss")) w = w.slice(0, -1);
  return w;
}

export function titleTerms(title) {
  const words = (String(title || "").toLowerCase().match(/[a-z0-9_]+/g) || []).filter((w) => w.length > 2 && !STOP.has(w));
  return new Set(words.map(stem));
}

export function jaccard(a, b) {
  if (!a.size && !b.size) return 0;
  let both = 0;
  for (const x of a) if (b.has(x)) both++;
  return both / (a.size + b.size - both);
}

const touchedPaths = (e) => new Set((e.procs || []).flatMap((p) => (p.touches || []).map((t) => t.path)));
const newer = (a, b) => (a.verified_at || "") >= (b.verified_at || "") ? a : b;

// ------------------------------------------------------------------ lint

// Everything that makes library.md inconsistent with itself. `error` breaks retrieval or a merge; `warn` is
// something to look at; `info` is housekeeping. The result is `ok` when there is no error.
export function lintLibrary(root, { lib = L.loadLibrary(root) } = {}) {
  const issues = [];
  const add = (severity, code, id, message, fix = null) => issues.push({ severity, code, id, message, ...(fix ? { fix } : {}) });
  const goalIds = new Set(lib.goals.map((g) => g.id));
  const wayIds = new Set(lib.ways.map((w) => w.id));

  for (const problem of lib.problems) add(/^conflict/.test(problem) ? "warn" : "error", "parse", null, problem,
    "stealthlab-mcp library index   (canonicalises the file; a conflict keeps the later verified_at)");

  for (const e of lib.entries) {
    if (e.goal && !goalIds.has(e.goal)) {
      add("error", "dangling_goal", e.id, `${e.id} links goal=${e.goal}, which is not in library.md`, "stealthlab-mcp library link");
    }
    for (const p of e.procs || []) {
      if (p.way && !wayIds.has(p.way)) {
        add("error", "dangling_way", `${e.id}.p${p.index}`, `${e.id}.p${p.index} links way=${p.way}, which is not in library.md`,
          "stealthlab-mcp library link");
      }
      if (p.solution && !fs.existsSync(path.join(L.stealthDir(root), "library", p.solution))) {
        add("warn", "missing_diff", e.id, `${e.id} names ${p.solution}, which is not on disk (the entry is kept, but its diff cannot be shown)`);
      }
    }
  }
  for (const g of lib.goals) {
    if (g.parent && !goalIds.has(g.parent)) {
      add("error", "dangling_parent", g.id, `${g.id} has parent=${g.parent}, which is not in library.md`,
        `stealthlab-mcp library goal ${g.id} --parent -`);
    }
  }
  for (const w of lib.ways) {
    if (!w.goal || !goalIds.has(w.goal)) {
      add("error", "way_without_goal", w.id, `${w.id} achieves ${w.goal || "no goal"}, which is not in library.md`, "stealthlab-mcp library link");
    }
  }

  // a parent cycle can only come from two machines setting parents that are fine alone and wrong together
  const parent = new Map(lib.goals.map((g) => [g.id, g.parent]));
  const reported = new Set();
  for (const g of lib.goals) {
    const seen = new Set();
    for (let cur = g.id; cur; cur = parent.get(cur)) {
      if (seen.has(cur)) {
        const cycle = [...seen].filter((x) => x === cur || seen.has(x));
        const key = [...new Set(cycle)].sort().join(",");
        if (!reported.has(key)) {
          reported.add(key);
          add("error", "parent_cycle", cur, `goals ${[...new Set(cycle)].sort().join(", ")} are each other's ancestors`,
            `stealthlab-mcp library goal ${cur} --parent -`);
        }
        break;
      }
      seen.add(cur);
    }
  }

  const usedWays = new Set(lib.entries.flatMap((e) => (e.procs || []).map((p) => p.way)).filter(Boolean));
  for (const w of lib.ways) if (!usedWays.has(w.id)) add("info", "unused_way", w.id, `${w.id} is not used by any entry`);
  const usedGoals = new Set([...lib.entries.map((e) => e.goal), ...lib.ways.map((w) => w.goal),
    ...lib.goals.map((g) => g.parent)].filter(Boolean));
  for (const g of lib.goals) if (!usedGoals.has(g.id)) add("info", "unused_goal", g.id, `${g.id} "${g.title}" is not used by any entry or Way`);

  const bytes = Buffer.byteLength(L.renderLibrary(lib));
  const pct = bytes / L.LIBRARY_MAX_BYTES;
  if (pct >= NEAR_BUDGET) {
    add("warn", "near_budget", null, `library.md is ${bytes} of ${L.LIBRARY_MAX_BYTES} bytes (${Math.round(pct * 100)}%); ` +
      "the oldest entries are archived when it passes the cap -- drop duplicates and superseded entries first", "stealthlab-mcp library tidy");
  }
  const order = { error: 0, warn: 1, info: 2 };
  issues.sort((a, b) => order[a.severity] - order[b.severity] || (a.code < b.code ? -1 : a.code > b.code ? 1 : 0) || String(a.id) .localeCompare(String(b.id)));
  const counts = { error: 0, warn: 0, info: 0 };
  for (const i of issues) counts[i.severity]++;
  return { ok: counts.error === 0, counts, issues, bytes, max_bytes: L.LIBRARY_MAX_BYTES, entries: lib.entries.length,
    goals: lib.goals.length, ways: lib.ways.length };
}

// ------------------------------------------------------------------ tidy

// Two entries that look like the same fix recorded twice. Title overlap decides, with the files they touched as a
// second signal: the same words and the same files is almost surely one fix; the same words over different files is
// a coincidence of vocabulary ("fix parser" ten times), so the bar is higher.
export const DUP_TITLE = 0.7;               // title terms alone
export const DUP_TITLE_WITH_FILES = 0.4;    // title terms when the touched files overlap this much...
export const DUP_FILES = 0.5;               // ...(Jaccard of touched paths)

export function duplicateEntries(lib) {
  const pool = lib.entries.filter((e) => e.outcome !== "fail");
  const terms = new Map(pool.map((e) => [e.id, titleTerms(e.title)]));
  const files = new Map(pool.map((e) => [e.id, touchedPaths(e)]));
  const out = [];
  for (let i = 0; i < pool.length; i++) {
    for (let j = i + 1; j < pool.length; j++) {
      const a = pool[i];
      const b = pool[j];
      if ((a.unit || ".") !== (b.unit || ".")) continue;
      const t = jaccard(terms.get(a.id), terms.get(b.id));
      const f = files.get(a.id).size && files.get(b.id).size ? jaccard(files.get(a.id), files.get(b.id)) : null;
      if (!(t >= DUP_TITLE || (f !== null && f >= DUP_FILES && t >= DUP_TITLE_WITH_FILES))) continue;
      const keep = newer(a, b);
      const drop = keep === a ? b : a;
      out.push({
        keep: keep.id, drop: drop.id, title_similarity: Math.round(t * 100) / 100,
        file_overlap: f === null ? null : Math.round(f * 100) / 100,
        keep_title: keep.title, drop_title: drop.title, keep_verified_at: keep.verified_at, drop_verified_at: drop.verified_at,
        drop_is_stale: drop.status === "stale",
        command: `stealthlab-mcp library drop ${drop.id} --superseded-by ${keep.id}`,
        judge: "Compare the two diffs (library show <id>; library/solutions/). Keep the one that is right for the code as it " +
          "is now -- the more recently verified wins unless its diff shows the older one is correct.",
      });
    }
  }
  return out.sort((x, y) => y.title_similarity - x.title_similarity || (x.keep < y.keep ? -1 : 1));
}

// Two knowledge Goals that are the same problem under different titles, and Goals where one is plainly a narrower
// version of another (every word of the narrower title appears in the broader one's -- the longer title is the more
// specific problem, so the shorter is its parent).
export const GOAL_MERGE = 0.75;

export function goalCandidates(lib) {
  const terms = new Map(lib.goals.map((g) => [g.id, titleTerms(g.title)]));
  const merges = [];
  const parents = [];
  const byId = new Map(lib.goals.map((g) => [g.id, g]));
  const isAncestor = (anc, id) => { for (let c = byId.get(id)?.parent; c; c = byId.get(c)?.parent) if (c === anc) return true; return false; };
  for (let i = 0; i < lib.goals.length; i++) {
    for (let j = i + 1; j < lib.goals.length; j++) {
      const a = lib.goals[i];
      const b = lib.goals[j];
      const ta = terms.get(a.id);
      const tb = terms.get(b.id);
      const sim = jaccard(ta, tb);
      if (sim >= GOAL_MERGE) {
        const [keep, drop] = a.id < b.id ? [a, b] : [b, a];
        if (!isAncestor(drop.id, keep.id) && !isAncestor(keep.id, drop.id)) {
          merges.push({ keep: keep.id, drop: drop.id, similarity: Math.round(sim * 100) / 100, keep_title: keep.title,
            drop_title: drop.title, command: `stealthlab-mcp library merge-goals ${keep.id} ${drop.id}` });
        }
        continue;
      }
      for (const [small, big, ts, tb2] of [[a, b, ta, tb], [b, a, tb, ta]]) {
        // `big` is the narrower problem (its title adds words to `small`'s); one that already has a parent is left alone
        if (ts.size >= 2 && ts.size < tb2.size && [...ts].every((w) => tb2.has(w)) && !big.parent && !isAncestor(big.id, small.id)) {
          parents.push({ goal: big.id, parent: small.id, goal_title: big.title, parent_title: small.title,
            command: `stealthlab-mcp library goal ${big.id} --parent ${small.id}` });
        }
      }
    }
  }
  return {
    merges: merges.sort((x, y) => y.similarity - x.similarity),
    parents: parents.sort((x, y) => (x.goal < y.goal ? -1 : x.goal > y.goal ? 1 : 0)),
  };
}

// When the file is filling up, which entries to archive first by usefulness rather than age alone: failed attempts,
// then entries whose files changed (stale), oldest first. Current mined history is NOT a reason by itself -- a mined
// library is all `historical`, and "archive everything" is not advice; the cap still archives by age if nothing is
// done. These are the entries a reader would not miss.
export function archiveCandidates(lib, bytes) {
  const pct = bytes / L.LIBRARY_MAX_BYTES;
  if (pct < ARCHIVE_SUGGEST_AT) return [];
  const rank = (e) => (e.outcome === "fail" ? 0 : e.status === "stale" ? 1 : 2);
  return lib.entries.filter((e) => rank(e) < 2)
    .sort((a, b) => rank(a) - rank(b) || (a.verified_at || "").localeCompare(b.verified_at || "") || (a.id < b.id ? -1 : 1))
    .map((e) => ({ id: e.id, title: e.title, outcome: e.outcome, status: e.status, verified_at: e.verified_at,
      command: `stealthlab-mcp library drop ${e.id}` }));
}

const cap = (list) => ({ items: list.slice(0, TIDY_MAX_PER_SECTION), total: list.length, truncated: Math.max(0, list.length - TIDY_MAX_PER_SECTION) });

// One report of everything worth doing, in the order worth doing it.
export function tidyReport(root, { lib = L.loadLibrary(root) } = {}) {
  const lint = lintLibrary(root, { lib });
  const goals = goalCandidates(lib);
  return {
    clean: lint.ok && !duplicateEntries(lib).length && !goals.merges.length,
    lint: { ok: lint.ok, counts: lint.counts, issues: cap(lint.issues.filter((i) => i.severity !== "info")) },
    stale: cap(lib.entries.filter((e) => e.status === "stale").map((e) => ({
      id: e.id, title: e.title, verified_at: e.verified_at,
      command: `stealthlab-mcp library check   # then, if it still holds: stealthlab-mcp library refresh ${e.id}`,
    }))),
    duplicate_entries: cap(duplicateEntries(lib)),
    duplicate_goals: cap(goals.merges),
    goal_parents: cap(goals.parents),
    archive_first: cap(archiveCandidates(lib, lint.bytes)),
    budget: { bytes: lint.bytes, max_bytes: lint.max_bytes, pct: Math.round((lint.bytes / lint.max_bytes) * 100) },
    totals: { entries: lib.entries.length, goals: lib.goals.length, ways: lib.ways.length },
  };
}

export const TIDY_RULES = [
  "Search before you write: look in index/terms.idx and library.idx for the same problem; attach with `library add --goal <G-id> --way <W-id>` instead of recording a near-duplicate.",
  "Keep each fact in one place: merge a duplicate into the entry you keep, do not leave both.",
  "The most recent verified entry wins unless its diff shows the older one is the correct one -- read both diffs before dropping.",
  "Change library.md only through `library drop`, `library merge-goals` and `library goal`; never hand-edit it. Dropped entries move to library/archive-superseded.md and stay greppable.",
  "A union merge keeps every line from both sides, so a dropped entry can return after the next merge. Run `library tidy` again after merging; it will be listed again and the same command removes it again.",
  "When done: `library check`, then `library lint`. Lint must report no errors.",
];

// The worklist as the agent reads it (written to .stealth/library/TIDY.md by `library tidy --write`; generated,
// not committed -- delete it any time).
export function renderTidy(report) {
  const out = ["# library TIDY worklist (generated by `stealthlab-mcp library tidy --write`; safe to delete)", "",
    `${report.totals.entries} entries, ${report.totals.goals} goals, ${report.totals.ways} ways; library.md is ${report.budget.pct}% of its ${report.budget.max_bytes}-byte cap.`,
    "", "## Rules", ...TIDY_RULES.map((r) => `- ${r}`), ""];
  const section = (title, block, line) => {
    if (!block.total) return;
    out.push(`## ${title} (${block.total}${block.truncated ? `, first ${block.items.length} shown` : ""})`, "");
    for (const item of block.items) out.push(...line(item));
    out.push("");
  };
  section("Fix first: library.md is inconsistent", report.lint.issues, (i) => [`- [${i.severity}] ${i.message}${i.fix ? `\n  fix: \`${i.fix}\`` : ""}`]);
  section("Duplicate entries (same fix recorded twice?)", report.duplicate_entries, (d) => [
    `- keep ${d.keep} "${d.keep_title}" (${d.keep_verified_at || "unverified"}), drop ${d.drop} "${d.drop_title}" (${d.drop_verified_at || "unverified"}${d.drop_is_stale ? ", stale" : ""}); ` +
    `title ${d.title_similarity}${d.file_overlap === null ? "" : `, files ${d.file_overlap}`}`,
    `  ${d.judge}`, `  \`${d.command}\``]);
  section("Duplicate goals (same problem, different title)", report.duplicate_goals, (m) => [
    `- keep ${m.keep} "${m.keep_title}", merge ${m.drop} "${m.drop_title}" (${m.similarity})`, `  \`${m.command}\``]);
  section("Goals that look narrower than another (give them a parent)", report.goal_parents, (p) => [
    `- ${p.goal} "${p.goal_title}" looks like a narrower version of ${p.parent} "${p.parent_title}"`, `  \`${p.command}\``]);
  section("Stale entries (a file they touched changed)", report.stale, (s) => [`- ${s.id} "${s.title}" (verified ${s.verified_at || "never"})`, `  \`${s.command}\``]);
  section("Archive these first if you need room", report.archive_first, (a) => [
    `- ${a.id} "${a.title}" (${a.outcome}${a.status === "stale" ? ", stale" : ""}, ${a.verified_at || "unverified"})  \`${a.command}\``]);
  if (report.clean) out.push("Nothing to do: no errors, no duplicates.", "");
  return out.join("\n");
}
