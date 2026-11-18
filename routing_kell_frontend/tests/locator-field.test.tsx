// @vitest-environment jsdom
// A step's locator: which kind it reads as, and the refusals the person sees while typing instead of after submitting.
// The rules mirror what the server does (submissions/review.py), so a step that passes here cannot fail there for these
// reasons.
import { describe, expect, it } from "vitest";
import { kindOf, locatorProblem } from "@/components/submit/LocatorField";

describe("a step's locator", () => {
  it("reads the kind from what is written, so a reopened step lands on the right tab", () => {
    expect(kindOf("https://example.com/page")).toBe("url");
    expect(kindOf("https://raw.githubusercontent.com/me/repo/main/tool.py")).toBe("script");
    expect(kindOf("https://example.com/tool.sh?v=2")).toBe("script");       // a query string does not hide the extension
    expect(kindOf("linear/create-issue")).toBe("tool");
    expect(kindOf("")).toBe("url");                                         // empty starts on the ordinary one
  });

  it("says why a script locator cannot work, in the reviewer's own words", () => {
    // kell cannot open the person's disk: review.py:157 refuses a non-URL
    expect(locatorProblem("script", "C:\\Users\\me\\tool.py")).toMatch(/cannot open a file on your computer/);
    expect(locatorProblem("script", "/home/me/tool.py")).toMatch(/cannot open a file on your computer/);
    expect(locatorProblem("script", "~/tool.py")).toMatch(/cannot open a file on your computer/);
    // a relative path is not a link either
    expect(locatorProblem("script", "./tool.py")).toMatch(/must be a link/);
    // only what the sandbox can run: review.py:160 / sandbox.py RUNTIMES
    expect(locatorProblem("script", "https://example.com/tool.rb")).toMatch(/Python \(\.py\), shell \(\.sh\) or JavaScript \(\.js\)/);
    expect(locatorProblem("script", "https://example.com/tool.py")).toBe("");
    expect(locatorProblem("script", "https://example.com/tool.sh")).toBe("");
    expect(locatorProblem("script", "https://example.com/tool.js")).toBe("");
  });

  it("refuses a local path on a plain link too, and says nothing about a value still being typed", () => {
    expect(locatorProblem("url", "C:\\Users\\me\\page.html")).toMatch(/cannot open a file on your computer/);
    expect(locatorProblem("url", "https://example.com")).toBe("");
    expect(locatorProblem("tool", "linear/create-issue")).toBe("");
    expect(locatorProblem("script", "")).toBe("");          // nothing typed yet is not an error
  });
});
