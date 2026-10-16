

=== TASK pynamodb__PynamoDB-864 | repo pynamodb/PynamoDB | python
--- Issue
Polymorphism?
Can dynamodb and pynamodb do polymorphism?
I have searched can't find any example

class BaseModel(Model):
  class meta:
    table_name = "BaseTable"
    ...
  id = UnicodeAttribute(hash_key=True)
  type = UnicodeAttribute(range_key=True)

class Child1Model(BaseModel):
  id = UnicodeAttribute(hash_key=True)
  type = UnicodeAttribute(range_key=True)
  child1data.....

class Child2Model(BaseModel):
  id = UnicodeAttribute(hash_key=True)
  type = UnicodeAttribute(range_key=True)
  child2data....

I tried it wouldn't let me do this. even if I added class meta, i am not sure how it would work.
any idea? thanks.
--- Real fix (for your judgement only)
diff --git a/docs/polymorphism.rst b/docs/polymorphism.rst
index 6a341d7..9d29b96 100644
--- a/docs/polymorphism.rst
+++ b/docs/polymorphism.rst
@@ -6,7 +6,6 @@ Polymorphism
 PynamoDB supports polymorphism through the use of discriminators.
 
 A discriminator is a value that is written to DynamoDB that identifies the python class being stored.
-(Note: currently discriminators are only supported on MapAttribute subclasses; support for model subclasses coming soon.)
 
 Discriminator Attributes
 ^^^^^^^^^^^^^^^^^^^^^^^^
@@ -49,3 +48,32 @@ A class can also be registered manually:
     Discriminator values are written to DynamoDB.
     Changing the value after items have been saved to the database can result in deserialization failures.
     In order to read items with an old discriminator value, the old value must be manually registered.
+
+
+Model Discriminators
+^^^^^^^^^^^^^^^^^^^^
+
+Model classes also support polymorphism through the use of discriminators.
+(Note: currently discriminator attributes cannot be used as the hash or range key of a table.)
+
+.. code-block:: python
+
+    class ParentModel(Model):
+        class Meta:
+            table_name = 'polymorphic_table'
+        id = UnicodeAttribute(hash_key=True)
+        cls = DiscriminatorAttribute()
+
+    class FooModel(ParentModel, discriminator='Foo'):
+        foo = UnicodeAttribute()
+
+    class BarModel(ParentModel, discriminator='Bar'):
+        bar = UnicodeAttribute()
+
+    BarModel(id='Hello', bar='World!').serialize()
+    # {'id': {'S': 'Hello'}, 'cls': {'S': 'Bar'}, 'bar': {'S': 'World!'}}
+.. note::
+
+    Read operations that are performed on a class that has a discriminator value are slightly modified to ensure that only instances of the class are returned.
+    Query and scan operations transparently add a filter condition to ensure that only items with a matching discriminator value are returned.
+    Get and batch get operations will raise a ``ValueError`` if the returned item(s) are n
--- Kel's top 3
  [1] Goal: Allow model subclasses to inherit the Meta class from parent models
      Inheriting from Model - should this work?
Hi,

Should it be possible to sub-class a pynamodb Model?  I'm getting the following error:

```
AttributeError: As of v1.0 PynamoDB Models require a `Meta` class with a table_name property.
                See https://pynamodb.readthedocs.io/en/latest/relea
      Way: allow model subclasses to inherit meta class from parent models
      - Locate metaclass logic responsible for processing model attributes and the inner Meta class during class creation.
      - Reproduce failure when subclassing a model without defining a duplicate Meta inner class.
      - Remove default Meta override on missing Meta class so subclasses inherit parent Meta classes, and adjust key/TTL attribute validation to account for inherited class attributes.
      - Run test suite to verify model subclassing works and key attribute constraints are strictly enforced across inheritance hierarchy.
  [2] Goal: Populate polymorphic JsonSubTypes regardless of schema name casing
      Polymorphic generation creates empty JsonSubtypes
If your schema casing isn't consistent with the class name casing, the JsonSubtypes value of polymorphic discriminator objects is empty.

e.g., given this (where only the case of `polymorphicEnumDiscriminator` is modified from the example in the pr
      Way: Normalise schema names to model class names when resolving polymorphic subtypes
      - Identify where Jackson polymorphic annotations, specifically @JsonSubTypes, are generated for discriminator models.
      - Examine how subtype schemas referencing the discriminator model via allOf are matched against the target discriminator model name.
      - Update the subtype filter check in JacksonModelGenerator to convert the reference schema name to model class casing using `toModelClassName()` before comparing it against `modelName`.
      - Run the generator test suite to verify models with non-standard schema casing properly populate JsonSubTypes.
  [3] Goal: Support model inheritance with distinct table names per child class
      Support for inheritance between models
Right now it is not possible to have a model `Foo` inherit from model `Bar`, i.e.:

```python
class Bar(Model):
    class Meta:
        table_name = 'bar_table'
...

class Foo(Bar):
    class Meta:
        table_name = 'foo_table'
...
```

Apparen
      Way: Ensure inherited ORM models reinitialize table connections when table names differ
      - Locate where connection instances are created and stored on model class attributes.
      - Reproduce the issue by creating a parent model with a specific table name and a child subclass overriding Meta.table_name, verifying connection sharing across subclasses.
      - Update model connection initialization to check if the existing connection's table name matches Meta.table_name, creating a new connection instance if they differ.
      - Remove redundant instance variable initializations from TableConnection __init__ if unneeded.
      - Run test suite to verify model inheritance with distinct table names works as expected.

=== TASK sphinx-gallery__sphinx-gallery-511 | repo sphinx-gallery/sphinx-gallery | python
--- Issue
ENH: remove API crossrefs from hover text
Currently if the first sentence of an example/tutorial/whatever contains an API cross-ref, the hover text can end up looking like this:

![Screenshot_2019-05-28_11-33-48](https://user-images.githubusercontent.com/1810515/58506751-989da580-813c-11e9-915d-f827ce715a82.png)

Enhancement would be to add a regex to the hover text generator that would strip out the cross-reference-generating text and leave just the class/function/method/attribute name. Ideally would be smart enough to behave the same way the cross-refs do w/r/t the presence of the leading `~`.
--- Real fix (for your judgement only)
diff --git a/sphinx_gallery/backreferences.py b/sphinx_gallery/backreferences.py
index ab1b204..4c44d49 100644
--- a/sphinx_gallery/backreferences.py
+++ b/sphinx_gallery/backreferences.py
@@ -196,7 +196,7 @@ BACKREF_THUMBNAIL_TEMPLATE = THUMBNAIL_TEMPLATE + """
 
 def _thumbnail_div(target_dir, src_dir, fname, snippet, is_backref=False,
                    check=True):
-    """Generates RST to place a thumbnail in a gallery"""
+    """Generate RST to place a thumbnail in a gallery."""
     thumb, _ = _find_image_ext(
         os.path.join(target_dir, 'images', 'thumb',
                      'sphx_glr_%s_thumb.png' % fname[:-3]))
diff --git a/sphinx_gallery/gen_rst.py b/sphinx_gallery/gen_rst.py
index 0bf420a..45991d4 100644
--- a/sphinx_gallery/gen_rst.py
+++ b/sphinx_gallery/gen_rst.py
@@ -196,9 +196,31 @@ def codestr2rst(codestr, lang='python', lineno=None):
     return code_directive + indented_block
 
 
-def extract_intro_and_title(filename, docstring):
-    """ Extract the first paragraph of module-level docstring. max:95 char"""
+def _regroup(x):
+    x = x.groups()
+    return x[0] + x[1].split('.')[-1] + x[2]
+
+
+def _sanitize_rst(string):
+    """Use regex to remove at least some sphinx directives."""
+    # :class:`a.b.c <thing here>`, :ref:`abc <thing here>` --> thing here
+    p, e = r'(\s|^):[^:\s]+:`', r'`(\W|$)'
+    string = re.sub(p + r'\S+\s*<([^>`]+)>' + e, r'\1\2\3', string)
+    # :class:`~a.b.c` --> c
+    string = re.sub(p + r'~([^`]+)' + e, _regroup, string)
+    # :class:`a.b.c` --> a.b.c
+    string = re.sub(p + r'([^`]+)' + e, r'\1\2\3', string)
+
+    # ``whatever thing`` --> whatever thing
+    p = r'(\s|^)`'
+    string = re.sub(p + r'`([^`]+)`' + e, r'\1\2\3', string)
+    # `whatever thing` --> whatever thing
+    string = re.sub(p + r'([^`]+)' + e, r'\1\2\3', string)
+    return string
 
+
+def extract_intro_and_title(filename, docstring):
+    """Extract and clean the first paragraph of module-level docstring."""
     # lstrip is 
--- Kel's top 3
  [1] Goal: Make linter allow crossorigin property on link tags
      no-unknown-property does not allow crossorigin on link tags
In the latest release, there was a [fix for the crossorigin property](https://github.com/yannickcr/eslint-plugin-react/pull/1643) (related to https://github.com/yannickcr/eslint-plugin-react/issues/1642). The updated rule only allows the `c
      Way: Allow crossorigin attribute on link tags in linter
      - Understand that the linter's `no-unknown-property` rule incorrectly flags `crossorigin` on `link` tags, despite it being a valid HTML attribute for them.
      - Locate the `no-unknown-property` rule definition and identify the `DOM_ATTRIBUTE_NAMES` and `ATTRIBUTE_TAGS_MAP` constants, which control attribute validation.
      - Correct the mapping in `DOM_ATTRIBUTE_NAMES` from `crossOrigin: 'crossorigin'` to `crossorigin: 'crossOrigin'` to accurately reflect the HTML attribute name to React property name conversion.
      - Add 'link' to the list of allowed tags for the `crossOrigin` attribute within the `ATTRIBUTE_TAGS_MAP` constant.
      - Run the linter tests, specifically ensuring that a `link` tag with `crossorigin` no longer triggers an error.
  [2] Goal: Improve error handling when none is combined with other text styles
      Improve error message when value `none` is in a list of text styles
When you provide an illegal value for the CSS rule `link-style`, you get a helpful message displaying all legal values, saying that `text-style` expects a space-separated list of those.

If you set `text-style: bold italic reverse
      Way: Catch and clarify style parse errors when mixing exclusive style flags
      - Locate CSS property descriptor handlers for style flag attributes (such as text-style and link-style) and the help text generator for style flags.
      - Update the help text generator for style flag properties to document that 'none' cannot be mixed with other flags.
      - Wrap style parsing in a try/except block catching StyleSyntaxError; if 'none' is present alongside other flag tokens, raise StyleValueError with a specific message and generated help text.
      - Add test cases ensuring properties like text_style and link_style raise StyleValueError with appropriate help text when 'none' is mixed with other style values.
      - Run the style property tests to verify the improved error message when 'none' is mixed with other flags.
  [3] Goal: Unescape regex fragments consistently when reversing URLs
      reverse_url() unescapes regex inconsistently
If you have a rule with an escaped regex character coming after a group in parentheses, like:
```python
URLSpec('/project/([0-9]+)/export/codebook\\.csv', ExportCodebookCsv)
```
it will correctly match `/project/1/export/codebook.csv`, but will revers
      Way: unescape regex characters across all URL reverse path fragments
      - Inspect the URL path matcher's reverse route generator to see how regex pattern fragments are parsed and unescaped after capture groups.
      - Add test cases for reversing URL pattern regexes that contain escaped characters (such as '\.') after capture groups.
      - Wrap the post-parenthesis slice of regex fragments with re_unescape inside a try-except block, returning (None, None) if unescaping raises ValueError, matching the error handling of un-grouped fragments.
      - Run the URL reversing test suite to ensure routes with escaped characters after capture groups reverse properly without literal backslashes.

=== TASK iterative__dvc-5005 | repo iterative/dvc | python
--- Issue
dvc 1.10.2 does not provide the extra 'webdavs'
## Bug Report
I wanted to test the webdav protocol. The command `dvc push -r webdavremote` (where webdavremote is the name of my remote). That gave me the useful output:
```
ERROR: failed to push data to the cloud - URL '***snipped***' is supported but requires these missing dependencies: ['webdavclient3']. If you have installed dvc using pip, choose one of these options to proceed: 

    1) Install specific missing dependencies:
        pip install webdavclient3
    2) Install dvc package that includes those missing dependencies: 
        pip install 'dvc[webdavs]'
    3) Install dvc package with all possible dependencies included: 
        pip install 'dvc[all]'

If you have installed dvc from a binary package and you are still seeing this message, please report it to us using https://github.com/iterative/dvc/issues. Thank you!
```
But the installation of the webdav extension using pip3 leads to the following first lines:
```
pip3 install 'dvc[webdavs]'
Requirement already satisfied: dvc[webdavs] in ${HOME}/.local/lib/python3.7/site-packages (1.10.2)
  dvc 1.10.2 does not provide the extra 'webdavs'
Requirement already satisfied: dictdiffer>=0.8.1 in ${HOME}/.local/lib/python3.7/site-packages (from dvc[webdavs]) (0.8.1)
Requirement already satisfied: toml>=0.10.1 in ${HOME}/.local/lib/python3.7/site-packages (from dvc[webdavs]) (0.10.2)
```
Hence the installation did nothing. I am a bit puzzled, why there
--- Real fix (for your judgement only)
diff --git a/dvc/tree/base.py b/dvc/tree/base.py
--- a/dvc/tree/base.py
+++ b/dvc/tree/base.py
@@ -70,7 +70,7 @@ def __init__(self, repo, config):
         self.repo = repo
         self.config = config
 
-        self._check_requires(config)
+        self._check_requires()
 
         shared = config.get("shared")
         self._file_mode, self._dir_mode = self.SHARED_MODE_MAP[shared]
@@ -107,31 +107,44 @@ def get_missing_deps(cls):
 
         return missing
 
-    def _check_requires(self, config):
+    def _check_requires(self):
+        from ..scheme import Schemes
+        from ..utils import format_link
+        from ..utils.pkg import PKG
+
         missing = self.get_missing_deps()
         if not missing:
             return
 
-        url = config.get("url", f"{self.scheme}://")
-        msg = (
-            "URL '{}' is supported but requires these missing "
-            "dependencies: {}. If you have installed dvc using pip, "
-            "choose one of these options to proceed: \n"
-            "\n"
-            "    1) Install specific missing dependencies:\n"
-            "        pip install {}\n"
-            "    2) Install dvc package that includes those missing "
-            "dependencies: \n"
-            "        pip install 'dvc[{}]'\n"
-            "    3) Install dvc package with all possible "
-            "dependencies included: \n"
-            "        pip install 'dvc[all]'\n"
-            "\n"
-            "If you have installed dvc from a binary package and you "
-            "are still seeing this message, please report it to us "
-            "using https://github.com/iterative/dvc/issues. Thank you!"
-        ).format(url, missing, " ".join(missing), self.scheme)
-        raise RemoteMissingDepsError(msg)
+        url = self.config.get("url", f"{self.scheme}://")
+
+        scheme = self.scheme
+        if scheme == Schemes.WEBDAVS:
+            scheme = Schemes.WEBDAV
+
+        by_pkg = {
+            "pip": f"pip install 'dvc[{
--- Kel's top 3
  [1] Goal: Prevent pushing directory objects when local cache is incomplete
      dvc push: when not all files are in local cache, still pushes the hash.dir file, breaking remote repository.
EDIT: Solved. The issue was pushing incomplete datasets from my gpu server to the new storage remotes. It pushed all the files present in the local cache (which is what I wanted) but then it 
      Way: skip directory manifest upload when local cache missing component files
      - Locate cache status analysis and push/upload plan generation logic for cache directories.
      - Track files marked as missing locally during status plan calculation in cache transfer logic.
      - Check if any files within a directory object manifest are present in the missing files list before uploading the directory manifest.
      - Skip uploading the directory manifest (.dir object) if any child file in that directory manifest is missing locally.
      - Run unit test suite to ensure directory push behavior skips missing directory files and all tests pass.
  [2] Goal: Handle unsupported ioctl errors when creating file reflinks on Linux
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
  [3] Goal: Support pickle protocol 5 on Python versions earlier than 3.8
      push: Unsupported pickle protocol: 5 on python 3.7.11
# Bug Report

## Description

When attempting to push to remote (Google Cloud Storage) using DVC version `2.10.2` and Python version `3.7.11` I encounter the following error:

```console
$ dvc push     
ERROR: unexpected error - unsupport
      Way: override diskcache disk_pickle_protocol and wrap cache access to handle pickle exceptions gracefully
      - Locate diskcache Cache usage across the project and identify where pickle protocol mismatches occur (e.g., Python version differences default to higher pickle protocols like protocol 5).
      - Create a wrapper around diskcache.Cache and diskcache.Disk that defaults 'disk_pickle_protocol' to a broadly compatible level (such as 4) and translates pickle deserialization/protocol errors into a distinct custom exception.
      - Replace direct diskcache.Cache and diskcache.Index imports with the wrapped Cache class in state and index modules.
      - Add exception handling in the CLI entry point to catch the custom disk error and display user-friendly instructions on clearing corrupted cache files.
      - Run unit tests verifying pickle protocol compatibility and error handling for invalid or higher-protocol cache files.

