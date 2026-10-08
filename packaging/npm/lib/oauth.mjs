// Browser sign-in for the CLI and its hooks: OAuth 2.1 authorization-code flow with PKCE (S256), the same flow a
// remote MCP client runs (backend/app/mcp_server/oauth_resource.py describes the server side).
//
// Why this exists: `login --token <jwt>` meant copying a Supabase access token out of a browser, and it expires in
// about an hour, so the hook (which has no MCP client to sign in for it) stopped working after the first hour.
// Here the CLI is itself an OAuth client:
//
//   1. discover   <origin>/.well-known/oauth-protected-resource/mcp  ->  the authorization server (RFC 9728),
//                 then its metadata (RFC 8414, else OpenID discovery)
//   2. register   dynamic client registration (RFC 7591), a public client with a loopback redirect
//   3. authorize  open the browser at the authorization endpoint with a PKCE challenge; the user signs in and
//                 approves on the website's consent page; the browser comes back to http://127.0.0.1:<port>/callback
//   4. exchange   code + verifier -> access token + refresh token; both are saved in ~/.stealthlab/config.json (0600)
//   5. refresh    freshToken() trades the refresh token for a new access token shortly before the old one expires,
//                 so the hooks keep working without anyone signing in again
//
// Nothing here ever blocks a prompt: every failure path returns what it had (the saved token) and the caller carries on.
import crypto from "node:crypto";
import http from "node:http";
import { spawn } from "node:child_process";
import { readConfig, writeConfig } from "./config.mjs";

const SCOPE = "openid email profile";          // what Supabase's OAuth server can issue (backend oauth_resource.py)
const REFRESH_SKEW_MS = 120_000;                 // refresh when the access token has under two minutes left
export const CLIENT_NAME = "stealthlab-mcp CLI";

const b64url = (buf) => Buffer.from(buf).toString("base64url");
export const pkcePair = () => {
  const verifier = b64url(crypto.randomBytes(48));
  return { verifier, challenge: b64url(crypto.createHash("sha256").update(verifier).digest()) };
};

async function getJson(fetchImpl, url, timeoutMs = 10000) {
  const res = await fetchImpl(url, { headers: { accept: "application/json" }, signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return JSON.parse(await res.text());
}

// The server's protected-resource document, then the authorization server's metadata.
export async function discover(mcpUrl, { fetchImpl = globalThis.fetch } = {}) {
  const u = new URL(mcpUrl);
  const prmUrl = `${u.origin}/.well-known/oauth-protected-resource${u.pathname.replace(/\/$/, "")}`;
  const prm = await getJson(fetchImpl, prmUrl);
  const issuer = Array.isArray(prm.authorization_servers) ? prm.authorization_servers[0] : null;
  if (!issuer) throw new Error("the server names no authorization server: sign-in is not configured there (use --token)");
  const i = new URL(issuer);
  const path = i.pathname.replace(/\/$/, "");
  const candidates = [
    `${i.origin}/.well-known/oauth-authorization-server${path}`,   // RFC 8414: well-known inserted before the path
    `${issuer.replace(/\/$/, "")}/.well-known/oauth-authorization-server`,
    `${issuer.replace(/\/$/, "")}/.well-known/openid-configuration`,
  ];
  let meta = null;
  for (const c of candidates) {
    try { meta = await getJson(fetchImpl, c); break; } catch { /* try the next */ }
  }
  if (!meta?.authorization_endpoint || !meta?.token_endpoint) throw new Error(`no usable OAuth metadata for ${issuer}`);
  return { issuer, resource: prm.resource || `${u.origin}${u.pathname}`, meta };
}

async function register(meta, redirectUri, { fetchImpl }) {
  if (!meta.registration_endpoint) throw new Error("the authorization server does not allow dynamic client registration");
  const res = await fetchImpl(meta.registration_endpoint, {
    method: "POST", headers: { "content-type": "application/json", accept: "application/json" },
    signal: AbortSignal.timeout(10000),
    body: JSON.stringify({
      client_name: CLIENT_NAME, redirect_uris: [redirectUri], grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"], token_endpoint_auth_method: "none", scope: SCOPE,
    }),
  });
  if (!res.ok) throw new Error(`client registration failed (HTTP ${res.status})`);
  const body = JSON.parse(await res.text());
  if (!body.client_id) throw new Error("client registration returned no client_id");
  return body.client_id;
}

async function tokenRequest(tokenEndpoint, params, { fetchImpl, now }) {
  const res = await fetchImpl(tokenEndpoint, {
    method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", accept: "application/json" },
    signal: AbortSignal.timeout(15000), body: new URLSearchParams(params).toString(),
  });
  const text = await res.text();
  let body = {};
  try { body = JSON.parse(text); } catch { /* not JSON */ }
  if (!res.ok || !body.access_token) {
    throw new Error(`token request failed (HTTP ${res.status}${body.error ? `: ${body.error}` : ""})`);
  }
  return {
    token: body.access_token,
    refresh_token: body.refresh_token || params.refresh_token || undefined,
    expires_at: body.expires_in ? now() + Number(body.expires_in) * 1000 : undefined,
  };
}

export function openBrowser(url, platform = process.platform) {
  const [cmd, args] = platform === "win32" ? ["rundll32", ["url.dll,FileProtocolHandler", url]]
    : platform === "darwin" ? ["open", [url]] : ["xdg-open", [url]];
  try {
    const child = spawn(cmd, args, { stdio: "ignore", detached: true });
    child.on("error", () => {});
    child.unref();
    return true;
  } catch {
    return false;
  }
}

// One loopback listener for the redirect. Resolves with the authorization code once, ignores everything else.
function listenForCode({ expectState, timeoutMs }) {
  let resolveCode, rejectCode;
  const done = new Promise((res, rej) => { resolveCode = res; rejectCode = rej; });
  const page = (title, msg) => `<!doctype html><meta charset=utf-8><title>${title}</title>` +
    `<body style="font:16px system-ui;max-width:32rem;margin:4rem auto"><h2>${title}</h2><p>${msg}</p>`;
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, "http://127.0.0.1");
    if (u.pathname !== "/callback") { res.writeHead(404).end(); return; }
    const reply = (status, title, msg) => { res.writeHead(status, { "content-type": "text/html; charset=utf-8" }).end(page(title, msg)); };
    if (u.searchParams.get("state") !== expectState) {
      reply(400, "Sign-in failed", "The response did not match this sign-in attempt. Close this tab and run login again.");
      return;                                                        // a stray or forged request: keep waiting
    }
    const err = u.searchParams.get("error");
    const code = u.searchParams.get("code");
    if (err || !code) {
      reply(400, "Sign-in not completed", `The server said: ${err || "no code"}. You can close this tab.`);
      rejectCode(new Error(`sign-in was not completed (${err || "no code returned"}${u.searchParams.get("error_description") ? `: ${u.searchParams.get("error_description")}` : ""})`));
      return;
    }
    reply(200, "Signed in", "StealthLab is connected. You can close this tab and return to your terminal.");
    resolveCode(code);
  });
  const timer = setTimeout(() => rejectCode(new Error(`no sign-in within ${Math.round(timeoutMs / 1000)}s`)), timeoutMs);
  done.catch(() => {});                                              // a rejection nobody awaited yet is not a crash
  const close = () => { clearTimeout(timer); server.close(); server.closeAllConnections?.(); };
  return new Promise((resolve, reject) => {
    server.once("error", (e) => { close(); reject(e); });
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({ redirectUri: `http://127.0.0.1:${port}/callback`, code: done, close });
    });
  });
}

