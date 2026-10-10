

=== TASK pymor__pymor-1479 | repo pymor/pymor | python
--- Issue
Remove slicing restrictions for vectorarray tests
Historically index tests were performed with a set of manually constructed `slice` objects. When rewriting the tests to instead draw slices from a hypothesis strategy it became necessary to add a filter to mimic the properties of the manual examples, or be drowned by test failures. Notably this filter excludes negative step sizes. It's unclear whether the assertions simply are defective or if real failures are among them. And I currently do not have time to look into this further. Anybody interested should check out the `vectorarray_tests_unrestricted_slicing` branch and run the tests there. 
--- Real fix (for your judgement only)
diff --git a/src/pymor/vectorarrays/block.py b/src/pymor/vectorarrays/block.py
index 3aa9eb29a..9fdb31fbd 100644
--- a/src/pymor/vectorarrays/block.py
+++ b/src/pymor/vectorarrays/block.py
@@ -65,6 +65,9 @@ class BlockVectorArray(VectorArray):
             return 0
 
     def __getitem__(self, ind):
+        if isinstance(ind, Number) and (ind >= len(self) or ind < -len(self)):
+            raise IndexError('VectorArray index out of range')
+        assert self.check_ind(ind)
         return BlockVectorArrayView(self, ind)
 
     def __delitem__(self, ind):
diff --git a/src/pymor/vectorarrays/interface.py b/src/pymor/vectorarrays/interface.py
index f2871ef06..7825d088a 100644
--- a/src/pymor/vectorarrays/interface.py
+++ b/src/pymor/vectorarrays/interface.py
@@ -664,14 +664,22 @@ class VectorArray(BasicObject):
         return len({i if i >= 0 else l+i for i in ind})
 
     def normalize_ind(self, ind):
-        """Normalize given indices such that they are independent of the array length."""
+        """Normalize given indices such that they are independent of the array length.
+
+        Does not check validity of the indices.
+        """
+        l = len(self)
         if type(ind) is slice:
-            return slice(*ind.indices(len(self)))
+            start, stop, step = ind.indices(l)
+            if start == stop:
+                return slice(0, 0, 1)
+            assert start >= 0
+            assert stop >= 0 or (step < 0 and stop >= -1)
+            return slice(start, None if stop == -1 else stop, step)
         elif not hasattr(ind, '__len__'):
-            ind = ind if 0 <= ind else len(self)+ind
+            ind = ind if 0 <= ind else l+ind
             return slice(ind, ind+1)
         else:
-            l = len(self)
             return [i if 0 <= i else l+i for i in ind]
 
     def sub_index(self, ind, ind_ind):
diff --git a/src/pymor/vectorarrays/list.py b/src/pymor/vectorarrays/list.py
index 8537648dd..23bd976bc 100644
--- a/src/pymor/vectorarr
--- Kel's top 3
  [1] Goal: Support negative indices when translating string slicing to SUBSTR
      Add support for more slicing cases with SUBSTR
Currently, PyDough only allows string slicing in cases where the step is absent (or 1), and the start/stop are both non-negative (or absent), by translating to `SUBSTR`. However, `SUBSTR` has support for negatives, which we should also support. This mea
      Way: Translate Python string slicing with negative indices to SQL SUBSTR
      - Locate string slice translation logic and error handling in SQL generation (e.g. `convert_slice`).
      - Validate that start, stop, and step parameters  are integer literals or Null/absent, and ensure step is 1 or absent, raising appropriate errors for unsupported non-integer literals or steps other than 1.
      - Create a helper function to translate negative indices to positive 1-based (or 0-based) expressions using `LENGTH(s) + neg_index + offset`.
      - Implement Python slice semantics matching all start/stop combinations ((None, None), (start, None), (None, stop), and (start, stop)) handling clamped bounds, length calculation, and generating SQL `Substring` expressions (or empty strings when bounds collapse).
      - Update documentation to clarify support for negative string slicing indices while keeping the restriction on step != 1.
      - Run end-to-end slice error and translation tests.
  [2] Goal: Preserve optional slice cardinality when assigning values directly to array slices
      SUSHI incorrectly handles cardinality inference in sliced arrays
It seems like SUHSI incorrectly infers the cardinality of a sliced array when values are assigned within that sliced array. See this example below:
```
Alias: $loinc = http://loinc.org
Alias: $snomed = http://snomed.info/sct

Prof
      Way: Preserve optional slice cardinality for direct assignments to $this discriminators
      - Identify the code responsible for inferring minimum cardinality for sliced elements based on value assignments.
      - Reproduce the issue by applying an assignment rule directly to a sliced element whose discriminator path is `$this` and observing that its minimum cardinality is incorrectly set to 1.
      - Modify the condition that enforces `min = 1` for discriminator paths. Exclude cases where the discriminator path is `$this` from this cardinality enforcement logic.
      - Run the tests that failed before the fix and pass after it.
  [3] Goal: Support constraints in DataFrame filter operations
      feat: support `constraints` in `DataFrame.filter`
As requested [here](https://github.com/vega/altair/pull/3631)

If it doesn't add too much complexity, then it would probably be quite a nice ergonomic feature for users
      Way: support keyword constraints in dataframe filter operations
      - Locate `filter` methods across public DataFrame/BaseFrame classes and underlying engine implementations.
      - Reproduce by passing keyword arguments (constraints) to `nw.DataFrame.filter(a=1)` and observing type or parameter errors.
      - Update `filter` method signatures in public frames (`DataFrame`, `LazyFrame`, `BaseFrame`) and engine backend classes (`ArrowDataFrame`, `DaskLazyFrame`, `PandasLikeDataFrame`) to accept `**constraints: Any`.
      - In `BaseFrame.filter`, pass `**constraints` into `_flatten_and_extract` and forward the resulting constraints dictionary to `_compliant_frame.filter(*predicates, **constraints)`.
      - In backend `filter` implementations, convert each key-value constraint into an equality expression (`plx.col(name) == v`) and chain them into `plx.all_horizontal(*chain(predicates, constraints_exprs))`, ensuring bool-list shortcut branch checks `and not constraints`.
      - Verify all filter constraint tests pass across eager and lazy execution engines.

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
--- Kel's top 3
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

=== TASK astropy__specreduce-190 | repo astropy/specreduce | python
--- Issue
TST: Do not build astropy dev from source
You can pull down astropy dev wheel like you do over at `specutils`.

https://github.com/astropy/specreduce/blob/0d800fd584ed484a35cb4d064894620cd71501fd/tox.ini#L56
--- Real fix (for your judgement only)
diff --git a/pyproject.toml b/pyproject.toml
index 98b0520..56d7d63 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -8,3 +8,7 @@ requires = ["setuptools",
             "cython"]
 
 build-backend = 'setuptools.build_meta'
+
+[tool.pytest.ini_options]
+
+filterwarnings = ["ignore::DeprecationWarning:datetime",]
diff --git a/tox.ini b/tox.ini
index 98cfc17..29a45de 100644
--- a/tox.ini
+++ b/tox.ini
@@ -1,12 +1,10 @@
 [tox]
 envlist =
-    py{38,39,310,311}-test{,-devdeps}{,-cov}
-    py{38,39,310,311}-test-numpy{120,121,122,123}
-    py{38,39,310,311}-test-astropy{lts,rc}
+    py{310,311,312}-test{,-devdeps,-predeps}{,-cov}
     build_docs
     codestyle
 requires =
-    setuptools >= 30.3.0
+    setuptools
     pip >= 19.3.1
 isolated_build = true
 
@@ -16,7 +14,8 @@ isolated_build = true
 passenv = HOME,WINDIR,LC_ALL,LC_CTYPE,CC,CI
 
 setenv =
-    devdeps: PIP_EXTRA_INDEX_URL = https://pypi.anaconda.org/scientific-python-nightly-wheels/simple
+    devdeps: PIP_EXTRA_INDEX_URL = https://pypi.anaconda.org/astropy/simple https://pypi.anaconda.org/scientific-python-nightly-wheels/simple
+    py312: PIP_EXTRA_INDEX_URL = https://pypi.anaconda.org/astropy/simple
 
 # Run the tests in a temporary directory to make sure that we don't import
 # this package from the source tree
@@ -35,40 +34,36 @@ description =
     devdeps: with the latest developer version of key dependencies
     oldestdeps: with the oldest supported version of key dependencies
     cov: enable remote data and measure test coverage
-    numpy120: with numpy 1.20.*
-    numpy121: with numpy 1.21.*
-    numpy122: with numpy 1.22.*
-    numpy123: with numpy 1.23.*
-    astropylts: with the latest astropy LTS
 
 # The following provides some specific pinnings for key packages
 deps =
 
-    numpy120: numpy==1.20.*
-    numpy121: numpy==1.21.*
-    numpy122: numpy==1.22.*
-    numpy123: numpy==1.23.*
-
-    astropy51: astropy==5.1.*
-    astropylts: astropy==5.1.*
-
     devdeps: numpy>=0.0.dev0
-    devd
--- Kel's top 3
  [1] Goal: Remove astropy_helpers submodule and migrate to standalone build configuration
      Remove astropy_helpers
We've come to the conclusion that we've outgrown the functionality the submodule provides and we never really used its facilities for Cython that much.
      Way: removing astropy_helpers and migrating to standard setuptools build
      - Identify all files and configuration referencing the astropy_helpers submodule, such as .gitmodules, ah_bootstrap.py, setup.py, setup.cfg, and coverage configuration.
      - Remove git submodule declaration for astropy_helpers from .gitmodules (or delete .gitmodules if empty) and delete ah_bootstrap.py.
      - Update coverage configuration (e.g. .coveragerc) to remove ah_bootstrap.py and include any replacement init scripts or modules as needed.
      - Verify the package builds and tests pass cleanly without requiring astropy_helpers.
  [2] Goal: Suppress pip install warnings for scripts not on PATH
      Disable warnings about Python scripts not in PATH.
During a recent run of `pip install` there was a new warning message:
```
  WARNING: The scripts fits2bitmap, fitscheck, fitsdiff, fitsheader, fitsinfo, samp_hub, showtable, volint and wcslint are installed in '/global/common/software/desi/users/b
      Way: suppress pip script location warnings and unlock permissions on reinstall
      - Search the codebase for invocations of pip install to locate where packages are installed using a custom prefix.
      - Add the '--no-warn-script-location' flag to the pip install argument list when installing into custom prefixes.
      - Add an unlock_permissions method that runs 'chmod -R u+w' on the install directory, and invoke it before recursively removing existing install directories when forced reinstallation is requested.
      - Run the test suite verifying that unlock permissions and install procedures pass.
  [3] Goal: Deprecate automatic conversion of non-standard CTYPE values to helioprojective coordinate types
      Deprecate changing solar-{x,y} CTYPE
Originally suggested in https://github.com/sunpy/sunpy/pull/5501#discussion_r724886420. Currently we do the following correction:

```python
        if ctype1.lower() in ("solar-x", "solar_x"):
            ctype1 = 'HPLN-TAN'

        if ctype2.lower() in (
      Way: Deprecate general non-standard coordinate header fallbacks and relocate to source subclasses
      - Identify base class header normalization methods and find instrument-specific subclasses that rely on legacy coordinate keywords.
      - Add deprecation warnings in the base class implementation when non-standard coordinate headers (e.g. 'solar-x', 'solar-y') are encountered.
      - Override the coordinate normalization method in instrument-specific subclasses to preserve backwards compatibility for specific instruments without triggering deprecation warnings.
      - Verify deprecation warnings trigger on generic maps and instrument-specific maps resolve coordinates without warnings using test suites.

=== TASK pydantic__pydantic-3177 | repo pydantic/pydantic | python
--- Issue
"extra" config not respected for TypedDicts in functions
### Checks

* [x] I added a descriptive title to this issue
* [x] I have searched (google, github) for similar issues and couldn't find anything
* [x] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and still think this is a bug

<!-- Sorry to sound so draconian, but every second saved replying to issues is time spend improving pydantic :-) -->

# Bug

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
             pydantic version: 1.8.2
            pydantic compiled: False
                 install path: /home/msharma216/.local/lib/python3.8/site-packages/pydantic
               python version: 3.8.0 (default, Feb 25 2021, 22:10:10)  [GCC 8.4.0]
                     platform: Linux-4.4.0-19041-Microsoft-x86_64-with-glibc2.27
     optional deps. installed: ['typing-extensions']

```
<!-- or if you're using pydantic prior to v1.3, manually include: OS, python version and pydantic version -->

