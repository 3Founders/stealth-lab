// The launcher that ties the console to the MCP: the console runs while the MCP runs, and stops when it stops.
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
// @ts-expect-error plain .mjs launcher
import { consoleUrl, isListening, mcpCommand, parseArgs } from "../scripts/start.mjs";

const START = path.resolve(__dirname, "..", "scripts", "start.mjs");
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
async function until(fn: () => boolean | Promise<boolean>, ms = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await fn()) return true; await wait(100); }
  return false;
}
const freePort = () => new Promise<number>((resolve) => { const s = net.createServer().listen(0, "127.0.0.1", () => { const p = (s.address() as net.AddressInfo).port; s.close(() => resolve(p)); }); });

describe("arguments and commands", () => {
  it("defaults to port 8765 and the console on a free port", () => {
    expect(parseArgs([])).toMatchObject({ mcpPort: 8765, consolePort: 0, noMcp: false, mcpCmd: null, build: true });
  });
  it("reads options and refuses nonsense", () => {
    expect(parseArgs(["--mcp-port", "9000", "--no-mcp", "--no-build"])).toMatchObject({ mcpPort: 9000, noMcp: true, build: false });
    expect(() => parseArgs(["--mcp-port", "abc"])).toThrow(/port number/);
    expect(() => parseArgs(["--bogus"])).toThrow(/unknown option/);
    expect(() => parseArgs(["--mcp-cmd"])).toThrow(/needs a value/);
  });
  it("starts the MCP the way the local start script does, per platform", () => {
    const win = mcpCommand({ platform: "win32", root: "C:\\repo", port: 8765 });
    expect(win.cmd).toBe("powershell.exe");
    expect(win.args.join(" ")).toMatch(/start_mcp_local\.ps1 -Port 8765$/);
    const nix = mcpCommand({ platform: "linux", root: "/repo", port: 8765, exists: () => false });
    expect(nix.cmd).toBe("python3");
    expect(nix.args).toEqual(["-m", "uvicorn", "app.mcp_server.server:app", "--host", "127.0.0.1", "--port", "8765", "--workers", "1"]);
    expect(nix.env.DEPLOYMENT_MODE).toBe("single_user"); // the same pin the local script applies, for this process only
    expect(mcpCommand({ platform: "linux", root: "/repo", exists: () => true }).cmd).toMatch(/\.venv/);
  });
  it("an override command gets the port in MCP_PORT", () => {
    const c = mcpCommand({ port: 9123, mcpCmd: "node x.js" });
    expect(c).toMatchObject({ cmd: "node x.js", shell: true });
    expect(c.env.MCP_PORT).toBe("9123");
  });
  it("finds the console address in the helper's output", () => {
    expect(consoleUrl("kell console on 127.0.0.1:51355 (this machine only). Open:\n  http://127.0.0.1:51355/?t=d1ad49\nCtrl+C")).toBe("http://127.0.0.1:51355/?t=d1ad49");
    expect(consoleUrl("nothing yet")).toBeNull();
  });
  it("tells a listening port from a closed one", async () => {
    const port = await freePort();
    expect(await isListening(port)).toBe(false);
    const s = net.createServer().listen(port, "127.0.0.1");
    await until(() => isListening(port));
    expect(await isListening(port)).toBe(true);
    s.close();
  });
});

