import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { runChecks, computeStatus, parseEvidence } from "../lib/status.mjs";
import { recordAcceptedEvidence, repoRefOf, uploadRecords } from "../lib/evidence_record.mjs";
import { CHECK_EVIDENCE_FIELDS, uploadEnabled, validateUploadPayload } from "../lib/evidence_upload.mjs";
import { evidenceKey } from "../lib/evidence_key.mjs";
import { flushOutbox, reportCheckEvidence } from "../lib/exec/evidence.mjs";
import { mockHosted, outboxFiles, testEnv, tmpDir } from "./fixtures/exec/helpers.mjs";

const README = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "README.md"), "utf8");
const SECRET_COMMAND = "node check.mjs --private-flag-xyz";
const SECRET_PATH = "src/secret_module/app.js";
const REPO_REF = "r:0123456789abcdef";
const ON = { STEALTHLAB_EVIDENCE_UPLOAD: "on" };
const OK = JSON.stringify({ recorded: true, duplicate: false, id: "e-1", state: "checked" });

function repo({ library = false } = {}) {
  const root = tmpDir("ev-send-");
  fs.mkdirSync(path.join(root, ".stealth"), { recursive: true });
  fs.mkdirSync(path.join(root, "src", "secret_module"), { recursive: true });
  const line1 = "export function main() { return 1; }";
  fs.writeFileSync(path.join(root, SECRET_PATH), line1 + "\n// LEAK-ME-IN-OUTPUT\n");
  fs.writeFileSync(path.join(root, ".stealth", "meta.json"), JSON.stringify({ repo_identity: { repo_id: REPO_REF, strength: "strong" } }));
  fs.writeFileSync(path.join(root, ".stealth", "claims.md"),
    `CLAIM|R-001|current|layout|repository|The entry point is \`main\` in ${SECRET_PATH}|source=${SECRET_PATH}:1#sha=${shaOfLine(line1)}|version=1\n`);
  fs.writeFileSync(path.join(root, ".stealth", "run.md"), `NODE|N-1|ready|One|step=-|claims=R-001|deps=-|check=${SECRET_COMMAND}\n`);
  if (library) {
    fs.writeFileSync(path.join(root, ".stealth", "library.md"), [
      "GOAL|L-aaaa1111|Fix the entry point|unit=.|g=-|outcome=pass|status=current|verified_at=2026-10-20|route=-|tags=-",
      `PROC|L-aaaa1111.p1|fix|p=-|solution=-|touches=${SECRET_PATH}#sha=${"0".repeat(12)}`,
      "STEP|L-aaaa1111.p1:1|action|edit main|check=-",
    ].join("\n") + "\n");
  }
  return root;
}
import crypto from "node:crypto";
function shaOfLine(line) { return crypto.createHash("sha1").update(line).digest("hex").slice(0, 7); }

const passAll = async () => ({ code: 0 });
const bodyOf = (hosted) => JSON.stringify(hosted.calls);

test("upload is off unless STEALTHLAB_EVIDENCE_UPLOAD=on: nothing is built, queued or sent", async () => {
  assert.equal(uploadEnabled({}), false);
  assert.equal(uploadEnabled({ STEALTHLAB_EVIDENCE_UPLOAD: "off" }), false);
  assert.equal(uploadEnabled({ STEALTHLAB_EVIDENCE_UPLOAD: "ON" }), true);
  const hosted = await mockHosted(() => ({ text: OK }));
  try {
    const root = repo();
    const env = testEnv({ url: hosted.url });
    const res = await runChecks(root, { exec: passAll, env });
    assert.equal(res.appended, 1, "the local evidence is written regardless");
    assert.equal(res.upload, undefined);
    assert.equal(hosted.calls.length, 0, "no request");
    assert.equal(outboxFiles(env).length, 0, "nothing queued");
    assert.equal(fs.existsSync(path.join(root, ".stealth", "evidence.key")), false, "not even a key is made while upload is off");
  } finally { await hosted.close(); }
});

