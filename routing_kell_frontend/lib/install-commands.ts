// The first-time install for keळ: `setup` registers the agents, writes the model settings, and checks what is still
// missing. (The site still serves public/install.sh and install.ps1, but those run only `install`, which registers
// the agents and nothing else.)
export type InstallPlatform = "unix" | "windows";

export const SETUP_COMMAND = "npx -y kell-mcp setup";

// The desktop app's whole install: the script downloads the newest installer from /download, runs it silently for the
// signed-in user and opens kell. The host is the canonical www one on purpose: kell.org.in answers with a 308 to www,
// and Windows PowerShell 5.1's Invoke-RestMethod does not follow a 308, so the apex would fail for the people most
// likely to run this. Keep this string and public/desktop.ps1's own default site on the same host.
export const DESKTOP_INSTALL_COMMAND = "irm https://www.kell.org.in/desktop.ps1 | iex";
// Linux (x86_64): the AppImage into ~/.local/bin with a menu entry (public/desktop.sh, 2026-11-17).
export const DESKTOP_INSTALL_COMMAND_LINUX = "curl -fsSL https://www.kell.org.in/desktop.sh | sh";

export function installPrompt(platform: InstallPlatform): string {
  return platform === "windows" ? "PS> " : "$ ";
}

export function defaultInstallPlatform(userAgent: string | undefined): InstallPlatform {
  return userAgent && /windows/i.test(userAgent) ? "windows" : "unix";
}
