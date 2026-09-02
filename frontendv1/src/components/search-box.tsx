"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

const PLACEHOLDERS = [
  "How do I debug a flaky pytest test?",
  "What's the best way to understand a large codebase?",
  "How should I run a coding agent overnight?",
  "What's the cheapest way to extract symbols from a repo?",
  "How do I reduce MCP context?",
  "What's the best way to migrate a Postgres table?",
];

export function SearchBox({
  initialQuery = "",
  size = "lg",
  className,
}: {
  initialQuery?: string;
  size?: "lg" | "sm";
  className?: string;
}) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState(initialQuery);
  const [placeholderIndex, setPlaceholderIndex] = useState(0);

  useEffect(() => {
    setQ(initialQuery);
  }, [initialQuery]);

  // Cmd/Ctrl+K focuses search from anywhere; Esc clears it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (q || size === "sm") return;
    const id = setInterval(
      () => setPlaceholderIndex((i) => (i + 1) % PLACEHOLDERS.length),
      4000
    );
    return () => clearInterval(id);
  }, [q, size]);

  return (
    <form
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        const query = q.trim();
        if (query) router.push(`/search?q=${encodeURIComponent(query)}`);
      }}
      className={cn("flex w-full items-center gap-3", className)}
    >
      <input
        ref={inputRef}
        type="search"
        name="q"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            setQ("");
            inputRef.current?.blur();
          }
        }}
        aria-label="What are you trying to accomplish?"
        placeholder={PLACEHOLDERS[placeholderIndex]}
        className={cn(
          "w-full rounded-lg border border-neutral-200 bg-white text-neutral-900 placeholder:text-neutral-400 outline-none transition-colors focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5",
          size === "lg" ? "h-14 px-5 text-base" : "h-9 px-3 text-sm"
        )}
      />
      <button
        type="submit"
        className={cn(
          "shrink-0 rounded-lg bg-neutral-900 font-medium text-neutral-50 transition-colors hover:bg-neutral-800",
          size === "lg" ? "h-14 px-6 text-sm" : "h-9 px-4 text-sm"
        )}
      >
        Search
      </button>
    </form>
  );
}
