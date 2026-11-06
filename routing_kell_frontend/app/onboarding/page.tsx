"use client";
import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import AnimatedHeading from "@/components/AnimatedHeading";
import Avatar from "@/components/Avatar";
import StateNotice from "@/components/NotConnected";
import { getMyProfile, suggestUsernames, updateMyProfile, uploadAvatar, type MyProfileResult } from "@/lib/kel-api";
import { isSafeRedirectPath } from "@/lib/session";
import type { ApiState } from "@/lib/api";
import { handleFor, usernameProblem } from "@/lib/profile-form";

const MAX_REGENERATE = 3;

function OnboardingInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [profile, setProfile] = useState<ApiState<MyProfileResult>>({ kind: "loading" });
  const [name, setName] = useState("");
  const [suggested, setSuggested] = useState("");
  const [touched, setTouched] = useState(false);
  const [regenCount, setRegenCount] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [avatarPreview, setAvatarPreview] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getMyProfile().then((r) => {
      setProfile(r);
      // The field starts EMPTY: a generated name was being kept by people who just pressed Continue, and then friends had to call their agents by it.
      // The generated name is offered with "Suggest a name"; the person decides.
      if (r.kind === "ok") setSuggested(r.data.profile.username || "");
    });
  }, []);

  async function suggestOne() {
    if (suggested && regenCount === 0) {            // the first press offers the name that was generated for this account
      setName(suggested);
      setRegenCount(1);
      return;
    }
    if (regenCount >= MAX_REGENERATE) return;
    setBusy(true);
    setError(null);
    const r = await suggestUsernames(1);
    setBusy(false);
    if (r.kind === "ok" && r.data.suggestions[0]) {
      setName(r.data.suggestions[0]);
      setRegenCount((n) => n + 1);
    } else if (r.kind === "error") {
      setError(r.message);
    }
  }

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setAvatarFile(file);
    setAvatarPreview(URL.createObjectURL(file));
  }

  async function continueToKel() {
    if (profile.kind !== "ok") return;
    const problem = usernameProblem(name);
    if (problem) { setTouched(true); setError(problem); return; }
    setBusy(true);
    setError(null);

    const trimmed = name.trim();
    const currentUsername = profile.data.profile.username;
    if (trimmed && trimmed !== currentUsername) {
      const r = await updateMyProfile({ username: trimmed });
      if (r.kind !== "ok") {
        setBusy(false);
        setError(r.kind === "error" ? r.message : "That name isn't available.");
        return;
      }
    }

    if (avatarFile) {
      const r = await uploadAvatar(avatarFile);
      if (r.kind !== "ok") {
        setBusy(false);
        setError(r.kind === "error" ? r.message : "Could not upload that picture.");
        return;
      }
    }

    const done = await updateMyProfile({ onboarding_complete: true });
    setBusy(false);
    if (done.kind !== "ok") {
      setError("Something went wrong finishing setup. Please try again.");
      return;
    }
    const raw = searchParams.get("redirect");
    router.push(isSafeRedirectPath(raw) ? raw : "/");
  }

  if (profile.kind !== "ok") {
    return (
      <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}>
        <StateNotice state={profile} empty={undefined} />
      </section>
    );
  }

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>WELCOME</b></div>
        <h1 className="display"><AnimatedHeading>Welcome to keळ</AnimatedHeading></h1>
        <p className="lead">Choose your username. It is how people call your agents: {name.trim() && !usernameProblem(name) ? <b>{handleFor(name)}</b> : <b>@yourname/kell</b>}.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="signin" style={{ gridColumn: "1 / span 6" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <Avatar username={avatarPreview ? null : (name.trim() && !usernameProblem(name) ? name.trim() : null)} size={48} authenticated />
            {avatarPreview && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={avatarPreview} alt="" width={48} height={48} style={{ width: 48, height: 48, borderRadius: "50%", objectFit: "cover" }} />
            )}
            <div className="cfield" style={{ flex: 1, minWidth: 0 }}>
              <label htmlFor="onboard-username">Your username</label>
              <input
                id="onboard-username" type="text" value={name} maxLength={32} autoComplete="username" autoCapitalize="none" spellCheck={false}
                placeholder="for example, anuj" aria-describedby="onboard-username-help"
                onChange={(e) => { setName(e.target.value); setTouched(true); setError(null); }}
                style={{ fontSize: 16, minHeight: 44 }}
              />
            </div>
          </div>
          <p id="onboard-username-help" className="small dim" style={{ marginTop: -4 }}>
            Letters and numbers, 3 to 32, starting with a letter. You can change it later in your profile; people who call you by the old name will need the new one.
          </p>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button type="button" className="oauth-btn" style={{ width: "auto" }} onClick={suggestOne} disabled={busy || (regenCount >= MAX_REGENERATE)}>
              Suggest a name{regenCount > 1 ? ` (${MAX_REGENERATE - regenCount} left)` : ""}
            </button>
          </div>

          <div>
            <p className="small dim" style={{ marginBottom: 8 }}>Profile picture (optional)</p>
            <input ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/webp" onChange={onPickFile} style={{ display: "none" }} />
            <button type="button" className="oauth-btn" style={{ width: "auto" }} onClick={() => fileInputRef.current?.click()}>
              {avatarFile ? "Choose a different image" : "Upload image"}
            </button>
          </div>

          {(error || (touched && usernameProblem(name))) && <p className="small" role="alert">{error || usernameProblem(name)}</p>}

          <button className="btn-ink" type="button" onClick={continueToKel} disabled={busy || !!usernameProblem(name)} style={{ alignSelf: "flex-start" }}>
            <span>{busy ? "Please wait…" : "Continue to keळ"}</span><span className="sq" aria-hidden="true">→</span>
          </button>

          <p className="small dim">This is your public keळ identity. Your sign-in details stay separate.</p>
        </div>
      </section>
    </>
  );
}

export default function OnboardingPage() {
  return (
    <Suspense fallback={<section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }} />}>
      <OnboardingInner />
    </Suspense>
  );
}
