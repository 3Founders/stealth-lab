import { test } from "node:test";
import assert from "node:assert/strict";
import { contextPack, contextPackEnabled } from "../lib/context_pack.mjs";

test("contextPack: the module's tests, the located files' history, reproduce first, the project's conventions -- short", () => {
  const t = contextPack({ files: ["src/a.py", "src/b.py", "src/c.py"], testFile: "tests/test_a.py" });
  assert.match(t, /read tests\/test_a\.py/);
  assert.match(t, /git log -n 3 -p -- src\/a\.py src\/b\.py`/);
  assert.doesNotMatch(t, /src\/c\.py/);
  assert.match(t, /reproduce the problem first/);
  assert.match(t, /signatures stay compatible/);
  assert.ok(t.length < 700, `${t.length} chars: it shares the task's 2,000`);
  assert.doesNotMatch(contextPack({}), /read |git log/, "nothing located: only the steps that need nothing");
  assert.equal(contextPackEnabled({}), false);
  assert.equal(contextPackEnabled({ STEALTHLAB_DISPATCH_CONTEXT_PACK: "on" }), true);
});
