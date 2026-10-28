import { test } from "node:test";
import assert from "node:assert/strict";
import { baselineFails, collectionErrorsAsFailures } from "../lib/dispatch.mjs";

test("baseline: old failures count as old even in the file the task names; a test the task names itself must still pass", () => {
  const out = "FAILED t/test_util.py::test_old - boom\nFAILED t/test_util.py::test_new_bug[x]\nERROR t/test_broken.py\n";
  assert.deepEqual(baselineFails(1, out, "add a test in t/test_util.py; make test_new_bug pass"), ["t/test_util.py::test_old", "t/test_broken.py"]);
  assert.deepEqual(baselineFails(1, out, "fix the import error in t/test_broken.py"), ["t/test_util.py::test_old", "t/test_util.py::test_new_bug[x]"]);
  assert.deepEqual(baselineFails(2, out, ""), [], "an interrupted run says nothing reliable about which tests fail");
});

test("a pytest check keeps going past a test file that cannot be imported; other checks are left as they are", () => {
  assert.equal(collectionErrorsAsFailures("python -m pytest -q"), "python -m pytest --continue-on-collection-errors -q");
  assert.equal(collectionErrorsAsFailures("pytest"), "pytest --continue-on-collection-errors");
  assert.equal(collectionErrorsAsFailures("python -m pytest --continue-on-collection-errors -q"), "python -m pytest --continue-on-collection-errors -q");
  assert.equal(collectionErrorsAsFailures("npm test"), "npm test");
  assert.equal(collectionErrorsAsFailures("pytest-xdist-run"), "pytest-xdist-run");
  assert.equal(collectionErrorsAsFailures(null), null);
});
