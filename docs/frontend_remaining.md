# Frontend: what is not said, and what is still open

The public pages say what Kell does. They do not list what Kell lacks or does not claim. Those things are kept here so
nobody reads a page and takes its silence as a promise, and so each one has an owner and a way to be closed.

Written 2026-10-28. Applies to `routing_kell_frontend/` (the live site), `console/` (the local console), and, where noted,
`prod_frontend/` and `admin/`.

## Not claimed on the public pages

| Not claimed | Why it matters | What would let us say it |
|---|---|---|
| A security certification (SOC 2, ISO 27001, HIPAA, PCI-DSS) | We hold none. Silence on the page must not become an implied claim. | A completed audit and its report |
| A third-party penetration test | None has been done | A test and a summary of findings |
| Zero data egress, or zero retention | Prompts and repository facts go to the Kell service; tasks and files go to the model providers a workspace uses. We have no signed zero-retention terms with every provider. | Written zero-retention terms from each provider and a log audit |
| Compliance with a named privacy law (GDPR, CCPA, DPDP and others) | Which laws apply has not been assessed | Counsel's assessment |
| Uptime or availability | The service runs as one instance and nothing has been measured | An external probe and 30 days of data |
| A contract with each subprocessor (DPA, standard clauses), their regions and retention | The Subprocessors page lists providers and purposes only | Signed terms and a recorded region per provider |
| A cost figure for any customer | Measured figures are per benchmark suite, against API prices, with small samples | A study on a real customer's tasks; see `docs/pilot_plan_lead_2026-10.md` |
| Results for presentations or open-ended question answering | Nothing in the repository tests them | The replay step of the pilot |
| A minimum age, a fees clause, an uptime promise | Not decided | A decision, then counsel |

## Open before the legal pages are final

- **Legal review.** None of the seven texts has been reviewed by counsel. Several statements come from internal notes
  and should be confirmed: hash-only query storage, no prompt or reply storage for routed calls, the database in the
  United States, backups kept for a limited time, the retention wording.
- **Company details.** `NEXT_PUBLIC_LEGAL_ENTITY`, `NEXT_PUBLIC_LEGAL_EMAIL` and `NEXT_PUBLIC_LEGAL_LAW` are unset, so
  the pages show no company name, no contact address and no governing law. The Privacy, Copyright and Security pages
  tell people to write to "the contact details at the bottom of this page", so a contact address is required.
- **Edits to `lib/legal-config.ts`'s inputs** appear on public pages at build time. Set them in the hosting
  environment, not in the repository.

## Open on the site

- **Headline and figures.** The home page headline ("Cut LLM API bills by 50% …") and its "~50%", "77%" and "93% → 91%"
  figures are not backed by a measurement on customer work. Per suite the cost change ranged from −89% to −1%, and the
  newest runs are mixed. Back the figure or soften it.
- **About.** Bios for Chaitanya Deshkar and Anuj Bhadbhade are not written. LinkedIn blocks automated reads, so the text
  has to come from the two of them.
- **Install command.** The site and the console show `npx -y kell-mcp …`. The `kell-mcp` package is not on npm (404),
  so the command fails for visitors until it is published.
- **Name.** The site still writes "keळ" in about 77 places outside the docs and legal pages. The docs and legal pages
  use "Kell".
- **Hosting.** The hosting account returned `402 DEPLOYMENT_DISABLED` on 2026-10-26. Deliberately left to the person who
  runs hosting.
- **Installer copies.** `routing_kell_frontend/scripts/sync-installers.mjs` still copies the install scripts from the
  repository at build time.

## Open on the console (`console/`)

- Not opened in a real browser. It is tested in a DOM against the real local helper and a fake provider.
- It is a local tool: the helper runs on the person's machine, and a hosted MCP cannot run it for them.
- `backend/scripts/start_mcp_local.ps1` does not start it, and `kell console` with no `--ui-dir` serves the API only.
  Both are filed on the build board for the setup session.
- The install preview shows the installer's own text, not a structured list of changed files.
- Start with `npm run mcp` from `console/`. The MCP and the console run independently, and each shows when the other
  closes. The real MCP was not started in testing, because it needs the backend environment and a database.

## Open in `prod_frontend/` and `admin/`

- **Pilot readout page.** The backend has no crossover flag, blind ratings, cost per accepted task or usage-export
  import, so a readout page would have nothing true to show. Needs backend work first.
- **Contact address** for the pilot page and the evidence page.
- **Sample data.** Admin's sample-data mode (`npm run dev:demo`) is development only and labelled; it must stay out of
  any production build.
