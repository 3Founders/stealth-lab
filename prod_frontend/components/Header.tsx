"use client";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { track } from "@/lib/analytics";
import { getMyProfile } from "@/lib/kel-api";
import { getSession } from "@/lib/session";
import AccountMenu from "@/components/AccountMenu";

const links = [
  { href: "/", label: "Home" },
  { href: "/problems", label: "Problems" },
  { href: "/search", label: "Search" },
  { href: "/docs", label: "Docs" },
  { href: "/#about", label: "About" },
];

const Arrow = () => (
  <span className="sq" aria-hidden="true">
    <svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M2 9 9 2M3.5 2H9v5.5" /></svg>
  </span>
);

export default function Header() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [aboutInView, setAboutInView] = useState(false);
  // Read only after mount (Supabase's session read is async) -- starts
  // signed-out, then reflects the real session once known. Never more
  // than a display convenience: every gated page still gates on the
  // real server response, not on this alone.
  const [username, setUsername] = useState<string | null | undefined>(undefined); // undefined = not resolved yet

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const session = await getSession();
      if (!session) {
        if (!cancelled) setUsername(null);
        return;
      }
      const profile = await getMyProfile();
      if (cancelled) return;
      setUsername(profile.kind === "ok" ? profile.data.profile.username : null);
    })();
    return () => { cancelled = true; };
  }, [pathname]);

  // Mirrors SectionRail's own scroll-tracking: the "About" nav link should read as
  // active while its section is on screen, the same way SectionRail's dots already do --
  // usePathname alone can't see this, since #about is a same-page anchor, not a route.
  useEffect(() => {
    const el = document.getElementById("about");
    if (!el) {
      setAboutInView(false);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => { if (e.isIntersecting) setAboutInView(e.isIntersecting); }),
      { rootMargin: "-40% 0px -55% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [pathname]);

  // On the homepage, About glides in place; elsewhere the default navigation to /#about runs and SmoothScroll resolves it.
  const onAbout = (e: React.MouseEvent) => {
    setOpen(false);
    if (pathname === "/" && window.__kelScrollTo) {
      e.preventDefault();
      window.history.pushState(null, "", "/#about");
      window.__kelScrollTo("#about");
    }
  };

  // Same idea as the logo: on the homepage, glide back to the top instead of a full
  // reload; elsewhere, the default Link navigation to "/" just lands there normally.
  const onHome = (e: React.MouseEvent) => {
    setOpen(false);
    if (pathname === "/" && window.__kelScrollTo) {
      e.preventDefault();
      window.history.pushState(null, "", "/");
      window.__kelScrollTo("#top");
    }
  };

  const item = (l: (typeof links)[number]) => (
    <Link
      key={l.href}
      href={l.href}
      aria-current={pathname === l.href || (l.href === "/#about" && aboutInView) ? "page" : undefined}
      onClick={l.label === "About" ? onAbout : l.label === "Home" ? onHome : () => setOpen(false)}
    >
      {l.label}
    </Link>
  );

  return (
    <div className="nav-wrap">
      <div className="frame">
        <header className="nav">
          <Link href="/" className="logo" aria-label="keळ, home" onClick={() => setOpen(false)}>
            <Image src="/kel-wordmark.png" alt="keळ" width={800} height={440} priority style={{ height: 54, width: "auto" }} />
          </Link>
          <nav aria-label="Primary" className="nav-links">{links.map(item)}</nav>
          {username ? (
            <AccountMenu username={username} />
          ) : (
            <Link href="/sign-in" className="btn-ink desk" onClick={() => track("sign_in_click", { where: "header" })}>Sign in <Arrow /></Link>
          )}
          <button className="btn-ink menu-btn" aria-expanded={open} aria-controls="nav-panel" onClick={() => setOpen(!open)}>
            {open ? "Close" : "Menu"}
          </button>
          {open && (
            <nav id="nav-panel" aria-label="Mobile" className="nav-panel">
              {links.map(item)}
              {username ? (
                <>
                  <Link href={`/u/${encodeURIComponent(username)}`} onClick={() => setOpen(false)}>View profile</Link>
                  <Link href="/account/credits" onClick={() => setOpen(false)}>Credits &amp; Standing</Link>
                  <Link href="/account/settings" onClick={() => setOpen(false)}>Settings</Link>
                </>
              ) : (
                <Link href="/sign-in" onClick={() => { setOpen(false); track("sign_in_click", { where: "menu" }); }}>Sign in</Link>
              )}
            </nav>
          )}
        </header>
      </div>
    </div>
  );
}
