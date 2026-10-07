// Offline tests for `stealthlab-mcp survey` (lib/survey/): every repo shape in plan §4.1 and every edge case
// in §4.6, on generated fixture repositories. No network, no StealthLab server.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { runSurvey } from "../lib/survey/survey.mjs";
import { normaliseRemote } from "../lib/survey/identity.mjs";
import { claimsOf, parseClaim, readPage, kvGet } from "../lib/survey/claims.mjs";
import { compileGitignore, ignored } from "../lib/survey/files.mjs";
import { parseToml } from "../lib/survey/parse.mjs";
import { SHAPES, git, makeRepo, tmpDir, writeFiles } from "./fixtures/survey/repos.mjs";

const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "stealthlab-mcp.mjs");
const survey = (dir, o = {}) => runSurvey(dir, { history: false, ...o });
const page = (dir, rel = "claims.md") => readPage(path.join(dir, ".stealth", rel));
const allClaims = (dir) => {
  const out = [];
  const sdir = path.join(dir, ".stealth");
  if (fs.existsSync(path.join(sdir, "claims.md"))) out.push(...claimsOf(readPage(path.join(sdir, "claims.md"))));
  if (fs.existsSync(path.join(sdir, "claims"))) for (const f of fs.readdirSync(path.join(sdir, "claims"))) out.push(...claimsOf(readPage(path.join(sdir, "claims", f))));
  return out;
};
const statements = (dir) => allClaims(dir).map((c) => c.statement).join("\n");
/** Append an agent-written CLAIM line to a page. */
const addAgentFact = (dir, line, rel = "claims.md") => fs.appendFileSync(path.join(dir, ".stealth", rel), line + "\n");

