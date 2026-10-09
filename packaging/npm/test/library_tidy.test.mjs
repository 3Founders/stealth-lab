// lib/library_tidy.mjs + dropEntry / mergeGoals (lib/library.mjs): finding what to clean in library.md, and the two
// edits that clean it. The point of the tests is the judgement boundary: it reports candidates with evidence and
// never edits; the edits are idempotent, keep what they must (the diff, the other entries) and refuse what would break.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import * as L from "../lib/library.mjs";
import * as T from "../lib/library_tidy.mjs";
import { runLibraryCli } from "../lib/library_cli.mjs";

function repo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stealth-tidy-"));
  fs.mkdirSync(path.join(dir, ".stealth", "library", "solutions"), { recursive: true });
  return dir;
}

const entry = (id, title, extra = {}) => ({
  id, title, unit: ".", g: null, outcome: "pass", status: "current", verified_at: "2026-10-01", route: null, tags: [],
  procs: [{ index: 1, name: title, p: null, solution: null, touches: [], steps: [{ order: 1, kind: "action", do: `do ${id}`, check: null }] }],
  ...extra,
});
const touching = (...paths) => [{ index: 1, name: "fix", p: null, solution: null,
  touches: paths.map((p) => ({ path: p, sha: "abc123" })), steps: [{ order: 1, kind: "action", do: "edit", check: null }] }];

// write a library with the knowledge layer linked, the way `library add` leaves it
function seed(root, entries, goals = []) {
  const lib = { entries, goals, ways: [] };
  L.linkKnowledge(lib);
  for (const g of goals) {
    const mine = lib.goals.find((x) => x.id === g.id);
    if (mine) Object.assign(mine, g);
  }
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), L.renderLibrary(lib));
  L.buildIndex(root);
  return L.loadLibrary(root);
}
const lib = (root) => L.loadLibrary(root);

// ------------------------------------------------------------------ lint

test("lint: a clean, linked library has no errors", () => {
  const root = repo();
  seed(root, [entry("L-000001", "Fix null deref in csv parser"), entry("L-000002", "Add retry to http client")]);
  const r = T.lintLibrary(root);
  assert.equal(r.ok, true);
  assert.equal(r.counts.error, 0);
});

test("lint: dangling goal, way and parent links are errors with the command that fixes them", () => {
  const root = repo();
  const bad = entry("L-000001", "Fix a thing", { goal: "G-deadbeef" });
  bad.procs[0].way = "W-cafef00d";
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), L.renderLibrary({
    entries: [bad], goals: [{ id: "G-aaaaaaaa", title: "Child goal", parent: "G-bbbbbbbb", g: null, unit: ".", tags: [], extra: [] }], ways: [] }));
  const r = T.lintLibrary(root);
  const codes = r.issues.map((i) => i.code);
  assert.equal(r.ok, false);
  assert.ok(codes.includes("dangling_goal") && codes.includes("dangling_way") && codes.includes("dangling_parent"));
  assert.match(r.issues.find((i) => i.code === "dangling_parent").fix, /library goal G-aaaaaaaa --parent -/);
});

test("lint: a parent cycle (two machines setting parents that are fine alone) is reported once", () => {
  const root = repo();
  const g = (id, parent) => ({ id, title: `Goal ${id}`, parent, g: null, unit: ".", tags: [], extra: [] });
  fs.writeFileSync(path.join(root, ".stealth", "library.md"),
    L.renderLibrary({ entries: [], goals: [g("G-aaaaaaaa", "G-bbbbbbbb"), g("G-bbbbbbbb", "G-aaaaaaaa")], ways: [] }));
  const cycles = T.lintLibrary(root).issues.filter((i) => i.code === "parent_cycle");
  assert.equal(cycles.length, 1);
});

