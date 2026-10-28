// @vitest-environment node
import { describe, expect, it } from "vitest";
import { Broker } from "@/lib/delegator/relay/broker";

describe("removing an agent", () => {
  it("takes it out of the room and stops waking it", async () => {
    const b = new Broker();
    b.addRoom({ id: "#r", title: "r", members: ["@you", "@assistant-1", "@assistant-2"] });
    const woken: string[] = [];
    for (const h of ["@assistant-1", "@assistant-2"]) { b.registerAgent(h); b.attach(h, async () => { woken.push(h); }); }
    b.removeMember("#r", "@assistant-1");
    expect(b.room("#r")?.members).toEqual(["@you", "@assistant-2"]);
    expect(b.isAgent("@assistant-1")).toBe(false);
    await b.send({ id: b.newId(), roomId: "#r", sender: { handle: "@you", displayName: "You", type: "human", status: "online" }, recipients: ["@all"], payload: { text: "hi" }, timestamp: Date.now(), depthCounter: 0 });
    expect(woken).toEqual(["@assistant-2"]);
  });
});
