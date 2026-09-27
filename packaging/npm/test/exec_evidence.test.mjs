import { test } from "node:test";
import assert from "node:assert/strict";

import { flushOutbox, reportModelRun } from "../lib/exec/evidence.mjs";
import { Registry, selectUnits } from "../lib/exec/select.mjs";
import { readExecConfig } from "../lib/exec/store.mjs";
import { mockHosted, outboxFiles, stubAdapter, testEnv, writeExecConfig } from "./fixtures/exec/helpers.mjs";

const PAYLOAD = { model: "m1", scaffold: "stub", accepted: true, instance_key: "ik-1", goal_id: "g-1", check_kind: "tests",
                  latency_ms: 12, attempt_index: 0, summary: "must never be sent", tokens_in: null };

test("evidence (spec 8.8): HTTP 500 -> queued; the next call flushes it, then sends its own", async () => {
  let mode = "down";
  const hosted = await mockHosted((name) => {
    if (mode === "down") return { status: 500, text: "internal error" };
    return { text: JSON.stringify({ observation_id: "o-" + Math.random(), goal_id: "g-1" }) };
  });
  const env = testEnv({ url: hosted.url });
  try {
    const first = await reportModelRun(PAYLOAD, { env });
    assert.equal(first.reported, false);
    assert.equal(first.queued, true);
    const queued = outboxFiles(env);
    assert.equal(queued.length, 1);
    assert.equal(queued[0].args.scaffold, "stub");
    assert.equal(queued[0].args.summary, undefined, "only whitelisted fields are ever queued or sent");
    assert.equal(queued[0].args.tokens_in, undefined, "nulls are dropped");

    mode = "up";
    const second = await reportModelRun({ ...PAYLOAD, attempt_index: 1 }, { env });
    assert.equal(second.reported, true);
    assert.equal(outboxFiles(env).length, 0);
    const sent = hosted.calls.filter((c) => c.name === "report_model_run");
    assert.deepEqual(sent.slice(-2).map((c) => c.args.attempt_index), [0, 1], "queued entry first, in order");
    assert.equal(sent.at(-1).auth, "Bearer tok-test-123456789");
  } finally {
    await hosted.close();
  }
});

test("evidence: 'relation does not exist' (tables not migrated) and tool errors queue; REFUSED goes to rejected/", async () => {
  let reply = { text: 'error: relation "routing_observations" does not exist', isError: true };
  const hosted = await mockHosted(() => reply);
  const env = testEnv({ url: hosted.url });
  try {
    const r1 = await reportModelRun(PAYLOAD, { env });
    assert.equal(r1.queued, true);
    assert.match(r1.error, /not ready|does not exist/);
    reply = { text: "UndefinedTableError: model_observations" };
    const r2 = await reportModelRun(PAYLOAD, { env });
    assert.equal(r2.queued, true);
    assert.equal(outboxFiles(env).length, 2);
    reply = { rpcError: "internal" };
    assert.equal((await flushOutbox({ env })).remaining, 2);

    reply = { text: JSON.stringify({ observation_id: "o1" }) };
    const flushed = await flushOutbox({ env });
    assert.equal(flushed.sent, 2);
    assert.equal(outboxFiles(env).length, 0);

    reply = { text: "REFUSED: goal g-1 not found" };
    const r3 = await reportModelRun(PAYLOAD, { env });
    assert.equal(r3.reported, false);
    assert.equal(r3.queued, false);
    assert.match(r3.rejected, /REFUSED/);
    assert.equal(outboxFiles(env, "rejected").length, 1);
  } finally {
    await hosted.close();
  }
});

