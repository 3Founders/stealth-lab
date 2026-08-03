"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, Proposal } from "@/lib/api";

export default function ProposalsDocket() {
  const [items, setItems] = useState<Proposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await api.listProposals("pending"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach the API.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <main className="shell">
      <div className="masthead">
        <div>
          <div className="masthead-title">Open proposals</div>
          <div className="masthead-sub">plans awaiting approval</div>
        </div>
      </div>

      <div className="nav-tabs">
        <Link href="/workbench" className="nav-tab">
          Workbench
        </Link>
        <Link href="/approvals" className="nav-tab active">
          Proposals
        </Link>
        <Link href="/archive" className="nav-tab">
          Runs
        </Link>
      </div>

      {error && (
        <div className="empty-state">
          Could not reach the API at the configured address.
          <br />
          Check NEXT_PUBLIC_API_BASE_URL and that the backend is running.
        </div>
      )}

      {!error && items === null && <p style={{ color: "var(--ink-text-dim)" }}>Loading…</p>}

      {!error && items?.length === 0 && (
        <div className="empty-state">
          Nothing pending. Submit a prompt on the workbench that no existing task covers.
        </div>
      )}

      {items?.map((item) => (
        <Link
          key={item.id}
          href={`/approvals/${item.id}`}
          className={`docket-item ${item.typecheck?.ok ? "clean" : "flagged"}`}
        >
          <div className="docket-eyebrow">
            {item.typecheck?.ok
              ? "typechecks · approvable"
              : `${item.typecheck?.messages?.length ?? 0} structural problem(s) · not approvable`}
          </div>
          <p className="docket-summary">{item.request_text}</p>
          <div className="docket-meta">
            <span>
              {item.plan.nodes?.length ?? 0} step
              {(item.plan.nodes?.length ?? 0) === 1 ? "" : "s"}
            </span>
            <span>
              {item.plan.nodes?.filter((n) => n.existing_task_id).length ?? 0} reused
            </span>
            <span>{new Date(item.created_at).toLocaleString()}</span>
          </div>
        </Link>
      ))}
    </main>
  );
}
