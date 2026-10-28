/**
 * The providers a person can bring a key for. The browser calls the provider directly, so each must allow browser requests
 * (CORS). Preflight answers checked 2026-10-28 with a plain OPTIONS request from a foreign origin:
 *   Anthropic   allows the request headers incl. anthropic-dangerous-direct-browser-access, origin *
 *   OpenAI      allows authorization + content-type, echoes the origin
 *   OpenRouter, Together, Groq   allow authorization + content-type, origin *
 * An "other" host is tried at run time: if the browser blocks it, the Test button says this provider cannot be used in
 * browser-only mode.
 *
 * Anthropic: ONLY an API key from console.anthropic.com is accepted. A claude.ai login or session token is never requested,
 * stored or relayed. The other hosts speak the OpenAI chat-completions protocol.
 */
export type ProviderKind = "anthropic" | "openai-compatible";

export interface Provider {
  id: string;
  label: string;
  kind: ProviderKind;
  baseUrl: string; // no trailing slash; "" means the person supplies it
  keyHint: string;
}

export const PROVIDERS: Provider[] = [
  { id: "anthropic", label: "Anthropic", kind: "anthropic", baseUrl: "https://api.anthropic.com", keyHint: "An API key from console.anthropic.com" },
  { id: "openai", label: "OpenAI", kind: "openai-compatible", baseUrl: "https://api.openai.com/v1", keyHint: "An API key from platform.openai.com" },
  { id: "openrouter", label: "OpenRouter", kind: "openai-compatible", baseUrl: "https://openrouter.ai/api/v1", keyHint: "An OpenRouter API key" },
  { id: "together", label: "Together", kind: "openai-compatible", baseUrl: "https://api.together.xyz/v1", keyHint: "A Together API key" },
  { id: "groq", label: "Groq", kind: "openai-compatible", baseUrl: "https://api.groq.com/openai/v1", keyHint: "A Groq API key" },
  { id: "other", label: "Other (OpenAI-compatible)", kind: "openai-compatible", baseUrl: "", keyHint: "A key for that host" },
];

export const providerById = (id: string): Provider | undefined => PROVIDERS.find((p) => p.id === id);

/** A base URL we will send a key to: https (http only for localhost), no credentials in the URL, no query or fragment. */
export function cleanBaseUrl(raw: string): string | null {
  let url: URL;
  try { url = new URL(raw.trim()); } catch { return null; }
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (url.protocol !== "https:" && !(url.protocol === "http:" && local)) return null;
  if (url.username || url.password || url.search || url.hash) return null;
  return url.origin + url.pathname.replace(/\/+$/, "");
}
