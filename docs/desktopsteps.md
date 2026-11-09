# Desktop app: what an admin has to do (2026-11-08)

Ordered runbook for putting the Windows app in people's hands. Design notes are in `desktop.md`; the older release notes
are in `desktop_release.md`. This file is the current one and matches what the live site does today.

## Where it stands (checked 2026-11-08)

- `https://www.kell.org.in/desktop` is live and shows the one-line install.
- `https://www.kell.org.in/desktop.ps1` serves as `text/plain`.
- `https://www.kell.org.in/download` returns **302** to
  `https://github.com/AnujB05/kell-desktop/releases/download/v0.1.8/kell-Setup-0.1.8.exe`
  (236,640,307 bytes; the asset itself downloads with 200).
- Release `v0.1.8` (published 2026-11-08) carries the `.exe`, `.blockmap` and `latest.yml`.
- **The one-liner works today:**

  ```powershell
  irm https://www.kell.org.in/desktop.ps1 | iex
  ```

Still to do before a public launch:

1. **The installer is unsigned.** Windows shows "Windows protected your PC".
2. **Supabase's redirect allow list** still needs `http://127.0.0.1:**` for the Google/GitHub buttons.
3. **No live end-to-end pass** has been run from a clean Windows machine.
4. **The next release must bump the version.** `desktop/package.json` is `0.1.8`, matching the published release; the
   next build must be `0.1.9` or higher or the updater sees nothing new.

## 1. Keep the releases repo names in sync

The installers live as releases of a **public repo that holds only releases, no code**: `AnujB05/kell-desktop`
(releases `v0.1.2` … `v0.1.8`). It is named in three places and they must agree:

- `desktop/package.json` → `build.publish.owner` / `build.publish.repo` (the updater reads this at build time)
- `routing_kell_frontend/app/download/route.ts` → the default (override with `KELL_DESKTOP_RELEASES_REPO`)
- the Vercel env `KELL_DESKTOP_RELEASES_REPO`

If you move it to the org (`3Founders/kell-desktop`), change all three. Do not put the app source there; a README is
enough.

## 2. Build the installer

```powershell
cd desktop
# bump "version" in package.json above the newest published release (0.1.8 -> 0.1.9)
npm run dist
```

Outputs in `desktop/release/`:

- `kell-Setup-<version>.exe`
- `kell-Setup-<version>.exe.blockmap`
- `latest.yml`

Rules:

- The file name is fixed (`kell-Setup-${version}.exe`, no spaces) because `/download` and `latest.yml` look for it.
- Check the exe's timestamp is **after** the latest `desktop/` commit. The copy in `desktop/release/` today is `0.1.0`
  from 2026-11-06 and is stale; do not publish it.
- If the build fails with `EPROTO ... wrong version number` while re-downloading Electron, use the cached zip:
  `npx electron-builder --win nsis --publish never --config.electronDist=%LOCALAPPDATA%\electron\Cache\<hash dir with the zip> --config.electronVersion=<version>`

## 3. Publish the GitHub release

Attach **all three** files. The updater needs `latest.yml`; `/download` needs the `.exe`; the `.blockmap` makes updates
small. Run this from `desktop/` (where `npm run dist` left `release/`):

```powershell
gh release create v0.1.9 `
  release/kell-Setup-0.1.9.exe `
  release/kell-Setup-0.1.9.exe.blockmap `
  release/latest.yml `
  --title "kell 0.1.9" --notes "What changed."
```

- Publish it (not a draft, not a prerelease).
- Tag must be `v<version>`.
- Never reuse or move a published version; the updater compares versions, not dates.

## 4. Point the website at that repo

In the Vercel project for the site (root directory `routing_kell_frontend`, production branch `main`):

- `KELL_DESKTOP_RELEASES_REPO` = the releases repo (currently `AnujB05/kell-desktop`).
- `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` must be present; without them
  `/api/desktop-config` answers 503 and Rooms sign-in fails.

Redeploy after changing either. The site's `/download` route caches the GitHub lookup for five minutes, so allow that
before concluding a release is missing.

Heads-up: the `vercel` CLI on this machine is linked to the `stealth13` team, which reports that the team is suspended
(add a payment method). The live site still deploys from `main`; if you deploy from the CLI, use the project/team that
actually serves `www.kell.org.in`, or fix that billing first.

## 5. Supabase sign-in

Supabase dashboard → **Authentication → URL Configuration**:

- **Redirect URLs:** add `http://127.0.0.1:**` (any port, any path). Without it, Google and GitHub fail at Supabase
  before the app ever sees the code.
- **Site URL:** `https://www.kell.org.in`.

Email and password sign-in loads the site's `/sign-in?redirect=/chat` inside the Rooms view and does not use the
loopback redirect.

## 6. Code signing (do this before a public launch)

The installer is unsigned today. Requirements:

