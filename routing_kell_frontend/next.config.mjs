/** @type {import('next').NextConfig} */

// Pages where a user signs in or approves access. The MCP security best-practices page requires the consent
// screen to be un-frameable (clickjacking: an attacker frames "Allow" under a decoy), so both the modern
// (`frame-ancestors`) and the legacy (`X-Frame-Options`) forms are sent. `no-referrer` keeps the
// `authorization_id` in the consent URL out of any Referer header, and `no-store` keeps the page out of caches.
export const SENSITIVE_PAGE_HEADERS = [
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Cache-Control", value: "no-store" },
];
export const SENSITIVE_PAGE_SOURCES = ["/oauth/consent", "/sign-in", "/account/:path*"];

export default {
  reactStrictMode: true,
  // `irm .../install.ps1 | iex` and `curl .../install.sh | bash` need the
  // installers as text, not a download (see scripts/sync-installers.mjs).
  async headers() {
    return [
      ...["/install.sh", "/install.ps1"].map((source) => ({
        source,
        headers: [
          { key: "Content-Type", value: "text/plain; charset=utf-8" },
          { key: "Cache-Control", value: "public, max-age=300" },
        ],
      })),
      ...SENSITIVE_PAGE_SOURCES.map((source) => ({ source, headers: SENSITIVE_PAGE_HEADERS })),
    ];
  },
};
