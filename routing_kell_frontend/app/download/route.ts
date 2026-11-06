// kell.org.in/download: sends the browser straight to the newest Windows installer.
//
// The installers live as releases of a public repo that holds only releases, no code (DESKTOP_RELEASES_REPO; decided
// 2026-11-04, docs/desktop_downloads_task.md). A new version needs no change here: the newest release is looked up (cached
// five minutes) and its .exe is where the browser goes. If GitHub cannot be asked, the releases page is the fallback, so
// the link never dead-ends.
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export const DESKTOP_RELEASES_REPO = process.env.KELL_DESKTOP_RELEASES_REPO || "3Founders/kell-desktop";
const PAGE = `https://github.com/${DESKTOP_RELEASES_REPO}/releases/latest`;

export async function GET() {
  try {
    const r = await fetch(`https://api.github.com/repos/${DESKTOP_RELEASES_REPO}/releases/latest`, {
      headers: { accept: "application/vnd.github+json", "user-agent": "kell-site" },
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(6000),
    });
    if (r.ok) {
      const j = await r.json();
      const asset = (j?.assets ?? []).find((a: { name?: string }) => /^kell-Setup-.*\.exe$/i.test(a?.name ?? ""));
      const url = asset?.browser_download_url;
      if (typeof url === "string" && url.startsWith(`https://github.com/${DESKTOP_RELEASES_REPO}/`)) {
        return NextResponse.redirect(url, { status: 302, headers: { "cache-control": "public, max-age=300" } });
      }
    }
  } catch { /* fall through to the releases page */ }
  return NextResponse.redirect(PAGE, 302);
}
