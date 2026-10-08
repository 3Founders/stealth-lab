// lib/library.mjs: the same grammar as backend/app/stealth/library.py (shared fixtures), plus the local
// file operations (index self-healing, staleness, write-back, routes/OBS, SUMMARY, request payload).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import * as L from "../lib/library.mjs";

const FIX = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "library");
const fx = (name) => fs.readFileSync(path.join(FIX, name), "utf8").replace(/\r\n/g, "\n");
const GID = "2c1d4a9e-0f3b-4c55-9e1a-7b2f0c6d8e11";
const hasGit = spawnSync("git", ["--version"]).status === 0;

function tmpRepo({ git = true } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stealth-lib-"));
  if (git && hasGit) {
    const g = (...a) => execFileSync("git", ["-C", dir, ...a], { stdio: "ignore" });
    g("init", "-q");
    g("config", "user.email", "t@example.com");
    g("config", "user.name", "t");
    g("config", "core.autocrlf", "false");
  }
  fs.mkdirSync(path.join(dir, ".stealth"), { recursive: true });
  return dir;
}

const entry = (id, extra = {}) => ({
  id, title: "Fix the parser", unit: ".", g: null, outcome: "pass", status: "current", verified_at: "2026-10-07",
  route: null, tags: [], procs: [{ index: 1, name: "patch", p: null, solution: null, touches: [], steps: [] }], ...extra,
});

// ------------------------------------------------------------ shared fixtures (== Python)

test("union-merged fixture canonicalises to the same text, idx and problems as Python", () => {
  const lib = L.parseLibrary(fx("union_merged.md"));
  const canonical = L.renderLibrary(lib);
  assert.equal(canonical, fx("canonical.md"));
  assert.equal(L.renderLibraryIdx(canonical), fx("canonical.idx"));
  assert.deepEqual(lib.problems, ["conflict: two versions of L-b00c1e; kept one", "line 16: not a library line, skipped",
    "orphan: L-ffffff.p1:1 has no PROC line; skipped"]);
});

test("routing fixture parses to the same routes, OBS and local counts as Python", () => {
  const want = JSON.parse(fx("routing.expected.json"));
  const { routes, obs } = L.parseRouting(fx("routing.md"));
  assert.deepEqual(routes, want.routes);
  assert.deepEqual(obs, want.obs);
  assert.deepEqual(L.localObsForGoal(routes, obs, GID), want.local_obs_b00c1e);
  assert.deepEqual(obs.map(L.renderObsLine), want.rendered.filter((l) => l.startsWith("OBS|")));
});

test("round trip keeps pipes, newlines, percent signs and csv separators exactly", () => {
  const e = entry("L-7f3a1c", { title: "Fix KeyError | in parser (90% %7C)", tags: ["a,b", "c#d"],
    procs: [{ index: 1, name: "patch | it", p: null, solution: null,
      touches: [{ path: "src/p,q.py", sha: "1a2b3c4" }, { path: "x#y.py", sha: "dead" }],
      steps: [{ order: 1, kind: "action", do: "edit\nmultiline", check: "pytest -q | tail -3" }] }] });
  const text = L.renderLibrary([e]);
  const noExtra = (x) => JSON.parse(JSON.stringify(x, (k, v) => (k === "extra" && Array.isArray(v) && !v.length ? undefined : v)));
  assert.deepEqual(noExtra(L.parseLibrary(text).entries), [e]);
  assert.match(text, /check=pytest -q %7C tail -3/);
});

test("idx rows point at exact block ranges with their hash", () => {
  const canonical = fx("canonical.md");
  const lines = canonical.split("\n");
  for (const r of L.libraryIdxRows(canonical)) {
    const block = lines.slice(r.start - 1, r.end);
    assert.ok(block[0].startsWith(`GOAL|${r.id}|`));
    assert.equal(L.shortHash(block.join("\n")), r.block_sha);
  }
  assert.deepEqual(L.parseLibraryRows(L.renderLibraryIdx(canonical)).map((r) => r.id), ["L-0a91f2", "L-77d3e0", "L-b00c1e"]);
});

