import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { splitLine, validatePlan, runPlanCli } from "../lib/plan_check.mjs";

function repo(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "plan-check-"));
  for (const [rel, text] of Object.entries(files)) {
    const f = path.join(root, ".stealth", rel);
    fs.mkdirSync(path.dirname(f), { recursive: true });
    fs.writeFileSync(f, text);
  }
  return root;
}

const CLAIMS = [
  "CLAIM|R-001|current|stack|repository|Express 4.19|source=package.json:12|version=1",
  "CLAIM|R-002|current|test|repository|Tests run with `npm test`|source=package.json:8|version=1",
  "CLAIM|R-003|current|layout|repository|Routes live in src/routes|source=src/routes/index.js:1|version=1",
].join("\n");
const UNIT = "CLAIM|R-api-001|current|stack|unit:api|Fastify 4|source=api/package.json:5|version=1\n";
const PROCS = [
  "PROCEDURE|P-1|proc-uuid|v2|express-rate-limit middleware|goal=Rate-limit an HTTP endpoint",
  "STEP|P-1:1|action|install express-rate-limit|locator=-|needs=node=20|check=npm ls express-rate-limit",
  "STEP|P-1:2|instruction|apply the limiter to /login|locator=-|needs=-|check=-",
].join("\n");
const GOOD_RUN = [
  "# plan",
  "NODE|N-1|done|Install the limiter|step=P-1:1|claims=R-001|deps=-|check=npm ls express-rate-limit",
  "NODE|N-2|ready|Limit /login|step=P-1:2|claims=R-001..R-003|deps=N-1|check=npm test -- login | grep -q passing",
  "NODE|N-3|skipped|Add a Redis store|step=-|claims=R-api-001|deps=-|check=-",
].join("\n");

test("splitLine keeps a shell pipe inside check=", () => {
  const { pos, kv } = splitLine("NODE|N-2|ready|do it|step=P-1:2|claims=-|deps=-|check=a | b", 4);
  assert.deepEqual(pos, ["NODE", "N-2", "ready", "do it"]);
  assert.equal(kv.check, "a | b");
  assert.equal(kv.step, "P-1:2");
});

test("a correct plan validates, unit pages included", () => {
  const r = validatePlan(repo({ "claims.md": CLAIMS, "claims/api.md": UNIT, "procedures.md": PROCS, "run.md": GOOD_RUN }));
  assert.deepEqual(r.errors, []);
  assert.equal(r.ok, true);
  assert.equal(r.nodes, 3);
  assert.equal(r.steps, 2);
});

test("broken references, statuses, checks and cycles are each reported", () => {
  const run = [
    "NODE|N-1|doing|x|step=P-1:9|claims=R-001..R-099|deps=N-2|check=npm test",
    "NODE|N-2|ready|y|step=P-2:1|claims=R-003..R-001|deps=N-1|check=-",
    "NODE|N-3|ready|z|step=-|claims=-|deps=N-7|check=ok",
    "NODE|N-3|ready|dup|step=-|claims=-|deps=-|check=ok",
  ].join("\n");
  const r = validatePlan(repo({ "claims.md": CLAIMS, "procedures.md": PROCS, "run.md": run }));
  const msgs = r.errors.map((e) => e.msg).join("\n");
  assert.equal(r.ok, false);
  for (const want of [
    /status "doing"/, /step=P-1:9 is not in procedures.md/, /claim R-099 is not/, /step=P-2:1 is not in procedures.md/,
    /runs backwards/, /N-2: check= must be concrete/, /deps names N-7/, /N-3 is declared twice/, /dependency cycle/,
  ]) assert.match(msgs, want);
});

test("procedures.md grammar is checked", () => {
  const procs = [
    "PROCEDURE|P-1|proc|2|name|goal=g",
    "STEP|P-1:1|run|do|locator=-|needs=-",
    "STEP|P-5:1|action|do|locator=-|needs=-|check=x",
  ].join("\n");
  const r = validatePlan(repo({ "procedures.md": procs, "run.md": "NODE|N-1|ready|x|step=-|claims=-|deps=-|check=t" }));
  const msgs = r.errors.map((e) => e.msg).join("\n");
  assert.match(msgs, /version "2" is not v<n>/);
  assert.match(msgs, /step kind "run"/);
  assert.match(msgs, /missing check=/);
  assert.match(msgs, /without its PROCEDURE P-5/);
});

test("warnings do not fail: a done node on an unfinished dependency", () => {
  const run = [
    "NODE|N-1|ready|a|step=-|claims=-|deps=-|check=t",
    "NODE|N-2|done|b|step=-|claims=-|deps=N-1|check=t",
  ].join("\n");
  const r = validatePlan(repo({ "run.md": run }));
  assert.equal(r.ok, true);
  assert.match(r.warnings[0].msg, /N-2 is done but its dependency N-1 is ready/);
});

test("CLI: exit 1 on errors, 0 when clean; no run.md is an error", () => {
  const lines = [];
  const io = { out: (s) => lines.push(s), err: () => {} };
  assert.equal(runPlanCli(["validate", "--root", repo({ "claims.md": CLAIMS, "procedures.md": PROCS, "run.md": GOOD_RUN.replace("R-api-001", "R-002") })], io), 0);
  assert.equal(runPlanCli(["--root", repo({ "run.md": "NODE|N-1|ready|x|step=-|claims=-|deps=-|check=-" })], io), 1);
  assert.equal(runPlanCli(["validate", "--root", repo({})], io), 1);
  assert.equal(JSON.parse(lines[0]).ok, true);
});
