/**
 * Framework-independent WebMCP registry. Progressive enhancement: does nothing
 * when document.modelContext is unavailable. Tools share the same typed API
 * client as the human UI and never bypass backend authorization.
 */
import { TOOL_DEFS } from "./schemas";
import { TOOL_HANDLERS, type ToolName } from "./tools";

interface RegisterableModelContext {
  registerTool(tool: {
    name: string;
    title: string;
    description: string;
    inputSchema: unknown;
    execute: (args: Record<string, unknown>) => Promise<unknown>;
  }): void;
}

function getModelContext(): RegisterableModelContext | null {
  if (typeof document === "undefined") return null;
  const mc = (document as unknown as Record<string, unknown>).modelContext;
  if (
    mc &&
    typeof mc === "object" &&
    typeof (mc as RegisterableModelContext).registerTool === "function"
  ) {
    return mc as RegisterableModelContext;
  }
  return null;
}

export function isWebMcpSupported(): boolean {
  return getModelContext() !== null;
}

/**
 * Current page context, used for repository-scoped operations when safe.
 * Only the public route id is exposed — never private page data.
 */
export function currentRepositoryContext(): string | null {
  if (typeof window === "undefined") return null;
  const match = /^\/repositories\/([^/]+)/.exec(window.location.pathname);
  if (!match) return null;
  const id = decodeURIComponent(match[1]);
  return /^[0-9a-fA-F-]{8,64}$/.test(id) ? id : null;
}

export interface WebMcpRegistration {
  unregister: () => void;
  toolNames: string[];
}

export function registerWebMcpTools(): WebMcpRegistration | null {
  const modelContext = getModelContext();
  if (!modelContext) return null;

  const registered: ToolName[] = [];
  for (const def of TOOL_DEFS) {
    try {
      modelContext.registerTool({
        name: def.name,
        title: def.title,
        description: def.description,
        inputSchema: def.inputSchema,
        execute: async (args: Record<string, unknown>) => {
          const handler = TOOL_HANDLERS[def.name as ToolName];
          // Repository context is injected only when explicitly viewing one.
          if (
            def.name === "search_stealth" &&
            args.repository_id === undefined &&
            currentRepositoryContext()
          ) {
            args = { ...args, repository_id: currentRepositoryContext() };
          }
          if (
            def.name === "find_best_way" &&
            args.repository_id === undefined &&
            currentRepositoryContext()
          ) {
            args = { ...args, repository_id: currentRepositoryContext() };
          }
          return handler(args);
        },
      });
      registered.push(def.name);
    } catch (err) {
      console.warn(`[webmcp] failed to register ${def.name}:`, err);
    }
  }

  return {
    toolNames: registered,
    unregister: () => {
      const mc = getModelContext();
      if (!mc) return;
      const maybeRemove = (
        mc as unknown as Record<string, unknown>
      ).removeTool as ((name: string) => void) | undefined;
      if (typeof maybeRemove === "function") {
        for (const name of registered) {
          try {
            maybeRemove.call(mc, name);
          } catch {
            /* best-effort cleanup */
          }
        }
      }
    },
  };
}