test("a real git union merge of two branches keeps every entry", { skip: !hasGit }, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stealth-merge-"));
  const base = entry("L-111111", { title: "Base" });
  const write = (n, es) => fs.writeFileSync(path.join(dir, n), L.renderLibrary(es));
  write("base.md", [base]);
  write("ours.md", [base, entry("L-222222", { title: "Ours" })]);
  write("theirs.md", [{ ...base, status: "stale", verified_at: "2026-10-09" }, entry("L-0a0a0a", { title: "Theirs" })]);
  spawnSync("git", ["merge-file", "--union", "ours.md", "base.md", "theirs.md"], { cwd: dir });
  const lib = L.parseLibrary(fs.readFileSync(path.join(dir, "ours.md"), "utf8"));
  assert.deepEqual(lib.entries.map((e) => e.id), ["L-0a0a0a", "L-111111", "L-222222"]);
  assert.equal(lib.entries[1].status, "stale");
});

// ------------------------------------------------------------ index, self-healing, summary

test("buildIndex writes canonical library.md, idx, terms, SUMMARY and git files; ensureIndex heals a stale idx", () => {
  const root = tmpRepo({ git: false });
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), fx("union_merged.md"));
  const r = L.buildIndex(root);
  assert.equal(r.entries, 3);
  const s = (f) => fs.readFileSync(path.join(root, ".stealth", f), "utf8");
  assert.equal(s("library.md"), fx("canonical.md"));
  assert.equal(s("index/library.idx"), fx("canonical.idx"));
  assert.match(s("index/terms.idx"), /^csv\|L-b00c1e$/m);
  assert.match(s("index/terms.idx"), /^export\.py\|L-b00c1e$/m);
  assert.match(s(".gitattributes"), /library\.md merge=union/);
  assert.ok(Buffer.byteLength(s("SUMMARY.md")) <= L.SUMMARY_MAX_BYTES);
  assert.match(s("SUMMARY.md"), /Library: 3 solved here/);
  // someone appends an entry by hand: the idx no longer matches and is rebuilt on the next read
  fs.appendFileSync(path.join(root, ".stealth", "library.md"), "\n" + L.renderGoalLine(entry("L-abcdef", { title: "New" })) + "\n");
  assert.equal(L.ensureIndex(root).rebuilt, true);
  assert.ok(L.readEntry(root, "L-abcdef").lines[0].startsWith("GOAL|L-abcdef|"));
  assert.equal(L.ensureIndex(root).rebuilt, false);
});

test("readEntry recovers when a range is wrong but source_sha was forged", () => {
  const root = tmpRepo({ git: false });
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), fx("canonical.md"));
  L.buildIndex(root);
  const idxFile = path.join(root, ".stealth", "index", "library.idx");
  fs.writeFileSync(idxFile, fs.readFileSync(idxFile, "utf8").replace("|15|18|", "|2|3|"));
  const got = L.readEntry(root, "L-b00c1e");
  assert.equal(got.via, "rebuilt-idx");
  assert.equal(got.lines.length, 4);
});

test("SUMMARY stays under 3 KB for a large monorepo library", () => {
  const root = tmpRepo({ git: false });
  const many = Array.from({ length: 300 }, (_, i) => entry(`L-${(0x100000 + i).toString(16)}`,
    { title: `A long problem title number ${i} about something in packages that matters a lot`, unit: `packages/p${i}` }));
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), L.renderLibrary(many).slice(0, 60000));
  fs.mkdirSync(path.join(root, ".stealth", "index"), { recursive: true });
  fs.writeFileSync(path.join(root, ".stealth", "index", "units.idx"), many.map((e) => e.unit).join("\n") + "\n");
  L.buildIndex(root);
  const text = fs.readFileSync(path.join(root, ".stealth", "SUMMARY.md"), "utf8");
  assert.ok(Buffer.byteLength(text) <= L.SUMMARY_MAX_BYTES);
  assert.match(text, /Units: 300 in 1 groups: packages\/ 300/);
});

