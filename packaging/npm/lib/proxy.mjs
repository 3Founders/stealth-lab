// stdio <-> Streamable HTTP relay.
//
// Agents launch `stealthlab-mcp` as a local stdio MCP server; every JSON-RPC
// line it receives is POSTed verbatim to the hosted /mcp endpoint and every
// reply (plain JSON or an SSE stream) is written back as one stdout line.
// The relay never interprets tool calls -- it only carries the transport
// headers (session id, negotiated protocol version, bearer token) that the
// Streamable HTTP transport requires. stdout carries protocol only; every
// diagnostic goes to stderr.
import readline from "node:readline";

const ACCEPT = "application/json, text/event-stream";

export function parseSse(onEvent) {
  // Incremental SSE parser: feed() text chunks, onEvent({event, data, id}).
  let buf = "";
  let data = [];
  let event = "message";
  let id;
  return function feed(chunk) {
    buf += chunk;
    let nl;
    while ((nl = buf.search(/\r\n|\r|\n/)) !== -1) {
      const line = buf.slice(0, nl);
      buf = buf.slice(nl + (buf.startsWith("\r\n", nl) ? 2 : 1));
      if (line === "") {
        if (data.length) onEvent({ event, data: data.join("\n"), id });
        data = [];
        event = "message";
        continue;
      }
      if (line.startsWith(":")) continue;
      const i = line.indexOf(":");
      const field = i === -1 ? line : line.slice(0, i);
      const value = i === -1 ? "" : line.slice(i + 1).replace(/^ /, "");
      if (field === "data") data.push(value);
      else if (field === "event") event = value;
      else if (field === "id") id = value;
    }
  };
}

export class Relay {
  constructor({ url, token, write, log, fetchImpl = globalThis.fetch, userAgent }) {
    this.url = url;
    this.token = token;
    this.write = write;
    this.log = log;
    this.fetch = fetchImpl;
    this.userAgent = userAgent;
    this.sessionId = undefined;
    this.protocolVersion = undefined;
    this.chain = Promise.resolve();
    this.inflight = new Set();
    this.listening = false;
    this.closed = false;
  }

  headers(extra = {}) {
    const h = { accept: ACCEPT, "user-agent": this.userAgent, ...extra };
    if (this.token) h.authorization = `Bearer ${this.token}`;
    if (this.sessionId) h["mcp-session-id"] = this.sessionId;
    if (this.protocolVersion) h["mcp-protocol-version"] = this.protocolVersion;
    return h;
  }

  emit(msg) {
    this.write(JSON.stringify(msg) + "\n");
  }

  fail(msg, message, code = -32000) {
    // Only requests (id present, has a method) get an error reply; a
    // notification or a client response has nobody waiting on it.
    if (msg && msg.id !== undefined && msg.id !== null && typeof msg.method === "string") {
      this.emit({ jsonrpc: "2.0", id: msg.id, error: { code, message } });
    } else {
      this.log(message);
    }
  }

  // Messages are dispatched in arrival order (each waits for the previous
  // one's response HEADERS, so the session id from `initialize` is known
  // before anything else is sent), but response BODIES stream concurrently
  // so one long tool call never blocks a second one.
  send(line) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      this.emit({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
      return this.chain;
    }
    const run = this.chain.then(() => this.post(msg, line));
    this.chain = run.catch(() => {});
    return run;
  }

  async post(msg, raw) {
    let res;
    try {
      res = await this.fetch(this.url, {
        method: "POST",
        headers: this.headers({ "content-type": "application/json" }),
        body: raw,
      });
    } catch (err) {
      this.fail(msg, `StealthLab MCP unreachable at ${this.url}: ${err.cause?.message || err.message}`);
      return;
    }
    const sid = res.headers.get("mcp-session-id");
    if (sid) this.sessionId = sid;

    if (res.status === 202 || res.status === 204) return;
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      let hint = "";
      if (res.status === 401 || res.status === 403) {
        hint = " -- set a token with `stealthlab-mcp login` (or STEALTHLAB_TOKEN)";
      } else if (res.status === 404 && this.sessionId) {
        hint = " -- server session expired; restart the MCP server in your agent";
        this.sessionId = undefined;
      }
      this.fail(msg, `StealthLab MCP HTTP ${res.status}${hint}${text ? `: ${text.slice(0, 300)}` : ""}`);
      return;
    }

    const type = (res.headers.get("content-type") || "").toLowerCase();
    const body = type.includes("text/event-stream") ? this.pumpSse(res) : this.pumpJson(res);
    const tracked = body.catch((err) => this.fail(msg, `StealthLab MCP stream error: ${err.message}`));
    this.inflight.add(tracked);
    tracked.finally(() => this.inflight.delete(tracked));
    if (!Array.isArray(msg) && msg.method === "initialize") {
      await tracked;
      this.openListenStream();
    }
  }

  handleServerMessage(obj) {
    for (const m of Array.isArray(obj) ? obj : [obj]) {
      const v = m?.result?.protocolVersion;
      if (typeof v === "string" && m.result.serverInfo) this.protocolVersion = v;
      this.emit(m);
    }
  }

  async pumpJson(res) {
    const text = await res.text();
    if (text.trim()) this.handleServerMessage(JSON.parse(text));
  }

  async pumpSse(res) {
    const feed = parseSse(({ data }) => {
      if (!data.trim()) return;
      try {
        this.handleServerMessage(JSON.parse(data));
      } catch {
        this.log(`ignored non-JSON SSE event: ${data.slice(0, 200)}`);
      }
    });
    const decoder = new TextDecoder();
    for await (const chunk of res.body) feed(decoder.decode(chunk, { stream: true }));
    feed(decoder.decode() + "\n\n");
  }

  // Optional server->client channel (GET /mcp). Servers that don't offer
  // one answer 405, which is normal and ends the attempt quietly.
  async openListenStream() {
    if (this.listening || this.closed || !this.sessionId) return;
    this.listening = true;
    let delay = 1000;
    while (!this.closed) {
      try {
        const res = await this.fetch(this.url, { method: "GET", headers: this.headers() });
        if (res.status === 405 || res.status === 404 || res.status === 400) return;
        if (res.ok && (res.headers.get("content-type") || "").includes("text/event-stream")) {
          delay = 1000;
          await this.pumpSse(res);
        } else {
          await res.body?.cancel?.();
        }
      } catch {
        /* network blip -- retry below */
      }
      if (this.closed) return;
      await new Promise((r) => setTimeout(r, delay).unref?.());
      delay = Math.min(delay * 2, 30000);
    }
  }

  async close() {
    this.closed = true;
    await this.chain;
    await Promise.allSettled([...this.inflight]);
    if (this.sessionId) {
      try {
        await this.fetch(this.url, { method: "DELETE", headers: this.headers(), signal: AbortSignal.timeout(2000) });
      } catch { /* best effort */ }
    }
  }
}

export async function runStdioRelay({ url, token, userAgent }) {
  const log = (m) => process.stderr.write(`[stealthlab-mcp] ${m}\n`);
  const relay = new Relay({ url, token, userAgent, log, write: (s) => process.stdout.write(s) });
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  rl.on("line", (line) => {
    if (line.trim()) relay.send(line);
  });
  await new Promise((resolve) => rl.once("close", resolve));
  await relay.close();
}