<!-- Please read the [docs](https://pydantic-docs.helpmanual.io/) and search through issues to
confirm your bug hasn't already been reported. -->

<!-- Where possible please include a self-contained code snippet describing your bug: -->

When trying to use the `validate_arguments` decorator, I face the issue of getting validation errors for an extra attribute for a TypedDict as below:

```py
from typing_extensions import TypedDict

from p
--- Real fix (for your judgement only)
diff --git a/pydantic/decorator.py b/pydantic/decorator.py
--- a/pydantic/decorator.py
+++ b/pydantic/decorator.py
@@ -254,6 +254,6 @@ def check_duplicate_kwargs(cls, v: Optional[List[str]]) -> None:
                 raise TypeError(f'multiple values for argument{plural}: {keys}')
 
             class Config(CustomConfig):
-                extra = Extra.forbid
+                extra = getattr(CustomConfig, 'extra', Extra.forbid)
 
         self.model = create_model(to_camel(self.raw_function.__name__), __base__=DecoratorBaseModel, **fields)

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
  [2] Goal: Allow extra keys in handler config TypedDict type definitions
      Use extra keys for TypedDict
https://github.com/Delgan/loguru/blob/fa3746fe8ce354e8e32e444b2bc7e4221a88ac86/loguru/__init__.pyi#L215

It seems mypy 1.9.0 implemented a fix https://github.com/python/mypy/pull/14225, can `_HandlerConfig` be made actually usable now? 😅
      Way: TypedDict union specialization for heterogeneous dictionary configurations
      - Examine type stub definitions where a single generic TypedDict is prevented from accommodating sink-specific configuration options due to TypedDict excess key checks.
      - Identify the distinct categories of sinks and their specific allowed kwargs across the overload signatures (e.g., standard sinks, file path sinks, and async coroutine sinks).
      - Split the single generic TypedDict into specialized TypedDict classes (BasicHandlerConfig, FileHandlerConfig, AsyncHandlerConfig) each containing only the keys valid for that sink category, and define a public union type alias combining them.
      - Update function signatures that consume the configuration dictionary (such as configure()) to accept the union of specialized TypedDict types instead of a generic Sequence[Dict[str, Any]].
      - Align overload method signatures (e.g. add()) to expose concrete keyword parameters (such as errors, newline, closefd, opener) matching the TypedDict fields.
      - Run the type safety test suite to ensure valid handler configuration dicts pass type checking and invalid keys trigger errors.
  [3] Goal: Standardize Pydantic model validation methods in array metadata class
      Nit: `__post_init__` vs pydantic `@model_validator(mode="after")`
[In the `ZArray` class](https://github.com/zarr-developers/VirtualiZarr/blob/179bb2ab42664546dd243a2e04fab8737846229a/virtualizarr/zarr.py#L76) then after #193 we now have multiple types of validation checks, which together look like 
      Way: replace Pydantic models with standard dataclasses in array metadata
      - Identify Pydantic models (`ZArray`, `ChunkEntry`, `Codec`) used for metadata representation and locate validation logic across `__post_init__`, `@field_validator`, and `@model_validator`.
      - Remove `pydantic` dependency from project configuration (`pyproject.toml`, `ci/environment.yml`).
      - Convert metadata classes (`ChunkEntry`, `Codec`, `ZArray`) from `pydantic.BaseModel` to `@dataclasses.dataclass`.
      - Consolidate `dtype` normalization, dimension checks, and default `fill_value` handling into `ZArray.__post_init__`.
      - Update serialization helpers to use `dataclasses.asdict` instead of `dict(self)` or Pydantic serialization.
      - Run test suite to verify metadata instantiation, validation, dictionary conversion, and replace methods.

=== TASK victorskl__yawsso-94 | repo victorskl/yawsso | python
--- Issue
Add parameter to control setting the region in the .aws/credentials file
Hi @victorskl,

I came across the issue reported in https://github.com/victorskl/yawsso/issues/76 because I was wondering why the aws clients are telling me that there is no default region defined in the profile. It has been working before, but then since I updated yawsso I started to have an issue. So actually my request would be the opposite as what is explained in https://github.com/victorskl/yawsso/issues/76. Do you think it would be possible to implement a parameter so that you can define whether you want the region in the credentials file or not? Something like a switch "-r" to tell it to copy the region for example?
yawsso -p dev:foo -r
--- Real fix (for your judgement only)
diff --git a/yawsso/cli.py b/yawsso/cli.py
index 4b2195e..db41fad 100644
--- a/yawsso/cli.py
+++ b/yawsso/cli.py
@@ -36,6 +36,9 @@ def boot():
     if args.bin:
         core.aws_bin = args.bin
 
+    if args.region:
+        core.region_flag = args.region
+
     logger.log(TRACE, f"args: {args}")
     logger.log(TRACE, f"AWS_CONFIG_FILE: {core.aws_config_file}")
     logger.log(TRACE, f"AWS_SHARED_CREDENTIALS_FILE: {core.aws_shared_credentials_file}")
@@ -60,6 +63,7 @@ def parser():
     ap.add_argument("-t", "--trace", help="Trace output", action="store_true")
     ap.add_argument("-e", "--export-vars", dest="export_vars1", help="Print out AWS ENV vars", action="store_true")
     ap.add_argument("-v", "--version", help="Print version and exit", action="store_true")
+    ap.add_argument("-r", "--region", help="Add region to credentials file", action="store_true")
 
     sp = ap.add_subparsers(title="available commands", metavar="", dest="command")
 
diff --git a/yawsso/core.py b/yawsso/core.py
index e9ca448..d80c39f 100644
--- a/yawsso/core.py
+++ b/yawsso/core.py
@@ -11,7 +11,8 @@ profiles = None
 aws_sso_cache_path = u.xu(os.getenv("AWS_SSO_CACHE_PATH", Constant.AWS_SSO_CACHE_PATH.value))
 aws_config_file = u.xu(os.getenv("AWS_CONFIG_FILE", Constant.AWS_CONFIG_FILE.value))
 aws_shared_credentials_file = u.xu(os.getenv("AWS_SHARED_CREDENTIALS_FILE", Constant.AWS_SHARED_CREDENTIALS_FILE.value))
-aws_default_region = os.getenv("AWS_DEFAULT_REGION", Constant.AWS_DEFAULT_REGION.value)
+aws_default_region = os.getenv("AWS_DEFAULT_REGION")
+region_flag = False  # See https://github.com/victorskl/yawsso/issues/76
 
 
 def get_aws_cli_v2_sso_cached_login(profile):
@@ -34,19 +35,29 @@ def update_aws_cli_v1_credentials(profile_name, profile, credentials):
         logger.warning(f"No appropriate credentials found. Skip syncing profile `{profile_name}`")
         return
 
-    # region = profile.get("region", aws_default_region)
     config = u.read_config(aws_shared_credent
--- Kel's top 3
  [1] Goal: Add an optional parameter to specify the AWS region
      Add optional parameter for region
Add an optional parameter to specify a region other than us-west-2. Use case is a Alexa and lex and potentially others aren't available in us-west-2
      Way: Add optional CLI region option and pass it through login workflows
      - Locate CLI option definitions and login helper functions where AWS region is hardcoded.
      - Add a `--region` option with a default value of 'us-west-2' to the Click CLI decorator and function signature in the entrypoint module.
      - Update `cached_login`, `non_cached_login`, and internal login helpers to accept the `region` argument and pass it to `write_to_config_file` instead of the hardcoded region string.
      - Update test callers and documentation to account for the new `region` parameter.
      - Run pytest on the login test suite to verify tests pass with the new region parameter.
  [2] Goal: Allow AWS authentication via environment variables without requiring a default profile
      "Invalid profile provided: default" when using environment variables
I'm using environment variables to configure the AWS credentials for aws-gate and don't have a default profile in .aws/config because of that. (I'm using https://github.com/99designs/aws-vault (eg `aws-vault exec profile_name -- aw
      Way: infer default AWS profile from AWS_VAULT environment variable
      - Search the CLI entrypoint for where the default AWS profile is determined from environment variables.
      - Inspect how configuration and profiles are resolved, reproducing the failure when AWS credentials are injected without AWS_PROFILE or a default profile in ~/.aws/config.
      - Update the default profile resolution logic to check the AWS_VAULT environment variable before falling back to AWS_PROFILE and finally the hardcoded default profile.
      - Add debug logging when AWS_VAULT is detected in os.environ.
      - Run the unit test suite to verify default profile resolution under aws-vault.
  [3] Goal: Add support for specifying an AWS profile in the CLI
      cfn CLI needs a way to specify --profile
We use a lot of profiles (multiple gov profiles, seller profiles, commercial profiles etc.)

Since cfn command can only use default profile, we have to manually edit credentials file to switch between profiles.
This makes it hard to automate testing and pu
      Way: propagate AWS profile CLI argument through session creation
      - Locate where boto3 sessions and subcommands (generate, init, invoke, submit, test) are instantiated and configured.
      - Update create_sdk_session helper to accept an optional profile_name parameter and pass it to boto3.session.Session.
      - Add --profile argument to CLI subparser definitions (such as init, generate, invoke, submit, test).
      - Thread the profile argument through command entrypoints, Project methods (generate, submit), and contract clients (ResourceClient, HookClient).
      - Update test suites to account for the additional profile argument in mocked calls and verify profile behavior.

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
--- Kel's top 3
  [1] Goal: Preserve keyword-only argument defaults during function serialization
      Required Keyword defaults missing after serialization round trip
When using Keyword-Only arguments in a function definition, the defaults for those keywords are lost on a round trip through the `pack/unpack_apply_message` functions of the `serialize` module.

Minimal example in a file called "stes
      Way: preserve keyword-only defaults and annotations during function serialization
      - Locate the function serialization and deserialization logic, typically where function objects are canned and uncanned.
      - Modify the function canning class to extract, serialize, and store the function's `__kwdefaults__` and `__annotations__` attributes if they exist.
      - Modify the function uncanning logic to deserialize these attributes and re-assign them to the newly reconstructed function object.
      - Run the test suite to verify that keyword-only argument defaults and type annotations survive the serialization round trip.
  [2] Goal: Disallow keyword arguments for range calls with multiple positional arguments
      Usage of kwargs not prevented for `range(N,M)` or `range(x,x+N)`  
### Version Information

* vyper Version (output of `vyper --version`): 0.3.10+commit.855f7349
* OS: OSX
* Python Version (output of `python --version`): 3.11.4

### What's your issue about?

Using the keyword argument `bound
      Way: disallow keyword arguments in multi-argument range expressions
      - Locate the semantic analysis logic responsible for validating `for` loop `range` iterator calls.
      - Inspect the branch handling `range` calls when positional argument count is greater than one (e.g. `len(args) != 1`).
      - Add a validation check in the multi-argument branch that raises a StructureException when keyword arguments are present on the range node.
      - Run syntax test suite for range loops to verify that invalid kwargs on multi-argument range calls raise compile errors.
  [3] Goal: Expand multiline parenthesized statements that end with a call expression
      False negative with statement ending with parens
```python
if (foo and
    bar()):
    pass
```

I expect to be rewritten to

```python
if (
    foo and
    bar()
):
    pass
```

But I imagine the same code that prevents this from being rewritten is firing:

```python
foo('bar {}
      Way: Unhug multiline expressions when inner opening delimiter is not immediately nested
      - Identify the unhugging / brace expansion logic that decides whether single-element multiline brace expressions should be expanded.
      - Reproduce the issue with a test case where a multiline parenthesized condition ends with a call expression (e.g. `if (foo and bar()): pass`) and observe that it is incorrectly skipped from being unhugged.
      - Update the condition that prevents unhugging single elements containing a multiline component: replace checking whether the open brace is hugged with checking whether the token immediately following the opening brace (`tokens[first_brace + 1].src`) is an opening delimiter in `START_BRACES`.
      - Run the test suite to ensure the new case expands correctly while preserving existing hugged multiline call expressions like `foo('bar {}'.format('baz'))`.

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
--- Kel's top 3
  [1] Goal: Preserve obsolete PO file entries with identical IDs across different contexts
      Obsoletes with same ID from different contexts get removed on update
## Overview Description

When a file contains multiple obsoletes _from different contexts_, and they have the same ID, all but the last one will get stripped from the file.

## Steps to Reproduce

1. Place multiple messages i
      Way: key obsolete PO file messages by context and message id
      - Locate PO file message parsing logic where obsolete messages are added to the catalog dictionary.
      - Change obsolete message catalog storage keying from plain message ID to catalog composite key function `_key_for(msgid, msgctxt)`.
      - Run test suite for PO file reading to verify obsolete messages with different contexts are retained.
  [2] Goal: Add a CLI command or script to validate changelog fragment filenames
      Check for invalid filenames in `changes.d/`
Although `towncrier create` enforces valid fragment file names, creating a fragment file manually can sometimes lead to mistakes in the file name. It would be useful to include in our CI a check that all files in `changes.d/` have valid fragment file names
      Way: validate fragment filenames and support custom ignore list in configuration
      - Locate fragment scanning and CLI check/build entrypoints.
      - Add an 'ignore' configuration setting (defaulting to None or empty list) to allow ignoring specific filenames in newsfragment directories.
      - Update fragment parser/finder function to accept a 'strict' mode and filter out standard ignored files (e.g. .gitignore, README) along with configured ignore files.
      - Raise an exception in fragment scanning when 'strict' is enabled and an invalid fragment filename is encountered.
      - Pass strict=True in 'check' command and strict=(config.ignore is not None) in 'build' command to enforce filename validation.
      - Run the test suite to verify invalid filename checking works as expected.
  [3] Goal: Add CLI option to include untracked files in diff quality checks
      diff-quality doesn't consider untracked files
Hi,

so I just found out, that diff-quality doesn't consider untracked files. So new files which are staged are checked, but unstaged new files are ignore. The root cause is, that git-diff doesn't consider them as well. As far as I know, it's needed to
      Way: Add CLI flag to incorporate untracked files into diff quality checks
      - Inspect CLI parsers and diff reporting orchestration to see how staged and unstaged file filters are currently defined and plumbed through diff reporters.
      - Implement a helper method on the git diff wrapper that calls 'git ls-files --exclude-standard --others' and returns a list of untracked file paths.
      - Update the diff reporter to accept an 'include_untracked' parameter, and when true, query the untracked files, count each file's total lines, and mark every line (1 to N) as changed in the diff mapping.
      - Add the '--include-untracked' boolean CLI option to both diff-cover and diff-quality command-line entry points and forward the flag to their report generation functions and reporter instances.
      - Run the test suite including new tests verifying that untracked files are discovered and reported with all lines modified when the option is enabled.

=== TASK mixkorshun__django-antispam-6 | repo mixkorshun/django-antispam | python
--- Issue
Akismet: AssertionError on comment_date type
python-akismet expects comment_date to be `datetime`, not a timestamp:

https://github.com/Nekmo/python-akismet/blob/322cf3e9a9a1986434a20e01cabb6767e91a226b/akismet/__init__.py#L49

django-antispam provides a timestamp:

https://github.com/mixkorshun/django-antispam/blob/3b193a8360ff8171f0c6d7cc1891fab0b69d0e0a/antispam/akismet/entities.py#L155

So I get an `AssertionError` when calling `akismet.check`
--- Real fix (for your judgement only)
diff --git a/antispam/akismet/entities.py b/antispam/akismet/entities.py
index 91d766a..a620516 100644
--- a/antispam/akismet/entities.py
+++ b/antispam/akismet/entities.py
@@ -1,6 +1,6 @@
 from datetime import datetime
 
-from .utils import get_client_ip, get_timestamp
+from .utils import get_client_ip
 
 
 class Request:
@@ -152,7 +152,7 @@ class Comment:
         params = {
             'comment_type': self.type,
             'comment_content': self.content,
-            'comment_date': get_timestamp(self.created),
+            'comment_date': self.created,
             'permalink': self.permalink,
         }
 
diff --git a/antispam/akismet/utils.py b/antispam/akismet/utils.py
index b6cdf72..c292c1a 100644
--- a/antispam/akismet/utils.py
+++ b/antispam/akismet/utils.py
@@ -18,11 +18,3 @@ def get_client_ip(request):
         return x_forwarded_for.split(',')[0]
 
     return request.META.get('REMOTE_ADDR')
-
-
-def get_timestamp(dt):
-    try:
-        return int(dt.timestamp())
-    except AttributeError:
-        import time
-        return int(time.mktime(dt.timetuple()))

--- Kel's top 3
  [1] Goal: Fix date and datetime comparison error when fetching filings
      Datetime Error on Daily Fillings
**Describe the bug**
`TypeError: can't compare datetime.datetime to datetime.date.`

**To Reproduce**
Steps to reproduce the behavior:
1. `$ secedgar daily -d 20210203`
2. See error
```
    elif self._date < datetime.date(1998, 3, 31):
TypeError: can't compa
      Way: convert parsed datetime to date object in CLI date cleanup helper
      - Identify the date parsing utility function in the CLI module responsible for converting input date strings.
      - Reproduce the type mismatch error when passing a string date to the CLI command that expects a datetime.date object.
      - Modify the date_cleanup function to call `.date()` on the result of `datetime.strptime()` when date is not None.
      - Run CLI tests to confirm `date_cleanup` returns a `datetime.date` object.
  [2] Goal: Add support for RFC 3339 timestamps in the query grammar
      Missing support for timestamps/datetime in grammar
The grammar is currently missing support for timestamps (or `datetime.datetime` in terms of Python types).

The relevant section in the spec may be found [here](https://github.com/Materials-Consortia/OPTiMaDe/blob/develop/optimade.rst#type-handlin
      Way: convert timestamp filter strings to timezone-aware BSON datetimes
      - Identify query fields representing timestamps (such as 'last_modified') during filter transformation.
      - Define a custom warning for non-RFC 3339 compliant queries (e.g. timestamps containing microseconds).
      - Add a postprocessing step in MongoTransformer to recursively detect timestamp fields and replace query string values with timezone-aware datetime objects using bson.json_util.
      - Update validator configuration and tests to quote timestamp literals and validate comparisons against timestamp fields.
      - Run the MongoDB filter transformer test suite to verify timestamp and ID handling.
  [3] Goal: Fix TypeError when calling astimezone on custom DateTime objects
      Exception when calling DateTime.astimezone
When attempting to use `tomlkit.items.DateTime.astimezone()`, a `TypeError` is raised.

```py
    date = date.astimezone()
TypeError: __new__() missing 2 required positional arguments: 'trivia' and 'raw'
```

The current workaround is to reinitialize
      Way: Make custom datetime subclass constructor accept optional metadata for timezone and replace calls
      - Locate custom datetime, date, and time subclasses deriving from standard library types.
      - Reproduce `TypeError` when calling methods like `astimezone()` or `replace()` on custom `DateTime` instances.
      - Update `__new__` and `__init__` signatures of the custom subclasses to accept variable or optional parameters for extra metadata (such as `trivia` and `raw`) so standard library methods like `replace()` and `astimezone()` can construct instances without failing.
      - Override `replace()` and `astimezone()` on custom datetime classes to correctly wrap returned standard datetime instances back into the subclass type when required.
      - Run test suite to verify datetime, date, and time objects work with `astimezone()` and `replace()`.

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
--- Kel's top 3
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

=== TASK rsalmei__alive-progress-47 | repo rsalmei/alive-progress | python
--- Issue
Use with logging module
In my module, I use the python logging module to print out messages.

During the operation which I want to monitor using alive-progress it may happen that messages are sent out using the logging module. Then, artifacts of the bar remain in the console:

```
Loading the fmu took 0.34800148010253906 s.

Starting simulation at t=0.0 s.          | ▁▃▅ 0/1000 [0%] in 0s (0.0/s, eta: ?)
Simulation took 0.7570006847381592 s.███▌| ▅▇▇ 990/1000 [99%] in 0s (1126.2/s, eta: 0s)
Simulation finished at t=1000.0 s.
|████████████████████████████████████████| 1000/1000 [100%] in 0.9s (1096.43/s)
```


The first message ("loading ... took ...") was logged before i instantiated the alive bar. The other messages were emitted while the bar was active.
I am using a vanilla StreamHandler that sends everything to stdout.

By the way - dunno if that's possible - is it possible to have the alive bar redirected to a logging module, too? Might be interesting to have the complete (or not, in case of an error!) progress bar in a log file. Gives structure...

By the way 2: it's pretty awesome :) 

Cheers, Jan
--- Real fix (for your judgement only)
diff --git a/alive_progress/__init__.py b/alive_progress/__init__.py
index 6e364dd..f0e16c0 100644
--- a/alive_progress/__init__.py
+++ b/alive_progress/__init__.py
@@ -12,7 +12,7 @@ from .core.progress import alive_bar
 from .styles.exhibit import print_chars, show_bars, show_spinners, showtime
 from .styles.internal import BARS, SPINNERS, THEMES
 
-VERSION = (1, 6, 0)
+VERSION = (1, 6, 1)
 
 __author__ = 'Rogério Sampaio de Almeida'
 __email__ = 'rsalmei@gmail.com'
diff --git a/alive_progress/core/logging_hook.py b/alive_progress/core/logging_hook.py
index 1ffa0b4..21570ea 100644
--- a/alive_progress/core/logging_hook.py
+++ b/alive_progress/core/logging_hook.py
@@ -1,12 +1,31 @@
+# coding=utf-8
+from __future__ import absolute_import, division, print_function, unicode_literals
+
 import logging
 import sys
 
 
-def install_logging_hook():  # pragma: no cover
+def install_logging_hook():
     root = logging.root
-    return {h: h.setStream(sys.stdout) for h in root.handlers
-            if isinstance(h, logging.StreamHandler)}
+    return {h: set_stream(h, sys.stdout) for h in root.handlers  # noqa
+            if h.__class__ == logging.StreamHandler}  # want only this, not its subclasses.
+
+
+def uninstall_logging_hook(before):
+    [set_stream(h, s) for h, s in before.items()]
 
 
-def uninstall_logging_hook(before):  # pragma: no cover
-    [h.setStream(s) for h, s in before.items()]
+if sys.version_info >= (3, 7):
+    def set_stream(handler, stream):
+        return handler.setStream(stream)
+else:  # pragma: no cover
+    def set_stream(handler, stream):
+        # from python 3.7 implementation.
+        result = handler.stream
+        handler.acquire()
+        try:
+            handler.flush()
+            handler.stream = stream
+        finally:
+            handler.release()
+        return result
diff --git a/alive_progress/core/progress.py b/alive_progress/core/progress.py
index 3848d16..00f95b9 100644
--- a/alive_progress/core/progress.py
+++ b/al
--- Kel's top 3
  [1] Goal: Ensure logger outputs print on a new line when progress bar format lacks bar component
      Incorrect log position
## 🐛 Bug description

<!-- A clear and concise description of what the bug is. -->

When `{bar}` is removed from `bar_format` of argument of `ProgressBar`, immediate logger output does not print in newline.

<img width="674" alt="Screen Shot 2020-06-03 at 3 58 28 PM" src
      Way: emit epoch completion logger messages after epoch completed events fire
      - Locate the epoch processing loop in the execution engine class where epoch completed time logs and events are dispatched.
      - Reproduce the issue by attaching a tqdm progress bar missing '{bar}' in its format to an engine logger and running a multi-epoch process to observe log output formatting.
      - Defer logging the epoch completion message until after firing the epoch completed and terminate events so event handlers (such as progress bars) finish printing before the log line.
      - Run the utility test suite to verify logger output behavior during engine execution.
  [2] Goal: Implement optional command-line progress logging for simulations
      Logging
Use the [logging](https://docs.python.org/3/library/logging.html) module to give (optional) command-line updates about the progress of a simulation, e.g.
```
Creating Single Particle Model ... done
Parametrising Single Particle Model ... done
Discretising Single Particle Model ... done

      Way: Integrate configurable logging for simulation progress
      - Identify key stages in the simulation lifecycle where progress updates are valuable, such as model creation, parameter setting, discretisation, and solving. These are the points where log messages will be inserted.
      - Create a dedicated Python module (e.g., `logger.py`) to configure and expose a custom logger instance. This module should set up basic logging configuration, including format and date, and provide a function to dynamically set the logging level.
      - Import the custom logger and its level-setting function into the main package's `__init__.py` file to make them easily accessible throughout the codebase (e.g., `pybamm.logger`, `pybamm.set_logging_level`).
      - Add `logger.info` calls at the start and end of major simulation steps (e.g., `discretise`, `process_model`, `solve`) to indicate overall progress. Use `logger.debug` for more granular, detailed operations within these steps (e.g., processing individual symbols, evaluating residuals).
      - Ensure that models or simulation objects have a `name` attribute, as this is used in the log messages to provide context (e.g., 'Start discretising Single Particle Model'). If not present, add a default name.
      - Update documentation or example scripts to demonstrate how users can control the logging level using the newly exposed `set_logging_level` function.
  [3] Goal: Enable progress bar callbacks for Zarr storage operations
      [Docs] Usage instructions for progressbars 
I've been inspired by @thodson-usgs's Virtualizarr / Cubed rechunking example work and figured it's time to give Cubed a shot. 

Wondering if it's possible to add some documentation on how to use progress bars to monitor a cubed job. I've tried piecing t
      Way: Enable progress bar callbacks for Zarr storage operations
      - Understand the error: The `TypeError` indicates that a `RichProgressBar` instance was passed where an iterable or a function was expected, specifically when `dask.array.store` was trying to process `callbacks`.
      - Locate the code path for `chunkmanager_store_kwargs` in `xarray.to_zarr` and `cubed`'s execution. Identify that `cubed`'s `Plan.execute_plan` is the entry point for computations.
      - Introduce a `compile_function` parameter to `Plan.execute_plan` and `_finalize_dag`. This function will be responsible for wrapping or modifying the functions within the computation graph.
      - Implement `_compile_blockwise` in `Plan`. It iterates DAG nodes, identifies 'primitive_op' nodes with `BlockwiseSpec` configs, and applies `compile_function` to their internal functions. It handles `compile_function`s with or without a `config` argument and uses `dataclasses.replace` to update immutable objects.
      - Call `_compile_blockwise` from `_finalize_dag` if a `compile_function` is provided, ensuring the DAG is copied before mutation.
      - Run the provided tests to ensure the changes correctly enable the compilation feature without regressions.

=== TASK isi-vista__vistautils-108 | repo isi-vista/vistautils | python
--- Issue
Script to turn a directory into a key-value zip usable as a Key-Value Source
We often need to pass experimental artifacts from one user to another.  Currently we mostly do this using lists of file paths together with the files themselves, but this is inconvenient when the data moves around (e.g. from a local laptop to our network code).

We have `KeyValueSource`s and `KeyValueSink`s backed by zip files which work better for this purpose. However, if we have incoming data which is not already stored as one of these, it would be helpful to have a script to convert a list of files in a directory into one. (Just using `zip` won't write the proper indices).
--- Real fix (for your judgement only)
diff --git a/vistautils/scripts/directory_to_key_value_store.py b/vistautils/scripts/directory_to_key_value_store.py
new file mode 100644
index 0000000..bc2855b
--- /dev/null
+++ b/vistautils/scripts/directory_to_key_value_store.py
@@ -0,0 +1,74 @@
+"""
+Given a directory, copies its content into an object which can be used as a `KeyValueSource`.
+
+The form of the output key-value store is given by the "output" sub-namespace of the parameters.
+See `byte_key_value_sink_from_params` for details.
+In the most common case of a zip-backed store, create a sub-namespace like this:
+.. code-block:: YAML
+
+    output:
+      type: zip
+      path: path of zip to write to
+
+Each file in the input directory will become one entry in the output store
+with a value equal to the binary content of the file.
+The key will by default be the file name (not the full path).
+This can be changed by specifying the *key_function* parameter.
+Currently the only key function supported is *strip_one_extension*,
+which removes the first extension (if present) from the file name before using it as the key.
+
+Because the correct way to handle them is unclear,
+if any sub-directories of the input directory are encountered, an error will be raised.
+This behavior is subject to change in the future and should not be relied upon.
+"""
+import logging
+from pathlib import Path
+from typing import Callable
+
+from vistautils.key_value import byte_key_value_sink_from_params
+from vistautils.parameters import Parameters
+from vistautils.parameters_only_entrypoint import parameters_only_entry_point
+
+
+def main(params: Parameters) -> None:
+    input_directory = params.existing_directory("input_directory")
+    key_function = key_function_from_params(params)
+
+    with byte_key_value_sink_from_params(params, eval_context=locals()) as sink:
+        for item_path in input_directory.glob("*"):
+            if item_path.is_dir():
+                raise NotImplementedError(
+                    f"We d
--- Kel's top 3
  [1] Goal: Configure keyring data file path using environment properties
      custom the data file path with KEYRING_PROPERTY_* property
Need an env switch to control the output keyring data path to a custom location, such as `KEYRING_PROPERTY_CRYPTFILE_PATH`
      Way: Overriding default keyring file path via environment variable
      - Identify where the storage file path is computed for the file-backed keyring backend.
      - Define a constant for the environment variable name (e.g., `KEYRING_CRYPTFILE_PATH`) in the file-backed base class module.
      - Update the file path property getter to check `os.environ` for the custom path variable and fall back to the default path joined with platform data root.
      - Document the new environment variable in documentation and changelogs.
      - Run the test suite verifying that setting the environment variable overrides the keyring file location.
  [2] Goal: Allow filesystem operations using relative paths on filesystem mapper instances
      using filesystem methods with mapper keys
The mapper interface is really convenient, especially when working with nested urls like
```
zip://directory::https://example.host/path/to/file.zip
```
However, getting open file objects or querying file metadata is not as easy and requires explicit path
      Way: expose scoped directory filesystem from key-value mapping wrapper
      - Identify the mapping class (FSMap) wrapping a filesystem with a root path prefix and check available sub-tree filesystem implementations (such as DirFileSystem).
      - Ensure DirFileSystem strips protocols from sub-paths when constructing joined paths with its base path.
      - Expose a cached property named 'dirfs' on FSMap that instantiates DirFileSystem using the mapper root prefix (`self._root_key_to_str`) and base filesystem (`self.fs`).
      - Verify the mapper's dirfs attribute handles relative path operations matching mapper keys and runs related filesystem tests.
  [3] Goal: Support importing source files directly from zip archives
      Import from zip files
It would be handy to be able to import .hy files from .zip archives (for those of us who like to deploy stuff in single file bundles).

Right now I'm working around this by doing:

```
BUNDLE=sushy.zip

clean:
    rm *.zip

bundle:
    hyc sushy/*.hy
    zip -r9 $(BUNDLE) . -i 
      Way: Support importing custom language source files from zip archives via zipimport
      - Locate the custom language's import hook or importer implementation.
      - Check if the Python version is 3.8 or higher, as zipimport is implemented in C in older versions and harder to patch.
      - Append the custom file extension (e.g., '.hy') to `zipimport._zip_searchorder` if it is not already present.
      - Monkey-patch `zipimport._compile_source` to intercept compilation of the custom extension, compiling it using the custom compiler, while delegating other extensions to the original compiler function.
      - Run the test suite to verify that modules with the custom extension can be successfully imported from a ZIP archive.

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
--- Kel's top 3
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

=== TASK pydantic__pydantic-4252 | repo pydantic/pydantic | python
--- Issue
An empty string is a valid JSON attribute name
### Checks

* [ x] I added a descriptive title to this issue
* [ x] I have searched (google, github) for similar issues and couldn't find anything
* [ x] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and still think this is a bug

# Bug

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
             pydantic version: 1.9.0
            pydantic compiled: False
                 install path: /Users/s_tsaplin/projects/github.com/sergeytsaplin/pydantic/pydantic
               python version: 3.9.13 (main, May 19 2022, 14:10:54)  [Clang 13.1.6 (clang-1316.0.21.2)]
                     platform: macOS-12.4-x86_64-i386-64bit
     optional deps. installed: ['devtools', 'dotenv', 'email-validator', 'typing-extensions']
```

```py
from pydantic import BaseModel, Field

class Model(BaseModel):
        empty_string_key: int = Field(alias='')

data = {'': 123}
m = Model(**data)
m.dict(by_alias=True)
```

## Current behavior:

The code causes the exception:

```
Traceback (most recent call last):
  File "<masked>/.pyenv/versions/3.10.1/lib/python3.10/code.py", line 90, in runcode
    exec(code, self.locals)
  File "<input>", line 1, in <module>
  File "pydantic/main.py", line 341, in pydantic.main.BaseModel.__init__
pydantic.error_wrappers.ValidationError: 1 validation error for Model
empty_string_key
  field required (type=value_error.m
--- Real fix (for your judgement only)
diff --git a/pydantic/fields.py b/pydantic/fields.py
--- a/pydantic/fields.py
+++ b/pydantic/fields.py
@@ -148,7 +148,7 @@ def __init__(self, default: Any = Undefined, **kwargs: Any) -> None:
         self.default = default
         self.default_factory = kwargs.pop('default_factory', None)
         self.alias = kwargs.pop('alias', None)
-        self.alias_priority = kwargs.pop('alias_priority', 2 if self.alias else None)
+        self.alias_priority = kwargs.pop('alias_priority', 2 if self.alias is not None else None)
         self.title = kwargs.pop('title', None)
         self.description = kwargs.pop('description', None)
         self.exclude = kwargs.pop('exclude', None)
@@ -394,8 +394,8 @@ def __init__(
     ) -> None:
 
         self.name: str = name
-        self.has_alias: bool = bool(alias)
-        self.alias: str = alias or name
+        self.has_alias: bool = alias is not None
+        self.alias: str = alias if alias is not None else name
         self.type_: Any = convert_generics(type_)
         self.outer_type_: Any = type_
         self.class_validators = class_validators or {}

--- Kel's top 3
  [1] Goal: Fix fake data generation for Optional type fields in Pydantic models
      AttributeError: 'NoneType' object has no attribute 'fake'
**Describe the bug**

In my pydantic data model I have the following attribute `X: List[Optional[types.Float32]]`. When I call fake() of this class I get the error 'AttributeError: 'NoneType' object has no attribute 'fake''

**To Reproduc
      Way: Assign internal_field for Union item types in ListField to support fake data generation
      - Locate ListField class definition and its __init__ and fake methods where item types are parsed.
      - Reproduce the AttributeError by calling fake() on a model containing a List of Optional/Union types.
      - Assign UnionField instance to self.internal_field when items_type is a union in ListField, and extract self.items_type from self.internal_field.get_avro_type() in both branches.
      - Run test suite to verify fake data generation for List of Optional types succeeds.
  [2] Goal: Allow serializing person name string values to JSON
      To_Json 'str' object has no attribute 'components'
<!-- Instructions For Filing a Bug: https://github.com/pydicom/pydicom/blob/master/CONTRIBUTING.md#filing-bugs -->

#### Description
<!-- Example: Attribute Error thrown when printing (0x0010, 0x0020) patient Id> 0-->

When converting a dataset
      Way: Fix JSON serialization and deserialization for PersonName and AttributeTag
      - Identify where DICOM JSON serialization (`to_json`) and deserialization (`from_json`) handle value representations (VRs), specifically Person Name (`PN`) and Attribute Tag (`AT`).
      - Update `to_json` handling for `PN` VR so that `self.is_empty` is checked, multi-valued person names (VM > 1) are iterated over, string values are coerced to unicode person name instances if needed, and all existing components (Alphabetic, Ideographic, Phonetic) are populated into a dictionary per value.
      - Update `from_json` handling for `PN` VR to properly reconstruct delimiters (`=`) joining Alphabetic, Ideographic, and Phonetic components into a valid person name string.
      - Implement JSON serialization for `AT` VR by formatting integer tag values as uppercase 8-character hex strings (`format(v, '08X')`), and handle deserialization of hex strings back to tag integers with invalid input warnings.
      - Run the JSON conversion test suite to verify PN and AT roundtrips and error handling.
  [3] Goal: Allow using an empty string as a field data key
      Empty string data_key disallowed
Having an empty string as `data_key` doesn't seem to work.

```py
import marshmallow
from marshmallow import fields


class RootSchema(marshmallow.Schema):
    x = fields.Raw(data_key="")


RootSchema().load({'': 1})

```
```py

---> 14 RootSchema().l
      Way: Allow empty string as field data key in schema serialization and deserialization
      - Locate where field data keys are resolved during serialization, deserialization, validation, and duplicate checking in the schema class.
      - Reproduce the issue by writing a test case that defines a schema with a field having `data_key=""` and attempts to load data containing an empty string key.
      - Replace truthiness checks on `field_obj.data_key` (such as `field_obj.data_key or attr_name`) with explicit `is not None` checks (such as `field_obj.data_key if field_obj.data_key is not None else attr_name`) across all serialization, deserialization, validation, and duplicate-checking logic in the schema class.
      - Run the test suite to verify that empty string data keys are now correctly recognized and processed without raising validation errors for unknown fields.

=== TASK marcharper__python-ternary-136 | repo marcharper/python-ternary | python
--- Issue
Planar to coordinates
Hello!

How to convert coordinates from planar simplex to maps (x,y,z) ternary? 
--- Real fix (for your judgement only)
diff --git a/ternary/helpers.py b/ternary/helpers.py
index a72cbb9..06c4ae7 100644
--- a/ternary/helpers.py
+++ b/ternary/helpers.py
@@ -103,6 +103,26 @@ def project_point(p, permutation=None):
     return numpy.array([x, y])
 
 
+def planar_to_coordinates(p,scale):
+    """
+    Planar simplex (regular x,y) to maps (x,y,z) ternary coordinates. The order of the coordinates is counterclockwise from the origin.
+
+    Parameters
+    ----------
+    p: 2-tuple
+        The planar simplex (x, y) point to be transformed to maps (x,y,z) coordinates
+    
+    scale: Int
+        The normalized scale of the simplex, i.e. N such that points (x,y,z)
+        satisify x + y + z == N
+
+    """
+    x = p[0] - y/2
+    y = p[1] /  SQRT3OVER2
+    z = scale - (x + y)
+    return numpy.array([x, y, z])
+    
+    
 def project_sequence(s, permutation=None):
     """
     Projects a point or sequence of points using `project_point` to lists xs, ys
diff --git a/ternary/ternary_axes_subplot.py b/ternary/ternary_axes_subplot.py
index 85a8ecf..629a223 100644
--- a/ternary/ternary_axes_subplot.py
+++ b/ternary/ternary_axes_subplot.py
@@ -441,7 +441,7 @@ class TernaryAxesSubplot(object):
                             cb_kwargs=cb_kwargs)
 
     def heatmapf(self, func, scale=None, cmap=None, boundary=True,
-                 style='triangular', colorbar=True, scientific=True,
+                 style='triangular', colorbar=True, scientific=False,
                  vmin=None, vmax=None, cbarlabel=None, cb_kwargs=None):
         if not scale:
             scale = self.get_scale()

--- Kel's top 3
  [1] Goal: Support general grid mapping coordinate transformations
      use more general grid mapping
We would like to have more general grid mappings. In this example, we use cartopy to compute the transformed coordinates in the global lat lon grid:

```python
import xarray as xr
from cartopy import crs as ccrs
import cordex as cx

eur11 = cx.cordex_domain('EUR-
      Way: generalize coordinate transformations with pyproj and cf-xarray
      - Identify coordinate transformation utilities relying on hardcoded rotated-pole logic and find where domain dataset creation calculates lat/lon coordinates.
      - Add pyproj (>=3.3.0) and cf_xarray (>=0.8.0) to package requirements to handle arbitrary CF grid mapping definitions and proj transformations.
      - Create or update transformation module functions (`transform`, `transform_coords`, `grid_mapping`, `vertices`) using pyproj CRS objects (`CRS.from_cf` and pyproj `Transformer`) to convert X/Y or rotated coordinates to target geographical coordinates.
      - Refactor domain creation helpers (`_get_rotated_dataset`, dummy variable creation) to use the new `transform` function and cf_xarray spatial dimension accessors (`ds.cf['Y']`, `ds.cf['X']`) instead of hardcoded cartopy rotated-pole steps.
      - Run the transformation test suite to verify coordinate transformations accurately convert between source CRS and target PlateCarree/WGS84.
  [2] Goal: Move coordinate conversion functions from transform to convert module
      Shift llh2xyz, xyz2llh, grid2geo, and geo2grid from transform module to convert module
These four functions are coordinate conversions within the same datum and are better placed in the convert module
      Way: relocate intradatum coordinate conversion functions between modules
      - Inspect the functions to move (llh2xyz, xyz2llh, geo2grid, grid2geo) and identify all helper functions, projection parameters, and math imports they depend on.
      - Move llh2xyz, xyz2llh, geo2grid, grid2geo, and their dependent helpers (rect_radius, alpha_coeff, beta_coeff, psfandgridconv, and projection constant `proj`) into the destination module, adding required math and constant imports.
      - Remove the moved functions and unused imports/constants from the source module.
      - Update internal package imports across the codebase that referenced the moved functions from the old module so they now import from the destination module.
      - Run the test suite to verify tests pass after relocating the functions.
  [3] Goal: Allow passing custom values for new coordinates during vector dimension conversions
      Allow users to pass new coordinate values in `to_Vector{X}D` and `to_{new coordinate names}`
### Describe the potential feature

Right now users can convert a 2D vector to 3D or 4D using `to_Vector3D()` and `to_Vector4D()`, but the returned vector always possesses 0 as the value of the new generated
      Way: allow custom coordinate parameters during vector dimension conversions
      - Locate dimension conversion methods (`to_Vector3D` and `to_Vector4D`) across vector classes in vector methods module.
      - Reproduce the missing optional coordinate keyword arguments by calling `to_Vector3D(z=1)` or `to_Vector4D(t=1)` on vector objects.
      - Update signature and logic of `to_Vector3D` and `to_Vector4D` in Vector2D and Vector3D classes to accept optional longitudinal (z, eta, theta) and temporal (t, tau) keyword arguments, validate at most one coordinate per category is passed, and set corresponding target coordinate types.
      - Run unit tests verifying conversion with custom longitudinal and temporal parameters across object, numpy, and awkward backends.

=== TASK timeoutdigital__treehugger-63 | repo timeoutdigital/treehugger | python
--- Issue
Make 'edit' reuse previous encrypted values that weren't changed
When editing a file, every `to_encrypt` value gets re-encrypted, which changes all the encrypted values as the encrypted blob KMS uses seems to include a timestamp. This is less than ideal as it means changing one password changes the whole file, leading to horrible diffs.

It would be nice if when running 'edit', treehugger compared the before/after, and reused the `encrypted` values for all those things that haven't changed their `to_encrypt` forms. This could maybe be done with a cache layer around the decrypt/encrypt calls to KMS, such that re-encrypting reuses the value found when decrypting to edit.
--- Real fix (for your judgement only)
diff --git a/HISTORY.rst b/HISTORY.rst
index f4be37f..8b242d6 100644
--- a/HISTORY.rst
+++ b/HISTORY.rst
@@ -9,6 +9,7 @@ Pending Release
 
 * (Insert new release notes below this line)
 * Add ``--json`` argument to ``print`` command.
+* Make 'edit' reuse previous encrypted values that weren't changed.
 
 1.1.0 (2017-04-05)
 ------------------
diff --git a/treehugger/kms.py b/treehugger/kms.py
index 47cc221..bb02d68 100644
--- a/treehugger/kms.py
+++ b/treehugger/kms.py
@@ -11,6 +11,9 @@ from .ec2 import get_current_region
 
 class KMSAgent(object):
 
+    def __init__(self):
+        self.cache = {}
+
     key_id = 'alias/treehugger'
 
     @property
@@ -29,9 +32,14 @@ class KMSAgent(object):
             CiphertextBlob=cipher_blob,
             EncryptionContext=encryption_context,
         )
-        return response['Plaintext'].decode('utf-8')
+        plaintext = response['Plaintext'].decode('utf-8')
+        self.cache[plaintext] = base64_ciphertext
+        return plaintext
 
     def encrypt(self, plaintext, encryption_context):
+        if plaintext in self.cache:
+            return self.cache[plaintext]
+
         response = self.kms_client.encrypt(
             KeyId=self.key_id,
             Plaintext=plaintext.encode('utf-8'),

--- Kel's top 3
  [1] Goal: Support recursive directory encryption and decryption while preserving directory structure
      Add Support for Recursive Directory Encryption\Decryption
Currently, the `encrypt`/`decrypt` command supports encrypting files in a single directory but does not handle files in subdirectories. This limitation can be restrictive for users who organize their environment files in nested directory stru
      Way: recursive directory processing preserving relative structure
      - Create a reusable CLI option decorator for `--recursion` / `-r` flag to allow opting into recursive directory traversal.
      - Implement a helper function that accepts source and target directory paths, a recursion flag, and a processing callback. Use `Path.rglob('*')` when recursion is enabled and `Path.iterdir()` otherwise, skipping non-files and ensuring parent target directories are created using `Path.relative_to(input_dir)` before invoking the file processing callback.
      - Apply the recursion decorator and integrate the directory traversal helper in the encryption and decryption CLI commands, delegating individual file encryption or decryption via callback functions.
      - Run the test suite to verify recursive and non-recursive directory encryption and decryption.
  [2] Goal: Add secure value masking when dumping or converting to tree
      Add Secure Value Masking
Add a new argument to `dumps` and `to_tree` that can mask secure values.
      Way: support  sensitive value masking and virtual fields in config serialization
      - Locate field base classes and config serialization methods to understand field metadata attributes and tree export routines.
      - Add a `sensitive: bool = False` attribute to base `Field.__init__` so fields can be marked sensitive.
      - Update `Config.to_tree()` to accept `virtual: bool = False` and `sensitive_mask: str = None`. Include virtual fields when `virtual=True`, pass parameters recursively to sub-schema configs, and mask sensitive field values according to `sensitive_mask` (single character repeats length of value; multicharacter/empty replaces whole value; None leaves as-is).
      - Update `Config.dumps()` to accept `virtual` and `sensitive_mask` keyword arguments and forward them to `self.to_tree()`.
      - Run the test suite to verify sensitive value masking, virtual field handling, and serialization options.
  [3] Goal: Allow restoring stashed changes when intent-to-add files exist
      "git add -N" prevents restoring stashed changes.
To reproduce, start with this simple `pre-commit-config.yaml` in an otherwise empty repo:

```yaml
repos:
- repo: https://github.com/pre-commit/pre-commit-hooks
  rev: master
  hooks: 
    - id: end-of-file-fixer
```

The hook used doesn't r
      Way: Handle Git intent-to-add files during stash/restore
      - Reproduce the issue by creating a Git repository, adding a pre-commit hook, and then using `git add -N` to create an 'intent-to-add' file. Commit other changes, then run `pre-commit`.
      - Identify the Git status parsing logic to detect 'intent-to-add' files. This involves using `git status --porcelain -z` and parsing its output to find files marked with status 'A' in the second column.
      - Modify the `staged_files_only` context manager to temporarily remove 'intent-to-add' files from the Git index before other unstaged changes are handled. This prevents `git apply` from failing when restoring stashed changes.
      - Ensure that 'intent-to-add' files are restored to their original state in the Git index after the pre-commit hooks have run and other stashed changes have been restored.
      - Run the provided tests to confirm the fix works and does not introduce regressions.

=== TASK streamlink__streamlink-3202 | repo streamlink/streamlink | python
--- Issue
Zattoo (403 Client Error)
<!--
Thanks for reporting a plugin issue!
USE THE TEMPLATE. Otherwise your plugin issue may be rejected.

First, see the contribution guidelines:
https://github.com/streamlink/streamlink/blob/master/CONTRIBUTING.md#contributing-to-streamlink

Also check the list of open and closed plugin issues:
https://github.com/streamlink/streamlink/issues?q=is%3Aissue+label%3A%22plugin+issue%22

Please see the text preview to avoid unnecessary formatting errors.
-->


## Plugin Issue

<!-- Replace [ ] with [x] in order to check the box -->
- [x] This is a plugin issue and I have read the contribution guidelines.


### Description

<!-- Explain the plugin issue as thoroughly as you can. -->


### Reproduction steps / Explicit stream URLs to test

<!-- How can we reproduce this? Please note the exact steps below using the list format supplied. If you need more steps please add them. -->

1. streamlink -o "RTS Un SD.ts" https://zattoo.com/watch/rts_un 1500k --zattoo-email xxx@xxx.xxx --zattoo-password xxx


### Log output

<!--
TEXT LOG OUTPUT IS REQUIRED for a plugin issue!
Use the `--loglevel debug` parameter and avoid using parameters which suppress log output.
https://streamlink.github.io/cli.html#cmdoption-l

Make sure to **remove usernames and passwords**
You can copy the output to https://gist.github.com/ or paste it below.
-->

```
[plugin.zattoo][debug] Restored cookies: zattoo.session, pzuid, beaker.session.id
[
--- Real fix (for your judgement only)
diff --git a/.codecov.yml b/.codecov.yml
new file mode 100644
index 00000000..1df71070
--- /dev/null
+++ b/.codecov.yml
@@ -0,0 +1,44 @@
+codecov:
+  notify:
+    require_ci_to_pass: true
+    # wait until at all test runners have uploaded a report (see the test job's build matrix)
+    # otherwise, coverage failures may be shown while some reports are still missing
+    after_n_builds: 10
+comment:
+  # this also configures the layout of PR check summaries / comments
+  layout: "reach, diff, flags, files"
+  # don't ever let the codecov bot comment on PRs
+  after_n_builds: 999999999
+coverage:
+  range: "50...100"
+  precision: 2
+  round: down
+  status:
+    changes: false
+    patch: false
+    # split up coverage reports by path
+    # don't set coverage targets and instead set coverage thresholds
+    project:
+      # we can't disable the overall default project, because only this will have PR check summaries / comments
+      # this replaces the PR comment
+      default:
+        target: 50
+      streamlink:
+        threshold: 1
+        paths:
+          - "src/streamlink/"
+          - "!src/streamlink/plugins/"
+      streamlink_cli:
+        threshold: 1
+        paths:
+          - "src/streamlink_cli/"
+      plugins:
+        # don't set a threshold on plugins
+        target: 30
+        paths:
+          - "src/streamlink/plugins/"
+      tests:
+        # new tests should always be fully covered
+        threshold: 0
+        paths:
+          - "tests/"
diff --git a/.coveragerc b/.coveragerc
index 742386c4..f99370cf 100644
--- a/.coveragerc
+++ b/.coveragerc
@@ -1,6 +1,7 @@
 [run]
 source =
     src
+    tests
 
 [report]
 omit =
diff --git a/.github/workflows/main.yml b/.github/workflows/main.yml
index 3200d07a..a74d6186 100644
--- a/.github/workflows/main.yml
+++ b/.github/workflows/main.yml
@@ -17,7 +17,8 @@ jobs:
     name: Test
     strategy:
       fail-fast: false
-      # when changing the build matrix, the `after_n_builds` value in co
--- Kel's top 3
  [1] Goal: Add extractor support for zaiko.io video streams
      Support for https://zaiko.io/
### DO NOT REMOVE OR SKIP THE ISSUE TEMPLATE

- [X] I understand that I will be **blocked** if I *intentionally* remove or skip any mandatory\* field

### Checklist

- [X] I'm reporting a new site support request
- [X] I've verified that I'm running yt-dlp version **202
      Way: implement-zaiko-extractor
      - Define a new InfoExtractor class `ZaikoIE` in a new module `yt_dlp/extractor/zaiko.py` matching Zaiko event and stream URLs.
      - Export `ZaikoIE` in `yt_dlp/extractor/_extractors.py` so the extractor is registered into the extractor registry.
      - Implement `_real_extract` in `ZaikoIE` to handle authentication, parse event/stream pages, and extract stream manifest/formats.
      - Run the test suite including JavaScript interpreter and signature tests to confirm no regressions.
  [2] Goal: Fix Chaturbate extractor failing with HTTP 403 Forbidden error
      Chaturbate Extractor Returns a 403 error
### DO NOT REMOVE OR SKIP THE ISSUE TEMPLATE

- [X] I understand that I will be **blocked** if I *intentionally* remove or skip any mandatory\* field

### Checklist

- [X] I'm reporting that a **supported** site is broken
- [X] I've verified that I'm running 
      Way: Fix thumbnail extension extraction and add missing extractors
      - Investigate thumbnail extension handling in YoutubeDL when media formats or thumbnails provide custom or non-standard URLs.
      - Update thumbnail writing logic in yt_dlp/YoutubeDL.py to prefer the 'ext' attribute explicitly set on thumbnail objects before falling back to determine_ext from the thumbnail URL.
      - Add support for missing extractors such as Bandlab, RadioRadicale, and fix thumbnail helper in AfreecaTV.
      - Run the test suite to verify traversal helpers and extractor changes pass without errors.
  [3] Goal: Fix HTTP 403 Forbidden errors when downloading from SpankBang
      spankbang - 403 Forbidden errors
### DO NOT REMOVE OR SKIP THE ISSUE TEMPLATE

- [X] I understand that I will be **blocked** if I *intentionally* remove or skip any mandatory\* field

### Checklist

- [X] I'm reporting that a **supported** site is broken
- [X] I've verified that I'm running y
      Way: allow explicit thumbnail extension override in metadata dicts
      - Identify where thumbnail download filenames and extensions are resolved from thumbnail metadata dictionaries.
      - Update thumbnail writing logic to inspect thumbnail dicts for an explicit 'ext' key before falling back to determine_ext on the thumbnail URL.
      - When format/multiple prefixes are added to the thumbnail extension, format the resolved extension string accordingly.
      - Run the traversal and downloader test suite to verify tests pass.

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
 
 

--- Kel's top 3
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
--- Kel's top 3
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
  [3] Goal: Ensure vector alignment returns valid rigid transformation matrices for nearly antiparallel vectors
      trimesh.geometry.align_vectors returns invalid rigid transform
Related to #540, it seems like there is still an issue where the method returns an invalid rigid transform when passed input that is close to the reverse vector. An example:
```python
In [1]: import trimesh
In [2]: import numpy as np
      Way: construct 180-degree rotation matrix using perpendicular vector basis for vector alignment
      - Identify where vector alignment and basis generation are defined in the geometry and utility modules.
      - Reproduce the determinant sign flip or non-rigid matrix output when aligning nearly antiparallel 3D unit vectors using the existing SVD logic.
      - Replace the SVD matrix construction for antiparallel vectors with 180-degree rotation around an arbitrary perpendicular basis vector obtained from generating a basis on (a - b). Use matrix formula 2*outer(perp, perp) - eye(3). Also fix fallback vector generation in basis generation so degenerate inputs return a valid orthogonal basis.
      - Run alignment test suite to verify determinant is strictly positive 1.0 for antiparallel and nearly antiparallel inputs.

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
--- Kel's top 3
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
  [2] Goal: Update documentation examples to explicitly set CRS when creating spatial dataframes
      ENH: Improves Examples in Gallery to Better Explain Plotting Coordinates With Background Maps
#### Is your feature request related to a problem?

The example "[Creating a GeoDataFrame from a DataFrame with coordinates](https://geopandas.org/en/stable/gallery/create_geopandas_from_pandas.html)" in 
      Way: explicitly set CRS when creating spatial dataframes in documentation
      - Locate gallery documentation or Jupyter notebook tutorials showing GeoDataFrame creation from coordinate columns.
      - Inspect the notebook cells constructing the GeoDataFrame from latitude/longitude coordinates.
      - Update the constructor call to pass `crs="EPSG:4326"` alongside `geometry=points_from_xy(...)`, and update preceding markdown text explaining why explicitly setting the CRS matters for lat/lon data.
      - Run the test suite across documentation and core modules to confirm no regressions.
  [3] Goal: Include coordinate reference system information in GeoJSON string export
      ENH: include CRS in "to_json"
_Note (by @martinfleis): see the discussion below for details on the enhancement proposal._

#### Question about geopandas

gdf.to_json() 

> {"type": "FeatureCollection", 
> "features": [{**"id": "0500"**, "type": "Feature", **"properties": {}**, "geometry": ...
      Way: include coordinate reference system in GeoJSON string export
      - Locate the GeoJSON serialization logic in the GeoDataFrame class, specifically the `to_json` method and the `__geo_interface__` property.
      - Modify the `to_json` method to check if the dataframe has a defined CRS that is not the default WGS84 (EPSG:4326). If so, attempt to resolve its authority and code using `crs.to_authority()`. If the authority is valid, append the CRS metadata in the standard URN OGC format to the serialized dictionary before dumping it to JSON. If the CRS cannot be represented, issue a warning.
      - Ensure that the `__geo_interface__` property remains unaffected and does not include the CRS metadata, as it is not part of the standard `__geo_interface__` specification.
      - Run the test suite to verify that the CRS is correctly included in `to_json` output, warnings are raised for non-representable CRSs, and existing tests pass.

=== TASK pydantic__pydantic-2319 | repo pydantic/pydantic | python
--- Issue
Empty tuple type
# Bug

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
             pydantic version: 1.7.3
            pydantic compiled: True
                 install path: /home/guilherme/PycharmProjects/bulk-uploader/venv/lib/python3.8/site-packages/pydantic
               python version: 3.8.5 (default, Jul 28 2020, 12:59:40)  [GCC 9.3.0]
                     platform: Linux-5.4.0-65-generic-x86_64-with-glibc2.29
     optional deps. installed: ['typing-extensions']

```


```py
from pydantic import BaseModel

class Example(BaseModel):
    example: Tuple[()]
```

Will result in:

```
Traceback (most recent call last):
  File "pydantic/validators.py", line 615, in pydantic.validators.find_validators
TypeError: issubclass() arg 1 must be a class

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
  File "...", line 12, in <module>
    class Example(BaseModel):
  File "pydantic/main.py", line 262, in pydantic.main.ModelMetaclass.__new__
  File "pydantic/fields.py", line 315, in pydantic.fields.ModelField.infer
  File "pydantic/fields.py", line 284, in pydantic.fields.ModelField.__init__
  File "pydantic/fields.py", line 356, in pydantic.fields.ModelField.prepare
  File "pydantic/fields.py", line 458, in pydantic.fields.ModelField._type_analysis
  File "pydantic/fields.py", line 516, in pydantic.fields.Mode
--- Real fix (for your judgement only)
diff --git a/pydantic/fields.py b/pydantic/fields.py
--- a/pydantic/fields.py
+++ b/pydantic/fields.py
@@ -449,18 +449,20 @@ def _type_analysis(self) -> None:  # noqa: C901 (ignore complexity)
 
         if issubclass(origin, Tuple):  # type: ignore
             # origin == Tuple without item type
-            if not get_args(self.type_):
+            args = get_args(self.type_)
+            if not args:  # plain tuple
                 self.type_ = Any
                 self.shape = SHAPE_TUPLE_ELLIPSIS
-            else:
+            elif len(args) == 2 and args[1] is Ellipsis:  # e.g. Tuple[int, ...]
+                self.type_ = args[0]
+                self.shape = SHAPE_TUPLE_ELLIPSIS
+            elif args == ((),):  # Tuple[()] means empty tuple
                 self.shape = SHAPE_TUPLE
+                self.type_ = Any
                 self.sub_fields = []
-                for i, t in enumerate(get_args(self.type_)):
-                    if t is Ellipsis:
-                        self.type_ = get_args(self.type_)[0]
-                        self.shape = SHAPE_TUPLE_ELLIPSIS
-                        return
-                    self.sub_fields.append(self._create_sub_type(t, f'{self.name}_{i}'))
+            else:
+                self.shape = SHAPE_TUPLE
+                self.sub_fields = [self._create_sub_type(t, f'{self.name}_{i}') for i, t in enumerate(args)]
             return
 
         if issubclass(origin, List):

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
  [2] Goal: Allow validation of generic Enum type fields with enum instances
      Doesn't correctly validate when type is Enum
# Bug

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
             pydantic version: 1.5.1
            pydantic compiled: True
                 install path: /Users/<redacted>/.venvs/<redacted>/lib/python3.6
      Way: validate generic Enum and IntEnum type annotations with instances
      - Identify how Enum subclasses are validated in type mapping and validator lookup, noting where `lenient_issubclass(type_, Enum)` is checked vs specific enum subclass members.
      - Define distinct error classes for invalid enum members (`EnumMemberError`), generic enum instances (`EnumError`), and integer enum instances (`IntEnumError`) in error definitions.
      - Add validators for `Enum` and `IntEnum` base types that verify incoming values are instances of `Enum` or `IntEnum` respectively, distinguishing them from concrete Enum subclasses which validate allowed member values.
      - Update field validator dispatch and schema generation to treat `Enum` and `IntEnum` as base types rather than empty member enumerations.
      - Run the test suite verifying generic Enum/IntEnum annotations and custom enum schema properties pass.
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

=== TASK maikelboogerd__eventcore-kafka-7 | repo maikelboogerd/eventcore-kafka | python
--- Issue
Support for blocking events
Whenever a consumer fails to process a message successfully sometimes it's desired behaviour to make that a blocking event.
For instance if your events need to be consumed in order you'd want to event to be blocking:
```
MESSAGE 1: Order 500 items of product Eggs
MESSAGE 2: Change order to 5 items of product Eggs
```
If `MESSAGE 2` fails, the end user is not going to be too happy about it. In this case you want the last message to be processed last (or the first not at all, but you can't anticipate that at all times). 

A probable cause of `MESSAGE 2` failure might be: NetworkError (DB, HTTP) or a failure in logic (schema change).
You might be able to handle NetworkErrors of error by retrying `MESSAGE 2`. But logic flaws need to be fixed manually and this might pose a problem when you have `Z` seconds to resolve the issue.
--- Real fix (for your judgement only)
diff --git a/eventcore_kafka/__init__.py b/eventcore_kafka/__init__.py
index b16a1d8..6edf4ad 100644
--- a/eventcore_kafka/__init__.py
+++ b/eventcore_kafka/__init__.py
@@ -1,2 +1,2 @@
-from .producer import KafkaProducer # noqa
-from .consumer import KafkaConsumer # noqa
+from .producer import KafkaProducer  # noqa
+from .consumer import KafkaConsumer, BlockingKafkaConsumer  # noqa
diff --git a/eventcore_kafka/consumer.py b/eventcore_kafka/consumer.py
index 3d380b4..650bad1 100644
--- a/eventcore_kafka/consumer.py
+++ b/eventcore_kafka/consumer.py
@@ -2,8 +2,9 @@ import json
 import logging
 
 import confluent_kafka as kafka
-
+from confluent_kafka.cimpl import Message
 from eventcore import Consumer
+from eventcore.exceptions import FatalConsumerError
 
 log = logging.getLogger(__name__)
 
@@ -16,14 +17,13 @@ class KafkaConsumer(Consumer):
     :param topics: list of topics to consume from.
     """
 
-    def __init__(self, servers, group_id, topics):
+    def __init__(self, servers, group_id, topics, **kwargs):
         # Parse the servers to ensure it's a comma-separated string.
         if isinstance(servers, list):
             servers = ','.join(servers)
-        self.kafka_consumer = kafka.Consumer({
-            'bootstrap.servers': servers,
-            'group.id': group_id
-        })
+        settings = {'bootstrap.servers': servers, 'group.id': group_id}
+        settings.update(kwargs)
+        self.kafka_consumer = kafka.Consumer(settings)
         # Parse the topics to ensure it's a list.
         if isinstance(topics, str):
             topics = topics.split(',')
@@ -32,28 +32,76 @@ class KafkaConsumer(Consumer):
 
     def consume(self):
         while True:
-            message = self.kafka_consumer.poll(1.0)
-            if not message:
-                continue
-            if message.error():
-                # PARTITION_EOF error can be ignored.
-                if message.error().code() == kafka.KafkaError._PARTITION_EOF:
-                  
--- Kel's top 3
  [1] Goal: Process connection blocked events while waiting for consume confirmation
      Connection blocked events do not get processed when calling `consume()`
If RabbitMQ is below a resource limit when I connect, the BlockingConnection can end up in a hanging state if I first publish a message and then directly go into `consume()`, even when using the `inactivity_timeout` parameter.

      Way: Add blocked connection timeout handling across AMQP adapters
      - Identify where connection blocked/unblocked signals are processed in the core AMQP connection class and note how blocked connections cause synchronous adapters to hang indefinitely when waiting on broker responses.
      - Add a `blocked_connection_timeout` configuration parameter to connection parameters to allow user-defined timeout limits for blocked broker states.
      - In the base `Connection` class, set up timer management so when a `connection.blocked` frame is received, a timeout timer is started, and when `connection.unblocked` or connection termination occurs, the timer is cleared.
      - Implement the blocked timeout callback to terminate the connection with an appropriate error reason code when the timeout triggers.
      - Run unit tests verifying blocked timer creation, unblocked cleanup, repeated blocked frames, and connection termination upon timeout.
  [2] Goal: Allow thread-safe interruption of blocking channel message consumption
      Question re. interrupting `BlockingChannel.consume` from another thread
In [Dramatiq][dramatiq] I've got an architecture where consumers live on their own thread and call `BlockingChannel`'s `consume` method with an inactivity timeout of `1s` by default. Whenever a consumer gets a message from Rabbi
      Way: Add thread-safe callback dispatch to connections and IOLoops
      - Identify where IOLoops and connection adapters handle callbacks and event loops across connection types (base connection, blocking connection, asyncio connection, select connection).
      - Implement `add_callback_threadsafe` across connection base classes and adapters to validate that the callback parameter is callable and schedule it safely onto the target event loop.
      - Delegate thread-safe callbacks in `BlockingConnection` by wrapping them as zero-delay timeouts so existing reentrancy logic executes the callback within the connection's thread.
      - Run the test suite to verify thread-safe callback scheduling and connection unit tests pass.
  [3] Goal: Prevent consumer starvation by receiving messages without waiting for batch completion
       snssqs: fix consumer starvation
## Actual Behavior
The current implementation uses separate goroutines to process each message retrieved by sqsClient.ReceiveMessageWithContext, which is executed when concurrencyMode is set to parallel. The consumeSubscription function waits for all spawned gorouti
      Way: decouple batch message fetching from processing via worker semaphore
      - Identify the queue consumer loop waiting for each batch of messages to finish processing before polling for new messages.
      - Add an optional concurrency limit configuration field to metadata parsing and validate that negative values return an error.
      - Remove sync.WaitGroup batch barrier synchronization across message processing iterations.
      - Introduce a buffered channel semaphore to bound parallel worker goroutines when concurrency limit is greater than zero.
      - Run package unit tests to verify metadata validation and subscriber behavior.

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
--- Kel's top 3
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
  [2] Goal: Add docstring examples for distance calculation functions
      Examples for functions in geogr.distances
For #86 we should provide examples for all external visible functions.
There are missing examples for
- `calculate_distance_matrix`
- `meters_to_decimal_degrees`

they can be found [here](https://github.com/mie-lab/trackintel/blob/b89f0c8c35cc1adcc858a8
      Way: add docstring examples and document missing preprocessing and io functions
      - Locate the distance calculation functions in the geography module and the CSV read/write functions in the input/output module.
      - Add Sphinx-style docstring examples and document missing parameters (such as args and kwargs) for the identified functions.
      - Add the missing staypoint merging function to the preprocessing documentation file.
      - Run the preprocessing tests to verify that the changes do not break existing functionality.
  [3] Goal: Fix boundary edges calculation for 3D meshes
      Incorrect edges returned by `Mesh3D.boundary_edges`
I've been working on applying the block-diagonally preconditioned MINRES+AMG Stokes solver of #342 & ex32 to other MeshTet geometries and found some odd errors.  It seems to have nothing to do with the operators but something to do with `.find_dofs
      Way: identify 3D mesh boundary edges via boundary facets instead of boundary nodes
      - Locate the `boundary_edges` method in the 3D mesh base class.
      - Write a test verifying that `boundary_edges` returns only edges that are part of boundary facets, excluding interior edges whose endpoints happen to lie on the boundary.
      - Update `Mesh3D.boundary_edges` to collect edges from `boundary_facets()` and find their matching indices in `self.edges`, rather than checking if both end nodes belong to `boundary_nodes()`.
      - Run test suite to verify the fix works and resolves the issue.

=== TASK EricssonResearch__arms-41 | repo EricssonResearch/arms | python
--- Issue
Communication with the ABB robot (IRC5)
A class that handles the communication with the IRC5. Requirements:
- Creating/ closing the socket.
- Sending/ receiving of messages in a readable way.
- Implementing of some example cases.

In addition, tests shall show that the class works as intended.
--- Real fix (for your judgement only)
diff --git a/arms/units/__init__.py b/arms/units/__init__.py
new file mode 100644
index 0000000..631db19
--- /dev/null
+++ b/arms/units/__init__.py
@@ -0,0 +1,1 @@
+"""Modules related to units"""
diff --git a/arms/units/abb.py b/arms/units/abb.py
new file mode 100644
index 0000000..5d39fa1
--- /dev/null
+++ b/arms/units/abb.py
@@ -0,0 +1,137 @@
+"""Utilities related to the communication with the ABB robotic arm."""
+
+import ast
+import json
+from arms.misc.socket import SocketClient
+from arms.utils import log
+
+
+class ABB:
+    """Handles the communication with the ABB robot with respect to
+        - sending command requests to and
+        - receiving/evaluating the responds from the ABB robot.
+
+    Attributes:
+        client: The socket interface.
+    """
+    def __init__(self):
+        self.client = SocketClient("abb")
+
+    def __enter__(self):
+        return self
+
+    def __exit__(self, exc_type, exc_val, exc_tb):
+        self.close()
+
+    def close(self):
+        """Close connection."""
+        case = 0
+        self._send(case)
+        self.client.close()
+
+    def get_system_information(self):
+        """Get system information."""
+        case = 1
+        ok, data = self._exchange(case)
+        return ok, data
+
+    def get_joint_coordinates(self):
+        """Get joint coordinates."""
+        case = 10
+        ok, data = self._exchange(case)
+        return ok, data
+
+    def _exchange(self, case, data=None):
+        """Exchanges messages with the ABB robot in two steps:
+            1. step: Send a command to the ABB robot.
+            2. step: Receive the answer from the ABB robot.
+
+        Moreover, the message to expect from the ABB robot is of JSON format
+        and is sent as a string; e.g.:
+            "{'id':1,'ok':1,'data':'[1, 2, 3]'}"
+        Hence, the 3. step is to convert this string into a dictionary.
+
+        Args:
+         case:  The case to address in the RAPID code running on the IRC5
+            
--- Kel's top 3
  [1] Goal: Refactor communicator to manage packet sending and receiving
      Re-structure Communicator
Based on discussion in 3bc29571ed1aef69f5b03bf1416e7bb15a072b1e

@romor wrote:

> Just my impression: I do not think that it is a nice solution to store a reference to communicator in `UTETeachIn` class. It could be provided as an argument to `send_response()` instead.
> 
>
      Way: move packet response logic from packet to communicator
      - Locate packet parsing logic in Packet.parse_msg and UTETeachIn class, where a communicator reference was passed to auto-send responses.
      - Rename UTETeachIn to UTETeachInPacket and remove the communicator dependency, communicator argument, and send_response method from it. Expose create_response_packet as a public method.
      - Update Communicator parsing loop to inspect parsed packets, check if a packet is a UTETeachInPacket and teach_in is enabled, and handle creating and sending the response packet directly.
      - Update all existing references and imports from UTETeachIn to UTETeachInPacket across test suites and example scripts.
      - Run pytest to verify teach-in response handling passes without communicator references inside packet classes.
  [2] Goal: Ensure WebSocket server sends correct close message status codes
      Websocket: Server incorrectly sends close message status 1005 or 1006
### Observed behavior

(This issue was reported internally by @aricart)

When a client does not send any status/payload in the close message, the server detects correctly that situation but is sending back status 1005 (no status
      Way: Prevent WebSocket server from sending reserved close status codes
      - Locate the WebSocket server's close message handling logic, specifically where incoming close frames are processed and where outgoing close messages are constructed based on internal server states.
      - Modify the logic that processes incoming WebSocket close frames to prevent sending a close message with status 1005 (No Status Received). If the received status is 1005, ensure the server's response close frame contains no status or payload.
      - Modify the logic that constructs server-initiated WebSocket close messages to prevent sending status 1006 (Abnormal Closure). Remap internal error conditions (like write errors, read errors, or stale connections) that previously resulted in 1006 to a valid, appropriate status code, such as 1001 (Going Away).
      - Run the provided tests to confirm the fix addresses the issue without introducing regressions.
  [3] Goal: Add asyncio adapter interfaces and abstract base classes
      asyncio Adapter support
With the now built-in support for an async enforcer, it would be great if we could migrate / make available asyncio stubs for the `BatchAdapter`, `UpdateAdapter`, `FilteredAdapter`, and `Adapter` interfaces as well.

That would complete the full feature set of loading from 
      Way: introducing dedicated asyncio adapter interfaces and abstract base classes
      - Inspect existing persistence adapter interfaces and synchronous/asynchronous enforcers to determine where adapter base classes are defined and how async enforcers validate adapter types.
      - Create dedicated asyncio adapter interfaces (AsyncAdapter, AsyncBatchAdapter, AsyncFilteredAdapter, AsyncUpdateAdapter, AsyncDispatcher) and base classes corresponding to their synchronous counterparts.
      - Refactor async adapter implementations (such as AsyncFileAdapter) to inherit from the new async adapter interfaces rather than sync interfaces.
      - Update AsyncInternalEnforcer and related async enforcers to check for AsyncAdapter instances instead of synchronous Adapter instances.
      - Ensure backwards compatibility aliases are preserved for existing adapter module imports.
      - Run test suite to verify synchronous and asynchronous adapter behaviors.

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
         
--- Kel's top 3
  [1] Goal: Avoid passing default desired capabilities when opening local Chrome browsers
      Open Browser keyword in 3.2 (and most likely 3.1 too) does not work with chromium-browser, but 3.0 works with chromium-browser. 
# Steps to reproduce the issue

```
| Open Browser | http://google.com | Chrome | # In Linux with chromium-browser |
```
# Error messages and additional information

      Way: Pass browser capabilities as keyword arguments to Selenium WebDriver
      - Identify the `Open Browser` keyword's implementation for the failing browser type (Chrome/Chromium) and locate where the Selenium WebDriver instance is created.
      - Observe how `desired_capabilities` are being passed to the `selenium.webdriver.Chrome` constructor. The issue indicates that explicitly passing `desired_capabilities` as a named argument, even if empty or default, causes a failure for local Chrome instances.
      - Modify the `_parse_capabilities` method to return a dictionary that can be unpacked as keyword arguments. For Chrome, this means returning `{'desired_capabilities': parsed_caps}`. For other browsers like IE, Firefox, and Edge, return `{'capabilities': parsed_caps}` to align with their specific constructor expectations.
      - Update the `create_chrome` (and similar browser creation) methods to unpack the dictionary returned by `_parse_capabilities` directly into the `webdriver.Chrome` constructor using the `**` operator, instead of passing `desired_capabilities` as a named argument.
      - Ensure the `_remote` method, used for remote WebDriver instances, also correctly handles the new structure of `desired_capabilities` by checking for and renaming the 'capabilities' key to 'desired_capabilities' if present, before passing them to `webdriver.Remote`.
      - Run the provided unit tests to confirm the fix resolves the issue and does not introduce regressions.
  [2] Goal: Allow passing SSL cert_data keyword argument in proxy connections
      Cannot pass ssl kwarg cert_data in proxy context
Discovered that one while writting tls-in-tls http/2 tests in #3030 

While we can do

```python
with proxy_from_url("...", ca_certs=DEFAULT_CA) as http:
    ...
```

we cannot 

```python
with proxy_from_url("...", cert_data=open(DEFAULT_
      Way: Propagate missing SSL keyword arguments through connection pool keys
      - Identify connection pool manager data structures and keyword sets where SSL configurations are filtered or mapped to pool cache keys.
      - Reproduce failure by instantiating a proxy manager or connection pool with the new SSL argument (such as ca_cert_data) and verifying it raises an unexpected keyword argument error.
      - Add ca_cert_data to SSL_KEYWORDS and add key_ca_cert_data (typed as str | bytes | None) to the PoolKey named tuple definition.
      - Run connection pool manager test suite to ensure ca_cert_data is accepted and keyed properly.
  [3] Goal: Pass proxy URL as a string to urllib3 ProxyManager
      urllib3.ProxyManager() instantiation is broken.
python 3.7.5
httpx 0.11.0
urllib3 1.25.7

```
$ ipython3 -c 'import httpx; r = httpx.get("https://www.google.com")'
parse_url http://127.0.0.1:1234 <class 'httpx.models.URL'>
----------------------------------------------------------------------
      Way: convert proxy url object to string for urllib3 ProxyManager
      - Trace the ProxyManager instantiation error from the traceback to where urllib3.ProxyManager is called with proxy.url.
      - Inspect urllib3 dispatcher initialization where ProxyManager is instantiated and check what type proxy.url has.
      - Pass str(proxy.url) instead of proxy.url into urllib3.ProxyManager proxy_url argument.
      - Run test suite to verify urllib3 proxy handling and decoding tests pass.

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

--- Kel's top 3
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

--- Kel's top 3
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
--- Kel's top 3
  [1] Goal: Allow Pilot click to use Screen as a selector
      Pilot.click can't use `Screen` as selector.
If you try something like `pilot.click(Screen)`, you get a `NoMatches` exception from the query.
      Way: Query root application rather than screen in test pilot actions
      - Inspect test automation methods (like pilot click or hover) where widget selectors are resolved against the current screen.
      - Write reproduction tests attempting to click or hover a selector referencing the top-level Screen widget itself.
      - Change widget query resolution in Pilot methods (such as click and hover) from `screen.query_one(selector)` to `app.query_one(selector)` so queries encompass top-level screens.
      - Run the pilot test suite to ensure selecting screens and child widgets works correctly for pilot actions.
  [2] Goal: Detect click events within a larger hit area
      Detect click events inside hit area
First, thank you for the library! Everything is very quick to set up and get working.

I wanted to get a similar functionality to VSCode where dblclick on the handle expands and collapses the panels. This works, except that, because the click events are only bei
      Way: Enable click events on resize handle hit area
      - Understand that the issue is about `PanelResizeHandle` not firing standard click events when the user clicks within its interactive area but without dragging.
      - Locate the `PanelResizeHandle` component definition and its associated types in the `react-resizable-panels` package.
      - Modify the `PanelResizeHandleProps` type to allow `onClick`, `onPointerDown`, and `onPointerUp` event handlers to be passed as props. These were previously explicitly omitted.
      - Update the `PanelResizeHandle` component to accept these new event handler props and store them in a `useRef` to ensure they are always current within event listeners.
      - Implement custom click detection logic within the `handlePointerUp` function: track if any pointer movement occurred between `pointerdown` and `pointerup`. If no movement occurred, invoke the `onClick` handler. Also, invoke `onPointerDown` and `onPointerUp` at the appropriate times.
      - If a wrapper component like `ResizeHandle.tsx` is used, ensure it passes through all `PanelResizeHandleProps` to the underlying `PanelResizeHandle` component.
  [3] Goal: Fix incorrect widget selection when clicking partially visible scrolled buttons
      Border demonstration acts improperly under scrolling
Recipe to reproduce:

Run `textual borders`. Click on `[Heavy`]. Border will change. Scroll down buttons panel to click on `[Wide]`. At top of screen click topmost visible button (in my case `[Heavy]`, but YMMV). _Ensure that only the bottom two
      Way: Matching mouse down and mouse up widgets for click generation
      - Identify the driver's mouse event handling logic where `Click` events are synthesized from `MouseDown` and `MouseUp` pairs.
      - Update the driver to record the widget at the mouse down coordinates (`self._mouse_down_widget = self._app.get_widget_at(event.x, event.y)[0]`) instead of only tracking timestamp.
      - Update the mouse up handler in the driver so a `Click` event is only emitted if the widget under the cursor on `MouseUp` matches `self._mouse_down_widget`.
      - Implement `mouse_down` and `mouse_up` helper methods on the test pilot to allow simulating individual mouse press and release actions in tests.
      - Run the test suite to verify focus, mouse press/release, and pilot click behaviors pass.

=== TASK pydantic__pydantic-2238 | repo pydantic/pydantic | python
--- Issue
Cannot parse nested object with custom root type
### Checks

* [x] I added a descriptive title to this issue
* [x] I have searched (google, github) for similar issues and couldn't find anything
* [x] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and couldn't find an answer
* [x] After submitting this, I commit to one of:
    * Look through open issues and helped at least one other person
    * Hit the "watch" button on this repo to receive notifications and I commit to help at least 2 people that ask questions in the future
    * Implement a Pull Request for a confirmed bug

<!-- Sorry to sound so draconian, but every second saved replying to issues is time spend improving pydantic :-) -->

# Question

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
python -c "import pydantic.utils; print(pydantic.utils.version_info())"
             pydantic version: 1.7.2
            pydantic compiled: True
                 install path: <path>/venv/lib/python3.7/site-packages/pydantic
               python version: 3.7.7 (default, Nov  3 2020, 00:02:20)  [Clang 12.0.0 (clang-1200.0.32.21)]
                     platform: Darwin-19.6.0-x86_64-i386-64bit
     optional deps. installed: []
```
Hello, thank you for the library - I have used it in production across multiple applications now! I am trying to parse a JSON with a nested JSON needing a custom root type of `Dict` - but get an error as shown in t
--- Real fix (for your judgement only)
diff --git a/pydantic/main.py b/pydantic/main.py
--- a/pydantic/main.py
+++ b/pydantic/main.py
@@ -509,12 +509,18 @@ def json(
         return self.__config__.json_dumps(data, default=encoder, **dumps_kwargs)
 
     @classmethod
-    def parse_obj(cls: Type['Model'], obj: Any) -> 'Model':
+    def _enforce_dict_if_root(cls, obj: Any) -> Any:
         if cls.__custom_root_type__ and (
             not (isinstance(obj, dict) and obj.keys() == {ROOT_KEY}) or cls.__fields__[ROOT_KEY].shape == SHAPE_MAPPING
         ):
-            obj = {ROOT_KEY: obj}
-        elif not isinstance(obj, dict):
+            return {ROOT_KEY: obj}
+        else:
+            return obj
+
+    @classmethod
+    def parse_obj(cls: Type['Model'], obj: Any) -> 'Model':
+        obj = cls._enforce_dict_if_root(obj)
+        if not isinstance(obj, dict):
             try:
                 obj = dict(obj)
             except (TypeError, ValueError) as e:
@@ -662,14 +668,13 @@ def __get_validators__(cls) -> 'CallableGenerator':
 
     @classmethod
     def validate(cls: Type['Model'], value: Any) -> 'Model':
+        value = cls._enforce_dict_if_root(value)
         if isinstance(value, dict):
             return cls(**value)
         elif isinstance(value, cls):
             return value.copy()
         elif cls.__config__.orm_mode:
             return cls.from_orm(value)
-        elif cls.__custom_root_type__:
-            return cls.parse_obj(value)
         else:
             try:
                 value_as_dict = dict(value)

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
  [2] Goal: ModelFactory must apply Annotated Field constraints to nested types
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
--- Kel's top 3
  [1] Goal: Fix string representation of delayed call objects when debug mode is enabled
      AttributeError: 'DelayedCall' object has no attribute 'creator'
|<img alt="allenap's avatar" src="https://avatars.githubusercontent.com/u/0?s=50" width="50" height="50">| allenap reported|
|-|-|
|Trac ID|trac#8306|
|Type|defect|
|Created|2016-04-26 16:04:05Z|

```
Python 3.5.1+ (default, Mar 30 2016
      Way: Fix missing attribute error when converting object to string after enabling debug mode
      - Locate the class defining the delayed call object and inspect its string/repr formatting logic when debug mode is active.
      - Define the optional creation traceback attribute (`creator`) at the class level with a default value of None.
      - Update the string representation method to check whether the creation traceback attribute is not None rather than relying solely on the debug flag.
      - Update exception logging checks that inspect the creation traceback attribute to check for `is not None` instead of `hasattr`.
      - Run unit tests for DelayedCall debug mode transitions.
  [2] Goal: Include typing imports in scope for custom class deserializers
      Missing types with custom class deserializers
The code generation doesn't put `Optional` or `Union` in scope when calling a custom class deserializer. This results in a `NameError` during deserialization.

Example
```py
from dataclasses import dataclass
from typing import Optional

from serde
      Way: expose typing module constructs in generated serde scopes
      - Reproduce the NameError by configuring a dataclass with a custom class deserializer and fields typed with typing constructs like Optional[T] or Union[...].
      - Update type inspection helpers in compat to yield bare typing origins (Optional, Union, List, Set, Tuple, Dict) during type iteration and handle bare typing forms safely in typename resolution.
      - Add a scope filtering helper in core to distinguish typing module objects from local scope assignments when rendering Jinja templates.
      - Update deserializer and serializer scope population and code generation templates to register typenames in scope globals and filter typing constructs from assignment headers.
      - Run the test suite covering type iteration and custom class serializers/deserializers.
  [3] Goal: Fix missing or misspelled attribute in MeshFix input spec
      'MeshFixInputSpec' object has no attribute 'save_as_vrml'
### Summary

Hi, I'm a newbie and am currently trying to revive a [4 years old repository](https://github.com/CyclotronResearchCentre/forward). This code makes use of MeshFix interface. When running an example I get an `AttributeError`.


      Way: fix misspelled attribute in interface input specification
      - Locate the interface definition file containing the input specification for the tool (MeshFix).
      - Identify the misspelled attribute name (e.g., 'save_as_vmrl' instead of 'save_as_vrml') in the input specification class, its `xor` constraints, and any logic referencing it.
      - Rename the misspelled attribute and all its references (including `xor` lists and conditional logic) to the correct spelling ('save_as_vrml').
      - Run the automated interface tests to verify that the input specification attributes are correctly defined and validated.

=== TASK nose-devs__nose2-410 | repo nose-devs/nose2 | python
--- Issue
verbosity is ignored when set in configuration file or as an argument to nose2.main()
This is, I think, related to, but is not the same as, issue #115. After seeing no affect when I set verbosity in my .cfg file or passed it as an argument from nose2.main(), I used a debugger to dive into the code and it seemed to lose its verbosity somewhere along the line and revert to using the session's default verbosity of 1.

I haven't been able to figure out what value verbosity takes when nose2 is run from the command line with "-v", though that does work.

--- Real fix (for your judgement only)
diff --git a/nose2/main.py b/nose2/main.py
index 67d5483..ea533c6 100644
--- a/nose2/main.py
+++ b/nose2/main.py
@@ -155,9 +155,17 @@ class PluggableTestProgram(unittest.TestProgram):
             dest='exclude_plugins', default=[],
             help="Do not load this plugin module")
         self.argparse.add_argument(
-            '--verbose', '-v', action='count', default=0, help="print test case names and statuses")
-        self.argparse.add_argument('--quiet', action='store_const',
-                                   dest='verbose', const=0)
+            '--verbosity', type=int,
+            help=("Set starting verbosity level (int). "
+                  "Applies before -v and -q"))
+        self.argparse.add_argument(
+            '--verbose', '-v', action='count', default=0,
+            help=("Print test case names and statuses. "
+                  "Use multiple '-v's for higher verbosity."))
+        self.argparse.add_argument(
+            '--quiet', '-q', action='count', default=0, dest='quiet',
+            help=("Reduce verbosity. Multiple '-q's result in "
+                  "lower verbosity."))
         self.argparse.add_argument(
             '--log-level', default=logging.WARN,
             help='Set logging level for message logged to console.')
@@ -172,13 +180,13 @@ class PluggableTestProgram(unittest.TestProgram):
         self.session.logLevel = util.parse_log_level(cfg_args.log_level)
         logging.basicConfig(level=self.session.logLevel)
         log.debug('logging initialized %s', cfg_args.log_level)
-        if cfg_args.verbose:
-            self.session.verbosity += cfg_args.verbose
-        self.session.startDir = cfg_args.start_dir
         if cfg_args.top_level_directory:
             self.session.topLevelDir = cfg_args.top_level_directory
         self.session.loadConfigFiles(*self.findConfigFiles(cfg_args))
-        self.session.setStartDir()
+        # set verbosity from config + opts
+        self.session.setVerbosity(
+        
--- Kel's top 3
  [1] Goal: Enable configurable logging verbosity for plugin output
      Ability to set logging verbosity
<!--
1. Please check if an issue already exists. This feature may have already been requested
2. Check out and follow our Guidelines: https://github.com/serverless/serverless-azure-functions/CONTRIBUTING.md
3. Fill out the whole template so we have a good overview
      Way: Add configurable logging via a shared LoggingService
      - Search the codebase for direct calls to `serverless.cli.log` and note each location (plugins and services). Verify the existing `LoggingService` implementation and its API.
      - In azureBasePlugin.ts import LoggingService and LogLevel, add a protected loggingService property, instantiate it in the constructor, replace the old log(message) method with a wrapper calling loggingService.log(message, level?), and add error, warn, info, debug methods that delegate to loggingService.
      - Update every plugin and service that previously called `this.serverless.cli.log` (e.g., AzureApimPlugin, AzureDeployPlugin, AzureLoginPlugin, ArmService) to use the new `this.log` wrapper (or the specific level methods) instead of the direct CLI call.
      - Add a README section describing the `--verbose` (`-v`) flag and the supported log levels (error, warn, info, debug).
      - Fix the Jest configuration to correctly exclude test files by changing the pattern from `!/src/test/` to `!src/test/*.ts`.
      - Run the full test suite and confirm that all logging‑related tests pass, especially those checking default, warn, error, and debug levels and the handling of an empty string option value.
  [2] Goal: Allow disabling the MSBuild verbosity flag in the CMake build helper
      Cmake build helper + Intel Fortran
This is similar to #2747, but there isn't as elegant a workaround, so I thought it was worth opening an issue. 

Intel fortran doesn't understand the verbosity command, and it causes the build to fail. 
The workaround I came up with is this:



        cmake
      Way: allow disabling msbuild verbosity flag in cmake wrapper
      - Inspect the CMake wrapper class initialization where `msbuild_verbosity` is processed, checking why passing an empty or falsy value still falls back to default verbosity.
      - Change the default value of `msbuild_verbosity` in `CMake.__init__` from `None` to `'minimal'`, and update the attribute assignment so it only falls back to the environment variable without forcing a `'minimal'` fallback when an explicit empty value is passed.
      - Run the CMake unit tests covering msbuild verbosity to confirm passing an empty string or None can disable or customize the verbosity flag.
  [3] Goal: Restore verbose command line options to configure logging levels properly
      --verbose option no longer working
Previously `-v` logged at INFO level, `-vv` at debug, but now this has stopped working
      Way: support predicate filtering and label enrichment in cross-ontology relational diffs
      - Identify the CLI subcommand handling diffs across mappings and the underlying pairwise relational diff generator function.
      - Add command-line options for predicate filtering and label populating (`--add-labels`), validate that `--source` provides multiple entries if passed, and convert predicate arguments into a normalized list.
      - Implement a helper utility `add_labels_to_object` to look up labels by CURIE using an ontology interface and set the corresponding attributes on diff objects.
      - Update `calculate_pairwise_relational_diff` to accept `predicates` and `add_labels` parameters, filter left-side subject and parent entities by source prefix and predicate, propagate predicates down to edge calculation, and attach labels when requested.
      - Run the cross-ontology relational diff test suite to ensure predicate filtering and source restrictions work properly.

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
--- Kel's top 3
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
  [2] Goal: Respect custom S3 endpoints for presigned URLs
      fix_s3_hosts always assumes you're using central aws instead of a local S3.
I've noticed when attempting to generate presigned urls that the logic behind fix_s3_hosts and the virtual styling always assumes you're using AWS S3. [fix_s3_host](https://github.com/boto/botocore/blob/6065566237de957507fc6
      Way: Bypass S3 host normalization when custom endpoint URL is provided
      - Inspect client creation logic where endpoint URLs and request handlers (such as 'before-sign.s3' hooks) are configured.
      - Add a helper method on the client creator to unregister the 'fix_s3_host' handler from the event emitter when a custom endpoint_url is specified.
      - Call the unregister helper during client argument preparation before the endpoint is created.
      - Run unit and functional tests checking that custom S3 endpoint URLs remain unmutated.
  [3] Goal: Fix S3 file listing when file names share a folder prefix
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
--- Kel's top 3
  [1] Goal: Recognize type annotation imports inside TYPE_CHECKING blocks to prevent unused import errors
      Typing.TYPE_CHECKING imports raise F401
```
$ pip install  flake8

```

```
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from my_module.test_class import MyClass

object: 'MyClass' = None
```
Flake 8 gives the following error in this scenario:

`my_module\test_class.py:4:5: F
      Way: Parse and defer string forward type annotations in AST checker
      - Inspect AST checker handling of type annotations on function definitions and variable annotations (`AnnAssign`), noting that string forward annotations (`ast.Str`) are treated as plain literals rather than referenced symbols.
      - Add a new message class `ForwardAnnotationSyntaxError` in messages module for reporting invalid syntax within string type annotations.
      - Implement a `handleAnnotation` method on the checker that checks if an annotation is an `ast.Str`, parses the string using `ast.parse` inside a deferred function, copies AST locations, reports `ForwardAnnotationSyntaxError` on syntax errors or invalid expression bodies, and visits the parsed expression tree via `handleNode`.
      - Update function argument annotation processing and `AnnAssign` handling in the checker to route annotations through `handleAnnotation` instead of direct `handleNode`.
      - Run test suite to verify string forward annotations resolve imported symbols properly without raising unused import warnings or crashing.
  [2] Goal: Handle non-name exception expressions when checking for re-raised exceptions
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
  [3] Goal: Fix compatibility with modern linter select options when emitting plugin warnings
      `should_warn` is incompatible with flake8 (next)
minimal reproduction (trigger any `B9*` warning with the default settings)

```console
$ rm -rf venv && virtualenv -qq venv && venv/bin/pip install -qq git+https://github.com/pycqa/flake8 git+https://github.com/pycqa/flake8-bugbear && venv/bin/flak
      Way: safely check options attributes that can be NoneType
      - Locate warning emission checks in the linter plugin where runner options like `select` and `extend_select` are inspected.
      - Reproduce the `TypeError: argument of type 'NoneType' is not iterable` by passing `options` where `select` or `extend_select` is `None`.
      - Add truthiness checks before testing membership in `options.select` and `options.extend_select` to avoid iterating over `None`.
      - Run the test suite to confirm warning filtering succeeds when options are `None`.

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
--- Kel's top 3
  [1] Goal: Add custom color support to spatter augmentation
      Цвет тумана у Spatter()-а
Хорошо бы иметь возможность задавать цвет "тумана" https://github.com/albumentations-team/albumentations/blob/master/albumentations/augmentations/transforms.py#L2482 .
      Way: Add custom RGB color support to image transform augmentations
      - Inspect the transform class (such as Spatter) and identify hardcoded RGB color arrays in the corruption application logic.
      - Extend `__init__` with a `color` parameter accepting a 3-element sequence, a mode-to-color dict, or None for defaults.
      - Add validation in `__init__` ensuring RGB sequences have length 3, multi-mode inputs use dicts containing keys for all specified modes, and invalid mode values are rejected.
      - Normalize `self.color` to a dictionary mapping each mode to its RGB values, scale RGB values by 1/255 in `get_params_dependent_on_targets`, and replace hardcoded color constants.
      - Include `color` in `get_transform_init_args_names` for serialization support.
      - Run the transform validation tests to verify that invalid color definitions raise expected ValueError exceptions.
  [2] Goal: Add support for hdr-CIELab and hdr-IPT color spaces
      Implement support for "hdr-CIELab" and "hdr-IPT" (2011) colourspaces.
References
---
- Fairchild, M. D., & Chen, P. (2011). Brightness , Lightness , and Specifying Color in High-Dynamic-Range Scenes and Images. doi:10.1117/12.872075
- #347 
- #321
      Way: implementing Fairchild 2011 HDR color spaces and lightness models
      - Examine existing lightness, luminance, hdr-CIELab, and hdr-IPT implementations (Fairchild 2010 models) to understand current Michaelis-Menten formulation, function signatures, and dispatch dictionaries.
      - Add lightness_Fairchild2011 and luminance_Fairchild2011 functions supporting both 'hdr-CIELAB' (maximum perception 247) and 'hdr-IPT' (maximum perception 246) parameterisations with default epsilon 0.710, update the default epsilon of Fairchild 2010 functions to 1.836, and register the new functions in LIGHTNESS_METHODS and LUMINANCE_METHODS dictionaries and exports.
      - Update hdr-CIELab and hdr-IPT color space forward and reverse transforms to accept a method parameter (e.g., 'Fairchild 2011' vs 'Fairchild 2010') using the appropriate exponents and lightness/luminance formulations.
      - Expose the new functions and methods in the package init files and update module __all__ declarations.
      - Run test suite covering Fairchild 2011 lightness, luminance, hdr-CIELab, and hdr-IPT transforms including n-dimensional and NaN handling tests.
  [3] Goal: Add background color support to RGB ANSI escape sequence formatting
      RGB background color support

hi!

Adding a switch to  `rgb256(r,g,b, bg=True)` you could return

```
if bg:
    return sequence('m', fields=3)(48, 5, colour)
else:
    return sequence('m', fields=3)(38, 5, colour)
```
      Way: Add background color option to ANSI 256-color sequence formatting
      - Locate the ANSI RGB color generation function (e.g., `rgb256`) that maps RGB triplets to ANSI escape sequences.
      - Add an optional boolean parameter (e.g., `bg=False`) to the function signature.
      - Update the ANSI escape code sequence generator to select 48 for background color when `bg` is True, and 38 for foreground color when `bg` is False.
      - Run the test suite to verify background and foreground ANSI sequence generation.

=== TASK zulip__python-zulip-api-633 | repo zulip/python-zulip-api | python
--- Issue
Allow passing config via environment variables to zulip-botserver
**Component:** `zulip-botserver`
**Package:** https://pypi.org/project/zulip-botserver/
**Code:** https://github.com/zulip/python-zulip-api/tree/master/zulip_botserver

Instead of requiring that config be passed as a file with the `--config-file` option, it would be great if we could pass config via environment variables.
This would make our secure key storage system much easier, as right now we use env variables to pass down secure keys to containers, and creating a config file to store them on disk is suboptimal.

```ini
[helloworld]
email=foo-bot@hostname
key=dOHHlyqgpt5g0tVuVl6NHxDLlc9eFRX4
site=http://hostname
token=aQVQmSd6j6IHphJ9m1jhgHdbnhl5ZcsY
```

Becomes:

```bash
env BOT_EMAIL=foo-bot@hostname BOT_KEY=abc... BOT_SITE=https://hostname BOT_TOKEN=abc... zulip-botserver
```

I would require adding ~20 LOC to https://github.com/zulip/python-zulip-api/blob/master/zulip_botserver/zulip_botserver/server.py, if this proposal sounds reasonable, I can probably submit a PR for it myself without too much difficulty.
--- Real fix (for your judgement only)
diff --git a/zulip_botserver/README.md b/zulip_botserver/README.md
index 5efbbeaf..c16e0b1e 100644
--- a/zulip_botserver/README.md
+++ b/zulip_botserver/README.md
@@ -1,16 +1,29 @@
 ```
 zulip-botserver --config-file <path to botserverrc> --hostname <address> --port <port>
-
 ```
 
 Example: `zulip-botserver --config-file ~/botserverrc`
 
 This program loads the bot configurations from the
-config file (botserverrc here) and loads the bot modules.
+config file (`botserverrc`, here) and loads the bot modules.
 It then starts the server and fetches the requests to the
 above loaded modules and returns the success/failure result.
 
-Please make sure you have a current botserverrc file with the
-configurations of the required bots.
-Hostname and Port are optional arguments. Default hostname is
-127.0.0.1 and default port is 5002.
+The `--hostname` and `--port` arguments are optional, and default to
+127.0.0.1 and 5002 respectively.
+
+The format for a configuration file is:
+
+    [helloworld]
+    key=value
+    email=helloworld-bot@zulip.com
+    site=http://localhost
+    token=abcd1234
+
+Is passed `--use-env-vars` instead of `--config-file`, the
+configuration can instead be provided via the `ZULIP_BOTSERVER_CONFIG`
+environment variable.  This should be a JSON-formatted dictionary of
+bot names to dictionary of their configuration; for example:
+
+    ZULIP_BOTSERVER_CONFIG='{"helloworld":{"email":"helloworld-bot@zulip.com","key":"value","site":"http://localhost","token":"abcd1234"}}' \
+      zulip-botserver --use-env-vars
diff --git a/zulip_botserver/zulip_botserver/input_parameters.py b/zulip_botserver/zulip_botserver/input_parameters.py
index 2d6a2fe0..1b7d585a 100644
--- a/zulip_botserver/zulip_botserver/input_parameters.py
+++ b/zulip_botserver/zulip_botserver/input_parameters.py
@@ -7,13 +7,19 @@ def parse_args() -> argparse.Namespace:
     '''
 
     parser = argparse.ArgumentParser(usage=usage)
-    parser.add_argument(
+    mutually_exclusive_args = pars
--- Kel's top 3
  [1] Goal: Support environment variable interpolation in configuration files
      Allow usage of env vars in config
The configuration should be parsed for environment variables when loaded. This would allow for secrets like api keys to be kept outside of the opsdroid configuration.

#### Example

```yaml
connectors:
  - name: slack
    default-room: '#general'
    bot-nam
      Way: Interpolate environment variables into YAML configuration files
      - Understand the requirement to replace placeholders like `$ENV_VAR` in YAML configuration files with the actual values of environment variables.
      - Identify the module responsible for loading and parsing the application's configuration, as this is where the environment variable interpolation logic needs to be added.
      - Implement a custom YAML implicit resolver and constructor to handle environment variable interpolation. The resolver should use a regular expression to identify strings matching the pattern `^\$([A-Z_]*)$`, and the constructor should extract the variable name and fetch its value from `os.environ`.
      - Run the specific test case designed to validate environment variable interpolation in configuration files.
  [2] Goal: Support passing and expanding environment variables in container configuration and aliases
      Using environment variables in .scuba.yml or passing them 
Is it already possible – or do you consider it a valuable addition – to use environment variables in the `.scuba.yml` file?

For one, these could be already present in the shell where `scuba` is run. Additionally, I think some variables ad
      Way: support environment variables in configuration and aliases
      - Locate configuration schema and parsing logic where top-level nodes and alias definitions are validated and loaded.
      - Update utility parsing for environment strings (such as 'KEY=VALUE' or 'KEY') so that an unset environment variable falls back to an empty string instead of None.
      - Add an environment parser function in the configuration module to accept either a mapping of key/values or a list of 'KEY=VALUE' strings, resolving missing values via host os.getenv and stringifying values.
      - Add 'environment' to allowed configuration nodes in the config class, store parsed top-level environment variables, and update alias objects to parse and store alias-level environment configurations.
      - In command processing, copy top-level environment variables into the execution context and merge alias-specific environment definitions over top-level variables.
      - Update the main entrypoint/runner setup to populate container environment variables from context environment mappings.
  [3] Goal: Support environment variable substitution in YAML configuration files
      implement env variables inside PYGEOAPI_CONFIG file
**Is your feature request related to a problem? Please describe.**

devop systems (docker, k8) use enviroment variable to set parameters like database connections of  set passwords inside containers. 

For example running pygeopai in openshift 
      Way: Support environment variable substitution in YAML configuration files
      - Locate the YAML loading utility function in the codebase.
      - Implement a helper function `get_typed_value` to convert string values parsed from environment variables into their native Python types (int, float, or string).
      - Define a custom PyYAML loader class inheriting from `yaml.SafeLoader` and register a regex pattern matcher and constructor to detect and substitute `${ENV_VAR}` placeholders.
      - Update the YAML loading function to use the custom loader class.
      - Verify the implementation by running the configuration and utility tests.

=== TASK iodevs__pyresult-49 | repo iodevs/pyresult | python
--- Issue
Operator `and_then` should returns error if passed function raise exception
* PyResult version: 0.8.0
* Python version: ANY
* Operating System: ANY

### Description

When function in `and_then` operator raise excpetion, then `and_then` function fail with exception, instead error result.

### What I Did

```python
from pyresult import and_then, ok

def raise_exception(val):
    raise Exception()

and_then(raise_exception, ok(1))

---------------------------------------------------------------------------
Exception                                 Traceback (most recent call last)
<ipython-input-46-f0756160f235> in <module>()
----> 1 and_then(raise_exception, ok(1))

/usr/local/opt/pyenv/versions/3.6.2/envs/pydecoder/lib/python3.6/site-packages/toolz/functoolz.py in __call__(self, *args, **kwargs)
    281     def __call__(self, *args, **kwargs):
    282         try:
--> 283             return self._partial(*args, **kwargs)
    284         except TypeError as exc:
    285             if self._should_curry(args, kwargs, exc):

/usr/local/opt/pyenv/versions/3.6.2/envs/pydecoder/lib/python3.6/site-packages/pyresult/operators.py in and_then(func, res)
     40     '''
     41     res = result(res)
---> 42     return result(func(res.value)) if is_ok(res) else res
     43
     44

<ipython-input-43-6bead73ba782> in raise_exception(val)
      1 def raise_exception(val):
----> 2     raise Exception()
```

Look at iodevs/pydecoder#32
--- Real fix (for your judgement only)
diff --git a/pyresult/operators.py b/pyresult/operators.py
index f8dd187..e045449 100644
--- a/pyresult/operators.py
+++ b/pyresult/operators.py
@@ -39,8 +39,11 @@ def and_then(func, res):
 
     and_then: (a -> Result b x) -> Result a x -> Result b x
     '''
-    res = result(res)
-    return result(func(res.value)) if is_ok(res) else res
+    try:
+        res = result(res)
+        return result(func(res.value)) if is_ok(res) else res
+    except Exception as e:
+        return error(str(e))
 
 
 @curry

--- Kel's top 3
  [1] Goal: Support passing functions with arguments to exception assertion matchers
      Assertion Errors
Love the library. It is super useful! 

I am trying to figure out how to see if a function raises an exception when called with a certain set of parameters. 

Here is an example (a bit contrived as I am trying to raise my own Exception based on valid inputs). 

```python
t = 
      Way: allow callable objects in exception assertion operators by checking callability
      - Locate exception assertion operator implementations in the repository.
      - Reproduce the issue by passing callable objects (such as bound built-in methods, partials, or custom callables) to the exception assertion operator.
      - Replace type checks like `inspect.isfunction` or `inspect.ismethod` with the built-in `callable()` check on the subject.
      - Run test suite to verify exception assertion matchers accept callables.
  [2] Goal: Allow reraising custom exceptions with multiple initialization arguments
      raise_ fails to reraise exception which requires arguments
When the `future.utils.raise_` function is used with all its three parameters, it is in some cases *not* able to reraise exceptions which need arguments for their initialization. For example, this code:

```python
import sys

from futur
      Way: support re-raising multi-argument exceptions and instance values
      - Examine the 3-argument raise helper and 2to3 raise fixer handling `(type, value, traceback)` semantics.
      - Update the raise helper on Python 3 to inspect whether `value` is already an instance of `tp` (use `value` directly), a tuple (unpack as `tp(*value)`), `None` (instantiate `tp()`), or another object (call `tp(value)`). Ensure `tp` subclass checks and instance checks reject invalid combinations.
      - Update the raise fixer AST transform to handle 3-argument syntax `raise exc, val, tb`, tuple/literal arguments, None values, and defer complex dynamic values to `future.utils.raise_`.
      - Run the fixer and raise utility test suites to verify reraising custom exceptions with multiple initialization arguments.
  [3] Goal: Prevent invalid replacement of exception information with bare raise statements
      exception stack trace is replaced by a bare `raise`
With python 3.7.4, the formatted exception stack trace will be replaced by a bare `raise`, and then make a syntax error.

But the strange thing is that this occurs only when the statement is executed in the pre-commit hook of git, the replacement
      Way: Fix operator precedence in AST node condition matching
      - Locate where calls like six.reraise or sys.exc_info() are checked for transformation in the AST visitor.
      - Reproduce the issue by testing an unrelated function call using *sys.exc_info() (e.g., traceback.format_exc(*sys.exc_info())) to verify it incorrectly matches the transformation rule.
      - Wrap the disjunctive conditions checking star arguments in parentheses so that `and` binds over `(not _starargs(node) or self._is_star_sys_exc_info(node))` rather than treating `self._is_star_sys_exc_info(node)` as an independent top-level `or` branch.
      - Run the regression test suite to ensure expressions unpacking sys.exc_info into other functions are not rewritten into bare raises.

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
 
 

--- Kel's top 3
  [1] Goal: Make client retry after connection reset
      Does not retry on connection reset
__Steps to reproduce:__
List the minimal actions needed to reproduce the behavior.

1. Don't start the influx server, so the connection for writing data is refused
2. Configure the Client to use retries
3. Try to write data
4. Turn on the influx server before
      Way: Enable client retry for connection errors
      - Locate the client's write retry logic, specifically where errors are evaluated to determine if a retry should occur. Identify the conditions under which a write operation is currently retried.
      - Reproduce the connection reset scenario by attempting to write data to a server that is initially unavailable, then making the server available within the client's retry period.
      - Modify the retry condition to include errors that do not have an HTTP status code (e.g., connection errors). This typically means checking for a status code of 0 or a specific error type that signifies a network-level issue rather than an HTTP response error.
      - Verify that the client now successfully retries and writes data when the server becomes available after an initial connection error.
  [2] Goal: Log retry attempts at warn level instead of error level
      Warn-level logging on retries attempts
Current implementation of (r *Request) Execute(method, url string) (*Response, error) uses Backoff function with hardcoded attempts errors log on Error level:

`r.client.log.Errorf("%v, Attempt %v", err, r.Attempt)`

With enabled retry feature it may be unn
      Way: support unescaped raw URL path parameters
      - Identify where path parameter storage and URL composition logic are defined on HTTP client and request structs.
      - Add RawPathParams map fields to Client and Request types alongside existing PathParams.
      - Implement SetRawPathParam and SetRawPathParams methods on Client and Request to store parameters intended to bypass URL escaping.
      - Ensure raw path parameter replacements are performed without calling url.PathEscape when building the target request URL.
      - Run the test suite to ensure existing path param parsing, auth, and retry tests pass alongside the new parameter options.
  [3] Goal: Ensure flush waits for write failure retries to complete
      Flush doesn't perform retries of write failures
The default write retry logic is documented to retry 5 times before failing.

It seems this may not in fact be the case.

__Steps to reproduce:__
List the minimal actions needed to reproduce the behaviour.

I have created a test showing what I b
      Way: Ensure Flush waits for write failure retries
      - Identify the `Flush` method in the write API that is not waiting for write failure retries to complete, leading to data loss.
      - Locate the underlying service responsible for managing write operations and retry queues.
      - Add a call to a new `Flush` method on the internal service from the `WriteAPI.Flush()` implementation. This ensures that after flushing the immediate buffer, the retry queue is also processed.
      - Implement the new `Flush` method in the internal service. This method must iterate through the retry queue, pop each batch, and attempt to write it immediately without re-adding it to the retry queue if the write fails again during this flush operation. Batches that have expired should be discarded.
      - Verify that `Flush` now correctly processes batches in the retry queue and waits for their completion, preventing data loss.

=== TASK jtesta__ssh-audit-44 | repo jtesta/ssh-audit | python
--- Issue
RuntimeError when auditing SSH-Server
```
❯ python3.8 ssh-audit.py xx.xxx.xxx.xxx -p 22022 --json
Traceback (most recent call last):
  File "ssh-audit.py", line 3103, in <module>
    main()
  File "ssh-audit.py", line 3099, in main
    audit(conf)
  File "ssh-audit.py", line 3088, in audit
    print(json.dumps(build_struct(banner, kex=kex, client_host=s.client_host), sort_keys=True))
  File "ssh-audit.py", line 2996, in build_struct
    for host_key_type in host_keys.keys():
RuntimeError: dictionary keys changed during iteration
```

This is reproducible for one of my old, inherited SSH Servers, but not for the new one, which I configured after your guide.

I can provide you the address in private if you need it to debug.

Anyway, the problem is pretty obvious, and the exception says it clearly. It is no good idea to change an Iterable while iterating over it.

The fix is trivial - just wrap `host_keys.keys()` from above in a `list()`.

The above line is not covered by a test, though, as all the JSON generation. So, this is good chance to raise test coverage.

I'll try to find some time and create a PR for this issue.
--- Real fix (for your judgement only)
diff --git a/ssh-audit.py b/ssh-audit.py
index a5702ab..c51241f 100755
--- a/ssh-audit.py
+++ b/ssh-audit.py
@@ -3366,7 +3366,7 @@ def build_struct(banner: Optional['SSH.Banner'], kex: Optional['SSH2.Kex'] = Non
         host_keys = kex.host_keys()
 
         # Normalize all RSA key types to 'ssh-rsa'.  Otherwise, due to Python's order-indifference dictionary types, we would iterate key types in unpredictable orders, which interferes with the docker testing framework (i.e.: tests would fail because elements are reported out of order, even though the output is semantically the same).
-        for host_key_type in host_keys.keys():
+        for host_key_type in list(host_keys.keys())[:]:
             if host_key_type in SSH2.HostKeyTest.RSA_FAMILY:
                 val = host_keys[host_key_type]
                 del host_keys[host_key_type]

--- Kel's top 3
  [1] Goal: Prevent KeyError when resolving T object paths in glom
      KeyError in _t_child from weakref
I'm running into a weird issue using glom in PySpark on Databricks.

This expression:

`glom(ping, (T[stub]["values"].values(), sum), default=0)`

(where `stub` is `"a11y_time"`)

is consistently throwing this exception when I run it on my real data:

```
      Way: enable pickling of weakref-tracked T objects by implementing state serialization
      - Locate the definition of the `T` object's underlying class, `_TType`, and the global weakref dictionary `_T_PATHS` tracking its paths.
      - Reproduce the issue by attempting to pickle and unpickle a `T` object expression (e.g., `T['a']['b']`), which fails because the weakref dictionary `_T_PATHS` does not survive serialization/deserialization across processes.
      - Implement `__getstate__` and `__setstate__` on the `_TType` class to serialize its path from `_T_PATHS` and restore it back into `_T_PATHS` upon deserialization.
      - Run the test suite to verify that `T` objects can now be successfully pickled and unpickled without raising a `KeyError`.
  [2] Goal: Prevent dictionary changed size error during dictionary iteration
      RuntimeError: dictionary changed size during iteration
https://github.com/nithinmurali/pygsheets/blob/0e2468fe9a1957b19f1cebe3667c601c32942bbd/pygsheets/authorization.py#L94

You can't delete keys from a dictionary you are currently iterating.
      Way: Avoid modifying iterated dictionary by using explicit keyword argument fallback
      - Locate the function handling deprecated keyword arguments and modifying the local variables dictionary or kwargs directly.
      - Replace the dynamic modification of `vars()` and deletion of keys from `kwargs` during iteration with explicit `kwargs.get()` fallbacks for each deprecated parameter.
      - Run the authorization test suite to verify that deprecated keyword arguments are correctly mapped and do not raise a RuntimeError.
  [3] Goal: Fix KeyError when formatting protocol struct string representations with missing fields
      KeyError: 'error_code' at kafka/protocol/struct.py __repr__
kafka-python==1.3.1, kafka 0.10.1.0
I've set consumer's session_timeout_ms bigger than group.max.session.timeout.ms in the broker and used group coordination. Here is my consumer setup:
```python
SECOND = 1000
consumer = kafka.KafkaCons
      Way: Ensure Struct field defaults and validate unexpected keyword arguments
      - Identify where protocol Struct instances initialize their attributes from schema names and kwargs.
      - Update Struct.__init__ to iterate over all schema field names, setting each in self.__dict__ with a default value of None when missing from kwargs, and pop matching entries from kwargs.
      - Raise a ValueError if any unexpected keyword arguments remain in kwargs after exhausting schema fields.
      - Check coordinator request dispatch routines to ensure target nodes are ready before sending requests to prevent failures when node connections are not ready.
      - Run the protocol struct tests to verify that missing fields default properly without causing KeyError in __repr__ and invalid kwargs raise a ValueError.

=== TASK labd__commercetools-python-sdk-71 | repo labd/commercetools-python-sdk | python
--- Issue
BUG broken auth missing token
commercetools==5.0.0

got valid creds (works with curl)
creds are present

always dies `*** oauthlib.oauth2.rfc6749.errors.MissingTokenError: (missing_token) Missing access token parameter.`

seems related to https://github.com/requests/requests-oauthlib/issues/324

same happens with 4.1.0 so i believe its the interface to the oauthlib that has changed
--- Real fix (for your judgement only)
diff --git a/CHANGES b/CHANGES
index 8c2726a..3ab1120 100644
--- a/CHANGES
+++ b/CHANGES
@@ -1,3 +1,7 @@
+5.0.1 (2019-11-18)
+------------------
+ - Allow passing in the base auth URL when creating a Client
+
 5.0.0 (2019-11-05)
 ------------------
 This is breaking change since the commercetools api specification is moving
diff --git a/README.rst b/README.rst
index 99c3176..ea2c4d3 100644
--- a/README.rst
+++ b/README.rst
@@ -42,7 +42,7 @@ Example
         client_secret="<your-client-secret>",
         scope=["<scopes>"],
         url="https://api.sphere.io",
-        token_url="https://auth.sphere.io/oauth/token",
+        token_url="https://auth.sphere.io",
     )
 
     product = client.products.get_by_id("00633d11-c5bb-434e-b132-73f7e130b4e3")
diff --git a/src/commercetools/client.py b/src/commercetools/client.py
index 0765893..971323f 100644
--- a/src/commercetools/client.py
+++ b/src/commercetools/client.py
@@ -39,7 +39,7 @@ from commercetools.services.stores import StoreService
 from commercetools.services.subscriptions import SubscriptionService
 from commercetools.services.tax_categories import TaxCategoryService
 from commercetools.services.types import TypeService
-from commercetools.utils import BaseTokenSaver, DefaultTokenSaver
+from commercetools.utils import BaseTokenSaver, DefaultTokenSaver, fix_token_url
 
 
 class RefreshingOAuth2Session(OAuth2Session):
@@ -112,6 +112,7 @@ class Client:
         del project_key, client_id, client_secret, url, token_url, scope
 
         self._config = self._read_env_vars(config)
+        self._config["token_url"] = fix_token_url(self._config["token_url"])
         self._token_saver = token_saver or DefaultTokenSaver()
         self._url = self._config["url"]
         self._base_url = f"{self._config['url']}/{self._config['project_key']}/"
@@ -282,16 +283,6 @@ class Client:
         if not config.get("token_url"):
             config["token_url"] = os.environ.get("CTP_AUTH_URL")
 
-            # When the token_url 
--- Kel's top 3
  [1] Goal: Add support for OAuth access tokens in the API client
      Add support for OAuth access tokens
By default, OAuth access tokens are not accepted in this package, while other packages do have support (e.g. mollie-api-php). 

I took a look there and found that there's little difference between access_token and API Key calls. The only difference is an additio
      Way: Add support for OAuth access tokens in API client
      - Locate the API client class and identify where API keys are validated and stored.
      - Implement a `validate_access_token` static method that strips whitespace and checks if the token starts with 'access_', raising a setup error if it does not.
      - Add a `set_access_token` method to the client that validates the token and assigns it to the internal authentication attribute.
      - Deprecate passing the API key directly to the client's constructor by raising a warning, advising users to use the setter methods instead.
      - Verify the changes by running the client tests, ensuring both API keys and access tokens are validated correctly and deprecation warnings are triggered.
  [2] Goal: Save access token during OIDC hybrid flow authorization
      OIDC request_type "code token" does not save access token
I was experimenting with `django-oauth-toolkit` and noticed when setting a request type of `code token` the returned access token was not valid because it was not saved into the database. Looking into the issue I believe that during the hybri
      Way: Save access tokens generated by modifiers during authorization code grant flows
      - Locate the authorization code grant class and find where the authorization code is created and modified.
      - Add a check after the code modifiers run to see if an 'access_token' is present in the grant dictionary, and if so, call `self.request_validator.save_token(grant, request)`.
      - Run the test suite to verify that the access token is successfully saved during hybrid flow authorization.
  [3] Goal: Fix OAuth2 client automatic token refresh parameter handling
      Auto token-refresh not working any more
**Description of Bug**
The login flow that's supposed to do an auto-refresh of the token loaded from disk doesn't work any more. I traced it to the parameter passing in `client_from_access_functions` to the `authlib` functions. `authlib`'s `OAuth2Client` expe
      Way: Update OAuth2 client refresh configuration to match Authlib token endpoint parameter
      - Locate the OAuth2 client session initialization in the auth module where token refresh parameters are constructed.
      - Check the authentication tests constructing clients from access functions and token files to verify expected session kwargs.
      - Replace requests-oauthlib legacy parameters `auto_refresh_url` and `auto_refresh_kwargs` with Authlib's `token_endpoint` keyword argument in `session_kwargs`.
      - Run the auth test suite to ensure the client is properly instantiated with the updated token endpoint parameter.

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

--- Kel's top 3
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
  [2] Goal: Prevent `isort` from adding blank lines at file start
      Might create blank line at start of file with comments
Running `isort --diff` with the following will add a blank line at the start:
```python
# comment
import os
# comment2
import sys
```

```diff
@@ -1,4 +1,5 @@
+
+import os
 # comment
-import os
 # comment2
 import sys
```

The 
      Way: Preserve file start boundaries when collecting comments preceding imports
      - Identify where leading comments preceding an import statement are popped from output lines into a comment dictionary.
      - Reproduce the issue by formatting a code snippet starting with a comment followed by imports where a blank line is erroneously prepended.
      - Inspect the loop that consumes preceding comments from `out_lines` and check why it continues popping or re-evaluating lines past the initial file header/comment block boundary.
      - Update the comment consumption loop condition to prevent inspecting or consuming past `_first_comment_index_end` when checking the updated trailing line of `out_lines`.
      - Run the targeted test suite to ensure no unexpected blank lines are added at the start of files containing leading comments.
  [3] Goal: Preserve right-alignment when upgrading percent-formatted string spec ifiers with width
      Behaviour change after upgrading '%9s' to '{:9}'
This pyupgrade change (https://github.com/hugovk/yamllint/commit/25e8e9be0838bff930b3b204360fc24385b6edbf) fails indentation-related unit tests which previously passed:

```diff
-            output += '%9s %s\n' % (token_type,
+            output 
      Way: Preserving string right alignment when converting percent formatting to format
      - Locate the function responsible for transforming printf-style percent format strings to str.format format strings.
      - Identify where specifiers like width, conversion flags, and conversion types ('s', '%', etc.) are translated into format spec components.
      - Update the conversion logic to set the alignment flag to '>' when a width is specified for string conversion ('s') and no explicit conversion flag is present.
      - Run pytest on the test suite to verify '%9s' formats to '{:>9}' rather than '{:9}'.

=== TASK pydantic__pydantic-2056 | repo pydantic/pydantic | python
--- Issue
Pattern type serialisation error
### Checks

* [X] I added a descriptive title to this issue
* [X] I have searched (google, github) for similar issues and couldn't find anything
* [X] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and still think this is a bug

<!-- Sorry to sound so draconian, but every second saved replying to issues is time spend improving pydantic :-) -->

# Bug

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
             pydantic version: 1.7
            pydantic compiled: True
                 install path: /root/.local/lib/python3.8/site-packages/pydantic
               python version: 3.8.6 (default, Oct 13 2020, 20:49:19)  [GCC 8.3.0]
                     platform: Linux-3.10.0-1127.19.1.el7.x86_64-x86_64-with-glibc2.2.5
     optional deps. installed: ['email-validator']
```
<!-- or if you're using pydantic prior to v1.3, manually include: OS, python version and pydantic version -->

<!-- Please read the [docs](https://pydantic-docs.helpmanual.io/) and search through issues to
confirm your bug hasn't already been reported. -->

<!-- Where possible please include a self-contained code snippet describing your bug: -->

```py
from typing import Pattern
from pydantic import BaseModel

class Model(BaseModel):
    filter: Pattern

Model(filter="^regex$").json()
```

This produce an error : 
TypeError: Object of type 'Pattern' is not JSON serializable

--- Real fix (for your judgement only)
diff --git a/pydantic/json.py b/pydantic/json.py
--- a/pydantic/json.py
+++ b/pydantic/json.py
@@ -5,7 +5,7 @@
 from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address, IPv6Interface, IPv6Network
 from pathlib import Path
 from types import GeneratorType
-from typing import Any, Callable, Dict, Type, Union
+from typing import Any, Callable, Dict, Pattern, Type, Union
 from uuid import UUID
 
 from .color import Color
@@ -54,6 +54,9 @@ def pydantic_encoder(obj: Any) -> Any:
     elif is_dataclass(obj):
         return asdict(obj)
 
+    if isinstance(obj, Pattern):
+        return obj.pattern
+
     # Check the class type and its superclasses for a matching encoder
     for base in obj.__class__.__mro__[:-1]:
         try:

--- Kel's top 3
  [1] Goal: Improve serialization type checking exception messages for invalid dataclass field values
      Not informative exception
Hi @wyfo ,

First of all, the project is impressive! We get a boost of 25% compared to well-tuned Pydantic serialization, which was like the following one when all non-BaseModel nested attributes were on the shoulders of orjson's SIMD.:

```
def default(obj: Any) -> An
      Way: Propagate specific type errors during serialization
      - Locate the serialization logic responsible for type checking and error handling when `check_type=True` is enabled.
      - Understand how the `wrapper` method handles exceptions during serialization of an object's internal structure.
      - Remove the exception suppression block within the `SerializationMethodVisitor.wrapper` method to allow specific type errors from internal serialization logic to propagate.
      - Refactor type checking utility calls from `inspect.isclass` to `apischema.typing.is_type` in relevant modules.
      - Run the provided integration test to confirm that the fix correctly propagates informative type errors.
  [2] Goal: Add regex pattern validation support to Pydantic generator
      Add pattern/regexp validation for pydanticgen
pydantic supports regexes/patterns:

https://docs.pydantic.dev/latest/concepts/fields/#string-constraints

We should generate this in pydanticgen

Should this be optional, to avoid potentially expensive checks at time of object generation?

This 
      Way: Add regex pattern validation support to Pydantic generator
      - Locate the Pydantic generator code where class fields and their constraints (like Field arguments) are generated.
      - Identify how pattern/regex constraints are defined in the LinkML schema and map them to Pydantic's Field constraints (e.g., using `pattern` for Pydantic v2 and `regex` for Pydantic v1).
      - Modify the Pydantic generator templates and field generation logic to append the pattern constraint to the generated `Field(...)` definition if a pattern is specified on the slot.
      - Remove the Pydantic generator exclusion from the pattern compliance tests to enable validation testing.
      - Run the Pydantic generator tests to verify that regex patterns are correctly generated and validated.
  [3] Goal: Add explicit type annotations to module-level function aliases for type checkers
      Published types aren't complete enough for Pyright/Pylance
It seems a bit unfair to file this as a "bug," when really what's going on is that the Python community is trying to figure out what a "typed" Python library looks like. In this case, what looks like perfectly reasonable code is, in another 
      Way: annotate top level module function aliases with callable types for type checkers
      - Locate module-level function aliases assigned directly from instance methods.
      - Reproduce type checker ambiguity by checking types on exported function aliases or running pyright.
      - Add explicit Callable type annotations or forward typed helper declarations for exported function aliases so type checkers infer parameter and return types correctly.
      - Run project test suite including option/validation tests to verify logic and typing consistency.