test("with upload on, status --run reports exactly the whitelisted keys and none of the command, path, output or key", async () => {
  const hosted = await mockHosted(() => ({ text: OK }));
  try {
    const root = repo();
    const env = testEnv({ url: hosted.url, extra: ON });
    const res = await runChecks(root, { exec: passAll, env });
    assert.deepEqual(res.upload, { built: 1, queued: 0, sent: 1, skipped: 0 });
    const sent = hosted.calls.filter((c) => c.name === "report_check_evidence");
    assert.equal(sent.length, 1);
    assert.deepEqual(Object.keys(sent[0].args), ["evidence"], "one argument");
    const keys = Object.keys(sent[0].args.evidence).sort();
    // exactly these keys; commit_sha and tree_id exist only inside a git work tree (this toy folder is not one), client_version when known
    const required = ["check_hash", "client_event_id", "hashes", "method", "observed_at", "repo_ref", "result", "target_id", "target_kind", "where_ran"];
    const optional = ["client_version", "commit_sha", "tree_id"];
    for (const k of required) assert.ok(keys.includes(k), `${k} is sent`);
    for (const k of keys) assert.ok(required.includes(k) || optional.includes(k), `${k} is a whitelisted optional key`);
    for (const k of keys) assert.ok(CHECK_EVIDENCE_FIELDS.includes(k));
    assert.equal(sent[0].args.evidence.repo_ref, REPO_REF);
    assert.equal(sent[0].args.evidence.target_kind, "plan_node");
    assert.deepEqual(Object.keys(sent[0].args.evidence.hashes[0]).sort(), ["path_hmac", "sha"]);
    const wire = bodyOf(hosted);
    const keyHex = evidenceKey(root, { env }).toString("hex");
    for (const forbidden of [SECRET_COMMAND, "private-flag", SECRET_PATH, "secret_module", "LEAK-ME-IN-OUTPUT", keyHex, root, "node check.mjs"]) {
      assert.ok(!wire.includes(forbidden), `not on the wire: ${forbidden}`);
    }
    assert.ok(fs.readFileSync(path.join(root, ".stealth", "evidence.md"), "utf8").includes(SECRET_COMMAND), "the command stays in the LOCAL file only");
    assert.ok(fs.existsSync(path.join(root, ".stealth", "evidence.key")));
    assert.ok(fs.readFileSync(path.join(root, ".stealth", ".gitignore"), "utf8").includes("evidence.key"));
  } finally { await hosted.close(); }
});

test("offline: the report is queued in the outbox, a later flush sends the same event, and the status is untouched", async () => {
  let mode = "down";
  const hosted = await mockHosted(() => (mode === "down" ? { status: 503, text: "unavailable" } : { text: OK }));
  try {
    const root = repo();
    const env = testEnv({ url: hosted.url, extra: ON });
    const before = JSON.stringify(computeStatus(root).targets);
    const res = await runChecks(root, { exec: passAll, env });
    assert.deepEqual(res.upload, { built: 1, queued: 1, sent: 0, skipped: 0 });
    const queued = outboxFiles(env);
    assert.equal(queued.length, 1);
    assert.equal(queued[0].tool, "report_check_evidence");
    assert.ok(!JSON.stringify(queued).includes(SECRET_COMMAND) && !JSON.stringify(queued).includes("secret_module"), "the outbox holds no command or path either");
    const eventId = queued[0].args.evidence.client_event_id;
    assert.notEqual(JSON.stringify(computeStatus(root).targets), before, "the local run did change the status (it is now verified)...");
    assert.equal(computeStatus(root).targets[0].status, "verified", "...and the failed upload changed nothing about it");
    mode = "up";
    const flushed = await flushOutbox({ env });
    assert.equal(flushed.sent, 1);
    assert.equal(outboxFiles(env).length, 0);
    const sent = hosted.calls.filter((c) => c.name === "report_check_evidence").at(-1);
    assert.equal(sent.args.evidence.client_event_id, eventId, "the same event, so the server's idempotency applies");
  } finally { await hosted.close(); }
});

