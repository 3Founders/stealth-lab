import { demoEnabled } from "@/lib/demo";

/** Shown on every page, and in print, whenever sample data is on. Sample data must never be mistaken for a real organisation. */
export default function DemoBanner() {
  if (!demoEnabled()) return null;
  return (
    <div role="alert" style={{ position: "relative", zIndex: 5, background: "var(--yellow)", borderBottom: "2px solid var(--ink)", padding: "8px var(--pad)", textAlign: "center", fontSize: 14 }}>
      <b>SAMPLE DATA.</b> Every number here is synthetic. It is not from any organisation, and nothing you do changes anything.
    </div>
  );
}
