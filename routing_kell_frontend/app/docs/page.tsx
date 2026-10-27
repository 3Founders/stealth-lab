import type { Metadata } from "next";
import AnimatedHeading from "@/components/AnimatedHeading";
import DocsSidebar from "@/components/DocsSidebar";
import { LEGAL_DOCS } from "@/lib/legal";

export const metadata: Metadata = { title: "Docs" };

export default function Docs() {
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>DOCS</b></div>
        <h1 className="display"><AnimatedHeading>Docs</AnimatedHeading></h1>
        <p className="lead">Rolling keळ out to a team, and the legal documents.</p>
      </section>

      <div className="frame docs">
        <DocsSidebar legalDocs={LEGAL_DOCS} />
      </div>
    </>
  );
}
