import { describe, expect, it } from "vitest";
import { fileKind, fileToken, formatSize, splitFiles, whyNotFile, MAX_ROOM_FILE_BYTES } from "@/lib/chat/files";

const F = { id: "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b", name: "Q3 report.pdf", size: 1_234_567, mime: "application/pdf" };

describe("room files", () => {
  it("a file token round-trips, and the message text keeps its words", () => {
    const text = `Here is the report\n${fileToken(F)}`;
    const out = splitFiles(text);
    expect(out.text).toBe("Here is the report");
    expect(out.files).toEqual([F]);
  });
  it("text with no token is returned untouched", () => {
    expect(splitFiles("hello [[not a file]]")).toEqual({ text: "hello [[not a file]]", files: [] });
  });
  it("two files keep their order, and characters that would break the token are replaced", () => {
    const a = { ...F, name: "a|b]c.pdf" };
    const out = splitFiles(`${fileToken(a)}\n${fileToken({ ...F, id: "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c", name: "two.txt", mime: "text/plain" })}`);
    expect(out.files.map((f) => f.name)).toEqual(["a_b_c.pdf", "two.txt"]);
  });
  it("sizes and kinds read like a chat app", () => {
    expect(formatSize(900)).toBe("900 B");
    expect(formatSize(2048)).toBe("2 KB");
    expect(formatSize(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(fileKind(F)).toBe("PDF");
    expect(fileKind({ name: "plan.xlsx", mime: "x" })).toBe("XLS");
    expect(fileKind({ name: "noext", mime: "" })).toBe("FILE");
  });
  it("refuses programs, empty and oversize files before any upload", () => {
    expect(whyNotFile({ name: "setup.exe", size: 10 }, 0)).toMatch(/Programs/);
    expect(whyNotFile({ name: "a.txt", size: 0 }, 0)).toMatch(/empty/);
    expect(whyNotFile({ name: "a.bin", size: MAX_ROOM_FILE_BYTES + 1 }, 0)).toMatch(/over/);
    expect(whyNotFile({ name: "a.txt", size: 5 }, 6)).toMatch(/At most/);
    expect(whyNotFile({ name: "a.txt", size: 5 }, 0)).toBeNull();
  });
});
