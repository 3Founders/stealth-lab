"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  deleteMyData,
  exportMyData,
  previewMyDeletion,
  type DeletionPlan,
} from "@/lib/api/privacy";
import {
  getMyProfile,
  setMyProfile,
  type MyProfileResponse,
} from "@/lib/api/people";
import { getAuth, onAuthChange } from "@/lib/auth";

function PublicProfileSection() {
  const [me, setMe] = useState<MyProfileResponse | null>(null);
  const [tagline, setTagline] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getMyProfile()
      .then((r) => {
        setMe(r);
        setTagline(r.profile?.tagline ?? "");
      })
      .catch((e) => setErr(e instanceof Error ? e.message : "Couldn’t load your profile."));
  }, []);

  async function save(visibility: "private" | "public") {
    setBusy(true);
    setErr(null);
    try {
      const r = await setMyProfile(visibility, tagline);
      setMe((prev) => (prev ? { ...prev, profile: r.profile, disclosure_required: false } : prev));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setBusy(false);
    }
  }

  const isPublic = me?.profile?.visibility === "public";

  return (
    <section className="mt-8">
      <h2 className="text-sm font-medium text-neutral-900">Public profile</h2>
      <p className="mt-1 text-sm text-neutral-500">
        Off by default. If you turn it on, your name
        {me?.display_name ? ` (${me.display_name})` : ""} and your aggregate
        contribution counts — procedures authored, verified procedures, claims,
        Commons publications — become publicly visible on a profile page, in
        people search, and on the contributor leaderboard. No private procedures,
        traces, or execution detail are ever shown. You can switch it back to
        private at any time.
      </p>

      {me ? (
        <>
          <label className="mt-3 block text-xs text-neutral-500" htmlFor="tagline">
            Tagline (optional, shown only when public)
          </label>
          <input
            id="tagline"
            value={tagline}
            maxLength={280}
            onChange={(e) => setTagline(e.target.value)}
            placeholder="e.g. Postgres migrations, CI reliability"
            className="mt-1 h-9 w-full max-w-md rounded-lg border border-neutral-200 px-3 text-sm outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
          />
          <div className="mt-3 flex items-center gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => void save(isPublic ? "private" : "public")}
              className={
                "rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50 " +
                (isPublic
                  ? "border border-neutral-200 text-neutral-700 hover:bg-neutral-50"
                  : "bg-neutral-900 text-neutral-50 hover:bg-neutral-800")
              }
            >
              {busy
                ? "Saving…"
                : isPublic
                  ? "Make my profile private"
                  : "Make my profile public"}
            </button>
            {isPublic ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => void save("public")}
                className="rounded-lg border border-neutral-200 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
              >
                Save tagline
              </button>
            ) : null}
            <span className="text-xs text-neutral-400">
              {isPublic ? "Currently public" : "Currently private"}
            </span>
          </div>
        </>
      ) : err ? null : (
        <p className="mt-3 text-sm text-neutral-400">Loading…</p>
      )}
      {err ? (
        <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800">
          {err}
        </p>
      ) : null}
    </section>
  );
}

