import { describe, expect, it } from "vitest";
import { connectorInstructions, newOnboardName, onboardPrompt } from "@/lib/chat/onboard";
import { validAgentName } from "@/lib/chat/relay-client";
import { planLine } from "@/lib/chat/plan";

const URL = "https://mcp.example.org/mcp";

describe("Connect agent", () => {
  it("a new name is always one the server accepts", () => {
    for (let i = 0; i < 200; i++) expect("error" in validAgentName(newOnboardName())).toBe(false);
  });
  it("the message for Claude Code and Cursor names the address, the agent, its inbox tool and the room", () => {
    const t = onboardPrompt({ name: "agent-ab12", room: "#general", url: URL });
    expect(t).toContain(`claude mcp add --transport http kell ${URL}`);
    expect(t).toContain('delegate_inbox, with agent="agent-ab12"');
    expect(t).toContain("#general");
    expect(t).toContain("@agent-ab12");
  });
  it("for the web tools the instructions lead with the connector address and the page steps", () => {
    const t = connectorInstructions({ name: "agent-ab12", room: "#general", url: URL, kind: "chatgpt" });
    expect(t.startsWith(`kell connector address: ${URL}`)).toBe(true);
    expect(t).toContain("developer mode");
    expect(t).toContain("kell's connector should already be added in ChatGPT");
    expect(connectorInstructions({ name: "a", room: "#r", url: URL, kind: "claude-web" })).toContain("Claude: Settings, Connectors, Add custom connector");
  });
});

describe("the plan line in the profile", () => {
  const now = new Date("2026-11-02T00:00:00Z");
  it("shows the plan and when it ends, an ended plan, no plan, and nothing while unknown", () => {
    expect(planLine({ plan: "pro", activeUntil: "2026-11-28T00:00:00Z" }, now)).toBe("Pro plan · until 28 November 2026");
    expect(planLine({ plan: "plus", activeUntil: "2026-10-01T00:00:00Z" }, now)).toBe("Plus plan ended");
    expect(planLine({ plan: null, activeUntil: null }, now)).toBe("No plan yet");
    expect(planLine(null, now)).toBe("");
  });
});
