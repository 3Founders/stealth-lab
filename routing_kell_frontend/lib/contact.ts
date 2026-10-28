/**
 * The Contact page's two optional destinations and the form's request. Both addresses come from this deployment's
 * configuration (nothing is guessed), and each is shown only when it is set:
 *   NEXT_PUBLIC_BOOKING_URL     a booking page (Calendly, Cal.com, ...), https only
 *   NEXT_PUBLIC_CONTACT_EMAIL   where to write when the form cannot be used
 */
import { API_URL } from "@/lib/api";

/** An https URL reduced to a clean string, or null. http is accepted only for localhost (development). */
export function parseBookingUrl(raw: string | undefined | null): string | null {
  const value = (raw ?? "").trim();
  if (!value) return null;
  let url: URL;
  try { url = new URL(value); } catch { return null; }
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (url.protocol !== "https:" && !(url.protocol === "http:" && local)) return null;
  if (url.username || url.password) return null;
  return url.toString();
}

export function parseContactEmail(raw: string | undefined | null): string | null {
  const value = (raw ?? "").trim();
  return /^[^\s@<>"',;]+@[^\s@<>"',;]+\.[^\s@<>"',;]+$/.test(value) ? value : null;
}

export const bookingUrl = () => parseBookingUrl(process.env.NEXT_PUBLIC_BOOKING_URL);
export const contactEmail = () => parseContactEmail(process.env.NEXT_PUBLIC_CONTACT_EMAIL);

export const TOPICS = [
  ["general", "Just asking"],
  ["pilot", "Running a pilot"],
  ["security", "Security or privacy"],
  ["other", "Something else"],
] as const;
export type Topic = (typeof TOPICS)[number][0];

export interface ContactInput {
  name: string;
  email: string;
  company: string;
  topic: Topic;
  message: string;
  website: string; // the honeypot: left empty by a person
  turnstileToken: string | null;
}

export type ContactResult = { ok: true } | { ok: false; message: string };

const GENERIC = "We couldn't send your message. Please try again, or write to us by email.";

/** POSTs the form to the backend. Every failure comes back as a sentence the visitor can act on; nothing throws. */
export async function submitContact(input: ContactInput, fetchImpl: typeof fetch = fetch, baseUrl: string = API_URL): Promise<ContactResult> {
  if (!baseUrl) return { ok: false, message: "The contact form isn't connected to a server on this site. Please write to us by email." };
  try {
    const res = await fetchImpl(`${baseUrl}/v1/contact`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({
        name: input.name, email: input.email, company: input.company || null, topic: input.topic, message: input.message,
        source_path: typeof window !== "undefined" ? window.location.pathname : null,
        turnstile_token: input.turnstileToken, website: input.website,
      }),
    });
    if (res.status === 202 || res.ok) return { ok: true };
    let detail: unknown;
    try { detail = (await res.json())?.detail; } catch { /* not JSON */ }
    return { ok: false, message: typeof detail === "string" && detail ? detail : GENERIC };
  } catch {
    return { ok: false, message: "We couldn't reach the server. Please try again, or write to us by email." };
  }
}
