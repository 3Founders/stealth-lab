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

/** The server's own username rule (backend/app/services/username_generator.py): 3 to 32 letters or digits, starting with a letter. null when fine. */
export function usernameProblem(raw: string): string | null {
  const name = raw.trim();
  if (!name) return "Choose a username.";
  if (name.length < 3) return "Use at least 3 characters.";
  if (name.length > 32) return "Use at most 32 characters.";
  if (!/^[A-Za-z]/.test(name)) return "Start with a letter.";
  if (!/^[A-Za-z0-9]+$/.test(name)) return "Use letters and numbers only, no spaces or symbols.";
  return null;
}

/** What other people type to call your agent: the username in lower case, as the room shows it ("@anuj/kell"). */
export const handleFor = (name: string): string => `@${name.trim().toLowerCase()}/kell`;

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
