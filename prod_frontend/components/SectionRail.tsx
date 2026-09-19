"use client";
import { useEffect, useState } from "react";

/** Left-edge pager (index + dots), echoing an editorial page counter. Purely navigational. */
export default function SectionRail({ ids }: { ids: { id: string; label: string }[] }) {
  const [active, setActive] = useState(0);

  useEffect(() => {
    const els = ids.map((s) => document.getElementById(s.id)).filter(Boolean) as HTMLElement[];
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => { if (e.isIntersecting) setActive(ids.findIndex((s) => s.id === e.target.id)); }),
      { rootMargin: "-40% 0px -55% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [ids]);

  return (
    <nav className="rail" aria-label="Sections">
      <span className="idx" aria-hidden="true">{String(active + 1).padStart(3, "0")}</span>
      {ids.map((s, i) => (
        <a
          key={s.id}
          href={`#${s.id}`}
          aria-label={s.label}
          aria-current={i === active}
          onClick={(e) => { if (window.__kelScrollTo) { e.preventDefault(); window.__kelScrollTo(`#${s.id}`); } }}
        />
      ))}
    </nav>
  );
}
