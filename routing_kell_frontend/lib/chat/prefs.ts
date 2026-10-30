/**
 * Chat preferences: how the room looks and how the message box behaves. Kept in this browser per account (switches only, no message
 * text). Notification preferences are separate (lib/chat/notify.ts) because they have their own rules.
 */
export type TextSize = "small" | "default" | "large";

export interface ChatPrefs {
  textSize: TextSize;
  /** Tighter spacing between messages. */
  compact: boolean;
  /** Enter sends and Shift+Enter starts a new line. Off: Enter starts a new line and Ctrl/Cmd+Enter sends. */
  enterToSend: boolean;
  showTimes: boolean;
}

export const DEFAULT_CHAT_PREFS: ChatPrefs = { textSize: "default", compact: false, enterToSend: true, showTimes: true };

export const TEXT_PX: Record<TextSize, number> = { small: 14, default: 15, large: 17 };

const key = (subject: string) => `kel.chat.prefs.${subject}`;

export function loadChatPrefs(subject: string, store: Pick<Storage, "getItem"> = window.localStorage): ChatPrefs {
  try {
    const v = JSON.parse(store.getItem(key(subject)) ?? "null") as Partial<ChatPrefs> | null;
    if (!v || typeof v !== "object") return DEFAULT_CHAT_PREFS;
    return {
      textSize: v.textSize === "small" || v.textSize === "large" ? v.textSize : "default",
      compact: v.compact === true,
      enterToSend: v.enterToSend !== false,
      showTimes: v.showTimes !== false,
    };
  } catch { return DEFAULT_CHAT_PREFS; }
}

export function saveChatPrefs(subject: string, prefs: ChatPrefs, store: Pick<Storage, "setItem"> = window.localStorage): void {
  try { store.setItem(key(subject), JSON.stringify(prefs)); } catch { /* storage may be blocked */ }
}

/** Does this key press send the message, under the person's setting? (A composing IME or a bare Shift+Enter never sends.) */
export function sends(e: { key: string; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; composing?: boolean }, enterToSend: boolean): boolean {
  if (e.key !== "Enter" || e.composing) return false;
  return enterToSend ? !e.shiftKey : e.ctrlKey || e.metaKey;
}
