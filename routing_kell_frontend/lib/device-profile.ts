/**
 * The profile when this site has no profile server (NEXT_PUBLIC_KEL_API_URL unset): honest and small, and not the full profile.
 *  - Your name and the line about you are saved on your keळ account itself (Supabase user metadata), so they follow you to any device.
 *  - Your photo is shrunk to a small square, kept in this browser so it shows at once, and also saved on keळ's server under your
 *    account so it follows you to every device (lib/photo-sync.ts). If the server can't be reached it stays on this device.
 * There is no public profile in this mode, so there is no visibility choice.
 */
import { getSupabase } from "@/lib/supabase";
import { browserFlag, defaultToken, photoSync } from "@/lib/photo-sync";

export const NAME_MAX = 40;
export const ABOUT_MAX = 280;
export const PHOTO_PX = 256;
export const PHOTO_EVENT = "kel-device-photo";
const photoKey = (subject: string) => `kel.profile.photo.${subject}`;

export interface DeviceProfile { subject: string; email: string | null; name: string; about: string }

export async function loadDeviceProfile(): Promise<DeviceProfile | null> {
  const c = getSupabase();
  if (!c) return null;
  try {
    const { data } = await c.auth.getUser();
    const u = data.user;
    if (!u) return null;
    const m = (u.user_metadata ?? {}) as Record<string, unknown>;
    const str = (v: unknown) => (typeof v === "string" ? v : "");
    return { subject: u.id, email: u.email ?? null, name: str(m.display_name) || str(m.full_name) || str(m.name), about: str(m.about) };
  } catch { return null; }
}

/** Saves the name and the line about you on the account. Returns an error sentence, or null when saved. */
export async function saveDeviceProfile(v: { name: string; about: string }): Promise<string | null> {
  const c = getSupabase();
  if (!c) return "Sign-in isn't configured here.";
  try {
    const { error } = await c.auth.updateUser({ data: { display_name: v.name.trim().slice(0, NAME_MAX), about: v.about.trim().slice(0, ABOUT_MAX) } });
    return error ? "We couldn't save your changes. Please sign in again and retry." : null;
  } catch { return "We couldn't reach the sign-in service. Please try again."; }
}

const sync = photoSync({
  token: defaultToken,
  store: { read: (s) => readDevicePhoto(s), write: (s, url) => writeDevicePhoto(s, url) },
  flag: browserFlag,
});
const pulled = new Set<string>();

function writeDevicePhoto(subject: string, url: string | null): void {
  try { if (url) window.localStorage.setItem(photoKey(subject), url); else window.localStorage.removeItem(photoKey(subject)); } catch { /* none */ }
}

/** Once per page load, brings this browser's copy in line with the server's and announces it if the photo changed. */
export async function syncDevicePhoto(subject: string): Promise<void> {
  if (pulled.has(subject)) return;
  pulled.add(subject);
  if (await sync.pull(subject)) announce(subject);
}

export function readDevicePhoto(subject: string): string | null {
  try { return window.localStorage.getItem(photoKey(subject)); } catch { return null; }
}

function announce(subject: string) { try { window.dispatchEvent(new CustomEvent(PHOTO_EVENT, { detail: subject })); } catch { /* none */ } }

export async function removeDevicePhoto(subject: string): Promise<void> {
  writeDevicePhoto(subject, null);
  announce(subject);
  await sync.drop(subject);
}

/** Crops the picture to a centred square, shrinks it, and keeps it in this browser. Returns an error sentence, or null when kept. */
export async function setDevicePhoto(subject: string, file: Blob): Promise<string | null> {
  try {
    const bmp = await createImageBitmap(file);
    const side = Math.min(bmp.width, bmp.height);
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = PHOTO_PX;
    const ctx = canvas.getContext("2d");
    if (!ctx) return "This browser can't prepare pictures.";
    ctx.drawImage(bmp, (bmp.width - side) / 2, (bmp.height - side) / 2, side, side, 0, 0, PHOTO_PX, PHOTO_PX);
    const url = canvas.toDataURL("image/jpeg", 0.85);
    writeDevicePhoto(subject, url);
    announce(subject);
    const sent = await sync.push(subject);
    return sent === "ok" ? null : "Saved on this device, but we couldn't reach the server to keep it on your other devices. Choose it again to retry.";
  } catch { return "We couldn't use that picture. Try a different one."; }
}
