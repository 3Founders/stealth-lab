// electron-builder re-downloads SHASUMS256.txt for every build, even when the Electron zip is already in the
// @electron/get cache. On a network that breaks that TLS request the whole `npm run dist` fails with
// "write EPROTO ... wrong version number" before it ever looks at the cached zip.
//
// This seeds the checksum file electron-builder looks for (`SHASUMS256.txt-<version>` at the cache root) from the
// Electron package that is already installed, so the cached zip can be validated offline. It never downloads
// anything and does nothing when the zip's checksum is not in the package.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const pkg = require("../node_modules/electron/package.json");
const sums = require("../node_modules/electron/checksums.json");

const version = String(pkg.version || "");
const platform = process.platform === "win32" ? "win32" : process.platform;
const arch = process.arch === "arm64" ? "arm64" : "x64";
const file = `electron-v${version}-${platform}-${arch}.zip`;
const hash = sums[file];

if (!version || !hash) {
  console.log(`electron cache: no checksum for ${file || "this platform"}; leaving the download to electron-builder`);
  process.exit(0);
}

const root = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
const dir = path.join(root, "electron", "Cache");
fs.mkdirSync(dir, { recursive: true });
const target = path.join(dir, `SHASUMS256.txt-${version}`);
fs.writeFileSync(target, `${hash} *${file}\n`, "ascii");
console.log(`electron cache: seeded ${path.basename(target)} for ${file}`);
