import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { compareImages, visualCheck } from "../lib/visual_check.mjs";

function home() {
  const h = fs.mkdtempSync(path.join(os.tmpdir(), "vis-home-"));
  fs.writeFileSync(path.join(h, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["glm-5.3-flash"] } },
    profiles: { "glm-5.3-flash": { openai_base_url: "https://example.test/v1", key_env: "K", vision: true, model_id: "glm-5.3-flash" } } }));
  return { STEALTHLAB_HOME: h, K: "k" };
}
const png = () => { const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "vis-")), "a.png"); fs.writeFileSync(f, Buffer.from([0x89, 0x50, 0x4e, 0x47])); return f; };
const reply = (text) => async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: text } }] }) });

test("the vision model's verdict: MATCH, MISMATCH with the reason, both images sent", async () => {
  let sent = null;
  const ok = await compareImages(png(), png(), { env: home(), fetchImpl: async (u, i) => { sent = JSON.parse(i.body); return reply("MATCH: same sections and text")(); } });
  assert.deepEqual(ok, { match: true, why: "same sections and text" });
  assert.equal(sent.messages[0].content.filter((c) => c.type === "image_url").length, 2);
  const bad = await compareImages(png(), png(), { env: home(), fetchImpl: reply("**MISMATCH**: the footer and sidebar are missing") });
  assert.equal(bad.match, false);
  assert.match(bad.why, /footer/);
});

test("no verdict (no vision model, an unreadable reply, nothing to render): null, which dispatch treats as not accepted", async () => {
  assert.equal(await compareImages(png(), png(), { env: { STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "vis-none-")) } }), null);
  assert.equal(await compareImages(png(), png(), { env: home(), fetchImpl: reply("looks fine to me") }), null);
  assert.equal(await visualCheck({ worktree: null, files: ["index.html"], images: ["a.png"], repo: os.tmpdir(), env: home() }), null);
  assert.equal(await visualCheck({ worktree: os.tmpdir(), files: ["style.css"], images: ["a.png"], repo: os.tmpdir(), env: home() }), null);
});
