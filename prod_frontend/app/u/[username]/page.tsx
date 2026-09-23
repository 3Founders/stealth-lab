"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import AnimatedHeading from "@/components/AnimatedHeading";
import Avatar from "@/components/Avatar";
import StateNotice from "@/components/NotConnected";
import { getPublicProfileByUsername, type PublicContributorProfile } from "@/lib/kel-api";
import type { ApiState } from "@/lib/api";

export default function PublicProfilePage() {
  const { username } = useParams<{ username: string }>();
  const router = useRouter();
  const [profile, setProfile] = useState<ApiState<PublicContributorProfile>>({ kind: "loading" });

  useEffect(() => {
    const ac = new AbortController();
    getPublicProfileByUsername(username, ac.signal).then((r) => {
      // An old, renamed-away username still resolves to the right account --
      // settle the URL onto the current one rather than staying on a
      // retired name forever.
      if (r.kind === "ok" && r.data.renamed_to && r.data.renamed_to !== username) {
        router.replace(`/u/${encodeURIComponent(r.data.renamed_to)}`);
        return;
      }
      setProfile(r);
    });
    return () => ac.abort();
  }, [username, router]);

  if (profile.kind === "error" && profile.status === 404) {
    return (
      <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}>
        <div className="empty" style={{ gridColumn: "1 / span 12" }}>
          <b>No such profile.</b>
          <p>Either this username doesn&rsquo;t exist, or its owner hasn&rsquo;t made it public.</p>
        </div>
      </section>
    );
  }

  if (profile.kind !== "ok") {
    return (
      <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}>
        <StateNotice state={profile} empty={undefined} />
      </section>
    );
  }

  const p = profile.data;
  const since = p.profile_since ? new Date(p.profile_since).toLocaleDateString(undefined, { year: "numeric", month: "long" }) : null;

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>PROFILE</b></div>
        <div style={{ gridColumn: "1 / span 8", display: "flex", alignItems: "center", gap: 20, marginTop: 8 }}>
          <Avatar username={p.username} size={72} authenticated={p.is_owner} />
          <div>
            <h1 className="h1" style={{ marginBottom: 4 }}><AnimatedHeading>{p.username}</AnimatedHeading></h1>
            {p.tagline && <p className="lead" style={{ marginTop: 4 }}>{p.tagline}</p>}
            {since && <p className="small dim" style={{ marginTop: 6 }}>Member since {since}</p>}
          </div>
        </div>
      </section>

      {p.is_owner && p.visibility === "private" && (
        <section className="frame grid" style={{ paddingBottom: 32 }}>
          <div className="empty" style={{ gridColumn: "1 / span 12", padding: 16 }}>
            <b style={{ fontSize: 16, marginBottom: 2 }}>This is a preview. Only you can see it.</b>
            <p style={{ fontSize: 14 }}>
              Your profile is private, so nobody else can view this page yet. <Link href="/account/settings" style={{ textDecoration: "underline" }}>Make it public in Settings</Link> to share it.
            </p>
          </div>
        </section>
      )}

      <section className="frame grid" style={{ paddingBottom: 100, rowGap: 40 }}>
        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 4 }}>Contribution</h2>
        </div>
        <div className="run-ledger">
          <div><b>{Number(p.counts.procedures_authored ?? 0)}</b><span>Procedures</span></div>
          <div><b>{Number(p.counts.verified_procedures ?? 0)}</b><span>Verified procedures</span></div>
          <div><b>{Number(p.counts.claims_authored ?? 0)}</b><span>Claims</span></div>
          <div><b>{Number(p.counts.commons_publications ?? 0)}</b><span>Commons publications</span></div>
        </div>

        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 4 }}>Work</h2>
        </div>
        <div className="empty" style={{ gridColumn: "1 / span 12" }}>
          <p>A per-contributor list of individual ways isn&rsquo;t available yet. The counts above are real and server-computed; browse <a href="/goals" style={{ textDecoration: "underline" }}>Goals</a> to find this person&rsquo;s work by Goal.</p>
        </div>
      </section>
    </>
  );
}
