import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { COMMAND, PACKAGE, isOwnCommand } from "../lib/names.mjs";

const OLD = ["stealthlab", "mcp"].join("-");

test("the package is kell-mcp, its command kell, and the old command still runs", () => {
  const pkg = JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  assert.equal(PACKAGE, "kell-mcp");
  assert.equal(COMMAND, "kell");
  assert.equal(pkg.name, PACKAGE);
  assert.equal(pkg.bin.kell, "bin/kell.mjs");
  assert.equal(pkg.bin["kell-mcp"], "bin/kell.mjs");
  assert.equal(pkg.bin[OLD], "bin/kell.mjs");
});

test("hooks written by this version and by older ones are both recognised", () => {
  for (const cmd of [
    'node "C:\\Users\\a b\\AppData\\Roaming\\npm\\node_modules\\kell-mcp\\bin\\kell.mjs" hook-prompt # mark',
    "node /usr/local/lib/node_modules/kell-mcp/bin/kell.mjs hook capture-tool",
    "kell hook-prompt",
    "npx -y kell-mcp hook-prompt",
    `node "C:\\Users\\a\\AppData\\Roaming\\npm\\node_modules\\${OLD}\\bin\\${OLD}.mjs" hook-prompt # mark`,
    `${OLD} hook-prompt`,
  ]) assert.ok(isOwnCommand(cmd), cmd);
});

test("someone else's command is not ours", () => {
  for (const cmd of ["node /home/kelly/tools/lint.mjs", "npx prettier --write .", "python kellogg.py", ""]) {
    assert.ok(!isOwnCommand(cmd), cmd);
  }
});