test("signed out: queued, not lost; a refusal is final and a rate-limit error is retried", async () => {
  const root = repo();
  const out = testEnv({ url: "http://127.0.0.1:9/mcp", token: "", extra: ON });
  const res = await runChecks(root, { exec: passAll, env: out });
  assert.equal(res.upload.queued, 1);
  assert.equal(outboxFiles(out).length, 1);

  let reply = { text: "REFUSED: fields not allowed: ['x']" };
  const hosted = await mockHosted(() => reply);
  try {
    const env = testEnv({ url: hosted.url, extra: ON });
    const payload = (await captureOne(root, env));
    const refused = await reportCheckEvidence(payload, { env });
    assert.equal(refused.reported, false);
    assert.match(refused.rejected, /REFUSED/);
    assert.equal(outboxFiles(env).length, 0);
    assert.equal(outboxFiles(env, "rejected").length, 1, "final: moved to rejected/, not retried");
    reply = { isError: true, text: "Error executing tool: rate limit -- retry in 30s" };
    const limited = await reportCheckEvidence(payload, { env });
    assert.equal(limited.queued, true, "a rate limit is an error, so it waits");
    assert.equal(outboxFiles(env).length, 1);
  } finally { await hosted.close(); }
});

async function captureOne(root, env) {
  const hosted = await mockHosted(() => ({ text: OK }));
  try {
    const e2 = { ...env, STEALTHLAB_MCP_URL: hosted.url };
    await runChecks(root, { exec: passAll, env: e2 });
    return hosted.calls.find((c) => c.name === "report_check_evidence").args.evidence;
  } finally { await hosted.close(); }
}

test("an upload that fails for any reason never fails the run: a bad key file, a network error, a malformed record", async () => {
  const root = repo();
  const env = testEnv({ url: "http://127.0.0.1:9/mcp", extra: ON });
  fs.writeFileSync(path.join(root, ".stealth", "evidence.key"), "not a key\n");
  const res = await runChecks(root, { exec: passAll, env });
  assert.equal(res.appended, 1);
  assert.equal(res.upload.skipped, 1, "no usable key: nothing built, nothing sent");
  assert.equal(outboxFiles(env).length, 0);
  const weird = await uploadRecords(root, [{ target: "fix the bug", method: "deterministic_check", result: "pass", at: "x", check: "c", hashes: [], targetKind: "plan_node" }],
    { env, log: () => {} });
  assert.equal(weird.built, 0);
  assert.equal(computeStatus(root).targets[0].status, "verified", "the status is computed from the local evidence alone");
  assert.equal(parseEvidence(fs.readFileSync(path.join(root, ".stealth", "evidence.md"), "utf8")).length, 1);
});

test("a malformed or extra-keyed payload is held, not queued or sent, and the outbox applies the whitelist itself", async () => {
  const hosted = await mockHosted(() => ({ text: OK }));
  try {
    const env = testEnv({ url: hosted.url, extra: ON });
    const bad = await reportCheckEvidence({ repo_ref: REPO_REF, command: SECRET_COMMAND }, { env });
    assert.equal(bad.reported, false);
    assert.ok(bad.held);
    assert.equal(hosted.calls.length, 0);
    assert.equal(outboxFiles(env).length, 0);
    assert.equal(outboxFiles(env, "held").length, 1);
    assert.ok(!JSON.stringify(outboxFiles(env, "held")).includes(SECRET_COMMAND), "the offending value is not even kept");
    assert.equal(validateUploadPayload({ repo_ref: REPO_REF, target_kind: "plan_node", target_id: "N-1", method: "deterministic_check",
      result: "pass", observed_at: "2026-10-23T00:00:00Z", check_hash: "a".repeat(64), client_event_id: "6f1f5f6e-7d7e-4f0e-9d3a-0a7b6c5d4e3f", hashes: [], path: "src/a.js" }), null);
  } finally { await hosted.close(); }
});

