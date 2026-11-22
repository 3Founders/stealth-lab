import { describe, expect, it } from "vitest";
import { priceLabel } from "@/lib/way-rate";

/**
 * Per-procedure billing (docs/procedure_billing.md §2.1). The rule that matters: **NULL means free, and 0 also
 * means free — but neither may render as "₹0".** A page that prints a zero price on every free Way invents a
 * price where none exists, and the page's whole job is not inventing things.
 */
describe("priceLabel", () => {
  it("returns null for an unpriced Way (NULL price_paise)", () => {
    expect(priceLabel(null)).toBeNull();
    expect(priceLabel(undefined)).toBeNull();
  });

  it("returns null for a deliberately free listing (0 paise) — never '₹0'", () => {
    expect(priceLabel(0)).toBeNull();
  });

  it("returns null for nonsense rather than printing it", () => {
    expect(priceLabel(-100)).toBeNull();
    expect(priceLabel(Number.NaN)).toBeNull();
  });

  it("prints rupees from paise, so nothing rounds between charge and display", () => {
    expect(priceLabel(15000)).toBe("₹150"); // the recommended default
    expect(priceLabel(10000)).toBe("₹100"); // the floor
    expect(priceLabel(20000)).toBe("₹200"); // the ceiling
  });

  it("prints a paise remainder exactly instead of hiding it", () => {
    expect(priceLabel(15050)).toBe("₹150.50");
  });

  it("groups thousands the way INR is written", () => {
    expect(priceLabel(100000)).toBe("₹1,000");
  });

  it("handles dollars", () => {
    expect(priceLabel(175, "USD")).toBe("$1.75");
  });

  it("falls back to the code for a currency it does not know, rather than guessing a symbol", () => {
    expect(priceLabel(1000, "EUR")).toBe("10.00 EUR");
  });
});
