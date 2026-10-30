"use client";
import { useEffect, useState } from "react";
import { useDevicePhoto } from "@/components/DevicePhoto";
import { personPhoto } from "@/lib/chat/people-photos";

/** A person's avatar in a room: their profile photo when they have one you may see, else their initials. */
export default function PersonAvatar({ sub, mine, selfSub, initials, className = "dg-avatar" }: {
  sub?: string; mine?: boolean; selfSub: string; initials: string; className?: string;
}) {
  const own = useDevicePhoto(mine ? selfSub : null);
  const [other, setOther] = useState<string | null>(null);
  useEffect(() => {
    if (mine || !sub) { setOther(null); return; }
    let live = true;
    void personPhoto(sub).then((u) => { if (live) setOther(u); });
    return () => { live = false; };
  }, [sub, mine]);
  const src = mine ? own : other;
  return (
    <span className={className} data-kind="human" aria-hidden="true">
      {/* eslint-disable-next-line @next/next/no-img-element -- a local blob: or data: URL */}
      {src ? <img src={src} alt="" /> : initials}
    </span>
  );
}
