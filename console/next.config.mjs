/** @type {import('next').NextConfig} */

// A static bundle. `kell console --ui-dir console/out` serves it same-origin from the loopback helper, which is what lets
// the page call the helper with its per-run token and no cross-origin permission. Nothing here needs a Node server, so
// nothing here may use server features (route handlers, middleware, server actions, rewrites, headers()).
export default {
  reactStrictMode: true,
  output: "export",
  images: { unoptimized: true },
};
