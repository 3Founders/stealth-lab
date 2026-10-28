import { streamChat, type ChatMessage } from "@/lib/byok/chat";
import { providerById } from "@/lib/byok/providers";
import type { KeyValue } from "@/lib/byok/spend";
import type { KeyMeta } from "@/lib/byok/vault";
import { MCP_ORIGIN } from "@/lib/connector";
import { kellTarget, readChoice } from "@/lib/kell-models";
import type { AssistantDeps } from "./assistant";

export const ASSISTANT_SYSTEM = "You are the assistant in a group chat room with several people and agents. Each message is prefixed with its sender's handle. Be concise and helpful.";

export interface ResolverDeps {
  vault: { list(): Promise<KeyMeta[]>; reveal(id: string): Promise<string | null> };
  /** The person's website-session access token, or null when signed out. */
  token(): Promise<string | null>;
  kv: KeyValue;
  subject: string;
  origin?: string;
}

/**
 * Chooses what backs the assistant for ONE message, from the person's own setting (Models page):
 *  - "key" (the default): their own key in the browser vault, sent only to their provider;
 *  - "kell": the opt-in pilot, with their session token to keळ's hosted server.
 * There is no fallback between the two. "Own key" with no key returns null (the room says "Add a key to chat here"); "kell" with
 * no session says so. Nothing is cached: the key or token is read for this request and not kept.
 */
export function makeResolver(d: ResolverDeps): AssistantDeps["resolve"] {
  return async () => {
    const choice = readChoice(d.kv, d.subject);
    if (choice.source === "kell") {
      const token = await d.token();
      const target = token ? kellTarget(token, d.origin ?? MCP_ORIGIN) : null;
      if (!token || !target) throw new Error("Sign in again to use keळ models.");
      return {
        secrets: [token],
        stream: (messages: ChatMessage[], signal: AbortSignal) => streamChat(target, choice.model, messages, { system: ASSISTANT_SYSTEM, signal }),
      };
    }
    const metas = await d.vault.list();
    const meta = metas.find((k) => k.model) ?? metas[0];
    if (!meta) return null;
    if (!meta.model) throw new Error("Open Models in your account menu and press Test on your key to choose a model.");
    const provider = providerById(meta.provider);
    const apiKey = await d.vault.reveal(meta.id);
    if (!provider || !apiKey) throw new Error("That key can't be read in this browser any more. Add it again under Models.");
    return {
      secrets: [apiKey],
      stream: (messages: ChatMessage[], signal: AbortSignal) => streamChat({ provider, baseUrl: meta.baseUrl, apiKey }, meta.model, messages, { system: ASSISTANT_SYSTEM, signal }),
    };
  };
}
