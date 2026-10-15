// A local bridge so that Claude Code -- the agent, its tools, its context handling -- can drive ANY open model of exec.json:
// Claude Code speaks Anthropic's Messages API, most providers only OpenAI's chat completions (General Compute: no
// /v1/messages). The bridge listens on 127.0.0.1, takes Claude Code's requests and sends them to the profile's
// openai_base_url, translating both ways (system, text, tool_use / tool_result, tools, stop reasons, usage).
//
// Why: our own minimal agent loop (lib/agent/step_agent.mjs) needed ~30 s per step for glm-5.3-flash on a large repo
// (sqlglot: 18 steps in 10 minutes, no finish); Claude Code works far more efficiently per step. Same models, a better
// agent.
//
// Safety: the provider key stays in THIS process (the bridge adds it upstream); the Claude Code child only gets a
// random per-process secret for the bridge, which refuses any request without it. Listens on loopback only.
// Not streamed upstream: the reply is fetched whole, then sent to Claude Code as one SSE stream (simple and robust;
// the model's own generation time dominates).
import http from "node:http";
import crypto from "node:crypto";
import { profileKey } from "./executors/profile.mjs";

let shared = null;          // {server, port, secret, profiles: Map(name -> profile), env}

const textOf = (c) => (typeof c === "string" ? c
  : Array.isArray(c) ? c.map((b) => (b?.type === "text" ? b.text : b?.type === "image" ? "[image omitted]" : "")).filter(Boolean).join("\n") : "");

// Anthropic request -> OpenAI chat completions body
export function toOpenAI(req, profile) {
  const messages = [];
  const sys = Array.isArray(req.system) ? req.system.map((b) => b?.text || "").join("\n\n") : String(req.system || "");
  if (sys && !profile.no_system_role) messages.push({ role: "system", content: sys });
  let pendingSystem = sys && profile.no_system_role ? sys : "";
  for (const m of req.messages || []) {
    const blocks = typeof m.content === "string" ? [{ type: "text", text: m.content }] : (m.content || []);
    if (m.role === "assistant") {
      const text = blocks.filter((b) => b.type === "text").map((b) => b.text).join("\n");
      const calls = blocks.filter((b) => b.type === "tool_use").map((b) => ({ id: b.id, type: "function",
        function: { name: b.name, arguments: JSON.stringify(b.input ?? {}) } }));
      messages.push({ role: "assistant", content: text || (calls.length ? null : ""), ...(calls.length ? { tool_calls: calls } : {}) });
    } else {
      for (const b of blocks.filter((x) => x.type === "tool_result")) {
        messages.push({ role: "tool", tool_call_id: b.tool_use_id, content: (b.is_error ? "ERROR: " : "") + (textOf(b.content) || "(no output)") });
      }
      const text = blocks.filter((b) => b.type === "text" || b.type === "image").map((b) => (b.type === "text" ? b.text : "[image omitted]")).join("\n");
      if (text) {
        messages.push({ role: "user", content: pendingSystem ? `${pendingSystem}\n\n${text}` : text });
        pendingSystem = "";
      }
    }
  }
  const tools = (req.tools || []).filter((t) => t.name && t.input_schema).map((t) => ({ type: "function",
    function: { name: t.name, description: String(t.description || "").slice(0, 4000), parameters: t.input_schema } }));
  const choice = req.tool_choice?.type === "any" ? "required" : req.tool_choice?.type === "tool" ? { type: "function", function: { name: req.tool_choice.name } }
    : req.tool_choice?.type === "none" ? "none" : undefined;
  const maxOut = Math.min(Number(req.max_tokens) || 8000, Number(profile.max_output_tokens) || 16000);
  return {
    model: profile.model_id, messages, max_tokens: maxOut,
    ...(tools.length ? { tools } : {}), ...(tools.length && choice ? { tool_choice: choice } : {}),
    ...(req.stop_sequences?.length ? { stop: req.stop_sequences.slice(0, 4) } : {}),
    ...(profile.request_extras || {}),
  };
}

// OpenAI chat completion -> Anthropic message
export function toAnthropic(resp, model) {
  const ch = resp?.choices?.[0] || {};
  const msg = ch.message || {};
  const content = [];
  if (msg.content) content.push({ type: "text", text: String(msg.content) });
  for (const c of msg.tool_calls || []) {
    let input = {};
    try { input = c.function?.arguments ? JSON.parse(c.function.arguments) : {}; } catch { input = { _raw: c.function?.arguments }; }
    content.push({ type: "tool_use", id: c.id || `toolu_${crypto.randomBytes(8).toString("hex")}`, name: c.function?.name, input });
  }
  if (!content.length) content.push({ type: "text", text: "" });
  const stop = (msg.tool_calls || []).length || ch.finish_reason === "tool_calls" ? "tool_use"
    : ch.finish_reason === "length" ? "max_tokens" : "end_turn";
  return {
    id: `msg_${crypto.randomBytes(10).toString("hex")}`, type: "message", role: "assistant", model, content,
    stop_reason: stop, stop_sequence: null,
    usage: { input_tokens: resp?.usage?.prompt_tokens || 0, output_tokens: resp?.usage?.completion_tokens || 0 },
  };
}

