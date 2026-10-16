

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
   
--- Kel's top 3
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

=== TASK macisamuele__language-formatters-pre-commit-hooks-160 | repo macisamuele/language-formatters-pre-commit-hooks | python
--- Issue
Allow trailing comma in TOML
Could trailing commas for TOML be configurable? I'd rather keep them (in fact, enforce them). Trailing commas lead to better git diff.
--- Real fix (for your judgement only)
diff --git a/language_formatters_pre_commit_hooks/pretty_format_toml.py b/language_formatters_pre_commit_hooks/pretty_format_toml.py
index 6b41911..17f0309 100644
--- a/language_formatters_pre_commit_hooks/pretty_format_toml.py
+++ b/language_formatters_pre_commit_hooks/pretty_format_toml.py
@@ -20,8 +20,25 @@ def pretty_format_toml(argv: typing.Optional[typing.List[str]] = None) -> int:
         dest="autofix",
         help="Automatically fixes encountered not-pretty-formatted files",
     )
-
+    parser.add_argument(
+        "--indent",
+        type=int,
+        default="2",
+        help="The number of spaces to be used as delimiter for indentation level (Default: %(default)s)",
+    )
     parser.add_argument("filenames", nargs="*", help="Filenames to fix")
+    parser.add_argument(
+        "--trailing-commas",
+        action="store_true",
+        dest="trailing_commas",
+        help="Add trailing commas to inline arrays",
+    )
+    parser.add_argument(
+        "--no-sort",
+        action="store_true",
+        dest="no_sort",
+        help="Don't sort keys",
+    )
     args = parser.parse_args(argv)
 
     status = 0
@@ -39,11 +56,11 @@ def pretty_format_toml(argv: typing.Optional[typing.List[str]] = None) -> int:
                     inline=True,
                     block=True,
                 ),
-                sort_config=SortConfiguration(tables=True),
+                sort_config=SortConfiguration(tables=not args.no_sort, table_keys=not args.no_sort),
                 format_config=FormattingConfiguration(
                     spaces_before_inline_comment=2,
-                    spaces_indent_inline_array=2,
-                    trailing_comma_inline_array=False,
+                    spaces_indent_inline_array=args.indent,
+                    trailing_comma_inline_array=args.trailing_commas,
                 ),
             ).sorted()
 

--- Kel's top 3
  [1] Goal: Allow trailing commas when parsing JSON
      Feature request: Ignore trailing commas
A common workflow of mine is to comment/uncomment items in a JSON file. This fails if I comment out the last item in a list/dictionary because the new last item now has a trailing comma. Is there any appetite for allowing trailing commas? Would a PR that inclu
      Way: Allow trailing commas in Lark JSON grammar and AST reconstruction
      - Locate the grammar definition and AST processing pipeline where JSON parsing and reconstruction occur.
      - Update the grammar rules for arrays and objects to accept an optional trailing comma token (e.g. `TRAILING_COMMA?`) and define the `TRAILING_COMMA` terminal.
      - Implement a helper function that traverses the parsed Lark AST and filters out `TRAILING_COMMA` tokens before passing the tree to the serializer/reconstructor.
      - Wrap the parsed AST with the trailing comma removal helper prior to reconstruct/serialization in the deserialization functions.
      - Run the test suite to verify JSON with trailing commas in lists and objects parses successfully without syntax errors.
  [2] Goal: Support trailing commas in parenthesized multi-item with statements
      --py39-plus: multi-with-statements
Add support for trailing commas for this:

```diff
 with (
     foo as bar,
-    baz as womp
+    baz as womp,
 ):
     # ...
```
      Way: Add trailing comma rewriting for parenthesized with statements
      - Identify AST nodes representing with statements (ast.With) and inspect tokenize-rt tokens following the 'with' keyword.
      - Register an AST visitor for ast.With under Python 3.9+ environments that yields token transformation callbacks keyed by node offset.
      - In the token callback, advance past optional unimportant whitespace to check for an opening parenthesis, compute the enclosing brace range, and ensure the closing brace immediately precedes the ':' token before applying comma adjustments.
      - Verify trailing comma handling across multi-line parenthesized with statements including adding, removing, and indentation variations.
  [3] Goal: Remove trailing comma when deleting the last element of an inline table
      remove last element of an Inline Table leaves a comma
removing the last element of an Inline Table leaves a comma
this fail :
```
import tomlkit

to_parse='entry = {  version = "11.0", registry = "reg", path="/tmp/foo" }'
data = tomlkit.parse(to_parse)
data["entry"].remove("path")
assert dat
      Way: Omit trailing separator in inline table rendering based on last non-trivia item index
      - Locate the string rendering method for inline tables where key-value pairs and trivia are formatted.
      - Reproduce the trailing comma bug by parsing an inline table, removing its last key, and asserting string serialization has no trailing comma.
      - Find the index of the last key-value item (ignoring trailing trivia or deleted entries where key is None) in the internal body array.
      - Update the comma appending logic in the rendering loop to check if the current iteration index is strictly less than `last_item_idx` rather than checking against total body length.
      - Run test suite to verify element removal on inline tables produces clean output without trailing commas.

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
--- Kel's top 3
  [1] Goal: Support image digests when parsing container image URLs
      _parse_image_url() doesn't support image digests
A recent deploy of a Hive operator failed with:

`Image does not exist: quay.io/****/pagerduty-operator-registry@sha256:bc1ed82a75f2ca160225b8281c50b7074e7678c2a1f61b1fb298e545b455925e`

The image exists:

```
docker pull quay.io/****/pagerduty
      Way: support container image digest parsing and reference resolution
      - Examine regex and parsing logic in container URL parser to see how it handles '@' and digests vs ':' tags.
      - Define a custom exception `NoTagForImageByDigest` for when tag-specific operations are performed on digest-only images.
      - Update the URL parsing regex in `_parse_image_url()` to parse `@sha256:...` digest groups and avoid assuming default `latest` tag when a digest is present.
      - Update image initialization to pre-cache `_cache_digest` if parsed from URL, use `tag or digest` when fetching manifests, raise `NoTagForImageByDigest` in `url_tag` if `tag` is None, and format `__str__` using `url_digest` when `tag` is None.
      - Run test suite to verify container parsing and string representations with image digests pass.
  [2] Goal: Parse Docker image versions from artifact metadata or path properties
      Formatting Question - KeepLatestNVersionImagesByProperty
Thank you for developing this wonderful tool, it has been very useful.
Having said that, i am having some issues getting some of the docker rules to work.

For example, my yaml file looks like this:

```yaml
artifactory-cleanup:
  serve
      Way: support 'v'-prefixed version strings in Docker cleanup version parser
      - Identify the version extraction method `get_version` in the Docker cleanup rule responsible for converting matched version regex strings into comparable numeric tuples.
      - Check how `get_version` processes regex matches and note that `map(int, version_str.split('.'))` raises `ValueError` if a prefix like 'v' remains in `version_str`.
      - In `get_version`, check if `version_str.startswith('v')`; if so, strip the leading 'v', parse the remaining dot-separated numbers into integers, and return a tuple prefixed with 'v' and the integers (`tuple(['v'] + list(map(int, ...)))`).
      - Update documentation to clarify how custom regular expressions can include optional 'v' prefixes (e.g., `(^v?\d+\.\d+\.\d+$)`).
      - Run Docker rule test suite to verify 'v'-prefixed tags and standard numeric tags parse and filter properly.
  [3] Goal: Add support for parsing YAML sequence nodes into objects
      Consider Sequence Support?
```python
# coding: utf-8
from yamlable import YamlObject2
import yaml

class Base:
    def __init__(self, *args, **kwargs):
        print("here", args, kwargs)
        self.args = args
        self.kwargs = kwargs

    def __repr__(self):
        return f"{sel
      Way: support yaml sequences and scalars in object loading
      - Inspect YAML node parsing in the base class and constructor dispatch to identify where nodes are assumed to be mappings.
      - Create a reproducing test attempting to deserialize a YAML sequence or scalar into a decorated YAML object.
      - Add hook methods (`__from_yaml_sequence__`, `__from_yaml_scalar__`, and corresponding codec methods) with default constructor implementations, and update node loading logic to check whether the incoming node is a MappingNode, SequenceNode, or ScalarNode, dispatching to the appropriate constructor method.
      - Run the new and existing test suite to verify sequence and scalar nodes parse properly into objects.

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

--- Kel's top 3
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
--- Kel's top 3
  [1] Goal: Restore SQL profiling report output in console logging
      No report shown in console in 1.1.1
**Describe the bug**
A clear and concise description of what the bug is.

I have updated to 1.1.1 today.
The SQL report no longer shows in console output.

I am using a mac but not sure if it has anything to do with it.
The setup to enable app - wide consol
      Way: Select platform-appropriate timer for query profiling
      - Locate query timing calls in the profiler logic that capture timestamps before and after cursor execution.
      - Define a platform-conditional timer helper (`time.perf_counter` on Windows, `time.time` on Unix platforms) to ensure consistent timestamp calculation across operating systems.
      - Replace direct calls to `time.perf_counter` in `_before_cursor_execute` and `_after_cursor_execute` with the platform timer helper.
      - Run the profiler test suite to verify cursor execution timestamps are recorded correctly.
  [2] Goal: Ignore deleted files when running diff-quality linter
      Error using diff-quality with deleted files
When running `diff-quality --violations sqlfluff` pre-commit when I have a file staged that has been deleted I get the following error
```
Quality tool not installed: 'sqlfluff'
```

Digging into this a little bit more, the code actually breaks in the
      Way: ignore nonexistent files in linter path traversal for diff tools
      - Locate where paths are resolved and linted in the linter class and identify entry points such as diff plugins and CLI commands.
      - Add an optional boolean parameter `ignore_non_existent_files` (defaulting to False) across path resolution methods (`paths_from_path`, `lint_path`, and `lint_paths`).
      - Update `paths_from_path` to return an empty list when `ignore_non_existent_files` is True instead of raising an IOError if `os.path.exists(path)` is False.
      - Pass `ignore_non_existent_files=True` when invoking `linter.lint_path` inside the diff-quality plugin's violation reporter.
      - Run test suite verifying that nonexistent paths return empty results when configured to ignore them and raise an error otherwise.
  [3] Goal: Write fixed files sequentially instead of batching writes at the end
      `fix` per file linted instead of at the end
### Search before asking

