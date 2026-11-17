# Desktop Linux handoff — 2026-11-17

**Read this with `docs/desktop_release.md`.** That file is how a desktop release is made; this file is the state of the
Linux port at handoff.

The only prompt the next instance needs is:

> read docs/handoff_desktop_linux_2026-11-17.md and finish the Linux size check

`main` moves fast: **`git pull origin main` first**, then read this handoff before touching anything.

State described here: the merge of `desktop-linux` into `main` plus `perf(desktop): ship only the Linux engine and PTY
binaries in the Linux packages` (the last commit on `main` when this was written).

---

## 1. Where this is

The desktop app now builds for Linux on **Ubuntu 20.04** (glibc 2.31), so the AppImage and the `.deb` run on 20.04 and
newer. Everything is in place and the last full check passed except for the one item below.

| Piece | State |
|---|---|
| `.github/workflows/desktop-linux.yml` | **Done.** Runs in an `ubuntu:20.04` container on `ubuntu-22.04`; triggers on pushes to `desktop-linux` and by hand. |
| node-pty on 20.04 | **Done.** Rebuilt from source with 20.04's own `g++-10` (Electron 45's headers need C++20; stock g++ 9 has no `-std=gnu++20`). |
| AppImage + `.deb` + `latest-linux.yml` | **Done.** Built by `npm run dist:linux`, kept as the `kell-linux` artifact. |
| `desktop/scripts/linux-smoke.sh` | **Done.** The five checks that failed were bugs in this script, not the app; fixed in `da827b7d`. |
| Package size | **Staged and committed, not yet confirmed by a build.** See section 2. |

## 2. The size item: closed (2026-11-17)

Confirmed by a green run of the workflow on `main` (run `44128994703`, 13m, all smoke checks PASS):

| Artifact | Before the `build.linux.files` change | After |
|---|---|---|
| AppImage | 437M | **392M** |
| `.deb` | 549M | **286M** |

`PASS node-pty spawns a shell` still passes, and the AppImage, the `.deb`, the installed app, the menu entry and
`desktop.sh` all pass too. The exclusions did what they were meant to.

**Publishing is the one thing left, and it needs write access to the releases repo.** The build is downloaded at
`desktop/release-linux/` (`kell-0.1.28-x86_64.AppImage`, `kell-0.1.28-amd64.deb`, `latest-linux.yml`). The
authenticated `gh` account (`ChaitIITB`) has READ only on `AnujB05/kell-desktop`, so the upload answered 404. From an
account with write access:

```
gh release upload v0.1.28 \
  desktop/release-linux/kell-0.1.28-x86_64.AppImage \
  desktop/release-linux/kell-0.1.28-amd64.deb \
  desktop/release-linux/latest-linux.yml \
  -R AnujB05/kell-desktop
```

`/download?os=linux` and `?os=linux&format=deb` already look for exactly those names, so the site needs no change once
the assets are on the latest release.

## 3. Things that will trip up the next person

- **`build.linux` takes a fixed set of keys** (`target`, `files`, `category`, `executableName`, …, and `deb` is
  **not** one of them). Electron-builder's schema validator rejects anything else there. Target options such as the
  `.deb` dependencies go at the top level (`build.deb.depends`, `build.appImage.*`), which is where they already are;
  do not "tidy" them under `build.linux`. Check any edit with the bundled validator before pushing:
  `node -e "const v=require('./node_modules/app-builder-lib/out/util/config/schemaValidator.js'),s=require('./node_modules/app-builder-lib/scheme.json');v.validateSchema(s,require('./package.json').build,{name:'t'})"`
  from `desktop/`.
- **`asar` is `false`.** That is why the smoke script can `require` `$R/node_modules/node-pty` directly.
- **The workflow only triggers on `desktop-linux`.** If that branch is gone, push to whatever branch the workflow
  names, or run it with `workflow_dispatch`.
- **The engine, documents and app routes are checked; model calls are not.** They need a signed-in account.

## 4. Not done, and deliberately left

- **No Linux release has been published.** The workflow keeps the AppImage and `.deb` as a run artifact; publishing is
  still `electron-builder --publish` against `kell-desktop`, as in `docs/desktop_release.md`.
- **arm64 is not covered.** The exclusions drop the arm64 engines, so the packages are x64 only, which matches what is
  built.
