import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { claudeChildEnv, INNER_CLAUDE } from "../lib/executors/common.mjs";

const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "kell.mjs");

test("every Claude Code kell starts is marked, so kell's own hooks stay out of it", () => {
  assert.equal(claudeChildEnv({ PATH: process.env.PATH })[INNER_CLAUDE], "1");
});

test("in a Claude Code kell started, the prompt hook and the other hooks do nothing (no lookup, no dispatch)", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "inner-"));
  const env = { ...process.env, STEALTHLAB_HOME: path.join(home, ".stealthlab"), STEALTHLAB_MCP_URL: "http://127.0.0.1:9/mcp",
                [INNER_CLAUDE]: "1" };
  const payload = JSON.stringify({ prompt: "is_empty_namespace returns True for all-None values; fix it", session_id: "s", cwd: home });
  for (const args of [["hook-prompt"], ["hook", "capture-stop"], ["hook", "cursor-prompt"]]) {
    const r = spawnSync(process.execPath, [BIN, ...args], { input: payload, env, encoding: "utf8", timeout: 20000 });
    assert.equal(r.status, 0, args.join(" "));
    assert.equal(r.stdout.trim(), args[1]?.startsWith("cursor-") ? "{}" : "", args.join(" "));
    assert.ok(!fs.existsSync(path.join(home, ".stealthlab", "hooks.log")), "nothing logged: the hook never ran");
  }
});
