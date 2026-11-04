// Reply on a message: the reply carries the id of the message it answers (reply_to on the server), the room shows that message quoted
// above it, and an agent asked in a reply is told what is being replied to, since a short "do this one" means nothing on its own.
import { splitFiles } from "./files";

/** A message's text as one short line for a quote: file tokens become their names, code blocks become "[code]". */
export function quoteText(text: string, max = 140): string {
  const { text: t, files } = splitFiles(text ?? "");
  const flat = t.replace(/```[\s\S]*?(```|$)/g, " [code] ").replace(/\s+/g, " ").trim() || files.map((f) => f.name).join(", ");
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat;
}

/** What an agent is asked when the message is a reply: the quoted message first, then what was typed. `who` is null for your own message. */
export function withReplyContext(line: string, who: string | null, quoted: string): string {
  const q = quoteText(quoted, 1200);
  if (!q) return line;
  return `(Replying to ${who ? `${who}'s` : "my earlier"} message: "${q}")\n\n${line}`;
}
