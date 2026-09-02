"use client";

import { useEffect } from "react";

import { registerWebMcpTools } from "@/webmcp/registry";

/**
 * Progressive enhancement mount point. Registers the semantic tool surface
 * when the browser supports WebMCP; no-ops otherwise.
 */
export function WebMcpProvider() {
  useEffect(() => {
    const reg = registerWebMcpTools();
    return () => reg?.unregister();
  }, []);
  return null;
}
