import { describe, expect, it } from "vitest";
import { defaultInstallPlatform, installCommand } from "@/lib/install-commands";

describe("install commands", () => {
  it("point at the site's own installer scripts", () => {
    expect(installCommand("unix", "https://kel.example/")).toBe("curl -fsSL https://kel.example/install.sh | bash");
    expect(installCommand("windows", "https://kel.example")).toBe("irm https://kel.example/install.ps1 | iex");
    expect(installCommand("npx", "https://kel.example")).toBe("npx -y stealthlab-mcp install");
  });

  it("defaults Windows visitors to the PowerShell one-liner", () => {
    expect(defaultInstallPlatform("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows");
    expect(defaultInstallPlatform("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)")).toBe("unix");
    expect(defaultInstallPlatform(undefined)).toBe("unix");
  });
});
