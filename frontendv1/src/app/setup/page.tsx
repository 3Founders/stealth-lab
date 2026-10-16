"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Section } from "@/components/section";
import { EmptyState } from "@/components/domain";
import { Skeleton } from "@/components/ui/skeleton";
import { API_URL, listWorkspaces, type Workspace } from "@/lib/api/client";
import { getAuth, onAuthChange } from "@/lib/auth";
import { isWebMcpSupported } from "@/webmcp/registry";

function Panel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-ink/10 py-8 first:pt-0 last:border-b-0">
      <h2 className="text-sm font-medium uppercase tracking-widest text-ink/65">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  );
}

export default function SetupPage() {
  const [auth, setAuth] = useState<ReturnType<typeof getAuth>>(null);
  const [mounted, setMounted] = useState(false);
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [workspacesError, setWorkspacesError] = useState(false);
  const [webMcp, setWebMcp] = useState<boolean | null>(null);

  useEffect(() => {
    setMounted(true);
    setAuth(getAuth());
    const off = onAuthChange(() => setAuth(getAuth()));
    setWebMcp(isWebMcpSupported());
    return off;
  }, []);

  const signedIn = mounted && !!auth;

  useEffect(() => {
    if (!signedIn) return;
    listWorkspaces()
      .then(setWorkspaces)
      .catch(() => setWorkspacesError(true));
  }, [signedIn]);

  return (
    <Section tone="paper" innerClassName="py-16">
      <h1 className="font-display text-3xl tracking-tight text-ink">Setup</h1>
      <p className="mt-2 max-w-lg text-sm text-ink/70">
        Workspace, connections, and account controls. Only capabilities the
        backend actually supports are shown here.
      </p>

      <div className="mt-10">
        <Panel title="Workspace">
          {!signedIn ? (
            <EmptyState title="Sign in to see your workspaces.">
              <Link href="/auth" className="text-sm text-ink underline">
                Sign in
              </Link>
            </EmptyState>
          ) : workspacesError ? (
            <p className="text-sm text-ink/65">Unable to load workspaces right now.</p>
          ) : workspaces === null ? (
            <Skeleton className="h-10 w-full max-w-sm" />
          ) : workspaces.length === 0 ? (
            <EmptyState
              title="No registered execution workspaces."
              hint="Workspaces are created when a durable execution run registers one."
            />
          ) : (
            <ul className="divide-y divide-ink/10">
              {workspaces.map((w) => (
                <li key={w.id} className="py-2 text-sm text-ink">
                  {w.name || w.id}
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="MCP &amp; agent connection">
          <p className="text-sm text-ink/70">
            keळ is reachable from coding agents over MCP, and exposes a WebMCP
            tool surface directly in this browser tab.
          </p>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between border-b border-ink/10 pb-2">
              <dt className="text-ink/65">WebMCP in this browser</dt>
              <dd className="font-medium text-ink">
                {webMcp === null ? "…" : webMcp ? "Available" : "Not available"}
              </dd>
            </div>
          </dl>
          <Link href="/webmcp" className="mt-3 inline-block text-sm text-ink underline">
            View WebMCP tool status
          </Link>
          <p className="mt-2">
            <Link href="/docs/agents-and-mcp" className="text-sm text-ink/70 underline">
              How agents connect to keळ
            </Link>
          </p>
        </Panel>

        <Panel title="API connection">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between border-b border-ink/10 pb-2">
              <dt className="text-ink/65">Backend API</dt>
              <dd className="font-mono text-xs text-ink">{API_URL}</dd>
            </div>
            <div className="flex justify-between border-b border-ink/10 pb-2">
              <dt className="text-ink/65">Identity</dt>
              <dd className="font-medium text-ink">
                {!mounted
                  ? "…"
                  : auth
                    ? auth.mode === "supabase"
                      ? "Verified (Supabase)"
                      : "Dev viewer identity (unverified)"
                    : "Signed out"}
              </dd>
            </div>
          </dl>
        </Panel>

        <Panel title="Billing">
          <EmptyState title="keळ has no billing yet — everything here is free during the commons phase." />
        </Panel>

        <Panel title="Usage">
          <EmptyState
            title="Per-account usage isn't exposed to the frontend yet."
            hint="The backend enforces daily spend governance server-side."
          />
        </Panel>

        <Panel title="Security &amp; preferences">
          <ul className="space-y-2 text-sm">
            <li>
              <Link href="/me/privacy" className="text-ink underline">
                Privacy &amp; data
              </Link>
              <span className="text-ink/65"> — public profile, export, deletion.</span>
            </li>
            <li>
              <Link href="/me" className="text-ink underline">
                Your contributions
              </Link>
              <span className="text-ink/65"> — procedures, claims, executions.</span>
            </li>
          </ul>
        </Panel>
      </div>
    </Section>
  );
}
