"use client";
import { useState } from "react";
import Markdown from "@/components/chat/Markdown";
import { splitArtifacts, type Artifact } from "@/lib/chat/artifacts";
import { dataUrlOf, hostOf, splitImages } from "@/lib/chat/images";
import type { ImageAttachment } from "@/lib/chat/types";

/** A picture drawn from a data: address or an attachment. Click to see it larger. */
function Picture({ src, alt }: { src: string; alt: string }) {
  const [big, setBig] = useState(false);
  return (
    <button type="button" className="dg-img" data-big={big || undefined} onClick={() => setBig((b) => !b)} aria-label={big ? "Make the picture smaller" : "Show the picture larger"}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={alt} loading="lazy" decoding="async" referrerPolicy="no-referrer" />
    </button>
  );
}

/** A picture on the web: nothing is requested until you ask. The address could record that you opened it, so it is your call. */
function RemotePicture({ url, alt }: { url: string; alt: string }) {
  const [load, setLoad] = useState(false);
  if (load) return <Picture src={url} alt={alt} />;
  return (
    <button type="button" className="dg-img-ask" onClick={() => setLoad(true)}>
      Load picture from {hostOf(url)}{alt ? ` (${alt})` : ""}
    </button>
  );
}

/**
 * A page an agent built (a slide deck, a small site, a chart), from a fenced ```html block. It runs only when the person
 * clicks Render, in a sandbox with no access to this page: scripts may run inside it, but it cannot read the room, the
 * account or this tab, and it is never fetched on sight. Source shows exactly what was posted.
 */
export function ArtifactCard({ artifact }: { artifact: Artifact }) {
  const [view, setView] = useState<"closed" | "rendered" | "source">("closed");
  return (
    <div className="dg-artifact" data-view={view}>
      <div className="dg-artifact-head">
        <span className="dg-artifact-title">{artifact.title}</span>
        <span className="dg-artifact-size">{Math.max(1, Math.ceil(artifact.html.length / 1024))} KB</span>
        <button type="button" className="dg-artifact-btn" aria-pressed={view === "rendered"} onClick={() => setView(view === "rendered" ? "closed" : "rendered")}>
          {view === "rendered" ? "Hide" : "Render"}
        </button>
        <button type="button" className="dg-artifact-btn" aria-pressed={view === "source"} onClick={() => setView(view === "source" ? "closed" : "source")}>
          {view === "source" ? "Hide source" : "Source"}
        </button>
      </div>
      {view === "rendered" && (
        <>
          <iframe
            className="dg-artifact-frame" title={artifact.title} srcDoc={artifact.html}
            sandbox="allow-scripts allow-forms allow-modals allow-popups" referrerPolicy="no-referrer"
            loading="lazy"
          />
          <p className="small dim dg-artifact-note">Rendered in a sandbox: it cannot read this page or your account.</p>
        </>
      )}
      {view === "source" && <pre className="dg-artifact-src"><code>{artifact.html}</code></pre>}
    </div>
  );
}

/** The words of a message, the pictures attached to it, any pictures a model put in its reply, and any HTML artifacts. */
export default function MessageText({ text, attachments }: { text: string; attachments?: ImageAttachment[] }) {
  return (
    <>
      {attachments && attachments.length > 0 && (
        <div className="dg-imgs">{attachments.map((a, i) => <Picture key={i} src={dataUrlOf(a)} alt={a.name} />)}</div>
      )}
      {splitArtifacts(text).map((g, i) => g.t === "artifact"
        ? <ArtifactCard key={i} artifact={g.artifact} />
        : splitImages(g.text).map((s, j) => s.t === "text"
          ? (s.s.trim() ? <Markdown key={`${i}-${j}`} text={s.s.trim()} /> : null)
          : <div key={`${i}-${j}`} className="dg-imgs">{s.src === "data" ? <Picture src={s.url} alt={s.alt} /> : <RemotePicture url={s.url} alt={s.alt} />}</div>))}
    </>
  );
}
