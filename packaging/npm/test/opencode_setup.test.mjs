import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { withKell, withoutKell, opencodeConfigFile, uninstallOpenCode, CHEAP_MODEL, STRONG_MODEL, modelPlan, DI_PROVIDER, DI_MODEL, DI_BASE_URL, pooledFromExec } from "../lib/opencode_setup.mjs";

const pluginDir = path.join(os.tmpdir(), "kell-oc-plugin");

test("withKell adds the provider, plugin, MCP server and model; withoutKell removes exactly those", () => {
  const mine = { theme: "dark", plugin: ["my-plugin"], mcp: { servers: { mine: { type: "remote", url: "https://x" } } }, model: "anthropic/claude" };
  const { config, owned } = withKell(mine, { pluginDir, url: "https://mcp.kell.org.in/mcp", token: "t" });
  assert.equal(config.provider.gc.options.apiKey, "{env:GENERAL_COMPUTE_API_KEY}");
  assert.deepEqual(config.plugin, ["my-plugin", pluginDir.replace(/\\/g, "/")]);
  assert.equal(config.mcp.servers.kell.codemode, false);
  assert.equal(config.mcp.servers.kell.headers.Authorization, "Bearer t");
  assert.equal(config.model, "anthropic/claude", "the user's own default model is kept");
  assert.equal(config.experimental.image_generate, false, "the built-in image generator is off; kell/images is the image tool");
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

test("the second provider (di) is added only when its key variable is given, and uninstall removes it too", () => {
  const plain = withKell(null, { pluginDir, url: "https://h/mcp" });
  assert.ok(!plain.config.provider.di, "no di provider without strongKeyEnv");
  const { config, owned } = withKell(null, { pluginDir, url: "https://h/mcp", strongKeyEnv: "DEEPINFRA_KEY" });
  assert.equal(config.provider.di.options.baseURL, DI_BASE_URL);
  assert.equal(config.provider.di.options.apiKey, "{env:DEEPINFRA_KEY}");
  assert.ok(config.provider.di.models[DI_MODEL], "the DeepSeek model is listed");
  assert.ok(DI_MODEL.includes("deepseek-ai/"), "a DeepInfra-listed DeepSeek id");
  assert.equal(withoutKell(config, { pluginDir, owned }).provider, undefined, "uninstall removes both providers");
});

test("model plan: with the second provider the strong rung moves there and gc/deepseek becomes its fallback; otherwise today's setup", () => {
  assert.deepEqual(modelPlan({}), { strong: STRONG_MODEL, fallback: CHEAP_MODEL });
  assert.deepEqual(modelPlan({ strongKeyEnv: "DEEPINFRA_KEY" }),
                   { strong: `${DI_PROVIDER}/${DI_MODEL}`, fallback: STRONG_MODEL });
  assert.equal(STRONG_MODEL.startsWith("gc/"), true);
  assert.equal(CHEAP_MODEL.startsWith("gc/"), true);
});

test("organization lane: join profiles point the provider at the gateway, with the kell token and no provider key", () => {
  const exec = {
    organization_id: "org-1",
    profiles: {
      "glm-5.3-flash": { model_id: "glm-5.3-flash", key_from: "stealthlab", openai_base_url: "https://api.kell.org.in/v1",
                         input_per_mtok: 0.2, output_per_mtok: 0.8 },
      "glm-5.3": { model_id: "glm-5.3", key_from: "stealthlab", openai_base_url: "https://api.kell.org.in/v1",
                   input_per_mtok: 0.6, output_per_mtok: 2.2 },
    },
  };
  const pooled = pooledFromExec(exec);
  const { config, owned } = withKell(null, { pluginDir, url: "https://mcp.kell.org.in/mcp", token: "tok", pooled });
  assert.equal(config.provider.gc, undefined, "no developer-held key provider on the organization lane");
  assert.equal(config.provider.kell.options.baseURL, "https://api.kell.org.in/v1");
  assert.equal(config.provider.kell.options.apiKey, "tok", "the person's kell sign-in is the bearer");
  assert.equal(config.provider.kell.options.headers["x-stealthlab-org"], "org-1");
  assert.equal(config.provider.kell.options.headers["x-stealthlab-data-class"], "USER_PRIVATE", "the conservative default");
  assert.deepEqual(config.provider.kell.models["glm-5.3"].cost, { input: 0.6, output: 2.2 });
  assert.equal(config.model, "kell/glm-5.3", "the priciest joined model is the strong rung");
  assert.deepEqual(modelPlan({ pooled }), { strong: "kell/glm-5.3", fallback: "kell/glm-5.3-flash" });
  assert.deepEqual(withoutKell(config, { pluginDir, owned }), {}, "uninstall removes the gateway provider too");
});

test("a machine with no join profile stays on the developer-held key lane", () => {
  assert.equal(pooledFromExec({ profiles: { "glm-5.3": { model_id: "glm-5.3", key_env: "ZAI_API_KEY", openai_base_url: "https://api.z.ai/api/paas/v4" } } }), null);
  assert.equal(pooledFromExec({}), null);
});
