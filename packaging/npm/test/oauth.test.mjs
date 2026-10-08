import { test } from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";

import { discover, freshToken, login, logoutConfig, pkcePair } from "../lib/oauth.mjs";
import { readConfig, writeConfig } from "../lib/config.mjs";

const tmpEnv = () => ({ STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "sl-oauth-")) });

// A real HTTP authorization server on loopback: protected-resource document, RFC 8414 metadata, dynamic client
// registration, /authorize (redirects straight back, as a signed-in browser would after consent) and /token with
// real PKCE verification and refresh-token rotation. `opts` lets a test break one step.
async function authServer(opts = {}) {
  const state = { clients: new Map(), codes: new Map(), refresh: new Map(), tokenCalls: [], seq: 0 };
  const server = http.createServer(async (req, res) => {
    const u = new URL(req.url, "http://x");
    const send = (status, body, headers = {}) => res.writeHead(status, { "content-type": "application/json", ...headers }).end(typeof body === "string" ? body : JSON.stringify(body));
    const readBody = async () => { const c = []; for await (const x of req) c.push(x); return Buffer.concat(c).toString("utf8"); };
    const origin = `http://127.0.0.1:${server.address().port}`;
    if (u.pathname === "/.well-known/oauth-protected-resource/mcp") {
      return send(200, { resource: `${origin}/mcp`, authorization_servers: opts.noAuthServer ? [] : [`${origin}/auth/v1`] });
    }
    if (u.pathname === "/.well-known/oauth-authorization-server/auth/v1") {
      return send(200, {
        issuer: `${origin}/auth/v1`, authorization_endpoint: `${origin}/auth/v1/oauth/authorize`,
        token_endpoint: `${origin}/auth/v1/oauth/token`, ...(opts.noRegistration ? {} : { registration_endpoint: `${origin}/auth/v1/oauth/clients/register` }),
      });
    }
    if (u.pathname === "/auth/v1/oauth/clients/register" && req.method === "POST") {
      const body = JSON.parse(await readBody());
      assert.equal(body.token_endpoint_auth_method, "none");
      assert.ok(body.grant_types.includes("refresh_token"));
      assert.match(body.redirect_uris[0], /^http:\/\/127\.0\.0\.1:\d+\/callback$/);
      const id = `client-${++state.seq}`;
      state.clients.set(id, body.redirect_uris[0]);
      return send(201, { client_id: id });
    }
    if (u.pathname === "/auth/v1/oauth/authorize") {
      const q = u.searchParams;
      assert.equal(q.get("code_challenge_method"), "S256");
      assert.equal(q.get("response_type"), "code");
      assert.equal(state.clients.get(q.get("client_id")), q.get("redirect_uri"));
      const back = new URL(q.get("redirect_uri"));
      if (opts.deny) back.searchParams.set("error", "access_denied");
      else {
        const code = `code-${++state.seq}`;
        state.codes.set(code, { challenge: q.get("code_challenge"), client: q.get("client_id"), redirect: q.get("redirect_uri") });
        back.searchParams.set("code", code);
      }
      back.searchParams.set("state", opts.wrongState ? "forged" : q.get("state"));
      return send(302, "", { location: back.toString() });
    }
    if (u.pathname === "/auth/v1/oauth/token" && req.method === "POST") {
      const p = new URLSearchParams(await readBody());
      state.tokenCalls.push(Object.fromEntries(p));
      if (p.get("grant_type") === "authorization_code") {
        const c = state.codes.get(p.get("code"));
        const ok = c && c.client === p.get("client_id") && c.redirect === p.get("redirect_uri") &&
          crypto.createHash("sha256").update(p.get("code_verifier") || "").digest("base64url") === c.challenge;
        if (!ok) return send(400, { error: "invalid_grant" });
        state.codes.delete(p.get("code"));
        const refresh = `refresh-${++state.seq}`;
        state.refresh.set(refresh, p.get("client_id"));
        return send(200, { access_token: `access-${++state.seq}`, refresh_token: refresh, expires_in: opts.expiresIn ?? 3600, token_type: "bearer" });
      }
      if (p.get("grant_type") === "refresh_token") {
        if (opts.refreshFails || state.refresh.get(p.get("refresh_token")) !== p.get("client_id")) return send(400, { error: "invalid_grant" });
        state.refresh.delete(p.get("refresh_token"));
        const refresh = `refresh-${++state.seq}`;
        state.refresh.set(refresh, p.get("client_id"));
        return send(200, { access_token: `access-${++state.seq}`, refresh_token: refresh, expires_in: 3600 });
      }
      return send(400, { error: "unsupported_grant_type" });
    }
    send(404, { error: "not found" });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return { server, state, url: `http://127.0.0.1:${server.address().port}/mcp`, close: () => { server.close(); server.closeAllConnections(); } };
}

// The signed-in browser: follow /authorize's redirect back to the CLI's loopback listener.
const browser = async (authorizeUrl) => {
  const r = await fetch(authorizeUrl, { redirect: "manual" });
  await fetch(r.headers.get("location")).catch(() => {});
};

test("pkce: the challenge is the S256 of the verifier", () => {
  const { verifier, challenge } = pkcePair();
  assert.equal(challenge, crypto.createHash("sha256").update(verifier).digest("base64url"));
  assert.notEqual(pkcePair().verifier, verifier);
});

test("discover: protected-resource document -> authorization server metadata", async () => {
  const a = await authServer();
  try {
    const d = await discover(a.url);
    assert.match(d.issuer, /\/auth\/v1$/);
    assert.match(d.meta.token_endpoint, /\/oauth\/token$/);
  } finally { a.close(); }
});

test("discover: a server with no authorization server says so", async () => {
  const a = await authServer({ noAuthServer: true });
  try { await assert.rejects(discover(a.url), /sign-in is not configured/); } finally { a.close(); }
});

test("login: full flow -- registers, PKCE, consent, code exchange; tokens saved 0600-style, refresh kept", async () => {
  const a = await authServer();
  const env = tmpEnv();
  const printed = [];
  try {
    const t = await login({ url: a.url, env, open: browser, print: (s) => printed.push(s) });
    assert.match(t.token, /^access-/);
    const cfg = readConfig(env);
    assert.equal(cfg.token, t.token);
    assert.match(cfg.refresh_token, /^refresh-/);
    assert.ok(cfg.expires_at > Date.now() + 3_000_000);
    assert.equal(cfg.oauth.client_id, "client-1");
    assert.match(printed[0], /\/oauth\/authorize\?.*code_challenge_method=S256/);
    const call = a.state.tokenCalls[0];
    assert.equal(call.grant_type, "authorization_code");
    assert.ok(call.code_verifier.length >= 43);                      // the verifier, never sent until the exchange
    if (process.platform !== "win32") assert.equal(fs.statSync(path.join(env.STEALTHLAB_HOME, "config.json")).mode & 0o777, 0o600);
  } finally { a.close(); }
});

test("login: the user declining is reported, nothing is saved", async () => {
  const a = await authServer({ deny: true });
  const env = tmpEnv();
  try {
    await assert.rejects(login({ url: a.url, env, open: browser }), /access_denied/);
    assert.equal(readConfig(env).token, undefined);
  } finally { a.close(); }
});

test("login: a callback whose state does not match is ignored, so the attempt times out instead of being hijacked", async () => {
  const a = await authServer({ wrongState: true });
  const env = tmpEnv();
  try {
    await assert.rejects(login({ url: a.url, env, open: browser, timeoutMs: 400 }), /no sign-in within/);
    assert.equal(readConfig(env).token, undefined);
    assert.equal(a.state.tokenCalls.length, 0);                     // no code was ever exchanged
  } finally { a.close(); }
});

test("login: a server without dynamic registration fails clearly", async () => {
  const a = await authServer({ noRegistration: true });
  try { await assert.rejects(login({ url: a.url, env: tmpEnv(), open: browser }), /dynamic client registration/); } finally { a.close(); }
});

test("login: --no-browser prints the URL and never opens one", async () => {
  const a = await authServer();
  const env = tmpEnv();
  let opened = 0;
  try {
    const url = new Promise((resolve) => {
      login({ url: a.url, env, noBrowser: true, open: () => { opened++; }, print: (s) => resolve(s.split("\n").pop()) }).catch(() => {});
    });
    await browser(await url);                                       // the person pastes the URL into their own browser
    await new Promise((r) => setTimeout(r, 200));
    assert.equal(opened, 0);
    assert.match(readConfig(env).token, /^access-/);
  } finally { a.close(); }
});

test("freshToken: refreshes shortly before expiry and saves the rotated pair", async () => {
  const a = await authServer({ expiresIn: 30 });                    // already inside the two-minute skew
  const env = tmpEnv();
  try {
    await login({ url: a.url, env, open: browser });
    const before = readConfig(env);
    const token = await freshToken({ token: before.token }, { env });
    assert.notEqual(token, before.token);
    const after = readConfig(env);
    assert.equal(after.token, token);
    assert.notEqual(after.refresh_token, before.refresh_token);     // rotated
    assert.equal(after.oauth.client_id, before.oauth.client_id);
    assert.equal(a.state.tokenCalls.at(-1).grant_type, "refresh_token");
  } finally { a.close(); }
});

test("freshToken: a token with plenty of life is left alone (no network call)", async () => {
  const env = tmpEnv();
  writeConfig({ token: "T", refresh_token: "R", expires_at: Date.now() + 3_000_000, oauth: { client_id: "c", token_endpoint: "http://127.0.0.1:1/token" } }, env);
  const boom = async () => { throw new Error("must not be called"); };
  assert.equal(await freshToken({ token: "T" }, { env, fetchImpl: boom }), "T");
});

test("freshToken: a failed refresh falls back to the saved token (never blocks a prompt)", async () => {
  const a = await authServer({ expiresIn: 30, refreshFails: true });
  const env = tmpEnv();
  try {
    await login({ url: a.url, env, open: browser });
    const saved = readConfig(env);
    assert.equal(await freshToken({ token: saved.token }, { env }), saved.token);
    assert.deepEqual(readConfig(env).refresh_token, saved.refresh_token);   // not clobbered
  } finally { a.close(); }
});

test("freshToken: an explicit token (flag or env) and a pasted token with no refresh data are never replaced", async () => {
  const env = tmpEnv();
  writeConfig({ token: "SAVED", refresh_token: "R", expires_at: 1, oauth: { client_id: "c", token_endpoint: "http://127.0.0.1:1/t" } }, env);
  const boom = async () => { throw new Error("must not be called"); };
  assert.equal(await freshToken({ token: "EXPLICIT" }, { env, fetchImpl: boom }), "EXPLICIT");
  const env2 = tmpEnv();
  writeConfig({ token: "PASTED" }, env2);
  assert.equal(await freshToken({ token: "PASTED" }, { env: env2, fetchImpl: boom }), "PASTED");
});

test("logout clears the token, the refresh token and the client record", async () => {
  const env = tmpEnv();
  writeConfig({ url: "http://x/mcp", token: "T", refresh_token: "R", expires_at: 5, oauth: { client_id: "c" } }, env);
  logoutConfig(env);
  assert.deepEqual(readConfig(env), { url: "http://x/mcp" });
});
