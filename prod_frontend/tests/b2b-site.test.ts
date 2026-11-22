import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { COMMONS_ENABLED } from "@/lib/features";

// The B2B pages make claims about a lead we have not signed and numbers we can only partly back. These checks hold the
// copy rules (see the pilot plan and final_prod_docs/06_claims_register.md) so a later edit cannot quietly break them.
const ROOT = path.resolve(__dirname, "..");
const PAGES = ["app/page.tsx", "app/evidence/page.tsx", "app/trust/page.tsx", "app/pilot/page.tsx"];
const text = (f: string) => fs.readFileSync(path.join(ROOT, f), "utf8");

describe("public copy", () => {
  for (const f of PAGES) {
    it(`${f} states no price and no banned claim`, () => {
      const t = text(f);
      expect(t).not.toMatch(/\$\s?\d/);
      expect(t).not.toMatch(/per (developer|seat|user)[^.]*month|\/month|per month/i);
      expect(t).not.toMatch(/self-improving|pilot planned|zero[- ]data[- ]retention|zero retention guaranteed/i);
      expect(t).not.toMatch(/SOC ?2[- ](certified|compliant|type)/i);
    });
  }

  it("the evidence page shows no overall saving, only per-suite rows", () => {
    const t = text("app/evidence/page.tsx");
    expect(t).not.toMatch(/(\boverall (saving|cost)|average saving|total saving)/i);
    expect(t).toContain("there is no overall figure on purpose");
  });

  it("the trust page says what we do not have", () => {
    const t = text("app/trust/page.tsx");
    for (const gap of ["No SOC 2 report", "No third-party penetration test", "not a zero-egress product"]) expect(t).toContain(gap);
  });
});

describe("the commons is off by default", () => {
  it("is off unless NEXT_PUBLIC_COMMONS=on", () => {
    expect(COMMONS_ENABLED).toBe(false);
  });
  for (const dir of ["app/goals", "app/search", "app/account/credits", "app/review"]) {
    it(`${dir} answers 404 while it is off`, () => {
      const t = text(`${dir}/layout.tsx`);
      expect(t).toContain("COMMONS_ENABLED");
      expect(t).toContain("notFound()");
    });
  }
});
