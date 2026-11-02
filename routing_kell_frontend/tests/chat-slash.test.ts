import { describe, expect, it } from "vitest";
import { matchCommands, slashQuery } from "@/lib/chat/ui";

describe("slash commands menu", () => {
  it("opens only for a first word that starts with /", () => {
    expect(slashQuery("/", 1)).toBe("");
    expect(slashQuery("/ad", 3)).toBe("ad");
    expect(slashQuery("hello /ad", 9)).toBeNull();
    expect(slashQuery("/add-agent --name=x", 19)).toBeNull();
  });
  it("lists matching commands, and nothing once one is typed in full so Enter sends it", () => {
    expect(matchCommands("").map((c) => c.name)).toEqual(["/add-agent", "/stop"]);
    expect(matchCommands("st").map((c) => c.name)).toEqual(["/stop"]);
    expect(matchCommands("stop")).toEqual([]);
    expect(matchCommands("zzz")).toEqual([]);
  });
});
