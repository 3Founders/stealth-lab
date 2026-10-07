import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { BEGIN, END, INSTRUCTIONS, applyInstructions, cursorRule, removeBlock, targets, upsertBlock } from "../lib/instructions.mjs";

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "sl-instr-"));

test("creates AGENTS.md with only our block", () => {
  const dir = tmp();
  const [a] = applyInstructions(dir);
  assert.equal(a.result, "created");
  const text = fs.readFileSync(path.join(dir, "AGENTS.md"), "utf8");
  assert.ok(text.startsWith(BEGIN) && text.trimEnd().endsWith(END));
  assert.match(text, /find_ways/);
  assert.match(text, /untrusted data/);
  assert.match(text, /report_result/);
});

test("keeps the user's own content and is idempotent", () => {
  const dir = tmp();
  const file = path.join(dir, "AGENTS.md");
  fs.writeFileSync(file, "# My project\n\nUse tabs.\n");
  assert.equal(upsertBlock(file), "updated");
  const once = fs.readFileSync(file, "utf8");
  assert.ok(once.startsWith("# My project\n\nUse tabs.\n"));
  assert.equal(upsertBlock(file), "unchanged");
  assert.equal(fs.readFileSync(file, "utf8"), once);
  assert.equal(once.split(BEGIN).length - 1, 1);
});

test("replaces an older block in place and leaves text on both sides alone", () => {
  const dir = tmp();
  const file = path.join(dir, "AGENTS.md");
  fs.writeFileSync(file, `before\n\n${BEGIN}\nold text\n${END}\n\nafter\n`);
  assert.equal(upsertBlock(file), "updated");
  const text = fs.readFileSync(file, "utf8");
  assert.ok(text.startsWith("before\n\n") && text.includes("\nafter\n"));
  assert.ok(!text.includes("old text") && text.includes("find_ways"));
});

test("remove takes out only our block, and deletes a file that held nothing else", () => {
  const dir = tmp();
  const file = path.join(dir, "AGENTS.md");
  fs.writeFileSync(file, "keep me\n");
  upsertBlock(file);
  assert.equal(removeBlock(file), "removed");
  assert.equal(fs.readFileSync(file, "utf8").trim(), "keep me");
  const only = path.join(dir, "ONLY.md");
  upsertBlock(only);
  assert.equal(removeBlock(only), "deleted");
  assert.ok(!fs.existsSync(only));
  assert.equal(removeBlock(path.join(dir, "missing.md")), "absent");
  fs.writeFileSync(file, "no block here\n");
  assert.equal(removeBlock(file), "absent");
});

test("the Cursor rule is written only for a repo that uses Cursor (or when asked)", () => {
  const plain = tmp();
  assert.equal(targets(plain).length, 1);
  assert.equal(targets(plain, { clients: ["cursor"] }).length, 2);
  const withCursor = tmp();
  fs.mkdirSync(path.join(withCursor, ".cursor"));
  const results = applyInstructions(withCursor);
  assert.deepEqual(results.map((r) => r.result), ["created", "created"]);
  const rule = fs.readFileSync(path.join(withCursor, ".cursor", "rules", "stealthlab.mdc"), "utf8");
  assert.ok(rule.startsWith("---\ndescription:") && rule.includes("alwaysApply: true") && rule.includes("find_ways"));
  assert.equal(rule, cursorRule());
  assert.deepEqual(applyInstructions(withCursor).map((r) => r.result), ["unchanged", "unchanged"]);
});

test("dry run changes nothing; remove undoes both files", () => {
  const dir = tmp();
  fs.mkdirSync(path.join(dir, ".cursor"));
  assert.deepEqual(applyInstructions(dir, { dryRun: true }).map((r) => r.result), ["would create", "would create"]);
  assert.ok(!fs.existsSync(path.join(dir, "AGENTS.md")));
  applyInstructions(dir);
  assert.deepEqual(applyInstructions(dir, { remove: true }).map((r) => r.result), ["deleted", "deleted"]);
  assert.ok(!fs.existsSync(path.join(dir, "AGENTS.md")) && !fs.existsSync(path.join(dir, ".cursor", "rules", "stealthlab.mdc")));
});

test("the text is short enough to carry in every request", () => {
  assert.ok(INSTRUCTIONS.length < 1800, `${INSTRUCTIONS.length} chars`);
});