=== TASK raimon49__pip-licenses-154 | repo raimon49/pip-licenses | python
--- Issue
Expose maintainers
Some packages do not have the `author` field set, but the `maintainer` one (example: https://github.com/opencv/opencv-python/blob/b776e5ce9f164600a1bd05178a058272122bf02a/setup.py#L272). Exposing the maintainer as well would allow users to work with the maintainer value if the author value would be empty otherwise.
--- Real fix (for your judgement only)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 2e60480..1c71659 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,5 +1,13 @@
 ## CHANGELOG
 
+### 4.2.0
+
+* Implement new option `--with-maintainers`
+* Implement new option `--python`
+* Allow version spec in `--ignore-packages` parameters
+* When the `Author` field is `UNKNOWN`, the output is automatically completed from `Author-email`
+* When the `home-page` field is `UNKNOWN`, the output is automatically completed from `Project-URL`
+
 ### 4.1.0
 
 * Support case-insensitive license name matching around `--fail-on` and `--allow-only` parameters
diff --git a/README.md b/README.md
index 7517b50..76855da 100644
--- a/README.md
+++ b/README.md
@@ -30,6 +30,7 @@ Dump the software license list of Python packages installed with pip.
     * [Format options](#format-options)
         * [Option: with\-system](#option-with-system)
         * [Option: with\-authors](#option-with-authors)
+        * [Option: with\-maintainers](#option-with-maintainers)
         * [Option: with\-urls](#option-with-urls)
         * [Option: with\-description](#option-with-description)
         * [Option: with\-license\-file](#option-with-license-file)
@@ -429,6 +430,12 @@ When executed with the `--with-authors` option, output with author of the packag
  pytz    2017.3   MIT      Stuart Bishop
 ```
 
+#### Option: with-maintainers
+
+When executed with the `--with-maintainers` option, output with maintainer of the package.
+
+**Note:** This option is available for users who want information about the maintainer as well as the author. See [#144](https://github.com/raimon49/pip-licenses/issues/144)
+
 #### Option: with-urls
 
 For packages without Metadata, the license is output as `UNKNOWN`. To get more package information, use the `--with-urls` option.
diff --git a/piplicenses.py b/piplicenses.py
index 488c338..90da0b7 100644
--- a/piplicenses.py
+++ b/piplicenses.py
@@ -49,13 +49,14 @@ from prettytable import NONE as RULE_NONE
 from prettytable
--- Kel's top 3
  [1] Goal: Keep author and maintainer fields adjacent in metadata ordering
      author and maintainer should be kept beside each other
found via https://github.com/pfmoore/editables/pull/2
      Way: Group related metadata key ordering definitions
      - Locate the configuration or schema defining the canonical ordering of package metadata fields.
      - Identify missing related metadata keys such as `maintainer` and `maintainer_email` that should be grouped with `author` and `author_email`.
      - Update the metadata section ordering list to include `maintainer` and `maintainer_email` immediately after `author` and `author_email`.
      - Update test cases that verify metadata key ordering to assert that maintainer fields are sorted adjacent to author fields.
      - Run the test suite to confirm ordering changes pass.
  [2] Goal: Allow optional authors and description fields when creating projects
      Allow optional PEP 621 fields (authors, description)
**Describe the bug**
Currently, `authors` and `description` are expected to be present in the root `pyproject.toml`, and the contents from the fields are fetched when creating new projects with `poly create project`.

According to PEP 621, thes
      Way: Conditionally render optional TOML fields in template generation
      - Inspect project creation templates and the code populating template fields (such as description and authors).
      - Identify how empty or missing values produce invalid TOML syntax when templates use hardcoded key-value formats like `authors = {authors}`.
      - Update template string definitions so that `{description}` and `{authors}` represent the entire key-value line rather than just the value.
      - In the project creation function, format the full line strings for `description` and `authors` only when values exist, passing an empty string otherwise.
      - Run the project template tests covering both standard templates and cases with missing optional fields.
  [3] Goal: Support optional description field in provisioner definitions and listings
      [feature request] Add a `description` field for the provisioners
To improve how PEs document their provisioners and expose them to their Devs, it would be better to have a `description` field in the provisioners files.

Today, if we add this field in the provisioners files, we get this error:
```
Er
      Way: Add optional description support to provisioner definitions and listings
      - Locate the provisioner struct used when unmarshalling provisioner YAML files (in the score-go library) and verify that it lacks a Description field, causing the unmarshal error.
      - Upgrade the score-go dependency to version v1.9.5, which introduces an optional Description field on the Provisioner struct.
      - Add a `description` key to each default provisioner definition in the internal/command/default.provisioners.yaml file, matching the new optional field.
      - Update test fixtures (e.g., provisioners.custom.golden) to include the new description lines so that golden‑file comparisons reflect the added field.
      - Run the full test suite, especially the provisioner display tests, to ensure the description column is rendered in table and JSON output and that no unmarshalling errors occur.

=== TASK riyasyash__CompetitiveProgramming-27 | repo riyasyash/CompetitiveProgramming | python
--- Issue
Column Name to Number
Come out with an algorithm for getting the column number provided the column name in an excel sheet and vice versa. Excel has a naming convention of A, B..Z, AA, AB, AC...ZZ,AAA... This had to be converted to the column numbers. A will be 1 and AA will 27... Also the algorithm to find the name provided column number. 
--- Real fix (for your judgement only)
diff --git a/NumberSystems/ExcelConverter.py b/NumberSystems/ExcelConverter.py
new file mode 100644
index 0000000..27c14d7
--- /dev/null
+++ b/NumberSystems/ExcelConverter.py
@@ -0,0 +1,22 @@
+class ExcelConverter:
+    column_start_ascii = 65
+    column = None
+    column_number = None
+    column_literal_base = 26
+
+    def value(self, column_literal):
+        return (ord(column_literal) - self.column_start_ascii) + 1
+
+    def is_valid_column(self):
+        return self.column.isalpha()
+
+    def to_capital(self):
+        self.column = self.column.upper()
+
+    def to_column_number(self):
+        if self.is_valid_column():
+            self.to_capital()
+            self.column = self.column[::-1]
+            self.column_number = 0
+            for i, literal in enumerate(self.column):
+                self.column_number += pow(self.column_literal_base, i) * self.value(literal)
diff --git a/NumberSystems/__init__.py b/NumberSystems/__init__.py
new file mode 100644
index 0000000..e69de29
diff --git a/Readme.md b/Readme.md
index 2e17a97..725a66f 100644
--- a/Readme.md
+++ b/Readme.md
@@ -16,3 +16,6 @@ This repo contains the implementations of different data structures and solution
 #### [Queue](Queue)
 * [Implementation of Queue](Queue/Queue.py)
 * [Stack using Queue](Queue/stack_with_queue.py)
+
+#### [Number Systems](NumberSystems)
+* [Convert Excel column names to numbers](NumberSystems/ExcelConverter.py)

--- Kel's top 3
  [1] Goal: Allow column letters when fetching column values from a worksheet
      call col_values via letters identifying the column
When I need to append values in columns where there may be blank values, for example:
```
row 1
row 2
row 3


row 6
```
It is necessary to collect the column values to calculate which first row will be empty to append the new values:
```

      Way: Adding column letter to index conversion utility
      - Identify where cell coordinate conversions and exceptions are defined across the codebase.
      - Define a new custom exception InvalidInputValue inheriting from the base library exception.
      - Implement a helper function column_letter_to_index that parses a column letter into a 1-based integer index using unbounded A1 notation parsing, validating against invalid characters and unbounded results and raising InvalidInputValue on failure.
      - Export the new exception in documentation and public module interfaces as needed.
      - Run the unit tests covering valid single and multi-letter column labels as well as invalid inputs.
  [2] Goal: Add method to convert cell objects to A1 notation
      Cell class should support conversion to A1 notation
Currently, there is no straightforward way to fetch data in `A1` notation from `Cell` object (of course one can use `utils` package explicitly). It will be a great to have this feature. 
Let's say you have the following steps to be performed:
1. 
      Way: expose A1 notation address property on spreadsheet cell objects
      - Inspect the Cell model to locate its row and column coordinates and identify existing coordinate-to-A1 conversion utilities.
      - Add an `address` property on the Cell class that delegates conversion of its `row` and `col` attributes using `rowcol_to_a1`.
      - Run the test suite verifying that accessing the `address` property on a Cell returns its corresponding A1 notation string.
  [3] Goal: Allow passing custom column names when loading Excel sheets
      Passing column_names to from_xls fails with TypeError
```
Traceback (most recent call last):
  File "<stdin>", line 5, in <module>
  File "/Users/jani.mikkonen/src/customers/vr/vr/venv/lib/python3.7/site-packages/agateexcel/table_xls.py", line 86, in from_xls
    tables[sheet.name] = agate.Table
      Way: Extract and pass explicit column names when forwarding kwargs
      - Inspect the functions loading sheet data into tables to see how `column_names` and `**kwargs` are passed into the downstream constructor.
      - Create or run a test passing custom `column_names` via `kwargs` when `header=False` to reproduce the `TypeError: got multiple values for argument 'column_names'`.
      - In XLS sheet loader logic, check if `column_names` exists in `kwargs`; if `header` is False, assign `column_names` from `kwargs`, then delete `'column_names'` from `kwargs` before instantiating the table.
      - In XLSX sheet loader logic, check if `column_names` exists in `kwargs`; if `header` is False, assign `column_names` from `kwargs`, then delete `'column_names'` from `kwargs` before instantiating the table.
      - Run the test suite to ensure custom column names can be passed without TypeError.

=== TASK geopandas__geopandas-2088 | repo geopandas/geopandas | python
--- Issue
BUG: GeoDataFrames with MultiIndex as columns do not support CRS
- [x] I have checked that this issue has not already been reported.

- [x] I have confirmed this bug exists on the latest version of geopandas.

- [x] (optional) I have confirmed this bug exists on the master branch of geopandas.

---

#### Code Sample

```python
>>> import pandas as pd
>>> import geopandas
>>> df = pd.DataFrame([[1, 0], [0, 1]], columns=[['location', 'location'], ['x', 'y']])
>>> gdf = geopandas.GeoDataFrame(df, geometry=geopandas.points_from_xy(df['location', 'x'], df['location', 'y']))
>>> print(gdf.crs)
None
>>> gdf = geopandas.GeoDataFrame(df, crs="EPSG:4326", geometry=geopandas.points_from_xy(df['location', 'x'], df['location', 'y']))
>>> print(gdf.crs)
None
>>> gdf = geopandas.GeoDataFrame(df, geometry=geopandas.points_from_xy(df['location', 'x'], df['location', 'y'], crs="EPSG:4326"))
>>> print(gdf.crs)
None
>>> gdf = geopandas.GeoDataFrame(df, crs="EPSG:4326", geometry=geopandas.points_from_xy(df['location', 'x'], df['location', 'y'], crs="EPSG:4326"))
>>> print(gdf.crs)
None
>>> gdf = gdf.set_crs("EPSG:4326")
>>> print(gdf.crs)
None
```

#### Problem description

The first print-statement works as expected because I never specified a CRS, but the following 4 methods of setting the CRS should work. They seem to work for `pd.Index`, but not `pd.MultiIndex`. Since many `geopandas` operations like `sjoin` require a matching CRS, this causes problems for oth
--- Real fix (for your judgement only)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 90e028b..a2ea82c 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -40,6 +40,7 @@ Bug fixes:
   Pyogrio if provided a URL as input (#2796)
 - Fix `copy()` downcasting GeoDataFrames without an active geometry column to a
   DataFrame (#2775)
+- Fix geometry column name propagation when GeoDataFrame columns are a multiindex (#2088)
 
 Notes on (optional) dependencies:
 
diff --git a/geopandas/geodataframe.py b/geopandas/geodataframe.py
index c8d80d4..83d7216 100644
--- a/geopandas/geodataframe.py
+++ b/geopandas/geodataframe.py
@@ -1444,6 +1444,18 @@ individually so that features may have different properties
         return a GeoDataFrame.
         """
         result = super().__getitem__(key)
+        # Custom logic to avoid waiting for pandas GH51895
+        # result is not geometry dtype for multi-indexes
+        if (
+            pd.api.types.is_scalar(key)
+            and key == ""
+            and isinstance(self.columns, pd.MultiIndex)
+            and isinstance(result, Series)
+            and not is_geometry_type(result)
+        ):
+            loc = self.columns.get_loc(key)
+            # squeeze stops multilevel columns from returning a gdf
+            result = self.iloc[:, loc].squeeze(axis="columns")
         geo_col = self._geometry_column_name
         if isinstance(result, Series) and isinstance(result.dtype, GeometryDtype):
             result.__class__ = GeoSeries
@@ -1452,8 +1464,6 @@ individually so that features may have different properties
                 result.__class__ = GeoDataFrame
                 if geo_col in result:
                     result._geometry_column_name = geo_col
-                else:
-                    result._geometry_column_name = None
             else:
                 result.__class__ = DataFrame
         return result

--- Kel's top 3
  [1] Goal: Raise an error when concatenating spatial dataframes with mismatched CRS
      BUG: pd.concat(axis=0) can override crs information
- [x] I have checked that this issue has not already been reported.

- [x] I have confirmed this bug exists on the latest version of geopandas.

- [x] (optional) I have confirmed this bug exists on the master branch of geopandas.

---
Splitt
      Way: raise error when concatenating spatial arrays with mismatched CRS
      - Locate the array concatenation logic for spatial geometries, typically in the custom ExtensionArray implementation.
      - Modify the concatenation method of the geometry array to determine a common CRS instead of blindly using the CRS of the first element.
      - Implement a helper function that checks the CRS of all arrays being concatenated: if multiple distinct non-null CRSs are present, raise a ValueError; if only one non-null CRS is present alongside null CRSs, issue a warning and use the non-null CRS; otherwise, return None.
      - Run the test suite to verify that concatenating objects with mismatched CRSs raises a ValueError and that mismatched null/non-null CRSs issue a warning.
  [2] Goal: Preserve CRS metadata when shifting a GeoSeries
      BUG: CRS lost after GeoSeries.shift()
- [x] I have checked that this issue has not already been reported.

- [x] I have confirmed this bug exists on the latest version of geopandas.

- [ ] (optional) I have confirmed this bug exists on the master branch of geopandas.

---

**Note**: Please r
      Way: preserve metadata on custom pandas ExtensionArray operations
      - Identify methods on ExtensionArray where CRS or custom metadata is dropped when delegating to pandas operations.
      - Reproduce metadata loss by calling shift() and factorize() / unique() on a GeoSeries or GeometryArray with a defined CRS and asserting the returned object retains the CRS.
      - Override shift(periods, fill_value) on GeometryArray to invoke super().shift(...) and set shifted.crs = self.crs before returning.
      - Update GeometryArray._from_factorized to pass crs=original.crs when reconstructing geometries via from_wkb.
      - Run the array tests to verify that shift and factorize operations preserve CRS without warnings.
  [3] Goal: Allow creating a GeoDataFrame with a crs column and external geometry
      BUG: "truth value of a Series is ambiguous" error raised when there is a column named 'crs': episode 2
- [x] I have checked that this issue has not already been reported.

- [x] I have confirmed this bug exists on the latest version of geopandas.

- [ ] (optional) I have confirmed this bug exists on
      Way: avoid ambiguous truth value when column names shadow attribute names
      - Reproduce the ambiguous truth value error when initializing a GeoDataFrame with a 'crs' column and separate geometry parameter.
      - Locate where `__setitem__` inspects the instance's `crs` attribute to determine coordinate reference system propagation.
      - Change the attribute access in `GeoDataFrame.__setitem__` to check `if "crs" in self._attrs:` or check `getattr(self, "_crs", None)` directly rather than checking `if "crs" in self:` or standard `getattr` which resolves to the 'crs' column Series.
      - Run the test suite verifying that a GeoDataFrame containing a column named 'crs' initializes properly with an external geometry list or Series.

=== TASK tomerfiliba__plumbum-366 | repo tomerfiliba/plumbum | python
--- Issue
SyntaxError in plumbum/cli/image.py
image.py contains the line

import . as cli

All versions of python I tried (3.6, 3.5, 2.7) raise SyntaxError reading this line. According to the Python Language Reference, such syntax of the import statement is not allowed. What exactly is the purpose of this line? And how to achieve this purpose in legal python?
--- Real fix (for your judgement only)
diff --git a/plumbum/cli/image.py b/plumbum/cli/image.py
index b207fa3..502af80 100644
--- a/plumbum/cli/image.py
+++ b/plumbum/cli/image.py
@@ -2,7 +2,7 @@ from __future__ import print_function, division
 
 from plumbum import colors
 from .termsize import get_terminal_size
-import . as cli
+from .. import cli
 import sys
 
 class Image(object):

--- Kel's top 3
  [1] Goal: Support relative imports without whitespace between dots and the import keyword
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
  [2] Goal: Allow parsing import.meta as an expression
      using `parse_file_as_expr` to parse `import.meta` will throw `SyntaxError::ImportMetaInScript`
### Describe the bug

![image](https://user-images.githubusercontent.com/49502170/211561872-2d9d83b7-7cf5-4304-b70b-5413a3527e66.png)

![image](https://user-images.githubusercontent.com/49502170/21156243
      Way: Allow parsing import.meta as an expression
      - Understand that `import.meta` is a module-specific syntax that causes `SyntaxError::ImportMetaInScript` when parsed in a non-module context by `parse_file_as_expr`.
      - Locate the definition of `parse_file_as_expr` within `crates/swc_ecma_parser/src/lib.rs`.
      - Modify the `parse_file_as_expr` function's internal parser context to indicate that the input can be treated as a module. Specifically, set `p.input().ctx.can_be_module` to `true` before calling `p.parse_expr()`.
      - Run the provided tests to ensure the fix works and doesn't introduce regressions.
  [3] Goal: Fix syntax error when fixing module level imports followed by function definitions
      SyntaxError rewriting specific combination of imports and defs
---

## Python Code
```python
def setup():
    pass

from a import b
from b import c
from b import d
```

## Command Line and Configuration

Command Line
```shell
$ autopep8 -i t.py
Traceback (most recent call last):
  
      Way: handle multiline from imports safely when checking future imports for top level reordering
      - Locate module-level import detection function and reproduce the SyntaxError when parsing incomplete multiline `from` import lines.
      - Implement incremental multi-line line assembly for `from` import checking in a helper function `has_future_import` that handles `SyntaxError` while reading lines from the source stream.
      - Advance the source iteration stream appropriately when a multiline `from` import is encountered so trailing definition or non-import lines are not concatenated improperly.
      - Run the test suite to verify multiline `from` imports and `__future__` imports are processed without SyntaxError.

=== TASK BraeWebb__allocate-10 | repo BraeWebb/allocate | python
--- Issue
Compositional Allocations
Allow for composition allocations by exporting an updated availability sheet based on an allocation run.

This would allow for a regular allocation to be generated for normal weeks and then an additional 'overflow' allocation to be generated when more tutors are required.

Example usage:
```bash
allocate --update-availability tutors.csv sessions.csv allocation.csv > new_allocation.csv
```
--- Real fix (for your judgement only)
diff --git a/README.md b/README.md
index 551f73a..1fddaa2 100644
--- a/README.md
+++ b/README.md
@@ -50,7 +50,27 @@ The typical command run, assuming `tutors.csv`, `sessions.csv` and `availability
 allocate tutors.csv sessions.csv availability.csv
 ```
 
-## Examples
+The `--stub` flag generates template `tutors.csv` and `sessions.csv` files from an availability spreadsheet.
+*Note* Ensure that the tutors.csv and sessions.csv files are empty first.
+```bash
+allocate --stub tutors.csv sessions.csv availability.csv
+```
+
+The `--doodle` flag tells the tool to parse the availability CSV file as if it is an export from doodle.
+```bash
+allocate --doodle tutors.csv sessions.csv doodle_export.csv
+```
+
+The `--update-availability` flag causes the output to be an availability CSV with the allocations removed from each tutors availability.
+An example usage might look like the below, in this case we first get the allocations and place them in `allocations.csv`
+We then update the availability and run availability again and place it in `extras.csv`
+```bash
+allocate tutors.csv sessions.csv availability.csv > allocations.csv
+allocate --update-availability tutors.csv sessions.csv availability.csv > updated_availability.csv
+allocate tutors.csv sessions.csv updated_availability > extras.csv
+```
+
+## Example 1
 The following tables represent the 3 example CSV files that come with the project.
 
 ### tutors.csv
@@ -96,6 +116,45 @@ Brae,T02,T04,P01,P03,P04,P05
 Emily,T03,P02,P04
 ```
 
+
+## Example 2
+This is an example of how to generate an allocation from doodle along with an extras allocation for weeks with extra tutors.
+
+Starting with a CSV file exported from doodle like the one below, named `doodle.csv`
+| "Poll ""Sample Doodle Availability""" |   |   |   |  |  |  |  |  |  |
+|---|---|---|---|---|---|---|---|---|---|
+| https://doodle\.com/poll/v7scx5pzwgeu3m2y |   |   |   |  |  |  |  |  |  |
+|   |   |   |   |  |  |  |  |  |  |
+|   | February 2020 |   |   |  |  |
--- Kel's top 3
  [1] Goal: Prevent multiple nested Compose and Sequential transforms from executing inside OneOf
      `A.Compose`, `A.Sequential` components run multiple times inside of `A.OneOf`
## 🐛 Bug

Hi, I used `A.OneOf` method on my `A.Compose` objects,
but when I run entire pipeline, all components of `A.Compose` objects are called altogether.


## To Reproduce

Steps to reproduce the behavior:


      Way: Normalize single transform into sequence in composition wrappers
      - Inspect `BaseCompose` and its derived composition classes (`OneOf`, `SomeOf`, `OneOrOther`) to see how single transforms or nested compositions passed as arguments are handled.
      - In `BaseCompose.__init__`, check if `transforms` is an instance of a single transform or composition (`BaseCompose` or `BasicTransform`). If so, emit a warning and wrap `transforms` into a single-element list `[transforms]` before assigning to `self.transforms`.
      - Update subclass initializers (`OneOf`, `SomeOf`, `OneOrOther`) to calculate probabilities and validate lengths using `self.transforms` instead of the raw `transforms` parameter, ensuring any wrapping performed by `super().__init__` is reflected.
      - Run the test suite verifying single transform instances passed directly into composition classes.
  [2] Goal: Safely allocate file descriptors to prevent exhaustion
      File descriptors are not allocated safely
The algorithm in use is pretty dumb (take the next highest number), which can run out for long-lived applications that open lots of files.

First step here is to write a test case that exhibits the failure.

      Way: Centralize file descriptor allocation via shared open-files module
      - Search the filesystem implementation for all uses of fs.allocDescriptor, fs.releaseDescriptor, and direct access to fs.openFiles.
      - Import the open-files module at the top of the implementation file.
      - Replace every call to `fs.allocDescriptor(...)` with `openFiles.allocDescriptor(...)` and every reference to `fs.openFiles[fd]` or `fs.releaseDescriptor(fd)` with the corresponding `openFiles.getOpenFileDescription(fd)` or `openFiles.releaseDescriptor(fd)`.
      - Remove the unused `fs` parameter from all affected filesystem functions (open, close, mknod, mkdir, access, mkdtemp, rmdir, stat, fstat, link, unlink, read, fsync, readFile, write, writeFile, appendFile) and adjust the argument count checks accordingly.
      - Update any construction of `Stats` objects that previously used `fs.name` to use `context.name` instead.
      - Run the project's test suite and verify that the descriptor‑reuse test and all other filesystem tests pass.
  [3] Goal: Only preallocate annotation wrapper bytes when wrapping a container
      Annotations should only preallocate if they wrap a container
When writing containers in binary Ion, writers must wait until the complete contents of the container have been encoded before the container's header (which includes its length) can be written. 

As an optimization, some writers go ahead
      Way: reclaiming preallocated binary header bytes by shifting buffer contents left
      - Identify where container/wrapper length headers are patched when closing containers or annotation wrappers, and how buffer positions are tracked.
      - Implement a shiftBytesLeft method on the underlying write buffer to shift the tail bytes backward over unused preallocated header bytes, handling both single-block and multi-block buffer layouts.
      - Add a helper method to PreallocationMode (e.g., numberOfLengthBytes) to report the preallocated length byte count.
      - Update container completion logic so that when container or wrapper payload length is <= 0xD (fits in the low nibble of the type descriptor byte), shift the encoded body backwards to overwrite preallocated length bytes, patch the type descriptor byte nibble, and reduce the reported length.
      - Run the writer and buffer unit tests to verify short annotations and containers no longer emit surplus preallocated length bytes.

=== TASK Adyen__adyen-python-api-library-102 | repo Adyen/adyen-python-api-library | python
--- Issue
Util generate_hpp_sig() value and hmac do not correspond
**Python version**: 2.7.16
**Library version**: 2.1.0 (and tested too with future 2.2.0)
**Description**
TEST ENVIRONMENT

Implemented webhook for Adyen notifications (https://docs.adyen.com/development-resources/notifications) and using Test Configuration form in Adyen backend.

Passed JSON received data from Adyen to generate_hpp_sig() function according to test here : https://github.com/Adyen/adyen-python-api-library/blob/develop/test/UtilTest.py#L13

The generated hmac does not correspond to the addtionnalData['hmacSignature'].


Even tested with future 2.2.0 function is_valid_hmac, returns False.


--- Real fix (for your judgement only)
diff --git a/Adyen/util.py b/Adyen/util.py
index 580ff31..6156876 100644
--- a/Adyen/util.py
+++ b/Adyen/util.py
@@ -44,3 +44,53 @@ def is_valid_hmac(dict_object, hmac_key):
             merchant_sign = generate_hpp_sig(dict_object, hmac_key)
             merchant_sign_str = merchant_sign.decode("utf-8")
             return merchant_sign_str == expected_sign
+
+
+def generate_notification_sig(dict_object, hmac_key):
+    if 'issuerId' in dict_object:
+        if dict_object['issuerId'] == "":
+            del dict_object['issuerId']
+
+    if not isinstance(dict_object, dict):
+        raise ValueError("Must Provide dictionary object")
+
+    def escape_val(val):
+        if isinstance(val, int):
+            return val
+        return val.replace('\\', '\\\\').replace(':', '\\:')
+
+    hmac_key = binascii.a2b_hex(hmac_key)
+
+    request_dict = dict(dict_object)
+    request_dict['value'] = request_dict['amount']['value']
+    request_dict['currency'] = request_dict['amount']['currency']
+
+    element_orders = [
+        'pspReference',
+        'originalReference',
+        'merchantAccountCode',
+        'merchantReference',
+        'value',
+        'currency',
+        'eventCode',
+        'success',
+    ]
+
+    signing_string = ':'.join(
+        map(escape_val, map(str, (
+            request_dict.get(element, '') for element in element_orders))))
+
+    hm = hmac.new(hmac_key, signing_string.encode('utf-8'), hashlib.sha256)
+    return base64.b64encode(hm.digest())
+
+
+def is_valid_hmac_notification(dict_object, hmac_key):
+    if 'additionalData' in dict_object:
+        if dict_object['additionalData']['hmacSignature'] == "":
+            raise ValueError("Must Provide hmacSignature in additionalData")
+        else:
+            expected_sign = dict_object['additionalData']['hmacSignature']
+            del dict_object['additionalData']
+            merchant_sign = generate_notification_sig(dict_object, hmac_key)
+            merchant_sign_str = me
--- Kel's top 3
  [1] Goal: Fix argument name typo for HAC covariance matrix estimation
      BUG: Typo in argument name for HAC standard errors
#### Issue summary


The argument name for HAC robust standard errors (located [here](https://github.com/statsmodels/statsmodels/blob/9046d59cf1220c739a7904c8d8487e155ef91be5/statsmodels/regression/linear_model.py#L2432)) should be `maxlags` rath
      Way: fix argument name typos in docstrings and integer constraints in proportion confidence intervals
      - Locate docstring typos for HAC covariance options and inspect input validation logic in proportion confidence interval functions.
      - Reproduce the issue by running fit with cov_type='HAC' or calling proportion confidence intervals with non-integer array inputs.
      - Update docstrings from `maxlag` to `maxlags` for HAC options in regression results, and restrict strict integer array validation in proportion confidence intervals to methods that require it like `binom_test`.
      - Run pytest on the proportion test suite to verify all confidence interval methods handle non-integer array inputs correctly.
  [2] Goal: Fix type formatting and required field defaults in Markdown configuration generator
      Markdown generation issues in v5
We use `goodconf` and `cog` to generate a list of config values for lincolnloop/django-layout.

After upgrading to v5, I'm seeing the following diff

```diff
63c63
<   * type: `Literal['development', 'dev', 'production']`
---
>   * type: `typing.Literal['deve
      Way: Fix type formatting and hide undefined defaults in Markdown generation
      - Locate the Markdown generation logic in the configuration class, specifically where field annotations and default values are formatted.
      - Extract the field type formatting logic into a helper function that strips the 'typing.' prefix from annotations (e.g., converting 'typing.Literal' to 'Literal') while preserving standard types and generic aliases.
      - Modify the default value check in the Markdown generator to exclude both `None` and `PydanticUndefined` so that required fields do not display a default value.
      - Run the test suite to verify that the generated Markdown correctly formats types and omits default values for required fields.
  [3] Goal: Honor custom SRID parameter when dumping geometry to EsriJSON
      srid option for geomet.esri.dumps does not do anything
The option for defining output srid for the esrijson geometry is actively ignored
I have installed version 1.0.0
```python
import geomet.esri
from pprint import pprint

input_geojson =  {'coordinates': [494252.595744681, 7066045.31914894],
      Way: Prioritizing explicit SRID parameter over GeoJSON default extraction
      - Locate serialization functions in the EsriJSON module where GeoJSON objects are converted and check how the SRID parameter is handled.
      - Reproduce the issue by passing a custom srid to dumps without CRS metadata in the GeoJSON object, observing that the output spatialReference defaults to wkid 4326.
      - Update each geometry dump helper (_dump_geojson_point, _dump_geojson_multipoint, _dump_geojson_polyline, _dump_geojson_polygon) to only call _extract_geojson_srid when the passed srid argument is None.
      - Run the test suite verifying that custom SRID values are preserved for all geometry types.

=== TASK rollbar__rollbar-agent-60 | repo rollbar/rollbar-agent | python
--- Issue
Support running agent on Python 3
The rollbar-agent does not run on Python 3 (see https://github.com/rollbar/rollbar-agent/issues/53).

Python 2 has been officially sunsetted and is no longer supported, does not receive security patches, etc (see https://www.python.org/doc/sunset-python-2/)
--- Real fix (for your judgement only)
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
new file mode 100644
index 0000000..7f35aa1
--- /dev/null
+++ b/.github/workflows/ci.yml
@@ -0,0 +1,31 @@
+name: Rollbar-Agent CI
+
+on:
+  push:
+    branches: [ master ]
+    tags: [ v* ]
+  pull_request:
+    branches: [ master ]
+
+jobs:
+  build:
+    runs-on: ubuntu-18.04
+    strategy:
+      matrix:
+        python-version: [2.7, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9]
+    steps:
+      - uses: actions/checkout@v2
+
+      - name: Setup Python
+        uses: actions/setup-python@v2
+        with:
+          python-version: ${{ matrix.python-version }}
+
+      - name: Install dependencies
+        run: |
+          python -m pip install --upgrade pip
+          python -m pip install --upgrade --force-reinstall setuptools
+          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
+
+      - name: Run tests
+        run: python setup.py test
diff --git a/README.rst b/README.rst
index fd1a6ae..d3dde4f 100644
--- a/README.rst
+++ b/README.rst
@@ -36,7 +36,7 @@ Requirements
 rollbar-agent requires:
 
 - A unix-like system (tested on Fedora and Ubuntu Linux and Mac OS X)
-- Python 2.6+
+- Python 3 or 2.6+
 - requests 0.13.1+ (will be installed by pip or setup.py, below)
 - a Rollbar_ account
 
diff --git a/rollbar-agent b/rollbar-agent
index c014b67..79d1f73 100755
--- a/rollbar-agent
+++ b/rollbar-agent
@@ -4,9 +4,7 @@ rollbar-agent: agent to monitor log files and send notices to Rollbar
 """
 import ast
 import codecs
-import ConfigParser
 import copy
-import dbm
 import fnmatch
 import hashlib
 import json
@@ -22,6 +20,16 @@ import sys
 import threading
 import time
 
+try:
+    import configparser
+except ImportError:
+    import ConfigParser as configparser
+
+try:
+    import dbm.ndbm as dbm
+except ImportError:
+    import dbm
+
 import requests
 
 log = logging.getLogger(__name__)
@@ -51,11 +59,21 @@ LOG_LEVEL = {
 
 terminal_character_attribute_pattern = re.compile(r'\x1b\[[
--- Kel's top 3
  [1] Goal: Drop support for Python 2 and require Python 3
      Require Python 3
Since Python 2 has been deprecated, I suggest we drop support for it the next major release. Many libraries, including developer tools, have done so… pip, pytest, etc.

IMO it is not worth spending the limited volunteer development efforts we have to keep this working on an obsole
      Way: Drop Python 2 support in package metadata and CI configurations
      - Identify repository package metadata files and CI workflows configured for Python 2 support.
      - Remove Python 2 versions (such as 2.7) and deprecated Python 3 versions from package classifiers and CI build matrices.
      - Remove legacy compatibility dependencies (e.g. six) from install_requires and tests_require in setup.py, and set python_requires / requires_python to requiring Python 3 (e.g., >=3.5).
      - Run package test suite across supported Python 3 environments to ensure tests pass.
  [2] Goal: End support for Python 2.7 in future releases
      Drop Python 2.7 support going forward?
Is it worth it to drop Py2.7 support going forward and make the current release LTS? I believe so, as at least NumPy has done the same, and we might end up needing 1.17 features.

cc @rgommers and anyone else interested.
      Way: Remove Python 2.7 support from project configuration and code
      - Identify all project configuration files that specify supported Python versions or build environments.
      - Update continuous integration (CI) configurations to remove Python 2.7 from the build matrix and add newer Python versions (e.g., 3.7). Also, update test runner commands from 'py.test' to 'pytest'.
      - Rename and update Python 2.7-specific environment files (e.g., conda environment files) to reflect a Python 3.x environment.
      - Modify project metadata and documentation to declare Python 2.7 as unsupported and specify the new minimum Python 3.x version.
      - Remove Python 2.7 compatibility shims and conditional imports from the codebase. Update class definitions to use Python 3-style `super()` calls and new-style class syntax.
      - Verify that all tests pass with the updated Python environment and codebase.
  [3] Goal: Drop Python 2 support
      Dropping Python 2 support for rio-cogeo version 2.0.0
We haven't had any feedback when we did https://github.com/cogeotiff/rio-tiler/issues/139 so I guess (🤞) it will be the same here. 
      Way: enforcing python 3 and resolving crs with default grid mapping
      - Check packaging metadata in setup.py to restrict python versions, setting python_requires to '>=3'.
      - Identify where CRS discovery logic is implemented for raster DataArrays and Datasets, moving common lookup logic to the base accessor class.
      - In the base class crs property, fall back to DEFAULT_GRID_MAP when retrieving the 'grid_mapping' attribute from the object attrs.
      - Update Dataset-level CRS resolution to check super().crs first, and if not found, iterate over dataset variables to find the first valid variable CRS.
      - Run integration tests to verify CRS detection on datasets and datasets with nonstandard grid mappings.

=== TASK pydantic__pydantic-2265 | repo pydantic/pydantic | python
--- Issue
secrets_dir configuration setting raises an error when the directory doesn't exist
### Checks

* [x] I added a descriptive title to this issue
* [x] I have searched (google, github) for similar issues and couldn't find anything
* [x] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and still think this is a bug

<!-- Sorry to sound so draconian, but every second saved replying to issues is time spend improving pydantic :-) -->

# Bug

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
             pydantic version: 1.7.3
            pydantic compiled: True
                 install path: ~/.pyenv/versions/3.8.5/envs/fetcher/lib/python3.8/site-packages/pydantic
               python version: 3.8.5 (default, Sep  4 2020, 14:39:18)  [GCC 9.3.0]
                     platform: Linux-5.8.0-7630-generic-x86_64-with-glibc2.29
     optional deps. installed: ['typing-extensions', 'devtools']
```

I have declared a settings model that can read values from the environment, a `.env` file, or from Docker secrets as follows.

```py
import pydantic

class Settings(BaseSettings):

    password: SecretStr = Field(..., env="APP_PASSWORD")

    class Config:
        """Configure settings values discovery locations."""

        env_file = ".env"
        secrets_dir = "/run/secrets"
```

[As per the documentation](https://pydantic-docs.helpmanual.io/usage/settings/#field-value-priority), checking for 
--- Real fix (for your judgement only)
diff --git a/pydantic/env_settings.py b/pydantic/env_settings.py
--- a/pydantic/env_settings.py
+++ b/pydantic/env_settings.py
@@ -197,7 +197,9 @@ def __call__(self, settings: BaseSettings) -> Dict[str, Any]:
         secrets_path = Path(self.secrets_dir).expanduser()
 
         if not secrets_path.exists():
-            raise SettingsError(f'directory "{secrets_path}" does not exist')
+            warnings.warn(f'directory "{secrets_path}" does not exist')
+            return secrets
+
         if not secrets_path.is_dir():
             raise SettingsError(f'secrets_dir must reference a directory, not a {path_type(secrets_path)}')
 

--- Kel's top 3
  [1] Goal: Make Pydantic read secrets_dir files on Linux
      Pydantic fails to read content of files in secrets_dir on Linux
### Checks

* [x] I added a descriptive title to this issue
* [x] I have searched (google, github) for similar issues and couldn't find anything
* [x] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and sti
      Way: Enable case-insensitive secret file loading from secrets_dir
      - Identify the `SecretsSettingsSource` class responsible for loading secrets from the `secrets_dir` configuration.
      - Understand how `SecretsSettingsSource` attempts to resolve file paths for secrets. The issue indicates that direct path concatenation (`secrets_path / env_name`) fails on case-sensitive file systems (like Linux) when the file name's case does not exactly match the environment variable name, even if `case_sensitive` is set to `False`.
      - Implement a helper function, `find_case_path`, that searches a given directory for a file matching a specified name. This function must support both case-sensitive and case-insensitive matching based on a `case_sensitive` flag. It should iterate through the directory's contents and compare file names, returning the `Path` object if found, or `None` otherwise.
      - Modify the `SecretsSettingsSource.get_field_value` method to use the new `find_case_path` function. Replace the direct path construction (`secrets_path / env_name`) with a call to `find_case_path`, passing the `secrets_path`, the `env_name`, and the `settings.__config__.case_sensitive` flag. Add a check to skip processing if `find_case_path` returns `None` (meaning the file was not found).
      - Run the provided test suite to ensure the fix correctly addresses the case-insensitivity issue and does not introduce regressions.
  [2] Goal: Fix setup installation failure caused by missing test files or directories
      missing directory dicomdirtests
<!-- Instructions For Filing a Bug: https://github.com/pydicom/pydicom/blob/master/CONTRIBUTING.md#filing-bugs -->

#### Description
A fresh clone and then `python setup.py install` triggers an error

#### Steps/Code to Reproduce

```
root@1cd9ed419b2f:/tmp# g
      Way: organizing package test data into package module
      - Identify setup script installation failure caused by static data files or missing directory paths during packaging.
      - Move data files into a dedicated package submodule with `__init__.py` files so they can be bundled as package data rather than loose file paths in setup script.
      - Create data accessor functions in the new package data module to query and load data files programmatically using standard paths.
      - Update test suites and internal references to import test data files via the new module helper functions instead of relative directory paths.
      - Run the test suite to verify tests find data files and pass.
  [3] Goal: Prevent false positives in Pydantic model detection
      False positives for `is_pydantic_model`
The following checks:
- `_has_field_function`
- `_has_pydantic_method`

Raises false positives with the following code:

- Using `django-import-export`:

```python
class SomeResource(resources.ModelResource):
    user = Field(attribute="user")
```

      Way: Tighten AST heuristics for detecting Pydantic model classes
      - Locate heuristic helper functions used in `is_pydantic_model` for matching `Field(...)` calls and Pydantic methods.
      - Reproduce false positives with test cases where non-Pydantic classes call a `Field(...)` with custom arguments (e.g. `attribute=...`) or define arbitrary methods prefixed with `model_`.
      - Refine `_has_field_function` to verify that all keyword arguments in `Field(...)` calls belong to a set of valid Pydantic `Field` arguments (`PYDANTIC_FIELD_ARGUMENTS`).
      - Refine `_has_pydantic_method` to check against an explicit set of known Pydantic methods (`PYDANTIC_METHODS`) instead of matching any method starting with `model_`, while preserving prefix checks for `__pydantic_` and `__get_pydantic_`.
      - Run the test suite to verify false positives are eliminated while valid Pydantic models are still detected.

=== TASK zulip__zulip-terminal-1463 | repo zulip/zulip-terminal | python
--- Issue
Support server-customized presence parameters
This is a fairly small migration in Zulip 7.0, server feature level 164 (https://zulip.com/api/changelog).

Quoting the changelog entry:
> [POST /register](https://zulip.com/api/register-queue): Added the server_presence_ping_interval_seconds and server_presence_offline_threshold_seconds fields for clients to use when implementing the [presence](https://zulip.com/api/get-presence) system.

Instead of hard-coded constants, we should therefore use these values provided by the server, falling back to the original constants when the server version is lower (when the keys in the response to register-queue are empty).

If tests are added or updated, note that they should likely be parametrized over different feature levels - see other tests which do so for comparisons.
--- Real fix (for your judgement only)
diff --git a/zulipterminal/api_types.py b/zulipterminal/api_types.py
index 119770e..b341993 100644
--- a/zulipterminal/api_types.py
+++ b/zulipterminal/api_types.py
@@ -34,6 +34,14 @@ MAX_TOPIC_NAME_LENGTH: Final = 60
 MAX_MESSAGE_LENGTH: Final = 10000
 
 
+###############################################################################
+# These values are in the register response from ZFL 164
+# Before this feature level, they had the listed default (fixed) values
+
+PRESENCE_OFFLINE_THRESHOLD_SECS: Final = 140
+PRESENCE_PING_INTERVAL_SECS: Final = 60
+
+
 ###############################################################################
 # Core message types (used in Composition and Message below)
 
diff --git a/zulipterminal/model.py b/zulipterminal/model.py
index af0b846..72215c8 100644
--- a/zulipterminal/model.py
+++ b/zulipterminal/model.py
@@ -34,6 +34,8 @@ from zulipterminal.api_types import (
     MAX_MESSAGE_LENGTH,
     MAX_STREAM_NAME_LENGTH,
     MAX_TOPIC_NAME_LENGTH,
+    PRESENCE_OFFLINE_THRESHOLD_SECS,
+    PRESENCE_PING_INTERVAL_SECS,
     TYPING_STARTED_EXPIRY_PERIOD,
     TYPING_STARTED_WAIT_PERIOD,
     TYPING_STOPPED_WAIT_PERIOD,
@@ -81,9 +83,6 @@ from zulipterminal.platform_code import notify
 from zulipterminal.ui_tools.utils import create_msg_box_list
 
 
-OFFLINE_THRESHOLD_SECS = 140
-
-
 class ServerConnectionFailure(Exception):
     pass
 
@@ -172,6 +171,8 @@ class Model:
         self.server_version = self.initial_data["zulip_version"]
         self.server_feature_level: int = self.initial_data.get("zulip_feature_level", 0)
 
+        self._store_server_presence_intervals()
+
         self.user_dict: Dict[str, MinimalUserData] = {}
         self.user_id_email_dict: Dict[int, str] = {}
         self._all_users_by_id: Dict[int, RealmUser] = {}
@@ -445,7 +446,7 @@ class Model:
                     view = self.controller.view
                     view.users_view.update_user_list(user_list=self.users)
                     view.middle_column.upda
--- Kel's top 3
  [1] Goal: Handle null realm message edit time limit when editing messages
      [bug] crash when editing message
Server: Zulip cloud server
Client: main branch
Python: 3.11
self.model.initial_data: `{'zulip_version': '9.0-dev-642-g9d469357ea', 'zulip_feature_level': 237, 'zulip_merge_base': '9.0-dev-605-g47a5459637', 'realm_message_content_edit_limit_seconds': None}`
log:

      Way: Handle nullable message edit time limit settings in UI key handling
      - Locate message keypress handling where `realm_message_content_edit_limit_seconds` is evaluated to decide message edit permissions.
      - Fetch `realm_message_content_edit_limit_seconds` into a variable and check that it is not `None` before performing numeric comparison (`> 0`). Reuse this variable for subsequent elapsed time comparisons.
      - Run the message keypress test suite covering scenarios where the edit time limit is None or unlimited.
  [2] Goal: Accept server URLs with protocol prefixes during login
      On login, providing zulip server prefixed by http:// or https:// manifests in incorrect username/password error
On running zulip-term for the first time:
```
(zulip_terminal_pyvenv) [ahelwer@node ~]$ zulip-term
zuliprc file was not found at /home/ahelwer/zuliprc
Please enter your credentials to 
      Way: Use server-reported canonical realm URL during CLI authentication
      - Identify CLI login helper functions that fetch server settings and authenticate user credentials using user-input server URLs.
      - Update the server settings fetching step to extract the canonical server URI (`realm_uri`) from the server properties response.
      - Refactor helper functions so that the server properties payload is parsed once and reused for prompt labels and authentication endpoints.
      - Use the server's canonical `realm_uri` instead of raw user input when querying credentials and saving configuration.
      - Run CLI login and authentication tests to verify correct parsing and handling of server properties.
  [3] Goal: Enforce timezone handling for transaction time query parameters
      Query parameters after-time and before-time for /v2/transactions always assume UTC even if a different timezone is specified
## Summary

Query parameters after-time and before-time for /v2/transactions always assume UTC even if a different timezone is specified.

```bash
$ curl -s "https://test
      Way: Ensure database queries use UTC for time parameters
      - Identify the code responsible for constructing database queries involving time parameters, specifically for transaction filtering.
      - Examine how `BeforeTime` and `AfterTime` fields from the `TransactionFilter` are used in the SQL query arguments.
      - Modify the query construction logic to explicitly convert `tf.BeforeTime` and `tf.AfterTime` to UTC before adding them to the SQL query arguments.
      - Verify that the fix correctly handles time parameters with specified timezones by running the relevant tests.

=== TASK dwavesystems__dwave-cloud-client-455 | repo dwavesystems/dwave-cloud-client | python
--- Issue
Event dispatch decorator
Call `dispatch_event` before and after decorated function execution, automating args and rval collection/reporting.
--- Real fix (for your judgement only)
diff --git a/dwave/cloud/client.py b/dwave/cloud/client.py
index 7986b84..db3c444 100644
--- a/dwave/cloud/client.py
+++ b/dwave/cloud/client.py
@@ -73,7 +73,7 @@ from dwave.cloud.config import load_config, parse_float, parse_int, parse_boolea
 from dwave.cloud.solver import Solver, available_solvers
 from dwave.cloud.concurrency import PriorityThreadPoolExecutor
 from dwave.cloud.upload import ChunkedData
-from dwave.cloud.events import dispatch_event
+from dwave.cloud.events import dispatches_events
 from dwave.cloud.utils import (
     TimeoutingHTTPAdapter, BaseUrlSession, user_agent,
     datetime_to_timestamp, utcnow, epochnow, cached, retried, is_caused_by)
@@ -343,6 +343,7 @@ class Client(object):
         logger.debug("Creating %s.Client() with: %r", _client, config)
         return _clients[_client](**config)
 
+    @dispatches_events('client_init')
     def __init__(self, endpoint=None, token=None, solver=None, **kwargs):
         # for (reasonable) backwards compatibility, accept only the first few
         # positional args.
@@ -354,9 +355,6 @@ class Client(object):
         if solver is not None:
             kwargs.setdefault('solver', solver)
 
-        dispatched_args = kwargs.copy()
-        dispatch_event('before_client_init', obj=self, args=dispatched_args)
-
         logger.debug("Client init called with: %r", kwargs)
 
         # derive instance-level defaults from class defaults and init defaults
@@ -523,9 +521,6 @@ class Client(object):
         self._encode_problem_executor = \
             ThreadPoolExecutor(self._ENCODE_PROBLEM_THREAD_COUNT)
 
-        dispatch_event(
-            'after_client_init', obj=self, args=dispatched_args, return_value=None)
-
     def create_session(self):
         """Create a new requests session based on client's (self) params.
 
@@ -728,6 +723,7 @@ class Client(object):
         self._load(future)
         return future
 
+    @dispatches_events('get_solvers')
     def get_solvers(self, refresh=False, order_b
--- Kel's top 3
  [1] Goal: Fix signature validation when registering decorated class methods as event handlers
      Bug adding handler in case of decoration + class function + filtered event
## 🐛 Bug description

I would like to report a bug using handler defined by decorated function in a class with filtered event.

The following code reproduces all possible situations to add an handler defined w/wo decorati
      Way: inspect signature parent target when validating decorated method event handlers
      - Locate signature checking helper function in engine utilities where handler arguments are inspected.
      - Reproduce the issue by calling event handler registration on a decorated bound class method with a filtered event.
      - In signature inspection logic, check if the function object has a `_parent` attribute and inspect `fn._parent()` instead of the wrapper function when `_parent` exists.
      - Run event handler tests to verify decorated class methods with event filters pass signature validation.
  [2] Goal: Pass the caught exception to handler functions in backoff decorators
      [Feature Suggestion] - Exception in _call_handlers
Would be great to know which exception was thrown in handlers from decorator exception tuple  in `backoff.on_exception`. Maybe receive exception as extra kwargs in _call_handlers. What you guys think?

      Way: Pass caught exception to retry handler details in backoff decorators
      - Locate sync and async retry exception handler invocations in retry logic functions.
      - Run existing decorator tests to verify base behavior.
      - Pass caught exception instance `e` as an `exception` keyword argument into `_call_handlers` calls for `on_backoff` and `on_giveup` events in both sync and async retry loops.
      - Document in documentation that the exception instance is accessible via the `exception` key in details passed to handlers.
      - Run test suite to verify handlers receive caught exception.
  [3] Goal: Support custom view decorators on API route handler functions
      Decorators
```
@api.get("/add")
@cache_page(60 * 15)
def add(request, a: int, b: int):
    return {"result": a + b}
```
so currently this will not work due to inspection procedure...

I guess there must be some `@decorate` helper to allow to add custom decorators that will not conflict with 
      Way: Add helpful error messages for missing functools.wraps on decorated endpoints
      - Locate where route handler view functions are invoked and exceptions are handled during API operations.
      - Reproduce the failure when endpoints are wrapped with custom decorators that do not preserve signatures using functools.wraps, resulting in TypeError about missing required positional arguments.
      - Update the exception handling block in the operation runner to intercept TypeErrors containing 'required positional argument' and prepend a hint asking if functools.wraps was omitted.
      - Run the test suite to verify that the enhanced TypeError message is caught and handled properly.

=== TASK duartegroup__autodE-77 | repo duartegroup/autodE | python
--- Issue
NCIComplex conformers are not generated correctly
**Describe the bug**
<!-- A clear and concise description of what the bug is. -->
NCI complex conformers are all generated with the same name, which means optimisations skip over all but the first one. The documentation water trimer works, but for other more complex surfaces this is not expected to.

**To Reproduce**
<!-- Steps to reproduce the behaviour: code with output -->

```
import autode as ade
from autode.species import NCIComplex

ade.Config.num_complex_sphere_points = 5
ade.Config.num_complex_random_rotations = 3

water = ade.Molecule(name='water', smiles='O')

trimer = NCIComplex(water, water, water, name='water_trimer')
trimer.find_lowest_energy_conformer(lmethod=ade.methods.XTB())
trimer.print_xyz_file()
```

The conformers directory only contains a single XTB output file

**Expected behavior**
<!-- A clear and concise description of what you expected to happen. -->

**Environment**
<!-- please complete the following information. -->
 - OS: CentOS
 - Version: v. 1.1.0

--- Real fix (for your judgement only)
diff --git a/autode/conformers/conformers.py b/autode/conformers/conformers.py
index ef46de0..cda456e 100644
--- a/autode/conformers/conformers.py
+++ b/autode/conformers/conformers.py
@@ -147,7 +147,7 @@ class Conformers(list):
             if any(calc_heavy_atom_rmsd(conf.atoms, other.atoms) < rmsd_tol
                    for o_idx, other in enumerate(self) if o_idx != idx):
 
-                logger.info(f'Conformer {idx} was close in geometry to at'
+                logger.info(f'Conformer {idx} was close in geometry to at '
                             f'least one other - removing')
 
                 del self[idx]
diff --git a/autode/species/complex.py b/autode/species/complex.py
index a67efc8..31f7a66 100644
--- a/autode/species/complex.py
+++ b/autode/species/complex.py
@@ -166,7 +166,7 @@ class Complex(Species):
 
             for points in iterprod(points_on_sphere, repeat=n-1):
 
-                conf = Conformer(species=self, name=f'{self.name}_conf{n}')
+                conf = Conformer(species=self, name=f'{self.name}_conf{m}')
                 conf.atoms = get_complex_conformer_atoms(self._molecules,
                                                          rotations,
                                                          points)
diff --git a/doc/common/water_trimer.py b/doc/common/water_trimer.py
index 728b945..22aaea9 100644
--- a/doc/common/water_trimer.py
+++ b/doc/common/water_trimer.py
@@ -1,12 +1,12 @@
-from autode import Molecule, Config
+import autode as ade
 from autode.species import NCIComplex
-from autode.wrappers.XTB import xtb
 
-Config.num_complex_sphere_points = 5
-Config.num_complex_random_rotations = 3
+ade.Config.num_complex_sphere_points = 5
+ade.Config.num_complex_random_rotations = 3
+xtb = ade.methods.XTB()
 
 # Make a water molecule and optimise at the XTB level
-water = Molecule(name='water', smiles='O')
+water = ade.Molecule(name='water', smiles='O')
 water.optimise(method=xtb)
 
 # Make the NCI complex and find the lowes
--- Kel's top 3
  [1] Goal: Add methods to enumerate SMIRNOFF and single improper torsions in a molecule
      Make `offmol.smirnoff_impropers` and `offmol.single_impropers`
**Describe the bug**
Not really a bug, possibly naive question about intended behavior in enumerating indices for improper torsions.

I was surprised at the large number of torsions included in `offmol.impropers` for some molecules (e
      Way: Add SMIRNOFF and AMBER improper torsion enumeration methods
      - Locate the molecule and topology classes where improper torsions are currently defined and enumerated.
      - Implement `smirnoff_impropers` and `amber_impropers` properties on the molecule class using SMARTS queries to filter for trivalent centers and order the atoms accordingly.
      - Expose `smirnoff_impropers` and `amber_impropers` on the `TopologyMolecule` and `Topology` classes, converting the reference molecule atoms to topology atoms.
      - Run the test suite to verify that the new improper torsion enumeration methods correctly identify and order the atoms.
  [2] Goal: Ensure DerivedShape.getArea() uses cold argument for area
      `DerivedShape.getArea()` doesn't respect `cold=True` argument
Here is a very simple blueprints file that will run with just the framework:
```yaml
nuclide flags:
  NA:
    burn: false
    xs: true
    expandTo:
  ZR:
    burn: false
    xs: true
    expandTo:
  C:
    burn: false
    xs
      Way: Support cold temperature area calculation in derived geometry shapes
      - Locate the `getArea` method on the derived shape component class where area calculations are performed.
      - Reproduce the issue by creating a parent block containing explicit components and a derived component, then compare `getArea(cold=True)` against `getArea(cold=False)` on the derived component.
      - Update `getArea(cold=True)` in the derived shape class to subtract the sum of cold areas of sibling components from the total area of the parent block.
      - Verify that `getArea(cold=True)` returns the correct non-expanded cold area for derived shapes.
  [3] Goal: Ensure deterministic bundle digests for identical input
      `bndl` produces bundles with differing digests despite same input.
Given the same input, we should produce a bundle with a constant digest. I believe the bug here is around timestamps. See below.

```bash
$ docker save nginx | sha256sum
bd4f716f157c4f0e7e4d3007fc38572a4b946884f1908d68520b88d8758
      Way: normalize file modification times and gzip headers for deterministic bundle output
      - Locate bundle creation and archive writing logic across packaging scripts.
      - Reproduce non-deterministic hashes by creating two bundles from the same source and comparing SHA256 hashes.
      - Extract modification timestamp (`mtime`) from OCI configuration when present or fall back to first file mtime in the archive directory.
      - Apply explicit mtime to zip entries, tar header entries, temporary files using `os.utime`, and set `mtime=0` on `GzipFile` instances to avoid timestamp non-determinism in compressed streams.
      - Run the test suite to confirm bundle creation determinism and helper utility functions pass.

=== TASK pydantic__pydantic-2384 | repo pydantic/pydantic | python
--- Issue
Use metadata in dataclass field to populate pydantic Field
### Checks

* [x ] I added a descriptive title to this issue
* [ x] I have searched (google, github) for similar issues and couldn't find anything
* [x ] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and still think this is a bug

<!-- Sorry to sound so draconian, but every second saved replying to issues is time spend improving pydantic :-) -->

# Bug

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
...             pydantic version: 1.7.3
            pydantic compiled: True
                 install path: $VENV_PATH/lib/python3.8/site-packages/pydantic
               python version: 3.8.5 (default, Jul 28 2020, 12:59:40)  [GCC 9.3.0]
                     platform: Linux-5.8.0-43-generic-x86_64-with-glibc2.29
     optional deps. installed: ['typing-extensions']

```
<!-- or if you're using pydantic prior to v1.3, manually include: OS, python version and pydantic version -->

<!-- Please read the [docs](https://pydantic-docs.helpmanual.io/) and search through issues to
confirm your bug hasn't already been reported. -->

<!-- Where possible please include a self-contained code snippet describing your bug: -->

```py

from pydantic import BaseModel, Field
from pydantic.dataclasses import dataclass
from dataclasses import field

class DescriptionFromBasemodel(BaseModel):
    descr_present: int = Field(
        42,
     
--- Real fix (for your judgement only)
diff --git a/docs/examples/dataclasses_default_schema.py b/docs/examples/dataclasses_default_schema.py
--- a/docs/examples/dataclasses_default_schema.py
+++ b/docs/examples/dataclasses_default_schema.py
@@ -1,5 +1,7 @@
 import dataclasses
-from typing import List
+from typing import List, Optional
+
+from pydantic import Field
 from pydantic.dataclasses import dataclass
 
 
@@ -8,6 +10,11 @@ class User:
     id: int
     name: str = 'John Doe'
     friends: List[int] = dataclasses.field(default_factory=lambda: [0])
+    age: Optional[int] = dataclasses.field(
+        default=None,
+        metadata=dict(title='The age of the user', description='do not lie!')
+    )
+    height: Optional[int] = Field(None, title='The height in cm', ge=50, le=300)
 
 
 user = User(id='42')
diff --git a/pydantic/dataclasses.py b/pydantic/dataclasses.py
--- a/pydantic/dataclasses.py
+++ b/pydantic/dataclasses.py
@@ -3,14 +3,14 @@
 from .class_validators import gather_all_validators
 from .error_wrappers import ValidationError
 from .errors import DataclassTypeError
-from .fields import Required
+from .fields import Field, FieldInfo, Required, Undefined
 from .main import create_model, validate_model
 from .typing import resolve_annotations
 from .utils import ClassAttribute
 
 if TYPE_CHECKING:
     from .main import BaseConfig, BaseModel  # noqa: F401
-    from .typing import CallableGenerator
+    from .typing import CallableGenerator, NoArgAnyCallable
 
     DataclassT = TypeVar('DataclassT', bound='Dataclass')
 
@@ -19,6 +19,7 @@ class Dataclass:
         __initialised__: bool
         __post_init_original__: Optional[Callable[..., None]]
         __processed__: Optional[ClassAttribute]
+        __has_field_info_default__: bool  # whether or not a `pydantic.Field` is used as default value
 
         def __init__(self, *args: Any, **kwargs: Any) -> None:
             pass
@@ -80,6 +81,30 @@ def is_builtin_dataclass(_cls: Type[Any]) -> bool:
     return not hasattr(_cls, '__processed
--- Kel's top 3
  [1] Goal: Allow instantiating messages generated with pydantic dataclasses
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

=== TASK streamlink__streamlink-389 | repo streamlink/streamlink | python
--- Issue
Mytf1.fr : TF 1, TMC, NT 1, HD 1, LCI
http://www.tf1.fr/direct
Channels on TF 1 site require only a free login and French proxy. They use HDS and HLS. 
LCi doesn't require login just proxy : http://www.lci.fr/direct/

But it would be of course better if you can bypass login. 
--- Real fix (for your judgement only)
diff --git a/docs/plugin_matrix.rst b/docs/plugin_matrix.rst
index fd3c8d39..846f39e2 100644
--- a/docs/plugin_matrix.rst
+++ b/docs/plugin_matrix.rst
@@ -137,6 +137,7 @@ tv4play             - tv4play.se         Yes   Yes   Streams may be geo-restrict
                                                      Only non-premium streams currently supported.
                     - fotbollskanalen.se
 tv8                 tv8.com.tr           Yes   No
+tv8cat              tv8.cat              Yes   No    Streams may be geo-restricted to Spain/Catalunya.
 tv360               tv360.com.tr         Yes   No
 tvcatchup           - tvcatchup.com      Yes   No    Streams may be geo-restricted to Great Britain.
 tvplayer            tvplayer.com         Yes   No    Streams may be geo-restricted to Great Britain. Premium streams are not supported.
diff --git a/src/streamlink/plugins/tf1.py b/src/streamlink/plugins/tf1.py
index 337677e6..f1e6fad8 100644
--- a/src/streamlink/plugins/tf1.py
+++ b/src/streamlink/plugins/tf1.py
@@ -11,9 +11,9 @@ class TF1(Plugin):
     url_re = re.compile(r"https?://(?:www\.)?(?:tf1\.fr/(\w+)/direct|(lci).fr/direct)/?")
     embed_url = "http://www.wat.tv/embedframe/live{0}"
     embed_re = re.compile(r"urlLive.*?:.*?\"(http.*?)\"", re.MULTILINE)
-    api_url = "http://www.wat.tv/get/androidlive{0}/591997"
-    swf_url = "http://www.wat.tv/images/v70/PlayerWat.swf?rev=04.00.861"
-    hds_channel_remap = {"tf1": "connect"}
+    api_url = "http://www.wat.tv/get/{0}/591997"
+    swf_url = "http://www.wat.tv/images/v70/PlayerLite.swf"
+    hds_channel_remap = {"tf1": "androidliveconnect", "lci": "androidlivelci"}
     hls_channel_remap = {"lci": "LCI", "tf1": "V4"}
 
     @classmethod
@@ -21,13 +21,15 @@ class TF1(Plugin):
         return cls.url_re.match(url) is not None
 
     def _get_hds_streams(self, channel):
-        channel = self.hds_channel_remap.get(channel, channel)
+        channel = self.hds_channel_remap.get(channel, "{0}live".format(channel))
   
--- Kel's top 3
  [1] Goal: Add export support for the Cf/Radial1 radar data format
      Cf/Radial1 writer
Based on the discussions held during the AMS developers meeting, there is a perceived need for a `to_cfradial1` function in the Cf/Radial1 format. The decision of its future relevance may depend on Radar Community's compatibility with Cf/Radial1. If necessary, we can consider depre
      Way: Export DataTree to Cf/Radial1 radar data format
      - Identify the module responsible for data export functionality, typically `xradar.io.export` for `xradar` library.
      - Implement the `to_cfradial1` function within the export module. This function should accept a `datatree.DataTree` object and a filename, and write the data to a Cf/Radial1 compliant NetCDF file. The implementation needs to handle the conversion of the `DataTree` structure and its variables/attributes into the Cf/Radial1 format.
      - Add documentation for the new `to_cfradial1` function, including its purpose, parameters, and usage examples. This involves updating relevant documentation files like `docs/exporters.md` and potentially adding a new example notebook.
      - Update project metadata and history to reflect the new feature and contributor.
      - Verify that the new `to_cfradial1` function correctly exports data by running the relevant tests.
  [2] Goal: Allow passing custom fsspec options for WebHDFS connections
      Allow passing fsspec options to WebHDFS to enable Kerberos and https
After #6662 it is no longer possible to use custom hdfscli clients with WebHDFS, as a result we are no longer able to connect using Kerberos or HTTPS.

fsspec allows passing in options to enable kerberos and https, we simply need
      Way: Expose custom fsspec options for WebHDFS connections
      - Locate the configuration schema and the WebHDFS filesystem implementation files.
      - Update the configuration schema to remove obsolete hdfscli options and add new WebHDFS options including kerberos, kerberos_principal, proxy_to, ssl_verify, token, and use_https.
      - Modify the WebHDFS filesystem class to extract and map the new configuration options (such as kerberos_principal to kerb_kwargs, and ssl_verify to the underlying fsspec session verify attribute).
      - Add requests-kerberos to the webhdfs extra dependencies in setup.cfg.
      - Run the unit tests to verify that the WebHDFS initialization and SSL verification options are correctly applied.
  [3] Goal: Enable HTTP client to load TLS files from custom FS
      Support custom `io/fs.FS` for HTTP clients
## Background

The Grafana Agent team is working on supporting the ServiceMonitor, PodMonitor, and Probe CRDs from Prometheus Operator directly in Grafana Agent. 

This works by Grafana Agent discovering those resources in Kubernetes, and configuring an
      Way: Add inline TLS fields and validation to HTTP client configuration
      - Locate the TLS configuration handling in the codebase (TLSConfig struct, NewTLSConfig, NewTLSRoundTripper, and callers such as NewRoundTripperFromConfig and oauth2RoundTripper).
      - Extend TLSConfig with inline fields (CA, Cert, Key) and implement a Validate method that ensures only one of inline or file‑based fields is set; call Validate from UnmarshalYAML and NewTLSConfig.
      - Add helper methods usingClientCert, usingClientKey, and roundTripperSettings to TLSConfig; modify NewTLSConfig to use inline CA when provided and to call cfg.getClientCertificate which now reads from file or inline data.
      - Create a TLSRoundTripperSettings struct and change NewTLSRoundTripper signature to accept it; replace direct file‑path fields with the settings struct; implement getTLSDataWithHash to read from either file paths or inline strings based on the settings.
      - Update all callers of NewTLSRoundTripper (NewRoundTripperFromConfig and oauth2RoundTripper) to pass cfg.TLSConfig.roundTripperSettings() instead of individual file names.
      - Run the full test suite to ensure the changes did not break existing behaviour.

=== TASK petrobras__ross-966 | repo petrobras/ross | python
--- Issue
Orientation is not available to plot_2d
When running modal analysis and plotting the results we use:

```python
modal = rotor.run_modal(...)
modal.plot_mode_2d(...)
```
In `plot_mode_2d()` we do not have an option to pass `orientation='y'` to the method, although that is available in the `Shape.plot_mode_2d()` method. 

We should add the `orientation` argument to the `plot_mode_2d` method and add this to the docstring.
--- Real fix (for your judgement only)
diff --git a/ross/results.py b/ross/results.py
index 129042b..4f23647 100644
--- a/ross/results.py
+++ b/ross/results.py
@@ -1129,6 +1129,7 @@ class ModalResults(Results):
         self,
         mode=None,
         fig=None,
+        orientation="major",
         frequency_type="wd",
         title=None,
         length_units="m",
@@ -1144,6 +1145,9 @@ class ModalResults(Results):
             The n'th vibration mode
         fig : Plotly graph_objects.Figure()
             The figure object with the plot.
+        orientation : str, optional
+            Orientation can be 'major', 'x' or 'y'.
+            Default is 'major' to display the major axis.
         frequency_type : str, optional
             "wd" calculates the damped natural frequencies.
             "wn" calculates the undamped natural frequencies.
@@ -1187,7 +1191,7 @@ class ModalResults(Results):
         }
 
         shape = self.shapes[mode]
-        fig = shape.plot_2d(fig=fig)
+        fig = shape.plot_2d(fig=fig, orientation=orientation)
 
         if title is None:
             title = ""

--- Kel's top 3
  [1] Goal: Fix error in modal split plot when passing an axis object
      BUG: error in modal split plot when passing axis
The modal split plotting function throws an error if the `axis` argument is not None. The error occurs in [this line](https://github.com/mie-lab/trackintel/blob/ca4300e6056b8500a27907faca515478ca268524/trackintel/visualization/modal_split.py#L112)
be
      Way: prevent undefined figure errors when passing custom matplotlib axes
      - Locate the plotting function that accepts an optional axis argument.
      - Add an optional `fig` parameter to the function signature and update its docstring.
      - Modify the title setting logic to use `ax.set_title` instead of `plt.title` to respect the passed axis.
      - Wrap the `fig.autofmt_xdate()` call in a conditional block so it only executes if `fig` is not None.
      - Fix the tick skipping logic to correctly hide every nth tick label based on the `skip_xticks` parameter.
      - Run the visualization tests to verify that passing an axis argument no longer raises an error and that tick skipping works correctly.
  [2] Goal: Allow passing a custom axes argument to 2D mesh plots
      ax arg in 2d plots is broken
```python
fig, ax = plt.subplots(1, 1)
pp.plot(sc.DataArray(sc.zeros(sizes={'x': 10, 'y': 10})), ax=ax)
```
raises
```

File ~/Software/mambaforge/envs/uncertain/lib/python3.8/site-packages/plopp/graphics/mesh.py:100, in Mesh.__init__(self, canvas, data, shading, 
      Way: Forward custom ax and cax to matplotlib 2D figures
      - Identify figure and canvas initializers where user-provided `ax` and `cax` arguments get forwarded to plotting artists or canvas constructors instead of being consumed by figure wrappers.
      - Update Figure2d and Canvas signatures to accept `ax` and `cax` parameters, passing them down to Canvas instead of forwarding `ax` into artist kwargs.
      - In Canvas, track whether axes were internally created (`_own_axes`) and attach colorbar axes using `mpl_toolkits.axes_grid1.make_axes_locatable` when `cbar` is True and `cax` is not supplied.
      - Add a `fit_to_page` method on Canvas that calls `tight_layout()` only when axes are internally owned, and call it after figure initialization in Figure1d and Figure2d.
      - Run the 1D and 2D figure test suite to verify custom `ax` and `cax` arguments are accepted and plotted properly.
  [3] Goal: Assign units attributes to spatial coordinates when reading GEMPAK grids
      gdxarray() should assign units to x and y coordinates
Is there a way to construct the proper `Dataset` with the current MetPy functionality so my attempt to plot wind barbs works?

Here's my attempt:
```python
from datetime import datetime

import xarray as xr
import metpy
from metpy.units i
      Way: Assign spatial coordinates units and latitude-longitude when constructing xarray datasets from grids
      - Locate grid reader logic converting native file data into xarray DataArray objects.
      - Update DataArray creation to attach geographic latitude/longitude coordinates via MetPy accessor and assign 'meters' to the units attribute of 'x' and 'y' coordinates.
      - Verify the coordinate attributes are properly attached and grid projection tests pass.

=== TASK iterative__dvc-5906 | repo iterative/dvc | python
--- Issue
dvc: expand on `--no-pager` option
1. ~~Make it the default for `dvc exp show`?~~ No longer needed after #5906
2. Include it in `dvc dag`?
3. Maybe put it directly in `dvc --no-pager` so it works for any commands that need it in the future.
4. Any other existing commands that need it? E.g. `list` or `stage list`

Rel. #4060
dag: do not use pager when output fits into one screen
The same way as Git determines screen size and does not use "less" to show diff, log, etc we should avoid using it when it's not actually needed.

Using "pager" should be considered an unavoidable "evil" in cases when output is too large. 

--- Real fix (for your judgement only)
diff --git a/dvc/utils/pager.py b/dvc/utils/pager.py
--- a/dvc/utils/pager.py
+++ b/dvc/utils/pager.py
@@ -14,40 +14,80 @@
 
 
 DEFAULT_PAGER = "less"
-DEFAULT_PAGER_FORMATTED = (
-    f"{DEFAULT_PAGER} --chop-long-lines --clear-screen --RAW-CONTROL-CHARS"
-)
-
-
-def make_pager(cmd):
+LESS = "LESS"
+PAGER_ENV = "PAGER"
+
+
+def prepare_default_pager(
+    clear_screen: bool = False,
+    quit_if_one_screen: bool = True,
+    ansi_escapes: bool = True,
+    chop_long_lines: bool = True,
+    no_init: bool = True,
+    no_tilde: bool = False,
+) -> str:
+    args = [DEFAULT_PAGER]
+    if clear_screen:
+        args.append("--clear-screen")  # -c
+    if quit_if_one_screen:
+        args.append("--quit-if-one-screen")  # -F
+    if ansi_escapes:
+        args.append("--RAW-CONTROL-CHARS")  # -R
+    if chop_long_lines:
+        args.append("--chop-long-lines")  # -S
+    if no_init:
+        args.append("--no-init")  # -X
+    if no_tilde:
+        args.append("--tilde")  # -~
+
+    return " ".join(args)
+
+
+def make_pager(cmd=None):
     def _pager(text):
         return pydoc.tempfilepager(pydoc.plain(text), cmd)
 
-    return _pager
+    return _pager if cmd else pydoc.plainpager
 
 
 def find_pager():
     if not sys.stdout.isatty():
-        return pydoc.plainpager
-
-    env_pager = os.getenv(DVC_PAGER)
-    if env_pager:
-        return make_pager(env_pager)
-
-    if os.system(f"({DEFAULT_PAGER}) 2>{os.devnull}") == 0:
-        return make_pager(DEFAULT_PAGER_FORMATTED)
-
-    logger.warning(
-        "Unable to find `less` in the PATH. Check out "
-        "{} for more info.".format(
-            format_link("https://man.dvc.org/pipeline/show")
+        return None
+
+    # pylint: disable=redefined-outer-name
+    pager = os.getenv(DVC_PAGER)
+    if not pager:
+        pager = os.getenv(PAGER_ENV)
+    if not pager:
+        if os.system(f"({DEFAULT_PAGER}) 2>{os.devnull}") != 0:
+            logger.warning(
+                "Unable to find `less` in the
--- Kel's top 3
  [1] Goal: Hide deprecated options from the run command help output
      run: hide --file and --single-stage
## Report

Per https://github.com/iterative/dvc.org/pull/1420#discussion_r442644095:

--file is still in the help output of `dvc run -h`, mentions DVC-file (a concept we are removing), and makes no sense without --single-stage (which is hidden). Please hide bo
      Way: Suppress deprecated CLI arguments from help output using argparse.SUPPRESS
      - Locate CLI parser definitions for the target command and check argument options.
      - Update the CLI argument definition to set `help=argparse.SUPPRESS` to hide it from help output.
      - Add runtime validation logic in the command handler to raise an error when mutually exclusive or invalid flag combinations involving the suppressed argument are used.
      - Run unit tests for the command's execution and argument validation logic.
  [2] Goal: Introduce option to completely remove stage files and their outputs
      Introduce `--purge` option for `dvc remove`
`dvc delete stage.dvc` should remove outputs of the specified stage(same as `dvc remove`) and also delete the dvc file itself and do things like cleaning up .gitignore.

Kudos @analystanand !
      Way: Add purge option to remove stage files and their outputs
      - Locate the CLI argument parser definition for the remove command and add mutually exclusive options for removing only outputs versus purging both the stage file and its outputs.
      - Update the remove command implementation to parse the purge flag and pass it down to the project remove method.
      - Modify the project remove method to accept an outs_only parameter, calling stage.remove_outs() when true, and stage.remove() when false.
      - Run the test suite to verify that both stage file removal and output-only removal work as expected.
  [3] Goal: Improve error message when no plots are found in the workspace
      plots show: targets are required but help output indicates otherwise [qa]
## Bug Report

Help output indicates `targets` are optional, but an error is thrown if not provided:

```console
λ dvc plots show -h
usage: dvc plots show [-h] [-q | -v] [-t [TEMPLATE]] [-o OUT] [-x X] [-y Y] [--no-csv-h
      Way: Improve error message when no plots are found in workspace
      - Locate the plots show command implementation and the exceptions module.
      - Define a new specific exception class `NoPlotsError` in the exceptions module that guides the user on how to mark stage outputs as plots.
      - Modify the plots show logic to catch the generic `NoDataOrTemplateProvided` exception when no targets are specified, and raise the new `NoPlotsError` instead.
      - Ensure that default properties are initialized to an empty dictionary if they are passed as None to prevent attribute errors when checking for templates.
      - Verify the fix by running the plots test suite.

=== TASK PyCQA__isort-1432 | repo PyCQA/isort | python
--- Issue
Comment incorrectly moved
When running isort over the following snippet, comments both incorrectly become `# DRY`:

```
from com.my_lovely_company.my_lovely_team.my_lovely_project.my_lovely_component import (
    MyLovelyCompanyTeamProjectComponent,  # NOT DRY
)
from com.my_lovely_company.my_lovely_team.my_lovely_project.my_lovely_component import (
    MyLovelyCompanyTeamProjectComponent as component,  # DRY
)
```
--- Real fix (for your judgement only)
diff --git a/isort/output.py b/isort/output.py
index 1a6c504b..8cf915f9 100644
--- a/isort/output.py
+++ b/isort/output.py
@@ -328,13 +328,20 @@ def _with_from_imports(
                 while from_imports and from_imports[0] in as_imports:
                     from_import = from_imports.pop(0)
                     as_imports[from_import] = sorting.naturally(as_imports[from_import])
-                    from_comments = parsed.categorized_comments["straight"].get(
-                        f"{module}.{from_import}"
+                    from_comments = (
+                        parsed.categorized_comments["straight"].get(f"{module}.{from_import}") or []
                     )
                     if (
                         parsed.imports[section]["from"][module][from_import]
                         and not only_show_as_imports
                     ):
+                        specific_comment = (
+                            parsed.categorized_comments["nested"]
+                            .get(module, {})
+                            .pop(from_import, None)
+                        )
+                        if specific_comment:
+                            from_comments.append(specific_comment)
                         output.append(
                             wrap.line(
                                 with_comments(
@@ -347,19 +354,31 @@ def _with_from_imports(
                                 config,
                             )
                         )
-                    output.extend(
-                        wrap.line(
-                            with_comments(
-                                from_comments,
-                                import_start + as_import,
-                                removed=config.ignore_comments,
-                                comment_prefix=config.comment_prefix,
-                            ),
-                            parsed.line_separator,
-                            config,
+                        from_
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
  [2] Goal: Preserve inline comments on the last imported item when formatting multiline imports
      comments in import produce invalid syntax
```
from a import b, c  # noqa
```

is transformed to:

```
from a import (
    b,
    c  # noqa,
)
```

which is of course invalid syntax
      Way: preserve inline comments on last imported item in multiline imports
      - Locate the import parsing and formatting logic, specifically where import statements are tokenized and reconstructed.
      - Refactor the parser to use a token-based approach (using regular expressions or a tokenizer) to distinguish between import keywords, names, commas, parentheses, and comments.
      - Ensure that inline comments (such as `# noqa`) are associated with their respective import items or statements, and are correctly preserved without trailing commas appended after them when formatting multiline imports.
      - Run the test suite to verify that all import formatting tests, including those with inline comments, pass successfully.
  [3] Goal: Fix binary operator relocation causing syntax errors when lines have comments
      Applying W504 may cause syntax error if there are comments
Hi, I just noticed that the following code:
```
x = (1 |
     2) # test
```
was being formatted as
```
x = (1|
     | 2) # test
```
causing a syntax error. It seems to be related to the presence of the comment.

I'll push a PR sh
      Way: Ensure comment line position matches target line when relocating binary operators
      - Locate the formatting logic responsible for PEP 8 rule W504 (relocating binary operators from end of line to beginning of next line) and inspect how inline comments are handled.
      - Reproduce the issue by formatting a multi-line expression where a binary operator is followed by a line that ends with a comment, observing whether the operator is duplicated or improperly spliced.
      - Update the token scanning logic that records comment locations to record both the row and column of the token preceding the comment.
      - Condition the target line splicing on both the presence of the comment index and whether the comment row belongs to the target line being modified (row 1 in tokenized snippet), falling back to standard splicing if the comment is on a subsequent line.
      - Run the test suite including the case with a comment on the second line to verify the syntax error is resolved.

=== TASK flyingcircusio__batou-263 | repo flyingcircusio/batou | python
--- Issue
buildout 3 test fails due to buildout / pip 21.1 incompatibility
```
Run bin/tox
py36 create: /home/runner/work/batou/batou/.tox/py36
SKIPPED: InterpreterNotFound: python3.6
py37 create: /home/runner/work/batou/batou/.tox/py37
SKIPPED: InterpreterNotFound: python3.7
py38 create: /home/runner/work/batou/batou/.tox/py38
py38 develop-inst: /home/runner/work/batou/batou
py38 installed: apipkg==1.5,attrs==21.2.0,-e git+https://github.com/flyingcircusio/batou@c811fad72083579e945d0c0eddfbdc85393f304a#egg=batou,certifi==2020.12.5,chardet==4.0.0,ConfigUpdater==2.0,coverage==5.5,execnet==1.8.0,idna==2.10,iniconfig==1.1.1,Jinja2==3.0.1,MarkupSafe==2.0.1,mock==4.0.3,packaging==20.9,pluggy==0.13.1,py==1.10.0,pyparsing==2.4.7,pytest==6.2.4,pytest-cov==2.12.0,pytest-cover==3.0.0,pytest-coverage==0.0,pytest-timeout==1.4.2,PyYAML==5.4.1,requests==2.25.1,toml==0.10.2,urllib3==1.26.4
py38 run-test-pre: PYTHONHASHSEED='3322565937'
py38 run-test: commands[0] | pytest src/batou
============================= test session starts ==============================
platform linux -- Python 3.8.10, pytest-6.2.4, py-1.10.0, pluggy-0.13.1
cachedir: .tox/py38/.pytest_cache
rootdir: /home/runner/work/batou/batou, configfile: pytest.ini
plugins: timeout-1.4.2, cov-2.12.0
timeout: 45.0s
timeout method: signal
timeout func_only: False
collected 364 items

src/batou/lib/tests/test_appenv.py ..                                    [  0%]
src/batou/lib/tests/test_archive.py .......s.             
--- Real fix (for your judgement only)
diff --git a/.pre-commit-config.yaml b/.pre-commit-config.yaml
index b3ab2188..baad4427 100644
--- a/.pre-commit-config.yaml
+++ b/.pre-commit-config.yaml
@@ -1,7 +1,7 @@
 exclude: '(components/component5/component.py|haproxy.cfg|encrypted.cfg|\.appenv|\.batou|lib/python|examples/.*/environments/.*/secret.*)'
 repos:
 -   repo: https://github.com/pre-commit/pre-commit-hooks
-    rev: v4.0.1
+    rev: v4.1.0
     hooks:
     -   id: check-yaml
     -   id: end-of-file-fixer
@@ -12,7 +12,7 @@ repos:
     -   id: isort
         name: isort (python)
 -   repo: https://github.com/pre-commit/mirrors-yapf
-    rev: 'v0.31.0'  # Use the sha / tag you want to point at
+    rev: 'v0.32.0'  # Use the sha / tag you want to point at
     hooks:
     -   id: yapf
         args: [-i, -p]
diff --git a/CHANGES.md b/CHANGES.md
index 6da4aab9..f07c0ec1 100644
--- a/CHANGES.md
+++ b/CHANGES.md
@@ -10,6 +10,9 @@
   `batou migrate`.
   ([#185](https://github.com/flyingcircusio/batou/issues/185))
 
+- Allow to pin the `pip` version used in the `Buildout` component.
+  ([#263](https://github.com/flyingcircusio/batou/issues/263))
+
 - Automatically migrate environments and secrets to the new structure using
   `./batou migrate`.
   ([#185](https://github.com/flyingcircusio/batou/issues/185))
diff --git a/doc/source/components/python.txt b/doc/source/components/python.txt
index 6c2f988e..917ef6cf 100644
--- a/doc/source/components/python.txt
+++ b/doc/source/components/python.txt
@@ -103,7 +103,8 @@ files makes it necessary. A typical usage example:
 
 .. code-block:: python
 
-    self += Buildout(python='2.7', version='2.2', setuptools='1.0',
+    self += Buildout(python='3.7', version='2.2', setuptools='1.0',
+                     pip='21.1',
                      additional_config=[Directory('profiles', source='profiles')])
 
 .. py:class:: batou.lib.buildout.Buildout()
@@ -129,6 +130,11 @@ files makes it necessary. A typical usage example:
     to the buildout version, e.g. since 2.2 bu
--- Kel's top 3
  [1] Goal: Report skipped test environments in the execution summary
      tox4: Not reporting skipped testenvs
So I trial-ran `tox==4.0.0a6` on the [latest master branch of aiosmtpd](https://github.com/aio-libs/aiosmtpd).

With tox3, I got the following summary conclusion:

```
  qa: commands succeeded
SKIPPED:  static: platform mismatch ('win32' does not match '^(?
      Way: report skipped environments during setup rather than filtering at list evaluation
      - Find where environments are filtered out or checked for platform compatibility during execution planning.
      - Remove active property filtering when building the execution environment list so all environments are scheduled.
      - Move platform checking into environment setup (`ensure_setup`) so non-matching environments raise a `Skip` exception.
      - Catch `Skip` during package dependency resolution and provision setup, logging/handling them instead of ignoring them.
      - Verify skipped environments are properly logged and reported in sequential and parallel runs.
  [2] Goal: Fix test failures and recursion depth errors on Python 3.8
      [python 3.8] failing tests: various issues but "max recursion depth reached" seems to be one
#### Description
Fedora is beginning to test python packages against python 3.8. Pydicom builds but tests fail with errors.

#### Steps/Code to Reproduce

```
python setup.py build
python setup.py ins
      Way: Prevent recursion in custom numeric subclasses str and repr methods
      - Identify numeric subclasses overriding `__str__` and `__repr__` that trigger recursion when converting instances to string representations in Python 3.8.
      - Refactor custom float subclass `__repr__` to call `super().__repr__()` wrapped in quotes rather than delegating through `str(self)`, and update `__str__` to slice quotes from `repr(self)` when no original string attribute exists.
      - Refactor custom int subclass `__repr__` and `__str__` similarly so `__repr__` invokes `super().__repr__()` and `__str__` retrieves the original string or slices quotes from `repr(self)`.
      - Run the value representation test suite to verify recursion depth errors are resolved across string and representation tests.
  [3] Goal: Do not skip missing interpreters by default when skip flag is absent
      tox4: missing interpreters are skipped by default
## Issue

tox4 skips missing interpreters and reports success even when `--skip-missing-interpreters` is not present.

## Environment

Provide at least:

- OS: macOS Ventura
- `pip list` of the host Python where `tox` is installed:

```con
      Way: Respect CLI skip missing interpreters setting via post-processing configuration
      - Locate the environment runner configuration setup where `skip_missing_interpreters` config key is registered.
      - Add a post-processing function to the `skip_missing_interpreters` configuration entry that checks if the CLI option was explicitly passed (not equal to 'config') and converts the CLI option value to boolean.
      - Ensure skipped environments handle status code appropriately when caught during run execution.
      - Run pytest on the affected test suite to verify CLI and config precedence for missing interpreters.

=== TASK samuelcolvin__pydantic-3595 | repo samuelcolvin/pydantic | python
--- Issue
V1.9 Name Collisions JSON model_as_dict
### Checks

* [ ] I added a descriptive title to this issue
* [ ] I have searched (google, github) for similar issues and couldn't find anything
* [ ] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and still think this is a bug

<!-- Sorry to sound so draconian, but every second saved replying to issues is time spend improving pydantic :-) -->

# Bug

In 1.9 Code changed in this merge #2650 The "models_as_dict" option for json has collision issues when the class names are the same but in multiple files. This is because of the change to __name__ in custom_pydantic_encoder. In the following code this will result in an error as DBUser.User does not have the field Address. If we force the encoder to be defined after the class this issue does not happen. The other option would be modification of update_forward_refs but that seems a lot worse. 

Output of `python -c "import pydantic.utils; print(pydantic.utils.version_info())"`:
```
  pydantic version: 1.9.0a2
  pydantic compiled: True
  install path: /PycharmProjects/pythonProject/venv/lib/python3.8/site-packages/pydantic
  python version: 3.8.9 (default, Oct 26 2021, 07:25:53)  [Clang 13.0.0 (clang-1300.0.29.30)]
 
 Tried it in 3.10 as well. 
 ```
<!-- or if you're using pydantic prior to v1.3, manually include: OS, python version and pydantic version -->

<!-- Please read the [docs](https://pydantic-docs.helpmanual.io/) and search through i
--- Real fix (for your judgement only)
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index 66717095d..c33c64d3a 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -138,7 +138,7 @@ jobs:
       COMPILED: no
       DEPS: yes
 
-    runs-on: ${{ format('{0}-latest', matrix.os) }}
+    runs-on: ${{ matrix.os }}-latest
     steps:
     - uses: actions/checkout@v2
 
@@ -164,8 +164,49 @@ jobs:
         name: coverage
         path: coverage
 
+  test-old-mypy:
+    name: test mypy v${{ matrix.mypy-version }}
+    runs-on: ubuntu-latest
+    strategy:
+      fail-fast: false
+      matrix:
+        mypy-version: ['0.910', '0.920', '0.921']
+
+    steps:
+    - uses: actions/checkout@v2
+
+    - name: set up python
+      uses: actions/setup-python@v2
+      with:
+        python-version: '3.10'
+
+    - name: install
+      run: |
+        make install-testing
+        pip freeze
+
+    - name: uninstall deps
+      run: pip uninstall -y mypy tomli toml
+
+    - name: install specific mypy version
+      run: pip install mypy==${{ matrix.mypy-version }}
+
+    - run: mkdir coverage
+
+    - name: run tests
+      run: pytest --cov=pydantic tests/mypy
+      env:
+        COVERAGE_FILE: coverage/.coverage.linux-py3.10-mypy${{ matrix.mypy-version }}
+        CONTEXT: linux-py3.10-mypy${{ matrix.mypy-version }}
+
+    - name: store coverage files
+      uses: actions/upload-artifact@v2
+      with:
+        name: coverage
+        path: coverage
+
   coverage-combine:
-    needs: [test-linux, test-windows-mac]
+    needs: [test-linux, test-windows-mac, test-old-mypy]
     runs-on: ubuntu-latest
 
     steps:
@@ -236,7 +277,7 @@ jobs:
 
   build:
     name: build py3.${{ matrix.python-version }} on ${{ matrix.platform || matrix.os }}
-    needs: [lint, test-linux, test-windows-mac, test-fastapi, benchmark]
+    needs: [lint, test-linux, test-windows-mac, test-old-mypy, test-fastapi, benchmark]
     if: "success() && (startsWith(github.ref, 'refs/tags/') || github.ref == 'refs/
--- Kel's top 3
  [1] Goal: Prevent validate_assignment from reordering model fields
      `validate_assignment` changes the field order
### Checks

* [*] I added a descriptive title to this issue
* [*] I have searched (google, github) for similar issues and couldn't find anything
* [*] I have read and followed [the docs](https://pydantic-docs.helpmanual.io/) and still think this is a
      Way: Preserve field order during validated assignment in Pydantic models
      - Identify the Pydantic BaseModel's `__setattr__` method as the place where field assignment validation occurs when `validate_assignment` is enabled.
      - Understand that the original implementation of `__setattr__` for validated assignment might be causing dictionary reordering by popping and re-inserting the field's value from `self.__dict__`.
      - Modify the `__setattr__` method to create a new dictionary for validation that excludes the field being assigned, instead of directly modifying `self.__dict__` before validation. This ensures the original insertion order of fields in `self.__dict__` is preserved.
      - Verify that the field order is now preserved after assignment with `validate_assignment=True` by running the provided test case.
  [2] Goal: Remove direct Pydantic dependencies from the public credential parsing API
      Forced dependency on pydantic for library users
Users of this library need to explicitly rely on symbols that are implemented in pydantic because the public interface expects pydantic instances that have to be built using `{RegistrationCredential, AuthenticationCredential}.parse_raw` (a _v1-ism_) or
      Way: Decouple credential parsing from pydantic in public API
      - Inspect public verification functions (e.g., `verify_authentication_response` and `verify_registration_response`) to understand credential input types and parsing mechanisms.
      - Create dedicated helper functions `parse_authentication_credential_json` and `parse_registration_credential_json` that encapsulate model deserialization and map Pydantic validation errors to library-level exceptions.
      - Update `verify_authentication_response` and `verify_registration_response` to accept `Union[str, CredentialType]` and automatically parse raw JSON strings using the new helper functions.
      - Export `parse_authentication_credential_json` and `parse_registration_credential_json` in the public helpers package and update documentation and examples to demonstrate JSON string input.
      - Run the test suite to ensure verification functions accept both JSON strings and model instances without throwing direct pydantic exceptions.
  [3] Goal: Fix TypeError when converting messages with optional fields to python dictionary
      TypeError in to_pydict with optional fields
See https://github.com/danielgtaylor/python-betterproto/issues/475#issuecomment-1512296041

<details>
<summary>Outdated</summary>

I am using betterproto to load a ton of messages at once from a binary file, however this operation is very slow so I wa
      Way: Handle None values when converting protobuf messages to Python dictionaries
      - Locate dictionary conversion logic in `Message.to_pydict` where nested message field serialization is handled.
      - Reproduce the `TypeError` or `AttributeError` by invoking `to_pydict(include_default_values=True)` on a message having an optional nested message field that is set to `None`.
      - In `Message.to_pydict`, add a branch checking `elif value is None:` before accessing attributes like `_serialized_on_wire` on `value`. Within this branch, set `output[cased_name] = None` if `include_default_values` is true.
      - Run the test suite to verify that optional nested fields with `None` values serialize properly without raising exceptions.

=== TASK natlibfi__skosmos-1588 | repo NatLibFi/Skosmos | php
--- Issue
Upgrade to Twig 3 for Skosmos 3
## Description of the enhancement

Twig 2 was EOL'd in December 2023. Skosmos 2 and 3 are both still using Twig 2. Skosmos 3 should upgrade to Twig 3 because it doesn't make sense to build a new application on an obsolete version.

Fortunately the required changes should be small. It's possible that only the PHP side initialization will need some adjustment. See https://symfony.com/blog/preparing-your-applications-for-twig-3

## Who are the users that would benefit from the enhancement and how?

Skosmos developers and users

## What new functionalities would the enhancement make possible?

E.g. security and other updates to Twig.

## Why is the enhancement important?

This has to be done anyway because Twig 2 is EOL. It's better to do it early to avoid extra work later.
--- Real fix (for your judgement only)
diff --git a/composer.json b/composer.json
index 966c9171..32998712 100644
--- a/composer.json
+++ b/composer.json
@@ -8,7 +8,7 @@
     "sweetrdf/easyrdf": "1.7.*",
     "symfony/polyfill-php80": "1.*",
     "symfony/polyfill-php81": "1.*",
-    "twig/twig": "^2.15.3",
+    "twig/twig": "3.8.*",
     "willdurand/negotiation": "3.1.*",
     "punic/punic": "3.5.1",
     "ml/json-ld": "1.*",
diff --git a/resource/css/skosmos.css b/resource/css/skosmos.css
index 829ecb8b..7db764c3 100644
--- a/resource/css/skosmos.css
+++ b/resource/css/skosmos.css
@@ -237,7 +237,6 @@ body {
   #search-wrapper {
     color: var(--headerbar-text-2);
     float: right;
-    height: 3.5em;
 }
 
   #search-wrapper select, #search-wrapper input, #search-wrapper button {
@@ -249,8 +248,7 @@ body {
     color: var(--headerbar-text-2);
     border: 1px solid var(--search-border);
     border-radius: 0;
-    padding-left: 0.5rem;
-    padding-right: 0.5rem;
+    width: 100px;
   }
 
   #headerbar .dropdown-toggle:hover, #headerbar .dropdown-toggle:active {
@@ -304,7 +302,7 @@ body {
     border: 1px solid var(--search-border);
     border-radius: 0;
     padding: 0;
-    width: 3.5em;
+    width: 38px;
   }
 
   #clear-button {
@@ -313,7 +311,7 @@ body {
     background: none;
     border: none;
     top: 0;
-    right: 50px;
+    right: 38px;
     height: 40px;
     width: 40px;
     padding: 0;
diff --git a/resource/js/vocab-search.js b/resource/js/vocab-search.js
index 093f8fc9..35c6a209 100644
--- a/resource/js/vocab-search.js
+++ b/resource/js/vocab-search.js
@@ -7,18 +7,17 @@ const vocabSearch = Vue.createApp({
       languages: [],
       selectedLanguage: null,
       searchTerm: null,
-      autoCompeteResults: [],
-      languageStrings: null
+      autoCompeteResults: []
     }
   },
   mounted () {
     this.languages = SKOSMOS.languageOrder
     this.selectedLanguage = SKOSMOS.content_lang
-    this.languageStrings = SKOSMOS.language_strings[SKOSMOS.lang]
   },
   methods: {
     au
--- Kel's top 3
  [1] Goal: Add Python 3 compatibility and fix relative imports
      0.4.x release with Python3 support
I'm testing twiggy on an isolated environment with `Python3.4` using `virtualenvwrapper`, here's the scenario:

## Creating the isolated environment:
```
(twiggy) username@username-VirtualBox ~/Devel/twiggy 
$ mkproject -p /usr/bin/python3 twiggy
Running virt
      Way: Fix package relative imports for Python 3 compatibility
      - Locate package initialization files where internal package modules are imported using top-level module names.
      - Reproduce the import error by importing the top-level package in Python 3.
      - Update implicit relative imports in the package initialization file to explicit relative imports using relative dot syntax.
      - Run the test suite to verify module imports and behavior pass under pytest.
  [2] Goal: Call setEscaperRuntime before adding safe classes in Twig
       call setEscaperRuntime() before calling Twig\Extension\EscaperExtension::addSafeClass().  
I just started getting this error, using Symfony ^7.0 for all the bundles.


```
 bin/console c:c -v

In EscaperExtension.php line 177:
                                                                 
      Way: Initialize EscaperExtension runtime before registering safe classes
      - Locate where the EscaperExtension is instantiated in the Twig Environment class.
      - Replace the direct instantiation with a two‑step process: create the EscaperExtension, call its new setEnvironment($this, false) method, then add it via addExtension().
      - Update EscaperExtension::setEnvironment signature to accept a second bool $triggerDeprecation parameter, wrap the deprecation trigger in an if, store the environment, and also fetch and store the EscaperRuntime via $environment->getRuntime(EscaperRuntime::class).
      - Modify addSafeClass() and setSafeClasses() to check for the presence of $this->environment instead of $this->escaper, and adjust the LogicException messages accordingly.
      - Run the full test suite to confirm the LogicException is no longer thrown and all tests pass.
  [3] Goal: Redesign schema selection and editing user interface
      UI edits to schema selection and edits saving in Wikibase extension
This has been mentioned in previous PRs and issues, but starting a new issue to track all the final adjustments needed in more detail. This is a request to improve the following UI features:
<img width="1025" alt="Screenshot 2022-1
      Way: split codeql workflows by language with path filtering
      - Identify the existing CodeQL GitHub Actions workflow that runs analysis across multiple languages in a single matrix.
      - Remove the unified CodeQL workflow file (`.github/workflows/codeql.yml`).
      - Create dedicated workflow files for each language (`codeql_java.yml` and `codeql_javascript.yml`) configuring single-language initialization (`languages: 'java'` and `languages: 'javascript'`) and path filtering under `paths` (`**/*.java` and `**/*.js`) for push and pull_request events.
      - Verify the project builds successfully and passes server and lint checks.

=== TASK serverless__serverless-2748 | repo serverless/serverless | js
--- Issue
Design Environment Variable Support
# This is a Feature Proposal

## Description

As environment variables are an often requested feature for Serverless and AWS I want to start the design process, so we can start with implementing support for Environment Variables when they become ready at AWS at some point in the future. This is also important to make sure we capture existing use cases and our design reflects that.

There are three main use cases for Environment variables:

1. Expose Service resources created by Serverless to the lambda function (e.g. S3 bucket created as an event)
2. Expose custom data from the development team as an environment variable
3. Configure environment variables for use with our future SDK so we can do service discovery in an easy way

## Proposal for config syntax

For this first proposal I want to focus on giving users the ability to define environment variables on a provider or function level. In the future we can then add additional auto-generated Environment variables.

We will create an `environment` config parameter that allows you to set environment variables on your service or lambda function. If you set it on a provider level every function will get the environment variable. You can use the full power of the Variable system to set those parameters. Of course as those should eventually be put into the CF template you should be able to use any built-in Cloudformation functions as well (e.g. reference the ARN of a custom res
--- Real fix (for your judgement only)
diff --git a/docs/providers/aws/guide/functions.md b/docs/providers/aws/guide/functions.md
index 6ead0c38d..81166a205 100644
--- a/docs/providers/aws/guide/functions.md
+++ b/docs/providers/aws/guide/functions.md
@@ -220,19 +220,38 @@ Then, when you run `serverless deploy`, VPC configuration will be deployed along
 
 ## Environment Variables
 
-We're working on great environment variable support. Until then, you'll be able to use the following tools for different languages to set environment variables and make them available to your code.
+You can add Environment Variable configuration to a specific function in `serverless.yml` by adding an `environment` object property in the function configuration. This object should contain a a key/value collection of string:string:
 
-## Javascript
-
-You can use [dotenv](https://www.npmjs.com/package/dotenv) to load files with environment variables. Those variables can be set during your CI process or locally and then packaged and deployed together with your function code.
+```yml
+# serverless.yml
+service: service-name
+provider: aws
 
-## Python
+functions:
+  hello:
+    handler: handler.hello
+    environment:
+      TABLE_NAME: tableName
+```
 
-You can use [python-dotenv](https://github.com/theskumar/python-dotenv) to load files with environment variables. Those variables can be set during your CI process or locally and then packaged and deployed together with your function code.
+Or if you want to apply Environment Variable configuration to all functions in your service, you can add the configuration to the higher level `provider` object, and overwrite these service level config at the function level. For example:
 
-## Java
+```yml
+# serverless.yml
+service: service-name
+provider:
+  name: aws
+  environment:
+    TABLE_NAME: tableName1
 
-For Java the easiest way to set up environment like configuration is through [property files](https://docs.oracle.com/javase/tutorial/essential/environment/properties.html). While 
--- Kel's top 3
  [1] Goal: Add the AWS Lambda initialization type environment variable to local emulation
      Feature request: Add AWS_LAMBDA_INITIALIZATION_TYPE environment variable
<!-- Make sure we don't have an existing Issue for the feature you are requesting (both open and closed). -->

### Describe your idea/feature/enhancement

According to the [official AWS documentation](https://docs.aws.amazo
      Way: Add AWS_LAMBDA_INITIALIZATION_TYPE to local Lambda emulation environment
      - Locate the class and method responsible for generating default AWS Lambda runtime environment variables.
      - Add 'AWS_LAMBDA_INITIALIZATION_TYPE': 'on-demand' to the dictionary of default AWS environment variables in EnvironmentVariables._get_aws_variables.
      - Run the unit tests for environment variable resolution and generation to verify the new variable is included.
  [2] Goal: Support environment variable interpolation in configuration files
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
  [3] Goal: Create a Cloudant client from VCAP services environment variable
      Implement method to create Cloudant client from VCAP services environment variable
Add an additional method to parse a `VCAP_SERVICES` environment variable allowing easy binding of a service instance to a Cloud Foundry application.

Example `VCAP_SERVICES` variable is JSON:
```json
{
    "cloud
      Way: parsing VCAP services payload directly for client configuration
      - Identify where Cloud Foundry VCAP_SERVICES configuration is read and passed to client initializers.
      - Update CloudFoundryService.__init__ to accept the VCAP_SERVICES payload as an argument, checking whether it is already a dict or needs to be parsed with json.loads.
      - Update cloudant_bluemix context manager to accept vcap_services (and instance_name) and forward them to CloudFoundryService.
      - Run the Cloud Foundry unit tests with both dict and stringified JSON payloads.

=== TASK yannickcr__eslint-plugin-react-1527 | repo yannickcr/eslint-plugin-react | js
--- Issue
jsx-no-comment-textnodes rejecting html-entity "&#x2F;&#x2F;"
The rule `jsx-no-comment-textnodes` is warning when using html-entities for slashes
(`//` -> `&#x2F;&#x2F;`)

Example:
```html
<pre>&#x2F;&#x2F; TODO: Put instructions about usage here</pre>
```
This should not warn, since it is not valid comment.
--- Real fix (for your judgement only)
diff --git a/lib/rules/jsx-no-comment-textnodes.js b/lib/rules/jsx-no-comment-textnodes.js
index 87ec1937..1dc7b1b8 100644
--- a/lib/rules/jsx-no-comment-textnodes.js
+++ b/lib/rules/jsx-no-comment-textnodes.js
@@ -34,7 +34,10 @@ module.exports = {
 
     return {
       Literal: function(node) {
-        if (/^\s*\/(\/|\*)/m.test(node.value)) {
+        const sourceCode = context.getSourceCode();
+        // since babel-eslint has the wrong node.raw, we'll get the source text
+        const rawValue = sourceCode.getText(node);
+        if (/^\s*\/(\/|\*)/m.test(rawValue)) {
           // inside component, e.g. <div>literal</div>
           if (node.parent.type !== 'JSXAttribute' &&
               node.parent.type !== 'JSXExpressionContainer' &&

--- Kel's top 3
  [1] Goal: Prevent false positive comment textnode warnings on URLs in JSX
      `no-comment-textnodes` false positive on URL
I have JSX like the following:
```js
<strong>
  &nbsp;https://www.example.com/attachment/download/1
</strong>
```

Since the line doesn't start with `//` or `/*`, there shouldn't be any way this is identified as an accidental leaked comment.
      Way: Anchor comment detection regex to start of line in multiline JSX literals
      - Identify where JSX text literal contents are evaluated for comment markers like '//' or '/*'.
      - Reproduce the issue by adding a test case containing a URL (e.g., 'https://...') or non-leading '//' inside JSX text nodes.
      - Update the RegExp that inspects literal node values by adding a line anchor '^' with the multiline flag 'm' (/^\s*\/(\/|\*)/m) to ensure comments are detected only at line starts.
      - In jsx-filename-extension rule, guard against linting mock input files where context.getFilename() equals '<text>'.
      - Run test suite to verify tests pass and URLs in JSX text nodes do not trigger false positive warnings.
  [2] Goal: Remove extra newline and indent for JSX expression containers with comments
      Extra newline and indent for JSXExpressionContainer with a dangling comment
Notice the extra indent and an extra newline.

```js
<div>
  {
    /* mimic the table layout */
  }
</div>
```

prints

```js
<div>
  {

      /* mimic the table layout */
  }
</div>;
```

https://jlongs
      Way: Fix JSX comment formatting and comment attachment logic
      - Locate the code that attaches comments to the AST and the printer logic for JSX expression containers.
      - Modify comment attachment: delete the original ast.comments property before attaching, ignore EmptyStatement nodes when sorting child nodes, and add a new handler for TryStatement comments.
      - Update the printer: group the braces of JSXExpressionContainer, print dangling comments for JSXEmptyExpression followed by a softline, and ensure StringLiteral expression statements are parenthesized.
      - Run the Prettier test suite focusing on JSX and comment related tests.
  [3] Goal: Report syntax error for unclosed multi-line comments at EOF
      複数行コメントの途中でEOFになってもエラーにならない
以下のように、複数行コメントの途中でEOFとなるコードが文法エラーにならない。
```js
/* comment
```

ただし、EOFの直前が`*`である場合は、「end of stream」エラーが出る。
```js
/* *
```
      Way: Detect and report unclosed multi-line comments at EOF
      - Locate the scanner or lexer component responsible for parsing multi-line comments (e.g., `/* ... */`).
      - Reproduce the issue by providing input code that contains an unclosed multi-line comment at the end of the file, such as `/* comment` or `/* *`.
      - Modify the comment parsing logic to explicitly check for End-Of-File (EOF) within the comment scanning loop. If EOF is encountered before the closing `*/` sequence, throw an `UnexpectedEOFError`.
      - Ensure that EOF is checked both at the start of each iteration of the comment scanning loop and immediately after consuming a potential `*` character, to catch cases where `*` is the last character before EOF.
      - Run the relevant syntax tests to confirm that unclosed multi-line comments now correctly report an error.

=== TASK mozilla__node-convict-234 | repo mozilla/node-convict | js
--- Issue
Object config values result in warning about undeclared properties
Hello,

I would like to suggest a small improvement over #213 from @mmcgahan.

We use multiple custom formats that are in the end nothing more than objects that we would like to validate in a specific way. We are receiving warnings when loading the base schema and not even loading configurations on top of it. Here's an example:

```javascript
const convict = require('convict');

const schema = {
  foo: {
    format: 'bar',
    default: {
      baz: 'blah',
    },
  },
};

convict.addFormats({ bar: { validate() {} } });
const config = convict(schema);
config.validate();
// Warning: configuration param 'foo.baz' not declared in the schema
```

Since `foo` is after all an object, we believe that the warning behavior there should be the same than for the format `Object`. Is there a known fix for that issue?

Otherwise, please have a look at #234.
  
--- Real fix (for your judgement only)
diff --git a/README.md b/README.md
index 02b518c..1db78e0 100644
--- a/README.md
+++ b/README.md
@@ -191,6 +191,21 @@ var config = convict({
       }
     },
     default: '3cec609c9bc601c047af917a544645c50caf8cd606806b4e0a23312441014deb'
+  },
+  name: {
+    doc: "user name",
+    format: function check (val) {
+      if (typeof val.first_name !== 'string') {
+        throw new TypeError(`first name '${val.first_name}' is not a string`);
+      }
+      if (typeof val.last_name !== 'string') {
+        throw new TypeError(`last name '${val.last_name}' is not a string`);
+      }
+    },
+    default: {
+      first_name: 'John',
+      last_name: 'Doe'
+    }
   }
 });
 ```
diff --git a/lib/convict.js b/lib/convict.js
index c2384a1..2b85585 100644
--- a/lib/convict.js
+++ b/lib/convict.js
@@ -171,8 +171,8 @@ function validate(instance, schema, strictValidation) {
     }
     delete flatInstance[name];
 
-    // ignore nested keys of 'object' config params
-    if (schemaItem.format === 'object') {
+    // ignore nested keys of schema 'object' properties
+    if (schemaItem.format === 'object' || typeof schemaItem.default === 'object') {
       Object.keys(flatInstance)
         .filter(function(key) {
           return key.lastIndexOf(name + '.', 0) === 0;

--- Kel's top 3
  [1] Goal: Warn on unknown configuration keys in JSON config files
      Warn on unknown config keys
If a config file is well-formed json but the key is misspelled, a warning should be issued.
      Way: warn on unknown configuration keys by popping consumed entries
      - Locate configuration loading logic and dictionary lookup utilities.
      - Extend configuration lookup methods with an option to remove keys upon access.
      - Update configuration initialiser to consume known keys using `remove=True` and track known environment variables.
      - Issue warnings via `warnings.warn` if remaining unconsumed keys or environment variables are non-empty after initialization.
      - Run configuration unit tests to verify warnings are issued for unknown keys.
  [2] Goal: Validate required schema properties when they are missing from properties object
      Required property not set in properties
<!--
Before you submit an issue we recommend you visit [Fastify Help](https://github.com/fastify/help) and ask any questions you have or mention any problems you've had getting started with Fastify.

**Please read this entire template before posting any iss
      Way: validate required schema properties missing from properties definition
      - Inspect schema compilation code where object properties and required fields are handled to see if required checks only run during iteration over `schema.properties`.
      - Add test cases where a field listed in `required` is absent from `properties` under both `additionalProperties: true` and `additionalProperties: false`.
      - Track unhandled required keys by copying `schema.required` and filtering out keys handled when iterating over `schema.properties` (including those with defaults or explicit property checks).
      - Generate runtime validation code after the property loop that iterates over any remaining required keys and throws an error if `obj[key] === undefined`.
      - Execute the test suite to verify that missing required properties throw errors even when omitted from `properties`.
  [3] Goal: Fix TypeError when logging warning objects
      TypeError: Cannot read property 'labels' of undefined
```js
//test.js
const pino = require('pino')();

process.on('warning', (warning) => {
  pino.info(warning);
});
process.on('foo', () => {});
process.on('foo', () => {})
process.on('foo', () => {});
process.on('foo', () => {})
process.o
      Way: safely access prototype properties and isolate logger instance prototypes
      - Reproduce the TypeError when logging objects (like warning errors) that trigger getter serialization under complex scopes.
      - Inspect level getter functions in the level helper module to add defensive null/undefined checks before indexing into labels mapping.
      - Change the prototype export from a static singleton object to a factory function returning `Object.create(prototype)` so that each logger instance receives an isolated prototype scope.
      - Update logger instantiation to call the prototype factory when setting the logger instance's prototype via `Object.setPrototypeOf`.
      - Run the test suite to verify that level handling and logging functions pass without crashing.

=== TASK mantoni__eslint_d.js-323 | repo mantoni/eslint_d.js | js
--- Issue
Debug mode
I sometimes have issues with eslint_d. To understand what is happening, I'm adding some log instructions to the code. It would be nice if eslint_d had a debug mode builtin. This can be done by writing log to stderr if that doesn't conflict with the client/daemon communication. Otherwise, this can be done by writing log to a temporary file.
--- Real fix (for your judgement only)
diff --git a/README.md b/README.md
index ab6dd43..24c0243 100644
--- a/README.md
+++ b/README.md
@@ -174,6 +174,26 @@ The server is also automatically restarted if one of the following files
 changed: `package.json`, `package-lock.json`, `npm-shrinkwrap.json`,
 `yarn.lock`, `pnpm-lock.yaml`.
 
+## Debug mode
+
+> Added in `v14.2.0`.
+
+Pass the `--debug` flag to `eslint_d` to enable debug output. Use the `DEBUG`
+environment variable to limit debug output to `eslint_d`:
+
+```bash
+❯ DEBUG="eslint_d:*"
+```
+
+For server side debug output, restart with `--debug` in a separate terminal:
+
+```bash
+❯ eslint_d restart --debug # eslint and eslint_d logs
+❯ DEBUG="eslint_d:*" eslint_d restart --debug # eslint_d logs only
+```
+
+This will keep the process attached to the terminal and print debug output.
+
 ## Compatibility
 
 - `14.0.0`: eslint 4 - 9, node 18 - 22 (ships with eslint 9) (see [^1])
diff --git a/bin/eslint_d.js b/bin/eslint_d.js
index a000910..8755bf0 100755
--- a/bin/eslint_d.js
+++ b/bin/eslint_d.js
@@ -1,11 +1,19 @@
 #!/usr/bin/env node
 
+import debug from 'debug';
 import { loadConfig, removeConfig } from '../lib/config.js';
 import { createResolver } from '../lib/resolver.js';
 import { forwardToDaemon, isAlive } from '../lib/forwarder.js';
 import { launchDaemon, stopDaemon } from '../lib/launcher.js';
 import { filesHash } from '../lib/hash.js';
 
+const is_debug_mode = process.argv.includes('--debug');
+if (is_debug_mode) {
+  debug.enable(
+    process.env.DEBUG || 'eslint_d:*,eslint:*,-eslint:code-path,eslintrc:*'
+  );
+}
+
 const command = process.argv[2];
 
 (async () => {
@@ -40,7 +48,7 @@ const command = process.argv[2];
         await removeConfig(resolver);
       }
 
-      await launchDaemon(resolver, hash);
+      await launchDaemon(resolver, hash, is_debug_mode);
 
       return;
     case 'stop':
@@ -54,7 +62,7 @@ const command = process.argv[2];
       if (config) {
         await stopDaemon(resolver, config);
       }
-      await 
--- Kel's top 3
  [1] Goal: Prevent rule disable comments from working inside shebang lines
      eslint-disable-next-line works in shebang comments
<!--
    ESLint adheres to the [JS Foundation Code of Conduct](https://js.foundation/community/code-of-conduct).

    This template is for bug reports. If you are reporting a bug, please continue on. If you are here for another reason, please see
      Way: Filter shebangs from ESLint directive processing
      - Understand that ESLint was incorrectly interpreting shebang comments as `eslint-disable` directives because the AST now includes shebangs in its `comments` array.
      - Locate the code responsible for iterating through `ast.comments` and processing them for ESLint directives. The issue points to `lib/linter.js` within the `modifyConfigsFromComments` function.
      - Reproduce the issue by running ESLint with a file containing a shebang like `#! eslint-disable-next-line` followed by code that should trigger an error (e.g., `foo;` with `no-undef` enabled). Observe that no error is reported.
      - Modify the iteration over `ast.comments` to filter out tokens of type `Shebang` before they are processed as potential ESLint directives. This ensures only actual line or block comments are considered for directives.
      - Verify that the fix prevents shebang comments from disabling rules by running the provided tests.
  [2] Goal: Make ESLint recognize multiline eslint-env directives
      Linter ignores multiline `/* eslint-env */` directives
<!--
    ESLint adheres to the Open JS Foundation Code of Conduct:
    https://eslint.org/conduct

    This template is for bug reports. If you are here for another reason, please see below:

    1. To propose a new rule: https://eslint.or
      Way: Enable multiline ESLint environment directives
      - Locate the regular expression responsible for parsing `eslint-env` directives in the linter's core logic.
      - Reproduce the issue by running ESLint on a file with a multiline `/* eslint-env */` directive that defines a global variable, and observe a `no-undef` error for that global.
      - Modify the `eslintEnvPattern` regular expression by adding the `s` (dotAll) flag. This flag allows the `.` character in the regex to match newline characters, enabling it to correctly parse multiline comments.
      - Verify that ESLint now correctly recognizes and applies the `eslint-env` directive from multiline comments, resolving `no-undef` errors for globals defined this way.
  [3] Goal: Prevent ESLint rule crash on missing meta property
      Internal consistent-docs-url rule crashes if meta isn't present
**Tell us about your environment**

* **ESLint Version:** master
* **Node Version:** n/a
* **npm Version:** n/a

**What parser (default, Babel-ESLint, etc.) are you using?**

Default

**Please show your full configuration:**

      Way: Safely check object properties using Object.prototype.hasOwnProperty.call
      - Identify code that checks for the existence of properties on objects using `object.hasOwnProperty(property)`.
      - Recognize that direct calls to `object.hasOwnProperty(property)` can fail if `object` is `null` or `undefined`, or if `hasOwnProperty` is shadowed on the object's prototype chain.
      - Replace direct calls to `object.hasOwnProperty(property)` with `Object.prototype.hasOwnProperty.call(object, property)`.
      - Verify that the change prevents crashes related to accessing properties on `undefined` or `null` objects, or issues with shadowed `hasOwnProperty` methods.

=== TASK gocql__gocql-1368 | repo gocql/gocql | go
--- Issue
cancellation of a single query affects multiple queries in the same session

### What version of Cassandra are you using?
3.4.0
### What version of Gocql are you using?
latest master at https://github.com/gocql/gocql/commit/df4b9cc330307b4a196f255fc0d1e8a53cabfbcd
### What did you do?
- start a new cassandra session
- run a statement in multiple queries multiple times concurrently in multiple Go routines each.
- statements created with `session.Query(selectStatement, x).WithContext(ctx)`
- each statement gets a different `ctx`
- one of thoese `ctx` variables is an already canceled context
### What did you expect to see?
Only the statement with the canceled context should receive the cancellation error.
### What did you see instead?
All statements running concurrently with the cancelled statement get cancelled.
--- Real fix (for your judgement only)
diff --git a/conn.go b/conn.go
index 553292a..209eddf 100644
--- a/conn.go
+++ b/conn.go
@@ -155,25 +155,26 @@ type Conn struct {
 	session *Session
 
 	closed int32
-	quit   chan struct{}
+	ctx    context.Context
+	cancel context.CancelFunc
 
 	timeouts int64
 }
 
 // connect establishes a connection to a Cassandra node using session's connection config.
-func (s *Session) connect(host *HostInfo, errorHandler ConnErrorHandler) (*Conn, error) {
-	return s.dial(host, s.connCfg, errorHandler)
+func (s *Session) connect(ctx context.Context, host *HostInfo, errorHandler ConnErrorHandler) (*Conn, error) {
+	return s.dial(ctx, host, s.connCfg, errorHandler)
 }
 
 // dial establishes a connection to a Cassandra node and notifies the session's connectObserver.
-func (s *Session) dial(host *HostInfo, connConfig *ConnConfig, errorHandler ConnErrorHandler) (*Conn, error) {
+func (s *Session) dial(ctx context.Context, host *HostInfo, connConfig *ConnConfig, errorHandler ConnErrorHandler) (*Conn, error) {
 	var obs ObservedConnect
 	if s.connectObserver != nil {
 		obs.Host = host
 		obs.Start = time.Now()
 	}
 
-	conn, err := s.dialWithoutObserver(host, connConfig, errorHandler)
+	conn, err := s.dialWithoutObserver(ctx, host, connConfig, errorHandler)
 
 	if s.connectObserver != nil {
 		obs.End = time.Now()
@@ -187,7 +188,7 @@ func (s *Session) dial(host *HostInfo, connConfig *ConnConfig, errorHandler Conn
 // dialWithoutObserver establishes connection to a Cassandra node.
 //
 // dialWithoutObserver does not notify the connection observer, so you most probably want to call dial() instead.
-func (s *Session) dialWithoutObserver(host *HostInfo, cfg *ConnConfig, errorHandler ConnErrorHandler) (*Conn, error) {
+func (s *Session) dialWithoutObserver(ctx context.Context, host *HostInfo, cfg *ConnConfig, errorHandler ConnErrorHandler) (*Conn, error) {
 	ip := host.ConnectAddress()
 	port := host.port
 
@@ -198,11 +199,6 @@ func (s *Session) dialWithoutObserver(host *HostInfo, cfg *C
--- Kel's top 3
  [1] Goal: Minimize CPU and allocation overhead of round robin host policy
      roundRobinHostPolicy uses lots of resources for every query
Please answer these questions before submitting your issue. Thanks!

### What version of Cassandra are you using?
3.11.5

### What version of Gocql are you using?
1982a06ad6b987c24beec15336019914d71b2e31

### What did you do?
Due t
      Way: None

  [2] Goal: Include paused executions in concurrency limits for JDBC queue
      Paused execution state should count to concurrency limits on JDBC
### Explain the bug

Currently, Paused state still doesn't count to concurrency limits as it should, at least on JDBC 

## Reproducer

```yaml
id: flow-paused-concurrency
namespace: dev
concurrency:
  limit: 1
  behavior: C
      Way: Account for paused executions in concurrency limits
      - Identify the component responsible for enforcing concurrency limits on execution processing. This is typically an `ExecutorService` or similar component that processes execution states.
      - Examine the condition that determines if an executor can be processed. The issue states that paused executions are not counted, implying the current condition excludes them.
      - Modify the processing condition to include executions that are considered 'terminated with listeners', which encompasses paused states that should consume a concurrency slot. This ensures that such executions are not re-processed or ignored by the concurrency logic.
      - Verify that the `concurrencyCancelPause` test now passes, confirming that paused executions are correctly factored into concurrency limits.
  [3] Goal: Ignore empty SQL statements caused by trailing or multiple consecutive semicolons
      InvalidSyntaxException When SQL Statement Ends with Multiple Semicolons
**Describe the bug**
When a SQL statement ends with multiple semicolons, it will be split to several statements with all of them other than the first one contain a semicolon only. And those semicolon only statement triggers Inv
      Way: Filter empty semicolon-only statements in sqlparse splitter
      - Locate the SQL statement splitting helper where input SQL scripts are broken into individual statements.
      - Reproduce the issue by running statement splitting or lineage analysis on SQL input containing multiple consecutive semicolons.
      - Update the split function to check the first non-comment token (`token_first(skip_cm=True)`) of each parsed statement segment and discard segments whose first token is punctuation with value ';'.
      - Run the test suite to verify semicolon-only statements are skipped and no InvalidSyntaxException is raised.

=== TASK natcap__invest-1838 | repo natcap/invest | python
--- Issue
Habitat Quality handle empty table fields
A user posted an issue on the [forums](https://community.naturalcapitalproject.org/t/habitat-quality-valueerror-could-not-convert-string-to-float/2765) where they were getting the following error:

```python
2022-05-04 16:55:57,649 (natcap.invest.habitat_quality) habitat_quality.execute(259) INFO Starting execute of Habitat Quality model.
2022-05-04 16:55:57,649 (natcap.invest.habitat_quality) habitat_quality.execute(265) INFO Creating workspace
2022-05-04 16:55:57,688 (natcap.invest.habitat_quality) habitat_quality.execute(278) INFO Checking Threat and Sensitivity tables for compliance
2022-05-04 16:55:57,700 (natcap.invest.utils) utils.prepare_workspace(167) ERROR Exception while executing Habitat-Quality
Traceback (most recent call last):
  File "natcap\invest\utils.py", line 165, in prepare_workspace
  File "natcap\invest\ui\model.py", line 1649, in _logged_target
  File "natcap\invest\habitat_quality.py", line 293, in execute
  File "natcap\invest\habitat_quality.py", line 294, in <dictcomp>
ValueError: could not convert string to float: ''
2022-05-04 16:55:57,701 (natcap.invest.utils) utils.prepare_workspace(170) INFO Elapsed time: 0.06s
2022-05-04 16:55:57,701 (natcap.invest.utils) utils.prepare_workspace(173) INFO Execution finished
```

If this is just a case of the threat or sensitivity tables having an empty field, I feel like we could provide a better error message there.

Error reported using InVEST 3.1
--- Real fix (for your judgement only)
diff --git a/src/natcap/invest/habitat_quality.py b/src/natcap/invest/habitat_quality.py
index 813e94f8c..fdb1865e5 100644
--- a/src/natcap/invest/habitat_quality.py
+++ b/src/natcap/invest/habitat_quality.py
@@ -26,6 +26,12 @@ MISSING_THREAT_RASTER_MSG = gettext(
     "A threat raster for threats: {threat_list} was not found or it "
     "could not be opened by GDAL.")
 DUPLICATE_PATHS_MSG = gettext("Threat paths must be unique. Duplicates: ")
+INVALID_MAX_DIST_MSG = gettext(
+    "The maximum distance value for threats: {threat_list} is less than "
+    "or equal to 0. MAX_DIST must be a positive value.")
+MISSING_MAX_DIST_MSG = gettext(
+    "Maximum distance value is missing for threats: {threat_list}.")
+MISSING_WEIGHT_MSG = gettext("Weight value is missing for threats: {threat_list}.")
 
 MODEL_SPEC = {
     "model_id": "habitat_quality",
@@ -1185,6 +1191,41 @@ def validate(args, limit_to=None):
 
             invalid_keys.add('sensitivity_table_path')
 
+        # check that max_dist and weight values are included in the
+        # threats table and that max_dist >= 0
+        invalid_max_dist = []
+        missing_max_dist = []
+        missing_weight = []
+        for threat, row in threat_df.iterrows():
+            if row['max_dist'] == '':
+                missing_max_dist.append(threat)
+            elif row['max_dist'] <= 0:
+                invalid_max_dist.append(threat)
+
+            if row['weight'] == '':
+                missing_weight.append(threat)
+
+        if invalid_max_dist:
+            validation_warnings.append((
+                ['threats_table_path'],
+                INVALID_MAX_DIST_MSG.format(threat_list=invalid_max_dist)
+            ))
+
+        if missing_max_dist:
+            validation_warnings.append((
+                ['threats_table_path'],
+                MISSING_MAX_DIST_MSG.format(threat_list=missing_max_dist)
+            ))
+
+        if missing_weight:
+            validation_warnings.append((
+                ['t
--- Kel's top 3
  [1] Goal: Allow float values for schema minimum and maximum validations
      openapi3.errors.SpecError when minimum/maximum validation is float
The following file::

<details><summary><code>openapi.json</code></summary>

```json
{
    "openapi": "3.0.3",
    "info": {
        "title": "Foo",
        "version": "1.0.0"
    },
    "paths": {
        "/foo": {
    
      Way: accepting float types in schema numeric boundary parsing
      - Inspect the schema parsing logic where numeric boundary keywords such as 'minimum' and 'maximum' are extracted and type-checked.
      - Reproduce the parsing failure by loading an OpenAPI document containing a schema with float values for 'minimum' or 'maximum'.
      - Update the type validation for 'minimum' and 'maximum' fields in the Schema parser to accept both int and float types instead of only int.
      - Run the test suite to verify that schemas with float minimum and maximum values parse without raising SpecError.
  [2] Goal: Prevent false blank-header and missing-value errors during structure-only table validation
      Validation report gives blank-header and missing-value, when validating 'structure' for valid csv
# Overview

When trying to validate on the structure (no schema validation involved) for a source csv, we are getting `blank-header` and `missing-value` errors, when the structure of the csv is valid.
      Way: mark virtual schema-padded cells to skip structure error checks
      - Identify where table cells are constructed from zip-padded header, schema, and row values.
      - Reproduce the issue by validating a table where schema fields exceed source headers using only structure checks, observing unexpected blank-header and missing-value errors.
      - Detect virtual cells when both header and value equal the zip fillvalue, and store an 'is-virtual' flag on the cell dict.
      - Update the blank-header and missing-value check functions to skip cells that have 'is-virtual' set.
      - Run the test suite to confirm that structure validation on schema-padded tables no longer triggers blank-header or missing-value errors.
  [3] Goal: Make dataset generated metadata field optional on client
      [BUG] Empty statistics from a data owner causes a global error
**Describe the bug**
The `generated_metadata` field of datasets in the client is set to be required:
https://github.com/mlcommons/medperf/blob/09edc70bcf7f1615c532c8f8b15f10de9b406e33/cli/medperf/entities/dataset.py#L30
However, on th
      Way: Enforce non-null JSON metadata and handle empty statistics
      - Identify the client-side validation logic that fails when `generated_metadata` is null or empty, specifically when listing datasets.
      - Locate the server-side model definition for `generated_metadata` to understand its current allowance for null values.
      - Modify the server-side `Dataset` model to disallow `null` for the `generated_metadata` field, ensuring it defaults to an empty dictionary instead of `null`.
      - Generate and apply a database migration to reflect the change in the `generated_metadata` field's nullability and default value on the server.
      - Add a check during client-side dataset preparation to explicitly prevent an empty statistics file from being processed, raising an error if the loaded statistics are `None`.
      - Verify that the client-side preparation now correctly handles empty statistics files and that the overall system no longer encounters validation errors for datasets with previously null `generated_metadata`.

=== TASK vaskoz__dailycodingproblem-go-12 | repo vaskoz/dailycodingproblem-go | go
--- Issue
Day 5 extra - solve hard interview problems email
The question we'll work through is the following: **return a new sorted merged list from K sorted lists, each with size N.** Before we move on any further, you should take some time to think about the solution!
--- Real fix (for your judgement only)
diff --git a/README.md b/README.md
index 29f92b7..764ff76 100644
--- a/README.md
+++ b/README.md
@@ -14,4 +14,5 @@ problems from
 * [Day 3](https://github.com/vaskoz/dailycodingproblem-go/issues/7)
 * [Day 4](https://github.com/vaskoz/dailycodingproblem-go/issues/5)
 * [Day 5](https://github.com/vaskoz/dailycodingproblem-go/issues/9)
+* [Day 5 merge k sorted lists](https://github.com/vaskoz/dailycodingproblem-go/issues/11)
 
diff --git a/mergeKSortedLists/problem.go b/mergeKSortedLists/problem.go
new file mode 100644
index 0000000..be37516
--- /dev/null
+++ b/mergeKSortedLists/problem.go
@@ -0,0 +1,86 @@
+package mergeklist
+
+import (
+	"container/heap"
+	"math"
+)
+
+// MergeKSortedLists merges K-sorted lists into a single list
+// Runtime is O(K*(K*N)) = O(K^2*N) assuming each list of is length N
+// Space is O(K) for the indicies without considering the answer list of O(K*N) size.
+// Use this if K is very small
+func MergeKSortedLists(lists [][]int) []int {
+	idxs := make([]int64, len(lists))
+	var result []int
+	hasMore := true
+	for hasMore {
+		listID := -1
+		smallest := math.MaxInt64
+		// find the smallest next value
+		for i, idx := range idxs {
+			if idx < int64(len(lists[i])) && lists[i][idx] < smallest {
+				smallest = lists[i][idx]
+				listID = i
+			}
+		}
+		if listID == -1 {
+			hasMore = false
+		} else {
+			result = append(result, smallest)
+			idxs[listID]++
+		}
+	}
+	return result
+}
+
+type tuple struct {
+	id, pos, value int
+}
+
+type priorityQueue []*tuple
+
+func (pq priorityQueue) Len() int { return len(pq) }
+
+func (pq priorityQueue) Less(i, j int) bool {
+	return pq[i].value < pq[j].value
+}
+
+func (pq priorityQueue) Swap(i, j int) {
+	pq[i], pq[j] = pq[j], pq[i]
+}
+
+func (pq *priorityQueue) Push(x interface{}) {
+	item := x.(*tuple)
+	*pq = append(*pq, item)
+}
+
+func (pq *priorityQueue) Pop() interface{} {
+	old := *pq
+	n := len(old)
+	tuple := old[n-1]
+	*pq = old[0 : n-1]
+	return tuple
+}
+
+// MergeKSortedListsUsingHea
--- Kel's top 3
  [1] Goal: Return sorted squares of a sorted integer array
      Day 118
Good morning! Here's your coding interview problem for today.

This problem was asked by Google.

Given a sorted list of integers, square the elements and give the output in sorted order.

For example, given `[-9, -2, 0, 2, 3]`, return `[0, 4, 4, 9, 81]`.
      Way: Add sorted‑squares functions with brute and linear implementations
      - Locate the missing Day 118 solution file and create a new Go source file for the package.
      - Implement SquareElementsSortedBrute: allocate a result slice, square each input element, then sort the slice with sort.Ints.
      - Implement SquareElementsSortedLinear: find the first non‑negative index, then merge the squared values from the negative side (moving left) and the non‑negative side (moving right) into a pre‑allocated result slice.
      - Add the new Day 118 entry to the README list of problems.
      - Run the Go test suite for the day118 package to verify both functions produce the expected sorted squares.
  [2] Goal: Support unhashable items like dicts in unique list merge strategy
      list merge strategy append_unique does not work for lists of dicts
Hi developers, especially @morph027 ,

I get an error when trying to apply list merge strategy `append_unique` for lists of dictionaries.
I am using deepmerge 1.0.1 and python 3.7.7.

When I am running the following code
```pyt
      Way: support unhashable items in unique list merge
      - Inspect list merge strategies that rely on set operations (such as append_unique) to find where converting lists to `set` causes `TypeError: unhashable type` for unhashable elements like dicts.
      - Implement a custom set-like container class (e.g. `ExtendedSet`) that checks if an element's `__hash__` attribute is None, falling back to hashing its string representation (`hash(str(element))`) if unhashable, and supporting membership testing via `__contains__`.
      - Update `strategy_append_unique` in the list strategy implementation to instantiate `ExtendedSet(base)` instead of `set(base)` for filtering duplicate incoming items.
      - Add unit tests verifying `append_unique` correctly handles lists containing unhashable items such as nested dictionaries without throwing TypeError.
      - Run the test suite to verify tests with nested dicts in append_unique pass.
  [3] Goal: Convert sorted array to height-balanced binary search tree
      Day 296
Good morning! Here's your coding interview problem for today.

This problem was asked by Etsy.

Given a sorted array, convert it into a height-balanced binary search tree.
      Way: Construct height-balanced BST from sorted slice
      - Understand the problem: create a height-balanced Binary Search Tree (BST) from a sorted array or slice. A height-balanced BST ensures that the difference in height between the left and right subtrees of any node is at most one.
      - Determine the algorithm: use a recursive divide-and-conquer strategy. The middle element of the sorted input becomes the root of the current subtree. The elements to its left form the left subtree, and elements to its right form the right subtree. This process is applied recursively.
      - Implement the base case for the recursion: if the input slice is empty, return `nil` (or the equivalent for an empty tree) as there are no nodes to form a subtree.
      - Implement the recursive step: calculate the middle index of the current slice. Create a new BST node with the value at this middle index. Recursively call the function with the sub-slice to the left of the middle element to build the left child, and with the sub-slice to the right of the middle element to build the right child.
      - Run the provided tests to ensure the implementation correctly produces a height-balanced BST from the sorted input.

=== TASK obsidian-tasks-group__obsidian-tasks-2486 | repo obsidian-tasks-group/obsidian-tasks | ts
--- Issue
Postponing of tasks only works once
### Please check that this issue hasn't been reported before.

- [X] I searched previous [Bug Reports](https://github.com/obsidian-tasks-group/obsidian-tasks/issues?q=is%3Aopen+is%3Aissue+label%3A%22type%3A+bug%22+sort%3Acomments-desc) didn't find any similar reports.

### Expected Behavior

The new 'postpone' button should allow postponing a task multiple times.

### Current behaviour

After the first postponement of a task, any subsequent attempts to postpone it give an error.


### Steps to reproduce


1. In a vault with the development version of tasks build - such as version bb479979619d4ca745920dbd65906f39383837f9....
1. Create a file called, for example, `Postpone Tests.md`
2. Paste in this content:

~~~
## Task

- [ ] #task Do stuff 📅 2025-12-02

## Search

```tasks
path includes {{query.file.path}}
```
~~~

4. View in Live Preview or Reading mode

![image](https://github.com/obsidian-tasks-group/obsidian-tasks/assets/4840096/9bdf29fe-dd6e-45a3-a4b1-31507980a712)

5. Click on the Postpone icon at the end of the Task in the query results
6. **Check**:
    - the date should advance to 2025-12-03 in the markdown line
    - the date should advance to 2025-12-03 in the task in the tasks results
    - a success message should be displayed:
        ![image](https://github.com/obsidian-tasks-group/obsidian-tasks/assets/4840096/f2800ddf-d895-4233-940e-81a5fd9f03e2)

7. Click on the Postpone icon at the end of the
--- Real fix (for your judgement only)
diff --git a/src/Scripting/TasksDate.ts b/src/Scripting/TasksDate.ts
index fbb7ecc6..a9384c3f 100644
--- a/src/Scripting/TasksDate.ts
+++ b/src/Scripting/TasksDate.ts
@@ -122,6 +122,6 @@ export class TasksDate {
             return today.add(amount, unitOfTime);
         }
 
-        return this._date.add(amount, unitOfTime);
+        return this._date.clone().add(amount, unitOfTime);
     }
 }

--- Kel's top 3
  [1] Goal: Ensure custom task statuses persist across sessions
      unreleased bug: Some "refactoring" broke reading of status settings
### Please check that this issue hasn't been reported before.

- [X] I searched previous [Bug Reports](https://github.com/obsidian-tasks-group/obsidian-tasks/issues?q=is%3Aopen+is%3Aissue+label%3A%22type%3A+bug%22+sort%3Acomments-de
      Way: Refactor status configuration methods to prevent deserialization issues
      - Identify the code responsible for loading and using custom status settings. The issue description points to `StatusConfiguration` objects losing methods after deserialization from JSON.
      - Reproduce the bug by adding custom statuses, restarting the application, and observing that the settings are lost or cause errors, as described in the issue.
      - Move validation methods (e.g., `validateIndicator`, `validateName`, `validateNextIndicator`, `validate`) from `StatusConfiguration` to a new `StatusValidator` class. Instantiate `StatusValidator` and call its methods, passing the `StatusConfiguration` object as an argument.
      - Move static factory methods (e.g., `makeTodo`, `makeInProgress`, `makeDone`, `makeCancelled`, `makeEmpty`) from `StatusConfiguration` to the `Status` class. Update call sites to use `Status.makeX().configuration` where a `StatusConfiguration` object is needed, or `Status.makeX()` where a `Status` object is needed.
      - Move other utility methods (e.g., `tasksPluginCanCreateCommandsForStatuses`, `previewText`) from `StatusConfiguration` to the `Status` class. Update call sites to reflect these changes, potentially by creating a `Status` instance from a `StatusConfiguration` object if necessary.
      - Ensure that the `Status` class correctly encapsulates the `StatusConfiguration` data and provides the necessary methods, while `StatusConfiguration` becomes primarily a data structure.
  [2] Goal: Sort tasks with invalid dates before tasks with valid dates
      `sort by due` and similar should put tasks with invalid dates before the tasks with valid dates.
### Please check that this issue hasn't been reported before.

- [X] I searched previous [Bug Reports](https://github.com/obsidian-tasks-group/obsidian-tasks/issues?q=is%3Aopen+is%3Aissue+label%3A%22type
      Way: Prioritize invalid dates in task sorting
      - Locate the `compareByDate` function, which is responsible for comparing two dates for sorting purposes. This function is typically found in a date utility file.
      - Reproduce the incorrect sorting behavior by creating tasks with invalid, valid, and no dates, and then applying a date-based sort (e.g., `sort by due`).
      - Modify the `compareByDate` function to change the comparison logic when one date is valid and the other is invalid. Specifically, if `a` is valid and `b` is invalid, `a` should now be considered 'greater' (come after) `b`. Conversely, if `a` is invalid and `b` is valid, `a` should be considered 'less' (come before) `b`.
      - Run the relevant date comparison tests to ensure the new sorting logic correctly places invalid dates before valid dates, and that other date comparison scenarios (e.g., valid vs. valid, null vs. date) remain correct.
  [3] Goal: Make urgency score for today's due date time-independent
      Urgency Score for due date changes at midday
### Please check that this issue hasn't been reported before.

- [X] I searched previous [Bug Reports](https://github.com/obsidian-tasks-group/obsidian-tasks/issues?q=is%3Aopen+is%3Aissue+label%3A%22type%3A+bug%22+sort%3Acomments-desc) didn't find any s
      Way: Normalize date comparisons to start of day for consistent urgency scores
      - Locate the code responsible for calculating task urgency, specifically where the difference between the current time and a task's due date is computed.
      - Reproduce the issue by creating a task due today and observing its urgency score change when queried at different times of the day (e.g., before and after midday).
      - Modify the urgency calculation to ensure that the current time used for comparison is normalized to the start of the current day. Replace `window.moment()` with `window.moment().startOf('day')` when calculating the difference for `daysOverdue`.
      - Verify that the urgency score for a task due today remains consistent regardless of the time of day.

=== TASK pion__turn-243 | repo pion/turn | go
--- Issue
Error when handling datagram
### Your environment.
Windows Server 2019
Pion Turn server 2.0.2

### What did you do?
I executed "turn-server-simple.exe" to create a Turn server for a WebRTC project Im developing

### What did you expect?
To work ok. It does sometimes. Others it get an error.

### What happened?
When haviong a videocall using WebRTC, the console throws a lots of errors. For example:

turn ERROR: 2021/03/09 09:47:34 error when handling datagram: failed to handle Refresh-request from 94.73.44.131:30866: no allocation found for 94.73.44.131:30866:0.0.0.0:3478
turn ERROR: 2021/03/09 09:47:34 error when handling datagram: failed to handle Refresh-request from 94.73.44.131:30858: no allocation found for 94.73.44.131:30858:0.0.0.0:3478
turn ERROR: 2021/03/09 09:47:34 error when handling datagram: failed to handle Refresh-request from 94.73.44.131:30860: no allocation found for 94.73.44.131:30860:0.0.0.0:3478
turn ERROR: 2021/03/09 09:52:12 error when handling datagram: failed to create stun message from packet: unexpected EOF: not enough bytes to read header
turn ERROR: 2021/03/09 09:52:12 error when handling datagram: failed to create stun message from packet: unexpected EOF: not enough bytes to read header
turn ERROR: 2021/03/09 09:52:25 error when handling datagram: failed to create stun message from packet: unexpected EOF: not enough bytes to read header

Others, I get:

turn ERROR: 2021/03/09 12:44:52 error when handling datagram: failed to 
--- Real fix (for your judgement only)
diff --git a/AUTHORS.txt b/AUTHORS.txt
index 8515b5c..ed79eeb 100644
--- a/AUTHORS.txt
+++ b/AUTHORS.txt
@@ -29,6 +29,7 @@ Sean DuBois <seaduboi@amazon.com>
 Sean DuBois <sean@pion.ly>
 Sean DuBois <sean@siobud.com>
 songjiayang <songjiayang@jd.com>
+ted <ted@skydio.com>
 Tom Clift <tom@clift.id.au>
 Yusuke Nakamura <yusuke1994525@gmail.com>
 Yutaka Takeda <yt0916@gmail.com>
diff --git a/internal/allocation/allocation.go b/internal/allocation/allocation.go
index 2b346f1..ac696ec 100644
--- a/internal/allocation/allocation.go
+++ b/internal/allocation/allocation.go
@@ -4,6 +4,7 @@ package allocation
 import (
 	"net"
 	"sync"
+	"sync/atomic"
 	"time"
 
 	"github.com/pion/logging"
@@ -12,6 +13,11 @@ import (
 	"github.com/pion/turn/v2/internal/proto"
 )
 
+type allocationResponse struct {
+	transactionID [stun.TransactionIDSize]byte
+	responseAttrs []stun.Setter
+}
+
 // Allocation is tied to a FiveTuple and relays traffic
 // use CreateAllocation and GetAllocation to operate
 type Allocation struct {
@@ -27,6 +33,12 @@ type Allocation struct {
 	lifetimeTimer       *time.Timer
 	closed              chan interface{}
 	log                 logging.LeveledLogger
+
+	// some clients (firefox or others using resiprocate's nICE lib) may retry allocation
+	// with same 5 tuple when received 413, for compatible with these clients,
+	// cache for response lost and client retry to implement 'stateless stack approach'
+	// https://datatracker.ietf.org/doc/html/rfc5766#section-6.2
+	responseCache atomic.Value // *allocationResponse
 }
 
 func addr2IPFingerprint(addr net.Addr) string {
@@ -164,6 +176,23 @@ func (a *Allocation) Refresh(lifetime time.Duration) {
 	}
 }
 
+// SetResponseCache cache allocation response for retransmit allocation request
+func (a *Allocation) SetResponseCache(transactionID [stun.TransactionIDSize]byte, attrs []stun.Setter) {
+	a.responseCache.Store(&allocationResponse{
+		transactionID: transactionID,
+		responseAttrs: attrs,
+	})
+}
+
+// GetResponseCa
--- Kel's top 3
  [1] Goal: Ensure remote track event fires reliably on negotiated peer connections
      DataChannel negotiated peer connection's server.AddTrack doesn't always trigger client.OnTrack 
### NOTE:  This is my first issue/contribution to Pion. I've done my best to follow the contribution guidelines but if I've missed the mark please let me know.  

### Your environment.
  - 2 webrtc pee
      Way: reset negotiation needed state machine on early return
      - Identify where negotiationNeededOp manages its state machine transitions and checks for handlers and signaling state.
      - Trace race conditions where onNegotiationNeeded handler is unset or signaling state prevents execution, causing deferred state resets placed after early return checks to be skipped.
      - Move the deferred cleanup closure that resets pc.negotiationNeededState to negotiationNeededStateEmpty (and re-queues if negotiationNeededStateQueue) to the very top of negotiationNeededOp so it runs on all exit paths.
      - Run the WebRTC peer connection test suite to verify negotiation-needed transitions and remote track triggering.
  [2] Goal: Propagate unhandled exceptions through HTTP middleware in mounted sub-applications
      middleware causes exceptions to not be raised/handled silently (back again)
Regression of #1976 #1977 #1609 #1940 

This time I have noticed that changing `MyExc(Exception)` to `MyExc(BaseException)` means the error does get sent to stdout (if that helps) I tried to have a dig but I am not too sur
      Way: Propagating unhandled exceptions out of ASGI middleware call loops
      - Identify where exceptions from the downstream ASGI app or background tasks are captured and re-raised in base HTTP middleware and streaming responses.
      - Move the exception container variable in the middleware's call implementation to outer scope so it survives past call_next and re-raise any stored downstream exception after the dispatch task group completes instead of within the stream generator.
      - Wrap the streaming response task group execution in collapse_excgroups to ensure exceptions unwrapped from task groups propagate consistently.
      - Run the middleware exception handling test suite across supported async backends.
  [3] Goal: Handle non-array JSON-RPC error responses for batch requests
      Batch requests crash when an empty batch is requested against some Ethereum clients like Erigon
### What happened?

A batch request to an Ethereum RPC endpoint is responded with an array containing the responses to each request in the batch:
```sh
curl http://localhost:8545 -H "Content-Type: app
      Way: Handle non-array JSON-RPC batch error responses
      - Inspect batch request handling in RequestManager and Web3Middleware to locate where batch responses are assumed to be lists.
      - Write tests mocking an RPC endpoint returning a single JSON-RPC error dict instead of a list when an empty or invalid batch is requested.
      - Update sync and async middleware batch wrappers in Web3Middleware to check if the batch response is a list, returning it directly without per-request response processing when it is a single dict.
      - Add a helper in RequestManager to validate single batch error responses and raise appropriate RPC or format errors when 'error' is present or missing.
      - Update RequestManager._make_batch_request and _async_make_batch_request to branch on isinstance(response, list), formatting responses when a list or calling the error handler when a single object.
      - Update type annotations across provider and middleware batch signatures from List[RPCResponse] to Union[List[RPCResponse], RPCResponse].

=== TASK jupyterhub__binderhub-1571 | repo jupyterhub/binderhub | python
--- Issue
Long-term fix for deleted default branches
<!-- Thank you for contributing. These HTML comments will not render in the issue, but you can delete them once you've read them if you prefer! -->

### Proposed change
<!-- Use this section to describe the feature you'd like to be added. -->

Redirect builds where  `unresolved_ref=master` and that ref doesn't resolve to the same spec with `unresolved_ref=HEAD`.

As discussed in #1145 (cc @bryevdv), there are likely a lot of old links that can't easily be updated after a default branch rename. We've fixed this for _new_ default URLs in #1186, but not in a way that keeps old links working.

### Alternative options
<!-- Use this section to describe alternative options and why you've decided on the proposed feature above. -->

Let old links break.

### Who would use this feature?
<!-- Describe who would benefit from using this feature. -->

Anyone with old Binder links in documentation, across the web who has renamed their default branch in the past few years, prior to the adoption of 'HEAD' as the default ref. We know folks are keeping an unused 'master' branch up-to-date just to avoid breaking links. This would allow them to stop doing that.

### (Optional): Suggest a solution
<!-- Describe what you think needs to be done. Doing that is an excellent first step to get the feature implemented. -->

Two possible solutions:

1. (better, IMO, but more complex): I think the 'right' behavior would be for master builds
--- Real fix (for your judgement only)
diff --git a/binderhub/builder.py b/binderhub/builder.py
index 6aaad7ae..de17837b 100644
--- a/binderhub/builder.py
+++ b/binderhub/builder.py
@@ -2,6 +2,7 @@
 Handlers for working with version control services (i.e. GitHub) for builds.
 """
 
+import asyncio
 import hashlib
 import json
 import re
@@ -303,16 +304,43 @@ class BuildHandler(BaseHandler):
                     'GitHub recently changed default branches from "master" to "main".'
                 )
 
-                if provider.unresolved_ref == "master":
-                    error_message.append('Did you mean the "main" branch?')
-                elif provider.unresolved_ref == "main":
-                    error_message.append('Did you mean the "master" branch?')
+                if provider.unresolved_ref in {"master", "main"}:
+                    error_message.append(
+                        "Tip: HEAD will always resolve to a repository's default branch."
+                    )
+
+                    # keep old links working for default branch names
+                    # by substituting 'master' or 'main' with 'HEAD'
+                    pre_ref_spec, _ = spec.rsplit("/", 1)
+                    spec = f"{pre_ref_spec}/HEAD"
+                    unresolved_ref = provider.unresolved_ref
+                    try:
+                        provider = self.get_provider(provider_prefix, spec=spec)
+                        ref = await provider.get_resolved_ref()
+                    except Exception as e:
+                        # if this fails, leave ref as None, which will fail below
+                        self.log.error(f"Error redirecting {key} to HEAD: {e}")
+                    else:
+                        # delayed redirect for deleted default branches
+                        await self.emit(
+                            {
+                                "phase": "waiting",
+                                "message": (
+                                    " ".join(error_message) + "\n"
+    
--- Kel's top 3
  [1] Goal: Append retry suffixes when creating branches that already exist
      Better handling of when a branch already exists
Currently, in `github_resolver/send_pull_request.py`, when pushing to github for a particular issue, the branch name is fixed here:
https://github.com/All-Hands-AI/openhands-resolver/blob/44aa2907d70852b7f98786c673304cc18b76d43e/openhands_resolver/sen
      Way: Append numbered retry suffixes when remote git branches already exist
      - Inspect the branch creation and pull request creation logic to identify where the git branch name is formatted before checkout and push.
      - Implement a helper function that checks if a branch exists on the remote repository using the GitHub API (`GET /repos/{owner}/{repo}/branches/{branch_name}`).
      - Before checking out the new branch locally, check if the base branch name exists remotely; while it exists, increment an attempt counter starting from 2 and append `-try{attempt}` to the base name until an unused branch name is found.
      - Run the pull request tests to verify that branch collision retries correctly increment branch names and succeed.
  [2] Goal: Check if default branch exists before attempting to rename it
      check for main branch before trying to change
As noted in [this comment](https://github.com/earthlab/abc-classroom/pull/335#discussion_r533625441) on PR #335, we should check whether the `master` branch exists before trying to change it. Suggested fix is:

`git show-ref --quiet --verify refs/heads
      Way: Verify git branch existence using show-ref before renaming
      - Locate branch renaming logic that unconditionally attempts to rename default branch `master` to `main` via `git branch -m`.
      - Update git execution helper to run subprocess with `universal_newlines=True` so stdout/stderr output strings do not require manual decoding upon error.
      - In the branch renaming helper, check for branch existence using `git show-ref --quiet --verify refs/heads/master`. If `master` exists, run `git branch -m master main`. If `master` does not exist, check if `main` exists via `refs/heads/main` and create it with `git checkout -b main` if missing.
      - Run the test suite verifying git repository initialization, branch configuration, and committing.
  [3] Goal: Fix redirect loops when linking to the latest documentation version
      Linking to latest doesn't work
The link to latest version is not working

Below is the debug log
(sample2 is the project name that contains only one version 0.1.2)


127.0.0.1 - - [02/Jul/2019 15:49:54] "GET /sample2/latest HTTP/1.1" 308 -
127.0.0.1 - - [02/Jul/2019 15:51:01] "GET /sample2/la
      Way: Prepend leading slash to redirect URLs to prevent relative redirect loops
      - Locate the route handling redirection to the latest version or document paths.
      - Run the test suite to observe how `redirect()` responses are validated.
      - Modify the redirect response in the latest route handler to prepend a leading slash (`/`) to the target path so the redirect target is treated as absolute relative to domain root rather than relative to the current URL path.
      - Run the unit tests to verify that redirect URLs start with a leading slash and resolve correctly without nesting path segments.

=== TASK julialinearalgebra__genericlinearalgebra.jl-125 | repo JuliaLinearAlgebra/GenericLinearAlgebra.jl | julia
--- Issue
svdvals! failing for empty matrices
The singular value decompositon fails for empty matrices with element types not `<: BlasFloat`:

```julia
julia> svdvals!(Float16.(ones(10,0)))
ERROR: DimensionMismatch("length of diagonal vector is 0, length of off-diagonal vector is 0")
Stacktrace:
 [1] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6/LinearAlgebra/src/bidiag.jl:11 [inlined]
 [2] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6/LinearAlgebra/src/bidiag.jl:17 [inlined]
 [3] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6/LinearAlgebra/src/bidiag.jl:75 [inlined]
 [4] bidiagonalize!(A::Matrix{Float16})
   @ GenericLinearAlgebra ~/.julia/packages/GenericLinearAlgebra/JoKjw/src/svd.jl:269
 [5] svdvals!(A::Matrix{Float16}; tol::Float16, debug::Bool)
   @ GenericLinearAlgebra ~/.julia/packages/GenericLinearAlgebra/JoKjw/src/svd.jl:487
 [6] svdvals!(A::Matrix{Float16})
   @ GenericLinearAlgebra ~/.julia/packages/GenericLinearAlgebra/JoKjw/src/svd.jl:487
 [7] top-level scope
   @ REPL[121]:1

julia> svdvals!(BigFloat.(ones(10,0)))
ERROR: DimensionMismatch("length of diagonal vector is 0, length of off-diagonal vector is 0")
Stacktrace:
 [1] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6/LinearAlgebra/src/bidiag.jl:11 [inlined]
 [2] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6/LinearAlgebra/src/bidiag.jl:17 [inlined]
 [3] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6
--- Real fix (for your judgement only)
diff --git a/src/svd.jl b/src/svd.jl
index 098a982..656ebb0 100644
--- a/src/svd.jl
+++ b/src/svd.jl
@@ -161,6 +161,10 @@ function __svd!(
 ) where {T<:Real}
 
     n = size(B, 1)
+    if n == 0
+        # Exit early in the empty case
+        return nothing
+    end
     n1 = 1
     n2 = n
     d = B.dv

--- Kel's top 3
  [1] Goal: Prevent svd from infinite looping on certain matrices
      svd stuck in infinite loop for certain matrices
Using svd on certain matrices leads to an infinite loop:
```julia
using LinearAlgebra, GenericLinearAlgebra
m = [1 0 0 0; 0 2 1 0; 0 1 2 0; 0 0 0 -1]
svd(m) # ok
svd(BigFloat.(m)) # stuck in infinite loop
svdvals!(Float64.(m), debug=true) # also 
      Way: Stabilize SVD `estimate_σ⁻` calculation to prevent infinite loops
      - Identify that the `svdvals!` function, specifically within its `estimate_σ⁻` helper, enters an infinite loop for certain matrices due to numerical instability.
      - Locate the `estimate_σ⁻` function in `src/svd.jl` and the specific line where the `μ` variable is updated within its loop.
      - Modify the calculation of `μ` in `estimate_σ⁻` to improve numerical stability. Change `μ = abs(dv[j + 1])*(μ*(μ + abs(ev[j])))` to `μ = abs(dv[j + 1])*(μ/(μ + abs(ev[j])))`.
      - Run the provided regression tests to confirm that the `svd` function no longer enters an infinite loop and produces correct results.
  [2] Goal: Handle empty support vectors properly in SVMSMOTE oversampling
      [BUG] ValueError: Found array with 0 sample(s) 
#### Describe the bug
When using SVMSMOTE on dataset which contains a minority class which has very few samples (may be < 10), it'll raise error `ValueError: Found array with 0 sample(s) (shape=(0, 600)) while a minimum of 1 is required.`

#### Step
      Way: Raise informative error when SVMSMOTE support vectors are all noise
      - Locate where support vectors are filtered by noise mask in SVMSMOTE.
      - Reproduce the `ValueError: Found array with 0 sample(s)` when filtering out all support vectors as noise.
      - Check if the support_vector array is empty after indexing non-noise indices, and raise a descriptive ValueError recommending alternative SMOTE variants if shape[0] is 0.
      - Run test suite including new test verifying the custom ValueError when all support vectors are noise.
  [3] Goal: Raise MissingChainError when embedding contains an empty chain
      Empty target array yields ZeroDivisionError
**Description**
An embedding with an empty chain, such as `embedding = {0: []}` yields a `ZeroDivisionError: float division by zero` error at https://github.com/dwavesystems/dwave-system/blob/8e7ffe9e9fa209572d508e7515d36aced2929a5f/dwave/embedding/transf
      Way: Catch ZeroDivisionError in variable embedding to raise MissingChainError
      - Locate embedding transform logic where variable biases are distributed across target chain nodes.
      - Wrap the bias division by chain length in a try/except ZeroDivisionError block and raise MissingChainError with the source variable.
      - Run test suite verifying that an empty chain in an embedding raises MissingChainError and sampler tests pass.

=== TASK theacodes__nox-310 | repo theacodes/nox | python
--- Issue
Session.run finds incorrect Python 
**Describe the bug**
`session.run('python', ..., ....)` can not find the *virtual* environments python. 

**How to reproduce**
Here is my noxfile.py. The last line is the where the issue occurs.  There are **no** issues in creating a conda environment, installing conda packages, installing pip packages, and performing the pytest. I have verified it all and it completely works as expected (installing and using binaries and scripts in the virtual environment). The issue is only the last command which could only find a default python on the system (external). I have diagnosed the issue and it is explained below.

```python
import nox
import os
VENV = 'conda' if os.name == 'nt' else None
NAME = 'win' if os.name == 'nt' else 'linux'

@nox.session(
    python=['3.6', '3.7'],
    reuse_venv=True,
    venv_backend=VENV,
    name='test_' + NAME)
def test(session):
    if VENV == 'conda':
        session.conda_install('--channel=conda-forge', 'shapely')
    session.install('-e', '.[dev]')
    session.run('pytest')
    session.run('python', 'setup.py', 'sdist', 'bdist_wheel')
```

**Fix**

The issue is here:  https://github.com/theacodes/nox/blob/ac0992332a266f85152db8a5252b1fdf9818eb75/nox/command.py#L82

where `cmd` is `python` and  `path` will be to the virtual environments `Script` directory. Here is a picture of what `path` is:
![scripts](https://user-images.githubusercontent.com/6628085/66622663-90f41000-ebb6-11e9-9
--- Real fix (for your judgement only)
diff --git a/nox/command.py b/nox/command.py
index 766481e..b2e99ce 100644
--- a/nox/command.py
+++ b/nox/command.py
@@ -14,7 +14,7 @@
 
 import os
 import sys
-from typing import Any, Iterable, Optional, Sequence, Union
+from typing import Any, Iterable, Optional, Sequence, Union, List
 
 import py
 from nox.logger import logger
@@ -29,12 +29,12 @@ class CommandFailed(Exception):
         self.reason = reason
 
 
-def which(program: str, path: Optional[str]) -> str:
+def which(program: str, paths: Optional[List[str]]) -> str:
     """Finds the full path to an executable."""
     full_path = None
 
-    if path:
-        full_path = py.path.local.sysfind(program, paths=[path])
+    if paths:
+        full_path = py.path.local.sysfind(program, paths=paths)
 
     if full_path:
         return full_path.strpath
@@ -66,7 +66,7 @@ def run(
     *,
     env: Optional[dict] = None,
     silent: bool = False,
-    path: Optional[str] = None,
+    paths: Optional[List[str]] = None,
     success_codes: Optional[Iterable[int]] = None,
     log: bool = True,
     external: bool = False,
@@ -80,12 +80,14 @@ def run(
     cmd, args = args[0], args[1:]
     full_cmd = "{} {}".format(cmd, " ".join(args))
 
-    cmd_path = which(cmd, path)
+    cmd_path = which(cmd, paths)
 
     if log:
         logger.info(full_cmd)
 
-        is_external_tool = path is not None and not cmd_path.startswith(path)
+        is_external_tool = paths is not None and not any(
+            cmd_path.startswith(path) for path in paths
+        )
         if is_external_tool:
             if external == "error":
                 logger.error(
diff --git a/nox/sessions.py b/nox/sessions.py
index bac226f..c35b0c0 100644
--- a/nox/sessions.py
+++ b/nox/sessions.py
@@ -131,10 +131,16 @@ class Session:
         """The python version passed into ``@nox.session``."""
         return self._runner.func.python
 
+    @property
+    def bin_paths(self) -> Optional[List[str]]:
+        """The bin directories for the v
--- Kel's top 3
  [1] Goal: Fix executable environment variables when running subprocesses inside a virtualenv
      conda commands as Python3 subprocesses inside a virtualenv fail under macOS
**Issue**

Describe what's the expected behaviour and what you're observing.

Running any conda command as a Python3 subprocess inside of a virtualenv fails under macOS with `ModuleNotFoundError: No module named 'conda'`
      Way: clean environment variables in virtual environment runtime startup hook
      - Locate environment initialization scripts and creator classes responsible for injecting startup python patches into created environments.
      - Refactor virtual environment setup to generate startup patch code via an extensible method rather than static file copy.
      - Append runtime snippet in POSIX CPython creation logic to delete `__PYVENV_LAUNCHER__` from `os.environ` on macOS for affected Python versions (3.7.0..3.7.7 and 3.8.0..3.8.2).
      - Run unit test suite to verify created virtual environments generate correct startup hooks and execute subprocesses without launcher env leaks.
  [2] Goal: Correctly resolve Python executable names when creating virtual environments
      Failing to create venv when passing --python version on linux
<!--
Thank you for filing a bug! Please feel free to answer as much or as little of this template as you can.

Please check pipx's Troubleshooting page to see if any of those solutions help solve your issue:
https://pipx.pypa.io/stabl
      Way: limit subprocess executable realpath resolution to Windows
      - Inspect `run_subprocess` in `src/pipx/util.py` where command binary paths are resolved prior to invoking `subprocess.run`.
      - Reproduce the issue where passing a bare executable name like `python3.9` on non-Windows triggers `os.path.realpath` without `shutil.which`, resolving it relative to the current working directory instead of searching PATH.
      - Restrict the `os.path.realpath` conversion for `cmd_str_list[0]` to Windows environments (`WINDOWS`) so that Unix platforms rely on standard PATH lookup instead of resolving relative paths against CWD.
      - Run unit tests verifying executable path resolution on Unix platforms.
  [3] Goal: Make Nox respect virtual environment backend for Python installation
      Nox installs Python using `uv` with non-uv backend
### Current Behavior

Nox installs Python using `uv` with non-uv backend. If uv is installed on the system.

### Expected Behavior

Don't use `uv` to install Python with non-uv backend.

### Steps To Reproduce

noxfile.py:

```python
import nox

      Way: Restricting automatic uv Python installation to uv virtualenv backend
      - Search for where automatic Python interpreter resolution and installation (via uv) occurs before virtual environment creation.
      - Identify the condition that triggers `uv_install_python` during interpreter resolution and check whether the session's virtual environment backend is checked.
      - Update the virtual environment interpreter resolution logic so that `uv_install_python` is only invoked if `venv_backend == 'uv'` in addition to uv availability.
      - Refactor process and session environment variable handling so blacklisted environment variables map to `None` and PATH construction is applied across session execution without eager pollution from parent environment.
      - Add explicit UTF-8 encoding when querying interpreter paths via `py` launcher on Windows subprocess calls.
      - Run the virtualenv and session environment tests to verify interpreter resolution and environment passing behaviour.

=== TASK plexis-js__plexis-60 | repo plexis-js/plexis | js
--- Issue
Feature request: `isString`
Checks whether the input is a string primitive type.

**Example usage**

```javascript
import isString from '@plexis/is-string';
isString('Foo');
// => true

isString(1);
// => false

isString(null);
// => false
```
--- Real fix (for your judgement only)
diff --git a/docs/pages/api.md b/docs/pages/api.md
index f72ac19..bba1eb4 100644
--- a/docs/pages/api.md
+++ b/docs/pages/api.md
@@ -10,3 +10,4 @@
 - [toSucc](/packages/toSucc)
 - [toTitle](/packages/toTitle)
 - [withoutDiacritics](/packages/withoutDiacritics)
+- [isString](/packages/isString)
diff --git a/packages/isString/README.md b/packages/isString/README.md
new file mode 100644
index 0000000..c09233b
--- /dev/null
+++ b/packages/isString/README.md
@@ -0,0 +1,21 @@
+## `isString`
+
+Checks whether the input is a string primitive type.
+
+`npm i @plexis/is-string`
+
+**Usage**
+
+```javascript
+import isString from '@plexis/is-string';
+
+isString('Foo'); // returns 'true'
+isString(1); // returns 'false'
+isString(null); // returns 'false'
+```
+
+**Aliases**
+
+```javascript
+import {isString} from 'plexis';
+```
diff --git a/packages/isString/package.json b/packages/isString/package.json
new file mode 100644
index 0000000..cbd922b
--- /dev/null
+++ b/packages/isString/package.json
@@ -0,0 +1,26 @@
+{
+  "name": "@plexis/is-string",
+  "version": "0.0.14",
+  "main": "dist/index.js",
+  "module": "dist/index.esm.js",
+  "jsnext:main": "./src/index.js",
+  "files": [
+    "dist",
+    "src"
+  ],
+  "license": "MIT",
+  "keywords": [
+    "utility",
+    "string",
+    "parse",
+    "format",
+    "validate"
+  ],
+  "repository": {
+    "type": "git",
+    "url": "git+https://github.com/plexis-js/plexis.git"
+  },
+  "publishConfig": {
+    "access": "public"
+  }
+}
diff --git a/packages/isString/src/index.js b/packages/isString/src/index.js
new file mode 100644
index 0000000..b8d3485
--- /dev/null
+++ b/packages/isString/src/index.js
@@ -0,0 +1,12 @@
+/**
+ * @description Checks whether the input is a string primitive type.
+ * @param {String} param
+ * @returns {Boolean}
+ * @example
+ * isString('Foo') // returns 'true'
+ * isString(1) // returns 'false'
+ * isString(null) // returns 'false'
+ */
+const isString = param => typeof param === 'string';
+
+exp
--- Kel's top 3
  [1] Goal: Check if text contains only alphanumeric characters
      Feature request: `isAlphaDigit`
Checks whether the input text contains only alpha and digit characters.

**Example usage**

```javascript
import isAlphaDigit from '@plexis/is-alphadigit';

isAlphaDigit('ABCD');
// => true

isAlphaDigit('Apollo11');
// => true

isEmpty('Hello world');
/
      Way: Implement alphanumeric string validation
      - Understand the feature request for a function that checks if a string contains only alphanumeric characters, including its expected behavior for various inputs (e.g., empty string, strings with spaces, special characters, or non-string types).
      - Create a new module or package for the `isAlphaNumeric` function, following the project's structure for utility functions.
      - Implement the core logic for the `isAlphaNumeric` function. This involves checking if the input is a string and then using a regular expression to verify that it contains only uppercase letters (A-Z), lowercase letters (a-z), or digits (0-9). An empty string should return false.
      - Integrate the new `isAlphaNumeric` function into the main library's exports, ensuring it is available under its primary name and any requested aliases (e.g., `isAlphaDigit`, `isAlphanumeric`).
      - Update relevant documentation, including the new package's `README.md` and the main API reference, to describe the function's purpose, usage, and examples.
      - Run the provided tests to confirm that the `isAlphaNumeric` function correctly identifies strings containing only alphanumeric characters and returns false for strings with other characters, empty strings, or non-string inputs.
  [2] Goal: Exclude strings from array-like type validation checks
      "array-like" check incorrectly accepts strings
### Description of the problem

`_validate_type()` accepts a string parameter value "array-like" to allow checking for array-like input. However, the implementation of this recent addition is incorrect by considering any `Sequence` as `array-like`

ht
      Way: exclude strings from array-like type validation checks
      - Locate the type validation utility mapping where 'array-like' is defined.
      - Modify the 'array-like' type mapping to use explicit collection types (list, tuple, set, np.ndarray) instead of the generic Sequence class, which matches strings.
      - Run the type validation tests to verify that strings are no longer accepted as array-like and that valid array-like types still pass.
  [3] Goal: Ensure decodeShortString decodes both hex and decimal short strings
      decodeShortString : wrong answer of the function
**Describe the bug**
Hello,
I noted a problem with the function `utils/shortstrings.ts/decodeShortString`. 
If I use it with an hex string ('0x321....456'), everything is fine.
But if the str:string is  an integer string ('1542233...56'), the func
      Way: Decode short strings from both hex and decimal formats
      - Examine the `decodeShortString` function to understand its current logic, which assumes input is always a hexadecimal string.
      - Identify the need to distinguish between hexadecimal and decimal string inputs for `decodeShortString`.
      - Add a utility function `isDecimalString` to check if a string consists solely of decimal digits.
      - Modify `decodeShortString`: If `isHex(str)`, decode as hex. Else if `isDecimalString(str)`, convert to hex (`'0X' + BigInt(str).toString(16)`) and recursively decode. Else, throw an error.
      - Run the provided tests to ensure `decodeShortString` correctly handles decimal number strings and throws errors for invalid input formats.

=== TASK keats__tera-43 | repo Keats/tera | rust
--- Issue
Add {% raw %} ... {% endraw %} tag
that makes it so the content inside is just text
--- Real fix (for your judgement only)
diff --git a/src/parser.rs b/src/parser.rs
index 8e489a5..8a22496 100644
--- a/src/parser.rs
+++ b/src/parser.rs
@@ -24,6 +24,7 @@ pub enum Node {
     For {variable: String, array: String, body: Box<Node>},
     Block {name: String, body: Box<Node>},
 
+    Raw(String),
     Identifier(String),
     Extends(String),
     VariableBlock(Box<Node>),
@@ -107,19 +108,23 @@ impl_rdp! {
         elif_tag        = !@{ tag_start ~ ["elif"] ~ expression ~ tag_end }
         else_tag        = !@{ tag_start ~ ["else"] ~ tag_end }
         for_tag         = !@{ tag_start ~ ["for"] ~ identifier ~ ["in"] ~ identifier ~ tag_end }
+        raw_tag         = !@{ tag_start ~ ["raw"] ~ tag_end }
+        endraw_tag      = !@{ tag_start ~ ["endraw"] ~ tag_end }
         endblock_tag    = !@{ tag_start ~ ["endblock"] ~ identifier ~ tag_end }
         endif_tag       = !@{ tag_start ~ ["endif"] ~ tag_end }
         endfor_tag      = !@{ tag_start ~ ["endfor"] ~ tag_end }
 
         elif_block = { elif_tag ~ content* }
+        raw_text   = { (!endraw_tag ~ any )* }
+        text       = { (!(block_start) ~ any )+ }
 
-        text = { (!(block_start) ~ any )+ }
         content = @{
             variable_tag |
             comment_tag |
             block_tag ~ content* ~ endblock_tag |
             if_tag ~ content* ~ elif_block* ~ (else_tag ~ content*)? ~ endif_tag |
             for_tag ~ content* ~ endfor_tag |
+            raw_tag ~ raw_text ~ endraw_tag |
             text
         }
 
@@ -167,6 +172,9 @@ impl_rdp! {
             (_: variable_tag, exp: _expression()) => {
                 Ok(Some(Node::VariableBlock(Box::new(try!(exp)))))
             },
+            (_: raw_tag, &body: raw_text, _: endraw_tag) => {
+                Ok(Some(Node::Raw(body.to_string())))
+            },
             (_: block_tag, &name: identifier, body: _template(), _: endblock_tag, &end_name: identifier) => {
                 if name != end_name {
                     let (line_no, col_no) = self
--- Kel's top 3
  [1] Goal: Allow rendering unescaped raw text in markdown components
      <Mrkdwn raw>
`<Mrkdwn raw>` is an idea to provide the solution for #160 right out of the box.

If enabled `raw` attribute, `<Mrkdwn>` won't parse the children as HTML that requires escaping some characters, and set the text to the content of `text` JSON field as is.

```jsx
console.log(
  <Mrk
      Way: add raw prop to markdown text component to bypass auto-escaping
      - Inspect the markdown composition component definition and find how child content is transformed into text output.
      - Add a `raw` optional boolean property to the component props interface (`MrkdwnProps`).
      - Update the component render function to extract text via `plainText(props.children).text` when `raw` is true instead of transforming through markdown parser `toMrkdwn(props.children)`.
      - Update component schema definitions and documentation to document the `raw` prop.
      - Add and run unit tests checking that `<Mrkdwn raw>` outputs unescaped text as-is without HTML-like transformation.
  [2] Goal: Ensure block HTML tags insert preceding newlines during text extraction
      Inline <p> not handled well
Input:

    Wie nennt man jemanden, der gegen Covid-Politik Proteste organisiert, und sich dann mit einem Covid-Testcenter an der Zitze des Staates labt?<p><a href="https://www.mdr.de/nachrichten/sachsen-anhalt/dessau/bitterfeld/buergermeister-raguhn-jessnitz-loth-afd-1
      Way: ensure block HTML tags insert preceding newlines during text extraction
      - Locate the HTML-to-Markdown conversion logic, specifically where child nodes are iterated and concatenated, and where block elements like paragraphs, headers, and lists are converted.
      - Modify the child node processing loop to merge adjacent text blocks by stripping trailing newlines from the left text and leading newlines from the right text, then joining them with a number of newlines equal to the maximum of the stripped newlines.
      - Update block-level converters (such as convert_p, convert_hn, convert_list, and underline) to prepend leading newlines (e.g., '\n\n' or '\n') to ensure they are properly separated from preceding text.
      - Run the test suite to verify that block elements are correctly separated by newlines without creating excessive blank lines.
  [3] Goal: Fix bracket new line formatting for inline HTML tags
      `--svelte-bracket-new-line true` not working correctly (e.g. button and span)
`src/test.svelte`:
```svelte
<button class="bg-green-500  text-gray-100 hover:bg-green-400 flex items-center px-4 py-2 border border-l-0 rounded-r"> Copy </button>
<div class="bg-green-500  text-gray-100 hover:bg-green-
      Way: Respect bracket newlines on HTML tags by checking hugStart
      - Locate element opening and closing tag formatting logic in the printer.
      - Check why bracket new lines are omitted on certain inline elements like button and span when svelteBracketNewLine is true.
      - Replace tag-level hugContent checks with hugStart in opening tag bracket newline formatting, and adjust softline omission and indentation in hugged content.
      - Run printer and formatting test suites to ensure proper tag bracket linebreaks and indentation.

=== TASK rstudio-education__gradethis-309 | repo rstudio-education/gradethis | r
--- Issue
`fail(hint = TRUE)` gives multiple hints in exercises with multiple solutions
`fail(hint = TRUE)` generates a hint for all solutions in exercises with multiple solutions.It should only generate one hint for the solution closest to the user input.

``` r
library(gradethis)

solution_code <- "
# mean ----
mean(1:10)
# median ----
median(1:10)"
```

`fail(hint = TRUE)` generates two hints: one for each solution.

```r
grade_this({
  pass_if_equal()
  fail(hint = TRUE)
})(
  mock_this_exercise("sum(1:10)", !!solution_code)
)
#> <gradethis_graded: [Incorrect]
#>   Incorrect. I expected you to call `mean()` where you called `sum()`.
#>   Try it again. You get better each time. I expected you to call
#>   `median()` where you called `sum()`.
#> >
```

`fail_if_code_feedback()` behaves as expected: it only generates one hint.

```r
grade_this({
  pass_if_equal()
  fail_if_code_feedback()
})(
  mock_this_exercise("sum(1:10)", !!solution_code)
)
#> <gradethis_graded: [Incorrect]
#>   I expected you to call `mean()` where you called `sum()`.
#> >
```

<sup>Created on 2022-04-21 by the [reprex package](https://reprex.tidyverse.org) (v2.0.1)</sup>
--- Real fix (for your judgement only)
diff --git a/R/code_feedback.R b/R/code_feedback.R
index d259c90a..99b203d6 100644
--- a/R/code_feedback.R
+++ b/R/code_feedback.R
@@ -420,7 +420,11 @@ give_code_feedback_.gradethis_graded <- function(
   env = rlang::env_parent(n = 2),
   location = "after"
 ) {
-  solution_code <- get0(".solution_code", envir = env, ifnotfound = NULL)
+  # Use code for multiple solutions if available, otherwise single solution
+  solution_code <-
+    get0(".solution_code_all", envir = env, ifnotfound = NULL) %||%
+    get0(".solution_code", envir = env, ifnotfound = NULL)
+
   user_code <- get0(".user_code", envir = env, ifnotfound = NULL)
 
   if (is.null(solution_code) || !identical(grade$correct, FALSE)) {

--- Kel's top 3
  [1] Goal: Fix rounding error when validating that probabilities sum to one
      Rounding error in assignment probabilities
I noticed that the check for whether the assignment probabilities passed to the `probs` argument is equal to 1 can fail due to a rounding error. As expected, this happens because fractions are converted to floats, which introduces some imprecision. The code
      Way: Use math.isclose for floating point sum equality validation
      - Locate floating point sum equality check in the parameter validation logic.
      - Reproduce the error by calling the function with float probabilities that sum to 1 in exact arithmetic but differ slightly in binary floating point representation (e.g., [1/2, 1/3, 1/6]).
      - Replace strict exact equality check `if probs_np.sum() != 1:` with fuzzy comparison `if not math.isclose(probs_np.sum(), 1, rel_tol=1e-9):` using Python standard library `math.isclose`.
      - Run pytest to verify that fractional floating point probabilities summing close to 1 pass validation and test suite passes.
  [2] Goal: Return False on equality comparison when code scheme mapping fails
      Comparing two codes where one is erroneously set as a SRT will throw KeyError
**Describe the bug**
The comparision of two codes does mapping from `SRT` codes to `SCT`. If comparision is done on a code that has `SRT` as the scheme designator but is not in the dictionary of `SRT` to `SCT` mappings, t
      Way: safely check mapping dict before code equivalence mapping
      - Locate the equality method `__eq__` in the coding object class and identify where missing map keys cause a KeyError.
      - Reproduce the error by attempting equality comparison on a code with scheme designator 'SRT' whose value is absent from the SNOMED mapping dictionary.
      - Modify the scheme designator check in `__eq__` to verify that the code's value exists in `snomed_mapping['SRT']` before performing dictionary lookup for mapping.
      - Run pytest on the test suite to ensure the unmapped SRT comparison returns False instead of raising KeyError.
  [3] Goal: Report multiple type errors in list literals instead of stopping at first error
      Analysis of lists stops at the first error
If I write `[1, "a", 1 + "a"]` the compiler only show an error for the first `"a"` but won't show an error for the second expression trying to sum an int and a string. I think we'd have to make analysis fault tolerant for lists in order to enable this.
      Way: Make AST expression type inference fault-tolerant
      - Locate expression typing methods in the type checker (such as `infer_list`, `infer_tuple`, `infer_panic`, `infer_todo`, `infer_echo`, and negation operations) that return early with `Result<TypedExpr, Error>` upon the first unification or inference error.
      - Update expression inference helper functions to use `infer_no_error` instead of `infer(...) ?` and record unification errors into the compiler problem/diagnostics accumulator instead of returning `Err(Error)`.
      - Change the return types of non-fatal expression typing methods to return `TypedExpr` directly, wrapping them in `Ok(...)` at the outer expression dispatcher matching branch.
      - Fix JavaScript code generator for numeric literals to strip leading underscores after leading zeros are trimmed, ensuring valid output for numbers like `0_1_2`.
      - Run the compiler test suite to ensure multiple errors in list literals are accumulated and JavaScript number formatting works as expected.

=== TASK mgechev__revive-876 | repo mgechev/revive | go
--- Issue
import-shadowing reacts on method names while it shouldn't
**Describe the bug**
`import-shadowing` rule marks method names as shadowing, but it's not a true shadowing since it's a part of a struct. I'm not sure if it was done intentionally or not, but it seems to be a bug.

**To Reproduce**

```go
package somepkg

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

type Struct struct{}

func (s *Struct) reconcile(md metav1.ObjectMeta) (reconcile.Result, error) {
	return reconcile.Result{}, nil
}
```

Run revive against this file and get this (I use `golangci-lint`, but I guess it's a problem in revive itself):

```
somepkg/t.go:10:18: import-shadowing: The name 'reconcile' shadows an import name (revive)
func (s *Struct) reconcile(md metav1.ObjectMeta) (reconcile.Result, error) {
                 ^
```

**Expected behavior**
It should not consider method names as a shadow for the imported packages. Only package-level functions (i.e. not methods) should be considered.

**Additional context**
I use `golangci-lint` [v1.54.2](https://github.com/golangci/golangci-lint/releases/tag/v1.54.2).

--- Real fix (for your judgement only)
diff --git a/rule/import-shadowing.go b/rule/import-shadowing.go
index 2bab704..046aeb6 100644
--- a/rule/import-shadowing.go
+++ b/rule/import-shadowing.go
@@ -29,6 +29,7 @@ func (*ImportShadowingRule) Apply(file *lint.File, _ lint.Arguments) []lint.Fail
 			failures = append(failures, failure)
 		},
 		alreadySeen: map[*ast.Object]struct{}{},
+		skipIdents:  map[*ast.Ident]struct{}{},
 	}
 
 	ast.Walk(walker, fileAst)
@@ -62,6 +63,7 @@ type importShadowing struct {
 	importNames      map[string]struct{}
 	onFailure        func(lint.Failure)
 	alreadySeen      map[*ast.Object]struct{}
+	skipIdents       map[*ast.Ident]struct{}
 }
 
 // Visit visits AST nodes and checks if id nodes (ast.Ident) shadow an import name
@@ -80,6 +82,10 @@ func (w importShadowing) Visit(n ast.Node) ast.Visitor {
 		*ast.SelectorExpr, // skip analysis of selector expressions (anId.otherId): because if anId shadows an import name, it was already detected, and otherId does not shadows the import name
 		*ast.StructType:   // skip analysis of struct type because struct fields can not shadow an import name
 		return nil
+	case *ast.FuncDecl:
+		if n.Recv != nil {
+			w.skipIdents[n.Name] = struct{}{}
+		}
 	case *ast.Ident:
 		if n == w.packageNameIdent {
 			return nil // skip the ident corresponding to the package name of this file
@@ -92,11 +98,12 @@ func (w importShadowing) Visit(n ast.Node) ast.Visitor {
 
 		_, isImportName := w.importNames[id]
 		_, alreadySeen := w.alreadySeen[n.Obj]
-		if isImportName && !alreadySeen {
+		_, skipIdent := w.skipIdents[n]
+		if isImportName && !alreadySeen && !skipIdent {
 			w.onFailure(lint.Failure{
 				Confidence: 1,
 				Node:       n,
-				Category:   "namming",
+				Category:   "naming",
 				Failure:    fmt.Sprintf("The name '%s' shadows an import name", id),
 			})
 

--- Kel's top 3
  [1] Goal: Prevent method parameter names from shadowing imported module names in generated clients
      Naming conflict with generated client for google/cloud/dataplex/v1
There is a naming conflict with `metadata` the generated client for [dataplex v1](https://github.com/googleapis/googleapis-gen/tree/master/google/cloud/dataplex/v1/dataplex-v1-py) and as a result all of the `metadata` unit tests fail
      Way: Disambiguate module names matching RPC method parameter keywords
      - Locate where module file names are disambiguated against Python reserved keywords in API schema processing.
      - Expand the set of invalid module names beyond standard Python keywords (`keyword.kwlist`) to include method parameter names like `metadata`, `retry`, `timeout`, and `request` that are imported and referenced in client method signatures.
      - Run the schema API unit tests to verify module name disambiguation logic appends a trailing underscore when a module name matches reserved method parameters.
  [2] Goal: Prevent wildcard module imports from shadowing the built-in int type
      The module `pyteal.ast.int` shadows the Python keyword `int`
When importing `pyteal` by `from pyteal import *` (as in the documentation examples), the module `pyteal.ast.int` shadows the Python keyword `int`.

In particular, we have
```
$ python3 -c "from pyteal import *; print(int(5))"
Traceba
      Way: Restricting wildcard package exports with explicit __all__ lists
      - Identify package `__init__.py` files performing wildcard imports (`from .subpackage import *`) or importing internal module names that shadow Python builtins or standard types.
      - Define explicit `__all__` lists in subpackage `__init__.py` files containing only the public classes, functions, and constants intended to be exposed, excluding submodule objects.
      - In the root package `__init__.py`, import the subpackage `__all__` lists and define a root `__all__` list combining the subpackage exports with explicit root-level exports.
      - Run the test suite to verify wildcard package imports no longer shadow built-in names like `int` while retaining access to intended exports.
  [3] Goal: Preserve named imports despite conflicting callback type parameters
      Incorrect elision of named import if typings of a callback function contains a parameter with the same name
I came across a bit of an odd bug while using Sucrase.

In the following code, notice that `someVariable` is used as an import name but also used in the type definition of the argument of th
      Way: Ignore type tokens during identifier declaration and shadowing analysis
      - Identify where scope tracking or shadowed global variable detection scans tokens for local declarations.
      - Reproduce the issue with a test case where a TypeScript function type parameter shares a name with an imported value used in the body.
      - Add checks ensuring tokens marked as types (such as `token.isType`) are excluded when determining if a token introduces a local declaration or shadows a global identifier in functions like `hasShadowedGlobals` and `markShadowedGlobals`.
      - Run the test suite to confirm that type parameter names do not cause real value imports to be shadowed or incorrectly elided.

=== TASK pyfar__pyfar-722 | repo pyfar/pyfar | python
--- Issue
dsp.normalize's reference_methods "power" and "energy" behave unexpectedly
## General

* pyfar version: 0.6.8 (issue persists in current code though)
* Python version: 3.12.4
* Operating System: windows
* Did you install pyfar via pip: yes

## Description

I tried to normalize several signals of different length to the same total energy using pyfar. I expected this to be possible using 
```
pyfar.dsp.normalize(signal, reference_method="energy", target=1)
```
such that each signal would end up with a total energy of 1. However, since the input is a pressure signal and the reference value is energetic, the output signals do not have energies of 1. In fact, they do not even have the same energies:

For instance, with an input signal with an energy of 20, normalize will output a signal with an energy of 0.05 (1/20), whereas an input energy of 5 will output an energy of 0.2 (1/5).
I talked about this with @f-brinkmann and we both think the function call above should return a signal with an energy of 1.
So, when the `reference_method` is set to "power" or "energy", we believe the signal should be divided by the root of the calculated reference value, not the value itself.

## Code Example
```py
shorter = pf.signals.noise(2000, rms=0.1)
longer = pf.signals.noise(4000, rms=0.1)

for input_signal, name in [(shorter, "shorter"), (longer, "longer")]:
    input_energy = pf.dsp.energy(input_signal)
    output_signal = pf.dsp.normalize(input_signal, reference_meth
--- Real fix (for your judgement only)
diff --git a/pyfar/dsp/dsp.py b/pyfar/dsp/dsp.py
index b44dbac7..c032e89e 100644
--- a/pyfar/dsp/dsp.py
+++ b/pyfar/dsp/dsp.py
@@ -2442,8 +2442,16 @@ def normalize(signal, reference_method='max', domain='auto',
             Compute the mean absolute values per channel.
         ``'energy'``
             Compute the energy per channel using :py:func:`~pyfar.dsp.energy`.
+            Note that the square root of the energy is used as `reference`,
+            after handling multi-channel signals (see *channel_handling*
+            below). This is required for the energy of the normalized signal to
+            match the `target`.
         ``'power'``
             Compute the power per channel using :py:func:`~pyfar.dsp.power`.
+            Note that the square root of the power is used as `reference`,
+            after handling multi-channel signals (see *channel_handling*
+            below). This is required for the power of the normalized signal to
+            match the `target`.
         ``'rms'``
             Compute the RMS per channel using :py:func:`~pyfar.dsp.rms`.
 
@@ -2661,6 +2669,13 @@ def normalize(signal, reference_method='max', domain='auto',
     else:
         raise ValueError(("channel_handling must be 'individual', 'max', "
                           "'min' or 'mean'."))
+
+    # scale normalization and target to achieve target energy and power. This
+    # must be done because they are energetic properties
+    if reference_method in ['energy', 'power']:
+        reference_norm = np.sqrt(reference_norm)
+        target = np.sqrt(target)
+
     # apply normalization
     normalized_signal = signal.copy() * target / reference_norm
     if return_reference:

--- Kel's top 3
  [1] Goal: Fix degrees of freedom keyword arguments in F-test power analysis
      BUG: FTestPower always Fails to converge on a solution, wrong names, unclear usage
**edit** (josef)
FTestPower mixes up df_num, df_denom names of keywords, meaning is reversed. 
Code itself works correctly but not obvious how to use it.


#### Describe the bug

I am trying to do a power analy
      Way: fix degrees of freedom keywords and non-central distribution calculations in power analysis
      - Locate F-test power functions and power distribution wrappers in the statistical power and oneway modules.
      - Reproduce non-convergence or erroneous numerical calculations caused by SciPy's `stats.ncf` / `stats.nct` methods.
      - Add non-central t and F distribution helper wrappers in power module using `scipy.special` functions (`nctdtr`, `ncfdtr`, `ncfdtri`) and replace direct `scipy.stats` calls across power and oneway modules.
      - Fix numerator and denominator degrees of freedom calculations in `ftest_anova_power` and introduce `ftest_power_f2` alongside `FTestPowerF2` to handle Cohen's f2 effect size with correct keyword argument names.
      - Run power module tests to ensure convergence issues are fixed and test power plots and calculations pass.
  [2] Goal: Ensure throughput capacity and energy calculation consistency across solution cycles and steps
      "Throughput capacity [A.h]" is different for sol.cycles[0] and sol.cycles[0].steps[0]
### Discussed in https://github.com/pybamm-team/PyBaMM/discussions/2523

<div type='discussions-op-text'>

<sup>Originally posted by **RuiheLi** December  5, 2022</sup>
Dear PyBaMM,

I am using PyBaMM 22.9 a
      Way: disable independent rhs variable removal by default in discretisation
      - Locate discretisation and simulation logic handling removal of independent variables from right-hand side (RHS) equations.
      - Move configuration flags `remove_independent_variables_from_rhs` and `check_model` from `Discretisation.process_model` method parameters to `Discretisation.__init__` parameters and set `remove_independent_variables_from_rhs` default to `False`.
      - Update `Simulation` initialization to accept `discretisation_kwargs` and remove deprecated `check_model` parameter from solve methods.
      - Run unit tests verifying RHS variable retention and simulation execution.
  [3] Goal: Calculate power spectrum correctly when GTIs are shorter than the light curve
      Bug in `Powerspectrum` when GTI is shorter than light curve?
I have a data set where the GTIs are slightly shorter than the data in the FITS file. When I create a light curve and then try to use `Powerspectrum`, I get an AssertionError that no GTIs are longer than `segment_size`. I think this is a b
      Way: handle single light curve power spectrum with tight gtis
      - Inspect Crossspectrum and Powerspectrum initialization and Fourier segment processing when segment_size is None and GTIs are shorter than the input timeseries data.
      - In Crossspectrum initialization, when segment_size is None, cross data GTIs, filter the input data with the common GTI using apply_gtis(inplace=False), and validate matching time bins for binned data.
      - In Fourier segment iteration (get_flux_iterable_from_segments), if segment_size is None, span the full range from gti[0, 0] to gti[-1, 1] across all time samples rather than segmenting by fixed interval.
      - In avg_cs_from_timeseries and avg_pds_from_timeseries, handle segment_size=None by setting n_bin directly from binned data length or adjusting segment size to integer sample counts via fix_segment_size_to_integer_samples.
      - Run test suite for crossspectrum and powerspectrum to verify single spectra calculations with shorter GTIs pass.

=== TASK neurodatawithoutborders__pynwb-1806 | repo NeurodataWithoutBorders/pynwb | python
--- Issue
[Bug]: `get_data_in_units` fails for ElectricalSeries with `channel_conversion` 
### What happened?

https://github.com/NeurodataWithoutBorders/pynwb/pull/1745 added a method to get the data in the corect units which is something very nice that I always wanted. 

One corner case that i think is not included is that the method will produce incorrect results with an `ElectricalSeries` object that has a channel conversion.

### Steps to Reproduce

This fails on the dev branch:


```python
from pynwb.testing.mock.ecephys import mock_ElectricalSeries

import numpy as np

data = np.asarray([[1, 1, 1, 1, 1], [1, 1, 1, 1, 1]])
conversion = 1.0
offset = 3.0
channel_conversion = np.asarray([2.0, 2.0])
electrical_series = mock_ElectricalSeries(
    data=data,
    conversion=conversion,
    offset=offset,
    channel_conversion=channel_conversion,
)

data_in_units = electrical_series.get_data_in_units()
expected_data =  data * conversion * channel_conversion[:, np.newaxis] + offset

np.testing.assert_almost_equal(data_in_units, expected_data)
```


### Traceback

```
AssertionError: 
Arrays are not almost equal to 7 decimals

Mismatched elements: 10 [/](https://file+.vscode-resource.vscode-cdn.net/) 10 (100%)
Max absolute difference: 1.
Max relative difference: 0.2
 x: array([[4., 4., 4., 4., 4.],
       [4., 4., 4., 4., 4.]])
 y: array([[5., 5., 5., 5., 5.],
       [5., 5., 5., 5., 5.]])
```

### Operating System

Windows

### Python 
--- Real fix (for your judgement only)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 60f0e42f..078eb5ce 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -8,6 +8,7 @@
 - Add `pynwb.get_nwbfile_version()`. @bendichter [#1703](https://github.com/NeurodataWithoutBorders/pynwb/pull/1703)
 - Updated timeseries data checks to warn instead of error when reading invalid files. @stephprince [#1793](https://github.com/NeurodataWithoutBorders/pynwb/pull/1793) and [#1809](https://github.com/NeurodataWithoutBorders/pynwb/pull/1809)
 - Expose the offset, conversion and channel conversion parameters in `mock_ElectricalSeries`. @h-mayorquin [#1796](https://github.com/NeurodataWithoutBorders/pynwb/pull/1796)
+- Enhance `get_data_in_units()` to work with objects that have a `channel_conversion` attribute like the `ElectricalSeries`  @h-mayorquin [#1806](https://github.com/NeurodataWithoutBorders/pynwb/pull/1806)
 
 ### Bug fixes
 - Fix bug where namespaces were loaded in "w-" mode. @h-mayorquin [#1795](https://github.com/NeurodataWithoutBorders/pynwb/pull/1795)
diff --git a/src/pynwb/base.py b/src/pynwb/base.py
index 84626685..42f7b7ff 100644
--- a/src/pynwb/base.py
+++ b/src/pynwb/base.py
@@ -298,7 +298,11 @@ class TimeSeries(NWBDataInterface):
             return np.arange(len(self.data)) / self.rate + self.starting_time
 
     def get_data_in_units(self):
-        return np.asarray(self.data) * self.conversion + self.offset
+        if "channel_conversion" in self.fields:
+            scale_factor = self.conversion * self.channel_conversion[:, np.newaxis]
+        else:
+            scale_factor = self.conversion
+        return np.asarray(self.data) * scale_factor + self.offset
 
 
 @register_class('Image', CORE_NAMESPACE)

--- Kel's top 3
  [1] Goal: Support interleaving array data with mixed column dimensions
      Interleave data breaks when dealing with a combination of (Nt,1) and (Nt,2) conductance models
**Describe the bug**
Suppose you have two geometries, one involves a conductance model that returns (Nt,1), and the other (Nt,2). Calling `throat.conductance` on the phase object fails.

**To Reproduce*
      Way: broadcasting 1D arrays to 2D column vectors when interleaving multi-column data
      - Locate data interleaving logic where arrays from multiple sub-domains or geometries are merged into a single array.
      - Reproduce the ValueError caused by shape mismatch when assigning 1D sub-arrays (shape (N,)) into a 2D interleaved destination (shape (N, W)).
      - Check if any array among the input arrays has ndim > 1, and if so, reshape every 1D array to a 2D column array using np.atleast_2d(a).T before calculating destination width and assigning slices.
      - Narrow exception handling around array dimension inspection (such as IndexError) when inferring the destination shape.
      - Run the interleaving test suite including mixed-dimension cases.
  [2] Goal: Correct unit conversion for inverse speed quantities
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
  [3] Goal: Support array exponents when raising unit arrays to a power
      An error occurs when the exponent is an array
* unyt version: 2.9.5
* Python version: 3.9.17
* Operating System: 14.6.1 

### Description

In the `power` operation of unyt arrays, when the exponent is also an array, it leads to an error.

```shell
In [1]: from unyt import unyt_array

In [
      Way: support array exponents in unit-aware power operations
      - Identify where `__pow__` and ufunc handling for `power` process exponents on unit-bearing array objects.
      - Update `__pow__` to verify the exponent is a scalar before checking equality to zero, avoiding boolean ambiguity errors when an array exponent is passed.
      - Update `__array_ufunc__` handling for `power` to support matching array shapes when exponents are dimensionless: if the base has units, verify exponent values are uniform using `np.ptp(u1) == 0` before applying the unit power using the exponent's first element.
      - Run the test suite to ensure array powers work for dimensionless and uniform array exponents and raise appropriate errors for invalid exponents.