// The whole sign-in. `open` opens the authorize URL (tests pass a fake "browser"); `print` shows it for headless
// machines. Saves the tokens and returns them.
export async function login({ url, env = process.env, fetchImpl = globalThis.fetch, open = openBrowser, print = () => {},
                              timeoutMs = 300_000, now = Date.now, noBrowser = false } = {}) {
  if (!url) throw new Error("no server URL: run `install --url <https://host/mcp>` first or pass --url");
  const { issuer, meta } = await discover(url, { fetchImpl });
  const state = b64url(crypto.randomBytes(24));
  const { verifier, challenge } = pkcePair();
  const listener = await listenForCode({ expectState: state, timeoutMs });
  let clientId, tokens;
  try {                                                              // whatever fails, the loopback listener must not outlive it
    clientId = await register(meta, listener.redirectUri, { fetchImpl });
    const authorize = new URL(meta.authorization_endpoint);
    for (const [k, v] of Object.entries({
      response_type: "code", client_id: clientId, redirect_uri: listener.redirectUri, scope: SCOPE,
      state, code_challenge: challenge, code_challenge_method: "S256",
    })) authorize.searchParams.set(k, v);
    print(`Opening your browser to sign in. If it does not open, visit:\n${authorize.toString()}`);
    if (!noBrowser) open(authorize.toString());
    const code = await listener.code;
    tokens = await tokenRequest(meta.token_endpoint, {
      grant_type: "authorization_code", code, redirect_uri: listener.redirectUri, client_id: clientId, code_verifier: verifier,
    }, { fetchImpl, now });
  } finally {
    listener.close();
  }
  writeConfig({
    token: tokens.token, refresh_token: tokens.refresh_token, expires_at: tokens.expires_at,
    oauth: { client_id: clientId, token_endpoint: meta.token_endpoint, issuer },
  }, env);
  return tokens;
}

// The access token to use now. Refreshes it (and saves the rotated pair) when it is about to expire; any failure
// returns the saved token unchanged -- a refresh problem must never keep a prompt from going through.
export async function freshToken(settings, { env = process.env, fetchImpl = globalThis.fetch, now = Date.now } = {}) {
  try {
    const cfg = readConfig(env);
    if (!cfg.refresh_token || !cfg.oauth?.token_endpoint || !cfg.oauth?.client_id) return settings.token;
    if (settings.token && settings.token !== cfg.token) return settings.token;   // an explicit flag/env token wins
    if (cfg.expires_at && cfg.expires_at - REFRESH_SKEW_MS > now()) return settings.token;
    const t = await tokenRequest(cfg.oauth.token_endpoint, {
      grant_type: "refresh_token", refresh_token: cfg.refresh_token, client_id: cfg.oauth.client_id,
    }, { fetchImpl, now });
    writeConfig({ token: t.token, refresh_token: t.refresh_token, expires_at: t.expires_at }, env);
    return t.token;
  } catch {
    return settings.token;
  }
}

export function logoutConfig(env = process.env) {
  writeConfig({ token: undefined, refresh_token: undefined, expires_at: undefined, oauth: undefined }, env);
}
