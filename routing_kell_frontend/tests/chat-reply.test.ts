// Reply on a message: the reply carries reply_to to the server and back, the quote is one short line, and an agent asked in a reply is
// told what is being replied to.
import { describe, expect, it } from "vitest";
import { toEvent, toMessage } from "@/lib/chat/events";
import { quoteText, withReplyContext } from "@/lib/chat/reply";
import { HUMAN } from "@/lib/chat/agents";
import type { GroupMessage } from "@/lib/chat/types";

const msg = (replyTo?: string): GroupMessage => ({ id: "m2", roomId: "#r", sender: HUMAN, recipients: ["@all"], payload: { text: "yes, that one" } as never, timestamp: 1, depthCounter: 0, ...(replyTo ? { replyTo } : {}) });

describe("reply", () => {
  it("sends reply_to with the message, and reads it back", () => {
    expect(toEvent(msg("m1"), "dev", 1).reply_to).toBe("m1");
    expect("reply_to" in toEvent(msg(), "dev", 1)).toBe(false);
    const back = toMessage({
      id: "m2", seq: 2, kind: "msg", body: "yes", mentions: [], depth: 0, lamport: 1, device_id: null, created_at: "2026-01-01T00:00:00Z", reply_to: "m1", target: null,
      sender: { kind: "person", me: true, sub: "s", name: "A", handle: "" },
    } as never, "#r");
    expect(back?.replyTo).toBe("m1");
  });
  it("quotes a message as one short line, files by name and code as [code]", () => {
    expect(quoteText("line one\n\nline two")).toBe("line one line two");
    expect(quoteText("see\n```js\nx()\n```")).toBe("see [code]");
    expect(quoteText("[[file:0a1b2c3d-0000-4000-8000-000000000001|deck.pdf|10|application/pdf]]")).toBe("deck.pdf");
    const long = quoteText("a".repeat(300));
    expect(long.length).toBe(140);
    expect(long.endsWith("…")).toBe(true);
  });
  it("tells the agent what is being replied to", () => {
    expect(withReplyContext("do this one", "kell", "1. a plan\n2. another")).toBe('(Replying to kell\'s message: "1. a plan 2. another")\n\ndo this one');
    expect(withReplyContext("again", null, "my question")).toContain("my earlier message");
    expect(withReplyContext("hi", "kell", "")).toBe("hi");
  });
});
