"use client";
import { useState } from "react";
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

/** The words of a message, the pictures attached to it, and any pictures a model put in its reply. */
export default function MessageText({ text, attachments }: { text: string; attachments?: ImageAttachment[] }) {
  const segs = splitImages(text);
  return (
    <>
      {attachments && attachments.length > 0 && (
        <div className="dg-imgs">{attachments.map((a, i) => <Picture key={i} src={dataUrlOf(a)} alt={a.name} />)}</div>
      )}
      {segs.map((g, i) => g.t === "text"
        ? (g.s.trim() ? <p key={i}>{g.s.trim()}</p> : null)
        : <div key={i} className="dg-imgs">{g.src === "data" ? <Picture src={g.url} alt={g.alt} /> : <RemotePicture url={g.url} alt={g.alt} />}</div>)}
    </>
  );
}
