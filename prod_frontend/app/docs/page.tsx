import type { Metadata } from "next";
import DocsSidebar from "@/components/DocsSidebar";
import { LEGAL_DOCS } from "@/lib/legal";

export const metadata: Metadata = { title: "Docs" };

export default function Docs() {
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>DOCS</b></div>
        <h1 className="display">Docs.</h1>
        <p className="lead">Setup, day-to-day use, and how contribution and rewards work.</p>
      </section>

      <div className="frame docs">
        <DocsSidebar legalDocs={LEGAL_DOCS} />
      </div>
    </>
  );
}