// ---------------------------------------------------------------------------------------------
// Every repo shape resolves to exactly the expected units, and every scanner fact is verified.
// ---------------------------------------------------------------------------------------------
for (const [name, shape] of Object.entries(SHAPES)) {
  test(`shape ${name}: units resolve exactly; every scanner fact cites a real line`, () => {
    const dir = makeRepo(shape.files);
    const r = survey(dir);
    assert.deepEqual(r.units.map((u) => u.path).sort(), [...shape.units].sort(), `units for ${name}`);
    if (shape.zones) assert.deepEqual(r.zones.map((z) => z.path).sort(), shape.zones);
    assert.deepEqual(r.rejected, [], `no scanner fact may be rejected (${name})`);
    for (const c of allClaims(dir)) {
      const src = kvGet(c, "source");
      assert.ok(src.startsWith("search:") || /^.+:\d+#sha=[0-9a-f]{7}$/.test(src), `${c.id} ${src}`);
      if (!src.startsWith("search:")) {
        const [, p, line] = src.match(/^(.+):(\d+)#/);
        const lines = fs.readFileSync(path.join(dir, p), "utf8").split("\n");
        assert.ok(Number(line) >= 1 && Number(line) <= lines.length, `${c.id} cites ${p}:${line}`);
      }
    }
    // The server's own parser grammar: CLAIM|id|status|topic|scope|statement|k=v...
    for (const c of allClaims(dir)) assert.ok(c.id && c.topic && c.scope && c.statement);
  });
}

test("key facts: versions, commands and package manager come out right", () => {
  const solo = makeRepo(SHAPES["single-npm"].files);
  survey(solo);
  const s = statements(solo);
  assert.match(s, /Node\.js 20\.11\.0 is pinned/);
  assert.match(s, /`npm test` runs `jest`/);
  assert.match(s, /`npm run build` runs `tsc -p \.`/);
  assert.match(s, /engines\.node `>=20`/);
  assert.match(s, /uses npm as its package manager \(package-lock\.json is committed\)/);
  assert.match(s, /describes it as: "A single package for testing\."/);

  const mono = makeRepo(SHAPES["pnpm-negation"].files);
  survey(mono);
  const m = statements(mono);
  assert.match(m, /pnpm workspace with 4 members|pnpm workspace with 3 members/);
  assert.match(m, /`pnpm test` runs `turbo run test`/);
  assert.match(m, /In `apps\/web`, `pnpm run build` runs `next build`/);
  assert.match(m, /uses Next\.js \(`next` 14\.2\.3/);
  assert.doesNotMatch(m, /legacy`? is a package/);

  const cargo = makeRepo(SHAPES["cargo-workspace"].files);
  survey(cargo);
  const c = statements(cargo);
  assert.match(c, /Rust toolchain is pinned to 1\.78\.0/);
  assert.match(c, /requires Rust 1\.75 or newer/);
  assert.match(c, /`cargo test -p core`/);
  assert.match(c, /builds the binary `acme`/);

  const uv = makeRepo(SHAPES["python-uv-workspace"].files);
  survey(uv);
  const u = statements(uv);
  assert.match(u, /requires Python `>=3\.11`/);
  assert.match(u, /pytest is configured .* with testpaths `tests`/);
  assert.match(u, /Ruff is configured/);
  assert.match(u, /uses FastAPI \(`fastapi>=0\.110`/);

  const poly = makeRepo(SHAPES.polyglot.files);
  survey(poly);
  const p = statements(poly);
  assert.match(p, /runs `pytest -q` in `backend`/);
  assert.doesNotMatch(p, /pip install/, "CI setup lines are not facts");
  assert.match(p, /`make test` runs `cd backend && pytest -q`/);
  assert.match(p, /sets up Python 3\.12/);

  const gr = makeRepo(SHAPES["gradle-multi"].files);
  survey(gr);
  assert.match(statements(gr), /compiles for Java 21/);
  assert.match(statements(gr), /`\.\/gradlew :app:test`/);

  const net = makeRepo(SHAPES["dotnet-sln"].files);
  survey(net);
  assert.match(statements(net), /targets net8\.0/);
  assert.match(statements(net), /\.NET SDK 8\.0\.100 is pinned/);
});

test("pages: root facts in claims.md, one page per unit, ids contiguous by topic, scope=unit:<path>", () => {
  const dir = makeRepo(SHAPES["pnpm-negation"].files);
  survey(dir);
  const ui = claimsOf(page(dir, "claims/ui.md"));
  assert.ok(ui.length > 0);
  assert.ok(ui.every((c) => c.scope === "unit:packages/ui" && c.id.startsWith("R-ui-")));
  const ids = claimsOf(page(dir)).map((c) => Number(c.id.slice(2)));
  assert.deepEqual(ids, [...ids].sort((a, b) => a - b), "a new page numbers ids straight down the file");
  const idx = fs.readFileSync(path.join(dir, ".stealth", "index", "units.idx"), "utf8");
  assert.match(idx, /^UNIT\|ui\|packages\/ui\|kind=package\|eco=npm\|name=@acme\/ui\|parent=root\|template=t-[0-9a-f]{8}\|/m);
  assert.match(idx, /^AUX\|packages\/legacy\|reason=undeclared-in-workspace$/m);
  const root = fs.readFileSync(path.join(dir, ".stealth", "index", "root.idx"), "utf8");
  assert.match(root, /^units\|index\/units\.idx\|/m);
  assert.ok(Buffer.byteLength(root) <= 4096);
});

test("templates: identical sibling packages cluster; non-representatives inherit", () => {
  const dir = makeRepo(SHAPES["pnpm-negation"].files);
  const r = survey(dir);
  const ui = r.units.find((u) => u.path === "packages/ui");
  const core = r.units.find((u) => u.path === "packages/core");
  assert.equal(ui.template, core.template);
  assert.equal(core.inherits, null);
  assert.equal(ui.inherits, "packages/core");
  assert.match(fs.readFileSync(path.join(dir, ".stealth", "claims", "ui.md"), "utf8"), /\|inherits=packages\/core/);
});

// ---------------------------------------------------------------------------------------------
// Identity
// ---------------------------------------------------------------------------------------------
test("remote normalisation: SSH == HTTPS, credentials/ports/.git stripped, subgroups kept, Azure unified, mirrors mapped", () => {
  const same = ["git@github.com:Owner/Repo.git", "https://github.com/owner/repo", "https://user:tok@github.com:443/owner/repo.git/",
    "ssh://git@github.com:22/owner/repo.git", "git+https://github.com/owner/repo.git", "git@github.com-work:owner/repo.git", "git@ssh.github.com:owner/repo"];
  for (const u of same) assert.equal(normaliseRemote(u), "github.com/owner/repo", u);
  assert.equal(normaliseRemote("git@gitlab.com:group/sub/proj.git"), "gitlab.com/group/sub/proj");
  for (const u of ["git@ssh.dev.azure.com:v3/org/proj/repo", "https://org@dev.azure.com/org/proj/_git/repo", "https://org.visualstudio.com/proj/_git/repo",
    "https://org.visualstudio.com/DefaultCollection/proj/_git/repo", "ssh://git@ssh.dev.azure.com:v3/org/proj/repo"]) {
    assert.equal(normaliseRemote(u), "dev.azure.com/org/proj/repo", u);
  }
  assert.equal(normaliseRemote("https://git.corp.example/mirrors/github/owner/repo.git", { "git.corp.example/mirrors/github": "github.com" }), "github.com/owner/repo");
  for (const local of ["../other", "/srv/git/x.git", "C:\\repos\\x", "file:///srv/x.git"]) assert.equal(normaliseRemote(local), null, local);
});

test("identity chain: remote -> root commit -> stored (shallow) -> weak", () => {
  const withRemote = makeRepo({ "package.json": '{"name":"a"}\n' }, { remote: "git@github.com:Owner/Repo.git" });
  const a = survey(withRemote).identity;
  assert.match(a.repo_id, /^r:[0-9a-f]{16}$/);
  assert.equal(a.strength, "strong");
  assert.equal(a.share_public_name, false, "public name is never shared by default");
  const https = makeRepo({ "x.txt": "" }, { remote: "https://github.com/owner/repo" });
  assert.equal(survey(https).identity.repo_id, a.repo_id, "SSH and HTTPS clones are the same repository");

  const upstream = makeRepo({ "x.txt": "" }, { remote: "git@github.com:me/repo-fork.git" });
  git(upstream, "remote", "add", "upstream", "https://github.com/owner/repo.git");
  const up = survey(upstream).identity;
  assert.equal(up.repo_id, a.repo_id, "a fork's upstream wins over its origin");
  assert.equal(up.fork, true);

  const local = makeRepo({ "pyproject.toml": "[project]\nname='p'\n" });
  const c = survey(local).identity;
  assert.match(c.repo_id, /^c:/);
  const root = git(local, "rev-list", "--max-parents=0", "HEAD").trim();
  const shallow = tmpDir();
  git(local, "commit", "-q", "--allow-empty", "-m", "second");
  execFileSync("git", ["clone", "-q", "--depth", "1", `file://${local.replace(/\\/g, "/")}`, shallow], { stdio: "ignore" });
  git(shallow, "remote", "remove", "origin");
  const weak = survey(shallow).identity;
  assert.equal(weak.strength, "weak", "a shallow clone with no remote cannot trust its root commit");
  assert.match(weak.repo_id, /^p:/);
  void root;
  // A stored strong identity survives into a later shallow copy.
  const stored = tmpDir();
  execFileSync("git", ["clone", "-q", "--depth", "1", `file://${local.replace(/\\/g, "/")}`, stored], { stdio: "ignore" });
  git(stored, "remote", "remove", "origin");
  writeFiles(stored, { ".stealth/meta.json": JSON.stringify({ repo_identity: { repo_id: c.repo_id, strength: "strong", source: "root_commit" } }) });
  const st = survey(stored).identity;
  assert.equal(st.repo_id, c.repo_id);
  assert.equal(st.source, "stored");

  const nogit = makeRepo({ "Cargo.toml": "[package]\nname = \"n\"\n", "src/main.rs": "" }, { git: false });
  const n = survey(nogit);
  assert.equal(n.identity.strength, "weak");
  assert.equal(n.listing.source, "walk");
  assert.ok(n.identity.units.every((u) => u.startsWith(n.identity.repo_id + ":")));
});

// ---------------------------------------------------------------------------------------------
// File listing
// ---------------------------------------------------------------------------------------------
test("walk fallback honours .gitignore (negation, dir-only, anchored, **)", () => {
  const rules = compileGitignore("build/\n*.log\n!keep.log\n/root-only.txt\ndocs/**/draft.md\n");
  assert.ok(ignored(rules, "build", true));
  assert.ok(!ignored(rules, "build", false), "dir-only pattern does not match a file");
  assert.ok(ignored(rules, "a/b.log", false));
  assert.ok(!ignored(rules, "a/keep.log", false));
  assert.ok(ignored(rules, "root-only.txt", false));
  assert.ok(!ignored(rules, "x/root-only.txt", false));
  assert.ok(ignored(rules, "docs/a/b/draft.md", false));
  assert.ok(ignored(rules, "docs/draft.md", false));

  const dir = makeRepo({ ".gitignore": "out/\n*.tmp\n", "package.json": '{"name":"w","scripts":{"test":"node t"}}\n', "out/package.json": '{"name":"o"}\n',
    "a.tmp": "", "node_modules/x/package.json": '{"name":"nm"}\n', "sub/.gitignore": "secret.txt\n", "sub/secret.txt": "" }, { git: false });
  const r = survey(dir);
  assert.equal(r.listing.source, "walk");
  assert.deepEqual(r.units.map((u) => u.path), ["."]);
});

test("TOML subset parser: tables, arrays of tables, dotted keys, multi-line arrays, inline tables, line numbers", () => {
  const t = parseToml("# c\n[package]\nname = \"x\" # trailing\n\"quoted.key\" = 1\n\n[[bin]]\nname = \"a\"\n[[bin]]\nname = \"b\"\n\n[dependencies]\ntokio = { version = \"1\", features = [\"full\"] }\nserde.workspace = true\n\n[workspace]\nmembers = [\n  \"a\", # one\n  'b',\n]\n");
  assert.equal(t.data.package.name, "x");
  assert.equal(t.data.package["quoted.key"], 1);
  assert.deepEqual(t.data.bin.map((b) => b.name), ["a", "b"]);
  assert.equal(t.data.dependencies.tokio.version, "1");
  assert.equal(t.data.dependencies.serde.workspace, true);
  assert.deepEqual(t.data.workspace.members, ["a", "b"]);
  assert.equal(t.lineOf.get("package.name"), 3);
  assert.equal(t.lineOf.get("workspace.members"), 16);
});

// ---------------------------------------------------------------------------------------------
// The validator
// ---------------------------------------------------------------------------------------------
function agentRepo() {
  const dir = makeRepo({
    "package.json": '{\n  "name": "v",\n  "scripts": {\n    "test": "vitest run",\n    "build": "vite build"\n  }\n}\n',
    "README.md": "# v\n\nRun the dev server with `npm run dev`.\nUses Postgres 15 in CI.\n",
    "src/export/pdf.ts": "export const pdf = 1;\n",
    "docs/CRLF.md": "line one\r\nThe API listens on port 8080\r\nline three\r\n",
    "Makefile": "check:\n\tnpm test\n",
  });
  survey(dir);
  return dir;
}

test("validator: a supported agent fact is stamped with its line hash; unsupported ones are rejected", () => {
  const dir = agentRepo();
  addAgentFact(dir, "CLAIM|R-900|current|features|repository|PDF export exists in `src/export/pdf.ts`|source=src/export/pdf.ts:1|version=1");
  addAgentFact(dir, "CLAIM|R-901|current|env|repository|CI runs against Postgres 15|source=README.md:4|version=1");
  addAgentFact(dir, "CLAIM|R-902|current|runtime|repository|Uses Postgres 16|source=README.md:4|version=1");
  addAgentFact(dir, "CLAIM|R-903|current|test|repository|Tests run with `npm test`|source=package.json:4|version=1");
  addAgentFact(dir, "CLAIM|R-904|current|build|repository|The site builds with `npm run deploy`|source=package.json:5|version=1");
  addAgentFact(dir, "CLAIM|R-905|current|lint|repository|Lint with `make lint`|source=Makefile:1|version=1");
  addAgentFact(dir, "CLAIM|R-906|current|runtime|repository|Node 20|source=.nvmrc:1|version=1");
  addAgentFact(dir, "CLAIM|R-907|current|env|repository|The API listens on port 8080|source=docs/CRLF.md:2|version=1");
  // A fake token, assembled at runtime so the source never contains a token-shaped literal.
  addAgentFact(dir, `CLAIM|R-908|current|env|repository|Token ${"ghp" + "_" + "abcdefghijklmnopqrstuvwxyz0123456789"} is used|source=README.md:1|version=1`);
  addAgentFact(dir, "CLAIM|R-909|current|test|repository|`make check` runs the tests|source=Makefile:1|version=1");
  addAgentFact(dir, "CLAIM|R-910|current|build|repository|Dev server: `npm run dev`|source=README.md:3|version=1");
  const r = survey(dir, { validateOnly: true });
  const rej = Object.fromEntries(r.rejected.map((x) => [x.id, x.reason]));
  assert.deepEqual(Object.keys(rej).sort(), ["R-902", "R-904", "R-905", "R-906", "R-908", "R-910"]);
  assert.match(rej["R-902"], /"16"|16/);
  assert.match(rej["R-904"], /no script deploy/);
  assert.match(rej["R-905"], /no target lint/);
  assert.match(rej["R-906"], /not a file/);
  assert.match(rej["R-908"], /secret/);
  assert.match(rej["R-910"], /no script dev/, "a README line that names a missing script is still a false fact");
  const kept = Object.fromEntries(claimsOf(page(dir)).map((c) => [c.id, c]));
  for (const id of ["R-900", "R-901", "R-903", "R-907", "R-909"]) assert.match(kvGet(kept[id], "source"), /#sha=[0-9a-f]{7}$/, id);
  assert.match(kvGet(kept["R-907"], "source"), /^docs\/CRLF\.md:2#sha=/, "CRLF lines validate after normalisation");
  const rejectedMd = fs.readFileSync(path.join(dir, ".stealth", "survey", "rejected.md"), "utf8");
  assert.match(rejectedMd, /^REJECTED\|claims\.md\|R-904\|/m);
  assert.doesNotMatch(fs.readFileSync(path.join(dir, ".stealth", "claims.md"), "utf8"), /ghp_/, "a secret never stays on a page");
});

test("validator: moved lines are re-anchored, changed lines go stale, legacy file sha is re-stamped, case is fixed", () => {
  const dir = agentRepo();
  const blob = git(dir, "hash-object", "README.md").trim().slice(0, 7);
  addAgentFact(dir, "CLAIM|R-950|current|env|repository|CI runs against Postgres 15|source=README.md:4|version=1");
  addAgentFact(dir, `CLAIM|R-951|current|build|repository|The dev server is documented in the README|source=README.md:3#sha=${blob}|version=1`);
  addAgentFact(dir, "CLAIM|R-952|current|features|repository|PDF export exists|source=SRC/Export/PDF.ts:1|version=1");
  survey(dir, { validateOnly: true });
  let c = Object.fromEntries(claimsOf(page(dir)).map((x) => [x.id, x]));
  assert.match(kvGet(c["R-951"], "source"), /^README\.md:3#sha=[0-9a-f]{7}$/);
  assert.notEqual(kvGet(c["R-951"], "source").split("sha=")[1], blob, "file sha replaced by line sha");
  assert.match(kvGet(c["R-952"], "source"), /^src\/export\/pdf\.ts:1#sha=/);

  // Insert two lines at the top: the Postgres line moves 4 -> 6 and is re-anchored, not staled.
  fs.writeFileSync(path.join(dir, "README.md"), "# v\nnew\nnew\n\nRun the dev server with `npm run dev`.\nUses Postgres 15 in CI.\n");
  survey(dir, { validateOnly: true });
  c = Object.fromEntries(claimsOf(page(dir)).map((x) => [x.id, x]));
  assert.match(kvGet(c["R-950"], "source"), /^README\.md:6#sha=/);
  assert.equal(c["R-950"].status, "current");

  // Change the line itself: the fact goes stale (kept, flagged), and the worklist asks to re-check it.
  fs.writeFileSync(path.join(dir, "README.md"), "# v\nnew\nnew\n\nRun the dev server with `npm run dev`.\nUses Postgres 16 in CI.\n");
  const r = survey(dir, { validateOnly: true });
  c = Object.fromEntries(claimsOf(page(dir)).map((x) => [x.id, x]));
  assert.equal(c["R-950"].status, "stale");
  assert.ok(r.validation.stale >= 1);
  assert.match(fs.readFileSync(path.join(dir, ".stealth", "survey", "worklist.md"), "utf8"), /^STALE\|\.\|R-950\|/m);
});

test("validator: a statement containing '|' is repaired, not mis-parsed", () => {
  const c = parseClaim("CLAIM|R-1|current|ci|repository|CI runs `a | b` nightly|source=x:1|version=1");
  assert.equal(c.statement, "CI runs `a ¦ b` nightly", "a raw pipe in an agent's statement is kept as ¦");
  assert.equal(kvGet(c, "source"), "x:1");
  assert.ok(c.repaired);
});

test("validator: sparse checkout -> unverifiable, not wrong", () => {
  const dir = makeRepo({ "package.json": '{"name":"s","scripts":{"test":"t"}}\n', "far/away.md": "Uses Redis 7\n" });
  survey(dir);
  addAgentFact(dir, "CLAIM|R-960|current|env|repository|Uses Redis 7|source=far/away.md:1|version=1");
  survey(dir, { validateOnly: true });
  git(dir, "update-index", "--skip-worktree", "far/away.md");
  fs.rmSync(path.join(dir, "far"), { recursive: true });
  const r = survey(dir, { validateOnly: true });
  assert.deepEqual(r.rejected, []);
  const c = claimsOf(page(dir)).find((x) => x.id === "R-960");
  assert.equal(c.status, "unverifiable");
});

test("validator: a symlinked source resolves to its target once", (t) => {
  const dir = makeRepo({ "real/config.toml": "port = 8080\n", "package.json": '{"name":"l"}\n' }, { git: false });
  try { fs.symlinkSync(path.join(dir, "real", "config.toml"), path.join(dir, "link.toml")); } catch { t.skip("symlinks not permitted here"); return; }
  survey(dir);
  addAgentFact(dir, "CLAIM|R-970|current|env|repository|The port is 8080|source=link.toml:1|version=1");
  survey(dir, { validateOnly: true });
  const c = claimsOf(page(dir)).find((x) => x.id === "R-970");
  assert.match(kvGet(c, "source"), /^real\/config\.toml:1#sha=/);
});

// ---------------------------------------------------------------------------------------------
// Re-survey: stable ids, agent lines untouched, incremental
// ---------------------------------------------------------------------------------------------
test("re-survey: scanner lines keep their ids; agent lines are byte-identical; removed scripts drop their facts", () => {
  const dir = makeRepo(SHAPES["single-npm"].files);
  survey(dir);
  const agent = "CLAIM|R-800|current|decisions|repository|Uses Jest, not Vitest, because `jest` is the only test runner declared|source=package.json:7|version=1";
  addAgentFact(dir, agent);
  survey(dir);
  const before = fs.readFileSync(path.join(dir, ".stealth", "claims.md"), "utf8");
  const testId = claimsOf(page(dir)).find((c) => kvGet(c, "key") === "script:test").id;
  survey(dir);
  assert.equal(fs.readFileSync(path.join(dir, ".stealth", "claims.md"), "utf8"), before, "a no-change re-run writes nothing new");
  const pjPath = path.join(dir, "package.json");
  fs.writeFileSync(pjPath, fs.readFileSync(pjPath, "utf8").replace('"build": "tsc -p ."', '"lint": "eslint ."'));
  survey(dir);
  const after = claimsOf(page(dir));
  assert.equal(after.find((c) => kvGet(c, "key") === "script:test").id, testId);
  assert.ok(!after.some((c) => kvGet(c, "key") === "script:build"), "a deleted script's fact is removed");
  assert.ok(after.some((c) => kvGet(c, "key") === "script:lint"));
  const agentLine = fs.readFileSync(path.join(dir, ".stealth", "claims.md"), "utf8").split("\n").find((l) => l.startsWith("CLAIM|R-800|"));
  assert.ok(agentLine.startsWith(agent.split("|source=")[0]), "agent statement never rewritten");
});

test("incremental: after a one-file change only the facts citing that file are touched", () => {
  const dir = makeRepo(SHAPES.polyglot.files);
  survey(dir);
  addAgentFact(dir, "CLAIM|R-700|current|test|repository|`make test` runs pytest in backend|source=Makefile:2|version=1");
  survey(dir);
  fs.appendFileSync(path.join(dir, "backend", "pyproject.toml"), "\n[tool.mypy]\nstrict = true\n");
  const r = survey(dir);
  assert.deepEqual(r.changed, ["backend/pyproject.toml"]);
  const sources = new Map(allClaims(dir).map((c) => [c.id, kvGet(c, "source")]));
  for (const id of r.touchedFacts) assert.match(sources.get(id) || "", /^backend\/pyproject\.toml:/, `${id} does not cite the changed file`);
  assert.equal(r.merge.added, 1, "only the new [tool.mypy] fact is added");
});

// ---------------------------------------------------------------------------------------------
// Scale: lazy surveying, budgets, and the token gate (cost follows templates, not units)
// ---------------------------------------------------------------------------------------------
function bigMonorepo(n) {
  const files = { "package.json": JSON.stringify({ name: "big", private: true, scripts: { test: "turbo run test" } }), "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n",
    "pnpm-lock.yaml": "lockfileVersion: '9.0'\n", "README.md": "# big\n" + "x".repeat(4000) + "\n" };
  for (let i = 0; i < n; i++) {
    const kind = i % 3;
    files[`packages/p${i}/package.json`] = JSON.stringify({ name: `@big/p${i}`, version: "1.0.0", description: `Package number ${i} of the big monorepo`,
      scripts: kind === 2 ? { build: "vite build", test: "vitest run" } : { build: "tsup src/index.ts", test: "vitest run" },
      dependencies: { [`dep-${i}`]: "1.0.0" }, devDependencies: kind === 2 ? { vite: "^5", vitest: "^1" } : { tsup: "^8", vitest: "^1", typescript: "^5" } }, null, 2);
    files[`packages/p${i}/README.md`] = `# p${i}\n` + "y".repeat(3000) + "\n";
    files[`packages/p${i}/src/index.ts`] = "export {};\n";
    if (kind === 1) files[`packages/p${i}/tsconfig.json`] = "{}\n";
  }
  return files;
}

test("token gate: a 500-package monorepo costs < 3x a single-package repo; lazy above 40 units", () => {
  const single = survey(makeRepo(SHAPES["single-npm"].files, { git: false }));
  const big = survey(makeRepo(bigMonorepo(500), { git: false }));
  assert.equal(big.units.length, 501);
  assert.equal(big.lazy, true);
  const templates = new Set(big.units.filter((u) => u.path !== ".").map((u) => u.template));
  assert.ok(templates.size <= 3, `500 packages built 3 ways -> <= 3 templates (got ${templates.size})`);
  assert.ok(big.estTokens < 3 * single.estTokens, `big ${big.estTokens} vs single ${single.estTokens}`);
  assert.deepEqual(big.worklist.map((w) => w.unit), ["."], "lazy: only the root is surveyed until a task touches a unit");
  const touched = survey(makeRepo(bigMonorepo(60), { git: false }), { touch: ["packages/p7/src/index.ts"] });
  assert.deepEqual(touched.worklist.map((w) => w.unit), [".", "packages/p7"]);
  // Not lazy below the threshold: one representative per template, the rest inherit.
  const small = survey(makeRepo(bigMonorepo(12), { git: false }));
  assert.equal(small.lazy, false);
  const reps = small.worklist.filter((w) => w.reason === "template-rep").length;
  assert.ok(reps <= 3, `reps ${reps}`);
});

test("read budget: files over budget are dropped and the unit is marked partial", () => {
  const files = { "package.json": '{"name":"b","scripts":{"test":"t"}}\n' };
  for (const f of ["README.md", "CONTRIBUTING.md", "AGENTS.md", "Makefile", "Dockerfile", "tsconfig.json", "vite.config.ts", "jest.config.js", "turbo.json", "docker-compose.yml",
    ".env.example", "CHANGELOG.md", "src/index.ts", "src/main.ts", "docs/architecture.md", "docs/development.md", "noxfile.py", "tox.ini"]) files[f] = "z".repeat(20000) + "\n";
  const r = survey(makeRepo(files, { git: false }));
  const w = r.worklist.find((x) => x.unit === ".");
  assert.ok(w.bytes <= 96 * 1024 && w.reads <= 16);
  assert.match(fs.readFileSync(path.join(r.root, ".stealth", "survey", "worklist.md"), "utf8"), /over_budget=/);
  assert.equal(r.units[0].coverage, "partial");
});

// ---------------------------------------------------------------------------------------------
// History mining
// ---------------------------------------------------------------------------------------------
test("history: fix commits become library.md entries with diffs; non-fixes, bots and secrets are kept out", () => {
  const dir = makeRepo({ "pkg/calc.py": "def add(a, b):\n    return a - b\n", "tests/test_calc.py": "from pkg.calc import add\n", "pyproject.toml": "[project]\nname='c'\n" }, {
    commits: [
      { message: "Fix add() returning the difference (#12)", files: { "pkg/calc.py": "def add(a, b):\n    return a + b\n", "tests/test_calc.py": "from pkg.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n" } },
      { message: "Add docs", files: { "README.md": "# c\n" } },
      { message: "fix typo in readme", files: { "README.md": "# c!\n" } },
      { message: "Fix crash when config is missing", files: { "pkg/config.py": `API_KEY = '${"AKIA" + "ABCDEFGHIJKLMNOP"}'\n`, "pkg/calc.py": "def add(a, b):\n    return int(a) + int(b)\n" } },
    ],
  });
  const r = runSurvey(dir, {});
  assert.equal(r.history.added, 2);
  const lib = fs.readFileSync(path.join(dir, ".stealth", "library.md"), "utf8");
  const goals = lib.split("\n").filter((l) => l.startsWith("GOAL|"));
  assert.equal(goals.length, 2);
  assert.match(lib, /^GOAL\|L-[0-9a-f]{7}\|Fix add\(\) returning the difference \(#12\)\|unit=\.\|g=-\|outcome=historical\|verified_at=\d{4}-\d{2}-\d{2}\|route=-\|commit=[0-9a-f]{40}$/m);
  assert.match(lib, /^PROC\|L-[0-9a-f]{7}\.p1\|.*\|p=-\|solution=solutions\/L-[0-9a-f]{7}\.diff\|touches=pkg\/calc\.py#sha=[0-9a-f]{7}/m);
  assert.match(lib, /^STEP\|L-[0-9a-f]{7}\.p1:1\|action\|.*\|check=pytest tests\/test_calc\.py$/m);
  assert.match(lib, /redacted=1/);
  const sol = fs.readdirSync(path.join(dir, ".stealth", "library", "solutions"));
  assert.equal(sol.length, 2);
  for (const f of sol) assert.doesNotMatch(fs.readFileSync(path.join(dir, ".stealth", "library", "solutions", f), "utf8"), /AKIA/);
  // Incremental: a second run adds only new fixes.
  writeFiles(dir, { "pkg/calc.py": "def add(a, b):\n    return a + b  # fixed overflow\n" });
  git(dir, "add", "-A");
  git(dir, "commit", "-qm", "Fix overflow regression in add");
  const r2 = runSurvey(dir, {});
  assert.equal(r2.history.added, 1);
});

test("CLI: `stealthlab-mcp survey <dir> --json` runs end to end", () => {
  const dir = makeRepo(SHAPES["go-work"].files);
  const out = execFileSync(process.execPath, [BIN, "survey", dir, "--json", "--no-history"], { encoding: "utf8" });
  const r = JSON.parse(out);
  assert.ok(r.units.some((u) => u.path === "svc/api"));
  assert.ok(fs.existsSync(path.join(dir, ".stealth", "index", "units.idx")));
  assert.ok(fs.existsSync(path.join(dir, ".stealth", "survey", "worklist.md")));
  const meta = JSON.parse(fs.readFileSync(path.join(dir, ".stealth", "meta.json"), "utf8"));
  assert.match(meta.repo_identity.repo_id, /^c:/);
});

// ---------------------------------------------------------------------------------------------
// The prompt hook sends claims.md plus the pages of the units a prompt is about
// ---------------------------------------------------------------------------------------------
test("hook readClaims: small repo sends every page; big repo sends the pages the prompt or cwd points at", async () => {
  const { readClaims } = await import("../lib/hook.mjs");
  const small = makeRepo(SHAPES["pnpm-negation"].files);
  survey(small);
  const all = readClaims(small, "fix the login form");
  assert.match(all, /unit:packages\/ui/);
  assert.match(all, /unit:apps\/web/);
  assert.match(readClaims(path.join(small, "packages", "ui"), "x"), /unit:packages\/ui/, ".stealth is found from a sub-directory");

  const big = tmpDir();
  const filler = (unit) => Array.from({ length: 120 }, (_, i) => `CLAIM|R-${unit}-${String(i).padStart(3, "0")}|current|features|unit:pk/${unit}|${"padding ".repeat(6)}|source=search:x|version=1`).join("\n") + "\n";
  const files = { ".stealth/claims.md": "CLAIM|R-001|current|layout|repository|Big repo|source=search:x|version=1\n" };
  const rows = ["UNIT|root|.|kind=root|page=claims.md"];
  for (const u of ["alpha", "beta", "gamma", "delta"]) {
    files[`.stealth/claims/${u}.md`] = filler(u);
    rows.push(`UNIT|${u}|pk/${u}|kind=package|eco=npm|name=@big/${u}|parent=root|template=-|inherits=-|page=claims/${u}.md|coverage=scanner|declared=-`);
  }
  files[".stealth/index/units.idx"] = rows.join("\n") + "\n";
  writeFiles(big, files);
  const out = readClaims(big, "Add retries to the @big/gamma client");
  assert.match(out, /unit:pk\/gamma/);
  assert.doesNotMatch(out, /unit:pk\/alpha/);
  assert.ok(Buffer.byteLength(out) <= 64000);
  const fromCwd = readClaims(path.join(big, "pk", "delta"), "something unrelated");
  assert.match(fromCwd, /unit:pk\/delta/);
  assert.equal(readClaims(tmpDir(), "x"), "", "no .stealth anywhere: nothing sent");
});

test("CLI: unset flags keep their defaults (lazy mode still applies above 40 units)", () => {
  const dir = makeRepo(bigMonorepo(45), { git: false });
  const r = JSON.parse(execFileSync(process.execPath, [BIN, "survey", dir, "--json", "--no-history"], { encoding: "utf8", maxBuffer: 1 << 26 }));
  assert.equal(r.lazy, true);
});

test("regressions from real repos: partial-clone remotes; a deep `vendor` dir that is not vendored code", () => {
  // `git remote -v` prints "(fetch) [blob:none]" for partial clones; the remote must still be found.
  const dir = makeRepo({ "Makefile": "all:\n\techo\n", "arch/riscv/include/asm/vendor/x.h": "", "lib/vendor/y.c": "",
    "go/go.mod": "module m\n\ngo 1.22\n", "go/vendor/modules.txt": "", "vendor/z.c": "" }, { remote: "https://github.com/torvalds/linux" });
  git(dir, "config", "remote.origin.promisor", "true");
  git(dir, "config", "remote.origin.partialclonefilter", "blob:none");
  const r = survey(dir);
  assert.equal(r.identity.source, "remote");
  assert.deepEqual(r.zones.map((z) => z.path).sort(), ["go/vendor", "vendor"], "vendor/ only at the root or next to a manifest");
});

// ---------------------------------------------------------------------------------------------
// Precision regressions found by hand-checking the gold-set sample (docs/survey_eval_2026-10.md)
// ---------------------------------------------------------------------------------------------
test("precision regressions: pipes, multi-line CI scripts, indirect deps, gradle commands, mix deps, setup blocks", async () => {
  const { logicalCommands } = await import("../lib/survey/parse.mjs");
  const block = (s) => s.split("\n").map((text, i) => ({ text, line: i + 1 }));
  assert.deepEqual(logicalCommands(block("cmake -B build \\\n  -DX=1 \\\n  -DY=2\nctest\njq -n '\n  \"x\" | test(\"y\")\n'\npkg=$(ls build)\ncat <<EOF\nnpm test\nEOF\n-check\n| grep x")).map((c) => c.text),
    ["cmake -B build -DX=1 -DY=2", "ctest", "cat <<EOF"], "continuations joined; quote bodies, heredocs, assignments and flag lines skipped");

  const dir = makeRepo({
    "package.json": JSON.stringify({ name: "p", engines: { node: "^20.19.0 || >=22.12.0" } }, null, 2),
    "go/go.mod": "module m\n\ngo 1.22\n\nrequire (\n\tgithub.com/spf13/cobra v1.8.0 // indirect\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n",
    "android/build.gradle": "plugins { id 'com.android.application' }\nandroid { compileOptions { sourceCompatibility = JavaVersion.VERSION_17 } }\n",
    "android/gradlew": "",
    "app/mix.exs": "defmodule App do\n  def project, do: [app: :phoenix_thing, elixir: \"~> 1.15\"]\n  defp deps, do: [{:phoenix, \"~> 1.7\"}]\nend\n",
    "lib/mix.exs": "defmodule Lib do\n  def project, do: [app: :phoenix, elixir: \"~> 1.15\"]\nend\n",
    ".github/workflows/ci.yml": "on: push\njobs:\n  t:\n    runs-on: x\n    steps:\n      - uses: actions/setup-dotnet@v4\n        with:\n          dotnet-version: |\n            8.0.x\n            9.0.x\n      - uses: actions/setup-node@v4\n        with:\n          node-version: ${{ env.NODE }}\n      - run: npm test\n",
  });
  survey(dir);
  const s = statements(dir);
  assert.match(s, /engines\.node `\^20\.19\.0 \u00a6\u00a6 >=22\.12\.0`/, "|| is kept as ¦¦, never //");
  assert.doesNotMatch(s, /Cobra/, "an // indirect dependency is not 'used'");
  assert.match(s, /uses Gin/);
  assert.match(s, /`android` is tested with `\.\/gradlew test` from `android`/, "a standalone Gradle build runs in its own directory");
  assert.match(s, /compiles for Java 17 \(Gradle sourceCompatibility\)/);
  assert.match(s, /`app` uses Phoenix/);
  assert.doesNotMatch(s, /`lib` uses Phoenix/, "app: :phoenix is Phoenix itself, not a dependency");
  assert.match(s, /sets up \.NET 8\.0\.x, 9\.0\.x/);
  assert.doesNotMatch(s, /\$\{\{ env\.NODE \}\}/, "an unresolvable expression is not a version");
});

test("precision regressions, round 2: nested JSON keys, applied plugins only, gradle include forms, backticks, Dockerfile ARGs", async () => {
  const { jsonKeyLine, gradleSettings } = await import("../lib/survey/parse.mjs");
  const pjText = '{\n  "name": "x",\n  "funding": {\n    "type": "opencollective"\n  },\n  "type": "module"\n}\n';
  assert.equal(jsonKeyLine(pjText, ["type"]), 6, "the top-level key, not funding.type");
  assert.equal(jsonKeyLine(pjText, ["funding", "type"]), 4);
  assert.equal(jsonKeyLine(pjText, ["nope"]), 0);
  const gs = gradleSettings('pluginManagement {\n  repositories {\n    google { content { includeGroupByRegex("com\\\\.android.*") } }\n  }\n}\ninclude(":app")\ninclude \':lib\', \':core:data\'\nincludeBuild("build-logic")\n');
  assert.deepEqual(gs.projects.map((p) => p.dir), ["app", "lib", "core/data"]);
  assert.equal(gs.projects[0].line, 6);

  const dir = makeRepo({
    "settings.gradle.kts": 'include(":app")\n',
    "build.gradle.kts": "plugins {\n  alias(libs.plugins.kotlin.multiplatform) apply false\n  alias(libs.plugins.android.application) apply false\n}\n",
    "app/build.gradle.kts": "plugins {\n  alias(libs.plugins.nia.android.application)\n}\n",
    "build-logic/convention/build.gradle.kts": "gradlePlugin {\n  plugins {\n    register(\"app\") { id = libs.plugins.nia.android.application.get().pluginId }\n  }\n}\n",
    "build-logic/settings.gradle.kts": 'include(":convention")\n',
    ".github/workflows/ci.yml": "on: push\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: ctest -j `nproc` --test-dir build\n",
    "docker/Dockerfile": "ARG BASE=python:3.12-slim\nFROM ${BASE}\n",
    "docker/Dockerfile.eex": "FROM ${BUILDER_IMAGE}\n",
    "priv/templates/app/Dockerfile": "FROM elixir:1.15\n",
  });
  git(dir, "add", "-A");
  survey(dir);
  const s = statements(dir);
  assert.doesNotMatch(s, /The repository root is built as Kotlin Multiplatform/, "`apply false` declares, it does not apply");
  assert.match(s, /`app` is built as an Android application/);
  assert.doesNotMatch(s, /`build-logic\/convention` is built as an Android application/, "registering a plugin is not applying it");
  assert.match(s, /runs `` ctest -j `nproc` --test-dir build ``/, "backticks inside a command are kept");
  assert.match(s, /docker\/Dockerfile builds from base image `python:3\.12-slim` \(ARG BASE default\)/);
  assert.doesNotMatch(s, /BUILDER_IMAGE|elixir:1\.15/, "templates are not this repository's images");
});

test("held-out regressions: multi-line make recipes, github-script bodies, conditional TargetFrameworks", () => {
  const dir = makeRepo({
    "Makefile": "test:\n\techo \"mode: count\" > coverage.out\n\tfor d in $(DIRS); do go test $$d; done\n\nlint:\n\tgolangci-lint run\n",
    "go.mod": "module m\n\ngo 1.22\n",
    ".github/workflows/x.yml": "on: push\njobs:\n  t:\n    runs-on: x\n    steps:\n      - uses: actions/github-script@v7\n        with:\n          script: |\n            // check the build output\n            core.info(build)\n      - run: go test ./...\n",
    "src/App/App.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\">\n  <PropertyGroup>\n    <TargetFrameworks Condition=\" '$(OS)' == 'Windows_NT'\">net48</TargetFrameworks>\n    <TargetFrameworks>$(TargetFrameworks);net8.0;net9.0</TargetFrameworks>\n  </PropertyGroup>\n</Project>\n",
  });
  survey(dir);
  const s = statements(dir);
  assert.match(s, /`make test` is a target \(Makefile\)/, "a multi-line recipe is not summarised by its first line");
  assert.match(s, /`make lint` runs `golangci-lint run`/);
  assert.doesNotMatch(s, /check the build output/, "github-script's JavaScript is not a shell command");
  assert.match(s, /runs `go test \.\/\.\.\.`/);
  assert.match(s, /targets net8\.0, net9\.0 \(App\.csproj\)/);
});

test("validator: blank-line citations and statements that share nothing with their source are rejected", () => {
  const dir = makeRepo({ "README.md": "# app\n\nThe service talks to Redis for sessions.\n", "package.json": '{"name":"a"}\n' });
  survey(dir);
  addAgentFact(dir, "CLAIM|R-900|current|env|repository|The service stores sessions in Redis|source=README.md:2|version=1");
  addAgentFact(dir, "CLAIM|R-901|current|env|repository|Deployment targets Kubernetes clusters|source=README.md:3|version=1");
  addAgentFact(dir, "CLAIM|R-902|current|env|repository|The service stores sessions in Redis|source=README.md:3|version=1");
  const r = survey(dir, { validateOnly: true });
  const rej = Object.fromEntries(r.rejected.map((x) => [x.id, x.reason]));
  assert.match(rej["R-900"], /blank line/);
  assert.match(rej["R-901"], /nothing in the statement appears/);
  assert.equal(rej["R-902"], undefined);
});

test("history: conventional-commit types decide; bodies don't make a feature a fix; library.md stays within 64 KB", async () => {
  const { isFixCommit, LIBRARY_MAX_BYTES } = await import("../lib/survey/history.mjs");
  assert.equal(isFixCommit("fix(parser): handle CRLF"), true);
  assert.equal(isFixCommit("feat: retries", "Avoids an error when the server is down"), false);
  assert.equal(isFixCommit("perf(ingest): faster", "fixes a slow path"), false);
  assert.equal(isFixCommit("Fix crash on empty input"), true);
  assert.equal(isFixCommit("Add retries", "Fixes #42"), true);
  assert.equal(isFixCommit("Add retries", "the old code had an error"), false);
  assert.equal(isFixCommit("docs: fix typo"), false);

  const commits = Array.from({ length: 220 }, (_, i) => ({ message: `fix: handle case number ${i} in the parser module with a long descriptive subject line`,
    files: { [`src/m${i % 7}.py`]: `def f():\n    return ${i}\n` }, date: `2026-01-01T00:${String(i % 60).padStart(2, "0")}:00` }));
  const dir = makeRepo({ "pyproject.toml": "[project]\nname='h'\n", "src/m0.py": "x = 0\n" }, { commits });
  const r = runSurvey(dir, { historyMax: 220 });
  assert.equal(r.history.added, 220);
  const lib = fs.readFileSync(path.join(dir, ".stealth", "library.md"));
  assert.ok(lib.length <= LIBRARY_MAX_BYTES, `library.md is ${lib.length} bytes`);
  const archive = fs.readFileSync(path.join(dir, ".stealth", "library", "archive-historical.md"), "utf8");
  const inLib = (lib.toString().match(/^GOAL\|/gm) || []).length;
  const inArchive = (archive.match(/^GOAL\|/gm) || []).length;
  assert.equal(inLib + inArchive, 220, "nothing is lost: what does not fit is archived");
  // A re-run adds nothing new and does not re-add archived entries.
  assert.equal(runSurvey(dir, { historyMax: 220 }).history.added, 0);
});
