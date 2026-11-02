// Memory audit 2026-11-02 (docs/revisit.md): a child that prints without end must not grow the process, and a timed-out
// hook must take its grandchildren with it (child.kill() on a shell leaves them behind on Windows).
import test from "node:test";
import assert from "node:assert/strict";
import { runHook } from "../lib/opencode/lib/run_hook.mjs";
import { runClaudeAsync } from "../lib/claude_call.mjs";

test("a hook that prints 6 MB is read as its last 1 MB", async () => {
  const command = `"${process.execPath}" -e "process.stdout.write('x'.repeat(6e6))"`;
  const r = await runHook({ command, payload: {}, timeoutMs: 30000 });
  assert.equal(r.code, 0);
  assert.ok(r.stdout.length <= 1_000_000, `kept ${r.stdout.length}`);
  assert.ok(r.stdout.length > 0);
});

test("a hook that hangs is killed with its child, and reports a timeout", async () => {
  const command = `"${process.execPath}" -e "require('child_process').spawn(process.execPath,['-e','setTimeout(()=>{},60000)'],{stdio:'inherit'});setTimeout(()=>{},60000)"`;
  const t0 = Date.now();
  const r = await runHook({ command, payload: {}, timeoutMs: 1500 });
  assert.equal(r.timedOut, true);
  assert.ok(Date.now() - t0 < 15000);
});

test("claude_call without a claude binary answers instead of throwing", async () => {
  const r = await runClaudeAsync({ PATH: "" }, ["-p"], "hi", undefined, 2000);
  assert.equal(typeof r.stdout, "string");
});
