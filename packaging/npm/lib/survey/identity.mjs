// Repository identity (docs/plan_2026-10_priors_library_survey.md §4.6, §5.1): a stable answer to
// "which repository is this?", computed locally. Only the hash leaves the machine unless the user
// allows the public name.
//
//   1. the normalised remote (upstream preferred over origin, SSH == HTTPS, GitLab subgroups kept,
//      Azure DevOps `_git` forms unified, mirror prefixes mapped)        -> "r:" + sha256[:16]
//   2. the root commit (not trusted on a shallow clone)                 -> "c:" + sha256[:16]
//   3. an identity recorded earlier in meta.json (shallow clone, copy) -> reused, source=stored
//   4. the first manifest's name + path, or the folder name (no git)   -> "p:" + sha256[:16], weak
import crypto from "node:crypto";
import path from "node:path";
import { git } from "./files.mjs";

const sha16 = (s) => crypto.createHash("sha256").update(s).digest("hex").slice(0, 16);

const HOST_ALIASES = [
  [/^ssh\.github\.com$/, "github.com"],
  [/^altssh\.gitlab\.com$/, "gitlab.com"],
  [/^altssh\.bitbucket\.org$/, "bitbucket.org"],
  // ssh-config host aliases for multiple accounts: github.com-work, gitlab.com_personal
  [/^(github\.com|gitlab\.com|bitbucket\.org|codeberg\.org)[-_].+$/, "$1"],
];

/**
 * Normalise a git remote URL to `host/path` (lowercase, no scheme, credentials, port or .git).
 * Returns null for a local path or file:// remote (it names no shared repository).
 * @param {string} url
 * @param {Record<string,string>} [mirrors] prefix map applied after normalisation
 */
