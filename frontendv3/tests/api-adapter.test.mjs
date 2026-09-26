import test from 'node:test';
import assert from 'node:assert/strict';
import { GET, PUT } from '../src/app/api/backend/[...path]/route.ts';

function request(path, options) {
  const req = new Request(`http://localhost:3003/api/backend/${path}`, options);
  Object.defineProperty(req, 'nextUrl', { value: new URL(req.url) });
  return req;
}
const context = path => ({ params: Promise.resolve({ path: path.split('/') }) });

test('does not expose administrative routes through the frontend', async () => {
  const result = await GET(request('v1/admin/scan'), context('v1/admin/scan'));
  assert.equal(result.status, 404);
});

test('forwards identity and query while preventing private response caching', async t => {
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    assert.match(url, /\/v1\/problems\?limit=20$/);
    assert.equal(init.headers.get('authorization'), 'Bearer test-user-token');
    assert.equal(init.headers.get('cookie'), null);
    assert.equal(init.cache, 'no-store');
    return Response.json({ problems: [] });
  });
  const result = await GET(request('v1/problems?limit=20', {
    headers: { authorization: 'Bearer test-user-token', cookie: 'unrelated=private' },
  }), context('v1/problems'));
  assert.deepEqual(await result.json(), { problems: [] });
  assert.equal(result.headers.get('cache-control'), 'private, no-store');
});

test('retains backend denial instead of turning it into an empty result', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'Denied' }, { status: 403 }));
  const result = await GET(request('v1/me/profile'), context('v1/me/profile'));
  assert.equal(result.status, 403);
});

test('rejects cross-origin profile writes before contacting the backend', async t => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('must not run'); });
  const result = await PUT(request('v1/me/profile', {
    method: 'PUT', headers: { origin: 'https://another.example', authorization: 'Bearer test-user-token' },
    body: JSON.stringify({ visibility: 'public' }),
  }), context('v1/me/profile'));
  assert.equal(result.status, 403);
  assert.equal(fetch.mock.callCount(), 0);
});

test('reports upstream failure without exposing infrastructure details', async t => {
  t.mock.method(globalThis, 'fetch', async () => { throw new Error('internal-host-secret'); });
  const result = await GET(request('v1/problems'), context('v1/problems'));
  assert.equal(result.status, 503);
  assert.doesNotMatch(await result.text(), /internal-host-secret/);
});
