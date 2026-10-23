import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  appendEvidence, computeStatus, fileShas, parseEvidence, renderEvidence, runChecks, runShell, runStatusCli,
} from "../lib/status.mjs";
import { lineHash } from "../lib/survey/validate.mjs";

const APP_LINES = ["export function main() { return 1; }", "// helper", "// more", "// tail", ""];

function toy({ run, claims, library, app = APP_LINES } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "status-"));
  fs.mkdirSync(path.join(root, "src"), { recursive: true });
  fs.mkdirSync(path.join(root, ".stealth"), { recursive: true });
  fs.writeFileSync(path.join(root, "src", "app.js"), app.join("\n"));
  const claim = (id) => `CLAIM|${id}|current|layout|repository|The entry point is \`main\` in src/app.js|source=src/app.js:1#sha=${lineHash(app[0])}|version=1`;
  fs.writeFileSync(path.join(root, ".stealth", "claims.md"), (claims ?? [claim("R-001")]).map((c) => (c.startsWith("CLAIM") ? c : claim(c))).join("\n") + "\n");
  if (run) fs.writeFileSync(path.join(root, ".stealth", "run.md"), run.join("\n") + "\n");
  if (library) fs.writeFileSync(path.join(root, ".stealth", "library.md"), library.join("\n") + "\n");
  return root;
}
const statusOf = (root, id) => computeStatus(root).targets.find((t) => t.id === id);
const edit = (root, rel, fn) => { const f = path.join(root, rel); fs.writeFileSync(f, fn(fs.readFileSync(f, "utf8"))); };
const passAll = async () => ({ code: 0 });
const failAll = async () => ({ code: 1 });
const evidenceText = (root) => fs.readFileSync(path.join(root, ".stealth", "evidence.md"), "utf8");

test("a node with check=- is open, and a done node with no evidence is open", () => {
  const root = toy({ run: [
    "NODE|N-1|ready|No check at all|step=-|claims=R-001|deps=-|check=-",
    "NODE|N-2|done|Done by hand|step=-|claims=R-001|deps=-|check=node -e 0",
  ] });
  assert.equal(statusOf(root, "N-1").status, "open");
  assert.match(statusOf(root, "N-1").why, /no check/);
  assert.equal(statusOf(root, "N-2").status, "open");
  assert.match(statusOf(root, "N-2").why, /done by hand/);
});

test("a pass recorded at the current hashes is verified; editing a cited file afterwards makes it expired", async () => {
  const root = toy({ run: ["NODE|N-1|ready|Check the entry|step=-|claims=R-001|deps=-|check=node -e 0"] });
  await runChecks(root, { exec: passAll });
  assert.equal(statusOf(root, "N-1").status, "verified");
  edit(root, "src/app.js", (t) => t.replace("// tail", "// tail changed"));    // line 1 (the claim) is untouched
  const after = statusOf(root, "N-1");
  assert.equal(after.status, "expired");
  assert.match(after.why, /changed/);
});

test("a cited claim the validator no longer keeps makes the node expired", async () => {
  const root = toy({ run: ["NODE|N-1|ready|Check the entry|step=-|claims=R-001|deps=-|check=node -e 0"] });
  await runChecks(root, { exec: passAll });
  assert.equal(statusOf(root, "N-1").status, "verified");
  edit(root, "src/app.js", (t) => t.replace("main", "start"));                 // the cited line itself changes
  const after = statusOf(root, "N-1");
  assert.equal(after.status, "expired");
  assert.match(after.why, /claim R-001/);
});

test("a claim already marked stale on the page expires the node", () => {
  const stale = `CLAIM|R-001|stale|layout|repository|The entry point is \`main\` in src/app.js|source=src/app.js:1#sha=${lineHash(APP_LINES[0])}|version=1`;
  const root = toy({ claims: [stale], run: ["NODE|N-1|ready|Check|step=-|claims=R-001|deps=-|check=node -e 0"] });
  assert.equal(statusOf(root, "N-1").status, "expired");
});

test("a fail on the current code is failed; a pass and a fail at the same hashes are contested", async () => {
  const root = toy({ run: ["NODE|N-1|ready|Check the entry|step=-|claims=R-001|deps=-|check=node -e 0"] });
  await runChecks(root, { exec: failAll });
  assert.equal(statusOf(root, "N-1").status, "failed");
  await runChecks(root, { exec: passAll });                                    // same code, now a pass too
  assert.equal(statusOf(root, "N-1").status, "contested");
});

test("a node whose dependency is not verified stays open, and becomes verified with it", async () => {
  const root = toy({ run: [
    "NODE|N-1|ready|First|step=-|claims=R-001|deps=-|check=node -e 0",
    "NODE|N-2|ready|Second|step=-|claims=R-001|deps=N-1|check=node -e 1",
  ] });
  await runChecks(root, { only: ["N-2"], exec: passAll });
  const open = statusOf(root, "N-2");
  assert.equal(open.status, "open");
  assert.match(open.why, /N-1 is open/);
  await runChecks(root, { only: ["N-1"], exec: passAll });
  assert.equal(statusOf(root, "N-2").status, "verified");
});

test("a dependency cycle does not hang and is never verified", async () => {
  const root = toy({ run: [
    "NODE|N-1|ready|A|step=-|claims=R-001|deps=N-2|check=node -e 0",
    "NODE|N-2|ready|B|step=-|claims=R-001|deps=N-1|check=node -e 1",
  ] });
  await runChecks(root, { exec: passAll });
  assert.notEqual(statusOf(root, "N-1").status, "verified");
  assert.notEqual(statusOf(root, "N-2").status, "verified");
});

