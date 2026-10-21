# run.md -- hand-written plan for the organization-key and join work (plan_and_run format).
# done = built and its check passed offline on 2026-10-21. ready/blocked = work another person or agent can take; claim a
# node by changing its status to running and putting your name in the commit message. Re-run `plan validate` after editing.

NODE|N-1|done|Migration 151: org_provider_keys and org_invites with strict RLS|step=P-1:0|claims=O-005|deps=-|check=test -s backend/db/151_org_provider_keys_and_invites.sql
NODE|N-2|done|Backend: secret_box, org_keys, org_onboarding, routes, provision_org script (41 offline tests)|step=P-1:1|claims=O-003..O-004,O-006..O-007,O-009..O-011|deps=N-1|check=cd backend && python -m pytest tests/test_org_onboarding_offline.py -q
NODE|N-3|done|Client: join command, key_from profiles, setup recognises them (13 tests)|step=P-1:3|claims=O-001,O-008,O-013|deps=-|check=cd packaging/npm && node --test test/join.test.mjs
NODE|N-4|done|Admin console: Get started page (keys, policy, invites, ready count)|step=P-1:2|claims=O-002|deps=-|check=cd admin && npx vitest run tests/onboarding-api.test.ts
NODE|N-5|done|docs/setup.md: the whole flow, files, operator steps, gaps|step=-|claims=O-017|deps=N-2,N-3,N-4|check=test -s docs/setup.md
NODE|N-6|ready|Apply migration 151 to staging and confirm it is the highest applied|step=P-1:0|claims=O-017|deps=N-1|check=cd backend && python scripts/migrate.py --status
NODE|N-7|ready|Real-database e2e test: RLS isolates two organizations, an invite is single-use under concurrency, a key round-trips encrypted|step=-|claims=O-005,O-012|deps=N-6|check=test -f backend/tests/test_org_onboarding_e2e.py
NODE|N-8|ready|Settle one statement of what leaves a developer's machine and make the hook and README agree; add a per-organization switch|step=-|claims=O-014|deps=-|check=grep -q "what leaves" packaging/npm/README.md
NODE|N-9|blocked|Host the API and MCP server, set PUBLIC_API_URL, publish the npm package and fill defaultMcpUrl|step=P-1:0|claims=O-011,O-015|deps=N-6|check=grep -q '"defaultMcpUrl": "https' packaging/npm/package.json
NODE|N-10|blocked|Run join on a real machine with Cursor and record the readiness output and hooks.log|step=P-1:3|claims=O-017|deps=N-9|check=test -f docs/join_first_real_run.md
NODE|N-11|ready|Register the organization key store in the MCP server process so call_model sees organization keys|step=-|claims=O-006,O-016|deps=-|check=grep -q "org_keys" backend/app/mcp_server/server.py
NODE|N-12|ready|Send X-Stealthlab-Org from hook dispatch calls so a person in two organizations is not refused|step=-|claims=O-006|deps=-|check=grep -rq "x-stealthlab-org" packaging/npm/lib/dispatch.mjs
NODE|N-13|ready|Member removal, role change and SSO provisioning routes with audit|step=-|claims=O-009,O-010,O-015|deps=-|check=test -f backend/tests/test_org_membership_offline.py
NODE|N-14|ready|Key rotation: a second encryption key setting and a re-encrypt job, plus a test call when a key is saved|step=-|claims=O-003|deps=-|check=test -f backend/scripts/rotate_org_keys.py
