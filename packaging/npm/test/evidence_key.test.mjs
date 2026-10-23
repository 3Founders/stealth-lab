import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { checkHash, evidenceKey, hmacHex, keyPath, pathHash } from "../lib/evidence_key.mjs";
import { CHECK_EVIDENCE_FIELDS, clientEventId, toUploadPayload } from "../lib/evidence_upload.mjs";
import { computeStatus, parseEvidence, runChecks, runStatusCli } from "../lib/status.mjs";

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "evkey-"));
const REPO = "r:0123456789abcdef";
const EV = { target: "N-3", method: "deterministic_check", result: "pass", at: "2026-10-23T00:00:00.000Z", commit: "a".repeat(40), tree: "b".repeat(40),
  hashes: [{ path: "src/secret_module/login.py", sha: "0123456789ab" }], where: "worktree", check: "npm test -- --token my-private-flag" };
const ctx = (key, extra = {}) => ({ key, repoRef: REPO, targetKind: "plan_node", clientVersion: "1.2.3", ...extra });

test("the key is created once, with 32 random bytes, and reused", () => {
  const root = tmp();
  const k1 = evidenceKey(root);
  assert.ok(Buffer.isBuffer(k1) && k1.length === 32);
  const file = keyPath(root);
  assert.match(fs.readFileSync(file, "utf8").trim(), /^[0-9a-f]{64}$/);
  if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  const before = fs.readFileSync(file, "utf8");
  const k2 = evidenceKey(root);
  assert.ok(k1.equals(k2), "the same key on the second call");
  assert.equal(fs.readFileSync(file, "utf8"), before, "the file is not rewritten");
  assert.ok(!evidenceKey(tmp()).equals(k1), "another repository gets another random key");
});

test("the key is listed in .stealth/.gitignore before it exists, and a corrupt key file is not trusted", () => {
  const root = tmp();
  evidenceKey(root);
  const lines = fs.readFileSync(path.join(root, ".stealth", ".gitignore"), "utf8").split(/\r?\n/);
  assert.ok(lines.includes("evidence.key"));
  assert.ok(lines.includes("evidence.md.prev"));
  fs.writeFileSync(keyPath(root), "not a key\n");
  const k = evidenceKey(root);                                                  // replaced only because nothing valid was there
  assert.ok(k === null || k.length === 32);
});

test("a shared key file is used when pointed at, never created, and a missing one means no key", () => {
  const root = tmp();
  const shared = path.join(tmp(), "team.key");
  const env = { STEALTHLAB_EVIDENCE_KEY_FILE: shared };
  assert.equal(evidenceKey(root, { env }), null, "no key file: nothing is made up, nothing is created");
  assert.equal(fs.existsSync(shared), false);
  fs.writeFileSync(shared, "ab".repeat(32) + "\n");
  assert.ok(evidenceKey(root, { env }).equals(Buffer.from("ab".repeat(32), "hex")));
});

test("two different keys give different HMACs for the same command and path; one key is stable and domain-separated", () => {
  const a = Buffer.alloc(32, 1), b = Buffer.alloc(32, 2);
  assert.notEqual(checkHash(a, "npm test"), checkHash(b, "npm test"));
  assert.notEqual(pathHash(a, "src/app.js"), pathHash(b, "src/app.js"));
  assert.equal(checkHash(a, "npm test"), checkHash(a, "npm test"));
  assert.equal(checkHash(a, "npm   test "), checkHash(a, "npm test"), "whitespace is normalised");
  assert.equal(pathHash(a, ".\\src\\app.js"), pathHash(a, "src/app.js"), "separators are normalised");
  assert.notEqual(checkHash(a, "src/app.js"), pathHash(a, "src/app.js"), "a command and a path with the same text hash differently");
  assert.match(hmacHex(a, "check", "x"), /^[0-9a-f]{64}$/);
});