test("lint: a missing diff file is a warning, and unused goals and ways are only info", () => {
  const root = repo();
  const e = entry("L-000001", "Fix a thing");
  e.procs[0].solution = "solutions/L-000001.diff";
  seed(root, [e]);
  let r = T.lintLibrary(root);
  assert.ok(r.issues.some((i) => i.code === "missing_diff" && i.severity === "warn"));
  assert.equal(r.ok, true);
  fs.writeFileSync(path.join(root, ".stealth", "library", "solutions", "L-000001.diff"), "diff");
  r = T.lintLibrary(root);
  assert.ok(!r.issues.some((i) => i.code === "missing_diff"));
  const l = lib(root);
  l.goals.push({ id: "G-11111111", title: "Nobody uses me", parent: null, g: null, unit: ".", tags: [], extra: [] });
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), L.renderLibrary(l));
  assert.ok(T.lintLibrary(root).issues.some((i) => i.code === "unused_goal" && i.severity === "info"));
});

test("lint: parse problems (a conflict, an orphan line) are surfaced, never swallowed", () => {
  const root = repo();
  seed(root, [entry("L-000001", "Fix a thing")]);
  fs.appendFileSync(path.join(root, ".stealth", "library.md"), "STEP|L-ffffff.p1:1|action|orphan|check=-\n");
  const r = T.lintLibrary(root);
  assert.ok(r.issues.some((i) => i.code === "parse" && /orphan/.test(i.message)));
  assert.equal(r.ok, false);
});

test("lint: warns from 90% of the size cap, and not before", () => {
  const root = repo();
  const big = [];
  for (let i = 0; i < 400; i++) big.push(entry(`L-${String(i).padStart(6, "0")}`, `Problem number ${i} ${"word ".repeat(30)}`));
  const small = lib(root);
  void small;
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), L.renderLibrary({ entries: big.slice(0, 5), goals: [], ways: [] }));
  assert.ok(!T.lintLibrary(root).issues.some((i) => i.code === "near_budget"));
  let n = 5;
  let text = "";
  while (Buffer.byteLength(text = L.renderLibrary({ entries: big.slice(0, n), goals: [], ways: [] })) < L.LIBRARY_MAX_BYTES * 0.91 && n < big.length) n++;
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), text);
  assert.ok(T.lintLibrary(root).issues.some((i) => i.code === "near_budget"));
});

// ------------------------------------------------------------------ tidy: candidates and evidence

test("tidy: the same fix recorded twice is a duplicate; the more recently verified one is the one to keep", () => {
  const root = repo();
  const a = entry("L-000001", "Fix null deref in csv parser", { verified_at: "2026-09-01", procs: touching("src/csv.py") });
  const b = entry("L-000002", "Fix null deref in the csv parser", { verified_at: "2026-10-05", procs: touching("src/csv.py") });
  seed(root, [a, b, entry("L-000003", "Add retry to http client")]);
  const r = T.tidyReport(root);
  assert.equal(r.duplicate_entries.total, 1);
  const d = r.duplicate_entries.items[0];
  assert.equal(d.keep, "L-000002");
  assert.equal(d.drop, "L-000001");
  assert.match(d.command, /library drop L-000001 --superseded-by L-000002/);
  assert.match(d.judge, /Compare the two diffs/);
  assert.equal(r.clean, false);
});

test("tidy: similar words over different files in different units are NOT duplicates", () => {
  const root = repo();
  seed(root, [
    entry("L-000001", "Fix parser crash on empty input", { unit: "svc/a", procs: touching("svc/a/parse.py") }),
    entry("L-000002", "Fix parser crash on empty input", { unit: "svc/b", procs: touching("svc/b/parse.py") }),
  ]);
  assert.equal(T.tidyReport(root).duplicate_entries.total, 0);
});

test("tidy: the vocabulary bar is higher unless the same files were touched; a failed attempt is never a duplicate", () => {
  const root = repo();
  seed(root, [
    entry("L-000001", "Fix flaky timeout in queue worker", { procs: touching("q/worker.py") }),
    entry("L-000002", "Repair queue worker timeout flake", { procs: touching("q/worker.py") }),     // same files, ~0.4 title overlap
    entry("L-000003", "Fix flaky timeout in queue worker", { outcome: "fail", procs: touching("q/worker.py") }),
  ]);
  const d = T.tidyReport(root).duplicate_entries.items.map((x) => [x.keep, x.drop].sort().join("+"));
  assert.ok(d.length >= 1);
  assert.ok(!d.some((p) => p.includes("L-000003")), "a failed attempt is not knowledge to merge");
});

