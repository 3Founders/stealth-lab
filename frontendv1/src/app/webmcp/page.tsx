"use client";

import { useEffect, useState } from "react";

import { isWebMcpSupported, registerWebMcpTools } from "@/webmcp/registry";
import { TOOL_DEFS } from "@/webmcp/schemas";

const EXPECTED = TOOL_DEFS.map((t) => t.name);

export default function WebMcpTestPage() {
  const [supported, setSupported] = useState<boolean | null>(null);
  const [registered, setRegistered] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try {
      setSupported(isWebMcpSupported());
      const reg = registerWebMcpTools();
      if (reg) setRegistered(reg.toolNames);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  return (
    <div className="pt-10">
      <h1 className="text-2xl font-medium tracking-tight">WebMCP status</h1>
      <p className="mt-2 text-sm text-neutral-500">
        Verification page for the site&apos;s agent tool surface.
      </p>

      <dl className="mt-8 space-y-3 text-sm">
        <div className="flex justify-between border-b border-neutral-100 pb-3">
          <dt className="text-neutral-500">document.modelContext</dt>
          <dd className="font-medium">
            {supported === null ? "…" : supported ? "available" : "not available"}
          </dd>
        </div>
        <div className="flex justify-between border-b border-neutral-100 pb-3">
          <dt className="text-neutral-500">Tools registered</dt>
          <dd className="font-medium">{registered.length}</dd>
        </div>
      </dl>

      {error ? (
        <p className="mt-6 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      ) : null}

      <h2 className="mt-10 text-lg font-medium">Expected tools</h2>
      <ul className="mt-4 space-y-2 text-sm">
        {EXPECTED.map((name) => {
          const ok = registered.includes(name);
          return (
            <li key={name} className="flex items-center justify-between">
              <code className="text-neutral-900">{name}</code>
              <span className={ok ? "text-green-700" : "text-neutral-400"}>
                {ok ? "registered" : supported ? "not registered" : "n/a (no WebMCP)"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
