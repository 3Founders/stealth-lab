import { avatarUrlFor } from "@/lib/kel-api";

/**
 * Always resolves to a real image: the backend's avatar-delivery endpoint
 * (GET /v1/contributors/by-username/{username}/avatar) serves the
 * contributor's own picture when set, or a deterministic initials
 * fallback otherwise — never a broken link, and never randomized per
 * render. This component has no fallback logic of its own on purpose: one
 * source of truth for "what does this person's avatar look like",
 * reused identically in the header, onboarding, profile, and settings.
 */
export default function Avatar({ username, size = 32, alt = "" }: { username: string | null | undefined; size?: number; alt?: string }) {
  if (!username) {
    return <span className="avatar" aria-hidden="true" style={{ width: size, height: size }} />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- cross-origin backend-served image, no next/image domain config assumed
    <img className="avatar" src={avatarUrlFor(username)} alt={alt} width={size} height={size} style={{ width: size, height: size }} />
  );
}
