// Who a room message is for: a mention of another agent never falls through to @kell; the solo-room default only applies
// to a line with no @handle at all.
import { describe, expect, it } from "vitest";
import { routeMessage } from "@/lib/chat/ui";

const room = (over: Partial<{ local: string[]; relay: string[]; people: string[] }> = {}) =>
  routeMessage("@test1", { local: [], relay: [], people: [], ...over });

describe("room message routing", () => {
  it("an unknown @name is refused, never handed to @kell", () => {
    const d = room();
    expect(d.unknown).toEqual(["test1"]);
    expect(d.toRelay).toEqual([]);
    expect(d.asksKell).toBe(false);
    expect(d.defaultToKell).toBe(false);
  });

  it("a connector agent is the only recipient", () => {
    const d = room({ relay: ["test1"] });
    expect(d.toRelay).toEqual(["test1"]);
    expect(d.asksKell).toBe(false);
    expect(d.unknown).toEqual([]);
  });

  it("an untagged line in a solo room goes to @kell", () => {
    const d = routeMessage("what can you do?", { local: [], relay: [], people: [] });
    expect(d.asksKell).toBe(true);
    expect(d.defaultToKell).toBe(true);
  });

  it("an untagged line with another person does not go to @kell", () => {
    const d = routeMessage("hello", { local: [], relay: [], people: ["@alice"] });
    expect(d.asksKell).toBe(false);
    expect(d.defaultToKell).toBe(false);
  });

  it("@kell is asked when named, with or without another mention", () => {
    expect(routeMessage("@kell hi", { local: [], relay: [], people: [] }).asksKell).toBe(true);
    const both = routeMessage("@test1 @kell hi", { local: [], relay: ["test1"], people: [] });
    expect(both.toRelay).toEqual(["test1"]);
    expect(both.asksKell).toBe(true);
  });

  it("an in-tab agent mention stays local and does not wake @kell", () => {
    const d = routeMessage("@reviewer please look", { local: ["@reviewer"], relay: [], people: [] });
    expect(d.localMentions).toEqual(["@reviewer"]);
    expect(d.asksKell).toBe(false);
  });
});

describe("another person's kell (@anuj/kell)", () => {
  const opts = { local: [], relay: [], people: ["@anuj"], remoteKells: ["@anuj/kell"] };
  it("is answered by that person's kell, not refused as unknown and not handed to your own @kell", () => {
    const r = routeMessage("@anuj/kell what are you working on?", opts);
    expect(r.remoteKells).toEqual(["@anuj/kell"]);
    expect(r.unknown).toEqual([]);
    expect(r.asksKell).toBe(false);
  });
  it("is still refused when that person is not in the room", () => {
    const r = routeMessage("@bob/kell hi", opts);
    expect(r.remoteKells).toEqual([]);
    expect(r.unknown).toEqual(["bob/kell"]);
  });
});