test("evidence: offline / not logged in -> queued without any network call; no goal -> held", async () => {
  const offline = testEnv({ url: "" });
  const r = await reportModelRun(PAYLOAD, { env: offline });
  assert.equal(r.queued, true);
  const hosted = await mockHosted(() => ({ text: "{}" }));
  const anon = testEnv({ url: hosted.url, token: "" });
  try {
    const r2 = await reportModelRun(PAYLOAD, { env: anon });
    assert.equal(r2.queued, true);
    assert.match(r2.error, /not logged in/);
    assert.equal(hosted.calls.length, 0);
    const held = await reportModelRun({ ...PAYLOAD, goal_id: undefined }, { env: anon });
    assert.equal(held.queued, false);
    assert.match(held.held, /goal_id or procedure_id/);
    assert.equal(outboxFiles(anon, "held")[0].args.scaffold, "stub");
  } finally {
    await hosted.close();
  }
});

function registryWith(env) {
  return new Registry({
    env,
    adapters: {
      alpha: stubAdapter({ id: "alpha" }),
      beta: stubAdapter({ id: "beta" }),
      broken: stubAdapter({ id: "broken", verifiedWith: null }),
    },
  });
}

test("selection (spec 8.9): recommend_models ladder is followed; unrunnable adapters never become candidates", async () => {
  const hosted = await mockHosted((name, args) => {
    assert.equal(name, "recommend_models");
    assert.deepEqual(args.candidates.sort(), ["a1|alpha", "b1|beta", "b2|beta"]);
    return { text: JSON.stringify({ status: "ok", recommendation_id: "rec-1", instance_key: "ik-9",
      recommended: { ladder: ["b2|beta", "zzz|nobody", "a1|alpha"], p_success: 0.8, p_success_q05: 0.7, p_success_q95: 0.9 } }) };
  });
  const env = testEnv({ url: hosted.url });
  writeExecConfig(env, { executors: { alpha: { models: ["a1"] }, beta: { models: ["b1", "b2"] }, broken: { models: ["x"] } },
                         default_order: ["a1|alpha", "b1|beta"] });
  try {
    const sel = await selectUnits({ registry: registryWith(env), config: readExecConfig(env), goal_id: "g-1", env });
    assert.equal(sel.source, "recommend_models");
    assert.deepEqual(sel.units, [{ model: "b2", executor: "beta" }]);
    assert.equal(sel.race, 1, "narrow interval: no automatic race");
    assert.equal(sel.recommendation.instance_key, "ik-9");
    assert.equal(sel.recommendation.recommendation_id, "rec-1");
  } finally {
    await hosted.close();
  }
});

test("selection: hosted error / not_ready -> default_order; a wide interval turns racing on", async () => {
  let reply = { status: 500 };
  const hosted = await mockHosted(() => reply);
  const env = testEnv({ url: hosted.url });
  writeExecConfig(env, { executors: { alpha: { models: ["a1"] }, beta: { models: ["b1"] } }, default_order: ["b1|beta", "a1|alpha"] });
  const cfg = readExecConfig(env);
  try {
    let sel = await selectUnits({ registry: registryWith(env), config: cfg, goal_id: "g-1", env });
    assert.equal(sel.source, "default_order");
    assert.deepEqual(sel.units, [{ model: "b1", executor: "beta" }]);
    assert.match(sel.note, /fell back to default_order/);

    reply = { text: JSON.stringify({ status: "not_ready", reason: "no fitted model yet" }) };
    sel = await selectUnits({ registry: registryWith(env), config: cfg, goal_id: "g-1", env });
    assert.equal(sel.source, "default_order");

    reply = { text: JSON.stringify({ status: "ok", recommended: { ladder: ["a1|alpha", "b1|beta"], p_success_q05: 0.1, p_success_q95: 0.9 } }) };
    sel = await selectUnits({ registry: registryWith(env), config: cfg, goal_id: "g-1", env });
    assert.equal(sel.race, 2);
    assert.deepEqual(sel.units.map((u) => u.executor), ["alpha", "beta"]);

    const explicit = await selectUnits({ registry: registryWith(env), config: cfg, race: 1, goal_id: "g-1", env });
    assert.equal(explicit.race, 1, "an explicit race=1 is never overridden");

    await assert.rejects(selectUnits({ registry: registryWith(env), config: cfg, executor: "broken", env }), /no VERIFIED_WITH/);
  } finally {
    await hosted.close();
  }
});
