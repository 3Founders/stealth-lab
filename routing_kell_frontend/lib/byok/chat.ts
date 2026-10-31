/**
 * Talking to a provider straight from the browser with the person's own key. Nothing here goes through kell.
 * Every error that can reach the page passes through `scrub`, so a key (or anything shaped like one) is never shown, logged,
 * sent in a room message or reported.
 */
import { readSse } from "@/lib/sse";
import type { Provider } from "@/lib/byok/providers";

/** One piece of a message that has a picture in it. `data` is base64 without the "data:" prefix. */
export type ChatPart = { type: "text"; text: string } | { type: "image"; mime: string; data: string };
/** `content` is plain text, or a list of parts when the message carries pictures. */
export interface ChatMessage { role: "user" | "assistant"; content: string | ChatPart[] }

/** The message as one provider wants it: OpenAI-style `image_url` parts, or Anthropic `image` blocks. Plain text passes through unchanged. */
export function encodeMessage(m: ChatMessage, anthropic: boolean): { role: string; content: unknown } {
  if (typeof m.content === "string") return { role: m.role, content: m.content };
  return {
    role: m.role,
    content: m.content.map((p) =>
      p.type === "text" ? { type: "text", text: p.text }
        : anthropic ? { type: "image", source: { type: "base64", media_type: p.mime, data: p.data } }
          : { type: "image_url", image_url: { url: `data:${p.mime};base64,${p.data}` } }),
  };
}

/** Pictures in a provider's reply (some OpenAI-compatible servers return them beside the text), as markdown the room can draw. */
function imagesAsMarkdown(list: unknown): string {
  if (!Array.isArray(list)) return "";
  let out = "";
  for (const it of list) {
    const url = (it as { image_url?: { url?: unknown } | string })?.image_url;
    const u = typeof url === "string" ? url : typeof url?.url === "string" ? url.url : "";
    if (u) out += `\n\n![image](${u})\n\n`;
  }
  return out;
}
export interface ProviderTarget { provider: Provider; baseUrl: string; apiKey: string; extraHeaders?: Record<string, string> }

export class ProviderError extends Error {}

/** Removes the key itself, and anything key-shaped, from text before it is shown anywhere. */
export function scrub(text: string, ...secrets: string[]): string {
  let out = String(text ?? "");
  for (const s of secrets) if (s && s.length >= 4) out = out.split(s).join("[key removed]");
  return out.replace(/\b(sk|rk|gsk|pk)[-_][A-Za-z0-9_-]{8,}/g, "[key removed]").replace(/Bearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, "Bearer [key removed]");
}

const ANTHROPIC_VERSION = "2023-06-01";

const emptyReply = (finish: unknown) =>
  finish === "length"
    ? "The model ran out of tokens before it wrote an answer (some models think first). Try again, or choose another model under Models."
    : `The model returned no text${finish ? ` (it stopped because: ${String(finish)})` : ""}. Try again, or choose another model under Models.`;

function headers(t: ProviderTarget): Record<string, string> {
  return t.provider.kind === "anthropic"
    ? {
        "content-type": "application/json",
        "x-api-key": t.apiKey,
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-dangerous-direct-browser-access": "true", // required by Anthropic for browser calls with an API key
      }
    : { "content-type": "application/json", authorization: `Bearer ${t.apiKey}`, ...(t.extraHeaders ?? {}) };
}

async function fail(res: Response, t: ProviderTarget): Promise<never> {
  let body = "";
  try { body = (await res.text()).slice(0, 600); } catch { /* none */ }
  // An OpenAI-style error envelope carries a sentence meant for people; show that, not the raw JSON.
  try { const m = (JSON.parse(body) as { error?: { message?: unknown } })?.error?.message; if (typeof m === "string" && m) body = m; } catch { /* not JSON */ }
  throw new ProviderError(scrub(`The provider answered ${res.status}${body ? `: ${body}` : "."}`, t.apiKey));
}

