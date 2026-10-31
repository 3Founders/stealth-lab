import { describe, expect, it } from "vitest";
import { canChangeRole, canDissolve, canRemove, newRoomId, seedRoles } from "@/lib/chat/rooms";
import { Broker } from "@/lib/chat/relay/broker";

describe("rooms and roles in the sandbox", () => {
  it("names a room from what was typed, and refuses blanks and repeats", () => {
    expect(newRoomId(["#a"], "Design Review")).toEqual({ id: "#design-review" });
    expect("error" in newRoomId(["#a"], "  ")).toBe(true);
    expect("error" in newRoomId(["#a"], "#A")).toBe(true);
  });
  it("lets only the owner change roles or dissolve, and an admin remove only plain members", () => {
    expect(canChangeRole("owner", "member")).toBe(true);
    expect(canChangeRole("owner", "owner")).toBe(false);
    expect(canChangeRole("admin", "member")).toBe(false);
    expect(canDissolve("owner")).toBe(true);
    expect(canDissolve("admin")).toBe(false);
    expect(canRemove("admin", "member", false)).toBe(true);
    expect(canRemove("admin", "admin", false)).toBe(false);
    expect(canRemove("admin", "owner", false)).toBe(false);
    expect(canRemove("owner", "admin", false)).toBe(true);
    expect(canRemove("member", "member", true)).toBe(true);
    expect(canRemove("owner", "owner", true)).toBe(false);
  });
  it("starts people as members under one owner, and gives agents no role", () => {
    const roles = seedRoles([{ id: "#r", title: "r", members: ["@alice", "@bob", "@alice/claude-code"] }], "@alice");
    expect(roles["#r"]).toEqual({ "@alice": "owner", "@bob": "member" });
  });
  it("forgets a removed member and a dissolved room", () => {
    const b = new Broker();
    b.addRoom({ id: "#r", title: "r", members: ["@alice", "@bob"] });
    b.removeMember("#r", "@alice");
    expect(b.room("#r")?.members).toEqual(["@bob"]);
    b.removeRoom("#r");
    expect(b.room("#r")).toBeUndefined();
  });
});
