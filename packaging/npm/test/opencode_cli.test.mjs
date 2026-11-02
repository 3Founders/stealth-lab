import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { runOpenCodeCli } from "../lib/opencode_cli.mjs";

test("kell opencode: the same arguments go to opencode, and its exit code comes back", async () => {
  const calls = [];
  const fake = (exe, args, opts) => { calls.push({ exe, args, opts }); return { status: 7 }; };
  const code = await runOpenCodeCli(["run", "--standalone", "hello"], { env: { STEALTHLAB_HOME: path.join(os.tmpdir(), "kell-none") }, spawn: fake, stderr: { write: () => {} } });
  assert.equal(code, 7, "the child's exit code is returned");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].exe, "opencode");
  assert.deepEqual(calls[0].args, ["run", "--standalone", "hello"]);
  assert.equal(calls[0].opts.stdio, "inherit");
});

test("kell opencode: a missing sign-in (nothing to refresh) never blocks the run", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "kell-oc-cli-"));
  const calls = [];
  const code = await runOpenCodeCli(["--version"], { env: { STEALTHLAB_HOME: path.join(root, "kell") }, spawn: (e, a, o) => { calls.push(1); return { status: 0 }; }, stderr: { write: () => {} } });
  assert.equal(code, 0);
  assert.equal(calls.length, 1, "opencode ran despite nothing to refresh");
});

const onPath = spawnSync(process.platform === "win32" ? "where.exe" : "which", [process.platform === "win32" ? "opencode" : "opencode"], { encoding: "utf8" }).status === 0;
test("kell opencode end to end: --version passes through and exits 0", { skip: !onPath && "opencode is not on PATH" }, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "kell-oc-cli-"));
  const out = execFileSync(process.execPath, [path.join(import.meta.dirname, "..", "bin", "kell.mjs"), "opencode", "--version"],
                           { encoding: "utf8", env: { ...process.env, STEALTHLAB_HOME: path.join(root, "kell") }, timeout: 60000 });
  assert.match(out, /\d+\.\d+/, "opencode's version came through the pass-through");
});
