import { describe, expect, it } from "vitest";
import { joinCommand, NEXT_STEP_TEXT, parseUnits } from "@/lib/onboarding-api";

describe("parseUnits", () => {
  it("reads model, input price, output price and an optional provider id", () => {
    const r = parseUnits("a, 0.09, 0.34, A-Provider\nb,1,2\n\n");
    expect(r).toEqual({ ok: true, units: [{ model: "a", input_per_mtok: 0.09, output_per_mtok: 0.34, provider_model: "A-Provider" }, { model: "b", input_per_mtok: 1, output_per_mtok: 2 }] });
  });
  it("refuses a missing or non-numeric price, naming the line (an unpriced model blocks every budgeted call)", () => {
    for (const bad of ["a", "a, 1", "a, 1, x", "a, , 2", "a, -1, 2", "a b, 1, 2", "a|b, 1, 2", "a, 1, 2, p, extra"]) {
      const r = parseUnits(bad);
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.error).toMatch(/^Line 1/);
    }
    const second = parseUnits("a, 1, 2\nb, nope, 2");
    expect(!second.ok && second.error).toMatch(/^Line 2/);
  });
  it("refuses an empty box and a model listed twice", () => {
    expect(parseUnits("  \n ").ok).toBe(false);
    const dup = parseUnits("a, 1, 2\na, 3, 4");
    expect(!dup.ok && dup.error).toMatch(/twice/);
  });
});

describe("joinCommand", () => {
  it("is one line carrying the code and both addresses", () => {
    const c = joinCommand("sl1_abc", "https://mcp.example.com/mcp", "https://api.example.com");
    expect(c).toBe("npx -y kell-mcp join sl1_abc --url https://mcp.example.com/mcp --api https://api.example.com");
    expect(c).not.toContain("\n");
  });
  it("leaves out an address it does not have rather than printing an empty flag", () => {
    expect(joinCommand("sl1_abc", "", "https://api.example.com")).toBe("npx -y kell-mcp join sl1_abc --api https://api.example.com");
  });
});

describe("next-step text", () => {
  it("has a sentence for every step the backend can answer", () => {
    for (const k of ["connect_key", "set_policy", "invite_developers", "waiting_for_developers", "running"] as const) expect(NEXT_STEP_TEXT[k].length).toBeGreaterThan(10);
  });
});
