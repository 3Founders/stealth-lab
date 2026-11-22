import { notFound } from "next/navigation";
import { COMMONS_ENABLED } from "@/lib/features";

// Part of the public commons, which is off unless NEXT_PUBLIC_COMMONS=on (lib/features.ts).
export default function Layout({ children }: { children: React.ReactNode }) {
  if (!COMMONS_ENABLED) notFound();
  return children;
}
