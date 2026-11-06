# Releasing the desktop app (runbook)

Status 2026-11-05: built and tested up to the public step. **Nothing here has been published**; each step marked (Anuj) needs his go.

1. `cd desktop && npm run dist` -> `release/kell-Setup-<version>.exe`, `.blockmap` and `latest.yml`. The file name is fixed
   (`kell-Setup-${version}.exe`, no spaces) because `latest.yml` and the website's /download look for it. If the build fails with
   `EPROTO ... wrong version number` while re-downloading Electron, use the cached zip instead:
   `npx electron-builder --win nsis --publish never --config.electronDist=%LOCALAPPDATA%\electron\Cache\<hash dir with the zip> --config.electronVersion=<version>`.
2. (Anuj) Create the public repo `3Founders/kell-desktop` (README only; no code). The name is in `desktop/package.json`
   (`build.publish`) and `routing_kell_frontend/app/download/route.ts` (override with `KELL_DESKTOP_RELEASES_REPO`).
3. (Anuj) Release: tag `v<version>`, attach the `.exe`, `.blockmap` and `latest.yml` (all three; the updater needs `latest.yml`).
   Bump `version` in `desktop/package.json` for every release or the updater sees nothing new.
4. (Anuj) Website push: `/download` (redirects to the newest `kell-Setup-*.exe`), the Download link in "Use it", `public/desktop.ps1`.
   People then run `irm https://kell.org.in/desktop.ps1 | iex`. Do not touch `install.ps1` / `install.sh`.
5. (Anuj) Backend deploy: not needed for the personal lane; it uses `/v1/models`, `/v1/billing/wallet` and `/v1/chat/completions`
   as deployed (`KELL_WALLETS=on`).

How the app pays (desktop/lib/pooled.ts): team profiles from `kell join` first; else a developer's own key if
`kell setup --client opencode` made one; else the signed-in person's own plan through the gateway (`KELL_GATEWAY` overrides
the host for tests). A wallet at zero shows "You need a plan to run models" with a link to /pricing.
Unsigned installer: Windows shows "Windows protected your PC" until a code-signing certificate is bought.
