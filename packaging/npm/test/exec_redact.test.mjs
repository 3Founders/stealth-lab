import { test } from "node:test";
import assert from "node:assert/strict";
import { isSensitivePath, redact, redactPorted, redactValue } from "../lib/exec/redact.mjs";

// Produced by running backend/app/services/trace_redaction.py::_redact_string on each input
// (2026-09-27). The JS port must give byte-identical output.
const PYTHON_VECTORS = [
  ["export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n", "export AWS_ACCESS_KEY_ID=[REDACTED:aws_access_key]\n"],
  ["token: ghp_" + "a".repeat(36), "token: [REDACTED:github_token]"],
  ["token xoxb-ABCDEFGHIJ1234567890", "token [REDACTED:slack_token]"],
  ["failed with stripe key sk_test_ABCDEFGHIJKLMNOPQRSTUV", "failed with stripe key [REDACTED:stripe_key]"],
  ["ok sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890 done", "ok [REDACTED:anthropic_key] done"],
  ["key sk-ABCDEFGHIJKLMNOPQRSTUVWX1234", "key [REDACTED:openai_key]"],
  ["bearer ABCDEFGHIJKLMNOPQRSTUVWX done", "[REDACTED:generic_bearer] done"],
  ["Authorization: Bearer eyJhbGciOi.JIUzI1NiJ9-abcdefgh", "Authorization: [REDACTED:generic_bearer]"],
  ["-----BEGIN RSA PRIVATE KEY-----\nMIIBogIBAAJ...\n-----END RSA PRIVATE KEY-----", "[REDACTED:private_key_block]"],
  ["-----BEGIN OPENSSH PRIVATE KEY-----\nreal key bytes\n", "-----BEGIN OPENSSH PRIVATE KEY-----\nreal key bytes\n"],
  ["nothing secret here", "nothing secret here"],
  ["sk-short", "sk-short"],
  ["sk-test-ABCDEFGHIJKLMNOPQRSTUVWX", "sk-test-ABCDEFGHIJKLMNOPQRSTUVWX"],
];

test("redact: the port matches the Python module's output on every vector", () => {
  for (const [input, expected] of PYTHON_VECTORS) assert.equal(redactPorted(input), expected, JSON.stringify(input));
});

test("redact: the superset agrees wherever the port already redacts fully, and closes the port's gaps", () => {
  for (const [input, expected] of PYTHON_VECTORS.slice(0, 9)) assert.equal(redact(input), expected);
  assert.equal(redact("sk-test-ABCDEFGHIJKLMNOPQRSTUVWX"), "[REDACTED:secret_key]");
  assert.equal(redact("sk-proj-abc_DEF-1234567890abcdef"), "[REDACTED:secret_key]");
  assert.equal(redact("-----BEGIN OPENSSH PRIVATE KEY-----\nreal key bytes\n"), "[REDACTED:private_key_block]");
  assert.equal(redact("OPENAI_API_KEY=abcd1234efgh5678"), "OPENAI_API_KEY=[REDACTED:secret_assignment]");
  assert.equal(redact('password: "hunter2hunter2"'), "password: [REDACTED:secret_assignment]");
  assert.equal(redact("nothing secret here, tokens=3"), "nothing secret here, tokens=3");
  assert.equal(redact("x tok-known-value-1 y", { secrets: ["tok-known-value-1"] }), "x [REDACTED:known_secret] y");
  assert.equal(redact(null), null);
});

test("redact: deep values keep their JSON shape; sensitive path rule matches the Python one", () => {
  const v = redactValue({ a: ["AKIAIOSFODNN7EXAMPLE", 3], b: { c: "ok" } });
  assert.deepEqual(v, { a: ["[REDACTED:aws_access_key]", 3], b: { c: "ok" } });
  const cases = { ".env": true, "src/.env.local": true, "C:\\Users\\x\\.ssh\\id_rsa": true, "cert.pem": true,
                  "a/id_ed25519": true, "home/.aws/credentials": true, "src/app.py": false, "environment.py": false };
  for (const [p, want] of Object.entries(cases)) assert.equal(isSensitivePath(p), want, p);
});
