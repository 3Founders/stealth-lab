

=== TASK PyCQA__flake8-bugbear-287 | repo PyCQA/flake8-bugbear | python
--- Issue
Proposed check: don't allow argument unpacking in a call after a keyword argument
Imagine a function like

```py
def foo(bar, baz):
    pass
```

The following call is a syntactically incorrect:

```py
foo(baz="baz", "bar")
```

However, with unpacking the following is allowed:

```py
foo(baz="baz", *["bar"])
```

[However](https://github.com/python/cpython/issues/82741#issuecomment-1093845041),

> [T]he resulting argument binding is awkward, and almost never does what you want/expect it to

It was [not ruled a bug or a behavior that can or should be deprecated](https://github.com/python/cpython/issues/82741#issuecomment-1093845049). Still, I think in most cases this is probably an oversight and should not be used.

If we want this check, I would be willing to contribute a patch.
--- REAL FIX (ground truth)
diff --git a/README.rst b/README.rst
index d7981f7..b7d4f38 100644
--- a/README.rst
+++ b/README.rst
@@ -160,6 +160,13 @@ the loop, because `late-binding closures are a classic gotcha
 This check identifies exception types that are specified in multiple ``except``
 clauses. The first specification is the only one ever considered, so all others can be removed.
 
+**B026**: Star-arg unpacking after a keyword argument is strongly discouraged, because
+it only works when the keyword parameter is declared after all parameters supplied by
+the unpacked sequence, and this change of ordering can surprise and mislead readers.
+There was `cpython discussion of disallowing this syntax
+<https://github.com/python/cpython/issues/82741>`_, but legacy usage and parser
+limitations make it difficult.
+
 Opinionated warnings
 ~~~~~~~~~~~~~~~~~~~~
 
diff --git a/bugbear.py b/bugbear.py
index 4b742b7..cc92b92 100644
--- a/bugbear.py
+++ b/bugbear.py
@@ -354,6 +354,8 @@ class BugBearVisitor(ast.NodeVisitor):
                 ):
                     self.errors.append(B010(node.lineno, node.col_offset))
 
+            self.check_for_b026(node)
+
         self.generic_visit(node)
 
     def visit_Assign(self, node):
@@ -641,6 +643,22 @@ class BugBearVisitor(ast.NodeVisitor):
 
         self.errors.append(B024(node.lineno, node.col_offset, vars=(node.name,)))
 
+    def check_for_b026(self, call: ast.Call):
+        if not call.keywords:
+            return
+
+        starreds = [arg for arg in call.args if isinstance(arg, ast.Starred)]
+        if not starreds:
+            return
+
+        first_keyword = call.keywords[0].value
+        for starred in starreds:
+            if (starred.lineno, starred.col_offset) > (
+                first_keyword.lineno,
+                first_keyword.col_offset,
+            ):
+                self.errors.append(B026(starred.lineno, starred.col_offset))
+
     def _get_assigned_names(self, loop_node):
         loop_targets = (ast.For, ast.AsyncFor, ast.comprehension)
         for node in children_in_scope(loop_node):
@@ -1203,6 +1221,14 @@ B025 = Error(
         " will be considered and all other except catches can be safely removed."
     )
 )
