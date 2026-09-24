// One-line installers for the StealthLab MCP connector. The scripts are
// served by this site (public/install.sh, public/install.ps1 -- copied from
// packaging/npm/install at build time), so the commands point at whatever
// origin the site is running on.
export type InstallPlatform = "unix" | "windows" | "npx";

export const INSTALL_PLATFORMS: ReadonlyArray<{ id: InstallPlatform; label: string }> = [
  { id: "unix", label: "macOS / Linux" },
  { id: "windows", label: "Windows" },
  { id: "npx", label: "npx" },
];

export function installCommand(platform: InstallPlatform, origin: string): string {
  const base = origin.replace(/\/+$/, "");
  switch (platform) {
    case "unix":
      return `curl -fsSL ${base}/install.sh | bash`;
    case "windows":
      return `irm ${base}/install.ps1 | iex`;
    case "npx":
      return "npx -y stealthlab-mcp install";
  }
}

export function defaultInstallPlatform(userAgent: string | undefined): InstallPlatform {
  return userAgent && /windows/i.test(userAgent) ? "windows" : "unix";
}
