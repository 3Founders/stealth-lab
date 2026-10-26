// The first-time install for keळ: `setup` registers the agents, writes the model settings, and checks what is still
// missing. (The site still serves public/install.sh and install.ps1, but those run only `install`, which registers
// the agents and nothing else.)
export type InstallPlatform = "unix" | "windows";

export const SETUP_COMMAND = "npx -y kell-mcp setup";

export function installPrompt(platform: InstallPlatform): string {
  return platform === "windows" ? "PS> " : "$ ";
}

export function defaultInstallPlatform(userAgent: string | undefined): InstallPlatform {
  return userAgent && /windows/i.test(userAgent) ? "windows" : "unix";
}
