"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import Avatar from "@/components/Avatar";
import DevicePhoto, { useDevicePhoto } from "@/components/DevicePhoto";
import { ABOUT_MAX, NAME_MAX, loadDeviceProfile, removeDevicePhoto, saveDeviceProfile, setDevicePhoto, type DeviceProfile } from "@/lib/device-profile";
import { getMyProfile, removeAvatar, updateMyProfile, uploadAvatar, type MyProfileResult } from "@/lib/kel-api";
import type { ApiState } from "@/lib/api";
import { AVATAR_MAX_BYTES, AVATAR_TYPES, TAGLINE_MAX, avatarProblem, profileChanges } from "@/lib/profile-form";
import { getSession, onSessionChange, type Session } from "@/lib/session";

/** Your profile: a photo, your username, a short line about you, and who can see it. Signed-in only. */
export default function ProfilePage() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSession(s); });
    const off = onSessionChange((s) => { if (live) setSession(s); });
    return () => { live = false; off(); };
  }, []);

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>PROFILE</b></div>
        <h1 className="display"><AnimatedHeading>Your profile</AnimatedHeading></h1>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="profile-wrap">
          {session === undefined && <p className="small dim" role="status">Checking your session…</p>}
          {session === null && (
            <div className="empty" role="status">
              <b>Sign in to see your profile.</b>
              <p><Link href="/sign-in?redirect=/account/profile" style={{ textDecoration: "underline" }}>Sign in</Link>.</p>
            </div>
          )}
          {session && <Editor />}
        </div>
      </section>
    </>
  );
}

type Status = "idle" | "saving" | "saved" | "error";

function Editor() {
  const [profile, setProfile] = useState<ApiState<MyProfileResult>>({ kind: "loading" });
  const [username, setUsername] = useState("");
  const [tagline, setTagline] = useState("");
  const [visibility, setVisibility] = useState<"private" | "public">("private");
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");
  const [photo, setPhoto] = useState<"idle" | "busy">("idle");
  const [photoError, setPhotoError] = useState("");
  const [photoKey, setPhotoKey] = useState(0); // bumps after an upload or removal so the picture is fetched again
  const file = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    setProfile({ kind: "loading" });
    getMyProfile().then((r) => {
      setProfile(r);
      if (r.kind === "ok") {
        setUsername(r.data.profile.username ?? "");
        setTagline(r.data.profile.tagline ?? "");
        setVisibility(r.data.profile.visibility);
      }
    });
  }, []);
  useEffect(load, [load]);

  if (profile.kind === "loading") return <p className="small dim" role="status">Loading your profile…</p>;
  if (profile.kind === "unconfigured") return <DeviceEditor />;
  if (profile.kind !== "ok") {
    const text = profile.kind === "unauthenticated" ? "Please sign in again."
      : profile.kind === "forbidden" ? profile.message
      : profile.kind === "error" ? profile.message : "Couldn't load your profile.";
    return (
      <div className="empty" role="alert">
        <b>Couldn&rsquo;t load your profile.</b>
        <p>{text}</p>
        <div className="cluster"><button type="button" className="btn-line" onClick={load}>Try again</button></div>
      </div>
    );
  }

  const saved = profile.data.profile;
  const changes = profileChanges({ username: saved.username, tagline: saved.tagline, visibility: saved.visibility }, { username, tagline, visibility });

  async function pick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (file.current) file.current.value = "";
    if (!f) return;
    const problem = avatarProblem(f);
    if (problem) { setPhotoError(problem); return; }
    setPhoto("busy"); setPhotoError("");
    const r = await uploadAvatar(f);
    setPhoto("idle");
    if (r.kind === "ok") { setProfile(r); setPhotoKey((k) => k + 1); }
    else setPhotoError(r.kind === "error" ? r.message : "We couldn't upload that picture. Please try again.");
  }

  async function remove() {
    setPhoto("busy"); setPhotoError("");
    const r = await removeAvatar();
    setPhoto("idle");
    if (r.kind === "ok") { setProfile(r); setPhotoKey((k) => k + 1); }
    else setPhotoError("We couldn't remove the picture. Please try again.");
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!changes) return;
    setStatus("saving"); setMessage("");
    const r = await updateMyProfile(changes);
    if (r.kind === "ok") {
      setProfile(r); setStatus("saved");
      setUsername(r.data.profile.username ?? ""); setTagline(r.data.profile.tagline ?? ""); setVisibility(r.data.profile.visibility);
    } else {
      setStatus("error");
      setMessage(r.kind === "error" ? r.message : "We couldn't save your changes. Please try again.");
    }
  }

  return (
    <>
      <div className="profile-card">
        <h2 className="h3">Photo</h2>
        <div className="profile-photo">
          <Avatar key={photoKey} username={saved.username} size={96} authenticated alt="Your profile photo" />
          <div className="stack">
            <input ref={file} id="p-photo" type="file" accept={AVATAR_TYPES.join(",")} onChange={pick} className="visually-hidden" aria-label="Choose a profile photo" />
            <div className="cluster">
              <button type="button" className="btn-ink" onClick={() => file.current?.click()} disabled={photo === "busy"}>
                <span>{photo === "busy" ? "Working…" : saved.avatar_locator ? "Change photo" : "Add a photo"}</span>
              </button>
              {saved.avatar_locator && <button type="button" className="btn-line" onClick={() => void remove()} disabled={photo === "busy"}>Remove</button>}
            </div>
            <p className="small dim">PNG, JPEG or WebP, up to {AVATAR_MAX_BYTES / (1024 * 1024)} MB. Without one, your initials are shown.</p>
            {photoError && <p className="small" role="alert">{photoError}</p>}
          </div>
        </div>
      </div>

      <form className="cform profile-form" onSubmit={save}>
        <h2 className="h3">About you</h2>
        <div className="cfield"><label htmlFor="p-username">Username</label>
          <input id="p-username" type="text" maxLength={32} autoComplete="username" value={username} onChange={(e) => { setUsername(e.target.value); setStatus("idle"); }} />
          <span className="hint">Your name on keळ. Letters, numbers and dashes.</span></div>
        <div className="cfield"><label htmlFor="p-tagline">About you <span className="dim">(optional)</span></label>
          <textarea id="p-tagline" rows={3} maxLength={TAGLINE_MAX} value={tagline} onChange={(e) => { setTagline(e.target.value); setStatus("idle"); }} />
          <span className="hint">{tagline.length}/{TAGLINE_MAX}. A line or two about what you work on.</span></div>
        <fieldset className="cfield profile-visibility">
          <legend>Who can see your profile</legend>
          <label><input type="radio" name="visibility" checked={visibility === "private"} onChange={() => { setVisibility("private"); setStatus("idle"); }} /> Only me</label>
          <label><input type="radio" name="visibility" checked={visibility === "public"} onChange={() => { setVisibility("public"); setStatus("idle"); }} /> Anyone: your username, photo and about line</label>
        </fieldset>
        <div className="cform-submit">
          <button className="btn-ink" type="submit" disabled={!changes || status === "saving"}>
            <span>{status === "saving" ? "Saving…" : "Save changes"}</span><span className="sq" aria-hidden="true">→</span>
          </button>
          {status === "saved" && <span className="small" role="status">Saved.</span>}
          {status === "error" && <span className="small" role="alert">{message}</span>}
        </div>
      </form>
    </>
  );
}

