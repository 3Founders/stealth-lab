import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { auditRate, parseVerdict, runAudit, startAudit } from "../lib/audit.mjs";

test("verdicts parse; anything else is no verdict", () => {
  assert.equal(parseVerdict("CORRECT: handles the empty list"), true);
  assert.equal(parseVerdict("**WRONG**: ignores negatives"), false);
  assert.equal(parseVerdict("I think it is fine"), null);
});

test("an unset audit share is 0 under node --test (0.1 otherwise); 0 turns it off", () => {
  assert.equal(auditRate({}), 0);
  assert.equal(auditRate({ STEALTHLAB_DISPATCH_AUDIT: "0.25" }), 0.25);
  assert.equal(startAudit({ instanceKey: "g.k", prompt: "p", diff: "d", model: "claude-sonnet-5-5",
                            env: { STEALTHLAB_DISPATCH_AUDIT: "0" }, rand: () => 0 }), false);
  assert.equal(startAudit({ instanceKey: "", prompt: "p", diff: "d", model: "claude-sonnet-5-5", env: {}, rand: () => 0 }),
    false, "no plan instance: nothing to report to");
});

test("an audit reports the verdict for the plan instance and records its cost", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "audit-"));
  fs.mkdirSync(path.join(home, "audits"));
  const job = path.join(home, "audits", "j.json");
  fs.writeFileSync(job, JSON.stringify({ instance_key: "g.k", prompt: "add two numbers", diff: "+return a+b", model: "claude-sonnet-5-5" }));
  const sent = [];
  const r = await runAudit(job, { env: { STEALTHLAB_HOME: home },
    runClaude: async () => ({ status: 0, stdout: JSON.stringify({ result: "WRONG: returns a-b", total_cost_usd: 0.004 }) }),
    report: async (a) => { sent.push(a); } });
  assert.deepEqual(r, { correct: false, cost_usd: 0.004 });
  assert.deepEqual(sent, [{ instance_key: "g.k", correct: false }]);
  const ledger = fs.readFileSync(path.join(home, "dispatch_costs.jsonl"), "utf-8");
  assert.match(ledger, /"what":"audit"/);
  assert.equal(fs.existsSync(job), false);
});

test("no clear verdict: nothing is reported", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "audit-"));
  const job = path.join(home, "j.json");
  fs.writeFileSync(job, JSON.stringify({ instance_key: "g.k", prompt: "p", diff: "d", model: "claude-sonnet-5-5" }));
  const sent = [];
  await runAudit(job, { env: { STEALTHLAB_HOME: home }, runClaude: async () => ({ stdout: "not json" }),
                        report: async (a) => { sent.push(a); } });
  assert.equal(sent.length, 0);
});
