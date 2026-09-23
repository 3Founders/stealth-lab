"use client";
import { useEffect, useRef, useState } from "react";
import Avatar from "@/components/Avatar";
import StateNotice from "@/components/NotConnected";
import { getMyProfile, removeAvatar, updateMyProfile, uploadAvatar, type MyProfileResult } from "@/lib/kel-api";
import type { ApiState } from "@/lib/api";

export default function AccountSettingsPage() {
  const [profile, setProfile] = useState<ApiState<MyProfileResult>>({ kind: "loading" });
  const [username, setUsername] = useState("");
  const [tagline, setTagline] = useState("");
  const [visibility, setVisibility] = useState<"private" | "public">("private");
  const [usernameStatus, setUsernameStatus] = useState<"idle" | "saving" | "error" | "saved">("idle");
  const [usernameError, setUsernameError] = useState<string | null>(null);
  const [taglineStatus, setTaglineStatus] = useState<"idle" | "saving" | "saved">("idle");
  const [avatarStatus, setAvatarStatus] = useState<"idle" | "busy">("idle");
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function load() {
    getMyProfile().then((r) => {
      setProfile(r);
      if (r.kind === "ok") {
        setUsername(r.data.profile.username || "");
        setTagline(r.data.profile.tagline || "");
        setVisibility(r.data.profile.visibility);
      }
    });
  }

  useEffect(load, []);

  async function saveUsername() {
    setUsernameStatus("saving");
    setUsernameError(null);
    const r = await updateMyProfile({ username: username.trim() });
    if (r.kind === "ok") {
      setUsernameStatus("saved");
      load();
    } else {
      setUsernameStatus("error");
      setUsernameError(r.kind === "error" ? r.message : "Could not save that username.");
    }
  }

  async function saveTaglineAndVisibility(nextVisibility: "private" | "public") {
    setTaglineStatus("saving");
    await updateMyProfile({ visibility: nextVisibility, tagline: tagline.trim() || undefined });
    setVisibility(nextVisibility);
    setTaglineStatus("saved");
    load();
  }

  async function onPickAvatar(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setAvatarStatus("busy");
    setAvatarError(null);
    const r = await uploadAvatar(file);
    setAvatarStatus("idle");
    if (r.kind === "ok") load();
    else setAvatarError(r.kind === "error" ? r.message : "Could not upload that picture.");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function onRemoveAvatar() {
    setAvatarStatus("busy");
    await removeAvatar();
    setAvatarStatus("idle");
    load();
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
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>SETTINGS</b></div>
        <h1 className="display">Your profile.</h1>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 40 }}>
        {/* contributor name */}
        <div className="signin" style={{ gridColumn: "1 / span 6" }}>
          <h2 className="h3" style={{ margin: 0 }}>Contributor name</h2>
          <p className="small dim">This is the name people see on your contributions.</p>
          <input
            type="text" value={username} onChange={(e) => { setUsername(e.target.value); setUsernameStatus("idle"); }}
            maxLength={32} style={{ font: "inherit", fontSize: 16, border: "1px solid var(--rule-strong)", borderRadius: 3, padding: "10px 12px" }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button className="btn-ink" type="button" onClick={saveUsername} disabled={usernameStatus === "saving" || !username.trim() || username.trim() === profile.data.profile.username}>
              <span>{usernameStatus === "saving" ? "Saving…" : "Save"}</span>
            </button>
            {usernameStatus === "saved" && <span className="small dim">Saved.</span>}
            {usernameStatus === "error" && <span className="small dim">{usernameError}</span>}
          </div>
        </div>

        {/* profile picture */}
        <div className="signin" style={{ gridColumn: "7 / span 6" }}>
          <h2 className="h3" style={{ margin: 0 }}>Profile picture</h2>
          <p className="small dim">Shown next to your public contributions.</p>
          <Avatar username={profile.data.profile.username} size={72} authenticated />
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <input ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/webp" onChange={onPickAvatar} style={{ display: "none" }} />
            <button type="button" className="oauth-btn" style={{ width: "auto" }} onClick={() => fileInputRef.current?.click()} disabled={avatarStatus === "busy"}>
              Upload new
            </button>
            {profile.data.profile.avatar_locator && (
              <button type="button" className="oauth-btn" style={{ width: "auto" }} onClick={onRemoveAvatar} disabled={avatarStatus === "busy"}>
                Remove
              </button>
            )}
          </div>
          {avatarError && <p className="small dim">{avatarError}</p>}
        </div>

        {/* tagline + visibility */}
        <div className="signin" style={{ gridColumn: "1 / span 8" }}>
          <h2 className="h3" style={{ margin: 0 }}>Tagline</h2>
          <p className="small dim">Optional, up to 280 characters. Shown only if your profile is public.</p>
          <textarea
            value={tagline} onChange={(e) => setTagline(e.target.value)} maxLength={280} rows={3}
            style={{ font: "inherit", fontSize: 15.5, border: "1px solid var(--rule-strong)", borderRadius: 3, padding: "10px 12px", resize: "vertical" }}
          />

          <h2 className="h3" style={{ margin: "12px 0 0" }}>Profile visibility</h2>
          {visibility === "public" ? (
            <p className="small dim">Your profile is public: your username, picture, tagline, and contribution counts are visible at <code>/u/{profile.data.profile.username}</code>. Credits and Standing are never public.</p>
          ) : (
            <p className="small dim">Your profile is private: nothing about you is visible to other people. Making it public will show your username, picture, tagline, and contribution counts — never Credits or Standing.</p>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {visibility === "private" ? (
              <button className="btn-ink" type="button" onClick={() => saveTaglineAndVisibility("public")} disabled={taglineStatus === "saving"}>
                <span>Save and make public</span>
              </button>
            ) : (
              <>
                <button className="btn-ink" type="button" onClick={() => saveTaglineAndVisibility("public")} disabled={taglineStatus === "saving"}>
                  <span>Save changes</span>
                </button>
                <button type="button" className="oauth-btn" style={{ width: "auto" }} onClick={() => saveTaglineAndVisibility("private")} disabled={taglineStatus === "saving"}>
                  Make private
                </button>
              </>
            )}
            {taglineStatus === "saved" && <span className="small dim">Saved.</span>}
          </div>
        </div>
      </section>
    </>
  );
}
