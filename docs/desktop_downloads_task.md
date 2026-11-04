# Desktop app: downloads and keys (task for Chaitanya, 2026-11-04)

**The goal in one line:** a stranger clicks **Download** on kell.org.in, installs kell, signs in, and it works, paid from their own plan.

Today two things stop that: the app has no way to use a single person's plan ("keys"), and there is nothing to download ("downloads").
Do them in the order below. Ask Anuj before anything goes public (a release, a website change, a deploy).

---

## Part 1: keys (a person's own plan pays in the app)

**Today** the app can run models in only two ways:

- **A team's keys.** After `kell join <invite>`, `~/.stealthlab/exec.json` holds the team's model profiles. `desktop/lib/pooled.ts` turns them
  into the engine's config: calls go to kell's gateway with the person's kell sign-in as the bearer, and the team's stored key pays.
- **The developer's own provider key** on the machine (dev setup only).

**Missing:** someone who bought Starter, Pro or Plus on the website and is in no team. They can install and sign in, but nothing pays for
their models.

**The server side already exists.** The gateway charges a person's own wallet when they are not in a team (`docs/billing.md`, "Who pays
by default"; `KELL_WALLETS=on`). The website chat already works this way.

**What to build:**

1. When the app is signed in and there is no joined team profile, build the same engine config as `pooled.ts`: kell's gateway, the
   person's sign-in token as the bearer, no provider key.
2. Get the model list for a personal account from the server, the way `kell join` gets the team's list from `POST /v1/join`
   (`packaging/npm/lib/join.mjs`). If no endpoint gives it for a person, add one (backend change: needs Anuj's go to deploy).
3. No plan or no balance: show a plain "You need a plan to run models" with a link to kell.org.in/pricing, not a raw error.
4. Never put a provider key on the machine, in the UI, in logs or in the installer.

**Done when:** a fresh account with a plan (or a test wallet credit) runs a coding prompt in the app, and the balance on `/account/billing`
goes down by about the cost the app shows.

---

## Part 2: build the installer and test it

1. `cd desktop && npm run dist` makes `desktop/release/kell Setup <version>.exe` (about 257 MB). This build includes the Rooms pane,
   the message queue and the tool sign-in button.
2. Install it on a **clean** Windows machine or VM (no Node, no kell CLI). Check:
   - installs without an admin prompt and opens;
   - Sign in works (Google or GitHub), and Rooms shows the same account;
   - a coding prompt runs (after Part 1);
   - a tool that needs a sign-in shows one Sign in button and runs after it.
3. Write down anything that breaks; fix it before Part 3.

---

## Part 3: downloads

**Where the file lives:** recommended is **GitHub Releases** on `3Founders/stealth-lab` (free, no size limit, fast). The website can't hold
the file itself: GitHub refuses files over 100 MB in the repo, and Vercel charges for big downloads. The other option is Cloudflare R2
(someone has to create the account). **Anuj decides.**

**What to build:**

1. **A release:** tag `desktop-v<version>`, the installer attached as the release file.
2. **`kell.org.in/download`:** a page (or redirect) that sends the browser straight to the newest installer file. A new version only
   changes this link.
3. **Download buttons** on the website pointing to `/download` (home page "Use it" section, and wherever Anuj wants).
4. **One-line install:** `public/desktop.ps1` in `routing_kell_frontend`. It downloads the installer, runs it silently (`/S`) and starts
   kell. People run:

   ```
   irm https://kell.org.in/desktop.ps1 | iex
   ```

   Do not touch `public/install.ps1` or `public/install.sh`: those are the CLI installers, and the dev server rewrites them.
5. The website goes live on every push to main, so check with Anuj before pushing the website part.

---

## Part 4: automatic updates

Add `electron-updater` with GitHub Releases as the source (`build.publish` in `desktop/package.json`). `npm run dist` then also makes
`latest.yml`; upload it to the same release. The app checks for a new version on start and installs it when it closes. Without this,
every fix means people download the app again.

---

## Later (owner decisions, not now)

- **Code signing.** Without it, Windows shows a blue "Windows protected your PC" warning on the downloaded file (the one-line install
  avoids most of it). A certificate costs about $100-400 a year.
- **macOS.** Needs a Mac to build on and an Apple developer account ($99 a year).

## Two questions for Anuj before Part 3

1. Who can download: invited teams first, or anyone (public)?
2. Host: GitHub Releases (recommended) or Cloudflare R2?

## Rules

- No keys anywhere in the app's UI, logs or installer.
- Nothing published (release, website, deploy) without Anuj's go.
- Never commit `.scratch/_walletdsn.txt`, `.env*` or other credential files.