test("tidy: two titles for the same goal are merge candidates; a narrower goal is offered a parent", () => {
  const root = repo();
  seed(root, [
    entry("L-000001", "Handle null value in csv parser"),
    entry("L-000002", "Handle null values in csv parser"),
    entry("L-000003", "Handle null value in csv parser for large files"),
    entry("L-000004", "Deploy the service"),
  ]);
  const r = T.tidyReport(root);
  assert.ok(r.duplicate_goals.total >= 1);
  assert.match(r.duplicate_goals.items[0].command, /library merge-goals G-/);
  const g = lib(root).goals;
  const narrow = g.find((x) => /large files/.test(x.title));
  const broad = g.find((x) => x.title === "Handle null value in csv parser");
  assert.ok(r.goal_parents.items.some((p) => p.goal === narrow.id && p.parent === broad.id));
});

test("tidy: stale entries are listed with how to re-check them; the archive list only appears when the file is filling", () => {
  const root = repo();
  seed(root, [entry("L-000001", "Fix a thing", { status: "stale" }), entry("L-000002", "Another problem", { outcome: "fail" })]);
  const r = T.tidyReport(root);
  assert.equal(r.stale.total, 1);
  assert.match(r.stale.items[0].command, /library refresh L-000001/);
  assert.equal(r.archive_first.total, 0);
  assert.ok(r.budget.pct < 75);
  const rank = T.archiveCandidates(lib(root), L.LIBRARY_MAX_BYTES * 0.8);
  assert.deepEqual(rank.map((x) => x.id), ["L-000002", "L-000001"]);              // failed first, then stale
});

test("tidy: each section is capped and says how many were left out", () => {
  const root = repo();
  const many = [];
  for (let i = 0; i < 40; i++) many.push(entry(`L-${String(i).padStart(6, "0")}`, "Fix null deref in csv parser", { verified_at: `2026-09-${String(1 + (i % 28)).padStart(2, "0")}` }));
  seed(root, many);
  const r = T.tidyReport(root);
  assert.equal(r.duplicate_entries.items.length, T.TIDY_MAX_PER_SECTION);
  assert.ok(r.duplicate_entries.truncated > 0);
});

test("tidy never edits library.md, the index or any diff", () => {
  const root = repo();
  seed(root, [entry("L-000001", "Fix null deref in csv parser"), entry("L-000002", "Fix null deref in csv parser")]);
  const snap = () => fs.readdirSync(path.join(root, ".stealth"), { recursive: true }).sort().map((f) => {
    const p = path.join(root, ".stealth", String(f));
    return fs.statSync(p).isFile() ? `${f}:${fs.readFileSync(p, "utf8")}` : String(f);
  }).join("\n---\n");
  const before = snap();
  T.tidyReport(root);
  T.lintLibrary(root);
  assert.equal(snap(), before);
});

