// One event log per run, read two ways: the console shows it (lib/console.mjs runSnapshot) and a room can follow it.
//
// What a run leaves on disk (~/.stealthlab/runs/<id>/): events.jsonl (state changes, written by the runtime), a<N>.transcript.jsonl
// (every tool call the open-model agent made, with the path it touched) and run.json (attempts, checks, files changed). This turns
// those into ONE ordered list of small events, so the console and a room cannot disagree about what happened:
//   { id: "<run>:<n>", ts, run_id, attempt, kind: "state" | "tool" | "final", text, locators: [{path, lines?}], data }
// The id is stable (the n-th event of the run), so a client that sees an event twice drops it, and a room post is idempotent.
//
// Rooms: an event is POSTed to /v1/rooms/{id}/events as the backend's room_routes.py / services/rooms.py take it: id a UUID (made
// from the run event's id, so a repost is a duplicate, not a second row), kind note | task_update, sender_kind "agent" with a
// @handle, body plain text (the paths it touched are on a "Files:" line), depth. Two honest limits: (1) that route takes the
// person's WEBSITE-SESSION token and refuses an MCP token (delegate_routes.py), which a local runner usually does not have, so
// KELL_ROOM_TOKEN must hold one until the MCP room_post tool exists; a 401/403 stops the follower with that message. (2) the
// route is rate limited per person per minute; a "retry" rejection leaves the event unsent for the next flush.
// Everything is redacted here (lib/exec/redact.mjs) because an open room is readable by the server.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { redact } from "./exec/redact.mjs";

const TEXT_MAX = 300;
const cut = (s, n = TEXT_MAX) => { const t = String(s ?? "").replace(/\s+/g, " ").trim(); return t.length <= n ? t : `${t.slice(0, n - 1)}…`; };

function readLines(file) {
  let text = "";
  try { text = fs.readFileSync(file, "utf8"); } catch { return []; }
  const out = [];
  for (const l of text.split("\n")) { if (!l) continue; try { out.push(JSON.parse(l)); } catch { /* a half-written line */ } }
  return out;
}

const stateText = (e) => {
  const who = [e.executor, e.model].filter(Boolean).join(" ");
  switch (e.event) {
    case "running": return `attempt ${e.attempt ?? "?"} started${who ? ` on ${who}` : ""}`;
    case "selected": return `chose ${(e.units || []).map((u) => `${u.model}`).join(", ") || "a model"}${e.note ? ` (${cut(e.note, 80)})` : ""}`;
    default: return `${e.event}${who ? ` (${who})` : ""}${e.reason ? `: ${cut(e.reason, 120)}` : ""}`;
  }
};

// -> the run's events in order, redacted. `secrets`: values to scrub besides the patterns redact() knows.
export function readRunEvents(dir, { secrets = [] } = {}) {
  const runId = path.basename(dir);
  const raw = [];
  for (const e of readLines(path.join(dir, "events.jsonl"))) {
    raw.push({ ts: e.ts, attempt: e.attempt ?? null, kind: "state", text: stateText(e), locators: [], data: { event: e.event } });
  }
  let names = [];
  try { names = fs.readdirSync(dir).filter((f) => /^a\d+\.transcript\.jsonl$/.test(f)).sort((a, b) => parseInt(a.slice(1), 10) - parseInt(b.slice(1), 10)); } catch { /* no dir */ }
  for (const f of names) {
    const attempt = parseInt(f.slice(1), 10);
    for (const t of readLines(path.join(dir, f))) {
      const p = t.args?.path;
      const failed = /^error:/i.test(String(t.result || ""));
      raw.push({ ts: t.ts || null, attempt, kind: "tool",
        text: `${t.tool}${p ? ` ${p}` : t.args?.command ? ` ${cut(t.args.command, 120)}` : t.args?.query ? ` ${cut(t.args.query, 80)}` : ""}${failed ? " (error)" : ""}`,
        locators: p ? [{ path: String(p).replace(/\\/g, "/") }] : [],
        data: { step: t.step ?? null, tool: t.tool, ok: !failed } });
    }
  }
  // order: events carry a timestamp; transcript lines may not, so they keep their place after the state event that opened the attempt
  const stateIdx = new Map();
  raw.forEach((e, i) => { if (e.kind === "state" && e.data.event === "running") stateIdx.set(e.attempt, i); });
  const ordered = raw.map((e, i) => ({ e, i, k: e.kind === "tool" && stateIdx.has(e.attempt) ? stateIdx.get(e.attempt) + 0.5 : i }))
    .sort((a, b) => a.k - b.k || a.i - b.i).map((x) => x.e);
  // the end of each attempt: its checks and the files it touched, from run.json
  const rec = (() => { try { return JSON.parse(fs.readFileSync(path.join(dir, "run.json"), "utf8")); } catch { return null; } })();
  for (const a of rec?.attempts || []) {
    if (!a.state || a.state === "running") continue;
    const checks = (a.checks || []).slice(0, 6).map((c) => ({ cmd: cut(c.cmd, 160), exit: c.exit }));
    const files = (a.diff?.files || []).slice(0, 30);
    ordered.push({ ts: a.finished_at || null, attempt: a.attempt, kind: "final",
      text: `attempt ${a.attempt} ${a.verified ? "passed its checks" : a.state}${a.model ? ` (${a.model})` : ""}${files.length ? `; changed ${files.length} file(s)` : ""}`,
      locators: files.map((f) => ({ path: String(f).replace(/\\/g, "/") })),
      data: { state: a.state, verified: !!a.verified, duration_s: a.duration_s ?? null, checks, files } });
  }
  return ordered.map((e, n) => {
    const scrub = (s) => redact(String(s), { secrets });
    return { id: `${runId}:${n}`, run_id: runId, ts: e.ts, attempt: e.attempt, kind: e.kind, text: scrub(e.text),
      locators: e.locators, data: JSON.parse(scrub(JSON.stringify(e.data))) };
  });
}

