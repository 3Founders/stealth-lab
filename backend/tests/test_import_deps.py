"""
Real tests for app/services/import_deps.py -- against real temp
directory fixtures with real files, same convention as
test_related_tests.py and call_graph.py's own tests.
"""
from pathlib import Path

from app.services.import_deps import import_targets, import_targets_for_many


def _touch(root: Path, rel_path: str, content: str = "") -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def test_python_dotted_absolute_import_resolves_to_real_file(tmp_path):
    _touch(tmp_path, "app/services/foo.py", "x = 1\n")
    _touch(tmp_path, "app/services/bar.py", "from app.services.foo import x\n")
    found = import_targets(str(tmp_path), "app/services/bar.py")
    assert "app/services/foo.py" in found


def test_python_plain_import_of_dotted_module_resolves(tmp_path):
    _touch(tmp_path, "pkg/sub.py", "y = 1\n")
    _touch(tmp_path, "pkg/__init__.py", "")
    _touch(tmp_path, "consumer.py", "import pkg.sub\n")
    found = import_targets(str(tmp_path), "consumer.py")
    assert "pkg/sub.py" in found


def test_python_relative_import_with_module_name_resolves(tmp_path):
    _touch(tmp_path, "app/pkg/target.py", "z = 1\n")
    _touch(tmp_path, "app/consumer.py", "from .pkg.target import z\n")
    found = import_targets(str(tmp_path), "app/consumer.py")
    assert "app/pkg/target.py" in found


def test_python_from_dot_import_name_resolves_to_the_named_submodule(tmp_path):
    """The real fix caught this session: `from . import foo` is a
    different shape from `from .foo import x` -- the dots alone carry no
    module name, and the real target is the NAME after 'import'
    (combined with the dot prefix), not the literal dots. Confirmed
    broken (resolved to a useless '.') before this test existed, then
    fixed."""
    _touch(tmp_path, "app/services/foo.py", "x = 1\n")
    _touch(tmp_path, "app/services/consumer.py", "from . import foo\n")
    found = import_targets(str(tmp_path), "app/services/consumer.py")
    assert "app/services/foo.py" in found
    assert "." not in found, "the raw dot-only target must never be returned as a useless literal '.'"


def test_python_double_dot_relative_import_resolves_one_level_up(tmp_path):
    _touch(tmp_path, "app/sibling.py", "w = 1\n")
    _touch(tmp_path, "app/services/consumer.py", "from .. import sibling\n")
    found = import_targets(str(tmp_path), "app/services/consumer.py")
    assert "app/sibling.py" in found


def test_python_import_init_package_resolves_to_init_file(tmp_path):
    _touch(tmp_path, "pkg/__init__.py", "")
    _touch(tmp_path, "consumer.py", "import pkg\n")
    found = import_targets(str(tmp_path), "consumer.py")
    assert "pkg/__init__.py" in found


def test_python_unresolvable_import_returned_as_raw_string_not_dropped(tmp_path):
    """A real stdlib/third-party import ('os') has no corresponding file
    in the checkout -- must still be returned (as a raw, honestly
    unresolved candidate), never silently dropped."""
    _touch(tmp_path, "consumer.py", "import os\n")
    found = import_targets(str(tmp_path), "consumer.py")
    assert "os" in found


def test_javascript_relative_import_resolves_to_real_file(tmp_path):
    _touch(tmp_path, "src/foo.js", "export const x = 1;\n")
    _touch(tmp_path, "src/bar.js", 'import x from "./foo";\n')
    found = import_targets(str(tmp_path), "src/bar.js")
    assert "src/foo.js" in found


def test_javascript_relative_import_resolves_via_index_file(tmp_path):
    _touch(tmp_path, "src/utils/index.js", "export const x = 1;\n")
    _touch(tmp_path, "src/consumer.js", 'import x from "./utils";\n')
    found = import_targets(str(tmp_path), "src/consumer.js")
    assert "src/utils/index.js" in found


def test_typescript_relative_import_resolves(tmp_path):
    _touch(tmp_path, "src/service.ts", "export const x = 1;\n")
    _touch(tmp_path, "src/consumer.ts", 'import { x } from "./service";\n')
    found = import_targets(str(tmp_path), "src/consumer.ts")
    assert "src/service.ts" in found


def test_javascript_bare_import_returned_unresolved_not_dropped(tmp_path):
    """A bare specifier ('lodash') needs node_modules resolution this
    module honestly doesn't implement -- must still be returned as a
    raw string, not silently dropped."""
    _touch(tmp_path, "src/consumer.js", 'import _ from "lodash";\n')
    found = import_targets(str(tmp_path), "src/consumer.js")
    assert "lodash" in found


def test_javascript_commonjs_require_is_also_extracted(tmp_path):
    _touch(tmp_path, "src/foo.js", "module.exports = 1;\n")
    _touch(tmp_path, "src/bar.js", 'const foo = require("./foo");\n')
    found = import_targets(str(tmp_path), "src/bar.js")
    assert "src/foo.js" in found


def test_go_import_returned_as_raw_string_unresolved(tmp_path):
    """Go module-path resolution needs go.mod, which this module
    honestly doesn't parse -- confirmed returned as a raw string, not
    silently dropped and not incorrectly resolved to a wrong path."""
    _touch(tmp_path, "main.go", 'package main\nimport "myapp/pkg/util"\n')
    found = import_targets(str(tmp_path), "main.go")
    assert "myapp/pkg/util" in found


def test_unrecognized_language_returns_empty_not_an_error(tmp_path):
    _touch(tmp_path, "README.md", "# hello\n")
    found = import_targets(str(tmp_path), "README.md")
    assert found == []


def test_nonexistent_file_returns_empty_not_an_error(tmp_path):
    found = import_targets(str(tmp_path), "does/not/exist.py")
    assert found == []


def test_from_future_import_is_captured_not_silently_missed(tmp_path):
    """Real finding, caught by testing against this repo's own real
    files (applicability.py uses `from __future__ import annotations`
    and it was silently absent from an earlier version's output):
    `from __future__ import X` is its OWN distinct tree-sitter grammar
    node (future_import_statement), not import_from_statement -- an
    earlier version of _python_import_targets only handled the latter.
    Confirmed via direct tree-sitter inspection before fixing, not
    assumed."""
    _touch(tmp_path, "consumer.py", "from __future__ import annotations\nimport os\n")
    found = import_targets(str(tmp_path), "consumer.py")
    assert "__future__" in found
    assert "os" in found


def test_import_targets_for_many_unions_and_deduplicates(tmp_path):
    _touch(tmp_path, "shared.py", "s = 1\n")
    _touch(tmp_path, "a.py", "import shared\n")
    _touch(tmp_path, "b.py", "import shared\n")
    found = import_targets_for_many(str(tmp_path), ["a.py", "b.py"])
    assert found.count("shared.py") == 1
