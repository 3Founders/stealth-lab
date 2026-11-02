import { describe, expect, it } from "vitest";
import { tempLabel, toTurns } from "@/lib/chat/temporary";
import type { GroupMessage } from "@/lib/chat/types";

const msg = (id: string, handle: string, name: string, text: string): GroupMessage => ({
  id, roomId: "#t", sender: { handle, displayName: name, type: handle === "@kell" ? "local_agent" : "human", status: "online" }, recipients: [],
  payload: { text } as GroupMessage["payload"], timestamp: 1, depthCounter: 0,
});

describe("temporary rooms", () => {
  it("a label is valid for a room name and never repeats one in use", () => {
    const l = tempLabel([]);
    expect(l).toMatch(/^#temp-[a-z0-9]{4}$/);
    const seq = [0, 0, 0, 0, 0.5, 0.5, 0.5, 0.5];
    let i = 0;
    const next = () => seq[i++ % seq.length];
    expect(tempLabel(["#temp-aaaa"], next)).not.toBe("#temp-aaaa");
  });
  it("the conversation goes to the agent as its own turns and everyone else's", () => {
    const turns = toTurns([msg("1", "@you", "You", "hello"), msg("2", "@kell", "Kell", "hi there"), msg("3", "@you", "You", "and now?")], "@kell");
    expect(turns).toEqual([
      { role: "user", content: "You (@you): hello" },
      { role: "assistant", content: "hi there" },
      { role: "user", content: "You (@you): and now?" },
    ]);
  });
  it("empty messages are skipped and only the last thirty turns are kept", () => {
    const many = Array.from({ length: 40 }, (_, i) => msg(String(i), "@you", "You", `m${i}`));
    expect(toTurns([msg("x", "@you", "You", "   "), ...many], "@kell").length).toBe(30);
  });
});
