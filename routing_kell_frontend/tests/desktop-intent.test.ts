import { describe, expect, it } from "vitest";
import { looksLikeDesktopCommand } from "@/lib/chat/desktop-intent";

describe("looksLikeDesktopCommand", () => {
  it("recognises the person's own computer", () => {
    for (const text of [
      "continue in my session",
      "resume where we left off on my laptop",
      "run the tests on my machine",
      "what did my desktop find?",
      "pick up the task on this computer",
      "run the build on my PC",
    ]) {
      expect(looksLikeDesktopCommand(text), text).toBe(true);
    }
  });

  it("does not hijack ordinary questions or tasks", () => {
    for (const text of [
      "what is a desktop app?",
      "explain the desktop build",
      "write a test for the parser",
      "run the tests",
      "summarise this article",
      "how do I install kell?",
    ]) {
      expect(looksLikeDesktopCommand(text), text).toBe(false);
    }
  });
});
