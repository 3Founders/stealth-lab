import type { AgentActivity } from "@/lib/delegator/types";

const LABEL: Record<AgentActivity, string> = { idle: "Idle", processing: "Processing", delivering: "Delivering via Relay" };
const TONE: Record<AgentActivity, string> = { idle: "unknown", processing: "executed", delivering: "successful" };

/** One agent and what it is doing now. */
export default function AgentStatus({ handle, activity }: { handle: string; activity: AgentActivity }) {
  return (
    <span className="cluster" style={{ gap: 8 }}>
      <code>{handle}</code>
      <span className="status" data-s={TONE[activity]} aria-label={`${handle}: ${LABEL[activity]}`}>{LABEL[activity]}</span>
    </span>
  );
}
