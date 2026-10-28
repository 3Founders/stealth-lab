import { describe, expect, it } from "vitest";
import { DEFAULT_MCP_ORIGIN, SITES, TASK_PROMPT, connectorUrl } from "@/lib/connector";

describe("connectorUrl", () => {
  it("is null for an empty address", () => {
    expect(connectorUrl("")).toBeNull();
  });
  it("adds /mcp to an origin and keeps it on an address that has it", () => {
    expect(connectorUrl("https://mcp.example.com")).toBe("https://mcp.example.com/mcp");
    expect(connectorUrl("https://mcp.example.com/")).toBe("https://mcp.example.com/mcp");
    expect(connectorUrl("https://mcp.example.com/mcp")).toBe("https://mcp.example.com/mcp");
    expect(connectorUrl("https://h.example.com/base")).toBe("https://h.example.com/base/mcp");
  });
  it("refuses plain http except on loopback, and anything that is not a URL", () => {
    expect(connectorUrl("http://mcp.example.com")).toBeNull();
    expect(connectorUrl("http://127.0.0.1:8765")).toBe("http://127.0.0.1:8765/mcp");
    expect(connectorUrl("not a url")).toBeNull();
  });
});

describe("the hosted address", () => {
  it("is https://mcp.kell.org.in/mcp unless the deployment overrides it", () => {
    expect(DEFAULT_MCP_ORIGIN).toBe("https://mcp.kell.org.in");
    expect(connectorUrl(DEFAULT_MCP_ORIGIN)).toBe("https://mcp.kell.org.in/mcp");
    expect(connectorUrl()).toBe(process.env.NEXT_PUBLIC_KEL_MCP_URL ? connectorUrl(process.env.NEXT_PUBLIC_KEL_MCP_URL) : "https://mcp.kell.org.in/mcp");
  });
});

describe("the connect page content", () => {
  it("tells the model the unit is a task and when to call find_ways", () => {
    expect(TASK_PROMPT).toContain("find_ways");
    expect(TASK_PROMPT).toContain("A task is a goal plus the context");
    expect(TASK_PROMPT).toContain("untrusted data");
    expect(TASK_PROMPT).toContain("Delegate by default.");
    expect(TASK_PROMPT).toContain("call_model");
    expect(TASK_PROMPT).toContain("glm-5.3-flash");
    expect(TASK_PROMPT).toContain("Never send keys, credentials or private material.");
  });
  it("covers Claude and ChatGPT with steps and where to paste the instruction", () => {
    expect(SITES.map((s) => s.id)).toEqual(["claude", "chatgpt"]);
    for (const s of SITES) {
      expect(s.steps.length).toBeGreaterThan(2);
      expect(s.promptWhere).not.toBe("");
      expect(s.openUrl.startsWith("https://")).toBe(true);
    }
  });
});
