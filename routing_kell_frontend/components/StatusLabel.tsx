export type Status =
  | "unknown" | "candidate" | "observed" | "claimed" | "evidenced" | "verified" | "executed" | "failed" | "successful";

export default function StatusLabel({ s }: { s: Status }) {
  return <span className="status" data-s={s}>{s}</span>;
}
