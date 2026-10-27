import test from "node:test";
import assert from "node:assert/strict";
import { ensureServer } from "../lib/ensure_server.mjs";

const up = async () => ({ status: 200 });
const down = async () => { throw new Error("ECONNREFUSED"); };
const spawnSpy = () => { const calls = []; return { calls, fn: (...a) => { calls.push(a); return { unref() {} }; } }; };
import os from "node:os";
import path from "node:path";
const HOME = path.join(os.tmpdir(), "kell-ensure-server-test");   // keep the hook log out of the real ~/.stealthlab
const env = (extra = {}) => ({ STEALTHLAB_HOME: HOME, STEALTHLAB_SERVER_START: process.execPath, ...extra });   // any existing file stands in for the script

test("a healthy local server is left alone", async () => {
  const s = spawnSpy();
  assert.equal(await ensureServer({ url: "http://127.0.0.1:8765/mcp", env: env(), fetchImpl: up, spawnImpl: s.fn, platform: "win32" }), "up");
  assert.equal(s.calls.length, 0);
});

test("a hosted URL is never started", async () => {
  const s = spawnSpy();
  assert.equal(await ensureServer({ url: "https://mcp.example.com/mcp", env: env(), fetchImpl: down, spawnImpl: s.fn, platform: "win32" }), "remote");
  assert.equal(s.calls.length, 0);
});

test("a down local server is started detached on its port", async () => {
  const s = spawnSpy();
  assert.equal(await ensureServer({ url: "http://127.0.0.1:8765/mcp", env: env(), fetchImpl: down, spawnImpl: s.fn, platform: "win32" }), "started");
  const [cmd, args, opts] = s.calls[0];
  assert.equal(cmd, "powershell.exe");
  assert.deepEqual(args.slice(-2), ["-Port", "8765"]);
  assert.equal(opts.detached, true);
});

test("the switch turns it off, and a missing script or platform does nothing", async () => {
  const s = spawnSpy();
  assert.equal(await ensureServer({ url: "http://127.0.0.1:8765/mcp", env: env({ STEALTHLAB_SERVER_AUTOSTART: "off" }), fetchImpl: down, spawnImpl: s.fn, platform: "win32" }), "off");
  assert.equal(await ensureServer({ url: "http://127.0.0.1:8765/mcp", env: env(), fetchImpl: down, spawnImpl: s.fn, platform: "linux" }), "no-script");
  assert.equal(s.calls.length, 0);
});
