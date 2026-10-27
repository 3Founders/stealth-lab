"use client";
import { useEffect, useState } from "react";
import CopyCommand from "@/components/CopyCommand";
import type { HelperClient } from "@/lib/helper";
import type { ReadinessItem } from "@/lib/types";

export type McpState = { state: "running" | "closed" | "unknown"; detail: string };

/** Reads the MCP's state from the helper's readiness list: its "server" line is a real call to the MCP address in use. */
export function mcpState(items: ReadinessItem[] | null): McpState {
  const s = items?.find((i) => i.name === "server");
  if (!s) return { state: "unknown", detail: "" };
  return s.ok ? { state: "running", detail: "" } : { state: "closed", detail: s.detail };
}

export const START_COMMANDS = { installed: "npx -y kell-mcp console", checkout: "npm run mcp" };

/**
 * Two lights that update on their own: whether the MCP answers, and whether the helper this page talks to is still there.
 * Each one stopping is shown here; neither stops the other.
 */
export default function StatusBar({ client, helperEveryMs = 4000, mcpEveryMs = 10000 }: { client: HelperClient; helperEveryMs?: number; mcpEveryMs?: number }) {
  const [helperUp, setHelperUp] = useState(true);
  const [mcp, setMcp] = useState<McpState>({ state: "unknown", detail: "" });

  useEffect(() => {
    let live = true;
    const pollHelper = async () => {
      const ok = await client.health();
      if (live) setHelperUp(ok);
    };
    const pollMcp = async () => {
      if (document.hidden) return;
      try {
        const items = await client.readiness();
        if (live) { setHelperUp(true); setMcp(mcpState(items)); }
      } catch {
        if (live) setMcp({ state: "unknown", detail: "" }); // the helper is the thing that is down; its light says so
      }
    };
    void pollHelper(); void pollMcp();
    const a = window.setInterval(pollHelper, helperEveryMs);
    const b = window.setInterval(pollMcp, mcpEveryMs);
    return () => { live = false; window.clearInterval(a); window.clearInterval(b); };
  }, [client, helperEveryMs, mcpEveryMs]);

  return (
    <div className="grid-full statusbar" role="status" aria-live="polite" aria-label="What is running">
      <div className="cluster">
        <span className="status" data-s={helperUp ? "successful" : "failed"} data-light="helper">Console helper {helperUp ? "running" : "stopped"}</span>
        <span className="status" data-s={!helperUp ? "unknown" : mcp.state === "running" ? "successful" : mcp.state === "closed" ? "failed" : "unknown"} data-light="mcp">
          MCP {!helperUp ? "unknown" : mcp.state === "running" ? "running" : mcp.state === "closed" ? "closed" : "checking…"}
        </span>
      </div>
      {!helperUp && (
        <div className="notice" data-tone="bad" style={{ marginTop: 10 }}>
          <p>The console helper has stopped, so this page cannot install, save keys or stream replies. Start it again, then reload.</p>
          <div className="stack" style={{ marginTop: 8 }}>
            <CopyCommand command={START_COMMANDS.installed} label="Start the helper, installed" />
            <CopyCommand command={START_COMMANDS.checkout} label="Start the helper and the MCP, from a checkout" />
          </div>
        </div>
      )}
      {helperUp && mcp.state === "closed" && (
        <p className="notice" style={{ marginTop: 10 }}>
          The MCP is closed{mcp.detail ? ` (${mcp.detail})` : ""}. This console is still running, so you can keep working here; tools that need the MCP will not work until it is back.
        </p>
      )}
    </div>
  );
}
