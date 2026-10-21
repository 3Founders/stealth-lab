"use client";
import { useEffect, useState } from "react";
import { avatarUrlFor } from "@/lib/kel-api";
import { getAccessToken } from "@/lib/session";

/**
 * Always resolves to a real image: the backend's avatar-delivery endpoint
 * (GET /v1/contributors/by-username/{username}/avatar) serves the
 * contributor's own picture when set, or a deterministic initials
 * fallback otherwise — never a broken link, and never randomized per
 * render. This component has no fallback logic of its own on purpose: one
 * source of truth for "what does this person's avatar look like",
 * reused identically in the header, onboarding, profile, and settings.
 *
 * `authenticated`: pass true when `username` is the CALLER's own (header,
 * settings, onboarding) — auth is bearer-token-only (no cookies, see
 * app/main.py's CORS comment), and a plain `<img src>` can never carry an
 * Authorization header. The avatar endpoint deliberately 404s an
 * unauthenticated request for a private profile (even the fallback initials
 * SVG) to avoid leaking "this username exists but is private" vs "doesn't
 * exist" -- so without this, a brand-new user (private by default) can
 * never see even their own avatar. This fetches the image with the
 * caller's token and hands the `<img>` a blob: URL instead. Omit it (or
 * leave false) for anyone else's avatar (e.g. a public profile page) --
 * that request needs no auth and the plain <img src> stays as-is.
 */
export default function Avatar({
  username, size = 32, alt = "", authenticated = false,
}: { username: string | null | undefined; size?: number; alt?: string; authenticated?: boolean }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!authenticated || !username) {
      setBlobUrl(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    (async () => {
      const token = await getAccessToken();
      if (!token) return;
      try {
        const res = await fetch(avatarUrlFor(username), { headers: { Authorization: `Bearer ${token}` } });
        if (!res.ok || cancelled) return;
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setBlobUrl(objectUrl);
      } catch {
        /* leave blobUrl null -- falls through to the empty placeholder below */
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [authenticated, username]);

  if (!username || (authenticated && !blobUrl)) {
    return <span className="avatar" aria-hidden="true" style={{ width: size, height: size }} />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- cross-origin backend-served image, no next/image domain config assumed
    <img
      className="avatar"
      src={authenticated ? blobUrl! : avatarUrlFor(username)}
      alt={alt}
      width={size}
      height={size}
      style={{ width: size, height: size }}
    />
  );
}
