import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type Tone = "paper" | "paper-dim" | "yellow" | "ink" | "botanical" | "cobalt";

/**
 * Full-bleed section wrapper carrying one tone from the brand's background
 * vocabulary (paper / yellow / ink / botanical / cobalt). Content is capped
 * and centered inside; the tone itself always runs edge to edge so page
 * rhythm reads as composed bands, not a white page with cards on it.
 */
export function Section({
  tone = "paper",
  grain = true,
  className,
  innerClassName,
  children,
}: {
  tone?: Tone;
  grain?: boolean;
  className?: string;
  innerClassName?: string;
  children: ReactNode;
}) {
  return (
    <section
      className={cn(
        `tone-${tone}`,
        grain && "grain",
        // Full-bleed breakout: this component is meant to be used inside the
        // app's centered <main>, but its background should still run edge to
        // edge, so it escapes that constraint here and re-centers content
        // via the inner div below.
        "relative left-1/2 right-1/2 -mx-[50vw] w-screen",
        className
      )}
    >
      <div className={cn("relative z-[1] mx-auto max-w-5xl px-6", innerClassName)}>
        {children}
      </div>
    </section>
  );
}