- **Certificate:** Windows Authenticode code-signing certificate, OV or EV. Since June 2023 the private key must live
  on FIPS 140-2 Level 2 hardware or a cloud HSM. OV ~$200–400/yr; EV ~$400–700/yr; Azure Trusted Signing ~$10/mo
  (needs 3+ years of verifiable business history). The subject must be the legal entity, not a person.
- **Validation documents:** incorporation/registration certificate, GST/VAT or address proof, phone callback, domain
  control; a D-U-N-S number is often required for EV/Azure. Expect 3–10 business days.
- **Signing method:** a hardware token (YubiKey FIPS, SafeNet eToken) or a cloud HSM (DigiCert KeyLocker, SSL.com
  eSigner, Azure Trusted Signing).
- **electron-builder:** `win.certificateFile` + `CSC_KEY_PASSWORD`, or `win.certificateSubjectName` for a token; a
  custom `sign` hook / `signtoolOptions` for cloud signing. Timestamp with an RFC 3161 server
  (e.g. `http://timestamp.digicert.com`).
- **Updater:** set `win.publisherName` to the certificate subject and enable `verifyUpdateCodeSignature` so
  `electron-updater` checks the publisher.
- **Secrets:** keep the certificate/password or cloud credentials in CI secrets, never in the repo.
- **Verify:** `signtool verify /pa /v` and `Get-AuthenticodeSignature`, then test on a clean Windows VM.

Until then, publish a SHA-256 of the installer next to the release and expect the SmartScreen warning.

## 7. Backend and access

- The personal lane uses `/v1/models`, `/v1/chat/completions` and `/v1/billing/wallet` on the deployed backend.
  `KELL_WALLETS=on` must be set.
- A new person needs credits or a plan. A zero wallet shows "You need a plan to run models". Decide whether to seed a
  trial (`backend/app/services/free_credits.py`) or send people to `/pricing`.
- The coding lane (`kell login`, MCP token) and the Rooms session (Supabase website session) are deliberately two
  tokens. Do not "fix" that by sharing one.

## 8. Verify the live install

From a machine that is not the build machine:

```powershell
curl.exe -s -D - -o NUL "https://www.kell.org.in/download" | findstr /i "HTTP/ Location:"
# expect: 302 to https://github.com/<repo>/releases/download/v<version>/kell-Setup-<version>.exe

curl.exe -s -L -o NUL -w "%{http_code} %{size_download}`n" `
  "https://github.com/<repo>/releases/download/v<version>/kell-Setup-<version>.exe"
# expect: 200 and a size above 10 MB (the script refuses anything smaller)
```

Then the real thing on a clean Windows 10/11 user profile (no Node, no `kell` CLI, no `~/.stealthlab`):

```powershell
irm https://www.kell.org.in/desktop.ps1 | iex
```

It should download, install per user, and open kell. Confirm:

- `%LOCALAPPDATA%\Programs\kell-desktop\kell.exe` exists and the Start-menu entry opens it.
- Sign in with Google, then GitHub, then email/password.
- Open a project and run one model call (needs credits).
- Close and reopen; the sign-in is still there and refreshes.
- Uninstall from Settings → Apps; it removes the app. User data under `~/.stealthlab` is separate — decide whether the
  uninstaller should offer to remove it.

## 9. Auto-update

The installed app checks the `build.publish` repo once at start, downloads a newer installer quietly and installs it on
quit. To prove it:

1. Install the previous version.
2. Bump `desktop/package.json`, build and publish the next version (all three assets).
3. Open the installed app; watch its log for `update <version> downloaded`.
4. Quit and reopen; the version should be the new one.

If the feed 404s, the `build.publish` repo and `KELL_DESKTOP_RELEASES_REPO` are not the same repo. Fix that first.

## 10. Rollback

- **Bad app version:** publish a **higher** version built from the last good code. The updater only moves forward.
- **Stop new downloads:** delete/unpublish the release. `/download` then falls back to the releases page, and the
  one-liner stops with "The download is not the installer."
- **Bad website deploy:** promote the previous deployment in Vercel.

## Checklist

- [ ] One releases repo chosen; `build.publish` and `KELL_DESKTOP_RELEASES_REPO` match it
- [ ] `desktop/package.json` version bumped above the newest published release
- [ ] `npm run dist` built after the latest `desktop/` commit
- [ ] Installer signed (or a SHA-256 published and the warning accepted)
- [ ] GitHub release has `.exe`, `.blockmap`, `latest.yml`
- [ ] `/download` 302s to the new asset and the asset is above 10 MB
- [ ] Supabase redirect URLs include `http://127.0.0.1:**`
- [ ] `/desktop`, `/desktop.ps1` and `/api/desktop-config` are 200
- [ ] Clean-Windows one-liner install, three sign-ins, one model call
- [ ] Auto-update from the previous version
- [ ] Uninstall removes the app; decide what to do with `~/.stealthlab`
