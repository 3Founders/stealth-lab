import OrgShell from "@/components/org/OrgShell";
import "./org.css";

export default function OrgLayout({ children }: { children: React.ReactNode }) {
  return <OrgShell>{children}</OrgShell>;
}
