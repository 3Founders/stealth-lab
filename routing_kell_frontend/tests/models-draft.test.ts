// The address rule for a model on the Models page: https for anything public, http allowed only for this machine (Ollama,
// vLLM). Pure so the rule is testable without the page.
import { describe, expect, it } from "vitest";
import { draftProblem, emptyDraft } from "@/components/console/ModelsPanel";

const ok = { ...emptyDraft(), name: "local-gemma", model_id: "gemma:2b", base: "http://127.0.0.1:11434/v1" };

describe("a model's address", () => {
  it("accepts a local server over http://localhost or 127.0.0.1, on any preset", () => {
    expect(draftProblem({ ...ok, preset: "local" })).toBeNull();
    expect(draftProblem({ ...ok, preset: "local", base: "http://localhost:11434/v1" })).toBeNull();
    expect(draftProblem({ ...ok, preset: "custom", base: "http://localhost:8000/v1", keyEnv: "OLLAMA_KEY" })).toBeNull();
  });

  it("still refuses plain http for anything that is not this machine", () => {
    expect(draftProblem({ ...ok, preset: "custom", base: "http://insecure.example.com/v1", keyEnv: "MY_KEY" })).toMatch(/https/);
  });

  it("a local preset must be on this machine", () => {
    expect(draftProblem({ ...ok, preset: "local", base: "https://api.example.com/v1" })).toMatch(/127\.0\.0\.1 or localhost/);
  });
});
