// `stealthlab-mcp join <code>`: a developer's whole setup when their company has already connected its provider key.
//
// Why: `setup` still asks each person for a provider key in an environment variable. Here the company's admin stores that
// key once in the admin console (backend/app/services/org_keys.py); a developer needs only the invite the admin created.
// This module does the parts that are not agent registration (that is the caller's `install`):
//
//   1. sign in (browser OAuth, lib/oauth.mjs) unless a token is already saved;
//   2. redeem the invite at POST <api>/v1/join: the server adds the person to the organization and answers with the models,
//      their prices and the gateway address;
//   3. write ~/.stealthlab/exec.json from that answer, as profiles with  key_from: "stealthlab"  (no provider key anywhere on
//      this machine; see lib/executors/profile.mjs). A file the person wrote themselves is moved aside, never overwritten;
//   4. save the address and organization in config.json;
//   5. make ONE tiny request through the gateway with the cheapest model. That request is the self-test: it proves the sign-in
//      is accepted, the company's policy allows the model and the stored key works, and its ledger row is what the admin's
//      dashboard counts as "ready".
//
// What it never does: ask for, read, store or print a provider key; or send the invite code anywhere except <api>/v1/join.
import fs from "node:fs";
import { execConfigPath, writePrivate } from "./exec/store.mjs";
import { normaliseProfiles } from "./executors/profile.mjs";

export const MANAGED_BY = "stealthlab-join";
const CODE = /^sl1_[0-9a-f]{32}_[0-9a-f]{24}$/;
const SELF_TEST_PROMPT = "Reply with the single word: ok";

export function parseInvite(code) {
  const c = String(code || "").trim();
  if (!CODE.test(c)) throw new Error("that is not a StealthLab invite code (it starts with sl1_); ask your admin for a new one");
  return c;
}

// The API address: https, or http only on this machine. No path: /v1/... is added here.
export function normalizeApi(raw) {
  let u;
  try { u = new URL(String(raw || "").trim()); } catch { throw new Error(`--api is not a URL: ${raw}`); }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname);
  if (u.protocol !== "https:" && !(u.protocol === "http:" && loopback)) throw new Error(`--api must be https (http only for this machine): ${raw}`);
  if (u.username || u.password) throw new Error("--api must not contain credentials");
  return `${u.protocol}//${u.host}`;
}

async function readError(res) {
  const text = await res.text().catch(() => "");
  try { const j = JSON.parse(text); return String(j?.error?.message || j?.detail?.message || j?.detail || j?.message || text).slice(0, 300); } catch { return text.slice(0, 300); }
}

export async function redeem({ api, token, code, fetchImpl = fetch, timeoutMs = 20000 }) {
  let res;
  try {
    res = await fetchImpl(`${api}/v1/join`, { method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
                                              body: JSON.stringify({ code }), signal: AbortSignal.timeout(timeoutMs) });
  } catch (err) {
    throw new Error(`cannot reach ${api} (${err.cause?.message || err.message})`);
  }
  if (res.status === 401) throw new Error("the server did not accept your sign-in; run `stealthlab-mcp login` and try again");
  if (res.status === 404) throw new Error("this invite is not valid, has expired, or was revoked; ask your admin for a new one");
  if (!res.ok) throw new Error(`the server refused the invite (HTTP ${res.status}): ${await readError(res)}`);
  const body = await res.json();
  if (!body?.organization_id || !body.profiles || !Object.keys(body.profiles).length) throw new Error("the server answered without any models");
  return body;
}

// exec.json from the server's answer. Validates through the same code that reads it, so a bad answer fails here, not at the
// first prompt. A file that is not ours is kept as exec.json.before-join.
export function writeExec(answer, { env = process.env } = {}) {
  const file = execConfigPath(env);
  const models = Object.keys(answer.profiles);
  const doc = { managed_by: MANAGED_BY, organization_id: answer.organization_id, executors: { stealth: { models } },
                profiles: answer.profiles, max_concurrent_runs: 2 };
  normaliseProfiles(doc.profiles);
  let movedTo = null;
  if (fs.existsSync(file)) {
    let ours = false;
    try { ours = JSON.parse(fs.readFileSync(file, "utf8"))?.managed_by === MANAGED_BY; } catch { /* unreadable: treat as not ours */ }
    if (!ours) { movedTo = `${file}.before-join`; fs.renameSync(file, movedTo); }
  }
  writePrivate(file, `${JSON.stringify(doc, null, 2)}\n`);
  return { file, models, movedTo };
}

// The cheapest model by list price (input + output per million tokens); a model without prices sorts last.
export function cheapest(profiles) {
  const price = (p) => (typeof p.input_per_mtok === "number" && typeof p.output_per_mtok === "number" ? p.input_per_mtok + p.output_per_mtok : Infinity);
  return Object.values(profiles).sort((a, b) => price(a) - price(b))[0];
}

export async function selfTest({ gateway, token, orgId, profile, fetchImpl = fetch, timeoutMs = 60000, now = Date.now }) {
  const started = now();
  let res;
  try {
    res = await fetchImpl(`${gateway}/chat/completions`, {
      method: "POST", signal: AbortSignal.timeout(timeoutMs),
      headers: { "content-type": "application/json", authorization: `Bearer ${token}`, "x-stealthlab-org": orgId },
      body: JSON.stringify({ model: profile.model_id, max_tokens: 8, messages: [{ role: "user", content: SELF_TEST_PROMPT }] }),
    });
  } catch (err) {
    return { ok: false, detail: `the gateway did not answer (${err.cause?.message || err.message})` };
  }
  if (!res.ok) {
    const why = await readError(res);
    const hint = res.status === 401 ? "your sign-in was not accepted" : res.status === 403 ? "your organization's policy does not allow this call" :
                 res.status === 402 || res.status === 429 ? "a budget or rate limit was reached" : `HTTP ${res.status}`;
    return { ok: false, detail: `${hint}: ${why}` };
  }
  let body;
  try { body = await res.json(); } catch { return { ok: false, detail: "the gateway answered, but not with a model reply" }; }
  if (!Array.isArray(body?.choices) || !body.choices.length) return { ok: false, detail: "the gateway answered, but not with a model reply" };
  return { ok: true, detail: `Done by ${profile.model_id} in ${now() - started} ms`, model: profile.model_id };
}

// -> { ready, items: [{ name, ok, detail, fix? }] }. Pure.
export function joinChecklist({ org, models, selfTest: st, hooksOk, wantsCursor, cursorModel }) {
  const items = [
    { name: "organization", ok: Boolean(org), detail: org ? `joined ${org}` : "not joined" },
    { name: "models", ok: models.length > 0, detail: models.length ? models.join(", ") : "none" },
    { name: "gateway test", ok: st.ok, detail: st.detail, fix: st.ok ? undefined : "tell your admin; the line above is what the server said" },
    { name: "agent hooks", ok: hooksOk, detail: hooksOk ? "installed" : "not installed", fix: hooksOk ? undefined : "stealthlab-mcp install --with-exec" },
  ];
  if (wantsCursor) {
    items.push({ name: "cursor model", ok: Boolean(cursorModel), detail: cursorModel || "not set ('auto' is not a model: dispatch stays off)",
                 fix: cursorModel ? undefined : "stealthlab-mcp join <code> --cursor-model <the model you picked in Cursor>" });
  }
  return { ready: items.every((i) => i.ok), items };
}
