/** @type {import('next').NextConfig} */
export default {
  reactStrictMode: true,
  // `irm .../install.ps1 | iex` and `curl .../install.sh | bash` need the
  // installers as text, not a download (see scripts/sync-installers.mjs).
  async headers() {
    return ["/install.sh", "/install.ps1"].map((source) => ({
      source,
      headers: [
        { key: "Content-Type", value: "text/plain; charset=utf-8" },
        { key: "Cache-Control", value: "public, max-age=300" },
      ],
    }));
  },
};