test("dispatch evidence: the check and the strong reviewer's verdict become evidence on the library entry, queued with no network", async () => {
  const hosted = await mockHosted(() => ({ text: OK }));
  try {
    const root = repo({ library: true });
    const env = testEnv({ url: hosted.url, extra: ON });
    const out = await recordAcceptedEvidence({ root, entryId: "L-aaaa1111", check: "pytest tests/test_x.py -q", reviewerFixes: true,
      reviewerModel: "claude-sonnet-5-5", executorModel: "glm-5.3-flash", env });
    assert.equal(out.lines, 2);
    const lines = parseEvidence(fs.readFileSync(path.join(root, ".stealth", "evidence.md"), "utf8"));
    assert.deepEqual(lines.map((l) => [l.target, l.method, l.result]), [["L-aaaa1111", "deterministic_check", "pass"], ["L-aaaa1111", "independent_agent", "pass"]]);
    assert.deepEqual(lines[0].hashes.map((h) => h.path), [SECRET_PATH], "recorded at the files the entry touched, read from the parsed library entry");
    assert.equal(hosted.calls.length, 0, "dispatch never waits on the network: it only queues");
    const queued = outboxFiles(env);
    assert.equal(queued[0].args.evidence.hashes.length, 1);
    assert.equal(queued.length, 2);
    const reviewer = queued.map((q) => q.args.evidence).find((e) => e.method === "independent_agent");
    assert.equal(reviewer.reviewer_model, "claude-sonnet-5-5");
    assert.equal(reviewer.executor_model, "glm-5.3-flash");
    assert.equal(reviewer.target_kind, "library_entry");
    assert.ok(!JSON.stringify(queued).includes("pytest tests/test_x.py"), "the check command is not in the queued report");
    // the next flush sends them
    assert.equal((await flushOutbox({ env })).sent, 2);
    assert.equal(hosted.calls.filter((c) => c.name === "report_check_evidence").length, 2);
  } finally { await hosted.close(); }
});

test("dispatch evidence: no reviewer line when the reviewer did not say fixes, or is the model that wrote the answer; none uploaded when off", async () => {
  const root = repo({ library: true });
  const env = testEnv({ url: "http://127.0.0.1:9/mcp", extra: ON });
  const notFixes = await recordAcceptedEvidence({ root, entryId: "L-aaaa1111", check: "pytest -q", reviewerFixes: false, reviewerModel: "claude-sonnet-5-5", executorModel: "glm-5.3-flash", env });
  assert.equal(notFixes.lines, 1);
  const same = await recordAcceptedEvidence({ root, entryId: "L-aaaa1111", check: "pytest -q", reviewerFixes: true, reviewerModel: "GLM-5.3-Flash", executorModel: "glm-5.3-flash", env });
  assert.equal(same.lines, 1, "a model does not review its own answer");
  const off = testEnv({ url: "http://127.0.0.1:9/mcp" });
  const local = await recordAcceptedEvidence({ root, entryId: "L-aaaa1111", check: "pytest -q", reviewerFixes: true, reviewerModel: "claude-sonnet-5-5", executorModel: "glm-5.3-flash", env: off });
  assert.equal(local.lines, 2, "the local evidence is written when upload is off");
  assert.equal(outboxFiles(off).length, 0, "and nothing is queued");
  const noEntry = await recordAcceptedEvidence({ root, entryId: "", check: "x", env });
  assert.equal(noEntry.lines, 0);
});

test("the repository identity comes from the survey's meta.json and is never a name", () => {
  const root = repo();
  assert.equal(repoRefOf(root), REPO_REF);
  fs.writeFileSync(path.join(root, ".stealth", "meta.json"), JSON.stringify({ repo_identity: { repo_id: "github.com/acme/private", public_name: "acme/private" } }));
  assert.equal(repoRefOf(root), null);
});

test("the README states that upload is off by default and what it sends and never sends", () => {
  assert.match(README, /### Check evidence: recorded here always, uploaded only if you switch it on/);
  assert.match(README, /\*\*Upload is OFF by default\.\*\*/);
  assert.match(README, /STEALTHLAB_EVIDENCE_UPLOAD=on/);
  assert.match(README, /report_check_evidence/);
  assert.match(README, /\*\*Never sent:\*\* the check command, a path, the output/);
  assert.match(README, /STEALTHLAB_EVIDENCE_KEY_FILE/);
});
