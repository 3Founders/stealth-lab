"use client";
import { useEffect, useState } from "react";
import { PHOTO_EVENT, readDevicePhoto } from "@/lib/device-profile";

/** The photo kept in this browser (see lib/device-profile.ts), or null. Follows changes made on the profile page. */
export function useDevicePhoto(subject: string | null | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!subject) { setUrl(null); return; }
    const read = () => setUrl(readDevicePhoto(subject));
    read();
    const on = (e: Event) => { if ((e as CustomEvent).detail === subject) read(); };
    window.addEventListener(PHOTO_EVENT, on);
    return () => window.removeEventListener(PHOTO_EVENT, on);
  }, [subject]);
  return url;
}

export default function DevicePhoto({ url, size, alt = "" }: { url: string; size: number; alt?: string }) {
  // eslint-disable-next-line @next/next/no-img-element -- a small data: URL kept in this browser
  return <img className="avatar" src={url} alt={alt} width={size} height={size} style={{ width: size, height: size }} />;
}
