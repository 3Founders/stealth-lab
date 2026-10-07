/** @type {import('next').NextConfig} */

// The admin console is never public: no indexing, no framing, no caching, no Referer leakage.
// Authorization is NOT decided here -- every request carries the signed-in user's Supabase token and the
// backend enforces the ADMIN_OPS scope (see lib/admin-api.ts). Hiding a page is a convenience, not a gate.
export const ADMIN_HEADERS = [
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Robots-Tag", value: "noindex, nofollow, noarchive" },
  { key: "Cache-Control", value: "no-store" },
];

export default {
  reactStrictMode: true,
  async headers() {
    return [{ source: "/:path*", headers: ADMIN_HEADERS }];
  },
};
