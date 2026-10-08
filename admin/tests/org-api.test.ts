import { describe, expect, it } from "vitest";
import { canSeePerPerson, isAdminRole, isOwnerRole, parseList } from "@/lib/org-api";

describe("parseList", () => {
  it("splits lines and commas, trims, de-duplicates, drops blanks", () => {
    expect(parseList("a, b\n b ,, c\n")).toEqual(["a", "b", "c"]);
  });
  it("an empty box is an empty list (which the backend reads as 'allow nothing'), never a wildcard", () => {
    expect(parseList("  \n ")).toEqual([]);
  });
});

describe("role gates", () => {
  it("only owner/admin are admins; only owner is owner", () => {
    expect(isAdminRole("owner") && isAdminRole("admin")).toBe(true);
    expect(isAdminRole("member") || isAdminRole("viewer") || isAdminRole(undefined)).toBe(false);
    expect(isOwnerRole("owner")).toBe(true);
    expect(isOwnerRole("admin")).toBe(false);
  });
});

describe("per-person visibility", () => {
  it("only owner, admin and team lead may see individuals; executives, auditors, members and viewers may not", () => {
    for (const r of ["owner", "admin", "team_lead"] as const) expect(canSeePerPerson(r)).toBe(true);
    for (const r of ["executive", "auditor", "member", "viewer", undefined] as const) expect(canSeePerPerson(r)).toBe(false);
  });
  it("a planned role gains no admin or owner rights by being typed", () => {
    for (const r of ["executive", "auditor", "team_lead"] as const) { expect(isAdminRole(r)).toBe(false); expect(isOwnerRole(r)).toBe(false); }
  });
});