// ------------------------------------------------------------ staleness and write-back (real git)

test("write-back records touches, refuses a failing check, and staleness marks edited files", { skip: !hasGit }, () => {
  const root = tmpRepo();
  fs.writeFileSync(path.join(root, "a.py"), "x = 1\n");
  execFileSync("git", ["-C", root, "add", "a.py"]);
  execFileSync("git", ["-C", root, "commit", "-qm", "base"]);
  fs.writeFileSync(path.join(root, "a.py"), "x = 2\n");
  fs.writeFileSync(path.join(root, "b.py"), "y = 1\n");
  const diff = L.diffFromGit(root, "HEAD");
  assert.deepEqual(L.diffPaths(diff).sort(), ["a.py", "b.py"]);

  assert.throws(() => L.addEntry(root, { title: "Bump x", diff, check: "exit 1" }), /check failed/);
  assert.throws(() => L.addEntry(root, { title: "Bump x", diff }), /needs --check/);
  const e = L.addEntry(root, { title: "Bump x", diff, check: "exit 0", g: GID, tags: ["x"] });
  assert.match(e.id, /^L-[0-9a-f]{6}$/);
  assert.equal(e.verified_at, new Date().toISOString().slice(0, 10));
  assert.deepEqual(e.procs[0].touches.map((t) => t.path).sort(), ["a.py", "b.py"]);
  assert.ok(fs.existsSync(path.join(root, ".stealth", "library", `solutions/${e.id}.diff`)));
  assert.deepEqual(L.checkStaleness(root), []);

  fs.writeFileSync(path.join(root, "a.py"), "x = 3\n");
  const report = L.checkStaleness(root);
  assert.deepEqual(report, [{ id: e.id, status: "stale", changed: ["a.py"] }]);
  assert.equal(L.loadLibrary(root).entries[0].status, "stale");
  L.refreshEntry(root, e.id);
  assert.equal(L.loadLibrary(root).entries[0].status, "current");
  assert.deepEqual(L.checkStaleness(root), []);
});

test("CRLF in the working tree does not make an entry stale (git hash-object applies the same filters)", { skip: !hasGit }, () => {
  const root = tmpRepo();
  execFileSync("git", ["-C", root, "config", "core.autocrlf", "true"]);
  fs.writeFileSync(path.join(root, "c.txt"), "a\nb\n");
  const sha = L.currentShas(root, ["c.txt"]).get("c.txt");
  fs.writeFileSync(path.join(root, "c.txt"), "a\r\nb\r\n");
  assert.equal(L.currentShas(root, ["c.txt"]).get("c.txt"), sha);
});

// ------------------------------------------------------------ routing upkeep and payload

test("routes are upserted per (route, step), OBS counted in place, payload carries them", () => {
  const root = tmpRepo({ git: false });
  const line = (fit) => `ROUTE|R-b00c1e|goal=-|g=${GID}|fit=${fit}|basis=-|as_of=2026-10-07|step=*|ladder=a::s|whole=-`;
  L.upsertRoutes(root, [line(1)]);
  L.recordObs(root, "R-b00c1e", "a", "s", true);
  L.recordObs(root, "R-b00c1e", "a", "s", false);
  L.upsertRoutes(root, [line(2)]);
  const { routes, obs } = L.parseRouting(fs.readFileSync(path.join(root, ".stealth", "routing.md"), "utf8"));
  assert.deepEqual(routes.map((r) => r.fit), ["2"]);
  assert.deepEqual(obs.map((o) => [o.n, o.ok]), [[2, 1]]);
  assert.equal(L.ensureRoute(root, GID), "R-b00c1e");
  assert.match(L.ensureRoute(root, "11111111-1111-4111-8111-111111111111"), /^R-[0-9a-f]{6}$/);

  fs.writeFileSync(path.join(root, ".stealth", "library.md"), fx("canonical.md"));
  fs.writeFileSync(path.join(root, ".stealth", "meta.json"),
    JSON.stringify({ repo_identity: { repo_id: "r:0123456789abcdef", public_name: "o/n" } }));
  const p = L.requestPayload(root, { env: {} });
  assert.equal(p.library_rows.split("\n").filter(Boolean).length, 3);
  assert.match(p.route_obs, /^OBS\|R-b00c1e\|a\|s\|n=2\|ok=1/m);
  assert.deepEqual(p.repo_identity, { repo_id: "r:0123456789abcdef" });          // name not shared by default
  assert.deepEqual(L.requestPayload(root, { env: { STEALTHLAB_SHARE_REPO_NAME: "1" } }).repo_identity,
    { repo_id: "r:0123456789abcdef", public_name: "o/n" });
});