test("the key is never in an upload payload, in evidence.md, or in the status output", async () => {
  const root = tmp();
  fs.mkdirSync(path.join(root, ".stealth"), { recursive: true });
  fs.mkdirSync(path.join(root, "src"), { recursive: true });
  fs.writeFileSync(path.join(root, "src", "app.js"), "export function main() { return 1; }\n");
  fs.writeFileSync(path.join(root, ".stealth", "claims.md"),
    "CLAIM|R-001|current|layout|repository|The entry point is `main` in src/app.js|source=src/app.js:1#sha=" +
    "0000000|version=1\n");
  fs.writeFileSync(path.join(root, ".stealth", "run.md"), "NODE|N-1|ready|One|step=-|claims=R-001|deps=-|check=node -e 0\n");
  const key = evidenceKey(root);
  const hex = key.toString("hex");
  await runChecks(root, { exec: async () => ({ code: 0 }) });
  const evidence = fs.readFileSync(path.join(root, ".stealth", "evidence.md"), "utf8");
  assert.ok(!evidence.includes(hex), "not in evidence.md");
  const out = [];
  await runStatusCli(["--root", root, "--json"], { out: (s) => out.push(s), err: (s) => out.push(s) });
  assert.ok(!out.join("\n").includes(hex), "not in the status output");
  const payload = toUploadPayload(parseEvidence(evidence)[0], ctx(key));
  assert.ok(payload, "a payload is built");
  const text = JSON.stringify(payload);
  assert.ok(!text.includes(hex), "not in the payload");
  assert.ok(!text.includes(Buffer.from(hex, "hex").toString("base64")), "nor in base64");
  assert.ok(computeStatus(root).targets.length === 1);
});

test("the upload payload has only whitelisted keys and no command text, path, output or review text", () => {
  const key = Buffer.alloc(32, 7);
  const p = toUploadPayload(EV, ctx(key));
  assert.deepEqual(Object.keys(p).sort(), ["check_hash", "client_event_id", "client_version", "commit_sha", "hashes", "method", "observed_at",
    "repo_ref", "result", "target_id", "target_kind", "tree_id", "where_ran"].sort(), "exactly these keys");
  for (const k of Object.keys(p)) assert.ok(CHECK_EVIDENCE_FIELDS.includes(k), `${k} is whitelisted`);
  const text = JSON.stringify(p);
  for (const secret of ["npm test", "my-private-flag", "secret_module", "login.py", "src/"]) assert.ok(!text.includes(secret), `no "${secret}"`);
  assert.deepEqual(Object.keys(p.hashes[0]).sort(), ["path_hmac", "sha"]);
  assert.equal(p.hashes[0].path_hmac, pathHash(key, "src/secret_module/login.py"));
  assert.equal(p.check_hash, checkHash(key, EV.check));
  assert.equal(p.client_event_id, clientEventId(key, { ...EV, at: EV.at }), "stable per line, so an outbox retry is the same event");
  assert.notEqual(p.client_event_id, clientEventId(key, { ...EV, result: "fail" }));
});

test("a reviewer report carries both model names, and nothing is built for a bad record", () => {
  const key = Buffer.alloc(32, 7);
  const review = { ...EV, method: "independent_agent" };
  assert.equal(toUploadPayload(review, ctx(key)), null, "independence needs both model names");
  const p = toUploadPayload(review, ctx(key, { reviewerModel: "claude-sonnet-5-5", executorModel: "glm-5.3-flash" }));
  assert.equal(p.reviewer_model, "claude-sonnet-5-5");
  assert.equal(p.executor_model, "glm-5.3-flash");
  assert.equal(toUploadPayload({ ...EV, target: "fix the login bug" }, ctx(key)), null, "a target id is an id, not text");
  assert.equal(toUploadPayload(EV, { ...ctx(key), key: null }), null, "no key, no payload");
  assert.equal(toUploadPayload(EV, { ...ctx(key), repoRef: "my-private-repo" }), null);
  assert.equal(toUploadPayload({ ...EV, method: "self_report" }, ctx(key)), null);
});
