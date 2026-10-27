// Shapes the local helper (`kell console`, packaging/npm/lib/console.mjs) returns. Kept to what the page reads.

export type ClientId = "claude-code" | "cursor" | "codex";

export interface ClientInfo {
  id: ClientId;
  label: string;
  detected: boolean;
  installed: boolean;
  /** Whether kell's per-prompt hooks are integrated for this tool. */
  hooks: boolean;
  notes: string;
}

export interface InstallResult {
  client: ClientId;
  ok: boolean;
  changed_files: string[];
  /** On a dry run: the CLI's own account of what it would do. */
  diff?: string;
  error?: string;
  fix?: string;
}

export interface ReadinessItem {
  name: string;
  ok: boolean;
  detail: string;
  fix?: string;
}

export interface ModelInfo {
  name: string;
  model_id: string;
  base_url: string | null;
  key_env: string | null;
  local: boolean;
  key_set: boolean;
  key_last4: string | null;
  input_per_mtok: number | null;
  output_per_mtok: number | null;
  tier: string | null;
}

export interface NewModel {
  name: string;
  model_id: string;
  openai_base_url: string;
  key_env?: string;
  input_per_mtok?: number;
  output_per_mtok?: number;
  no_system_role?: boolean;
}

export interface KeyInfo {
  name: string;
  set: boolean;
  last4: string | null;
}

export interface TestResult {
  ok: boolean;
  latency_ms: number;
  error?: string;
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

/** One event of the /chat stream. Every event names its model. */
export type ChatEvent =
  | { model: string; delta: string }
  | { model: string; done: true; ttft_ms: number | null; total_ms: number; tokens_in: number | null; tokens_out: number | null; cost_usd: number | string | null }
  | { model: string; error: string };
