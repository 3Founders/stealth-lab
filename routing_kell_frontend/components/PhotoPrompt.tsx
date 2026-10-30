"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { readDevicePhoto, setDevicePhoto, syncDevicePhoto } from "@/lib/device-profile";
import { AVATAR_TYPES, avatarProblem } from "@/lib/profile-form";
import { getSession, onSessionChange } from "@/lib/session";

// Pages where a question would be in the way: signing in, approving access, the older onboarding flow.
const QUIET = ["/sign-in", "/oauth", "/onboarding"];
const askedKey = (subject: string) => `kel.profile.photo.asked.${subject}`;
const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * After sign-in, once per account: "Add a profile photo?". It is skippable, and "Not now" is remembered, so nobody is asked twice.
 * It waits for the photo on the account to be looked up first (lib/device-profile.ts), so someone who already has one from another
 * device is never asked. The photo can always be added or changed later under Account, Profile.
 */
export default function PhotoPrompt() {
  const path = usePathname();
  const [subject, setSubject] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    const apply = (s: { subject: string } | null) => { if (live) setSubject(s ? s.subject : null); };
    getSession().then(apply);
    const off = onSessionChange(apply);
    return () => { live = false; off(); };
  }, []);

  useEffect(() => {
    if (!subject || QUIET.some((p) => path.startsWith(p))) { setOpen(false); return; }
    let live = true;
    (async () => {
      try { if (window.localStorage.getItem(askedKey(subject))) return; } catch { return; }
      await syncDevicePhoto(subject);
      if (!live) return;
      // Asked once, ever: the question is marked as asked the moment it is shown, whatever happens next (a photo added, "Not now",
      // the dialog dismissed, the page left). A person who already has a photo is marked too, so a later hiccup cannot bring it back.
      try { window.localStorage.setItem(askedKey(subject), "1"); } catch { /* storage blocked: it may ask again, harmless */ }
      if (!readDevicePhoto(subject)) setOpen(true);
    })();
    return () => { live = false; };
  }, [subject, path]);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  // Closing takes a moment so it can animate out (the iOS sheet motion in globals.css), then the dialog is really closed.
  function done() {
    const d = dialog.current;
    if (!d || !d.open) { setOpen(false); return; }
    d.setAttribute("data-closing", "");
    window.setTimeout(() => { d.removeAttribute("data-closing"); setOpen(false); }, reducedMotion() ? 0 : 260);
  }

  async function pick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (file.current) file.current.value = "";
    if (!f || !subject) return;
    const problem = avatarProblem(f);
    if (problem) { setError(problem); return; }
    setBusy(true); setError("");
    const msg = await setDevicePhoto(subject, f);
    setBusy(false);
    // A photo that is saved here but not yet on the server is still a photo: only a failure to use the picture keeps the question open.
    if (msg && !msg.startsWith("Saved on this device")) { setError(msg); return; }
    done();
  }

  return (
    <dialog ref={dialog} className="photo-ask" aria-labelledby="photo-ask-title" onClose={() => setOpen(false)} onCancel={(e) => { e.preventDefault(); done(); }}>
      <h2 className="h3" id="photo-ask-title">Add a profile photo?</h2>
      <p className="small">People in your rooms see it next to your messages. It&rsquo;s optional, and you can change it later under Account, Profile.</p>
      <input ref={file} type="file" accept={AVATAR_TYPES.join(",")} onChange={pick} className="visually-hidden" aria-label="Choose a profile photo" />
      <div className="cluster">
        <button type="button" className="btn-ink" onClick={() => file.current?.click()} disabled={busy}><span>{busy ? "Working…" : "Choose a photo"}</span></button>
        <button type="button" className="btn-line" onClick={done} disabled={busy}>Not now</button>
      </div>
      {error && <p className="small" role="alert">{error}</p>}
    </dialog>
  );
}
