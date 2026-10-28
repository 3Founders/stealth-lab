import { test } from "node:test";
import assert from "node:assert/strict";
import { cachedTokensOf, costOf, tokenCost } from "../lib/spend.mjs";
import { normaliseProfile } from "../lib/executors/profile.mjs";

test("costOf: cached input at the profile's cached price; without one, or with nothing cached, the full price", () => {
  const glm = normaliseProfile("glm-5.3-flash", { key_env: "K", openai_base_url: "https://z.example/v1", input_per_mtok: 0.15,
    output_per_mtok: 0.5, cached_input_per_mtok: 0.03 });
  const usage = { prompt_tokens: 1_000_000, completion_tokens: 100_000, prompt_tokens_details: { cached_tokens: 900_000 } };
  assert.equal(cachedTokensOf(usage), 900_000);
  assert.ok(Math.abs(costOf(glm, usage) - (100_000 * 0.15 + 900_000 * 0.03 + 100_000 * 0.5) / 1e6) < 1e-12);
  const gc = normaliseProfile("gpt-oss-120b", { key_env: "K", openai_base_url: "https://gc.example/v1", input_per_mtok: 0.037, output_per_mtok: 0.17 });
  assert.ok(Math.abs(costOf(gc, usage) - (1_000_000 * 0.037 + 100_000 * 0.17) / 1e6) < 1e-12, "no cached price: cached input is billed in full");
  assert.ok(Math.abs(costOf(glm, { prompt_tokens: 1000, completion_tokens: 0 }) - 1000 * 0.15 / 1e6) < 1e-15, "nothing reported cached");
  assert.equal(tokenCost(glm, { input: 10, cached: 50, output: 0 }), 10 * 0.03 / 1e6, "cached never exceeds the input");
  assert.equal(costOf(normaliseProfile("x", { key_env: "K", openai_base_url: "https://x.example/v1" }), usage), null, "no prices: unknown");
  assert.throws(() => normaliseProfile("x", { key_env: "K", openai_base_url: "https://x.example/v1", input_per_mtok: 1, output_per_mtok: 1, cached_input_per_mtok: -1 }), /non-negative/);
});
