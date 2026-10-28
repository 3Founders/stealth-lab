import { describe, expect, it } from "vitest";
import { parseAdminUrl } from "@/lib/admin-link";

describe("admin console link", () => {
  it("is absent unless the deployment sets an address", () => {
    expect(parseAdminUrl(undefined)).toBeNull();
    expect(parseAdminUrl("")).toBeNull();
    expect(parseAdminUrl("   ")).toBeNull();
  });
  it("accepts https, and http only on this machine", () => {
    expect(parseAdminUrl("https://admin.example.com")).toBe("https://admin.example.com");
    expect(parseAdminUrl("http://localhost:3200")).toBe("http://localhost:3200");
    expect(parseAdminUrl("http://127.0.0.1:3200/")).toBe("http://127.0.0.1:3200");
    expect(parseAdminUrl("http://admin.example.com")).toBeNull();
  });
  it("keeps only the origin, and refuses anything that is not a plain address", () => {
    expect(parseAdminUrl("https://admin.example.com/some/path?x=1#y")).toBe("https://admin.example.com");
    expect(parseAdminUrl("https://user:pw@admin.example.com")).toBeNull();
    expect(parseAdminUrl("javascript:alert(1)")).toBeNull();
    expect(parseAdminUrl("not a url")).toBeNull();
  });
});
