# Admin console: signing in as an admin, and trying billing yourself

The admin console is the separate app in `admin/`. It uses the same Supabase sign-in as the website. What you can see is decided by the **backend**, from grants in the database, never by the page.

Two kinds of access:

| Access | Where it comes from | What it opens |
|---|---|---|
| **Organisation admin** (owner or admin) | Being a member of an organisation with that role (`org_memberships`). Owners and admins are set by whoever administers the organisation. | The per-organisation pages: Executive, Get started, Team, Policy, Usage, Performance, People, Calls, Denials, Audit, Compliance. |
| **Platform admin** | A row in `platform_role_grants` (role `platform_admin`), which gives the `admin:ops` scope. | The operator pages: Operations, Ingestion, Review, Moderation, Credits, **Billing**, Security. |

## 1. Run the console

```powershell
cd admin
copy .env.example .env.local        # then fill in the values below
npm install
npm run dev                          # http://localhost:3200
```

`.env.local`: `NEXT_PUBLIC_KEL_API_URL` (the backend), `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` (the same Supabase project as the website). The backend must allow the console's origin in its `FRONTEND_ORIGIN` setting (add `http://localhost:3200`).

To see the pages with sample data and no backend: `npm run dev:demo` (port 3201). Billing is not part of the sample.

## 2. Become a platform admin (once)

1. Sign in to the **website** (or the console) once with the account you want to use. That creates your row in `users`.
2. From `backend/`, with `DATABASE_URL` set (the script also reads `backend/.env`):

```powershell
python scripts\grant_platform_admin.py --email you@example.com            # shows who it matched; changes nothing
python scripts\grant_platform_admin.py --email you@example.com --apply    # grants platform_admin
```

Take it away again with `--revoke --apply`. The grant is read from the database on every request, so it applies at once; sign out and in again if the console still says "not an admin".

## 3. Apply the billing tables

`python scripts\migrate.py` (migration `161_invoicing.sql`: seller details, invoices, credit notes, and the order id on credits). Apply it **before** deploying the backend that has the invoicing routes. The payment webhook still credits a payment if the migration is late (it falls back and logs a warning), but the Billing page needs the tables.

## 4. Second factor (recommended before real money)

- In the Supabase project, turn on **multi-factor authentication (TOTP)** under Authentication.
- In the console open **Security**, choose "Set up an authenticator", scan the QR code, enter the code. Your session is now at the second level (`aal2`).
- To make it required:
  - backend: `ADMIN_REQUIRE_MFA=on` (the Billing endpoints then refuse a token without the second factor);
  - console: `NEXT_PUBLIC_ADMIN_REQUIRE_MFA=on` (it sends you to Security first).
- Supabase TOTP has **no recovery codes**. If someone loses their authenticator, another admin removes their factor, so keep at least two admins. Security keys and passkeys are not offered yet.

## 5. Try billing

1. **Billing → Seller details**: legal name, GSTIN, state, address, SAC code, GST rate, invoice prefix. Invoices cannot be issued until these are complete and consistent (the GSTIN's first two digits must match the state). The SAC code and rate are for your accountant to confirm; nothing is guessed.
2. **Payments** lists money that arrived (needs the Razorpay keys and webhook from `docs/billing.md`; in test mode you can make a ₹100 order from the API). **Issue invoice** on a payment asks for the buyer's name, GSTIN if they have one, and state (place of supply). Same state as the seller: CGST + SGST; another state: IGST. The amount is treated as the total the buyer paid, GST included.
3. **Invoices**: view, print or save as PDF from the browser, issue a **credit note** (reverses the whole invoice; invoices are never edited), and export a CSV for the accountant.
4. **Reconcile** shows orders that were started but never credited, and payments that have no invoice. It never creates a credit.

## What is not built (so you are not surprised)

- Government e-invoice registration (IRN and QR code). The research found the threshold at ₹5 crore of turnover from August 2023 and no official change since; confirm with your accountant whether it applies yet.
- A server-made PDF or emailed invoice (print to PDF from the browser for now).
- Refunds through the gateway (a credit note records the reversal on paper; the money movement is done in the Razorpay dashboard).
- GST added on top of the amount (only GST-inclusive totals).
- Team page: changing roles, deactivating members and transferring ownership. Invitations and the member list work.
- SSO, SCIM, IP allowlists, API-key management. See `docs/team_enterprise_admin_research.md` for what is standard and a suggested order.
