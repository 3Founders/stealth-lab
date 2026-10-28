import { describe, expect, it } from "vitest";
import { HUMAN, MAX_AGENTS, newAgentHandle } from "@/lib/delegator/agents";

describe("adding agents", () => {
  it("the first free @assistant-N, in order, filling gaps", () => {
    expect(newAgentHandle([])).toEqual({ handle: "@assistant-1" });
    expect(newAgentHandle(["@assistant-1"])).toEqual({ handle: "@assistant-2" });
    expect(newAgentHandle(["@assistant-1", "@assistant-3"])).toEqual({ handle: "@assistant-2" });
  });
  it("a chosen name becomes the handle, lower-cased, once", () => {
    expect(newAgentHandle([], "Reviewer")).toEqual({ handle: "@reviewer" });
    expect(newAgentHandle(["@reviewer"], "REVIEWER")).toMatchObject({ error: expect.stringContaining("already") });
    expect(newAgentHandle([], "you")).toEqual({ handle: "@you" }.handle === HUMAN.handle ? { error: expect.anything() } : { handle: "@you" });
  });
  it("refuses an odd name and a sixth agent", () => {
    expect(newAgentHandle([], "bad name!")).toMatchObject({ error: expect.any(String) });
    expect(newAgentHandle([], "x".repeat(25))).toMatchObject({ error: expect.any(String) });
    const full = Array.from({ length: MAX_AGENTS }, (_, i) => `@a${i}`);
    expect(newAgentHandle(full)).toMatchObject({ error: expect.stringContaining(String(MAX_AGENTS)) });
  });
});
