

=== TASK beetbox__beets-5022 | repo beetbox/beets | python
--- Issue
Expose import.incremental_skip_later config option as CLI option
To reduce the time needed for manual prompting during the import of a large number of tracks and to import as much as possible without prompting, I run the import once with the `-i --quiet` options and the `import.incremental_skip_later` config option enabled and then again without the `--quiet` option and `import.incremental_skip_later` disabled within the configuration, letting beets prompt for the previously skipped tracks only.
However, currently that requires me to change the configuration file during the workflow which I'd like to avoid.
<!--
If you're landing here as a user, we ask you bring up your idea in the
Discussions (https://github.com/beetbox/beets/discussions).
-->

### Proposed solution

In addition to the configuration file the  `import.incremental_skip_later` option should be configurable via the CLI as e.g. `--incremental-skip-later` option of the `import` command.

Usage example:
```sh
beet import -siq --incremental-skip-later # no questions asked, tracks skipped in doubt
beet import -si                           # prompting for previously skipped tracks only
```

### Objective

<!-- Ref to Discussions -->

#### Goals

<!-- What is the purpose of feature request? -->
Improving the import workflow / making the import command more flexible.

#### Non-goals

<!--
What else could be accomplished with this feature request, but is currently out
of scope?
-->
Changin
--- Real fix (for your judgement only)
diff --git a/beets/ui/commands.py b/beets/ui/commands.py
index 63f25fca..26eb5320 100755
--- a/beets/ui/commands.py
+++ b/beets/ui/commands.py
@@ -1506,6 +1506,20 @@ import_cmd.parser.add_option(
     action="store_false",
     help="do not skip already-imported directories",
 )
+import_cmd.parser.add_option(
+    "-R",
+    "--incremental-skip-later",
+    action="store_true",
+    dest="incremental_skip_later",
+    help="do not record skipped files during incremental import",
+)
+import_cmd.parser.add_option(
+    "-r",
+    "--noincremental-skip-later",
+    action="store_false",
+    dest="incremental_skip_later",
+    help="record skipped files during incremental import",
+)
 import_cmd.parser.add_option(
     "--from-scratch",
     dest="from_scratch",
diff --git a/beetsplug/mbsubmit.py b/beetsplug/mbsubmit.py
index e4c0f372..d215e616 100644
--- a/beetsplug/mbsubmit.py
+++ b/beetsplug/mbsubmit.py
@@ -21,11 +21,13 @@ implemented by MusicBrainz yet.
 [1] https://wiki.musicbrainz.org/History:How_To_Parse_Track_Listings
 """
 
+import subprocess
 
 from beets import ui
 from beets.autotag import Recommendation
 from beets.plugins import BeetsPlugin
 from beets.ui.commands import PromptChoice
+from beets.util import displayable_path
 from beetsplug.info import print_data
 
 
@@ -37,6 +39,7 @@ class MBSubmitPlugin(BeetsPlugin):
             {
                 "format": "$track. $title - $artist ($length)",
                 "threshold": "medium",
+                "picard_path": "picard",
             }
         )
 
@@ -56,7 +59,21 @@ class MBSubmitPlugin(BeetsPlugin):
 
     def before_choose_candidate_event(self, session, task):
         if task.rec <= self.threshold:
-            return [PromptChoice("p", "Print tracks", self.print_tracks)]
+            return [
+                PromptChoice("p", "Print tracks", self.print_tracks),
+                PromptChoice("o", "Open files with Picard", self.picard),
+            ]
+
+    def picard(self, session, task):
+   
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Allow importing music files directly from a previous import logfile
      Allow importing from logfile
When importing large collections, it makes sense to do the automated import first, then do the manual part afterwards:

    $ beet import -l skipped.log -q /path/to/library

It would be great if it was possible to continue the import using the logfile instead of a di
      Way: importing skipped files directly from a previous import logfile
      - Examine the CLI import command parser and execution logic to see how import log files are created and how file paths are validated and gathered.
      - Add a `--from-logfile` option to the import command parser allowing multiple log files to be supplied.
      - Implement a helper to parse logfiles line by line, validating line formats and verbs ('asis', 'skip', 'duplicate-skip' vs ignored verbs like 'import', 'duplicate-keep', 'duplicate-replace'), extracting path components with commonpath.
      - Integrate logfile path extraction into the import command handler, ensuring missing user-specified paths raise UserError while missing logfile paths produce warnings, raising UserError only if no valid paths remain.
      - Run unit tests verifying valid and invalid logfile line parsing, skipped path extraction, and handling of non-existent log paths.
  [2] Goal: Disable automatic audio file scrubbing during import when metadata writing is disabled
      scrub: The `auto` option should be a no-op when `import.write` is disabled
### Problem

I was importing new tracks with `import.write` disabled and seeing all tracks populated with new tags. With help from the [forum](https://discourse.beets.io/t/tags-are-being-written-to-imported-files-even-thoug
      Way: Bypass automatic file scrubbing during import when write disabled
      - Inspect the scrub plugin's import event listener to determine how `scrub.auto` interacts with the `import.write` setting.
      - Update the auto-scrub hook logic triggered on import to check if writing is enabled, returning early as a no-op when `import.write` is False.
      - Run importer tests to confirm that tags are not stripped and rewritten during import when write is disabled.
  [3] Goal: Add an option to skip remaining items and apply previous selections
      Selection interface: option to "skip remaining tracks" and take action
### Problem

I love the `modify` command's select mode, because it means you don't have to write super-specific queries - instead you can write a more general query and then simply type an `n` to skip those tracks you don't wan
      Way: Add early exit option to interactive item selection prompts
      - Locate the helper function responsible for prompting the user interactively during item selection (such as `input_select_objects`).
      - Replace binary yes/no prompting (`input_yn`) in the item selection loop with multi-option prompting (`input_options`) supporting yes ('y'), no ('n'), and quit/stop ('q').
      - Handle the 'q' option by immediately returning the already-accumulated list of selected objects rather than aborting or prompting remaining items.
      - Update user documentation and CLI reference to describe the new quit/early apply option.
      - Run UI input method tests verifying that choosing 'q' returns the items selected up to that point.

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
--- Real fix (for your judgement only)
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
         loop_targets = (ast.For, ast.AsyncFor, 
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Allow operator getter and caller functions in default argument values
      B008: Whitelist operator.{attrgetter,itemgetter,methodcaller} default args
Calling those functions in default args is perfectly fine and *not* calling them would most likely be a bug.
      Way: Allow operator getters and methodcaller in default argument linting
      - Locate the linter rule definition and its set of allowed immutable function calls in default arguments (e.g. for rule B008).
      - Add qualified and unqualified names for operator getter functions (`operator.attrgetter`, `operator.itemgetter`, `operator.methodcaller`, `attrgetter`, `itemgetter`, `methodcaller`) as well as `re.compile` to the immutable calls set.
      - Run the test suite to verify that functions calling operator getters and callers in default arguments no longer trigger B008 errors.
  [2] Goal: Detect pytest raises in B017 when imported directly
      B017: False negative when "from" imports used
## Summary
B017 fails when raises is imported directly

## Version
Replicated on version `23.9.16`

## Sample code
```
import pytest
from pytest import raises


def foo():
    raise


def test_pytest_raises():
    with raises(Exception):
      Way: Tracking direct module imports in AST visitors for linter checks
      - Locate the AST visitor handler for import statements and the specific linter check checking context managers or calls (such as B017).
      - Add support for `ast.ImportFrom` nodes in the import visitor methods so that imported symbols are stored along with their module name (e.g. `pytest.raises`).
      - Update the context manager / call checking logic to allow matching direct function names (`ast.Name` nodes) if the symbol was recorded from an `ast.ImportFrom` node, while preserving attribute calls (`ast.Attribute`).
      - Run the test suite to verify that direct imports trigger the linter rule as expected.
  [3] Goal: Allow metaclass init methods to use cls as the first parameter
      B902 requires first parameter named self for __init__ of metaclass
I have a metaclass defined like this:

    class StorageMeta(type):
        def __init__(cls, name, bases, d):

Naturally B902 flagged the first parameter of `__init__`, but I'm not sure if it's intended in the case of metaclass
      Way: Allow cls first parameter for metaclass instance methods
      - Inspect the AST visitor logic responsible for enforcing method first parameter naming rules (e.g. self vs cls).
      - Check how the enclosing class definition is retrieved from the AST node traversal stack.
      - Update the parameter validation to check if the enclosing class node directly inherits from 'type' via its bases, and if so, expect 'cls' instead of 'self'.
      - Update documentation to clarify that metaclasses directly inheriting from 'type' accept 'cls' for instance methods.
      - Run the test suite to verify metaclass methods accept 'cls' and existing method checks still pass.

=== TASK dandi__dandi-cli-216 | repo dandi/dandi-cli | python
--- Issue
remove POSIX filenames assumptions in the tests
This is a follow up to https://github.com/dandi/dandi-cli/pull/210
where I did that in `test_upload`.  So the goal is to check other tests and provide the same (or similar?) changes in other test files.
--- Real fix (for your judgement only)
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
+
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Add an option to preserve leading directory structure when downloading assets
      download: add --keep-leading-path|-k or some mode for `--download`
Inspired by 
- https://github.com/dandi/dandi-archive/issues/1981

ATM whenever we download an asset, we download file into current directory (besides if path is a glob as I showed in https://github.com/dandi/dandi-archive/issues/
      Way: preserve remote hierarchy and root metadata on partial asset downloads
      - Identify where CLI download options are defined, where download paths are calculated across parsed URL types, and where the downloader orchestrator configures output directories and metadata fetching.
      - Add a `--preserve-tree` CLI flag to the download command that implies downloading both assets and metadata (`get_metadata=True`, `get_assets=True`).
      - Update URL parsing classes to accept `preserve_tree: bool` in `get_asset_download_path`, returning full relative asset paths (`asset.path.lstrip('/')`) instead of stripping leading directory prefixes when `preserve_tree` is active.
      - Update `Downloader` to nest outputs under `url.dandiset_id` when `preserve_tree` is true and `dandiset_id` exists, and trigger `dandiset.yaml` download when `preserve_tree` is true alongside regular dandiset/metadata conditions.
      - Run download CLI test suite to ensure default download behaviors, metadata-only/assets-only filtering, and URL instance routing still pass.
  [2] Goal: Add codespell to linting checks and pre-commit hooks to fix typos
      codespell the code base
works like charm:
```shell
$> codespell dandi
dandi/_version.py:280: unparseable ==> unparsable
dandi/download.py:100: Activites ==> Activities
dandi/models.py:438: alpha-numeric ==> alphanumeric
dandi/utils.py:112: tollerance ==> tolerance
dandi/utils.py:121: tolleran
      Way: Integrate codespell into linting checks and pre-commit hooks
      - Run codespell on the codebase to identify reported spelling typos and vendored files to ignore.
      - Add codespell hook to `.pre-commit-config.yaml` with exclusions for generated or third-party vendored files.
      - Add `[codespell]` configuration in `setup.cfg` to skip vendored/generated files by default.
      - Update the lint environment in `tox.ini` to depend on codespell and execute `codespell` during lint runs.
      - Fix detected typos in documentation, comments, strings, and parameter names across the source files.
      - Run the test suite to ensure parameter renames and code typo fixes do not break dependent tests.
  [3] Goal: Support upload and download with the new API server
      Interface new implementation (API server) in dandi-cli top-level interfaces
A decision was made in https://github.com/dandi/dandi-cli/pull/283 to aim to get `master` in a shape to support interaction with both current girder-based deployed dandiarchive **and** WiP https://github.com/dandi/dandi-api 
      Way: support alternative REST API instances in client and configuration
      - Inspect instance registry definitions and the instance tuple structure in configuration constants.
      - Add a metadata version field to the instance tuple definition and update existing instance entries with default schema version 0.
      - Add new API-based instances with metadata version 1 pointing to the respective GUI and API endpoint URLs while dropping legacy publish endpoints.
      - Update authentication and keyring handling to support API token lookup from environment variables or keyring storage keyed by instance name.
      - Run the instance lookup unit tests to verify instance resolution and URL mapping.

=== TASK XKNX__xknx-271 | repo XKNX/xknx | python
--- Issue
Error when reading group addresses with timers
I have an extensive KNX installation with all kinds of actors, sensors, switches, etc.
My integration with Home Assistant works perfect, but I do get the following error in the logs when input is detected on an object linked to a timer:

> Error doing job: Exception in callback UDPTransport._on_read_ready
Traceback (most recent call last):
File "uvloop/cbhandles.pyx", line 69, in uvloop.loop.Handle._run
File "uvloop/handles/udp.pyx", line 64, in uvloop.loop.UDPTransport._on_read_ready
File "/usr/local/lib/python3.6/site-packages/xknx/io/udp_client.py", line 55, in datagram_received self.data_received_callback(data)
File "/usr/local/lib/python3.6/site-packages/xknx/io/udp_client.py", line 85, in data_received_callback knxipframe.from_knx(raw)
File "/usr/local/lib/python3.6/site-packages/xknx/knxip/knxip.py", line 78, in from_knx pos += self.body.from_knx(data[pos:])
File "/usr/local/lib/python3.6/site-packages/xknx/knxip/tunnelling_request.py", line 48, in from_knx pos += self.cemi.from_knx(raw[pos:])
File "/usr/local/lib/python3.6/site-packages/xknx/knxip/cemi_frame.py", line 116, in from_knx return self.from_knx_data_link_layer(raw)
File "/usr/local/lib/python3.6/site-packages/xknx/knxip/cemi_frame.py", line 147, in from_knx_data_link_layer self.cmd = APCICommand(tpci_apci & 0xFFC0)
File "/usr/local/lib/python3.6/enum.py", line 291, in __call__ return cls.__new__(cls, value)
File "/usr/local/lib/python3.6/enum.py",
--- Real fix (for your judgement only)
diff --git a/xknx/exceptions/__init__.py b/xknx/exceptions/__init__.py
index 0875fb6f..95609fa0 100644
--- a/xknx/exceptions/__init__.py
+++ b/xknx/exceptions/__init__.py
@@ -2,4 +2,5 @@
 # flake8: noqa
 from .exception import (
     ConversionError, CouldNotParseAddress, CouldNotParseKNXIP,
-    CouldNotParseTelegram, DeviceIllegalValue, XKNXException)
+    CouldNotParseTelegram, DeviceIllegalValue, UnsupportedCEMIMessage,
+    XKNXException)
diff --git a/xknx/exceptions/exception.py b/xknx/exceptions/exception.py
index 626299bd..18702952 100644
--- a/xknx/exceptions/exception.py
+++ b/xknx/exceptions/exception.py
@@ -39,7 +39,7 @@ class CouldNotParseKNXIP(XKNXException):
     """Exception class for wrong KNXIP data."""
 
     def __init__(self, description=""):
-        """Initialize CouldNotParseTelegram class."""
+        """Initialize CouldNotParseKNXIP class."""
         super().__init__()
         self.description = description
 
@@ -49,6 +49,20 @@ class CouldNotParseKNXIP(XKNXException):
             .format(self.description)
 
 
+class UnsupportedCEMIMessage(XKNXException):
+    """Exception class for unsupported CEMI Messages."""
+
+    def __init__(self, description=""):
+        """Initialize UnsupportedCEMIMessage class."""
+        super().__init__()
+        self.description = description
+
+    def __str__(self):
+        """Return object as readable string."""
+        return '<UnsupportedCEMIMessage description="{0}" />' \
+            .format(self.description)
+
+
 class ConversionError(XKNXException):
     """Exception class for error while converting one type to another."""
 
diff --git a/xknx/knxip/cemi_frame.py b/xknx/knxip/cemi_frame.py
index 1fc80ead..dffa5064 100644
--- a/xknx/knxip/cemi_frame.py
+++ b/xknx/knxip/cemi_frame.py
@@ -12,7 +12,8 @@ Documentation within:
     File: AN117 v02.01 KNX IP Communication Medium DV.pdf
 """
 from xknx.dpt import DPTArray, DPTBinary
-from xknx.exceptions import ConversionError, CouldNotParseKNXIP
+from 
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Handle out-of-order KNX tunneling request sequence numbers
      'TunnellingRequest with sequence number not equal to expected' error every few hours
Every few hours 'Received TunnellingRequest with sequence number not equal to expected' error is stuck while the sequence finally loops around. During that time messages are sent but not read.

- [ ] using xknx st
      Way: Handle out-of-order tunneling sequence numbers by triggering connection loss
      - Identify where incoming UDP tunneling requests and sequence number checks are processed in the tunneling transport layer.
      - Locate the sequence number mismatch handler where a received request's sequence number does not equal the expected sequence counter and does not match a repeated frame.
      - Update the sequence number mismatch branch to explicitly invoke tunnel connection loss callback (`_tunnel_lost()`) so the tunnel resets and reconnects instead of stalling until sequence counters wrap around.
      - Run the tunnel test suite to ensure sequence mismatch triggers tunnel disconnection and repeated request tests pass.
  [2] Goal: Fix KNXIP telegram parsing errors caused by unsupported APCI messages
      Invalid date causes error / disconnect
**Description of problem:**

- [ ] using xknx standalone
- [x] using Home-Assistant knx plugin

I do see this error in my logfile exactly every 10 minutes:
```
2020-09-20 18:22:52 WARNING (MainThread) [xknx.log] CEMI not supported: <UnsupportedCEMIMessag
      Way: consume full body length when parsing unsupported CEMI frames
      - Locate where tunneling requests parse incoming CEMI payloads and where unsupported CEMI exceptions are caught.
      - Trace how consumed byte offsets are accumulated and verified against total KNXIP frame length in the parent frame parser.
      - In TunnellingRequest.from_knx, return `len(raw)` immediately when catching UnsupportedCEMIMessage instead of adding `len(raw)` to `pos`, ensuring the entire payload is marked as consumed without exceeding the actual buffer length.
      - Adjust unsupported CEMI log level to debug and improve error message strings to include hex-encoded raw data.
      - Run the tunneling and string representation tests to verify unsupported CEMI frames are acknowledged and parsed without triggering frame length errors.
  [3] Goal: Handle unsupported or unknown APCI commands without raising ValueError
      ValueError: [448,256] is not a valid APCICommand
**Description of problem:**
Exception during processing of messages, likely while ETS is communicating with new Secure IP Interface.

- [ ] using xknx standalone
- [X] using Home-Assistant knx plugin

**Version information:**
- xknx / Home-Assi
      Way: Catch unknown enum values during frame parsing and raise domain exception
      - Locate where frame fields are converted to Enum types like CEMIMessageCode and APCICommand during frame parsing.
      - Wrap Enum instantiations in try/except blocks to catch ValueError when raw values do not map to known enum members.
      - Raise domain-specific parse exception (such as CouldNotParseKNXIP) with a descriptive error message on ValueError.
      - Run unit tests covering invalid raw values for message codes and APCI commands to verify exceptions are handled as expected.

=== TASK datosgobar__pydatajson-137 | repo datosgobar/pydatajson | python
--- Issue
Función push theme_to_ckan()
**Contexto**

Por el momento, la función `push_dataset_to_ckan()` federa los datasets sin tener en cuenta los `themes` presentes en el catálogo de destino. La operación se concreta, pero cualquier `theme` del dataset que no esté presente en el catálogo es ignorado.

El objetivo es implementar una función `push_theme_to_ckan()`, que mediante la API, cree los grupos de CKAN indicados. Esta función será luego utilizada por si sola o en conjunto con los wrappers para generar los temas pertinentes de un catalogo.
--- Real fix (for your judgement only)
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
 from .helpe
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Add option to convert dataset themes to keywords when pushing to CKAN
      Agregar opción para mover `theme` a `keyword` en `push_dataset_to_ckan`
**Contexto**

Los temas específicos (`theme`) son temas que tienen sentido dentro del contexto y dominio temático bajo el cual cada nodo original opera. Sirven para clasificar a sus datasets según categorías temáticas específica
      Way: demoting dataset themes to ckan tags conditionally
      - Identify where dataset metadata is mapped to CKAN package format and where themes were previously handled during CKAN push operations.
      - Update `map_dataset_to_package` to accept `owner_org`, `theme_taxonomy`, and an optional boolean flag `demote_themes` (defaulting to True). Move theme-to-tag mapping logic into `map_dataset_to_package`, mapping theme labels to package `tags` when `demote_themes` is True, or adding theme slugs to package `groups` when `demote_themes` is False.
      - Update `push_dataset_to_ckan` to accept the optional parameter `demote_themes=True`, pass `owner_org`, `catalog.themes`, and `demote_themes` down to `map_dataset_to_package`, and remove redundant theme conversion logic from `push_dataset_to_ckan`.
      - Update documentation to describe the `demote_themes` parameter and default behavior.
      - Run unit tests covering dataset conversion to CKAN package with and without theme demotion.
  [2] Goal: Add an option to copy superTheme values to theme when pushing datasets
      Agregar opción para copiar `superTheme` a `theme` en `push_dataset_to_ckan`
**Contexto**

Los temas globales (`superTheme`) son temas transversales bajo los cuales los nodos originales clasifican a sus datasets para indexarlos en un nodo integrador.

El `superTheme` de un dataset en su nodo original
      Way: conditionally map superTheme taxonomy to CKAN package groups
      - Locate where dataset metadata is transformed into CKAN package dictionaries and where the federation push function exposes mapping options.
      - Add a `demote_superThemes` boolean parameter (defaulting to True) to `map_dataset_to_package`, only populating `package['groups']` with mapped super themes when the flag is True.
      - Safeguard the assignment in `map_dataset_to_package` when `demote_themes=False` so it appends to `package.get('groups', [])` instead of assuming `package['groups']` was previously initialized.
      - Expose `demote_superThemes=True` in `push_dataset_to_ckan` and pass it down to `map_dataset_to_package`, updating corresponding docstrings and documentation.
      - Run test suite to verify dataset conversion with both enabled and disabled superTheme demotion.
  [3] Goal: Handle missing theme taxonomy labels when federating datasets to CKAN
      Robustecer el manejo de themes por parte de push_dataset_to_ckan()

Este es un issue que hace referencia al 24 de monitoreo apertura:
https://github.com/datosgobar/monitoreo-apertura/issues/24
Lo abro acá porque me parece que es más apropiado discutirlo en este repo.

--------------------------
      Way: Lookup and sanitize catalog theme labels when converting datasets to CKAN packages
      - Identify where dataset themes are converted to CKAN tags and locate the direct generator expression searching for matching theme IDs.
      - Update the catalog theme search logic in `pydatajson/search.py` (`get_theme`) to perform case-insensitive identifier comparisons.
      - Update `map_dataset_to_package` in `pydatajson/ckan_utils.py` and its callers in `pydatajson/federation.py` to take `catalog` instead of `theme_taxonomy`, lookup themes via `catalog.get_theme(identifier=theme)['label']`, and sanitize the label with unicode regex before assigning it to CKAN package tags.
      - Run the CKAN utils test suite to verify dataset conversion to package format passes.

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
--- Real fix (for your judgement only)
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
 
 

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent DVC status from incorrectly reporting parameters as changed
      dvc status always mark a param as changed
## Bug Report

### Please provide information about your setup

**Output of `dvc version`:**

```console
$ dvc version
DVC version: 1.1.7
Python version: 3.8.3
Platform: Linux-4.19.104-microsoft-standard-x86_64-with-glibc2.10
Binary: False
Packag
      Way: preserve falsy values when tracking parameter status
      - Locate parameter dependency tracking logic where parameter values are extracted and saved into stage dependency information.
      - Reproduce the issue by defining a parameter with a falsy value (such as boolean `false`, `null`, `0`, or empty container `[]`) in parameters file and checking dependency status.
      - Update parameter extraction logic to check key existence in values map (`if param in values:`) rather than checking value truthiness (`if value:`), so that falsy parameter values are saved properly.
      - Run unit tests for parameter dependencies including test cases for falsy parameter values.
  [2] Goal: Support stage-level variables in dvc.yaml configuration
      Stage-level `vars` alternative in DVC 3.X
# Bug Report

<!--
## Issue name

Issue names must follow the pattern `command: description` where the command is the dvc command that you are trying to run. The description should describe the consequence of the bug.

Example: `repro: doesn't detect 
      Way: support stage level variables in pipeline configuration
      - Locate the schema definition for pipeline stages and the parsing logic for stage entries.
      - Update the stage definition schema to optionally accept a `vars` key matching the global `vars` schema format.
      - Modify the stage entry resolution logic to extract `vars` from the stage definition, resolve the working directory (`wdir`), and load the stage-level variables into a cloned context before resolving other stage fields.
      - Run the parsing and validation tests to verify that stage-level variables are correctly parsed, validated, and interpolated.
  [3] Goal: Ignore untracked values in parameter files during experiment commands
      params: untracked values in params files are not ignored in params/exp commands
# Bug Report

exp show: Invalid or wrong values for column names and values

<!--
## Issue name

Issue names must follow the pattern `command: description` where the command is the dvc command that you are trying 
      Way: filter parameter files to only show tracked stage dependencies
      - Locate the parameter dependency class and add a method to read only the specific keys/paths that are explicitly declared as dependencies, rather than reading the entire file.
      - Update the parameter collection and reading logic in the repository layer to accept a flag (e.g., `deps` or `param_deps`) that triggers reading only the declared dependencies instead of the whole file.
      - Expose this filtering option to the command-line interface by adding a `--deps` or `--param-deps` flag to the relevant show and diff commands.
      - Verify the changes by running the unit tests for experiment and parameter diff/show commands.

=== TASK tensorly__tensorly-320 | repo tensorly/tensorly | python
--- Issue
[BUG] singular vectors in `tl.partial_svd` are not orthonormal
When `n_eigenvecs` is greater than the input matrix rank, the returned singular values are not orthonormal. However, the result is still a valid low-rank decomposition (<img src="https://render.githubusercontent.com/render/math?math=A = USV">). 

```python3
import tensorly as tl
import numpy as np
np.random.seed(0)

a = tl.dot(tl.randn((4, 2)), tl.randn((2, 4)))
u, s, v = tl.partial_svd(a, n_eigenvecs=3)
print(tl.dot(u.T, u))
```
```
[[ 1. -0.  0.]
 [-0.  1.  0.]
 [ 0.  0.  0.]]
```
<img src="https://render.githubusercontent.com/render/math?math=U^\top U \neq I"> ☹️.

---
To fix this bug we could use the Gram–Schmidt process and extend the singular vectors basis corresponding to the zero singular values.

--- Real fix (for your judgement only)
diff --git a/tensorly/backend/core.py b/tensorly/backend/core.py
index ae9beb6..41c483a 100644
--- a/tensorly/backend/core.py
+++ b/tensorly/backend/core.py
@@ -1079,6 +1079,9 @@ class Backend(object):
                 S = np.sqrt(np.clip(S, 0, None))
                 S = np.clip(S, np.finfo(S.dtype).eps, None)  # To avoid divide by zero warning on next line
                 V = np.dot(matrix.T.conj(), U * np.where(np.abs(S) <= np.finfo(S.dtype).eps, 0, 1/S)[None, :])
+                U, S, V = U[:, ::-1], S[::-1], V[:, ::-1]
+                V, R = np.linalg.qr(V)
+                V = V * (2*(np.diag(R) >= 0) - 1)  # we can't use np.sign because np.sign(0) == 0
             else:
                 S, V = scipy.sparse.linalg.eigsh(
                     np.dot(matrix.T.conj(), matrix), k=n_eigenvecs, which='LM', v0=v0
@@ -1086,9 +1089,11 @@ class Backend(object):
                 S = np.sqrt(np.clip(S, 0, None))
                 S = np.clip(S, np.finfo(S.dtype).eps, None)
                 U = np.dot(matrix, V) * np.where(np.abs(S) <= np.finfo(S.dtype).eps, 0, 1/S)[None, :]
+                U, S, V = U[:, ::-1], S[::-1], V[:, ::-1]
+                U, R = np.linalg.qr(U)
+                U = U * (2*(np.diag(R) >= 0) - 1)
 
             # WARNING: here, V is still the transpose of what it should be
-            U, S, V = U[:, ::-1], S[::-1], V[:, ::-1]
             V = V.T.conj()
 
         if flip:
diff --git a/tensorly/backend/pytorch_backend.py b/tensorly/backend/pytorch_backend.py
index 267cda3..b96e7d7 100644
--- a/tensorly/backend/pytorch_backend.py
+++ b/tensorly/backend/pytorch_backend.py
@@ -148,6 +148,10 @@ class PyTorchBackend(Backend):
     def stack(arrays, axis=0):
         return torch.stack(arrays, dim=axis)
 
+    @staticmethod
+    def diag(tensor, k=0):
+        return torch.diag(tensor, diagonal=k)
+
     @staticmethod
     def sort(tensor, axis, descending = False):
         if axis is None:
@@ -212,7 +216,7 @@ class PyTorchBackend(Backend):
 for na
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent NaN singular vectors in partial SVD when requesting more eigenvectors than rank
      [BUG] nan singular vectors in `tl.partial_svd`
Occasionally `tl.partial_svd` returns nans when `max_dim > n_eigenvecs > rank`.
```python3
import tensorly as tl
import numpy as np
np.random.seed(1)

a = tl.dot(tl.randn((4, 2)), tl.randn((2, 4)))
u, s, v = tl.partial_svd(a, n_eigenvecs=3)
prin
      Way: Prevent NaN singular vectors in partial SVD by clipping eigenvalues before square root
      - Locate the backend implementation of partial SVD that calculates singular values from matrix eigenvalues using eigensolvers.
      - Reproduce the issue by running partial SVD requesting more singular vectors than the matrix rank and checking for NaN values.
      - Clip calculated eigenvalues to non-negative values using `np.clip(S, 0, None)` before taking `np.sqrt` to prevent square root of slightly negative numerical noise, and floor singular values `S` at machine epsilon `np.finfo(S.dtype).eps` to avoid division by zero or NaN propagation during normalization.
      - Run backend test suite to verify partial SVD handles rank-deficient matrices without producing NaNs.
  [2] Goal: Return both U and V matrices in non-negative SVD
      make_svd_non_negative only returns the updated U matrix
#### Describe the bug
I'm not sure if this is a bug or if this is actually how this function is supposed to behave but currently, it only returns the updated U value. Incase it's not a bug, I don't understand why the H matrix is computed in th
      Way: Return both transformed matrices and bound rank loop in non-negative SVD
      - Locate non-negative SVD helper and SVD interface wrapper functions.
      - Run existing SVD tests to reproduce missing returned matrix and index bounds issues.
      - Update loop iteration in make_svd_non_negative to stop at minimum of U columns and V rows to avoid index out of bounds.
      - Apply post-processing (thresholding/mean replacement) to both W and H non-negative factors and return both (W, H).
      - Update caller in svd_interface to capture both returned factors from make_svd_non_negative.
      - Verify the fix by running SVD interface shape tests.
  [3] Goal: Fix shape broadcasting error in masked Khatri-Rao product for NumPy backend
      Masked CP decomposition returns error in numpy backend kr
Hi, like milanlanlan in [Handling missing data in decomposition #4](https://github.com/tensorly/tensorly/issues/4#issuecomment-557899752) I am trying to decompose a tensor using the CP decomposition with missing values. Unfortunately, I also 
      Way: Reshape mask array before broadcasting in Khatri-Rao product backend function
      - Locate the backend Khatri-Rao implementation (`kr` function) handling masked matrix multiplications.
      - Run the failing masked CANDECOMP/PARAFAC test to reproduce the shape broadcasting ValueError when applying a mask in `kr`.
      - Modify the `kr` method in the NumPy backend to check if `mask` is not None and reshape it to `(-1, 1)` before multiplying with the reshaped `einsum` result, rather than multiplying directly by an unshaped mask or defaulting to 1 initially.
      - Run masked CP decomposition tests to verify elementwise broadcasting succeeds without shape errors.

=== TASK corteva__rioxarray-14 | repo corteva/rioxarray | python
--- Issue
Add ability to set CRS and write CRS information to dataset
See: https://github.com/geoxarray/geoxarray/issues/9
--- Real fix (for your judgement only)
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
+        input_crs=dst_crs, grid_m
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Document the difference between set_crs and write_crs
      Document difference between `set_crs` and `write_crs`
`set_crs`:

> Set the CRS value for the Dataset/DataArray without modifying the dataset/data array.

`write_crs`:

> Write the CRS to the dataset in a CF compliant manner.

The `input_crs` and `inplace` arguments are the same; the only di
      Way: Deprecate lossy CRS setter and allow one-dimensional box clipping
      - Examine raster clipping and CRS assignment logic in the spatial accessor base and raster array/dataset modules.
      - Add an optional boolean parameter `allow_one_dimensional_raster=False` to `clip_box` on raster DataArray and Dataset classes, bypassing OneDimensionalRaster errors when True.
      - Extract internal non-warning method `_set_crs` from `set_crs`, issue a `FutureWarning` in public `set_crs` recommending `write_crs`, and route internal calls to `_set_crs`.
      - Document deprecation in `set_crs` docstrings, user guide notebook, and release history.
      - Execute the integration test suite covering `clip_box` with one-dimensional rasters allowed.
  [2] Goal: Preserve CRS metadata when exporting raster after slicing or squeezing dimensions
      rioxarray export to_raster and CRS being dropped - user error or bug?
hi there @snowman2 
I have a question about CRS persistence when you export a raster using rioxarray. It seems like when we begin to squeeze dimensions from the data, that the CRS gets dropped. I tried to reset the CRS before exp
      Way: consistent dataset CRS resolution and validation across data variables
      - Identify where dataset-level CRS lookup and export serialization are handled in the raster extension classes.
      - Update the dataset CRS getter property to collect CRS instances across all variables, verify that all non-None CRSs are identical, and raise an error if differing CRSs are found.
      - Simplify dataset raster export to rely directly on the dataset-level CRS property and write it to the converted DataArray if present, removing redundant validation loops.
      - Run the integration tests for dataset CRS access and raster export with mismatched CRS values.
  [3] Goal: Support exporting two-dimensional datasets to raster format
      Add support for `to_raster()` with 2D datasets.
It is often times useful to write a dataset to a raster. This is only possible if the data is 2D as the band dimension is taken by the different variables.

It would be nice to support metadata and writing band descriptions.
      Way: Exporting 2D xarray datasets to multi-band raster files
      - Inspect the existing `to_raster` implementation for DataArray objects on the raster accessor and identify the corresponding Dataset accessor class.
      - Implement `to_raster` on `RasterDataset` ensuring the dataset variables are verified as 2D spatial arrays sharing coordinates, CRS, and transform.
      - Handle dataset-to-raster writing by treating each data variable as a raster band, setting band descriptions to variable names, preserving or homogenizing nodata/metadata, and passing rasterio creation options.
      - Handle mismatch cases gracefully, such as validating consistent dimensions or rejecting variables with mismatched nodata when writing to formats requiring a single global nodata.
      - Run the integration tests for exporting datasets to raster.

=== TASK kinnala__scikit-fem-667 | repo kinnala/scikit-fem | python
--- Issue
Examples providing your own mesh and retriving the basis functions?
Are there any examples for supplying your own mesh (for example from scipy Delaunay) and extracting basis functions of some degree? 

For example you might evaluate the basis functions at a set of points for fitting against data as a kind of smoothing spline.

--- Real fix (for your judgement only)
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
+        lcar
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Support projecting functions onto composite vector finite element bases
      Projecting on a Basis with a combination of ElementVector and ElementComposite
```python
from skfem import *
from skfem.helpers import *

mesh = MeshTri()

basis = Basis(mesh, ElementVector(ElementTriP1()) * ElementTriP1())


@LinearForm
def from_form(v_t, v_z, w):  # e_t=[y,-x], e_z = 2

      Way: Support projecting nested tuple or list structures onto composite finite element bases
      - Locate the basis projection and interpolation normalization logic, typically found in the abstract basis class.
      - Modify the interpolation normalization method to recursively normalize nested tuples or lists, and ensure callable outputs are also normalized recursively.
      - Ensure that the normalization method always returns a flat or correctly structured tuple of numpy arrays matching the composite element's sub-elements.
      - Run the test suite to verify that projecting a lambda returning nested arrays/lists onto a composite vector basis succeeds.
  [2] Goal: Fix boundary edges calculation for 3D meshes
      Incorrect edges returned by `Mesh3D.boundary_edges`
I've been working on applying the block-diagonally preconditioned MINRES+AMG Stokes solver of #342 & ex32 to other MeshTet geometries and found some odd errors.  It seems to have nothing to do with the operators but something to do with `.find_dofs
      Way: identify 3D mesh boundary edges via boundary facets instead of boundary nodes
      - Locate the `boundary_edges` method in the 3D mesh base class.
      - Write a test verifying that `boundary_edges` returns only edges that are part of boundary facets, excluding interior edges whose endpoints happen to lie on the boundary.
      - Update `Mesh3D.boundary_edges` to collect edges from `boundary_facets()` and find their matching indices in `self.edges`, rather than checking if both end nodes belong to `boundary_nodes()`.
      - Run test suite to verify the fix works and resolves the issue.
  [3] Goal: Include internal boundaries when importing meshes from meshio
      internal boundaries from meshio not in bndfacets
I was porting some old one-dimensional code to scikit-fem and found that whereas previously I had identified an internal vertex facet as a `Physical Point` in the Gmsh GEO, this wasn't picked up by `skfem.io.meshio.from_meshio`.

Roughly the code is
      Way: Include internal boundaries when importing meshes from meshio
      - Locate the meshio import logic and boundary definition logic in the codebase.
      - Modify the meshio importer to allow facets that are not strictly on the external boundary to be included in named boundaries, while keeping the external boundary filter only where strictly necessary.
      - Update the boundary definition method on the Mesh class to accept a `boundaries_only` parameter, allowing users to define internal boundaries via function handles.
      - Verify the changes by running the test suite, ensuring that internal boundaries are correctly identified and existing tests pass.

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
--- Real fix (for your judgement only)
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
         
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent truncated pickle unpickling errors when reading captured driver requests
      _pickle.UnpicklingError: pickle data was truncated
When itterating over driver requests I get this error:
`_pickle.UnpicklingError: pickle data was truncated`
Sometimes it doesn't throw it, but most oftenly it does. It's really weird.
edit:
Alright so I figured that if you wrap it a try: except:
      Way: Handle truncated or corrupted pickle files by returning None
      - Identify where stored request files are read and unpickled from disk.
      - Wrap the `pickle.load` call inside a `try...except Exception` block in the request loading method, returning `None` if unpickling fails due to truncated or incomplete disk writes.
      - Update callers that load multiple items to filter out `None` values so that partially written or unpicklable items are gracefully omitted.
      - Run the storage unit tests to verify corrupted pickle data does not crash the loader.

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
--- Real fix (for your judgement only)
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

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Support tilde in configuration paths by expanding user home directory
      Path with `~` in config breaks repo parsing
An entry like this:

```ini
[cache]
    dir = ~/Projects
```

Breaks DVC into a weird error (and breaks at the moment Studio parsing, which is a separate issue a bit - we need to make resilient to this, but changes here would help us): 

<details>
      Way: expand user home directory in configuration paths
      - Locate configuration loading and path resolution logic, specifically where relative and absolute paths are parsed and resolved.
      - Introduce a custom string subclass to represent expanded paths, preserving the original user-defined path with the tilde.
      - Update the path resolution logic to expand tildes using `os.path.expanduser` and return the custom expanded path type if it resolves to an absolute path.
      - Update the path serialization/dumping logic to handle the custom expanded path type and preserve the original tilde-based path when writing back to configuration.
      - Verify the fix by running the configuration resolution and loading tests.
  [2] Goal: Handle empty lines in ignore files without throwing errors
      .dvcignore - new lines throw error, expected str instance, NoneType found.
```
DVC version: 1.0.0b2
Python version: 3.7.6
Platform: Ubuntu 18.04.4 LTS
Package: dvc_1.0.0b2_amd64.deb
Filesystem: ceph-fuse
```
- **Issue:** After installing DVC v1.0.0b2, all DVC commands fail with the following 
      Way: Filter out None values when grouping ignore file patterns
      - Locate the ignore file parsing and pattern compilation logic, specifically where patterns are grouped.
      - Reproduce the issue by creating an ignore file containing blank lines and running a command that triggers ignore file parsing.
      - Modify the pattern grouping list comprehension in the ignore pattern class to skip groups where the ignore status is None.
      - Verify the fix by running the test suite for ignore files.
  [3] Goal: Allow adding multiple files including modified tracked directories without duplicate stage errors
      Bug: `dvc add` fails with a modified file (or directory) at the end of a list of files
Using DVC 0.71.0 on RHEL6, installed via `conda`, configured for system-wide hard links (not sure if it's relevant):

```
$ dvc --version
0.71.0

$ cat ~/.config/dvc/config
[cache]
protected = true
type =
      Way: Reset repository graph cache when modifying stages or acquiring locks
      - Locate the repository lock decorator and the stage creation logic where cached stages/graphs might become stale.
      - Reset the repository graph cache inside the repository lock decorator immediately after acquiring the lock to ensure a clean state.
      - Reset the repository graph cache during multi-target stage creation after each individual stage is created, preventing subsequent checks from using stale cached stages.
      - Run the test suite to verify that adding multiple targets (including modified tracked directories) no longer raises duplicate stage errors.

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
--- Real fix (for your judgement only)
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

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Persist credentials across restarts to avoid repeated 2FA prompts
      New 2FA email every 24hrs
Been working with RC2, with some adjustments I've been able to stabilize it a bit but I definitely can't run a script more than once every 2-3 minutes as it surely barfs on me within that windows. Does anyone ever hit the blink servers that often and have issues?

Also I 
      Way: adapt authentication payload and token extraction to new API schema
      - Identify login request payload structure and response parsing logic in API and auth client modules.
      - Update login request payload parameters to match the newer API requirements, including setting reauth to boolean True and dropping deprecated keys.
      - Update response extraction logic in the auth module to read token, region, account, client ID, and 2FA verification flags from the updated schema paths under the account and auth keys.
      - Update authorization headers to supply required content type headers alongside the auth token and user agent.
      - Update test fixtures and unit tests to reflect the new API login response schema and test updated auth workflows.
      - Run the auth test suite to ensure login, token refresh, and key validation succeed with the new schema.
  [2] Goal: Support two-factor authentication for user login
      New Login Method - blinkpy will stop working May 11th unless fixed!
**Describe the bug**
As of March 30, 2020 (according to the "What's New" section in the Blink App), 2FA will be available for blink.  What this means for the blinkpy library is uncertain, but I wouldn't be surprised if current logi
      Way: Adding two-factor authentication verification flow to client login
      - Examine the authentication lifecycle in the API client and login handler to locate where login tokens, device identifiers, and subsequent network setup calls occur.
      - Update the API request payload to supply the required device identifier fields and update the PIN verification endpoint to send PIN data with client authorization headers.
      - Update the login handler to support persistent device identifiers and helper methods to determine if two-factor PIN verification is required and to submit the verification PIN.
      - Refactor the main client start workflow to detect two-factor requirements, accept user PIN entry when interactive prompting is enabled or defer initialization to a post-verification method when headless (`no_prompt=True`), and fetch network setup upon authentication.
      - Run the test suite covering interactive/non-interactive prompt handling and login handler key verification.
  [3] Goal: Handle non-JSON responses during authentication and network requests gracefully
      Error requesting networks
I have been running 0.15 for over a week now and no issues, but yesterday it started to error on self.get_networks()

```
Traceback (most recent call last):
  File "scripts/grab-photo.py", line 9, in <module>
    blink.start()
  File "/var/services/homes/admin/.local/
      Way: Removing manual Host header override from HTTP request headers
      - Identify where request headers are constructed for authenticated requests and check for overriding standard headers such as Host.
      - Remove the custom 'Host' header entry from the authentication header dictionary, retaining only required auth tokens.
      - Update test assertions expecting the old header structure to expect only the remaining authentication tokens.
      - Run the authentication test suite to verify headers and response handling.

=== TASK Textualize__textual-3360 | repo Textualize/textual | python
--- Issue
Restrict Pilot.click to visible area
I suspect that `Pilot.click` will allow clicking of widgets that may not be in the visible area of the screen.

Since we are simulating a real user operating the mouse, we should make that not work. Probably by throwing an exception.

It also looks like it is possible to supply an `offset` that would click outside of the visible area. This should probably also result in an exception.
--- Real fix (for your judgement only)
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
-          
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Allow Pilot click to use Screen as a selector
      Pilot.click can't use `Screen` as selector.
If you try something like `pilot.click(Screen)`, you get a `NoMatches` exception from the query.
      Way: Query root application rather than screen in test pilot actions
      - Inspect test automation methods (like pilot click or hover) where widget selectors are resolved against the current screen.
      - Write reproduction tests attempting to click or hover a selector referencing the top-level Screen widget itself.
      - Change widget query resolution in Pilot methods (such as click and hover) from `screen.query_one(selector)` to `app.query_one(selector)` so queries encompass top-level screens.
      - Run the pilot test suite to ensure selecting screens and child widgets works correctly for pilot actions.
  [2] Goal: Fix incorrect widget selection when clicking partially visible scrolled buttons
      Border demonstration acts improperly under scrolling
Recipe to reproduce:

Run `textual borders`. Click on `[Heavy`]. Border will change. Scroll down buttons panel to click on `[Wide]`. At top of screen click topmost visible button (in my case `[Heavy]`, but YMMV). _Ensure that only the bottom two
      Way: Matching mouse down and mouse up widgets for click generation
      - Identify the driver's mouse event handling logic where `Click` events are synthesized from `MouseDown` and `MouseUp` pairs.
      - Update the driver to record the widget at the mouse down coordinates (`self._mouse_down_widget = self._app.get_widget_at(event.x, event.y)[0]`) instead of only tracking timestamp.
      - Update the mouse up handler in the driver so a `Click` event is only emitted if the widget under the cursor on `MouseUp` matches `self._mouse_down_widget`.
      - Implement `mouse_down` and `mouse_up` helper methods on the test pilot to allow simulating individual mouse press and release actions in tests.
      - Run the test suite to verify focus, mouse press/release, and pilot click behaviors pass.
  [3] Goal: Preserve cursor position when focusing the Input widget
      Focusing Input widget should not move cursor to the end
When the Input widget is focused, the cursor currently moves to the end.

This leads to a flickering effect when it's focused via a click, as the cursor jumps to the end (due to the focus handler), and then jumps to the click location (due to
      Way: preserve input cursor position on widget focus
      - Inspect the widget's focus handler and cursor position initialization logic to identify where cursor position is forced to the end of the input value on focus.
      - Remove the line in `_on_focus` that overrides `cursor_position` to `len(self.value)`, ensuring the cursor retains its existing position when focused.
      - Introduce a state flag (e.g. `_initial_value = True`) so that the cursor position is placed at the end only during the initial value assignment in `_watch_value`.
      - Refactor cursor blinking logic into helper methods (`_restart_blink_cycle` and `_pause_blink_cycle`) and hook them into focus, blur, key, and mouse events.
      - Run the input modification test suite to verify that key actions and cursor positioning behave correctly.

=== TASK kytos__python-openflow-280 | repo kytos/python-openflow | python
--- Issue
OF 1.3 Compliance: Missing attribute
File: `v0x04/controller2switch/common.py`

`class DecStats(GenericStruct):`

Following attribute is missing:
`dp_desc = Char(length=DESC_STR_LEN)`
--- Real fix (for your judgement only)
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
+            dp_desc (str): Datap
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Fix attribute unpacking error during Python 3.6 compatibility migration
      Error while migrating to python 3.6
While testing the full project on python 3.6 (running kyco against a mininet instance with 3 hosts and 3 switches, no loop version), the error below were throughed on the kytos console:

> Exception in thread Thread-198:
> Traceback (most recent call last):
> 
      Way: Fix attribute unpacking error on read-only property attribute
      - Locate where the attribute assignment occurs during unpacking and check property definitions on the class.
      - Identify that the class defines a read-only `@property` without a setter for the attribute being updated during unpack.
      - Modify the unpack method (and internal property accesses) to update the underlying private storage attribute directly instead of assigning to the read-only property.
      - Run the network types test suite to verify property assignment succeeds during binary unpacking.
  [2] Goal: Ensure descriptor attributes are not shared across class instances
      Error on Descriptor utilization
The way we are using Descriptors is incurring in a wrong behavior when instantiating two objects of the same class.
      Way: instantiate descriptor type attributes per class instance via metaclass or constructor
      - Identify shared state across class instances caused by class-level descriptor definitions.
      - Update base field type implementations to support optional enum references (`enum_ref`) and handle custom bitmasks or enums during packing and unpacking.
      - Update class definitions and fields to reference appropriate enum types and bitmask classes.
      - Run pytest on the test suite to verify class instances no longer share descriptor attribute state.
  [3] Goal: Rename class attribute that shadows a Python builtin name
      Attribute with builtin name
In `/v0x04/common/queue.py`, `class QueuePropHeader` has an attribute called `property`, which is a Python builtin name. Is this needed somehow?
      Way: Rename class attributes and parameter names that shadow Python builtins across modules
      - Search the codebase for class field attributes and constructor parameters that shadow Python builtins like 'property' or 'len'.
      - Identify all instantiations and references to those updated class attributes and constructor keyword arguments across protocol versions.
      - Rename `property` and `len` attributes/parameters in `QueuePropHeader` (and references in instantiated headers like `QueuePropMinRate`, `QueuePropMaxRate`, and `QueuePropExperimenter`) to descriptive non-builtin names like `queue_property` and `length`.
      - Run the test suite for impacted protocol messages to verify packing and unpacking work with renamed attributes.

=== TASK iterative__dvc-5378 | repo iterative/dvc | python
--- Issue
Support additional configuration parameters for S3 transfer
**Problem**
When using S3 as remote for DVC, boto3 reads also the configuration file `~/.aws/config` typically used by the original AWS CLI tool. Not all configuration parameters from this file have an effect in DVC at the moment. For instance, the last line `multipart_threshold = 500MB` will be skipped by DVC while the previous line `signature_version = s3` will be applied when pushing data into S3:      
```ini
[default]
output = json
s3 =
  signature_version = s3
  multipart_threshold = 500MB
```
**Suggestion**
Suggestion is to support optional yet useful parameters from `~/.aws/config` as per [AWS CLI S3 documentation](https://docs.aws.amazon.com/cli/latest/topic/s3-config.html) to control S3 transfers for performance reasons or to unlock the advanced configuration capabilities. 

These parameters should be passed to TransferConfig:
- `max_concurrent_requests` - The maximum number of concurrent requests.
- `max_queue_size` - The maximum number of tasks in the task queue.
- `multipart_threshold` - The size threshold the CLI uses for multipart transfers of individual files.
- `multipart_chunksize` - When using multipart transfers, this is the chunk size that the CLI uses for multipart transfers of individual files.
- `max_bandwidth` - The maximum bandwidth that will be consumed for uploading and downloading data to and from Amazon S3.

If it is reasonable and doable - support also the following s
--- Real fix (for your judgement only)
diff --git a/dvc/config_schema.py b/dvc/config_schema.py
--- a/dvc/config_schema.py
+++ b/dvc/config_schema.py
@@ -138,6 +138,7 @@ class RelPath(str):
                     "region": str,
                     "profile": str,
                     "credentialpath": str,
+                    "configpath": str,
                     "endpointurl": str,
                     "access_key_id": str,
                     "secret_access_key": str,
diff --git a/dvc/tree/s3.py b/dvc/tree/s3.py
--- a/dvc/tree/s3.py
+++ b/dvc/tree/s3.py
@@ -11,12 +11,14 @@
 from dvc.path_info import CloudURLInfo
 from dvc.progress import Tqdm
 from dvc.scheme import Schemes
-from dvc.utils import error_link
+from dvc.utils import conversions, error_link
 
 from .base import BaseTree
 
 logger = logging.getLogger(__name__)
 
+_AWS_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".aws", "config")
+
 
 class S3Tree(BaseTree):
     scheme = Schemes.S3
@@ -60,6 +62,56 @@ def __init__(self, repo, config):
         if shared_creds:
             os.environ.setdefault("AWS_SHARED_CREDENTIALS_FILE", shared_creds)
 
+        config_path = config.get("configpath")
+        if config_path:
+            os.environ.setdefault("AWS_CONFIG_FILE", config_path)
+        self._transfer_config = None
+
+    # https://github.com/aws/aws-cli/blob/0376c6262d6b15dc36c82e6da6e1aad10249cc8c/awscli/customizations/s3/transferconfig.py#L107-L113
+    _TRANSFER_CONFIG_ALIASES = {
+        "max_queue_size": "max_io_queue",
+        "max_concurrent_requests": "max_concurrency",
+        "multipart_threshold": "multipart_threshold",
+        "multipart_chunksize": "multipart_chunksize",
+    }
+
+    def _transform_config(self, s3_config):
+        """Splits the general s3 config into 2 different config
+        objects, one for transfer.TransferConfig and other is the
+        general session config"""
+
+        config, transfer_config = {}, {}
+        for key, value in s3_config.items():
+            if key in self._TRANSFER
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Fix adding an entire S3 directory to DVC
      Unable to dvc add more than 1 file at a time in s3 bucket
** Note: DVC version 0.66.1, pip install, Ubuntu 16.04

I am having difficulty configuring DVC to track files on an s3 bucket with the structure

```
ellesdatabucket
       ├── data
       │     ├── img1.png
       │     ├── img2.png
      Way: ignore directory key objects when listing files and support empty directories in s3 remote
      - Locate the S3 remote implementation handling object listing, directory checking, directory creation, and file walking.
      - Reproduce the issue or verify behavior when walking S3 prefixes that contain S3 directory marker keys ending with a trailing slash (`/`).
      - Update file walking logic in S3 remote to skip key paths ending with `/`, and implement `makedirs` by putting an empty object with a trailing slash key.
      - Run unit tests for S3 remote to verify that walking files ignores directory keys and directory creation works.
  [2] Goal: Fix S3 file listing when file names share a folder prefix
      Cannot add file having name with substring of a folder as prefix in s3
### Steps to reproduce
1. Upload two files in s3: `folder/data/data.csv` and` folder/datasets.md`.
2. Setup remotes and caches.
```sh
dvc remote add -f s3 s3://dvc-temp/folder
dvc remote add -f cache remote://s3/cache
dvc c
      Way: Ensure trailing slash on directory path during S3 object listing and checking
      - Identify S3 file listing and existence checking logic where prefix matching without trailing slashes causes partial string matches against non-child paths.
      - Reproduce by attempting to walk or check existence for a path prefix (e.g., `folder/data`) when sibling paths with shared prefixes exist (e.g., `folder/datasets.md`).
      - Implement `isfile` for S3 using `head_object` to accurately check single file existence and return False if path ends with `/` or throws a 404 ClientError.
      - Update S3 `exists` method to return `self.isfile(path_info) or self.isdir(path_info)`.
      - Append a trailing slash (e.g. `path_info / ""`) in `walk_files` before invoking `_list_paths` so S3 prefix matching only targets contents inside the directory.
      - Run unit tests for S3 existence and file checking.
  [3] Goal: Allow modifying shared remotes in local configuration
      remote local config does not see existing remote
## Bug Report

### Please provide information about your setup

**Output of `dvc version`:**

```console
DVC version: 1.1.10+1c9588
Python version: 3.8.4
Platform: macOS-10.15.4-x86_64-i386-64bit
Binary: False
Package: None
Supported remot
      Way: allow modifying inherited configuration entries in local config scope
      - Locate the remote configuration modify command handler and config validation logic.
      - Reproduce the error by defining a remote globally/project-wide and attempting to modify an option on it targeting local configuration level.
      - Make the dict merging helper function public so config subcommands can access merged configurations across levels.
      - Update remote modify command to check remote existence against the merged configuration up to the requested level, and initialize an empty dictionary for the target section in the level-specific configuration if not yet present.
      - Run the remote configuration functional test suite to confirm local modification of existing remotes works without configuration errors.

=== TASK PyCQA__flake8-bugbear-316 | repo PyCQA/flake8-bugbear | python
--- Issue
New check: visit_ function with no generic_visit/visit
This is kind of super niche, and mostly just of interest to flake8 plugin writers in particular, but I accidentally forgot to continue `visit`'ing in a couple functions I wrote recently in flake8-trio and realized it'd be incredibly handy - and very easy to implement - an optional check where if a function name matches `visit_*` it raises a warning if there's no `self.visit(...)` or `self.generic_visit(...)` within it's body.

I imagine it'd be nice to have that in the CI for flake8-bugbear itself as well, even though your structure makes it less likely than it is for flake8-trio currently.
--- Real fix (for your judgement only)
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
+        # with a non-empty _fields
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Handle non-name exception expressions when checking for re-raised exceptions
      Error in latest version of flake8-bugbear 24.1.15
Getting error with latest version of flake8-bugbear

```
Traceback (most recent call last):
  File "/usr/local/lib/python3.10/multiprocessing/pool.py", line 125, in worker
    result = (True, func(*args, **kwds))
  File "/usr/local/lib/python3.
      Way: safely check ast.Name attributes on raise node expressions 
      - Locate the AST visitor method handling `ast.Raise` nodes inside exception handlers based on the traceback.
      - Add a test case with a try/except BaseException block containing a `raise Call(...)` or non-identifier expression to reproduce the `AttributeError: 'Call' object has no attribute 'id'` crash.
      - Update the check in `visit_Raise` to ensure `node.exc` is an instance of `ast.Name` before accessing `node.exc.id` when matching the bound exception variable.
      - Run the test suite to verify the fix prevents crashes when `raise` raises non-name expressions.
  [2] Goal: Fix compatibility with modern linter select options when emitting plugin warnings
      `should_warn` is incompatible with flake8 (next)
minimal reproduction (trigger any `B9*` warning with the default settings)

```console
$ rm -rf venv && virtualenv -qq venv && venv/bin/pip install -qq git+https://github.com/pycqa/flake8 git+https://github.com/pycqa/flake8-bugbear && venv/bin/flak
      Way: safely check options attributes that can be NoneType
      - Locate warning emission checks in the linter plugin where runner options like `select` and `extend_select` are inspected.
      - Reproduce the `TypeError: argument of type 'NoneType' is not iterable` by passing `options` where `select` or `extend_select` is `None`.
      - Add truthiness checks before testing membership in `options.select` and `options.extend_select` to avoid iterating over `None`.
      - Run the test suite to confirm warning filtering succeeds when options are `None`.
  [3] Goal: Handle starred expressions in except clauses without crashing
      crash on tuple expansion in try/except block
Running flake8 against the following file produces no report as an exception is raised in bugbear
```python
# dummy.py
grey_list = (ValueError,)
black_list = (TypeError,)

try:
    int("1e3")
except (*grey_list, *black_list):
    print("error cau
      Way: Handle non-attribute nodes when resolving AST qualified exception names
      - Locate helper functions that traverse AST nodes into string representations for exception handlers (e.g. `_to_name_str`).
      - Reproduce the crash by creating a test case with unpacked tuples/starred expressions (e.g. `except (*a, *b):`) in an except clause.
      - Replace strict attribute assertions with handling that safely traverses node values when `node.attr` is absent (such as an `ast.Starred` node having a `.value` without `.attr`).
      - Run the test suite to verify starred expressions in except clauses no longer crash AST traversal.

=== TASK PyCQA__flake8-1440 | repo PyCQA/flake8 | python
--- Issue
add color support for formatting
~essentially replace `flake8-colors`

(needs a bit of design before implementation)
--- Real fix (for your judgement only)
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
+       
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: List available formatters in the command line format option help message
      Display list of available formatters with help for --format
In GitLab by @blueyed on Apr 26, 2018, 03:32

`flake8 --help` contains:

> --format=format       Format errors according to the chosen formatter.

It would be nice/helpful if the list of available formats would be listed there.
      Way: dynamically include plugin-provided choices in CLI option help text
      - Locate where CLI options and their help strings are defined, and trace where plugin reporters/formatters are discovered.
      - Pass the list of discovered formatter/reporter plugin names to the option manager during initialization.
      - Store the formatter names on the option manager instance.
      - Update the `--format` option definition to format its help string with the sorted list of available formatter names, format-string substitution examples, and default value placeholder.
      - Run integration and unit tests to verify option parsing, formatting help output, and manager initialization.
  [2] Goal: Add an option to require specified plugins to be installed
      Add a "require plugin" option
In GitLab by @mschwager on Jan 7, 2019, 07:34

Hi all,

I'd like to request an additional option to `flake8` for requiring certain plugin(s) are installed and selected during a run. For example,

```
$ flake8 --require=XYZ --select=XYZ /path/to/lint
```

I could also en
      Way: enforcing required plugins via early stage cli and config options
      - Add the CLI flag `--require-plugins` to the early-stage argument parser so it is parsed before plugins are loaded.
      - Extend `PluginOptions` and `parse_plugin_options` in the plugin finder to parse and store `require_plugins` from CLI arguments or configuration files.
      - Implement a check in `find_plugins` that normalizes package distribution names and raises an `ExecutionError` when any expected plugin package is missing.
      - Update application bootstrap logic and legacy API entry points to pass `require_plugins` through `find_plugins` and `parse_plugin_options`.
      - Run the unit and integration tests for plugin finding, parsing, and required plugin validation.
  [3] Goal: Handle non-existent home directory gracefully during configuration file lookup
      Exception if HOME is not an existing path
### how did you install flake8?

```console
$ pip install flake8
```


### unmodified output of `flake8 --bug-report`

```json
{
  "platform": {
    "python_implementation": "CPython",
    "python_version": "3.9.13",
    "system": "Linux"
  },
  "plugi
      Way: Handle non-existent or inaccessible home directories during config lookup
      - Locate configuration discovery logic that resolves the user home directory using `os.path.expanduser("~")` and checks its stat information.
      - Reproduce the `FileNotFoundError` or `OSError` by pointing the `HOME` environment variable to a non-existent path during config discovery.
      - Wrap the `_stat_key(home)` invocation in a `try...except OSError` block, falling back to `home_stat = None` when stat fails.
      - Verify the fix by running the configuration discovery unit tests with a missing home directory.

=== TASK postlund__pyatv-978 | repo postlund/pyatv | python
--- Issue
Add retries for heartbeats (MRP)
**What feature would you like?**
When a heartbeat fails, an error is logged and the connection is closed. Generally this is good, but in case the response happens to be delayed long enough (even though nothing is wrong), this is quite pessimistic and it's probably a good idea to make another try before failing. Do not impose an initial delay on the retry though (just retry immediately) as we still want to fail as early as possible in case of an actual problem.

**Describe the solution you'd like**
From the description above: make a retry immediately when a heartbeat fails. Only log error if retry also fails.

**Any other information to share?**
Heartbeat errors seems to happen sporadically and I believe there's not actually a problem in most cases. This is an attempt to recover in those cases, but it's still not a very well tested method so I can't be sure that it works.
--- Real fix (for your judgement only)
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
 
 

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent infinite login loops on DAAP authentication failure
      pyatv implicitly logged out, logging in again
By a coincidence I was looking through my logs of home-assistant and saw that the same message was spammed over and over again. I will start by removing the remote from apple tv and add again. I have the second or third gen apple tv.

The message is re
      Way: reset revision number on DAAP push update failure to prevent re-authentication loops
      - Locate push update exception handling in the DMAP module where playstatus error callbacks are scheduled.
      - Reproduce the infinite re-authentication loop when push status updates fail with an authentication error or non-200 response while retaining a non-zero revision number.
      - In the DMAP push updater exception handler, set the playstatus revision number back to 0 before dispatching the error to listeners.
      - Run the targeted test suite to confirm the playstatus revision resets on error.
  [2] Goal: Ensure media playing metadata objects are immutable after creation
      Playing objects returned by playing interface is not immutable for MRP
**Describe the bug**
A while back I made some changes so that `Playing` objects from `Medata.playing` are immutable. Before that, content would potentially change when new updates were made because the returned object would quer
      Way: Make media playing metadata objects immutable by value encapsulation
      - Identify the `Playing` class as the source of mutable metadata objects, specifically where its properties are defined as abstract methods.
      - Add an `__init__` method to the `Playing` class that accepts all relevant metadata attributes (media type, device state, title, artist, album, genre, total time, position, shuffle, repeat, hash) as parameters.
      - Inside the new `__init__` method, assign each parameter to a corresponding private instance variable (e.g., `self._title = title`).
      - Remove the `@abstractmethod` decorator from all property definitions within the `Playing` class and modify them to return the value from their respective private instance variables (e.g., `return self._title`).
      - Adjust the `hash` property to prioritize returning an explicitly provided hash from `self._hash` if available, otherwise compute it.
      - Run the functional and unit tests to confirm that `Playing` objects are now immutable and all related functionality works as expected.
  [3] Goal: Suppress push updates when playing state has not changed
      Push updates are sent when nothing has changed
**Describe the bug**
This is a very old "bug" that I have somewhat let slip, but it annoys me too much so it's time to fix it. The gist is that push updates are supposed to be sent whenever something in the playing API changes, e.g. title or media type
      Way: Implement playing state equality check and deduplicate push updates
      - Locate the `Playing` interface and `PushUpdater` base class in the media player interface module.
      - Add `__eq__` implementation to `Playing` that compares public properties like media_type, device_state, title, artist, album, genre, total_time, position, shuffle, and repeat across instances.
      - Update `PushUpdater` to track `_previous_state` and filter out update callbacks in `post_update` when the new playing state equals `_previous_state`.
      - Run interface unit tests to verify playing state equality and update suppression logic.

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
--- Real fix (for your judgement only)
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

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent misplaced closing parentheses when formatting imports with trailing comment parentheses
      isort introduces SyntaxError with multiline "from a import b as c" plus a comment ending in a parenthesis
```
% cat frimp.py 
from a import (
    b as c,  # comment that is long enough that this import doesn't fit in one line (parens)
)
% isort --diff frimp.py --profile=black
--- /Users/jelle/
      Way: Preserving parentheses in comments during import wrapping
      - Identify the code that wraps long import lines across multiple lines and where trailing comments are parsed and handled.
      - Reproduce the issue by formatting a multiline import with an inline comment that ends with a closing parenthesis, observing the closing parenthesis being stripped from the comment and misplaced into code.
      - In the line wrapping logic (`wrap.py`), ensure the post-processing branch that re-positions the closing parenthesis before a trailing comment only executes within the specific wrapper mode block where the closing parenthesis was appended to the final line rather than running unconditionally across all wrap modes.
      - In the parsing logic (`parse.py`), add safety checks to ensure `just_imports` is non-empty and `just_imports[-1]` is truthy before indexing and splitting to detect trailing commas.
      - Run the wrap unit tests to verify comments ending in parentheses remain untouched.
  [2] Goal: Support relative imports without whitespace between dots and the import keyword
      Unrecoverable exception with ``  from ..import ccc`` 
Hello, 
I got ` ERROR: Unrecoverable exception thrown when parsing ./aaa/bbb/ccc.py! This should NEVER happen.`  

~~~bash
$ python --version
Python 3.11.4
$ isort --version

                 _                 _
                (_) ___  
      Way: Normalize multi-dot relative imports missing whitespace before import
      - Inspect the line normalization and import parsing logic to find where spaces are injected around keywords and dots.
      - Reproduce the crash with relative import lines having multiple dots and no space before import or cimport (e.g. `from ..import bbb` or `from ..cimport bbb`).
      - Replace exact string matching with regex substitution (e.g., using `re.sub`) to match arbitrary numbers of relative dots in `from(\.+)import `, `from(\.+)cimport `, ` (\.+)import `, and ` (\.+)cimport ` and insert the required spacing around them.
      - Rename any private helper functions like `_normalize_line` and `_strip_syntax` to public `normalize_line` and `strip_syntax` across parser and identify modules if needed, updating imports accordingly.
      - Run the test suite to verify line normalization and parsing succeed for all single and multi-dot relative import variations.
  [3] Goal: Fix multiline export sorting corruption with sort reexports option
      BUG: --sort-reexports don't work with multiple lines __all__
```python
import datetime

from mylib import (
    module1,
    module2,
    module3,
    module4,
)

__all__ = (
    "func1",
    "func2",
    "func3",
    "func4",
)
```

after using `isort file.py --sort-reexports`, be
      Way: relative stream seek and truncate for reexport rewriting
      - Identify where reexported literal definitions like `__all__` are parsed and rewritten to the output stream.
      - Reproduce the corruption when running with `--sort-reexports` on multiline imports and exports.
      - Replace absolute character tracking (`sort_section_pointer`) with relative rollback tracking (`reexport_rollback`) based on the length of the line where reexport sorting started.
      - When rewriting the sorted code for a reexport, seek back by `output_stream.tell() - reexport_rollback`, reset rollback to 0, write the sorted code, and call `output_stream.truncate()` to remove any remaining stale output.
      - Verify that multiline imports and exports are sorted and output cleanly without overwriting earlier lines.

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

--- Real fix (for your judgement only)
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
   
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Support importing or getting data from repositories with cloud-versioned remotes
      cloud versioning: imports
* [x]  For chained `import-url` imports I think it will not work right now unless the file has been pushed to a DVC remote, but when we get to refactoring regular cached objects to use dvc-data index for push/pull (so everything is unified to use the dvc-data index) this ki
      Way: delegate repository dependency operations to DVCFileSystem
      - Locate the dependency class responsible for external repository imports.
      - Refactor the dependency class to delegate file system operations, revision resolution, and status checks directly to `DVCFileSystem` instead of manually building hash files, checking out objects, or managing local object databases.
      - Simplify the dependency class by removing redundant methods like `download`, `get_used_objs`, `_get_used_and_obj`, `_check_circular_import`, `get_obj`, and `get_meta`.
      - Run the import test suite to verify that importing from external repositories (including cloud-versioned ones) works correctly.
  [2] Goal: Allow retrieving data from a git revision when master is not a DVC repository
      dvc get --rev doesn't work if master is not a dvc repo
## Bug Report

### Please provide information about your setup

**Output of `dvc version`:**

```console
DVC version: 1.1.7
Python version: 3.7.2
Platform: Darwin-18.7.0-x86_64-i386-64bit
Binary: False
Package: pip
Supported remotes:
      Way: use tree abstraction for config loading to support non-working-tree revisions
      - Locate where the repository configuration is loaded and saved, and where the repository tree is initialized.
      - Modify the configuration class to accept a tree parameter, defaulting to the working tree, and use this tree instead of direct OS filesystem calls when reading or writing configuration files.
      - Ensure the repository initialization sets up the tree before initializing the configuration, passing the tree to the configuration instance.
      - Implement `makedirs` on the tree abstractions so that configuration directories can be created safely on both working trees and virtual trees.
      - Verify the fix by running the unit tests for repository branch configuration.
  [3] Goal: Allow using a read-only cache directory
      0.90.0 requires write permission on cache dir
Since 0.90.0, dvc fetch, dvc checkout and dvc pull all fail with:

$ dvc pull
ERROR: unexpected error - [Errno 30] Read-only file system: '/Volumes/dvc/eac-plv-dataset-iphone5-4k-okr_q4_2019/2c/b5d8b77eb1ac4f56dfbce8aa2f4dfd' 

/Volumes/dvc is a rea
      Way: ignore read only file system errors when setting file permissions
      - Locate permission handling code in the local storage remote implementation.
      - Reproduce failure on read-only file systems during file protection where os.chmod raises OSError with errno.EROFS.
      - Catch errno.EROFS in the chmod exception handler and ignore it since files on a read-only filesystem cannot and do not need to be modified.
      - Update permission mask comparisons to use stat.S_IMODE to extract file mode bits properly.
      - Run unit tests for local remote file protection to verify EROFS errors are safely ignored.

=== TASK jupyterhub__chartpress-63 | repo jupyterhub/chartpress | python
--- Issue
Support images defined as string
Add support for images defined as `<registry>/<repo>:<tag>`.

Possible solutions to keep compatibility with images defined as mapping:

1. check the image value in `values.yaml`
2. if it is mapping use current way of setting the dictionary;
3. else use formatted string `"{registry}/{repo}:{tag}".format(...)`

Please let me know what you think and I will send a PR.
--- Real fix (for your judgement only)
diff --git a/.travis.yml b/.travis.yml
index a47cf03..998bf51 100644
--- a/.travis.yml
+++ b/.travis.yml
@@ -5,11 +5,12 @@ cache: pip
 install:
   - set -e
   - pip install --upgrade pip
-  - pip install pyflakes .
+  - pip install pyflakes pytest .
 script:
   - chartpress --version
   - chartpress --help
   - pyflakes .
+  - pytest -v ./tests
 
   # This is a workaround to an issue caused by the existence of a docker
   # registrymirror in our CI environment. Without this fix that removes the
diff --git a/CHANGELOG.md b/CHANGELOG.md
index fdbd930..f6b591c 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -4,6 +4,23 @@
 
 ## 0.4
 
+### 0.4.3
+
+- Support `valuesPath` pointing to a single `image:tag` string in
+  addition to a dict with separate `repository` and `tag` keys.
+- Support lists in `valuesPath` by using integer indices,
+  e.g. `section.list.1.image` for the yaml:
+
+  ```yaml
+  section:
+    list:
+      - first: item
+        image: "not set"
+      - second: item
+        image: "image:tag"  #  <--sets this here
+  ```
+
+
 ### 0.4.2
 
 - --long flag to always output build information in image tags and chart version [#57](https://github.com/jupyterhub/chartpress/pull/57) ([@consideRatio](https://github.com/consideRatio))
diff --git a/README.md b/README.md
index 7dbf6a9..6caa976 100644
--- a/README.md
+++ b/README.md
@@ -157,3 +157,19 @@ in your `.travis.yml`:
 git:
   depth: false
 ```
+
+## Development
+
+Testing of this python package can be done using [`pyflakes`](https://github.com/PyCQA/pyflakes) and [`pytest`](https://github.com/pytest-dev/pytest). There is also some additional testing that is only run as part of TravisCI, as declared in [`.travis.yml`](.travis.yml).
+
+```
+# install chartpress locally
+pip install  -e .
+
+# install dev dependencies
+pip install pyflakes pytest
+
+# run tests
+pyflakes .
+pytest -v
+```
diff --git a/chartpress.py b/chartpress.py
index df0fb19..f6847da 100755
--- a/chartpress.py
+++ b/chartpress.py
@@ -10,6 +10,
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Push latest image tag by default when pushing images
      --push-latest-tag by default
### Proposed change
To publish a `latest` image tag by default and make it configurable by a flag.

### Who would use this feature?
It would allow for example a GitHub action setup to do a security scan of the latest image be able to do it without having a lot of cod
      Way: Redirect CLI logging to stderr and add list images option
      - Locate CLI argument parsing, subprocess call wrappers, and logging calls in the main application file.
      - Define a logging helper that outputs messages to stderr and update subprocess checks to redirect stdout to stderr.
      - Replace stdout print statements across image building and chart updating routines with calls to the stderr logging helper.
      - Add a `--list-images` flag to the argument parser that enables image listing, sets `skip_build` to True, and prints unique image specifications to stdout.
      - Run pytest to verify that `--list-images` works cleanly and existing repository integration tests pass.
  [2] Goal: Prevent image listing command from modifying Chart version
      --list-images updates Chart.yaml's version
Running `chartpress --list-images` will also update Chart.yaml with a version which I consider unwanted.
      Way: prevent configuration file updates during read-only listing commands
      - Locate the main execution flow where configuration files (such as Chart.yaml) are parsed and updated.
      - Identify the command-line argument or flag that triggers a read-only operation, such as listing images.
      - Wrap the chart version update logic in a conditional block so that it is skipped when the read-only listing flag is active.
      - Run the test suite to ensure that listing images no longer modifies the chart configuration files.
  [3] Goal: Add a command line flag to specify a custom configuration file
      add a `--file` flag to use a different `chartpress.yaml`
### Proposed change
In projects involving more than one chart, it would be useful to have a `--file` flag such that a specific `chartpress.yaml` file could be targeted, e.g. 

```
chartpress --file another-chartpress.yaml
```
### Alterna
      Way: add custom config file flag to cli tool
      - Locate CLI argument parsing and configuration file loading logic in the main entry point and internal path helper functions.
      - Add a `--config` command-line argument to argparse defaulting to `chartpress.yaml`, and validate readability upon parsing.
      - Update internal path helper functions and file read calls to accept and use the custom configuration file path instead of hardcoded `chartpress.yaml` strings.
      - Adjust mutually exclusive argument checks (such as `--reset`) to allow passing `--config` alongside restricted flags.
      - Run test suite to verify the custom config option and check argument exclusivity behavior.

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

--- Real fix (for your judgement only)
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

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent adding git-ignored directories to version control
      add: confusing interaction with gitignore'd subdirectories
# Bug Report

## Description

`dvc add` will happily complete when targeting subdirectories that appear transiently in a `.gitignore`. The tracked data has a corresponding `.dvc` file generated and appears in the cache correctly, but att
      Way: prevent adding files when their tracking files are git-ignored
      - Locate where DVC file names and paths are validated during stage creation and file operations.
      - Implement a helper function to check if a path is git-ignored using the repository's SCM interface.
      - Update the DVC file path validation logic to check if the target path is git-ignored, raising an exception if it is.
      - Run the test suite to verify that attempting to add a git-ignored directory or file fails with an explicit error.
  [2] Goal: Optimize pull performance when specifying multiple target files
      Pull with masked targets is slow
`dvc pull` with specifying targets by shell-expanded glob mask (e.g. `dvc pull */*.dvc`) works significantly slower then global `dvc pull`. This is especially hurtful when pulling large number of small files. At the same time, passing glob mask to dvc via `dvc pull '
      Way: batch multi-target data sync operations instead of looping per target
      - Identify where data sync commands (pull, push, fetch, status) iterate over targets sequentially at the CLI command level.
      - Refactor CLI command handlers to pass all targets at once (`targets=self.args.targets`) instead of executing `do_run(target)` in a loop.
      - Update underlying repository API methods (`pull`, `push`, `fetch`, `status`, `used_cache`, `_local_status`, `_cloud_status`) to accept `targets` as a collection instead of a single `target`.
      - Aggregate stages for all specified targets during cache collection in `used_cache` and `status` so remote/local sync runs in a single batch operation.
      - Run test suite to verify data sync operations across multiple targets.
  [3] Goal: Support pulling specific targets when using run cache
      Make `dvc pull --run-cache` allow `targets` for a granular pull process
Right now, if I do 

`dvc pull [target]`
this will pull *only* the `target` I am interested in.

However, if I do `dvc pull --run-cache [targets]`
the `target` is ignored, *everything* is pulled from remote. Not even a war
      Way: respect target filters when fetching run cache and add pull support on restore
      - Locate fetch logic and stage run cache restoration handling target filtering and stage execution.
      - Remove unconstrained run-cache pulling during target-filtered fetch calls so targets are respected when collecting used cache.
      - Pass a `--pull` argument through `repro` command options down to stage restoration to pull missing run-cache stage outputs on demand.
      - Verify the command argument defaults and parsing by running repro unit tests.

=== TASK spectacles-ci__spectacles-179 | repo spectacles-ci/spectacles | python
--- Issue
Failing SQL is not saved to file
After some architectural changes, failing SQL queries are no longer being logged to file. We need to reintroduce this feature. It should probably live in `cli.py`, using the value of the `sql` key in the dictionary returned by the runner.

Previously, in single-dimension mode, spectacles would create a folder per explore with a `.sql` file for each dimension query. In batch mode, it would create a `.sql` file per explore in the base directory.

These files were all created in the `logs` directory.
--- Real fix (for your judgement only)
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
-   
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Move SQL query details to error metadata in database errors
      Move SQL in error object to metadata
Since the `test` field of the `ValidationError` is only used by `SqlError`, move it to the error's `metadata` and remove the `test` attribute.
      Way: move sql error details into metadata dictionary
      - Locate where error objects are printed or formatted in CLI output routines.
      - Update error accessing logic to retrieve SQL text from `error['metadata']` (e.g., `error['metadata']['sql']`) instead of `error['test']`.
      - Run test suite to ensure error formatting and output work with the metadata dictionary structure.
  [2] Goal: Recommend single-dimension and hybrid modes when validation errors occur
      Recommend re-running in hybrid/single mode when errors found in batch mode
When spectacles is run in batch mode and encounters an error, we should suggest the user re-run the explore (maybe even pre-populating the command with the explore argument in the output) in single-dimension mode to zero in o
      Way: Recommend alternative validation modes on batch error
      - Identify the code path responsible for reporting validation results and errors in batch mode. The recommendation message should be displayed after individual error details but before the final error exit.
      - Implement properties on `Explore`, `Model`, and `Project` classes to recursively count the total number of validation errors. This count is needed to dynamically phrase the recommendation message (e.g., 'this error' vs. 'these errors').
      - Add a conditional log message in the `run_sql` function in `spectacles/cli.py`. This message should only appear when the validation `mode` is 'batch' and errors have occurred. The message should recommend re-running in single-dimension mode (`--mode single`) or hybrid mode (`--mode hybrid`), using the newly implemented error count to correctly pluralize 'error'.
      - Run the provided tests to ensure the error counting logic and the overall behavior are correct.
  [3] Goal: Log API error responses at debug level in the exception handler
      Log response from Looker in handle_exceptions
We should catch and handle `LookerApiError` specifically [here](https://github.com/spectacles-ci/spectacles/blob/master/spectacles/cli.py#L148).

The exception object has a `response` attribute which is the raw response from Looker. We can log (at a de
      Way: log specialized api exceptions with formatted debug details in exception handler
      - Locate the CLI top-level exception handler decorator in the codebase.
      - Add a specific exception block for API exceptions prior to general exception handling, extraction of error response payload formatted as JSON, logging standard error with help text, and debug logging response status and body details before exiting.
      - Run CLI exception handler tests to ensure API exceptions output response details at debug level and exit with correct status code.

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

--- Real fix (for your judgement only)
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
 

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Provide option to force check-merge-conflict hook execution
      Option to force run check-merge-conflict
check-merge-conflict only runs when it detects merge/rebase in progress.
This means it will always pass on CI setups.

Am not sure if this should be fixed specifically for check-merge-conflict or make a generic CI functionality available via [pre-commit](h
      Way: Add option to force execution of a conditional hook
      - Identify the hook's conditional execution logic that prevents it from running its main checks under certain circumstances (e.g., not being in a merge state).
      - Add a new boolean command-line argument to the hook's argument parser, allowing users to explicitly request the hook to assume the condition is met.
      - Modify the conditional execution logic to bypass the original condition if the new 'assume' argument is provided.
      - Verify that the hook now executes its main logic when the new 'assume' argument is passed, even if the original condition is not met.
  [2] Goal: Handle detached HEAD state gracefully in branch protection hook
      no-commit-to-branch on CI
As a safety net, we've started running pre-commit on CI, e.g.
```
git diff --name-only "$GIT_COMMIT"^..."$GIT_COMMIT" | xargs pre-commit run --files
```

one of our projects uses `no-commit-to-branch` and we just noticed this behavior on CI:
```
Don't commit to branc
      Way: handle detached HEAD in git branch checking hook
      - Locate the branch validation logic that retrieves the current branch using git symbolic-ref HEAD.
      - Reproduce the failure by running the hook or test on a detached HEAD state where git symbolic-ref returns non-zero status.
      - Catch CalledProcessError when querying symbolic-ref and return False to indicate the repository is not on a protected branch when in detached HEAD state.
      - Run test suite to verify detached HEAD state is handled without raising an uncaught exception.
  [3] Goal: Fix executable file shebang validation on Windows systems
      check-executables-have-shebangs not correct on windows
does not seem to work right on windows

i am dealing with a primarily linux / python project but on a windows machine.

i have set `git config core.filemode false`

i created a new file and stage and i verify that filemode is 644:
```
>g
      Way: Check executable bit via Git stage metadata on Windows for shebang validation
      - Locate the shebang validation logic in the hook implementation file.
      - Identify why the hook fails on Windows: file system execution flags do not mirror Unix permissions, causing files to be treated as executable based on filesystem metadata rather than Git filemode.
      - Branch execution logic when `sys.platform == 'win32'` to query Git index stage info (`git ls-files --stage -- <paths>`) instead of checking local filesystem executable permission.
      - Parse `git ls-files --stage` metadata output to check if the last three octal digits of the mode indicate executable permissions (ending in 1, 3, 5, or 7), reporting shebang missing errors only for actual Git-tracked executables.
      - Run test suite to verify Windows git filemode check handling.

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

--- Real fix (for your judgement only)
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
                 n
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Support Marshmallow schemas containing a field named fields
      Marshmallow 3.12 schema with `fields` property produce error
Marshmallow added Fields be accessed by name as Schema attributes 

I have a schema like this 

```python
import marshmallow as ma

class Schema(ma.Schema):
    fields = ma.fields.List(ma.fields.Int)
```

Produces error 

```
      Way: Support Marshmallow schemas containing a field named fields
      - Locate the helper function that extracts fields from a Marshmallow schema or schema class.
      - Change the schema type checking from attribute-based checks (`hasattr(schema, 'fields')`) to explicit type and subclass checks (`isinstance(schema, marshmallow.Schema)` and `issubclass(schema, marshmallow.Schema)`). This prevents a schema field named 'fields' from overriding the schema's own `fields` dictionary attribute on the instance.
      - Update the error message raised when the schema is neither an instance nor a subclass of `marshmallow.Schema`.
      - Run the test suite to verify that schemas with a field named 'fields' are processed correctly and invalid schemas raise the expected errors.
  [2] Goal: Prevent schemas referenced in docstrings from being incorrectly generated as arrays
      Docstring-only schemas incorrectly referenced as arrays?
Tested with Marshmallow 3.0.0rc3 and APISpec 1.0.0rc1.

Took a copy of the APISpec quickstart code and removed the defintions, so that schemas were only being referenced from the docstrings.  I'm seeing that the nested schema is being picked
      Way: prevent nested schemas with many=True from being registered as arrays
      - Locate where marshmallow schemas are converted to JSON schemas, specifically looking for where the `many` attribute of a schema is handled.
      - Modify the schema conversion logic to avoid wrapping the base schema definition in an array type when `many=True` is set on the schema instance itself. Instead, apply the array wrapping logic only when resolving the schema reference or generating the inline schema representation for a field/reference.
      - Run the test suite to verify that nested schemas with `many=True` are correctly registered as object schemas, while their references or inline usages are correctly wrapped as arrays.
  [3] Goal: Generate compliant OpenAPI 3.0 schemas for nullable nested marshmallow fields
      Incorrect handling of marshmallow nested schema with allow_none=True in OpenAPI 3.0
This is a spinoff of #833, but for OpenAPI 3.0.

The following schemas:

```
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from marshmallow import Schema, fields

spec = A
      Way: generate compliant OpenAPI 3.0 schemas for nullable nested fields
      - Locate the field converter logic where marshmallow fields are mapped to OpenAPI schema properties, specifically handling nullable attributes and references.
      - Modify the OpenAPI 3.0.x (minor version < 1) nullable handling for fields containing a '$ref'. Instead of setting 'nullable: true' alongside 'allOf' containing the '$ref', wrap the reference in an 'anyOf' array containing both a null-typed object schema with 'nullable: true' and the original '$ref' schema.
      - Run the test suite to verify that nested nullable fields generate compliant OpenAPI 3.0 schemas using 'anyOf' instead of 'allOf' with 'nullable'.

=== TASK pypa__auditwheel-287 | repo pypa/auditwheel | python
--- Issue
ncurses 5 -> 6 transition means we probably need to drop some libraries from the manylinux whitelist
The [PEP 513 whitelist](https://www.python.org/dev/peps/pep-0513/#the-manylinux1-policy) includes `libpanelw.so.5` and `libncursesw.so.5`, both of which are part of ncurses version 5.

But apparently the new hostness is [ncurses 6, which breaks ABI with ncurses 5](https://www.gnu.org/software/ncurses/). I'm told that Fedora 24 and successors have already switched to ncurses 6, with the ncurses 5 libraries being available in a compatibility package, but not installed by default. So... I guess these have to be dropped from the whitelist.
--- Real fix (for your judgement only)
diff --git a/auditwheel/policy/__init__.py b/auditwheel/policy/__init__.py
index 22879c0..a9d59d0 100644
--- a/auditwheel/policy/__init__.py
+++ b/auditwheel/policy/__init__.py
@@ -1,7 +1,8 @@
 import sys
 import json
 import platform as _platform_module
-from typing import Optional, List
+from collections import defaultdict
+from typing import Dict, List, Optional, Set
 from os.path import join, dirname, abspath
 import logging
 
@@ -23,9 +24,42 @@ def get_arch_name() -> str:
 _ARCH_NAME = get_arch_name()
 
 
+def _validate_pep600_compliance(policies) -> None:
+    symbol_versions: Dict[str, Dict[str, Set[str]]] = {}
+    lib_whitelist: Set[str] = set()
+    for policy in sorted(policies, key=lambda x: x['priority'], reverse=True):
+        if policy['name'] == 'linux':
+            continue
+        if not lib_whitelist.issubset(set(policy['lib_whitelist'])):
+            diff = lib_whitelist - set(policy["lib_whitelist"])
+            raise ValueError(
+                'Invalid "policy.json" file. Missing whitelist libraries in '
+                f'"{policy["name"]}" compared to previous policies: {diff}'
+            )
+        lib_whitelist.update(policy['lib_whitelist'])
+        for arch in policy['symbol_versions'].keys():
+            symbol_versions_arch = symbol_versions.get(arch, defaultdict(set))
+            for prefix in policy['symbol_versions'][arch].keys():
+                policy_symbol_versions = set(
+                    policy['symbol_versions'][arch][prefix])
+                if not symbol_versions_arch[prefix].issubset(
+                        policy_symbol_versions):
+                    diff = symbol_versions_arch[prefix] - \
+                           policy_symbol_versions
+                    raise ValueError(
+                        'Invalid "policy.json" file. Symbol versions missing '
+                        f'in "{policy["name"]}_{arch}" for "{prefix}" '
+                        f'compared to previous policies: {diff}'
+         
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Add manylinux_2_27 policy support for wheel auditing
      Add manylinux_2_27 policy
Hey team!

We at @robotpy build wheels on Ubuntu 18.04 (as our upstream targets 18.04). We've been manually tagging our wheels as `manylinux_2_27` as we wanted to get our wheels on PyPI quickly early this year. It'd be awesome if we could use auditwheel instead to check t
      Way: add new manylinux policy definition to policy configuration json
      - Locate policy definition files containing platform ABI requirements, symbol versions, and library whitelists.
      - Add a new manylinux policy entry with appropriate priority, architecture symbol versions for GLIBC and GCC/C++ ABIs, and whitelisted libraries.
      - Run policy unit tests to verify policies load correctly and can be retrieved by name and priority.
  [2] Goal: Exclude Python minor version dynamic libraries from repaired wheel dependencies
      libpython3.x is skipped but libpython3.xx is included
howdy,

i noticed when running auditwheel repair on a wheel with dependency on libpython3.8.so or libpython3.9.so, they are not added to the wheel libs directory, but libpython3.10.so and libpython3.11.so are added. I'm not very familiar with t
      Way: Update libpython regex pattern to match multi-digit version numbers
      - Locate the regular expression pattern defined for matching libpython shared libraries in external reference filtering module.
      - Reproduce the issue by running unit tests for external reference library filtering with dual-digit minor version library names such as libpython3.10.so.
      - Update the libpython regular expression pattern to use quantifier '\d+' instead of single digit '\d' for major and minor Python version numbers.
      - Run policy unit tests to verify double-digit Python minor version shared libraries are correctly matched and excluded from external dependencies.
  [3] Goal: Compress output wheel files when repairing Python wheels
      auditwheel repair: whls not getting compressed for auditwheel 5.1.0 and above?
After upgrading to 5.1.0, we noticed that our wheels are not getting compressed, resulting in very large whls after running `auditwheel repair foo.whl`:

```
-rw-r--r-- 1 root root 104093514 Jan  7 01:54 accera-1.2.1.d
      Way: Compress zip entries when writing custom ZipInfo objects with ZipFile writestr
      - Locate the archive creation utility function that packs output wheel directory contents into a zip archive.
      - Inspect how ZipInfo and ZipFile.writestr are used when writing entries to the archive.
      - Update file packaging logic to use ZipInfo.from_file and set compress_type explicitly on each ZipInfo object before calling ZipFile.writestr.
      - Run the unit test suite to verify zip entries are correctly compressed using DEFLATE.

=== TASK duartegroup__autodE-242 | repo duartegroup/autodE | python
--- Issue
Division and multiplication of `autode.values.Value` creates wrong units
**Describe the bug**
<!-- A clear and concise description of what the bug is. -->

Multiplication and division do not generate autode.values with the correct units. As a simple fix multiplication could return a float, given it's (potentially) an uncommon occurrence.

**To Reproduce**
<!-- Steps to reproduce the behaviour: code with output -->


```python
>>> import autode as ade
>>> e = ade.values.PotentialEnergy(1)
>>> e
Energy(1.0 Ha)
>>> e * e
Energy(1.0 Ha)
>>> e / e
Energy(1.0 Ha)
>>> # Addition and subtraction are okay
>>> e + e
Energy(2.0 Ha)
>>> e - e
Energy(0.0 Ha)
```

**Expected behavior**
<!-- A clear and concise description of what you expected to happen. -->

Division results in no units and multiplication Ha^2

**Environment**
<!-- please complete the following information. -->
 - autodE version: 1.3.4

--- Real fix (for your judgement only)
diff --git a/autode/values.py b/autode/values.py
index dcf4c09..295444d 100644
--- a/autode/values.py
+++ b/autode/values.py
@@ -62,7 +62,7 @@ def _to(value: Union["Value", "ValueArray"], units: Union[Unit, str]):
 
     except StopIteration:
         raise TypeError(
-            f"No viable unit conversion from {value.units} " f"-> {units}"
+            f"No viable unit conversion from {value.units} -> {units}"
         )
 
     # Convert to the base unit, then to the new units
@@ -214,16 +214,22 @@ class Value(ABC, float):
             float(self) + self._other_same_units(other), units=self.units
         )
 
-    def __mul__(self, other) -> "Value":
+    def __mul__(self, other) -> Union[float, "Value"]:
         """Multiply this value with another"""
         if isinstance(other, np.ndarray):
             return other * float(self)
 
+        if isinstance(other, Value):
+            logger.warning(
+                "Multiplying autode.Value returns a float with no units"
+            )
+            return float(self) * self._other_same_units(other)
+
         return self.__class__(
             float(self) * self._other_same_units(other), units=self.units
         )
 
-    def __rmul__(self, other) -> "Value":
+    def __rmul__(self, other) -> Union[float, "Value"]:
         return self.__mul__(other)
 
     def __radd__(self, other) -> "Value":
@@ -232,16 +238,19 @@ class Value(ABC, float):
     def __sub__(self, other) -> "Value":
         return self.__add__(-other)
 
-    def __floordiv__(self, other):
-        raise NotImplementedError(
-            "Integer division is not supported by " "autode.values.Value"
-        )
+    def __floordiv__(self, other) -> Union[float, "Value"]:
+        x = float(self) // self._other_same_units(other)
+        if isinstance(other, Value):
+            return x
 
-    def __truediv__(self, other) -> "Value":
-        return self.__class__(
-            float(self) / float(self._other_same_units(other)),
-            uni
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent unexpected unit conversion during attribute inspection on Hessian objects
      Using autodE in Interactive python shell causes unusual change in units
Only seen from interactive python shell.

```python
>>> import autode as ade
>>> mol = ade.Molecule(smiles='CCO')
>>> mol.calc_hessian(method=ade.methods.XTB(),n_cores=6)
>>> mol.hessian.units
Unit(Ha Å^-2)
>>> mol.hessi
      Way: prevent unintended in-place mutation during array unit conversion and inspection
      - Identify properties or methods on the array subclass (such as normal mode or mass-weighted Hessian calculation properties) that invoke unit conversion or mutate internal array data during evaluation.
      - Update the unit conversion helper function to default to returning an out-of-place copy rather than mutating in-place, adding an explicit `inplace` parameter and separate in-place method (e.g. `to_`) if needed.
      - Ensure arithmetic operations on custom float/array wrapper classes preserve custom attributes and do not inadvertently mutate operands.
      - Ensure internal mass-weighting and normal mode property calculations operate on standard ndarrays or non-mutated arrays without modifying the instance's units.
      - Run the value and array unit conversion tests to verify in-place restrictions, attribute preservation, and non-destructive inspection.
  [2] Goal: Rename coordinate distance primitive and use distance values for optimizer trust radius
      Rename autode.opt.coordinates.primitive.Distance and use values for optimisers
1. There is a clash between `autode.opt.coordinates.primitives.Distance` and `autode.values.Distance`. Maybe the first one could be renamed to BondDistance?
2. Use `autode.value.Distance` for trust radius in the optimise
      Way: Rename coordinate primitives and use value objects for trust radius
      - Identify all classes in `autode.opt.coordinates.primitives` and `autode.opt.coordinates.internals` that represent geometric primitives (like Distance, InverseDistance, BondAngle, DihedralAngle) and rename them by prepending 'Primitive' to their names to avoid clashes with `autode.values` classes.
      - Update all references and instantiations of the renamed primitive classes across the codebase to use their new 'Primitive' prefixed names.
      - Modify the `CartesianCoordinates` constructor to automatically handle `ValueArray` inputs by converting them to the specified units, removing the need for explicit unit conversions (`.to('ang')`) before passing them to the constructor.
      - Update code that creates `CartesianCoordinates` instances, specifically in `autode/bracket/imagepair.py`, to remove redundant explicit unit conversions (`.to('ang')`) now that the `CartesianCoordinates` constructor handles them.
      - Modify the `CRFOptimiser` class to accept `init_alpha` (the trust radius) as an `autode.values.Distance` object, ensuring it is stored and used as a dimensioned quantity, and add an assertion that it is positive.
      - Ensure that `OptimiserHistory` correctly handles coordinate types when setting species coordinates by explicitly converting them to 'cart' units if necessary.
  [3] Goal: Raise a useful exception when template folder path is empty
      Throw useful exception if `ade.Config.ts_template_folder_path = ""`
      Way: validating configuration path string before resolving default template folders
      - Search the codebase for accesses or resolution of the configuration parameter controlling the template folder path (e.g., `Config.ts_template_folder_path`).
      - Identify the function resolving the template folder path (e.g., `get_ts_template_folder_path`) when an explicit path argument is not provided.
      - In the path resolution function, check if the configuration folder path is set to an empty string (`""`), and raise a `ValueError` explaining that the path cannot be an empty string prior to checking if it is not None.
      - Run the test suite verifying that an empty configuration string raises the expected ValueError.

=== TASK snowflakedb__snowflake-connector-python-1856 | repo snowflakedb/snowflake-connector-python | python
--- Issue
SNOW-1013234: Fix config permissions warning message 
### What is the current behavior?

If permissions / owner of config file is incorrect, the command outputs long one-liner containing two commands (`chmod` and `chown`) to call to fix the issue.
1. Many users tend to overlook that there are two commands and give up after one does not fix the issue
2. In Snowflake CLI on MacOS default config path is inside "Application Support" directory - space inside the path turned out to be problematic for some users

```
/Users/pczajka/Snowflake/venv3.12/lib/python3.12/site-packages/snowflake/connector/config_manager.py:344: UserWarning: Bad owner or permissions on /Users/pczajka/Library/Application Support/snowflake/config.toml. To change owner, run `chown $USER /Users/pczajka/Library/Application Support/snowflake/config.toml`. To restrict permissions, run `chmod 0600 /Users/pczajka/Library/Application Support/snowflake/config.toml`.
  warn(f"Bad owner or permissions on {str(filep)}{chmod_message}")
  ```

### What is the desired behavior?

Reformattinmg the message to highlight that there are two commands to call (fix 1) and add quotes around filenames (fix 2).
For example:
```
/Users/pczajka/Snowflake/venv3.12/lib/python3.12/site-packages/snowflake/connector/config_manager.py:344: UserWarning: Bad owner or permissions on /Users/pczajka/Library/Application Support/snowflake/config.toml.
 * To change owner, run `chown $USER "/Users/pczajka/Library/Application Support/snowfla
--- Real fix (for your judgement only)
diff --git a/DESCRIPTION.md b/DESCRIPTION.md
index a13a3feb..1aa2982a 100644
--- a/DESCRIPTION.md
+++ b/DESCRIPTION.md
@@ -18,6 +18,7 @@ Source code is also available at: https://github.com/snowflakedb/snowflake-conne
   - Added support for connecting using an existing connection via the session and master token.
   - Added support for connecting to Snowflake by authenticating with multiple SAML IDP using external browser.
   - Fixed compilation issue due to missing cstdint header on gcc13.
+  - Improved config permissions warning message.
 
 - v3.6.0(December 09,2023)
 
diff --git a/src/snowflake/connector/config_manager.py b/src/snowflake/connector/config_manager.py
index d9ecf80a..e981a40c 100644
--- a/src/snowflake/connector/config_manager.py
+++ b/src/snowflake/connector/config_manager.py
@@ -336,7 +336,7 @@ class ConfigManager:
             ):
                 # for non-Windows, suggest change to 0600 permissions.
                 chmod_message = (
-                    f". To change owner, run `chown $USER {str(filep)}`. To restrict permissions, run `chmod 0600 {str(filep)}`."
+                    f'.\n * To change owner, run `chown $USER "{str(filep)}"`.\n * To restrict permissions, run `chmod 0600 "{str(filep)}"`.\n'
                     if not IS_WINDOWS
                     else ""
                 )

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Fix client MFA token caching and temporary credential storage
      SNOW-721779: Client MFA caching not working in 2.9.0
Please answer these questions before submitting your issue. Thanks!

1. What version of Python are you using?

```
Python 3.9.16 (main, Dec 21 2022, 09:20:09) 
[GCC 8.3.0]
```
2. What operating system and processor architecture are you usi
      Way: load cached MFA credentials before initializing MFA authentication class
      - Locate the connection initialization logic where authenticator types and authentication classes are assigned.
      - Check whether cached temporary credentials (MFA tokens) are retrieved from credential storage when user-password MFA authentication is selected.
      - Update the connection setup for MFA authentication to explicitly call the temporary credential reader before instantiating the MFA auth handler and pass the read MFA token.
      - Run the MFA auth unit tests to verify the token cache is populated and passed correctly.
  [2] Goal: Prevent segmentation fault during pickle dump in multi-threaded caching
      SNOW-856569: Segmentation Fault in cache.py pickle dump
### Python version

Python 3.8.12 (default, Nov 17 2021, 08:36:07)  [Clang 7.1.0 (tags/RELEASE_710/final)]

### Operating system and processor architecture

Linux _x86_64_

### Installed packages

```python
dbt-core==1.5.2
dbt-snowf
      Way: thread-safe file cache pickle operations under mutex lock
      - Locate cache class file and identify unsafe non-thread-safe dictionary access during serialization/deserialization or lookup in multi-threaded file cache.
      - Reproduce thread safety or segmentation fault issues when multiple threads access and serialize cache entries simultaneously.
      - Refactor dictionary cache methods to enforce thread lock acquisition during load, lookup, and pickle serialization operations, ensuring locks are held while accessing shared cache state.
      - Run unit tests for file cache to verify lock acquisition and serialization stability.
  [3] Goal: Send partner environment variable value as application name in connection requests
      SNOW-576084: Setting environment variable SF_PARTNER doesn't send overridden `application` value in requests
Please answer these questions before submitting your issue. Thanks!

1. What version of Python are you using?

```
Python 3.9.11 (main, Apr  5 2022, 09:45:25) 
[Clang 13.1.6 (clang-1316
      Way: override connection application keyword argument from partner environment variable
      - Locate connection initialization logic where keyword arguments and environment variables are processed.
      - Reproduce the issue by running the partner environment variable test.
      - Move the partner environment variable check before internal connect call so that kwargs['application'] is updated prior to establishing connection requests rather than mutating private properties after connect.
      - Run connection unit tests to confirm application parameter propagates correctly during request payload construction.

=== TASK iterative__dvc-4642 | repo iterative/dvc | python
--- Issue
BUG: JSON parsing error for `dvc metrics show`
## Bug Report

I have a simple JSON of test metrics, in file `scores.json`:
```json
{
    "test_1": {
        "precision": 0,
        "f1": 0.0
    },
    "test_2": {
        "fpr": 0.0
    },
    "notes": "if precision is zero, there were zero positive predictions; if f1 is zero, something went very wrong"
}
```

I expect the key "notes" to fail, but the numeric keys should show up. However, they don't:
```console
$ dvc metrics show -v scores.json
2020-09-28 17:16:09,022 DEBUG: Check for update is enabled.
2020-09-28 17:16:09,024 DEBUG: fetched: [(3,)]                        
2020-09-28 17:16:09,029 DEBUG: Could not parse 'precision' metric from 'scores.json' at '' due to its unsupported type: 'int'
2020-09-28 17:16:09,029 DEBUG: Could not parse 'f1' metric from 'scores.json' at '' due to its unsupported type: 'float'
2020-09-28 17:16:09,029 DEBUG: Could not parse 'test_1' metric from 'scores.json' at '' due to its unsupported type: 'dict'
2020-09-28 17:16:09,029 DEBUG: Could not parse 'fpr' metric from 'scores.json' at '' due to its unsupported type: 'float'
2020-09-28 17:16:09,029 DEBUG: Could not parse 'test_2' metric from 'scores.json' at '' due to its unsupported type: 'dict'
2020-09-28 17:16:09,029 DEBUG: Could not parse 'notes' metric from 'scores.json' at '' due to its unsupported type: 'str'
2020-09-28 17:16:09,030 DEBUG: fetched: [(11243,)]
2020-09-28 17:16:09,031 ERROR: failed to show metri
--- Real fix (for your judgement only)
diff --git a/dvc/command/metrics.py b/dvc/command/metrics.py
--- a/dvc/command/metrics.py
+++ b/dvc/command/metrics.py
@@ -11,7 +11,7 @@
 DEFAULT_PRECISION = 5
 
 
-def show_metrics(
+def _show_metrics(
     metrics, all_branches=False, all_tags=False, all_commits=False
 ):
     from dvc.utils.diff import format_dict
@@ -21,22 +21,25 @@ def show_metrics(
     # specified as `targets` in `repo.metrics.show` didn't contain any metrics.
     missing = metrics.pop(None, None)
 
+    lines = []
     for branch, val in metrics.items():
         if all_branches or all_tags or all_commits:
-            logger.info(f"{branch}:")
+            lines.append(f"{branch}:")
 
         for fname, metric in val.items():
             if not isinstance(metric, dict):
-                logger.info("\t{}: {}".format(fname, str(metric)))
+                lines.append("\t{}: {}".format(fname, str(metric)))
                 continue
 
-            logger.info(f"\t{fname}:")
+            lines.append(f"\t{fname}:")
             for key, value in flatten(format_dict(metric)).items():
-                logger.info(f"\t\t{key}: {value}")
+                lines.append(f"\t\t{key}: {value}")
 
     if missing:
         raise BadMetricError(missing)
 
+    return "\n".join(lines)
+
 
 class CmdMetricsBase(CmdBase):
     UNINITIALIZED = True
@@ -58,12 +61,14 @@ def run(self):
 
                 logger.info(json.dumps(metrics))
             else:
-                show_metrics(
+                table = _show_metrics(
                     metrics,
                     self.args.all_branches,
                     self.args.all_tags,
                     self.args.all_commits,
                 )
+                if table:
+                    logger.info(table)
         except DvcException:
             logger.exception("failed to show metrics")
             return 1
diff --git a/dvc/command/repro.py b/dvc/command/repro.py
--- a/dvc/command/repro.py
+++ b/dvc/command/repro.py
@@ -4,7 +4,7 @@
 
 from dvc.
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent crash when showing metrics for Git tags without DVC directory
      dvc metrics -T raises exception if there are tags unrelated to the DVC sub-directory
DVC version: 0.94.0
Python version: 3.6.7
Platform: Windows-10-10.0.18362-SP0
Binary: False
Package: pip
Supported remotes: http, https
Filesystem type (workspace): ('NTFS', 'C:\\')

```
NT> dvc metrics sho
      Way: prevent crash when showing metrics for Git tags without DVC directory
      - Locate where the repository branches/tags are iterated over during metrics collection, and where the repository tree is constructed.
      - Modify the brancher utility to skip revisions (such as tags) that do not contain the DVC repository root directory.
      - Update the CleanTree wrapper to accept an optional custom tree root, and use it to correctly resolve paths when the tree root differs from the underlying tree's root.
      - Update the Repo tree setter to pass the repository root directory to CleanTree if the tree is not a working tree and its root does not match the repository root.
      - Run the metrics show tests to verify that preexisting tags without the DVC subdirectory are ignored without raising a FileNotFoundError.
  [2] Goal: Allow comparing metrics when the old version does not exist
      metrics diff with missing old value
If metrics file(s) has only no old version (it was just created):
```
$ dvc metrics diff HEAD^
WARNING: Metrics file 'metrics/eval.json' does not exist at the reference 'HEAD^'.
WARNING: Metrics file 'metrics/train.json' does not exist at the reference 'HEAD^'
      Way: return empty string for empty metrics diff formatting
      - Locate the metrics diff CLI output formatting logic in the metrics command module.
      - Modify the diff table formatter function `_show_diff` so that if the diff payload is empty, it returns an empty string (`""`) instead of the hardcoded string `"No changes."`.
      - Run unit tests for metrics diff command formatting to ensure an empty diff outputs nothing to stdout.
  [3] Goal: Format float values in metrics diff output with configurable precision
      metrics diff precision
Currently, DVC just uses regular float rounding without any adjustments. Sometimes it does not look good:
```
$ dvc metrics diff
Path          Metric          Value                 Change
summary.json  train.accuracy  0.9886999726295471    0.00001300000009
summary.json  t
      Way: Format float values in metrics diff output with configurable precision
      - Locate the command-line interface parser and execution logic for the metrics diff command.
      - Add a `--precision` command-line argument to the metrics diff parser that accepts an integer and defaults to a predefined precision constant.
      - Update the diff rendering function to accept a precision parameter and round float values (old, new, and diff) to the specified number of decimal places.
      - Pass the parsed precision argument from the command execution context to the diff rendering function.
      - Verify the changes by running the metrics diff unit and functional tests.

=== TASK python-trio__trio-1129 | repo python-trio/trio | python
--- Issue
Should we somehow expose Pipe*Stream on Unix, and if so, how?
We have `PipeReceiveStream` and `PipeSendStream` classes on Unix, that are currently not exposed publicly; they're only used internally for subprocesses.

The actual code is a pretty generic building block: they can wrap around any Unix file-descriptor that supports non-blocking mode. So they could potentially be useful for talking to [FIFOs](http://man7.org/linux/man-pages/man7/fifo.7.html), TTYs, stdio streams (#174), etc.

Should we expose them more directly? If so, how? In `trio.hazmat`, with some sort of [`os.fdopen`](https://docs.python.org/3/library/os.html#os.fdopen) equivalent?

If we do we'll want to think through the ownership semantics: should they take ownership of a passed in fd, or should they dup it?

Also, they should restore the fd's blocking/non-blocking state before closing it.
--- Real fix (for your judgement only)
diff --git a/docs-requirements.txt b/docs-requirements.txt
index 02ecb74a..d1466815 100644
--- a/docs-requirements.txt
+++ b/docs-requirements.txt
@@ -11,7 +11,7 @@ babel==2.7.0              # via sphinx
 certifi==2019.6.16        # via requests
 chardet==3.0.4            # via requests
 click==7.0                # via towncrier
-docutils==0.14            # via sphinx
+docutils==0.15            # via sphinx
 idna==2.8
 imagesize==1.1.0          # via sphinx
 immutables==0.9
@@ -21,7 +21,7 @@ markupsafe==1.1.1         # via jinja2
 outcome==1.0.0
 packaging==19.0           # via sphinx
 pygments==2.4.2           # via sphinx
-pyparsing==2.4.0          # via packaging
+pyparsing==2.4.1          # via packaging
 pytz==2019.1              # via babel
 requests==2.22.0          # via sphinx
 six==1.12.0               # via packaging
diff --git a/docs/source/reference-hazmat.rst b/docs/source/reference-hazmat.rst
index 6cad8476..e12d3601 100644
--- a/docs/source/reference-hazmat.rst
+++ b/docs/source/reference-hazmat.rst
@@ -174,6 +174,26 @@ All environments provide the following functions:
    yourself afterwards.
 
 
+Unix-specific API
+-----------------
+
+`FdStream` supports wrapping Unix files (such as a pipe or TTY) as
+a stream.
+
+If you have two different file descriptors for sending and receiving,
+and want to bundle them together into a single bidirectional
+`~trio.abc.Stream`, then use `trio.StapledStream`::
+
+    bidirectional_stream = trio.StapledStream(
+        trio.hazmat.FdStream(write_fd),
+        trio.hazmat.FdStream(read_fd)
+    )
+
+.. autoclass:: FdStream
+   :show-inheritance:
+   :members:
+
+
 Kqueue-specific API
 -------------------
 
diff --git a/newsfragments/829.feature.rst b/newsfragments/829.feature.rst
new file mode 100644
index 00000000..34090857
--- /dev/null
+++ b/newsfragments/829.feature.rst
@@ -0,0 +1,1 @@
+Add `trio.hazmat.FdStream` for wrapping a Unix file descriptor as a `~trio.abc.Stream`.
diff --git a/trio/_subprocess.py b/tr
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Enable non-blocking socket readability checks for all socket types
      Add a method to trio socket objects that checks whether the OS thinks the socket is readable
Say you're writing an HTTP/1.1 client library, that supports connection pooling. When you take an idle connection out of your pool, it might be fine to use... OR, the server might have gotten tired of waitin
      Way: Add non-blocking socket readability check method
      - Identify the socket wrapper class and locate where synchronous socket utility methods are implemented.
      - Implement an `is_readable()` method on the socket wrapper class using a timeout of 0 with `select.select()` on Windows (`win32`) and `select.poll()` with `select.POLLIN` on other platforms.
      - Document the new `is_readable` method in the public socket API documentation and release notes.
      - Write and run unit tests that verify `is_readable()` returns False on newly created connected sockets and True once data or EOF is available.
  [2] Goal: Detect or prevent deadlock when a task holding a lock finishes without releasing it
      Problems with `trio.Lock()` across multiple tasks
Some time ago - never mind how long precisely - having a little coordination between tasks and nothing particular to interest me in semaphores, I saw our `RuntimeError("can't release a Lock you don't own")`.

I approve pretty strongly of keeping th
      Way: break-parking-lots-on-task-exit
      - Identify where synchronization primitives manage waiting tasks and where task lifecycles are tracked.
      - Add a mechanism to break parking lots, marking them as broken and rescheduling parked tasks with an exception when broken.
      - Register task-to-parking-lot ownership links when locks are acquired, and remove them when locks are released.
      - Hook into task exit handling to automatically trigger breaking registered parking lots when a task holding a lock terminates.
      - Run the test suite to verify parking lot breaker mechanics and lock behavior on task termination.
  [3] Goal: Immediately cancel `move_on_after(0)` and `fail_after(0)` scopes
      Maybe move_on_after(0)/fail_after(0) should immediately cancel their scope
It's tempting to think that you can fake a "check once, but don't block" semantics by setting up a 0-timeout cancel scope:

```python
async def fake_accept_nowait(self):
    with fail_after(0):
        await self.accept(
      Way: Ensure immediate cancellation for already-expired cancel scopes
      - Understand that `move_on_after(0)` or `fail_after(0)` scopes do not immediately cancel due to the timing of timeout processing in the event loop. This can lead to non-blocking operations succeeding before cancellation is applied.
      - Locate the `CancelScope` class definition in `trio/_core/_run.py`, as this is where the core logic for managing cancellation scopes resides.
      - Modify the `CancelScope.__enter__` method to check if the scope's deadline is already in the past at the moment of entry. If `current_time()` is greater than or equal to `self._deadline`, immediately set `self.cancel_called = True` to mark the scope as cancelled.
      - Verify that the fix correctly implements immediate cancellation for zero-timeout scopes and does not introduce regressions.

=== TASK aio-libs__aioftp-110 | repo aio-libs/aioftp | python
--- Issue
Leap Year Issue
Hi,

```
import asyncio
import aioftp

async def get_files_path(host, port, login, password):
    async with aioftp.ClientSession(host, port, login, password) as client:
        for path, info in (await client.list(PATH)):
            print(path)

asyncio.run(get_files_path(HOST, port, USERNAME, PASSWORD))
```

It seems that aioftp fails to list up the files created after 02/29/2020 00:00.
When I used Python ftplib, I could get the correct number of files totally the same with I was checking on FileZilla.

Can you check this issue? Thanks.
--- Real fix (for your judgement only)
diff --git a/aioftp/client.py b/aioftp/client.py
index bcb854f..a1072b6 100644
--- a/aioftp/client.py
+++ b/aioftp/client.py
@@ -1,4 +1,5 @@
 import re
+import calendar
 import collections
 import pathlib
 import logging
@@ -26,6 +27,7 @@ from .common import (
     async_enterable,
     setlocale,
     HALF_OF_YEAR_IN_SECONDS,
+    TWO_YEARS_IN_SECONDS,
 )
 
 __all__ = (
@@ -354,7 +356,8 @@ class BaseClient:
         """
         return d.strftime("%Y%m%d%H%M00")
 
-    def parse_ls_date(self, s, *, now=None):
+    @classmethod
+    def parse_ls_date(cls, s, *, now=None):
         """
         Parsing dates from the ls unix utility. For example,
         "Nov 18  1958" and "Nov 18 12:29".
@@ -368,16 +371,29 @@ class BaseClient:
             try:
                 if now is None:
                     now = datetime.datetime.now()
-                d = datetime.datetime.strptime(s, "%b %d %H:%M")
-                d = d.replace(year=now.year)
-                diff = (now - d).total_seconds()
-                if diff > HALF_OF_YEAR_IN_SECONDS:
-                    d = d.replace(year=now.year + 1)
-                elif diff < -HALF_OF_YEAR_IN_SECONDS:
-                    d = d.replace(year=now.year - 1)
+                if s.startswith('Feb 29'):
+                    # Need to find the nearest previous leap year
+                    prev_leap_year = now.year
+                    while not calendar.isleap(prev_leap_year):
+                        prev_leap_year -= 1
+                    d = datetime.datetime.strptime(
+                        f"{prev_leap_year} {s}", "%Y %b %d %H:%M"
+                    )
+                    # Check if it's next leap year
+                    diff = (now - d).total_seconds()
+                    if diff > TWO_YEARS_IN_SECONDS:
+                        d = d.replace(year=prev_leap_year + 4)
+                else:
+                    d = datetime.datetime.strptime(s, "%b %d %H:%M")
+                    d = d.replace(year=now.year)
+      
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Parse FTP directory listing dates with single-space separators
      `parse_ls_date` doesn't handle singe-spaced servers.
I'm using aioftp across a multitude of FTP servers, and just discovered that one of them uses a timestamp of `%b %d %Y` instead of `%b %d  %Y`.
Because the parser assumes the date is exactly 12 characters long, it will catch `Jan 03 2018` as `Jan
      Way: strip ls date strings before datetime parsing
      - Locate where FTP directory listing lines are parsed into file metadata and where `parse_ls_date` is called.
      - Apply `.strip()` on the sliced date string (`s[:12].strip()`) before passing it to `parse_ls_date` to remove trailing spaces from single-spaced date formats.
      - Run the test suite including directory listing parsing tests to confirm both double-spaced and single-spaced dates parse successfully.
  [2] Goal: Prevent base path directory traversal using Windows absolute paths
      Directory traversal / base path escape possible on Windows
The server allows escaping the user `base_path` on Windows using absolute path (segments).
This is due to [`get_paths`](https://github.com/aio-libs/aioftp/blob/81d4bc018261268bf2ea88f42e05f130ab4c9229/aioftp/server.py#L991):
While the virt
      Way: Validate joined real path subpath confinement against Windows path traversal
      - Locate virtual-to-real path resolution logic that joins a user virtual path to a base path.
      - Reproduce directory traversal where a virtual path containing Windows drive/anchor segments (e.g., 'C:\...') breaks out of the base path when joined to a Windows path.
      - Wrap the joined real path with a `relative_to(base_path)` check inside a try-except ValueError block, falling back to base path and root virtual path if a traversal breakout is detected.
      - Mark the path resolution helper method with `@staticmethod` if it does not access instance state.
      - Run test suite to verify that Windows path traversal attempts are safely caught and reset to base path.

=== TASK iterative__dvc-8381 | repo iterative/dvc | python
--- Issue
hydra: append (`+`) doesn't work with sweeps
# Bug Report

## Description

In `dvc exp`, using `+` from the basic override syntax doesn't work when doing a choice or range sweep. Similar non-DVC hydra examples seem to work fine, so I assume it's a DVC issue.

### Reproduce

Script:

```
git init hydra
cd hydra
dvc init
echo 'foo: 1' > params.yaml
dvc stage add -n hydra -p 'params.yaml:' 'cat params.yaml'
git add .
git commit -m 'setup'
dvc exp run -v --queue -S '+bar=1,2'
```

Output:

```
2022-09-28 15:48:16,027 ERROR: unexpected error - mismatched input '+' expecting EQUAL
See https://hydra.cc/docs/next/advanced/override_grammar/basic for details
------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/dave/Code/dvc/dvc/cli/__init__.py", line 185, in main
    ret = cmd.do_run()
  File "/Users/dave/Code/dvc/dvc/cli/command.py", line 22, in do_run
    return self.run()
  File "/Users/dave/Code/dvc/dvc/commands/experiments/run.py", line 32, in run
    results = self.repo.experiments.run(
  File "/Users/dave/Code/dvc/dvc/repo/experiments/__init__.py", line 521, in run
    return run(self.repo, *args, **kwargs)
  File "/Users/dave/Code/dvc/dvc/repo/__init__.py", line 49, in wrapper
    return f(repo, *args, **kwargs)
  File "/Users/dave/Code/dvc/dvc/repo/experiments/run.py", line 67, in run
    sweeps = get_hydra_sweeps(path_overrides)
  File "/Users/dave/Code/dvc/dvc/utils/hydra.py", l
--- Real fix (for your judgement only)
diff --git a/dvc/utils/hydra.py b/dvc/utils/hydra.py
--- a/dvc/utils/hydra.py
+++ b/dvc/utils/hydra.py
@@ -1,4 +1,3 @@
-from collections import defaultdict
 from pathlib import Path
 from typing import TYPE_CHECKING, List
 
@@ -83,32 +82,21 @@ def to_hydra_overrides(path_overrides):
     return parser.parse_overrides(overrides=path_overrides)
 
 
+def dict_product(dicts):
+    import itertools
+
+    return [dict(zip(dicts, x)) for x in itertools.product(*dicts.values())]
+
+
 def get_hydra_sweeps(path_overrides):
-    merged_overrides = []
+    path_sweeps = {}
     for path, overrides in path_overrides.items():
-        # `.` is reserved character in hydra syntax
-        # _merge_ is required to support sweeps across multiple files.
-        merged_overrides.extend(
-            [
-                f"{path.replace('.', '_')}_merge_{override}"
-                for override in overrides
-            ]
-        )
-
-    hydra_overrides = to_hydra_overrides(merged_overrides)
-    for hydra_override in hydra_overrides:
-        if hydra_override.value_type == ValueType.GLOB_CHOICE_SWEEP:
-            raise InvalidArgumentError(
-                f"Glob override '{hydra_override.input_line}' "
-                "is not supported."
-            )
-
-    splits = BasicSweeper.split_arguments(hydra_overrides, None)[0]
-    sweeps = []
-    for split in splits:
-        sweep_overrides = defaultdict(list)
-        for merged_override in split:
-            path, override = merged_override.split("_merge_")
-            sweep_overrides[path.replace("_", ".")].append(override)
-        sweeps.append(dict(sweep_overrides))
-    return sweeps
+        overrides = to_hydra_overrides(overrides)
+        for override in overrides:
+            if override.value_type == ValueType.GLOB_CHOICE_SWEEP:
+                raise InvalidArgumentError(
+                    f"Glob override '{override.input_line}' "
+                    "is not supported."
+                )
+        path_sweeps[
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Support stage-level variables in dvc.yaml configuration
      Stage-level `vars` alternative in DVC 3.X
# Bug Report

<!--
## Issue name

Issue names must follow the pattern `command: description` where the command is the dvc command that you are trying to run. The description should describe the consequence of the bug.

Example: `repro: doesn't detect 
      Way: support stage level variables in pipeline configuration
      - Locate the schema definition for pipeline stages and the parsing logic for stage entries.
      - Update the stage definition schema to optionally accept a `vars` key matching the global `vars` schema format.
      - Modify the stage entry resolution logic to extract `vars` from the stage definition, resolve the working directory (`wdir`), and load the stage-level variables into a cloned context before resolving other stage fields.
      - Run the parsing and validation tests to verify that stage-level variables are correctly parsed, validated, and interpolated.
  [2] Goal: Handle empty lines in ignore files without throwing errors
      .dvcignore - new lines throw error, expected str instance, NoneType found.
```
DVC version: 1.0.0b2
Python version: 3.7.6
Platform: Ubuntu 18.04.4 LTS
Package: dvc_1.0.0b2_amd64.deb
Filesystem: ceph-fuse
```
- **Issue:** After installing DVC v1.0.0b2, all DVC commands fail with the following 
      Way: Filter out None values when grouping ignore file patterns
      - Locate the ignore file parsing and pattern compilation logic, specifically where patterns are grouped.
      - Reproduce the issue by creating an ignore file containing blank lines and running a command that triggers ignore file parsing.
      - Modify the pattern grouping list comprehension in the ignore pattern class to skip groups where the ignore status is None.
      - Verify the fix by running the test suite for ignore files.
  [3] Goal: Ignore untracked values in parameter files during experiment commands
      params: untracked values in params files are not ignored in params/exp commands
# Bug Report

exp show: Invalid or wrong values for column names and values

<!--
## Issue name

Issue names must follow the pattern `command: description` where the command is the dvc command that you are trying 
      Way: filter parameter files to only show tracked stage dependencies
      - Locate the parameter dependency class and add a method to read only the specific keys/paths that are explicitly declared as dependencies, rather than reading the entire file.
      - Update the parameter collection and reading logic in the repository layer to accept a flag (e.g., `deps` or `param_deps`) that triggers reading only the declared dependencies instead of the whole file.
      - Expose this filtering option to the command-line interface by adding a `--deps` or `--param-deps` flag to the relevant show and diff commands.
      - Verify the changes by running the unit tests for experiment and parameter diff/show commands.

=== TASK iterative__dvc-4045 | repo iterative/dvc | python
--- Issue
plots diff: WARN about bad templates sent to -t [qa]
## ~~Bug Report~~

UPDATE: Jump to https://github.com/iterative/dvc/issues/3969#issuecomment-640879725

---
OLD:

Using logs.csv from the example in https://dvc-landing-2020-05-31-evbiu1f.herokuapp.com/doc/command-reference/plots/modify#examples (see iterative/dvc.org/pull/1382):

```console
dvc plots diff -t train.json
file://C:\Users\poj12\DVC-repos\tests\plots.html
```

![image](https://user-images.githubusercontent.com/1477535/83983047-c3ded700-a8f0-11ea-90e7-d3db24a21916.png)

### Please provide information about your setup

**Output of `dvc version`:**

```console
λ dvc version
DVC version: 1.0.0a9+3683c2
Python version: 3.8.2
Platform: Windows-10-10.0.18362-SP0
Binary: False
Package: None
Supported remotes: azure, gdrive, gs, hdfs, http, https, s3, ssh, oss
Cache: reflink - not supported, hardlink - supported, symlink - supported
Filesystem type (cache directory): ('NTFS', 'C:\\')
Repo: dvc, git
Filesystem type (workspace): ('NTFS', 'C:\\')
```

**Additional Information (if any):**

If applicable, please also provide a `--verbose` output of the command, eg: `dvc add --verbose`.


--- Real fix (for your judgement only)
diff --git a/dvc/repo/plots/template.py b/dvc/repo/plots/template.py
--- a/dvc/repo/plots/template.py
+++ b/dvc/repo/plots/template.py
@@ -12,6 +12,10 @@ def __init__(self, path):
         super().__init__(f"Template '{path}' not found.")
 
 
+class BadTemplateError(DvcException):
+    pass
+
+
 class NoFieldInDataError(DvcException):
     def __init__(self, field_name):
         super().__init__(
@@ -39,6 +43,12 @@ def __init__(self, content=None, name=None):
     def render(self, data, props=None):
         props = props or {}
 
+        if self.METRIC_DATA_ANCHOR not in self.content:
+            raise BadTemplateError(
+                f"Template '{self.filename}' is not using "
+                f"'{self.METRIC_DATA_ANCHOR}' anchor"
+            )
+
         if props.get("x"):
             Template._check_field_exists(data, props.get("x"))
         if props.get("y"):

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent crash when showing metrics for Git tags without DVC directory
      dvc metrics -T raises exception if there are tags unrelated to the DVC sub-directory
DVC version: 0.94.0
Python version: 3.6.7
Platform: Windows-10-10.0.18362-SP0
Binary: False
Package: pip
Supported remotes: http, https
Filesystem type (workspace): ('NTFS', 'C:\\')

```
NT> dvc metrics sho
      Way: prevent crash when showing metrics for Git tags without DVC directory
      - Locate where the repository branches/tags are iterated over during metrics collection, and where the repository tree is constructed.
      - Modify the brancher utility to skip revisions (such as tags) that do not contain the DVC repository root directory.
      - Update the CleanTree wrapper to accept an optional custom tree root, and use it to correctly resolve paths when the tree root differs from the underlying tree's root.
      - Update the Repo tree setter to pass the repository root directory to CleanTree if the tree is not a working tree and its root does not match the repository root.
      - Run the metrics show tests to verify that preexisting tags without the DVC subdirectory are ignored without raising a FileNotFoundError.
  [2] Goal: Fix crash when running diff on untracked revisions
      dvc diff ERROR: unexpected error - 'NoneType' object has no attribute 'endswith' 
If given rev is not tracked fails. I.E 2f05868ebe3693a7e7afbdd8923c15ce327b179d does not have metrics:
```
dvc diff  --show-json 2f05868ebe3693a7e7afbdd8923c15ce327b179d 19b33235917f6b3732de8486b35235ba200e74a2
ERRO
      Way: Handle None checksum values in base remote directory check
      - Locate checksum directory check logic in `BaseRemote.is_dir_checksum`.
      - Reproduce the crash by calling `is_dir_checksum(None)` or running diff on revisions without tracked metrics/checksums.
      - Add a guard check returning `False` if `checksum` is `None` or falsy before calling `endswith` on it.
      - Verify the unit test passes when passing `None` as a checksum argument.
  [3] Goal: Report correct directory file diff statistics when running diff command
      diff: Stats reported by dvc not correct
# Description

When tracking a directory using DVC, the stats reported by `dvc diff` are not correct. For example, in a small test repository, the directory `test` is being tracked by DVC. When running `dvc diff` between two commits, the following is reporte
      Way: increment modified files counter during directory diff
      - Search the codebase for the diff output message formatting and directory diff logic.
      - Inspect the function calculating modified files (`_modified_files`) when comparing directory entries.
      - Fix the missing increment statement `modified_count += 1` inside `_modified_files` when an entry value changes.
      - Update the diff summary message format string from 'not changed' to 'untouched' if required by updated output specifications.
      - Run the functional diff test suite to ensure directory diff counts and statistics are accurately reported.

=== TASK Krukov__cashews-29 | repo Krukov/cashews | python
--- Issue
Case sensitive keys (do not convert silently all string arguments to lowercase by default)
I was surprised when I occasionally found that all strings are converted to lowercase when building a key. 
```python
import asyncio
import logging
from datetime import timedelta

from cashews import cache

logger = logging.getLogger(__name__)
logging.basicConfig(level="DEBUG")


async def logging_middleware(call, *args, backend=None, cmd=None, **kwargs):
    key = args[0] if args else kwargs.get("key", kwargs.get("pattern", ""))
    logger.info(f"{args}; {kwargs}")
    # logger.info("=> Cache request: %s ", cmd, extra={"command": cmd, "cache_key": key})
    return await call(*args, **kwargs)


cache.setup("mem://", middlewares=(logging_middleware, ))


@cache(ttl=timedelta(minutes=1))
async def get_name(user, version="v1"):
    return user


async def main():
    await get_name("Bob")
    result_1 = await get_name("Bob")
    result_2 = await get_name("bob")
    print(result_1)
    print(result_2)

    value = await cache.get("__main__:get_name:user:bob:version:v1")
    print(f"value: {value}")
    value = await cache.get("__main__:get_name:user:Bob:version:v1")
    print(f"value: {value}")


asyncio.run(main())
```
# Question

1. Is it a bug or a future?
2. Is there an easy way to disable strings lowercase when building a key? So if I pass `my_string=FooBar`, it will be saved as `FooBar`, not as `foobar`.
--- Real fix (for your judgement only)
diff --git a/cashews/__init__.py b/cashews/__init__.py
index d55971e..9611fa0 100644
--- a/cashews/__init__.py
+++ b/cashews/__init__.py
@@ -7,7 +7,7 @@ from .decorators import (  # noqa
     fast_condition,
 )
 from .formatter import default_formatter, get_template_and_func_for, get_template_for_key  # noqa
-from .helpers import add_prefix  # noqa
+from .helpers import add_prefix, all_keys_lower  # noqa
 from .validation import set_invalidate_further  # noqa
 from .wrapper import Cache  # noqa
 
diff --git a/cashews/backends/redis/backend.py b/cashews/backends/redis/backend.py
index 5bdeffa..f33099c 100644
--- a/cashews/backends/redis/backend.py
+++ b/cashews/backends/redis/backend.py
@@ -198,8 +198,5 @@ class _Redis(Backend):
         return await self._client.get(key)
 
     def close(self):
-        del self._client
         self._client = None
         self.__is_init = False
-
-    __del__ = close
diff --git a/cashews/formatter.py b/cashews/formatter.py
index fef1406..9fd9bf9 100644
--- a/cashews/formatter.py
+++ b/cashews/formatter.py
@@ -18,7 +18,7 @@ class _ReplaceFormatter(Formatter):
             return self.__default(field_name), None
 
     def format_field(self, value, format_spec):
-        return format(value)
+        return format(format_value(value))
 
 
 class _FuncFormatter(_ReplaceFormatter):
@@ -38,9 +38,9 @@ class _FuncFormatter(_ReplaceFormatter):
 
     def format_field(self, value, format_spec):
         format_spec, args = self.parse_format_spec(format_spec)
-        value = super().format_field(value, format_spec if format_spec not in self._functions else "")
         if format_spec in self._functions:
-            return str(self._functions[format_spec](value, *args))
+            value = str(self._functions[format_spec](value, *args))
+        value = super().format_field(value, format_spec if format_spec not in self._functions else "")
         return value
 
     @staticmethod
@@ -51,6 +51,14 @@ class _FuncFormatter(_ReplaceFormatter)
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Allow templated cache tags with None values without raising registration errors
      Templated tags with None values throw TagNotRegisteredError
Hi, first thanks for the great library! Love the cutting edge features and performance.

## What is the Issue

When we register a tag using the `@cache` decorator, and that tag is a template, and then we call the cached function with a 
      Way: Normalizing None values to empty strings in template tag pattern matching
      - Trace tag validation failure in cache tag registry when function arguments are None, comparing tag generation at decoration time vs key validation time.
      - Reproduce TagNotRegisteredError by creating a test with a cached function using a templated tag with an optional argument defaulting to None.
      - Update TagsRegistry.get_tags_by_key to replace None values in regex match groupdict with empty strings before formatting tag templates.
      - Run the tag feature tests to ensure templated tags with None values are recognized as registered tags.

=== TASK PyThaiNLP__pythainlp-399 | repo PyThaiNLP/pythainlp | python
--- Issue
countthai not cover all cases
**Describe the bug**
Input countthai('123') or countthai(' ') or countthai('1 2 3 4') create error instead of zero? 

**To Reproduce**
Steps to reproduce the behavior:
1. from pythainlp.util import countthai
2. input either '123' or ' '

**Expected behavior**
it should return zero?
--- Real fix (for your judgement only)
diff --git a/pythainlp/util/thai.py b/pythainlp/util/thai.py
index 1a78dc32..b141b260 100644
--- a/pythainlp/util/thai.py
+++ b/pythainlp/util/thai.py
@@ -127,7 +127,7 @@ def countthai(text: str, ignore_chars: str = _DEFAULT_IGNORE_CHARS) -> float:
         # output: 0.0
     """
     if not text or not isinstance(text, str):
-        return 0
+        return 0.0
 
     if not ignore_chars:
         ignore_chars = ""
@@ -143,4 +143,7 @@ def countthai(text: str, ignore_chars: str = _DEFAULT_IGNORE_CHARS) -> float:
 
     num_count = len(text) - num_ignore
 
+    if num_count == 0:
+        return 0.0
+
     return (num_thai / num_count) * 100

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Make countthai return zero for inputs without Thai characters
      countthai not cover all cases
**Describe the bug**
Input countthai('123') or countthai(' ') or countthai('1 2 3 4') create error instead of zero? 

**To Reproduce**
Steps to reproduce the behavior:
1. from pythainlp.util import countthai
2. input either '123' or ' '

**Expected behavior**
i
      Way: Handle division by zero when calculating character percentage
      - Locate character counting and percentage calculation function in the utility module.
      - Reproduce the error (such as ZeroDivisionError) by passing input strings containing only ignored characters like whitespace or numbers.
      - Update the function to check if the effective character count (total length minus ignored characters) is zero before division, returning 0.0 in that case, and ensure consistent float return types for empty or invalid input.
      - Run the test suite to verify calculation for zero-match and ignored-character strings.
  [2] Goal: Raise an error when custom dictionary is used with unsupported tokenizer engines
      bug: Should raise error for using custom dict with unsupported word tokenization engine
### Description

Should raise an error when `custom_dict` is passed into `word_tokenizer` function but the selected engine is not supported custom_dict

### Expected results

Should either:
1. Raise `NotIm
      Way: Enforce custom dictionary support for word tokenizers
      - Locate the `word_tokenize` function and identify the section where different tokenizer engines are dispatched based on the `engine` parameter. Understand how `custom_dict` is passed to this function.
      - Reproduce the issue by calling `word_tokenize` with a `custom_dict` and an engine known not to support it (e.g., 'attacut'). Observe that no error is raised and the custom dictionary is silently ignored.
      - Add a conditional check at the beginning of the `word_tokenize` function's implementation. If a `custom_dict` is provided AND the selected `engine` is one of the known unsupported engines ('attacut', 'icu', 'nercut', 'sefr_cut', 'tltk', 'oskut'), raise a `NotImplementedError` with a message indicating the engine does not support custom dictionaries.
      - Run the relevant tests to confirm that the `NotImplementedError` is now raised when an unsupported engine is used with a custom dictionary, and that other functionalities remain correct.
  [3] Goal: Implement a longest common subsequence algorithm
      Add longest common sub-sequence algorithm
Hi, due to my work require me to write calculate "similarity" of strings, I've created a dynamic programming version of my own and I would like to contribute to pythainlp.

I will submit a pull request within 2-3 days if there is not any problem.

Refere
      Way: Implement dynamic programming for longest common subsequence
      - Identify the need for a longest common subsequence (LCS) algorithm and its standard dynamic programming implementation for string comparison.
      - Create a new Python module `pythainlp/util/lcs.py` and define the `longest_common_subsequence(str1: str, str2: str) -> str` function within it. Implement the dynamic programming algorithm to compute the length of the LCS using a 2D array, then reconstruct the actual subsequence string by backtracking through the array.
      - Integrate the new function into the package by importing `longest_common_subsequence` from `pythainlp.util.lcs` into `pythainlp/util/__init__.py` and adding it to the `__all__` list to make it publicly accessible.
      - Add documentation for the `longest_common_subsequence` function to `docs/api/util.rst` using `.. autofunction::` directive, including a brief description and an example.
      - Verify the correctness of the implemented `longest_common_subsequence` function and ensure no existing functionality is broken.

=== TASK bids-standard__pybids-615 | repo bids-standard/pybids | python
--- Issue
should pybids more lenient or at least get a dedicated exception like NonCompliantBIDSError
ATM pybids immediately throws a generic ValueError if found contained derivatives dataset is not fully compliant:

```shell
/tmp > datalad install https://github.com/OpenNeuroDatasets/ds001868 && cd ds001868
[INFO   ] Cloning https://github.com/OpenNeuroDatasets/ds001868 [1 other candidates] into '/tmp/ds001868' 
[INFO   ]   Remote origin not usable by git-annex; setting annex-ignore                                         
[INFO   ] access to 1 dataset sibling s3-PRIVATE not auto-enabled, enable with:
| 		datalad siblings -d "/tmp/ds001868" enable -s s3-PRIVATE 
install(ok): /tmp/ds001868 (dataset)

/tmp/ds001868 > python -c 'from bids import BIDSLayout; b=BIDSLayout(".", derivatives=True)' 
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "/home/yoh/proj/bids/pybids/bids/layout/layout.py", line 227, in __init__
    index_metadata=index_metadata)
  File "/home/yoh/proj/bids/pybids/bids/layout/layout.py", line 555, in add_derivatives
    raise ValueError("Every valid BIDS-derivatives dataset must "
ValueError: Every valid BIDS-derivatives dataset must have a PipelineDescription.Name field set inside dataset_description.json.
```
But I wonder
- if this should be a warning and not error (optionally or not)
- there should be a dedicated exception (e.g. `NonCompliantBIDSError(ValueError)` and `NonCompliantDerivativesBIDSError(NonComplian
--- Real fix (for your judgement only)
diff --git a/bids/exceptions.py b/bids/exceptions.py
new file mode 100644
index 00000000..7c599ea6
--- /dev/null
+++ b/bids/exceptions.py
@@ -0,0 +1,37 @@
+""" Exceptions.
+
+Exceptions relating to problems with BIDS itself, should carry BIDS in their
+name.  All exceptions should subclass from PyBIDSError
+"""
+
+
+class PyBIDSError(Exception):
+    """ Base class.  Typically for mix-in."""
+
+
+class ConfigError(ValueError, PyBIDSError):
+    """ Problems with config file. """
+
+
+class NoMatchError(ValueError, PyBIDSError):
+    """ No match found where it is required. """
+
+
+class BIDSEntityError(AttributeError, PyBIDSError):
+    """ An unknown entity. """
+
+
+class TargetError(ValueError, PyBIDSError):
+    """ An unknown target. """
+
+
+class BIDSValidationError(ValueError, PyBIDSError):
+    """ An invalid BIDS dataset. """
+
+
+class BIDSDerivativesValidationError(BIDSValidationError):
+    """ An invalid BIDS derivative dataset. """
+
+
+class BIDSConflictingValuesError(BIDSValidationError):
+    """ A value conflict (e.g. in filename and side-car .json) """
diff --git a/bids/layout/index.py b/bids/layout/index.py
index ee41acda..e181b463 100644
--- a/bids/layout/index.py
+++ b/bids/layout/index.py
@@ -6,6 +6,7 @@ from collections import defaultdict
 from bids_validator import BIDSValidator
 from .models import Config, Entity, Tag, FileAssociation
 from ..utils import listify, make_bidsfile
+from ..exceptions import BIDSConflictingValuesError
 
 
 def _extract_entities(bidsfile, entities):
@@ -367,8 +368,9 @@ class BIDSLayoutIndexer(object):
                             "filename {} (value='{}') versus its JSON sidecar "
                             "(value='{}'). Please reconcile this discrepancy."
                         )
-                        raise ValueError(msg.format(md_key, bf.path, file_val,
-                                                    md_val))
+                        raise BIDSConflictingValuesError(
+                           
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Allow indexing of datasets with null metadata values without errors
      BUG: null metadata results in indexing failure
```python
sdcflows/conftest.py:39: in <module>
    layouts = {
sdcflows/conftest.py:40: in <dictcomp>
    p.name: BIDSLayout(str(p), validate=False, derivatives=True)
/usr/share/miniconda/envs/test/lib/python3.10/site-packages/bids/layout/layout.py
      Way: Ignore None metadata entries during dataset indexing
      - Trace metadata indexing to the loop that processes key-value metadata pairs and constructs database tags/entities.
      - Check for `None` values (deserialized JSON `null`s) during the metadata key-value iteration and skip tag creation for them.
      - Run the layout and indexing test suite to verify datasets with null/None metadata values index without raising ValueError.
  [2] Goal: Index derivatives directories lacking dataset description metadata
      Derivatives indexing fails silently for deriv directories that lack a `dataset_description.json` file
I have a set of derivatives files that look like this:

derivatives/rt/sub-02/func/sub-02_task-dts_meanRT.tsv

they seem to pass the validator fine, but I get nothing when I use .get(scope='deri
      Way: warn when derivative dataset indexing finds no valid dataset descriptions
      - Identify derivative discovery logic where directories are filtered by the presence of metadata description files (e.g., `dataset_description.json`).
      - Update docstrings to clearly document that derivative indexing requires a `dataset_description.json` file in each derivative directory.
      - Add a warning when derivative indexing is requested but no valid derivative directories containing the required description metadata are discovered.
      - Run the layout derivative tests to ensure the warning is emitted and valid indexing passes.
  [3] Goal: Add missing underscore separator before tracer entity when building BIDS paths
      BIDSValidationError When Using build_path with tracer
**Description:**

I'm encountering a `BIDSValidationError` when using the `build_path` method from the `pybids` library (version 0.15.5) to generate BIDS-compliant paths using specific entities. The error message suggests that the built path is
      Way: Add missing underscore separator before path building pattern entities
      - Search path template configuration patterns (such as `bids.json`) for entity placeholders like `trc-{tracer}` missing the leading underscore `_` separator inside optional brackets.
      - Update all PET datatype path patterns to prefix the tracer entity template with an underscore, changing `[trc-{tracer}]` to `[_trc-{tracer}]`.
      - Run the path building test suite to verify pet path construction works without validation errors.

=== TASK Axelrod-Python__Axelrod-653 | repo Axelrod-Python/Axelrod | python
--- Issue
Add tests for reproducibility of stochastic results
I'd like to add a property based test similar to https://github.com/Nikoleta-v3/Axelrod/blob/635/axelrod/tests/integration/test_matches.py but that checks that when setting a seed we get the same results for stochastic strategies (those tests there only check for 'well behaved deterministic' strategies. I have a hunch that this isn't actually true as we're not setting numpy's seed...

If that's the case and both seeds need to be set, we should document how to get reproducible results and either:

1. Indicate that you should set both seeds (numpy and stdlib);
2. Write a little helper function that does this (and just document it: `axelrod.seed()` or something like that...
--- Real fix (for your judgement only)
diff --git a/axelrod/__init__.py b/axelrod/__init__.py
index 7cb2ebda..482df852 100644
--- a/axelrod/__init__.py
+++ b/axelrod/__init__.py
@@ -2,7 +2,7 @@ from __future__ import absolute_import
 
 # The order of imports matters!
 from .actions import Actions, flip_action
-from .random_ import random_choice
+from .random_ import random_choice, seed
 from .plot import Plot
 from .game import DefaultGame, Game
 from .player import init_args, is_basic, obey_axelrod, update_history, Player
diff --git a/axelrod/random_.py b/axelrod/random_.py
index 0c3bc72e..9dc783e5 100644
--- a/axelrod/random_.py
+++ b/axelrod/random_.py
@@ -1,4 +1,5 @@
 import random
+import numpy
 from axelrod import Actions
 
 
@@ -21,3 +22,9 @@ def randrange(a, b):
     c = b - a
     r = c * random.random()
     return a + int(r)
+
+
+def seed(seed):
+    """Sets a seed"""
+    random.seed(seed)
+    numpy.random.seed(seed)
diff --git a/axelrod/strategies/_strategies.py b/axelrod/strategies/_strategies.py
index 004108fc..7362e44e 100644
--- a/axelrod/strategies/_strategies.py
+++ b/axelrod/strategies/_strategies.py
@@ -56,7 +56,7 @@ from .sequence_player import SequencePlayer, ThueMorse, ThueMorseInverse
 from .titfortat import (
     TitForTat, TitFor2Tats, TwoTitsForTat, Bully, SneakyTitForTat,
     SuspiciousTitForTat, AntiTitForTat, HardTitForTat, HardTitFor2Tats,
-    OmegaTFT, Gradual, ContriteTitForTat)
+    OmegaTFT, Gradual, ContriteTitForTat, SlowTitForTwoTats)
 
 
 # Note: Meta* strategies are handled in .__init__.py
@@ -166,6 +166,7 @@ strategies = [
     Ripoff,
     RiskyQLearner,
     Shubik,
+    SlowTitForTwoTats,
     SneakyTitForTat,
     SoftGrudger,
     SoftJoss,
diff --git a/axelrod/strategies/titfortat.py b/axelrod/strategies/titfortat.py
index fef73595..6086ca2a 100644
--- a/axelrod/strategies/titfortat.py
+++ b/axelrod/strategies/titfortat.py
@@ -386,3 +386,36 @@ class ContriteTitForTat(Player):
         Player.reset(self)
         self.contrite = False
         self._reco
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Filter strategies based on classifier dictionary attributes
      Strategy Filtering by Dictionary
Within the api, there are a set of functions to filter strategies from their classifier entries using a dictionary. (This is because parameters within a url are passed as dictionary).

For example, the url https://axelrod-api.herokuapp.com/strategies/?stochastic=fa
      Way: filter strategy classes by classifier dictionary attributes
      - Examine how strategies and their `classifier` dictionaries are structured and exposed in the strategies module.
      - Create helper filter functions (`passes_operator_filter`, `passes_in_list_filter`, and `passes_filterset`) that inspect strategy classifiers, normalize special values like string `'infinity'` to `float('inf')`, support equality/comparison operators, and verify subset conditions for list attributes like `makes_use_of`.
      - Map supported filter keys (e.g., boolean flags, exact memory depth, `min_memory_depth`, `max_memory_depth`, and `makes_use_of`) to corresponding filter predicates in `passes_filterset`.
      - Expose `filtered_strategies(filterset, strategies=all_strategies)` in the strategies package namespace to return all strategies satisfying all filter criteria.
      - Run unit and integration filter tests to ensure filtering behavior matches manual list comprehensions.
  [2] Goal: Correct payoff difference means calculation for self interactions in tournament results
      Error in payoff_diffs_means?
I think there's a bug for self interactions for the payoff_diffs_means (this only affects stochastic strategies):

```
>>> import axelrod as axl
>>> from numpy import mean
>>> axl.seed(0)
>>> players = [s() for s in axl.demo_strategies]
>>> tournament = axl.Tourna
      Way: derive mean payoff differences directly from precomputed score differences
      - Find where `build_payoff_diffs_means` or equivalent summary statistics are computed in the tournament result aggregation code.
      - Inspect how self-interactions (`player == opponent`) are processed when rebuilding difference metrics directly from raw pairwise interaction dictionaries.
      - Simplify `build_payoff_diffs_means` by computing the mean over each list of differences in the precalculated `self.score_diffs` matrix instead of recomputing scores from `self.interactions`.
      - Run the result set unit test suite targeting self-interactions with stochastic strategies to verify correct sign and values.
  [3] Goal: Reset finite state machine player state when resetting the player
      Finite state machine players don't reset properly
```
>>> import axelrod as axl
>>> tft = axl.TitForTat()
>>> predator = axl.Predator()
>>> predator.fsm.state
1
>>> m = axl.Match((tft, predator), 2)
>>> m.play()
[('C', 'C'), ('C', 'D')]
>>> predator.fsm.state
 2
>>> predator.reset()
>>> 
      Way: Restoring finite state machine initial state during player reset
      - Identify the base player class managing the state machine lifecycle and inspect how its reset method handles state machine properties.
      - Reproduce the issue by running consecutive match plays or calling reset() on a finite state machine player instance and checking whether fsm.state retains the post-match state instead of returning to the initial state.
      - Store initial_state as an instance attribute on the FSM player class during initialization (__init__).
      - Implement a reset method on the FSM player class that calls the superclass reset and sets self.fsm.state back to self.initial_state.
      - Run the unit and integration tests to verify that reset restores initial state across all FSM player subclasses and match replays produce identical outcomes.

=== TASK iterative__dvc-9391 | repo iterative/dvc | python
--- Issue
Being able to call  `exp show` with multiple branches (`--rev`) and a number of commits (`-n`)
In the VS Code extension, we would like to show multiple branches at the same time in the table. Each with its own number of commits. We're currently doing this by calling `exp show` once per branch (`dvc exp show --rev main -n 5`, `dvc exp show --rev other-branch -n 3`...). We'd like to be able to do this in one call only (something like `dvc exp show --rev main -n 5 --rev other-branch -n 3...`). Would it be possible?

--- Real fix (for your judgement only)
diff --git a/dvc/commands/experiments/__init__.py b/dvc/commands/experiments/__init__.py
--- a/dvc/commands/experiments/__init__.py
+++ b/dvc/commands/experiments/__init__.py
@@ -82,6 +82,7 @@ def add_rev_selection_flags(
     experiments_subcmd_parser.add_argument(
         "--rev",
         type=str,
+        action="append",
         default=None,
         help=msg,
         metavar="<commit>",

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Support multiple checkpoint outputs in a single stage
      exp run: fails with multiple checkpoint outs in a stage
# Bug Report

## Description

Might not be technically a bug, but it seems impossible right now to have multiple checkpoints. For example, if I have my model weights as one output and a file with the current step/epoch as another, I want bo
      Way: Support multiple checkpoint outputs with identical environment variables in a stage
      - Locate the stage environment variable generation logic where environment variables from different outputs are merged and validated.
      - Modify the environment variable validation to allow duplicate keys across outputs as long as their values are identical, raising an exception only when there are conflicting values.
      - Update the list command implementation to use the UI writer instead of the logger for writing JSON and prettified output.
      - Verify the fix by running the list command unit tests.
  [2] Goal: Show metrics across branches without failing on missing files
      dvc metrics: show metrics across branches fails with file does not exist
DVC version 0.35.7

```
dvc metrics show -a -v 
ERROR: failed to show metrics - 'data/data-2010-2016.h5.dvc' does not exist.
------------------------------------------------------------
Traceback (most recent call last):
      Way: Yield absolute root paths during tree walking across SCM revisions
      - Locate Git and SCM file system tree walking mechanisms used when traversing repository revisions.
      - Reproduce the error by running metrics CLI commands across git branches from within subdirectories or non-root working directories.
      - Update git tree traversal to yield absolute root paths (`tree.abspath`) instead of relative tree paths (`tree.path`), and ensure `WorkingTree` walk converts the `top` path to an absolute path before delegating to directory walking.
      - Run the tests covering tree directory walking and cross-branch metrics retrieval to confirm missing file errors are resolved.
  [3] Goal: Inspect files across Git revisions using APIs instead of checking out branches
      Use git/dvc APIs instead of actually checking out revisions
There are few DVC commands that accept `--all-branches` and `--all-tags` options. Namely,  [`dvc metrics show`](https://dvc.org/doc/commands-reference/metrics-show), [`dvc gc`](https://dvc.org/doc/commands-reference/gc), [`dvc fetch`](https
      Way: abstract filesystem operations to inspect git revisions without switching branches
      - Locate branch iteration and workspace checkout logic in the repo/SCM layers.
      - Create filesystem tree abstractions (such as working tree vs git object tree wrappers) providing unified open/walk/isdir/exists methods for a given revision.
      - Update commands and utilities (like metrics reading, walking files, and brancher) to query the tree abstraction for a given target revision instead of invoking git checkout.
      - Run test suite to verify commands inspect historical/branch revisions cleanly without workspace side-effects.

=== TASK datalad__datalad-next-632 | repo datalad/datalad-next | python
--- Issue
Discontinue (direct) usage of any runner implementations
With #538 we made the start of a new paradigm: iterator-based interaction with (concurrent) subprocesses.

It is our (@mih and @christian-monch) understanding that this basic approach would work for most (if not all) use cases that involve the execution of subprocesses. Respective experiments have been made and the results look promising. The "ultimate" challenge will be the implementation of a queue-based remote shell.

In order to bring down the complexity of implementations after the introduction of this new paradigm, all subprocess execution in datalad-next should be switch to it.

Afterwards all runner-related imports from datalad-core should be discontinued (which involves updating any extensions that make use of it).

This plan answers the questions of #519. `GitRunner` has no future.
--- Real fix (for your judgement only)
diff --git a/datalad_next/gitremotes/datalad_annex.py b/datalad_next/gitremotes/datalad_annex.py
index a39fa8d..561d4c4 100755
--- a/datalad_next/gitremotes/datalad_annex.py
+++ b/datalad_next/gitremotes/datalad_annex.py
@@ -210,8 +210,9 @@ from datalad_next.datasets import (
 from datalad_next.exceptions import CapturedException
 from datalad_next.runners import (
     CommandError,
-    NoCapture,
-    StdOutCapture,
+    call_git,
+    call_git_oneline,
+    call_git_success,
 )
 from datalad_next.uis import ui_switcher as ui
 from datalad_next.utils import (
@@ -224,6 +225,7 @@ from datalad_next.utils import (
     get_specialremote_credential_envpatch,
     get_specialremote_credential_properties,
     needs_specialremote_credential_envpatch,
+    patched_env,
     specialremote_credential_envmap,
     update_specialremote_credential,
 )
@@ -494,8 +496,9 @@ class RepoAnnexGitRemote(object):
         try:
             # send annex into private mode, if supported
             # this repo will never ever be shared
-            ra.call_git(['config', 'annex.private', 'true'])
-            ra.call_git(['annex', 'init'])
+            call_git_success(['config', 'annex.private', 'true'],
+                             cwd=ra.pathobj, capture_output=True)
+            call_git_success(['annex', 'init'], capture_output=True)
             ra = AnnexRepo(self._repoannexdir)
             if 'type=web' in self.initremote_params:
                 self._init_repoannex_type_web(ra)
@@ -620,8 +623,15 @@ class RepoAnnexGitRemote(object):
             # otherwise we can end up in a conflict situation where the mirror
             # points to 'master' (or something else) and the source actually
             # has 'main' (or something different)
-            src_head_ref = self.repo.call_git(['symbolic-ref', 'HEAD']).strip()
-            mr.call_git(['symbolic-ref', 'HEAD', src_head_ref])
+            src_head_ref = call_git_oneline(
+                ['symbolic-ref', 'HEAD'],
+     
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Consolidate filesystem directory traversers into a shared layered implementation
      Consolidate "traverser" from gooey and metalad into a common implementation
This implementation should be layered, to be useful for listing, but also status/diff reporting.

It should also be benchmarked, relative to git-lsdir and and git-ls-tree.

Ping @christian-monch 
      Way: implementing layered git traversal and structured status results
      - Identify the existing filesystem and git traversal abstractions needing consolidation and plan a layered design separating raw git subprocess execution, diff/status iterators, and command-level result reporting.
      - Create a unified result reporting hierarchy using dataclasses (`CommandResult` and command-specific subclasses like `StatusResult`) supporting dict-like access for legacy result handlers.
      - Implement low-level git command runners and layered iterators (such as `iter_gitstatus` and `iter_gitdiff`) to stream structured git state items.
      - Implement the status command interface consuming the layered git iterators, mapping git diff and tree item states into structured command results.
      - Register the new status command into the command suite.
      - Execute unit tests covering git runners, diff/status iterators, and command result data models.
  [2] Goal: Implement non-in-place bitwise or operation for AltConstraints
      Implement `AltConstraints.__or__` not `__ior__`
This is the sibling of https://github.com/datalad/datalad/issues/7164.

I think the real issue is that the current implementation matches the semantics of `__ior__()` and not `__or__()`.

And this needs to be fixed.
      Way: implement non-in-place logical operators for constraint compositions
      - Inspect constraint classes implementing `__or__` and `__and__` methods to check whether they mutate `self.constraints` and return `self`.
      - Write or identify tests verifying that combining constraints with `|` or `&` returns a new instance without mutating existing instances.
      - Update constraint classes (e.g., renaming `Constraints` to `AllOf` and `AltConstraints` to `AnyOf` while preserving aliases) and modify `__or__` and `__and__` to copy existing constraints, append or extend into a new list, and instantiate a new object rather than mutating in place.
      - Update base constraint factory methods (`Constraint.__and__` and `Constraint.__or__`) and package exports to use the updated class definitions.
      - Update any internal references or imports that relied on the old classes to use the updated class names or aliases.
      - Run the constraint test suite to verify immutability of constraint composition and backward compatibility.
  [3] Goal: Standardize and document item name path conventions for collection iterators
      Double check and document (if needed) `.name` conventions for `iter_collections`
I cannot put my finger on it, but I feel the nature of the value of `.name` for a reported item is non-uniform.

I am not aware of a documentation for the convention, if how iterators should behave.

The `.name` sho
      Way: standardizing collection iterator item name types and path properties
      - Inspect the base item classes and collection iterator item dataclasses to identify discrepancies between raw string identifiers (such as archive members or git paths) and PurePath/Path instances.
      - Update PathBasedItem in iter_collections/utils.py to define a cached_property `path` returning PurePath(self.name), and FileSystemItem to provide a cached_property `link_target_path`.
      - Update format-specific items like TarfileItem and GitTreeItem to store `name` (and `link_target`) as raw `str` and provide cached properties returning PurePosixPath instances.
      - Ensure worktree items override `name` as `PurePath` and update yield docstrings across iterators (annexworktree, directory, gittree, gitworktree, tarfile) documenting their path and name conventions.
      - Run collection iterator and ls file collection test suites to verify that item name conventions and path properties function properly across archive and git formats.

=== TASK asottile__all-repos-138 | repo asottile/all-repos | python
--- Issue
Cloning does not work for GitHub Enterprise installations with custom domain
Looks like `github.com` is hardcoded in the repo url:

https://github.com/asottile/all-repos/blob/651ab491d1c3a799f677c107c2edde561364c013/all_repos/github_api.py#L52

I could get the command to work by changing this to my custom domain name.

Am I doing something wrong? We should add an option to allow setting this?
--- Real fix (for your judgement only)
diff --git a/all_repos/github_api.py b/all_repos/github_api.py
index b55f91c..2ac931d 100644
--- a/all_repos/github_api.py
+++ b/all_repos/github_api.py
@@ -44,12 +44,19 @@ def get_all(url: str, **kwargs: Any) -> List[Dict[str, Any]]:
     return ret
 
 
+def _strip_trailing_dot_git(ssh_url: str) -> str:
+    if ssh_url.endswith('.git'):
+        return ssh_url[:-1 * len('.git')]
+    else:
+        return ssh_url
+
+
 def filter_repos(
         repos: List[Dict[str, Any]], *,
         forks: bool, private: bool, collaborator: bool, archived: bool,
 ) -> Dict[str, str]:
     return {
-        repo['full_name']: 'git@github.com:{}'.format(repo['full_name'])
+        repo['full_name']: _strip_trailing_dot_git(repo['ssh_url'])
         for repo in repos
         if (
             (forks or not repo['fork']) and

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Skip archived repositories by default with an option to include them
      github, github_org: don't clone archived repos by default
and have an `archived: true` option for enabling them
      Way: Filter out archived GitHub repositories by default with opt-in setting
      - Locate repository filtering functions and settings classes used by GitHub repository sources.
      - Update the repository filtering function to accept an `archived` boolean keyword argument and filter repos with `(archived or not repo['archived'])`.
      - Add `archived: bool = False` to the configuration `Settings` classes for GitHub user and organization sources, and pass `archived=settings.archived` to the repository filter.
      - Run test suites covering GitHub repository listing to confirm archived repositories are skipped unless explicitly enabled.
  [2] Goal: Add a command-line tool to run sed replacements across repositories
      Make an `all-repos-sed`
Sometimes I don't want to write the full gamut of a fixer and such and really just want some nice `sed -i`.

I think the interface would be something like:

```
$ all-repos-sed 's/foo/bar/g' -- baz.f
```

And this tool would make a fixer which does essentially (for ea
      Way: Add git-aware sed autofix CLI tool
      - Create a helper function for splitting null-byte-delimited bytes output from git ls-files into lists, safely returning an empty list on empty inputs.
      - Update existing ls-files logic to use the new null-splitting helper instead of inline splitting.
      - Implement the sed CLI module using autofix infrastructure: define find_repos to filter repositories containing matching files via `git ls-files`, apply_fix to run `sed -i` on matched files, construct default commit messages with shell-quoted commands, and handle CLI arguments including pattern, filename glob, and extended regex.
      - Register the new CLI executable entry point in setup packaging configuration.
      - Run test suite to verify the sed fixer and null-split utility function across matching repositories.
  [3] Goal: Enable tracking and checkout of remote branches in cloned repositories
      remote branches
After running the `all-repos-clone` command, I am not able to checkout any remote branches for the cloned repositories.

I have tried running `git fetch --all` though that does not do anything.

_(discussed briefly on stream)_
      Way: Configurable git fetch refspec for all remote branches
      - Identify the configuration schema and the repository cloning/fetching routines responsible for setting up remotes and fetching branches.
      - Add an `all_branches` boolean field (defaulting to `False`) to the configuration object and parser.
      - Update the fetch helper to accept `all_branches`; when true, set `remote.origin.fetch` config to `+refs/heads/*:refs/remotes/origin/*` instead of `remote set-branches origin <branch>`, then run `git fetch origin`.
      - Pass the `all_branches` configuration flag to the worker function in the clone runner using `functools.partial`.
      - Write unit tests covering clone behavior when `all_branches` is set to true and verify all branches are fetched.