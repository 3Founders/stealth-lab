import type { NextConfig } from 'next';
import path from 'node:path';
const config: NextConfig = { reactStrictMode: true, poweredByHeader: false, outputFileTracingRoot: path.join(process.cwd(), '..'), outputFileTracingIncludes: { '/docs/*': ['../docs/legal/*.md', '../SECURITY.md', '../DATA_STATEMENT.md', '../backend/README_MCP_SERVER.md'] }, async headers() { return [{source:'/(.*)',headers:[{key:'X-Content-Type-Options',value:'nosniff'},{key:'Referrer-Policy',value:'strict-origin-when-cross-origin'},{key:'X-Frame-Options',value:'DENY'}]}]; } };
export default config;
