/**
 * keळ-hosted models for signed-in people: a capped pilot on keळ's own provider accounts (public material only). It is a
 * separate, opt-in choice made on the Models page. It is never a fallback: with no key and no opt-in the assistant says
 * "Add a key to chat here".
 *
 * Contract (backend/app/mcp_server/chat_routes.py): OpenAI-compatible POST /v1/chat/completions (stream: true gives SSE) and
 * GET /v1/models on the hosted MCP origin. Auth is the person's Supabase website-session token as a Bearer; the data-class
 * header X-Stealthlab-Data-Class: PUBLIC_SOURCE is required (without it the server refuses, since private is its default).
 * The organisation header is left out: the server fills the pilot organisation. A cap hit is 403 policy_denied with display text.
 */
import { MCP_ORIGIN } from "@/lib/connector";
import { providerById } from "@/lib/byok/providers";
import type { ProviderTarget } from "@/lib/byok/chat";
import type { KeyValue } from "@/lib/byok/spend";

/** The two pilot models. /v1/models may list more for other callers; only these are offered here. */
export const KELL_MODEL_IDS = ["glm-5.3-flash", "gemma-4-31b-it"] as const;

export function kellBase(origin: string = MCP_ORIGIN): string | null {
  if (!origin) return null;
  try {
    const u = new URL(origin);
    const loopback = u.hostname === "localhost" || u.hostname === "127.0.0.1";
    if (u.protocol !== "https:" && !(u.protocol === "http:" && loopback)) return null;
    return `${u.origin}/v1`;
  } catch { return null; }
}

/** Offered only when this deployment has a hosted server address, unless NEXT_PUBLIC_KEL_MODELS=off. */
export const kellModelsAvailable = (origin: string = MCP_ORIGIN, flag: string | undefined = process.env.NEXT_PUBLIC_KEL_MODELS): boolean =>
  kellBase(origin) !== null && (flag ?? "").trim().toLowerCase() !== "off";

export function kellTarget(token: string, origin: string = MCP_ORIGIN): ProviderTarget | null {
  const base = kellBase(origin);
  if (!base || !token) return null;
  return {
    provider: providerById("other")!, // OpenAI-compatible
    baseUrl: base,
    apiKey: token,
    extraHeaders: { "x-stealthlab-data-class": "PUBLIC_SOURCE" },
  };
}

export const onlyKellModels = (ids: string[]): string[] => ids.filter((id) => (KELL_MODEL_IDS as readonly string[]).includes(id));

// What the person chose for the assistant. Not a secret, so it may live in localStorage.
export type AssistantChoice = { source: "key" } | { source: "kell"; model: string };
const choiceKey = (subject: string) => `kel.assistant.source.${subject}`;

export function readChoice(store: KeyValue, subject: string): AssistantChoice {
  try {
    const v = JSON.parse(store.getItem(choiceKey(subject)) ?? "null") as { source?: string; model?: string } | null;
    if (v?.source === "kell" && typeof v.model === "string" && (KELL_MODEL_IDS as readonly string[]).includes(v.model)) return { source: "kell", model: v.model };
  } catch { /* fall through */ }
  return { source: "key" }; // the default is always the person's own key
}

export function writeChoice(store: KeyValue, subject: string, choice: AssistantChoice): void {
  try { store.setItem(choiceKey(subject), JSON.stringify(choice)); } catch { /* storage blocked: the default (own key) applies */ }
}