export default function PrivacyPage() {
  const [signedIn, setSignedIn] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<DeletionPlan | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    const s = () => setSignedIn(Boolean(getAuth()));
    s();
    return onAuthChange(s);
  }, []);

  async function onExport() {
    setBusy("export");
    setError(null);
    try {
      const data = await exportMyData();
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "stealthlab-export.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed.");
    } finally {
      setBusy(null);
    }
  }

  async function onPreviewDelete() {
    setBusy("preview");
    setError(null);
    try {
      setPlan(await previewMyDeletion());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the deletion preview.");
    } finally {
      setBusy(null);
    }
  }

  async function onConfirmDelete() {
    setBusy("delete");
    setError(null);
    try {
      const r = await deleteMyData();
      setDone(
        `Deleted ${r.physically_deleted ?? 0} private procedure(s); ` +
          `tombstoned ${r.tombstoned ?? 0}; ` +
          `${r.global_objects_preserved ?? 0} published Commons object(s) preserved.`
      );
      setPlan(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Deletion failed.");
    } finally {
      setBusy(null);
    }
  }

  if (!signedIn) {
    return (
      <div className="pt-24 text-center text-sm text-neutral-600">
        <Link href="/auth" className="underline">
          Sign in
        </Link>{" "}
        to manage your data.
      </div>
    );
  }

  return (
    <article className="max-w-2xl pt-16">
      <h1 className="text-2xl font-semibold tracking-tight">Privacy &amp; Data</h1>

      <section className="mt-8 space-y-2 text-sm text-neutral-600">
        <h2 className="font-medium text-neutral-900">What StealthLab stores</h2>
        <ul className="list-disc space-y-1 pl-5">
          <li>Your account (from Supabase Auth): user id, email.</li>
          <li>Procedures you add — private by default until you publish them.</li>
          <li>Your publication actions (references into the Global Commons).</li>
          <li>Executions and evidence you can see.</li>
        </ul>
      </section>

      <PublicProfileSection />

      <section className="mt-8">
        <h2 className="text-sm font-medium text-neutral-900">Export my data</h2>
        <p className="mt-1 text-sm text-neutral-500">
          A machine-readable JSON bundle. Global Commons knowledge is listed as
          publication references, not as owned data.
        </p>
        <button
          type="button"
          onClick={() => void onExport()}
          disabled={busy !== null}
          className="mt-3 rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-neutral-50 disabled:opacity-50"
        >
          {busy === "export" ? "Preparing…" : "Download my data"}
        </button>
      </section>

      <section className="mt-8">
        <h2 className="text-sm font-medium text-neutral-900">Delete my data</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Private procedures with no published copy are permanently deleted
          (including their search vectors). A procedure you published stays as
          immutable history but stops being retrievable. Independently-verified
          Commons procedures are preserved.
        </p>
        {!plan ? (
          <button
            type="button"
            onClick={() => void onPreviewDelete()}
            disabled={busy !== null}
            className="mt-3 rounded-lg border border-red-200 bg-white px-4 py-2 text-sm text-red-700 disabled:opacity-50"
          >
            {busy === "preview" ? "Loading…" : "Preview deletion"}
          </button>
        ) : (
          <div className="mt-3 space-y-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
            {plan.legal_hold ? (
              <p>Your account is under a legal hold; deletion is unavailable.</p>
            ) : (
              <>
                <p>
                  {plan.private_procedures_physical_delete.length} private
                  procedure(s) permanently deleted ·{" "}
                  {plan.private_procedures_tombstone.length} tombstoned ·{" "}
                  {plan.global_objects_preserved.length} Commons object(s)
                  preserved.
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => void onConfirmDelete()}
                    disabled={busy !== null}
                    className="rounded-lg bg-red-700 px-4 py-2 text-xs font-medium text-white disabled:opacity-50"
                  >
                    {busy === "delete" ? "Deleting…" : "Confirm deletion"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setPlan(null)}
                    className="rounded-lg border border-red-200 bg-white px-4 py-2 text-xs text-red-700"
                  >
                    Cancel
                  </button>
                </div>
              </>
            )}
          </div>
        )}
        {done ? (
          <p className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            {done}
          </p>
        ) : null}
      </section>

      <section className="mt-8 space-y-2 text-sm text-neutral-600">
        <h2 className="font-medium text-neutral-900">Connected repositories</h2>
        <p>
          Manage from{" "}
          <Link href="/repositories" className="underline">
            Repositories
          </Link>
          .
        </p>
        <h2 className="mt-4 font-medium text-neutral-900">AI / model-provider</h2>
        <p>
          Your private content is not sent to external model providers unless a
          provider policy for your organization explicitly allows that data
          class. Embeddings and model calls obey the same policy.
        </p>
        <h2 className="mt-4 font-medium text-neutral-900">Privacy contact</h2>
        <p>privacy@stealthlab.example (replace before launch).</p>
      </section>

      {error ? (
        <p className="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </p>
      ) : null}

      <Link href="/me" className="mt-10 inline-block text-sm text-neutral-500 underline">
        Back to your contributions
      </Link>
    </article>
  );
}