// RFC 4122 version-5 UUID of `name` under a fixed namespace: the room route wants a UUID per event and a repost must be the same one.
const NS = Buffer.from("6b656c6c2d72756e2d6576656e74732d", "hex");
export function eventUuid(name) {
  const h = crypto.createHash("sha1").update(NS).update(String(name)).digest();
  h[6] = (h[6] & 0x0f) | 0x50; h[8] = (h[8] & 0x3f) | 0x80;
  const x = h.subarray(0, 16).toString("hex");
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20, 32)}`;
}

export const ROOM_HANDLE = "@kell-run";
export const ROOM_BATCH = 50;

// A run event as the room route takes it: activity is a `note`, an attempt's end a `task_update`. `lamport`: the caller's
// counter. `depth`: agent hops (a kell run started by a person's prompt is 1).
export function toRoomEvent(ev, { lamport = 0, depth = 1 } = {}) {
  const files = (ev.locators || []).map((l) => l.path).slice(0, 12);
  const body = [ev.text, files.length ? `Files: ${files.join(", ")}` : "",
    ev.kind === "final" && ev.data?.checks?.length ? `Checks: ${ev.data.checks.map((c) => `${c.cmd} -> exit ${c.exit}`).join("; ")}` : ""]
    .filter(Boolean).join("\n");
  return { id: eventUuid(ev.id), kind: ev.kind === "final" ? "task_update" : "note", body, lamport, depth,
    sender_kind: "agent", sender_handle: ROOM_HANDLE };
}

// POST events to a room. -> { posted, retry: [ids], unavailable?, unauthorized?, error? }. 404/405: the server has no rooms route;
// 401/403: the token is not a website-session token (or not a member); a rejection with retry:true is left for the next flush.
export async function postToRoom({ baseUrl, token, roomId, events, fetchImpl = globalThis.fetch }) {
  if (!events.length) return { posted: 0, retry: [] };
  try {
    const res = await fetchImpl(`${String(baseUrl).replace(/\/$/, "")}/v1/rooms/${encodeURIComponent(roomId)}/events`, {
      method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${token}` }, body: JSON.stringify({ events }) });
    if (res.status === 404 || res.status === 405) return { posted: 0, retry: [], unavailable: true };
    if (res.status === 401 || res.status === 403) return { posted: 0, retry: [], unauthorized: true };
    if (!res.ok) return { posted: 0, retry: [], error: `HTTP ${res.status}` };
    const body = await res.json().catch(() => ({}));
    const rejected = body.rejected || [];
    return { posted: (body.accepted || events).length, retry: rejected.filter((r) => r.retry).map((r) => r.id), rejected };
  } catch (err) {
    return { posted: 0, retry: [], error: String(err.message || err).slice(0, 120) };
  }
}

// Follow a run: every `everyMs`, post the events not yet sent. -> stop(): sends what is left, then resolves. It never throws and
// never blocks the run; once the server says the route is unavailable it stops asking.
export function followRun({ dir, baseUrl, token, roomId, secrets = [], everyMs = 2000, fetchImpl, onResult = () => {} }) {
  let sent = 0, lamport = 0, dead = false, busy = Promise.resolve();
  const flush = () => {
    busy = busy.then(async () => {
      if (dead) return;
      const all = readRunEvents(dir, { secrets });
      for (;;) {
        const fresh = all.slice(sent, sent + ROOM_BATCH);
        if (!fresh.length) return;
        const wire = fresh.map((e, i) => toRoomEvent(e, { lamport: sent + i + 1 }));
        const r = await postToRoom({ baseUrl, token, roomId, events: wire, fetchImpl });
        onResult(r);
        if (r.unavailable || r.unauthorized) { dead = true; return; }
        if (r.error) return;
        // advance over the events that were stored or refused for good; stop at the first one to retry later
        const retry = new Set(r.retry);
        const firstRetry = wire.findIndex((w) => retry.has(w.id));
        sent += firstRetry === -1 ? fresh.length : firstRetry;
        if (firstRetry !== -1) return;
      }
    }).catch(() => {});
    return busy;
  };
  const timer = setInterval(flush, everyMs);
  timer.unref?.();
  return async () => { clearInterval(timer); await flush(); };
}
