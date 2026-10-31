import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { withKell, withoutKell, opencodeConfigFile, uninstallOpenCode, CHEAP_MODEL, STRONG_MODEL } from "../lib/opencode_setup.mjs";

const pluginDir = path.join(os.tmpdir(), "kell-oc-plugin");

test("withKell adds the provider, plugin, MCP server and model; withoutKell removes exactly those", () => {
  const mine = { theme: "dark", plugin: ["my-plugin"], mcp: { servers: { mine: { type: "remote", url: "https://x" } } }, model: "anthropic/claude" };
  const { config, owned } = withKell(mine, { pluginDir, url: "https://mcp.kell.org.in/mcp", token: "t" });
  assert.equal(config.provider.gc.options.apiKey, "{env:GENERAL_COMPUTE_API_KEY}");
  assert.deepEqual(config.plugin, ["my-plugin", pluginDir.replace(/\\/g, "/")]);
  assert.equal(config.mcp.servers.kell.codemode, false);
  assert.equal(config.mcp.servers.kell.headers.Authorization, "Bearer t");
  assert.equal(config.model, "anthropic/claude", "the user's own default model is kept");
  assert.ok(!("kell_owned" in config));
  assert.deepEqual(withoutKell(config, { pluginDir, owned }), mine);
});

test("on an empty config kell's strong model becomes the default, and uninstall leaves an empty config", () => {
  const { config, owned } = withKell(null, { pluginDir, url: "https://h/mcp" });
  assert.equal(config.model, STRONG_MODEL);
  assert.ok(CHEAP_MODEL.startsWith("gc/"));
  const back = withoutKell(config, { pluginDir, owned });
  assert.deepEqual(back, {});
});

test("uninstall does not rewrite an OpenCode config kell never touched", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "kell-oc-"));
  const env = { STEALTHLAB_HOME: path.join(root, "kell"), XDG_CONFIG_HOME: path.join(root, "xdg") };
  const file = opencodeConfigFile(env);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const text = '{"theme":"dark"}';
  fs.writeFileSync(file, text);
  assert.equal(uninstallOpenCode({ env }), false);
  assert.equal(fs.readFileSync(file, "utf8"), text);
});