test("a library entry with touches hashes is verified until a touched file changes, then expired", () => {
  const root = toy();
  const sha = fileShas(root, ["src/app.js"]).get("src/app.js").slice(0, 12);
  fs.writeFileSync(path.join(root, ".stealth", "library.md"), [
    "GOAL|L-aaaa1111|Fix the entry point|unit=.|g=-|outcome=pass|status=current|verified_at=2026-10-20|route=-|tags=-",
    `PROC|L-aaaa1111.p1|fix|p=-|solution=-|touches=src/app.js#sha=${sha}`,
    "STEP|L-aaaa1111.p1:1|action|edit main|check=-",
  ].join("\n") + "\n");
  assert.equal(statusOf(root, "L-aaaa1111").status, "verified");
  edit(root, "src/app.js", (t) => t + "// edited\n");
  const after = statusOf(root, "L-aaaa1111");
  assert.equal(after.status, "expired");
  assert.match(after.why, /src\/app\.js changed/);
});

test("a library entry without recorded hashes, or only historical, is open", () => {
  const root = toy({ library: [
    "GOAL|L-bbbb2222|No hashes|unit=.|g=-|outcome=pass|status=current|verified_at=2026-10-20|route=-|tags=-",
    "PROC|L-bbbb2222.p1|fix|p=-|solution=-|touches=-",
    "GOAL|L-cccc3333|Old|unit=.|g=-|outcome=historical|status=current|verified_at=2026-10-20|route=-|tags=-",
    "PROC|L-cccc3333.p1|fix|p=-|solution=-|touches=src/app.js#sha=abcdef123456",
  ] });
  assert.equal(statusOf(root, "L-bbbb2222").status, "open");
  assert.equal(statusOf(root, "L-cccc3333").status, "expired");               // its recorded hash does not match the file
});

test("without --run nothing is executed; --run appends exactly one evidence line per check", async () => {
  const root = toy({ run: [
    "NODE|N-1|ready|One|step=-|claims=R-001|deps=-|check=node -e 0",
    "NODE|N-2|ready|Two|step=-|claims=R-001|deps=-|check=node -e 1",
    "NODE|N-3|ready|No check|step=-|claims=R-001|deps=-|check=-",
  ] });
  let calls = 0;
  const exec = async () => { calls += 1; return { code: 0 }; };
  const out = [];
  assert.equal(await runStatusCli(["--root", root, "--json"], { out: (s) => out.push(s), err: () => {}, exec }), 0);
  assert.equal(calls, 0, "no child process without --run");
  assert.equal(fs.existsSync(path.join(root, ".stealth", "evidence.md")), false);
  assert.equal(JSON.parse(out[0]).targets.length, 3);

  assert.equal(await runStatusCli(["--root", root, "--run", "--json"], { out: (s) => out.push(s), err: () => {}, exec }), 0);
  assert.equal(calls, 2, "one run per check, none for check=-");
  assert.equal(parseEvidence(evidenceText(root)).length, 2);
  assert.equal(evidenceText(root).split("\n").filter((l) => l.startsWith("EVIDENCE|")).length, 2);
});

test("evidence.md never holds check output or a secret-looking value", async () => {
  const secretCheck = "curl -H 'x-api-key: sk-ant-abcdefghijklmnopqrstuvwx' https://example.invalid";
  const root = toy({ run: [
    "NODE|N-1|ready|Prints output|step=-|claims=R-001|deps=-|check=node -e \"console.log(['LEAK','ED-OUT','PUT'].join(''))\"",
    `NODE|N-2|ready|Has a secret in the command|step=-|claims=R-001|deps=-|check=${secretCheck}`,
  ] });
  const info = await runChecks(root, { inPlace: true });                       // the real runner, real child process
  assert.equal(info.appended, 1);
  assert.equal(info.skipped.length, 1);
  assert.match(info.skipped[0].why, /secret/);
  const text = evidenceText(root);
  assert.ok(!text.includes("LEAKED-OUTPUT"), "no output in the evidence file");
  assert.ok(!text.includes("sk-ant-"), "no secret in the evidence file");
  assert.equal(renderEvidence({ target: "N-1", result: "pass", check: secretCheck }), null);
  assert.equal((await runShell(`"${process.execPath}" -e "process.exit(3)"`)).code, 3);
});

test("evidence lines round-trip, keep a pipe inside check=, and skip lines that are not evidence", () => {
  const line = renderEvidence({ target: "N-1", method: "deterministic_check", result: "pass", at: "2026-10-23T00:00:00.000Z", commit: "abc123",
    hashes: [{ path: "src/app.js", sha: "0123456789abcdef" }], check: "npm test | grep -q passing" });
  const [e] = parseEvidence("# header\nnot evidence\n" + line + "\nEVIDENCE|N-2|bogus|pass|at=x\n");
  assert.equal(e.target, "N-1");
  assert.equal(e.check, "npm test | grep -q passing");
  assert.deepEqual(e.hashes, [{ path: "src/app.js", sha: "0123456789ab" }]);
  assert.equal(parseEvidence(line + "\nEVIDENCE|N-2|bogus|pass|at=x\n").length, 1);
});

test("a missing .stealth folder or a missing file fails open to open/empty, never verified", () => {
  const empty = fs.mkdtempSync(path.join(os.tmpdir(), "status-empty-"));
  assert.equal(computeStatus(empty).targets.length, 0);
  const root = toy({ run: ["NODE|N-1|done|Gone file|step=-|claims=R-001|deps=-|check=node -e 0"] });
  fs.rmSync(path.join(root, "src", "app.js"));
  assert.notEqual(statusOf(root, "N-1").status, "verified");
  assert.equal(appendEvidence(root, []), 0);
});
