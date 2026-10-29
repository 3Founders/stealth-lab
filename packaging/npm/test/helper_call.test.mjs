import { test } from "node:test";
import assert from "node:assert/strict";
import { helperCall } from "../lib/helper_call.mjs";

const profiles = {
  gemma: { model_id: "g", openai_base_url: "https://gc.example/v1", input_per_mtok: 0.1, output_per_mtok: 0.3 },
  glm: { model_id: "z", openai_base_url: "https://z.example/v4", request_extras: { thinking: { type: "enabled" } } },
};
const readConfig = () => ({ profiles });
// each host answers after its delay with its text, or with an HTTP error
function endpoints(spec, seen = []) {
  return (url, init) => new Promise((resolve, reject) => {
    const host = new URL(url).host;
    const s = spec[host];
    seen.push({ host, body: JSON.parse(init.body) });
    const t = setTimeout(() => resolve(s.status
      ? { ok: false, status: s.status, text: async () => s.body || "" }
      : { ok: true, status: 200, json: async () => ({ choices: [{ message: { content: s.text } }], usage: { prompt_tokens: 100, completion_tokens: 10 } }) }),
      s.ms);
    init.signal?.addEventListener("abort", () => { clearTimeout(t); reject(new DOMException("aborted", "AbortError")); });
  });
}
const base = { what: "t", primary: "gemma", system: "s", user: "u", readConfig, fallback: "glm", env: {} };

test("a fast primary answers alone; the fallback is never asked", async () => {
  const seen = [];
  const r = await helperCall({ ...base, hedgeMs: 200, fetchImpl: endpoints({ "gc.example": { ms: 10, text: "A" }, "z.example": { ms: 10, text: "B" } }, seen) });
  assert.deepEqual({ value: r.value, model: r.model }, { value: "A", model: "gemma" });
  assert.deepEqual(seen.map((s) => s.host), ["gc.example"]);
});

test("a slow primary: the fallback starts at the hedge and the first usable reply wins", async () => {
  const logs = [];
  const t0 = Date.now();
  const r = await helperCall({ ...base, hedgeMs: 50, log: (m) => logs.push(m),
    fetchImpl: endpoints({ "gc.example": { ms: 3000, text: "A" }, "z.example": { ms: 20, text: "B" } }) });
  assert.equal(r.model, "glm");
  assert.ok(Date.now() - t0 < 1000, "did not wait for the slow one");
  assert.match(logs.join("\n"), /gemma was slow; glm answered/);
});

test("a primary that fails starts the fallback at once; both failing say why, with the provider's own text", async () => {
  const t0 = Date.now();
  const r = await helperCall({ ...base, hedgeMs: 5000, fetchImpl: endpoints({ "gc.example": { ms: 5, status: 503 }, "z.example": { ms: 5, text: "B" } }) });
  assert.equal(r.model, "glm");
  assert.ok(Date.now() - t0 < 1000, "not held until the hedge");
  const logs = [];
  assert.equal(await helperCall({ ...base, hedgeMs: 5, log: (m) => logs.push(m),
    fetchImpl: endpoints({ "gc.example": { ms: 5, status: 400, body: '{"error":"context too long"}' }, "z.example": { ms: 5, text: "" } }) }), null);
  assert.match(logs[0], /gemma HTTP 400: \{"error":"context too long"\}/);
  assert.match(logs[0], /glm gave no usable reply/);
});

test("a thinking profile is asked to think briefly, with room for it on top of the reply", async () => {
  const seen = [];
  await helperCall({ ...base, primary: "glm", fallback: "off", maxTokens: 100, fetchImpl: endpoints({ "z.example": { ms: 1, text: "ok" } }, seen) });
  assert.equal(seen[0].body.reasoning_effort, "low");
  assert.equal(seen[0].body.max_tokens, 2100);
});

test("nothing answers within the time: null, and the calls are stopped", async () => {
  const logs = [];
  const r = await helperCall({ ...base, hedgeMs: 10, timeoutMs: 100, log: (m) => logs.push(m),
    fetchImpl: endpoints({ "gc.example": { ms: 5000, text: "A" }, "z.example": { ms: 5000, text: "B" } }) });
  assert.equal(r, null);
  assert.match(logs.join("\n"), /timed out/);
});
