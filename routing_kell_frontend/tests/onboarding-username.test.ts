// The username step of onboarding: the person chooses it (the field starts empty), the rule matches the server's, and the handle preview is what others type.
import { describe, expect, it } from "vitest";
import { handleFor, usernameProblem } from "@/lib/profile-form";

describe("onboarding username", () => {
  it("accepts 3 to 32 letters and numbers starting with a letter", () => {
    for (const ok of ["anuj", "Anuj", "anuj2", "QuietOtter", "a".repeat(32)]) expect(usernameProblem(ok)).toBeNull();
  });
  it("says what is wrong, in words", () => {
    expect(usernameProblem("")).toMatch(/Choose a username/);
    expect(usernameProblem("  ")).toMatch(/Choose a username/);
    expect(usernameProblem("ab")).toMatch(/at least 3/);
    expect(usernameProblem("a".repeat(33))).toMatch(/at most 32/);
    expect(usernameProblem("2fast")).toMatch(/Start with a letter/);
    expect(usernameProblem("anuj b")).toMatch(/letters and numbers only/);
    expect(usernameProblem("anuj_b")).toMatch(/letters and numbers only/);
  });
  it("shows the handle the way the room shows it: lower case, with /kell", () => {
    expect(handleFor("Anuj")).toBe("@anuj/kell");
    expect(handleFor(" SlateSummit ")).toBe("@slatesummit/kell");
  });
});
