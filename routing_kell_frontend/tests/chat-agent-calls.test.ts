import { describe, expect, it } from "vitest";
import { relayKey } from "@/lib/chat/relay-client";
import { routeMessage } from "@/lib/chat/ui";
import { nameOf } from "@/components/chat/bare";

describe("calling agents", () => {
  const mine = { name: "claude", owner: "chaitanya", mine: true };
  const hers = { name: "claude", owner: "alice", mine: false };
  it("your own agent is called by name, someone else's by owner/name", () => {
    expect(relayKey(mine)).toBe("claude");
    expect(relayKey(hers)).toBe("alice/claude");
  });
  const relay = ["claude", "chaitanya/claude", "alice/claude"];
  it("@claude is yours and @alice/claude is hers", () => {
    expect(routeMessage("@claude hello", { local: [], relay, people: [] }).toRelay).toEqual(["claude"]);
    expect(routeMessage("@alice/claude hello", { local: [], relay, people: [] }).toRelay).toEqual(["alice/claude"]);
    expect(routeMessage("@alice/claude/Q4 plan hello", { local: [], relay, people: [] }).toRelay).toEqual(["alice/claude"]);
  });
  it("an agent is shown exactly as it joined", () => {
    expect(nameOf("@chaitanya/claude-code/Q4 plan review")).toBe("chaitanya/claude-code/Q4 plan review");
    expect(nameOf("@alice/claude")).toBe("alice/claude");
  });
});
