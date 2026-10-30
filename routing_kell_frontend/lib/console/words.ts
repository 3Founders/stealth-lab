// Everything the page tells a person when something goes wrong is built here, so no raw response body, status line or
// stack trace can reach the screen, and no secret can be echoed into a message.

export type HelperFailure = "unreachable" | "unauthorized" | "forbidden" | "refused" | "failed";

export class HelperError extends Error {
  constructor(public kind: HelperFailure, message: string, public status?: number) {
    super(message);
    this.name = "HelperError";
  }
}

export const WORDS: Record<HelperFailure, string> = {
  unreachable: "The local helper is not answering. It may not be running, or it stopped.",
  unauthorized: "The helper did not accept this page's token. Open the address that `kell console` printed; it carries the token.",
  forbidden: "The helper refused this page. It only talks to the address it prints, or to an origin it was started with --allow-origin for.",
  refused: "The helper said no.",
  failed: "Something went wrong in the helper. Try again.",
};

/** Takes the helper's own `{error}` text only when it is short plain text; otherwise falls back to a fixed sentence. */
export function helperMessage(kind: HelperFailure, body: unknown): string {
  if (kind === "refused" && body && typeof body === "object" && "error" in body) {
    const e = (body as { error: unknown }).error;
    if (typeof e === "string" && e.length > 0 && e.length <= 200 && !/[\n\r<>{}]/.test(e)) return e;
  }
  return WORDS[kind];
}

/** Removes a secret from any text before it can be shown. Used on every message that follows a key being sent. */
export function scrub(text: string, ...secrets: Array<string | null | undefined>): string {
  let out = text;
  for (const s of secrets) {
    if (s && s.length >= 4) out = out.split(s).join("[hidden]");
  }
  return out;
}

/** The environment-variable name a provider's key is kept under: letters, digits, underscore, starting with a letter. */
export function suggestKeyName(provider: string): string {
  const base = provider.toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^[0-9]+/, "");
  return base ? `${base}_API_KEY`.slice(0, 64) : "";
}

export const KEY_NAME = /^[A-Z][A-Z0-9_]{1,63}$/;
export const MODEL_NAME = /^[A-Za-z0-9][\w.-]{0,63}$/;

export function isLoopbackAddress(raw: string): boolean {
  try {
    const u = new URL(raw);
    if (u.protocol !== "http:" && u.protocol !== "https:") return false;
    const h = u.hostname.replace(/^\[|\]$/g, "");
    return h === "127.0.0.1" || h === "localhost" || h === "::1";
  } catch {
    return false;
  }
}
