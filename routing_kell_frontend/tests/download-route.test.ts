import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/download/route";

const release = (assets: { name: string; browser_download_url: string }[]) =>
  new Response(JSON.stringify({ assets }), { status: 200 });

afterEach(() => vi.unstubAllGlobals());

describe("/download", () => {
  it("sends the browser to the newest installer", async () => {
    const url = "https://github.com/AnujB05/kell-desktop/releases/download/v0.2.0/kell-Setup-0.2.0.exe";
    vi.stubGlobal("fetch", vi.fn(async () => release([
      { name: "latest.yml", browser_download_url: "https://github.com/AnujB05/kell-desktop/releases/download/v0.2.0/latest.yml" },
      { name: "kell-Setup-0.2.0.exe", browser_download_url: url },
    ])));
    const r = await GET();
    expect(r.status).toBe(302);
    expect(r.headers.get("location")).toBe(url);
  });

  it("sends the browser to GitHub's file server, uncached, when github.com's download link fails", async () => {
    const url = "https://github.com/AnujB05/kell-desktop/releases/download/v0.2.0/kell-Setup-0.2.0.exe";
    const direct = "https://release-assets.githubusercontent.com/github-production-release-asset/1/2?se=x&sig=y";
    const fetchMock = vi.fn(async (u: string) => {
      if (u === url) return new Response(null, { status: 504 });
      if (u.endsWith("/releases/assets/7")) return new Response(null, { status: 302, headers: { location: direct } });
      return new Response(JSON.stringify({ assets: [{ id: 7, name: "kell-Setup-0.2.0.exe", browser_download_url: url }] }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const r = await GET();
    expect(r.headers.get("location")).toBe(direct);
    expect(r.headers.get("cache-control")).toBe("no-store");
    // A file server link from anywhere else is not followed: the usual link is kept.
    fetchMock.mockImplementation(async (u: string) => {
      if (u === url) return new Response(null, { status: 504 });
      if (u.endsWith("/releases/assets/7")) return new Response(null, { status: 302, headers: { location: "https://evil.example/x.exe" } });
      return new Response(JSON.stringify({ assets: [{ id: 7, name: "kell-Setup-0.2.0.exe", browser_download_url: url }] }), { status: 200 });
    });
    expect((await GET()).headers.get("location")).toBe(url);
  });

  it("falls back to the releases page when GitHub cannot be asked or the file is elsewhere", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    expect((await GET()).headers.get("location")).toBe("https://github.com/AnujB05/kell-desktop/releases/latest");
    vi.stubGlobal("fetch", vi.fn(async () => release([{ name: "kell-Setup-1.exe", browser_download_url: "https://evil.example/x.exe" }])));
    expect((await GET()).headers.get("location")).toBe("https://github.com/AnujB05/kell-desktop/releases/latest");
  });

  it("gives Linux the AppImage, or the .deb when asked, and Windows stays the default", async () => {
    const base = "https://github.com/AnujB05/kell-desktop/releases/download/v0.2.0/"
    vi.stubGlobal("fetch", vi.fn(async () => release([
      { name: "kell-Setup-0.2.0.exe", browser_download_url: base + "kell-Setup-0.2.0.exe" },
      { name: "kell-0.2.0-x86_64.AppImage", browser_download_url: base + "kell-0.2.0-x86_64.AppImage" },
      { name: "kell-0.2.0-amd64.deb", browser_download_url: base + "kell-0.2.0-amd64.deb" },
      { name: "latest-linux.yml", browser_download_url: base + "latest-linux.yml" },
    ])));
    const at = (q: string) => GET(new Request(`https://www.kell.org.in/download${q}`)).then((r) => r.headers.get("location"));
    expect(await at("?os=linux")).toBe(base + "kell-0.2.0-x86_64.AppImage");
    expect(await at("?os=linux&format=deb")).toBe(base + "kell-0.2.0-amd64.deb");
    expect(await at("")).toBe(base + "kell-Setup-0.2.0.exe");
  });
});
