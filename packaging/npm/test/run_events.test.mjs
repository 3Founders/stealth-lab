import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { followRun, postToRoom, readRunEvents, toRoomEvent } from "../lib/run_events.mjs";

function fakeRun() {
  const dir = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "runev-")), "r-20261028000000-abcdef");
  fs.mkdirSync(dir, { recursive: true });
  const j = (o) => `${JSON.stringify(o)}\n`;
  fs.writeFileSync(path.join(dir, "events.jsonl"),
    j({ ts: "2026-10-28T10:00:00Z", event: "queued" }) +
    j({ ts: "2026-10-28T10:00:01Z", event: "selected", units: [{ executor: "stealth", model: "glm-5.3-flash" }] }) +
    j({ ts: "2026-10-28T10:00:02Z", event: "running", attempt: 1, executor: "stealth", model: "glm-5.3-flash" }));
  fs.writeFileSync(path.join(dir, "a1.transcript.jsonl"),
    j({ step: 1, tool: "read_file", args: { path: ".kell/goals/G-1.md" }, result: "GOAL|G-1" }) +
    j({ step: 2, tool: "search", args: { query: "CLAIM|" }, result: "R-9 ..." }) +
    j({ step: 3, tool: "edit_file", args: { path: "src\\dates.py" }, result: "error: no match for sk-secretsecretsecret1234" }));
  fs.writeFileSync(path.join(dir, "run.json"), JSON.stringify({ attempts: [
    { attempt: 1, executor: "stealth", model: "glm-5.3-flash", state: "verified", verified: true, duration_s: 41,
      checks: [{ cmd: "pytest -q", exit: 0 }], diff: { files: ["src/dates.py"] } }] }));
  return dir;
}

test("one ordered log: state, then the attempt's tool steps with the paths they touched, then its end", () => {
  const ev = readRunEvents(fakeRun(), { secrets: ["sk-secretsecretsecret1234"] });
  assert.deepEqual(ev.map((e) => e.kind), ["state", "state", "state", "tool", "tool", "tool", "final"]);
  assert.match(ev[3].text, /read_file \.kell\/goals\/G-1\.md/);
  assert.deepEqual(ev[3].locators, [{ path: ".kell/goals/G-1.md" }]);
  assert.deepEqual(ev[5].locators, [{ path: "src/dates.py" }], "backslashes become slashes");
  assert.equal(ev[5].data.ok, false);
  assert.equal(ev[6].data.verified, true);
  assert.deepEqual(ev[6].locators, [{ path: "src/dates.py" }]);
  assert.deepEqual(ev[6].data.checks, [{ cmd: "pytest -q", exit: 0 }]);
  assert.equal(new Set(ev.map((e) => e.id)).size, ev.length, "ids are unique and stable");
  assert.match(ev[0].id, /^r-20261028000000-abcdef:0$/);
});

test("a secret in a tool result never reaches an event", () => {
  const ev = readRunEvents(fakeRun(), { secrets: ["sk-secretsecretsecret1234"] });
  assert.ok(!JSON.stringify(ev).includes("sk-secretsecretsecret1234"));
});

test("a run with no files yet gives no events and does not throw", () => {
  assert.deepEqual(readRunEvents(path.join(os.tmpdir(), "no-such-run-dir")), []);
});

