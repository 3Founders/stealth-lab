import { describe, expect, it } from "vitest";
import { routeMessage } from "@/lib/chat/ui";

const base = { local: [], relay: ["claude-code"], people: ["@asha"] };

describe("only kell continues with the agent that spoke last", () => {
  it("does not stay with a connected agent after its answer", () => {
    const r = routeMessage("yes, go ahead", { ...base, follow: "claude-code" });
    expect(r.toRelay).toEqual([]);
    expect(r.asksKell).toBe(false);                                  // the room has another person: the line is just a room message
    const solo = routeMessage("yes, go ahead", { local: [], relay: ["claude-code"], people: [], follow: "claude-code" });
    expect(solo.toRelay).toEqual([]);
    expect(solo.asksKell).toBe(true);                                // a solo room falls back to kell
  });
  it("goes to kell when kell spoke last, even with other people in the room", () => {
    expect(routeMessage("and the second one?", { ...base, follow: "kell" }).asksKell).toBe(true);
  });
  it("an @name always wins, and nobody is followed when no agent spoke last", () => {
    expect(routeMessage("@asha look", { ...base, follow: "claude-code" }).toRelay).toEqual([]);
    expect(routeMessage("hello", { ...base, follow: null })).toMatchObject({ toRelay: [], asksKell: false });
  });
});

import { nameOf } from "@/components/chat/bare";
describe("a connected agent is shown by the handle it joined with", () => {
  it("keeps the owner, spaces and capitals; a plain handle is capitalised", () => {
    expect(nameOf("@chaitanya/Q4 plan review")).toBe("chaitanya/Q4 plan review");
    expect(nameOf("@chaitanya/claude-code")).toBe("chaitanya/claude-code");
    expect(nameOf("@kell")).toBe("Kell");
  });
});
