/** Pure helpers for the profile page: what a picked photo must satisfy, and which fields actually changed. */
import type { ProfileUpdateInput } from "@/lib/kel-api";

export const AVATAR_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export const AVATAR_MAX_BYTES = 5 * 1024 * 1024; // the server's own limit (backend/app/services/avatar.py); checked here first so the person is told before an upload
export const TAGLINE_MAX = 280; // matches the server's field length

/** null when the file is fine, else a sentence for the person. */
export function avatarProblem(file: { type: string; size: number }): string | null {
  if (!(AVATAR_TYPES as readonly string[]).includes(file.type)) return "Use a PNG, JPEG or WebP picture.";
  if (file.size > AVATAR_MAX_BYTES) return "That picture is over 5 MB. Choose a smaller one.";
  if (file.size === 0) return "That file is empty.";
  return null;
}

export interface ProfileForm { username: string; tagline: string; visibility: "private" | "public" }

/** Only the fields that differ from what is saved, so an untouched field is never rewritten. Null when nothing changed. */
export function profileChanges(saved: { username: string | null; tagline: string | null; visibility: "private" | "public" }, form: ProfileForm): ProfileUpdateInput | null {
  const out: ProfileUpdateInput = {};
  const username = form.username.trim();
  if (username && username !== (saved.username ?? "")) out.username = username;
  const tagline = form.tagline.trim().slice(0, TAGLINE_MAX);
  if (tagline !== (saved.tagline ?? "").trim()) out.tagline = tagline;
  if (form.visibility !== saved.visibility) out.visibility = form.visibility;
  return Object.keys(out).length ? out : null;
}
