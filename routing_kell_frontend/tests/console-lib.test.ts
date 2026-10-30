import { describe, expect, it } from "vitest";
import { formatCost, formatMs, formatPrices, formatTokens } from "@/lib/console/format";
import { readConnection } from "@/lib/console/helper";
import { createSseParser } from "@/lib/console/sse";
import { helperMessage, isLoopbackAddress, scrub, suggestKeyName, WORDS } from "@/lib/console/words";

describe("sse parser", () => {
  it("emits one event per blank-line-terminated block, however the chunks fall", () => {
    const got: unknown[] = [];
    const p = createSseParser((e) => got.push(e));
    const wire = 'data: {"model":"a","delta":"Hel"}\n\ndata: {"model":"a","delta":"lo"}\n\ndata: {"model":"a","done":true}\n\n';
    for (const ch of wire) p.push(ch); // worst case: one character at a time
    p.end();
    expect(got).toEqual([{ model: "a", delta: "Hel" }, { model: "a", delta: "lo" }, { model: "a", done: true }]);
  });
  it("handles CRLF, skips a bad line, and flushes a final unterminated event", () => {
    const got: unknown[] = [];
    const p = createSseParser((e) => got.push(e));
    p.push('data: {"x":1}\r\n\r\ndata: not json\n\n');
    p.push('data: {"x":2}');
    expect(got).toEqual([{ x: 1 }]);
    p.end();
    expect(got).toEqual([{ x: 1 }, { x: 2 }]);
  });
});

describe("words", () => {
  it("never shows a raw body: only a short plain {error} from the helper, otherwise a fixed sentence", () => {
    expect(helperMessage("refused", { error: "the name must be letters, digits, dots, dashes" })).toBe("the name must be letters, digits, dots, dashes");
    expect(helperMessage("refused", { error: "<html>Traceback (most recent call last)\n  File..." })).toBe(WORDS.refused);
    expect(helperMessage("refused", { error: "x".repeat(500) })).toBe(WORDS.refused);
    expect(helperMessage("failed", { error: "secret internals" })).toBe(WORDS.failed);
    expect(helperMessage("refused", "just a string")).toBe(WORDS.refused);
  });
  it("scrub removes a secret wherever it appears", () => {
    expect(scrub("bad key sk-abc-123456789 was refused; sk-abc-123456789", "sk-abc-123456789")).toBe("bad key [hidden] was refused; [hidden]");
    expect(scrub("nothing here", undefined, null, "")).toBe("nothing here");
  });
  it("suggests a valid key name", () => {
    expect(suggestKeyName("my provider")).toBe("MY_PROVIDER_API_KEY");
    expect(suggestKeyName("9lives")).toBe("LIVES_API_KEY");
    expect(suggestKeyName("   ")).toBe("");
  });
  it("loopback means this machine only", () => {
    for (const ok of ["http://127.0.0.1:11434/v1", "http://localhost:3000", "http://[::1]:80/"]) expect(isLoopbackAddress(ok)).toBe(true);
    for (const bad of ["https://example.com", "http://127.0.0.1.evil.com", "http://192.168.1.5", "file:///etc/passwd", "javascript:alert(1)", "nonsense"]) expect(isLoopbackAddress(bad)).toBe(false);
  });
});

describe("format", () => {
  it("says 'price not set' instead of showing $0 when the helper sent no cost", () => {
    expect(formatCost(null)).toBe("price not set");
    expect(formatCost(undefined)).toBe("price not set");
    expect(formatCost("")).toBe("price not set");
    expect(formatCost(0.00012)).toBe("$0.00012");
    expect(formatCost(0.1234)).toBe("$0.1234");
    expect(formatPrices(null, null)).toBe("price not set");
    expect(formatPrices(1, 2)).toBe("$1 in / $2 out per 1M tokens");
  });
  it("formats time and tokens", () => {
    expect(formatMs(null)).toBe("n/a");
    expect(formatMs(412)).toBe("412 ms");
    expect(formatMs(2500)).toBe("2.50 s");
    expect(formatTokens(null)).toBe("n/a");
    expect(formatTokens(12345)).toBe("12,345");
  });
});

describe("connection", () => {
  it("takes the token out of the address and keeps it for the tab", () => {
    const c = readConnection("?t=abc123&tab=x", null);
    expect(c.token).toBe("abc123");
    expect(c.cleanSearch).toBe("?tab=x");
    expect(c.cleanSearch).not.toContain("abc123");
  });
  it("falls back to the token kept for this tab, and to none", () => {
    expect(readConnection("", "kept").token).toBe("kept");
    expect(readConnection("", null).token).toBeNull();
  });
  it("points at another helper only if it is on this machine", () => {
    expect(readConnection("?api=http://127.0.0.1:5000/&t=z", null).base).toBe("http://127.0.0.1:5000");
    const evil = readConnection("?api=https://evil.example&t=z", null);
    expect(evil.base).toBe("");
    expect(evil.cleanSearch).not.toContain("evil");
  });
});