/** No profile server on this site: name and about line on the account, photo on the account through keळ's server, with a copy in this browser. See lib/device-profile.ts. */
function DeviceEditor() {
  const [p, setP] = useState<DeviceProfile | null | undefined>(undefined);
  const [name, setName] = useState("");
  const [about, setAbout] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");
  const [photoError, setPhotoError] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const photo = useDevicePhoto(p?.subject);

  useEffect(() => {
    let live = true;
    loadDeviceProfile().then((r) => { if (!live) return; setP(r); if (r) { setName(r.name); setAbout(r.about); } });
    return () => { live = false; };
  }, []);

  if (p === undefined) return <p className="small dim" role="status">Loading your profile…</p>;
  if (p === null) return <div className="empty" role="alert"><b>Couldn&rsquo;t load your profile.</b><p>Please sign in again.</p><div className="cluster"><Link className="btn-line" href="/sign-in?redirect=/account/profile">Sign in</Link></div></div>;

  const changed = name.trim() !== p.name.trim() || about.trim() !== p.about.trim();
  async function pick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (file.current) file.current.value = "";
    if (!f || !p) return;
    const problem = avatarProblem(f);
    if (problem) { setPhotoError(problem); return; }
    setPhotoError(await setDevicePhoto(p.subject, f) ?? "");
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!changed) return;
    setStatus("saving"); setMessage("");
    const err = await saveDeviceProfile({ name, about });
    if (err) { setStatus("error"); setMessage(err); return; }
    setP({ ...p!, name: name.trim(), about: about.trim() }); setStatus("saved");
  }

  return (
    <>
      <div className="profile-card">
        <h2 className="h3">Photo</h2>
        <div className="profile-photo">
          {photo ? <DevicePhoto url={photo} size={96} alt="Your profile photo" /> : <span className="avatar" aria-hidden="true" style={{ width: 96, height: 96, display: "grid", placeItems: "center", fontSize: 32 }}>{(name || p.email || "?").trim().charAt(0).toUpperCase()}</span>}
          <div className="stack">
            <input ref={file} id="p-photo" type="file" accept={AVATAR_TYPES.join(",")} onChange={pick} className="visually-hidden" aria-label="Choose a profile photo" />
            <div className="cluster">
              <button type="button" className="btn-ink" onClick={() => file.current?.click()}><span>{photo ? "Change photo" : "Add a photo"}</span></button>
              {photo && <button type="button" className="btn-line" onClick={() => void removeDevicePhoto(p.subject)}>Remove</button>}
            </div>
            <p className="small dim">PNG, JPEG or WebP. It is shrunk, then saved on your keळ account so it shows on every device you sign in on.</p>
            {photoError && <p className="small" role="alert">{photoError}</p>}
          </div>
        </div>
      </div>
      <form className="cform profile-form" onSubmit={save}>
        <h2 className="h3">About you</h2>
        {p.email && <p className="small dim">Signed in as {p.email}</p>}
        <div className="cfield"><label htmlFor="p-name">Name</label>
          <input id="p-name" type="text" maxLength={NAME_MAX} autoComplete="name" value={name} onChange={(e) => { setName(e.target.value); setStatus("idle"); }} />
          <span className="hint">Saved on your keळ account.</span></div>
        <div className="cfield"><label htmlFor="p-about">About you <span className="dim">(optional)</span></label>
          <textarea id="p-about" rows={3} maxLength={ABOUT_MAX} value={about} onChange={(e) => { setAbout(e.target.value); setStatus("idle"); }} />
          <span className="hint">{about.length}/{ABOUT_MAX}. A line or two about what you work on. Only you see it.</span></div>
        <div className="cform-submit">
          <button className="btn-ink" type="submit" disabled={!changed || status === "saving"}>
            <span>{status === "saving" ? "Saving…" : "Save changes"}</span><span className="sq" aria-hidden="true">→</span>
          </button>
          {status === "saved" && <span className="small" role="status">Saved.</span>}
          {status === "error" && <span className="small" role="alert">{message}</span>}
        </div>
      </form>
    </>
  );
}
