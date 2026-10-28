import type { GroupMessage } from "../types";

/**
 * Mirrors human messages between tabs of the same browser (BroadcastChannel is same-origin and never leaves the
 * machine), so two tabs can act as two people. Nothing is stored. Where BroadcastChannel is missing it does nothing.
 */
export function openChannel(name: string, onMessage: (m: GroupMessage) => void): { post: (m: GroupMessage) => void; close: () => void } {
  if (typeof BroadcastChannel === "undefined") return { post: () => {}, close: () => {} };
  const ch = new BroadcastChannel(name);
  ch.onmessage = (e: MessageEvent) => { if (e.data && typeof e.data.id === "string") onMessage(e.data as GroupMessage); };
  return { post: (m) => ch.postMessage(m), close: () => ch.close() };
}