describe("the console and the MCP are independent, and say when the other closes", () => {
  const spawned: ChildProcess[] = [];
  afterEach(() => { for (const c of spawned.splice(0)) { try { if (process.platform === "win32") spawn("taskkill", ["/PID", String(c.pid), "/T", "/F"]); else c.kill("SIGKILL"); } catch { /* gone */ } } });

  /** The launcher's own summary line, not the helper banner it also relays. */
  const summaryUrl = (t: string) => /Console\s+(http:\/\/127\.0\.0\.1:\d+\/\?t=[a-f0-9]+)/.exec(t)?.[1] ?? null;
  function launch(args: string[], tmp: string) {
    const c = spawn(process.execPath, [START, ...args], { env: { ...process.env, STEALTHLAB_HOME: path.join(tmp, "kell"), HOME: tmp, USERPROFILE: tmp }, stdio: ["ignore", "pipe", "pipe"] });
    spawned.push(c);
    let out = "";
    c.stdout!.on("data", (d) => { out += d; });
    c.stderr!.on("data", (d) => { out += d; });
    return { c, out: () => out, exited: () => c.exitCode !== null };
  }
  const healthy = async (base: string) => { try { return (await fetch(`${base}/health`)).ok; } catch { return false; } };

  it("starts the MCP, then the console; when the MCP closes the console keeps running and the launcher says so", async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "kel-start-"));
    const stopFile = path.join(tmp, "stop");
    const dummy = path.join(tmp, "mcp.js");
    fs.writeFileSync(dummy, `const net=require("net"),fs=require("fs");const s=net.createServer(c=>c.end()).listen(Number(process.env.MCP_PORT),"127.0.0.1");setInterval(()=>{if(fs.existsSync(${JSON.stringify(stopFile)})){s.close();process.exit(0)}},100);`);
    const port = await freePort();
    const l = launch(["--mcp-port", String(port), "--mcp-cmd", `node "${dummy}"`, "--no-build"], tmp);
    expect(await until(() => summaryUrl(l.out()) !== null, 30000), l.out()).toBe(true);
    const base = summaryUrl(l.out())!.split("/?")[0];
    expect(await isListening(port)).toBe(true);
    expect(await healthy(base)).toBe(true);
    expect((await fetch(`${base}/`)).status).toBe(200);

    fs.writeFileSync(stopFile, "x"); // the MCP closes
    expect(await until(() => /MCP closed/.test(l.out()), 15000), l.out()).toBe(true);
    expect(l.exited()).toBe(false); // the launcher did not follow it down
    expect(await healthy(base)).toBe(true); // and neither did the console
    expect((await fetch(`${base}/`)).status).toBe(200);
  }, 60000);

  it("reuses an MCP that is already running, leaves it alone, and notices when it closes", async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "kel-start-"));
    const port = await freePort();
    const mine = net.createServer((c) => c.end()).listen(port, "127.0.0.1");
    const l = launch(["--mcp-port", String(port), "--no-build"], tmp);
    expect(await until(() => summaryUrl(l.out()) !== null, 30000), l.out()).toBe(true);
    const base = summaryUrl(l.out())!.split("/?")[0];
    expect(l.out()).toMatch(/already running/);
    mine.close(); // someone else closes their MCP
    expect(await until(() => /MCP closed/.test(l.out()), 15000), l.out()).toBe(true);
    expect(l.exited()).toBe(false);
    expect(await healthy(base)).toBe(true);
    const back = net.createServer((c) => c.end()).listen(port, "127.0.0.1"); // and when it comes back, that is said too
    expect(await until(() => /MCP is running again/.test(l.out()), 15000), l.out()).toBe(true);
    back.close();
  }, 60000);

  it("starts the console even if the MCP dies at once, and says the MCP is closed", async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "kel-start-"));
    const port = await freePort();
    const l = launch(["--mcp-port", String(port), "--mcp-cmd", `node -e "process.exit(3)"`, "--no-build"], tmp);
    expect(await until(() => summaryUrl(l.out()) !== null, 30000), l.out()).toBe(true);
    expect(l.out()).toMatch(/stopped right away/);
    expect(l.exited()).toBe(false);
    expect(await healthy(summaryUrl(l.out())!.split("/?")[0])).toBe(true);
  }, 60000);

  it("--no-mcp runs the console alone", async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "kel-start-"));
    const l = launch(["--no-mcp", "--no-build"], tmp);
    expect(await until(() => summaryUrl(l.out()) !== null, 30000), l.out()).toBe(true);
    expect(l.out()).toMatch(/stops the console/);
    expect(l.out()).not.toMatch(/MCP\s+http/);
  }, 60000);
});