async function send(t: ProviderTarget, path: string, init: RequestInit, fetchImpl: typeof fetch): Promise<Response> {
  try {
    return await fetchImpl(`${t.baseUrl}${path}`, { ...init, headers: headers(t), cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer" });
  } catch (err) {
    if ((err as { name?: string })?.name === "AbortError") throw err;
    // A browser reports a blocked request, a dropped connection and (for some providers) a rejected key all as the same
    // opaque failure, because the provider's error reply may carry no browser-access headers. Say all of it.
    throw new ProviderError("The browser could not complete the request. You may be offline, the provider may have rejected the key (some providers send no browser headers on a rejection), or it may not allow requests from a web page, so it can't be used in browser-only mode.");
  }
}

/** The models this key can reach (also the "Test": it proves the key and the address work). */
export async function listModels(t: ProviderTarget, fetchImpl: typeof fetch = fetch, signal?: AbortSignal): Promise<string[]> {
  const res = await send(t, t.provider.kind === "anthropic" ? "/v1/models" : "/models", { method: "GET", signal }, fetchImpl);
  if (!res.ok) await fail(res, t);
  const json = (await res.json()) as { data?: Array<{ id?: unknown }> };
  return (json.data ?? []).map((m) => String(m.id ?? "")).filter(Boolean);
}

/** Streams a reply as text pieces. Aborting `signal` stops it. */
export async function* streamChat(
  t: ProviderTarget, model: string, messages: ChatMessage[], opts: { system?: string; maxTokens?: number; signal?: AbortSignal; fetchImpl?: typeof fetch } = {},
): AsyncGenerator<string> {
  const fetchImpl = opts.fetchImpl ?? fetch;
  const maxTokens = opts.maxTokens ?? 4096; // never small: a reasoning model (glm-5.3-flash) thinks first and a small limit is spent before any answer appears
  const anthropic = t.provider.kind === "anthropic";
  const body = anthropic
    ? { model, max_tokens: maxTokens, stream: true, ...(opts.system ? { system: opts.system } : {}), messages: messages.map((m) => encodeMessage(m, true)) }
    : { model, max_tokens: maxTokens, stream: true, messages: [...(opts.system ? [{ role: "system", content: opts.system }] : []), ...messages.map((m) => encodeMessage(m, false))] };
  const res = await send(t, anthropic ? "/v1/messages" : "/chat/completions", { method: "POST", body: JSON.stringify(body), signal: opts.signal }, fetchImpl);
  if (!res.ok || !res.body) await fail(res, t);

  // Some servers answer a streaming request with one plain JSON reply. Read that too, instead of showing nothing.
  if (!anthropic && (res.headers.get("content-type") ?? "").includes("application/json")) {
    let json: { choices?: Array<{ message?: { content?: unknown; images?: unknown }; finish_reason?: unknown }>; error?: { message?: unknown } } = {};
    try { json = await res.json(); } catch { throw new ProviderError("The provider's reply could not be read."); }
    if (json.error) throw new ProviderError(scrub(String(json.error.message ?? "The provider returned an error."), t.apiKey));
    const msg = json.choices?.[0]?.message as { content?: unknown; images?: unknown } | undefined;
    let text = "";
    if (typeof msg?.content === "string") text = msg.content;
    else if (Array.isArray(msg?.content)) {
      for (const part of msg.content as Array<{ type?: string; text?: unknown; image_url?: unknown }>) {
        if (part?.type === "text" && typeof part.text === "string") text += part.text;
        else if (part?.type === "image_url") text += imagesAsMarkdown([part]);
      }
    }
    text += imagesAsMarkdown(msg?.images);
    if (text.trim()) { yield text; return; }
    throw new ProviderError(emptyReply(json.choices?.[0]?.finish_reason));
  }

  const queue: string[] = [];
  let finish: unknown = null;
  let produced = false;
  let wake: (() => void) | null = null;
  let done = false;
  let error: unknown = null;
  const reading = readSse(res.body!, (e) => {
    const ev = e as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    const piece = anthropic
      ? (ev.type === "content_block_delta" ? ev.delta?.text : undefined)
      : ev.choices?.[0]?.delta?.content;
    if (ev.type === "error" || (!anthropic && ev.error)) error = new ProviderError(scrub(String(ev.error?.message ?? "The provider reported an error."), t.apiKey));
    if (!anthropic && ev.choices?.[0]?.finish_reason) finish = ev.choices[0].finish_reason;
    if (typeof piece === "string" && piece) { produced = true; queue.push(piece); wake?.(); }
    const pics = !anthropic ? imagesAsMarkdown(ev.choices?.[0]?.delta?.images) : "";
    if (pics) { produced = true; queue.push(pics); wake?.(); }
  }).catch((err) => { error = err; }).finally(() => { done = true; wake?.(); });

  while (true) {
    if (queue.length) { yield queue.shift()!; continue; }
    if (error) throw error instanceof ProviderError || (error as { name?: string })?.name === "AbortError" ? error : new ProviderError("The reply stream broke.");
    if (done) break;
    await new Promise<void>((r) => { wake = r; });
    wake = null;
  }
  await reading;
  if (!produced) throw new ProviderError(emptyReply(finish)); // never end a reply in silence
}