test("a room event is what the backend's room route takes: a UUID id, plain-text body, an agent sender with a handle and depth", () => {
  const ev = readRunEvents(fakeRun());
  const note = toRoomEvent(ev[3], { lamport: 4 });
  const end = toRoomEvent(ev[6], { lamport: 7, depth: 1 });
  assert.match(note.id, /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(note.id, toRoomEvent(ev[3], { lamport: 99 }).id, "the same run event is always the same room id (a repost is a duplicate)");
  assert.notEqual(note.id, end.id);
  assert.equal(note.kind, "note");
  assert.equal(end.kind, "task_update");
  assert.equal(end.sender_kind, "agent");
  assert.match(end.sender_handle, /^@[a-z0-9][a-z0-9_.-]{0,38}$/i);
  assert.equal(typeof end.body, "string");
  assert.match(end.body, /passed its checks/);
  assert.match(end.body, /Files: src\/dates\.py/);
  assert.match(end.body, /Checks: pytest -q -> exit 0/);
  assert.equal(end.depth, 1);
  assert.equal(end.lamport, 7);
  assert.ok(end.body.length <= 8000);
});

test("postToRoom: 404/405 is no route, 401/403 is a token the route will not take, a retry rejection is returned for later", async () => {
  const mk = (status, body = {}) => async () => ({ status, ok: status < 300, json: async () => body });
  const args = { baseUrl: "https://x.example/", token: "t", roomId: "#r", events: [{ id: "a" }] };
  assert.deepEqual(await postToRoom({ ...args, fetchImpl: mk(404) }), { posted: 0, retry: [], unavailable: true });
  assert.deepEqual(await postToRoom({ ...args, fetchImpl: mk(405) }), { posted: 0, retry: [], unavailable: true });
  assert.deepEqual(await postToRoom({ ...args, fetchImpl: mk(401) }), { posted: 0, retry: [], unauthorized: true });
  assert.deepEqual(await postToRoom({ ...args, fetchImpl: mk(403) }), { posted: 0, retry: [], unauthorized: true });
  assert.deepEqual(await postToRoom({ ...args, fetchImpl: mk(500) }), { posted: 0, retry: [], error: "HTTP 500" });
  const ok = await postToRoom({ ...args, fetchImpl: mk(201, { accepted: [{ id: "a", seq: 1 }], rejected: [{ id: "b", reason: "slow down", retry: true }, { id: "c", reason: "bad", retry: false }] }) });
  assert.equal(ok.posted, 1);
  assert.deepEqual(ok.retry, ["b"]);
  assert.deepEqual(await postToRoom({ ...args, events: [], fetchImpl: () => assert.fail("no events, no call") }), { posted: 0, retry: [] });
});

test("followRun posts each event once, to the route's URL with the bearer token, and the stop call sends the rest", async () => {
  const dir = fakeRun();
  const calls = [];
  const fetchImpl = async (url, init) => { calls.push({ url, init, body: JSON.parse(init.body) }); return { status: 201, ok: true, json: async () => ({ accepted: JSON.parse(init.body).events.map((e) => ({ id: e.id, seq: 1 })), rejected: [] }) }; };
  const stop = followRun({ dir, baseUrl: "https://mcp.example", token: "tok", roomId: "#auth squad", fetchImpl, everyMs: 3_600_000 });
  await stop();
  await stop();                                                   // a second flush finds nothing new
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://mcp.example/v1/rooms/%23auth%20squad/events");
  assert.equal(calls[0].init.headers.authorization, "Bearer tok");
  assert.equal(calls[0].body.events.length, 7);
  assert.ok(calls[0].body.events.every((e) => /^[0-9a-f-]{36}$/.test(e.id) && e.sender_kind === "agent"));
  fs.appendFileSync(path.join(dir, "a1.transcript.jsonl"), `${JSON.stringify({ step: 4, tool: "run_shell", args: { command: "pytest -q" }, result: "ok" })}\n`);
  const stop2 = followRun({ dir, baseUrl: "https://mcp.example", token: "tok", roomId: "#r", fetchImpl, everyMs: 3_600_000 });
  await stop2();
  assert.equal(calls.length, 2);                                  // a new follower starts from the top: the server de-duplicates by id
});

test("followRun sends at most 50 events a request, and leaves a rate-limited event for the next flush", async () => {
  const dir = fakeRun();
  let n = 0;
  const seen = [];
  const fetchImpl = async (url, init) => {
    n++;
    const evs = JSON.parse(init.body).events;
    seen.push(evs.length);
    if (n === 1) return { status: 201, ok: true, json: async () => ({ accepted: evs.slice(0, 3).map((e) => ({ id: e.id, seq: 1 })), rejected: [{ id: evs[3].id, reason: "slow down", retry: true }] }) };
    return { status: 201, ok: true, json: async () => ({ accepted: evs.map((e) => ({ id: e.id, seq: 2 })), rejected: [] }) };
  };
  const stop = followRun({ dir, baseUrl: "https://mcp.example", token: "t", roomId: "#r", fetchImpl, everyMs: 3_600_000 });
  await stop();                                                    // first flush: 3 stored, the 4th is told to retry
  assert.deepEqual(seen, [7]);
  await stop();                                                    // the next flush resumes at the 4th: 4 events
  assert.deepEqual(seen, [7, 4]);
});

test("followRun stops asking once the route is missing or refuses the token", async () => {
  for (const status of [404, 401]) {
    const dir = fakeRun();
    let n = 0;
    const results = [];
    const fetchImpl = async () => { n++; return { status, ok: false, json: async () => ({}) }; };
    const stop = followRun({ dir, baseUrl: "https://mcp.example", token: "t", roomId: "#r", fetchImpl, everyMs: 3_600_000, onResult: (r) => results.push(r) });
    await stop();
    await stop();
    assert.equal(n, 1, `status ${status}`);
    assert.ok(results[0].unavailable || results[0].unauthorized);
  }
});
