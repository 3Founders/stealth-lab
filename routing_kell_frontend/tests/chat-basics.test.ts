import { describe, expect, it } from "vitest";
import { freeRoomName, readInstructions, writeInstructions } from "@/lib/chat/rooms";

function memoryKv() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); } };
}

describe("chat basics", () => {
  it("+ New room fills in a free name", () => {
    expect(freeRoomName(["#general"])).toBe("chat-1");
    expect(freeRoomName(["#general", "#chat-1", "#chat-2"])).toBe("chat-3");
  });

  it("instructions for every chat are kept per account, capped", () => {
    const kv = memoryKv();
    expect(readInstructions(kv as never, "me")).toBe("");
    writeInstructions(kv as never, "me", "answer briefly");
    expect(readInstructions(kv as never, "me")).toBe("answer briefly");
    expect(readInstructions(kv as never, "someone-else")).toBe("");
    writeInstructions(kv as never, "me", "x".repeat(5000));
    expect(readInstructions(kv as never, "me")).toHaveLength(1500);
  });
});
