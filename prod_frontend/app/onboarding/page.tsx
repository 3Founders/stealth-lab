"use client";
import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Avatar from "@/components/Avatar";
import StateNotice from "@/components/NotConnected";
import { getMyProfile, suggestUsernames, updateMyProfile, uploadAvatar, type MyProfileResult } from "@/lib/kel-api";
import { isSafeRedirectPath } from "@/lib/session";
import type { ApiState } from "@/lib/api";

const MAX_REGENERATE = 3;

function OnboardingInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [profile, setProfile] = useState<ApiState<MyProfileResult>>({ kind: "loading" });
  const [name, setName] = useState("");
  const [editing, setEditing] = useState(false);
  const [regenCount, setRegenCount] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [avatarPreview, setAvatarPreview] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getMyProfile().then((r) => {
      setProfile(r);
      if (r.kind === "ok") setName(r.data.profile.username || "");
    });
  }, []);

  async function generateAnother() {
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
        <h1 className="display">Welcome to keळ.</h1>
        <p className="lead">Choose the name people will see on your contributions.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="signin" style={{ gridColumn: "1 / span 6" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <Avatar username={avatarPreview ? null : name} size={48} authenticated />
            {avatarPreview && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={avatarPreview} alt="" width={48} height={48} style={{ width: 48, height: 48, borderRadius: "50%", objectFit: "cover" }} />
            )}
            {editing ? (
              <input
                type="text" value={name} onChange={(e) => setName(e.target.value)} maxLength={32}
                style={{ font: "inherit", fontSize: 22, border: "1px solid var(--rule-strong)", borderRadius: 3, padding: "6px 10px" }}
                autoFocus onBlur={() => setEditing(false)}
              />
            ) : (
              <h2 className="h3" style={{ margin: 0 }}>{name || "…"}</h2>
            )}
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button type="button" className="oauth-btn" style={{ width: "auto" }} onClick={() => setEditing(true)}>Edit name</button>
            <button type="button" className="oauth-btn" style={{ width: "auto" }} onClick={generateAnother} disabled={busy || regenCount >= MAX_REGENERATE}>
              Generate another{regenCount > 0 ? ` (${MAX_REGENERATE - regenCount} left)` : ""}
            </button>
          </div>

          <div>
            <p className="small dim" style={{ marginBottom: 8 }}>Profile picture — optional</p>
            <input ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/webp" onChange={onPickFile} style={{ display: "none" }} />
            <button type="button" className="oauth-btn" style={{ width: "auto" }} onClick={() => fileInputRef.current?.click()}>
              {avatarFile ? "Choose a different image" : "Upload image"}
            </button>
          </div>

          {error && <p className="small dim">{error}</p>}

          <button className="btn-ink" type="button" onClick={continueToKel} disabled={busy || !name.trim()} style={{ alignSelf: "flex-start" }}>
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
