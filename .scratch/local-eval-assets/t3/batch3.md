

=== TASK electron__electron-packager-1449 | repo electron/electron-packager | js
--- Issue
-all should not attempt to build Linux/ia32 since it fails to find required files
### Preflight Checklist
<!-- Please ensure you've completed the following steps by replacing [ ] with [x]-->

* [ X] I have read the [contribution documentation](https://github.com/electron/electron-packager/blob/main/CONTRIBUTING.md) for this project.
* [X ] I agree to follow the [code of conduct](https://github.com/electron/electron/blob/main/CODE_OF_CONDUCT.md) that this project follows, as appropriate.
* X[ ] I have searched the issue tracker for a bug that matches the one I want to file, without success.

### Issue Details

* **Electron Packager Version:**
Electron Packager 17.1.1
Node v18.9.0
Host Operating system: win32 10.0.22621 (x64)
* **Electron Version:**
v21.3.0
* **Operating System:**
Host Operating system: win32 10.0.22621 (x64)* 
**Last Known Working Electron Packager version:**:
  * <!-- (if applicable) e.g. 11.0.0 -->

### Expected Behavior
Since Linux/ia32 is deprecated for the version of Electron electron-package should not attempt to build it when --all is specified

### Actual Behavior
electron-packager fails with 404 error

C:\Development\YADAMU>npx electron-packager ./src yadamu --all  --out=c:\dist --overwrite
Official Linux/ia32 support is deprecated.
For more info: https://electronjs.org/blog/linux-32bit-support
Packaging app for platform darwin universal using electron v21.3.0 - Building x64 and arm64 slices now
Packaging app for platform
--- REAL FIX (ground truth)
diff --git a/src/targets.js b/src/targets.js
index f896aa4..aba6cc0 100644
--- a/src/targets.js
+++ b/src/targets.js
@@ -20,6 +20,7 @@ const buildVersions = {
   },
   linux: {
     arm64: '>= 1.8.0',
+    ia32: '<19.0.0-beta.1',
     mips64el: '^1.8.2-beta.5'
   },
   mas: {

--- Proposal A
## Root cause
`electron-packager` expands the `--all` flag to every known platform/arch combination, including **linux‑ia32**.  
Since Linux ia32 support was removed from Electron v21 (and later) the corresponding binary (`electron‑vX.Y.Z‑linux‑ia32.zip`) no longer exists, causing a 404 download error and the whole packaging run to fail.

## Fix
Skip the deprecated `linux/ia32` combination when building with `--all` (or any explicit request that includes it).  
The change is made in the core packing logic where the list of target platform/arch pairs is generated.

```diff
--- a/lib/packager.js
+++ b/lib/packager.js
@@
   // Resolve the list of platforms/architectures to build for.
   // This is where the `--all` flag is expanded.
   const getTargets = (opts) => {
-    const platforms = opts.all ? knownPlatforms : [opts.platform];
-    const archs = opts.all ? knownArchs : [opts.arch];
-
-    // Build a cartesian product of platforms × archs.
-    const targets = [];
-    for (const platform of platforms) {
-      for (const arch of archs) {
-        targets.push({ platform, arch });
-      }
-    }
-    return targets;
+    const platforms = opts.all ? knownPlatforms : [opts.platform];
+    const archs = opts.all ? knownArchs : [opts.arch];
+
+    // Build a cartesian product of platforms × archs, but filter out
+    // any combinations that are no longer supported by Electron.
+    // Linux/ia32 was officially deprecated and the binaries were removed
+    // starting with Electron 21.0.0. Attempting to download them results
+    // in a 404 error, so we simply omit this target.
+    const targets = [];
+    for (const platform of platforms) {
+      for (const arch of archs) {
+        // Skip deprecated Linux/ia32 builds.
+        if (platform === 'linux' && arch === 'ia32') {
+          // Emit a helpful warning so users understand why it is omitted.
+          console.warn(
+            'Skipping Linux/ia32 target – support was deprecated and the binary is no longer available.'
+          );
+          continue;
+        }
+        targets.push({ platform, arch });
+      }
+    }
+    return targets;
   };
 
   // -------------------------------------------------------------------------
   // Main entry point
   // -------------------------------------------------------------------------
   const packager = async (options) => {
```

* **File path** – `lib/packager.js` (the main implementation used by both the CLI and the programmatic API).  
* The patch adds a filter that drops the `linux/ia32` pair and prints a concise warning, preventing the 404 download and allowing the rest of the `--all` build to succeed.

## How to verify
1. **Install the patched version** (or run the tests against the branch containing the change).  
2. Execute a packaging run that includes `--all` on a recent Electron version (e.g., v21.3.0) where the ia32 binary is missing:

   ```bash
   npx electron-packager ./src myapp --all --out=dist --overwrite
   ```

3. **Expected outcome**  
   * The console shows a warning similar to:  
     `Skipping Linux/ia32 target – support was deprecated and the binary is no longer available.`  
   * No 404 error is reported.  
   * Packaging completes for the remaining platforms/architectures (darwin, mas, win32, linux/x64, linux/arm64, etc.).  

4. **Negative test** – Run the same command with an older Electron version that still provides the ia32 binary (e.g., v20). The warning should still appear, but the build will suc
--- Proposal B
## Root cause
`electron-packager` builds the list of target architectures for each platform in **`lib/targets.js`** (the helper that resolves the `--all` flag).  
The list for Linux always contains `['ia32', 'x64', 'armv7l', 'arm64']`.  
When `--all` is used the code iterates over that list and tries to download the corresponding Electron binaries.  

Since Linux ia32 support was deprecated (the binaries are no longer published), the download URL returns **404** and the whole packaging process fails.  
The deprecation warning that is printed (`Official Linux/ia32 support is deprecated…`) is only informational – the architecture is still kept in the target list, so the failure still occurs.

## Fix
* Introduce a small table of **deprecated architectures per platform**.
* Filter those architectures out when the target list is generated for the `--all` case (or when the platform is `all`).
* Keep the ability for a user to **explicitly request** a deprecated arch (e.g. `--platform=linux --arch=ia32`) – the warning will still be shown and the download will fail, which is the expected behaviour for an explicit request.

```diff
--- a/lib/targets.js
+++ b/lib/targets.js
@@
 const os = require('os')
 const path = require('path')
 const { archFromString, platformFromString } = require('./util')
 
+// ---------------------------------------------------------------------------
+// Deprecated architectures
+// ---------------------------------------------------------------------------
+// The map contains platforms as keys and a Set of architectures that are no
+// longer distributed by the Electron project.  When `--all` is used we simply
+// skip those architectures so that packaging does not attempt to download a
+// non‑existent binary.
+//
+// NOTE: We keep the ability to request a deprecated arch explicitly – the
+// caller will still see the deprecation warning and the download will fail,
+// which is the correct behaviour for an explicit request.
+const DEPRECATED_ARCHS = {
+  linux: new Set(['ia32'])
+}
+
 /**
  * Resolve the list of platforms/architectures that should be packaged.
  *
  * @param {Object} opts Options passed to electron-packager
  * @returns {Array<{platform:string, arch:string}>}
  */
 function resolveTargets (opts) {
@@
   // If the user asked for `--all`, we need to expand the list to every
   // supported platform/arch combination.
   if (opts.all) {
-    const platforms = ['darwin', 'mas', 'win32', 'linux']
-    const archs = ['x64', 'ia32', 'armv7l', 'arm64']
+    const platforms = ['darwin', 'mas', 'win32', 'linux']
+    const archs = ['x64', 'ia32', 'armv7l', 'arm64']
 
-    // Build a cartesian product of platforms × archs
-    for (const platform of platforms) {
-      for (const arch of archs) {
-        // Skip architectures that are not supported on the current platform
-        if (!isArchSupported(platform, arch)) continue
-        targets.push({ platform, arch })
-      }
-    }
+    // Build a cartesian product of platforms × archs, but filter out any
+    // architecture that is known to be deprecated for the given platform.
+    for (const platform of platforms) {
+      for (const arch of archs) {
+        // Skip architectures that are not supported on the current platform
+        if (!isArchSupported(platform, arch)) continue
+
+        // Skip deprecated architectures when `--all` is used.
+        if (DEPRECATED_ARCHS[platform] && DEPRECATED_ARCHS[platform].has(arch)) {
+          // Emit the same 

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
--- REAL FIX (ground truth)
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
+                    diff = (now - d).total_seconds()
+                    if diff > HALF_OF_YEAR_IN_SECONDS:
+                        d = d.replace(year=now.year + 1)
+                    elif diff < -HALF_OF_YEAR_IN_SECONDS:
+                        d = d.replace(year=now.year - 1)
             except ValueError:
                 d = datetime.datetime.strptime(s, "%b %d  %Y")
-        return self.format_date_time(d)
+        return cls.format_date_time(d)
 
     def parse_list_line_unix(self, b):
         """
diff --git a/aioftp/common.py b/aioftp/common.py
index d94ea6a..50f766f 100644
--- a/aioftp/common.py
+++ b/aioftp/common.py
@@ -36,6 +36,7 @@ DEFAULT_USER = "anonymous"
 DEFAULT_PASSWORD = "anon@"
 DEFAULT_ACCOUNT = ""
 HALF_OF_YEAR_IN_SECONDS = 15778476
+TWO_YEARS_IN_SECONDS = ((365 * 3 + 366) * 24 * 60 * 60) / 2
 
 
 def _now():
diff --git a/aioftp/errors.py b/aioftp/errors.py
index fac144a..2955cc4 100644
--- a/aioftp/errors.py
+++ b/aioftp/errors.py
@@ -2,13 +2,21 @@ from . import
--- Proposal A
## Root cause
`aioftp` parses the *MLSD* / *LIST* response by building a `datetime` from the month, day and (when the year is omitted) the **current year**.  
For a file whose modification time is reported as `Feb 29 HH:MM` the code tries to create  

```python
datetime(current_year, 2, 29, hour, minute)
```  

If the current year is **not a leap year** (e.g. 2023) this raises `ValueError: day is out of range for month`.  
The exception is caught by the surrounding iterator, causing the entry to be dropped from the result set – therefore files created on 29 Feb 2020 are invisible when the client runs in a non‑leap year.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/aioftp/parser.py
+++ b/aioftp/parser.py
@@
-        # When the year is omitted the server uses the current year.
-        # This works for most dates but fails for Feb 29 on non‑leap years.
-        if year is None:
-            year = datetime.datetime.now().year
-        try:
-            modify = datetime.datetime(
-                year, month, day, hour, minute, second, tzinfo=tzinfo
-            )
-        except ValueError as exc:
-            # Propagate the original error – the caller will treat it as a
-            # parsing failure and drop the entry.
-            raise exc
+        # When the year is omitted the server uses the current year.
+        # This works for most dates but fails for Feb 29 on non‑leap years.
+        # In that case we fall back to the most recent previous leap year.
+        if year is None:
+            year = datetime.datetime.now().year
+
+        # ``datetime`` will raise ``ValueError`` for an invalid day/month
+        # combination (e.g. 29 Feb on a non‑leap year).  Instead of bubbling the
+        # exception up and discarding the entry we try to adjust the year to a
+        # valid one.
+        try:
+            modify = datetime.datetime(
+                year, month, day, hour, minute, second, tzinfo=tzinfo
+            )
+        except ValueError:
+            # Only a Feb 29 can cause this situation when the year is omitted.
+            # Find the closest previous leap year (at most 4 years back).
+            if month == 2 and day == 29:
+                fallback_year = year
+                while not calendar.isleap(fallback_year):
+                    fallback_year -= 1
+                modify = datetime.datetime(
+                    fallback_year, month, day, hour, minute, second, tzinfo=tzinfo
+                )
+            else:
+                # Re‑raise for any other impossible date – this is a genuine
+                # parsing error.
+                raise
```

*Explanation of the changes*

1. **Detect missing year** – keep the original behaviour of assuming the current year.
2. **Wrap the `datetime` construction in a `try/except`** to catch the `ValueError` that occurs for `Feb 29` on a non‑leap year.
3. **If the problematic date is exactly 29 Feb**, compute the most recent previous leap year (by decrementing the year until `calendar.isleap` is true) and rebuild the `datetime` with that year.
4. **For any other invalid date** we re‑raise the exception – those are genuine parsing errors and should not be silently corrected.

The patch adds an import for `calendar` at the top of the file (if not already present):

```diff
@@
-import datetime
+import datetime
+import calendar
```

## How to verify
1. **Unit test** – add a test that feeds a fabricated LIST line containing a recen
--- Proposal B
## Root cause
`aioftp` parses the date part of an FTP *LIST* line in `aioftp/parser.py` (function `parse_ls_date`).  
When the listing shows a **time** instead of a year (e.g. `Feb 28 12:34`), the code builds a `datetime` object for the current year by:

```python
dt = datetime.strptime(date_str, "%b %d %H:%M")
dt = dt.replace(year=datetime.now().year)
```

If the entry is **Feb 29** (or any date that would become invalid after the year is replaced with a non‑leap year), `datetime.replace` raises `ValueError`. The exception propagates and the whole `list()` operation fails, which is why files created after 02/29/2020 cannot be listed.

## Fix
Add robust handling for the “time‑only” format:

* Parse the month/day/time string.
* Try to replace the year with the current year.
* If that fails because the day‑month combination is invalid for the current year (e.g. Feb 29 on a non‑leap year), fall back to the **previous** year (which is guaranteed to be a valid date for the same month/day).

```diff
--- a/aioftp/parser.py
+++ b/aioftp/parser.py
@@
-def parse_ls_date(date_str: str) -> datetime:
-    """
-    Parse the date part of an FTP LIST line.
-
-    The FTP LIST format shows either a year (e.g. "Jan 01 2020")
-    or a time (e.g. "Jan 01 12:34") for recent files.
-    """
-    # Try year‑first format
-    try:
-        return datetime.strptime(date_str, "%b %d %Y")
-    except ValueError:
-        pass
-
-    # Time‑only format – assume the current year
-    dt = datetime.strptime(date_str, "%b %d %H:%M")
-    return dt.replace(year=datetime.now().year)
+def parse_ls_date(date_str: str) -> datetime:
+    """
+    Parse the date part of an FTP LIST line.
+
+    The FTP LIST format shows either a year (e.g. ``"Jan 01 2020"``)
+    or a time (e.g. ``"Jan 01 12:34"``) for recent files.  When the
+    time‑only format is used we have to infer the year.  The original
+    implementation simply replaced the year with ``datetime.now().year``.
+    This crashes for *Feb 29* on non‑leap years because ``datetime.replace``
+    raises ``ValueError`` (the date does not exist in the target year).
+
+    The fix is to attempt the replacement and, if it fails, fall back to
+    the previous year – which is always a valid year for the same month/day.
+    """
+    # 1️⃣ Try the “month day year” format first.
+    try:
+        return datetime.strptime(date_str, "%b %d %Y")
+    except ValueError:
+        pass
+
+    # 2️⃣ Time‑only format – assume the current year, but handle Feb 29 safely.
+    dt = datetime.strptime(date_str, "%b %d %H:%M")
+    now_year = datetime.now().year
+    try:
+        return dt.replace(year=now_year)
+    except ValueError:
+        # Most likely ``Feb 29`` on a non‑leap year.  Use the previous year,
+        # which is guaranteed to be a valid date for the same month/day.
+        return dt.replace(year=now_year - 1)
```

### Why this works
* The normal path (year present) is unchanged.
* For the time‑only path we still assume the current year, preserving existing
  behaviour for the vast majority of listings.
* The additional `except ValueError` catches the only situation that caused the
  crash (Feb 29 on a non‑leap year) and substitutes a safe year, allowing the
  `list()` operation to continue and return all entries.

## How to verify
1. **Unit test for the parser**  
   Add a test that calls `parse_ls_date` with the string `"Feb 29 12:34"` while the
   current year is a non‑leap year (e.g. mock `datetime.now()` to retu

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
--- REAL FIX (ground truth)
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
+    Get and batch get operations will raise a ``ValueError`` if the returned item(s) are not a subclass of the model being read.
diff --git a/docs/release_notes.rst b/docs/release_notes.rst
index 9112b4e..106e22b 100644
--- a/docs/release_notes.rst
+++ b/docs/release_notes.rst
@@ -1,7 +1,7 @@
 Release Notes
 =============
 
-v5.0.0b1
+v5.0.0b2
 -------------------
 
 :date: 2020-xx-xx
diff --git a/pynamodb/__init__.py b/pynamodb/__init__.py
index 4439b51..fa730ba 100644
--- a/pynamodb/__init__.py
+++ b/pynamodb/__init__.py
@@ -7,4 +7,4 @@ A simple abstraction over DynamoDB
 """
 __author__ = 'Jharrod LaFon'
 __license__ = 'MIT'
-__version__ = '5.0.0b1'
+__version__ = '5.0.0b2'
diff --git a/pynamodb/attributes.py b/pynamodb/attributes.py
index 30aab73..a2262a7 100644
--- a/pynamodb/attributes.py
+++ b/pynamodb/attributes.py
@@ -41,6 +41,7 @@ _T = TypeVar('_T')
 _KT = TypeVar('_KT', bound=str)
 _VT = TypeVar('_VT')
 _MT = TypeVar('_MT', bound='MapAttribute')
+_ACT = TypeVar('_ACT', bound = 'AttributeContainer')
 
 _A = TypeVar('_A', bound='Attribute')
 
@@ -259,9 +260,6 @@ cl
--- Proposal A
## Root cause
`pynamodb.models.Model` uses a metaclass (`ModelMeta`) that **collects attribute definitions at class creation time** and **validates that exactly one hash‑key and one range‑key are defined**.  
When a user tries to model inheritance as in the example:

```python
class BaseModel(Model):
    class meta:                     # ← wrong name, should be `Meta`
        table_name = "BaseTable"
    id   = UnicodeAttribute(hash_key=True)
    type = UnicodeAttribute(range_key=True)

class Child1Model(BaseModel):
    id   = UnicodeAttribute(hash_key=True)   # re‑declared
    type = UnicodeAttribute(range_key=True) # re‑declared
    child1data = UnicodeAttribute()
```

two things go wrong:

1. **`meta` vs `Meta`** – the library only looks for a `Meta` inner class. Because the user defined `meta`, the table name is never set, causing a `TableError` at runtime.

2. **Attribute re‑definition** – the metaclass sees the `id` and `type` attributes again in the subclass, treats them as *new* hash/range keys and raises a validation error (“Only one hash key may be defined”).  
   PynamoDB was never designed to let you re‑declare the key attributes in a subclass; they should be **inherited** automatically.

Consequently the user cannot create “polymorphic” models that share the same DynamoDB table.

## Fix
Add support for proper inheritance of attributes (including hash‑ and range‑keys) and make the metaclass tolerant of a lower‑case `meta` definition (for backward compatibility). The fix lives in `pynamodb/models.py` (the file that defines `ModelMeta` and `Model`).

```diff
--- a/pynamodb/models.py
+++ b/pynamodb/models.py
@@
 class ModelMeta(type):
@@
-    def __new__(mcs, name, bases, attrs):
-        # Gather attributes from the class dict
-        attributes = {}
-        for key, value in list(attrs.items()):
-            if isinstance(value, Attribute):
-                attributes[key] = value
-                del attrs[key]
-
-        # Validate that there is exactly one hash key and (optionally) one range key
-        hash_keys = [k for k, a in attributes.items() if a.is_hash_key]
-        range_keys = [k for k, a in attributes.items() if a.is_range_key]
-
-        if len(hash_keys) != 1:
-            raise ModelDefinitionError('Exactly one hash key must be defined')
-        if len(range_keys) > 1:
-            raise ModelDefinitionError('At most one range key may be defined')
-
-        # Build the new class
-        new_cls = super(ModelMeta, mcs).__new__(mcs, name, bases, attrs)
-
-        # Attach the collected attributes to the class
-        for attr_name, attr in attributes.items():
-            setattr(new_cls, attr_name, attr)
-
-        # Store meta information
-        meta = getattr(new_cls, 'Meta', None)
-        if meta is None:
-            raise ModelDefinitionError('Model %s must have a Meta inner class' % name)
-
-        new_cls._hash_key_attribute = hash_keys[0]
-        new_cls._range_key_attribute = range_keys[0] if range_keys else None
-
-        return new_cls
+    def __new__(mcs, name, bases, attrs):
+        """
+        Create a new Model subclass.
+
+        * Collect ``Attribute`` objects from the class dict **and** from any
+          parent ``Model`` classes, allowing normal Python inheritance.
+        * Validate that **exactly one** hash key and **at most one** range key
+          exist across the whole inheritance chain.
+        * Accept both ``Meta`` and the historic (incorrect) ``meta`` inner
+ 
--- Proposal B
## Root cause
`pynamodb.models.Model` uses a custom metaclass (`ModelMeta`) that **always injects a dummy `Meta` class** when a subclass does not define one and **validates hash‑key / range‑key attributes only against the attributes defined directly in the subclass**.  

When a user creates a subclass that only adds extra attributes (the typical “polymorphic” use‑case) the metaclass:

1. Replaces the inherited `Meta` with an empty one, so the subclass loses the table name, billing mode, etc.  
2. Re‑examines the attribute dictionary *without* the inherited attributes, causing the validation logic to think the required hash‑key and range‑key are missing or duplicated.  
3. Because the hash‑key and range‑key are already defined on the parent, redefining them in the child raises a “duplicate key attribute” error.

Consequently, PynamoDB appears to forbid model inheritance/polymorphism even though the library’s design can support it.

## Fix
* Remove the automatic creation of a dummy `Meta` class so that Python’s normal inheritance supplies the parent’s `Meta`.  
* When collecting attributes for a new model class, start with the **inherited attribute map** and then add/override attributes defined in the subclass.  
* Perform hash‑key / range‑key validation against the **combined attribute map**, not just the subclass’s `attrs`.  

```diff
--- a/pynamodb/models.py
+++ b/pynamodb/models.py
@@
 class ModelMeta(type):
     """
     Metaclass for Model that handles attribute collection and validation.
     """
     def __new__(mcs, name, bases, attrs):
-        # ------------------------------------------------------------------
-        # NOTE: Historically ModelMeta injected a dummy ``Meta`` class when a
-        # subclass did not define one.  This broke normal Python inheritance
-        # because the child would lose the parent’s Meta (table name, etc.).
-        # It also caused attribute‑validation logic to see only the attributes
-        # defined *directly* on the subclass, leading to false “duplicate key”
-        # errors when the parent already defined the hash/range keys.
-        # ------------------------------------------------------------------
-        if "Meta" not in attrs:
-            class Meta:  # pragma: no cover – legacy behaviour
-                pass
-
-            attrs["Meta"] = Meta
-
-        # ------------------------------------------------------------------
-        # Collect attributes defined on the class itself.
-        # ------------------------------------------------------------------
-        attribute_map = {}
-        for attr_name, attr_val in attrs.items():
-            if isinstance(attr_val, Attribute):
-                attribute_map[attr_name] = attr_val
-
-        # ------------------------------------------------------------------
-        # Validate that there is exactly one hash key and at most one range key.
-        # The original implementation only looked at ``attrs`` which meant
-        # inherited attributes were ignored.
-        # ------------------------------------------------------------------
-        hash_keys = [n for n, a in attribute_map.items() if a.is_hash_key]
-        range_keys = [n for n, a in attribute_map.items() if a.is_range_key]
-
-        if len(hash_keys) != 1:
-            raise ModelDefinitionError(
-                f"{name} must have exactly one hash key attribute, found {len(hash_keys)}"
-            )
-        if len(range_keys) > 1:
-            raise Mode

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
--- Proposal B
## Root cause
The CLI stopped persisting the SQL that caused a validation failure.  
After the recent refactor the runner still returns the failing query in the
`"sql"` field of its result dictionary, but the code that writes that query to
disk was removed. Consequently no `.sql` files are created in the `logs/`
directory for either *single‑dimension* or *batch* runs.

## Fix
Add logic in **`spectacles/cli.py`** that, after each runner result is processed,
detects a failing result containing a `"sql"` key and writes the query to a
file in the `logs` directory:

* **single‑dimension mode** – create a sub‑folder per explore (`logs/<explore>/`)
  and write one file per dimension (`<dimension>.sql`).
* **batch mode** – write a single file per explore directly in `logs/`
  (`<explore>.sql`).

The implementation creates the required directories on‑demand and uses the
same naming scheme that existed before the refactor.

```diff
--- a/spectacles/cli.py
+++ b/spectacles/cli.py
@@
 import os
 import sys
 import json
+import pathlib
 import logging
@@
 from .runner import run_explore
@@
 def main():
@@
-    results = run_explore(project, mode, **run_opts)
-    # existing processing of results …
+    results = run_explore(project, mode, **run_opts)
+    # existing processing of results …
+
+    # ------------------------------------------------------------------
+    # Persist failing SQL queries to the logs directory.
+    # ------------------------------------------------------------------
+    _persist_failing_sql(results, mode)
+    # ------------------------------------------------------------------
 
     # existing exit handling …
@@
 def _persist_failing_sql(results: list[dict], mode: str) -> None:
@@
-    pass
+    """
+    Write the SQL that caused a validation failure to a file under the ``logs``
+    directory.
+
+    * In *single‑dimension* mode a folder per explore is created and each
+      dimension's SQL is saved as ``<dimension>.sql`` inside that folder.
+    * In *batch* mode a single file per explore is created directly under
+      ``logs`` named ``<explore>.sql``.
+    """
+    logs_root = pathlib.Path("logs")
+    logs_root.mkdir(exist_ok=True)
+
+    for result in results:
+        # Only interested in failures that contain the SQL text.
+        sql_text = result.get("sql")
+        if not sql_text:
+            continue
+
+        # ``status`` is the conventional key used by the runner to indicate
+        # success/failure.  If it is missing we conservatively assume failure.
+        status = result.get("status", "failed")
+        if status == "passed":
+            continue
+
+        explore_name = result.get("explore")
+        if not explore_name:
+            # Without an explore name we cannot construct a sensible path.
+            continue
+
+        if mode == "single":
+            # Expect a ``dimension`` key in single‑dimension mode.
+            dimension_name = result.get("dimension", "unknown")
+            explore_dir = logs_root / explore_name
+            explore_dir.mkdir(parents=True, exist_ok=True)
+            file_path = explore_dir / f"{dimension_name}.sql"
+        else:  # batch (or any other mode that behaves like batch)
+            file_path = logs_root / f"{explore_name}.sql"
+
+        try:
+            file_path.write_text(sql_text, encoding="utf-8")
+            logging.debug(
+                "Saved failing SQL for explore=%s %s to %s",
+                explore_name,
+                f"

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

--- REAL FIX (ground truth)
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
+                "Unable to find `less` in the PATH. Check out "
+                "{} for more info.".format(
+                    format_link("https://man.dvc.org/pipeline/show")
+                )
+            )
+        else:
+            pager = DEFAULT_PAGER
+
+    if pager == DEFAULT_PAGER:
+        # if pager is less (i.e. default), regardless of `$LESS`, apply `-RS`.
+        # `-R` is required to render ansi escape sequences for exp show
+        # and, `-S` is required for horizontal scrolling.
+        less_env = bool(os.getenv(LESS))
+        pager = prepare_default_pager(
+            ansi_escapes=True,
+            chop_long_lines=True,
+            quit_if_one_screen=not less_env,
+            no_init=not less_env,
         )
-    )
-    return pydoc.plainpager
+
+    return pager
 
 
 def pager(text: str) -> None:
-    find_pager()(text)
+    _pager = find_pager()
+    logger.trace(f"Using pager: '{_pager}'")  # type: ignore[attr-defined]
+    make_pager(_pager)(text)
 
 
 class DvcPager(Pager):

--- Proposal A
## Root cause
`dvc` always pipes command output through a pager (e.g. `less`) when the command calls `click.echo_via_pager`.  
There is no way for the user to disable this globally, and the pager is invoked even when the output would fit on a single terminal screen. This makes the UX noisy and diverges from Git’s behaviour, which only uses a pager when the output exceeds the terminal height.

## Fix
* Add a global `--no-pager` flag to the top‑level CLI group and store the value in the Click context.  
* Introduce a small helper (`dvc/cli/pager.py`) that decides whether paging is required:
  * If `--no-pager` is set → never page.  
  * Otherwise compare the number of output lines with the terminal height (using `shutil.get_terminal_size`).  
* Replace direct calls to `click.echo_via_pager` in commands that produce potentially large output (`exp show`, `dag`, `list`, `stage list`) with the new helper `pager.echo`.  
* The helper falls back to plain `click.echo` when paging is not needed.

### Unified diff
```diff
--- a/dvc/cli/main.py
+++ b/dvc/cli/main.py
@@
-import click
+import click
+
+# expose the pager helper for sub‑commands
+from dvc.cli import pager
 
@@
-@click.group()
+@click.group()
+@click.option(
+    "--no-pager",
+    is_flag=True,
+    default=False,
+    help="Disable paging of command output.",
+)
 @click.version_option(version=__version__)
 def dvc(no_pager):
     """
@@
-    pass
+    # Store the flag in the Click context so sub‑commands can read it.
+    click.get_current_context().obj = {"no_pager": no_pager}
+    # No further action needed; sub‑commands will use the pager helper.
+    pass
--- a/dvc/cli/pager.py
+++ b/dvc/cli/pager.py
@@
-"""
-Utility functions for handling paging of command output.
-"""
+"""
+Utility functions for handling paging of command output.
+
+The logic mirrors Git’s behaviour: only invoke a pager when the output
+does not fit into the current terminal window, unless the user explicitly
+disables paging via ``--no-pager``.
+"""
+
+from __future__ import annotations
+
+import shutil
+import click
+from typing import Optional
+
+
+def _terminal_height() -> Optional[int]:
+    """
+    Return the height of the current terminal in lines, or ``None`` if it
+    cannot be determined (e.g. when stdout is not a TTY).
+    """
+    try:
+        size = shutil.get_terminal_size()
+        return size.lines
+    except OSError:
+        return None
+
+
+def _needs_pager(text: str, no_pager: bool) -> bool:
+    """
+    Decide whether the given *text* should be sent through a pager.
+
+    * If ``no_pager`` is ``True`` we never page.
+    * Otherwise we compare the number of lines in *text* with the terminal
+      height.  When the terminal size cannot be determined we conservatively
+      page.
+    """
+    if no_pager:
+        return False
+
+    lines = text.count("\n") + 1
+    height = _terminal_height()
+    if height is None:
+        # Unable to determine terminal size – assume paging is needed.
+        return True
+    return lines > height
+
+
+def echo(text: str, *, no_pager: bool = False) -> None:
+    """
+    Echo *text* to the console, paging it only when appropriate.
+    """
+    if _needs_pager(text, no_pager):
+        click.echo_via_pager(text)
+    else:
+        click.echo(text)
--- a/dvc/cli/exp.py
+++ b/dvc/cli/exp.py
@@
-import click
+import click
 from dvc.repo import Repo
+from dvc.cli import pager
 
@@
-@exp.command("show")
+@exp.command("show")
 @click.pass_context
 d
--- Proposal B
## Root cause
`dvc` uses a generic “pager” helper (wrapping `click.echo_via_pager`) for commands that may produce long output (`dvc dag`, `dvc exp show`, `dvc list`, `dvc stage list`).  
The helper always invokes the pager regardless of the actual size of the output, and there is no global way for users to disable it.  
Consequently:

* Users see a pager even when the output fits on a single screen.  
* There is no `--no-pager` flag that works across all commands that use the pager.  

The issue therefore requires two things:

1. **A global `--no-pager` flag** that any command can query.  
2. **Automatic suppression of the pager** when the rendered output would fit into the current terminal size.

Both can be solved by centralising pager logic in a small utility module and wiring the flag into the top‑level Click group.

---

## Fix
```diff
--- a/dvc/cli.py
+++ b/dvc/cli.py
@@
-import click
+import click
+
+# NOTE: The ``--no-pager`` flag is added to the top‑level command group.
+# It is stored in the Click context object (``ctx.obj``) so that any
+# sub‑command can read it without having to declare the option again.
 
 @click.group(context_settings=dict(help_option_names=['-h', '--help']))
-@click.version_option(version=__version__)
+@click.version_option(version=__version__)
+@click.option(
+    '--no-pager',
+    is_flag=True,
+    default=False,
+    help='Disable paging of command output even if the command supports it.',
+)
 @click.pass_context
 def dvc(ctx, **kwargs):
@@
-    ctx.obj = {}
+    # ``ctx.obj`` is a mutable dict that is passed to all sub‑commands.
+    # Initialise it if it does not exist (e.g. when invoked programmatically).
+    ctx.obj = ctx.obj or {}
+    # Propagate the ``--no-pager`` flag.
+    ctx.obj['no_pager'] = kwargs.get('no_pager', False)
```

```diff
--- a/dvc/utils/pager.py
+++ b/dvc/utils/pager.py
@@
-import click
+import click
+import shutil
+from typing import Optional
+
+def _output_fits_terminal(output: str) -> bool:
+    """
+    Return ``True`` if the number of lines in *output* is less than or equal to
+    the height of the current terminal.  If the terminal size cannot be
+    determined (e.g. when stdout is not a TTY), conservatively return ``False``
+    so that the pager is used.
+    """
+    try:
+        rows, _ = shutil.get_terminal_size()
+    except OSError:
+        # No terminal attached (e.g. piped output) – fall back to paging.
+        return False
+
+    line_count = output.count('\n') + 1  # ``splitlines`` would also work.
+    return line_count <= rows
+
+def echo(output: str, *, no_pager: bool = False) -> None:
+    """
+    Print *output* using a pager when appropriate.
+
+    Parameters
+    ----------
+    output:
+        The string to be displayed.
+    no_pager:
+        If ``True`` the pager is never used, regardless of output size.
+    """
+    # Respect the explicit ``--no-pager`` flag.
+    if no_pager:
+        click.echo(output)
+        return
+
+    # Auto‑disable the pager when the output fits on one screen.
+    if _output_fits_terminal(output):
+        click.echo(output)
+        return
+
+    # Fallback to the standard Click pager.
+    click.echo_via_pager(output)
```

```diff
--- a/dvc/command/dag.py
+++ b/dvc/command/dag.py
@@
-import click
+import click
 from dvc.repo import Repo
+from dvc.utils.pager import echo as pager_echo
 
@@
-@click.command()
+@click.command()
 @click.pass_context
 def dag(ctx, **kwargs):
@@
-    click.echo_via_pager(output)
+ 

=== TASK bitshifter__glam-rs-187 | repo bitshifter/glam-rs | rust
--- Issue
".fract" for vector types
(Minor convenience thing, absolutely not urgent in any way).

In GLSL, fract is quite commonly used, in things like this hash function in https://www.shadertoy.com/view/tlGfzd:

```glsl
// by Dave_Hoskins
float hash12(vec2 p)
{
    vec3 p3  = fract(vec3(p.xyx) * .1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
```

It just returns the fractional value, or `x - floor(x)`:  (https://www.khronos.org/registry/OpenGL-Refpages/gl4/html/fract.xhtml).

Having this in glam would be convenient when translating GLSL shaders to rust-gpu, for example.
--- REAL FIX (ground truth)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index b2be6d4..e323c93 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -9,6 +9,8 @@ The format is based on [Keep a Changelog], and this project adheres to
 
 ### Added
 
+* Added `fract()` method to float vector types which return a vector containing
+  `self - self.floor()`.
 * Added optional support for the `approx` crate. Note that all glam types
   implement their own `abs_diff_eq()` method without requiring the `approx`
   dependency.
diff --git a/src/core/traits/scalar.rs b/src/core/traits/scalar.rs
index fc1a399..1b2c18e 100644
--- a/src/core/traits/scalar.rs
+++ b/src/core/traits/scalar.rs
@@ -278,8 +278,7 @@ impl FloatEx for f64 {
     }
     #[inline(always)]
     fn acos_approx(self) -> Self {
-        // TODO: clamp range
-        f64::acos(self)
+        f64::acos(self.max(-1.0).min(1.0))
     }
 }
 
@@ -383,4 +382,8 @@ fn test_scalar_acos() {
     // input is clamped to -1.0..1.0
     assert_approx_eq!(2.0_f32.acos_approx(), 0.0);
     assert_approx_eq!((-2.0_f32).acos_approx(), core::f32::consts::PI);
+
+    // input is clamped to -1.0..1.0
+    assert_eq!(2.0_f64.acos_approx(), 0.0);
+    assert_eq!((-2.0_f64).acos_approx(), core::f64::consts::PI);
 }
diff --git a/src/mat3.rs b/src/mat3.rs
index e961fb7..f71bf3f 100644
--- a/src/mat3.rs
+++ b/src/mat3.rs
@@ -29,7 +29,8 @@ macro_rules! define_mat3_struct {
         ///
         /// This 3x3 matrix type features convenience methods for creating and using linear and
         /// affine transformations. If you are primarily dealing with 2D affine transformations the
-        /// [`Affine2`] type is much faster and more space efficient than using a 3x3 matrix.
+        /// [`Affine2`][crate::Affine2] type is much faster and more space efficient than using a
+        /// 3x3 matrix.
         ///
         /// Linear transformations including 3D rotation and scale can be created using methods
         /// such as [`Self::from_diagonal()`], [`Self::from_quat()`], [`Self::from_axis_angle()`],
diff --git a/src/mat4.rs b/src/mat4.rs
index fe2b526..cddded6 100644
--- a/src/mat4.rs
+++ b/src/mat4.rs
@@ -741,7 +741,8 @@ type InnerF32 = Columns4<XYZW<f32>>;
 ///
 /// This 4x4 matrix type features convenience methods for creating and using affine transforms and
 /// perspective projections. If you are primarily dealing with 3D affine transformations
-/// condidering using [`Affine3A`] which is faster tha a 4x4 matrix for some affine operations.
+/// condidering using [`Affine3A`][crate::Affine3A] which is faster tha a 4x4 matrix for some
+/// affine operations.
 ///
 /// Affine transformations including 3D translation, rotation and scale can be created
 /// using methods such as [`Self::from_translation()`], [`Self::from_quat()`],
@@ -813,7 +814,8 @@ type InnerF64 = Columns4<XYZW<f64>>;
 ///
 /// This 4x4 matrix type features convenience methods for creating and using affine transforms and
 /// perspective projections. If you are primarily dealing with 3D a
--- Proposal A
## Root cause
`glam` provides scalar `fract` (via the `FloatExt` trait) and vector methods such as `floor`, `ceil`, `round`, etc., but it does **not** expose a `fract` method on its vector types (`Vec2`, `Vec3`, `Vec4`, …).  GLSL code frequently uses `fract` on vectors, so translating GLSL shaders to Rust (e.g. with rust‑gpu) requires a manual `x - x.floor()` pattern.  Adding a proper `fract` method to the vector types fixes the convenience issue.

## Fix
Add a `fract` method to all public vector types.  The implementation is simply `self - self.floor()`, re‑using the already‑existing `floor` method.

```diff
--- a/crates/glam/src/vec2.rs
+++ b/crates/glam/src/vec2.rs
@@
 impl Vec2 {
     /// Returns a vector with each component rounded toward negative infinity.
     #[inline]
     pub fn floor(self) -> Self {
         Self::new(self.x.floor(), self.y.floor())
     }
+
+    /// Returns a vector containing the fractional part of each component.
+    ///
+    /// This is equivalent to `self - self.floor()`.
+    ///
+    /// # Example
+    ///
+    /// ```
+    /// # use glam::Vec2;
+    /// let v = Vec2::new(1.7, -2.3);
+    /// assert_eq!(v.fract(), Vec2::new(0.7, -0.3));
+    /// ```
+    #[inline]
+    pub fn fract(self) -> Self {
+        self - self.floor()
+    }
 }
 
 impl Default for Vec2 {
     #[inline]
     fn default() -> Self {
--- a/crates/glam/src/vec3.rs
+++ b/crates/glam/src/vec3.rs
@@
 impl Vec3 {
     /// Returns a vector with each component rounded toward negative infinity.
     #[inline]
     pub fn floor(self) -> Self {
         Self::new(self.x.floor(), self.y.floor(), self.z.floor())
     }
+
+    /// Returns a vector containing the fractional part of each component.
+    ///
+    /// This is equivalent to `self - self.floor()`.
+    ///
+    /// # Example
+    ///
+    /// ```
+    /// # use glam::Vec3;
+    /// let v = Vec3::new(1.7, -2.3, 3.14);
+    /// assert_eq!(v.fract(), Vec3::new(0.7, -0.3, 0.14));
+    /// ```
+    #[inline]
+    pub fn fract(self) -> Self {
+        self - self.floor()
+    }
 }
 
 impl Default for Vec3 {
     #[inline]
     fn default() -> Self {
--- a/crates/glam/src/vec4.rs
+++ b/crates/glam/src/vec4.rs
@@
 impl Vec4 {
     /// Returns a vector with each component rounded toward negative infinity.
     #[inline]
     pub fn floor(self) -> Self {
         Self::new(self.x.floor(), self.y.floor(), self.z.floor(), self.w.floor())
     }
+
+    /// Returns a vector containing the fractional part of each component.
+    ///
+    /// This is equivalent to `self - self.floor()`.
+    ///
+    /// # Example
+    ///
+    /// ```
+    /// # use glam::Vec4;
+    /// let v = Vec4::new(1.7, -2.3, 3.14, -0.9);
+    /// assert_eq!(v.fract(), Vec4::new(0.7, -0.3, 0.14, -0.9));
+    /// ```
+    #[inline]
+    pub fn fract(self) -> Self {
+        self - self.floor()
+    }
 }
 
 impl Default for Vec4 {
     #[inline]
     fn default() -> Self {
```

*The same change should be applied to any other vector types (`DVec2`, `DVec3`, `DVec4`, `IVec*`, etc.) if they expose `floor`.  For brevity only the primary `f32` vectors are shown.*

## How to verify
1. **Compile the crate** – run `cargo test --all-features` to ensure the new methods don’t break existing code.
2. **Add unit tests** (e.g. in `tests/fract.rs`):

```rust
#[test]
fn test_vec_fract() {
    use glam::{Vec2, Vec3, Vec4};

    let v2 = Vec2::new(1.7, -2.3);
    assert_eq!(v2.fract(), Vec2::new(0.7, -0.3));

    let v3 = Vec3::new(1.7, -2.3, 3
--- Proposal B
## Root cause
`glam` provides scalar `fract()` (via the inherent `f32`/`f64` method) but does not expose a component‑wise version for its vector types (`Vec2`, `Vec3`, `Vec3A`, `Vec4`).  Shader code frequently uses `fract(v)` on vectors, so the lack of a corresponding method forces users to write `v.map(|c| c.fract())` or similar boiler‑plate.  Adding a simple `fract` method to each vector type resolves the convenience issue.

## Fix
```diff
--- a/src/vec2.rs
+++ b/src/vec2.rs
@@
 impl Vec2 {
     #[inline]
     pub fn new(x: f32, y: f32) -> Self {
         Self { x, y }
     }
@@
     #[inline]
     pub fn floor(self) -> Self {
         Self::new(self.x.floor(), self.y.floor())
     }
+
+    /// Returns the fractional part of each component.
+    ///
+    /// This is equivalent to `self - self.floor()` and matches the GLSL
+    /// `fract` function.
+    ///
+    /// # Example
+    ///
+    /// ```
+    /// # use glam::Vec2;
+    /// let v = Vec2::new(1.7, -2.3);
+    /// assert_eq!(v.fract(), Vec2::new(0.7, -0.3));
+    /// ```
+    #[inline]
+    pub fn fract(self) -> Self {
+        Self::new(self.x.fract(), self.y.fract())
+    }
*** End of File
--- a/src/vec3.rs
+++ b/src/vec3.rs
@@
 impl Vec3 {
     #[inline]
     pub fn new(x: f32, y: f32, z: f32) -> Self {
         Self { x, y, z }
     }
@@
     #[inline]
     pub fn floor(self) -> Self {
         Self::new(self.x.floor(), self.y.floor(), self.z.floor())
     }
+
+    /// Returns the fractional part of each component.
+    ///
+    /// Mirrors the GLSL `fract` function.
+    #[inline]
+    pub fn fract(self) -> Self {
+        Self::new(self.x.fract(), self.y.fract(), self.z.fract())
+    }
*** End of File
--- a/src/vec3a.rs
+++ b/src/vec3a.rs
@@
 impl Vec3A {
     #[inline]
     pub fn new(x: f32, y: f32, z: f32) -> Self {
         Self { x, y, z }
     }
@@
     #[inline]
     pub fn floor(self) -> Self {
         Self::new(self.x.floor(), self.y.floor(), self.z.floor())
     }
+
+    /// Returns the fractional part of each component.
+    ///
+    /// This matches the behaviour of GLSL's `fract`.
+    #[inline]
+    pub fn fract(self) -> Self {
+        Self::new(self.x.fract(), self.y.fract(), self.z.fract())
+    }
*** End of File
--- a/src/vec4.rs
+++ b/src/vec4.rs
@@
 impl Vec4 {
     #[inline]
     pub fn new(x: f32, y: f32, z: f32, w: f32) -> Self {
         Self { x, y, z, w }
     }
@@
     #[inline]
     pub fn floor(self) -> Self {
         Self::new(
             self.x.floor(),
             self.y.floor(),
             self.z.floor(),
             self.w.floor(),
         )
     }
+
+    /// Returns the fractional part of each component.
+    ///
+    /// Equivalent to the GLSL `fract` function.
+    #[inline]
+    pub fn fract(self) -> Self {
+        Self::new(
+            self.x.fract(),
+            self.y.fract(),
+            self.z.fract(),
+            self.w.fract(),
+        )
+    }
*** End of File
--- a/tests/fract.rs
+++ b/tests/fract.rs
@@
 #[test]
 fn test_vec2_fract() {
     let v = glam::Vec2::new(1.3, -2.7);
-    // placeholder – will be replaced by the real test once the method exists
-    assert_eq!(v, v);
+    assert_eq!(v.fract(), glam::Vec2::new(0.3, -0.7));
 }
 
 #[test]
 fn test_vec3_fract() {
     let v = glam::Vec3::new(3.9, 0.0, -1.2);
-    assert_eq!(v, v);
+    assert_eq!(v.fract(), glam::Vec3::new(0.9, 0.0, -0.2));
 }
 
 #[test]
 fn test_vec3a_fract() {
     let v = glam::Vec3A::new(-0.5, 2.25, 4.75);
-    assert_eq!(v, v);
+    a

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
--- REAL FIX (ground truth)
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
+                        from_comments = []
+
+                    for as_import in as_imports[from_import]:
+                        specific_comment = (
+                            parsed.categorized_comments["nested"]
+                            .get(module, {})
+                            .pop(as_import, None)
                         )
-                        for as_import in as_imports[from_import]
-                    )
+                        if specific_comment:
+                            from_comments.append(specific_comment)
+
+                        output.append(
+                            wrap.line(
+                                with_comments(
+                                    from_comments,
+                                    import_start + as_import,
+                                    removed=config.ignore_comments,
+                                    comment_prefix=config.comment_prefix,
+                                ),
+                                parsed.line_separator,

--- Proposal A
## Root cause
`isort` merges duplicate import statements by combining their imported names.  
During this merge it only keeps a **single** `comment` attribute on the
`ImportStatement`. When the second import has an inline comment (e.g.
`# DRY`) that comment overwrites the comment from the first import
(`# NOT DRY`). Later, when the statement is rendered, the same comment is
attached to **all** imported names, producing:

```python
from com.my_lovely_company.my_lovely_team.my_lovely_project.my_lovely_component import (
    MyLovelyCompanyTeamProjectComponent,  # DRY
    MyLovelyCompanyTeamProjectComponent as component,  # DRY
)
```

Thus the bug is caused by loss of *per‑name* comments when duplicate
imports are merged.

## Fix
* Preserve comments that belong to individual imported names.
* Keep a separate `name_comments` dictionary on `ImportStatement`.
* Merge these dictionaries when two statements are combined.
* Parse inline comments into `name_comments` while reading the source.
* Render per‑name comments when formatting the import.

```diff
--- a/isort/statement.py
+++ b/isort/statement.py
@@
-    def merge(self, other: "ImportStatement") -> None:
-        """Merge another ImportStatement into this one."""
-        # Combine imported names
-        self.names.extend(other.names)
-        self.as_names.update(other.as_names)
-        # If the other import has a comment, prefer it (buggy)
-        if other.comment:
-            self.comment = other.comment
+    def merge(self, other: "ImportStatement") -> None:
+        """Merge another ImportStatement into this one.
+
+        Preserve comments that are attached to individual imported names.
+        ``ImportStatement`` stores a single ``comment`` attribute which
+        historically represented a comment that applied to the whole import
+        line.  When two imports of the same module are merged, each may have
+        a comment that is actually attached to a specific name (e.g.
+        ``MyClass  # comment``).  The previous implementation overwrote
+        ``self.comment`` with ``other.comment``, causing the comment from the
+        later import to be applied to *all* names.  This resulted in the bug
+        where both names received the ``# DRY`` comment.
+
+        The fix stores per‑name comments in ``self.name_comments`` (a dict
+        mapping the imported name to its comment).  When merging we combine
+        the name lists and merge the comment dictionaries, keeping existing
+        comments for names that already have them.
+        """
+        # Combine imported names
+        self.names.extend(other.names)
+        self.as_names.update(other.as_names)
+
+        # Ensure we have a dict for per‑name comments
+        if not hasattr(self, "name_comments"):
+            self.name_comments = {}
+        if not hasattr(other, "name_comments"):
+            other.name_comments = {}
+
+        # Merge per‑name comments, preferring existing comments on self
+        for name, comment in other.name_comments.items():
+            self.name_comments.setdefault(name, comment)
+
+        # Preserve a trailing comment that applies to the whole import line only
+        # (e.g. ``# noqa``).  If ``self`` already has such a comment we keep it,
+        # otherwise we take ``other``'s comment.
+        if other.comment and not self.comment:
+            self.comment = other.comment
```

```diff
--- a/isort/parser.py
+++ b/isort/parser.py
@@
-        # existing logic extracts c
--- Proposal B
## Root cause
`isort` merges duplicate `from … import …` statements into a single statement.  
During this merge the comment attached to each imported name is stored on the
`ImportFromItem` objects, but the rendering code only uses the comment of the
*last* item when building the final line. Consequently, when two imports from
the same module each have an inline comment, the comment from the second import
overwrites the first and both names end up with the same comment (`# DRY`).

## Fix
* Preserve per‑name comments when merging duplicate imports.
* Render each name together with its own comment instead of using a single
  comment for the whole import line.

```diff
--- a/isort/statement.py
+++ b/isort/statement.py
@@
 class ImportFromItem:
     def __init__(self, name: str, as_name: Optional[str] = None,
                  comment: Optional[str] = None):
         self.name = name
         self.as_name = as_name
-        self.comment = comment
+        # Inline comment that appears after this particular imported name.
+        # ``None`` means no comment.
+        self.comment = comment
 
 
 class ImportFromStatement(Statement):
@@
     def merge(self, other: "ImportFromStatement") -> None:
         """Merge another ImportFromStatement into this one."""
         assert self.module == other.module
-        # Merge the imported names, keeping the order of the original statement.
-        existing_names = {item.name for item in self.names}
-        for item in other.names:
-            if item.name not in existing_names:
-                self.names.append(item)
-                existing_names.add(item.name)
-        # If the other statement has a comment, prefer it (the later comment wins).
-        if other.comment:
-            self.comment = other.comment
+        # Merge the imported names, keeping the order of the original statement.
+        # Each name may have its own inline comment, which must be preserved.
+        existing_names = {item.name for item in self.names}
+        for item in other.names:
+            if item.name not in existing_names:
+                # New name – keep its comment as‑is.
+                self.names.append(item)
+                existing_names.add(item.name)
+            else:
+                # Name already present – we need to keep the comment that already
+                # exists on the original item *and* the comment from the incoming
+                # item (if any).  The original comment is more likely to be the
+                # correct one because it appears earlier in the file, but we do
+                # not want to lose the later comment entirely.  Therefore we
+                # concatenate them, separated by a space, unless they are equal.
+                for existing_item in self.names:
+                    if existing_item.name == item.name:
+                        if item.comment and item.comment != existing_item.comment:
+                            # Preserve both comments in a deterministic order.
+                            combined = f"{existing_item.comment or ''} {item.comment}".strip()
+                            existing_item.comment = combined
+                        break
+        # ``ImportFromStatement`` itself does not have a top‑level comment; any
+        # comment attached to the whole statement is stored on the individual
+        # items, so we no longer override ``self.comment`` here.
*** End of File
--- a/isort/output.py
+++ b/isort/output.py
@@
 def format_

=== TASK urlaubsverwaltung__urlaubsverwaltung-4858 | repo urlaubsverwaltung/urlaubsverwaltung | java
--- Issue
Überstundenabbau über Api abfragbar

**Is your feature request related to a problem? Please describe.**
Damit Mitarbeiter kummuliert über einen bestimmten Zeitraum wissen, wie viele Überstunden sie abgebaut haben, braucht das Tool die Anzahl an Stunden/Minuten welche in einer Abwesenheit  vom Type `Überstundenabbau`  konfiguriert wurden, in der API.

**Describe the solution you'd like**
Da aktuell die Api `/api/persons/{personId}/absences` schon benutzt wird, wäre es dufte, wenn diese Information in diese API mitgegeben wird. Es könnte z.B. in einem optionalem Feld `hours` oder `duration` eingeführt werden. Dafür könnte `java.time.Duration` verwendet werden. 

**Optionally describe alternatives you've considered**
Eine separate API in der Details zu einem bestimmten Abwesenheit abgefragt werden können. Das würde funktionieren, jedoch müsste das Tool dann für jede Abwesenheit vom Typ `Überstundenabbau` nochmal separat die Stunden/Minuten abfragen. Das ist in meinen Augen ineffizienter, aber dafür wäre die API "sauberer".


--- REAL FIX (ground truth)
diff --git a/src/main/java/org/synyx/urlaubsverwaltung/absence/AbsenceApiController.java b/src/main/java/org/synyx/urlaubsverwaltung/absence/AbsenceApiController.java
index 82daab6ad..2d522fb74 100644
--- a/src/main/java/org/synyx/urlaubsverwaltung/absence/AbsenceApiController.java
+++ b/src/main/java/org/synyx/urlaubsverwaltung/absence/AbsenceApiController.java
@@ -14,6 +14,7 @@ import org.springframework.web.bind.annotation.RequestParam;
 import org.springframework.web.bind.annotation.RestController;
 import org.springframework.web.server.ResponseStatusException;
 import org.synyx.urlaubsverwaltung.api.RestControllerAdviceMarker;
+import org.synyx.urlaubsverwaltung.application.vacationtype.VacationCategory;
 import org.synyx.urlaubsverwaltung.period.DayLength;
 import org.synyx.urlaubsverwaltung.person.Person;
 import org.synyx.urlaubsverwaltung.person.PersonService;
@@ -25,6 +26,8 @@ import java.util.function.Predicate;
 import java.util.stream.Stream;
 
 import static org.springframework.hateoas.MediaTypes.HAL_JSON_VALUE;
+import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.linkTo;
+import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.methodOn;
 import static org.springframework.http.HttpStatus.BAD_REQUEST;
 import static org.springframework.http.MediaType.APPLICATION_JSON_VALUE;
 import static org.synyx.urlaubsverwaltung.absence.AbsenceDto.AbsenceType.NO_WORKDAY;
@@ -160,7 +163,14 @@ public class AbsenceApiController {
     private AbsenceDto toAbsenceDto(LocalDate date, DayLength dayLength, AbsencePeriod.RecordInfo recordInfo) {
         final AbsenceDto.AbsenceType type = toAbsenceTypes(recordInfo.getAbsenceType());
         final String status = recordInfo.getStatus().name();
-        return new AbsenceDto(date, type, recordInfo.getId().orElse(null), status, dayLength, recordInfo.getCategory().orElse(null), recordInfo.getTypeId().orElse(null));
+        final String category = recordInfo.getCategory().orElse(null);
+        AbsenceDto absenceDto = new AbsenceDto(date, type, recordInfo.getId().orElse(null), status, dayLength, category, recordInfo.getTypeId().orElse(null));
+
+        Optional<Long> absenceId = recordInfo.getId();
+        if (VacationCategory.OVERTIME.name().equals(category) && absenceId.isPresent()) {
+            absenceDto.add(linkTo(methodOn(OvertimeAbsenceApiController.class).overtimeAbsence(recordInfo.getPerson().getId(), absenceId.get())).withRel("overtime"));
+        }
+        return absenceDto;
     }
 
     private List<AbsenceDto.AbsenceType> toAbsenceTypes(List<String> dayAbsenceTypes) {
diff --git a/src/main/java/org/synyx/urlaubsverwaltung/absence/OvertimeAbsenceApiController.java b/src/main/java/org/synyx/urlaubsverwaltung/absence/OvertimeAbsenceApiController.java
new file mode 100644
index 000000000..2bf5988c3
--- /dev/null
+++ b/src/main/java/org/synyx/urlaubsverwaltung/absence/OvertimeAbsenceApiController.java
@@ -0,0 +1,92 @@
+package org.synyx.urlaubsverwaltung.abs
--- Proposal A
## Root cause
The public API `/api/persons/{personId}/absences` returns a list of absences but does **not** expose the configured amount of time for absences of type **Überstundenabbau** (overtime‑reduction).  
The underlying `Absence` entity already stores this value (as a `java.time.Duration` or a numeric hour/minute field), but the DTO that is sent to the client (`AbsenceDto`) does not contain a corresponding property, therefore the information cannot be retrieved via the API.

## Fix
Add an optional `Duration duration` field to the DTO and map it from the entity.  
The field is only filled for absences of type `OVERTIME_REDUCTION`; for all other types it stays `null` and is omitted from the JSON output.

```diff
--- a/src/main/java/org/urlaubsverwaltung/absence/web/AbsenceDto.java
+++ b/src/main/java/org/urlaubsverwaltung/absence/web/AbsenceDto.java
@@
 import com.fasterxml.jackson.annotation.JsonInclude;
 import java.time.LocalDate;
+import java.time.Duration;
 import java.util.List;
@@
 @JsonInclude(JsonInclude.Include.NON_NULL)
 public class AbsenceDto {
 
     private Long id;
@@
     private String type;
 
+    /**
+     * Only set for absences of type {@code OVERTIME_REDUCTION}. Represents the amount of overtime that is
+     * reduced by this absence (e.g. {@code PT2H30M} for 2 hours 30 minutes). For all other absence types the
+     * field stays {@code null} and is omitted from the JSON payload.
+     */
+    private Duration duration;
@@
     private List<AbsenceCommentDto> comments;
 
@@
     public String getType() {
         return type;
     }
 
     public void setType(String type) {
         this.type = type;
     }
+
+    public Duration getDuration() {
+        return duration;
+    }
+
+    public void setDuration(Duration duration) {
+        this.duration = duration;
+    }
 
     public List<AbsenceCommentDto> getComments() {
         return comments;
     }
```

```diff
--- a/src/main/java/org/urlaubsverwaltung/absence/web/AbsenceMapper.java
+++ b/src/main/java/org/urlaubsverwaltung/absence/web/AbsenceMapper.java
@@
 import org.urlaubsverwaltung.absence.Absence;
 import org.urlaubsverwaltung.absence.AbsenceComment;
 import org.urlaubsverwaltung.absence.AbsenceType;
+import java.time.Duration;
 
 public class AbsenceMapper {
 
     public static AbsenceDto mapToDto(Absence absence) {
         AbsenceDto dto = new AbsenceDto();
@@
         dto.setType(absence.getType().name());
 
+        // Only expose the configured duration for overtime‑reduction absences.
+        if (absence.getType() == AbsenceType.OVERTIME_REDUCTION) {
+            Duration duration = absence.getDuration(); // the entity already stores a Duration
+            dto.setDuration(duration);
+        }
+
         dto.setComments(mapComments(absence.getComments()));
 
         return dto;
     }
```

*If the `Absence` entity does not yet have a `Duration getDuration()` method, add it (and the corresponding field) in the entity class. The change below is only required if the field is missing.*

```diff
--- a/src/main/java/org/urlaubsverwaltung/absence/Absence.java
+++ b/src/main/java/org/urlaubsverwaltung/absence/Absence.java
@@
 import jakarta.persistence.*;
 import java.time.LocalDate;
+import java.time.Duration;
 
 @Entity
 public class Absence {
@@
     @Column(name = "type", nullable = false)
     @Enumerated(EnumType.STRING)
     private AbsenceType type;
 
+    /**
+     * Only used when {@link #type} is {@code OVERTIME_REDUCTION}. Stores 
--- Proposal B
**## Root cause**

The REST endpoint `/api/persons/{personId}/absences` returns a list of *absence* DTOs that contain only generic information (id, dates, type, …).  
For absences of the category **Überstundenabbau** (overtime reduction) the domain model already stores the amount of time that is reduced (a `java.time.Duration`).  
Because the DTO does not expose this field, callers cannot see how many overtime‑hours have been taken via the API.

**## Fix (a unified diff with your best‑guess file paths and code)**

```diff
--- a/src/main/java/org/urlaubsverwaltung/absences/web/AbsenceDto.java
+++ b/src/main/java/org/urlaubsverwaltung/absences/web/AbsenceDto.java
@@
 import com.fasterxml.jackson.annotation.JsonInclude;
 import java.time.LocalDate;
+import java.time.Duration;
 
 /**
  * DTO used by the REST API to transfer absence information.
  */
 @JsonInclude(JsonInclude.Include.NON_NULL)
 public class AbsenceDto {
@@
     private LocalDate endDate;
 
     private String category;
 
+    /**
+     * Only set for absences of category {@code OVERTIME_REDUCTION} (German: „Überstundenabbau“).
+     * Represents the amount of overtime that has been reduced by this absence.
+     */
+    private Duration duration;
+
     // -----------------------------------------------------------------------
     // getters & setters
     // -----------------------------------------------------------------------
@@
     public String getCategory() {
         return category;
     }
 
     public void setCategory(String category) {
         this.category = category;
     }
+
+    public Duration getDuration() {
+        return duration;
+    }
+
+    public void setDuration(Duration duration) {
+        this.duration = duration;
+    }
 }
```

```diff
--- a/src/main/java/org/urlaubsverwaltung/absences/web/AbsenceMapper.java
+++ b/src/main/java/org/urlaubsverwaltung/absences/web/AbsenceMapper.java
@@
 import org.urlaubsverwaltung.absence.Absence;
 import org.urlaubsverwaltung.absence.AbsenceCategory;
 import org.urlaubsverwaltung.absence.DayLength;
+import java.time.Duration;
 
 public class AbsenceMapper {
 
     public AbsenceDto mapToDto(Absence absence) {
         AbsenceDto dto = new AbsenceDto();
@@
         dto.setCategory(absence.getCategory().name());
 
-        // other generic fields are already set above
+        // For overtime reduction we also expose the configured duration
+        if (absence.getCategory() == AbsenceCategory.OVERTIME_REDUCTION) {
+            // The domain model stores the duration directly; it may be null for legacy data.
+            Duration duration = absence.getDuration();
+            dto.setDuration(duration);
+        }
 
         return dto;
     }
```

```diff
--- a/src/main/java/org/urlaubsverwaltung/absences/web/AbsenceController.java
+++ b/src/main/java/org/urlaubsverwaltung/absences/web/AbsenceController.java
@@
 import org.urlaubsverwaltung.absence.AbsenceService;
 import org.urlaubsverwaltung.absence.web.AbsenceDto;
 import org.urlaubsverwaltung.absence.web.AbsenceMapper;
+import org.urlaubsverwaltung.absence.AbsenceCategory;
 
 @RestController
 @RequestMapping("/api/persons")
 public class AbsenceController {
@@
     @GetMapping("/{personId}/absences")
     public List<AbsenceDto> getAbsences(@PathVariable("personId") Long personId) {
         List<Absence> absences = absenceService.getAbsencesForPerson(personId);
-        return absences.stream()
-                .map(absenceMapper::mapToDto)
-             

=== TASK dandi__dandi-cli-208 | repo dandi/dandi-cli | python
--- Issue
organize: -f "auto"
ATM we default to "dry" mode, and https://www.dandiarchive.org/handbook/10_using_dandi/ instructs to use "symlink" after running  a dry.
I think by default we should do "auto" which would either symlink, hardlink, or copy if not possible (test on the filesystem within that folder on some bogus file to see if symlinks/hardlinks are supported).
--- REAL FIX (ground truth)
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
         if dandiset_id is not None:
diff --git a/dandi/consts.py b/dandi/consts.py
index eab3d3fb..2ed0fb90 100644
--- a/dandi/consts.py
+++ b/dandi/consts.py
@@ -108,7 +108,15 @@ known_instances_rev = {vv: k for k, v in known_instances.items() for vv in v if
 collection_drafts = "drafts"
 collection_releases = "releases"
 
-file_operation_modes = ["dry", "simulate", "copy", "move", "hardlink", "symlink"]
+file_operation_modes = [
+    "dry",
+    "simulate",
+    "copy",
+    "move",
+    "hardlink",
+    "symlink",
+    "auto",
+]
 
 #
 # Download (upload?) specific constants
diff --git a/dandi/organize.py b/dandi/organize.py
index c8d112e3..bf7277fc 100644
--- a/dandi/organize.py
+++ b/dandi/organize.py
@@ -8,7 +8,9 @@ import numpy as np
 import re
 from collections import Counter
 
+import os
 import os.path as op
+from pathlib import Path
 
 from .exceptions import OrganizeImpossibleError
 from . import get_logger
@@ -572,3 +574,43 @@ def _get_non_unique_paths(metadata):
                     orig_paths.append(e["
--- Proposal A
## Root cause
The **`organize`** command defaults the `-f/--filemode` option to **`dry`**, which only prints what would be done without actually creating any links or copies.  
The Dandi handbook recommends using **`symlink`** after a dry‑run, and the more convenient behaviour is to let the tool automatically choose the best supported mode (`symlink`, `hardlink`, or `copy`).  

The code currently:
* Declares the click option with `default="dry"`.
* Passes the raw value straight to the implementation, which never handles the special value `"auto"`.

Because the default is `"dry"` and `"auto"` is not interpreted, users must manually add `-f auto` (or `-f symlink`) after a dry‑run.  

## Fix
* Change the default of the `-f/--filemode` option from `"dry"` to `"auto"`.
* Implement the `"auto"` mode: probe the target directory with a temporary file to see whether symlinks or hardlinks are supported, falling back to copying if neither works.
* Keep the existing behaviour for the explicit modes (`dry`, `symlink`, `hardlink`, `copy`).

```diff
--- a/dandi/cli/commands/organize.py
+++ b/dandi/cli/commands/organize.py
@@
-@click.option(
-    "-f",
-    "--filemode",
-    type=click.Choice(["dry", "symlink", "hardlink", "copy", "auto"]),
-    default="dry",
-    show_default=True,
-    help=(
-        "How to handle files when organizing. "
-        "'dry' only prints actions, 'symlink' creates symlinks, "
-        "'hardlink' creates hardlinks, 'copy' copies files, "
-        "'auto' chooses the best available method."
-    ),
-)
+@click.option(
+    "-f",
+    "--filemode",
+    type=click.Choice(["dry", "symlink", "hardlink", "copy", "auto"]),
+    default="auto",               # <-- default changed from "dry" to "auto"
+    show_default=True,
+    help=(
+        "How to handle files when organizing. "
+        "'dry' only prints actions, 'symlink' creates symlinks, "
+        "'hardlink' creates hardlinks, 'copy' copies files, "
+        "'auto' chooses the best available method for the target "
+        "filesystem (symlink → hardlink → copy)."
+    ),
+)
@@
-def organize_command(
-    src: Path,
-    dst: Path,
-    filemode: str,
-    preserve: bool,
-    dry_run: bool,
-    **_: Any,
-) -> None:
+def organize_command(
+    src: Path,
+    dst: Path,
+    filemode: str,
+    preserve: bool,
+    dry_run: bool,
+    **_: Any,
+) -> None:
@@
-    mode = filemode
+    # Resolve "auto" to an actual mode that works on the destination filesystem
+    mode = filemode
+    if filemode == "auto":
+        mode = _detect_best_filemode(dst)
@@
-    _organize(src, dst, mode, preserve, dry_run)
+    _organize(src, dst, mode, preserve, dry_run)
@@
-def _organize(
-    src: Path,
-    dst: Path,
-    filemode: str,
-    preserve: bool,
-    dry_run: bool,
-) -> None:
+def _organize(
+    src: Path,
+    dst: Path,
+    filemode: str,
+    preserve: bool,
+    dry_run: bool,
+) -> None:
@@
-    # existing implementation that expects filemode to be one of
-    # {"dry", "symlink", "hardlink", "copy"}
+    # existing implementation that expects filemode to be one of
+    # {"dry", "symlink", "hardlink", "copy"}
     ...
```

```diff
--- a/dandi/cli/commands/organize.py
+++ b/dandi/cli/commands/organize.py
@@
 import click
 import os
 import tempfile
 from pathlib import Path
 from typing import Any
@@
 from .utils import log
@@
 
 # ----------------------------------------------------------------------
 # Helper functions
 # ---------------------------------
--- Proposal B
## Root cause
The **`organize`** command’s `-f/--file-mode` option defaults to **`dry`**, which forces a dry‑run even when the user wants the command to actually create the dataset layout.  
The DANDI handbook recommends using **`symlink`** after a dry‑run, and many users would prefer the tool to automatically choose the best possible method (symlink → hardlink → copy) based on the capabilities of the target filesystem.  
Because the default is hard‑coded to `"dry"` and there is no logic for an `"auto"` mode, the command never performs the automatic selection, leading to the reported mismatch between documentation and behaviour.

## Fix
* Change the default value of the `-f/--file-mode` option from `"dry"` to `"auto"`.
* Implement an `"auto"` mode that detects, at runtime, whether the target directory supports symlinks, hardlinks, or only plain copies.
* Add a small helper `_detect_file_mode(target_dir)` that performs a quick test using a temporary file inside the target directory.
* Adjust the command logic to resolve `"auto"` to the concrete mode before proceeding with the organization steps.

```diff
--- a/dandi/cli/commands/organize.py
+++ b/dandi/cli/commands/organize.py
@@
 import os
 import pathlib
 import shutil
+import tempfile
 from typing import Optional
 
 import click
 
@@
-@click.option(
-    "-f",
-    "--file-mode",
-    type=click.Choice(["dry", "symlink", "hardlink", "copy"]),
-    default="dry",
-    help=(
-        "How to handle files when creating the organized layout. "
-        '"dry" performs a dry‑run (no files are written). '
-        '"symlink", "hardlink", and "copy" force the respective behaviour.'
-    ),
-)
+@click.option(
+    "-f",
+    "--file-mode",
+    type=click.Choice(["dry", "auto", "symlink", "hardlink", "copy"]),
+    default="auto",
+    help=(
+        "How to handle files when creating the organized layout. "
+        '"dry" performs a dry‑run (no files are written). '
+        '"auto" (default) automatically selects the best method supported '
+        "by the target filesystem (symlink → hardlink → copy). "
+        '"symlink", "hardlink", and "copy" force the respective behaviour.'
+    ),
+)
@@
 def organize(
@@
-    file_mode: str,
+    file_mode: str,
@@
-    # Existing implementation assumes file_mode is already concrete
-    if file_mode == "dry":
-        click.echo("Running in dry‑run mode – no files will be written.")
-        # ... rest of dry‑run handling ...
+    # Resolve "auto" to a concrete mode based on the target directory
+    if file_mode == "auto":
+        # ``target`` is the directory where the organized layout will be created.
+        # It is guaranteed to be a pathlib.Path object by the click option above.
+        file_mode = _detect_file_mode(target)
+        click.echo(f'Auto‑selected file mode: "{file_mode}"')
+
+    # Existing implementation assumes file_mode is now concrete
+    if file_mode == "dry":
+        click.echo("Running in dry‑run mode – no files will be written.")
+        # ... rest of dry‑run handling ...
@@
     # ... rest of the command implementation ...
     pass
+
+
+def _detect_file_mode(target_dir: pathlib.Path) -> str:
+    """
+    Detect the most suitable file handling mode for *target_dir*.
+
+    The detection order is:
+        1. Symlink – if the filesystem permits creating a symbolic link.
+        2. Hardlink – if the filesystem permits creating a hard link.
+        3. Copy – fallback when neither link type works.
+
+    A tempor

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
--- REAL FIX (ground truth)
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

--- Proposal A
## Root cause
`ModalResult.plot_mode_2d()` forwards the plotting work to `Shape.plot_mode_2d()`, but its signature does **not** expose the `orientation` argument that `Shape.plot_mode_2d()` accepts. Consequently users cannot control the orientation of the 2‑D plot when using the high‑level modal API, leading to the reported error.

## Fix
Add the missing `orientation` parameter to `ModalResult.plot_mode_2d()` (and propagate it to the underlying `Shape.plot_mode_2d()` call). Update the docstring accordingly.

```diff
--- a/ross/modal.py
+++ b/ross/modal.py
@@
 class ModalResult:
@@
-    def plot_mode_2d(self, mode, ax=None, **kwargs):
-        """
-        Plot the 2‑D shape of a given mode.
-
-        Parameters
-        ----------
-        mode : int
-            Mode number to plot.
-        ax : matplotlib.axes.Axes, optional
-            Axes object to plot on. If ``None`` a new figure and axes are created.
-        **kwargs : dict
-            Additional keyword arguments passed to :meth:`Shape.plot_mode_2d`.
-
-        Returns
-        -------
-        matplotlib.axes.Axes
-            Axes with the plotted mode shape.
-        """
-        shape = self.shape
-        return shape.plot_mode_2d(mode, ax=ax, **kwargs)
+    def plot_mode_2d(self, mode, ax=None, orientation='x', **kwargs):
+        """
+        Plot the 2‑D shape of a given mode.
+
+        This method is a thin wrapper around :meth:`Shape.plot_mode_2d`.  The
+        ``orientation`` argument (available in the lower‑level ``Shape`` class)
+        was previously inaccessible from the modal result object, which made it
+        impossible to plot the mode shape in the *y* direction.  The argument is
+        now exposed and forwarded to the underlying ``Shape`` method.
+
+        Parameters
+        ----------
+        mode : int
+            Mode number to plot.
+        ax : matplotlib.axes.Axes, optional
+            Axes object to plot on. If ``None`` a new figure and axes are created.
+        orientation : {'x', 'y'}, default 'x'
+            Direction in which the mode shape is projected for a 2‑D plot.
+            ``'x'`` plots the shape in the *x‑z* plane, ``'y'`` plots it in the
+            *y‑z* plane.  This argument is passed directly to
+            :meth:`Shape.plot_mode_2d`.
+        **kwargs : dict
+            Additional keyword arguments passed to :meth:`Shape.plot_mode_2d`.
+
+        Returns
+        -------
+        matplotlib.axes.Axes
+            Axes with the plotted mode shape.
+        """
+        shape = self.shape
+        # Forward the orientation argument together with any other kwargs.
+        return shape.plot_mode_2d(mode, ax=ax, orientation=orientation, **kwargs)
```

## How to verify
1. **Run the existing test suite** – all current tests should still pass.
2. **Manual check** (or add a new test):
   ```python
   from ross import Rotor
   # create a simple rotor (use any example from the docs)
   rotor = Rotor(...)
   modal = rotor.run_modal(...)
   # Plot using the new orientation argument – this should not raise an exception
   ax = modal.plot_mode_2d(mode=0, orientation='y')
   assert ax is not None
   ```
   The resulting figure should display the mode shape in the *y‑z* plane (i.e., the shaft appears rotated compared to the default *x‑z* view).

If the call succeeds without errors and the plot orientation changes as expected, the fix is verified.
--- Proposal B
## Root cause
`ModalResult.plot_mode_2d()` forwards the plotting work to `Shape.plot_mode_2d()` but does **not** expose the `orientation` argument that `Shape.plot_mode_2d()` accepts. Consequently users cannot change the orientation of the 2‑D plot for modal analysis results, even though the underlying method supports it.

## Fix
Add an `orientation` keyword argument (defaulting to `'x'` – the same default used by `Shape.plot_mode_2d`) to `ModalResult.plot_mode_2d()` and forward it to the internal `Shape` object. Update the docstring accordingly.

```diff
--- a/ross/modal_analysis.py
+++ b/ross/modal_analysis.py
@@
 class ModalResult:
@@
-    def plot_mode_2d(self, mode, **kwargs):
-        r\"\""
-        Plot the 2‑D shape of a given mode.
-
-        Parameters
-        ----------
-        mode : int
-            Mode number to plot.
-
-        **kwargs
-            Additional keyword arguments passed to :meth:`Shape.plot_mode_2d`.
-
-        Returns
-        -------
-        matplotlib.figure.Figure
-            The figure object.
-        \"\"\"
-        shape = self.shapes[mode]
-        return shape.plot_mode_2d(**kwargs)
+    def plot_mode_2d(self, mode, orientation='x', **kwargs):
+        r"""Plot the 2‑D shape of a given mode.
+
+        This method is a thin wrapper around :meth:`Shape.plot_mode_2d`.  The
+        original ``Shape`` implementation already provides an ``orientation``
+        argument (default ``'x'``) that determines whether the shape is plotted
+        in the *x*‑*z* plane (``orientation='x'``) or the *y*‑*z* plane
+        (``orientation='y'``).  Previously this wrapper did not expose the
+        parameter, making it impossible for callers to change the orientation
+        when visualising modal shapes.  The argument is now added and passed
+        through to the underlying ``Shape`` object.
+
+        Parameters
+        ----------
+        mode : int
+            Mode number to plot.
+        orientation : {'x', 'y'}, optional
+            Plane in which the 2‑D shape is plotted. ``'x'`` plots the shape
+            in the *x*‑*z* plane, ``'y'`` plots it in the *y*‑*z* plane.
+            Defaults to ``'x'`` (the same default as :meth:`Shape.plot_mode_2d`).
+        **kwargs
+            Additional keyword arguments passed to :meth:`Shape.plot_mode_2d`.
+
+        Returns
+        -------
+        matplotlib.figure.Figure
+            The figure object.
+        \"\"\"
+        shape = self.shapes[mode]
+        # Forward the orientation argument together with any other kwargs.
+        return shape.plot_mode_2d(orientation=orientation, **kwargs)
```

## How to verify
1. **Unit test** – add (or extend) a test in `ross/tests/test_modal_analysis.py`:

   ```python
   def test_plot_mode_2d_orientation():
       rotor = ross.Rotor([...])          # build a simple rotor
       modal = rotor.run_modal(...)
       # Should not raise an exception when orientation='y' is supplied
       fig = modal.plot_mode_2d(0, orientation='y')
       assert isinstance(fig, matplotlib.figure.Figure)
   ```

   Run the test suite (`pytest -q`). The new test must pass.

2. **Manual check** – execute a small script:

   ```python
   import ross, matplotlib.pyplot as plt
   rotor = ross.Rotor([...])          # any simple rotor definition
   modal = rotor.run_modal(...)
   modal.plot_mode_2d(0, orientation='y')
   plt.show()
   ```

   The plot should appear in the *y‑z* plane (the shape is rotated compared to the default view).

=== TASK argoproj-labs__applicationset-282 | repo argoproj-labs/applicationset | go
--- Issue
Issue #170 is not solved when using `server`, only when using `name` for destination
`ValidationDestination` is being used [here](https://github.com/argoproj-labs/applicationset/blob/v0.1.0/pkg/controllers/applicationset_controller.go#L560) to determine if we should remove the finalizer.  You can see in its implementation [here](https://github.com/argoproj/argo-cd/blob/master/util/argo/argo.go#L296), the entire function only does any validation when `dest.Name != ""`.  So when using `server` for the destination, the function passes and the finalizer is never removed.  
--- REAL FIX (ground truth)
diff --git a/api/v1alpha1/applicationset_types.go b/api/v1alpha1/applicationset_types.go
index 67e1b21..aff0161 100644
--- a/api/v1alpha1/applicationset_types.go
+++ b/api/v1alpha1/applicationset_types.go
@@ -65,6 +65,7 @@ type ApplicationSetTemplateMeta struct {
 	Namespace   string            `json:"namespace,omitempty"`
 	Labels      map[string]string `json:"labels,omitempty"`
 	Annotations map[string]string `json:"annotations,omitempty"`
+	Finalizers  []string          `json:"finalizers,omitempty"`
 }
 
 // ApplicationSetGenerator include list item info
diff --git a/api/v1alpha1/zz_generated.deepcopy.go b/api/v1alpha1/zz_generated.deepcopy.go
index b0f7f3d..5eae33e 100644
--- a/api/v1alpha1/zz_generated.deepcopy.go
+++ b/api/v1alpha1/zz_generated.deepcopy.go
@@ -260,6 +260,11 @@ func (in *ApplicationSetTemplateMeta) DeepCopyInto(out *ApplicationSetTemplateMe
 			(*out)[key] = val
 		}
 	}
+	if in.Finalizers != nil {
+		in, out := &in.Finalizers, &out.Finalizers
+		*out = make([]string, len(*in))
+		copy(*out, *in)
+	}
 }
 
 // DeepCopy is an autogenerated deepcopy function, copying the receiver, creating a new ApplicationSetTemplateMeta.
diff --git a/manifests/crds/argoproj.io_applicationsets.yaml b/manifests/crds/argoproj.io_applicationsets.yaml
index 0d2f3f5..46a748d 100644
--- a/manifests/crds/argoproj.io_applicationsets.yaml
+++ b/manifests/crds/argoproj.io_applicationsets.yaml
@@ -122,6 +122,10 @@ spec:
                                   additionalProperties:
                                     type: string
                                   type: object
+                                finalizers:
+                                  items:
+                                    type: string
+                                  type: array
                                 labels:
                                   additionalProperties:
                                     type: string
@@ -623,6 +627,10 @@ spec:
                                   additionalProperties:
                                     type: string
                                   type: object
+                                finalizers:
+                                  items:
+                                    type: string
+                                  type: array
                                 labels:
                                   additionalProperties:
                                     type: string
@@ -1099,6 +1107,10 @@ spec:
                                   additionalProperties:
                                     type: string
                                   type: object
+                                finalizers:
+                                  items:
+                                    type: string
+                                  type: array
                                 labels:
                                   additionalProperties:
                                     type: string
@@ -1566,6 +1578,10 @@ spec:
                  
--- Proposal A
## Root cause
`applicationset_controller.go` decides whether to remove the ApplicationSet finalizer by calling Argo CD’s `ValidateDestination`.  
`ValidateDestination` only performs validation when `dest.Name != ""`; when the destination is specified **only** with `server`, the function returns `nil` without doing any checks.  
The controller interprets a *successful* validation as “do **not** remove the finalizer”, so for `server`‑only destinations the finalizer is never cleared, leaving stale resources after the ApplicationSet is deleted.

## Fix
Add explicit handling for `server`‑only destinations in the controller.  
If `dest.Name` is empty **and** `dest.Server` is set, treat the destination as valid and allow the finalizer to be removed.  
The change is confined to `pkg/controllers/applicationset_controller.go` around the finalizer‑removal logic.

```diff
--- a/pkg/controllers/applicationset_controller.go
+++ b/pkg/controllers/applicationset_controller.go
@@
-    // Remove finalizer if the destination is invalid (e.g. cluster no longer exists)
-    if err := argo.ValidateDestination(appSet.Spec.Template.Spec.Destination); err != nil {
-        // Destination is invalid – keep the finalizer so we can retry later
-        log.Info("destination validation failed, keeping finalizer", "error", err)
-        return ctrl.Result{}, nil
-    }
-
-    // Destination is valid – safe to remove finalizer
-    if controllerutil.ContainsFinalizer(appSet, finalizerName) {
-        controllerutil.RemoveFinalizer(appSet, finalizerName)
-        if err := r.Update(ctx, appSet); err != nil {
-            return ctrl.Result{}, err
-        }
-    }
+    // -------------------------------------------------------------------------
+    // Finalizer handling – determine if the destination is *valid*.
+    // -------------------------------------------------------------------------
+    // Argo CD's ValidateDestination only validates when dest.Name != "".
+    // For ApplicationSets that use a `server`‑only destination we must treat the
+    // destination as valid as well, otherwise the finalizer is never removed.
+    dest := appSet.Spec.Template.Spec.Destination
+
+    // isDestValid returns true when the destination can be considered valid.
+    //   * If a cluster name is provided we delegate to Argo CD's validation.
+    //   * If only a server URL is provided we accept it as valid (the server
+    //     string is guaranteed to be non‑empty by the CRD schema).
+    //   * Otherwise the destination is invalid.
+    isDestValid := func(d argov1.ApplicationDestination) bool {
+        if d.Name != "" {
+            // Use Argo CD's validation for named clusters.
+            return argo.ValidateDestination(d) == nil
+        }
+        // When Name is empty, a non‑empty Server is considered a valid destination.
+        return d.Server != ""
+    }
+
+    if !isDestValid(dest) {
+        // Destination is invalid – keep the finalizer so we can retry later.
+        log.Info("destination validation failed, keeping finalizer")
+        return ctrl.Result{}, nil
+    }
+
+    // Destination is valid – safe to remove finalizer.
+    if controllerutil.ContainsFinalizer(appSet, finalizerName) {
+        controllerutil.RemoveFinalizer(appSet, finalizerName)
+        if err := r.Update(ctx, appSet); err != nil {
+            return ctrl.Result{}, err
+        }
+    }
```

*Key points of the patch*  

* Introduces a small inline helper `isDestValid` that mirro
--- Proposal B
## Root cause
`applicationset_controller.go` uses `argo.ValidationDestination` to decide whether the finalizer on an `ApplicationSet` can be removed.  
`argo.ValidationDestination` only performs validation when `dest.Name != ""`.  
When a destination is expressed with `server` (i.e. `dest.Server != ""` and `dest.Name == ""`) the function returns **no error**, so the controller thinks the destination is valid and never removes the finalizer. This leaves the `ApplicationSet` stuck in a terminating state when the destination is invalid.

## Fix
Add a wrapper that validates both `dest.Name` **and** `dest.Server`.  
Replace the direct call to `argo.ValidationDestination` with this wrapper in the controller.

```diff
--- a/pkg/controllers/applicationset_controller.go
+++ b/pkg/controllers/applicationset_controller.go
@@
 import (
     "context"
     "fmt"
     "time"
 
     appsv1 "k8s.io/api/apps/v1"
@@
     argo "github.com/argoproj/argo-cd/v2/util/argo"
     v1alpha1 "github.com/argoproj-labs/applicationset/api/v1alpha1"
 )
 
+// validateDestination validates an ApplicationDestination against the
+// destinations allowed in the given AppProject.  The upstream
+// argo.ValidationDestination only checks the `Name` field, which means a
+// destination that uses `Server` is never validated.  This helper mirrors the
+// upstream logic but also validates the `Server` field, ensuring that the
+// finalizer is correctly removed when the destination is not permitted.
+func validateDestination(proj *v1alpha1.AppProject, dest v1alpha1.ApplicationDestination) error {
+    // If neither Name nor Server is set, there is nothing to validate.
+    if dest.Name == "" && dest.Server == "" {
+        return nil
+    }
+
+    // The upstream validation checks that the destination (Name/Server + Namespace)
+    // is listed in the project's spec.destinations.  We replicate that logic here.
+    for _, allowed := range proj.Spec.Destinations {
+        // Match by Name if it is set on the destination.
+        if dest.Name != "" && allowed.Name != "" && dest.Name == allowed.Name {
+            // Namespace must also match (or be empty which means “any”).
+            if allowed.Namespace == "" || dest.Namespace == allowed.Namespace {
+                return nil
+            }
+        }
+
+        // Match by Server if Name is not used.
+        if dest.Server != "" && allowed.Server != "" && dest.Server == allowed.Server {
+            if allowed.Namespace == "" || dest.Namespace == allowed.Namespace {
+                return nil
+            }
+        }
+    }
+
+    // No matching destination found – return an error similar to the upstream
+    // function so the controller removes the finalizer.
+    if dest.Name != "" {
+        return fmt.Errorf("destination name %q is not allowed in project %q", dest.Name, proj.Name)
+    }
+    return fmt.Errorf("destination server %q is not allowed in project %q", dest.Server, proj.Name)
+}
+
 func (r *ApplicationSetReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
@@
-        // ValidationDestination is used to determine if we should remove the finalizer.
-        if err := argo.ValidationDestination(&proj, dest); err != nil {
-            // Destination is no longer valid – remove finalizer so the object can be deleted.
-            controllerutil.RemoveFinalizer(appSet, finalizerName)
-        }
+        // ValidationDestination is used to determine if we should remove the finalize

=== TASK zulko__moviepy-1768 | repo Zulko/moviepy | python
--- Issue
Changes in FFmpeg libavformat break FFmpegInfosParser
#### Expected Behavior
`FFmpegInfosParser.parse` can parse FFmpeg output.

#### Actual Behavior
`FFmpegInfosParser.parse` fails to parse FFmpeg output starting with commit [b7251ae](https://github.com/FFmpeg/FFmpeg/commit/b7251aed46f5ac96fe65c1c68ddac44b07429467), which reduced indentation for streams.

#### Steps to Reproduce the Problem
Point MoviePy to FFmpeg executable with the mentioned revision or newer and run `FFmpegInfosParser.parse`.

#### Specifications
- Python Version: 3.9.9
- Moviepy Version: 2.0.0.dev2 8eaf3f0
- Platform Name: Linux
- Platform Version: Fedora 34

#### Additional Info
ffmpeg 4.2.2 (imageio) output:
```
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':
  Metadata:
    major_brand     : isom
    minor_version   : 512
    compatible_brands: isomiso2avc1mp41
    encoder         : Lavf58.12.100
  Duration: 01:00:00.00, start: 0.000000, bitrate: 1222 kb/s
    Stream #0:0(und): Video: h264 (Main) (avc1 / 0x31637661), yuv420p, 1280x720 [SAR 1:1 DAR 16:9], 1221 kb/s, 25 fps, 25 tbr, 12800 tbn, 50 tbc (default)
    Metadata:
      handler_name    : VideoHandler
At least one output file must be specified
```
ffmpeg 4.4.1 (system) output:
```
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':
  Metadata:
    major_brand     : isom
    minor_version   : 512
    compatible_brands: isomiso2avc1mp41
    encoder         : Lavf58.12.100
  Duration: 01:00:00.00, start: 0
--- REAL FIX (ground truth)
diff --git a/moviepy/video/io/ffmpeg_reader.py b/moviepy/video/io/ffmpeg_reader.py
index 3e24557..e961017 100644
--- a/moviepy/video/io/ffmpeg_reader.py
+++ b/moviepy/video/io/ffmpeg_reader.py
@@ -415,14 +415,14 @@ class FFmpegInfosParser:
                 else:
                     self._last_metadata_field_added = field
                 self.result["metadata"][field] = value
-            elif line.startswith("    Stream "):
+            elif line.lstrip().startswith("Stream "):
                 # exit stream "    Metadata:"
                 if self._current_stream:
                     self._current_input_file["streams"].append(self._current_stream)
 
                 # get input number, stream number, language and type
                 main_info_match = re.search(
-                    r"^\s{4}Stream\s#(\d+):(\d+)\(?(\w+)?\)?:\s(\w+):", line
+                    r"^Stream\s#(\d+):(\d+)\(?(\w+)?\)?:\s(\w+):", line.lstrip()
                 )
                 (
                     input_number,

--- Proposal A
## Root cause
`moviepy.video.io.ffmpeg_reader.FFmpegInfosParser` (used by `ffmpeg_parse_infos`) assumes that every *stream* line in FFmpeg’s stderr output starts with **four spaces** (`"    Stream "`).  
Since FFmpeg commit b7251ae the indentation was reduced to **two spaces** (`"  Stream "`).  
The parser therefore never enters the *stream*‑parsing branch for newer FFmpeg versions, resulting in missing stream information and a failure of `FFmpegInfosParser.parse`.

## Fix
Make the parser tolerant to any amount of leading whitespace when detecting stream lines and when applying the regular‑expression that extracts the stream description.

```diff
--- a/moviepy/video/io/ffmpeg_reader.py
+++ b/moviepy/video/io/ffmpeg_reader.py
@@
-        for line in ffmpeg_output.splitlines():
-            # Stream lines start with exactly four spaces in older FFmpeg versions
-            if line.startswith("    Stream "):
-                # Example line:
-                #     Stream #0:0(und): Video: h264 (Main) (avc1 / 0x31637661), ...
-                stream_match = re.search(
-                    r"^\s{4}Stream #(?P<index>\d+):(?P<subindex>\d+)"
-                    r"(?:\((?P<lang>[^)]+)\))?:\s+(?P<type>\w+):\s+(?P<codec>[^,]+),\s+(?P<details>.+)",
-                    line,
-                )
-                if stream_match:
-                    stream_info = stream_match.groupdict()
-                    streams.append(stream_info)
-                continue
+        for line in ffmpeg_output.splitlines():
+            # -----------------------------------------------------------------
+            # Stream lines – older FFmpeg used 4 leading spaces, newer versions
+            # (e.g. 4.4+) use only 2.  We therefore strip leading whitespace
+            # before checking the prefix and use a flexible regular expression.
+            # -----------------------------------------------------------------
+            if line.lstrip().startswith("Stream "):
+                # Example line (old style):
+                #     Stream #0:0(und): Video: h264 (Main) (avc1 / 0x31637661), ...
+                # Example line (new style):
+                #   Stream #0:0(und): Video: h264 (Main) (avc1 / 0x31637661), ...
+                stream_match = re.search(
+                    r"""^
+                        \s*                # any leading whitespace
+                        Stream\ \#(?P<index>\d+):(?P<subindex>\d+)   # stream ids
+                        (?:\((?P<lang>[^)]+)\))?                     # optional language tag
+                        :\s+
+                        (?P<type>\w+):\s+                           # stream type (Video/Audio/...)
+                        (?P<codec>[^,]+),\s+                        # codec name
+                        (?P<details>.+)                             # the rest of the line
+                    """,
+                    line,
+                    re.VERBOSE,
+                )
+                if stream_match:
+                    stream_info = stream_match.groupdict()
+                    streams.append(stream_info)
+                # No need for an explicit ``continue`` – the next iteration
+                # will naturally process the following line.
+                continue
*** End of File ***
```

### What the patch does
1. **Detects stream lines** with `line.lstrip().startswith("Stream ")` instead of a fixed‑space check.
2. **Uses a tolerant regex** (`\s*` at the start) that works for any indentation
--- Proposal B
## Root cause
`FFmpegInfosParser.parse` relied on the exact indentation of the *Stream* line in the output of `ffmpeg -i`.  
Older FFmpeg versions printed the line with **four** leading spaces, while newer versions (starting with commit b7251ae) use only **two** spaces.  
The parser used `re.match` anchored at the start of the line, so the change in indentation caused the regular expression not to match and the parser raised an exception.

## Fix (a unified diff with your best‑guess file paths and code)
```diff
--- a/moviepy/video/io/ffmpeg_reader.py
+++ b/moviepy/video/io/ffmpeg_reader.py
@@
 class FFmpegInfosParser:
@@
-    @staticmethod
-    def parse(ffmpeg_output):
-        """
-        Parse the output of `ffmpeg -i <file>` and return a dict with
-        duration, fps, size, etc.
-        """
-        # The original implementation expected the line with the stream
-        # information to start with exactly four spaces, e.g.
-        #     Stream #0:0(und): Video: h264 ...
-        # Newer FFmpeg versions reduced the indentation to two spaces,
-        # causing the regular expression to fail.
-        # -----------------------------------------------------------------
-        # Old (fragile) implementation:
-        #   stream_line = re.search(r'^\s{4}Stream #\d+:\d+.*Video:.*', ffmpeg_output,
-        #                          re.MULTILINE).group(0)
-        # -----------------------------------------------------------------
-        # The fix: ignore leading whitespace completely and use ``re.search``
-        # with a pattern that does not depend on the exact number of spaces.
-        # -----------------------------------------------------------------
-
-        # Find the line that contains the video stream description.
-        stream_line = None
-        for line in ffmpeg_output.splitlines():
-            if 'Video:' in line:
-                stream_line = line
-                break
-        if stream_line is None:
-            raise ValueError('No video stream found in ffmpeg output')
-
-        # Extract duration
-        duration_match = re.search(r'Duration: (\d+):(\d+):([\d\.]+)', ffmpeg_output)
-        if not duration_match:
-            raise ValueError('Duration not found in ffmpeg output')
-        hours, minutes, seconds = map(float, duration_match.groups())
-        duration = hours * 3600 + minutes * 60 + seconds
-
-        # Extract fps
-        fps_match = re.search(r'(\d+(?:\.\d+)?) fps', stream_line)
-        fps = float(fps_match.group(1)) if fps_match else None
-
-        # Extract size
-        size_match = re.search(r'(\d+)x(\d+)', stream_line)
-        if not size_match:
-            raise ValueError('Size not found in ffmpeg output')
-        width, height = map(int, size_match.groups())
-
-        return {
-            'duration': duration,
-            'fps': fps,
-            'size': (width, height)
-        }
+    @staticmethod
+    def parse(ffmpeg_output: str) -> dict:
+        """
+        Parse the output of ``ffmpeg -i <file>`` and return a dictionary with
+        the most useful information (duration, fps, size, etc.).
+
+        The parser is deliberately tolerant to changes in the formatting of
+        FFmpeg's output – it no longer depends on a fixed amount of leading
+        whitespace before the ``Stream`` line.
+        """
+        # -----------------------------------------------------------------
+        # 1️⃣  Find the line that describes the *video* stream.
+        # -

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
--- REAL FIX (ground truth)
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
+        """The bin directories for the virtualenv."""
+        return self.virtualenv.bin_paths
+
     @property
     def bin(self) -> Optional[str]:
-        """The bin directory for the virtualenv."""
-        return self.virtualenv.bin
+        """The first bin directory for the virtualenv."""
+        paths = self.bin_paths
+        return paths[0] if paths is not None else None
 
     def create_tmp(self) -> str:
         """Create, and return, a temporary directory."""
@@ -279,7 +285,7 @@ class Session:
             kwargs["external"] = True
 
         # Run a shell command.
-        return nox.command.run(args, env=env, path=self.bin, **kwargs)
+        return nox.command.run(args, env=env, paths=self.bin_paths, **kwargs)
 
     def conda_install(self, *args: str, **kwargs: Any) -> None:
         """Install invokes `conda install`_ to install packages inside of the
diff --git a/nox/virtualenv.py b/nox/virtualenv.py
index fea0238..5824bfc 100644
--- a/nox/virtualenv.py
+++ b/nox/virtualenv.py
@@ -17,7 +17,7 @@ import p
--- Proposal A
## Root cause
`nox.command.run` resolves the executable to run with `py.path.local.sysfind`.  
When a virtual environment is active, `session.bin_path` points to the *Scripts* (or *bin*) directory of the environment.  
The code only searches that directory:

```python
if path:
    full_path = py.path.local.sysfind(program, paths=[path])
```

On Windows (and also on Unix when using conda), the actual Python interpreter lives **one level up** (e.g. `…\env\python.exe`), not inside the *Scripts* folder. Consequently `session.run('python', …)` falls back to the system‑wide `python` found on `PATH`, breaking isolation.

## Fix
Search both the *Scripts* directory **and its parent directory** when looking for the executable.

```diff
--- a/nox/command.py
+++ b/nox/command.py
@@
-        if path:
-            full_path = py.path.local.sysfind(program, paths=[path])
+        # ``path`` points to the virtual‑environment's ``Scripts`` (Windows) or
+        # ``bin`` (Unix) directory.  The Python interpreter itself lives one
+        # level higher (e.g. ``<venv>/python.exe`` on Windows).  To ensure that
+        # ``session.run('python', …)`` picks up the interpreter from the
+        # virtual environment we search both the ``path`` and its parent.
+        if path:
+            # ``os.path.dirname(path)`` is the directory that contains the
+            # interpreter (or other binaries that are not placed inside the
+            # ``Scripts``/``bin`` folder).
+            full_path = py.path.local.sysfind(
+                program,
+                paths=[path, os.path.dirname(path)],
+            )
```

* The additional comment explains the reasoning.
* The change is minimal and keeps the original behaviour for all other commands.

## How to verify
1. **Re‑run the reproducing `noxfile.py`** (the one from the issue) on a platform that uses a conda backend (e.g. Linux or Windows).  
   ```bash
   nox -s test_linux
   ```
   The session should now complete without error, and the `python` command used in the last `session.run` must be the interpreter from the created conda environment.

2. **Add a quick sanity check** inside the session (optional):
   ```python
   session.run('python', '-c', 'import sys, os; print(sys.executable)')
   ```
   The printed path should be inside the virtual environment (e.g. `…/env/bin/python` or `…\env\python.exe`), not the system‑wide interpreter.

3. **Run the project's test suite** (`pytest`) to ensure no regressions:
   ```bash
   nox -s test
   ```

If the above steps pass, the bug is fixed.
--- Proposal B
## Root cause
`nox.command.Command._resolve_program` (used by `Session.run`) only searches for the requested executable inside the *scripts* directory of the virtual environment (`path`).  
On most platforms the actual Python interpreter lives **one level up** (e.g. `…/venv/bin/python` on Unix or `…\venv\python.exe` on Windows).  
Because the resolver does not look at the parent directory, `session.run('python', …)` falls back to the system‑wide Python instead of the one from the created environment.

## Fix
Search both the scripts directory **and its parent** when locating the program. This keeps the original behaviour for normal scripts while correctly finding the interpreter.

```diff
--- a/nox/command.py
+++ b/nox/command.py
@@
-        if path:
-            full_path = py.path.local.sysfind(program, paths=[path])
+        # ``path`` points at the *scripts* directory of the virtual environment.
+        # The Python interpreter itself lives one level higher (e.g. ``venv/bin``
+        # on POSIX or ``venv`` on Windows).  We therefore search both the
+        # scripts directory *and* its parent directory.  This mirrors the
+        # behaviour of ``shutil.which`` and ensures ``session.run('python')``
+        # resolves to the environment's interpreter.
+        if path:
+            search_paths = [path, os.path.dirname(path)]
+            full_path = py.path.local.sysfind(program, paths=search_paths)
```

*Explanation of changes*

1. Added a comment describing why the extra directory is needed.
2. Constructed `search_paths` containing the original `path` and its parent.
3. Passed `search_paths` to `py.path.local.sysfind` so the resolver can locate the interpreter.

No other parts of the code need to be altered.

## How to verify
1. **Clone the repository** and checkout the branch with the patch.
2. Install the development dependencies (`pip install -e .[dev]`).
3. Create a `noxfile.py` reproducing the issue (the one from the report).
4. Run the session, e.g.:

   ```bash
   nox -s test_linux
   ```

5. Observe the output of the last command:

   ```bash
   session.run('python', 'setup.py', 'sdist', 'bdist_wheel')
   ```

   It should now invoke the **virtual‑environment Python** (the path printed by the command should be something like `…/venv/bin/python` or `…\venv\python.exe`), not the system Python.

6. Additionally, run the same session on Windows (or with `VENV='conda'`) to confirm the fix works for conda‑based environments as well.

If the command resolves to the interpreter inside the created environment and the subsequent `setup.py` steps succeed, the bug is fixed.

=== TASK core-gatech-group__serpent-tools-34 | repo CORE-GATECH-GROUP/serpent-tools | python
--- Issue
Add more message types
Add some future/depreciated warnings that can either piggy back off the [python builtin warnings](https://docs.python.org/2/library/warnings.html) but also conform to our messaging methodology
--- REAL FIX (ground truth)
diff --git a/serpentTools/__init__.py b/serpentTools/__init__.py
index 87c984c..bf0e5d3 100644
--- a/serpentTools/__init__.py
+++ b/serpentTools/__init__.py
@@ -5,13 +5,14 @@ ROOT_DIR = os.path.dirname(__file__)
 from serpentTools import settings
 
 # List TODOS/feature requests here for now
-# Compatability
+# Compatibility
 # TODO: Python 2 support
 # TODO: Test compatibility with earlier numpy releases
 # Usage/scripting
 # TODO: Update rc with dictionary
 # TODO: Update rc with yaml file into dictionary
 # TODO: Capture materials with underscores for depletion
+# TODO: Find a way to capture some or all of log messages for testing
 
 from ._version import get_versions
 __version__ = get_versions()['version']
diff --git a/serpentTools/objects/materials.py b/serpentTools/objects/materials.py
index 43e3db8..49e2ff4 100644
--- a/serpentTools/objects/materials.py
+++ b/serpentTools/objects/materials.py
@@ -3,7 +3,7 @@
 import numpy
 from matplotlib import pyplot
 
-
+from serpentTools.settings import messages
 from serpentTools.objects import _NamedObject
 
 
@@ -62,6 +62,7 @@ class DepletedMaterial(_NamedObject):
             List of strings corresponding to the raw data from the file
         """
         newName = self._convertVariableName(variable)
+        messages.debug('Adding {} data to {}'.format(newName, self.name))
         if isinstance(rawData, str):
             scratch = [float(item) for item in rawData.split()]
         else:
diff --git a/serpentTools/parsers/depletion.py b/serpentTools/parsers/depletion.py
index 2f31b49..c053805 100644
--- a/serpentTools/parsers/depletion.py
+++ b/serpentTools/parsers/depletion.py
@@ -37,22 +37,15 @@ class DepletionReader(MaterialReader):
     def __init__(self, filePath):
         MaterialReader.__init__(self, filePath, 'depletion')
         self._matPatterns = self._makeMaterialRegexs()
-        self.matchMatNVar = r'[A-Z]{3}_([0-9a-zA-Z]*)_([A-Z]*_?[A-Z]*)'
-        """
-        Captures material name and variable from string::
+        self._matchMatNVar = r'[A-Z]{3}_([0-9a-zA-Z]*)_([A-Z]*_?[A-Z]*)'
+        # Captures material name and variable from string
+        #  MAT_fuel1_ADENS --> ('fuel1', 'ADENS')
+        #  MAT_fUeL1g_ING_TOX --> ('fUeL1', 'ING_TOX')
 
-            MAT_fuel1_ADENS --> ('fuel1', 'ADENS')
-            MAT_fUeL1g_ING_TOX --> ('fUeL1', 'ING_TOX')
-
-        """
-        self.matchTotNVar = r'[A-Z]{3}_([A-Z]*_?[A-Z]*)'
-        """
-        Captures variables for total block from string::
-
-            TOT_ADENS --> ('ADENS', )
-            ING_TOX --> ('ING_TOX', )
-
-        """
+        self._matchTotNVar = r'[A-Z]{3}_([A-Z]*_?[A-Z]*)'
+        # Captures variables for total block from string::
+        #  TOT_ADENS --> ('ADENS', )
+        #  ING_TOX --> ('ING_TOX', )
 
     def _makeMaterialRegexs(self):
         """Return the patterns by which to find the requested materials."""
@@ -64,7 +57,7 @@ class DepletionReader(M
--- Proposal A
## Root cause
The library only defines a limited set of message types (e.g., `INFO`, `WARNING`, `ERROR`).  Users now need *future* and *deprecation* messages that follow the same internal “messaging methodology” while still integrating with Python’s built‑in `warnings` framework.  Because the code base does not currently expose such types, attempts to emit them raise `AttributeError` or are silently ignored, causing the reported issue.

## Fix
Add two new message types – **FUTURE** and **DEPRECATED** – to the central messaging module and provide thin wrappers that both (a) forward the message to the internal logger and (b) raise the appropriate `warnings.warn` with the standard Python warning categories (`FutureWarning` and `DeprecationWarning`).  

```diff
--- a/serpent_tools/messaging.py
+++ b/serpent_tools/messaging.py
@@
-from enum import Enum, auto
-import logging
-
-# ---------------------------------------------------------------------------
-# Core message handling
-# ---------------------------------------------------------------------------
-
-class MessageType(Enum):
-    """Supported message categories."""
-    INFO = auto()
-    WARNING = auto()
-    ERROR = auto()
-
-
-class Message:
-    """Simple container for a message."""
-
-    def __init__(self, msg_type: MessageType, text: str):
-        self.type = msg_type
-        self.text = text
-
-    def __str__(self):
-        return f"[{self.type.name}] {self.text}"
-
-
-# Global logger used by the library
-LOGGER = logging.getLogger("serpent_tools")
-
-
-def emit(msg_type: MessageType, text: str, *args, **kwargs):
-    """Emit a message using the library's logger."""
-    message = Message(msg_type, text.format(*args, **kwargs))
-    if msg_type is MessageType.INFO:
-        LOGGER.info(message)
-    elif msg_type is MessageType.WARNING:
-        LOGGER.warning(message)
-    elif msg_type is MessageType.ERROR:
-        LOGGER.error(message)
-        raise RuntimeError(message)
-    else:
-        LOGGER.debug(message)
-
-
-# Convenience helpers -------------------------------------------------------
-
-def info(text: str, *args, **kwargs):
-    emit(MessageType.INFO, text, *args, **kwargs)
-
-
-def warning(text: str, *args, **kwargs):
-    emit(MessageType.WARNING, text, *args, **kwargs)
-
-
-def error(text: str, *args, **kwargs):
-    emit(MessageType.ERROR, text, *args, **kwargs)
+from enum import Enum, auto
+import logging
+import warnings
+
+# ---------------------------------------------------------------------------
+# Core message handling
+# ---------------------------------------------------------------------------
+
+class MessageType(Enum):
+    """Supported message categories."""
+    INFO = auto()
+    WARNING = auto()
+    ERROR = auto()
+    # New message types required by the issue
+    FUTURE = auto()
+    DEPRECATED = auto()
+
+
+class Message:
+    """Simple container for a message."""
+
+    def __init__(self, msg_type: MessageType, text: str):
+        self.type = msg_type
+        self.text = text
+
+    def __str__(self):
+        return f"[{self.type.name}] {self.text}"
+
+
+# Global logger used by the library
+LOGGER = logging.getLogger("serpent_tools")
+
+
+def _log_message(msg_type: MessageType, text: str):
+    """Internal helper that logs a message according to its type."""
+    if msg_type is MessageType.INFO:
+        LOGGER.info(text)
+    elif msg_type is MessageType.WARNING:
+        LOGGER.warning(text)
+    elif msg_type is MessageType.E
--- Proposal B
## Root cause
The library’s messaging module only provides the classic log‑level helpers (`debug`, `info`, `warning`, `error`).  Users and developers need a way to emit *future* and *deprecation* notices that follow the project’s “messaging methodology” while still integrating with Python’s built‑in `warnings` system.  Because the module lacks these helpers, callers have to manually call `warnings.warn` and then a logger, which leads to inconsistent formatting and makes it hard to capture these messages in tests.

## Fix
Add two new message helpers – `future` and `deprecated` – that:
* Issue a proper `FutureWarning` / `DeprecationWarning` via the standard `warnings` module (so they can be filtered, silenced, or turned into errors by users).
* Log the same text through the package logger at **WARNING** level, prefixed with a clear tag (`[FUTURE]` or `[DEPRECATED]`) so that the library’s log output remains consistent with the existing messaging style.
* Expose the new helpers via `__all__` and document their behaviour.

```diff
--- a/serpent_tools/messaging.py
+++ b/serpent_tools/messaging.py
@@
-import logging
+import logging
+import warnings
@@
-logger = logging.getLogger(__name__)
+logger = logging.getLogger(__name__)
@@
-__all__ = ["debug", "info", "warning", "error"]
+# Public API of the messaging module
+__all__ = [
+    "debug",
+    "info",
+    "warning",
+    "error",
+    "future",
+    "deprecated",
+]
@@
-def debug(msg, *args, **kwargs):
-    """Log a debug message."""
-    logger.debug(msg, *args, **kwargs)
+def debug(msg, *args, **kwargs):
+    """Log a debug message."""
+    logger.debug(msg, *args, **kwargs)
@@
-def info(msg, *args, **kwargs):
-    """Log an informational message."""
-    logger.info(msg, *args, **kwargs)
+def info(msg, *args, **kwargs):
+    """Log an informational message."""
+    logger.info(msg, *args, **kwargs)
@@
-def warning(msg, *args, **kwargs):
-    """Log a warning message."""
-    logger.warning(msg, *args, **kwargs)
+def warning(msg, *args, **kwargs):
+    """Log a warning message."""
+    logger.warning(msg, *args, **kwargs)
@@
-def error(msg, *args, **kwargs):
-    """Log an error message."""
-    logger.error(msg, *args, **kwargs)
+def error(msg, *args, **kwargs):
+    """Log an error message."""
+    logger.error(msg, *args, **kwargs)
+
+
+# ---------------------------------------------------------------------------
+# Future / Deprecation helpers
+# ---------------------------------------------------------------------------
+
+def _log_and_warn(message: str, category: type[Warning], prefix: str, stacklevel: int = 2) -> None:
+    """
+    Central helper that emits a standard Python warning *and* logs the same
+    message using the package logger.
+
+    Parameters
+    ----------
+    message : str
+        The warning / log message.
+    category : Warning subclass
+        The warning category to pass to :func:`warnings.warn` (e.g.
+        :class:`FutureWarning` or :class:`DeprecationWarning`).
+    prefix : str
+        Text that will be prepended to the logged message so that log output
+        clearly indicates the nature of the notice (e.g. ``[FUTURE]``).
+    stacklevel : int, optional
+        The ``stacklevel`` argument forwarded to :func:`warnings.warn`.  The
+        default of ``2`` points to the caller of the public helper.
+    """
+    # Emit the standard warning – this respects the user's warning filters.
+    warnings.warn(message, category=category, stacklevel=stackl

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
`dvc resolve` does not exist. Users need a way to obtain the exact remote URL (or cache path) that DVC uses for a tracked data file, optionally for a specific Git revision. The repository already knows how to map a file’s hash to a remote URL via the `Cloud` (remote) implementation, but there is no CLI entry that exposes this functionality. Adding a dedicated command that loads the repository (optionally at a given revision), finds the DVC‑tracked outputs for the supplied paths, and prints the remote URL for each resolves the issue.

## Fix
```diff
--- a/dvc/command/__init__.py
+++ b/dvc/command/__init__.py
@@
 from .push import push
 from .pull import pull
 from .repro import repro
 from .remove import remove
 from .repo import repo
 from .run import run
+from .resolve import resolve   # <-- new command
@@
     push,
     pull,
     repro,
     remove,
     repo,
     run,
+    resolve,
 )
 
 # NOTE: the order of commands matters for the help output.
 # Keep it alphabetical (except for the “repo” command which is a
 # special case).
```

```diff
--- a/dvc/command/resolve.py
+++ b/dvc/command/resolve.py
@@
+"""Implementation of the ``dvc resolve`` command.
+
+The command prints the remote URL (or cache URL) for each supplied
+data file.  It works for the current working tree as well as for any
+Git revision that contains the DVC metadata.
+"""
+
+from __future__ import annotations
+
+import os
+from typing import Iterable, List, Optional
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
+    type=click.Path(exists=False, dir_okay=False, resolve_path=True),
+)
+@click.option(
+    "--rev",
+    default=None,
+    help=(
+        "Git revision (commit SHA, tag, branch, etc.) to resolve the "
+        "outputs from.  If omitted, the current working tree is used."
+    ),
+)
+def resolve(paths: Iterable[str], rev: Optional[str] = None) -> None:
+    """Show the remote URL for the cached version of each *PATH*.
+
+    Example::
+
+        $ dvc resolve data/file.txt
+        data/file.txt s3://my-bucket/dvc-cache/2d/10f00a05f1fe70eaa3c42aa9f44b95
+
+    The command works with any revision that contains the DVC metadata:
+
+        $ dvc resolve data/file.txt --rev v1.2
+        data/file.txt gs://my-bucket/dvc-cache/99/370a5386cac00c93c9fdc836076a7d
+    """
+    # Initialise the repository.  ``rev`` may be ``None`` – in that case the
+    # repository is opened at the current HEAD.
+    try:
+        repo = Repo(root_dir=os.getcwd(), rev=rev)
+    except DvcException as exc:
+        raise click.ClickException(str(exc))
+
+    if not paths:
+        raise click.ClickException("At least one path must be provided.")
+
+    # ``Repo.get_outs_by_path`` returns a list of ``Output`` objects that
+    # correspond to the supplied data files.  It raises a helpful error if a
+    # path is not tracked by DVC.
+    try:
+        outs = repo.get_outs_by_path(list(paths), strict=True)
+    except DvcException as exc:
+        raise click.ClickException(str(exc))
+
+    # ``repo.cloud`` is the default remote (or a ``LocalRemote`` when no
+    # remote is configured).  ``hash_to_url`` converts a hash value into the
+    # full URL that DVC would use to fetch the object.
+    cloud = repo.cloud
+
+    for out in outs:
+        # ``out`` is an ``Output`` instance.  Its ``hash_info`` attribute holds
+        # the checksum (e.g. MD5) that i
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

=== TASK ajalt__mordant-77 | repo ajalt/mordant | kotlin
--- Issue
`Terminal.print()` ignores new line feeds at the end of Strings
I'm trying to interface Mordant with an existing logging structure by implementing the `Appendable` interface and forwarding the methods to Mordant using the `Terminal.print()` method, however I'm noticing that new line feeds at the end of `CharSequences/Strings` are getting ignored. Somehow Mordant is stripping them.

**Sample code:**
```
class TerminalAppendable(private val terminal: Terminal): Appendable {

    override fun append(charSequence: CharSequence): Appendable {
        terminal.print(charSequence)
        return this
    }

    override fun append(charSequence: CharSequence, start: Int, end: Int): Appendable {
        terminal.print(charSequence.subSequence(start, end))
        return this
    }

    override fun append(char: Char): Appendable {
        terminal.print(char.toString())
        return this
    }
}
```

```
val appendable = TerminalAppendable(Terminal())
appendable.appendLine("Foo Bar")
appendable.appendLine("Lorem Ipsum")
// Result:   `Foo BarLorem Ipsum`
// Expected: `Foo Bar\nLorem Ipsum\n`

appendable.append("Foo Bar\n")
appendable.append("Lorem Ipsum\n")
// Result:   `Foo BarLorem Ipsum`
// Expected: `Foo Bar\nLorem Ipsum\n`

appendable.append("Foo\nBar\n")
appendable.append("Lorem\nIpsum\n")
// Result:   `Foo\nBarLorem\nIpsum`
// Expected: `Foo\nBar\nLorem\nIpsum\n`
```
--- REAL FIX (ground truth)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 6f85a06b5..4639aabe1 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,5 +1,10 @@
 # Changelog
 
+## 2.0.0-beta8
+### Changed
+- Stop stripping trailing newline from text when using `Whitespace.PRE` [(#75)](https://github.com/ajalt/mordant/issues/75)
+
+
 ## 2.0.0-beta8
 ### Added
 - Implemented `hideInput` for prompts on native targets [(#63)](https://github.com/ajalt/mordant/issues/63)
diff --git a/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/internal/Parsing.kt b/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/internal/Parsing.kt
index fad79fb9b..1ccb4343f 100644
--- a/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/internal/Parsing.kt
+++ b/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/internal/Parsing.kt
@@ -78,7 +78,7 @@ private fun splitWords(chunk: Chunk): List<Chunk> {
 }
 
 /** Split a flat list of chunks into a list of lines, removing linebreak chunks */
-private fun splitLines(words: Iterable<Chunk>): List<Line> {
+private fun splitLines(words: List<Chunk>): List<Line> {
     val lines = mutableListOf<Line>()
     var line = mutableListOf<Span>()
 
@@ -92,6 +92,11 @@ private fun splitLines(words: Iterable<Chunk>): List<Line> {
     }
 
     if (line.isNotEmpty()) lines += Line(line)
+    words.lastOrNull()?.let {
+        if (it.text.endsWith("\n")) {
+            lines.add(Line(listOf(), endStyle = it.style))
+        }
+    }
 
     return lines
 }
diff --git a/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/markdown/MarkdownRenderer.kt b/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/markdown/MarkdownRenderer.kt
index 9665bca43..f62581fbd 100644
--- a/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/markdown/MarkdownRenderer.kt
+++ b/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/markdown/MarkdownRenderer.kt
@@ -287,9 +287,8 @@ internal class MarkdownRenderer(
             }
 
             MarkdownTokenTypes.EOL -> {
-                // Add an extra linebreak, since the first one will get folded away by foldLines.
                 // Parse the text rather than hard coding the return value to support NEL and LS.
-                Lines(listOf(EMPTY_LINE) + parseText(node.nodeText(), DEFAULT_STYLE).lines)
+                Lines(parseText(node.nodeText(), DEFAULT_STYLE).lines)
             }
 
             else -> error("Unexpected token when parsing inlines: $node; [${node.type}:'${node.nodeText().take(10)}'}]")

--- Proposal A
## Root cause
`Terminal.print(CharSequence)` splits the incoming text on `'\n'` using Kotlin’s default `String.split('\n')`.  
The default `split` **drops trailing empty strings**, so when the supplied text ends with a newline the final empty segment is discarded and the terminal never emits the corresponding line‑feed.  
Consequently any `\n` that appears at the end of a string (or the last line of a multi‑line string) is silently ignored, which is why the `Appendable` wrapper loses the line breaks.

## Fix
Update the implementation of `Terminal.print` (and the overload that prints a single `Char`) to keep trailing empty segments when splitting. This is done by using `split('\n', limit = -1)`, which preserves all empty strings, and then explicitly printing a newline after every segment except the last *non‑empty* one.

```diff
--- a/src/main/kotlin/com/github/ajalt/mordant/Terminal.kt
+++ b/src/main/kotlin/com/github/ajalt/mordant/Terminal.kt
@@
-    /** Print a [CharSequence] to the terminal without adding a newline. */
-    public fun print(text: CharSequence) {
-        // Existing implementation (simplified)
-        val lines = text.split('\n')
-        for ((i, line) in lines.withIndex()) {
-            output.print(line)
-            if (i != lines.lastIndex) output.println()
-        }
-    }
-
-    /** Print a single character to the terminal without adding a newline. */
-    public fun print(ch: Char) {
-        print(ch.toString())
-    }
+    /** Print a [CharSequence] to the terminal without adding a newline.
+     *
+     * The original implementation used `String.split('\n')`, which discards
+     * trailing empty strings.  That caused a newline at the end of the input
+     * to be lost (e.g. `"Foo\n"` would be printed as `"Foo"`).  By splitting
+     * with `limit = -1` we keep all empty segments, ensuring that a final
+     * newline is emitted.
+     */
+    public fun print(text: CharSequence) {
+        // Preserve trailing empty strings so a final '\n' is not dropped.
+        val parts = text.split('\n', limit = -1)
+        for ((index, part) in parts.withIndex()) {
+            output.print(part)
+            // Print a newline after every part *except* the very last one.
+            // The last part will be an empty string when the original text
+            // ended with a newline, and we still need to emit that newline.
+            if (index != parts.lastIndex) {
+                output.println()
+            }
+        }
+    }
+
+    /** Print a single character to the terminal without adding a newline. */
+    public fun print(ch: Char) {
+        // Delegating to the CharSequence overload guarantees the same newline
+        // handling logic is used for single‑character prints.
+        print(ch.toString())
+    }
```

*Explanation of the changes*

* **`split('\n', limit = -1)`** – keeps all empty substrings, including the one that follows a trailing newline.
* The loop now prints a newline after every part **except** the final part.  
  When the original text ends with `\n`, the final part is an empty string, so the loop prints a newline for the preceding part and then finishes, effectively reproducing the missing line‑feed.
* The `print(Char)` overload now forwards to the updated `print(CharSequence)` implementation, ensuring consistent behaviour.

## How to verify
1. **Add a unit test** (e.g. `TerminalPrintNewlineTest.kt`) that creates a `Terminal` backed by a `StringBuilder`:

```kotlin
@Test fun 
--- Proposal B
## Root cause
`Terminal.print(CharSequence)` strips a trailing line‑feed (`'\n'`) from the supplied text before writing it to the underlying output.  
The implementation was written to avoid printing a double newline when `println` was used, but it unintentionally removes **any** newline that appears at the end of the string.  
Because `Appendable.append…` forwards the raw `CharSequence` to `Terminal.print`, the final `'\n'` of each appended chunk is lost, which leads to the observed concatenation of lines.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/src/main/kotlin/com/github/ajalt/mordant/Terminal.kt
+++ b/src/main/kotlin/com/github/ajalt/mordant/Terminal.kt
@@
-    /**
-     * Print the given text to the terminal. If the text ends with a newline it is
-     * stripped because `println` will add its own newline.
-     */
-    fun print(text: CharSequence) {
-        // NOTE: Historically we removed a trailing '\n' to avoid double newlines.
-        // This broke Appendable implementations that forward raw strings.
-        val str = text.toString()
-        if (str.endsWith('\n')) {
-            // Write everything except the final newline; the caller can add it
-            // explicitly with `println` if desired.
-            output.print(str.substring(0, str.length - 1))
-        } else {
-            output.print(str)
-        }
-        output.flush()
-    }
+    /**
+     * Print the given text to the terminal **exactly** as supplied.
+     *
+     * The previous implementation removed a trailing `'\n'` under the assumption
+     * that callers would use `println` for line‑terminated output.  This caused
+     * `Terminal.print` to silently drop the final newline, breaking any code
+     * (including `Appendable` adapters) that forwards raw strings containing
+     * line‑feeds.  The method now writes the full text unchanged; callers who
+     * want an automatic newline should use `println` instead.
+     */
+    fun print(text: CharSequence) {
+        // Write the text verbatim – do **not** strip trailing newlines.
+        output.print(text.toString())
+        output.flush()
+    }
```

### What changed
* Removed the conditional that trimmed a trailing `'\n'`.
* Updated the KDoc to explain the new behaviour and why the change was required.
* Kept the flush call unchanged.

## How to verify
1. **Add a unit test** (or run the snippet from the issue) that captures the terminal output using a `StringBuilder`‑backed `Terminal`:

```kotlin
@Test
fun `appendable forwards newlines correctly`() {
    val sb = StringBuilder()
    val terminal = Terminal(terminal = TerminalBuilder.builder()
        .streams(OutputStream.nullOutputStream(), OutputStream.nullOutputStream())
        .writer { sb.append(it) }
        .build())
    val appendable = TerminalAppendable(terminal)

    // Using the same calls as in the issue description
    appendable.appendLine("Foo Bar")
    appendable.appendLine("Lorem Ipsum")
    assertEquals("Foo Bar\nLorem Ipsum\n", sb.toString())

    sb.clear()
    appendable.append("Foo Bar\n")
    appendable.append("Lorem Ipsum\n")
    assertEquals("Foo Bar\nLorem Ipsum\n", sb.toString())

    sb.clear()
    appendable.append("Foo\nBar\n")
    appendable.append("Lorem\nIpsum\n")
    assertEquals("Foo\nBar\nLorem\nIpsum\n", sb.toString())
}
```

2. **Run the test suite** (`./gradlew test`). All existing tests should still pass, and the new test should now succeed, confirming that `

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
`push_dataset_to_ckan()` only federates datasets.  
When a dataset contains a *theme* that does not already exist in the target CKAN
catalog, the theme is silently ignored – the function never creates the
corresponding CKAN *group*.  The library therefore lacks a public helper that
creates groups (CKAN “themes”) via the API, which is required by the new
feature *push_theme_to_ckan()*.

## Fix
Add a new public helper **`push_theme_to_ckan`** in **`pydatajson/ckan_utils.py`**.
The function:

* receives the CKAN endpoint, an API key and an iterable of theme
  dictionaries (the same structure used in the catalog’s `theme_taxonomy`);
* normalises the theme title into a CKAN‑compatible *name* (slug) using the
  existing `title_to_name` utility;
* checks whether a group with that name already exists (`group_show`);
* creates the group (`group_create`) when it does not exist, preserving the
  original title and optional description;
* returns a list with the names of the groups that were created (or that already
  existed).

The implementation mirrors the error‑handling style used in the existing
`push_dataset_to_ckan` helper and re‑uses the `RemoteCKAN` client already
imported in the module.

```diff
--- a/pydatajson/ckan_utils.py
+++ b/pydatajson/ckan_utils.py
@@
 from .utils import title_to_name, clean_string
 from .exceptions import CKANError
@@
 def push_dataset_to_ckan(
@@
     return result
+
+def push_theme_to_ckan(
+    ckan_url: str,
+    api_key: str,
+    themes: list[dict],
+    organization_id: str | None = None,
+) -> list[str]:
+    """
+    Create CKAN groups (themes) in a remote CKAN instance.
+
+    Parameters
+    ----------
+    ckan_url: str
+        Base URL of the CKAN instance (e.g. ``https://demo.ckan.org``).
+    api_key: str
+        API key with permission to create groups.
+    themes: list[dict]
+        Iterable of theme dictionaries as defined in a catalog’s
+        ``theme_taxonomy``.  Each dict must contain at least a ``title`` key;
+        optional keys are ``description`` and any other extras that should be
+        stored in the group’s ``extras`` field.
+    organization_id: str | None, optional
+        If supplied, the created groups will be associated with the given
+        organization (CKAN ``owner_org`` field).  This mirrors the behaviour
+        of ``push_dataset_to_ckan`` when groups are created automatically.
+
+    Returns
+    -------
+    list[str]
+        The list of group *names* (slugs) that now exist in the CKAN instance.
+        The list includes groups that were already present as well as those
+        created by this call.
+    """
+    from ckanapi import RemoteCKAN, NotAuthorized, NotFound, ValidationError
+
+    client = RemoteCKAN(ckan_url, apikey=api_key)
+    created_groups: list[str] = []
+
+    for theme in themes:
+        # ``title`` is mandatory – fall back to ``id`` if missing.
+        title = theme.get("title") or theme.get("id")
+        if not title:
+            # Skip malformed theme entries silently – they cannot be turned
+            # into a CKAN group.
+            continue
+
+        # Normalise the title to a CKAN‑compatible slug.
+        name = title_to_name(title)
+
+        # Try to fetch the group; if it exists we just record the name.
+        try:
+            client.action.group_show(id=name)
+            created_groups.append(name)
+            continue
+        except NotFound:
+            # Group does not exist – we will create it

=== TASK goadesign__goa-2708 | repo goadesign/goa | go
--- Issue
Non Required attributes with Default/Enum creating error
```
package design

var Storedload = ResultType("application/vnd.test", func() {
	TypeName("Storedload")

	Attributes(func() {
		Attribute("uploaded", Boolean, func() {
			Default(true)
		})
		//Required("uploaded")
	})

	View("default", func() {
		Attribute("uploaded")
	})

})
```
If I comment Required with Default value then it's creating below code. That has a type mismatch error.
```
{
		var zero bool
		if vres.Uploaded == zero {
			vres.Uploaded = true
		}
	}
```
--- REAL FIX (ground truth)
diff --git a/codegen/go_transform.go b/codegen/go_transform.go
index a033158f..0e8aeda7 100644
--- a/codegen/go_transform.go
+++ b/codegen/go_transform.go
@@ -236,7 +236,7 @@ func transformObject(source, target *expr.AttributeExpr, sourceVar, targetVar st
 		// Default value handling. We need to handle default values if the target
 		// type uses default values (i.e. attributes with default values are
 		// non-pointers) and has a default value set.
-		if tdef := tgtMatt.GetDefault(n); tdef != nil && ta.TargetCtx.UseDefault && !srcMatt.IsRequired(n) {
+		if tdef := tgtMatt.GetDefault(n); tdef != nil && ta.TargetCtx.UseDefault && !ta.TargetCtx.Pointer && !srcMatt.IsRequired(n) {
 			switch {
 			case ta.SourceCtx.IsPrimitivePointer(n, srcMatt.AttributeExpr) || !expr.IsPrimitive(srcc.Type):
 				// source attribute is a primitive pointer or not a primitive

--- Proposal A
## Root cause
`ResultType` attributes that are **not required** are generated as pointer fields (e.g. `*bool`).  
When a default value is supplied the code generator still treats the attribute as a
value type and emits:

```go
var zero bool
if vres.Uploaded == zero {
    vres.Uploaded = true
}
```

The comparison `vres.Uploaded == zero` is a type‑mismatch (`*bool` vs `bool`) and the
assignment `vres.Uploaded = true` also fails because a pointer is expected.  
The generator therefore does not take the *required* flag into account when emitting
default‑value handling code.

## Fix
Update the result‑type generator to emit default‑value handling that works for both
required (value) and optional (pointer) attributes.

```diff
--- a/gen/resulttype.go
+++ b/gen/resulttype.go
@@
-        // Emit default handling for required attributes.
-        if a.Default != nil && a.Required {
-            // Zero value of the attribute type.
-            fmt.Fprintf(buf, "\tvar zero %s\n", a.Type.Ref())
-            fmt.Fprintf(buf, "\tif vres.%s == zero {\n", a.Name)
-            fmt.Fprintf(buf, "\t\tvres.%s = %s\n", a.Name, a.Default.Literal())
-            fmt.Fprintf(buf, "\t}\n")
-        }
-
-        // Emit default handling for optional attributes.
-        if a.Default != nil && !a.Required {
-            // Optional attributes are pointers, compare with nil.
-            fmt.Fprintf(buf, "\tif vres.%s == nil {\n", a.Name)
-            // Use the goa helper to create a pointer to the default value.
-            fmt.Fprintf(buf, "\t\tvres.%s = %s(%s)\n",
-                a.Name,
-                a.Type.PointerHelper(),
-                a.Default.Literal())
-            fmt.Fprintf(buf, "\t}\n")
-        }
+        // Emit default handling.
+        // --------------------------------------------------------------
+        // * Required attributes are generated as value types.
+        //   We compare against the zero value of the underlying type.
+        // * Optional attributes are generated as pointers.
+        //   We compare against nil and assign a pointer to the default.
+        // --------------------------------------------------------------
+        if a.Default != nil {
+            if a.Required {
+                // Value type – compare with zero value.
+                fmt.Fprintf(buf, "\tvar zero %s\n", a.Type.Ref())
+                fmt.Fprintf(buf, "\tif vres.%s == zero {\n", a.Name)
+                fmt.Fprintf(buf, "\t\tvres.%s = %s\n", a.Name, a.Default.Literal())
+                fmt.Fprintf(buf, "\t}\n")
+            } else {
+                // Pointer type – compare with nil.
+                fmt.Fprintf(buf, "\tif vres.%s == nil {\n", a.Name)
+                // goa provides helper functions (e.g. Bool, Int, String, etc.)
+                // that return a pointer to the supplied literal.
+                fmt.Fprintf(buf, "\t\tvres.%s = %s(%s)\n",
+                    a.Name,
+                    a.Type.PointerHelper(),
+                    a.Default.Literal())
+                fmt.Fprintf(buf, "\t}\n")
+            }
+        }
```

### What the patch does
* Consolidates the default‑value handling into a single block.
* Distinguishes **required** vs **optional** attributes:
  * Required → compare with the zero value of the underlying type and assign the
    literal directly.
  * Optional → compare with `nil` and assign a pointer created via the appropriate
    helper (`Bool`, `Int`, `String`, …) supplied by the goa runtim
--- Proposal B
## Root cause
When an attribute is **not required** but has a **Default** (or Enum) value, the Go code generator creates a *zero‑value* check (`if vres.Uploaded == zero { … }`).  
For non‑required primitive attributes the generated struct field is a **pointer** (e.g. `*bool`). Comparing a pointer with a primitive zero value (`bool`) is a type‑mismatch and the generated code does not compile.  

The generator therefore assumes the field is a non‑pointer type when a default is present, which is only correct for required attributes. The logic that emits the default‑value assignment must take the attribute’s required‑ness into account and generate a **nil‑pointer** check for optional attributes.

## Fix
Update the response/result code generator to emit a nil‑pointer check when the attribute is optional (i.e. not required) and has a default value. The change is made in `codegen/response.go` (the function that renders default handling for result attributes).

```diff
--- a/codegen/response.go
+++ b/codegen/response.go
@@
 func (g *genResult) renderAttribute(attr *expr.AttributeExpr, name string, required bool) {
     // Existing code that renders the attribute field...
 
-    // Emit default handling.
-    if attr.Default != nil {
-        // For required attributes we compare with the zero value.
-        // For optional attributes we also compare with the zero value,
-        // which is wrong for pointer types.
-        g.Printf("\t{\n")
-        g.Printf("\t\tvar zero %s\n", g.GoType(attr.Type))
-        g.Printf("\t\tif vres.%s == zero {\n", name)
-        g.Printf("\t\t\tvres.%s = %s\n", name, g.literal(attr.Default))
-        g.Printf("\t\t}\n")
-        g.Printf("\t}\n")
-    }
+    // Emit default handling.
+    // * If the attribute is required, the generated field is a value type,
+    //   so we compare against the zero value (the previous behaviour).
+    // * If the attribute is optional, the generated field is a pointer.
+    //   In that case we must check for nil and assign a pointer to the
+    //   default literal.
+    if attr.Default != nil {
+        if required {
+            // Required → value type.
+            g.Printf("\t{\n")
+            g.Printf("\t\tvar zero %s\n", g.GoType(attr.Type))
+            g.Printf("\t\tif vres.%s == zero {\n", name)
+            g.Printf("\t\t\tvres.%s = %s\n", name, g.literal(attr.Default))
+            g.Printf("\t\t}\n")
+            g.Printf("\t}\n")
+        } else {
+            // Optional → pointer type.
+            // Generate a temporary variable holding the default value and
+            // assign its address when the field is nil.
+            g.Printf("\tif vres.%s == nil {\n", name)
+            // Use a temporary variable to avoid taking the address of a
+            // constant literal (which is illegal in Go).
+            g.Printf("\t\tdef := %s\n", g.literal(attr.Default))
+            g.Printf("\t\tvres.%s = &def\n", name)
+            g.Printf("\t}\n")
+        }
+    }
 }
```

### What the patch does
* Detects whether the attribute is required (`required == true`) or optional.
* For **required** attributes it keeps the original zero‑value comparison.
* For **optional** attributes it:
  * Checks `if vres.<field> == nil`.
  * Creates a temporary variable `def` holding the default literal.
  * Assigns `&def` to the field, guaranteeing the correct pointer type.
* Works for any primitive default (bool, int, string, enum, etc.) because `g.literal` already returns a Go

=== TASK kubernetes-incubator__client-python-50 | repo kubernetes-incubator/client-python | python
--- Issue
Support kube-config change context
Right now the load kube config function loads the default context. We should support loading other contexts and [maybe?] listing them.
--- REAL FIX (ground truth)
diff --git a/examples/example4.py b/examples/example4.py
new file mode 100644
index 000000000..af309b191
--- /dev/null
+++ b/examples/example4.py
@@ -0,0 +1,62 @@
+# Copyright 2016 The Kubernetes Authors.
+#
+# Licensed under the Apache License, Version 2.0 (the "License");
+# you may not use this file except in compliance with the License.
+# You may obtain a copy of the License at
+#
+#     http://www.apache.org/licenses/LICENSE-2.0
+#
+# Unless required by applicable law or agreed to in writing, software
+# distributed under the License is distributed on an "AS IS" BASIS,
+# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
+# See the License for the specific language governing permissions and
+# limitations under the License.
+
+import os
+
+from kubernetes import client, config
+from kubernetes.client import configuration
+
+
+def main():
+    config_file = os.path.join(os.path.expanduser('~'), '.kube', 'config')
+    contexts, active_context = config.list_kube_config_contexts(config_file)
+    if not contexts:
+        print("Cannot find any context in kube-config file.")
+        return
+    contexts = [context['name'] for context in contexts]
+    active_context = active_context['name']
+    for i, context in enumerate(contexts):
+        format_str = "%d. %s"
+        if context == active_context:
+            format_str = "* " + format_str
+        print(format_str % (i, context))
+    context = input("Enter context number: ")
+    context = int(context)
+    if context not in range(len(contexts)):
+        print(
+            "Number out of range. Using default context %s." %
+            active_context)
+        context_name = active_context
+    else:
+        context_name = contexts[context]
+
+    # Configs can be set in Configuration class directly or using helper
+    # utility
+    config.load_kube_config(config_file, context_name)
+
+    print("Active host is %s" % configuration.host)
+
+    v1 = client.CoreV1Api()
+    print("Listing pods with their IPs:")
+    ret = v1.list_pod_for_all_namespaces(watch=False)
+    for item in ret.items:
+        print(
+            "%s\t%s\t%s" %
+            (item.status.pod_ip,
+             item.metadata.namespace,
+             item.metadata.name))
+
+
+if __name__ == '__main__':
+    main()
diff --git a/kubernetes/config/__init__.py b/kubernetes/config/__init__.py
index 9254c408b..92632043c 100644
--- a/kubernetes/config/__init__.py
+++ b/kubernetes/config/__init__.py
@@ -12,6 +12,7 @@
 # See the License for the specific language governing permissions and
 # limitations under the License.
 
-from .kube_config import load_kube_config
+from .config_exception import ConfigException
 from .incluster_config import load_incluster_config
-from .incluster_config import ConfigException
+from .kube_config import list_kube_config_contexts
+from .kube_config import load_kube_config
diff --git a/kubernetes/config/config_exception.py b/kubernetes/config/config_exception.py
new f
--- Proposal A
## Root cause
`kubernetes.config.kube_config.load_kube_config()` always loads the *current* context from the kube‑config file.  
The function does not accept a `context` argument, nor is there any helper to enumerate the available contexts.  
Consequently callers cannot programmatically switch to a different context or discover which contexts exist, which is exactly what the issue requests.

## Fix
Add support for an optional `context` argument to `load_kube_config()` and expose a new helper `list_kube_config_contexts()` that returns the list of contexts defined in the kube‑config file.

```diff
--- a/kubernetes/config/kube_config.py
+++ b/kubernetes/config/kube_config.py
@@
-def load_kube_config(config_file=None, client_configuration=None,
-                    persist_config=True):
-    """Load kube-config from default location and set the default configuration.
-
-    If the environment variable KUBECONFIG is set, its value will be used as
-    the config file path. If not set, the default location is
-    $HOME/.kube/config.
-
-    :param config_file: Name of the kube-config file.
-    :type config_file: str
-    :param client_configuration: The configuration object to set.
-    :type client_configuration: kubernetes.client.Configuration
-    :param persist_config: If True, the configuration will be persisted to
-        the default location.
-    :type persist_config: bool
-    """
-    if client_configuration is None:
-        client_configuration = configuration
-
-    config = _get_kube_config_loader_for_yaml_file(
-        config_file=config_file, persist_config=persist_config)
-    config.load_and_set(client_configuration)
+def load_kube_config(config_file=None, context=None,
+                    client_configuration=None, persist_config=True):
+    """Load kube-config from a file and optionally switch to a specific context.
+
+    The function behaves like the original implementation but adds the ability
+    to select a *named* context.  If ``context`` is ``None`` the current context
+    from the kube‑config file is used (the historic behaviour).  When a context
+    name is supplied the configuration is populated with the data from that
+    context.
+
+    :param config_file: Path to the kube‑config file.  If omitted the function
+        follows the standard lookup rules (``KUBECONFIG`` env var or
+        ``$HOME/.kube/config``).
+    :type config_file: str
+    :param context: Name of the context to load.  If ``None`` the default
+        context from the file is used.
+    :type context: str
+    :param client_configuration: The ``Configuration`` object to populate.
+    :type client_configuration: kubernetes.client.Configuration
+    :param persist_config: If ``True`` the configuration will be persisted to
+        the default location.
+    :type persist_config: bool
+    """
+    if client_configuration is None:
+        client_configuration = configuration
+
+    # Load the raw kube‑config data.
+    config = _get_kube_config_loader_for_yaml_file(
+        config_file=config_file, persist_config=persist_config)
+
+    # If a specific context is requested, tell the loader to use it.
+    if context is not None:
+        config.set_active_context(context)
+
+    config.load_and_set(client_configuration)
@@
 def _get_kube_config_loader_for_yaml_file(config_file=None, persist_config=False):
@@
     return loader
+
+
+def list_kube_config_contexts(config_file=None):
+    """Return a list of contexts defined in the kube‑
--- Proposal B
**Root cause**  
`config.load_kube_config()` always loads the *current‑context* stored in the kube‑config file. The loader (`KubeConfigLoader`) has no way to override that value, and the public API does not expose a way to list or select a different context. Consequently users cannot programmatically switch to another context without editing the kube‑config file itself.

**Fix**  
* Add an optional `context` argument to `load_kube_config()` and pass it to `KubeConfigLoader`.  
* Extend `KubeConfigLoader` to store the overridden context and use it when loading the configuration.  
* Expose a helper `list_kube_config_contexts()` that returns the available contexts together with the current one (the upstream client already has a similar helper, but it was missing in this version).  

```diff
--- a/kubernetes/config/kube_config.py
+++ b/kubernetes/config/kube_config.py
@@
-import os
-import yaml
-from . import client
+import os
+import yaml
+from . import client
@@
-def load_kube_config(config_file=None, client_configuration=None, persist_config=True):
-    """Load config from default location and set the default configuration."""
-    loader = KubeConfigLoader(config_file)
-    loader.load_and_set(client_configuration, persist_config)
+def load_kube_config(
+    config_file=None,
+    context=None,
+    client_configuration=None,
+    persist_config=True,
+):
+    """
+    Load a kube‑config file and configure the default client.
+
+    Parameters
+    ----------
+    config_file: str, optional
+        Path to the kube‑config file. If omitted the standard locations are used.
+    context: str, optional
+        Name of the context to load. If omitted the ``current-context`` entry in the
+        file is used.
+    client_configuration: kubernetes.client.Configuration, optional
+        Existing configuration object to populate. If ``None`` a new one is created.
+    persist_config: bool, default ``True``
+        When ``True`` the loaded configuration is stored in the global ``Configuration``.
+    """
+    loader = KubeConfigLoader(config_file, context=context)
+    loader.load_and_set(client_configuration, persist_config)
@@
-def list_kube_config_contexts(config_file=None):
-    """Return a tuple of (contexts, current_context)."""
-    loader = KubeConfigLoader(config_file)
-    return loader.list_contexts()
+def list_kube_config_contexts(config_file=None):
+    """
+    Return a tuple ``(contexts, current_context)`` where *contexts* is a list of
+    dictionaries as they appear in the kube‑config file and *current_context* is
+    the name of the context marked as current.
+    """
+    loader = KubeConfigLoader(config_file)
+    return loader.list_contexts()
@@
-class KubeConfigLoader(object):
-    def __init__(self, config_file=None):
-        self._config_file = config_file or os.getenv('KUBECONFIG', os.path.expanduser('~/.kube/config'))
-        self._config = yaml.safe_load(open(self._config_file))
-        self._contexts = self._config.get('contexts', [])
-        self._current_context = self._config.get('current-context')
+class KubeConfigLoader(object):
+    def __init__(self, config_file=None, context=None):
+        """
+        Parameters
+        ----------
+        config_file: str, optional
+            Path to the kube‑config file.
+        context: str, optional
+            If supplied, this context name overrides the ``current-context`` entry
+            in the file.
+        """
+        self._config_file = config_f

=== TASK apigee__registry-1100 | repo apigee/registry | go
--- Issue
Registry tool: setting unknown `registry config` parameters seems to silently fail
`invalid` here could be any unrecognized parameter.

```
% registry config set invalid parameter
----
Need more help?
https://github.com/apigee/registry/wiki

% registry config set invalid 123      
----
Need more help?
https://github.com/apigee/registry/wiki
```
--- REAL FIX (ground truth)
diff --git a/cmd/registry/cmd/auth/print_token.go b/cmd/registry/cmd/auth/print_token.go
index 17e2260..d445d99 100644
--- a/cmd/registry/cmd/auth/print_token.go
+++ b/cmd/registry/cmd/auth/print_token.go
@@ -27,9 +27,6 @@ func printTokenCommand() *cobra.Command {
 		Short: "Invoke token-source to print a token",
 		Args:  cobra.ExactArgs(0),
 		RunE: func(cmd *cobra.Command, args []string) error {
-			cmd.SilenceUsage = true
-			cmd.SilenceErrors = true
-
 			_, c, err := config.ActiveRaw()
 			if err != nil {
 				if err == config.ErrNoActiveConfiguration {
diff --git a/cmd/registry/cmd/config/config.go b/cmd/registry/cmd/config/config.go
index 62f1d01..cc1c114 100644
--- a/cmd/registry/cmd/config/config.go
+++ b/cmd/registry/cmd/config/config.go
@@ -15,8 +15,6 @@
 package config
 
 import (
-	"fmt"
-
 	"github.com/apigee/registry/cmd/registry/cmd/config/configurations"
 	"github.com/spf13/cobra"
 )
@@ -38,11 +36,3 @@ func Command() *cobra.Command {
 	cmd.AddCommand(unsetCommand())
 	return cmd
 }
-
-type UnknownPropertyError struct {
-	property string
-}
-
-func (n UnknownPropertyError) Error() string {
-	return fmt.Sprintf("Unknown property: %q.", n.property)
-}
diff --git a/cmd/registry/cmd/config/configurations/activate.go b/cmd/registry/cmd/config/configurations/activate.go
index cf15bee..453829a 100644
--- a/cmd/registry/cmd/config/configurations/activate.go
+++ b/cmd/registry/cmd/config/configurations/activate.go
@@ -27,8 +27,6 @@ func activateCommand() *cobra.Command {
 		Short: "Activates an existing named configuration",
 		Args:  cobra.ExactArgs(1),
 		RunE: func(cmd *cobra.Command, args []string) error {
-			cmd.SilenceUsage = true
-			cmd.SilenceErrors = true
 			name := args[0]
 			if err := config.ValidateName(name); err != nil {
 				return err
diff --git a/cmd/registry/cmd/config/configurations/create.go b/cmd/registry/cmd/config/configurations/create.go
index 24d9c34..60dcfa9 100644
--- a/cmd/registry/cmd/config/configurations/create.go
+++ b/cmd/registry/cmd/config/configurations/create.go
@@ -30,8 +30,6 @@ func createCommand() *cobra.Command {
 		Example: "registry config configurations create localhost --registry.address='locahost:8080'",
 		Args:    cobra.ExactArgs(1),
 		RunE: func(cmd *cobra.Command, args []string) error {
-			cmd.SilenceUsage = true
-			cmd.SilenceErrors = true
 			name := args[0]
 			if err := config.ValidateName(name); err != nil {
 				return err
diff --git a/cmd/registry/cmd/config/configurations/delete.go b/cmd/registry/cmd/config/configurations/delete.go
index 8110121..b4ce254 100644
--- a/cmd/registry/cmd/config/configurations/delete.go
+++ b/cmd/registry/cmd/config/configurations/delete.go
@@ -28,8 +28,6 @@ func deleteCommand() *cobra.Command {
 		Short: "Deletes a named configuration",
 		Args:  cobra.MinimumNArgs(1),
 		RunE: func(cmd *cobra.Command, args []string) error {
-			cmd.SilenceUsage = true
-			cmd.SilenceErrors = true
 			for _, name := range args {
 				if err := config.ValidateNam
--- Proposal A
## Root cause
`registry config set` forwards the supplied key/value pair directly to Viper without checking whether the key is a supported configuration option.  
Viper silently creates a new entry for an unknown key, and the command’s error handling treats the operation as successful, so the CLI only prints the generic help text. This makes it appear that the command “fails silently”.

## Fix
Add explicit validation of the configuration key against a whitelist of supported keys.  
If the key is not recognised, return an error so the CLI prints a helpful message and exits with a non‑zero status.

```diff
--- a/cmd/registry/cmd/config/set.go
+++ b/cmd/registry/cmd/config/set.go
@@
 package config
 
 import (
     "fmt"
 
     "github.com/spf13/cobra"
     "github.com/spf13/viper"
 )
 
+// allowedConfigKeys is the set of configuration keys that the CLI recognises.
+// When a new configurable option is added, it should be added to this map.
+var allowedConfigKeys = map[string]struct{}{
+    // Core connection / project settings
+    "project":   {},
+    "location":  {},
+    // Output formatting
+    "output":    {},
+    "format":    {},
+    // Default resource identifiers
+    "api":       {},
+    "version":   {},
+    "spec":      {},
+    // Authentication / credential handling
+    "auth_token": {},
+    "credential_file": {},
+}
+
 // setCmd represents the `registry config set` command.
 var setCmd = &cobra.Command{
     Use:   "set <key> <value>",
     Short: "Set a configuration value",
     Args:  cobra.ExactArgs(2),
     RunE: func(cmd *cobra.Command, args []string) error {
-        key := args[0]
-        value := args[1]
-
-        viper.Set(key, value)
-        if err := viper.WriteConfig(); err != nil {
-            return fmt.Errorf("failed to write config: %w", err)
-        }
-        fmt.Fprintf(cmd.OutOrStdout(), "Config %q set to %q\n", key, value)
-        return nil
+        key := args[0]
+        value := args[1]
+
+        // Validate that the key is a known configuration option.
+        if _, ok := allowedConfigKeys[key]; !ok {
+            return fmt.Errorf("unknown configuration key %q; run \"registry config list\" to see supported keys", key)
+        }
+
+        viper.Set(key, value)
+        if err := viper.WriteConfig(); err != nil {
+            return fmt.Errorf("failed to write config: %w", err)
+        }
+        fmt.Fprintf(cmd.OutOrStdout(), "Config %q set to %q\n", key, value)
+        return nil
     },
 }
 
 func init() {
     ConfigCmd.AddCommand(setCmd)
 }
```

### What was changed
1. **Added `allowedConfigKeys` map** – enumerates all configuration keys that the CLI currently supports.
2. **Inserted validation logic** – before writing the config, the command now checks whether the supplied key exists in `allowedConfigKeys`.  
   *If the key is unknown, an error is returned with a clear message.*  
3. **Preserved existing behaviour** for valid keys (set the value, write the config file, and print a confirmation).

## How to verify
1. **Build the CLI** (or run `go test ./...` to ensure the repository still compiles).  
   ```bash
   go build ./cmd/registry
   ```

2. **Run a valid set command** – it should succeed and print the confirmation line.
   ```bash
   ./registry config set project my-project
   # Expected output: Config "project" set to "my-project"
   ```

3. **Run an unknown‑key set command** – it should now exit with a non‑zero status and display an error.
   ```bash
   ./registry 
--- Proposal B
## Root cause
`registry config set` forwards the supplied key/value directly to Viper (`viper.Set(key, value)`).  
Viper will happily store any key, even if the application never reads it.  
Because the command does not validate the key against the list of supported configuration
options, an unknown key is silently ignored – the CLI prints the generic help banner but
does not return an error.  

## Fix
Add explicit validation of the configuration key before writing it.  
The set of supported keys is defined in `internal/config/keys.go` (new file) and the
`config set` command now checks the supplied key against this whitelist, returning a
clear error for unknown parameters.

```diff
--- a/cmd/config/set.go
+++ b/cmd/config/set.go
@@
 package config
 
 import (
+	"fmt"
 	"github.com/spf13/cobra"
 	"github.com/spf13/viper"
 )
 
 // setCmd represents the config set command
 var setCmd = &cobra.Command{
@@
-	RunE: func(cmd *cobra.Command, args []string) error {
-		key := args[0]
-		value := args[1]
-		viper.Set(key, value)
-		if err := viper.WriteConfig(); err != nil {
-			return err
-		}
-		return nil
-	},
+	RunE: func(cmd *cobra.Command, args []string) error {
+		key := args[0]
+		value := args[1]
+
+		// Validate the key against the list of known configuration options.
+		if !config.IsValidKey(key) {
+			return fmt.Errorf("unknown configuration key %q. Run \"registry config list\" to see supported keys", key)
+		}
+
+		viper.Set(key, value)
+		if err := viper.WriteConfig(); err != nil {
+			return err
+		}
+		return nil
+	},
 }
 
 func init() {
 	configCmd.AddCommand(setCmd)
 }
 
--- a/internal/config/keys.go
+++ b/internal/config/keys.go
@@
 package config
 
 import "strings"
 
 // Known configuration keys. Keep this list in sync with the documentation
 // and with the fields that the application actually reads.
 var knownConfigKeys = map[string]struct{}{
-	// TODO: add real keys when they exist
+	"project":          {}, // Google Cloud project ID
+	"location":         {}, // Default location for resources
+	"output":           {}, // Output format (json|yaml|text)
+	"api_endpoint":     {}, // Registry API endpoint
+	"auth_token":       {}, // Authentication token
+	"disable_tls":      {}, // Disable TLS verification (bool)
+	"cache_dir":        {}, // Directory used for local caching
+	"timeout_seconds":  {}, // HTTP timeout in seconds
 }
 
 // IsValidKey reports whether the supplied key is a known configuration option.
 func IsValidKey(key string) bool {
-	// TODO: Please implement
-	return false
+	_, ok := knownConfigKeys[strings.ToLower(key)]
+	return ok
 }
 
 // ListKeys returns a slice of all known configuration keys, sorted alphabetically.
 func ListKeys() []string {
-	// TODO: Please implement
-	return nil
+	keys := make([]string, 0, len(knownConfigKeys))
+	for k := range knownConfigKeys {
+		keys = append(keys, k)
+	}
+	// Simple alphabetical sort
+	sort.Strings(keys)
+	return keys
 }
```

### What was changed
1. **`cmd/config/set.go`**
   * Imported `fmt`.
   * Added validation step using a new helper `config.IsValidKey`.
   * Returns a descriptive error when the key is unknown.

2. **`internal/config/keys.go`** (new file)
   * Declares a whitelist `knownConfigKeys` containing all supported configuration options.
   * Implements `IsValidKey` to check membership (case‑insensitive).
   * Implements `ListKeys` to expose the list for potential future commands (e.g., `registry config list`).

## How to verify
1. **Build the CLI**  
