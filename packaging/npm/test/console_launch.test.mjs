import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { ensureConsole, statePath } from "../lib/console_launch.mjs";

const up = async () => ({ status: 200 });
const down = async () => { throw new Error("ECONNREFUSED"); };

function world({ withPage = true, running = false } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "kell-console-launch-"));
  const ui = path.join(home, "ui");
  if (withPage) { fs.mkdirSync(ui); fs.writeFileSync(path.join(ui, "index.html"), "<html></html>"); }
  const env = { STEALTHLAB_HOME: home, STEALTHLAB_CONSOLE_UI: ui };
  if (running) fs.writeFileSync(statePath(env), JSON.stringify({ pid: 1, port: 4321, url: "http://127.0.0.1:4321/?t=abc" }));
  const spawned = [];
  // a started helper "publishes" its address the way the real one does
  const spawnImpl = (...a) => {
    spawned.push(a);
    fs.writeFileSync(statePath(env), JSON.stringify({ pid: 2, port: 4322, url: "http://127.0.0.1:4322/?t=def" }));
    return { unref() {}, on() {} };
  };
  const opened = [];
  return { env, spawned, spawnImpl, opened, open: (u) => opened.push(u) };
}

test("MCP up and no console yet: the helper is started and the page opened", async () => {
  const w = world();
  const r = await ensureConsole({ url: "http://127.0.0.1:8765/mcp", server: "up", source: "startup", env: w.env, fetchImpl: up, spawnImpl: w.spawnImpl, open: w.open });
  assert.equal(r, "opened");
  assert.equal(w.spawned.length, 1);
  assert.deepEqual(w.spawned[0][1].slice(1, 2), ["console"]);
  assert.deepEqual(w.opened, ["http://127.0.0.1:4322/?t=def"]);
});

test("a console already running is reused, not started twice", async () => {
  const w = world({ running: true });
  const r = await ensureConsole({ url: "http://127.0.0.1:8765/mcp", server: "up", source: "startup", env: w.env, fetchImpl: up, spawnImpl: w.spawnImpl, open: w.open });
  assert.equal(r, "opened");
  assert.equal(w.spawned.length, 0);
  assert.deepEqual(w.opened, ["http://127.0.0.1:4321/?t=abc"]);
});

test("resume, clear and compact do not open another tab", async () => {
  const w = world({ running: true });
  const r = await ensureConsole({ url: "http://127.0.0.1:8765/mcp", server: "up", source: "resume", env: w.env, fetchImpl: up, spawnImpl: w.spawnImpl, open: w.open });
  assert.equal(r, "running");
  assert.equal(w.opened.length, 0);
});

test("an MCP that does not answer opens nothing", async () => {
  const w = world();
  for (const server of ["up", "remote", "off"]) {
    assert.equal(await ensureConsole({ url: "https://mcp.example.com/mcp", server, env: w.env, fetchImpl: down, spawnImpl: w.spawnImpl, open: w.open }), "not-connected");
  }
  assert.equal(await ensureConsole({ url: "http://127.0.0.1:8765/mcp", server: "no-script", env: w.env, fetchImpl: down, spawnImpl: w.spawnImpl, open: w.open }), "not-connected");
  assert.equal(w.spawned.length + w.opened.length, 0);
});

test("a hosted MCP that answers counts as connected", async () => {
  const w = world();
  assert.equal(await ensureConsole({ url: "https://mcp.example.com/mcp", server: "remote", env: w.env, fetchImpl: up, spawnImpl: w.spawnImpl, open: w.open }), "opened");
});

test("a local MCP that was just started counts as connected (the console shows it as closed until it is up)", async () => {
  const w = world();
  assert.equal(await ensureConsole({ url: "http://127.0.0.1:8765/mcp", server: "started", env: w.env, fetchImpl: up, spawnImpl: w.spawnImpl, open: w.open }), "opened");
});

test("no built page means nothing is opened; the switch turns it off", async () => {
  const w = world({ withPage: false });
  assert.equal(await ensureConsole({ url: "http://127.0.0.1:8765/mcp", server: "up", env: { ...w.env, STEALTHLAB_CONSOLE_UI: path.join(w.env.STEALTHLAB_HOME, "none") }, fetchImpl: up, spawnImpl: w.spawnImpl, open: w.open }), "no-page");
  assert.equal(await ensureConsole({ url: "http://127.0.0.1:8765/mcp", server: "up", env: { ...w.env, STEALTHLAB_CONSOLE_AUTOOPEN: "off" }, fetchImpl: up, spawnImpl: w.spawnImpl, open: w.open }), "disabled");
  assert.equal(w.spawned.length + w.opened.length, 0);
});

import { closeConsole, consoleCtl, installConsoleCommand, removeConsoleCommand, renderConsoleCommand } from "../lib/console_launch.mjs";

test("the slash command file runs console-ctl with the caller's arguments and allows only that command", () => {
  const text = renderConsoleCommand({ command: "C:\Program Files\nodejs\node.exe", args: ["C:\k\kell.mjs"] });
  assert.match(text, /^allowed-tools: Bash\("C:\\Program Files\\nodejs\\node.exe" ?|^allowed-tools: Bash\(/m);
  assert.ok(text.includes("console $ARGUMENTS status`"));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kell-cmd-"));
  const file = installConsoleCommand(dir, { command: "kell", args: [] });
  assert.ok(fs.existsSync(file) && file.endsWith(path.join("commands", "kell-console.md")));
  removeConsoleCommand(dir);
  assert.equal(fs.existsSync(file), false);
});

test("open, status and close work through consoleCtl; close stops the named process and forgets it", async () => {
  const w = world({ running: true });
  assert.match(await consoleCtl("status", { env: w.env, fetchImpl: up }), /running: http:\/\/127.0.0.1:4321/);
  const killed = [];
  assert.match(await consoleCtl("close", { env: w.env, fetchImpl: up, kill: (p) => killed.push(p) }), /Console closed/);
  assert.deepEqual(killed, [1]);
  assert.equal(fs.existsSync(statePath(w.env)), false);
  assert.match(await closeConsole({ env: w.env, fetchImpl: up }), /not running/);
  assert.match(await consoleCtl("bogus", { env: w.env }), /Usage/);
  const o = await consoleCtl("open", { env: w.env, fetchImpl: up, spawnImpl: w.spawnImpl, open: w.open });
  assert.match(o, /started and opened/);
  assert.deepEqual(w.opened, ["http://127.0.0.1:4322/?t=def"]);
});
