import { describe, expect, it } from "vitest";
import { DEFAULT_PREFS, decide, inQuietHours, levelOf, loadPrefs, previewText, savePrefs, titleWithCount, type Prefs } from "@/lib/chat/notify";

const at = (h: number, m = 0) => new Date(2026, 9, 30, h, m);
const prefs = (over: Partial<Prefs> = {}): Prefs => ({ ...DEFAULT_PREFS, desktop: true, ...over });
const ev = (over = {}) => ({ kind: "message" as const, room: "#team", onScreen: false, hidden: false, ...over });

describe("who gets told about what", () => {
  it("never about your own messages, nor the room you are looking at in a visible tab", () => {
    expect(decide(ev({ mine: true }), prefs(), at(12)).record).toBe(false);
    expect(decide(ev({ onScreen: true }), prefs(), at(12)).record).toBe(false);
    expect(decide(ev({ onScreen: true, hidden: true }), prefs(), at(12)).record).toBe(true);   // the tab is in the background
  });

  it("a visible tab gets the bell and a chime but no OS alert; a hidden tab gets the OS alert", () => {
    expect(decide(ev(), prefs(), at(12))).toEqual({ record: true, os: false, sound: true });
    expect(decide(ev({ hidden: true }), prefs(), at(12))).toEqual({ record: true, os: true, sound: true });
    expect(decide(ev({ hidden: true }), prefs({ desktop: false }), at(12)).os).toBe(false);
    expect(decide(ev(), prefs({ sound: false }), at(12)).sound).toBe(false);
  });

  it("room levels: mentions keeps mentions, results and approvals; mute keeps only a direct mention", () => {
    const m = prefs({ rooms: { "#team": "mentions" } });
    expect(decide(ev({ kind: "message" }), m, at(12)).record).toBe(false);
    for (const kind of ["mention", "approval", "task", "agent"] as const) expect(decide(ev({ kind }), m, at(12)).record).toBe(true);
    const mute = prefs({ rooms: { "#team": "mute" } });
    expect(decide(ev({ kind: "approval" }), mute, at(12)).record).toBe(false);
    expect(decide(ev({ kind: "mention" }), mute, at(12)).record).toBe(true);
    expect(levelOf(mute, "#other")).toBe("all");
  });

  it("quiet hours silence the sound and the OS alert but still record, including across midnight", () => {
    const q = prefs({ quiet: { on: true, from: "22:00", to: "07:00" } });
    expect(inQuietHours(q.quiet, at(23))).toBe(true);
    expect(inQuietHours(q.quiet, at(3))).toBe(true);
    expect(inQuietHours(q.quiet, at(12))).toBe(false);
    expect(decide(ev({ hidden: true, kind: "approval" }), q, at(23))).toEqual({ record: true, os: false, sound: false });
    expect(inQuietHours({ on: true, from: "09:00", to: "09:00" }, at(9))).toBe(false);
    expect(inQuietHours({ on: false, from: "22:00", to: "07:00" }, at(23))).toBe(false);
  });
});

describe("what is shown", () => {
  it("previews can be turned off, and long text is cut", () => {
    expect(previewText(prefs({ previews: false }), "a secret")).toBe("New message");
    expect(previewText(prefs(), "  a   b\n c ")).toBe("a b c");
    expect(previewText(prefs(), "x".repeat(300)).length).toBe(140);
  });

  it("the tab title carries the unread count", () => {
    expect(titleWithCount("Chat", 0)).toBe("Chat");
    expect(titleWithCount("Chat", 3)).toBe("(3) Chat");
    expect(titleWithCount("Chat", 250)).toBe("(99+) Chat");
  });
});

describe("saved settings", () => {
  const mem = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }; };

  it("round-trip, and bad stored data falls back to the defaults", () => {
    const s = mem();
    const p = prefs({ sound: false, rooms: { "#a": "mute" }, quiet: { on: true, from: "21:30", to: "06:00" } });
    savePrefs("u1", p, s);
    expect(loadPrefs("u1", s)).toEqual(p);
    expect(loadPrefs("nobody", s)).toEqual(DEFAULT_PREFS);
    s.setItem("kel.chat.notify.bad", "{not json");
    expect(loadPrefs("bad", s)).toEqual(DEFAULT_PREFS);
    s.setItem("kel.chat.notify.odd", JSON.stringify({ rooms: { "#a": "weird", "#b": "mute" }, quiet: { from: "xx" } }));
    const odd = loadPrefs("odd", s);
    expect(odd.rooms).toEqual({ "#b": "mute" });
    expect(odd.quiet.from).toBe("22:00");
  });
});
