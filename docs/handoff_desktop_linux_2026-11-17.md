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

## 2. The one open item: confirm the size reduction

The Linux packages were larger than the Windows one because npm installs every Linux engine variant while only one is
used, and because node-pty ships prebuilts for all platforms.

The fix is `build.linux.files` in `desktop/package.json`. It is **Linux-only on purpose**: excluding node-pty's
prebuilt binaries for every platform would break the Windows terminal, which uses its `win32-x64` prebuild.

What is excluded, and why it is safe:

- `@opencode/cli-linux-x64-{baseline,musl,baseline-musl}` and `@opencode/cli-linux-arm64{,-musl}` — the engine is
  chosen by `lib/engine.ts` and only `cli-linux-x64/bin/opencode` is ever loaded.
- `node-pty/prebuilds/{darwin,win32}-*` — the Linux PTY is compiled into `build/Release` by `@electron/rebuild`, and
  `node-pty/lib/utils.js` looks in `build/Release` **before** `prebuilds/<platform>-<arch>`.

**Do not** add `!node_modules/node-pty/prebuilds/linux-x64/**`: Linux does not use a prebuild here, but the pattern
would also match the directory the loader probes if the rebuild ever moves to a prebuild.

To finish:

1. Push to `desktop-linux` (or run the workflow by hand) and let the run go green.
2. Read the AppImage and `.deb` sizes in the workflow log: `linux-smoke.sh` prints them as
   `AppImage built: … (NNN M)` and `.deb built: … (NNN M)`. Compare against the previous run.
3. Keep the terminal check's `PASS node-pty spawns a shell` — that is the evidence the exclusions did not cut the PTY.
4. If both are better and the run is green, the item is closed. Move it out of this file and out of `docs/revisit.md`.

**Caveat to re-check if the numbers look wrong:** electron-builder merges `build.files` with `build.linux.files`, and
the exclusion globs only work if they match whole paths. Check one build with
`unzip -l desktop/release/*.AppImage` (or `ar t` on the `.deb`) to see what actually landed, rather than trusting the
matches alone.

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