test("the rendered worklist carries the rules, the evidence and the commands", () => {
  const root = repo();
  seed(root, [entry("L-000001", "Fix null deref in csv parser", { verified_at: "2026-09-01" }),
    entry("L-000002", "Fix null deref in csv parser", { verified_at: "2026-10-01" })]);
  const text = T.renderTidy(T.tidyReport(root));
  assert.match(text, /## Rules/);
  assert.match(text, /Search before you write/);
  assert.match(text, /most recent verified entry wins unless its diff shows/);
  assert.match(text, /`stealthlab-mcp library drop L-000001 --superseded-by L-000002`/);
  assert.match(T.renderTidy(T.tidyReport(seedClean())), /Nothing to do/);
  function seedClean() { const r = repo(); seed(r, [entry("L-000009", "Deploy the service")]); return r; }
});

// ------------------------------------------------------------------ the edits

test("drop: the entry leaves library.md and the index, lands in the superseded archive with who replaced it, and keeps its diff", () => {
  const root = repo();
  const a = entry("L-000001", "Fix null deref in csv parser", { verified_at: "2026-09-01" });
  a.procs[0].solution = "solutions/L-000001.diff";
  seed(root, [a, entry("L-000002", "Fix null deref in csv parser", { verified_at: "2026-10-01" })]);
  fs.writeFileSync(path.join(root, ".stealth", "library", "solutions", "L-000001.diff"), "the diff");
  const r = L.dropEntry(root, "L-000001", { supersededBy: "L-000002", now: new Date("2026-10-09T00:00:00Z") });
  assert.equal(r.entries, 1);
  assert.deepEqual(lib(root).entries.map((e) => e.id), ["L-000002"]);
  assert.ok(!fs.readFileSync(path.join(root, ".stealth", "index", "library.idx"), "utf8").includes("L-000001"));
  const archived = fs.readFileSync(path.join(root, ".stealth", "library", L.SUPERSEDED_ARCHIVE), "utf8");
  assert.match(archived, /GOAL\|L-000001\|/);
  assert.match(archived, /superseded_by=L-000002/);
  assert.match(archived, /dropped_at=2026-10-09/);
  assert.equal(fs.readFileSync(path.join(root, ".stealth", "library", "solutions", "L-000001.diff"), "utf8"), "the diff");
  assert.equal(T.lintLibrary(root).ok, true);
});

test("drop: refuses an unknown entry, an entry superseding itself, and an unknown replacement; knowledge no entry uses goes", () => {
  const root = repo();
  seed(root, [entry("L-000001", "Fix the first problem"), entry("L-000002", "A completely different task")]);
  assert.throws(() => L.dropEntry(root, "L-999999"), /no library entry L-999999/);
  assert.throws(() => L.dropEntry(root, "L-000001", { supersededBy: "L-000001" }), /cannot supersede itself/);
  assert.throws(() => L.dropEntry(root, "L-000001", { supersededBy: "L-777777" }), /no library entry L-777777/);
  const goalsBefore = lib(root).goals.length;
  L.dropEntry(root, "L-000001");
  assert.equal(lib(root).goals.length, goalsBefore - 1);                          // its Goal and Way are no longer used
  assert.equal(lib(root).ways.length, 1);
});

test("drop is repeatable: a union merge that brings the entry back is cleaned by the same command", () => {
  const root = repo();
  seed(root, [entry("L-000001", "Fix null deref in csv parser"), entry("L-000002", "Fix null deref in csv parser")]);
  const before = fs.readFileSync(path.join(root, ".stealth", "library.md"), "utf8");
  L.dropEntry(root, "L-000001", { supersededBy: "L-000002" });
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), before);           // the other branch's copy wins the merge
  L.buildIndex(root);
  assert.equal(lib(root).entries.length, 2);
  assert.equal(T.tidyReport(root).duplicate_entries.total, 1);                    // it is a candidate again...
  L.dropEntry(root, "L-000001", { supersededBy: "L-000002" });                    // ...and the same command removes it
  assert.deepEqual(lib(root).entries.map((e) => e.id), ["L-000002"]);
  const archived = L.parseLibrary(fs.readFileSync(path.join(root, ".stealth", "library", L.SUPERSEDED_ARCHIVE), "utf8"));
  assert.equal(archived.entries.filter((e) => e.id === "L-000001").length, 1);    // archived once, not twice
});

test("merge-goals: entries, ways and child goals move to the kept goal; the global link and tags are carried over", () => {
  const root = repo();
  const l = seed(root, [entry("L-000001", "Handle null value in csv parser", { tags: ["csv"] }),
    entry("L-000002", "Handle null values in csv parser", { g: "2c1d4a9e-0f3b-4c55-9e1a-7b2f0c6d8e11", tags: ["nulls"] })]);
  const [keep, drop] = [l.goals.find((g) => g.title === "Handle null value in csv parser"), l.goals.find((g) => g.title === "Handle null values in csv parser")];
  const child = { id: "G-12121212", title: "Handle null in csv header", parent: drop.id, g: null, unit: ".", tags: [], extra: [] };
  const withChild = lib(root);
  withChild.goals.push(child);
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), L.renderLibrary(withChild));
  drop.g = "2c1d4a9e-0f3b-4c55-9e1a-7b2f0c6d8e11";
  const withG = lib(root);
  withG.goals.find((g) => g.id === drop.id).g = drop.g;
  withG.goals.find((g) => g.id === drop.id).tags = ["nulls"];
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), L.renderLibrary(withG));

  const r = L.mergeGoals(root, keep.id, drop.id);
  assert.equal(r.entries_moved, 1);
  assert.equal(r.children_moved, 1);
  const after = lib(root);
  assert.ok(!after.goals.some((g) => g.id === drop.id));
  assert.ok(after.entries.every((e) => e.goal === keep.id));
  assert.equal(after.goals.find((g) => g.id === "G-12121212").parent, keep.id);
  const merged = after.goals.find((g) => g.id === keep.id);
  assert.equal(merged.g, "2c1d4a9e-0f3b-4c55-9e1a-7b2f0c6d8e11");
  assert.deepEqual([...merged.tags].sort(), ["csv", "nulls"]);
  assert.equal(T.lintLibrary(root).ok, true);
});

