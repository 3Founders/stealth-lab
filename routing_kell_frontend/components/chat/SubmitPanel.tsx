"use client";
import PanelShell from "@/components/chat/PanelShell";
import SubmitWay from "@/components/submit/SubmitWay";
import type { SubmissionsApi } from "@/lib/submissions-api";

/** "Submit a Way" beside the chat: the same form as the website's /submit page (components/submit/SubmitWay.tsx). */
export default function SubmitPanel({ open, onClose, api }: { open: boolean; onClose: () => void; api: SubmissionsApi }) {
  return (
    <PanelShell open={open} label="Submit a Way">
      <div className="dg-canvas-head">
        <div className="dg-canvas-headrow"><b className="dg-dk-title">Submit a Way</b></div>
        <button type="button" className="dg-cv-close" aria-label="Close" onClick={onClose}>
          <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
        </button>
      </div>
      <div className="dg-canvas-body dg-dk-body"><SubmitWay api={api} compact /></div>
    </PanelShell>
  );
}
