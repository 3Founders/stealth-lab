import Image from "next/image";

import { cn } from "@/lib/utils";

import fullLogo from "../../public/full-logo.png";
import logoObject from "../../public/logo-object.png";

/**
 * The keळ mark — supplied artwork (public/logo-object.png): two bananas
 * bound by a single black calligraphic stroke that hooks into a curl at the
 * end, standing in for ळ's own hook. Full colour, used as-is — not a
 * recolourable silhouette, so it doesn't take a `text-*` colour prop.
 */
export function Mark({
  className,
  priority,
}: {
  className?: string;
  priority?: boolean;
}) {
  return (
    <Image
      src={logoObject}
      alt="keळ"
      className={cn("shrink-0 object-contain", className)}
      priority={priority}
    />
  );
}

/**
 * The full wordmark lockup — supplied artwork (public/full-logo.png): "ke"
 * with the mark's hook standing in for ळ, already composed as one image.
 * Use this wherever the brand name appears with its mark; use `Mark` alone
 * for icon-only spots (favicon-adjacent, decorative accents).
 */
export function Logo({
  className,
  priority,
}: {
  className?: string;
  priority?: boolean;
}) {
  return (
    <Image
      src={fullLogo}
      alt="keळ"
      className={cn("h-8 w-auto shrink-0 object-contain", className)}
      priority={priority}
    />
  );
}
