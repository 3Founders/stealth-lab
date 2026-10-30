import { describe, expect, it } from "vitest";
import { rupees, stateName } from "@/lib/billing-api";
import { nextStep, type MfaState } from "@/lib/mfa";

describe("rupees", () => {
  it("shows whole paise exactly, with Indian grouping", () => {
    expect(rupees(0)).toBe("₹0.00");
    expect(rupees(5)).toBe("₹0.05");
    expect(rupees(10_000)).toBe("₹100.00");
    expect(rupees(12_345_678)).toBe("₹1,23,456.78");
    expect(rupees(-250)).toBe("-₹2.50");
  });
  it("knows state names for place of supply", () => {
    expect(stateName("27")).toBe("Maharashtra");
    expect(stateName("29")).toBe("Karnataka");
    expect(stateName("99")).toBe("99");
  });
});

describe("second-factor steps", () => {
  const s = (current: MfaState["current"], statuses: string[]): MfaState => ({ current, next: "aal2", configured: true, factors: statuses.map((status, i) => ({ id: `f${i}`, name: null, status })) });
  it("a session that already passed the second factor needs nothing more", () => { expect(nextStep(s("aal2", ["verified"]))).toBeNull(); });
  it("with an enrolled factor the person enters a code", () => { expect(nextStep(s("aal1", ["verified"]))).toBe("verify"); });
  it("with none (or only an unfinished enrolment) the person enrols first", () => {
    expect(nextStep(s("aal1", []))).toBe("enroll");
    expect(nextStep(s("aal1", ["unverified"]))).toBe("enroll");
  });
});