+B026 = Error(
+    message=(
+        "B026 Star-arg unpacking after a keyword argument is strongly discouraged, "
+        "because it only works when the keyword parameter is declared after all "
+        "parameters supplied by the unpacked sequence, and this change of ordering can "
+        "surprise and mislead readers."
+    )
+)
 
 # Warnings disabled by default.
 B901 = Error(

--- Proposal A
## Root cause
`flake8-bugbear` does not currently check for the use of a starred argument (`*args`) **after** a keyword argument in a function call.  
Python’s grammar permits this (`foo(baz=1, *args)`), but the resulting argument binding is confusing and is considered an anti‑pattern. Because the AST separates positional (`node.args`) and keyword (`node.keywords`) arguments, the existing checks never see the ordering that makes the pattern problematic, so no warning is emitted.

## Fix
Add a new rule **B041** that flags any call where a `Starred` argument appears *after* a normal keyword argument.

```diff
diff --git a/bugbear.py b/bugbear.py
index 5fbfa19..e3c9d7a 100644
--- a/bugbear.py
+++ b/bugbear.py
@@
 import ast
 import sys
@@
 from . import __version__
@@
 class Error(NamedTuple):
@@
     message: str
@@
     code: str
@@
 
 # ----------------------------------------------------------------------
 # Error definitions
 # ----------------------------------------------------------------------
@@
 B040 = Error(
     message=(
         "B040: use of `assert` on a tuple, which is always truthy; "
         "did you mean `assert a == b`?"
     ),
     code="B040",
 )
+
+# ----------------------------------------------------------------------
+# B041 – argument unpacking after a keyword argument
+# ----------------------------------------------------------------------
+
+B041 = Error(
+    message=(
+        "B041: argument unpacking (`*args`) after a keyword argument is confusing "
+        "and should be avoided"
+    ),
+    code="B041",
+)
@@
 class BugBearVisitor(ast.NodeVisitor):
@@
     def visit_Call(self, node):
         self.check_for_b001(node)
         self.check_for_b002(node)
@@
         self.check_for_b040(node)
+        self.check_for_b041(node)          # ← new check
         self.generic_visit(node)
@@
         self.check_for_b040(node)
         self.generic_visit(node)
 
+    # ------------------------------------------------------------------
+    # B041 – argument unpacking after a keyword argument
+    # ------------------------------------------------------------------
+    def check_for_b041(self, node: ast.Call) -> None:
+        """
+        Detect ``*args`` that appear after a keyword argument in a call.
+
+        The AST stores positional arguments (including ``*args``) in ``node.args``
+        and keyword arguments in ``node.keywords``.  Because the order between
+        the two lists is lost, we compare the source locations (lineno/col_offset)
+        of each ``Starred`` node with the locations of the normal keyword nodes.
+        If any starred argument is positioned *after* a keyword argument we emit
+        B041.
+        """
+        # No starred args or no keyword args → nothing to check
+        if not node.args or not node.keywords:
+            return
+
+        # Gather normal keyword nodes (ignore ``**kwargs`` which have arg=None)
+        keyword_nodes = [kw for kw in node.keywords if kw.arg is not None]
+        if not keyword_nodes:
+            return
+
+        # For each starred argument, see if it appears after any keyword
+        for arg in node.args:
+            if isinstance(arg, ast.Starred):
+                # ``arg`` is a ``*something`` expression
+                for kw in keyword_nodes:
+                    # Compare source positions – the AST guarantees lineno/col_offset
+                    # are set for both nodes.
+                    if (kw.lineno < arg.lineno) or (
+ 
--- Proposal B
## Root cause
`flake8-bugbear` does not currently check for the pattern where a starred argument (`*args`) appears **after** a keyword argument in a function call.  
Python’s grammar allows this (`foo(baz="baz", *["bar"])`) but the resulting argument binding is confusing and almost never what the programmer intends. Because the AST separates positional (`node.args`) and keyword (`node.keywords`) arguments, the existing checks never see the relative ordering and therefore never flag this situation.

## Fix
Add a new check **B904** that walks `ast.Call` nodes, finds any normal keyword argument (`kw.arg is not None`) and then looks for a `ast.Starred` argument whose source position comes *after* that keyword. When such a pattern is found, report a `B904` warning.

```diff
--- a/flake8_bugbear/checks.py
+++ b/flake8_bugbear/checks.py
@@
 from __future__ import annotations
@@
 import ast
 import sys
@@
 from . import utils
@@
 __all__ = [
@@
     "B902",
     "B903",
+    "B904",          # new check
 ]
@@
 class B902(Check):
@@
     pass
@@
 class B903(Check):
@@
     pass
+
+# ----------------------------------------------------------------------
+# B904: argument unpacking after a keyword argument
+# ----------------------------------------------------------------------
+
+class B904(Check):
+    """
+    Detect calls where a starred argument (``*args``) appears after a
+    keyword argument.  This construct is syntactically valid but
+    produces confusing argument binding and is almost never what the
+    author intended.
+
+    Example::
+
+        foo(baz=\"baz\", *[\"bar\"])   # <-- flagged
+    """
+
+    #: The error code and message used by flake8.
+    #: The message follows the style of other bugbear checks.
+    #: ``B904`` is the next free code in the series.
+    name = "bugbear"
+    version = "2.1.0"
+    #: This check is enabled by default.
+    off_by_default = False
+
+    def visit_Call(self, node: ast.Call) -> None:
+        """
+        Visit a ``Call`` node and emit a warning if a ``Starred`` argument
+        appears after a normal keyword argument.
+        """
+        # Gather normal keyword arguments (kw.arg is not None)
+        keyword_positions = [
+            (kw.lineno, kw.col_offset)
+            for kw in node.keywords
+            if kw.arg is not None
+        ]
+        if not keyword_positions:
+            # No keyword arguments → nothing to check.
+            return
+
+        # Determine the earliest position of any keyword argument.
+        # We need the *latest* keyword position that precedes a starred arg,
+        # but the simplest safe test is: if a starred arg's position is
+        # greater than *any* keyword position, we flag it.
+        for arg in node.args:
+            if isinstance(arg, ast.Starred):
+                # ``arg`` may be on a different line than the keyword.
+                # Compare line first, then column for the same line.
+                for kw_line, kw_col in keyword_positions:
+                    if (arg.lineno > kw_line) or (
+                        arg.lineno == kw_line and arg.col_offset > kw_col
+                    ):
+                        self.error_from_node(
+                            arg,
+                            "B904 argument unpacking after keyword argument is confusing",
+                        )
+                        # One warning per starred argument is enough.
+                        break
+
+        # Continue generic traversa

=== TASK dandi__dandi-cli-216 | repo dandi/dandi-cli | python
--- Issue
remove POSIX filenames assumptions in the tests
This is a follow up to https://github.com/dandi/dandi-cli/pull/210
where I did that in `test_upload`.  So the goal is to check other tests and provide the same (or similar?) changes in other test files.
--- REAL FIX (ground truth)
diff --git a/dandi/cli/cmd_ls.py b/dandi/cli/cmd_ls.py
index 4d2c2c95..89c50d5c 100644
--- a/dandi/cli/cmd_ls.py
+++ b/dandi/cli/cmd_ls.py
@@ -68,7 +68,7 @@ def ls(paths, fields=None, format="auto", recursive=False):
 
     # For now we support only individual files
     if recursive:
-        files = list(find_files(".nwb$", paths))
+        files = list(find_files(r"\.nwb\Z", paths))
     else:
         files = paths
 
diff --git a/dandi/cli/cmd_organize.py b/dandi/cli/cmd_organize.py
index 9b0c0037..06e07ac2 100644
--- a/dandi/cli/cmd_organize.py
+++ b/dandi/cli/cmd_organize.py
@@ -151,7 +151,7 @@ def organize(
         # Our dumps of metadata
         metadata = load_jsonl(paths[0])
     else:
-        paths = list(find_files(r"\.nwb$", paths=paths))
+        paths = list(find_files(r"\.nwb\Z", paths=paths))
         lgr.info("Loading metadata from %d files", len(paths))
         # Done here so we could still reuse cached 'get_metadata'
         # without having two types of invocation and to guard against
diff --git a/dandi/cli/command.py b/dandi/cli/command.py
index 71cc8261..28ee656c 100644
--- a/dandi/cli/command.py
+++ b/dandi/cli/command.py
@@ -106,9 +106,7 @@ from .cmd_download import download  # noqa: E402
 from .cmd_validate import validate  # noqa: E402
 from .cmd_register import register  # noqa: E402
 
-main.add_command(ls)
-main.add_command(organize)
-main.add_command(upload)
-main.add_command(download)
-main.add_command(validate)
-main.add_command(register)
+__all_commands__ = (ls, organize, upload, download, validate, register)
+
+for cmd in __all_commands__:
+    main.add_command(cmd)
diff --git a/dandi/utils.py b/dandi/utils.py
index d0e0e317..baa8cde7 100644
--- a/dandi/utils.py
+++ b/dandi/utils.py
@@ -360,7 +360,8 @@ def move_file(src, dst):
 def find_dandi_files(paths):
     """Adapter to find_files to find files of interest to dandi project
     """
-    yield from find_files(r"(dandiset\.yaml|\.nwb)$", paths)
+    sep = re.escape(os.sep)
+    yield from find_files(rf"((^|{sep})dandiset\.yaml|\.nwb)\Z", paths)
 
 
 def find_parent_directory_containing(filename, path=None):

--- Proposal A
## Root cause
Several test modules construct file paths using hard‑coded POSIX separators (e.g. `"tmp/file.txt"`).  
On Windows these paths are invalid, causing the test suite to fail. The fix is to build all temporary file paths with `os.path.join` (or `pathlib.Path`) so they are platform‑independent.

## Fix
```diff
--- a/tests/test_download.py
+++ b/tests/test_download.py
@@
-import tempfile
+import os
+import tempfile
@@
-    tmp_dir = tempfile.mkdtemp()
-    filename = "tmp/file.txt"
-    full_path = os.path.join(tmp_dir, filename)
+    tmp_dir = tempfile.mkdtemp()
+    # Build a platform‑independent relative path
+    filename = os.path.join("tmp", "file.txt")
+    full_path = os.path.join(tmp_dir, filename)
@@
-    with open("tmp/file.txt", "w") as f:
+    # Use a platform‑independent path for the temporary file
+    with open(os.path.join(tmp_dir, "tmp", "file.txt"), "w") as f:
         f.write("test")
@@
-    assert os.path.exists("tmp/file.txt")
+    assert os.path.exists(os.path.join(tmp_dir, "tmp", "file.txt"))
*** End of File
--- a/tests/test_cli.py
+++ b/tests/test_cli.py
@@
-import tempfile
+import os
+import tempfile
@@
-    workdir = tempfile.mkdtemp()
-    filename = "tmp/file.txt"
-    path = os.path.join(workdir, filename)
+    workdir = tempfile.mkdtemp()
+    # Build a platform‑independent relative path
+    filename = os.path.join("tmp", "file.txt")
+    path = os.path.join(workdir, filename)
@@
-    with open("tmp/file.txt", "w") as f:
+    # Use a platform‑independent path for the temporary file
+    with open(os.path.join(workdir, "tmp", "file.txt"), "w") as f:
         f.write("dummy")
@@
-    assert os.path.isfile("tmp/file.txt")
+    assert os.path.isfile(os.path.join(workdir, "tmp", "file.txt"))
*** End of File
--- a/tests/test_upload.py
+++ b/tests/test_upload.py
@@
-import tempfile
+import os
+import tempfile
@@
-    tmp_dir = tempfile.mkdtemp()
-    filename = "tmp/file.txt"
-    full_path = os.path.join(tmp_dir, filename)
+    tmp_dir = tempfile.mkdtemp()
+    # Build a platform‑independent relative path
+    filename = os.path.join("tmp", "file.txt")
+    full_path = os.path.join(tmp_dir, filename)
@@
-    with open("tmp/file.txt", "w") as f:
+    # Use a platform‑independent path for the temporary file
+    with open(os.path.join(tmp_dir, "tmp", "file.txt"), "w") as f:
         f.write("test")
@@
-    assert os.path.exists("tmp/file.txt")
+    assert os.path.exists(os.path.join(tmp_dir, "tmp", "file.txt"))
*** End of File
```

**Explanation of changes**

* Added `import os` to each affected test file.
* Replaced every hard‑coded POSIX path (`"tmp/file.txt"`) with a path built via `os.path.join`, ensuring the correct separator on Windows, macOS, and Linux.
* Updated all file‑creation and existence‑check statements to use the new platform‑independent paths.
* Kept the original test logic intact; only the path handling was altered.

## How to verify
1. **Run the full test suite on a non‑POSIX platform** (e.g., Windows) and on a POSIX platform (Linux/macOS) to ensure no platform‑specific failures:
   ```bash
   python -m pip install -e .[test]
   pytest -q
   ```
2. The suite should pass on all platforms without any `FileNotFoundError` or path‑related assertion failures.
3. Optionally, add a quick sanity check:
   ```python
   import os, tempfile
   tmp = tempfile.mkdtemp()
   p = os.path.join(tmp, "tmp", "file.txt")
   os.makedirs(os.path.dirname(p), exist_ok=True)
   with open(p, "w") as f:
       f.wri
--- Proposal B
## Root cause
Several test modules built file‑paths by concatenating strings with the POSIX separator “/”.  
That works on Unix‑like systems but fails on Windows where the native separator is “\\”.  
The tests therefore raise `AssertionError` or `FileNotFoundError` on non‑POSIX platforms.  
The fix is to stop using hard‑coded “/” in the tests and build paths with the
cross‑platform helpers provided by the standard library (`os.path.join` or
`pathlib.Path`).

## Fix
```diff
diff --git a/tests/test_download.py b/tests/test_download.py
index 3b1c9e7..e8f2a4d 100644
--- a/tests/test_download.py
+++ b/tests/test_download.py
@@
-import os
-from pathlib import Path
+import os
+from pathlib import Path
@@
-    # Create a dummy asset hierarchy using POSIX style paths
-    (tmp_path / "subdir/file1.txt").write_text("content1")
-    (tmp_path / "subdir/file2.txt").write_text("content2")
-    (tmp_path / "subdir/nested/file3.txt").write_text("content3")
+    # Create a dummy asset hierarchy using OS‑independent paths
+    (tmp_path / Path(*["subdir", "file1.txt"])).write_text("content1")
+    (tmp_path / Path(*["subdir", "file2.txt"])).write_text("content2")
+    (tmp_path / Path(*["subdir", "nested", "file3.txt"])).write_text("content3")
@@
-    expected_files = {
-        "subdir/file1.txt",
-        "subdir/file2.txt",
-        "subdir/nested/file3.txt",
-    }
+    # Build the expected set using the native separator
+    expected_files = {
+        os.path.join("subdir", "file1.txt"),
+        os.path.join("subdir", "file2.txt"),
+        os.path.join("subdir", "nested", "file3.txt"),
+    }
@@
-    for root, _, files in os.walk(download_dir):
-        for f in files:
-            rel_path = os.path.relpath(os.path.join(root, f), download_dir).replace(os.sep, "/")
-            assert rel_path in expected_files
+    for root, _, files in os.walk(download_dir):
+        for f in files:
+            rel_path = os.path.relpath(os.path.join(root, f), download_dir)
+            assert rel_path in expected_files
```

```diff
diff --git a/tests/test_cli.py b/tests/test_cli.py
index a7d4c2f..b3e9d1a 100644
--- a/tests/test_cli.py
+++ b/tests/test_cli.py
@@
-import os
-from pathlib import Path
+import os
+from pathlib import Path
@@
-    # Prepare a temporary directory with a POSIX‑style layout
-    (tmp_path / "data/file1.nwb").write_text("dummy")
-    (tmp_path / "data/file2.nwb").write_text("dummy")
+    # Prepare a temporary directory with an OS‑independent layout
+    (tmp_path / Path(*["data", "file1.nwb"])).write_text("dummy")
+    (tmp_path / Path(*["data", "file2.nwb"])).write_text("dummy")
@@
-    expected = {"data/file1.nwb", "data/file2.nwb"}
+    expected = {
+        os.path.join("data", "file1.nwb"),
+        os.path.join("data", "file2.nwb"),
+    }
@@
-    for root, _, files in os.walk(output_dir):
-        for f in files:
-            rel = os.path.relpath(os.path.join(root, f), output_dir).replace(os.sep, "/")
-            assert rel in expected
+    for root, _, files in os.walk(output_dir):
+        for f in files:
+            rel = os.path.relpath(os.path.join(root, f), output_dir)
+            assert rel in expected
```

### What the changes do
* Replace all hard‑coded POSIX path strings with `os.path.join` (or `Path` construction) so the native separator is used automatically.
* When creating files in the temporary test directories, build the sub‑paths with `Path(*parts)` instead of a literal `"subdir/file.txt"`.
* When comparing

=== TASK datosgobar__pydatajson-137 | repo datosgobar/pydatajson | python
--- Issue
Función push theme_to_ckan()
**Contexto**

Por el momento, la función `push_dataset_to_ckan()` federa los datasets sin tener en cuenta los `themes` presentes en el catálogo de destino. La operación se concreta, pero cualquier `theme` del dataset que no esté presente en el catálogo es ignorado.

El objetivo es implementar una función `push_theme_to_ckan()`, que mediante la API, cree los grupos de CKAN indicados. Esta función será luego utilizada por si sola o en conjunto con los wrappers para generar los temas pertinentes de un catalogo.
--- REAL FIX (ground truth)
diff --git a/docs/MANUAL.md b/docs/MANUAL.md
index cd72809..914f87c 100644
--- a/docs/MANUAL.md
+++ b/docs/MANUAL.md
@@ -107,7 +107,7 @@ Toma los siguientes parámetros:
   mantener una consistencia más estricta dentro del catálogo a federar, es necesario validar los datos antes de pasarlos
   a la función. 
 
-- **pydatajson.DataJson.remove_dataset_from_ckan()**: Hace un borrado físico de un dataset en un portal de CKAN.
+- **pydatajson.federation.remove_dataset_from_ckan()**: Hace un borrado físico de un dataset en un portal de CKAN.
 Toma los siguientes parámetros:
     - **portal_url**: La URL del portal CKAN. Debe implementar la funcionalidad de `/data.json`.
     - **apikey**: La apikey de un usuario con los permisos que le permitan borrar el dataset.
@@ -121,6 +121,16 @@ Toma los siguientes parámetros:
     En caso de pasar más de un parámetro opcional, la función `remove_dataset_from_ckan()` borra aquellos datasets que
     cumplan con todas las condiciones.
 
+- **pydatajson.DataJson.push_theme_to_ckan()**: Crea un tema en el portal de destino
+Toma los siguientes parámetros:
+    - **portal_url**: La URL del portal CKAN. Debe implementar la funcionalidad de `/data.json`.
+    - **apikey**: La apikey de un usuario con los permisos que le permitan borrar el dataset.
+    - **identifier** (opcional, default: None): Id del `theme` que se quiere federar, en el catálogo de origen.
+    - **label** (opcional, default: None): label del `theme` que se quiere federar, en el catálogo de origen.
+
+    Debe pasarse por lo menos uno de los 2 parámetros opcionales. En caso de que se provean los 2, se prioriza el
+    identifier sobre el label.
+
 ## Uso
     
 ### Setup
diff --git a/pydatajson/ckan_utils.py b/pydatajson/ckan_utils.py
index 9724f44..9f68692 100644
--- a/pydatajson/ckan_utils.py
+++ b/pydatajson/ckan_utils.py
@@ -2,7 +2,6 @@
 # -*- coding: utf-8 -*-
 import json
 import re
-import sys
 from datetime import time
 from dateutil import parser, tz
 from .helpers import title_to_name
@@ -109,10 +108,21 @@ def map_distributions_to_resources(distributions, catalog_id=None):
         resource['mimetype'] = distribution.get('mediaType')
         resource['size'] = distribution.get('byteSize')
         resource['accessURL'] = distribution.get('accessURL')
-        resource['fileName'] = distribution.get('fileName')
+        fileName = distribution.get('fileName')
+        if fileName:
+            resource['fileName'] = fileName
         dist_fields = distribution.get('field')
         if dist_fields:
             resource['attributesDescription'] = json.dumps(dist_fields)
         resources.append(resource)
 
     return resources
+
+
+def map_theme_to_group(theme):
+
+    return {
+        "name": title_to_name(theme.get('id') or theme['label']),
+        "title": theme.get('label'),
+        "description": theme.get('description'),
+    }
diff --git a/pydatajson/federation.py b/pydatajson/federation.py
index 9573040..9f132cd 100644
--- a/pydat
--- Proposal A
## Root cause
`push_dataset_to_ckan()` only federates datasets.  
When a dataset contains a **theme** that does not already exist in the target CKAN instance, the theme is silently ignored because there is no code that creates CKAN groups (the CKAN representation of *themes*).  
The library therefore lacks a public helper that, given a catalog, creates the corresponding CKAN groups via the CKAN API.

## Fix
Add a new helper `push_theme_to_ckan()` that:

* receives a *catalog* (the parsed `data.json` dict) and the CKAN connection parameters,
* iterates over the catalog’s `theme` objects,
* builds a CKAN‑compatible *group* payload (slugified `id` → `name`, `title`, `description`),
* checks whether the group already exists (`group_show`), and creates it only when it does not,
* respects a `dry_run` flag,
* raises a clear `RuntimeError` when the CKAN request fails,
* is exported from the module so that callers (including the existing `push_dataset_to_ckan`) can import it.

Below is a **unified diff** that introduces the function in the module that already contains the CKAN helpers (`pydatajson/ckan.py`). The diff also adds a small private utility `_slugify` used to generate valid CKAN group names.

```diff
--- a/pydatajson/ckan.py
+++ b/pydatajson/ckan.py
@@
 import json
 import logging
 import requests
 from urllib.parse import urljoin
+import re
 
 __all__ = [
     "push_dataset_to_ckan",
+    "push_theme_to_ckan",
     "get_dataset",
     "delete_dataset",
     "ckan_url_from_config",
 ]
@@
 logger = logging.getLogger(__name__)
 
+def _slugify(value: str) -> str:
+    """
+    Convert a string to a slug that can be used as a CKAN group ``name``.
+
+    CKAN group names must be lower‑case, alphanumeric and may contain ``-`` or ``_``.
+    This helper:
+    * lower‑cases the string,
+    * replaces any sequence of non‑alphanumeric characters with a single ``-``,
+    * strips leading/trailing ``-``.
+    """
+    value = value.lower()
+    # Replace any run of characters that are not a‑z, 0‑9, or underscore with a dash
+    value = re.sub(r"[^a-z0-9_]+", "-", value)
+    value = value.strip("-")
+    return value
+
@@
 def push_dataset_to_ckan(
     dataset: dict,
     ckan_url: str,
     api_key: str,
     organization_id: Optional[str] = None,
@@
     response.raise_for_status()
     return response.json()["result"]
 
+def push_theme_to_ckan(
+    catalog: dict,
+    ckan_url: str,
+    api_key: str,
+    dry_run: bool = False,
+) -> List[dict]:
+    """
+    Create CKAN groups that correspond to the ``theme`` objects defined in a
+    *data.json* catalog.
+
+    Parameters
+    ----------
+    catalog: dict
+        The parsed ``data.json`` (as returned by ``read_json`` or similar).
+    ckan_url: str
+        Base URL of the CKAN instance (e.g. ``https://demo.ckan.org``).
+    api_key: str
+        CKAN API key with permission to create groups.
+    dry_run: bool, default ``False``
+        When ``True`` the function logs the actions it *would* take but does not
+        perform any HTTP request.
+
+    Returns
+    -------
+    list[dict]
+        A list with the JSON responses of the successful ``group_create`` calls.
+        If a group already exists, its existing representation is returned.
+    """
+    if "theme" not in catalog or not isinstance(catalog["theme"], list):
+        logger.info("No themes defined in the catalog – nothing to push.")
+        return []
+
+    created_groups: List[dict] = []
+    headers = {"Authorizat
--- Proposal B
## Root cause
`push_dataset_to_ckan()` only creates/updates CKAN *datasets* (packages).  
When a dataset contains themes that are not already present as CKAN groups, the
function silently ignores them – the groups are never created.  The repository
does not provide any helper that creates CKAN groups (themes) via the CKAN
API, therefore federating a catalog that introduces new themes fails to
populate the destination portal correctly.

## Fix
Add a new public helper **`push_theme_to_ckan()`** that receives the CKAN
endpoint, an API‑key and a list of theme objects (as they appear in a
`pydatajson` catalog) and creates the corresponding CKAN groups if they do
not exist yet.  The implementation re‑uses the existing `title_to_name`
utility to generate a slug for the group name, checks the existence of the
group with the `group_show` action and creates it with `group_create`.  The
function returns the list of group names that were created.

```diff
diff --git a/pydatajson/ckan_utils.py b/pydatajson/ckan_utils.py
index 31f6737..e9b2c4f 100644
--- a/pydatajson/ckan_utils.py
+++ b/pydatajson/ckan_utils.py
@@
 import json
 import logging
+import requests
 from .utils import title_to_name, decode_json, encode_json, get_logger
 
@@
 def map_dataset_to_package(dataset, catalog_id, owner_org, theme_taxonomy,
                            demote_superThemes=True, demote_themes=True):
@@
     return package
 
+# ---------------------------------------------------------------------------
+# CKAN group (theme) handling
+# ---------------------------------------------------------------------------
+
+def _ckan_action(portal_url, apikey, action, data):
+    """
+    Low‑level helper that calls a CKAN action endpoint.
+
+    Parameters
+    ----------
+    portal_url : str
+        Base URL of the CKAN portal (e.g. ``https://demo.ckan.org``).
+    apikey : str
+        API key of a user that has permission to create groups.
+    action : str
+        CKAN action name (``group_show``, ``group_create`` …).
+    data : dict
+        JSON payload sent to the action.
+
+    Returns
+    -------
+    dict
+        The ``result`` part of the CKAN response.
+    """
+    url = f"{portal_url.rstrip('/')}/api/3/action/{action}"
+    headers = {'Authorization': apikey, 'Content-Type': 'application/json'}
+    response = requests.post(url, json=data, headers=headers)
+    # Raise a clear exception if the HTTP request fails
+    response.raise_for_status()
+    payload = response.json()
+    if not payload.get('success'):
+        # CKAN returns a 200 even on logical errors – surface the error message
+        raise RuntimeError(f"CKAN action '{action}' failed: {payload.get('error')}")
+    return payload['result']
+
+
+def push_theme_to_ckan(portal_url, apikey, themes, owner_org=None):
+    """
+    Ensure that every theme in *themes* exists as a CKAN group.
+
+    The function iterates over the supplied theme dictionaries (as they appear
+    in a ``pydatajson`` catalog) and, for each one, creates a CKAN group with
+    the slug generated by :func:`pydatajson.utils.title_to_name`.  If a group
+    with that slug already exists, it is left untouched.\n
+
+    Parameters
+    ----------
+    portal_url : str
+        URL of the destination CKAN portal.
+    apikey : str
+        API key of a user authorised to create groups.
+    themes : list[dict]
+        List of theme objects.  Each object must contain at least a ``label``
+        field (the human‑readable name).  O

=== TASK iterative__dvc-8166 | repo iterative/dvc | python
--- Issue
dvc.api.params_show: conflict of `stages` with `deps=True` with nondefault target
# Bug Report

<!--
## Issue name

Issue names must follow the pattern `command: description` where the command is the dvc command that you are trying to run. The description should describe the consequence of the bug. 

Example: `repro: doesn't detect input changes`
-->

## Description

<!--
A clear and concise description of what the bug is.
-->

When run with `stages!=None` and a nondefault params file in `*targets` (or one which is not in the same directory as dvc.yaml), the `deps` parameter is ignored (as if it were always `False` as in default) and so even even parameters which do not belong to any stages are shown.



### Reproduce

<!--
Step list of how to reproduce the bug
-->
1. Clone e.g. the [example-starged-repo](https://github.com/iterative/example-get-started)
2. Create `params_foo.yaml` as `foo: bar`
4. run the following script in python from 
```
import dvc.api
print(dvc.api.params_show("params_foo.yaml", stages="prepare", deps=True))
```
The output is then `{'foo': 'bar'}`
<!--
Example:

1. dvc init
5. Copy dataset.zip to the directory
6. dvc add dataset.zip
7. dvc run -d dataset.zip -o model ./train.sh
8. modify dataset.zip
9. dvc repro
-->

### Expected

<!--
A clear and concise description of what you expect to happen.
-->
The output should be empty (or perhaps a warning raised that no such parameters exist)

### Environment 
--- REAL FIX (ground truth)
diff --git a/dvc/repo/params/show.py b/dvc/repo/params/show.py
--- a/dvc/repo/params/show.py
+++ b/dvc/repo/params/show.py
@@ -56,7 +56,9 @@ def _collect_configs(
             default_params
         ):
             fs_paths.append(default_params)
-
+    if targets and (deps or stages) and not params:
+        # A target has been provided but it is not used in the stages
+        fs_paths = []
     return params, fs_paths
 
 

--- Proposal A
## Root cause
`dvc.api.params_show` builds the result in two different ways:

* When the *target* is the default `params.yaml` it loads the full parameter
  mapping from the repository and then applies the `stages`/`deps` filter.
* When a *non‑default* target file is supplied it bypasses the repository‑wide
  collection and reads the file directly with `yaml.safe_load`.  
  In this branch the `deps` flag is **ignored**, so the function always returns
  the raw contents of the file even if `stages` is given. Consequently parameters
  that do not belong to the requested stage are shown.

The bug appears only when `stages` is not `None` **and** the target is a
non‑default params file (e.g. `params_foo.yaml`). The function should always
honour the `deps` flag, regardless of the target location.

## Fix
Update `dvc/api/params.py` (the implementation of `params_show`) so that the
`deps` flag is applied consistently. The new implementation always asks the
repository for the *filtered* parameter mapping and then, if a specific target
was requested, extracts only the entries belonging to that file.

```diff
--- a/dvc/api/params.py
+++ b/dvc/api/params.py
@@
-def params_show(
-    target: Optional[str] = None,
-    stages: Optional[Union[str, List[str]]] = None,
-    deps: bool = False,
-    repo: Optional[Repo] = None,
-    **kwargs,
-) -> Dict[str, Any]:
-    """
-    Show parameters from a params file.
-
-    Parameters
-    ----------
-    target : str, optional
-        Path to the params file. If omitted, the default ``params.yaml`` is used.
-    stages : str or list of str, optional
-        Stage name(s) to filter parameters by.
-    deps : bool, default=False
-        If True, only parameters that are *dependencies* of the given stages
-        are returned.
-    repo : dvc.repo.Repo, optional
-        Repository object. If omitted, the current repo is used.
-
-    Returns
-    -------
-    dict
-        Mapping of parameter names to values.
-    """
-    repo = repo or Repo()
-
-    # Fast path for the default params file – we can reuse the repository's
-    # internal machinery which already knows about stage‑parameter relationships.
-    if target is None or target == "params.yaml":
-        return repo.params.show(stages=stages, deps=deps)
-
-    # For a non‑default file we fall back to a simple yaml load.  This path
-    # historically ignored ``deps`` which caused the bug described in
-    # https://github.com/iterative/dvc/issues/XXXX.
-    with repo.fs.open(target) as fobj:
-        data = yaml.safe_load(fobj) or {}
-
-    # ``stages`` is ignored for non‑default files because we cannot know which
-    # parameters belong to which stage without parsing the whole repo.  This is
-    # acceptable for the default behaviour but leads to surprising results when
-    # ``deps=True`` is requested.
-    return data
+def params_show(
+    target: Optional[str] = None,
+    stages: Optional[Union[str, List[str]]] = None,
+    deps: bool = False,
+    repo: Optional[Repo] = None,
+    **kwargs,
+) -> Dict[str, Any]:
+    """
+    Show parameters from a params file.
+
+    The function now **always** respects the ``deps`` flag, even when a
+    non‑default params file is supplied.  It does this by first obtaining the
+    repository‑wide filtered mapping (which knows the stage‑dependency
+    relationships) and then, if ``target`` is given, returning only the part
+    that originates from that file.
+    """
+    repo = repo or Repo()
+
+   
--- Proposal B
## Root cause
`dvc.api.params_show` forwards the request to `Repo.params_show`.  
When `deps=True` the implementation only looked at `ParamsDependency` objects whose
*default* path (`params.yaml`) matched the requested file.  
If a non‑default parameters file (e.g. `params_foo.yaml`) was supplied, the
dependency‑filtering logic was skipped, so the function behaved as if
`deps=False` and returned **all** parameters from the file.  

## Fix
Update the dependency‑filtering logic to compare the *actual* path of each
`ParamsDependency` with the user‑provided `path`.  This makes the `deps`
behaviour work for any parameters file.

```diff
diff --git a/dvc/repo.py b/dvc/repo.py
index 7c9e1a2..b3f4d8c 100644
--- a/dvc/repo.py
+++ b/dvc/repo.py
@@
 from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union
 
 from dvc.dependency.base import Dependency
+from dvc.dependency.param import ParamsDependency
 from dvc.exceptions import DvcException
 from dvc.fs import localfs
@@
-    def params_show(
-        self,
-        path: str = "params.yaml",
-        stages: Optional[Union[str, List[str]]] = None,
-        deps: bool = False,
-    ) -> Dict[str, Any]:
-        """Show parameters from a params file.
-
-        If ``stages`` is provided, only parameters that belong to the given
-        stages are shown.  If ``deps`` is ``True`` only parameters that are
-        declared as dependencies of the given stages are shown.
-        """
-        if not deps:
-            return self._load_params(path)
-
-        # ``deps`` is True – we need to filter parameters that are actually used
-        # by the requested stages.  The original implementation only considered
-        # the default ``params.yaml`` file, which caused the bug when a custom
-        # params file was supplied.
-        stage_names = self._resolve_stage_names(stages)
-        used_params: Set[str] = set()
-
-        for name in stage_names:
-            stage = self.stages[name]
-            for dep in stage.deps:
-                if isinstance(dep, ParamsDependency) and dep.path == "params.yaml":
-                    used_params.update(dep.params)
-
-        all_params = self._load_params(path)
-        return {k: v for k, v in all_params.items() if k in used_params}
+    def params_show(
+        self,
+        path: str = "params.yaml",
+        stages: Optional[Union[str, List[str]]] = None,
+        deps: bool = False,
+    ) -> Dict[str, Any]:
+        """Show parameters from a params file.
+
+        * ``stages`` – limit the output to parameters that belong to the given
+          stages (or all stages if ``None``).
+        * ``deps`` – when ``True`` return **only** parameters that are declared
+          as dependencies of the selected stages.
+
+        The previous implementation filtered dependencies only for the default
+        ``params.yaml`` file, which ignored ``deps=True`` for any other
+        parameters file.  The logic below now matches the *actual* path of each
+        ``ParamsDependency`` against the user‑provided ``path`` so that the
+        flag works for custom files as well.
+        """
+        # Load the whole file first – we may need to filter it later.
+        all_params = self._load_params(path)
+
+        if not deps:
+            # No dependency filtering requested – return the whole file.
+            return all_params
+
+        # ``deps`` is True – we need to keep only parameters that are used by
+        # the selected stages **a

=== TASK corteva__rioxarray-14 | repo corteva/rioxarray | python
--- Issue
Add ability to set CRS and write CRS information to dataset
See: https://github.com/geoxarray/geoxarray/issues/9
--- REAL FIX (ground truth)
diff --git a/rioxarray/exceptions.py b/rioxarray/exceptions.py
index 723a391..99a58fd 100644
--- a/rioxarray/exceptions.py
+++ b/rioxarray/exceptions.py
@@ -26,3 +26,7 @@ class TooManyDimensions(RioXarrayError):
 
 class InvalidDimensionOrder(RioXarrayError):
     """This is raised when there the dimensions are not ordered correctly."""
+
+
+class MissingCRS(RioXarrayError):
+    """Missing the CRS in the dataset."""
diff --git a/rioxarray/rioxarray.py b/rioxarray/rioxarray.py
index acefb4b..1a9b0ec 100644
--- a/rioxarray/rioxarray.py
+++ b/rioxarray/rioxarray.py
@@ -25,6 +25,7 @@ from scipy.interpolate import griddata
 
 from rioxarray.exceptions import (
     InvalidDimensionOrder,
+    MissingCRS,
     NoDataInBounds,
     OneDimensionalRaster,
     TooManyDimensions,
@@ -32,7 +33,7 @@ from rioxarray.exceptions import (
 from rioxarray.crs import crs_to_wkt
 
 FILL_VALUE_NAMES = ("_FillValue", "missing_value", "fill_value", "nodata")
-UNWANTED_RIO_ATTRS = ("nodata", "nodatavals", "crs", "is_tiled", "res")
+UNWANTED_RIO_ATTRS = ("nodatavals", "crs", "is_tiled", "res")
 DEFAULT_GRID_MAP = "spatial_ref"
 
 
@@ -123,23 +124,9 @@ def add_xy_grid_meta(coords):
 
 
 def add_spatial_ref(in_ds, dst_crs, grid_map_name):
-    # remove old grid map if exists
-    try:
-        del in_ds.coords[grid_map_name]
-    except KeyError:
-        pass
-
-    # add grid mapping variable
-    in_ds.coords[grid_map_name] = xarray.Variable((), 0)
-    match_proj = crs_to_wkt(CRS.from_user_input(dst_crs))
-
-    grid_map_attrs = dict()
-    # add grid mapping variable
-    grid_map_attrs["spatial_ref"] = match_proj
-    # http://cfconventions.org/Data/cf-conventions/cf-conventions-1.7/cf-conventions.html#appendix-grid-mappings
-    # http://desktop.arcgis.com/en/arcmap/10.3/manage-data/netcdf/spatial-reference-for-netcdf-data.htm
-    grid_map_attrs["crs_wkt"] = match_proj
-    in_ds.coords[grid_map_name].attrs = grid_map_attrs
+    in_ds.rio.write_crs(
+        input_crs=dst_crs, grid_mapping_name=grid_map_name, inplace=True
+    )
     return in_ds
 
 
@@ -230,7 +217,93 @@ class XRasterBase(object):
         """:obj:`rasterio.crs.CRS`:
             The projection of the dataset.
         """
-        return self._crs
+        raise NotImplementedError
+
+    def set_crs(self, input_crs, inplace=True):
+        """
+        Set the CRS value for the Dataset/DataArray without modifying
+        the dataset/data array.
+
+        Parameters:
+        -----------
+        input_crs: object
+            Anythong accepted by `rasterio.crs.CRS.from_user_input`.
+
+        Returns:
+        --------
+        xarray.Dataset or xarray.DataArray:
+        Dataset with crs attribute.
+
+        """
+        crs = CRS.from_user_input(input_crs)
+        if inplace:
+            self._crs = crs
+            return self._obj
+        xobj = self._obj.copy(deep=True)
+        xobj.rio._crs = crs
+        return xobj
+
+    def write_crs(
+        self, input_crs=None, grid_mapping_
--- Proposal A
## Root cause
`rioxarray` only implements CRS handling for `xarray.DataArray` objects. The accessor (`.rio`) does not expose a `set_crs` / `write_crs` method for `xarray.Dataset`, so attempts to set or persist a CRS on a `Dataset` silently fail or raise an `AttributeError`.  The missing implementation means that CRS information cannot be attached to a `Dataset` nor written to disk when the dataset is saved (e.g., to NetCDF or GeoTIFF).

## Fix
Add CRS handling to the `Dataset` accessor by implementing `set_crs` and `write_crs` methods that:

* Accept a `rasterio.crs.CRS` object, EPSG code, PROJ string, or dict.
* Store the CRS in the dataset’s attributes (`crs_wkt` and `spatial_ref` – the same keys used by rasterio).
* Optionally propagate the CRS to all data variables that contain spatial dimensions.
* Respect the `inplace` flag (default `False`) to either modify the original object or return a copy.
* Ensure that the CRS is also written when the dataset is exported via `rio.to_raster` or `rio.to_netcdf`.

Below is a **unified diff** that adds the required functionality to `rioxarray/rioxarray_accessor.py`.  The diff assumes the existing accessor already defines methods for `DataArray`; we extend the `Dataset` branch accordingly.

```diff
--- a/rioxarray/rioxarray_accessor.py
+++ b/rioxarray/rioxarray_accessor.py
@@
 class RasterioAccessor:
@@
     def __init__(self, xarray_obj):
         self._obj = xarray_obj
         self._validate()
 
+    # -----------------------------------------------------------------
+    # Helper utilities (shared between DataArray and Dataset)
+    # -----------------------------------------------------------------
+    @staticmethod
+    def _parse_crs(crs):
+        """
+        Convert various CRS specifications to a rasterio CRS object.
+        """
+        import rasterio.crs
+        if isinstance(crs, rasterio.crs.CRS):
+            return crs
+        # Accept EPSG integer, PROJ string, dict, etc.
+        return rasterio.crs.CRS.from_user_input(crs)
+
+    @staticmethod
+    def _crs_attrs(crs):
+        """
+        Return a dict of attributes that rasterio expects for CRS storage.
+        """
+        # rasterio stores CRS as WKT in the attribute ``crs_wkt`` and also
+        # provides a ``spatial_ref`` attribute for compatibility with GDAL.
+        return {
+            "crs_wkt": crs.to_wkt(),
+            "spatial_ref": crs.to_wkt(),
+        }
+
+    # -----------------------------------------------------------------
+    # CRS handling for DataArray (existing implementation – unchanged)
+    # -----------------------------------------------------------------
@@
     def crs(self):
         """Return the CRS of the DataArray."""
         return self._obj.rio._crs
@@
     def set_crs(self, crs, inplace=False):
         """
         Set the CRS on the DataArray without writing to disk.
@@
         self._obj.rio._crs = crs_obj
         if not inplace:
             return self._obj.copy()
         return self._obj
+
+    # -----------------------------------------------------------------
+    # New: CRS handling for Dataset
+    # -----------------------------------------------------------------
+    @property
+    def crs(self):
+        """
+        Return the CRS stored in the Dataset attributes.
+        If the attribute is missing, ``None`` is returned.
+        """
+        # Prefer the explicit ``crs_wkt`` attribute; fall back to ``spatial_ref``.
+        wkt = self._obj.attrs.get("crs
--- Proposal B
## Root cause
`rioxarray` only provided a CRS‑setter (`set_crs`) for `xarray.Dataset` objects.  
`set_crs` only stored the CRS in a *lossy* internal attribute and did **not** write the CRS information to the underlying CF‑compliant metadata. Consequently, users could not persist a CRS on a `Dataset` nor have it automatically written when exporting to a raster file.

## Fix
* Add a proper `write_crs` method to the `RasterDataset` accessor that writes the CRS to the dataset’s metadata (and to every data variable) in a CF‑compliant way.  
* Deprecate the old `set_crs` for `Dataset` by routing it to `write_crs` and emitting a `FutureWarning` (mirroring the behaviour already introduced for `DataArray`).  
* Ensure the dataset‑level `crs` property returns the written CRS and that the CRS is propagated to all variables.

```diff
diff --git a/rioxarray/rioxarray.py b/rioxarray/rioxarray.py
index 648fc6ff..b7e3a1c4 100644
--- a/rioxarray/rioxarray.py
+++ b/rioxarray/rioxarray.py
@@
 class RasterDataset(XRasterBase):
@@
     def __init__(self, xarray_obj):
         super().__init__(xarray_obj)
         self._crs = None
@@
-    def set_crs(self, crs, inplace=False):
-        """Set the CRS for the dataset.
-
-        This method is **lossy** – it only stores the CRS in the accessor
-        and does **not** write it to the underlying xarray metadata.
-
-        Use :meth:`write_crs` if you want the CRS to be persisted.
-
-        Parameters
-        ----------
-        crs : rasterio.crs.CRS or anything accepted by rasterio.CRS.from_user_input
-            The CRS to set.
-        inplace : bool, default False
-            If True, modify the dataset in‑place and return ``None``.
-
-        Returns
-        -------
-        xarray.Dataset or None
-            The dataset with the CRS set (or ``None`` if ``inplace=True``).
-        """
-        warnings.warn(
-            "set_crs is deprecated and will be removed in a future release. "
-            "Use write_crs instead.",
-            FutureWarning,
-            stacklevel=2,
-        )
-        return self._set_crs(crs, inplace=inplace)
-
-    # internal helper used by both the deprecated ``set_crs`` and the new ``write_crs``
-    def _set_crs(self, crs, inplace=False):
-        """Internal method that actually sets the CRS on the accessor."""
-        crs_obj = rasterio.crs.CRS.from_user_input(crs)
-        if inplace:
-            self._crs = crs_obj
-            # also write to each variable so that export works
-            for var in self.vars:
-                self._obj[var].rio._set_crs(crs_obj, inplace=True)
-            return None
-        else:
-            new = self._obj.copy()
-            new.rio._set_crs(crs_obj, inplace=True)
-            return new
+    def write_crs(self, crs, inplace=False):
+        """Write the CRS to the dataset and all its data variables.
+
+        The CRS is stored in a CF‑compliant way (as the ``crs_wkt`` attribute)
+        on the dataset and on each ``DataArray`` that belongs to the dataset.
+        This ensures that the CRS survives round‑tripping to disk (e.g. via
+        :meth:`to_raster`).
+
+        Parameters
+        ----------
+        crs : rasterio.crs.CRS or anything accepted by ``rasterio.CRS.from_user_input``
+            The CRS to write.
+        inplace : bool, default False
+            If ``True`` the dataset is modified in‑place and ``None`` is
+            returned.  If ``False`` a shallow copy of the dataset with the
+        

=== TASK kinnala__scikit-fem-667 | repo kinnala/scikit-fem | python
--- Issue
Examples providing your own mesh and retriving the basis functions?
Are there any examples for supplying your own mesh (for example from scipy Delaunay) and extracting basis functions of some degree? 

For example you might evaluate the basis functions at a set of points for fitting against data as a kind of smoothing spline.

--- REAL FIX (ground truth)
diff --git a/README.md b/README.md
index ec13b5ff..453498ed 100644
--- a/README.md
+++ b/README.md
@@ -257,6 +257,9 @@ with respect to documented and/or tested features.
 - Added: `ElementTriCCR` and `ElementTetCCR`, conforming Crouzeix-Raviart finite elements
 - Fixed: `Mesh.mirrored` returned a wrong mesh when a point other than the origin was used
 - Fixed: `MeshLine` constructor accepted only NumPy arrays and not plain Python lists
+- Fixed: `Mesh.element_finder` (and `CellBasis.probes`, `CellBasis.interpolator`) was not working properly for a small number of elements (<5) or a large number of input points (>1000)
+- Fixed: `MeshTet` and `MeshTri.element_finder` is are now more robust against degenerate elements
+- Fixed: `Mesh.element_finder` (and `CellBasis.probes`, `CellBasis.interpolator`) raises exception if the query point is outside of the domain
 
 ### [3.1.0] - 2021-06-18
 
diff --git a/docs/examples/ex28.py b/docs/examples/ex28.py
index 5c526815..987ce837 100644
--- a/docs/examples/ex28.py
+++ b/docs/examples/ex28.py
@@ -88,15 +88,7 @@ import numpy as np
 import pygmsh
 
 
-if version.parse(pygmsh.__version__) < version.parse('7.0.0'):
-    class NullContextManager():
-        def __enter__(self):
-            return None
-        def __exit__(self, *args):
-            pass
-    geometrycontext = NullContextManager()
-else:
-    geometrycontext = pygmsh.geo.Geometry()
+geometrycontext = pygmsh.geo.Geometry()
 
 halfheight = 1.
 length = 10.
@@ -110,17 +102,12 @@ peclet = 357.
 def make_mesh(halfheight: float,  # mm
               length: float,
               thickness: float) -> MeshTri:
-    with geometrycontext as g:
-        if version.parse(pygmsh.__version__) < version.parse('7.0.0'):
-            geom = pygmsh.built_in.Geometry()
-            geom.add_curve_loop = geom.add_line_loop
-        else:
-            geom = g
+    with geometrycontext as geom:
 
         points = []
         lines = []
 
-        lcar = halfheight / 2**2
+        lcar = halfheight / 2 ** 2
 
         for xy in [(0., halfheight),
                    (0., -halfheight),
@@ -155,10 +142,7 @@ def make_mesh(halfheight: float,  # mm
         geom.add_physical(geom.add_plane_surface(geom.add_curve_loop(
             [*lines[-3:], -lines[1]])), 'solid')
 
-        if version.parse(pygmsh.__version__) < version.parse('7.0.0'):
-            return from_meshio(pygmsh.generate_mesh(geom, dim=2))
-        else:
-            return from_meshio(geom.generate_mesh(dim=2))
+        return from_meshio(geom.generate_mesh(dim=2))
 
 mesh = from_file(Path(__file__).parent / 'meshes' / 'ex28.json')
 element = ElementTriP1()
diff --git a/skfem/mesh/mesh_line_1.py b/skfem/mesh/mesh_line_1.py
index ef85502c..e04afbda 100644
--- a/skfem/mesh/mesh_line_1.py
+++ b/skfem/mesh/mesh_line_1.py
@@ -74,6 +74,7 @@ class MeshLine1(Mesh):
         return np.max(np.abs(self.p[0, self.t[1]] - self.p[0, self.t[0]]))
 
     def element_finder(self, mapping=None):
+
         ix = np.argsort
--- Proposal A
## Root cause
The repository does not provide a ready‑to‑use example that shows how to:

1. Build a `MeshTri` from an arbitrary point set (e.g., the output of `scipy.spatial.Delaunay`).
2. Evaluate the finite‑element basis functions of a chosen degree at an arbitrary collection of physical points.

While the core functionality (`MeshTri(p, t)` and `Basis.eval`) already exists, the lack of a convenience constructor and an illustrative example makes it hard for users to discover and use this workflow.

## Fix
* Add a small convenience class‑method `MeshTri.from_delaunay` that accepts the point array and triangle index array returned by `scipy.spatial.Delaunay`.
* Add a new example script `docs/examples/ex_custom_mesh.py` that demonstrates:
  * Creating a Delaunay triangulation with SciPy.
  * Converting it to a `MeshTri` via the new constructor.
  * Building a basis of arbitrary degree.
  * Evaluating the basis functions on a user‑defined set of points.
  * (Optional) visualising a few basis functions.

```diff
diff --git a/skfem/mesh/mesh2d/mesh_tri.py b/skfem/mesh/mesh2d/mesh_tri.py
index c0d5b383..e7f2a1c9 100644
--- a/skfem/mesh/mesh2d/mesh_tri.py
+++ b/skfem/mesh/mesh2d/mesh_tri.py
@@
 class MeshTri(Mesh2D):
@@
     def __init__(self, p, t, **kwargs):
         """
         Initialise a triangular mesh.
@@
         self.t = t
         self._init_mesh(**kwargs)
 
+    # ---------------------------------------------------------------------\n+    # Convenience constructor for external triangulations (e.g. SciPy Delaunay)\n+    # ---------------------------------------------------------------------\n+    @classmethod\n+    def from_delaunay(cls, points, triangles, **kwargs):\n+        \"\"\"Create a :class:`MeshTri` from a SciPy Delaunay object or raw arrays.\n+\n+        Parameters\n+        ----------\n+        points : (n_points, dim) array_like\n+            Cartesian coordinates of the mesh vertices. ``dim`` must be ``2``.\n+        triangles : (n_tri, dim) array_like\n+            Indices of the vertices that form each triangle. The indices are\n+            expected to be zero‑based (as returned by ``scipy.spatial.Delaunay``).\n+        **kwargs : dict\n+            Additional keyword arguments forwarded to the regular ``MeshTri``\n+            constructor (e.g. ``boundaries``).\n+\n+        Returns\n+        -------\n+        MeshTri\n+            A mesh ready to be used with the rest of *scikit‑fem*.\n+        \"\"\"\n+        import numpy as np\n+\n+        # Ensure correct shape and dtype\n+        points = np.asarray(points, dtype=float)\n+        triangles = np.asarray(triangles, dtype=int)\n+\n+        if points.ndim != 2 or points.shape[1] != 2:\n+            raise ValueError(\"`points` must be of shape (n_points, 2) for a 2‑D mesh.\")\n+        if triangles.ndim != 2 or triangles.shape[1] != 3:\n+            raise ValueError(\"`triangles` must be of shape (n_tri, 3) for triangular elements.\")\n+\n+        # Convert to the column‑major layout used internally: (dim, n_points)\n+        p = points.T\n+        t = triangles.T\n+        return cls(p, t, **kwargs)\n*** End of File
diff --git a/docs/examples/ex_custom_mesh.py b/docs/examples/ex_custom_mesh.py
new file mode 100644
index 00000000..a1b2c3d4
--- /dev/null
+++ b/docs/examples/ex_custom_mesh.py
@@
+\"\"\"Custom mesh from SciPy Delaunay and evaluation of basis functions.\n+\n+This example demonstrates how to:\n+\n+1. Generate an unstructured triangular mesh using ``sci
--- Proposal B
## Root cause
The repository does not contain an example that shows how to build a `Mesh` from an external triangulation (e.g., a `scipy.spatial.Delaunay` object) and how to evaluate the corresponding finite‑element basis functions at arbitrary points.  
While the core library already provides the necessary building blocks (`MeshTri`, `Basis`, `basis.evaluate`), there is no convenience wrapper to convert a Delaunay object into a `MeshTri`, nor is there any example script that demonstrates the whole workflow. This makes it hard for users to discover the feature and leads to the reported “missing example” issue.

## Fix
1. **Add a convenience constructor** `MeshTri.from_delaunay` in `skfem/mesh.py` that builds a `MeshTri` directly from a `scipy.spatial.Delaunay` object.  
2. **Add an example script** `examples/mesh_from_delaunay.py` that:
   * Generates a set of points,
   * Creates a Delaunay triangulation,
   * Converts it to a `MeshTri`,
   * Builds a `Basis` of a chosen degree,
   * Evaluates the basis functions on a user‑defined set of points,
   * Plots the basis functions (optional, using `matplotlib`).

```diff
--- a/skfem/mesh.py
+++ b/skfem/mesh.py
@@
 class MeshTri(Mesh):
@@
     def __init__(self, points, cells, **kwargs):
         super().__init__(points, cells, **kwargs)
         self._check_consistency()
         self._init_boundary()
 
+    @classmethod
+    def from_delaunay(cls, delaunay, **kwargs):
+        """
+        Construct a ``MeshTri`` from a ``scipy.spatial.Delaunay`` object.
+
+        Parameters
+        ----------
+        delaunay : scipy.spatial.Delaunay
+            The Delaunay triangulation to be converted.
+        **kwargs : dict
+            Additional keyword arguments passed to the ``MeshTri`` constructor.
+
+        Returns
+        -------
+        MeshTri
+            A mesh representing the same triangulation.
+        """
+        # ``delaunay.points`` contains the vertex coordinates (N x dim)
+        # ``delaunay.simplices`` contains the indices of the vertices for each triangle (M x 3)
+        points = np.asarray(delaunay.points, dtype=np.float64)
+        cells = np.asarray(delaunay.simplices, dtype=np.int64)
+        # Ensure the orientation of each triangle is positive (counter‑clockwise)
+        # The orientation test works for 2‑D meshes only.
+        if points.shape[1] == 2:
+            # Compute signed area for each cell
+            p0 = points[cells[:, 0]]
+            p1 = points[cells[:, 1]]
+            p2 = points[cells[:, 2]]
+            signed_area = 0.5 * ((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) -
+                                 (p2[:, 0] - p0[:, 0]) * (p1[:, 1] - p0[:, 1]))
+            # Flip cells with negative area
+            flip = signed_area < 0
+            cells[flip] = cells[flip][:, ::-1]
+        return cls(points, cells, **kwargs)
+
     @classmethod
     def from_vertices_and_cells(cls, vertices, cells, **kwargs):
         """Create a MeshTri from vertices and cells."""
         return cls(vertices, cells, **kwargs)
*** End of File
--- a/examples/mesh_from_delaunay.py
+++ b/examples/mesh_from_delaunay.py
@@
-"""
-Example: Build a mesh from scipy.spatial.Delaunay and evaluate basis functions.
-
-This script demonstrates:
-  * Creating a point cloud.
-  * Building a Delaunay triangulation with SciPy.
-  * Converting the triangulation to a scikit‑fem MeshTri.
-  * Constructing a finite‑element basis of a given degree.
-  * Evaluating the basis funct

=== TASK wkeeling__selenium-wire-509 | repo wkeeling/selenium-wire | python
--- Issue
importing uc from seleniumwire ignores proxy option
On newer versions of undetected chromedriver, I need to use this arg ``use_subprocess=True`` else I can't run it without getting a Runtime error. (https://github.com/ultrafunkamsterdam/undetected-chromedriver/issues/432) This argument seems to break the usage of proxies with selenium wire though.

```python
import seleniumwire.undetected_chromedriver.v2 as uc
from seleniumwire import webdriver

chrome_options = webdriver.ChromeOptions()
chrome_options.headless = False
seleniumwire_options = {'proxy': {'https': 'type://host:port',}} 
driver = uc.Chrome(options=chrome_options, seleniumwire_options=seleniumwire_options, use_subprocess=True)

driver.get('https://whatismyipaddress.com/')
```

Ignores passed proxy option and loads website without proxy.
--- REAL FIX (ground truth)
diff --git a/README.rst b/README.rst
index 17e62f7..bb02330 100644
--- a/README.rst
+++ b/README.rst
@@ -694,21 +694,19 @@ If you wish to take advantage of this make sure you have undetected_chromedriver
 
     pip install undetected-chromedriver
 
-Then you can select the version of undetected_chromedriver you want to use by importing ``Chrome`` and ``ChromeOptions`` from the appropriate package.
-
-For undetected_chromedriver version 1:
+Then in your code, import the ``seleniumwire.undetected_chromedriver`` package:
 
 .. code:: python
 
-    from seleniumwire.undetected_chromedriver import Chrome, ChromeOptions
-
-For undetected_chromedriver version 2:
+    import seleniumwire.undetected_chromedriver as uc
 
-.. code:: python
+    chrome_options = uc.ChromeOptions()
 
-    from seleniumwire.undetected_chromedriver.v2 import Chrome, ChromeOptions
+    driver = uc.Chrome(
+        options=chrome_options,
+        seleniumwire_options={}
+    )
 
-See the `undetected_chromedriver docs <https://github.com/ultrafunkamsterdam/undetected-chromedriver>`_ for differences between the two versions.
 
 Certificates
 ~~~~~~~~~~~~
diff --git a/seleniumwire/storage.py b/seleniumwire/storage.py
index 6d1cd66..1be95da 100644
--- a/seleniumwire/storage.py
+++ b/seleniumwire/storage.py
@@ -181,12 +181,9 @@ class RequestStorage:
         request_dir = self._get_request_dir(request_id)
 
         with open(os.path.join(request_dir, 'request'), 'rb') as req:
-            try:
-                request = pickle.load(req)
-            except Exception:
-                # Errors may sometimes occur with unpickling - e.g.
-                # sometimes data hasn't been fully flushed to disk
-                # by the OS by the time we come to unpickle it.
+            request = self._unpickle(req)
+
+            if request is None:
                 return None
 
             ws_messages = self._ws_messages.get(request.id)
@@ -198,19 +195,37 @@ class RequestStorage:
             try:
                 # Attach the response if there is one.
                 with open(os.path.join(request_dir, 'response'), 'rb') as res:
-                    response = pickle.load(res)
-                    request.response = response
-
-                    # The certificate data has been stored on the response but we make
-                    # it available on the request which is a more logical location.
-                    if hasattr(response, 'cert'):
-                        request.cert = response.cert
-                        del response.cert
+                    response = self._unpickle(res)
+
+                    if response is not None:
+                        request.response = response
+
+                        # The certificate data has been stored on the response but we make
+                        # it available on the request which is a more logical location.
+                        if hasattr(response, 'cert'):
+                            request.cert = response.cert
+  
--- Proposal A
## Root cause
`seleniumwire.undetected_chromedriver.v2.Chrome` is a thin wrapper around the regular Selenium‑Wire `Chrome` driver.  
When the wrapper is instantiated with `use_subprocess=True` it follows a special code‑path that delegates the actual browser launch to **undetected‑chromedriver**’s own `Chrome` implementation.  
In that path the wrapper **does not forward** the `seleniumwire_options` (and therefore the proxy configuration) to the base `seleniumwire.webdriver.Chrome` class, nor does it start Selenium‑Wire’s internal MITM proxy.  
Consequently the browser is started without the Selenium‑Wire proxy and the request is sent directly, ignoring the proxy option supplied by the user.

## Fix
Update `seleniumwire/undetected_chromedriver/v2.py` so that:

1. The `seleniumwire_options` argument is always stored and passed to the parent `seleniumwire.webdriver.Chrome` constructor, even when `use_subprocess=True`.
2. The internal Selenium‑Wire proxy is started **before** the subprocess driver is created, and the generated proxy address is injected into the Chrome options that are handed to undetected‑chromedriver.

```diff
--- a/seleniumwire/undetected_chromedriver/v2.py
+++ b/seleniumwire/undetected_chromedriver/v2.py
@@
-class Chrome(seleniumwire.webdriver.Chrome):
-    """
-    Wrapper around seleniumwire's Chrome driver that adds support for
-    undetected_chromedriver's ``use_subprocess`` argument.
-    """
-
-    def __init__(self, *args, seleniumwire_options=None, use_subprocess=False, **kwargs):
-        """
-        Parameters
-        ----------
-        seleniumwire_options : dict, optional
-            Options passed to Selenium Wire.
-        use_subprocess : bool, default False
-            If True, launch Chrome via undetected_chromedriver's subprocess mode.
-        """
-        self.use_subprocess = use_subprocess
-
-        # When ``use_subprocess`` is True we cannot rely on Selenium Wire's
-        # normal proxy injection because the driver is started in a separate
-        # process.  The original implementation simply ignored the proxy
-        # configuration, which caused the issue described in #XXX.
-
-        if use_subprocess:
-            # Initialise the base Selenium Wire driver without proxy handling.
-            super().__init__(*args, seleniumwire_options=seleniumwire_options, **kwargs)
-            # Launch undetected_chromedriver in subprocess mode.
-            self.driver = uc.Chrome(*args, **kwargs, use_subprocess=True)
-        else:
-            # Normal path – let Selenium Wire handle everything.
-            super().__init__(*args, seleniumwire_options=seleniumwire_options, **kwargs)
+class Chrome(seleniumwire.webdriver.Chrome):
+    """
+    Wrapper around Selenium‑Wire's Chrome driver that adds support for
+    undetected_chromedriver's ``use_subprocess`` argument while **preserving**
+    Selenium‑Wire's proxy handling.
+    """
+
+    def __init__(
+        self,
+        *args,\n+        seleniumwire_options: dict | None = None,\n+        use_subprocess: bool = False,\n+        **kwargs,\n+    ):\n+        \"\"\"Create a Chrome driver that works with Selenium‑Wire **and**\n+        undetected‑chromedriver's subprocess mode.\n+\n+        Parameters\n+        ----------\n+        seleniumwire_options:\n+            Options passed to Selenium‑Wire (e.g. ``{\"proxy\": {...}}``).\n+        use_subprocess:\n+            When ``True`` the underlying Chrome instance is started via\n+            
--- Proposal B
## Root cause
`seleniumwire.undetected_chromedriver.v2.Chrome` creates a **sub‑process driver** when `use_subprocess=True`.  
In that branch the wrapper directly instantiates **undetected‑chromedriver** (`uc.Chrome`) and **does not pass** the `seleniumwire_options` (and therefore the proxy configuration) to Selenium Wire.  
Because Selenium Wire never gets a chance to start its local proxy server, the launched Chrome instance connects directly to the internet, so the supplied proxy is ignored.

## Fix (a unified diff with your best‑guess file paths and code)
```diff
diff --git a/seleniumwire/undetected_chromedriver/v2.py b/seleniumwire/undetected_chromedriver/v2.py
index 3a7c9f2..e4b1d8a 100644
--- a/seleniumwire/undetected_chromedriver/v2.py
+++ b/seleniumwire/undetected_chromedriver/v2.py
@@
-from seleniumwire import webdriver as sw_webdriver
-import undetected_chromedriver as uc
+from seleniumwire import webdriver as sw_webdriver
+import undetected_chromedriver as uc
@@
-class Chrome(uc.Chrome):
-    \"\"\"Thin wrapper around undetected_chromedriver that also enables Selenium Wire.\n-\n-    The only difference to the upstream class is that we accept a ``seleniumwire_options``\n-    argument that is passed to the underlying Selenium Wire driver.\n-    \"\"\"\n-\n-    def __init__(self, *args, seleniumwire_options=None, use_subprocess=False, **kwargs):\n-        \"\"\"Create a new Chrome driver.\n-\n-        Parameters\n-        ----------\n-        seleniumwire_options : dict, optional\n-            Options that are passed to Selenium Wire. See the Selenium Wire docs for details.\n-        use_subprocess : bool, default ``False``\n-            When ``True`` the driver is started in a separate subprocess. This is required for\n-            newer versions of undetected‑chromedriver (see issue #432).\n-        \"\"\"\n-\n-        self.seleniumwire_options = seleniumwire_options or {}\n-\n-        if use_subprocess:\n-            # When using a subprocess we cannot rely on the normal ``uc.Chrome`` constructor\n-            # because it does not expose the ``seleniumwire_options`` argument. Instead we start\n-            # a regular Selenium Wire driver in a subprocess and let it handle the proxying.\n-            # NOTE: ``use_subprocess`` is deliberately *not* forwarded – the subprocess handling\n-            # is performed here.\n-            self.driver = sw_webdriver.Chrome(*args, seleniumwire_options=self.seleniumwire_options, **kwargs)\n-        else:\n-            # Normal path – let the parent class handle everything.\n-            super().__init__(*args, **kwargs)\n+class Chrome(uc.Chrome):\n+    \"\"\"Thin wrapper around ``undetected_chromedriver`` that also enables Selenium Wire.\n+\n+    The upstream ``uc.Chrome`` class does **not** understand the ``seleniumwire_options``\n+    argument.  When ``use_subprocess=True`` the original implementation instantiated a plain\n+    ``uc.Chrome`` instance, which meant Selenium Wire never received the proxy configuration.\n+\n+    This patch makes sure that **both** the subprocess mode *and* the normal mode always create\n+    a Selenium Wire driver, passing the ``seleniumwire_options`` (including proxy settings) to\n+    it.  The underlying ``undetected_chromedriver`` is still used – Selenium Wire internally\n+    creates the driver via ``webdriver.Chrome`` which we monkey‑patch to point at the undetected\n+    implementation.\n+    \"\"\"\n+\n+    def __init__(self, *args, seleniumwire_o

=== TASK iterative__dvc-2892 | repo iterative/dvc | python
--- Issue
"unexpected error - unable to open database" file after updating from 0.59.2 to 0.71.0
**Please provide information about your setup**
DVC version: 0.71.0
Platform: Ubuntu 18.04
Method of installation: pip

I've used dvc for managinf model files. I have not updated dvc for a while and after doing so going from  0.59.2 to 0.71.0 I could not add any new files to the existing dvc cache.

Here is the verbose output of the dvc add command

```
dvc add Empty\ Document -v
/home/mlburk/anaconda3/envs/tf-gpu/lib/python3.7/site-packages/git/repo/base.py:129: UserWarning: The use of environment variables in paths is deprecated
for security reasons and may be removed in the future!!
  warnings.warn("The use of environment variables in paths is deprecated" +
ERROR: unexpected error - unable to open database file
------------------------------------------------------------
Traceback (most recent call last):
  File "/home/mlburk/anaconda3/envs/tf-gpu/lib/python3.7/site-packages/dvc/main.py", line 49, in main
    ret = cmd.run()
  File "/home/mlburk/anaconda3/envs/tf-gpu/lib/python3.7/site-packages/dvc/command/add.py", line 25, in run
    fname=self.args.file,
  File "/home/mlburk/anaconda3/envs/tf-gpu/lib/python3.7/site-packages/dvc/repo/__init__.py", line 35, in wrapper
    with repo.lock, repo.state:
  File "/home/mlburk/anaconda3/envs/tf-gpu/lib/python3.7/site-packages/dvc/state.py", line 136, in __enter__
    self.load()
  File "/home/mlburk/anaconda3/envs/tf-gp
--- REAL FIX (ground truth)
diff --git a/dvc/state.py b/dvc/state.py
--- a/dvc/state.py
+++ b/dvc/state.py
@@ -496,8 +496,14 @@ def _connect_sqlite(filename, options):
 
 
 def _build_sqlite_uri(filename, options):
+    # In the doc mentioned below we only need to replace ? -> %3f and
+    # # -> %23, but, if present, we also need to replace % -> %25 first
+    # (happens when we are on a weird FS that shows urlencoded filenames
+    # instead of proper ones) to not confuse sqlite.
+    uri_path = filename.replace("%", "%25")
+
     # Convert filename to uri according to https://www.sqlite.org/uri.html, 3.1
-    uri_path = filename.replace("?", "%3f").replace("#", "%23")
+    uri_path = uri_path.replace("?", "%3f").replace("#", "%23")
     if os.name == "nt":
         uri_path = uri_path.replace("\\", "/")
         uri_path = re.sub(r"^([a-z]:)", "/\\1", uri_path, flags=re.I)

--- Proposal A
## Root cause
`dvc` 0.71 switched to opening the **state SQLite database** using a raw path string that may contain user‑home shortcuts (`~`) or be a relative path.  
When the repository root (or the `.dvc` directory) is expressed with a tilde, the path passed to `sqlite3.connect()` is **not expanded** and the parent directory may not exist. SQLite therefore raises  

```
sqlite3.OperationalError: unable to open database file
```

The older 0.59.2 implementation expanded the path (and ensured the directory existed) before opening the DB, so the error only appears after the upgrade.

## Fix
* Expand `~` and resolve the state file to an absolute path.  
* Make sure the directory that will contain the state DB exists before calling `sqlite3.connect`.  
* Keep the original behaviour for non‑string `Path` objects.

```diff
diff --git a/dvc/state.py b/dvc/state.py
index 1234567..89abcde 100644
--- a/dvc/state.py
+++ b/dvc/state.py
@@
 import sqlite3
+import os
 from typing import Any, Dict, Optional
 
@@
-def _connect_sqlite(state_file: str, options: Dict[str, Any]) -> sqlite3.Connection:
-    """Connect to the SQLite state database."""
-    uri = state_file
-    return sqlite3.connect(uri, uri=True, **options)
+def _connect_sqlite(state_file: Any, options: Dict[str, Any]) -> sqlite3.Connection:
+    """Connect to the SQLite state database.\n+\n+    The *state_file* may be a ``str`` containing a user‑home shortcut (``~``),\n+    a relative path, or a ``pathlib.Path`` object.  Older DVC versions expanded\n+    the path and created the parent directory before opening the DB; newer\n+    versions omitted this step which caused ``sqlite3.OperationalError`` on\n+    systems where the path was not already absolute or the directory did not\n+    exist.\n+    \"\"\n+\n+    # Convert ``Path`` objects to ``str`` and expand ``~``.\n+    if hasattr(state_file, \"as_posix\"):\n+        # pathlib.Path –> string\n+        path = state_file.as_posix()\n+    else:\n+        path = str(state_file)\n+\n+    # Expand user home and resolve to an absolute path.\n+    path = os.path.abspath(os.path.expanduser(path))\n+\n+    # Ensure the directory that will hold the DB exists.\n+    db_dir = os.path.dirname(path)\n+    if db_dir:\n+        os.makedirs(db_dir, exist_ok=True)\n+\n+    # ``uri=True`` tells sqlite3 to treat the string as a URI; we keep the same\n+    # semantics as the original implementation.\n+    return sqlite3.connect(path, uri=True, **options)\n*** End of File
```

## How to verify
1. **Setup a fresh environment** with DVC 0.71.0 (or the latest version containing the fix).  
2. Clone a repository that uses a home‑directory shortcut for its location, e.g.:

   ```bash
   cd ~
   mkdir test_repo && cd test_repo
   dvc init
   ```

3. Run a DVC command that touches the state DB, such as:

   ```bash
   dvc add some_file.txt -v
   ```

4. Expected outcome:  
   * No traceback, the command finishes successfully.  
   * A file `.dvc/state.db` is created inside the repository (verify with `ls -l .dvc/state.db`).  

5. **Regression test** – run the existing DVC test suite (`pytest -q`). All tests should pass, and the new test added in step 3 should also pass.

If the command succeeds and the state database file is present, the issue is resolved.
--- Proposal B
## Root cause
`dvc` 0.71 switched the internal state storage from a plain file (`.dvc/state`) to an SQLite database (`.dvc/state.db`).  
When an existing repository is upgraded, the new *state directory* (`.dvc/state`) is **not created automatically** (the old file may have been removed during the upgrade).  
`State.load()` tries to open the SQLite file directly:

```python
self.database = _connect_sqlite(self.state_file, {"nolock": 1})
```

If the parent directory does not exist, `sqlite3.connect()` raises  

```
sqlite3.OperationalError: unable to open database file
```

Hence every command that touches the repo state (e.g. `dvc add`) fails with *“unexpected error – unable to open database file”*.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/dvc/state.py
+++ b/dvc/state.py
@@
 class State:
@@
     def load(self):
         """Load the state from the SQLite file."""
-        self.database = _connect_sqlite(self.state_file, {"nolock": 1})
+        # NOTE: In DVC ≥0.71 the state is stored in an SQLite DB
+        # located at ``self.state_file`` (by default ``.dvc/state.db``).
+        # When upgrading from older versions the ``.dvc/state`` directory
+        # may not exist yet, which makes ``sqlite3.connect`` raise
+        # ``OperationalError: unable to open database file``.
+        # Ensure the directory hierarchy exists before trying to open the DB.
+        state_dir = os.path.dirname(self.state_file)
+        if state_dir and not os.path.isdir(state_dir):
+            try:
+                os.makedirs(state_dir, exist_ok=True)
+            except OSError as exc:
+                # Propagate a clear error if we cannot create the directory.
+                raise DvcException(
+                    f"Unable to create DVC state directory '{state_dir}': {exc}"
+                ) from exc
+
+        self.database = _connect_sqlite(self.state_file, {"nolock": 1})
*** End of File ***
--- a/dvc/repo/__init__.py
+++ b/dvc/repo/__init__.py
@@
 class Repo:
@@
-        self.state_file = os.path.join(self.root_dir, ".dvc", "state")
+        # ``state_file`` points to the SQLite DB used by the new state implementation.
+        # Historically DVC stored a plain text file at ``.dvc/state``; the new
+        # implementation stores an SQLite DB at ``.dvc/state.db``.
+        # Keep backward compatibility: if the old file exists, keep using it,
+        # otherwise switch to the new location.
+        old_state = os.path.join(self.root_dir, ".dvc", "state")
+        new_state = os.path.join(self.root_dir, ".dvc", "state.db")
+        self.state_file = new_state if os.path.exists(new_state) else old_state
*** End of File ***
```

### What the patch does
1. **Ensures the state directory exists** before SQLite tries to open the DB, preventing the `unable to open database file` error.
2. **Adds a clear error message** if the directory cannot be created (e.g. permission problems).
3. **Keeps backward compatibility** by detecting an existing legacy state file and falling back to it, avoiding accidental data loss for users who still have the old format.

## How to verify
1. **Setup a fresh environment** (or use the reported failing repo).  
   ```bash
   python - <<'PY'
   import os, shutil, subprocess, sys, venv, pathlib
   # create a temporary dir
   work = pathlib.Path("/tmp/dvc_test_repo")
   shutil.rmtree(work, ignore_errors=True)
   work.mkdir()
   os.chdir(work)
   # initialise a git repo and a DVC repo with an 

=== TASK fronzbot__blinkpy-268 | repo fronzbot/blinkpy | python
--- Issue
Trouble working with 0.16-RC0 - New Login Methods
Hi, I have the new RC up and running but seem to be having trouble getting it to work.  I setup a new routine to basically get and generate a new credential file:

from blinkpy.blinkpy import Blink
from blinkpy.auth import Auth
from blinkpy.helpers.util import json_load

blink = Blink()
#auth = Auth(json_load("c:\Blinkpy\cred.json"))
auth = Auth({"username":"MY EMAIL", "password":"MY PASS"}, no_prompt=False)
blink.auth = auth
blink.start()
blink.save("C:\Blinkpy\cred.json")

On first run it works, I get a verification code, plug it in and my cred.json file is generated.

Then I go to run another method to enable/disable a bunch of cameras, like:
from blinkpy.blinkpy import Blink
from blinkpy.auth import Auth
from blinkpy.helpers.util import json_load
import time

blink = Blink()
auth = Auth(json_load("c:\Blinkpy\cred.json"))
blink.auth = auth
blink.start()
blink.save("C:\Blinkpy\cred.json") <- I also tried to save the cred file at the end, which way shoudl I do it?

blink.cameras["Garage"].set_motion_detect(enable=False)
time.sleep(4)
blink.cameras["Mud Room"].set_motion_detect(enable=False)
time.sleep(4)
blink.cameras["Kitchen"].set_motion_detect(enable=False)
time.sleep(4)
blink.cameras["Dining"].set_motion_detect(enable=False)
time.sleep(4)
... A bunch of other cameras.

It may work once but eventually fails on blink.start or on one of the camera commands to disable motion with:
C:\Blin
--- REAL FIX (ground truth)
diff --git a/blinkpy/auth.py b/blinkpy/auth.py
index 778daf3..2f07048 100644
--- a/blinkpy/auth.py
+++ b/blinkpy/auth.py
@@ -111,12 +111,14 @@ class Auth:
         if not json_resp:
             return response
 
-        json_data = response.json()
         try:
+            json_data = response.json()
             if json_data["code"] in ERROR.BLINK_ERRORS:
                 raise exceptions.ConnectionError
         except KeyError:
             pass
+        except (AttributeError, ValueError):
+            raise BlinkBadResponse
 
         return json_data
 
@@ -161,6 +163,8 @@ class Auth:
                     )
             except (TokenRefreshFailed, LoginError):
                 _LOGGER.error("Endpoint %s failed. Unable to refresh login tokens", url)
+        except BlinkBadResponse:
+            _LOGGER.error("Expected json response, but received: %s", response)
         _LOGGER.error("Endpoint %s failed", url)
         return None
 
@@ -192,3 +196,7 @@ class TokenRefreshFailed(Exception):
 
 class LoginError(Exception):
     """Class to throw failed login exception."""
+
+
+class BlinkBadResponse(Exception):
+    """Class to throw bad json response exception."""

--- Proposal A
## Root cause
`blinkpy.auth.Auth.validate_response` blindly calls `response.json()` assuming the server always returns a JSON payload.  
When the saved authentication token expires (or the server returns a 401/204 with an empty body), the HTTP request returns **no JSON**.  
Calling `response.json()` on such a response raises `json.decoder.JSONDecodeError`, which propagates up to `blink.start()` and aborts the script.  
Because the exception is not caught, the library never attempts to refresh the token or re‑authenticate, leading to the intermittent failures described.

## Fix
Add robust handling in `Auth.validate_response`:

* Detect non‑JSON or empty responses and skip JSON parsing.
* When the status code is not 2xx, raise a clear `AuthenticationError` (or return the raw response) so the caller can decide to re‑login.
* Catch `JSONDecodeError` (and generic `ValueError`) to avoid the uncaught exception.

```diff
diff --git a/blinkpy/auth.py b/blinkpy/auth.py
index 7c9f2a1..d3e4b6c 100644
--- a/blinkpy/auth.py
+++ b/blinkpy/auth.py
@@
 import logging
 import time
 from typing import Any, Dict, Optional
+from json import JSONDecodeError
 
 from .api import http_get, http_post, request_login, request_verify
 from .helpers.util import json_load, json_save
@@
-    def validate_response(self, response, json_resp=None):
-        """Validate the response from the server."""
-        json_data = response.json()
-        if response.status_code != 200:
-            _LOGGER.error("Invalid response: %s", response.text)
-            raise BlinkException("Invalid response from Blink server")
-        if json_resp is not None:
-            json_data.update(json_resp)
-        return json_data
+    def validate_response(self, response, json_resp: Optional[Dict[str, Any]] = None):
+        """
+        Validate the HTTP response from the Blink server.
+
+        The original implementation assumed every successful response contained a
+        JSON body and called ``response.json()`` unconditionally.  In practice the
+        Blink API can return an empty body (e.g. after a token expires or when a
+        401/204 is returned).  Attempting to decode such a body raises
+        ``JSONDecodeError`` which bubbled up as an uncaught exception.
+
+        This method now:
+        * Returns the raw ``requests.Response`` object when the body is empty or
+          not JSON‑parseable.
+        * Raises a clear ``BlinkException`` for non‑2xx status codes.
+        * Merges any additional ``json_resp`` data when a valid JSON payload is
+          present.
+        """
+        # First handle HTTP error codes – they are not recoverable here.
+        if not response.ok:
+            _LOGGER.error(
+                "Invalid response (status %s): %s", response.status_code, response.text
+            )
+            raise BlinkException(
+                f"Invalid response from Blink server (status {response.status_code})"
+            )
+
+        # Try to decode JSON; if it fails (empty body, HTML error page, etc.)
+        # simply return the raw response so callers can decide what to do.
+        try:
+            json_data = response.json()
+        except (JSONDecodeError, ValueError):
+            _LOGGER.debug(
+                "Response did not contain valid JSON (status %s). Returning raw response.",
+                response.status_code,
+            )
+            return response
+
+        # If the caller supplied extra data, merge it.
+        if json_r
--- Proposal B
## Root cause
`auth.validate_response` assumes that every HTTP response from the Blink API can be parsed as JSON:

```python
json_data = response.json()
```

When the authentication token expires (or the API returns a non‑JSON error such as a *429 Too Many Requests* or *401 Unauthorized*), the body is empty or contains plain‑text HTML.  
Calling `response.json()` then raises a `json.decoder.JSONDecodeError`, which propagates up to the user code as an uncaught exception.  
The library never checks the HTTP status code before trying to decode the payload, so token‑expiry or rate‑limit errors cause the “Expecting value” crash you see.

## Fix
Add proper status‑code handling and graceful JSON parsing in `blinkpy/auth.py`.  
If the response is not JSON, raise a clear `BlinkAPIError`.  
If the status code indicates an authentication problem (401/403), clear the stored token so the next call forces a re‑login.

```diff
--- a/blinkpy/auth.py
+++ b/blinkpy/auth.py
@@
-    def validate_response(self, response, json_resp):
-        """Validate the response from the Blink API."""
-        json_data = response.json()
-        if json_resp is not None:
-            json_data.update(json_resp)
-        return json_data
+    def validate_response(self, response, json_resp):
+        """
+        Validate the response from the Blink API.
+
+        The Blink API does not always return JSON – for example when the
+        authentication token has expired or when the service throttles
+        requests.  The original implementation called ``response.json()``
+        unconditionally which raised ``json.decoder.JSONDecodeError`` and
+        caused the library to crash.
+
+        This method now:
+        1. Checks the HTTP status code.
+        2. Handles authentication‑related errors (401/403) by clearing the
+           stored token and raising a dedicated ``BlinkAuthError``.
+        3. Attempts to decode JSON only when the ``Content‑Type`` header
+           indicates a JSON payload; otherwise raises ``BlinkAPIError`` with
+           the raw text for easier debugging.
+        4. Merges any additional ``json_resp`` data supplied by the caller.
+        """
+        # ------------------------------------------------------------------
+        # 1️⃣  Authentication errors – force a re‑login on the next call.
+        # ------------------------------------------------------------------
+        if response.status_code in (401, 403):
+            # Invalidate the stored token so ``blink.start()`` will trigger a
+            # fresh authentication flow.
+            self.auth_token = None
+            raise BlinkAuthError(
+                f"Authentication failed (status {response.status_code}). "
+                "Token may be expired – a new login is required."
+            )
+
+        # ------------------------------------------------------------------
+        # 2️⃣  Non‑JSON responses (e.g., 429 Too Many Requests, HTML error page)
+        # ------------------------------------------------------------------
+        content_type = response.headers.get('Content-Type', '')
+        if 'application/json' not in content_type.lower():
+            raise BlinkAPIError(
+                f"Unexpected non‑JSON response (status {response.status_code}): "
+                f"{response.text[:200]!r}"
+            )
+
+        # ------------------------------------------------------------------
+        # 3️⃣  Safe JSON decoding
+        # ----------------------

=== TASK Textualize__textual-3360 | repo Textualize/textual | python
--- Issue
Restrict Pilot.click to visible area
I suspect that `Pilot.click` will allow clicking of widgets that may not be in the visible area of the screen.

Since we are simulating a real user operating the mouse, we should make that not work. Probably by throwing an exception.

It also looks like it is possible to supply an `offset` that would click outside of the visible area. This should probably also result in an exception.
--- REAL FIX (ground truth)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index c401104de..330dac5bf 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -11,6 +11,15 @@ and this project adheres to [Semantic Versioning](http://semver.org/).
 
 - `Pilot.click`/`Pilot.hover` can't use `Screen` as a selector https://github.com/Textualize/textual/issues/3395
 
+### Added
+
+- `OutOfBounds` exception to be raised by `Pilot` https://github.com/Textualize/textual/pull/3360
+
+### Changed
+
+- `Pilot.click`/`Pilot.hover` now raises `OutOfBounds` when clicking outside visible screen https://github.com/Textualize/textual/pull/3360
+- `Pilot.click`/`Pilot.hover` now return a Boolean indicating whether the click/hover landed on the widget that matches the selector https://github.com/Textualize/textual/pull/3360
+
 ## [0.38.1] - 2023-09-21
 
 ### Fixed
diff --git a/src/textual/pilot.py b/src/textual/pilot.py
index c15441d0b..9069f61a3 100644
--- a/src/textual/pilot.py
+++ b/src/textual/pilot.py
@@ -16,6 +16,7 @@ import rich.repr
 from ._wait import wait_for_idle
 from .app import App, ReturnType
 from .events import Click, MouseDown, MouseMove, MouseUp
+from .geometry import Offset
 from .widget import Widget
 
 
@@ -44,6 +45,10 @@ def _get_mouse_message_arguments(
     return message_arguments
 
 
+class OutOfBounds(Exception):
+    """Raised when the pilot mouse target is outside of the (visible) screen."""
+
+
 class WaitForScreenTimeout(Exception):
     """Exception raised if messages aren't being processed quickly enough.
 
@@ -83,19 +88,30 @@ class Pilot(Generic[ReturnType]):
         shift: bool = False,
         meta: bool = False,
         control: bool = False,
-    ) -> None:
-        """Simulate clicking with the mouse.
+    ) -> bool:
+        """Simulate clicking with the mouse at a specified position.
+
+        The final position to be clicked is computed based on the selector provided and
+        the offset specified and it must be within the visible area of the screen.
 
         Args:
-            selector: The widget that should be clicked. If None, then the click
-                will occur relative to the screen. Note that this simply causes
-                a click to occur at the location of the widget. If the widget is
-                currently hidden or obscured by another widget, then the click may
-                not land on it.
-            offset: The offset to click within the selected widget.
+            selector: A selector to specify a widget that should be used as the reference
+                for the click offset. If this is not specified, the offset is interpreted
+                relative to the screen. You can use this parameter to try to click on a
+                specific widget. However, if the widget is currently hidden or obscured by
+                another widget, the click may not land on the widget you specified.
+            offset: The offset to click. The offset is relative to the selector provided
+                or to the screen, if no s
--- Proposal A
## Root cause
`Pilot.click` calculates a click position for the target widget and sends the mouse events directly to the driver.  
The implementation never checks whether the calculated point is actually **visible** on the screen (i.e. whether the widget is currently rendered in the viewport).  
If a widget is scrolled out of view, hidden by another widget, or the supplied `offset` moves the click outside the widget’s region, the driver will still synthesize a `Click` event. This makes the pilot behave like a user could click on invisible parts of the UI, which is not the intended behaviour.

## Fix
Add validation in `Pilot.click` (and `Pilot.hover`, which shares the same logic) to ensure that the final click coordinates:

1. Lie inside the widget’s own region (taking the optional `offset` into account).  
2. Resolve back to the same widget when queried through `App.get_widget_at`, i.e. the widget is actually visible at that screen location.

If either check fails, raise a `ValueError` with a clear message.

```diff
diff --git a/src/textual/pilot.py b/src/textual/pilot.py
index 9c2f1e7..d4b8a3f 100644
--- a/src/textual/pilot.py
+++ b/src/textual/pilot.py
@@
 class Pilot(Generic[ReturnType]):
@@
-    def click(
-        self,
-        selector: str | None = None,
-        button: int = 1,
-        offset: tuple[int, int] | None = None,
-    ) -> ReturnType:
-        """Click on a widget.
-
-        Args:
-            selector: CSS selector for the widget to click.
-            button: Mouse button to use (1 = left, 2 = middle, 3 = right).
-            offset: Optional (x, y) offset from the centre of the widget.
-
-        Returns:
-            The return value of the widget's click handler, if any.
-        """
-        app = self.app
-        screen = app.screen
-        if selector is not None:
-            target_widget = app.query_one(selector)
-        else:
-            target_widget = screen
-
-        # Resolve the click position
-        region = target_widget.region
-        x = region.x + region.width // 2
-        y = region.y + region.height // 2
-        if offset is not None:
-            x += offset[0]
-            y += offset[1]
-
-        # Perform the click
-        self.mouse_down(button=button, x=x, y=y)
-        self.mouse_up(button=button, x=x, y=y)
-
-        # Return whatever the widget's click handler returned
-        return target_widget._click_result  # type: ignore[attr-defined]
+    def click(
+        self,
+        selector: str | None = None,
+        button: int = 1,
+        offset: tuple[int, int] | None = None,
+    ) -> ReturnType:
+        """Click on a widget, but only if the click would be on a *visible* part
+        of the widget.
+
+        The method now validates that the calculated click coordinates are
+        inside the widget’s region **and** that the widget is actually the one
+        returned by ``App.get_widget_at`` for those coordinates.  If either
+        condition fails a :class:`ValueError` is raised – this mirrors the
+        behaviour of a real user who cannot click on invisible UI elements.
+
+        Args:
+            selector: CSS selector for the widget to click.
+            button: Mouse button to use (1 = left, 2 = middle, 3 = right).
+            offset: Optional ``(x, y)`` offset from the centre of the widget.
+
+        Returns:
+            The return value of the widget's click handler, if any.
+        """
+        app = self.app
+        screen = app.screen
+
--- Proposal B
## Root cause
`Pilot.click` currently calculates a click position based only on the widget’s region and an optional offset.  
It does **not** verify that the widget is actually visible on the screen (i.e. that the widget’s region is within the current terminal size and that the widget is not hidden).  
Consequently a test can “click” a widget that is off‑screen or hidden, which does not reflect real user behaviour.  
The same problem exists for an explicit `offset` that points outside the widget’s visible bounds.

## Fix
Add validation inside `Pilot.click`:

* Ensure the widget is visible (`widget.visible` is `True`).
* Ensure the widget’s region lies inside the current screen size.
* If an `offset` is supplied, make sure the resulting click point is still inside the widget’s region.
* If any of these checks fail, raise a `ValueError` (the same exception type used elsewhere in the pilot for invalid actions).

```diff
--- a/textual/_pilot.py
+++ b/textual/_pilot.py
@@
 class Pilot:
@@
-    async def click(
-        self,
-        widget: "Widget",
-        button: int = 1,
-        modifiers: Optional[Set[Key]] = None,
-        offset: Optional[Tuple[int, int]] = None,
-    ) -> None:
-        \"\"\"Simulate a mouse click on *widget*.\n-
-
-        *button* is the mouse button (1‑left, 2‑middle, 3‑right).\n-
-        *modifiers* is a set of :class:`~textual.keys.Key` values.\n-
-        *offset* is an optional ``(x, y)`` tuple relative to the widget’s\n-        top‑left corner.\n-        \"\"\"
-        region = widget.region
-        if region is None:
-            raise ValueError(\"Widget has no region – it may not be mounted.\")
-
-        # Default to centre of the widget if no offset is supplied
-        if offset is None:
-            x = region.x + region.width // 2
-            y = region.y + region.height // 2
-        else:
-            x = region.x + offset[0]
-            y = region.y + offset[1]
-
-        await self._mouse_move(x, y)
-        await self._mouse_down(button, modifiers)
-        await self._mouse_up(button, modifiers)
+    async def click(
+        self,
+        widget: "Widget",
+        button: int = 1,
+        modifiers: Optional[Set[Key]] = None,
+        offset: Optional[Tuple[int, int]] = None,
+    ) -> None:
+        \"\"\"Simulate a mouse click on *widget*.\n+\n+        The click is only allowed if the widget is **visible** and the\n+        calculated click point lies inside the widget’s region **and** inside\n+        the current screen bounds.  If any of these conditions are not met a\n+        :class:`ValueError` is raised – this mirrors the behaviour of other\n+        pilot methods that validate their arguments.\n+\n+        Parameters\n+        ----------\n+        widget:\n+            The widget to click.\n+        button:\n+            Mouse button (1‑left, 2‑middle, 3‑right).\n+        modifiers:\n+            Optional set of :class:`~textual.keys.Key` values.\n+        offset:\n+            Optional ``(x, y)`` tuple relative to the widget’s top‑left corner.\n+        \"\"\n+        # ------------------------------------------------------------------\n+        # Basic sanity checks – the widget must be mounted and visible.\n+        # ------------------------------------------------------------------\n+        region = widget.region\n+        if region is None:\n+            raise ValueError(\"Widget has no region – it may not be mounted.\")\n+\n+        if not getattr(widget, \"visible\

=== TASK kytos__python-openflow-280 | repo kytos/python-openflow | python
--- Issue
OF 1.3 Compliance: Missing attribute
File: `v0x04/controller2switch/common.py`

`class DecStats(GenericStruct):`

Following attribute is missing:
`dp_desc = Char(length=DESC_STR_LEN)`
--- REAL FIX (ground truth)
diff --git a/pyof/v0x04/common/action.py b/pyof/v0x04/common/action.py
index 9b550a2..78d6004 100644
--- a/pyof/v0x04/common/action.py
+++ b/pyof/v0x04/common/action.py
@@ -72,7 +72,6 @@ class ActionType(Enum):
     OFPAT_POP_PBB = 27
     #: Experimenter type
     OFPAT_EXPERIMENTER = 0xffff
-    OFPAT_VENDOR = 0xffff
 
 
 class ControllerMaxLen(Enum):
@@ -254,6 +253,8 @@ class ActionPopMPLS(GenericStruct):
     length = UBInt16(8)
     #: Ethertype
     ethertype = UBInt16()
+    #: Padding
+    pad = Pad(2)
 
     def __init__(self, ethertype=None):
         """Action structure for OFPAT_POP_MPLS.
diff --git a/pyof/v0x04/common/port.py b/pyof/v0x04/common/port.py
index db3b41c..41b5be3 100644
--- a/pyof/v0x04/common/port.py
+++ b/pyof/v0x04/common/port.py
@@ -44,7 +44,7 @@ class PortNo(Enum):
     #: Wildcard port used only for flow mod (delete) and flow stats requests.
     #: Selects all flows regardless of output port (including flows with no
     #: output port).
-    OFPP_NONE = 0xffffffff
+    OFPP_ANY = 0xffffffff
 
 
 class PortConfig(GenericBitMask):
diff --git a/pyof/v0x04/controller2switch/common.py b/pyof/v0x04/controller2switch/common.py
index bebb8b2..42a4a46 100644
--- a/pyof/v0x04/controller2switch/common.py
+++ b/pyof/v0x04/controller2switch/common.py
@@ -251,9 +251,11 @@ class DescStats(GenericStruct):
     sw_desc = Char(length=DESC_STR_LEN)
     #: Serial number
     serial_num = Char(length=SERIAL_NUM_LEN)
+    #: Datapath description
+    dp_desc = Char(length=DESC_STR_LEN)
 
     def __init__(self, mfr_desc=None, hw_desc=None, sw_desc=None,
-                 serial_num=None):
+                 serial_num=None, dp_desc=None):
         """The constructor just assings parameters to object attributes.
 
         Args:
@@ -261,12 +263,14 @@ class DescStats(GenericStruct):
             hw_desc (str): Hardware description
             sw_desc (str): Software description
             serial_num (str): Serial number
+            dp_desc (str): Datapath description
         """
         super().__init__()
         self.mfr_desc = mfr_desc
         self.hw_desc = hw_desc
         self.sw_desc = sw_desc
         self.serial_num = serial_num
+        self.dp_desc = dp_desc
 
 
 class FlowStats(GenericStruct):
diff --git a/pyof/v0x04/controller2switch/packet_out.py b/pyof/v0x04/controller2switch/packet_out.py
index e23fc87..1520f85 100644
--- a/pyof/v0x04/controller2switch/packet_out.py
+++ b/pyof/v0x04/controller2switch/packet_out.py
@@ -13,7 +13,7 @@ __all__ = ('PacketOut',)
 # Classes
 
 #: in_port valid virtual port values, for validation
-_VIRT_IN_PORTS = (PortNo.OFPP_LOCAL, PortNo.OFPP_CONTROLLER, PortNo.OFPP_NONE)
+_VIRT_IN_PORTS = (PortNo.OFPP_LOCAL, PortNo.OFPP_CONTROLLER, PortNo.OFPP_ANY)
 
 
 class PacketOut(GenericMessage):

--- Proposal A
## Root cause
`DecStats` (the structure that represents the *Description* statistics in the OpenFlow 1.3 `multipart` reply) is missing the `dp_desc` field.  
According to the OpenFlow 1.3 specification a description stats reply must contain five string fields, each of length `DESC_STR_LEN`:

* `mfr_desc`
* `hw_desc`
* `sw_desc`
* `serial_num`
* **`dp_desc`** – the datapath description (missing)

Because the attribute is not defined, packing/unpacking of a description‑stats message fails the compliance tests and the field is never exposed to the user.

## Fix
Add the missing `dp_desc` attribute to `DecStats` in `pyof/v0x04/controller2switch/common.py`.  
The other string fields already use the `Char` type with the constant `DESC_STR_LEN`; we follow the same pattern.

```diff
diff --git a/pyof/v0x04/controller2switch/common.py b/pyof/v0x04/controller2switch/common.py
index 1234567..89abcde 100644
--- a/pyof/v0x04/controller2switch/common.py
+++ b/pyof/v0x04/controller2switch/common.py
@@
 class DecStats(GenericStruct):
@@
-    mfr_desc = Char(length=DESC_STR_LEN)
-    hw_desc = Char(length=DESC_STR_LEN)
-    sw_desc = Char(length=DESC_STR_LEN)
-    serial_num = Char(length=DESC_STR_LEN)
+    mfr_desc = Char(length=DESC_STR_LEN)
+    hw_desc = Char(length=DESC_STR_LEN)
+    sw_desc = Char(length=DESC_STR_LEN)
+    serial_num = Char(length=DESC_STR_LEN)
+    # NOTE: OpenFlow 1.3 adds a datapath description field. It was omitted
+    # in the original implementation, causing a compliance failure.
+    dp_desc = Char(length=DESC_STR_LEN)
```

* No other files need to be changed – the import of `Char` and `DESC_STR_LEN` already exists at the top of the module.
* The comment explains why the field is added and helps future maintainers.

## How to verify
1. **Run the library’s test suite** – especially the OpenFlow 1.3 multipart description‑stats tests (`tests/v0x04/...`). All tests should now pass.
2. **Manual packing/unpacking check**  
   ```python
   from pyof.v0x04.controller2switch.common import DecStats
   from pyof.foundation.basic_types import Char
   from pyof.v0x04.common.constants import DESC_STR_LEN

   stats = DecStats()
   stats.mfr_desc = Char(b'Manufacturer')
   stats.hw_desc = Char(b'Hardware')
   stats.sw_desc = Char(b'Software')
   stats.serial_num = Char(b'123456')
   stats.dp_desc = Char(b'Datapath')
   packed = stats.pack()
   unpacked = DecStats()
   unpacked.unpack(packed)
   assert unpacked.dp_desc.value == b'Datapath'
   ```
   The assertion must succeed, confirming that the field is correctly serialized and deserialized.

3. **Compliance check** – run any external OpenFlow 1.3 compliance tools used by the project (e.g., `tox -e compliance`) to ensure the description‑stats message now conforms to the spec.
--- Proposal B
## Root cause
`DecStats` (the structure that represents *Description Statistics* in the OpenFlow 1.3
controller‑to‑switch messages) is missing the `dp_desc` field.  
According to the OF 1.3 specification the description stats must contain the
following string fields (all of length `DESC_STR_LEN`):

* `mfr_desc`
* `hw_desc`
* `sw_desc`
* `serial_num`
* **`dp_desc`** – datapath description

Because the attribute is absent, any attempt to serialize or deserialize a
`DecStats` instance that includes a datapath description fails, breaking OF 1.3
compliance tests.

## Fix
Add the missing attribute to the `DecStats` definition and import the constant
`DESC_STR_LEN` if it is not already in the module’s namespace.

```diff
--- a/kytos/python-openflow/kytos/of/core/v0x04/controller2switch/common.py
+++ b/kytos/python-openflow/kytos/of/core/v0x04/controller2switch/common.py
@@
 from pyof.foundation.base import GenericStruct
 from pyof.foundation.basic_types import Char, UBInt32, UBInt64, FixedTypeList
 from pyof.v0x04.common.constants import OFP_MAX_TABLE_NAME_LEN, OFP_MAX_PORT_NAME_LEN
+from pyof.v0x04.common.constants import DESC_STR_LEN   # <-- added import
@@
 class DecStats(GenericStruct):
@@
     mfr_desc = Char(length=DESC_STR_LEN)
     hw_desc = Char(length=DESC_STR_LEN)
     sw_desc = Char(length=DESC_STR_LEN)
     serial_num = Char(length=DESC_STR_LEN)
+    dp_desc = Char(length=DESC_STR_LEN)   # <-- added missing attribute
     n_buffers = UBInt32()
     n_tables = UBInt8()
     capabilities = UBInt32()
```

* If `DESC_STR_LEN` is already imported elsewhere in the file, the added import
  line can be omitted – the diff keeps it safe by explicitly importing it.

## How to verify
1. **Run the project's test suite** – especially the OF 1.3 compliance tests
   (`tests/of/v0x04/...`). All tests should now pass.

2. **Manual serialization check**  
   ```python
   from kytos.of.core.v0x04.controller2switch.common import DecStats
   from kytos.of.core.v0x04.common.constants import DESC_STR_LEN

   ds = DecStats()
   ds.dp_desc = "My datapath"
   raw = ds.pack()
   # unpack again
   ds2 = DecStats()
   ds2.unpack(raw)
   assert ds2.dp_desc == "My datapath"
   ```
   The pack/unpack round‑trip must succeed without raising an exception.

3. **OpenFlow 1.3 compliance validation** – use a known OF 1.3‑compliant
   controller (e.g., `ryu` or `of-test`) to request description stats from a
   Kytos switch instance. The response should now contain the `dp_desc` field
   correctly populated.

After these steps the missing attribute issue is resolved and the library
conforms to the OF 1.3 specification.

=== TASK PyCQA__flake8-bugbear-316 | repo PyCQA/flake8-bugbear | python
--- Issue
New check: visit_ function with no generic_visit/visit
This is kind of super niche, and mostly just of interest to flake8 plugin writers in particular, but I accidentally forgot to continue `visit`'ing in a couple functions I wrote recently in flake8-trio and realized it'd be incredibly handy - and very easy to implement - an optional check where if a function name matches `visit_*` it raises a warning if there's no `self.visit(...)` or `self.generic_visit(...)` within it's body.

I imagine it'd be nice to have that in the CI for flake8-bugbear itself as well, even though your structure makes it less likely than it is for flake8-trio currently.
--- REAL FIX (ground truth)
diff --git a/README.rst b/README.rst
index 498b000..53a3dab 100644
--- a/README.rst
+++ b/README.rst
@@ -205,6 +205,10 @@ to raise a ``ValueError`` if the arguments are exhausted at differing lengths. T
 was added in Python 3.10, so don't enable this flag for code that should work on <3.10.
 For more information: https://peps.python.org/pep-0618/
 
+**B906**: ``visit_`` function with no further call to a ``visit`` function. This is often an error, and will stop the visitor from recursing into the subnodes of a visited node. Consider adding a call ``self.generic_visit(node)`` at the end of the function.
+Will only trigger on function names where the part after ``visit_`` is a valid ``ast`` type with a non-empty ``_fields`` attribute.
+This is meant to be enabled by developers writing visitors using the ``ast`` module, such as flake8 plugin writers.
+
 **B950**: Line too long. This is a pragmatic equivalent of
 ``pycodestyle``'s ``E501``: it considers "max-line-length" but only triggers
 when the value has been exceeded by **more than 10%**. You will no
@@ -302,6 +306,11 @@ MIT
 Change Log
 ----------
 
+Future
+~~~~~~~~~
+
+* Add B906: ``visit_`` function with no further calls to a ``visit`` function. (#313)
+
 22.12.6
 ~~~~~~~~~
 
diff --git a/bugbear.py b/bugbear.py
index 2d2b7cd..90d238a 100644
--- a/bugbear.py
+++ b/bugbear.py
@@ -413,6 +413,7 @@ class BugBearVisitor(ast.NodeVisitor):
         self.check_for_b018(node)
         self.check_for_b019(node)
         self.check_for_b021(node)
+        self.check_for_b906(node)
         self.generic_visit(node)
 
     def visit_ClassDef(self, node):
@@ -969,6 +970,27 @@ class BugBearVisitor(ast.NodeVisitor):
         ):
             self.errors.append(B905(node.lineno, node.col_offset))
 
+    def check_for_b906(self, node: ast.FunctionDef):
+        if not node.name.startswith("visit_"):
+            return
+
+        # extract what's visited, only error if it's a valid ast subclass
+        # with a non-empty _fields attribute - which is what's iterated over in
+        # ast.NodeVisitor.generic_visit
+        class_name = node.name[len("visit_") :]
+        class_type = getattr(ast, class_name, None)
+        if class_type is None or not getattr(class_type, "_fields", None):
+            return
+
+        for n in itertools.chain.from_iterable(ast.walk(nn) for nn in node.body):
+            if isinstance(n, ast.Call) and (
+                (isinstance(n.func, ast.Attribute) and "visit" in n.func.attr)
+                or (isinstance(n.func, ast.Name) and "visit" in n.func.id)
+            ):
+                break
+        else:
+            self.errors.append(B906(node.lineno, node.col_offset))
+
 
 def compose_call_path(node):
     if isinstance(node, ast.Attribute):
@@ -990,7 +1012,7 @@ class NameFinder(ast.NodeVisitor):
 
     names = attr.ib(default=attr.Factory(dict))
 
-    def visit_Name(self, node):
+    def visit_Name(self, node):  # noqa: B906 # names don't contain other names
        
--- Proposal A
## Root cause
`flake8-bugbear` does not currently check that a method whose name matches the pattern `visit_*` actually performs a visitor call (`self.visit(...)` or `self.generic_visit(...)`).  This can let a plugin author forget to continue the AST traversal, which is a common source of bugs when writing `ast.NodeVisitor`‑based plugins.

## Fix
Add a new check that walks every `FunctionDef` node, and when the function:

* is a method (first argument is named `self`), **and**
* its name starts with `visit_`,

it verifies that the function body contains at least one call of the form `self.visit(...)` or `self.generic_visit(...)`.  
If no such call is found, emit a new BugBear warning `B950`.

```diff
--- a/flake8_bugbear/checks.py
+++ b/flake8_bugbear/checks.py
@@
 from collections import defaultdict
 import ast
 import re
@@
 from . import utils
@@
 class BugBearVisitor(ast.NodeVisitor):
@@
     def __init__(self, tree, filename, options):
         self.tree = tree
         self.filename = filename
         self.options = options
         self.errors = []
         self._seen = defaultdict(list)
@@
         self.generic_visit(node)
         self._check_unused_imports(node)
         self._check_unused_variables(node)
+
+    # ------------------------------------------------------------------
+    # New check: visit_* methods must call self.visit or self.generic_visit
+    # ------------------------------------------------------------------
+    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # pragma: no cover
+        """
+        If a method is named ``visit_*`` and its first argument is ``self``,
+        ensure that the body contains a call to ``self.visit`` or
+        ``self.generic_visit``.  If not, emit ``B950``.
+        """
+        # Continue walking the tree first so that nested definitions are also
+        # visited (the check itself does not need to stop traversal).
+        self.generic_visit(node)
+
+        # Only consider methods (first argument named ``self``) – free functions
+        # named ``visit_*`` are ignored.
+        if not node.args.args:
+            return
+        first_arg = node.args.args[0]
+        if not isinstance(first_arg, ast.arg) or first_arg.arg != "self":
+            return
+
+        # Name must start with ``visit_``.
+        if not node.name.startswith("visit_"):
+            return
+
+        # Walk the function body looking for a call ``self.visit`` or
+        # ``self.generic_visit``.
+        has_visit_call = False
+        for child in ast.walk(node):
+            if isinstance(child, ast.Call):
+                func = child.func
+                if (
+                    isinstance(func, ast.Attribute)
+                    and isinstance(func.value, ast.Name)
+                    and func.value.id == "self"
+                    and func.attr in ("visit", "generic_visit")
+                ):
+                    has_visit_call = True
+                    break
+
+        if not has_visit_call:
+            self.error_from_node(
+                node,
+                "B950",
+                "B950 visit_* method missing a call to self.visit or self.generic_visit",
+            )
*** End of File
--- a/flake8_bugbear/__init__.py
+++ b/flake8_bugbear/__init__.py
@@
 __all__ = ["BugbearChecker"]
@@
 # Mapping of error codes to human‑readable messages.
 # Existing codes omitted for brevity …
+ERRORS = {
+    # ... existing error definitions ...
+    "B950": "visit_* method m
--- Proposal B
## Root cause
`flake8-bugbear` does not currently verify that methods whose name starts with `visit_` (i.e. visitor methods) actually continue the AST traversal by calling `self.visit(...)` or `self.generic_visit(...)`.  Plugin authors can easily forget to add such a call, which makes their visitor silently stop walking the tree.  The issue requests an optional check that emits a warning when a `visit_*` method lacks any call to `self.visit` or `self.generic_visit`.

## Fix
Add a new check (code **B951**) that inspects every `ClassDef` node, looks for `FunctionDef` members whose name starts with `visit_`, and reports a warning if the function body does not contain a call to `self.visit` or `self.generic_visit`.

```diff
diff --git a/bugbear.py b/bugbear.py
index 8f47666..d3c9a1e 100644
--- a/bugbear.py
+++ b/bugbear.py
@@
 class BugBearChecker(ast.NodeVisitor):
@@
     def __init__(self, tree, filename, options):
         self.tree = tree
         self.filename = filename
         self.options = options
         self.errors = []
+        # Stack of class definitions we are currently inside.
+        # Used by the new B951 check to know whether a function is a method.
+        self._class_stack = []
@@
     def error(self, lineno, col_offset, msg):
         self.errors.append((lineno, col_offset, msg, type(self)))
 
@@
-    def visit_ClassDef(self, node):
-        self.generic_visit(node)
+    def visit_ClassDef(self, node):
+        """
+        Visit a class definition.
+
+        In addition to the normal generic traversal we also run the new
+        B951 check: for any method whose name starts with ``visit_`` we verify
+        that the method body contains a call to ``self.visit`` or
+        ``self.generic_visit``.  If not, we emit a B951 warning.
+        """
+        # Keep track of nesting so that we can tell whether a FunctionDef is a
+        # method (i.e. defined inside a class).
+        self._class_stack.append(node)
+        # First run the existing generic traversal so that other checks still
+        # see the class body.
+        self.generic_visit(node)
+
+        # Run the B951 visitor‑method check.
+        for stmt in node.body:
+            if isinstance(stmt, ast.FunctionDef) and stmt.name.startswith("visit_"):
+                if not self._function_calls_self_visit(stmt):
+                    self.error(
+                        stmt.lineno,
+                        stmt.col_offset,
+                        f"B951 function '{stmt.name}' defined as a visitor but does not call self.visit or self.generic_visit",
+                    )
+
+        self._class_stack.pop()
+
+    # ------------------------------------------------------------------
+    # Helper for B951
+    # ------------------------------------------------------------------
+    def _function_calls_self_visit(self, func_node: ast.FunctionDef) -> bool:
+        """
+        Return ``True`` if *func_node* contains a call of the form
+        ``self.visit(...)`` or ``self.generic_visit(...)``.
+        """
+        for n in ast.walk(func_node):
+            if isinstance(n, ast.Call):
+                # ``self.visit`` or ``self.generic_visit``
+                if isinstance(n.func, ast.Attribute):
+                    if (
+                        isinstance(n.func.value, ast.Name)
+                        and n.func.value.id == "self"
+                        and n.func.attr in ("visit", "generic_visit")
+                    ):
+                      

=== TASK PyCQA__flake8-1440 | repo PyCQA/flake8 | python
--- Issue
add color support for formatting
~essentially replace `flake8-colors`

(needs a bit of design before implementation)
--- REAL FIX (ground truth)
diff --git a/docs/source/user/options.rst b/docs/source/user/options.rst
index faeb17e..9cdb0ee 100644
--- a/docs/source/user/options.rst
+++ b/docs/source/user/options.rst
@@ -40,6 +40,8 @@ Index of Options
 
 - :option:`flake8 --quiet`
 
+- :option:`flake8 --color`
+
 - :option:`flake8 --count`
 
 - :option:`flake8 --diff`
@@ -181,6 +183,35 @@ Options and their Descriptions
 
         quiet = 1
 
+.. option:: --color
+
+    :ref:`Go back to index <top>`
+
+    Whether to use color in output. Defaults to ``auto``.
+
+    Possible options are ``auto``, ``always``, and ``never``.
+
+    This **can** be specified in config files.
+
+    When color is enabled, the following substitutions are enabled:
+
+    - ``%(bold)s``
+    - ``%(black)s``
+    - ``%(red)s``
+    - ``%(green)s``
+    - ``%(yellow)s``
+    - ``%(blue)s``
+    - ``%(magenta)s``
+    - ``%(cyan)s``
+    - ``%(white)s``
+    - ``%(reset)s``
+
+    Example config file usage:
+
+    .. code-block:: ini
+
+        color = never
+
 
 .. option:: --count
 
diff --git a/src/flake8/formatting/_windows_color.py b/src/flake8/formatting/_windows_color.py
new file mode 100644
index 0000000..1d2c73f
--- /dev/null
+++ b/src/flake8/formatting/_windows_color.py
@@ -0,0 +1,59 @@
+"""ctypes hackery to enable color processing on windows.
+
+See: https://github.com/pre-commit/pre-commit/blob/cb40e96/pre_commit/color.py
+"""
+import sys
+
+if sys.platform == "win32":  # pragma: no cover (windows)
+
+    def _enable() -> None:
+        from ctypes import POINTER
+        from ctypes import windll
+        from ctypes import WinError
+        from ctypes import WINFUNCTYPE
+        from ctypes.wintypes import BOOL
+        from ctypes.wintypes import DWORD
+        from ctypes.wintypes import HANDLE
+
+        STD_ERROR_HANDLE = -12
+        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 4
+
+        def bool_errcheck(result, func, args):
+            if not result:
+                raise WinError()
+            return args
+
+        GetStdHandle = WINFUNCTYPE(HANDLE, DWORD)(
+            ("GetStdHandle", windll.kernel32),
+            ((1, "nStdHandle"),),
+        )
+
+        GetConsoleMode = WINFUNCTYPE(BOOL, HANDLE, POINTER(DWORD))(
+            ("GetConsoleMode", windll.kernel32),
+            ((1, "hConsoleHandle"), (2, "lpMode")),
+        )
+        GetConsoleMode.errcheck = bool_errcheck
+
+        SetConsoleMode = WINFUNCTYPE(BOOL, HANDLE, DWORD)(
+            ("SetConsoleMode", windll.kernel32),
+            ((1, "hConsoleHandle"), (1, "dwMode")),
+        )
+        SetConsoleMode.errcheck = bool_errcheck
+
+        # As of Windows 10, the Windows console supports (some) ANSI escape
+        # sequences, but it needs to be enabled using `SetConsoleMode` first.
+        #
+        # More info on the escape sequences supported:
+        # https://msdn.microsoft.com/en-us/library/windows/desktop/mt638032(v=vs.85).aspx
+        stderr = GetStdHandle(STD_ERROR_HANDLE)
+        flags = GetConsoleMode(stderr
--- Proposal A
## Root cause
`flake8` prints its results using the built‑in *default* formatter, which always emits plain text.  There is no option to enable ANSI colour codes, so users who previously relied on the external `flake8‑colors` plugin cannot get coloured output out‑of‑the‑box.  The formatter therefore needs a small redesign to optionally wrap parts of the output (filename, line/column numbers, error code, message) in colour when the user requests it (or when output is a TTY).

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/flake8/options.py
+++ b/flake8/options.py
@@
 def add_options(parser):
@@
     parser.add_argument(
         "--statistics",
         action="store_true",
         help="Show statistics about the errors/warnings reported.",
     )
+
+    # ------------------------------------------------------------------
+    # Colour output support (new)
+    # ------------------------------------------------------------------
+    colour_group = parser.add_mutually_exclusive_group()
+    colour_group.add_argument(
+        "--color",
+        dest="color",
+        action="store_true",
+        default=False,
+        help="Enable coloured output (requires a TTY).",
+    )
+    colour_group.add_argument(
+        "--no-color",
+        dest="color",
+        action="store_false",
+        help="Disable coloured output (default).",
+    )
*** End of File
--- a/flake8/main.py
+++ b/flake8/main.py
@@
 from flake8 import defaults, utils
 from flake8.formatting import base as formatting_base
 from flake8.formatting import default as formatting_default
 from flake8.formatting import json as formatting_json
+# colour support
+try:
+    import colorama
+except Exception:  # pragma: no cover – colour is optional
+    colorama = None
@@
 def main(argv=None):
@@
     options = parser.parse_args(argv)
@@
-    # Initialise the formatter
-    formatter = formatting_default.DefaultFormatter(options)
+    # Initialise colour handling (if requested)
+    if options.color and colorama is not None:
+        # Initialise colourama on Windows; on POSIX this is a no‑op.
+        colorama.init()
+
+    # Initialise the formatter (colour handling is delegated to the formatter)
+    formatter = formatting_default.DefaultFormatter(options)
*** End of File
--- a/flake8/formatting/default.py
+++ b/flake8/formatting/default.py
@@
 from __future__ import annotations
@@
 from flake8.formatting import base
@@
 class DefaultFormatter(base.BaseFormatter):
@@
     def format(self, error: Error) -> str:
-        return f"{error.filename}:{error.line_number}:{error.column_number}: {error.code} {error.text}"
+        # If colour is enabled and we are writing to a TTY, wrap parts of the
+        # output in ANSI colour codes.  The colour choice mirrors the behaviour
+        # of the historic ``flake8‑colors`` plugin.
+        if getattr(self.options, "color", False) and self._is_tty():
+            # Lazy import to avoid pulling colourama unless needed.
+            try:
+                from colorama import Fore, Style
+            except Exception:  # pragma: no cover
+                # Fallback to plain output if colourama is unavailable.
+                return (
+                    f"{error.filename}:{error.line_number}:{error.column_number}: "
+                    f"{error.code} {error.text}"
+                )
+
+            filename = f"{Fore.CYAN}{error.filename}{Style.RESET_ALL}"
+            line_col = (
+                f"{For
--- Proposal B
## Root cause
`flake8` ships only with plain‑text formatters (e.g. *default*, *pylint*, *json*).  
The external plugin **flake8‑colors** provides a colourised output, but it is
installed separately and therefore not available in a minimal installation.
The issue requests built‑in colour support so that users can obtain a coloured
report simply by selecting a new formatter (e.g. `--format=colored`) without
installing an extra plugin.

## Fix
* Add a new built‑in formatter **colored** that prints the same information as
  the default formatter but decorates the parts with ANSI colour codes.
* Register the formatter with the internal plugin manager so that it appears
  in the dynamically generated `--format` help text.
* Keep the implementation lightweight and avoid new runtime dependencies –
  colour codes are emitted directly and `colorama` is used only when available
  (to enable Windows console support).

```diff
diff --git a/src/flake8/main/application.py b/src/flake8/main/application.py
index 13ece4e..c9f2b3a 100644
--- a/src/flake8/main/application.py
+++ b/src/flake8/main/application.py
@@
 from flake8 import defaults, utils
 from flake8.options.manager import OptionManager
 from flake8.plugins.finder import PluginFinder
 from flake8.plugins.manager import PluginManager
+from flake8.reporting.color import ColorReport   # <-- new import
 
 
 class Application:
@@
         self.plugins = PluginManager()
         self.option_manager = OptionManager(
@@
         self.plugins.register_reporters()
         self.plugins.register_options(self.option_manager)
 
         # Register built‑in reporters (default, pylint, …)
         self.plugins.register_reporter("default", self.plugins.reporters["default"])
         self.plugins.register_reporter("pylint", self.plugins.reporters["pylint"])
         self.plugins.register_reporter("json", self.plugins.reporters["json"])
+        # New built‑in colourised formatter
+        self.plugins.register_reporter("colored", ColorReport)
 
         # Register built‑in formatters (default, …) – they are the same objects
         # as the reporters; the name is just an alias.
         self.plugins.register_formatter("default", self.plugins.reporters["default"])
         self.plugins.register_formatter("pylint", self.plugins.reporters["pylint"])
         self.plugins.register_formatter("json", self.plugins.reporters["json"])
+        self.plugins.register_formatter("colored", ColorReport)
```

```diff
diff --git a/src/flake8/reporting/color.py b/src/flake8/reporting/color.py
new file mode 100644
--- /dev/null
+++ b/src/flake8/reporting/color.py
@@
+"""
+Colourised formatter for Flake8.
+
+This formatter mirrors the behaviour of the built‑in ``default`` formatter but
+adds ANSI colour codes to make the output easier to read in a terminal.
+
+The implementation deliberately avoids adding a hard dependency on an external
+colour library.  It emits raw escape sequences and, if the optional *colorama*
+package is present, initialises it so that colours work on Windows terminals.
+"""
+
+from __future__ import annotations
+
+import sys
+from typing import Any
+
+from flake8.reporting.base import BaseReport
+
+# Simple ANSI colour helpers -------------------------------------------------
+RESET = "\033[0m"
+RED = "\033[31m"
+YELLOW = "\033[33m"
+CYAN = "\033[36m"
+
+
+def _maybe_init_colorama() -> None:
+    """
+    Initialise *colorama* if it is installed.  The import is optional – the
+    formatter works without

=== TASK postlund__pyatv-978 | repo postlund/pyatv | python
--- Issue
Add retries for heartbeats (MRP)
**What feature would you like?**
When a heartbeat fails, an error is logged and the connection is closed. Generally this is good, but in case the response happens to be delayed long enough (even though nothing is wrong), this is quite pessimistic and it's probably a good idea to make another try before failing. Do not impose an initial delay on the retry though (just retry immediately) as we still want to fail as early as possible in case of an actual problem.

**Describe the solution you'd like**
From the description above: make a retry immediately when a heartbeat fails. Only log error if retry also fails.

**Any other information to share?**
Heartbeat errors seems to happen sporadically and I believe there's not actually a problem in most cases. This is an attempt to recover in those cases, but it's still not a very well tested method so I can't be sure that it works.
--- REAL FIX (ground truth)
diff --git a/pyatv/mrp/protocol.py b/pyatv/mrp/protocol.py
index 9e31abcf..c152b2d3 100644
--- a/pyatv/mrp/protocol.py
+++ b/pyatv/mrp/protocol.py
@@ -14,6 +14,7 @@ from pyatv.mrp.srp import Credentials
 _LOGGER = logging.getLogger(__name__)
 
 HEARTBEAT_INTERVAL = 30
+HEARTBEAT_RETRIES = 1  # One regular attempt + retries
 
 Listener = namedtuple("Listener", "func data")
 OutstandingMessage = namedtuple("OutstandingMessage", "semaphore response")
@@ -23,10 +24,14 @@ async def heartbeat_loop(protocol):
     """Periodically send heartbeat messages to device."""
     _LOGGER.debug("Starting heartbeat loop")
     count = 0
+    attempts = 0
     message = messages.create(protobuf.ProtocolMessage.SEND_COMMAND_MESSAGE)
     while True:
         try:
-            await asyncio.sleep(HEARTBEAT_INTERVAL)
+            # Re-attempts are made with no initial delay to more quickly
+            # recover a failed heartbeat (if possible)
+            if attempts == 0:
+                await asyncio.sleep(HEARTBEAT_INTERVAL)
 
             _LOGGER.debug("Sending periodic heartbeat %d", count)
             await protocol.send_and_receive(message)
@@ -34,11 +39,18 @@ async def heartbeat_loop(protocol):
         except asyncio.CancelledError:
             break
         except Exception:
-            _LOGGER.exception(f"heartbeat {count} failed")
-            protocol.connection.close()
-            break
+            attempts += 1
+            if attempts > HEARTBEAT_RETRIES:
+                _LOGGER.error(f"heartbeat {count} failed after {attempts} tries")
+                protocol.connection.close()
+                break
+            else:
+                _LOGGER.debug(f"heartbeat {count} failed")
         else:
+            attempts = 0
+        finally:
             count += 1
+
     _LOGGER.debug("Stopping heartbeat loop at %d", count)
 
 

--- Proposal A
## Root cause
The MRP implementation closes the connection as soon as a heartbeat request raises an exception.  
A transient network hiccup or a delayed response can cause a single heartbeat to fail even though the
connection is still healthy. Because the failure is treated as fatal, the client disconnects
prematurely. The code never gives the remote side a second chance to answer the heartbeat.

## Fix
Add a single immediate retry when a heartbeat fails. Only if the retry also fails do we log an
error and close the connection.

```diff
diff --git a/pyatv/mrp/connection.py b/pyatv/mrp/connection.py
index 3b2c1e7..e5f9a4b 100644
--- a/pyatv/mrp/connection.py
+++ b/pyatv/mrp/connection.py
@@
 class MRPConnection:
@@
     async def _heartbeat(self) -> None:
         """Send a heartbeat to keep the connection alive."""
-        try:
-            await self._protocol.send_heartbeat()
-        except Exception as exc:  # pylint: disable=broad-except
-            _LOGGER.error("Heartbeat failed: %s", exc)
-            await self.close()
+        # The original implementation treated any exception as fatal and closed the
+        # connection immediately.  In practice a single missed heartbeat is often
+        # caused by a temporary delay, so we give the remote side one more chance
+        # before giving up.
+        try:
+            await self._protocol.send_heartbeat()
+        except Exception as exc:  # pylint: disable=broad-except
+            # First attempt failed – try once more immediately.
+            _LOGGER.debug("Heartbeat failed, retrying once: %s", exc)
+            try:
+                await self._protocol.send_heartbeat()
+            except Exception as exc2:  # pylint: disable=broad-except
+                # Second attempt also failed – now we consider the connection broken.
+                _LOGGER.error("Heartbeat failed after retry: %s", exc2)
+                await self.close()
+            else:
+                # Retry succeeded – continue normal operation.
+                _LOGGER.debug("Heartbeat retry succeeded")
+        # If the first attempt succeeded we simply return; the periodic task will
+        # schedule the next heartbeat as before.
```

*Explanation of changes*

1. **Added retry logic** – after the first `send_heartbeat()` raises, we immediately call it a second time.
2. **Logging** – a `debug` message for the first failure and a second `debug` when the retry succeeds; an `error` is only emitted if the retry also fails.
3. **Behaviour unchanged for successful heartbeats** – the method returns normally when the first call succeeds.

## How to verify
1. **Unit test** – add a test that mocks `MRPProtocol.send_heartbeat` to raise an exception on the first call and succeed on the second.  
   * Expected outcome: the connection stays open and no error is logged.
2. **Failure test** – mock `send_heartbeat` to raise on both calls.  
   * Expected outcome: an error is logged and `MRPConnection.close()` is invoked.
3. Run the full test suite: `pytest -q`. All existing tests must still pass and the new tests should succeed.
4. Manual verification (optional): run a real Apple TV with a flaky network (e.g., introduce a short latency spike) and observe that the client does not disconnect after a single delayed heartbeat. The log should show a “Heartbeat failed, retrying once” debug line followed by normal operation.
--- Proposal B
## Root cause
The MRP implementation closes the connection as soon as a heartbeat request times‑out.  
When the remote device simply replies a little later (e.g. because of a temporary pause), the single‑shot timeout is too aggressive and results in an unnecessary disconnect. The code never gives the heartbeat a second chance, so a transient delay always leads to a closed session and an error log.

## Fix
Add a single immediate retry for a failed heartbeat.  
* Keep a per‑connection flag (`_heartbeat_retried`) that is cleared after each successful heartbeat.  
* On the first timeout, resend the heartbeat immediately and set the flag.  
* If the heartbeat times‑out again (i.e. the flag is already set) log the error and close the connection as before.  

```diff
--- a/pyatv/mrp/connection.py
+++ b/pyatv/mrp/connection.py
@@
 class MRPConnection:
     def __init__(self, ...):
         ...
-        self._heartbeat_task = None
+        # Heartbeat handling
+        self._heartbeat_task = None
+        # ``True`` when we have already retried a failed heartbeat.
+        # It is cleared after every successful heartbeat.
+        self._heartbeat_retried = False
@@
     async def _send_heartbeat(self) -> None:
         """Send a heartbeat request to the device."""
         try:
@@
         except Exception as exc:  # pragma: no cover – exercised via integration tests
-            _LOGGER.error("Heartbeat failed: %s", exc)
-            await self._close()
+            # First failure – try once more before giving up
+            if not self._heartbeat_retried:
+                _LOGGER.debug("Heartbeat failed (%s), retrying once", exc)
+                self._heartbeat_retried = True
+                # Resend the heartbeat immediately without waiting for the next interval
+                await self._send_heartbeat()
+                return
+
+            # Second failure – give up
+            _LOGGER.error("Heartbeat failed after retry: %s", exc)
+            await self._close()
@@
     async def _handle_heartbeat_response(self, response: mrp_pb2.HeartbeatResponse) -> None:
         """Process a successful heartbeat response."""
-        self._heartbeat_retried = False
+        # Reset retry flag on a successful heartbeat so the next timeout can retry again
+        self._heartbeat_retried = False
         # (existing handling code continues …)
```

### What the patch does
1. **State tracking** – adds `_heartbeat_retried` to remember whether we have already performed a retry for the current heartbeat cycle.
2. **Retry logic** – on the first exception while sending a heartbeat we log at *debug* level, set the retry flag, and immediately call `_send_heartbeat()` again.
3. **Failure handling** – if the second attempt also raises, we log an *error* (as before) and close the connection.
4. **Reset** – when a heartbeat response is successfully received the retry flag is cleared, allowing the next timeout to retry again.

## How to verify
1. **Unit test** – add a test that mocks the transport so the first heartbeat send raises a timeout exception and the second succeeds.  
   *Assert* that the connection stays open and no error is logged, and that `_heartbeat_retried` is cleared after the successful response.

2. **Failure path test** – mock the transport so both the first and the retry raise.  
   *Assert* that an error is logged, `_close()` is called, and the connection is marked closed.

3. **Integration test** – run the existing test‑suite (`pytest 

=== TASK PyCQA__isort-1717 | repo PyCQA/isort | python
--- Issue
Line break and tab indentation makes wrong fix
`isort` causes odd fixing with import line which contains line berak and tab indentation.

before `isort` (Notice that this code uses Tab indentation, not spaces.)

```py
from sys \
	import version
print(version)
```

after `isort`
```py
from sys import importversion

print(version)
```

expected
```py
from sys import version

print(version)
```

### Versions
- macOS Big Sur 11.2.3
- Python 3.9.4
- isort 5.7.0
--- REAL FIX (ground truth)
diff --git a/isort/parse.py b/isort/parse.py
index 714e39ab..2a15c90f 100644
--- a/isort/parse.py
+++ b/isort/parse.py
@@ -293,6 +293,7 @@ def file_contents(contents: str, config: Config = DEFAULT_CONFIG) -> ParsedConte
             else:
                 while line.strip().endswith("\\"):
                     line, new_comment = parse_comments(in_lines[index])
+                    line = line.lstrip()
                     index += 1
                     if new_comment:
                         comments.append(new_comment)

--- Proposal A
## Root cause
`isort` joins lines that end with a back‑slash (`\`) **without inserting a separating space**.  
When a multiline import uses a back‑slash and the continuation line is indented with a tab, the
joined string becomes:

```
from sys importversion
```

During the later parsing step the word `import` is interpreted as part of the imported name,
producing the broken output `from sys import importversion`.

## Fix
* Add a small helper that correctly joins back‑slash continuations, inserting a single space
  between the parts.
* Call this helper at the start of `file_contents` (the function that turns the raw file
  contents into a list of normalized lines) so that the rest of the parser works on the
  correctly‑joined lines.
* Keep the existing tab‑to‑space normalisation.

```diff
diff --git a/isort/parse.py b/isort/parse.py
index 7dfd7cef..c3f9a1b2 100644
--- a/isort/parse.py
+++ b/isort/parse.py
@@
 from collections import OrderedDict, defaultdict
 from functools import partial
 from itertools import chain
 from typing import Iterator, NamedTuple, Optional, TextIO, Tuple
+from typing import List
 
 from .settings import Config, DEFAULT_CONFIG
 from .utils import split_comment_from_line, split_trailing_comment
 
 
@@
 def _infer_line_separator(contents: str) -> str:
     return "\n"
 
 
-def _normalize_line(raw_line: str) -> Tuple[str, str]:
+def normalize_line(raw_line: str) -> Tuple[str, str]:
@@
     line = line.replace("\t", " ")
-    return (line, raw_line)
+    return (line, raw_line)
 
 
+def _join_line_continuations(raw_lines: List[str]) -> List[str]:
+    """
+    Join lines that end with a back‑slash (``\\``) into a single logical line.
+    A single space is inserted between the parts to avoid token merging
+    (e.g. ``importversion``).
+    """
+    joined: List[str] = []
+    buffer: str = ""
+    for raw in raw_lines:
+        stripped = raw.rstrip("\n")
+        if stripped.endswith("\\"):
+            # Remove the back‑slash and keep a trailing space for the next part.
+            buffer += stripped[:-1] + " "
+        else:
+            if buffer:
+                # Continuation existed – prepend the current line (lstrip to drop
+                # indentation that belongs only to the continuation).
+                joined.append(buffer + stripped.lstrip())
+                buffer = ""
+            else:
+                joined.append(stripped)
+    # If the file ends with a continuation line, keep what we have.
+    if buffer:
+        joined.append(buffer.rstrip())
+    return joined
+
+
 def file_contents(contents: str, config: Config = DEFAULT_CONFIG) -> ParsedContent:
@@
-    lines = contents.splitlines()
+    # Split the raw file into physical lines first.
+    raw_lines = contents.splitlines()
+
+    # Join any back‑slash continuations *before* any other normalisation.
+    lines = _join_line_continuations(raw_lines)
 
     # Detect line separator used in the original file.
     line_separator = _infer_line_separator(contents)
@@
-                line, _raw_line = _normalize_line(statement)
+                line, _raw_line = normalize_line(statement)
*** End of File
```

### What changed
1. **Renamed** `_normalize_line` to the public `normalize_line` (already used elsewhere).
2. **Added** `_join_line_continuations` that:
   * Detects lines ending with `\`.
   * Removes the back‑slash, adds a single space, and concatenates the next line (with leading
     indentation stripped).
   * Guarantees a space bet
--- Proposal B
## Root cause
`isort` builds import statements by naïvely concatenating lines that are split with a back‑slash (`\`).  
When the continuation line is indented with a **tab**, the tab character is kept after the back‑slash is removed.  
The resulting string looks like:

```
"from sys \timport version"
```

During the later tokenisation the tab is not stripped, so the two identifiers are glued together and `isort` produces the malformed import:

```
from sys import importversion
```

The bug lives in the helper that normalises line‑continuations – it only removes the back‑slash and the newline but does **not** strip the indentation that follows the newline.

## Fix
Add a small utility that removes a back‑slash continuation **and** any leading whitespace (spaces or tabs) on the next line, replacing the whole sequence with a single space.  
Update the import‑parsing code to use this utility.

```diff
--- a/isort/code.py
+++ b/isort/code.py
@@
 import re
 from typing import List, Tuple
 
+_BACKSLASH_CONTINUATION_RE = re.compile(r'\\\s*\n\s*')
+
+def _remove_backslash_continuations(line: str) -> str:
+    """
+    Replace a back‑slash line‑continuation (``\\``) together with the newline
+    and any indentation that follows it with a single space.
+
+    This normalises constructs such as::
+
+        from sys \\
+            import version
+
+    into ``"from sys import version"`` regardless of whether the continuation
+    line is indented with spaces or tabs.
+    """
+    # ``\\`` + optional whitespace + newline + optional whitespace (including tabs)
+    # → single space
+    return _BACKSLASH_CONTINUATION_RE.sub(' ', line)
+
 # Existing helpers ------------------------------------------------------------
 
 def _strip_comments(line: str) -> str:
@@
 def _split_import_line(line: str) -> Tuple[str, List[str]]:
@@
-    # Remove any back‑slash line continuations – they are already handled by the
-    # tokenizer, but we keep this for safety.
-    line = line.replace("\\\n", " ")
+    # Remove any back‑slash line continuations – they are already handled by the
+    # tokenizer, but we keep this for safety.  The helper also strips the
+    # indentation that may follow the continuation (including tabs), which
+    # prevents the “importversion” bug.
+    line = _remove_backslash_continuations(line)
*** End of File
```

### What was changed
1. **Added a compiled regular expression** (`_BACKSLASH_CONTINUATION_RE`) that matches a back‑slash, optional whitespace, a newline, and any following whitespace (spaces or tabs).
2. **Implemented `_remove_backslash_continuations`** – replaces the whole match with a single space, effectively normalising the line.
3. **Updated `_split_import_line`** to call the new helper instead of the previous naïve `replace("\\\n", " ")`.

## How to verify
1. **Run the failing example**  

   ```bash
   echo -e "from sys \\\n\timport version\nprint(version)" > sample.py
   isort sample.py
   cat sample.py
   ```

   Expected output:

   ```python
   from sys import version

   print(version)
   ```

2. **Run the project's test suite** – the new helper is pure and should not affect other behaviours.

   ```bash
   pytest -q
   ```

   All tests should pass.

3. **Add a dedicated regression test** (optional but recommended) in `tests/test_backslash_continuation.py`:

   ```python
   def test_backslash_continuation_with_tab(tmp_path):
       src = "from sys \\\n\timport version\nprint(version)\n"
       expected = "from sys 

=== TASK iterative__dvc-3156 | repo iterative/dvc | python
--- Issue
get: show url to cache
In some cases, users need a direct link to a data file in the cloud. It might be just a matter of convenience or when DVC is used from automation tools like CD4ML scenarios. We need to provide a way to get a direct link.

I suggest creating a new command `dvc resolve`. Other options can be also be considered. For example, it might be a part of `dvc status` (when it start supporting data files as argument).

```
$ dvc resolve file.txt df.csv
file.txt s3://dvc-temp/dataset1/2d10f00a05f1fe70eaa3c42aa9f44b95
df.csv s3://dvc-temp/dataset1/99370a5386cac00c93c9fdc836076a7d
```

It should support revisions (checksum, tag, branch or HEAD^^):
```
$ dvc resolve file.txt df.csv --rev ada9d973
file.txt s3://dvc-temp/dataset1/d30abac6950583e6a5f1dcd31cb1043b
df.csv gs://project42/cache/2290babda371e52eeca2a2065a358783
```

Note, the commands with `revision` options should resolve old remotes as well (see `s3://` and `gs://` above).

--- REAL FIX (ground truth)
diff --git a/dvc/api.py b/dvc/api.py
--- a/dvc/api.py
+++ b/dvc/api.py
@@ -10,7 +10,7 @@
 from voluptuous import Schema, Required, Invalid
 
 from dvc.repo import Repo
-from dvc.exceptions import DvcException
+from dvc.exceptions import DvcException, NotDvcRepoError
 from dvc.external_repo import external_repo
 
 
@@ -43,13 +43,27 @@ class SummonError(DvcException):
     pass
 
 
+class UrlNotDvcRepoError(DvcException):
+    """Thrown if given url is not a DVC repository.
+
+    Args:
+        url (str): url to the repository.
+    """
+
+    def __init__(self, url):
+        super().__init__("URL '{}' is not a dvc repository.".format(url))
+
+
 def get_url(path, repo=None, rev=None, remote=None):
     """Returns an url of a resource specified by path in repo"""
-    with _make_repo(repo, rev=rev) as _repo:
-        abspath = os.path.join(_repo.root_dir, path)
-        out, = _repo.find_outs_by_path(abspath)
-        remote_obj = _repo.cloud.get_remote(remote)
-        return str(remote_obj.checksum_to_path_info(out.checksum))
+    try:
+        with _make_repo(repo, rev=rev) as _repo:
+            abspath = os.path.join(_repo.root_dir, path)
+            out, = _repo.find_outs_by_path(abspath)
+            remote_obj = _repo.cloud.get_remote(remote)
+            return str(remote_obj.checksum_to_path_info(out.checksum))
+    except NotDvcRepoError:
+        raise UrlNotDvcRepoError(repo)
 
 
 def open(path, repo=None, rev=None, remote=None, mode="r", encoding=None):
diff --git a/dvc/command/get.py b/dvc/command/get.py
--- a/dvc/command/get.py
+++ b/dvc/command/get.py
@@ -10,7 +10,27 @@
 
 
 class CmdGet(CmdBaseNoRepo):
+    def _show_url(self):
+        from dvc.api import get_url
+
+        try:
+            url = get_url(
+                self.args.path, repo=self.args.url, rev=self.args.rev
+            )
+            logger.info(url)
+        except DvcException:
+            logger.exception("failed to show url")
+            return 1
+
+        return 0
+
     def run(self):
+        if self.args.show_url:
+            return self._show_url()
+
+        return self._get_file_from_repo()
+
+    def _get_file_from_repo(self):
         from dvc.repo import Repo
 
         try:
@@ -55,4 +75,9 @@ def add_parser(subparsers, parent_parser):
     get_parser.add_argument(
         "--rev", nargs="?", help="Git revision (e.g. branch, tag, SHA)"
     )
+    get_parser.add_argument(
+        "--show-url",
+        action="store_true",
+        help="Returns path/url to the location in remote for specified path",
+    )
     get_parser.set_defaults(func=CmdGet)

--- Proposal A
## Root cause
`dvc resolve` (or an equivalent feature) does not exist.  
Users need a way to obtain the exact remote URL that points to the cached
object for a tracked file (including support for a specific revision).  
Without a command, they have to manually look up the file’s checksum and
construct the remote URL themselves, which is error‑prone and inconvenient
for automation.

## Fix
Add a new CLI command **`dvc resolve`** that:

* Accepts one or more workspace paths.
* Optionally takes `--rev` (any Git‑compatible revision specifier).
* For each path, determines the DVC checksum at the requested revision,
  resolves the default remote (or a named remote via `--remote` in the future),
  and prints the full remote URL that points directly to the cached object.

The implementation re‑uses existing DVC APIs:
* `Repo.get_outs_by_path(..., rev=rev)` to obtain the `Output` object and its
  `hash_info`.
* `repo.cloud.get_remote()` to obtain the default remote.
* `Remote.hash_to_path(hash)` to get the relative cache path.
* The remote’s `url` attribute to build the final URL.

### Files changed
* **`dvc/command/resolve.py`** – new command implementation.
* **`dvc/cli.py`** – register the new sub‑command.

```diff
diff --git a/dvc/command/resolve.py b/dvc/command/resolve.py
new file mode 100644
--- /dev/null
+++ b/dvc/command/resolve.py
@@
+"""Implementation of the ``dvc resolve`` command.
+
+The command prints the remote URL that points directly to the cached object
+corresponding to a tracked file (or files).  It supports an optional ``--rev``
+argument to resolve the file as it existed in a specific Git revision.
+"""
+
+from __future__ import annotations
+
+import sys
+from typing import List, Optional
+
+from dvc.repo import Repo
+from dvc.exceptions import DvcException
+
+
+def _hash_to_remote_url(repo: Repo, checksum: str) -> str:
+    """
+    Convert a checksum (hash) to the full remote URL where the object is stored.
+
+    The function uses the repository's default remote (``repo.cloud.get_remote()``)
+    and the remote's ``hash_to_path`` helper to build the URL.
+    """
+    remote = repo.cloud.get_remote()
+    # ``hash_to_path`` returns a POSIX‑style relative path inside the remote cache.
+    rel_path = remote.hash_to_path(checksum)
+    # Ensure there is exactly one ``/`` between the remote base URL and the relative path.
+    base = remote.url.rstrip("/")
+    return f"{base}/{rel_path}"
+
+
+def _resolve_path(repo: Repo, path: str, rev: Optional[str]) -> str:
+    """
+    Resolve a single workspace path to its remote URL.
+
+    Parameters
+    ----------
+    repo:
+        The active DVC repository.
+    path:
+        Path to a DVC‑tracked file (relative to the repository root).
+    rev:
+        Optional Git revision (branch, tag, commit, etc.).  If ``None`` the
+        current workspace revision is used.
+    """
+    # ``get_outs_by_path`` returns a list of Output objects that match the path.
+    # For a regular file there will be exactly one output.
+    outs = repo.get_outs_by_path(path, rev=rev)
+    if not outs:
+        raise DvcException(f"'{path}' is not a DVC‑tracked file.")
+
+    out = outs[0]
+    if out.hash_info is None or out.hash_info.value is None:
+        raise DvcException(f"Unable to determine checksum for '{path}'.")
+
+    return _hash_to_remote_url(repo, out.hash_info.value)
+
+
+def resolve(args) -> int:
+    """
+    Entry point for the ``dvc resolve`` command.
+    """
+    repo = Repo(
--- Proposal B
## Root cause
DVC does not have a user‑facing command that translates a tracked file (or a list of files) into the exact remote URL where the corresponding cached object lives.  
The functionality already exists internally – `Repo.get_url()` can compute the remote URL for a given path and revision – but it is only used by the internal `dvc get-url` command. Because no public CLI entry point calls this helper, users cannot obtain a direct link to the cached object, which is what the issue requests.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
diff --git a/dvc/command/__init__.py b/dvc/command/__init__.py
index 1234567..89abcde 100644
--- a/dvc/command/__init__.py
+++ b/dvc/command/__init__.py
@@
 from .add import add
 from .checkout import checkout
 from .commit import commit
 from .config import config
 from .diff import diff
+from .resolve import resolve   # <-- new import
 from .fetch import fetch
 from .gc import gc
 from .import_cmd import import_cmd
@@
     cli.add_command(commit)
     cli.add_command(config)
     cli.add_command(diff)
+    cli.add_command(resolve)      # <-- expose the new command
     cli.add_command(fetch)
     cli.add_command(gc)
     cli.add_command(import_cmd)
```

```diff
diff --git a/dvc/command/resolve.py b/dvc/command/resolve.py
new file mode 100644
index 0000000..e1f2d3c
--- /dev/null
+++ b/dvc/command/resolve.py
@@
+"""Implementation of the ``dvc resolve`` command.
+
+The command prints the remote URL that points to the cached object for each
+provided DVC‑tracked file.  It mirrors the behaviour of the internal
+``Repo.get_url`` helper and adds optional ``--rev`` support so that users can
+resolve URLs for historic revisions (commit SHA, tag, branch, etc.).
+"""
+
+from __future__ import annotations
+
+import click
+
+from dvc.repo import Repo
+from dvc.exceptions import DvcException
+
+
+@click.command()
+@click.argument(
+    "paths",
+    nargs=-1,
+    required=True,
+    type=click.Path(exists=False, dir_okay=False, resolve_path=True),
+)
+@click.option(
+    "--rev",
+    default=None,
+    help=(
+        "Revision (commit SHA, tag, branch, or any git rev‑spec). "
+        "If omitted, the current workspace is used."
+    ),
+)
+def resolve(paths: tuple[str, ...], rev: str | None) -> None:
+    """Show the remote URL for each DVC‑tracked file.
+
+    Example::
+
+        $ dvc resolve data/file.txt model.pkl
+        data/file.txt s3://my-bucket/dvc-cache/2d10f00a05f1fe70eaa3c42aa9f44b95
+        model.pkl   gs://my-bucket/dvc-cache/99370a5386cac00c93c9fdc836076a7d
+
+    The command works with the ``--rev`` option to resolve URLs for historic
+    revisions as well.
+    """
+    # ``Repo`` implements the context‑manager protocol which ensures that any
+    # temporary resources (e.g. a temporary Git worktree for a different rev)
+    # are cleaned up correctly.
+    with Repo() as repo:
+        for path in paths:
+            try:
+                # ``Repo.get_url`` already knows how to:
+                #   * locate the .dvc file or the .dvc.yaml entry,
+                #   * read the stored checksum for the requested revision,
+                #   * prepend the appropriate remote URL prefix.
+                url = repo.get_url(path, rev=rev)
+                click.echo(f"{path} {url}")
+            except DvcException as exc:
+                # Keep the output format consistent – print the path followed
+                # by the error message on *stderr*.
+     

=== TASK iterative__dvc-7687 | repo iterative/dvc | python
--- Issue
push/pull: globs that match no targets should fail
When using `--glob` with `push` or `pull`, a glob that matches no targets will instead act on all targets. In other words, `dvc push/pull --glob 'nomatches*'` will be the same as `dvc push/pull/repro` without any target argument.

```
$ mkdir repo
$ mkdir dvcremote
$ cd repo
$ git init
Initialized empty Git repository in /private/tmp/glob/repo/.git/
$ dvc init -q
$ dvc remote add -d default ../dvcremote
Setting 'default' as a default remote.
$ echo foo > foo
$ dvc add foo
100% Adding...|████████████████████████████████████████████████|1/1 [00:00, 52.98file/s]

To track the changes with git, run:

    git add foo.dvc .gitignore

To enable auto staging, run:

        dvc config core.autostage true
$ dvc push --glob 'z*'
1 file pushed
$ rm foo
$ dvc pull --glob 'z*'
A       foo
1 file added
```

I would expect the commands to fail or push/pull nothing if there are no `--glob` matches.

--- REAL FIX (ground truth)
diff --git a/dvc/utils/__init__.py b/dvc/utils/__init__.py
--- a/dvc/utils/__init__.py
+++ b/dvc/utils/__init__.py
@@ -476,17 +476,25 @@ def is_exec(mode):
 
 
 def glob_targets(targets, glob=True, recursive=True):
+    from ..exceptions import DvcException
+
     if not glob:
         return targets
 
     from glob import iglob
 
-    return [
+    results = [
         exp_target
         for target in targets
         for exp_target in iglob(target, recursive=recursive)
     ]
 
+    if not results:
+        msg = f"Glob {targets} has no matches."
+        raise DvcException(msg)
+
+    return results
+
 
 def error_handler(func):
     def wrapper(*args, **kwargs):

--- Proposal A
## Root cause
`dvc push` / `dvc pull` accept a `--glob` option that is turned into a list of targets inside the command implementation.  
The existing code treats an empty result of the glob expansion as “no filter”, i.e. it passes `None` (or an empty list) to the repository layer which then operates on **all** tracked files. Consequently a glob that matches nothing silently pushes/pulls the whole workspace instead of failing.

## Fix
* Resolve the glob pattern **before** calling the repository sync methods.
* If the glob is supplied and the expansion yields no matches, abort the command with a non‑zero exit code and an explanatory error message.
* Apply the same logic to `push`, `pull` and `fetch` (the three commands that support `--glob`).

```diff
diff --git a/dvc/command/data_sync.py b/dvc/command/data_sync.py
index 7b83789d2..c3f9a1e4b 100644
--- a/dvc/command/data_sync.py
+++ b/dvc/command/data_sync.py
@@
 class CmdDataBase(CmdBase):
-    @classmethod
-    def check_up_to_date(cls, processed_files_count):
-        if processed_files_count == 0:
-            logger.info("Everything is up to date.")
+    @classmethod
+    def _resolve_targets(cls, repo, args):
+        """
+        Resolve ``args.targets`` and ``args.glob`` into a concrete list of
+        targets that will be passed to the repository sync methods.
+
+        If ``--glob`` is used and the pattern matches no DVC files, the
+        command should fail instead of falling back to “all targets”.
+        """
+        # Explicit targets have priority – they are already resolved.
+        if args.targets:
+            return args.targets
+
+        # No explicit targets, but a glob pattern was supplied.
+        if args.glob:
+            # ``repo.find_targets_by_glob`` returns a list of DVC file paths
+            # that match the glob.  It is part of the public Repo API.
+            matches = repo.find_targets_by_glob(args.glob)
+            if not matches:
+                logger.error(
+                    f"No DVC targets match the glob pattern '{args.glob}'."
+                )
+                # Returning ``None`` signals the caller to abort with a
+                # non‑zero exit status.
+                return None
+            return matches
+
+        # Neither explicit targets nor a glob – operate on the whole repo.
+        return None
+
+    @classmethod
+    def check_up_to_date(cls, processed_files_count):
+        if processed_files_count == 0:
+            logger.info("Everything is up to date.")
 
 
 class CmdDataPull(CmdDataBase):
-    def run(self):
+    def run(self):
         try:
-            processed_files_count = self.repo.pull(
-                targets=self.args.targets,
+            targets = self._resolve_targets(self.repo, self.args)
+            if targets is None and self.args.glob:
+                # Glob was supplied but matched nothing → abort.
+                return 1
+
+            processed_files_count = self.repo.pull(
+                targets=targets,
                 jobs=self.args.jobs,
                 remote=self.args.remote,
                 show_checksums=self.args.show_checksums,
                 verify=self.args.verify,
                 all_branches=self.args.all_branches,
                 all_tags=self.args.all_tags,
                 all_commits=self.args.all_commits,
                 with_deps=self.args.with_deps,
                 recursive=self.args.recursive,
                 force=self.args.force,
                 re
--- Proposal B
## Root cause
`dvc push` and `dvc pull` accept a `--glob` option that expands to a list of matching DVC‑tracked files.  
The implementation that resolves the command‑line arguments treats an *empty* list of matches as “no targets were supplied”, and therefore falls back to the default behaviour of operating on **all** targets. Consequently a glob that matches nothing silently pushes/pulls the whole repository instead of failing.

## Fix
* Detect the case where a glob pattern was supplied but the expansion yields no matches.  
* In that situation raise a user‑friendly error (via `click.ClickException`) so the command aborts with a non‑zero exit status.  
* Apply the same logic to both `push` and `pull` (they share the same target‑resolution helper).

```diff
--- a/dvc/command/base.py
+++ b/dvc/command/base.py
@@
-def resolve_targets(repo, args):
-    """Resolve command line targets and glob patterns to a list of DVC files."""
-    # If explicit targets are given, use them.
-    if args.targets:
-        return args.targets
-
-    # If a glob pattern is supplied, expand it.
-    if args.glob:
-        matches = repo.find_targets_by_glob(args.glob)
-        # NOTE: an empty list is interpreted as “no targets”, which falls back
-        # to the default behaviour of operating on all DVC files.
-        return matches
-
-    # No targets or globs – operate on the whole repo.
-    return None
+def resolve_targets(repo, args):
+    """Resolve command line targets and glob patterns to a list of DVC files.
+
+    Returns:
+        * ``None`` – when no explicit targets or glob are supplied (operate on
+          the whole repository, the historic behaviour).
+        * ``list`` – a list of matching DVC files when explicit targets or a
+          glob pattern are supplied.
+
+    Raises:
+        click.ClickException: if a ``--glob`` pattern is supplied but it does
+          not match any DVC‑tracked files.  This makes the command fail fast
+          instead of silently falling back to the “all‑targets” mode.
+    """
+    # Explicit targets have priority.
+    if args.targets:
+        return args.targets
+
+    # Handle glob patterns.
+    if args.glob:
+        matches = repo.find_targets_by_glob(args.glob)
+        if not matches:
+            # The user asked for a specific glob, but nothing matched.
+            # Raising ClickException aborts the command with a clear error
+            # message and a non‑zero exit code.
+            raise click.ClickException(
+                f"No DVC targets match the glob pattern '{args.glob}'."
+            )
+        return matches
+
+    # No targets and no glob – historic default (operate on the whole repo).
+    return None
```

The same helper is used by the `push` and `pull` commands, so the change automatically fixes both.

If the project does not already import `click` in `base.py`, add it at the top:

```diff
--- a/dvc/command/base.py
+++ b/dvc/command/base.py
@@
-import sys
+import sys
+import click
```

No further modifications to `push.py` or `pull.py` are required because they already call `resolve_targets`.

## How to verify
1. **Re‑run the reproducer from the issue**  

   ```bash
   $ mkdir repo && cd repo
   $ git init -q
   $ dvc init -q
   $ dvc remote add -d default ../dvcremote
   $ echo foo > foo
   $ dvc add foo
   $ dvc push --glob 'z*'
   ```
   Expected outcome: the command exits with a non‑zero status and prints  
   `Error: No DVC targets match the glob pattern 'z*'.`

2. 

=== TASK spectacles-ci__spectacles-179 | repo spectacles-ci/spectacles | python
--- Issue
Failing SQL is not saved to file
After some architectural changes, failing SQL queries are no longer being logged to file. We need to reintroduce this feature. It should probably live in `cli.py`, using the value of the `sql` key in the dictionary returned by the runner.

Previously, in single-dimension mode, spectacles would create a folder per explore with a `.sql` file for each dimension query. In batch mode, it would create a `.sql` file per explore in the base directory.

These files were all created in the `logs` directory.
--- REAL FIX (ground truth)
diff --git a/spectacles/cli.py b/spectacles/cli.py
index 9b7a275..15f244f 100644
--- a/spectacles/cli.py
+++ b/spectacles/cli.py
@@ -5,17 +5,14 @@ from yaml.parser import ParserError
 import argparse
 import logging
 import os
-from typing import Callable, Iterable, List
+from typing import Callable, Iterable, List, Optional
 from spectacles import __version__
 from spectacles.runner import Runner
 from spectacles.client import LookerClient
-from spectacles.exceptions import SpectaclesException, ValidationError
-from spectacles.logger import GLOBAL_LOGGER as logger, FileFormatter
+from spectacles.exceptions import SpectaclesException, ValidationError, SqlError
+from spectacles.logger import GLOBAL_LOGGER as logger, set_file_handler
 import spectacles.printer as printer
 
-LOG_FILENAME = "spectacles.log"
-LOG_FILEPATH = Path()
-
 
 class ConfigFileAction(argparse.Action):
     """Parses an arbitrary config file and assigns its values as arg defaults."""
@@ -142,7 +139,7 @@ def handle_exceptions(function: Callable) -> Callable:
             logger.debug(error, exc_info=True)
             logger.error(
                 f'Encountered unexpected {error.__class__.__name__}: "{error}"\n'
-                f"Full error traceback logged to {LOG_FILEPATH}\n\n"
+                f"Full error traceback logged to file.\n\n"
                 + printer.dim(
                     "For support, please create an issue at "
                     "https://github.com/spectacles-ci/spectacles/issues"
@@ -154,23 +151,6 @@ def handle_exceptions(function: Callable) -> Callable:
     return wrapper
 
 
-def set_file_handler(directory: str) -> None:
-
-    global LOG_FILEPATH
-
-    log_directory = Path(directory)
-    LOG_FILEPATH = Path(log_directory / LOG_FILENAME)
-    log_directory.mkdir(exist_ok=True)
-
-    fh = logging.FileHandler(LOG_FILEPATH)
-    fh.setLevel(logging.DEBUG)
-
-    formatter = FileFormatter("%(asctime)s %(levelname)s | %(message)s")
-    fh.setFormatter(formatter)
-
-    logger.addHandler(fh)
-
-
 @handle_exceptions
 def main():
     """Runs main function. This is the entry point."""
@@ -191,6 +171,7 @@ def main():
         )
     elif args.command == "sql":
         run_sql(
+            args.log_dir,
             args.project,
             args.branch,
             args.explores,
@@ -440,6 +421,31 @@ def _build_assert_subparser(
     )
 
 
+def log_failing_sql(
+    error: SqlError,
+    log_dir: str,
+    model_name: str,
+    explore_name: str,
+    dimension_name: Optional[str] = None,
+):
+
+    file_name = (
+        model_name
+        + "__"
+        + explore_name
+        + ("__" + dimension_name if dimension_name else "")
+        + ".sql"
+    )
+    file_path = Path(log_dir) / "queries" / file_name
+    print(file_path)
+
+    logger.debug(f"Logging failing SQL query for '{error.path}' to '{file_path}'")
+    logger.debug(f"Failing SQL for {error.path}: \n{error.sql}")
+
+    with open(file_path, "w") as file:
+        file.write(error.s
--- Proposal A
## Root cause
The recent architectural changes removed the logic that persisted the SQL text of failing queries to disk.  
`run_sql` receives a list of error dictionaries (each containing a `sql` key – either at the top level or inside `metadata`).  
No code writes this SQL to the `logs` directory, so the expected `.sql` files are never created.

## Fix
```diff
diff --git a/spectacles/cli.py b/spectacles/cli.py
index 3bbaa9e..f7c9d2a 100644
--- a/spectacles/cli.py
+++ b/spectacles/cli.py
@@
 import logging
 import sys
+from pathlib import Path
 import traceback
@@
 def run_sql(
@@
     else:
         logger.info("")
         raise GenericValidationError
+
+    # ----------------------------------------------------------------------
+    # Persist failing SQL queries to the logs directory
+    # ----------------------------------------------------------------------
+    # `errors` is a list of dictionaries returned by the runner.  Each dict
+    # contains the SQL that caused the failure either directly under the
+    # ``sql`` key or inside the ``metadata`` mapping.  The original behaviour
+    # (pre‑refactor) was:
+    #   * single‑dimension mode → logs/<explore>/<dimension>.sql
+    #   * batch mode            → logs/<explore>.sql
+    # Re‑introduce that behaviour here.
+    if errors:
+        logs_root = Path("logs")
+        logs_root.mkdir(parents=True, exist_ok=True)
+
+        for err in errors:
+            # Extract the SQL string – it may be stored in two possible places.
+            sql_text = err.get("sql") or err.get("metadata", {}).get("sql")
+            if not sql_text:
+                # Nothing to write for this error.
+                continue
+
+            # Determine explore and dimension identifiers.
+            explore = (
+                err.get("explore")
+                or err.get("metadata", {}).get("explore")
+                or "unknown_explore"
+            )
+            dimension = (
+                err.get("dimension")
+                or err.get("metadata", {}).get("dimension")
+                or None
+            )
+
+            if mode == "single" and dimension:
+                # One file per dimension inside a folder named after the explore.
+                explore_dir = logs_root / explore
+                explore_dir.mkdir(parents=True, exist_ok=True)
+                sql_path = explore_dir / f"{dimension}.sql"
+            else:
+                # Batch mode – one file per explore.
+                sql_path = logs_root / f"{explore}.sql"
+
+            try:
+                sql_path.write_text(sql_text)
+                logger.debug(
+                    "Saved failing SQL for %s%s to %s",
+                    explore,
+                    f\" (dimension {dimension})\" if dimension else "",
+                    sql_path,
+                )
+            except Exception as exc:  # pragma: no cover – defensive
+                logger.warning(
+                    "Unable to write failing SQL to %s: %s", sql_path, exc
+                )
```

## How to verify
1. **Run Spectacles in single‑dimension mode** on a project that contains at least one dimension that triggers a SQL error.  
   *Expected result*:  
   - A `logs/` directory is created.  
   - Inside `logs/<explore_name>/` there is a `<dimension_name>.sql` file containing the exact failing query.

2. **Run Spectacles in batch mode** on the same project (e.g., `spectacles sql --mode batch`).  
   *Expected result*:  
   - A `logs/` d
--- Proposal B
## Root cause
During the recent refactor the code that persisted the SQL of failing queries was removed from the CLI.  
The runner still returns a dictionary that contains a `sql` key (the raw query that caused the failure) together with the explore name and, in single‑dimension mode, the dimension name.  
Because the CLI no longer writes this information to disk, no `.sql` files appear in the `logs/` directory, breaking the feature that users rely on for debugging.

## Fix
Add logic to `spectacles/cli.py` that, after each runner result is processed, checks for a failing result that contains a `sql` entry and writes that SQL to the appropriate file inside the `logs` directory.

```diff
--- a/spectacles/cli.py
+++ b/spectacles/cli.py
@@
 import sys
 import json
+import os
+from pathlib import Path
@@
 def main():
@@
-    results = runner.run()
-    for result in results:
-        # existing handling of the result (printing, exit codes, etc.)
-        pass
+    results = runner.run()
+    # Determine the base logs directory (default “logs” in the current working dir)
+    LOGS_DIR = Path(os.getenv("SPECTACLES_LOGS", "logs"))
+    LOGS_DIR.mkdir(parents=True, exist_ok=True)
+
+    for result in results:
+        # ------------------------------------------------------------------
+        # Existing handling of the result (printing, exit codes, etc.)
+        # ------------------------------------------------------------------
+        # (the original code is left untouched – we only add the SQL‑dumping
+        # behaviour after the original handling)
+
+        # ------------------------------------------------------------------
+        # Persist failing SQL to a file (new feature)
+        # ------------------------------------------------------------------
+        # A failing result is identified by the presence of a non‑empty
+        # ``sql`` key and a status that is not ``passed``.  The exact status
+        # values used by the runner are ``passed`` / ``failed`` (or similar);
+        # we treat anything other than ``passed`` as a failure.
+        sql = result.get("sql")
+        status = result.get("status", "").lower()
+        if sql and status != "passed":
+            explore = result.get("explore") or result.get("name")
+            # In single‑dimension mode the result also contains a ``dimension``
+            # key; otherwise we are in batch mode.
+            dimension = result.get("dimension")
+
+            if dimension:  # single‑dimension mode
+                # logs/<explore>/<dimension>.sql
+                explore_dir = LOGS_DIR / explore
+                explore_dir.mkdir(parents=True, exist_ok=True)
+                sql_path = explore_dir / f"{dimension}.sql"
+            else:  # batch mode
+                # logs/<explore>.sql
+                sql_path = LOGS_DIR / f"{explore}.sql"
+
+            try:
+                sql_path.write_text(sql, encoding="utf-8")
+                # Optional: inform the user that the file was written
+                print(f"❗  Failing SQL written to {sql_path}", file=sys.stderr)
+            except OSError as exc:
+                print(
+                    f"⚠️  Could not write failing SQL to {sql_path}: {exc}",
+                    file=sys.stderr,
+                )
+
+        # end of per‑result loop
*** End of File
```

### What the patch does
1. **Creates/ensures the `logs/` directory** (or a custom location via `SPECTACLES_LOGS` env‑var).  
2. **Iterates over each runn

=== TASK pre-commit__pre-commit-hooks-575 | repo pre-commit/pre-commit-hooks | python
--- Issue
check-case-conflict doesn't notice directory name conflicts
```
$ git status
On branch master

Initial commit

Changes to be committed:
  (use "git rm --cached <file>..." to unstage)

    new file:   TEST/foo
    new file:   test/bar

$ git commit -m "Test"
Check for case conflicts...................................................................................Passed
[master (root-commit) 6931ba5] Test
 2 files changed, 0 insertions(+), 0 deletions(-)
 create mode 100644 TEST/foo
 create mode 100644 test/bar

$ cat .pre-commit-config.yaml
- repo: git://github.com/pre-commit/pre-commit-hooks
  sha: '0d88124ef6343fbbc6c9d2872853f73546c98a3f'
  hooks:
      - id: check-case-conflict
```

A conflict such as `Test/foo` and `test/foo` is caught though.

--- REAL FIX (ground truth)
diff --git a/pre_commit_hooks/check_case_conflict.py b/pre_commit_hooks/check_case_conflict.py
index 6b8ba82..024c1c3 100644
--- a/pre_commit_hooks/check_case_conflict.py
+++ b/pre_commit_hooks/check_case_conflict.py
@@ -1,5 +1,7 @@
 import argparse
+import os.path
 from typing import Iterable
+from typing import Iterator
 from typing import Optional
 from typing import Sequence
 from typing import Set
@@ -12,9 +14,22 @@ def lower_set(iterable: Iterable[str]) -> Set[str]:
     return {x.lower() for x in iterable}
 
 
+def parents(file: str) -> Iterator[str]:
+    file = os.path.dirname(file)
+    while file:
+        yield file
+        file = os.path.dirname(file)
+
+
+def directories_for(files: Set[str]) -> Set[str]:
+    return {parent for file in files for parent in parents(file)}
+
+
 def find_conflicting_filenames(filenames: Sequence[str]) -> int:
     repo_files = set(cmd_output('git', 'ls-files').splitlines())
+    repo_files |= directories_for(repo_files)
     relevant_files = set(filenames) | added_files()
+    relevant_files |= directories_for(relevant_files)
     repo_files -= relevant_files
     retv = 0
 

--- Proposal A
## Root cause
`check-case-conflict` only compares the *full* file paths case‑insensitively.  
If two files live in different directories that only differ by case (e.g. `TEST/foo` vs `test/bar`), their lower‑cased full paths (`test/foo` and `test/bar`) are distinct, so the hook does not see a conflict.  
The hook never inspects the directory components themselves, therefore it misses directory‑name conflicts.

## Fix
Add a second pass that records every directory prefix of each staged file, lower‑cases it and checks for duplicates with a different original case.

```diff
--- a/pre_commit_hooks/check_case_conflict.py
+++ b/pre_commit_hooks/check_case_conflict.py
@@
 from __future__ import annotations
 
 import sys
 from pathlib import Path
@@
 def main(argv: list[str] | None = None) -> int:
-    files = list(_git_ls_files())
-    lowered = {}
-    for f in files:
-        lc = f.lower()
-        if lc in lowered and lowered[lc] != f:
-            print(f'Case conflict: {lowered[lc]} vs {f}')
-            return 1
-        lowered[lc] = f
-
-    return 0
+    """
+    Detect case‑conflicts among staged files **and** among directory names.
+
+    The original implementation only compared the full file paths.  This
+    missed cases where two directories differ only by case (e.g.
+    ``TEST/foo`` vs ``test/bar``).  The fix adds a second pass that walks
+    each file's parent directories, lower‑cases each directory prefix and
+    checks for duplicate case‑insensitive entries with a different original
+    spelling.
+    """
+    files = list(_git_ls_files())
+
+    # ------------------------------------------------------------------
+    # 1. Detect conflicts on the full file paths (original behaviour)
+    # ------------------------------------------------------------------
+    lowered: dict[str, str] = {}
+    for f in files:
+        lc = f.lower()
+        if lc in lowered and lowered[lc] != f:
+            print(f'Case conflict: {lowered[lc]} vs {f}')
+            return 1
+        lowered[lc] = f
+
+    # ------------------------------------------------------------------
+    # 2. Detect conflicts on directory names (new behaviour)
+    # ------------------------------------------------------------------
+    dir_lowered: dict[str, str] = {}
+    for f in files:
+        # Split the path using '/' – pre‑commit works with POSIX‑style paths
+        parts = f.split('/')
+        # Walk each directory prefix (exclude the file itself)
+        for i in range(1, len(parts)):
+            dir_path = '/'.join(parts[:i])
+            lc_dir = dir_path.lower()
+            if lc_dir in dir_lowered and dir_lowered[lc_dir] != dir_path:
+                print(
+                    f'Case conflict in directory name: '
+                    f'{dir_lowered[lc_dir]} vs {dir_path}'
+                )
+                return 1
+            dir_lowered[lc_dir] = dir_path
+
+    return 0
*** End of File ***
```

## How to verify
1. **Create a temporary repo**  

   ```bash
   mkdir case-conflict-test && cd case-conflict-test
   git init
   ```

2. **Add the pre‑commit configuration**  

   ```yaml
   # .pre-commit-config.yaml
   repos:
     - repo: https://github.com/pre-commit/pre-commit-hooks
       rev: v4.5.0   # or any recent tag
       hooks:
         - id: check-case-conflict
   ```

3. **Install the hook**

   ```bash
   pre-commit install
   ```

4. **Create the conflicting files**

   ```bash
   mkdir TEST test
   touch TEST/foo test/bar
   gi
--- Proposal B
## Root cause
`check-case-conflict` only compares the *file* paths that are passed to the hook.  
When two directories differ only by case (e.g. `TEST/` vs `test/`) the hook never sees a duplicate because the filenames themselves (`TEST/foo` and `test/bar`) are distinct after lower‑casing. The directory components are never added to the case‑conflict map, so a conflict between the directories is missed.

## Fix
Add the directory components of every staged path to the case‑conflict detection map.  
If any component (file or directory) collides with another component that differs only by case, the hook exits with a non‑zero status and prints a helpful message.

```diff
diff --git a/pre_commit_hooks/check_case_conflict.py b/pre_commit_hooks/check_case_conflict.py
index 5c2e1b7..d4f9a3c 100644
--- a/pre_commit_hooks/check_case_conflict.py
+++ b/pre_commit_hooks/check_case_conflict.py
@@
-import argparse
-import sys
-
-
-def main(argv=None):
-    parser = argparse.ArgumentParser()
-    parser.add_argument('filenames', nargs='*')
-    args = parser.parse_args(argv)
-
-    # Map of lower‑cased path → original path
-    lower_map = {}
-    for filename in args.filenames:
-        lc = filename.lower()
-        if lc in lower_map and lower_map[lc] != filename:
-            print(
-                'Case conflict detected: {} and {}'.format(
-                    lower_map[lc], filename,
-                ),
-                file=sys.stderr,
-            )
-            return 1
-        lower_map[lc] = filename
-
-    return 0
-
-
-if __name__ == '__main__':
-    sys.exit(main())
+import argparse
+import sys
+from pathlib import PurePosixPath
+
+
+def _add_path_to_map(path: str, lower_map: dict) -> int:
+    """
+    Add *path* (file or directory) to ``lower_map``.
+
+    ``lower_map`` maps a lower‑cased representation of a path to the first
+    observed case‑sensitive version.  If a different case version of the same
+    lower‑cased path already exists, a conflict is reported and ``1`` is
+    returned.  Otherwise ``0`` is returned.
+    """
+    lc = path.lower()
+    if lc in lower_map and lower_map[lc] != path:
+        print(
+            f'Case conflict detected: {lower_map[lc]} and {path}',
+            file=sys.stderr,
+        )
+        return 1
+    lower_map[lc] = path
+    return 0
+
+
+def main(argv=None):
+    parser = argparse.ArgumentParser()
+    parser.add_argument('filenames', nargs='*')
+    args = parser.parse_args(argv)
+
+    # ``lower_map`` holds every file *and* directory component that appears in
+    # the staged set, keyed by its lower‑cased form.
+    lower_map: dict[str, str] = {}
+
+    for filename in args.filenames:
+        # First, add the file itself.
+        if _add_path_to_map(filename, lower_map):
+            return 1
+
+        # Then, add each directory component of the file path.
+        # ``PurePosixPath`` is used because git always reports forward‑slash
+        # paths, even on Windows.
+        parts = PurePosixPath(filename).parts
+        # ``parts`` includes the file name as the last element; we only want
+        # the directories, so iterate up to ``len(parts) - 1``.
+        for i in range(1, len(parts)):
+            dir_path = '/'.join(parts[:i])
+            if _add_path_to_map(dir_path, lower_map):
+                return 1
+
+    return 0
+
+
+if __name__ == '__main__':
+    sys.exit(main())
```

### What changed
* Extracted the duplicate‑detection logic into a helper `_add_path_to_map`.

=== TASK marshmallow-code__apispec-361 | repo marshmallow-code/apispec | python
--- Issue
Should marshmallow helpers respect `only`, `exclude`?
When introspecting `Nested` fields, we don't check for the `only` or `exclude` attributes. For example:

``` python
class FooSchema(Schema):
    bar = fields.Str()
    baz = fields.Str()

class BobSchema(Schema):
    foo = fields.Nested(FooSchema, only=('bar', ))
```

If we call `schema2jsonschema(BobSchema)`, the nested `foo` will include `bar` and `baz` fields, even though `baz` will never be included in this case. Which isn't necessarily a problem, unless `baz` is required:

``` python
class FooSchema(Schema):
    bar = fields.Str()
    baz = fields.Str(required=True)
```

In this case, users of apispec will likely return JSON that wouldn't validate against the definition schema, since there's going to be a missing required field. I haven't actually encountered this situation, and I don't know how often it's going to come up--just wanted to raise for discussion.

--- REAL FIX (ground truth)
diff --git a/apispec/ext/marshmallow/openapi.py b/apispec/ext/marshmallow/openapi.py
index b18a21a..a254079 100644
--- a/apispec/ext/marshmallow/openapi.py
+++ b/apispec/ext/marshmallow/openapi.py
@@ -447,25 +447,22 @@ class OpenAPIConverter(object):
 
         :param schema: schema to add to the spec
         """
-
-        schema_cls = self.resolve_schema_class(schema)
-        name = self.schema_name_resolver(schema_cls)
-
-        if not name:
-            try:
-                return self.schema2jsonschema(schema)
-            except RuntimeError:
-                raise APISpecError(
-                    'Name resolver returned None for schema {schema} which is '
-                    'part of a chain of circular referencing schemas. Please'
-                    ' ensure that the schema_name_resolver passed to'
-                    ' MarshmallowPlugin returns a string for all circular'
-                    ' referencing schemas.'.format(schema=schema),
-                )
-
         schema_instance = resolve_schema_instance(schema)
         schema_key = make_schema_key(schema_instance)
         if schema_key not in self.refs:
+            schema_cls = self.resolve_schema_class(schema)
+            name = self.schema_name_resolver(schema_cls)
+            if not name:
+                try:
+                    return self.schema2jsonschema(schema)
+                except RuntimeError:
+                    raise APISpecError(
+                        'Name resolver returned None for schema {schema} which is '
+                        'part of a chain of circular referencing schemas. Please'
+                        ' ensure that the schema_name_resolver passed to'
+                        ' MarshmallowPlugin returns a string for all circular'
+                        ' referencing schemas.'.format(schema=schema),
+                    )
             name = get_unique_schema_name(self.spec.components, name)
             self.spec.components.schema(
                 name,

--- Proposal A
## Root cause
`apispec` builds a JSON‑Schema definition for a `marshmallow.fields.Nested` field by directly using the *original* schema class (`FooSchema` in the example).  
The `only` and `exclude` arguments that were passed to the `Nested` field are stored on the field instance (`field.only`, `field.exclude`) but are never taken into account when the nested schema is converted. Consequently the generated definition contains **all** fields of the nested schema, including required ones that are not actually exposed by the `only`/`exclude` filter. This leads to JSON that would not validate against the generated schema.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/apispec/ext/marshmallow/plugin.py
+++ b/apispec/ext/marshmallow/plugin.py
@@
 class MarshmallowPlugin(BasePlugin):
@@
     def map_field(self, field, **kwargs):
         """Map a marshmallow field to an OpenAPI schema property."""
@@
         if isinstance(field, fields.Nested):
-            # NOTE: we ignore `only`/`exclude` on the Nested field.
-            # This means the generated schema may contain fields that are
-            # not actually present in the serialized output.
-            nested_schema = field.schema
+            # Respect `only` and `exclude` that were supplied to the Nested
+            # field.  Marshmallow stores these values on the field instance
+            # (`field.only`, `field.exclude`).  We create a *restricted* schema
+            # instance that reflects the same view that will be used at dump/
+            # load time.  This ensures that required fields that are filtered
+            # out are not emitted in the generated JSON‑Schema.
+
+            # Start with the original schema (could be a class or an instance)
+            nested_schema = field.schema
+
+            # `field.only` may be a tuple/list of field names or ``None``.
+            # `field.exclude` may be a tuple/list of field names or ``None``.
+            # Marshmallow's ``only``/``exclude`` methods accept *positional*
+            # arguments, so we unpack the collections.
+            if getattr(field, "only", None):
+                # ``only`` overrides ``exclude`` – marshmallow applies it first.
+                # Convert to a list to allow multiple calls without mutating
+                # the original collection.
+                only_fields = list(field.only)
+                nested_schema = nested_schema.only(*only_fields)
+
+            if getattr(field, "exclude", None):
+                exclude_fields = list(field.exclude)
+                nested_schema = nested_schema.exclude(*exclude_fields)
+
+            # At this point ``nested_schema`` is a *Schema* instance that
+            # reflects the view defined by the Nested field.
+
+            # Recursively map the (possibly restricted) nested schema.
+            # ``self.map_schema`` returns a reference name (or a full schema
+            # dict if ``inline`` is True).  We keep the original behaviour
+            # regarding ``inline`` handling.
+            if kwargs.get("inline"):
+                # Inline the nested schema definition.
+                return self.map_schema(nested_schema, **kwargs)
+            else:
+                # Use a $ref to the component schema.
+                ref = self.map_schema(nested_schema, **kwargs)
+                return {"$ref": ref}
 
-            # Recursively map the nested schema.
-            if kwargs.get("inline"):
-            
--- Proposal B
## Root cause
`apispec.ext.marshmallow` converts a `fields.Nested` field by calling `self.schema2jsonschema(field.schema)`.  
`field.schema` returns the *original* nested schema class/instance **without** applying the `only` or `exclude` arguments that were passed to the `Nested` field. Consequently the generated JSON Schema always contains all fields of the nested schema, even when the user asked for a subset via `only`/`exclude`. Required fields that are omitted by `only` therefore appear as required in the output, producing invalid OpenAPI specifications.

## Fix
Update the handling of `fields.Nested` in `apispec/ext/marshmallow/openapi.py` so that a schema instance is created with the `only`, `exclude`, and `many` arguments supplied to the `Nested` field before it is passed to `schema2jsonschema`.

```diff
diff --git a/src/apispec/ext/marshmallow/openapi.py b/src/apispec/ext/marshmallow/openapi.py
index 4bffa36..c1e9d7a 100644
--- a/src/apispec/ext/marshmallow/openapi.py
+++ b/src/apispec/ext/marshmallow/openapi.py
@@
 from marshmallow import fields, Schema
@@
 class OpenAPIConverter(object):
@@
     def field2property(self, field):
@@
-        if isinstance(field, fields.Nested):
-            # Resolve the nested schema (may be a class or an instance)
-            schema = field.schema if isinstance(field.schema, Schema) else field.schema()
-            json_schema = self.schema2jsonschema(schema)
-            if getattr(field, "many", False):
-                json_schema = {"type": "array", "items": json_schema}
-            return json_schema
+        if isinstance(field, fields.Nested):
+            """
+            ``fields.Nested`` can be configured with ``only`` and ``exclude`` to
+            limit the fields that appear in the nested object.  The original
+            implementation passed ``field.schema`` directly to ``schema2jsonschema``,
+            which ignores those arguments.  We now instantiate the nested schema
+            with the same ``only``, ``exclude`` and ``many`` values that were
+            supplied to the ``Nested`` field so that the generated JSON Schema
+            respects the user's intent.
+            """
+            # Resolve the original schema definition (class or instance)
+            nested_def = field.schema
+
+            # Determine whether we need to (re)instantiate the schema with
+            # ``only``/``exclude``/``many``.  If the field supplies any of these
+            # arguments we create a fresh instance; otherwise we reuse the
+            # existing instance (if any) to preserve any custom initialization.
+            needs_instantiation = any(
+                getattr(field, attr, None) for attr in ("only", "exclude", "many")
+            )
+
+            if needs_instantiation:
+                # ``nested_def`` may be a class or an already‑instantiated schema.
+                # In both cases we create a new instance using the class, passing
+                # the appropriate arguments.
+                schema_cls = (
+                    nested_def
+                    if isinstance(nested_def, type) and issubclass(nested_def, Schema)
+                    else nested_def.__class__
+                )
+                schema = schema_cls(
+                    only=getattr(field, "only", None),
+                    exclude=getattr(field, "exclude", None),
+                    many=getattr(field, "many", False),
+                )
+            else:
+                # No fil