import { describe, expect, it } from "vitest";
import { routeMessage } from "@/lib/chat/ui";

const base = { local: [], relay: ["claude-code"], people: ["@asha"] };

describe("an answer with no @name continues with the agent that spoke last", () => {
  it("goes to the connected agent that just spoke", () => {
    const r = routeMessage("yes, go ahead", { ...base, follow: "claude-code" });
    expect(r.toRelay).toEqual(["claude-code"]);
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
describe("a connected agent's chat title is shown as given", () => {
  it("keeps spaces and capitals; a plain tool handle is unchanged", () => {
    expect(nameOf("@chaitanya/Q4 plan review")).toBe("Q4 plan review");
    expect(nameOf("@chaitanya/claude-code")).toBe("Chaitanya/claude-code");
    expect(nameOf("@kell")).toBe("Kell");
  });
});