test("merge-goals refuses itself, an unknown goal, and a merge that would make a goal its own ancestor", () => {
  const root = repo();
  const g = (id, parent) => ({ id, title: `Goal ${id}`, parent, g: null, unit: ".", tags: [], extra: [] });
  fs.writeFileSync(path.join(root, ".stealth", "library.md"),
    L.renderLibrary({ entries: [], goals: [g("G-aaaaaaaa", null), g("G-bbbbbbbb", "G-aaaaaaaa")], ways: [] }));
  assert.throws(() => L.mergeGoals(root, "G-aaaaaaaa", "G-aaaaaaaa"), /itself/);
  assert.throws(() => L.mergeGoals(root, "G-aaaaaaaa", "G-ffffffff"), /no knowledge goal G-ffffffff/);
  assert.throws(() => L.mergeGoals(root, "G-bbbbbbbb", "G-aaaaaaaa"), /its own ancestor/);
  assert.equal(lib(root).goals.length, 2);
});

// ------------------------------------------------------------------ the CLI

test("cli: lint exits 1 on an error, tidy --write saves the worklist, drop and merge-goals report what they did", async () => {
  const root = repo();
  seed(root, [entry("L-000001", "Fix null deref in csv parser"), entry("L-000002", "Fix null deref in csv parser", { verified_at: "2026-10-05" })]);
  const out = [];
  const run = (argv) => runLibraryCli([...argv, "--root", root], { print: (o) => out.push(o) });

  await run(["tidy", "--write"]);
  assert.equal(out.at(-1).written, ".stealth/library/TIDY.md");
  assert.match(fs.readFileSync(path.join(root, ".stealth", "library", "TIDY.md"), "utf8"), /library drop L-000001/);

  await run(["drop", "L-000001", "--superseded-by", "L-000002"]);
  assert.equal(out.at(-1).dropped, "L-000001");
  await run(["tidy"]);
  assert.equal(out.at(-1).clean, true);
  await assert.rejects(run(["drop"]), /library drop <L-id>/);
  await assert.rejects(run(["merge-goals", "G-aaaaaaaa"]), /merge-goals <keep/);

  process.exitCode = 0;
  fs.appendFileSync(path.join(root, ".stealth", "library.md"), "STEP|L-ffffff.p1:1|action|orphan|check=-\n");
  await run(["lint"]);
  assert.equal(out.at(-1).ok, false);
  assert.equal(process.exitCode, 1);
  process.exitCode = 0;
});

test("stem: plurals, -ies and -es collapse to one term; short words and -ss are left alone", () => {
  assert.equal(T.stem("values"), T.stem("value"));
  assert.equal(T.stem("timeouts"), T.stem("timeout"));
  assert.equal(T.stem("retries"), "retry");
  assert.equal(T.stem("classes"), "class");
  assert.equal(T.stem("class"), "class");
  assert.equal(T.stem("bus"), "bus");
  assert.deepEqual([...T.titleTerms("Handle null values in the csv parsers")].sort(), ["csv", "handle", "null", "parser", "value"]);
});

test("archive_first: current mined history is not a reason to archive (a mined library is all `historical`)", () => {
  const root = repo();
  seed(root, [entry("L-000001", "Fix a mined problem", { outcome: "historical" }), entry("L-000002", "Another mined one", { outcome: "historical" })]);
  assert.deepEqual(T.archiveCandidates(lib(root), L.LIBRARY_MAX_BYTES * 0.9), []);
});
