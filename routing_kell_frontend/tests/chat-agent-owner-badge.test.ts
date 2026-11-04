import { describe, expect, it } from "vitest";
import { ownerBadge } from "@/lib/chat/relay-client";

// The badge on someone else's agent says whose it is, not the word "theirs".
describe("ownerBadge", () => {
  it("uses the first word of the owner's display name", () => {
    expect(ownerBadge({ owner: "slatesummit", owner_name: "Anuj Bhatt" })).toBe("Anuj's");
  });
  it("capitalises a lowercase display name", () => {
    expect(ownerBadge({ owner: "slatesummit", owner_name: "anuj" })).toBe("Anuj's");
  });
  it("falls back to the username when the server sends no display name (an older server)", () => {
    expect(ownerBadge({ owner: "slatesummit" })).toBe("slatesummit's");
    expect(ownerBadge({ owner: "slatesummit", owner_name: "   " })).toBe("slatesummit's");
  });
  it("says theirs only when nothing is known about the owner", () => {
    expect(ownerBadge({})).toBe("theirs");
  });
});
