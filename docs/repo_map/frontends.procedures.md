# repo_map/frontends.procedures.md -- how to do the common frontend tasks, each step pinned to a file.
# Written 2026-10-19 at commit eb524ed. Shape mirrors a Procedure (schema.md: steps[].source_locator):
#   goal / preconditions (claim ids from frontends.claims.md) / steps, each `-> path[:line]` as the locator.
# A step's locator is where to LOOK or EDIT, not a citation of authority. Line numbers drift; `path` is the stable part.
# Retrieval: `rg -n "^## PROCEDURE" docs/repo_map/frontends.procedures.md`, then read one block.

## PROCEDURE add-a-page-to-the-website
goal: Add or change a public page on the keळ website (prod_frontend)
preconditions: F-001 F-007 F-008 F-023
steps:
  0. Create the route folder with a page.tsx -> prod_frontend/app/<route>/page.tsx   (sibling to prod_frontend/app/goals/page.tsx)
  1. Add the typed client call and response type -> prod_frontend/lib/kel-api.ts
  2. Reuse the not-signed-in / not-connected states -> prod_frontend/components/NotConnected.tsx
  3. Add or extend a test next to the client change -> prod_frontend/tests/kel-api.test.ts
  4. Check: `cd prod_frontend; npm run typecheck; npm test`   -> prod_frontend/package.json:9
  5. If a nav link is needed -> prod_frontend/components/Header.tsx

## PROCEDURE edit-legal-page
goal: Change a legal or policy page text
preconditions: F-009
steps:
  0. Edit the Markdown source -> prod_frontend/content/legal/<NAME>.md
  1. Slug list and loader, only if adding a new page -> prod_frontend/lib/legal.ts
  2. Rendered by -> prod_frontend/app/docs/legal/[slug]/page.tsx
  3. Do not claim more than the register allows -> final_prod_docs/06_claims_register.md

## PROCEDURE add-an-admin-page
goal: Add a dashboard page to the admin console
preconditions: F-002 F-010 F-011 F-012 F-014
steps:
  0. Create the route -> admin/app/<route>/page.tsx   (pattern: admin/app/usage/page.tsx)
  1. Put the data fetch in the matching lib module -> admin/lib/org-api.ts (org data) or admin/lib/admin-api.ts (global admin)
  2. Put pure calculations in their own lib file so they are testable -> admin/lib/<topic>.ts, test in admin/tests/<topic>.test.ts
  3. Add the sample-data answer so the page works with no backend -> admin/lib/demo.ts
  4. Add the nav entry -> admin/components/AdminHeader.tsx
  5. The backend route must already exist; if not, that is a backend change -> backend/app/api/org_admin.py or backend/app/api/admin.py
  6. Check: `cd admin; npm run typecheck; npm test`; look at it with `npm run dev:demo` (port 3201)

## PROCEDURE change-a-backend-response-shape
goal: A /v1 response changed; keep the frontends in step
preconditions: F-008 F-013 F-017
steps:
  0. Find who calls it -> rg -n "/v1/<path>" prod_frontend/lib admin/lib
  1. Update the website client -> prod_frontend/lib/kel-api.ts
  2. Update the admin clients -> admin/lib/org-api.ts, admin/lib/admin-api.ts
  3. Do NOT update frontendv1/2/3 or frontend/ (F-018 F-020); they are not live
  4. Run both typechecks and test suites

## PROCEDURE edit-stealth-files-by-hand
goal: Hand-edit a .stealth projection file in a browser
preconditions: F-003
steps:
  0. Start the editor API -> backend/scripts/stealth_md_editor.py   (`python scripts/stealth_md_editor.py --port 8767` from backend/)
  1. Start the React client -> stealth_md_editor_ui/src/App.tsx   (`npm run dev`)
  2. Open http://127.0.0.1:8767/?repo_path=<repo>   -> stealth_md_editor_ui/README.md
  3. Remember .stealth files are GENERATED projections, not canonical (see .stealth/claims.md R-077)

## PROCEDURE change-public-website-copy
goal: Edit text on the B2B public pages without breaking the copy rules
preconditions: F-023 F-024
steps:
  0. Home, evidence, trust, pilot pages -> prod_frontend/app/page.tsx, prod_frontend/app/evidence/page.tsx, prod_frontend/app/trust/page.tsx, prod_frontend/app/pilot/page.tsx
  1. Benchmark numbers come only from the handoff, per suite, never a total -> docs/HANDOFF_abtest_2026-10-11.md
  2. Data-path wording must match the subprocessor draft and the claims register -> prod_frontend/content/legal/SUBPROCESSORS.md, final_prod_docs/06_claims_register.md
  3. Nav and footer links -> prod_frontend/components/Header.tsx, prod_frontend/components/Footer.tsx
  4. Check: `cd prod_frontend; npm run typecheck; npm test` (b2b-site.test.ts holds the rules) -> prod_frontend/tests/b2b-site.test.ts
