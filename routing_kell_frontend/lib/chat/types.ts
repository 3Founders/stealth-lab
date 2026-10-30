export const MAX_DEPTH = 4;

export interface AgentIdentity {
  handle: string; // e.g. "@alice/agent-1"
  displayName: string;
  type: "human" | "local_agent" | "remote_agent";
  status: "online" | "busy" | "offline";
  /** The account this person is (people only), for showing their profile photo. */
  sub?: string;
}

export interface DualPayload {
  text: string; // Human-readable Markdown
  structuredPayload?: {
    protocol: "application/mcp+json";
    action: string;
    locators?: Array<{ path: string; lines?: string }>;
    data?: Record<string, unknown>;
  };
}

export interface GroupMessage {
  id: string;
  roomId: string;
  sender: AgentIdentity;
  recipients: string[]; // ["@all"] or ["@bob/opencode"]
  payload: DualPayload;
  timestamp: number;
  depthCounter: number; // agent-to-agent hops so far; dispatch to agents stops at MAX_DEPTH
}

export interface Room {
  id: string; // "#auth-refactor-squad"
  title: string;
  members: string[]; // handles
}

/** What an agent is doing right now, as the room shows it. */
export type AgentActivity = "idle" | "processing" | "delivering";

/** A message in flight: it exists only until the recipient takes it. */
export interface TransportPacket {
  to: string;
  message: GroupMessage;
}

export type RelayEvent =
  | { kind: "message"; message: GroupMessage }
  | { kind: "notice"; roomId: string; text: string }
  | { kind: "activity"; handle: string; activity: AgentActivity }
  | { kind: "paused"; paused: boolean }
  | { kind: "members"; roomId: string; members: string[] };