// the message as Claude Code's stream (server-sent events)
export function sseOf(message) {
  const ev = (type, data) => `event: ${type}\ndata: ${JSON.stringify({ type, ...data })}\n\n`;
  let out = ev("message_start", { message: { ...message, content: [], stop_reason: null, usage: { input_tokens: message.usage.input_tokens, output_tokens: 0 } } });
  message.content.forEach((b, i) => {
    if (b.type === "text") {
      out += ev("content_block_start", { index: i, content_block: { type: "text", text: "" } });
      if (b.text) out += ev("content_block_delta", { index: i, delta: { type: "text_delta", text: b.text } });
    } else {
      out += ev("content_block_start", { index: i, content_block: { type: "tool_use", id: b.id, name: b.name, input: {} } });
      out += ev("content_block_delta", { index: i, delta: { type: "input_json_delta", partial_json: JSON.stringify(b.input) } });
    }
    out += ev("content_block_stop", { index: i });
  });
  out += ev("message_delta", { delta: { stop_reason: message.stop_reason, stop_sequence: null }, usage: { output_tokens: message.usage.output_tokens } });
  out += ev("message_stop", {});
  return out;
}

async function handle(req, res, state, fetchImpl) {
  const send = (code, obj) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(obj)); };
  const m = /^\/p\/([^/]+)(\/v1\/messages(?:\/count_tokens)?)/.exec(req.url || "");
  const token = req.headers["x-api-key"] || String(req.headers.authorization || "").replace(/^Bearer\s+/i, "");
  if (token !== state.secret) return send(401, { type: "error", error: { type: "authentication_error", message: "bad bridge secret" } });
  const profile = m && state.profiles.get(decodeURIComponent(m[1]));
  if (!profile || req.method !== "POST") return send(404, { type: "error", error: { type: "not_found_error", message: "no such route" } });
  let raw = "";
  for await (const chunk of req) raw += chunk;
  let body;
  try { body = JSON.parse(raw || "{}"); } catch { return send(400, { type: "error", error: { type: "invalid_request_error", message: "bad JSON" } }); }
  if (m[2].endsWith("count_tokens")) return send(200, { input_tokens: Math.ceil(raw.length / 4) });
  try {
    const up = await fetchImpl(`${profile.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(600000),
      headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, state.env)}` },
      body: JSON.stringify(toOpenAI(body, profile)),
    });
    const text = await up.text();
    if (!up.ok) {
      const kind = up.status === 429 ? "rate_limit_error" : up.status >= 500 ? "api_error" : "invalid_request_error";
      return send(up.status === 429 ? 429 : up.status >= 500 ? 529 : 400, { type: "error", error: { type: kind, message: `upstream ${up.status}: ${text.slice(0, 300)}` } });
    }
    const message = toAnthropic(JSON.parse(text), profile.model_id);
    if (body.stream) {
      res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
      return res.end(sseOf(message));
    }
    return send(200, message);
  } catch (err) {
    return send(529, { type: "error", error: { type: "overloaded_error", message: `bridge: ${err.message}` } });
  }
}

// -> {baseUrl, secret} for this profile (the bridge starts on first use and lives as long as this process)
export async function bridgeFor(profile, { env = process.env, fetchImpl = globalThis.fetch } = {}) {
  if (!profile?.openai_base_url) throw new Error(`profile ${profile?.name} has no openai_base_url for the bridge`);
  if (!shared) {
    const state = { secret: crypto.randomBytes(24).toString("hex"), profiles: new Map(), env, fetchImpl };
    const server = http.createServer((req, res) => { handle(req, res, state, state.fetchImpl).catch(() => { try { res.writeHead(500); res.end(); } catch { /* */ } }); });
    await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    server.unref();
    shared = { server, state, port: server.address().port };
  }
  shared.state.profiles.set(profile.name, profile);
  shared.state.env = env;
  shared.state.fetchImpl = fetchImpl;
  return { baseUrl: `http://127.0.0.1:${shared.port}/p/${encodeURIComponent(profile.name)}`, secret: shared.state.secret };
}

export function closeBridge() {
  if (shared) { try { shared.server.close(); } catch { /* */ } shared = null; }
}
