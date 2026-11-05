import { describe, expect, it } from "vitest";
import { toolLabel } from "@/lib/chat/rooms-api";

describe("toolLabel", () => {
  it("names a registry server the way people say it", () => {
    expect(toolLabel("io.github.scite/scite-mcp")).toBe("Scite");
    expect(toolLabel("ai.exa/exa")).toBe("Exa");
    expect(toolLabel("io.github.peer/peer-research-papers")).toBe("Peer Research Papers");
    expect(toolLabel("com.example/mcp-server")).toBe("Server");
    expect(toolLabel("")).toBe("This tool");
  });
});
