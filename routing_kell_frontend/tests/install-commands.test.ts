import { describe, expect, it } from "vitest";
import { DESKTOP_INSTALL_COMMAND, SETUP_COMMAND, defaultInstallPlatform, installPrompt } from "@/lib/install-commands";

describe("install commands", () => {
  it("is the first-time setup command", () => {
    expect(SETUP_COMMAND).toBe("npx -y kell-mcp setup");
  });

  it("is the Windows desktop one-liner, on the canonical www host", () => {
    expect(DESKTOP_INSTALL_COMMAND).toBe("irm https://www.kell.org.in/desktop.ps1 | iex");
  });

  it("shows a PowerShell prompt to Windows visitors", () => {
    expect(defaultInstallPlatform("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows");
    expect(defaultInstallPlatform("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)")).toBe("unix");
    expect(defaultInstallPlatform(undefined)).toBe("unix");
    expect(installPrompt("windows")).toBe("PS> ");
    expect(installPrompt("unix")).toBe("$ ");
  });
});
