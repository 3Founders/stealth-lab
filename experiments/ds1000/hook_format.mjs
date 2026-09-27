// The shipped Claude Code hook's own formatter (packaging/npm/lib/hook.mjs), for DS-1000 round 5 arm KH:
// reads a find_ways reply (JSON) on stdin, prints exactly the additionalContext the hook would inject ("" = none).
import { formatKnowledge, hookPolicy } from "../../packaging/npm/lib/hook.mjs";

const chunks = [];
for await (const c of process.stdin) chunks.push(c);
const reply = JSON.parse(Buffer.concat(chunks).toString("utf8") || "null");
process.stdout.write(formatKnowledge(reply, hookPolicy({}).maxChars));
