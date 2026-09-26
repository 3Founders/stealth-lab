import type { NextRequest } from 'next/server';

// Fixed upstream and explicit routes: this is a transport adapter, not an
// arbitrary proxy. Backend identity/visibility checks remain authoritative.
const readRoutes = [
  /^v1\/solutions\/search$/,
  /^v1\/best-way$/,
  /^v1\/problems(?:\/find|\/[\w-]+(?:\/(?:leaderboard|evaluations|benchmarks|solutions))?)?$/,
  /^v1\/procedures\/[\w-]+(?:\/(?:evidence|versions|claims))?$/,
  /^v1\/evaluations\/[\w-]+$/,
  /^v1\/tasks\/[\w-]+$/,
  /^v1\/me(?:\/(?:profile|export|deletion))?$/,
  /^v1\/contributors\/(?:search|leaderboard|[\w-]+)$/,
  /^v1\/workspaces$/,
];

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join('/');
  const allowed = request.method === 'GET'
    ? readRoutes.some(pattern => pattern.test(route))
    : request.method === 'PUT' && route === 'v1/me/profile';
  if (!allowed) return Response.json({ detail: 'Unsupported operation.' }, { status: 404 });

  // Never forward browser cookies or inject a shared MCP/service credential.
  const headers = new Headers({ Accept: 'application/json' });
  const authorization = request.headers.get('authorization');
  if (authorization) headers.set('Authorization', authorization);
  let body: string | undefined;
  if (request.method === 'PUT') {
    if (request.headers.get('origin') !== request.nextUrl.origin) {
      return Response.json({ detail: 'Origin not allowed.' }, { status: 403 });
    }
    if (!authorization) return Response.json({ detail: 'Sign in required.' }, { status: 401 });
    body = await request.text();
    if (body.length > 8192) return Response.json({ detail: 'Request too large.' }, { status: 413 });
    headers.set('Content-Type', 'application/json');
  }

  try {
    const origin = process.env.API_URL || 'http://127.0.0.1:8000';
    const upstream = await fetch(`${origin.replace(/\/$/, '')}/${route}${request.nextUrl.search}`, {
      method: request.method, headers, body, cache: 'no-store',
      redirect: 'error', signal: AbortSignal.timeout(12000),
    });
    if (!upstream.headers.get('content-type')?.includes('application/json')) {
      return Response.json({ detail: 'The library returned an unexpected response.' }, { status: 502 });
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'private, no-store' },
    });
  } catch (error) {
    const timeout = error instanceof Error && error.name === 'TimeoutError';
    return Response.json({ detail: timeout ? 'The library is taking too long to respond.' : 'The library is temporarily unavailable.' }, {
      status: timeout ? 504 : 503, headers: { 'Cache-Control': 'no-store' },
    });
  }
}

export const GET = forward;
export const PUT = forward;
