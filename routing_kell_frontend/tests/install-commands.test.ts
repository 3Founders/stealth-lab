import { describe, expect, it } from "vitest";
import { SETUP_COMMAND, defaultInstallPlatform, installPrompt } from "@/lib/install-commands";

describe("install commands", () => {
  it("is the first-time setup command", () => {
    expect(SETUP_COMMAND).toBe("npx -y kell-mcp setup");
  });

  it("shows a PowerShell prompt to Windows visitors", () => {
    expect(defaultInstallPlatform("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows");
    expect(defaultInstallPlatform("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)")).toBe("unix");
    expect(defaultInstallPlatform(undefined)).toBe("unix");
    expect(installPrompt("windows")).toBe("PS> ");
    expect(installPrompt("unix")).toBe("$ ");
  });
});