- [X] I searched the [issues](https://github.com/sqlfluff/sqlfluff/issues) and found no similar issues.


### Description

I am just testing sqlfluff on a small example project.
We have configured it as part of `pre-commit`.

`
      Way: Sequential in-flight file fixing during multi-file linting
      - Identify CLI fix commands and linter runner logic where files are linted and writes are batched at the end.
      - Trace `lint_paths`, `LintedDir.persist_changes`, and `LintedFile.persist_tree` to see how fixes are collected and written.
      - Move formatting dispatch logic from directory persistence down to `LintedFile.persist_tree` so individual files can write and report status independently.
      - Add safety check method `discard_fixes_if_tmp_or_prs_errors` on `LintedFile` to strip fixes when parse/template errors exist.
      - Update `Linter.lint_paths` to take `apply_fixes`, `fixed_file_suffix`, and `fix_even_unparsable` flags, applying file fixes as each file finishes linting when `apply_fixes` is True.
      - Update CLI `fix` command to pass `apply_fixes=force` directly to `lint_paths` and skip the post-linting `do_fixes` batch step when force mode is active.

=== TASK microsoft__electionguard-python-756 | repo microsoft/electionguard-python | python
--- Issue
🐞 Error Occurs Exporting Ballots if No Spoiled Ballots
### Is there an existing issue for this?

- [X] I have searched the existing issues

### Current Behavior

If there are no spoiled ballots and you export an election record it will produce this error:

```
DEBUG:eel_log_service.py.debug:#L35: getting ballots for 62ff94aa397efaddb14ac8db
Traceback (most recent call last):
  File "C:\dev\ElectionGuard\electionguard-python\.venv\lib\site-packages\eel\__init__.py", line 281, in _process_message
    return_val = _exposed_functions[message['name']](*message['args'])
  File "C:\dev\ElectionGuard\electionguard-python\src\electionguard_gui\components\export_election_record_component.py", line 64, in export_election_record
    spoiled_ballots = decryption.get_plaintext_spoiled_ballots()
  File "C:\dev\ElectionGuard\electionguard-python\src\electionguard_gui\models\decryption_dto.py", line 136, in get_plaintext_spoiled_ballots
    raise ValueError("No plaintext spoiled ballots found")
ValueError: No plaintext spoiled ballots found
```

### Expected Behavior

_No response_

### Steps To Reproduce

_No response_

### Environment

```markdown
- OS:
```


### Anything else?

_No response_
--- Real fix (for your judgement only)
diff --git a/src/electionguard_gui/models/decryption_dto.py b/src/electionguard_gui/models/decryption_dto.py
index 9326cf7..2d12795 100644
--- a/src/electionguard_gui/models/decryption_dto.py
+++ b/src/electionguard_gui/models/decryption_dto.py
@@ -49,7 +49,7 @@ class DecryptionDto:
     can_join: Optional[bool]
     decryption_shares: list[Any]
     plaintext_tally: Optional[str]
-    plaintext_spoiled_ballots: Optional[dict[str, str]]
+    plaintext_spoiled_ballots: dict[str, str]
     lagrange_coefficients: Optional[str]
     ciphertext_tally: Optional[str]
     completed_at_utc: Optional[datetime]
@@ -69,7 +69,9 @@ class DecryptionDto:
         self.guardians_joined = _get_list(decryption, "guardians_joined")
         self.decryption_shares = _get_list(decryption, "decryption_shares")
         self.plaintext_tally = decryption.get("plaintext_tally")
-        self.plaintext_spoiled_ballots = decryption.get("plaintext_spoiled_ballots")
+        self.plaintext_spoiled_ballots = _get_dict(
+            decryption, "plaintext_spoiled_ballots"
+        )
         self.lagrange_coefficients = decryption.get("lagrange_coefficients")
         self.ciphertext_tally = decryption.get("ciphertext_tally")
         self.completed_at_utc = decryption.get("completed_at")
@@ -132,8 +134,6 @@ class DecryptionDto:
         return from_raw(PlaintextTally, self.plaintext_tally)
 
     def get_plaintext_spoiled_ballots(self) -> list[PlaintextTally]:
-        if not self.plaintext_spoiled_ballots:
-            raise ValueError("No plaintext spoiled ballots found")
         return [
             from_raw(PlaintextTally, tally)
             for tally in self.plaintext_spoiled_ballots.values()
@@ -157,6 +157,13 @@ def _get_list(decryption: dict[str, Any], name: str) -> list:
     return []
 
 
+def _get_dict(decryption: dict[str, Any], name: str) -> dict:
+    value = decryption.get(name)
+    if value:
+        return dict(value)
+    return {}
+
+
 def _get_int(decryption: dict[str, Any
--- Kel's top 3
  [1] Goal: Ensure consistent manifest and ballot serialization across C++ and Python
      Fix Serialization Issues
## Bug Report

**Expected Behavior**
The serialization behavior of manifests and ballots between C++ and Python is inconsistent.
- Dates are handled inconsistently
- hashing of numbers is handled inconsistently

**Actual Behavior**
_A clear and concise description of
      Way: Aligning cryptographic hashing and serialization across language implementations
      - Locate where cryptographic constants and large integers are hashed during election context creation.
      - Convert large mathematical constants (like P, Q, G) to unchecked ElementModP or ElementModQ objects before hashing them, ensuring consistent representation with other platforms.
      - Locate where dates and timestamps are formatted or serialized in the manifest and encryption device modules.
      - Implement standard UTC timestamp generation in the encryption device and ensure date serialization uses ISO date strings instead of ticks.
      - Run the property tests to verify that encryption, decryption, and serialization are consistent and correct.
  [2] Goal: Ensure deserialized ElgamalCiphertext objects can be compared for equality
      🐞 Deserializing SubmittedBallot makes ElgamalCiphertext comparison fail
### Is there an existing issue for this?

- [X] I have searched the existing issues

### Current Behavior

Since the new serialization has been merged, deserialization fails to reconstruct `SubmittedBallot`s

I'm trying to wri
      Way: Implement explicit equality check for ciphertext dataclasses to support Pydantic deserialized instances
      - Identify where ciphertext equality comparisons fail when using deserialized Pydantic dynamic subclass instances.
      - Reproduce equality failure between standard ciphertext instances and deserialized objects.
      - Implement explicit __eq__ method on ElGamalCiphertext checking for isinstance and comparing pad and data fields.
      - Run the integration tests to confirm deserialized ciphertext instances now compare correctly.
  [3] Goal: Fix TypeError when masking secret diff values containing None
      TypeError on SealedSecret
Reported in PR #78 

error below for e.g. `SealedSecret.v1alpha1.bitnami.com`

```
Traceback (most recent call last):
  File "/Users/po/src/kubediff/kubediff", line 48, in <module>
    main()
  File "/Users/po/src/kubediff/kubediff", line 42, in main
    failed = c
      Way: Handle None or non-sequence values safely when masking diff arguments for secrets
      - Locate the string formatting logic in the difference representation class where secret values are masked with asterisks.
      - Define a masking helper function that attempts to convert a string into an equal-length string of asterisks using len(), but catches TypeError and returns the original value if it lacks a length (such as None).
      - Replace direct len() calls on difference arguments with the new masking helper function in the secret formatting logic.
      - Run test suite to verify formatting of secret differences containing None values passes without raising TypeError.

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
 

--- Kel's top 3
  [1] Goal: Enable pre-commit to manage unstaged files despite post-checkout hooks
      post-checkout hook prevents pre-commit hooks from working with unstaged files
Hi there!

Most important thing first: pre-commit is great, it makes managing git hooks trivial and when combined with static analysis hooks, helps our team immeasurably - **thanks for all the hard work**.

We have pre
      Way: Skip internal post-checkout hook execution during staging
      - Understand that pre-commit's mechanism for clearing unstaged files involves an internal `git checkout` command, which can inadvertently trigger user-defined `post-checkout` hooks.
      - Locate the code responsible for clearing unstaged files, specifically the `git checkout` calls within `pre_commit/staged_files_only.py`.
      - Modify the internal `git checkout` calls in `pre_commit/staged_files_only.py` to set a custom environment variable, `_PRE_COMMIT_SKIP_POST_CHECKOUT=1`, when executing these commands. This signals that the `git checkout` is an internal operation.
      - Modify the `run` command in `pre_commit/commands/run.py` to check for the `_PRE_COMMIT_SKIP_POST_CHECKOUT` environment variable. If it's set and the current hook stage is `post-checkout`, the `run` command should exit immediately with a success status (0), effectively skipping the user's `post-checkout` hook.
      - Verify that pre-commit can now successfully manage unstaged files and commit, even when a failing `post-checkout` hook is installed, because the internal `git checkout` no longer triggers the user's hook.
  [2] Goal: Provide option to force check-merge-conflict hook execution
      Option to force run check-merge-conflict
check-merge-conflict only runs when it detects merge/rebase in progress.
This means it will always pass on CI setups.

Am not sure if this should be fixed specifically for check-merge-conflict or make a generic CI functionality available via [pre-commit](h
      Way: Add option to force execution of a conditional hook
      - Identify the hook's conditional execution logic that prevents it from running its main checks under certain circumstances (e.g., not being in a merge state).
      - Add a new boolean command-line argument to the hook's argument parser, allowing users to explicitly request the hook to assume the condition is met.
      - Modify the conditional execution logic to bypass the original condition if the new 'assume' argument is provided.
      - Verify that the hook now executes its main logic when the new 'assume' argument is passed, even if the original condition is not met.
  [3] Goal: Make pre-commit detect binary file changes despite Git textconv
      Binary file change by hook not detected due to Git textconv
### search you tried in the issue tracker

textconv

### describe your issue

I’ve set up Git to use `exiftool` as a text conversion filter for JPEGs, with this in `~/.config/git/config`:

```ini
[diff "exiftool"]
    textconv = exiftoo
      Way: Disable Git textconv for accurate change detection in pre-commit
      - Identify the internal Git command used by pre-commit to detect file changes.
      - Reproduce the issue by configuring Git with a `textconv` filter for a binary file type (e.g., JPEG) and a pre-commit hook that modifies such a file. Run the hook and observe that pre-commit reports no changes.
      - Modify the `_get_diff` function to include the `--no-textconv` flag in the `git diff` command. This prevents Git's text conversion from masking binary changes.
      - Modify the `_has_unstaged_config` function to use `--quiet` instead of `--exit-code` for its `git diff` command. This is a more direct way to check for changes without outputting the diff.
      - Run the relevant tests to ensure the fix correctly detects changes and does not introduce regressions.

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
--- Kel's top 3
  [1] Goal: Fix nested field exclusion with dot notation on subsequent schema dumps
      Defining fields with dot notations in Meta.exclude only works when initialising first time
Demonstration of the issue
``` python
from marshmallow import Schema, fields


class AuthorSchema(Schema):
    first_name = fields.String(required=True)
    last_name = fields.String(required=True)


      Way: Preserve schema meta exclude options across multiple dumps by avoiding in-place mutation
      - Locate where schema exclusion options are initialized and applied to nested fields during serialization.
      - Modify the schema initialization to merge the class-level `Meta.exclude` options with instance-level `exclude` options into `self.exclude` as a set, rather than mutating `self.opts.exclude` in-place during field filtering.
      - Update the field filtering logic to apply nested exclusions using the merged `self.exclude` and remove the destructive in-place modification of `self.opts.exclude` which caused subsequent dumps to lose the nested exclusion rules.
      - Run the test suite to verify that nested field exclusions with dot notation persist across multiple schema dumps.
  [2] Goal: Propagate dotted only and exclude parameters to nested schema instances
      Dotted `only` and `exclude` not working for nested schema instances
#1229 fixed propagation of `only` and `exclude` for nested schema classes but no instances.
Dotted strings passed to `only` and `exclude` are not respected when a schema instance is nested.


```python
from marshmallow import S
      Way: propagate dotted only and exclude options to nested schema instances
      - Locate where nested schema fields are initialized and where schema options like only and exclude are applied.
      - Modify the nested field initialization logic to deepcopy the nested schema instance, merge the parent's only/exclude options with the nested schema's existing options, and re-initialize its fields.
      - Refactor the schema field initialization method to update fields, load_fields, and dump_fields in-place so that re-initializing fields on an existing schema instance correctly updates all internal field mappings.
      - Run the test suite to verify that dotted only and exclude parameters are correctly respected when using nested schema instances.
  [3] Goal: Ensure only and exclude arguments work with List of Nested fields
      `only` argument inconsistent between Nested(S, many=True) and List(Nested(S))
```python
from pprint import pprint

from marshmallow import Schema
from marshmallow.fields import Integer, List, Nested, String


class Child(Schema):
    name = String()
    age = Integer()


class Family(Sch
      Way: Propagate schema filtering arguments to nested container fields
      - Locate container field implementations (such as List and Mapping/Dict fields) and check how they initialize and bind their inner container fields to schema fields.
      - Reproduce the bug by instantiating a schema with a List or Dict wrapping a Nested field, filtering with `only` or `exclude`, and checking if nested properties are filtered.
      - In List and Mapping field initialization, copy `only` and `exclude` from the inner `Nested` field onto the container. During `_bind_to_schema`, forward the container's `only` and `exclude` properties down into the inner `Nested` container field.
      - Run the targeted field tests to ensure schema filtering attributes propagate properly through List and Mapping fields.

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
--- Kel's top 3
  [1] Goal: Support configuring API version compatibility in configuration files
      RFC Perfect backwards compatibility policy
Curious to hear thoughts on this one - especially from @baggiponte 

Narwhals follows a strict subset of the Polars API. What happens if/when some part of this subset gets changed in Polars itself?

For example:
- pre 1.0, Polars coalesces overlapping 
      Way: provide versioned stable namespaces for backwards compatibility
      - Design and document a backwards compatibility policy using versioned namespaces (e.g., stable.v1) instead of configuration files to avoid parsing overhead and complexity.
      - Create a stable namespace package and versioned modules (e.g., stable/v1.py) that expose the stable subset of the API, wrapping or aliasing the main implementation.
      - Expose the stable namespace in the package's main entry point and document its usage in the README and documentation.
      - Run the test suite to ensure that existing tests pass and the stable namespace is correctly integrated.
  [2] Goal: Update the default Linux wheel platform for PyPy to manylinux2014
      Move the default for PyPy to manylinux2014
@mattip suggested moving to manylinux2014, since pypy is looking at dropping manylinux2010 in the future. cibuildwheel 2.3 (which will add pypy3.8) might be a good place to do it.
      Way: Updating default Linux build container images to newer manylinux standard
      - Identify configuration files, code paths, and documentation defining default Linux container images across target architectures.
      - Update default container configuration settings in defaults configuration (such as defaults.toml) to map x86_64 and i686 architectures (both CPython and PyPy) to manylinux2014.
      - Update image fallback resolution logic in options processing so that when no image configuration value is supplied, it resolves to manylinux2014 directly.
      - Update documentation examples and descriptions to reflect manylinux2014 as the default image standard across supported architectures.
      - Run the option and configuration unit tests to confirm default image resolution matches the new manylinux2014 targets.
  [3] Goal: Support legacy genome_dir configuration key for backward compatibility
      Please provide genomes_dir error after upgrade to 0.8.1
In the config `genome_dir` seems to be renamed to `genomes_dir`. When I upgrade my genomepy version it does not add `genomes_dir` to the config, and gives me this error.
```
Traceback (most recent call last):
  File "/home/sande/anaconda3/en
      Way: Fall back to legacy configuration key for directory resolution
      - Search for where the configuration key `genomes_dir` is retrieved across utility and configuration modules.
      - Update `get_genomes_dir` in `genomepy/utils.py` to check for `genomes_dir` first, falling back to legacy `genome_dir` before raising `ConfigError`.
      - Check `gzip_and_name` in `genomepy/utils.py` to remove the uncompressed source file with `os.unlink` after creating the `.gz` file.
      - Run the utils and plugin test suite to verify configuration fallback and file operations.

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
--- Kel's top 3
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
  [2] Goal: Support fractional and negative exponents in unit definitions
      Unsupported operation for units
Maybe this is out of scope, but I think it would be nice if this worked:

```
In [1]: from gpkit import Variable

In [2]: x = Variable('x', 's^0.5/m^0.5')

In [3]: y = Variable('y', '(m/s)^-0.5')
----------------------------------------------------------------
      Way: Restricting dimensionless hyphen replacement in unit string parsing
      - Trace unit string initialization in variable creation to identify where unit string expressions are altered before being parsed by the unit registry.
      - Reproduce the error by instantiating a variable with a unit string containing negative exponents, such as '(m/s)^-0.5'.
      - In the variable key class, replace the substring replacement of '-' with an exact equality check so only standalone '-' strings are changed to 'dimensionless'.
      - Run the unit parsing tests to confirm variables with negative exponents parse properly and dimensionless hyphens are still handled.
  [3] Goal: Correct unit conversion for inverse speed quantities
      Unit conversions with time/length are wrong
* unyt version: 1.0.7
* Python version: 3.6.8
* Operating System: macOS 10.14.2

### Description

When I create quantities with units of `time/length` the unit conversions (e.g., `in_mks` or `in_cgs`) always seem to produce a nonsensical quantity wit
      Way: Distinguish electromagnetic units from dimensionally equivalent non-EM units during conversion
      - Identify the unit conversion logic for electromagnetic (EM) quantities, specifically the `em_conversions` mapping and the `_check_em_conversion` function.
      - Reproduce the incorrect unit conversion for inverse speed quantities (e.g., 's/m') to 'ohm' using `in_cgs()` or `in_mks()`.
      - Modify the `em_conversions` dictionary keys to include both the unit symbol and its dimensions, instead of just the dimensions. This prevents non-EM units that happen to share dimensions with EM units from being incorrectly identified as EM units.
      - Implement a helper function `_split_prefix` in `unyt/unit_registry.py` to correctly separate a unit symbol into its SI prefix and base unit. This function should handle single and two-character prefixes and ensure the base unit is prefixable.
      - Update the `_check_em_conversion` function in `unyt/unit_object.py` to use `_split_prefix` to extract the base unit symbol and then use this symbol along with the dimensions for lookup in the modified `em_conversions` dictionary. Ensure that prefixes are re-applied to the converted unit symbol.
      - Update the `_lookup_unit_symbol` function in `unyt/unit_registry.py` to also utilize the new `_split_prefix` function for consistent prefix handling during unit symbol lookups.

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

--- Kel's top 3
  [1] Goal: Resolve conflicting CLI command name warning when registering API endpoints
      CLI: GET /aliases/{alias} endpoint is inaccessible
The CLI emitted a confusing `UserWarning` of `UserWarning: conflicting command name: get` when I used the terminal command of `civis files download <file-id> <filename>`. This is the line that triggered the said `UserWarning`:

https://github.com/
      Way: aligning CLI command name generation with client resource method parsing
      - Find where CLI commands are mapped from API paths and methods and inspect how command names are generated.
      - Reproduce the command naming collision by invoking CLI initialization or testing endpoint paths that collide when path parameters are stripped.
      - Replace custom regex stripping and prefixing logic in make_operation_name with the existing parse_method_name utility, converting underscores to dashes.
      - Update collision warning messages to include both command name and the offending API path for better diagnostic reporting.
      - Run CLI unit tests including test_generate_cli_civis and endpoint naming parameterized tests.
  [2] Goal: Prevent duplicate details in configuration error messages
      Double crap in error message

```python
# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved
from dataclasses import dataclass

import hydra
from hydra.core.config_store import ConfigStore


@dataclass
class MySQLConfig:
    driver: str = "mysql"
    host: str = "localh
      Way: prevent duplicate details in nested configuration exception formatting
      - Locate the exception formatting and raising logic, typically in a utility function that wraps exceptions with configuration context.
      - Modify the exception raising utility to detect if the causing exception is already an initialized configuration exception. If it is, reuse or override it directly instead of wrapping it again and duplicating the context details.
      - Remove redundant try-except blocks in container operations (like dict/list access, pop, and merge) that catch and re-raise already formatted configuration exceptions.
      - Run the test suite to verify that configuration errors no longer print duplicate context blocks (such as full_key, reference_type, and object_type).
  [3] Goal: Make temporary cluster configuration paths unique per user to prevent permission conflicts
      [K8S] `sky local up` failed on a multi-user system
<!-- Describe the bug report / feature request here -->

On commit `7f30ce5e`

Currently the `sky local up` will create a config yaml under `/tmp` and does not clean it up after local cluster creation. If another user on the same Linux OS tries 
      Way: scope local cluster config paths per user using user hash
      - Locate where local cluster deployment command is constructed and where temporary cluster configuration files are created.
      - Pass a unique user identifier into the local cluster launch script call.
      - Update the cluster generation and setup scripts to append the unique user identifier or hash to temporary YAML configuration file paths in shared directories like /tmp.
      - Run unit tests verifying storage utility and configuration file generation logic.

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
--- Kel's top 3
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
--- Kel's top 3
  [1] Goal: Trigger pipeline execution when only a single exposure file is present
      Trigger pipeline on only a desi file.
Currently the pipeline is activated when three files are present.  Change this so that only a `desi-{exposure}.fits.fz` file needs to be present.  This requires only changing a configuration file, not any actual code.
      Way: Update daemon expected files configuration for pipeline triggers
      - Search configuration files for the list of expected exposure file patterns that trigger the pipeline daemon.
      - Edit the daemon configuration file to update the `expected_files` setting from multiple file patterns to only `desi-{exposure}.fits.fz`.
      - Update documentation changes log to reflect the new pipeline trigger requirement.
      - Run the daemon unit tests to verify initialization and configuration loading with the updated expected files list.
  [2] Goal: Prevent convert to pipe code action on use expressions
      "Convert to pipe" code action interacts weirdly with `use`
The new "Convert to pipe" code action doesn't properly take into account `use` expressions. For example, given this code:
```gleam
import gleam/result

pub fn main() {
  use x <- result.try(todo)
  Ok(x + 1)
}
```

The Language Server offers
      Way: disable pipe transformation on use expression call targets
      - Locate the language server code action handler for converting expressions to pipe calls (e.g. ConvertToPipe AST visitor).
      - Identify how use expressions and function call targets within use expressions are visited during code action traversal.
      - Modify the AST visitor logic for ConvertToPipe to skip or ignore function calls that are on the right-hand side of a `use` expression so that pipe conversion actions are not offered for them.
      - Run language server test suite to ensure convert-to-pipe is suppressed on use call targets but still works inside use bodies.
  [3] Goal: Allow named pipes as input files for streamable inputs
      Request for cwltool to support named pipes
## Expected Behavior
Some runners such as Toil might implement file streaming with named pipes when `streamable` flag is true:
https://github.com/DataBiosphere/toil/issues/3469

## Actual Behavior
An exception is raised because a named pipe is not a re
      Way: permitting FIFO named pipes as streamable input files
      - Identify where input file existence and regular file checks reject non-regular files during job setup.
      - Add a configuration flag to RuntimeContext (e.g., streaming_allowed defaulted to False) to guard when named pipes should be accepted.
      - In the job file validation loop, allow non-regular files when streaming is permitted by the runtime context, the input definition has streamable enabled, and os.stat mode matches stat.S_ISFIFO.
      - Run test suite to verify named pipe input files succeed under streamable configuration and fail otherwise.

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
--- Kel's top 3
  [1] Goal: Implement asynchronous file opening method for directory filesystem wrappers
      DirFileSystem missing `open_async()` method for proper async operation

```python
import asyncio
import fsspec

async def async_test():
    dirfs = fsspec.filesystem('dir', path='/tmp', asynchrnous=True)
    file = await dirfs.open_async('hello', 'wb')
    await file.close()

asyncio.run(a
      Way: implement asynchronous open delegation on directory filesystem wrappers
      - Identify where directory-prefixing filesystem wrappers delegate calls to an underlying filesystem.
      - Reproduce the issue by instantiating the directory filesystem with async enabled and calling open_async(), verifying that it raises NotImplementedError.
      - Implement open_async on DirFileSystem as an async coroutine that delegates to the underlying fs.open_async using self._join(path) and passing through remaining arguments.
      - Run the targeted directory filesystem tests to verify that open_async successfully opens the joined path.
  [2] Goal: Support time freezing decorators on asyncio test coroutines
      Mark notation does not work when using pytest-asyncio library
Simple test case fails when using both libraries and the mark notation:

```
from freezegun import freeze_time
import datetime
import asyncio
import pytest
import pytest_asyncio

@freeze_time("2012-01-14")
async def test():
   
      Way: support time freezing decorator on asyncio coroutines
      - Identify where the time-freezing decorator inspects target callables and creates synchronous wrapper functions.
      - Create an async wrapper helper that enters the context manager around yielding from the decorated coroutine.
      - Update the main decorator class to check `inspect.iscoroutinefunction` and dispatch coroutines to the async wrapper while falling back gracefully on Python versions lacking coroutine support.
      - Run the asyncio test suite to ensure decorated async test functions freeze time correctly during execution.
  [3] Goal: Await coroutines properly in async REST client requests
      Getting an error coroutine 'AsyncRestClient._request_with_session' was never awaited
### Checklist

- [X] I have looked into the [Readme](https://github.com/auth0/auth0-python#readme) and [Examples](https://github.com/auth0/auth0-python/blob/master/EXAMPLES.md), and have not found a suitable solutio
      Way: await async request helper calls in rest client
      - Locate the async REST client request dispatch method that delegates HTTP operations to session helper methods.
      - Check how `_request_with_session` is called inside `_request`. Notice that `_request_with_session` is an async coroutine function, but its calls inside `_request` are returned directly without `await` and one branch passes `self.session` instead of `self._session`.
      - Update `_request` to `await self._request_with_session(self._session, *args, **kwargs)` when reusing an existing session and `await self._request_with_session(session, *args, **kwargs)` when creating a context session.
      - Run the async client and token verifier test suite to verify requests properly await and return parsed responses.

=== TASK honeycombio__beeline-python-219 | repo honeycombio/beeline-python | python
--- Issue
Categorize span types on send
<!---
Thank you for contributing an idea to this project!

Please see our [OSS process document](https://github.com/honeycombio/home/blob/main/honeycomb-oss-lifecycle-and-practices.md#) to get an idea of how we operate.
--->

**Is your feature request related to a problem? Please describe.**


**Describe the solution you'd like**


**Describe alternatives you've considered**


**Additional context**

--- Real fix (for your judgement only)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index a03dbd3..47a1880 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -12,6 +12,8 @@
 
 ### Enhancements
 
+**NOTE** If you are using the [FileTransmission](https://github.com/honeycombio/libhoney-py/blob/main/libhoney/transmission.py#L448) method and setting a false API key - and still working in Classic mode - you must update the key to be 32 characters in length to keep the same behavior.
+
 - feat: Add Environment & Services support (#213) | [@JamieDanielson](https://github.com/JamieDanielson)
 
 ## 3.2.0 2022-02-10
diff --git a/README.md b/README.md
index 87a6326..af05e2a 100644
--- a/README.md
+++ b/README.md
@@ -14,17 +14,15 @@ Currently, supports Django (>2), Flask, Bottle, and Tornado.
 
 Compatible with Python 3.
 
-## Get in touch
+## Updating to 3.3.0
 
-Please reach out to [support@honeycomb.io](mailto:support@honeycomb.io) or ping
-us with the chat bubble on [our website](https://www.honeycomb.io) for any
-assistance. We also welcome [bug reports](https://github.com/honeycombio/beeline-python/issues).
+Version 3.3.0 added support for Environment & Services, which changes sending behavior based on API Key.
+
+If you are using the [FileTransmission](https://github.com/honeycombio/libhoney-py/blob/main/libhoney/transmission.py#L448) method and setting a false API key - and still working in Classic mode - you must update the key to be 32 characters in length to keep the same behavior.
 
 ## Contributions
 
-Features, bug fixes and other changes to `beeline-python` are gladly accepted. Please
-open issues or a pull request with your change. Remember to add your name to the
-CONTRIBUTORS file!
+Features, bug fixes and other changes to `beeline-python` are gladly accepted.
 
 If you add a new test module, be sure and update `beeline.test_suite` to pick up the new tests.
 
diff --git a/beeline/trace.py b/beeline/trace.py
index 7606102..2c546e9 100644
--- a/beeline/trace.py
+++ b/beeline/trace.py
@@ -101,9 +101,9 @@ cla
--- Kel's top 3
  [1] Goal: Add a custom span name formatter option to HTTP router instrumentation
      otelmux: Add config option for a custom span name formatter
Currently, spans created in the [`otelmux`](https://pkg.go.dev/go.opentelemetry.io/contrib/instrumentation/github.com/gorilla/mux/otelmux) instrumentation module are named after the route template  (or the route regexp if no template is ava
      Way: Add custom span name formatter to HTTP router instrumentation
      - Identify the module responsible for HTTP router instrumentation and span naming. The issue states `otelmux` is the module, and the `ServeHTTP` method within its middleware is where spans are created and named.
      - Introduce a new configurable option to allow users to provide a custom function for generating span names. This involves adding a function type field to the configuration struct (`config`) and a corresponding `WithSpanNameFormatter` option function.
      - Modify the middleware's internal state (`traceware` struct) to store the custom span name formatter. Initialize this formatter in the middleware creation function (`Middleware`), providing a default implementation if none is specified.
      - Update the span creation logic in the `ServeHTTP` method to use the configured `spanNameFormatter`. The formatter should receive the extracted route name (template or regexp) and the `http.Request` object, then its return value should be used as the span name.
      - Run the provided tests to ensure the new functionality works as expected and no regressions were introduced.
  [2] Goal: Automatically name HTTP trace spans using the matched serve mux pattern
      Automatic route/pattern naming for `http.ServeMux`
### Problem Statement

Go recently added more sophisticated pattern matching to `http.ServeMux`, including matching methods and path variables. I think this patterns can make good span names: They are descriptive, but also bounded (unlike request 
      Way: Update HTTP trace span name with matched mux pattern
      - Understand that `http.Request.Pattern` is populated by `http.ServeMux` (or similar routing middleware) *during* the execution of the handler chain, not before.
      - Locate the `otelhttp` middleware's `serveHTTP` method, which is responsible for creating and naming the HTTP trace span.
      - Modify the `middleware.serveHTTP` method to re-evaluate and update the span name *after* the `next.ServeHTTP` call has completed, but only if `http.Request.Pattern` has been populated.
      - Update the documentation for `WithSpanNameFormatter` to clarify that the formatter will run twice when `http.Request.Pattern` is used: once at span creation and again after the handler chain if a pattern is set.
      - Run the relevant tests to ensure the span naming correctly uses the pattern and that no regressions are introduced.
  [3] Goal: Add name as an alias for description in start_span
      Add `name` as alias to `description` to `start_span()`.
To align our API more with the OpenTelementry API we will add a `name` parameter to the `start_span()` function. Currently `start_span()` has a `description` parameter. The old parameter will still work and the new parameter is an alias to the 
      Way: add name parameter as alias for description in span creation
      - Locate span creation methods and constructor where span description is handled.
      - Add `name` parameter to the `Span` constructor defaulting to `None`, setting `self.description = name or description`.
      - Add `name` parameter to type hints and docstrings for span creation keyword arguments, marking `description` as deprecated.
      - Emit a `DeprecationWarning` when `description` is passed in keyword arguments of helper methods that start spans or child spans.
      - Run tracing test suite to confirm `name` parameter works and deprecation warnings are emitted for `description`.

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
--- Kel's top 3
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

--- Kel's top 3
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
--- Kel's top 3
  [1] Goal: Preserve original key casing in case-insensitive dictionaries and sets
      Update `caseInsensitveDict` to preserve initial capitalization
# Description
Currently, keys are cast to lower case on initialization and subsequent addition. We can instead keep those as they are, and just change the string comparison so that they are case insensitive. We should do the same thing 
      Way: Preserve original key casing in case-insensitive collections using lowercase mapping
      - Locate the case-insensitive dictionary and set implementations in the codebase.
      - Modify the case-insensitive dictionary to store a mapping from lowercase keys to their original-cased keys, updating this map on initialization, item setting, deletion, and updates.
      - Modify the case-insensitive set to similarly maintain a mapping from lowercase elements to their original-cased elements, updating it on addition, removal, and updates.
      - Override comparison and subset methods in the set class to perform case-insensitive comparisons using lowercase representations.
      - Run the test suite to verify that original casing is preserved while case-insensitive lookups and operations still function correctly.
  [2] Goal: Handle case-sensitive enum symbols uniquely during Python code generation
      Issue with case sensitivity in enums
**Describe the bug**
python Fields that represent enums values are translated directly to UPPERCASE regardless of their original casing.
This results in duplicate fields if original values from the schema only differ in casing.

I encounted this is issue in o
      Way: Deduplicate enum member keys during Python code generation
      - Locate the enum field template rendering logic in the schema-to-model code generator module.
      - Reproduce the duplicate enum attribute issue by rendering a schema containing enum symbols that only differ by casing (e.g., 'p' and 'P').
      - Modify the enum symbol rendering logic to build a mapping of member keys to symbol values. Convert symbols to uppercase by default, but fallback to the raw symbol as key if the uppercase key already exists in the mapping.
      - Run test suite to verify case-sensitive enum collisions generate distinct enum member keys without syntax errors.
  [3] Goal: Prevent lowercase conversion of ssdeep hash algorithm keys
      SSDEEP Hashing algorithm case mismatch
Currently if an `ssdeep` hash is added to an object it will change it to its lowercase variant (not aligned with Section 10.6 STIX 2.1 https://docs.oasis-open.org/cti/stix/v2.1/cs01/stix-v2.1-cs01.html#_tumklw3o2gyz) because stix2 will "clean" the field name. T
      Way: Prevent lowercase conversion of ssdeep hash algorithm keys
      - Locate where hash algorithm keys are defined and cleaned in properties and patterns.
      - Modify the SSDEEP hash algorithm key mapping to use uppercase 'SSDEEP' instead of lowercase 'ssdeep' for STIX 2.1, while preserving lowercase 'ssdeep' for STIX 2.0 compatibility.
      - Run the test suite to verify that the ssdeep hash key is correctly preserved as uppercase in STIX 2.1.

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

--- Kel's top 3
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
  [2] Goal: Allow CountFrequencyEncoder to encode unseen categories as zeros
      `CountFrequencyEncoder` could output zeros for new categories
The `CountFrequencyEncoder` has an `errors` argument which can either raise an error or output NaNs when encountering new categories. For this particular class, it'd make sense (perhaps even as a default behavior) to output zeros when a n
      Way: Add encode errors option to count frequency encoder using defaultdict
      - Create a helper function check_parameter_errors(errors, accepted_values) to validate error parameter options across encoders.
      - Refactor CategoricalInitExpandedMixin in base_encoder to use check_parameter_errors for 'raise' and 'ignore' validation.
      - Update CountFrequencyEncoder inheritance and __init__ to accept errors='encode' alongside 'ignore' and 'raise', storing errors on the instance and using CategoricalInitMixin.
      - In CountFrequencyEncoder.fit, initialize encoder dictionaries using defaultdict(lambda: 0) when errors is 'encode', passing it to Series.to_dict(dct_init).
      - Run the encoder tests to verify parameter validation and encoding behavior for unseen categories.
  [3] Goal: Support itertools.count for generating incremental field values in recipes and calls
      Use of itertools.count to generate record ids in Recipe
Hi,

`itertools.count` does not seem to be working in recipe or in a direct make statement

when I declare:
```
import itertools as it
baker.make('Play',  puzzle_id=it.count(start=1), _quantity=7)
```

or 
`fake_play = Recipe(Play, p
      Way: Support iterator sequences for relation ID fields in model generation
      - Locate where model fields are processed and skipped during generation, specifically looking for ForeignKey and OneToOneField handling.
      - Modify the field skipping logic to ensure that related '_id' fields (like ForeignKey or OneToOneField) are not skipped if they are defined in the iterator attributes.
      - Modify the model attribute assignment logic to check if a field is a ForeignKey or OneToOneField with its 'attname' present in the iterator attributes, and if so, assign the next value from the iterator.
      - Update the ForeignKey check when generating values to use 'field.attname' instead of hardcoded '_id' formatting.
      - Run the test suite to verify that itertools.count and other iterators work correctly for foreign key and one-to-one ID fields.

=== TASK pydantic__pydantic-3847 | repo pydantic/pydantic | python
--- Issue
Discriminator field on BaseModel instances are incorrectly accessed by alias in validation
### Checks

* [x] I added a descriptive title to this issue
* [x] I have searched (google, github) for similar issues and couldn't find anything
* [x] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and still think this is a bug

# Bug

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
$ python -c "import pydantic.utils; print(pydantic.utils.version_info())"
             pydantic version: 1.9.0
            pydantic compiled: True
                 install path: ./venv/lib/python3.10/site-packages/pydantic
               python version: 3.10.2 (main, Jan 17 2022, 00:00:00) [GCC 11.2.1 20211203 (Red Hat 11.2.1-7)]
                     platform: Linux-5.16.9-200.fc35.x86_64-x86_64-with-glibc2.34
     optional deps. installed: ['typing-extensions']
```

When validating a discriminated union where the union value has been passed as a Pydantic model instance, it appears that the `_validate_discriminated_union` function is trying to access the value of the discriminator field via its alias. Since the alias does not exist on the model as an attribute, the validation fails.

I believe this is the result of a tiny mistake when setting up the `_validate_discriminated_union` function and that whenever a `BaseModel` or dataclass is detected then we should use the discriminator key and not the alias to access the dis
--- Real fix (for your judgement only)
diff --git a/pydantic/fields.py b/pydantic/fields.py
--- a/pydantic/fields.py
+++ b/pydantic/fields.py
@@ -1086,7 +1086,7 @@ def _validate_discriminated_union(
         except TypeError:
             try:
                 # BaseModel or dataclass
-                discriminator_value = getattr(v, self.discriminator_alias)
+                discriminator_value = getattr(v, self.discriminator_key)
             except (AttributeError, TypeError):
                 return v, ErrorWrapper(MissingDiscriminator(discriminator_key=self.discriminator_key), loc)
 

--- Kel's top 3
  [1] Goal: Support nested Pydantic models within discriminated union fields
      Nested Model in Union gives AttributeError: model_fields 
Apologies if this is the wrong way to communicate but I am trying get an example working that matches the pydantic models I am trying to render in a dash app.

I have taken the example in your Discriminate Unions doc section and extended it
      Way: Propagate concrete model instances when resolving nested discriminated union types
      - Identify where nested model field types are resolved from parent path strings and inspect how union/discriminator annotations are handled.
      - Reproduce the `AttributeError: model_fields` when rendering form fields for nested models inside discriminated union variants or nested models containing discriminated unions.
      - Update `get_subitem_cls` to accept an optional `item` instance, inspect whether the current field annotation is a `Type.DISCRIMINATED_MODEL`, resolve the concrete variant class via `handle_discriminated`, and propagate the nested subitem instance recursively.
      - Update all callers of `get_subitem_cls` across form helpers and value extractors to pass the current `item` instance.
      - Filter field representations to ensure only valid `subitem_cls.model_fields` are rendered when switching between union variants.
      - Run the test suite to verify form rendering passes for nested structures.
  [2] Goal: Support list query parameters using field aliases in schema models
      Query parameters from Schema do not recognise lists with pydantic.Field alias
Using a schema to encapsulate GET requests with a `pydantic.Field` to handle `alias` will result in a 422 error.

```python
class Filters(Schema):
  slug__in: typing.List[str] = pydantic.Field(
      None,
      alia
      Way: Extract schema collection field names using model field aliases
      - Identify where collection fields in query or form parameter schemas are discovered and extracted from model annotations.
      - Write a test passing multiple repeated query parameters matching a schema field defined with an alias (e.g., `Field(..., alias='...')`).
      - Implement a helper to inspect model `__fields__` using `ModelField.outer_type_` to check collection origins and return `field.alias` rather than inspecting raw `__annotations__` keys.
      - Update collection detection logic when handling schema models to incorporate the alias-aware collection field list.
      - Run the test suite to verify repeated query parameters with aliases parse into list fields correctly.
  [3] Goal: ModelFactory must apply Annotated Field constraints to nested types
      Bug: Annotations for nested types are ignored
### Description

When a pydantic model contains a simple annotated attribute, the restrictions from the annotation are used for generating the value.

Example:
```python
Dimension = Annotated[int, Field(gt=1000, lt=2000)]

class Item(BaseModel):
 
      Way: Apply Annotated Field constraints to nested types in Pydantic factories
      - Identify the factory responsible for generating Pydantic models and the method that processes type annotations for field constraints.
      - Reproduce the issue by running the provided MCVE, observing that `Annotated` constraints on nested types (e.g., `list[Annotated[int, Field(...)]]`) are ignored, leading to validation errors.
      - Introduce a new helper function `get_annotation_metadata` to extract all metadata arguments from an `Annotated` type.
      - Modify `FieldMeta.from_type` to use the new `get_constraints_metadata` class method for extracting metadata from `Annotated` types, ensuring that subclasses can override this behavior. Also, ensure that `cls.from_type` is used recursively for children fields to maintain polymorphism.
      - Implement `PydanticFieldMeta.get_constraints_metadata` to specifically handle Pydantic v2's `FieldInfo` objects, unwrapping them to expose the actual `Field` metadata. This ensures that Pydantic `Field` constraints are correctly extracted when wrapped by `FieldInfo`.
      - Verify that the fix correctly applies `Annotated` constraints to nested types by running the provided tests.

=== TASK pydantic__pydantic-817 | repo pydantic/pydantic | python
--- Issue
Root Errors and "__obj__" vs "__root__"
Currently [here](https://github.com/samuelcolvin/pydantic/blob/master/pydantic/main.py#L343) (for invalid data to `parse_obj`) and [here](https://github.com/samuelcolvin/pydantic/blob/master/pydantic/main.py#L361) (in `parse_raw`) we use `'__obj__'` as the location for errors to indicate an error which is general to the model, not specific to a field.

We should change this in v1 to `__root__` to better match other nomenclature, eg. custom root types, where we use `__root__`.

We should also implement at some point entire model validation, as discussed at https://github.com/samuelcolvin/pydantic/issues/691#issuecomment-518318096. I guess to match the above naming, we should do this using a special value to a validator `@validator('__root__')`. If this raised an error it would end up in `ValidationError` details with `'location': '__root__'`, (Unless it's a "`@validator('__root__')`" validator for a submodel, when obviously it should have the location of that model. 

Perhaps it would would be nice to have a special decorator `@root_validator` which is an alias of `@validator('__root__')`, we should support multiple root validators either as a result of inheritance or on a single class.

I guess the `__obj__` > `__root__` change must happen with v1 and the new root validator could go in a later release, but we should be sure we won't need other backwards incompatible changes to implement the root validator.

Thoughts?

--- Real fix (for your judgement only)
diff --git a/docs/examples/validators_root.py b/docs/examples/validators_root.py
new file mode 100644
--- /dev/null
+++ b/docs/examples/validators_root.py
@@ -0,0 +1,41 @@
+from pydantic import BaseModel, ValidationError, root_validator
+
+class UserModel(BaseModel):
+    username: str
+    password1: str
+    password2: str
+
+    @root_validator(pre=True)
+    def check_card_number_omitted(cls, values):
+        assert 'card_number' not in values, 'card_number should not be included'
+        return values
+
+    @root_validator
+    def check_passwords_match(cls, values):
+        pw1, pw2 = values.get('password1'), values.get('password2')
+        if pw1 is not None and pw2 is not None and pw1 != pw2:
+            raise ValueError('passwords do not match')
+        return values
+
+print(UserModel(username='scolvin', password1='zxcvbn', password2='zxcvbn'))
+#> UserModel username='scolvin' password1='zxcvbn' password2='zxcvbn'
+
+try:
+    UserModel(username='scolvin', password1='zxcvbn', password2='zxcvbn2')
+except ValidationError as e:
+    print(e)
+"""
+1 validation error for UserModel
+__root__
+  passwords do not match (type=value_error)
+"""
+
+try:
+    UserModel(username='scolvin', password1='zxcvbn', password2='zxcvbn', card_number='1234')
+except ValidationError as e:
+    print(e)
+"""
+1 validation error for UserModel
+__root__
+  card_number should not be included (type=assertion_error)
+"""
diff --git a/docs/examples/validators_simple.py b/docs/examples/validators_simple.py
--- a/docs/examples/validators_simple.py
+++ b/docs/examples/validators_simple.py
@@ -1,6 +1,5 @@
 from pydantic import BaseModel, ValidationError, validator
 
-
 class UserModel(BaseModel):
     name: str
     username: str
@@ -24,16 +23,15 @@ def username_alphanumeric(cls, v):
         assert v.isalpha(), 'must be alphanumeric'
         return v
 
-
 print(UserModel(name='samuel colvin', username='scolvin', password1='zxcvbn', password2='zxcvbn'))
-# > UserModel name='Samuel
--- Kel's top 3
  [1] Goal: Refactor model validation to avoid unnecessary Pydantic validations
      Use `pydantic` effectively
**Is your feature request related to a problem? Please describe.**

Currently `pydantic` validations are sorely used to validate types, which can be elided assuming type annotations are respected.

**Describe the feature you'd like**

- Use `pydantic` to check additi
      Way: replace Pydantic models with standard dataclasses and union types
      - Identify Pydantic BaseModel subclasses used solely for static typing/data container purposes where runtime validation overhead is unneeded.
      - Implement tag and kind validation utilities using __init_subclass__ if structural consistency of tagged data classes needs to be enforced without runtime Pydantic overhead.
      - Refactor hierarchy of classes from Pydantic BaseModel to @dataclasses.dataclass, replacing base inheritance hierarchies with Union types, dataclasses.field default_factory for mutable defaults, and ClassVar for enum tags.
      - Update instantiations and conversions across the codebase to use positional or updated keyword arguments matching standard dataclass constructors.
      - Run the test suite to verify no regressions in data structures and backend functionality.
  [2] Goal: Standardize Pydantic model validation methods in array metadata class
      Nit: `__post_init__` vs pydantic `@model_validator(mode="after")`
[In the `ZArray` class](https://github.com/zarr-developers/VirtualiZarr/blob/179bb2ab42664546dd243a2e04fab8737846229a/virtualizarr/zarr.py#L76) then after #193 we now have multiple types of validation checks, which together look like 
      Way: replace Pydantic models with standard dataclasses in array metadata
      - Identify Pydantic models (`ZArray`, `ChunkEntry`, `Codec`) used for metadata representation and locate validation logic across `__post_init__`, `@field_validator`, and `@model_validator`.
      - Remove `pydantic` dependency from project configuration (`pyproject.toml`, `ci/environment.yml`).
      - Convert metadata classes (`ChunkEntry`, `Codec`, `ZArray`) from `pydantic.BaseModel` to `@dataclasses.dataclass`.
      - Consolidate `dtype` normalization, dimension checks, and default `fill_value` handling into `ZArray.__post_init__`.
      - Update serialization helpers to use `dataclasses.asdict` instead of `dict(self)` or Pydantic serialization.
      - Run test suite to verify metadata instantiation, validation, dictionary conversion, and replace methods.
  [3] Goal: Improve readability and formatting of validation error messages
      Better formatting for error messages
Some error messages are currently somewhat hard to read, example:

```console
pydantic.error_wrappers.ValidationError: 1 validation error for RegionProcessor
mappings -> ['REMIND 3.0']
  Region(s) ['REMIND 3.0|Canada, NZ, Australia', 'REMIND 3.0|China', 'REM
      Way: formatting validation error templates with strategic line breaks
      - Locate custom validation error definitions and error message templates across the codebase, specifically for undefined region reporting and ISO3 code validation.
      - Format the RegionNotDefinedError template message by placing line breaks between the prefix, the region list placeholder, the file path, and the trailing message.
      - Update ISO3 country code field validation to accumulate errors into an ErrorCollector before raising, ensuring consistency with collector-based error formatting.
      - Run the test suite to verify tests expecting error messages pass with updated formatting.

=== TASK kgaughan__komorebi-11 | repo kgaughan/komorebi | python
--- Issue
Embed scaling
YouTube recently started making its embeds _tiny_. Here's an example:
```html
<iframe
    width="200"
    height="113"
    src="https://www.youtube.com/embed/1234567890a?feature=oembed"
    frameborder="0"
    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
    allowfullscreen></iframe>
```
This is very, very small. I think I need a way to rescale things like these.
--- Real fix (for your judgement only)
diff --git a/Makefile b/Makefile
index c9ef6f9..89313eb 100644
--- a/Makefile
+++ b/Makefile
@@ -26,4 +26,7 @@ lint:
 	poetry run flake8 --max-line-length=105 --ignore=E203 --per-file-ignores="komorebi/oembed.py:N802" komorebi
 	poetry run pylint komorebi
 
-.PHONY: clean develop run build tidy lint
+test:
+	poetry run python -m unittest discover -bf tests
+
+.PHONY: clean develop run build tidy lint test
diff --git a/komorebi/blog.py b/komorebi/blog.py
index 75168d0..21bf6ab 100644
--- a/komorebi/blog.py
+++ b/komorebi/blog.py
@@ -16,7 +16,7 @@ from flask_httpauth import HTTPBasicAuth
 import markdown
 from passlib.apache import HtpasswdFile
 
-from . import db, forms, oembed, time, xmlutils
+from . import db, forms, futz, oembed, time, xmlutils
 
 blog = Blueprint("blog", __name__)
 blog.add_app_template_filter(time.to_iso_date)
@@ -51,14 +51,22 @@ def process_archive(records):
         if record["year"] != year:
             if last_month != 0:
                 for i in range(1, 13 - last_month):
-                    yield {"n": 0, "year": year, "month": last_month + i}
+                    yield {
+                        "n": 0,
+                        "year": year,
+                        "month": last_month + i,
+                    }
             year = record["year"]
             last_month = 0
 
         # Pad out between months in a year.
         if record["month"] - 1 != last_month:
             for i in range(1, record["month"] - last_month):
-                yield {"n": 0, "year": record["year"], "month": last_month + i}
+                yield {
+                    "n": 0,
+                    "year": record["year"],
+                    "month": last_month + i,
+                }
 
         yield record
         last_month = record["month"]
@@ -161,13 +169,22 @@ def add_entry():
 
         try:
             entry_id = db.add_entry(
-                link=form.link.data, title=title, via=form.via.data, note=form.note.data
+                link=form.
--- Kel's top 3
  [1] Goal: Configure video resolution and scale text overlays
      Increase video resolution
A user reported that the overlay text breaks a little bit.

We need to increase the video size from 640x360 maybe to 960x540 (with a specific bitrate, e.g., `5000k`) as we need to trade off between quality and processing time.



![Image](https://github.com/user-attachments
      Way: Scale text overlays proportionally to video resolution
      - Identify the class responsible for video creation and text overlay, and its initialization parameters for video size and font size.
      - Define a new default video resolution constant.
      - Modify the video creator's constructor to accept a video size parameter and a bitrate parameter, and remove the direct font size parameter.
      - Implement proportional font size calculation within the video creator's constructor, basing it on the video height and a known reference font size for a specific height.
      - Pass the configured bitrate to the video writing function.
      - Update the main application's instantiation of the video creator to use the new default video resolution.
  [2] Goal: Keep linear terms within range when embedding QUBO into Ising models
      Embedding QUBO leads to out-of-range linear terms.
If I determine the correct scaling factor to scale a QUBO such that the Ising form fits in the QPU range, I should be able to scale the QUBO by this amount, embed it, and solve the Ising.  However, the embedding causes some linear QUBO terms to be o
      Way: support vartype-specific smearing when embedding binary quadratic models
      - Locate the `embed_bqm` function that distributes linear biases across target embedding chains.
      - Add an optional `smear_vartype` parameter to `embed_bqm` defaulting to `None`.
      - Check if `smear_vartype` differs from `source_bqm.vartype`; if so, convert `source_bqm` to the target `smear_vartype` (e.g. `.spin` or `.binary`), recursively invoke `embed_bqm` with `smear_vartype=None`, and convert the result back to the original vartype.
      - Verify the fix by running the test suite on embedding transforms.
  [3] Goal: Parse and output average bandwidth for IFramePlaylist
      Output Average Bandwidth for IFramePlaylist
iframe_stream_info sets average bandwidth to None at https://github.com/globocom/m3u8/blob/master/m3u8/model.py#L847

It should be passed along if available per https://tools.ietf.org/html/rfc8216#section-4.3.4.3 and https://tools.ietf.org/html/rfc8216#s
      Way: Parse and dump average bandwidth attribute for iframe playlist streams
      - Locate I-frame stream parsing logic in the parser module and playlist model class.
      - Update the attribute parser definition for I-frame stream lines in the parser module to parse average_bandwidth as an integer.
      - Update IFramePlaylist initialization to extract average_bandwidth from parsed iframe stream info instead of passing None.
      - Update IFramePlaylist string dump method to append AVERAGE-BANDWIDTH when present.
      - Run test suite to verify iframe playlist parsing and serialization with average bandwidth.

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
--- Kel's top 3
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
  [2] Goal: Validate generated datasets against the BIDS specification in automated tests
      Add test to pass a BIDS validator
<!--- Provide a general summary of the issue in the Title above -->

## Detailed Description
<!--- Provide a detailed description of the change or addition you are proposing -->
I run a BIDS validator on a dataset that I BIDSified with an older version of phys2b
      Way: Add automated BIDS validation CI job and fix dataset README filename
      - Identify where dataset metadata files like README are generated in the BIDS output generation logic.
      - Update the dataset README output filename from 'README.md' to 'README' to conform to the BIDS spec.
      - Configure a CI pipeline job that installs the official bids-validator CLI via npm, generates a sample BIDS dataset, fetches mock neuroimaging functional files, and runs bids-validator on the output directory.
      - Run the dataset generation unit tests to verify the updated README filename.
  [3] Goal: Index derivatives directories lacking dataset description metadata
      Derivatives indexing fails silently for deriv directories that lack a `dataset_description.json` file
I have a set of derivatives files that look like this:

derivatives/rt/sub-02/func/sub-02_task-dts_meanRT.tsv

they seem to pass the validator fine, but I get nothing when I use .get(scope='deri
      Way: warn when derivative dataset indexing finds no valid dataset descriptions
      - Identify derivative discovery logic where directories are filtered by the presence of metadata description files (e.g., `dataset_description.json`).
      - Update docstrings to clearly document that derivative indexing requires a `dataset_description.json` file in each derivative directory.
      - Add a warning when derivative indexing is requested but no valid derivative directories containing the required description metadata are discovered.
      - Run the layout derivative tests to ensure the warning is emitted and valid indexing passes.

=== TASK beeware__briefcase-684 | repo beeware/briefcase | python
--- Issue
On Android, if your app is called `x.y.switch`, it fails to build
**Describe the bug**

If you call your app `switch` (or another Java reserved word), you will be unable to build it on Android.


**To Reproduce**

You can reproduce by creating an app whose module_name is `switch`. For example, you can use a Toga demo app.

Steps to reproduce the behavior:

1. Download the toga source code, then `cd` into `examples/switch`
2. Run `briefcase create android && briefcase build android`
3. Wait for the build to complete (about 60 seconds if you've used Briefcase on this system to make Android apps)
4. See an error:

```
> Task :app:processDebugResources FAILED

FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':app:processDebugResources'.
> A failure occurred while executing com.android.build.gradle.internal.tasks.Workers$ActionFacade
   > Package 'org.beeware.switch' from AndroidManifest.xml is not a valid Java package name as 'switch' is a Java keyword.

* Try:
Run with --stacktrace option to get the stack trace. Run with --info or --debug option to get more log output. Run with --scan to get full insights.

* Get more help at https://help.gradle.org

BUILD FAILED in 9s
15 actionable tasks: 15 executed

Error while building project.
```

**Expected behavior**

The app should build or briefcase should print an error at the `briefcase new` phase, or briefcase should print an error before trying to run `br
--- Real fix (for your judgement only)
diff --git a/changes/460.bugfix.rst b/changes/460.bugfix.rst
new file mode 100644
index 00000000..bd578ac8
--- /dev/null
+++ b/changes/460.bugfix.rst
@@ -0,0 +1,1 @@
+Bundle identifiers are now validated to ensure they don't contain reserved words.
diff --git a/src/briefcase/commands/new.py b/src/briefcase/commands/new.py
index 5d984b77..451abec7 100644
--- a/src/briefcase/commands/new.py
+++ b/src/briefcase/commands/new.py
@@ -7,14 +7,12 @@ from urllib.parse import urlparse
 
 from cookiecutter import exceptions as cookiecutter_exceptions
 
-from briefcase.config import is_valid_app_name
+from briefcase.config import is_valid_app_name, is_valid_bundle_identifier
 from briefcase.exceptions import NetworkFailure
 
 from .base import BaseCommand, BriefcaseCommandError
 from .create import InvalidTemplateRepository
 
-VALID_BUNDLE_RE = re.compile(r'[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$')
-
 
 def titlecase(s):
     """
@@ -138,12 +136,15 @@ class NewCommand(BaseCommand):
         :returns: True. If there are any validation problems, raises ValueError
             with a diagnostic message.
         """
-        if not VALID_BUNDLE_RE.match(candidate):
+        if not is_valid_bundle_identifier(candidate):
             raise ValueError(
-                "Bundle should be a reversed domain name. It must contain at "
-                "least 2 dot-separated sections, and each section may only "
-                "include letters, numbers, and hyphens."
+                f"{candidate!r} is not a valid bundle identifier.\n\n"
+                "The bundle should be a reversed domain name. It must contain at least 2\n"
+                "dot-separated sections; each section may only include letters, numbers,\n"
+                "and hyphens; and each section may not contain any reserved words (like\n"
+                "'switch', or 'while')."
             )
+
         return True
 
     def make_domain(self, bundle):
diff --git a/src/briefcase/config.py b/src/briefcase/config.py
index
--- Kel's top 3
  [1] Goal: Prevent crash when switching screens without a result callback
      Report a bug about switch_screen
```python
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Header, Footer

class ScreenA(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("A")
       
      Way: Ensure screen result callbacks are maintained when switching screens
      - Inspect screen switching logic in the application core to see how screen stacks and result callback stacks are updated.
      - Resolve the target screen before equality checks to detect if the target screen instance is already the active screen (whether passed as an instance or by name/string).
      - Update screen switching to return early as a no-op if the target screen is already active.
      - Push a empty/None result callback onto the newly switched-to screen so subsequent pops on switch don't underflow or crash.
      - Run screen management test cases to verify switching between screens and no-op switching to current screen work correctly.
  [2] Goal: Ensure project loads after manually matching reconciled cells with errors
      Impossible to load a project again after manually matching reconciled cells with errors
In some circumstances it is possible that loading a previously saved project fails in 3.8-beta1.

### To Reproduce
Steps to reproduce the behavior:
1. First, reconcile a column to a reconciliation service tha
      Way: Allow project loading with manually matched reconciled cells that had errors
      - Locate the `Recon` class, specifically its constructor and methods related to setting reconciliation state, as the issue points to deserialization failures and inconsistencies within `Recon` objects.
      - Understand the existing invariant check in the `Recon` constructor that prevents deserialization. The issue states it's `error != null && match != null`.
      - Modify the `Recon` constructor's invariant check to allow an error to coexist with a match, but not with a list of candidates. Change the condition from `error != null && match != null` to `error != null && !this.candidates.isEmpty()`.
      - Ensure that when a reconciliation candidate is explicitly added, any existing error is cleared. In the `addCandidate` method, set the `error` field to `null`.
      - Run the provided tests to confirm the fix resolves the project loading issue and does not introduce regressions.
  [3] Goal: Refresh container list properly when switching between projects
      Failure to retrieve containers after switching to a a project with no containers
#### Describe the bug
Failure to retrieve containers after switching to a a project with no containers

#### To Reproduce
Steps to reproduce the behaviour:
1. Log in
2.1 If there is a project with some containers 
      Way: prevent caching on error responses in aiohttp web middleware
      - Identify middleware handling HTTP error responses (such as 401, 403, 404) that serve static error pages.
      - Replace web.FileResponse usages for error pages with a helper function reading static files into web.Response containing explicit no-cache headers ('Cache-Control': 'no-cache, no-store, must-revalidate', 'Pragma': 'no-cache', 'Expires': '0').
      - Update error_middleware to return the custom web.Response from the helper for handled error statuses both from normal responses and raised HTTPException instances.
      - Run the middleware test suite to verify that error responses set the correct status and headers.

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
--- Kel's top 3
  [1] Goal: Allow setting a random seed in the sample method
      [Enh]: Random seed for `sample`
### We would like to learn about your use case. For example, if this feature is needed  to adopt Narwhals in an open source project, could you please enter the link to it below?


I would like to use the `sample` method that allows for reproducibility. However, there 
      Way: add optional random seed parameter to frame expression and series sampling methods
      - Locate the public API methods and internal engine implementations for frame/expression and series sampling.
      - Add an optional keyword-only `seed: int | None = None` parameter to the public `sample` signature on top-level classes and pass it down to backend compliant implementation calls.
      - Update backend implementations (such as pandas-like and arrow wrappers) to accept `seed` and forward it to backend sampling logic (e.g. `random_state` in pandas or `np.random.default_rng(seed)` in NumPy/Arrow).
      - Run sampling tests to verify reproducible results when `seed` is provided.
  [2] Goal: Allow user-defined random seed for reproducible experiments
      Increase flexibility of setting seed
I was running some code that has randomness during evaluation. However I found that my, supposedly, random preprocessing was always done in exactly the same way.

This is related to setting the random seed by default to 12: https://github.com/pytorch/ignite/blo
      Way: Make engine's default seed respect global random state
      - Locate the `run` method within the engine's core logic where a default random seed is set if none is explicitly provided.
      - Understand that the current hardcoded default seed (e.g., `12`) overrides any global random state set by the user, leading to non-reproducible experiments when relying on global seeding.
      - Modify the default seed assignment to derive a seed from the current global random state using `torch.randint`. This ensures that if a user has set a global seed (e.g., with `torch.manual_seed`), the engine's default seed will be consistent with that state.
      - Update the docstring for the `seed` parameter in the `run` method to clearly state that the default behavior now respects the global random state.
      - Run the relevant tests to ensure the change correctly implements the desired behavior and does not introduce regressions.
  [3] Goal: Ensure consistent random noise generation behavior when using random seeds
      Inconsistent use of noise seed
I noticed that in `rpy.utils.random.noise` a new random number generator is created in each function call.  As such, if we call the function multiple times in a row with the same seed value, we get the same output:
```
>>> import reservoirpy as rpy
>>> rpy.utils.ran
      Way: use persistent random generator for node noise functions
      - Identify where noise generation and reservoir kernel calculations are performed in node execution.
      - Reproduce inconsistent seed behavior where input noise uses unseeded noise function while feedback uses noise generator partials.
      - Update reservoir kernel logic to use the node's configured noise generator for input noise instead of a direct top-level noise call.
      - Instantiate a single NumPy random generator instance via seed when initialising noise partials for reservoir nodes.
      - Verify the noise generation tests pass consistently across repeated calls with set seeds.

=== TASK DataBiosphere__newt-transformer-24 | repo DataBiosphere/newt-transformer | python
--- Issue
Change data_bleach.py to retain sample.submitter_id
The field `sample.submitter_id` is a useful value that should not be sanitized (cleared) by `data_bleach.py`
--- Real fix (for your judgement only)
diff --git a/newt/transform/gen3old.py b/newt/transform/gen3old.py
index 2c1ac2f..6c0c940 100644
--- a/newt/transform/gen3old.py
+++ b/newt/transform/gen3old.py
@@ -34,13 +34,17 @@ class Gen3Transformer(AbstractTransformer):
 
     @staticmethod
     def _build_metadata_dict(input_metadata):
-        def build_metadata_field(metadata, field_name: str):
+        def build_metadata_field(metadata, field_name: str, key: str):
             """
             Builds dict from metadata where key is node_id, value is the corresponding metadata
             """
-            return {entry['node_id']: entry for entry in metadata[field_name]}
+            return {entry[key]: entry for entry in metadata[field_name]}
         for field in input_metadata:
-            input_metadata[field] = build_metadata_field(input_metadata, field)
+            if field == 'project' or field == 'program':
+                key = 'id'
+            else:
+                key = 'node_id'
+            input_metadata[field] = build_metadata_field(input_metadata, field, key)
         return input_metadata
 
     def __init__(self, input_dict: dict) -> None:
diff --git a/newt/transform/gen3standard.py b/newt/transform/gen3standard.py
index 5970385..c68389d 100644
--- a/newt/transform/gen3standard.py
+++ b/newt/transform/gen3standard.py
@@ -38,7 +38,8 @@ class Bundle(dict):
         self['data_bundle'] = self.data_bundle
         self['data_objects'] = self.data_objects
 
-    def add_metadata_field(self, metadata_source: dict,
+    def add_metadata_field(self,
+                           metadata_source: dict,
                            source_field_name: str,
                            linked_field_name: str,
                            link_name: str):
@@ -57,7 +58,6 @@ class Bundle(dict):
             linked_field_dict = metadata_source[linked_field_name][linked_field_key]
             # Add it to this bundle's metadata
             self.metadata[linked_field_name] = linked_field_dict
-
         el
--- Kel's top 3
  [1] Goal: Preserve blob metadata when uploading new content to an existing blob
      Blob.metadata not preserved when uploading new file content
`blob.metadata` is not preserved when uploading new file content on an existing Blob instance with `blob.upload_from_string(...)` unless you first reset it with `blob.metadata = blob.metadata`.

I suspect this bug / surprising behaviour i
      Way: Mark existing object metadata as modified before content uploads
      - Locate the upload initiation logic (such as multipart and resumable upload methods) on the Blob class.
      - Reproduce the issue by running unit tests checking if existing metadata is retained during consecutive uploads on the same Blob instance.
      - Add logic in upload methods (`_do_multipart_upload` and `_initiate_resumable_upload`) to check if `metadata` exists in instance properties and mark `metadata` in tracked property changes if not already present.
      - Run the targeted unit tests to verify metadata is included in upload requests.
  [2] Goal: Preserve submissions key in attributes of grouped submissions
      GroupedSubmission 'submissions' key:value deleted
# Describe the bug

In #280 we added the first instance of nesting a list of one type of object inside another. While this generally works well, there is a small side effect: The `'submissions'` key:value pair is removed from the `attributes` dict.
      Way: Preserve raw attribute keys when instantiating wrapper objects with nested objects
      - Locate the `GroupedSubmission` class initialization logic where nested `Submission` objects are extracted from attributes.
      - Remove the key deletion (`del attributes['submissions']`) and call `super().__init__(requester, attributes)` at the beginning of `__init__` before or during processing nested items.
      - Run unit tests to verify that the `submissions` key remains present in `attributes` and regression test passes.
  [3] Goal: Track changes when clearing a ListField so updates are saved
      Changes from clear() on ListField are not saved
```python
class MyDoc(Document):
    things = ListField(StringField())
```

If I get hold of a document with some things and do
```python
doc.things.clear()
doc.save()
```
`things` will not be emptied. If I do
```python
doc.things = []
doc
      Way: wrap list mutation methods with change tracking in custom list datastructures
      - Identify the custom collection class backing the list field and inspect how mutating methods notify parent objects of changes.
      - Reproduce the issue by creating a test that calls the in-place mutating method (e.g. clear) on the list datastructure and checks whether change tracking is triggered.
      - Wrap the mutating list method (list.clear) with the change-marking wrapper on the custom list class.
      - Run the datastructure unit tests to ensure that the mutating method marks changes and parent saves include the updated field.

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

--- Kel's top 3
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

=== TASK alexmojaki__pure_eval-12 | repo alexmojaki/pure_eval | python
--- Issue
TypeError for malformed metaclass example
In https://github.com/ipython/ipython/issues/13481, the following example used to show a fatal error in IPython:
```python
class X(type):
    def __prepare__(cls, *args, **kwargs):
        return []
class Y(metaclass=X):
    pass
```

If I try the same example with friendly-traceback, I also get a fatal error, with the following as part of a long traceback:
```
    During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "LOCAL:\pure_eval\core.py", line 445, in group_expressions
    for node, value in expressions:
  File "FRIENDLY:\info_variables.py", line 119, in <genexpr>
    for nodes, obj in group_expressions(
  File "LOCAL:\pure_eval\core.py", line 358, in find_expressions
    value = self[node]
  File "LOCAL:\pure_eval\core.py", line 68, in __getitem__
    self._cache[node] = result = self._handle(node)
  File "LOCAL:\pure_eval\core.py", line 89, in _handle
    return self.names[node.id]
TypeError: list indices must be integers or slices, not str
```

In https://github.com/friendly-traceback/friendly-traceback/commit/276ec1b85f7c5949b0e5d1fb325b30b59b57d9c5, I've guarded against this type of fatal error.

I didn't see any evidence that the IPython crash is caused by pure_eval or an other library of yours, but I thought you might want to know about it - and possibly include some safeguards in pure_eval.



--- Real fix (for your judgement only)
diff --git a/.travis.yml b/.travis.yml
deleted file mode 100644
index fb89d46..0000000
--- a/.travis.yml
+++ /dev/null
@@ -1,32 +0,0 @@
-dist: xenial
-language: python
-sudo: false
-
-python:
-  - 3.5
-  - 3.6
-  - 3.7
-  - 3.8-dev
-  - 3.9-dev
-
-env:
-  global:
-    - PURE_EVAL_SLOW_TESTS=1
-    - COVERALLS_PARALLEL=true
-
-before_install:
-  - pip install --upgrade coveralls setuptools>=44 setuptools_scm>=3.4.3 pep517
-
-install:
-  - pip install ".[tests]"
-
-script:
-  - coverage run --branch --include='pure_eval/*' -m pytest --junitxml=./rspec.xml
-  - coverage report -m
-
-after_success:
-  - coveralls
-
-notifications:
-  webhooks: https://coveralls.io/webhook
-  email: false
diff --git a/MANIFEST.in b/MANIFEST.in
index 800dfd8..09204c8 100644
--- a/MANIFEST.in
+++ b/MANIFEST.in
@@ -1,2 +1,3 @@
 include LICENSE.txt
 include pure_eval/py.typed
+include README.md
diff --git a/make_release.sh b/make_release.sh
index f9b8308..f0c1ac8 100755
--- a/make_release.sh
+++ b/make_release.sh
@@ -26,5 +26,5 @@ export TAG="v${1}"
 git tag "${TAG}"
 git push origin master "${TAG}"
 rm -rf ./build ./dist
-python3 -m pep517.build -bs .
+python -m build --sdist --wheel .
 twine upload ./dist/*.whl dist/*.tar.gz
diff --git a/pure_eval/core.py b/pure_eval/core.py
index 0a0381e..748f051 100644
--- a/pure_eval/core.py
+++ b/pure_eval/core.py
@@ -15,6 +15,7 @@ from pure_eval.utils import (
     of_standard_types,
     is_any,
     of_type,
+    ensure_dict,
 )
 
 
@@ -39,9 +40,9 @@ class Evaluator:
         """
 
         return cls(ChainMap(
-            frame.f_locals,
-            frame.f_globals,
-            frame.f_builtins,
+            ensure_dict(frame.f_locals),
+            ensure_dict(frame.f_globals),
+            ensure_dict(frame.f_builtins),
         ))
 
     def __getitem__(self, node: ast.expr) -> Any:
diff --git a/pure_eval/utils.py b/pure_eval/utils.py
index 139d6dd..a8a3730 100644
--- a/pure_eval/utils.py
+++ b/pure_eval/utils.py
@@ -189,3 +189,13 @@ def copy
--- Kel's top 3
  [1] Goal: Prevent index out of range error when checking completeness of code input
      Cannot enter docstring in IPython
Type the following snippet and then hit `C-m` or `C-j` or `C-o` then IPython produces "Exception list index out of range" from `IPython/core/inputtransformer2.py`

```
In [1]: def f():
   ...:     """
```

Traceback:

```pytb
Unhandled exception in event l
      Way: bounds check line tokens when inspecting leading whitespace tokens
      - Locate the token transformer find logic in the input transformation module where `line[ix]` indexing occurs.
      - Run `pytest IPython/core/tests/test_inputtransformer2.py::test_check_complete_II` to reproduce the index error when checking incomplete multi-line input.
      - Add checks for empty lines and bound `ix` against line length (`ix < len(line)`) before accessing token properties by index.
      - Run test suite to verify the index out of range exception is fixed.
  [2] Goal: Handle non-subclassable exception types during exception propagation
      Error propagating exceptions on-non subclassable types
I was using joblib to run 8 optimizations using nlopt in parallel, and got this error:

```
---------------------------------------------------------------------------
TypeError                                 Traceback (most recent call las
      Way: Handle non-subclassable exception types during exception propagation
      - Locate the exception wrapper or factory function that dynamically creates custom exception subclasses for propagation.
      - Wrap the dynamic subclass creation (`type(...)`) in a try-except block to catch `TypeError` exceptions.
      - Fallback to a generic base exception class (like a custom library exception) when subclassing fails due to non-subclassable types or MRO conflicts.
      - Run the exception handling tests to verify that non-subclassable exceptions (such as those from SWIG or C-extensions) are safely wrapped without raising a TypeError.
  [3] Goal: Support type and metaclass type hints in multimethod dispatch
      TypeError when one of the type hints is type or another metaclass
Using `type` (or any other subclass of `type`, e.g. a custom metaclass) as the type hint for one of the parameters of a function of a multimethod makes the dispatch process raise a `TypeError` when calling `type.mro()` (which expects 
      Way: Using explicit type.mro to support metaclass instances in dispatch
      - Identify where method resolution order (.mro()) is invoked on class objects during type comparison or distance calculations.
      - Reproduce the error by registering a multimethod that annotates an argument with `type` (or a metaclass) and dispatching a class object to it.
      - Replace instance-style method calls like `subclass.mro()` with `type.mro(subclass)` in distance and MRO lookup functions so metaclass instances are properly inspected without calling unbound metaclass methods.
      - Run the test suite to verify dispatch works when parameters are annotated with `type`.

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
--- Kel's top 3
  [1] Goal: Ensure chain properly closes active iterators upon early termination
      Change in behavior of chain() between 3.10.5 and 3.10.6.
There is a subtle change in behavior introduced in the recent changes to `chain()`. Chain's iterator appears to be escaping from its "scope" when it is interrupted.

Here is an example program that produces different output. I'm using Python
      Way: Ensure chain properly closes active iterators upon early termination
      - Locate the implementation of the `chain` class in the itertools module.
      - Reproduce the issue where interrupting a chained iterator leaves the underlying iterators unclosed, delaying their cleanup.
      - Refactor `chain` to track its active iterators and implement an `aclose` method that explicitly closes both the main generator and any owned iterators.
      - Run the test suite to verify that `chain` properly closes partial iterators upon early termination.
  [2] Goal: Support async generator operations in memory dataflow runner
      df: memory: async iterator operations
Functions which are async iterators (`async def` with `yield`) should be supported by `@op`, as well as `dffml/df/memory.py` needs to be able to run one of these kinds of operations. Everytime the operation `yield`s, whatever it `yield`'d should be added to the 
      Way: support async generator operations in memory dataflow network
      - Locate the memory dataflow network execution context handling operation run output processing.
      - Check if operation output is an async generator using `inspect.isasyncgen()`; if not, wrap the returned dict in an async generator helper function.
      - Iterate over the async generator using `async for`, processing each yielded output set individually and adding the resulting `MemoryInputSet` to the input context.
      - Run the memory dataflow async generator operation tests to verify both auto-start and input-driven generator operations work.
  [3] Goal: Support asynchronous iterators in streaming responses
      StreamingResponse doesn't support PEP-492 asynchronous iterators
[PEP-492 asynchronous iterators](https://www.python.org/dev/peps/pep-0492/#asynchronous-iterators-and-async-for) have the following signature:

```python
class AsyncIterable:
    def __aiter__(self):
        return self

    asy
      Way: accepting arbitrary async iterables in streaming responses
      - Find where StreamingResponse initializes and distinguishes synchronous content from asynchronous content.
      - Write tests passing custom classes implementing `__aiter__` and `__anext__` to StreamingResponse to reproduce failure when `inspect.isasyncgen` evaluates to False.
      - Change the content type check in StreamingResponse.__init__ from `inspect.isasyncgen(content)` to `isinstance(content, typing.AsyncIterable)`.
      - Remove unneeded inspect module import if it is no longer used in the responses module.
      - Run the response tests to confirm both custom async iterators and custom async iterables stream properly.

=== TASK pydantic__pydantic-1989 | repo pydantic/pydantic | python
--- Issue
GenericModel does not support generic subclasses
# Bug

It seems the implementation of pydantic.generics.GenericModel does not support generic subclasses. Example:
```
import pydantic.generics
import typing

T = typing.TypeVar("T")

class ResponseModel(pydantic.generics.GenericModel, typing.Generic[T]):
    data: List[T]

class PaginatedResponseModel(ResponseModel[T]):
    total_count: int

ConcreteModel = PaginatedReponseModel[dict] # TypeError: Cannot parameterize a concrete instantiation of a generic model

```
`PaginatedReponseModel` is erroneously considered "concrete" even though type variables are used as parameters, and not concrete types.
It seems GenericModel.__class_getitem__ does not check if any parameters are `TypeVar`, like `typing.Generic` does. 

To support generic subclasses, `__class_getitem__` should check if all of the parameters are fully concrete type(i.e. any of them are `typing.TypeVar`s, or any of them are generic types that have non-concrete type parameters). If not, a concrete model should not be created.

I would consider this a bug since it does not follow the behavior of other `typing.Generic` usages, but it could also be a feature request.

Please complete:
* OS: Ubuntu 18.04.1
* Python version `import sys; print(sys.version)`: 3.7.1 (default, Oct 22 2018, 11:21:55) 
[GCC 8.2.0]
* Pydantic version `import pydantic; print(pydantic.VERSION)`: `1.0`

GenericModel - inheritance and Optional[TypeX] - fail to instantiate
--- Real fix (for your judgement only)
diff --git a/pydantic/generics.py b/pydantic/generics.py
--- a/pydantic/generics.py
+++ b/pydantic/generics.py
@@ -1,10 +1,14 @@
 import sys
+import typing
 from typing import (
     TYPE_CHECKING,
     Any,
     ClassVar,
     Dict,
     Generic,
+    Iterable,
+    Iterator,
+    List,
     Optional,
     Tuple,
     Type,
@@ -17,8 +21,8 @@
 from .class_validators import gather_all_validators
 from .fields import FieldInfo, ModelField
 from .main import BaseModel, create_model
-from .typing import get_origin
-from .utils import lenient_issubclass
+from .typing import display_as_type, get_args, get_origin, typing_base
+from .utils import all_identical, lenient_issubclass
 
 _generic_types_cache: Dict[Tuple[Type[Any], Union[Any, Tuple[Any, ...]]], Type[BaseModel]] = {}
 GenericModelT = TypeVar('GenericModelT', bound='GenericModel')
@@ -37,6 +41,16 @@ class GenericModel(BaseModel):
 
     # Setting the return type as Type[Any] instead of Type[BaseModel] prevents PyCharm warnings
     def __class_getitem__(cls: Type[GenericModelT], params: Union[Type[Any], Tuple[Type[Any], ...]]) -> Type[Any]:
+        """Instantiates a new class from a generic class `cls` and type variables `params`.
+
+        :param params: Tuple of types the class . Given a generic class
+            `Model` with 2 type variables and a concrete model `Model[str, int]`,
+            the value `(str, int)` would be passed to `params`.
+        :return: New model class inheriting from `cls` with instantiated
+            types described by `params`. If no parameters are given, `cls` is
+            returned as is.
+
+        """
         cached = _generic_types_cache.get((cls, params))
         if cached is not None:
             return cached
@@ -50,16 +64,24 @@ def __class_getitem__(cls: Type[GenericModelT], params: Union[Type[Any], Tuple[T
             raise TypeError(f'Type {cls.__name__} must inherit from typing.Generic before being parameterized')
 
         check_parameters_count(cls, params)

--- Kel's top 3
  [1] Goal: ModelFactory must apply Annotated Field constraints to nested types
      Bug: Annotations for nested types are ignored
### Description

When a pydantic model contains a simple annotated attribute, the restrictions from the annotation are used for generating the value.

Example:
```python
Dimension = Annotated[int, Field(gt=1000, lt=2000)]

class Item(BaseModel):
 
      Way: Apply Annotated Field constraints to nested types in Pydantic factories
      - Identify the factory responsible for generating Pydantic models and the method that processes type annotations for field constraints.
      - Reproduce the issue by running the provided MCVE, observing that `Annotated` constraints on nested types (e.g., `list[Annotated[int, Field(...)]]`) are ignored, leading to validation errors.
      - Introduce a new helper function `get_annotation_metadata` to extract all metadata arguments from an `Annotated` type.
      - Modify `FieldMeta.from_type` to use the new `get_constraints_metadata` class method for extracting metadata from `Annotated` types, ensuring that subclasses can override this behavior. Also, ensure that `cls.from_type` is used recursively for children fields to maintain polymorphism.
      - Implement `PydanticFieldMeta.get_constraints_metadata` to specifically handle Pydantic v2's `FieldInfo` objects, unwrapping them to expose the actual `Field` metadata. This ensures that Pydantic `Field` constraints are correctly extracted when wrapped by `FieldInfo`.
      - Verify that the fix correctly applies `Annotated` constraints to nested types by running the provided tests.
  [2] Goal: Support generic dictionaries in serializable type method annotations
      "`dict[str, T]` as a field type is not supported"
* mashumaro version: 3.10
* Python version: 3.11.5

In a seemingly simple example with generics, close to the one presented in the docs, mashumaro seems to stumble on `dict[str, T]` annotation; wonder if this is intended, or a bug, or some misunde
      Way: resolve string annotations and forward refs in generic serialization hooks
      - Examine how function argument and return annotations are fetched in helper functions when inspecting serialization and deserialization hooks.
      - Convert string annotations returned by inspect.signature in annotation helper functions into ForwardRef objects attached to the function's module.
      - Update code builder forward ref evaluation to use the ForwardRef's module __dict__ as global namespace when available.
      - In packing and unpacking logic for annotated serializable types and serialization strategies, evaluate any ForwardRef before substituting generic type parameters.
      - Run the forward ref test suite covering generic serializable types and serialization strategies.
  [3] Goal: Support class invariants on subclasses of generic typing mappings
      Invariant on subclass of generic Mapping produces ValueError
An `invariant` decorator on a subclass of a generic `typing.Mapping` class produces a `ValueError`.  Using Python 3.8.5 and icontract 2.3.7.

Example file `foo.py`:
``` python
from icontract import invariant
from typing import Dict, I
      Way: exempt __new__ and handle missing self when instrumenting class methods
      - Identify class methods and dunder methods inspected and wrapped by invariant checkers when scanning a class's attributes.
      - Reproduce the failure by decorating a subclass of typing.Mapping (or any class triggering __new__ inspection) with an invariant decorator and instantiating it.
      - Add '__new__' to the ignored method name list in add_invariant_checks to prevent wrapping it as an instance method.
      - Update _find_self to safely handle cases where 'self' is not in param_names by catching ValueError before falling back to kwargs lookup.
      - Wrap invocations of _find_self in wrapper functions with try-except to raise an informative KeyError if 'self' is missing from both args and kwargs.
      - Execute test suite to confirm the fix works for generic mapping subclasses and special methods.

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

--- Kel's top 3
  [1] Goal: Support custom SSH host aliases in remote URLs for GitHub integration
      `git machete github anno-prs` doesn't work with custom GitHub URL
# Overview

I've stumbled upon some issues with GitHub recently: _sometimes_ for _some_ repos, `git push` wouldn't work. With trial-and-error, I found a reliable workaround for me:

```shellsession
$ git remote -v
origin	github:
      Way: support custom SSH host aliases via explicit git config overrides
      - Identify where remote URLs are parsed and where repository remotes are mapped to remote hosting provider metadata (organization, repository name, remote name).
      - Define configuration keys (e.g. `machete.github.remote`, `machete.github.organization`, `machete.github.repository`) allowing users to override automatic remote URL parsing when custom SSH host aliases or non-standard remote URLs are used.
      - Update remote and organization derivation logic to check for the custom configuration values first, returning them directly when present before falling back to parsing standard remote URLs.
      - Update remote parsing helper functions and callsites across command handlers so that remote resolution consistently accepts overrides or passes the remote context.
      - Add documentation and release notes describing the new git configuration keys for custom hosting URLs.
      - Run the GitHub integration test suite to verify commands succeed when custom config or standard remotes are configured.
  [2] Goal: Support git credential helper authentication for remote git repositories
      pull: pulling from private github dataregistry repo with git credential helper not working anymore
# Bug Report

<!--
## Issue name

Issue names must follow the pattern `command: description` where the command is the dvc command that you are trying to run. The description should describe the co
      Way: force dulwich backend for fetching experiments to support git credential helper
      - Locate where remote git repositories are cloned and where experiment references are fetched.
      - Modify `fetch_all_exps` to accept arbitrary keyword arguments (`**kwargs`) and pass them down to both `iter_remote_refs` and `scm.fetch_refspecs`.
      - Update the clone and pull operations in `dvc/scm.py` and `dvc/external_repo.py` to pass `backends=["dulwich"]` to `fetch_all_exps`.
      - Fix URL schema validation in `dvc/config_schema.py` to handle empty netlocs correctly by falling back to the local schema.
      - Verify the fix by running the test suite, specifically ensuring that filenames with colons and remote git operations work correctly.
  [3] Goal: Strip host names from default clone target directories for SSH URLs
      clone: over ssh - (default) target directory contains `:` causing git to error out
could probably add to confusion in #5829 but that one has other issues preventing it even to get there,  but would be seen there too eventually

`maint`:

```
$> datalad clone smaug:datalad                       
      Way: Parse SSH resource identifiers when deriving default clone target directory
      - Locate the helper function responsible for deriving the default clone destination directory name from a given source URL or resource identifier.
      - Inspect how different RI types (such as URL, DataLadRI, SSHRI, PathRI) are handled when determining path components vs falling back to raw path parsing.
      - Update the type check in `_get_installationpath_from_url` to include `SSHRI` alongside `URL` and `DataLadRI` so that SSH URLs (like `host:repo`) extract `ri.path` and omit the hostname and colon from the destination directory name.
      - Run the clone destination derivation unit tests to ensure SSH and other URL formats resolve to expected folder names.

=== TASK kislyuk__argcomplete-204 | repo kislyuk/argcomplete | python
--- Issue
Support for python -m module
Is it possible for argcomplete to work for modules run as `python -m module`? This is necessary to do once you start organizing your code in a module (rather than just a single script) to make relative imports work correctly, but I don't really want to make a separate entry point script if I don't have to. 

I am using global completion and it isn't working, at least not out of the box. 
--- Real fix (for your judgement only)
diff --git a/README.rst b/README.rst
index 1620461..b77e533 100644
--- a/README.rst
+++ b/README.rst
@@ -212,6 +212,10 @@ In global completion mode, you don't have to register each argcomplete-capable e
 will look for the string **PYTHON_ARGCOMPLETE_OK** in the first 1024 bytes of any executable that it's running
 completion for, and if it's found, follow the rest of the argcomplete protocol as described above.
 
+Additionally, completion is activated for scripts run as ``python <script>`` and ``python -m <module>``.
+This also works for alternate Python versions (e.g. ``python3`` and ``pypy``), as long as that version of Python has
+argcomplete installed.
+
 .. admonition:: Bash version compatibility
 
  Global completion requires bash support for ``complete -D``, which was introduced in bash 4.2. On OS X or older Linux
diff --git a/argcomplete/__init__.py b/argcomplete/__init__.py
index 029c6b8..25d4328 100644
--- a/argcomplete/__init__.py
+++ b/argcomplete/__init__.py
@@ -203,9 +203,13 @@ class CompletionFinder(object):
         comp_line = ensure_str(comp_line)
         cword_prequote, cword_prefix, cword_suffix, comp_words, last_wordbreak_pos = split_line(comp_line, comp_point)
 
-        if os.environ["_ARGCOMPLETE"] == "2":
-            # Shell hook recognized the first word as the interpreter; discard it
-            comp_words.pop(0)
+        # _ARGCOMPLETE is set by the shell script to tell us where comp_words
+        # should start, based on what we're completing.
+        # 1: <script> [args]
+        # 2: python <script> [args]
+        # 3: python -m <module> [args]
+        start = int(os.environ["_ARGCOMPLETE"]) - 1
+        comp_words = comp_words[start:]
 
         debug("\nLINE: '{l}'\nPREQUOTE: '{pq}'\nPREFIX: '{p}'".format(l=comp_line, pq=cword_prequote, p=cword_prefix),
               "\nSUFFIX: '{s}'".format(s=cword_suffix),
diff --git a/argcomplete/_check_module.py b/argcomplete/_check_module.py
new file mode 100644
index 0000000..9d14b3d
--
--- Kel's top 3
  [1] Goal: Fix protobuf module import when module path contains 'proto'
      Having proto as a module name makes things tricky
When I run the program with this command:

```
protobuf-uml-diagram --output doc --proto gs.proto.configs_data_pb2
```

I get:

```
ModuleNotFoundError: No module named 'gs_pb2'
```

After some initial confusion, I found the reason:

``
      Way: safely import protobuf modules with dot-proto in their names
      - Locate the module import logic where the `.proto` extension is replaced with `_pb2`.
      - Modify the import logic to only strip or replace `.proto` if it is at the end of the string, rather than using a global string replacement.
      - Wrap the module import call in a try-except block to log a clear error message when a `ModuleNotFoundError` occurs.
      - Run the test suite to verify that modules containing `.proto` in the middle of their names are imported correctly.
  [2] Goal: Support reloading modules with relative imports
      Reloading modules with relative imports fails
Structure to reproduce:

```
main.py
package/
   module.py
   module1.py
```

main.py:
-----
```
from package.module1 import add_more_text

def main():
    
    while True:
        s = input()
        print(add_more_text(s))

if __nam
      Way: Preserve module name fallback in code reloader initialization
      - Locate the module reloading implementation and identify where module attributes like name and filename are initialized.
      - Reproduce the module reload failure when reloading a module containing relative imports.
      - Fix the attribute fallback logic in the reload class initializer so that `mod.__name__` is used when `mod_name` is `None`, rather than evaluating the condition on `mod_name` directly.
      - Verify the module reload tests pass.
  [3] Goal: Fix help completion for commands using unknown argument parsers
      Backport bug in help completion handling of commands using argparser_with_unknown_args
During the autocompleter development I found a bug in handling of help completion that affects the 0.8.x line so I'm going to port those changes back to the python2 branch.
      Way: Store subcommands dictionary in decorator for commands with unknown arguments
      - Locate the `with_argparser_and_unknown_args` decorator where subcommand information is attached to command wrappers.
      - Run existing completion tests to observe failures related to subcommands missing `subcommands` dictionary.
      - Update `with_argparser_and_unknown_args` to attach a `subcommands` dictionary (mapping subcommand names to completers or None) instead of setting `subcommand_names`.
      - Run test suite to verify help and subcommand tab completion passes.

=== TASK MatterMiners__tardis-150 | repo MatterMiners/tardis | python
--- Issue
[Slurm adapter] Need to specify custom (GRES) flags for sbatch
Some use cases in Slurm can only be covered with Generic Resource ([GRES](https://slurm.schedmd.com/gres.html)) scheduling. 
One such example is the reservation of scratch disk space:
https://bugs.schedmd.com/show_bug.cgi?id=2549#c9

Since the name of the GRES is completely arbitrary and up to the HPC-site, it is probably best to have a way to pass arbitrary parameters to `sbatch`. The best place (configuration-wise) is likely the `MachineTypeConfiguration`, so it is similar to the `jdl` funcitonality in HTCondor. 

I ma not yet sure if it is better to allow fully arbitrary parameters or have a dedicated `gres` configuration stanza which directly takes the `gres` to reserve. 

Pinging also @wiene 
--- Real fix (for your judgement only)
diff --git a/docs/source/adapters/site.rst b/docs/source/adapters/site.rst
index f13b12b..ef9a436 100644
--- a/docs/source/adapters/site.rst
+++ b/docs/source/adapters/site.rst
@@ -16,9 +16,9 @@ Site Adapter
     :py:class:`~tardis.resources.dronestates.AvailableState` before draining it. If no value is given, infinite lifetime
     is assumed. Multiple sites are supported by using SequenceNodes.
 
-.. note::
-    Even a minimum lifetime is set, it is not guaranteed that the :py:class:`~tardis.resources.drone.Drone` is not
-    drained due to a dropping demand for it before its minimum lifetime is exceeded.
+    .. note::
+        Even if a minimum lifetime is set, it is not guaranteed that the :py:class:`~tardis.resources.drone.Drone` is not
+        drained due to its dropping demand before its minimum lifetime is exceeded.
 
 
 Generic Site Adapter Configuration
@@ -414,6 +414,23 @@ Available adapter configuration options
     |                | Default: ShellExecutor is used!                                                             |                 |
     +----------------+---------------------------------------------------------------------------------------------+-----------------+
 
+Available machine type configuration options
+~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
+
+.. content-tabs:: left-col
+
+    +----------------+--------------------------------------------------------------------------------------------------+-----------------+
+    | Option         | Short Description                                                                                | Requirement     |
+    +================+==================================================================================================+=================+
+    | Walltime       | Expected walltime of drone                                                                       |  **Required**   |
+    +----------------+----------------------------------------------------------------------------
--- Kel's top 3
  [1] Goal: Allow omitting or customizing GPU parameters in generated Slurm submission scripts
      How to comment a slurm variable?
Hi All,

I observe the following error which is due to added: "#SBATCH --gpus-per-node=4" line in the generated slurm script.

Error : submitit.core.utils.FailedJobError: sbatch: error: Batch job submission failed: Invalid generic resource (gres) specification

      Way: make Slurm submission script parameters optional by changing defaults to None
      - Identify the function generating the Slurm submission script headers and inspect parameter defaults.
      - Change the default value of parameters intended to be optional (such as ntasks_per_node) from a hardcoded integer to Optional[int] = None.
      - Run the Slurm script generation tests to confirm default batch header output matches expected optional behavior.
  [2] Goal: Support fractional memory specifications in Slurm site adapter
      Slurm siteadapter fails with fractional GB of memory
@wiene and I found that trying to define drones with non-integer `Memory` in their `MachineMetaData` works fine with most siteadapters (e.g. HTCondor), but breaks with Slurm:
```
2021-07-11 03:32:12 [WARNING][cobald.runtime.tardis.adapters.sites
      Way: Convert fractional gigabyte memory to integer megabytes for Slurm
      - Identify where Slurm sbatch arguments and environment variables are constructed.
      - Implement a helper utility function `convert_to` that safely attempts type conversion with a fallback value on ValueError.
      - Update Slurm adapter command generation to format `--mem` as integer megabytes (`int(Memory * 1024)mb`) instead of gigabytes, and cast drone environment variables to integers when convertible.
      - Run the Slurm adapter unit tests to verify the formatted sbatch options and environment variables match the integer megabytes representation.
  [3] Goal: Allow overriding executor parameters using generic and auto names
      Cannot override parameters with their generic/auto name
`local_cpus_per_task` does not work while `slurm_cpus_per_task` does.
@gwenzek any reason for this? Can we make it work too?

      Way: support prefixed overrides for generic configuration parameters in executor wrappers
      - Identify where prefixed executor parameters (such as `local_<param>` or `slurm_<param>`) are validated and mapped to backend-specific and generic arguments.
      - Update validation logic so prefixed arguments recognize both backend-specific parameters and the set of generic parameters as valid.
      - Before converting generic parameters, extract and override generic keyword arguments matching the current backend from the prefixed keyword arguments.
      - Ensure only non-generic backend-specific parameters are assigned directly to the backend parameters dictionary during the subsequent update step.
      - Run the auto executor test suite to verify overrides with generic names behave correctly across executor types.

=== TASK equinor__webviz-config-656 | repo equinor/webviz-config | python
--- Issue
Intelligent loading of plugins through entry_points
`webviz-config` is using a quite standard way of discovering and loading plugins, i.e. https://github.com/equinor/webviz-config/blob/5139c3039f0198ba23b55ec2ca9e7019ae6dd54d/webviz_config/plugins/__init__.py#L51-L53

However, we could change this slightly to easily improve loading time on systems where many plugins are installed (but only a small subset of them are used in an actual user configuration file) by only loading the modules / `entry_points` related to plugins used in the given `yaml` file.

Related to #185.
--- Real fix (for your judgement only)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index ee81118..40f207d 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -12,6 +12,7 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 - [#648](https://github.com/equinor/webviz-config/pull/648) - Allow `blob:` in `script-src` CSP in order to enable web worker usage in Dash components.
 - [#652](https://github.com/equinor/webviz-config/pull/652) - Enabled support for LaTeX math/equations in markdown.
 - [#653](https://github.com/equinor/webviz-config/pull/653) - Reduce time for running `webviz --help` by lazy importing top level entrypoints.
+- [#656](https://github.com/equinor/webviz-config/pull/656) - Further reduce startup time by only loading plugin entrypoints used in the application.
 
 ### Added
 - [#644](https://github.com/equinor/webviz-config/pull/644) - Added option to download tables in `DataTable` and `PivotTable`.
diff --git a/webviz_config/_config_parser.py b/webviz_config/_config_parser.py
index b64b6d7..c8fd588 100644
--- a/webviz_config/_config_parser.py
+++ b/webviz_config/_config_parser.py
@@ -9,7 +9,6 @@ import yaml
 
 import webviz_config.plugins
 from .utils import terminal_colors
-from .utils._get_webviz_plugins import _get_webviz_plugins
 from . import _deprecation_store as _ds
 
 SPECIAL_ARGS = ["self", "app", "webviz_settings", "_call_signature"]
@@ -211,9 +210,7 @@ class ParserError(Exception):
 
 class ConfigParser:
 
-    STANDARD_PLUGINS = [
-        name for (name, _) in _get_webviz_plugins(webviz_config.plugins)
-    ]
+    INSTALLED_PLUGINS = webviz_config.plugins.__all__
 
     def __init__(self, yaml_file: pathlib.Path):
 
@@ -436,13 +433,13 @@ class ConfigParser:
                     plugin_variables = next(iter(plugin.values()))
                     kwargs = {} if plugin_variables is None else {**plugin_variables}
 
-                    if plugin_name not in ConfigParser.STANDARD_PLUGINS:
+                    if plugin_name not in ConfigParser.INSTALLED_PL
--- Kel's top 3
  [1] Goal: Expose the current run mode to plugins during build and runtime
      Expose instance of `WebvizRunMode` during build and runtime
Plugins may have code in their` __init__` method which isn't required to run when building portables. E.g. if their are methods called during `__init__` which are also included in `add_webvizstore`, the code will be executed twice during po
      Way: expose global instance info singleton with two-stage initialization
      - Inspect the instance info class and how runtime configuration (run mode, dash app, theme, storage path) is passed to registries and templates.
      - Refactor the instance info class to use a singleton pattern with an uninitialized `__init__` and an explicit `initialize(dash_app, run_mode, theme, storage_folder)` method enforcing type checks and one-time initialization.
      - Export a global singleton instance of the info class in the module and top-level package namespace.
      - Remove the instance info argument from factory registry initialization, deprecating the property access on the registry in favor of the global instance.
      - Update code generation templates (e.g. build and runtime jinja templates) to initialize the global instance info singleton early with the Dash app, run mode enum, theme, and storage folder.
      - Run the unit tests covering factory registry and instance info behavior.
  [2] Goal: Optimize generated client module loading by reducing `__init__.py` imports
      Load of module and client extremely slow.
## Description

I have a fairly complex GraphQL repository running on Hasura, that might have hundreds if not thousands of types and definitions. Right now I implemented this library for some specific queries and use cases, and all of those types available
      Way: Reduce generated client __init__.py imports for faster loading
      - Identify the generated client package's `__init__.py` file and observe its content and the time it takes to import the client module.
      - Locate the configuration for the code generation tool (e.g., `pyproject.toml` or `ariadne_codegen.yaml`).
      - Add `ariadne_codegen.contrib.no_reimports.NoReimportsPlugin` to the list of plugins in the code generation configuration.
      - Regenerate the client code using the updated configuration.
      - Verify that the client module loads quickly and that the application functions correctly.
  [3] Goal: Load plugins last during library initialization
      Plugins should be last thing loaded in the SeleniumLibrary __init__
In the library init, plugins are loaded after the Event Firing WebDriver. There was discussion in the slack that user wants to plugin to also add a Event Firing WebDriver class to the library. But this is not currently possible beca
      Way: Ensure plugin modifications persist by reordering library initialization
      - Understand that plugins' modifications to the Event Firing WebDriver are being overwritten because the WebDriver is initialized after plugins are loaded.
      - Locate the main library initialization method, `__init__`, within the `SeleniumLibrary` class.
      - Identify the code block responsible for initializing the `event_firing_webdriver` attribute and the code block responsible for parsing and loading `plugins`.
      - Reorder the initialization logic within `SeleniumLibrary.__init__` so that the `event_firing_webdriver` is initialized *before* plugins are parsed and loaded. This allows plugins to interact with an already established `event_firing_webdriver` instance.
      - Verify that the reordering correctly allows plugins to interact with the Event Firing WebDriver without their changes being overwritten.

=== TASK nipy__nipype-3248 | repo nipy/nipype | python
--- Issue
Mrtrix3's dwibiascorrect interface crashes due to "-"
Interface crashes because of "-" before algorithm specification.

```python
RuntimeError: Command:
```
```bash
dwibiascorrect -mask <mask_file> -ants <in_file> <out_file>

Standard output:

Standard error:

Error: argument algorithm: invalid choice: <in_file> (choose from 'ants', 'fsl')
Usage: dwibiascorrect algorithm [ options ] ...
       (Run dwibiascorrect -help for more information)

Return code: 1
```
The expected behavior would be to execute the following command:
```bash
dwibiascorrect ants [options] <in_file> <out_file>
```
To replicate the behavior, simply execute:
```python
import nipype.interface.mrtrix3 as mrt
bc = mrt.DWIBiasCorrect()
bc.inputs.in_file = <in_file>
bc.inputs.out_file = <out_file>
bc.inputs.use_ants = True
bc.run()
```
Running on a 64 bit Linux machine (Ubuntu)

```
{'commit_hash': '72945ef34',
 'commit_source': 'installation',
 'networkx_version': '2.4',
 'nibabel_version': '3.0.0',
 'nipype_version': '1.5.0',
 'numpy_version': '1.19.0',
 'pkg_path': '/home/groot/Projects/labbing/venv/lib/python3.7/site-packages/nipype',
 'scipy_version': '1.5.0',
 'sys_executable': '/home/groot/Projects/labbing/venv/bin/python',
 'sys_platform': 'linux',
 'sys_version': '3.7.8 (default, Jun 29 2020, 04:26:04) \n[GCC 9.3.0]',
 'traits_version': '6.1.1'}
```
- My python environment outside container

--- Real fix (for your judgement only)
diff --git a/.zenodo.json b/.zenodo.json
index cb9ffacc3..a8ae4c70e 100644
--- a/.zenodo.json
+++ b/.zenodo.json
@@ -697,6 +697,10 @@
       "affiliation": "Sagol School of Neuroscience, Tel Aviv University",
       "name": "Baratz, Zvi"
     },
+    {
+      "affiliation": "Sagol School of Neuroscience, Tel Aviv University",
+      "name": "Ben-Zvi, Gal"
+    },
     {
       "name": "Matsubara, K"
     },
diff --git a/nipype/interfaces/mrtrix3/preprocess.py b/nipype/interfaces/mrtrix3/preprocess.py
index 9384ef43c..aa3347c7f 100644
--- a/nipype/interfaces/mrtrix3/preprocess.py
+++ b/nipype/interfaces/mrtrix3/preprocess.py
@@ -181,15 +181,17 @@ class DWIBiasCorrectInputSpec(MRTrix3BaseInputSpec):
     )
     in_mask = File(argstr="-mask %s", desc="input mask image for bias field estimation")
     use_ants = traits.Bool(
-        argstr="-ants",
+        argstr="ants",
         mandatory=True,
         desc="use ANTS N4 to estimate the inhomogeneity field",
+        position=0,
         xor=["use_fsl"],
     )
     use_fsl = traits.Bool(
-        argstr="-fsl",
+        argstr="fsl",
         mandatory=True,
         desc="use FSL FAST to estimate the inhomogeneity field",
+        position=0,
         xor=["use_ants"],
     )
     bias = File(argstr="-bias %s", desc="bias field")
@@ -224,14 +226,20 @@ class DWIBiasCorrect(MRTrix3Base):
     >>> bias_correct.inputs.in_file = 'dwi.mif'
     >>> bias_correct.inputs.use_ants = True
     >>> bias_correct.cmdline
-    'dwibiascorrect -ants dwi.mif dwi_biascorr.mif'
+    'dwibiascorrect ants dwi.mif dwi_biascorr.mif'
     >>> bias_correct.run()                             # doctest: +SKIP
     """
 
     _cmd = "dwibiascorrect"
     input_spec = DWIBiasCorrectInputSpec
     output_spec = DWIBiasCorrectOutputSpec
-
+    def _format_arg(self, name, trait_spec, value):
+        if name in ("use_ants", "use_fsl"):
+            ver = self.version
+            # Changed in version 3.0, after release candidates
+            if v
--- Kel's top 3
  [1] Goal: Position the algorithm argument correctly in MRtrix3 dwi2response command generation
      Mrtrix3 `dwi2response` - bad algorithm argument position
### Summary

Th Mrtrix3 `dwi2response` CL wrapper generates the following runtime error:
```shell
dwi2response:
mrinfo: [ERROR] no diffusion encoding information found in image "<DWI_FILE>"
dwi2response: [ERROR] Script requires diffusion
      Way: Position command line arguments correctly in command generation wrappers
      - Locate the command-line interface wrapper definition for the tool (e.g., MRtrix3 dwi2response) and find where the algorithm argument position is defined.
      - Modify the position parameter of the algorithm argument in the input specification class (e.g., ResponseSDInputSpec) from a late position (like -6) to an early position (like 1) so it immediately follows the command name.
      - Update any associated doctests or unit tests that assert the exact generated command-line string to reflect the new argument order.
      - Run the interface tests to verify that the generated command line matches the expected order and that the input specification is correct.
  [2] Goal: Correct the command line argument order for MeshFix fine-tuning parameters
      Meshfix interface inverting command parameters
### Summary
Hi,
While using the MeshFix interface, I received this error:
```shell
RuntimeError: Command:
meshfix /path/to/file.off -a 2.000000 0.200000 --fineTuneIn  4 --shells 2 -o file_fixed.off -q
Standard output:
Fixing asin tolerance to 3.4
      Way: fix command line parameter positioning for multi-argument flag traits
      - Locate the interface input specification for the tool in the codebase.
      - Check the command-line flag definitions for the fine-tuning traits (`finetuning_inwards`, `finetuning_outwards`, `finetuning_distance`, and `finetuning_substeps`) to verify `argstr` values and argument positions.
      - Fix the command flag for `finetuning_outwards` from `--fineTuneIn ` to `--fineTuneOut ` and set explicit relative `position` attributes on the flag (`position=-3`), distance (`position=-2`), and substeps (`position=-1`) traits so command line parameters assemble in the expected order.
      - Run interface input specification tests to confirm correct command string generation.
  [3] Goal: Handle unsupported ioctl errors when creating file reflinks on Linux
       [Errno 25] Inappropriate ioctl for device 
Dear Team,

we have been running dvc from a CircleCi job **successfully** for a while now. Recently, we have suddenly not been able to pull data anymore.                                                  

We are running a Linux machine (X large) on Cir
      Way: handle unsupported ioctl errors when creating file reflinks
      - Locate the file linking and transfer logic, typically found in filesystem utility modules.
      - Refactor the linking logic to catch all potential errors (such as `OSError`, `AttributeError`, and `RemoteActionNotImplemented`) during a link attempt, and raise a unified `OSError` with `errno.ENOTSUP` to signal that the link type is unsupported.
      - Implement a robust `test_links` utility that creates temporary files in the correct parent directories, attempts each link type, and returns only the supported ones.
      - Update the system info and checkout modules to use the new `test_links` and `transfer` utilities instead of manual, error-prone link checks.
      - Run the test suite to verify that unsupported link types are gracefully skipped and fallback strategies (like copying) are used.

=== TASK cltk__cltk-661 | repo cltk/cltk | python
--- Issue
Add Swadesh list for Tocharian B
Data here: https://en.wiktionary.org/wiki/Appendix:Tocharian_B_Swadesh_list

Follow the pattern here: https://github.com/cltk/cltk/blob/4bf42fc9a19cf711f7eb1e908850fb64c65b0582/cltk/corpus/swadesh.py#L6

Call the list `swadesh_txb`.
--- Real fix (for your judgement only)
diff --git a/cltk/corpus/swadesh.py b/cltk/corpus/swadesh.py
index 9d1b3759..0c1a960d 100644
--- a/cltk/corpus/swadesh.py
+++ b/cltk/corpus/swadesh.py
@@ -7,6 +7,8 @@ swadesh_la = ['ego', 'tū', 'is, ea, id', 'nōs', 'vōs', 'eī, iī, eae, ea', '
 
 swadesh_gr = ['ἐγώ', 'σύ', 'αὐτός, οὗ, ὅς, ὁ, οὗτος', 'ἡμεῖς', 'ὑμεῖς', 'αὐτοί', 'ὅδε', 'ἐκεῖνος', 'ἔνθα, ἐνθάδε, ἐνταῦθα', 'ἐκεῖ', 'τίς', 'τί', 'ποῦ, πόθι', 'πότε, πῆμος', 'πῶς', 'οὐ, μή', 'πᾶς, ἅπᾱς', 'πολύς', 'τις', 'ὀλίγος, βαιός, παῦρος', 'ἄλλος, ἕτερος', 'εἷς', 'δύο', 'τρεῖς', 'τέσσαρες', 'πέντε', 'μέγας', 'μακρός', 'εὐρύς', 'πυκνός', 'βαρύς', 'μῑκρός', 'βραχύς', 'στενός', 'μανός', 'γυνή', 'ἀνήρ', 'ἄνθρωπος', 'τέκνον, παῖς, παιδίον', 'γυνή', 'ἀνήρ', 'μήτηρ', 'πατήρ', 'ζῷον', 'ἰχθύς', 'ὄρνις, πετεινόν', 'κύων', 'φθείρ', 'ὄφις', 'ἑρπετόν, σκώληξ, ἕλμινς', 'δένδρον', 'ὕλη', 'βακτηρία, ῥάβδος', 'καρπός', 'σπέρμα', 'φύλλον', 'ῥίζα', 'φλοιός', 'ἄνθος', 'χλόη', 'δεσμός, σχοινίον', 'δέρμα', 'κρέας', 'αἷμα', 'ὀστοῦν', 'δημός', 'ᾠόν', 'κέρας', 'οὐρά, κέρκος', 'πτερόν', 'θρίξ, κόμη', 'κεφαλή', 'οὖς', 'ὀφθαλμός', 'ῥίς', 'στόμα', 'ὀδούς', 'γλῶσσα', 'ὄνυξ', 'πούς', 'κῶλον, σκέλος', 'γόνυ', 'χείρ', 'πτέρυξ', 'γαστήρ, κοιλία', 'ἔντερα, σπλάγχνα', 'αὐχήν, τράχηλος', 'νῶτον', 'μαστός, στῆθος', 'καρδία', 'ἧπαρ', 'πίνω', 'ἐσθίω, ἔφαγον', 'δάκνω', 'σπάω', 'πτύω', 'ἐμέω', 'φυσάω', 'πνέω', 'γελάω', 'βλέπω, ὁράω, εἶδον', 'ἀκούω, ἀΐω', 'οἶδα, γιγνώσκω', 'νομίζω, δοκέω, νοέω, οἴομαι', 'ὀσφραίνομαι', 'φοβέομαι', 'καθεύδω, εὕδω, εὐνάζομαι, κοιμάομαι, ἰαύω', 'ζάω, βιόω, οἰκέω', 'ἀποθνῄσκω, θνῄσκω, τελευτάω, ὄλομαι', 'ἀποκτείνω, ἔπεφνον', 'μάχομαι', 'θηρεύω, θηράω, ἰχνεύω, κυνηγετέω, κυνηγέω, σεύω', 'τύπτω', 'τέμνω', 'σχίζω', 'κεντέω', 'κνάω', 'ὀρύσσω, σκᾰ́πτω', 'νέω, κολυμβάω', 'πέτομαι', 'περιπατέω, πατέω, στείχω, βαίνω, βαδίζω, πεζεύω, πορεύω', 'ἱκνέομαι, ἵκω, ἔρχομαι, εἶμι', 'κεῖμαι', 'καθίζω', 'ἵστημι', 'τρέπω', 'πίπτω', 'παρέχω, δίδωμι', 'ἔχω', 'πιέζω', 'τρίβω', 'λούω, πλύνω, νίπτω', 'ἀπομάσσω', 'ἕλκω', 'ὠθέω', 'ῥίπτω, βάλλω', 'δέω', 'ῥάπτω',
--- Kel's top 3
  [1] Goal: Support wrapped XML list elements without intermediate wrapper classes
      Support for a wrapped list
XML data structures commonly wrap element and primitive collections. For instance, a library may have some books and magazines as well; exemplified below.
```xml
<Library>
    <Books>
        <Book>book 1</Book>
        <Book>book 2</Book>
    </Books>
    <Magazine
      Way: Add field wrapper metadata for XML collection elements
      - Extend dataclass model metadata representations (XmlVar and XmlMeta) to store an optional wrapper QName and mapping of wrapper names to wrapped variables.
      - Update XmlVarBuilder and XmlMetaBuilder to extract 'wrapper' from field metadata, validate that wrapped fields use collection types (list, set, tuple), resolve default namespaces into wrapper QNames, and index wrappers in XmlMeta.
      - Introduce WrapperNode parser node and update parser base logic (NodeParser) to recognize wrapper tags and dispatch parsing of wrapped child elements directly into the parent model's collection fields.
      - Update XML serializer handlers to emit opening and closing wrapper tags around wrapped collection elements.
      - Improve XmlContext xsi/class matching heuristics when scoring field name diffs against candidates.
      - Run test suite to verify parsing, serialization, and metadata building for wrapped collections.
  [2] Goal: Serialize DelimitedList schema fields as comma-separated query parameters in OpenAPI specifications
      Unexpected behaviour for webargs.fields.DelimitedList
Taking the following schema:
```python
from marshmallow import Schema
from webargs import fields

class MyScghema(Schema):
    a = fields.String()
    b = fields.String()

    sort_by = fields.DelimitedList(fields.String())
```

Accor
      Way: Serialize DelimitedList fields as comma-separated query parameters in OpenAPI
      -  Locate where the OpenAPI specification generation and marshmallow/apispec plugin integration are configured.
      - Define a parameter attribute function that checks if a field is an instance of `webargs.fields.DelimitedList`. For OpenAPI 2.0, set `collectionFormat` to `csv`. For OpenAPI 3.0+, set `explode` to `False` and `style` to `form`.
      - Register the new parameter attribute function with the apispec marshmallow plugin converter using `add_parameter_attribute_function`.
      - Run the test suite to verify that DelimitedList fields are correctly documented as comma-separated query parameters in both OpenAPI 2.0 and 3.0+.
  [3] Goal: Convert a list of Mapping objects into a MappingSet or MappingSetDoc
      What to do when I have a list of `Mapping` objects?
I can't find anywhere in sssom-py a utility function that will take a list of `Mapping` objects and turn them into either a MSDF, MappingSet, or MappingSetDoc.

OAK implemented its own janky solution for this, but we should provide something firs
      Way: Convert a list of Mapping objects into a MappingSet or MappingSetDoc
      - Locate the utility module and class representing mapping set dataframes, and identify where mapping set documents are parsed or constructed.
      - Add class methods to the mapping set dataframe class to instantiate it from a list of mappings, a mapping set, or a mapping set document, and add instance methods to convert back to these structures.
      - Refactor existing standalone conversion functions to delegate to the newly added class methods to avoid code duplication.
      - Run the test suite to verify that converting a list of mappings to a mapping set dataframe and back works correctly.

=== TASK pydantic__pydantic-2461 | repo pydantic/pydantic | python
--- Issue
dataclass config fields not warranted in json schema (1.8)
### Checks

* [X] I added a descriptive title to this issue
* [X] I have searched (google, github) for similar issues and couldn't find anything
* [X] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and still think this is a bug

# Bug

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
             pydantic version: 1.8
            pydantic compiled: True
                 install path: /Users/dafcok/miniconda3/lib/python3.6/site-packages/pydantic
               python version: 3.6.8 |Anaconda, Inc.| (default, Dec 29 2018, 19:04:46)  [GCC 4.2.1 Compatible Clang 4.0.1 (tags/RELEASE_401/final)]
                     platform: Darwin-19.6.0-x86_64-i386-64bit
     optional deps. installed: ['devtools', 'dotenv', 'email-validator', 'typing-extensions'
```
```py
from pydantic import BaseModel
from pydantic.dataclasses import dataclass

# works
class A(BaseModel):
    a: str

    class Config:
        fields = {"a": {"description": "descr"}}

assert A.schema()["properties"]["a"]["description"] == "descr"

# works on 1.7.3
# fails on 1.8
@dataclass(config=A.Config)
class Ad:
    a: str

assert Ad.__pydantic_model__.schema()["properties"]["a"]["description"] == "descr"
```
```bash
Traceback (most recent call last):
  File "tests/test_schema_descr.py", line 24, in <module>
    assert Ad.__pydantic_model__.schema()["properties
--- Real fix (for your judgement only)
diff --git a/pydantic/fields.py b/pydantic/fields.py
--- a/pydantic/fields.py
+++ b/pydantic/fields.py
@@ -106,7 +106,8 @@ class FieldInfo(Representation):
         'extra',
     )
 
-    __field_constraints__ = {  # field constraints with the default value
+    # field constraints with the default value, it's also used in update_from_config below
+    __field_constraints__ = {
         'min_length': None,
         'max_length': None,
         'regex': None,
@@ -153,6 +154,20 @@ def get_constraints(self) -> Set[str]:
         """
         return {attr for attr, default in self.__field_constraints__.items() if getattr(self, attr) != default}
 
+    def update_from_config(self, from_config: Dict[str, Any]) -> None:
+        """
+        Update this FieldInfo based on a dict from get_field_info, only fields which have not been set are dated.
+        """
+        for attr_name, value in from_config.items():
+            try:
+                current_value = getattr(self, attr_name)
+            except AttributeError:
+                # attr_name is not an attribute of FieldInfo, it should therefore be added to extra
+                self.extra[attr_name] = value
+            else:
+                if current_value is self.__field_constraints__.get(attr_name, None):
+                    setattr(self, attr_name, value)
+
     def _validate(self) -> None:
         if self.default not in (Undefined, Ellipsis) and self.default_factory is not None:
             raise ValueError('cannot specify both default and default_factory')
@@ -354,17 +369,20 @@ def _get_field_info(
                 raise ValueError(f'cannot specify multiple `Annotated` `Field`s for {field_name!r}')
             field_info = next(iter(field_infos), None)
             if field_info is not None:
+                field_info.update_from_config(field_info_from_config)
                 if field_info.default not in (Undefined, Ellipsis):
                     raise ValueError(f'`Field` default cannot be set in `
--- Kel's top 3
  [1] Goal: Add missing type annotations to model base config fields
      Incomplete type annotations in BaseConfig yield mypy errors when Config is defined from BaseConfig
### Checks

* [X] I added a descriptive title to this issue
* [X] I have searched (google, github) for similar issues and couldn't find anything
* [X] I have read and followed [the docs](https://py
      Way: Add explicit type annotations to config class attributes
      - Locate base config class and identify attributes lacking explicit type annotations (e.g., initialized to None, boolean, or enum defaults) and check how validators consume defaults.
      - Add explicit type annotations to class attributes in the base configuration class (such as Optional[str] for title, int for min_anystr_length with default 0, Optional[int] for max_anystr_length, and bool for boolean flags).
      - Update string length validators to handle explicit 0 length limits correctly using explicit `is not None` checks rather than truthy `or` fallbacks.
      - Update documentation to reflect accurate default values (e.g., max_anystr_length default is None).
      - Run the test suite to verify string length validation with zero length limits and type checking behavior.
  [2] Goal: Fix fake data generation for Optional type fields in Pydantic models
      AttributeError: 'NoneType' object has no attribute 'fake'
**Describe the bug**

In my pydantic data model I have the following attribute `X: List[Optional[types.Float32]]`. When I call fake() of this class I get the error 'AttributeError: 'NoneType' object has no attribute 'fake''

**To Reproduc
      Way: Assign internal_field for Union item types in ListField to support fake data generation
      - Locate ListField class definition and its __init__ and fake methods where item types are parsed.
      - Reproduce the AttributeError by calling fake() on a model containing a List of Optional/Union types.
      - Assign UnionField instance to self.internal_field when items_type is a union in ListField, and extract self.items_type from self.internal_field.get_avro_type() in both branches.
      - Run test suite to verify fake data generation for List of Optional types succeeds.
  [3] Goal: Allow instantiating messages generated with pydantic dataclasses
      pydantic option broken
### Summary

Instantiating messages generated with pydantic option throws exceptions.

### Protobuf schema

```proto
syntax = "proto3";

package prototest.v1;

message One {
  uint32 message = 1;
}

message Two {
  string message = 1;
}

message Message {
 
      Way: support Python 3.10 union type hints in message default generators
      - Locate where field default generator functions inspect dataclass field type hints and find where `issubclass(t, Enum)` fails when `t` is a `types.UnionType`.
      - Import `types.UnionType` on Python 3.10+ (falling back to a dummy class on earlier versions) so pipe union types can be identified.
      - Update field default generation to check if the type hint is an instance of `UnionType` or has `__origin__` matching `Union`, returning `type(None)` before reaching `issubclass` checks.
      - Update modern typing compiler to wrap Python 3.10+ union and generic type annotations in forward-reference strings and import iterable collections from `collections.abc`.
      - Run test suite to verify typing compiler formatting and type evaluation on Python 3.10+ types.

=== TASK dandi__dandi-cli-208 | repo dandi/dandi-cli | python
--- Issue
organize: -f "auto"
ATM we default to "dry" mode, and https://www.dandiarchive.org/handbook/10_using_dandi/ instructs to use "symlink" after running  a dry.
I think by default we should do "auto" which would either symlink, hardlink, or copy if not possible (test on the filesystem within that folder on some bogus file to see if symlinks/hardlinks are supported).
--- Real fix (for your judgement only)
diff --git a/dandi/cli/cmd_organize.py b/dandi/cli/cmd_organize.py
index 8c9972d9..9b0c0037 100644
--- a/dandi/cli/cmd_organize.py
+++ b/dandi/cli/cmd_organize.py
@@ -25,12 +25,13 @@ from ..consts import dandiset_metadata_file, file_operation_modes
     "-f",
     "--files-mode",
     help="If 'dry' - no action is performed, suggested renames are printed. "
-    "I 'simulate' - hierarchy of empty files at --local-top-path is created. "
-    "Note that previous layout should be removed prior this operation.  The "
-    "other modes (copy, move, symlink, hardlink) define how data files should "
-    "be made available.",
+    "If 'simulate' - hierarchy of empty files at --local-top-path is created. "
+    "Note that previous layout should be removed prior this operation.  "
+    "If 'auto' - whichever of symlink, hardlink, copy is allowed by system. "
+    "The other modes (copy, move, symlink, hardlink) define how data files "
+    "should be made available.",
     type=click.Choice(file_operation_modes),
-    default="dry",
+    default="auto",
     show_default=True,
 )
 @click.argument("paths", nargs=-1, type=click.Path(exists=True))
@@ -41,7 +42,7 @@ def organize(
     dandiset_path=None,
     dandiset_id=None,
     invalid="fail",
-    files_mode="dry",
+    files_mode="auto",
     devel_debug=False,
 ):
     """(Re)organize files according to the metadata.
@@ -84,6 +85,7 @@ def organize(
         filter_invalid_metadata_rows,
         populate_dataset_yml,
         create_dataset_yml_template,
+        detect_link_type,
     )
     from ..metadata import get_metadata
     from ..dandiset import Dandiset
@@ -210,6 +212,9 @@ def organize(
     if not op.exists(dandiset_path):
         act(os.makedirs, dandiset_path)
 
+    if files_mode == "auto":
+        files_mode = detect_link_type(dandiset_path)
+
     dandiset_metadata_filepath = op.join(dandiset_path, dandiset_metadata_file)
     if op.lexists(dandiset_metadata_filepath):
         if dandiset_id is not Non
--- Kel's top 3
  [1] Goal: Add a dry run mode that outputs diffs without modifying files
      feature request: dryrun mode?
Would it be possible to support a mode that doesn't modify files in place? Perhaps just printing a diff to stdout about what would change, and then exit status based on whether any files would be modified?
      Way: add dry-run CLI flag to output diffs without modifying files
      - Add a dry-run flag (`-d`, `--dry-run`) to the CLI argument parser and propagate its state to global configuration state.
      - In the CLI entrypoint, print an informational message when dry-run mode is enabled before processing files.
      - In the file rewriting logic, check the dry-run state: if active, output a `difflib.unified_diff` of the original and transformed content instead of writing the updated content to disk.
      - Verify the dry-run behavior using integration tests.
  [2] Goal: Canonicalize paths to prevent duplicate files when debugging symlinked Python code
      Regression compared to ptvsd: debugging python running in a bazel runtime sandbox causes duplicate tabs to open
## Context

When using python in a bazel sandbox (`bazel run //python/target`), bazel symlinks the required files into a 'runfiles' sandbox. My approach to debugging is to run it under t
      Way: Resolve symlinks in debugger paths to prevent duplicate files
      - Locate the file path normalization logic in the debugger's file utility module.
      - Introduce a global configuration option `_global_resolve_symlinks` (and a setter function) to control whether symlinks should be resolved.
      - Modify the path normalization function to use `os.path.realpath` instead of `os.path.abspath` when `_global_resolve_symlinks` is enabled.
      - Update the JSON command processor to parse the `resolveSymlinks` argument from incoming debug adapter protocol requests and apply it.
      - Enhance the JSON schema validator to support converting string values to numbers when validating numeric types.
      - Verify the changes by running the messaging and type conversion tests.
  [3] Goal: Enable unprivileged directory symlink creation on Windows
      Symlinks on Modern Windows with latest node/npm.
Theree are not only two options to creating a symlink. Either Node should change it's default behavior for the symlink type 'dir', or module makes should start using 'junction' instead of 'dir' when on windows. The symlink 'dir' on Windows does not be
      Way: Default to NTFS junctions for directory symlinks on Windows
      - Locate the file streaming logic responsible for creating symlinks and inspect how symlink type ('dir' vs 'file') and relative target paths are determined.
      - Detect if the current operating platform is Windows (`os.platform() === 'win32'`).
      - Add support for a `useJunctions` option (defaulting to `true` on Windows for directories, `false` elsewhere) and resolve symlink directory type to 'junction' when active.
      - Prevent converting the target path to a relative path when `useJunctions` is active, as NTFS junctions do not support relative target paths.
      - Run the test suite to verify symlinks and junctions are handled correctly across file modes and platforms.

=== TASK pydantic__pydantic-2094 | repo pydantic/pydantic | python
--- Issue
TypeError during generation schema for model field (type Callable) with provided default value.
### Checks

* [x] I added a descriptive title to this issue
* [x] I have searched (google, github) for similar issues and couldn't find anything
* [x] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and still think this is a bug

<!-- Sorry to sound so draconian, but every second saved replying to issues is time spend improving pydantic :-) -->

# Bug

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
             pydantic version: 1.7.1
            pydantic compiled: True
                 install path: /home/***/.local/share/virtualenvs/lerdem-oY9IIzMI/lib/python3.6/site-packages/pydantic
               python version: 3.6.9 (default, Oct  8 2020, 12:12:24)  [GCC 8.4.0]
                     platform: Linux-5.0.0-32-generic-x86_64-with-LinuxMint-19.3-tricia
     optional deps. installed: []
```
<!-- or if you're using pydantic prior to v1.3, manually include: OS, python version and pydantic version -->

<!-- Please read the [docs](https://pydantic-docs.helpmanual.io/) and search through issues to
confirm your bug hasn't already been reported. -->

<!-- Where possible please include a self-contained code snippet describing your bug: -->

```py
>>> from pydantic import BaseModel, Field
>>> from typing import Callable
>>> class Foo(BaseModel):
...     # without default value, getting user wa
--- Real fix (for your judgement only)
diff --git a/pydantic/main.py b/pydantic/main.py
--- a/pydantic/main.py
+++ b/pydantic/main.py
@@ -198,7 +198,11 @@ def validate_custom_root_type(fields: Dict[str, ModelField]) -> None:
         raise ValueError('__root__ cannot be mixed with other fields')
 
 
-UNTOUCHED_TYPES = FunctionType, property, type, classmethod, staticmethod
+# Annotated fields can have many types like `str`, `int`, `List[str]`, `Callable`...
+# If a field is of type `Callable`, its default value should be a function and cannot to ignored.
+ANNOTATED_FIELD_UNTOUCHED_TYPES: Tuple[Any, ...] = (property, type, classmethod, staticmethod)
+# When creating a `BaseModel` instance, we bypass all the methods, properties... added to the model
+UNTOUCHED_TYPES: Tuple[Any, ...] = (FunctionType,) + ANNOTATED_FIELD_UNTOUCHED_TYPES
 
 # Note `ModelMetaclass` refers to `BaseModel`, but is also used to *create* `BaseModel`, so we need to add this extra
 # (somewhat hacky) boolean to keep track of whether we've created the `BaseModel` class yet, and therefore whether it's
@@ -245,7 +249,6 @@ def __new__(mcs, name, bases, namespace, **kwargs):  # noqa C901
         class_vars = set()
         if (namespace.get('__module__'), namespace.get('__qualname__')) != ('pydantic.main', 'BaseModel'):
             annotations = resolve_annotations(namespace.get('__annotations__', {}), namespace.get('__module__', None))
-            untouched_types = UNTOUCHED_TYPES + config.keep_untouched
             # annotation only fields need to come first in fields
             for ann_name, ann_type in annotations.items():
                 if is_classvar(ann_type):
@@ -255,14 +258,14 @@ def __new__(mcs, name, bases, namespace, **kwargs):  # noqa C901
                     value = namespace.get(ann_name, Undefined)
                     allowed_types = get_args(ann_type) if get_origin(ann_type) is Union else (ann_type,)
                     if (
-                        isinstance(value, untouched_types)
+                        isi
--- Kel's top 3
  [1] Goal: ModelFactory must apply Annotated Field constraints to nested types
      Bug: Annotations for nested types are ignored
### Description

When a pydantic model contains a simple annotated attribute, the restrictions from the annotation are used for generating the value.

Example:
```python
Dimension = Annotated[int, Field(gt=1000, lt=2000)]

class Item(BaseModel):
 
      Way: Apply Annotated Field constraints to nested types in Pydantic factories
      - Identify the factory responsible for generating Pydantic models and the method that processes type annotations for field constraints.
      - Reproduce the issue by running the provided MCVE, observing that `Annotated` constraints on nested types (e.g., `list[Annotated[int, Field(...)]]`) are ignored, leading to validation errors.
      - Introduce a new helper function `get_annotation_metadata` to extract all metadata arguments from an `Annotated` type.
      - Modify `FieldMeta.from_type` to use the new `get_constraints_metadata` class method for extracting metadata from `Annotated` types, ensuring that subclasses can override this behavior. Also, ensure that `cls.from_type` is used recursively for children fields to maintain polymorphism.
      - Implement `PydanticFieldMeta.get_constraints_metadata` to specifically handle Pydantic v2's `FieldInfo` objects, unwrapping them to expose the actual `Field` metadata. This ensures that Pydantic `Field` constraints are correctly extracted when wrapped by `FieldInfo`.
      - Verify that the fix correctly applies `Annotated` constraints to nested types by running the provided tests.
  [2] Goal: Add missing type annotations to model base config fields
      Incomplete type annotations in BaseConfig yield mypy errors when Config is defined from BaseConfig
### Checks

* [X] I added a descriptive title to this issue
* [X] I have searched (google, github) for similar issues and couldn't find anything
* [X] I have read and followed [the docs](https://py
      Way: Add explicit type annotations to config class attributes
      - Locate base config class and identify attributes lacking explicit type annotations (e.g., initialized to None, boolean, or enum defaults) and check how validators consume defaults.
      - Add explicit type annotations to class attributes in the base configuration class (such as Optional[str] for title, int for min_anystr_length with default 0, Optional[int] for max_anystr_length, and bool for boolean flags).
      - Update string length validators to handle explicit 0 length limits correctly using explicit `is not None` checks rather than truthy `or` fallbacks.
      - Update documentation to reflect accurate default values (e.g., max_anystr_length default is None).
      - Run the test suite to verify string length validation with zero length limits and type checking behavior.
  [3] Goal: Fix fake data generation for Optional type fields in Pydantic models
      AttributeError: 'NoneType' object has no attribute 'fake'
**Describe the bug**

In my pydantic data model I have the following attribute `X: List[Optional[types.Float32]]`. When I call fake() of this class I get the error 'AttributeError: 'NoneType' object has no attribute 'fake''

**To Reproduc
      Way: Assign internal_field for Union item types in ListField to support fake data generation
      - Locate ListField class definition and its __init__ and fake methods where item types are parsed.
      - Reproduce the AttributeError by calling fake() on a model containing a List of Optional/Union types.
      - Assign UnionField instance to self.internal_field when items_type is a union in ListField, and extract self.items_type from self.internal_field.get_avro_type() in both branches.
      - Run test suite to verify fake data generation for List of Optional types succeeds.

=== TASK cython__cython-2237 | repo cython/cython | python
--- Issue
cycache fingerprint does not look at compiler directives and Cython version
The compiler directives used for Cythonizing should be part of the fingerprint for cycache.

Migrated from http://trac.cython.org/ticket/842

--- Real fix (for your judgement only)
diff --git a/Cython/Build/Dependencies.py b/Cython/Build/Dependencies.py
index 086287a0c..acf3d3b51 100644
--- a/Cython/Build/Dependencies.py
+++ b/Cython/Build/Dependencies.py
@@ -616,15 +616,28 @@ class DependencyTree(object):
     def newest_dependency(self, filename):
         return max([self.extract_timestamp(f) for f in self.all_dependencies(filename)])
 
-    def transitive_fingerprint(self, filename, extra=None):
+    def transitive_fingerprint(self, filename, module, compilation_options):
+        r"""
+        Return a fingerprint of a cython file that is about to be cythonized.
+
+        Fingerprints are looked up in future compilations. If the fingerprint
+        is found, the cythonization can be skipped. The fingerprint must
+        incorporate everything that has an influence on the generated code.
+        """
         try:
             m = hashlib.md5(__version__.encode('UTF-8'))
             m.update(file_hash(filename).encode('UTF-8'))
             for x in sorted(self.all_dependencies(filename)):
                 if os.path.splitext(x)[1] not in ('.c', '.cpp', '.h'):
                     m.update(file_hash(x).encode('UTF-8'))
-            if extra is not None:
-                m.update(str(extra).encode('UTF-8'))
+            # Include the module attributes that change the compilation result
+            # in the fingerprint. We do not iterate over module.__dict__ and
+            # include almost everything here as users might extend Extension
+            # with arbitrary (random) attributes that would lead to cache
+            # misses.
+            m.update(str((module.language, module.py_limited_api, module.np_pythran)).encode('UTF-8'))
+
+            m.update(compilation_options.get_fingerprint().encode('UTF-8'))
             return m.hexdigest()
         except IOError:
             return None
@@ -881,8 +894,6 @@ def cythonize(module_list, exclude=None, nthreads=0, aliases=None, quiet=False,
     if 'include_path' not in options:
   
--- Kel's top 3
  [1] Goal: Avoid checking for C++ compiler when querying optional dependencies in C projects
      meson claims Unknown compiler(s): ['c++', 'g++', 'clang++', 'pgc++', 'icpc']
This is kinda weird: I have a very small dummy meson.build which errors out with:

> meson.build:7:0: ERROR: Unknown compiler(s): ['c++', 'g++', 'clang++', 'pgc++', 'icpc']

Whereas it's correct I don't have any of thos
      Way: Configure unity build source file batch size
      - Identify where backend unity build files are generated and where build options are defined.
      - Add a new integer builtin option `unity_size` with minimum value 2 and default 4.
      - Update unity source file generation to split target sources into batches of size `unity_size` per language, giving each unity file a numbered suffix.
      - Update unity object extraction logic to account for multiple generated unity source files per compiler.
      - Run unity subproject unit tests to verify multi-file unity generation works as expected.
  [2] Goal: Detect compiler version and type from custom compiler binary names
      Conan fails to detect the compiler although `CC` and `CXX` are set
### Environment details

* Operating System+version: ubuntu-22.04
* Compiler+version: 13.1.0
* Conan version: 2.0.9
* Python version: 3.11.4


### Steps to reproduce

I am creating a Conda package of my software, as part of
      Way: detect gcc compiler from conda and target-prefixed executable names
      - Search for default compiler detection logic that inspects CC and CXX environment variable names to identify the compiler family.
      - Check compiler name matching in detect_default_compiler() when CC or CXX contains custom target-triplet prefixes like 'x86_64-conda-linux-gnu-cc'.
      - Update the compiler detection branch in detect_default_compiler() to check for 'gnu-cc' alongside 'gcc', 'g++', and 'c++' so target-prefixed GCC binaries are recognized as GCC.
      - Run the integration tests covering build context tool requirements to verify detection and dependency resolution pass without regressions.
  [3] Goal: Fix cache control directive parsing and update related type stubs
      Several issues with CacheControl types
This issue is in three parts:

### `no-transform` directive always returns None

This directive has no arguments (either in current RFC 9111 or older RFC 2616)

```python
from werkzeug.http import parse_cache_control_header

cc = parse_cache_control_he
      Way: correct cache control directive property definitions and type stubs
      - Inspect cache control directive properties and corresponding type stub definitions to identify misconfigured default values and type mismatches.
      - Change no_transform property type to bool, change min_fresh default valueless sentinel to None, and add the must_understand boolean property to response cache control.
      - Update the type stub file (.pyi) to reflect bool returns for flags, str | None for directives that store string values (like no_cache and private), Literal['*'] | int | None for max_stale, remove setters from immutable request cache control, and add must_understand.
      - Add type ignore comments where code assigns True to string-storing cache-control properties like no_cache.
      - Run the cache control test suite to ensure directives parse and serialize properly.