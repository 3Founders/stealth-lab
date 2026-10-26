import { test } from "node:test";
import assert from "node:assert/strict";
import { claudeChildEnv } from "../lib/executors/common.mjs";

test("a hook's claude child gets the token from STEALTHLAB_CLAUDE_CODE_OAUTH_TOKEN when Claude Code removed its own", () => {
  const env = { PATH: "/bin", STEALTHLAB_CLAUDE_CODE_OAUTH_TOKEN: "tok", STEALTHLAB_HOME: "/h" };
  const out = claudeChildEnv(env);
  assert.equal(out.CLAUDE_CODE_OAUTH_TOKEN, "tok");
  assert.equal(out.STEALTHLAB_HOME, undefined, "kell's own variables still never reach the child");
  assert.equal(out.STEALTHLAB_CLAUDE_CODE_OAUTH_TOKEN, undefined);
});

test("credentials already present win; nothing set means nothing added (a login stored in ~/.claude is used)", () => {
  assert.equal(claudeChildEnv({ CLAUDE_CODE_OAUTH_TOKEN: "own", STEALTHLAB_CLAUDE_CODE_OAUTH_TOKEN: "x" }).CLAUDE_CODE_OAUTH_TOKEN, "own");
  assert.equal(claudeChildEnv({ ANTHROPIC_API_KEY: "k", STEALTHLAB_CLAUDE_CODE_OAUTH_TOKEN: "x" }).CLAUDE_CODE_OAUTH_TOKEN, undefined);
  assert.equal(claudeChildEnv({ PATH: "/bin" }).CLAUDE_CODE_OAUTH_TOKEN, undefined);
});