export function normaliseRemote(url, mirrors = {}) {
  let u = String(url || "").trim();
  if (!u) return null;
  u = u.replace(/^git\+/, "");
  let host;
  let p;
  const scheme = u.match(/^([a-z][a-z0-9+.-]*):\/\/(.*)$/i);
  if (scheme) {
    if (scheme[1].toLowerCase() === "file") return null;
    let rest = scheme[2];
    const slash = rest.indexOf("/");
    const authority = slash < 0 ? rest : rest.slice(0, slash);
    p = slash < 0 ? "" : rest.slice(slash + 1);
    const hostport = authority.slice(authority.lastIndexOf("@") + 1);
    host = hostport.replace(/:.*$/, "");
    // ssh://git@ssh.dev.azure.com:v3/org/proj/repo is not legal URL syntax but appears in the wild.
    if (/:v3$/.test(hostport)) p = "v3/" + p;
  } else {
    if (/^[a-zA-Z]:[\\/]/.test(u) || u.startsWith("/") || u.startsWith(".") || u.startsWith("\\\\")) return null;
    const scp = u.match(/^(?:[^@/]+@)?([^:/]+):(.+)$/);
    if (!scp) return null;
    host = scp[1];
    p = scp[2];
  }
  host = host.toLowerCase().replace(/^www\./, "");
  for (const [re, to] of HOST_ALIASES) if (re.test(host)) { host = host.replace(re, to); break; }
  p = p.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "").replace(/\/{2,}/g, "/").replace(/\.git$/i, "").replace(/\/+$/, "");
  // Azure DevOps: ssh.dev.azure.com:v3/org/proj/repo | dev.azure.com/org/proj/_git/repo |
  // org.visualstudio.com/[DefaultCollection/]proj/_git/repo | vs-ssh.visualstudio.com:v3/org/proj/repo
  if (host === "ssh.dev.azure.com" || host === "vs-ssh.visualstudio.com") {
    host = "dev.azure.com";
    p = p.replace(/^v3\//, "");
  } else if (host.endsWith(".visualstudio.com")) {
    const org = host.slice(0, -".visualstudio.com".length);
    host = "dev.azure.com";
    p = `${org}/${p.replace(/^defaultcollection\//i, "")}`;
  }
  if (host === "dev.azure.com") p = p.replace(/\/_git\//, "/").replace(/^_git\//, "");
  if (!p) return null;
  let out = `${host}/${p}`.toLowerCase();
  for (const [from, to] of Object.entries(mirrors || {})) {
    const f = String(from).toLowerCase().replace(/\/+$/, "") + "/";
    if (out.startsWith(f)) { out = String(to).toLowerCase().replace(/\/+$/, "") + "/" + out.slice(f.length); break; }
  }
  return out;
}

/** Remotes as {name: url}, from git config (`git remote -v` decorates partial clones: "(fetch) [blob:none]"). */
export function listRemotes(root) {
  const out = git(root, ["config", "--get-regexp", "^remote\\..*\\.url$"]);
  const remotes = {};
  if (!out) return remotes;
  for (const line of out.split(/\r?\n/)) {
    const m = line.match(/^remote\.(.+)\.url\s+(\S+)$/);
    if (m && !(m[1] in remotes)) remotes[m[1]] = m[2];
  }
  return remotes;
}

export function pickRemote(remotes, mirrors) {
  const names = Object.keys(remotes);
  const order = ["upstream", "origin", ...names.filter((n) => n !== "upstream" && n !== "origin").sort()];
  for (const n of order) {
    if (!(n in remotes)) continue;
    const norm = normaliseRemote(remotes[n], mirrors);
    if (norm) return { name: n, normalised: norm };
  }
  return null;
}

export function rootCommit(root) {
  const shallow = (git(root, ["rev-parse", "--is-shallow-repository"]) || "").trim() === "true";
  if (shallow) return { commit: null, shallow: true };
  const out = git(root, ["rev-list", "--max-parents=0", "HEAD"]);
  const roots = (out || "").split(/\s+/).filter(Boolean).sort();
  return { commit: roots[0] || null, shallow: false };
}

/**
 * @param {string} root scan root
 * @param {{gitRoot: string|null, units: {path:string,name:string,manifests:string[]}[], meta?: object,
 *          mirrors?: Record<string,string>, allowPublicName?: boolean}} ctx
 */
export function computeIdentity(root, ctx) {
  const prev = ctx.meta?.repo_identity || null;
  const prefix = ctx.gitRoot ? path.relative(ctx.gitRoot, root).split(path.sep).join("/") : "";
  let id = null;
  if (ctx.gitRoot) {
    const remotes = listRemotes(root);
    const r = pickRemote(remotes, ctx.mirrors);
    if (r) {
      // A survey of a sub-folder of a repository is its own identity inside that repository.
      const key = prefix ? `${r.normalised}//${prefix}` : r.normalised;
      id = { repo_id: "r:" + sha16(key), strength: "strong", source: "remote", remote_name: r.name,
             public_name: key, has_upstream: "upstream" in remotes };
    } else {
      const rc = rootCommit(root);
      if (rc.commit) {
        const key = prefix ? `${rc.commit}//${prefix}` : rc.commit;
        id = { repo_id: "c:" + sha16(key), strength: "strong", source: "root_commit", public_name: null };
      } else if (rc.shallow) {
        id = { shallow: true };
      }
    }
  }
  if (!id || id.shallow) {
    if (prev && prev.strength === "strong") {
      id = { ...prev, source: "stored" };
    } else {
      const rootUnit = ctx.units.find((u) => u.path === ".");
      const first = (rootUnit?.manifests.length ? rootUnit : ctx.units.find((u) => u.manifests.length)) || null;
      const key = first ? `${first.name}@${first.manifests[0]}` : `dir:${path.basename(path.resolve(root))}`;
      id = { repo_id: "p:" + sha16(key), strength: "weak", source: first ? "manifest" : "folder", public_name: null,
             reason: ctx.gitRoot ? "shallow clone with no remote and no stored identity" : "not a git repository" };
    }
  }
  const previous = new Set(prev?.previous_ids || []);
  if (prev?.repo_id && prev.repo_id !== id.repo_id) previous.add(prev.repo_id);
  return {
    repo_id: id.repo_id,
    strength: id.strength,
    source: id.source,
    ...(id.remote_name ? { remote_name: id.remote_name } : {}),
    ...(id.has_upstream ? { fork: true } : {}),
    ...(id.reason ? { reason: id.reason } : {}),
    public_name: id.public_name || null,
    share_public_name: Boolean(ctx.allowPublicName ?? prev?.share_public_name ?? false),
    units: ctx.units.map((u) => `${id.repo_id}:${u.path}`),
    ...(previous.size ? { previous_ids: [...previous].sort() } : {}),
  };
}
