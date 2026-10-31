import { describe, expect, it } from "vitest";
import { fallbackName, personHandle, toMessage } from "@/lib/chat/events";
import { matchHandles } from "@/lib/chat/ui";
import { mentionedNames } from "@/lib/chat/relay-client";

describe("mentioning people", () => {
  it("turns an account name into the handle the server stores for a mention of them", () => {
    expect(personHandle("Chaitanya D")).toBe("@chaitanya-d");
    expect(personHandle(null)).toBe("@member");
    expect(personHandle(fallbackName("ab12-cd"))).toBe("@member-ab12");
  });
  it("lists people next to agents when @ is typed", () => {
    expect(matchHandles(["@opencode", "@chaitanya-d", "@priya"], "ch")).toEqual(["@chaitanya-d"]);
    expect(matchHandles(["@opencode", "@chaitanya-d", "@priya"], "")).toHaveLength(3);
  });
  it("a stored message that mentions you carries your handle as a recipient", () => {
    const m = toMessage({
      id: "1", seq: 1, kind: "msg", body: "hi @chaitanya-d look", mentions: ["chaitanya-d"], depth: 0, lamport: 1, device_id: null, created_at: "2026-01-01T00:00:00Z",
      sender: { kind: "person", me: false, sub: "s1", name: "Priya", handle: "" },
    } as never, "room");
    expect(m?.recipients).toEqual(["@chaitanya-d"]);
    expect(mentionedNames("hi @chaitanya-d look")).toEqual(["chaitanya-d"]);
  });
});
