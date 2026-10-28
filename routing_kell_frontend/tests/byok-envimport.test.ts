// @vitest-environment node
// Importing keys from a pasted .env block.
import { describe, expect, it } from "vitest";
import { parseEnvBlock } from "@/lib/byok/envimport";

describe("pasted .env import", () => {
  it("reads the standard provider variables, with export, quotes, comments and CRLF", () => {
    const r = parseEnvBlock([
      "# my keys", "export ANTHROPIC_API_KEY='sk-ant-AAAA1111'", 'OPENAI_API_KEY="sk-oai-BBBB2222" # work', "GROQ_API_KEY=gsk_DDDD4444  # trailing", "TOGETHER_API_KEY=tg-EEEE5555", "",
    ].join("\r\n"));
    expect(r.found.map((f) => [f.varName, f.provider, f.apiKey])).toEqual([
      ["ANTHROPIC_API_KEY", "anthropic", "sk-ant-AAAA1111"],
      ["OPENAI_API_KEY", "openai", "sk-oai-BBBB2222"],
      ["GROQ_API_KEY", "groq", "gsk_DDDD4444"],
      ["TOGETHER_API_KEY", "together", "tg-EEEE5555"],
    ]);
  });

  it("ignores everything else, including other secrets, and returns none of it", () => {
    const r = parseEnvBlock("DATABASE_URL=postgres://u:pw@h/db\nSTRIPE_SECRET=sk_live_zzz\nnot a line\nOPENAI_API_KEY=sk-ok-12345678");
    expect(r.found).toHaveLength(1);
    expect(r.ignored).toBe(3);
    expect(JSON.stringify(r)).not.toContain("postgres");
    expect(JSON.stringify(r)).not.toContain("sk_live_zzz");
  });

  it("skips empty values, ${REFERENCES} and repeats", () => {
    const r = parseEnvBlock("OPENAI_API_KEY=\nANTHROPIC_API_KEY=${SOMETHING}\nGROQ_API_KEY=gsk_one\nGROQ_API_KEY=gsk_two");
    expect(r.found.map((f) => f.apiKey)).toEqual(["gsk_one"]);
  });
});
