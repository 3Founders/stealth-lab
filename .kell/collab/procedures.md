# procedures.md -- hand-written planner file (the plan_and_run format) for the organization-key and join work.
# `stealthlab-mcp plan validate` reads .kell/procedures.md and .kell/run.md; to check this copy, put these three
# files under a temporary <root>/.kell/ and run `stealthlab-mcp plan validate --root <root>`.

PROCEDURE|P-1|org-key-onboarding-v1|v1|Org-held provider keys and developer onboarding|goal=A company's developers use its models through the company's own key with one command
STEP|P-1:0|action|Apply migration 151 and set ORG_KEY_ENCRYPTION_KEY and PUBLIC_API_URL|locator=backend/db/151_org_provider_keys_and_invites.sql|needs=DATABASE_URL|check=python backend/scripts/migrate.py --status
STEP|P-1:1|action|Operator creates the organization and its owner|locator=backend/scripts/provision_org.py|needs=owner has signed in once|check=python backend/scripts/provision_org.py --name X --slug x --owner-email e --dry-run
STEP|P-1:2|action|Owner connects the provider key, applies the policy with budgets, creates an invite in the admin console|locator=admin/app/start/page.tsx|needs=-|check=cd admin && npx vitest run tests/onboarding-api.test.ts
STEP|P-1:3|action|Developer runs the join command and reads the readiness list|locator=packaging/npm/lib/join.mjs|needs=invite,mcp url,api url|check=cd packaging/npm && node --test test/join.test.mjs
STEP|P-1:4|instruction|Read the Ready count on the Get started page; a developer is ready after one settled gateway call|locator=backend/app/services/org_onboarding.py|needs=-|check=cd backend && python -m pytest tests/test_org_onboarding_offline.py -q
