// kell.org.in/download: sends the browser straight to the newest Windows installer.
//
// The installers live as releases of a public repo that holds only releases, no code (DESKTOP_RELEASES_REPO; decided
// 2026-11-04, docs/desktop_downloads_task.md). A new version needs no change here: the newest release is looked up (cached
// five minutes) and its .exe is where the browser goes. If GitHub cannot be asked, the releases page is the fallback, so
// the link never dead-ends.
//
// github.com's download pages can fail while GitHub's API and file servers still work (2026-11-08: every
// /releases/download link answered 504, on other projects too). So the usual link is checked first, and when it fails the
// file is asked for through the API, which answers with a short-lived link straight to GitHub's file server. That link
// expires, so that answer is never cached. The API is only asked on a failure: unsigned API calls are limited per IP.
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const DESKTOP_RELEASES_REPO = process.env.KELL_DESKTOP_RELEASES_REPO || "AnujB05/kell-desktop";
const PAGE = `https://github.com/${DESKTOP_RELEASES_REPO}/releases/latest`;
const API = `https://api.github.com/repos/${DESKTOP_RELEASES_REPO}`;
const FILE_HOSTS = /^https:\/\/(release-assets|objects)\.githubusercontent\.com\//;

type Asset = { id?: number; name?: string; browser_download_url?: string };

/** The usual download link answers with GitHub's redirect to its file server (anything under 400 counts). */
async function answers(url: string): Promise<boolean> {
  try {
    const r = await fetch(url, { method: "HEAD", redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(5000) });
    return r.status < 400;
  } catch { return false; }
}

/** The file server link for an asset, through the API; "" when the API cannot give one. */
async function fileServerLink(id: number): Promise<string> {
  try {
    const r = await fetch(`${API}/releases/assets/${id}`, {
      headers: { accept: "application/octet-stream", "user-agent": "kell-site" },
      redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(6000),
    });
    const loc = r.headers.get("location") ?? "";
    return FILE_HOSTS.test(loc) ? loc : "";
  } catch { return ""; }
}

export async function GET() {
  try {
    const r = await fetch(`${API}/releases/latest`, {
      headers: { accept: "application/vnd.github+json", "user-agent": "kell-site" },
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(6000),
    });
    if (r.ok) {
      const j = await r.json();
      const asset: Asset | undefined = (j?.assets ?? []).find((a: Asset) => /^kell-Setup-.*\.exe$/i.test(a?.name ?? ""));
      const url = asset?.browser_download_url;
      if (typeof url === "string" && url.startsWith(`https://github.com/${DESKTOP_RELEASES_REPO}/`)) {
        if (await answers(url) || typeof asset?.id !== "number") {
          return NextResponse.redirect(url, { status: 302, headers: { "cache-control": "public, max-age=300" } });
        }
        const direct = await fileServerLink(asset.id);
        if (direct) return NextResponse.redirect(direct, { status: 302, headers: { "cache-control": "no-store" } });
        return NextResponse.redirect(url, 302);
      }
    }
  } catch { /* fall through to the releases page */ }
  return NextResponse.redirect(PAGE, 302);
}
