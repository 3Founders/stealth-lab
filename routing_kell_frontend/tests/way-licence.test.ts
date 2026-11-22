import { describe, expect, it } from "vitest";
import { priceLabel } from "@/lib/way-rate";

/**
 * The Way page must never offer a run it cannot deliver (docs/procedure_billing.md §3, §7).
 *
 * The backend refuses a run on a priced Way without a paid licence. The page therefore has two states that have
 * to agree: the price block ("Buy for ₹150") and the provenance call-to-action ("Run this Way"). If the page
 * shows "Run this Way" on an unbought paid Way, the buyer clicks and is refused — which reads as a broken product
 * rather than an unpurchased one.
 *
 * The predicate lives in the page; this pins the reasoning it encodes so a later edit has to state its case.
 */
const needsLicence = (pricePaise: number | null | undefined, licenceStatus?: string | null): boolean => {
  const price = priceLabel(pricePaise, "INR");
  return !!price && licenceStatus !== "purchased";
};

describe("run is offered only when it can be delivered", () => {
  it("a free Way always runs — no licence exists to have", () => {
    expect(needsLicence(null, "none")).toBe(false);
    expect(needsLicence(undefined, undefined)).toBe(false);
    expect(needsLicence(0, "none")).toBe(false);
  });

  it("a priced Way the reader has not bought does not offer a run", () => {
    expect(needsLicence(15000, "none")).toBe(true);
    expect(needsLicence(15000, undefined)).toBe(true);
  });

  it("a refunded licence goes back to needing one", () => {
    // The purchase happened, then was reversed: the right to run went with the money.
    expect(needsLicence(15000, "refunded")).toBe(true);
  });

  it("a purchased licence runs without asking again", () => {
    expect(needsLicence(15000, "purchased")).toBe(false);
  });

  it("an unpriced contract is treated as free, not as unknown-and-blocked", () => {
    // Before the backend ships `price_paise`, every Way must keep running exactly as it does today.
    expect(needsLicence(undefined, undefined)).toBe(false);
  });
});
