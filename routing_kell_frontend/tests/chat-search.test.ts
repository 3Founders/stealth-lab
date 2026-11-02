import { describe, expect, it } from "vitest";
import { searchMessages, snippet } from "@/lib/chat/search";
import { fileToken } from "@/lib/chat/files";
import type { GroupMessage } from "@/lib/chat/types";

const msg = (id: string, text: string, t: number, who = "Asha"): GroupMessage => ({
  id, roomId: "#a", sender: { handle: `@${who.toLowerCase()}`, displayName: who, type: "human", status: "online" }, recipients: [],
  payload: { text } as GroupMessage["payload"], timestamp: t, depthCounter: 0,
});
const all = [
  { room: "#a", m: msg("1", "The Q3 budget needs a review", 100) },
  { room: "#b", m: msg("2", "review the pricing page, budget later", 300) },
  { room: "#b", m: msg("3", `see attached\n${fileToken({ id: "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b", name: "Roadmap.pdf", size: 10, mime: "application/pdf" })}`, 200, "Ravi") },
];

describe("search", () => {
  it("needs every word, in any order, ignoring case, newest first", () => {
    expect(searchMessages(all, "BUDGET review").map((h) => h.id)).toEqual(["2", "1"]);
    expect(searchMessages(all, "pricing budget").map((h) => h.id)).toEqual(["2"]);
    expect(searchMessages(all, "nothing here")).toEqual([]);
    expect(searchMessages(all, "   ")).toEqual([]);
  });
  it("can be limited to one room, and finds a sender's name and a file's name", () => {
    expect(searchMessages(all, "budget", { room: "#a" }).map((h) => h.id)).toEqual(["1"]);
    expect(searchMessages(all, "ravi").map((h) => h.id)).toEqual(["3"]);
    expect(searchMessages(all, "roadmap").map((h) => h.id)).toEqual(["3"]);
  });
  it("a snippet marks the matches and cuts long text with an ellipsis", () => {
    const long = `${"word ".repeat(60)}needle ${"tail ".repeat(60)}`;
    const parts = snippet(long, "needle");
    expect(parts.filter((p) => p.hit).map((p) => p.s)).toEqual(["needle"]);
    expect(parts[0].s.startsWith("…")).toBe(true);
    expect(parts.at(-1)!.s.endsWith("…")).toBe(true);
    expect(snippet("a (b) c", "(b)").some((p) => p.hit)).toBe(true);   // regex characters in the query are harmless
  });
});