test("no .stealth library: the payload is empty (find_ways unchanged)", () => {
  const root = tmpRepo({ git: false });
  assert.deepEqual(L.requestPayload(root, { env: {} }), {});
});

// ------------------------------------------------------------ hook + capture integration

test("formatKnowledge puts this repo's library match (steps + local diff) before global knowledge", async () => {
  const { formatKnowledge } = await import("../lib/hook.mjs");
  const root = tmpRepo({ git: false });
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), fx("canonical.md"));
  fs.mkdirSync(path.join(root, ".stealth", "library", "solutions"), { recursive: true });
  fs.writeFileSync(path.join(root, ".stealth", "library", "solutions", "L-b00c1e.diff"), "+++ b/packages/api/export.py\n+import csv\n");
  const reply = { outcome: "resolved", procedures: [{ name: "Global way", steps: [{ do: "do the global thing" }] }],
    library_matches: [{ id: "L-b00c1e", title: "Make the CSV export handle commas inside quoted fields", unit: "packages/api",
      outcome: "pass", status: "stale", verified_at: "2026-10-05", judged: true, relation: "matches", confidence: 0.9 }] };
  const text = formatKnowledge(reply, 8000, { root });
  const local = text.indexOf("Solved before in THIS repo");
  assert.ok(local > 0 && local < text.indexOf("Global way"));
  assert.match(text, /STALE/);
  assert.match(text, /1\. Replace the manual split with csv\.reader \(check: pytest packages\/api\/tests\/test_export\.py -q\)/);
  assert.match(text, /```diff\n\+\+\+ b\/packages\/api\/export\.py\n\+import csv/);
  assert.equal(formatKnowledge(reply, 8000, {}).includes("Solved before"), false);   // no root: nothing local
});

test("callFindWays sends the library arguments only when given", async () => {
  const { callFindWays } = await import("../lib/hook.mjs");
  const bodies = [];
  const fetchImpl = async (url, init) => {
    if (init.method === "DELETE") return { ok: true };
    const body = JSON.parse(init.body);
    bodies.push(body);
    const result = body.method === "tools/call" ? { content: [{ type: "text", text: "{\"outcome\":\"no_match\"}" }] } : { protocolVersion: "x" };
    return { ok: true, status: 200, headers: new Map(), text: async () => JSON.stringify({ jsonrpc: "2.0", id: body.id, result }) };
  };
  for (const extra of [undefined, { library_rows: "L-abcdef|current|pass|.|-|-|1|1|x|t\n", repo_identity: { repo_id: "r:0123456789abcdef" } }]) {
    await callFindWays({ url: "http://x/mcp", userAgent: "t", query: "q", repoClaims: "", timeoutMs: 5000, extra, fetchImpl });
  }
  const calls = bodies.filter((b) => b.method === "tools/call").map((b) => b.params.arguments);
  assert.deepEqual(Object.keys(calls[0]).sort(), ["query", "repo_claims"]);
  assert.equal(calls[1].repo_identity.repo_id, "r:0123456789abcdef");
});

test("capture Stop counts a resolved lookup's verdict as a local OBS, without a token", async () => {
  const { recordLocalObs } = await import("../lib/capture_hook.mjs");
  const root = tmpRepo({ git: false });
  const s = { lookup: { outcome: "resolved", goal_id: GID }, cwd: root, route: null,
    tests: [{ verdict: false }, { verdict: true }, { verdict: null }] };
  assert.equal(recordLocalObs(s, "claude-sonnet-5-5"), true);
  assert.equal(recordLocalObs(s, "claude-sonnet-5-5"), false);             // once per prompt
  const { routes, obs } = L.parseRouting(fs.readFileSync(path.join(root, ".stealth", "routing.md"), "utf8"));
  assert.equal(routes[0].g, GID);
  assert.deepEqual(obs.map((o) => [o.route, o.model, o.scaffold, o.n, o.ok]), [[routes[0].id, "claude-sonnet-5-5", "claude-code", 1, 1]]);
  // a near-miss Goal ("suggested") is not this Goal: never counted; no .stealth: nothing written
  assert.equal(recordLocalObs({ ...s, obs_recorded: false, lookup: { outcome: "suggested", goal_id: GID } }, "m"), false);
  const bare = fs.mkdtempSync(path.join(os.tmpdir(), "no-stealth-"));
  assert.equal(recordLocalObs({ ...s, obs_recorded: false, cwd: bare }, "m"), false);
  assert.equal(fs.existsSync(path.join(bare, ".stealth")), false);
});

test("library CLI: add, show, obs, payload round trip", { skip: !hasGit }, async () => {
  const { runLibraryCli } = await import("../lib/library_cli.mjs");
  const root = tmpRepo();
  fs.writeFileSync(path.join(root, "a.py"), "x = 1\n");
  execFileSync("git", ["-C", root, "add", "a.py"]);
  execFileSync("git", ["-C", root, "commit", "-qm", "base"]);
  fs.writeFileSync(path.join(root, "a.py"), "x = 2\n");
  const outs = [];
  const print = (o) => outs.push(o);
  await runLibraryCli(["add", "--root", root, "--title", "Bump x", "--check", "exit 0", "--step", "action|Edit a.py|"], { print });
  const id = outs[0].id;
  await runLibraryCli(["show", id, "--root", root], { print });
  assert.ok(outs[1].lines[0].startsWith(`GOAL|${id}|Bump x|`));
  await runLibraryCli(["route", "--root", root, "--line", `ROUTE|R-abcdef|goal=${id}|g=${GID}|fit=-|basis=-|as_of=x|step=*|ladder=-|whole=-`], { print });
  await runLibraryCli(["obs", "R-abcdef", "--root", root, "--model", "m", "--scaffold", "s", "--fail"], { print });
  await runLibraryCli(["payload", "--root", root], { print });
  assert.match(outs[4].library_rows, new RegExp(`^${id}\|current\|pass\|`));
  assert.match(outs[4].route_obs, /^OBS\|R-abcdef\|m\|s\|n=1\|ok=0/m);
  await assert.rejects(runLibraryCli(["add", "--root", root, "--title", "x", "--step", "bogus"], { print }), /--step is/);
});

// ------------------------------------------------------------ compatibility with the survey (feat/survey)

test("survey-mined entries keep their extra fields, unknown shas are unverifiable, UNIT lines are read", { skip: !hasGit }, () => {
  const root = tmpRepo();
  fs.writeFileSync(path.join(root, "a.py"), "x = 1\n");
  const mined = [
    "GOAL|L-1a2b3c4|fix: off by one in paginate|unit=.|g=-|outcome=historical|verified_at=2026-01-02|route=-|commit=1a2b3c4d5e",
    "PROC|L-1a2b3c4.p1|fix: off by one in paginate|p=-|solution=solutions/L-1a2b3c4.diff|touches=a.py#sha=-|diff=truncated",
    "STEP|L-1a2b3c4.p1:1|action|fix: off by one in paginate|check=tests:pytest",
  ].join("\n");
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), mined + "\n");
  fs.mkdirSync(path.join(root, ".stealth", "index"), { recursive: true });
  fs.writeFileSync(path.join(root, ".stealth", "index", "units.idx"),
    "# units\nUNIT|root|.|kind=python\nUNIT|api|packages/api|kind=node|page=claims/api.md\n");
  L.buildIndex(root);
  const text = fs.readFileSync(path.join(root, ".stealth", "library.md"), "utf8");
  assert.match(text, /\|tags=-\|commit=1a2b3c4d5e$/m);
  assert.match(text, /\|touches=a\.py#sha=-\|diff=truncated$/m);
  assert.deepEqual(L.checkStaleness(root), [{ id: "L-1a2b3c4", status: "current", unverifiable: ["a.py (no recorded sha)"] }]);
  assert.match(fs.readFileSync(path.join(root, ".stealth", "SUMMARY.md"), "utf8"), /^Units: 2: \., packages\/api$/m);
});

test("findStealthRoot finds .stealth from a sub-directory, and null outside one", () => {
  const root = tmpRepo({ git: false });
  const sub = path.join(root, "packages", "api", "src");
  fs.mkdirSync(sub, { recursive: true });
  assert.equal(L.findStealthRoot(sub), path.resolve(root));
  assert.equal(L.findStealthRoot(fs.mkdtempSync(path.join(os.tmpdir(), "none-"))), null);
});

test("share drafts a submit_way call from an entry, never the diff or paths", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "lib-share-"));
  fs.mkdirSync(path.join(root, ".stealth"), { recursive: true });
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), [
    "GOAL|L-0a91f2|Fix KeyError when the config has no db section|unit=services/api|g=-|outcome=pass|status=current|verified_at=2026-08-02|route=R-00aa11|tags=config",
    "PROC|L-0a91f2.p1|Default the section|p=-|solution=solutions/L-0a91f2.diff|touches=src/secret_loader.py#sha=77aa001",
    "STEP|L-0a91f2.p1:1|action|Use config.get(\"db\", {})|check=pytest -q tests/test_config.py",
    "GOAL|L-77d3e0|Speed up the slow test|unit=.|g=-|outcome=fail|status=current|verified_at=2026-10-01|route=-|tags=-",
    "",
  ].join("\n"));
  const d = L.shareDraft(root, "L-0a91f2");
  assert.equal(d.call, "submit_way");
  assert.equal(d.arguments.goal, "Fix KeyError when the config has no db section");
  assert.deepEqual(JSON.parse(d.arguments.steps_json), ["Use config.get(\"db\", {}) (check: pytest -q tests/test_config.py)"]);
  const text = JSON.stringify(d.arguments);
  for (const leak of ["secret_loader", "services/api", "R-00aa11", ".diff", "77aa001"]) assert.ok(!text.includes(leak), leak);
  assert.throws(() => L.shareDraft(root, "L-77d3e0"), /failed attempt/);
  assert.throws(() => L.shareDraft(root, "L-ffffff"), /no library entry/);
});

test("task_features: fix sizes per entry and the repo median, numbers only", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "lib-tf-"));
  fs.mkdirSync(path.join(root, ".stealth", "library", "solutions"), { recursive: true });
  const diff = (n) => [`diff --git a/src/a${n}.py b/src/a${n}.py`, "@@ -1 +1 @@", "-x", "+y", "+z",
                       "diff --git a/pkg/b.ts b/pkg/b.ts", "@@ -1 +1 @@", "+w"].join("\n");
  fs.writeFileSync(path.join(root, ".stealth", "library", "solutions", "L-0a91f2.diff"), diff(1));
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), [
    "GOAL|L-0a91f2|Fix it|unit=.|g=-|outcome=pass|status=current|verified_at=2026-08-02|route=-|tags=-",
    "PROC|L-0a91f2.p1|Way|p=-|solution=solutions/L-0a91f2.diff|touches=-",
    "GOAL|L-77d3e0|Failed|unit=.|g=-|outcome=fail|status=current|verified_at=2026-08-02|route=-|tags=-", ""].join("\n"));
  const tf = L.taskFeatures(root);
  assert.deepEqual(tf.entries["L-0a91f2"], { files: 2, hunks: 2, lines_added: 3, lines_removed: 1, languages: 2, packages: 2 });
  assert.deepEqual(tf.repo, tf.entries["L-0a91f2"]);
  assert.ok(!JSON.stringify(tf).includes("src/"));
  assert.equal(L.requestPayload(root).task_features.entries["L-0a91f2"].files, 2);
});

// ---- knowledge layer (Goals with parents, reusable Ways, their Steps)

test("linked fixture: knowledge lines round-trip byte for byte (the same text Python renders)", () => {
  const text = fx("linked.md");
  const lib = L.parseLibrary(text);
  assert.deepEqual(lib.problems, []);
  assert.equal(L.renderLibrary(lib), text);
  assert.ok(lib.goals.length >= 2 && lib.ways.length >= 2);
  for (const e of lib.entries.filter((x) => x.outcome !== "fail")) {
    assert.ok(lib.goals.some((g) => g.id === e.goal), e.id);
    assert.ok(e.procs.every((p) => lib.ways.some((w) => w.id === p.way)), e.id);
  }
  assert.equal(L.linkKnowledge(lib), 0);                     // already linked: nothing to add
});

test("linking reuses one Goal per problem and one Way per procedure, with content-hash ids", () => {
  const step = { order: 1, kind: "action", do: "Use config.get(\"db\", {})", check: "pytest -q" };
  const entry = (id, title, stepDo) => ({ id, title, unit: ".", g: null, outcome: "pass", status: "current",
    verified_at: "2026-10-01", route: null, tags: [], extra: [],
    procs: [{ index: 1, name: "Default the section", p: null, solution: null, touches: [], extra: [],
      steps: [{ ...step, do: stepDo, extra: [] }] }] });
  const lib = { entries: [entry("L-000001", "Fix KeyError  when DB missing", step.do),
                          entry("L-000002", "fix keyerror when db missing", step.do),
                          entry("L-000003", "Another problem", step.do),
                          entry("L-000004", "Another problem", "Something else")], goals: [], ways: [] };
  assert.equal(L.linkKnowledge(lib), 8);
  assert.equal(lib.goals.length, 2);                          // the first two titles differ only in case/space
  assert.equal(lib.ways.length, 2);                           // three entries share one procedure
  assert.equal(lib.entries[0].goal, lib.entries[1].goal);
  assert.equal(lib.entries[0].procs[0].way, lib.entries[2].procs[0].way);
  assert.notEqual(lib.entries[2].procs[0].way, lib.entries[3].procs[0].way);
  assert.equal(lib.entries[0].goal, L.goalIdFor("Fix KeyError when DB missing"));
  const again = L.parseLibrary(L.renderLibrary(lib));         // what another machine would write: identical
  assert.equal(L.renderLibrary(again), L.renderLibrary(lib));
});

test("goal parents: set, clear, and a cycle is refused", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "lib-know-"));
  fs.mkdirSync(path.join(root, ".stealth"), { recursive: true });
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), fx("linked.md"));
  const [a, b] = L.loadLibrary(root).goals.map((g) => g.id);
  assert.equal(L.setGoalParent(root, a, b).parent, b);
  assert.throws(() => L.setGoalParent(root, b, a), /cannot be its own ancestor/);
  assert.equal(L.loadLibrary(root).goals.find((g) => g.id === a).parent, b);
  assert.equal(L.setGoalParent(root, a, "-").parent, null);
  assert.throws(() => L.setGoalParent(root, "G-ffffffff", "-"), /no knowledge goal/);
});

test("linkLibrary migrates an unlinked file; addEntry attaches to an existing Way; payload names it", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "lib-link-"));
  fs.mkdirSync(path.join(root, ".stealth"), { recursive: true });
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), fx("canonical.md"));
  const r = L.linkLibrary(root);
  assert.ok(r.linked > 0 && r.goals >= 2 && r.ways >= 2);
  assert.equal(L.linkLibrary(root).linked, 0);
  const lib = L.loadLibrary(root);
  const way = lib.ways[0];
  const e = L.addEntry(root, { title: "A new problem", way: way.id, verify: false, outcome: "historical",
    steps: [{ kind: "action", do: "x" }] }, { gitImpl: () => "" });
  assert.equal(e.procs[0].way, way.id);
  const after = L.loadLibrary(root);
  assert.ok(after.goals.some((g) => g.id === e.goal));
  assert.match(fs.readFileSync(path.join(root, ".stealth", "SUMMARY.md"), "utf8"), /Knowledge: \d+ goals/);
});

test("survey history mining keeps the knowledge lines", async () => {
  const { readLibrary } = await import("../lib/survey/history.mjs");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lib-hist-"));
  const file = path.join(dir, "library.md");
  fs.writeFileSync(file, fx("linked.md"));
  const got = readLibrary(file);
  assert.ok(got.knowledge.some((l) => l.startsWith("G|")) && got.knowledge.some((l) => l.startsWith("S|")));
  assert.ok(got.blocks.size >= 2);
});

test("codeWays: uncoded Ways get the server's code on their W line; vectors stay local; failures leave them uncoded", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "lib-codes-"));
  fs.mkdirSync(path.join(root, ".stealth"), { recursive: true });
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), fx("linked.md"));
  const ways = L.loadLibrary(root).ways;
  let sent;
  const fetchImpl = async (url, init) => {
    sent = { url, body: JSON.parse(init.body), auth: init.headers.authorization };
    return { ok: true, status: 200, json: async () => ({ version: "cb1-test", embedding_model_id: "m",
      codes: Object.fromEntries(sent.body.items.map((it, i) => [it.id, `c0${i}.1`])),
      vectors: Object.fromEntries(sent.body.items.map((it) => [it.id, [0.1, 0.2]])) }) };
  };
  assert.deepEqual((await L.codeWays(root, { url: "https://h/mcp" })).coded, 0);      // no token: nothing sent
  const r = await L.codeWays(root, { url: "https://h/mcp", token: "t", fetchImpl });
  assert.equal(r.coded, ways.length);
  assert.equal(sent.url, "https://h/routing/codes");
  assert.equal(sent.auth, "Bearer t");
  assert.ok(sent.body.items.every((it) => it.text.length > 0 && it.text.length <= 4000));
  const after = L.loadLibrary(root);
  assert.ok(after.ways.every((w) => /^c0\d\.1$/.test(w.code)));
  assert.match(fs.readFileSync(path.join(root, ".stealth", "library.md"), "utf8"), /^W\|W-[0-9a-f]{8}\|.*\|code=c0\d\.1/m);
  const kept = JSON.parse(fs.readFileSync(path.join(root, ".stealth", "index", "way_vectors.json"), "utf8"));
  assert.equal(kept.version, "cb1-test");
  assert.equal(Object.keys(kept.vectors).length, ways.length);
  assert.match(fs.readFileSync(path.join(root, ".stealth", ".gitignore"), "utf8"), /index\/way_vectors\.json/);
  assert.equal((await L.codeWays(root, { url: "https://h/mcp", token: "t", fetchImpl })).coded, 0);   // all coded
  const root2 = fs.mkdtempSync(path.join(os.tmpdir(), "lib-codes2-"));
  fs.mkdirSync(path.join(root2, ".stealth"), { recursive: true });
  fs.writeFileSync(path.join(root2, ".stealth", "library.md"), fx("linked.md"));
  const down = await L.codeWays(root2, { url: "https://h/mcp", token: "t",
    fetchImpl: async () => ({ ok: false, status: 503, json: async () => ({ error: "no codebook" }) }) });
  assert.equal(down.coded, 0);
  assert.match(down.skipped, /503: no codebook/);
});
