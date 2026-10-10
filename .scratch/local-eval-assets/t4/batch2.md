

=== TASK pypa__auditwheel-287 | repo pypa/auditwheel | python
--- Issue
ncurses 5 -> 6 transition means we probably need to drop some libraries from the manylinux whitelist
The [PEP 513 whitelist](https://www.python.org/dev/peps/pep-0513/#the-manylinux1-policy) includes `libpanelw.so.5` and `libncursesw.so.5`, both of which are part of ncurses version 5.

But apparently the new hostness is [ncurses 6, which breaks ABI with ncurses 5](https://www.gnu.org/software/ncurses/). I'm told that Fedora 24 and successors have already switched to ncurses 6, with the ncurses 5 libraries being available in a compatibility package, but not installed by default. So... I guess these have to be dropped from the whitelist.
--- REAL FIX (ground truth)
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
+                    )
+                symbol_versions_arch[prefix].update(
+                    policy['symbol_versions'][arch][prefix])
+            symbol_versions[arch] = symbol_versions_arch
+
+
 with open(join(dirname(abspath(__file__)), 'policy.json')) as f:
     _POLICIES = []
     _policies_temp = json.load(f)
+    _validate_pep600_compliance(_policies_temp)
     for _p in _policies_temp:
         if _ARCH_NAME in _p['symbol_versions'].keys() or _p['name'] == 'linux':
             if _p['name'] != 'linux':
diff --git a/auditwheel/policy/policy.json b/auditwheel/policy/policy.json
index 7c9b722..bce6fb9 100644
--- a/auditwheel/policy/policy.json
+++ b/auditwheel/policy/policy.json
@@ -9,8 +9,8 @@
      "symbol_versions": {
          "i686": {
            "CXXABI": ["1.3", "1.3.1"],
-           "GCC": ["3.0", "3.3", "3.3.1", "3.4", "3.4.2", "3.4.4", "4.0.0", "4.2.0"],
-           "GLIBC": ["2.0", "2.1", "2.1.1", "2.1.2", "2.1.3", "2.2", "2.2.1", "2.2.2", "2.2.3", "2.2.4", "2.2.5", "2.2.6
--- Proposal A
## Root cause
`auditwheel` uses a hard‑coded whitelist of system libraries that are allowed to remain unbundled when repairing wheels for the *manylinux* policies.  
The original **manylinux1** whitelist (derived from PEP 513) contains the entries  

```
libpanelw.so.5
libncursesw.so.5
```

These libraries belong to **ncurses 5**. Modern Fedora (≥ 24) and most current Linux distributions ship **ncurses 6** only; the ncurses 5 libraries are provided only by an optional compatibility package and are not present on the default build hosts. Because the ABI changed between ncurses 5 and ncurses 6, keeping the old entries in the whitelist makes `auditwheel` incorrectly treat wheels that link against `libncursesw.so.5` as “manylinux‑compatible”, even though the required library will not be available on the target system.  

The fix is to remove the ncurses 5 entries from the whitelist (and any derived whitelist for newer manylinux policies that inherit from the manylinux1 list).

## Fix
```diff
--- a/auditwheel/policy.py
+++ b/auditwheel/policy.py
@@
-MANYLINUX1_LIBS = {
-    # ... many other entries ...
-    "libpanelw.so.5",
-    "libncursesw.so.5",
-    # ... many other entries ...
-}
+MANYLINUX1_LIBS = {
+    # ... many other entries ...
+    # NOTE: libpanelw.so.5 and libncursesw.so.5 (ncurses 5) have been removed
+    # because modern manylinux build environments provide ncurses 6 only.
+    # Keeping the old entries would incorrectly whitelist a library that is
+    # not guaranteed to be present on the target system.
+    # ... many other entries ...
+}
 
@@
-# manylinux2010 inherits from manylinux1 and adds a few more libs
-MANYLINUX2010_LIBS = MANYLINUX1_LIBS.union({
-    # ... additional entries for manylinux2010 ...
-})
+# manylinux2010 inherits from manylinux1 and adds a few more libs.
+# The ncurses‑5 entries have already been removed from MANYLINUX1_LIBS,
+# so they are not present here either.
+MANYLINUX2010_LIBS = MANYLINUX1_LIBS.union({
+    # ... additional entries for manylinux2010 ...
+})
 
@@
-# manylinux2014 inherits from manylinux2010 and adds a few more libs
-MANYLINUX2014_LIBS = MANYLINUX2010_LIBS.union({
-    # ... additional entries for manylinux2014 ...
-})
+# manylinux2014 inherits from manylinux2010 and adds a few more libs.
+# The ncurses‑5 entries have already been removed from the base set.
+MANYLINUX2014_LIBS = MANYLINUX2010_LIBS.union({
+    # ... additional entries for manylinux2014 ...
+})
```

*Explanation of the diff*  

* The two ncurses 5 library names are deleted from `MANYLINUX1_LIBS`.  
* A comment is added to explain why they are removed.  
* No other code changes are required because the newer manylinux policy sets (`MANYLINUX2010_LIBS`, `MANYLINUX2014_LIBS`, etc.) are built by union‑ing the base set; they automatically inherit the corrected list.

## How to verify
1. **Unit‑test check** – Run the existing `auditwheel` test suite. All tests should still pass, confirming that the whitelist change does not break unrelated functionality.

2. **Manual whitelist inspection**  
   ```bash
   python -c "from auditwheel.policy import MANYLINUX1_LIBS; \
             print('libncursesw.so.5' in MANYLINUX1_LIBS, \
                   'libpanelw.so.5' in MANYLINUX1_LIBS)"
   ```
   Expected output: `False False`.

3. **Repair a wheel that depends on ncurses 5**  
   * Build a small C extension that links against `-lncursesw` on a system that still has ncurses 5 installed (or manually copy the library name into
--- Proposal B
## Root cause
`auditwheel` uses the *manylinux* policy JSON files to decide which shared libraries are allowed to remain bundled in a wheel.  
The original PEP 513 whitelist (used for the `manylinux1` policy) contains the entries  

```
libpanelw.so.5
libncursesw.so.5
```

These libraries belong to **ncurses 5**. Modern manylinux build images (Fedora 24 + manylinux2010/2014) ship **ncurses 6**, whose ABI is incompatible with version 5. The compatibility packages that provide the old libraries are **not installed by default**, so a wheel built on a recent image will never actually have those `.so.5` files present. Keeping them in the whitelist therefore lets wheels incorrectly pass the audit even though the libraries cannot be satisfied on the target system.

The fix is simply to drop the two ncurses‑5 libraries from every policy’s `lib_whitelist`.

## Fix
```diff
diff --git a/src/auditwheel/policy/policy.json b/src/auditwheel/policy/policy.json
index 0ed5d38..f3a9c1e 100644
--- a/src/auditwheel/policy/policy.json
+++ b/src/auditwheel/policy/policy.json
@@
-         "lib_whitelist": [
-             "libgcc_s.so.1",
-             "libstdc++.so.6",
-             "libm.so.6", "libdl.so.2", "librt.so.1",
-             "libc.so.6", "libnsl.so.1", "libutil.so.1", "libpthread.so.0",
-             "libX11.so.6", "libXext.so.6", "libXrender.so.1", "libICE.so.6",
-             "libSM.so.6", "libGL.so.1", "libgobject-2.0.so.0",
-             "libgthread-2.0.so.0", "libglib-2.0.so.0", "libresolv.so.2",
-             "libpanelw.so.5", "libncursesw.so.5"
-         ]},
+         "lib_whitelist": [
+             "libgcc_s.so.1",
+             "libstdc++.so.6",
+             "libm.so.6", "libdl.so.2", "librt.so.1",
+             "libc.so.6", "libnsl.so.1", "libutil.so.1", "libpthread.so.0",
+             "libX11.so.6", "libXext.so.6", "libXrender.so.1", "libICE.so.6",
+             "libSM.so.6", "libGL.so.1", "libgobject-2.0.so.0",
+             "libgthread-2.0.so.0", "libglib-2.0.so.0", "libresolv.so.2"
+         ]},
@@
-         "lib_whitelist": [
-             "libgcc_s.so.1",
-             "libstdc++.so.6",
-             "libm.so.6", "libdl.so.2", "librt.so.1",
-             "libc.so.6", "libnsl.so.1", "libutil.so.1", "libpthread.so.0",
-             "libX11.so.6", "libXext.so.6", "libXrender.so.1", "libICE.so.6",
-             "libSM.so.6", "libGL.so.1", "libgobject-2.0.so.0",
-             "libgthread-2.0.so.0", "libglib-2.0.so.0", "libresolv.so.2",
-             "libpanelw.so.5", "libncursesw.so.5"
-         ]},
+         "lib_whitelist": [
+             "libgcc_s.so.1",
+             "libstdc++.so.6",
+             "libm.so.6", "libdl.so.2", "librt.so.1",
+             "libc.so.6", "libnsl.so.1", "libutil.so.1", "libpthread.so.0",
+             "libX11.so.6", "libXext.so.6", "libXrender.so.1", "libICE.so.6",
+             "libSM.so.6", "libGL.so.1", "libgobject-2.0.so.0",
+             "libgthread-2.0.so.0", "libglib-2.0.so.0", "libresolv.so.2"
+         ]},
@@
-         "lib_whitelist": [
-             "libgcc_s.so.1",
-             "libstdc++.so.6",
-             "libm.so.6", "libdl.so.2", "librt.so.1",
-             "libc.so.6", "libnsl.so.1", "libutil.so.1", "libpthread.so.0",
-             "libX11.so.6", "libXext.so.6", "libXrender.so.1", "libICE.so.6",
-             "libSM.so.6", "libGL.so.1", "libgobject-2.0.so.0",
-             "libgthread-2.0.so.0", "libglib-2.0.so.0", "libresolv.so.2",
-             "libpanelw.so.5", "libncursesw.so.5"


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

--- REAL FIX (ground truth)
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
-            units=self.units,
-        )
+        return self.__class__(x, units=self.units)
+
+    def __truediv__(self, other) -> Union[float, "Value"]:
+        x = float(self) / self._other_same_units(other)
+        if isinstance(other, Value):
+            return x
+
+        return self.__class__(x, units=self.units)
 
     def __abs__(self) -> "Value":
         """Absolute value"""
diff --git a/doc/changelog.rst b/doc/changelog.rst
index ab9c69a..8303c13 100644
--- a/doc/changelog.rst
+++ b/doc/changelog.rst
@@ -19,6 +19,8 @@ Functionality improvements
 Bug Fixes
 *********
 - Fixes :code:`ERROR` logging level being ignored from environment variable :code:`AUTODE_LOG_LEVEL`
+- Fixes :code:`autode.values.Value` instances generating items with units on division, and throw a warning if multiplying
+
 
 1.3.4
 --------

--- Proposal A
## Root cause
`autode.values.Value` (and its subclasses such as `PotentialEnergy`) overload arithmetic operators, but the implementations of `__mul__` and `__truediv__` simply return a new instance of the left‑hand class without recomputing the resulting units. Consequently:

* `e * e` keeps the original energy unit (`Ha`) instead of producing a unit of `Ha²`.
* `e / e` also keeps the energy unit instead of yielding a dimension‑less result.

The bug stems from the operator overloads ignoring unit algebra.

## Fix
```diff
diff --git a/autode/values.py b/autode/values.py
index 295444d..c9f7e2a 100644
--- a/autode/values.py
+++ b/autode/values.py
@@
 class Value(float):
@@
     def __add__(self, other):
         return self.__class__(float(self) + float(other), units=self.units)
 
     def __sub__(self, other):
         return self.__class__(float(self) - float(other), units=self.units)
 
-    def __mul__(self, other):
-        # NOTE: multiplication currently returns the left‑hand class unchanged.
-        # This is incorrect for unit handling – e.g. Energy * Energy should give
-        # a value with units Ha**2, not Ha.
-        return self.__class__(float(self) * float(other), units=self.units)
-
-    def __truediv__(self, other):
-        # NOTE: division suffers from the same problem as multiplication.
-        return self.__class__(float(self) / float(other), units=self.units)
+    # --------------------------------------------------------------------- #
+    # Arithmetic with proper unit handling                                    #
+    # --------------------------------------------------------------------- #
+    def __mul__(self, other):
+        """
+        Multiply two ``Value`` objects (or a ``Value`` with a scalar) while
+        correctly propagating the units.
+
+        * ``Value * scalar`` → same subclass, original units
+        * ``Value * Value``   → generic ``Value`` with combined units
+        """
+        if isinstance(other, Value):
+            # combine the numeric values and the units
+            new_val = float(self) * float(other)
+            new_units = self.units * other.units
+            # Return a generic ``Value`` – there is no dedicated subclass for
+            # the product of two arbitrary quantities.
+            return Value(new_val, units=new_units)
+        elif isinstance(other, (int, float, np.ndarray)):
+            return self.__class__(float(self) * float(other), units=self.units)
+        else:
+            return NotImplemented
 
+    def __rmul__(self, other):
+        # ``scalar * Value`` should behave like ``Value * scalar``
+        return self.__mul__(other)
+
+    def __truediv__(self, other):
+        """
+        Divide two ``Value`` objects (or a ``Value`` by a scalar) while
+        correctly propagating the units.
+
+        * ``Value / scalar`` → same subclass, original units
+        * ``Value / Value``   → generic ``Value`` with divided units
+        """
+        if isinstance(other, Value):
+            new_val = float(self) / float(other)
+            new_units = self.units / other.units
+            return Value(new_val, units=new_units)
+        elif isinstance(other, (int, float, np.ndarray)):
+            return self.__class__(float(self) / float(other), units=self.units)
+        else:
+            return NotImplemented
+
+    def __rtruediv__(self, other):
+        # ``scalar / Value`` – the scalar is treated as a dimension‑less value
+        if isinstance(othe
--- Proposal B
## Root cause
`autode.values.Value` (and its subclasses such as `PotentialEnergy`) overload the arithmetic operators `__mul__` and `__truediv__` in a way that always returns an instance of the left‑hand operand’s class, preserving its original unit.  
When two `Value` objects are multiplied or divided the code simply copies the left operand’s unit instead of combining the units of both operands. Consequently:

* `e * e` returns an `Energy` object with unit **Ha** instead of a value with unit **Ha²** (or a generic `Value`).
* `e / e` returns an `Energy` object with unit **Ha** instead of a dimension‑less value.

The bug stems from the operator implementations in `autode/values.py` (or the equivalent module) that do not compute the resulting unit and that always instantiate the left‑hand class.

## Fix
Update the arithmetic operator overloads in `autode/values.py` (the central `Value` implementation) to:

1. Detect when the right‑hand operand is also a `Value`.
2. Combine the units using the underlying unit handling library (the project uses **pint** via `self._quantity`).
3. Return a new instance of the most appropriate class:
   * If the resulting unit matches the subclass’s canonical unit, keep the subclass.
   * Otherwise return a generic `Value` (or `Dimensionless` when the unit is dimensionless).

```diff
--- a/autode/values.py
+++ b/autode/values.py
@@
 class Value:
@@
     def __add__(self, other):
         """Add two values – units must be compatible."""
         if isinstance(other, Value):
-            return self.__class__(self.value + other.value, self.unit)
+            # Use pint to handle unit conversion automatically
+            new_qty = self._quantity + other._quantity
+            return self._new_from_quantity(new_qty)
         else:
-            return self.__class__(self.value + other, self.unit)
+            new_qty = self._quantity + other * self._unit
+            return self._new_from_quantity(new_qty)
@@
     def __sub__(self, other):
         """Subtract two values – units must be compatible."""
         if isinstance(other, Value):
-            return self.__class__(self.value - other.value, self.unit)
+            new_qty = self._quantity - other._quantity
+            return self._new_from_quantity(new_qty)
         else:
-            return self.__class__(self.value - other, self.unit)
+            new_qty = self._quantity - other * self._unit
+            return self._new_from_quantity(new_qty)
@@
-    def __mul__(self, other):
-        \"\"\"Multiply two values – units are *not* combined (bug).\"\"\"
-        if isinstance(other, Value):
-            # BUG: returns left‑hand class with original unit
-            return self.__class__(self.value * other.value, self.unit)
-        else:
-            return self.__class__(self.value * other, self.unit)
+    def __mul__(self, other):
+        \"\"\"Multiply two values – combine units correctly.\"
+
+        The result is a ``Value`` whose unit is the product of the
+        operands' units.  If the resulting unit matches the canonical
+        unit of the left‑hand class, an instance of that class is
+        returned; otherwise a generic ``Value`` (or ``Dimensionless``)
+        is produced.
+        \"\"\"
+        if isinstance(other, Value):
+            # Use pint to multiply the underlying quantities
+            new_qty = self._quantity * other._quantity
+        else:
+            # ``other`` is a plain number – treat it as dimensionless
+          

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
--- REAL FIX (ground truth)
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

--- Proposal A
## Root cause
`config_manager.py` builds the warning message as a single line string that concatenates the two fix commands.  
Because the filename is inserted without quoting, paths that contain spaces (e.g. *~/Library/Application Support/*) break the displayed command.  
The single‑line format also makes it easy for users to miss that **both** commands are required.

## Fix
* Build the warning message as a multi‑line string.
* Quote the filename (`"{filep}"`) in both commands so that spaces are handled correctly.
* Add a leading bullet (`*`) on each line to make the two commands visually distinct.

```diff
--- a/snowflake/connector/config_manager.py
+++ b/snowflake/connector/config_manager.py
@@
-    # Build warning about bad owner / permissions.
-    chmod_message = ""
-    if not filep.owner() == getpass.getuser():
-        chmod_message += (
-            f". To change owner, run `chown $USER {filep}`."
-        )
-    if not (filep.stat().st_mode & 0o600) == 0o600:
-        chmod_message += (
-            f" To restrict permissions, run `chmod 0600 {filep}`."
-        )
-
-    warn(f"Bad owner or permissions on {str(filep)}{chmod_message}")
+    # Build warning about bad owner / permissions.
+    # NOTE: The warning is now multi‑line and the filename is quoted so that
+    # paths containing spaces are handled correctly.
+    warning_lines = [
+        f'Bad owner or permissions on "{filep}".',
+    ]
+
+    # Owner fix
+    if not filep.owner() == getpass.getuser():
+        warning_lines.append(
+            f' * To change owner, run `chown $USER "{filep}"`.'
+        )
+
+    # Permissions fix
+    if not (filep.stat().st_mode & 0o600) == 0o600:
+        warning_lines.append(
+            f' * To restrict permissions, run `chmod 0600 "{filep}"`.'
+        )
+
+    # Join the lines with a newline so the warning appears as a readable block.
+    warn("\n".join(warning_lines))
```

### Explanation of changes
1. **Quote the path** – `"{filep}"` ensures that spaces or other special characters are interpreted correctly by the shell commands shown to the user.
2. **Multi‑line formatting** – Using a list of strings (`warning_lines`) that are later joined with `"\n"` produces a clear, readable block.
3. **Bulleted list** – Prefixing each fix line with `*` makes it obvious that there are two separate actions.
4. The logic that decides whether each line should be added remains unchanged; only the presentation is altered.

## How to verify
1. **Unit‑test / integration test**  
   *Create a temporary config file with a space in its path* (e.g. `tmpdir / "My Config/config.toml"`).  
   *Set the file owner to a different user* (or mock `filep.owner()` to return a wrong value) and set its mode to something other than `0600`.  
   *Import the module and trigger the warning* (e.g. call the function that validates the config).  
   Capture `stderr` (or use `warnings.catch_warnings`) and assert that:
   * The warning contains **two separate lines** after the first line.
   * Each command line is **quoted** (`"…/My Config/config.toml"`).
   * The lines start with `*` (bullet) as shown in the desired output.

2. **Manual verification**  
   *Run the connector on a machine where the config file lives under `~/Library/Application Support/snowflake/config.toml` with deliberately wrong permissions.*  
   Observe that the warning printed to the console matches the new format:
   ```
   UserWarning: Bad owner or permissions on "/Users/.../Library/Applicati
--- Proposal B
## Root cause
`config_manager.py` builds the warning message as a single string:

```python
warn(f"Bad owner or permissions on {str(filep)}{chmod_message}")
```

`chmod_message` already contains the two shell commands concatenated on the same line.  
Because the filename is inserted without quoting, paths that contain spaces (e.g. *Application Support*) break when the user copies the command, and the one‑liner makes it easy to miss that **both** commands must be run.

## Fix
* Build the warning message explicitly with line breaks and bullet points.  
* Quote the filename with double quotes so that spaces are handled correctly.  
* Keep the original behaviour for non‑problematic paths.

```diff
diff --git a/src/snowflake/connector/config_manager.py b/src/snowflake/connector/config_manager.py
index 7c9e1a2..d4f3b8c 100644
--- a/src/snowflake/connector/config_manager.py
+++ b/src/snowflake/connector/config_manager.py
@@
-        if not (filep.is_file() and filep.stat().st_mode & 0o600 == 0o600):
-            # Build a warning that tells the user how to fix the permissions.
-            chmod_message = (
-                f". To change owner, run `chown $USER {str(filep)}`."
-                f" To restrict permissions, run `chmod 0600 {str(filep)}`."
-            )
-            warn(f"Bad owner or permissions on {str(filep)}{chmod_message}")
+        # NOTE: The config file must be owned by the current user and have mode 0600.
+        # If the check fails we emit a multi‑line, easy‑to‑read warning.
+        if not (filep.is_file() and filep.stat().st_mode & 0o600 == 0o600):
+            # Quote the path so that spaces (e.g. in “Application Support”) are handled
+            # correctly when the user copies‑pastes the command.
+            quoted_path = f'"{str(filep)}"'
+
+            # Build a clear, multi‑line warning.
+            # The leading newline after the period makes the warning appear on its own line
+            # (the `warn` function already prefixes the message with the file/line info).
+            chmod_message = (
+                f".\n"
+                f" * To change owner, run `chown $USER {quoted_path}`.\n"
+                f" * To restrict permissions, run `chmod 0600 {quoted_path}`."
+            )
+            warn(f"Bad owner or permissions on {str(filep)}{chmod_message}")
*** End of File ***
```

### What changed
1. **Quoted the filename** (`quoted_path = f'"{str(filep)}"'`) to protect spaces.
2. **Re‑structured the warning**:
   * Starts with a period and newline.
   * Uses bullet points (`*`) for each command.
   * Each command is on its own line and ends with a period.
3. Kept the original `warn` call, only the message string is now more user‑friendly.

## How to verify
1. **Unit test** – add a test that creates a temporary config file with wrong permissions, invokes the permission‑check routine, and captures the emitted warning (using `warnings.catch_warnings`).  
   *Assert* that the warning text contains:
   * Two separate lines beginning with `* To change owner` and `* To restrict permissions`.
   * The filename wrapped in double quotes.
2. **Manual test** – run the connector on a macOS machine where the default config path is  
   `~/Library/Application Support/snowflake/config.toml`.  
   *Set the file’s mode to something other than `0600`* and start a connection.  
   The warning printed to the console should look like:

   ```
   UserWarning: Bad owner or permissions on /Users/.../Library/Application Su

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
--- REAL FIX (ground truth)
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
 
 from dvc.command import completion
 from dvc.command.base import CmdBase, append_doc_link
-from dvc.command.metrics import show_metrics
+from dvc.command.metrics import _show_metrics
 from dvc.command.status import CmdDataStatus
 from dvc.dvcfile import PIPELINE_FILE
 from dvc.exceptions import DvcException
@@ -51,7 +51,8 @@ def run(self):
 
                 if self.args.metrics:
                     metrics = self.repo.metrics.show()
-                    show_metrics(metrics)
+                    logger.info(_show_metrics(metrics))
+
             except DvcException:
                 logger.exception("")
                 ret = 1
diff --git a/dvc/repo/metrics/show.py b/dvc/repo/metrics/show.py
--- a/dvc/repo/metrics/show.py
+++ b/dvc/repo/metrics/show.py
@@ -48,7 +48,7 @@ def _extract_metrics(metrics, path, rev):
     ret = {}
     for key, val in metrics.items():
         m = _extract_metrics(val, path, rev)
-        if m:
+        if m not in (None, {}):
             ret[key] = m
         els
--- Proposal A
## Root cause
`dvc metrics show` parses JSON metric files by walking the JSON object and only accepting values of a **whitelisted type**.  
The whitelist mistakenly omitted the built‑in numeric types (`int` and `float`).  
Consequently every leaf value – even valid numbers – was reported as “unsupported type”, and the command always failed with *Could not parse metric files*.

## Fix
Add the missing numeric types to the whitelist and make the parser recurse into dictionaries so that nested numeric values are collected correctly.

```diff
--- a/dvc/repo/metrics.py
+++ b/dvc/repo/metrics.py
@@
-def _parse_metric(value, path=""):
-    """Parse a metric value from a JSON/YAML file.
-
-    Returns a dict mapping metric paths to scalar values.
-    Unsupported types are ignored (debug‑logged by the caller)."""
-    # NOTE: only scalar numeric values are supported.
-    if isinstance(value, (list, tuple)):
-        # Lists are not supported – they would need an index in the metric name.
-        logger.debug("Could not parse metric from %r at %r due to its unsupported type: %s",
-                     value, path, type(value).__name__)
-        return {}
-
-    if isinstance(value, dict):
-        # Dictionaries are not supported – they would need a dot‑separated name.
-        logger.debug("Could not parse metric from %r at %r due to its unsupported type: %s",
-                     value, path, type(value).__name__)
-        return {}
-
-    # At this point we only have scalar values.
-    if isinstance(value, (int, float)):
-        # NOTE: this branch was *incorrectly* marked as unsupported in the
-        # original implementation, causing the bug.
-        logger.debug("Could not parse metric from %r at %r due to its unsupported type: %s",
-                     value, path, type(value).__name__)
-        return {}
-
-    # Anything else (e.g. str, bool, None) is unsupported.
-    logger.debug("Could not parse metric from %r at %r due to its unsupported type: %s",
-                 value, path, type(value).__name__)
-    return {}
+def _parse_metric(value, path=""):
+    """Parse a metric value from a JSON/YAML file.
+
+    Returns a dict mapping metric *paths* (dot‑separated for nested keys) to
+    scalar numeric values.  Unsupported leaf types are ignored – the caller
+    logs a debug message for them.
+    """
+    # Containers -------------------------------------------------------------
+    if isinstance(value, dict):
+        # Recurse into dictionaries, building a dotted path.
+        metrics: dict = {}
+        for k, v in value.items():
+            sub_path = f"{path}.{k}" if path else k
+            metrics.update(_parse_metric(v, sub_path))
+        return metrics
+
+    if isinstance(value, (list, tuple)):
+        # Lists are not supported because they would require an index in the
+        # metric name.  Emit a debug message and drop the whole list.
+        logger.debug(
+            "Could not parse metric from %r at %r due to its unsupported type: %s",
+            value,
+            path,
+            type(value).__name__,
+        )
+        return {}
+
+    # Scalars ---------------------------------------------------------------
+    # Numeric scalar values are the only supported leaf type.
+    if isinstance(value, (int, float)):
+        return {path: value}
+
+    # Everything else (str, bool, None, etc.) is unsupported.
+    logger.debug(
+        "Could not parse metric from %r at %r due to its unsupported t
--- Proposal B
## Root cause
`dvc metrics show` parses metric files by walking the JSON/YAML structure and only accepts a hard‑coded list of *supported* value types.  
The list mistakenly omitted the native numeric types `int` and `float` (and consequently `bool`), treating them as “unsupported”.  
Because of that every leaf value in the example JSON was rejected, producing the debug messages shown in the issue and resulting in an empty metric set.

## Fix
Add the missing numeric types to the set of supported metric values and make the parser use `numbers.Number` (which covers `int`, `float` and `bool`).  
While touching the code we also tighten the recursion logic so that nested dictionaries are correctly flattened with a dot‑separated key path.

```diff
diff --git a/dvc/metrics/show.py b/dvc/metrics/show.py
index 7c9a1b2..e3f4d8a 100644
--- a/dvc/metrics/show.py
+++ b/dvc/metrics/show.py
@@
-import json
-import yaml
-from typing import Any, Dict, Iterable, List, Tuple
+import json
+import yaml
+import numbers
+from typing import Any, Dict, Iterable, List, Tuple
@@
-def _extract_metrics(
-    data: Any, prefix: str = ""
-) -> Iterable[Tuple[str, Any]]:
-    """Yield (key, value) pairs from a JSON/YAML metric file.
-
-    Only scalar numeric values are considered valid metrics.  Nested
-    dictionaries are traversed recursively and their keys are joined
-    with a dot ('.') separator.
-    """
-    if isinstance(data, dict):
-        for k, v in data.items():
-            new_prefix = f"{prefix}.{k}" if prefix else k
-            yield from _extract_metrics(v, new_prefix)
-    elif isinstance(data, (list, tuple)):
-        # Lists are not supported as metrics – they are ignored.
-        return
-    else:
-        # Only int and float are accepted as metric values.
-        if isinstance(data, (int, float)):
-            yield (prefix, data)
-        else:
-            # Unsupported type – let the caller log a debug message.
-            return
+def _extract_metrics(
+    data: Any, prefix: str = ""
+) -> Iterable[Tuple[str, Any]]:
+    """Yield (key, value) pairs from a JSON/YAML metric file.
+
+    * Only scalar *numeric* values are considered valid metrics.
+    * Nested dictionaries are traversed recursively and their keys are
+      joined with a dot ('.') separator (e.g. ``test_1.precision``).
+    * Lists/tuples are ignored – DVC does not treat them as scalar metrics.
+    """
+    # Recurse into dictionaries, building a dotted key path.
+    if isinstance(data, dict):
+        for k, v in data.items():
+            new_prefix = f"{prefix}.{k}" if prefix else k
+            yield from _extract_metrics(v, new_prefix)
+        return
+
+    # Lists/tuples are not supported as scalar metrics.
+    if isinstance(data, (list, tuple)):
+        return
+
+    # ``numbers.Number`` covers ``int``, ``float`` and ``bool`` (bool is a
+    # subclass of int, but we keep the explicit check for clarity).
+    if isinstance(data, numbers.Number):
+        yield (prefix, data)
+        return
+
+    # Anything else (e.g. str, dict that fell through, etc.) is ignored.
+    return
@@
-def _load_metric_file(path: str) -> Any:
-    \"\"\"Load a JSON/YAML metric file.\n-
-
-    Returns the parsed data structure (dict, list, etc.).\n-
-    \"\"\"\n-    with open(path, \"r\", encoding=\"utf-8\") as f:\n-        if path.lower().endswith(('.json', '.jsonl')):\n-            return json.load(f)\n-        return yaml.safe_load(f)\n+def _load_metric_file(path: str) -> Any

=== TASK python-trio__trio-1129 | repo python-trio/trio | python
--- Issue
Should we somehow expose Pipe*Stream on Unix, and if so, how?
We have `PipeReceiveStream` and `PipeSendStream` classes on Unix, that are currently not exposed publicly; they're only used internally for subprocesses.

The actual code is a pretty generic building block: they can wrap around any Unix file-descriptor that supports non-blocking mode. So they could potentially be useful for talking to [FIFOs](http://man7.org/linux/man-pages/man7/fifo.7.html), TTYs, stdio streams (#174), etc.

Should we expose them more directly? If so, how? In `trio.hazmat`, with some sort of [`os.fdopen`](https://docs.python.org/3/library/os.html#os.fdopen) equivalent?

If we do we'll want to think through the ownership semantics: should they take ownership of a passed in fd, or should they dup it?

Also, they should restore the fd's blocking/non-blocking state before closing it.
--- REAL FIX (ground truth)
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
diff --git a/trio/_subprocess.py b/trio/_subprocess.py
index ae530e3a..f9bc0647 100644
--- a/trio/_subprocess.py
+++ b/trio/_subprocess.py
@@ -1,9 +1,8 @@
 import os
-import select
 import subprocess
-from functools import partial
+from typing import Optional
 
-from ._abc import AsyncResource
+from ._abc import AsyncResource, SendStream, ReceiveStream
 from ._highlevel_generic import StapledStream
 from ._sync import Lock
 from ._subprocess_platform import (
@@ -101,9 +100,10 @@ class Process(AsyncResource):
                     .format(key)
                 )
 
-        self.stdin = None
-        self.stdout = None
-        self.stderr = None
+        self.stdin = None  # type: Optional[SendStream]
+        self.stdout = None  # type: Optional[ReceiveStream]
+        self.stderr = None  # type: Optional[ReceiveStream]
+        self.stdio = None  # type: Optional[StapledStream]
 
         if os.name == "posix":
             if isinstance(command, str) and not options.get("shell"):
@@ -153,8 +153,6 @@ class Process(AsyncR
--- Proposal A
## Root cause
`PipeReceiveStream` and `PipeSendStream` are internal building blocks used by Trio’s subprocess support.  
They are defined in the Unix‑specific subprocess module, but they are **not exported** from the public API, so users cannot reuse them for other file‑descriptors (e.g. FIFOs, TTYs, stdio).  
Because they are internal, they also do not expose any way to control ownership of the underlying file‑descriptor – they always close the fd when the stream is closed.  

The issue asks to make these stream classes publicly usable and to give callers control over whether the stream takes ownership of the supplied fd (or dup’s it) while still restoring the original blocking mode on close.

## Fix
* Add a thin public wrapper in `trio/hazmat` that creates the appropriate stream from an existing fd.
* Extend the internal `PipeReceiveStream` / `PipeSendStream` classes to accept an `own_fd` flag (default `True`).  
  * When `own_fd` is `False` the stream dup’s the fd on construction and closes the duplicate on exit, leaving the original fd untouched.
* Export the classes and the helper functions from `trio/hazmat/__init__.py`.
* Update the internal implementations so that they restore the fd’s original blocking mode before closing (the code already does this, but we make the behaviour explicit for the new ownership path).

### Unified diff
```diff
diff --git a/trio/_subprocess.py b/trio/_subprocess.py
index 7c9a1b2..e3f4d8a 100644
--- a/trio/_subprocess.py
+++ b/trio/_subprocess.py
@@
 class PipeReceiveStream(_BasePipeStream):
@@
-    def __init__(self, fd):
-        super().__init__(fd)
+    def __init__(self, fd, *, own_fd: bool = True):
+        """
+        Parameters
+        ----------
+        fd:
+            The file descriptor to read from.
+        own_fd:
+            If ``True`` (the default) the stream takes ownership of *fd* and
+            closes it when the stream is closed.  If ``False`` the stream
+            dup’s *fd* and closes the duplicate, leaving the original descriptor
+            untouched.
+        """
+        # Store the original descriptor for later restoration of its blocking mode
+        self._original_fd = fd
+        # Decide which descriptor the stream will actually use
+        if own_fd:
+            self._fd = fd
+            self._own_fd = True
+        else:
+            # Duplicate the descriptor so we can close it safely later
+            self._fd = os.dup(fd)
+            self._own_fd = True   # we own the duplicated fd
+        super().__init__(self._fd)
@@
-    async def aclose(self):
-        await super().aclose()
+    async def aclose(self):
+        # Restore the original blocking mode before we close the descriptor we own
+        if self._fd is not None:
+            _restore_blocking(self._fd, self._original_blocking)
+        await super().aclose()
+        # If we duplicated the fd we must close the duplicate now
+        if not self._own_fd and self._fd is not None:
+            os.close(self._fd)
+        self._fd = None
@@
 class PipeSendStream(_BasePipeStream):
@@
-    def __init__(self, fd):
-        super().__init__(fd)
+    def __init__(self, fd, *, own_fd: bool = True):
+        """
+        Parameters
+        ----------
+        fd:
+            The file descriptor to write to.
+        own_fd:
+            If ``True`` (the default) the stream takes ownership of *fd* and
+            closes it when the stream is closed.  If ``False`` the stream
+            dup’s *fd
--- Proposal B
## Root cause
`PipeReceiveStream` and `PipeSendStream` are defined in the Trio code‑base (in `trio/_subprocess.py`) but they are not exported from any public module.  Consequently users cannot import them or use them as a generic “wrap an arbitrary Unix file descriptor” primitive, even though the implementation already supports non‑blocking I/O, restores the original blocking mode on close, and works with any FD that behaves like a pipe (FIFOs, TTYs, etc.).  The issue is therefore simply that the classes (and a convenient factory) are hidden from the public API.

## Fix
* Export the two classes from a public namespace (`trio.lowlevel` – the place for low‑level building blocks).
* Provide a small factory helper `open_pipe(fd, *, mode, closefd=True)` that:
  * validates `mode` (`"r"`/`"rb"` → `PipeReceiveStream`, `"w"`/`"wb"` → `PipeSendStream`);
  * optionally duplicates the FD when `closefd=False` (so the original FD is not closed when the stream is closed);
  * returns the appropriate stream instance.
* Add the new symbols to `__all__` so they appear in the generated documentation and are importable via `from trio.lowlevel import …`.

Below is a **unified diff** that implements the above changes.  The diff assumes the existing implementation of `PipeReceiveStream` and `PipeSendStream` lives in `trio/_subprocess.py`.

```diff
--- a/trio/_subprocess.py
+++ b/trio/_subprocess.py
@@
 from ._core._run import run_sync_soon
 from ._core._tasks import current_task
 from ._core._traps import wait_readable, wait_writable
 from ._core._util import aclose_forcefully, aclose_gracefully
 from ._core._windows import is_windows
@@
 __all__ = [
     "Subprocess",
     "SubprocessError",
     "SubprocessProtocol",
-    # Pipe*Stream are deliberately *not* exported – they are internal.
+    # Pipe*Stream are now part of the public low‑level API.
+    "PipeReceiveStream",
+    "PipeSendStream",
+    "open_pipe",
 ]
 
@@
 class PipeReceiveStream(AsyncResource, ReceiveStream):
@@
 class PipeSendStream(AsyncResource, SendStream):
@@
     async def aclose(self) -> None:
         await aclose_gracefully(self._fd, self._original_blocking)
 
+# ----------------------------------------------------------------------
+# Public helper – expose a generic “wrap an fd as a Trio stream”.
+# ----------------------------------------------------------------------
+
+def _dup_fd(fd: int) -> int:
+    """Duplicate *fd* using ``os.dup`` and set the duplicate to non‑blocking.
+
+    The caller is responsible for closing the returned descriptor.
+    """
+    import os
+
+    dup = os.dup(fd)
+    # Ensure the duplicate is in non‑blocking mode – the stream classes
+    # expect this and will restore the original mode on close.
+    flags = os.fcntl(dup, os.F_GETFL)
+    os.fcntl(dup, os.F_SETFL, flags | os.O_NONBLOCK)
+    return dup
+
+
+def open_pipe(fd: int, *, mode: str, closefd: bool = True) -> AsyncResource:
+    """Wrap an existing Unix file descriptor in a Trio stream.
+
+    Parameters
+    ----------
+    fd:
+        The file descriptor to wrap.  It must refer to an object that
+        supports non‑blocking I/O (e.g. a pipe, FIFO, TTY, socket, etc.).
+    mode:
+        ``"r"`` or ``"rb"`` for a receive‑only stream,
+        ``"w"`` or ``"wb"`` for a send‑only stream.
+    closefd:
+        If ``True`` (the default) the returned stream takes ownership of
+        *fd* and will close it when the stream is closed.  If ``False`` the
+        descriptor is duplicated (u

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
`aioftp` parses the date part of an FTP `LIST` line in `BaseClient.parse_ls_date`.  
When the listing contains a *recent* file (format `"Feb 29 12:34"`), the parser builds a `datetime` object for the current year and then calls `replace(year=now.year)`.  
If the current year is **not** a leap year (e.g., 2021), `datetime.replace` raises `ValueError` because `Feb 29` does not exist in that year. The exception is not caught, so the whole directory‑listing parsing fails and the client cannot list files created after 29 Feb 2020.

## Fix
Handle the special case where the month/day is `Feb 29` and the target year is not a leap year.  
If `replace(year=now.year)` fails, fall back to the previous year (which must be a leap year) before returning the datetime.

```diff
diff --git a/aioftp/client.py b/aioftp/client.py
index a1072b6..c3f9e1d 100644
--- a/aioftp/client.py
+++ b/aioftp/client.py
@@
 class BaseClient:
@@
-    def parse_ls_date(cls, s, *, now=None):
-        """
-        Parsing dates from the ls unix utility. For example,
-        "Nov 18  1958", "Jan 03 2018", and "Nov 18 12:29".
-
-        :param s: ls date
-        :type s: :py:class:`str`
-        :param now: reference time (defaults to ``datetime.now()``)
-        :type now: :py:class:`datetime.datetime`
-        :return: parsed datetime
-        :rtype: :py:class:`datetime.datetime`
-        """
-        if now is None:
-            now = datetime.datetime.now()
-
-        # recent file – time is given, year is assumed to be current year
-        if ':' in s:
-            dt = datetime.datetime.strptime(s, "%b %d %H:%M")
-            dt = dt.replace(year=now.year)
-        else:
-            dt = datetime.datetime.strptime(s, "%b %d %Y")
-
-        return dt
+    def parse_ls_date(cls, s, *, now=None):
+        """
+        Parse the date part of an FTP ``LIST`` line.
+
+        The FTP server can emit two different formats:
+
+        * ``"Feb 28 2020"`` – older than six months, year is present.
+        * ``"Feb 28 12:34"`` – newer than six months, time is present and the
+          year is *implicitly* the current year.
+
+        The original implementation simply replaced the year with ``now.year``.
+        This crashes for the special case ``Feb 29`` when the current year is
+        **not** a leap year (e.g. 2021).  The fix attempts to replace the year;
+        if a ``ValueError`` is raised (invalid date), we fall back to the
+        previous year, which must be a leap year for ``Feb 29``.
+
+        :param s: ls date string (already stripped of surrounding whitespace)
+        :type s: :class:`str`
+        :param now: reference time (defaults to ``datetime.datetime.now()``)
+        :type now: :class:`datetime.datetime`
+        :return: parsed ``datetime`` (naïve, in the server's timezone)
+        :rtype: :class:`datetime.datetime`
+        """
+        if now is None:
+            now = datetime.datetime.now()
+
+        # Recent file – time is given, year is assumed to be the current year.
+        if ':' in s:
+            dt = datetime.datetime.strptime(s, "%b %d %H:%M")
+            try:
+                dt = dt.replace(year=now.year)
+            except ValueError:
+                # Most likely ``Feb 29`` on a non‑leap year.  Use the previous
+                # year which must be a leap year.
+                dt = dt.replace(year=now.year - 1)
+        else:
+            # Old file – year is explicitly present.
+            dt = datetime.date

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
--- REAL FIX (ground truth)
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
@@ -51,6 +51,14 @@ class _FuncFormatter(_ReplaceFormatter):
         return format_spec, args.replace(")", "").split(",")
 
 
+def format_value(value):
+    if value is None:
+        return ""
+    elif isinstance(value, bool):
+        return str(value).lower()
+    return value
+
+
 default_formatter = _FuncFormatter(lambda name: "")
 default_formatter._register("len", len)
 
diff --git a/cashews/helpers.py b/cashews/helpers.py
index 0c15206..c61fd99 100644
--- a/cashews/helpers.py
+++ b/cashews/helpers.py
@@ -19,6 +19,23 @@ def add_prefix(prefix: str):
     return _middleware
 
 
+def all_keys_lower():
+    async def _middleware(call, *args, backend=None, cmd=None, **kwargs):
+        if cmd.lower() == "get_many":
+            return await call(*[key.lower() for key in args])
+        call_values = get_call_values(call, args, kwargs)
+        as_key = "key"
+        if cmd == "delete_match":
+            as_key = "pattern"
+        key = call_values.get(as_key)
+        if key:
+            call_values[as_key] = key.lower()
+            r
--- Proposal A
## Root cause
`cashews` builds cache keys by concatenating the function name with its arguments.  
During this process every string argument is **unconditionally lower‑cased**:

```python
if isinstance(value, str):
    value = value.lower()
```

The behaviour is hard‑coded, there is no configuration flag to turn it off, so users cannot keep the original case of their keys. This is why the example in the issue sees the same cache entry for `"Bob"` and `"bob"`.

## Fix
* Introduce a **global configuration option** `case_sensitive` (default `True`).
* Store the option when `cache.setup()` is called.
* Update the key‑generation routine to lower‑case string arguments **only when** `case_sensitive` is `False`.
* Keep the public API unchanged – existing code continues to work, but now the default preserves case.  
  Users that relied on the old lower‑casing can explicitly opt‑out by calling  
  `cache.setup(..., case_sensitive=False)`.

### Unified diff

```diff
diff --git a/cashews/config.py b/cashews/config.py
index 7c9a1f2..e3b4d8a 100644
--- a/cashews/config.py
+++ b/cashews/config.py
@@
 class CacheConfig:
@@
     backend: str = "mem://"
     middlewares: Tuple[Callable, ...] = ()
     ttl: Optional[timedelta] = None
+    # ------------------------------------------------------------------
+    # New option: keep the original case of string arguments when building
+    # cache keys.  The library historically forced lower‑case which broke
+    # case‑sensitive use‑cases.  By default we preserve case (True) to match
+    # typical expectations.  Setting it to False restores the historic
+    # behaviour.
+    # ------------------------------------------------------------------
+    case_sensitive: bool = True
@@
     def __init__(self, **kwargs):
         for name, value in kwargs.items():
             if not hasattr(self, name):
                 raise AttributeError(f"Unknown config option: {name}")
             setattr(self, name, value)
@@
     def update(self, **kwargs):
         for name, value in kwargs.items():
             if not hasattr(self, name):
                 raise AttributeError(f"Unknown config option: {name}")
             setattr(self, name, value)
+
+    # Helper used by the key builder
+    def should_lowercase(self) -> bool:
+        """Return ``True`` when string arguments must be lower‑cased."""
+        return not self.case_sensitive
*** End Patch
diff --git a/cashews/__init__.py b/cashews/__init__.py
index 5d2c9a1..a1f3e4b 100644
--- a/cashews/__init__.py
+++ b/cashews/__init__.py
@@
-def setup(backend: str = "mem://", *, middlewares: Tuple[Callable, ...] = (), ttl: Optional[timedelta] = None):
+def setup(
+    backend: str = "mem://",
+    *,
+    middlewares: Tuple[Callable, ...] = (),
+    ttl: Optional[timedelta] = None,
+    case_sensitive: bool = True,
+):
     """
     Initialise the global cache configuration.
@@
-    config.update(backend=backend, middlewares=middlewares, ttl=ttl)
+    config.update(
+        backend=backend,
+        middlewares=middlewares,
+        ttl=ttl,
+        case_sensitive=case_sensitive,
+    )
*** End Patch
diff --git a/cashews/wrapper/key.py b/cashews/wrapper/key.py
index 3f9b2c4..d1e7a9f 100644
--- a/cashews/wrapper/key.py
+++ b/cashews/wrapper/key.py
@@
-from cashews.config import config
+from cashews.config import config
@@
-def _make_key(func_name: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> str:
-    parts = [func_name]
-    for i, arg in enumerate(args):
-  
--- Proposal B
## Root cause
`cashews` builds cache keys by concatenating the function name with its arguments.  
During this process **all string arguments are silently lower‑cased**:

```python
if isinstance(value, str):
    value = value.lower()          # <-- forces case‑insensitivity
```

This behaviour is hard‑coded and there is no public switch to keep the original
casing. Consequently calls such as `get_name("Bob")` and `get_name("bob")`
produce the same cache key, which is why the second call hits the cached
result of the first one.

## Fix
Add an explicit *case‑sensitivity* flag that can be set on the decorator (and
propagated to the internal key‑building routine). When the flag is `True` the
original string value is kept; when `False` (the historic default) the value is
lower‑cased.

### Files changed
* `cashews/decorator.py` – extend the `cache` decorator signature and store the
  flag on the wrapper.
* `cashews/cache.py` – modify the internal `_make_key` helper to honour the
  `case_sensitive` flag.

```diff
--- a/cashews/decorator.py
+++ b/cashews/decorator.py
@@
-def cache(ttl: Optional[timedelta] = None, **options):
+def cache(
+    ttl: Optional[timedelta] = None,
+    *,
+    case_sensitive: bool = False,
+    **options,
+):
@@
-    def wrapper(func):
+    def wrapper(func):
         @functools.wraps(func)
         async def inner(*args, **kwargs):
@@
-            key = cache._make_key(func, args, kwargs)
+            # Pass the case‑sensitivity flag to the key builder
+            key = cache._make_key(func, args, kwargs, case_sensitive=case_sensitive)
@@
-        return inner
+        # expose the flag for possible introspection (e.g. in middlewares)
+        inner.__cashews_case_sensitive__ = case_sensitive
+        return inner
*** End of File
--- a/cashews/cache.py
+++ b/cashews/cache.py
@@
-def _make_key(func: Callable, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> str:
+def _make_key(
+    func: Callable,
+    args: Tuple[Any, ...],
+    kwargs: Dict[str, Any],
+    *,
+    case_sensitive: bool = False,
+) -> str:
@@
-    parts = [func.__module__, func.__qualname__]
+    parts = [func.__module__, func.__qualname__]
@@
-    for arg in args:
-        if isinstance(arg, str):
-            parts.append(arg.lower())
-        else:
-            parts.append(str(arg))
+    for arg in args:
+        if isinstance(arg, str):
+            parts.append(arg if case_sensitive else arg.lower())
+        else:
+            parts.append(str(arg))
@@
-    for key, value in sorted(kwargs.items()):
-        if isinstance(value, str):
-            parts.append(f"{key}:{value.lower()}")
-        else:
-            parts.append(f"{key}:{value}")
+    for key, value in sorted(kwargs.items()):
+        if isinstance(value, str):
+            parts.append(f"{key}:{value if case_sensitive else value.lower()}")
+        else:
+            parts.append(f"{key}:{value}")
*** End of File
```

### Explanation of changes
1. **Decorator signature** – added a keyword‑only argument `case_sensitive`
   (default `False` to keep the historic behaviour). The flag is stored on the
   wrapper and forwarded to the key builder.
2. **Key builder** – accepts the same flag and only lower‑cases string values
   when `case_sensitive` is `False`. This affects both positional and keyword
   arguments.
3. **Propagation** – the flag is passed from the decorator to `_make_key`,
   ensuring middle‑wares that rely on the generated key see the correct value.

## How to verify
C

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
--- REAL FIX (ground truth)
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

--- Proposal A
## Root cause
`countthai` calculates the percentage of Thai characters as  

```python
(num_thai / num_count) * 100
```  

where `num_count = len(text) - num_ignore`.  
When the input contains **only characters that are ignored** (e.g. a space) or **only non‑Thai characters that are also listed in the default ignore set** (e.g. digits when they are part of `_DEFAULT_IGNORE_CHARS`), `num_count` becomes `0`.  
Dividing by zero raises a `ZeroDivisionError`, which is what the user sees for inputs such as `'123'`, `' '` or `'1 2 3 4'`.  
The function also returns an `int` (`0`) for empty or non‑string inputs, while the documented return type is `float`.

## Fix
```diff
diff --git a/pythainlp/util/thai.py b/pythainlp/util/thai.py
index 1a78dc32..b141b260 100644
--- a/pythainlp/util/thai.py
+++ b/pythainlp/util/thai.py
@@
-def countthai(text: str, ignore_chars: str = _DEFAULT_IGNORE_CHARS) -> float:
-    \"\"\"Return the percentage of Thai characters in *text*.\n-\n-    The function counts Thai characters (Unicode range U+0E00‑U+0E7F) and\n-    divides that by the number of characters that are **not** in ``ignore_chars``.\n-    ``ignore_chars`` defaults to a set of punctuation, whitespace and other\n-    characters that are usually not considered when measuring Thai content.\n-\n-    Parameters\n-    ----------\n-    text : str\n-        Input string.\n-    ignore_chars : str, optional\n-        Characters to be ignored when calculating the percentage.\n-\n-    Returns\n-    -------\n-    float\n-        Percentage of Thai characters in the input string.\n-\n-    Examples\n-    --------\n-    >>> countthai('สวัสดี')\n-    100.0\n-    >>> countthai('Hello สวัสดี')\n-    50.0\n-    \"\"\"\n-    if not text or not isinstance(text, str):\n-        return 0\n-\n-    if not ignore_chars:\n-        ignore_chars = \"\"\n-\n-    # Count Thai characters\n-    num_thai = len(re.findall(r\"[\\u0E00-\\u0E7F]\", text))\n-\n-    # Count characters that should be ignored\n-    num_ignore = sum(1 for ch in text if ch in ignore_chars)\n-\n-    # Effective length after ignoring characters\n-    num_count = len(text) - num_ignore\n-\n-    return (num_thai / num_count) * 100\n+def countthai(text: str, ignore_chars: str = _DEFAULT_IGNORE_CHARS) -> float:\n+    \"\"\"Return the percentage of Thai characters in *text*.\n+\n+    The function counts Thai characters (Unicode range U+0E00‑U+0E7F) and\n+    divides that by the number of characters that are **not** in ``ignore_chars``.\n+    ``ignore_chars`` defaults to a set of punctuation, whitespace and other\n+    characters that are usually not considered when measuring Thai content.\n+\n+    The original implementation raised ``ZeroDivisionError`` when the input\n+    consisted solely of ignored characters (e.g. a space) or of characters\n+    that are not Thai but are also part of the ignore set (e.g. digits).  It\n+    also returned an ``int`` for empty or non‑string inputs even though the\n+    function is documented to return a ``float``.\n+\n+    This patched version:\n+\n+    * Returns ``0.0`` for empty strings, non‑string inputs, or when the\n+      effective character count after ignoring is ``0``.\n+    * Guarantees a ``float`` return type in all cases.\n+\n+    Parameters\n+    ----------\n+    text : str\n+        Input string.\n+    ignore_chars : str, optional\n+        Characters to be ignored when calculating the percentage.\n+\n+    Returns\n+    -------\n+    float\n+        Percentage of Thai charac
--- Proposal B
## Root cause
`countthai` was implemented in a way that assumed the input string only contained Thai characters or was non‑empty.  
When the function received digits, spaces or an empty string it tried to process the text (e.g. by indexing or splitting) and raised an exception instead of simply returning `0`.  

## Fix
```diff
--- a/pythainlp/util.py
+++ b/pythainlp/util.py
@@
-import re
-
-def countthai(text):
-    \"\"\"Count Thai characters in *text*.\n-
-
-    The original implementation assumed *text* always contained Thai characters\n-    and would raise an exception for digits, spaces or an empty string.\n-    \"\"\"\n-    # original buggy code (illustrative) -------------------------------------------------\n-    # thai_chars = [c for c in text if u'\u0E00' <= c <= u'\u0E7F']\n-    # return len(thai_chars)\n-    # -----------------------------------------------------------------------------------\n-    # The above raised errors for non‑string inputs or strings without Thai characters.\n-    # -------------------------------------------------------------------------------\n-    # Fixed implementation follows.\n-    pass\n+import re
+
+def countthai(text):
+    """
+    Count the number of Thai characters in *text*.
+
+    The function is now defensive:
+    * If *text* is not a ``str`` (e.g. ``None`` or an integer) it is safely converted\n+      to ``str``; if conversion fails the function returns ``0``.\n+    * It works correctly for strings that contain only digits, spaces, punctuation\n+      or are completely empty – it simply returns ``0`` instead of raising an error.\n+\n+    Parameters
+    ----------
+    text : Any
+        The input to be examined.\n+\n+    Returns
+    -------
+    int
+        The count of characters whose Unicode code point lies in the Thai block\n+        (U+0E00‑U+0E7F).\n+    \"\"\"\n+\n+    # Ensure we are working with a string; fall back to ``0`` on failure.\n+    if not isinstance(text, str):\n+        try:\n+            text = str(text)\n+        except Exception:  # pragma: no cover – extremely unlikely path\n+            return 0\n+\n+    # Use a compiled regular expression for efficiency and clarity.\n+    # The pattern matches any character in the Thai Unicode block.\n+    thai_pattern = re.compile(r\"[\\u0E00-\\u0E7F]\")\n+    return len(thai_pattern.findall(text))
```

## How to verify
1. **Run the library’s test suite** (if available) after the change to ensure no existing tests break.  
2. **Manual checks** in a Python REPL:

```python
from pythainlp.util import countthai

assert countthai('123') == 0          # digits only
assert countthai(' ') == 0            # single space
assert countthai('1 2 3 4') == 0      # digits with spaces
assert countthai('สวัสดี') == 6       # pure Thai
assert countthai('สวัสดี 123') == 6   # mixed Thai and non‑Thai
assert countthai('') == 0             # empty string
assert countthai(None) == 0           # non‑string input
```

All assertions should pass without raising an exception.  
If the project includes CI, push the change and confirm that the CI pipeline succeeds.

=== TASK Axelrod-Python__Axelrod-653 | repo Axelrod-Python/Axelrod | python
--- Issue
Add tests for reproducibility of stochastic results
I'd like to add a property based test similar to https://github.com/Nikoleta-v3/Axelrod/blob/635/axelrod/tests/integration/test_matches.py but that checks that when setting a seed we get the same results for stochastic strategies (those tests there only check for 'well behaved deterministic' strategies. I have a hunch that this isn't actually true as we're not setting numpy's seed...

If that's the case and both seeds need to be set, we should document how to get reproducible results and either:

1. Indicate that you should set both seeds (numpy and stdlib);
2. Write a little helper function that does this (and just document it: `axelrod.seed()` or something like that...
--- REAL FIX (ground truth)
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
         self._recorded_history = []
+
+
+class SlowTitForTwoTats(Player):
+    """
+    A player plays C twice, then if the opponent plays the same move twice, 
+    plays that move 
+    """
+
+    name = 'Slow Tit For Two Tats'
+    classifier = {
+        'memory_depth': 2, 
+        'stochastic': False,
+        'makes_use_of': set(),
+        'inspects_source': False,
+        'manipulates_source': False,
+        'manipulates_state': False
+    }
+
+    def strategy(self, opponent):
+        
+        #Start with two cooperations
+        if len(self.history) < 2:
+            return C
+        
+        #Mimic if opponent plays the same move twice
+        if opponent.history[-2] == opponent.history[-1]:
+            return opponent.history[-1]
+        
+        #Otherwise cooperate
+        return C
+
+        
+
diff --git a/docs/tutorials/advanced/index.rst b/docs/tutorials/advanced/index.rst
index dcefec08..ea868106 100644
--- a/docs/tutorials/advanced/index.rst
+++ b/docs/tutorials/advanced
--- Proposal A
## Root cause
Stochastic strategies in Axelrod may draw random numbers from both Python’s built‑in `random` module **and** NumPy’s random generator.  
The library only exposed a way to set the standard library seed (e.g. via `random.seed()`), so when a user sets that seed they still get nondeterministic results for strategies that rely on `numpy.random`. Consequently, reproducibility of stochastic matches is not guaranteed.

## Fix
Add a public helper `axelrod.seed()` that sets **both** the standard library and NumPy random seeds.  
Expose the function in the top‑level package so that users (and tests) can simply call `axelrod.seed(42)` before running matches.

```diff
diff --git a/axelrod/__init__.py b/axelrod/__init__.py
index 7c9f1a2b..e3b5c8d4 100644
--- a/axelrod/__init__.py
+++ b/axelrod/__init__.py
@@
-from .player import Player
-from .match import Match
-from .tournament import Tournament
-from .result_set import ResultSet
-from .game import Game
+from .player import Player
+from .match import Match
+from .tournament import Tournament
+from .result_set import ResultSet
+from .game import Game
@@
-__all__ = [
-    "Player",
-    "Match",
-    "Tournament",
-    "ResultSet",
-    "Game",
-    # strategies are added to __all__ dynamically in axelrod/strategies/__init__.py
-]
+__all__ = [
+    "Player",
+    "Match",
+    "Tournament",
+    "ResultSet",
+    "Game",
+    "seed",
+    # strategies are added to __all__ dynamically in axelrod/strategies/__init__.py
+]
+
+# ----------------------------------------------------------------------
+# Reproducibility helper
+# ----------------------------------------------------------------------
+def seed(value: int | None = None) -> None:
+    """
+    Set the random seed for both the Python standard library ``random`` module
+    and NumPy's random generator.
+
+    Parameters
+    ----------
+    value : int or None, optional
+        The seed value to use.  If ``None`` (the default) the seed is drawn
+        from ``random.SystemRandom`` which mimics the behaviour of calling
+        ``random.seed()`` with no argument.
+
+    Notes
+    -----
+    Many stochastic strategies in Axelrod use ``random`` while others use
+    ``numpy.random``.  Setting only one of the two generators does **not**
+    guarantee reproducible results.  This helper ensures that both sources of
+    randomness are synchronised.
+    """
+    import random
+
+    # Import NumPy lazily – NumPy is an optional dependency for some
+    # strategies, but the import is cheap and safe here.
+    try:
+        import numpy as np
+    except Exception:  # pragma: no cover – NumPy is a hard dependency in CI
+        np = None
+
+    random.seed(value)
+    if np is not None:
+        np.random.seed(value)
+
+    # Return ``None`` explicitly for clarity.
+    return None
```

### What the patch does
1. **Imports** `random` (and lazily imports `numpy` if available) inside the new `seed` function.
2. **Sets** the seed for both generators to the same value (or to a system‑generated seed when `value` is `None`).
3. **Adds** the function name to `__all__` so it is part of the public API.
4. Provides a detailed docstring explaining why both seeds are required.

## How to verify
1. **Create a small script** (or run in a REPL) that plays a match involving a stochastic strategy that uses NumPy (e.g. `axelrod.Random` or any strategy that calls `np.random` internally).

```python
import axelrod as axl

# Helper to run a match and retur
--- Proposal B
## Root cause
Stochastic strategies in Axelrod use NumPy’s random number generator (e.g., `numpy.random.choice`).  
The library only sets the seed for Python’s built‑in `random` module, so when a user calls `random.seed()` the NumPy RNG remains uncontrolled. Consequently, runs that are supposed to be reproducible can yield different results, and the existing integration tests only verify deterministic strategies.

## Fix
Add a small helper that sets **both** seeds (Python’s `random` and NumPy’s RNG) and expose it as `axelrod.seed`.  
Update the package’s public API and documentation accordingly.

```diff
--- a/axelrod/__init__.py
+++ b/axelrod/__init__.py
@@
-"""Axelrod library entry point."""
+"""Axelrod library entry point.
+
+This module provides a convenience function :func:`seed` that sets the random
+seed for both the Python standard library ``random`` module **and** NumPy’s
+random generator.  Stochastic strategies (e.g. ``Random``, ``ProbabilisticTitForTat``)
+rely on NumPy’s RNG, so setting only ``random.seed`` is insufficient for
+reproducible simulations.
+"""
@@
-from . import strategies
+from . import strategies
@@
-__all__ = [
-    "Tournament",
-    "Match",
-    "Player",
-    "ResultSet",
-    "Result",
-    "Game",
-    "AxelrodError",
-    "AxelrodWarning",
-    "strategies",
-]
+__all__ = [
+    "Tournament",
+    "Match",
+    "Player",
+    "ResultSet",
+    "Result",
+    "Game",
+    "AxelrodError",
+    "AxelrodWarning",
+    "strategies",
+    "seed",               # exported helper for reproducibility
+]
 
@@
 
 # The version is set by bumpversion
 __version__ = "4.14.0"
 
+def seed(value: int) -> None:
+    """Set the random seed for reproducible simulations.
+
+    This function sets the seed for both the Python ``random`` module and NumPy's
+    random number generator.  It should be called **once** before any matches,
+    tournaments, or strategy instances that rely on stochastic behaviour are
+    created.
+
+    Parameters
+    ----------
+    value : int
+        The seed value to use.
+
+    Examples
+    --------
+    >>> import axelrod as axl
+    >>> axl.seed(42)          # sets both seeds
+    >>> match = axl.Match(axl.Random(), axl.Cooperator())
+    >>> match.play()
+    """
+    import random
+    import numpy as np
+
+    random.seed(value)
+    np.random.seed(value)
+
*** End of File ***
```

### Additional notes
* No changes are required in the existing strategy implementations because they already rely on the global NumPy RNG.
* The helper is deliberately lightweight and has no side‑effects beyond setting the two seeds.

## How to verify
1. **Add a new test** (e.g. `tests/integration/test_reproducibility.py`) that:
   ```python
   import axelrod as axl

   def run_match(seed):
       axl.seed(seed)
       m = axl.Match(axl.Random(), axl.Cooperator(), turns=10)
       m.play()
       return m.result

   def test_numpy_and_stdlib_seed():
       r1 = run_match(12345)
       r2 = run_match(12345)
       assert r1 == r2
   ```
   The test should pass, confirming that identical seeds produce identical match results.

2. **Run the full test suite**:
   ```bash
   pytest -q
   ```
   All existing tests must still pass, and the new reproducibility test should succeed.

3. **Manual sanity check** (optional):
   ```python
   >>> import axelrod as axl
   >>> axl.seed(0)
   >>> m1 = axl.Match(axl.Random(), axl.Cooperator(), turns=5).play()
   >>> axl.seed(0)
   >>> m2 = axl.Match(axl.Random(), axl.Coope

=== TASK iterative__dvc-9391 | repo iterative/dvc | python
--- Issue
Being able to call  `exp show` with multiple branches (`--rev`) and a number of commits (`-n`)
In the VS Code extension, we would like to show multiple branches at the same time in the table. Each with its own number of commits. We're currently doing this by calling `exp show` once per branch (`dvc exp show --rev main -n 5`, `dvc exp show --rev other-branch -n 3`...). We'd like to be able to do this in one call only (something like `dvc exp show --rev main -n 5 --rev other-branch -n 3...`). Would it be possible?

--- REAL FIX (ground truth)
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

--- Proposal A
## Root cause
`dvc exp show` only accepted a single `--rev` argument and a single global `-n` (number of commits). The CLI parser stored `rev` as a scalar value, so users had to invoke the command repeatedly for each branch they wanted to display. There was no way to associate a different `-n` with each revision, which is required by the VS Code extension.

## Fix
* Extend the CLI parser to allow **multiple** `--rev` arguments and **multiple** `-n` arguments (both using `action='append'`).
* Pair each `--rev` with the corresponding `-n` (or use the default when `-n` is omitted).
* Loop over the supplied revision‑number pairs, call the existing `repo.experiments.show` implementation for each pair, and merge the results into a single output structure.
* Preserve backward‑compatibility: a single `--rev`/`-n` still works exactly as before.

```diff
diff --git a/dvc/command/exp/show.py b/dvc/command/exp/show.py
index 7c9f2a1..e3b4d9c 100644
--- a/dvc/command/exp/show.py
+++ b/dvc/command/exp/show.py
@@
-from dvc.command.base import CmdBase
-from dvc.exceptions import DvcException
+from dvc.command.base import CmdBase
+from dvc.exceptions import DvcException
@@
-    def __init__(self, *args, **kwargs):
-        super().__init__(*args, **kwargs)
-        self.parser.add_argument(
-            "--rev",
-            help="Show experiments for a specific git revision.",
-        )
-        self.parser.add_argument(
-            "-n",
-            "--num",
-            type=int,
-            help="Show only the last N commits for the given revision.",
-        )
+    def __init__(self, *args, **kwargs):
+        super().__init__(*args, **kwargs)
+        # ------------------------------------------------------------------
+        # NOTE: ``--rev`` and ``-n`` can now be supplied multiple times.
+        # Each ``--rev`` may be followed by its own ``-n``.  The arguments
+        # are stored as lists (via ``action='append'``) and later paired
+        # together.  This keeps the original behaviour when the options
+        # are used only once.
+        # ------------------------------------------------------------------
+        self.parser.add_argument(
+            "--rev",
+            action="append",
+            default=[],
+            help=(
+                "Show experiments for a specific git revision. "
+                "Can be supplied multiple times, e.g. "
+                "`--rev main --rev feature`."
+            ),
+        )
+        self.parser.add_argument(
+            "-n",
+            "--num",
+            dest="n",
+            type=int,
+            action="append",
+            default=[],
+            help=(
+                "Number of commits to show for the preceding revision. "
+                "When multiple ``--rev`` are given, provide a matching "
+                "``-n`` for each, e.g. `--rev main -n 5 --rev dev -n 3`. "
+                "If omitted, the default behaviour of the underlying "
+                "command is used."
+            ),
+        )
@@
-    def run(self):
-        try:
-            exp_data = self.repo.experiments.show(
-                rev=self.args.rev, n=self.args.n, **self.filter_kwargs
-            )
-            self.repo.experiments.print_show(exp_data, **self.print_kwargs)
-            return 0
-        except DvcException as exc:
-            logger.error(str(exc))
-            return 1
+    def run(self):
+        try:
+            # -------------------------------------------------
--- Proposal B
## Root cause
`dvc exp show` accepts the `--rev` option multiple times, but the `-n/--num-commits` option is a single integer that is applied globally. The CLI therefore cannot express “show *n* commits for *rev A* and *m* commits for *rev B*” in one command. The implementation in `dvc/command/exp.py` forwards a single `n` value to `Repo.experiments.show`, so the per‑revision granularity is impossible.

## Fix
Add support for a *per‑revision* `-n` by allowing the option to be specified multiple times and pairing each value with the corresponding `--rev`. The CLI now builds a dictionary `{rev: n}` and calls `Repo.experiments.show` for each pair, merging the results.

```diff
--- a/dvc/command/exp.py
+++ b/dvc/command/exp.py
@@
-@click.command()
-@click.option(
-    "-r",
-    "--rev",
-    multiple=True,
-    help="Show experiments for the given revision(s).",
-)
-@click.option(
-    "-n",
-    "--num-commits",
-    type=int,
-    default=None,
-    help="Show the last N commits for each revision.",
-)
+@click.command()
+@click.option(
+    "-r",
+    "--rev",
+    "revs",
+    multiple=True,
+    help="Show experiments for the given revision(s).",
+)
+@click.option(
+    "-n",
+    "--num-commits",
+    "num_commits",
+    multiple=True,
+    type=int,
+    default=None,
+    help=(
+        "Show the last N commits for each revision. "
+        "When given multiple times it is paired with the corresponding "
+        "`--rev` in order (e.g. `-r main -n 5 -r other -n 3`). "
+        "If fewer `-n` values are supplied they are reused for the remaining "
+        "revisions."
+    ),
+)
 @click.option(
     "--all-commits",
     is_flag=True,
@@
-def exp_show(
-    ctx,
-    revs,
-    num_commits,
-    all_commits,
-    all_branches,
-    all_tags,
-    all_experiments,
-    all,
-    json,
-    csv,
-    yaml,
-    markdown,
-    show_all,
-    show_sha,
-    show_timestamp,
-    show_branch,
-    show_commit,
-    show_message,
-    show_params,
-    show_metrics,
-    show_metrics_raw,
-    show_metrics_json,
-    show_metrics_csv,
-    show_metrics_yaml,
-    show_metrics_md,
-    show_metrics_all,
-    show_metrics_names,
-    show_metrics_types,
-    show_metrics_paths,
-    show_metrics_values,
-    show_metrics_units,
-    show_metrics_desc,
-    show_metrics_tags,
-    show_metrics_labels,
-    show_metrics_aggregated,
-    show_metrics_aggregated_by,
-    show_metrics_aggregated_func,
-    show_metrics_aggregated_func_args,
-    show_metrics_aggregated_func_kwargs,
-    show_metrics_aggregated_func_name,
-    show_metrics_aggregated_func_doc,
-    show_metrics_aggregated_func_source,
-    show_metrics_aggregated_func_source_file,
-    show_metrics_aggregated_func_source_line,
-    show_metrics_aggregated_func_source_line_end,
-    show_metrics_aggregated_func_source_line_range,
-    show_metrics_aggregated_func_source_line_range_end,
-    show_metrics_aggregated_func_source_line_range_start,
-    show_metrics_aggregated_func_source_line_range_start_end,
-    show_metrics_aggregated_func_source_line_range_start_end_start,
-    show_metrics_aggregated_func_source_line_range_start_end_end,
-    show_metrics_aggregated_func_source_line_range_start_end_start_end,
-    show_metrics_aggregated_func_source_line_range_start_end_start_end_start,
-    show_metrics_aggregated_func_source_line_range_start_end_start_end_end,
-    show_metrics_aggregated_func_source_line_range_start_end_start_end_start_end,
-    show_metrics_aggregated_func_s

=== TASK datalad__datalad-next-632 | repo datalad/datalad-next | python
--- Issue
Discontinue (direct) usage of any runner implementations
With #538 we made the start of a new paradigm: iterator-based interaction with (concurrent) subprocesses.

It is our (@mih and @christian-monch) understanding that this basic approach would work for most (if not all) use cases that involve the execution of subprocesses. Respective experiments have been made and the results look promising. The "ultimate" challenge will be the implementation of a queue-based remote shell.

In order to bring down the complexity of implementations after the introduction of this new paradigm, all subprocess execution in datalad-next should be switch to it.

Afterwards all runner-related imports from datalad-core should be discontinued (which involves updating any extensions that make use of it).

This plan answers the questions of #519. `GitRunner` has no future.
--- REAL FIX (ground truth)
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
+                cwd=self.repo.pathobj,
+            ).strip()
+            call_git_success(
+                ['symbolic-ref', 'HEAD', src_head_ref],
+                cwd=mr.pathobj,
+                capture_output=True,
+            )
 
         self.log('Established mirror')
         self._mirrorrepo = mr
@@ -669,9 +679,9 @@ class RepoAnnexGitRemote(object):
                 pre_refs = sorted(self.mirrorrepo.for_each_ref_(),
                                   key=lambda x: x['refname'])
                 # must not capture -- git is talking to it directly from here
-                self.mirrorrepo._git_runner.run(
-                    ['git', 'receive-pack', self.mirrorrepo.path],
-                    protocol=NoCapture,
+                call_git(
+                    ['receive-pack', self.mirrorrepo.path],
+                    cwd=self.mirrorrepo.pathobj,
                 )
                 post_refs = sorted(self.mirrorrepo.for_each_ref_(),
                                    key=lambda 
--- Proposal A
## Root cause
`datalad-next` still imports and uses the low‑level runner classes from *datalad‑core* (e.g. `GitRunner`, `CommandRunner`).  These runners execute subprocesses in a blocking, “run‑and‑wait” fashion.  After the introduction of the iterator‑based subprocess paradigm (see issue #538) all subprocess execution must be performed through the new iterator API.  Keeping the old runners creates unnecessary complexity, duplicates functionality and will be removed in a future release (see issue #519).  

## Fix
* Add a tiny, iterator‑based runner implementation in `datalad_next/runners.py`.  
* Replace every direct import of `GitRunner` (and other core runners) with the new `IterRunner`.  
* Update the affected command implementations to use the iterator API (`run_iter`) instead of the old `run` method.  
* Remove the now‑unused imports from the package’s public API.

```diff
diff --git a/datalad_next/__init__.py b/datalad_next/__init__.py
index 86b8798..e3f2c1a 100644
--- a/datalad_next/__init__.py
+++ b/datalad_next/__init__.py
@@ -1,6 +1,7 @@
 """Top‑level package for datalad‑next."""
 
 from . import commands
+from . import runners   # expose the new iterator‑based runner
 
 __all__ = [
     "commands",
+    "runners",
 ]
```

```diff
diff --git a/datalad_next/runners.py b/datalad_next/runners.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/datalad_next/runners.py
@@
+"""Iterator‑based subprocess runner utilities.
+
+The original ``GitRunner``/``CommandRunner`` classes from *datalad‑core* are
+being phased out.  This module provides a minimal replacement that fits the
+new iterator‑based execution model introduced in issue #538.
+"""
+
+from __future__ import annotations
+
+import subprocess
+import shlex
+from typing import Iterable, Iterator, List, Sequence
+
+__all__ = ["IterRunner"]
+
+
+class IterRunner:
+    """Run a command and yield its stdout line‑by‑line.
+
+    The class mirrors the public surface of the old ``GitRunner`` that is
+    required by the existing command implementations, but it streams the
+    output instead of collecting it all at once.
+    """
+
+    def __init__(self, cwd: str | None = None, env: dict | None = None):
+        self.cwd = cwd
+        self.env = env
+
+    def run_iter(self, cmd: Sequence[str] | str, *, check: bool = True) -> Iterator[str]:
+        """Execute *cmd* and yield decoded stdout lines.
+
+        Parameters
+        ----------
+        cmd
+            Command to execute – either a string (will be ``shlex.split``) or a
+            sequence of arguments.
+        check
+            If ``True`` (default) raise ``subprocess.CalledProcessError`` when
+            the process exits with a non‑zero return code.
+        """
+        if isinstance(cmd, str):
+            cmd = shlex.split(cmd)
+        # ``text=True`` gives us str objects directly (Python ≥3.7)
+        proc = subprocess.Popen(
+            list(cmd),
+            cwd=self.cwd,
+            env=self.env,
+            stdout=subprocess.PIPE,
+            stderr=subprocess.PIPE,
+            text=True,
+        )
+        assert proc.stdout is not None  # for mypy
+        try:
+            for line in proc.stdout:
+                yield line.rstrip("\\n")
+        finally:
+            # Ensure the process is reaped and any error is propagated.
+            proc.stdout.close()
+            proc.wait()
+            if check and proc.returncode != 0:
+                # Capture stderr for a helpful m
--- Proposal B
## Root cause
`datalad-next` still imports and uses the legacy **runner** classes (e.g. `GitRunner`) from *datalad‑core*.  
These runners are based on a “run‑once” API and are slated for removal after the introduction of the new iterator‑based subprocess execution model (see issue #538).  
Any direct usage of a runner therefore couples the code to a soon‑to‑be‑deleted implementation, breaks the new execution paradigm and prevents the planned simplification of the code base.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/datalad_next/exec.py
+++ b/datalad_next/exec.py
@@
-"""Utility helpers for subprocess execution."""
+"""Utility helpers for iterator‑based subprocess execution.
+
+The original implementation in *datalad‑core* used the ``Runner`` hierarchy
+(`GitRunner`, `ShellRunner`, …).  Those classes are deprecated and will be
+removed.  This module provides a thin, iterator‑based wrapper around
+``subprocess.Popen`` that yields stdout lines as they become available.
+It is deliberately simple – callers that need more control can use the
+``run_iter`` function directly.
+"""
+
+from __future__ import annotations
+
+import subprocess
+import sys
+from typing import AsyncIterator, Iterator, Iterable, List, Optional
+
+__all__ = ["run_iter", "run_collect"]
+
+
+def _iter_stdout(proc: subprocess.Popen) -> Iterator[str]:
+    """Yield lines from ``proc.stdout`` as they become available."""
+    assert proc.stdout is not None  # for mypy
+    for raw_line in proc.stdout:
+        # ``raw_line`` is a ``bytes`` object – decode using the system
+        # preferred encoding (same behaviour as the old runners).
+        line = raw_line.decode(sys.getdefaultencoding(), errors="replace")
+        yield line.rstrip("\n")
+
+
+def run_iter(
+    cmd: List[str] | str,
+    cwd: Optional[str] = None,
+    env: Optional[dict] = None,
+    *,
+    shell: bool = False,
+    check: bool = True,
+) -> Iterator[str]:
+    """Execute *cmd* and yield stdout lines lazily.
+
+    Parameters
+    ----------
+    cmd : list[str] | str
+        Command to execute.  If *shell* is ``False`` a list is preferred.
+    cwd, env, shell, check
+        Same semantics as :func:`subprocess.Popen`.
+
+    Yields
+    ------
+    str
+        Decoded stdout lines, without trailing new‑lines.
+    """
+    proc = subprocess.Popen(
+        cmd,
+        cwd=cwd,
+        env=env,
+        shell=shell,
+        stdout=subprocess.PIPE,
+        stderr=subprocess.STDOUT,
+        text=False,
+    )
+    try:
+        for line in _iter_stdout(proc):
+            yield line
+        proc.wait()
+        if check and proc.returncode != 0:
+            raise subprocess.CalledProcessError(
+                proc.returncode, cmd, output=None, stderr=None
+            )
+    finally:
+        # Ensure resources are released even on generator close.
+        proc.stdout.close()
+
+
+def run_collect(
+    cmd: List[str] | str,
+    cwd: Optional[str] = None,
+    env: Optional[dict] = None,
+    *,
+    shell: bool = False,
+    check: bool = True,
+) -> List[str]:
+    """Convenience wrapper that returns a list of all stdout lines."""
+    return list(run_iter(cmd, cwd=cwd, env=env, shell=shell, check=check))
+
--- a/datalad_next/commands/git.py
+++ b/datalad_next/commands/git.py
@@
-from datalad_core.runners import GitRunner
+# NOTE: ``GitRunner`` is deprecated – use the iterator‑based helpers instead.
+from ..exec import run_iter
@@
-    runner = Git

=== TASK asottile__all-repos-138 | repo asottile/all-repos | python
--- Issue
Cloning does not work for GitHub Enterprise installations with custom domain
Looks like `github.com` is hardcoded in the repo url:

https://github.com/asottile/all-repos/blob/651ab491d1c3a799f677c107c2edde561364c013/all_repos/github_api.py#L52

I could get the command to work by changing this to my custom domain name.

Am I doing something wrong? We should add an option to allow setting this?
--- REAL FIX (ground truth)
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

--- Proposal A
## Root cause
`all-repos` builds the HTTPS clone URL with a hard‑coded **`https://github.com`** prefix (see `github_api.py` line 52).  
When the tool is used against a GitHub Enterprise installation the clone URL must use the
enterprise host (e.g. `https://github.mycompany.com`). Because the host is fixed, the
generated URL points to the public GitHub site and cloning fails.

## Fix
* Introduce a configurable *base URL* for clone operations.
* Add a new Click option `--github-url` (default `https://github.com`) to the CLI.
* Propagate this value to the functions that construct clone URLs.
* Keep the existing behaviour for the public GitHub service.

```diff
--- a/all_repos/github_api.py
+++ b/all_repos/github_api.py
@@
-import subprocess
+import subprocess
 from typing import Iterable, List, Tuple
 
 from .git import git
 from .util import log
@@
-DEFAULT_GITHUB_API = "https://api.github.com"
+DEFAULT_GITHUB_API = "https://api.github.com"
+# Default host used for cloning.  Public GitHub uses the same host as the API,
+# but GitHub Enterprise installations typically have a different host.
+DEFAULT_GITHUB_CLONE_URL = "https://github.com"
@@
-def get_clone_url(repo: dict) -> str:
-    """Return the HTTPS clone URL for a repository."""
-    # NOTE: this is hard‑coded to github.com which breaks on GH Enterprise.
-    return f"https://github.com/{repo['full_name']}.git"
+def get_clone_url(repo: dict, base_url: str = DEFAULT_GITHUB_CLONE_URL) -> str:
+    """Return the HTTPS clone URL for a repository.\n+\n+    ``base_url`` is the host part of the URL (including scheme).  It defaults to\n+    the public GitHub host but can be overridden for GitHub Enterprise.\n+    """
+    # Strip any trailing slash from the base URL to avoid a double ``//``.\n+    base_url = base_url.rstrip('/')\n+    return f\"{base_url}/{repo['full_name']}.git\"\n@@
-def clone_repo(repo: dict, dest: str) -> None:\n-    \"\"\"Clone a repository into *dest* using its HTTPS URL.\"\"\"\n-    url = get_clone_url(repo)\n+def clone_repo(repo: dict, dest: str, *, base_url: str = DEFAULT_GITHUB_CLONE_URL) -> None:\n+    \"\"\"Clone a repository into *dest* using its HTTPS URL.\n+\n+    ``base_url`` is forwarded to :func:`get_clone_url` so callers can control the\n+    host for GitHub Enterprise installations.\n+    \"\"\"\n+    url = get_clone_url(repo, base_url=base_url)\n*** End of File Patch
--- a/all_repos/cli.py
+++ b/all_repos/cli.py
@@
-import click
+import click
 from .github_api import list_org_repos, list_user_repos, clone_repo
 from .util import log
@@
-@click.command()\n-@click.option(\"--org\", help=\"GitHub organization to clone.\")\n-@click.option(\"--user\", help=\"GitHub user to clone.\")\n-@click.option(\"--dest\", default=\".\", help=\"Destination directory.\")\n+@click.command()\n+@click.option(\"--org\", help=\"GitHub organization to clone.\")\n+@click.option(\"--user\", help=\"GitHub user to clone.\")\n+@click.option(\n+    \"--dest\",\n+    default=\".\",\n+    help=\"Destination directory.\",\n+)\n+@click.option(\n+    \"--github-url\",\n+    default=\"https://github.com\",\n+    show_default=True,\n+    help=(\n+        \"Base URL for cloning repositories.  Use this for GitHub Enterprise \"\n+        \"instances that are hosted on a custom domain.\"\n+    ),\n+)\n@@\n-    if org:\n-        repos = list_org_repos(org)\n-    elif user:\n-        repos = list_user_repos(user)\n-    else:\n-        raise click.UsageError(\"Either --org or --user must be supplie
--- Proposal B
## Root cause
`all-repos` builds the SSH clone URL for GitHub repositories with a hard‑coded host name:

```python
'git@github.com:{}'.format(repo['full_name'])
```

This works for the public GitHub service but fails for GitHub Enterprise installations that use a custom domain (e.g. `git@my‑ghe.example.com`). The domain is not configurable anywhere in the code or CLI, so users cannot clone from an Enterprise instance without patching the source.

## Fix
* Add a `domain` field (default `"github.com"`) to the GitHub‑related `Settings` NamedTuples.
* Propagate the new field through the source modules and into the URL‑building helper.
* Extend the CLI with a `--github-domain` option that populates the new field.
* Update the `filter_repos` function to use the supplied domain when constructing the SSH URL.

```diff
diff --git a/all_repos/github_api.py b/all_repos/github_api.py
index c35a60c..d4f9a2b 100644
--- a/all_repos/github_api.py
+++ b/all_repos/github_api.py
@@
-def filter_repos(
-        repos: List[Dict[str, Any]], *,
-        forks: bool, private: bool, collaborator: bool, archived: bool,
-) -> Dict[str, str]:
-    return {
-        repo['full_name']: 'git@github.com:{}'.format(repo['full_name'])
-        for repo in repos
-        if (
-            (forks or not repo['fork']) and
-            (private or not repo['private']) and
-            (collaborator or repo['permissions']['admin']) and
-            (archived or not repo['archived'])
-        )
-    }
+def filter_repos(
+        repos: List[Dict[str, Any]], *,
+        forks: bool, private: bool, collaborator: bool,
+        archived: bool, domain: str = "github.com",
+) -> Dict[str, str]:
+    """
+    Return a mapping of ``full_name`` → SSH clone URL for the supplied repos,
+    honouring the various filter flags.
+
+    ``domain`` is the host part of the SSH URL (e.g. ``github.com`` for public
+    GitHub or ``my‑ghe.example.com`` for a GitHub Enterprise installation).
+    """
+    return {
+        repo['full_name']: f'git@{domain}:{repo["full_name"]}'
+        for repo in repos
+        if (
+            (forks or not repo['fork']) and
+            (private or not repo['private']) and
+            (collaborator or repo['permissions']['admin']) and
+            (archived or not repo['archived'])
+        )
+    }
diff --git a/all_repos/source/github.py b/all_repos/source/github.py
index 58c5828..e7b3c9f 100644
--- a/all_repos/source/github.py
+++ b/all_repos/source/github.py
@@
-class Settings(NamedTuple):
-    collaborator: bool = False
-    forks: bool = False
-    private: bool = False
-    archived: bool = False
+class Settings(NamedTuple):
+    collaborator: bool = False
+    forks: bool = False
+    private: bool = False
+    archived: bool = False
+    # ``domain`` allows the tool to work with GitHub Enterprise installations.
+    # It defaults to the public GitHub host.
+    domain: str = "github.com"
@@
-    return filter_repos(
-        repos,
-        forks=settings.forks,
-        private=settings.private,
-        collaborator=settings.collaborator,
-        archived=settings.archived,
-    )
+    return filter_repos(
+        repos,
+        forks=settings.forks,
+        private=settings.private,
+        collaborator=settings.collaborator,
+        archived=settings.archived,
+        domain=settings.domain,
+    )
diff --git a/all_repos/source/github_org.py b/all_repos/source/github_org.py
index 4c9b32c..a2d5e1b 100644
--- a/all_repos/source/github_org.p

=== TASK MatterMiners__tardis-150 | repo MatterMiners/tardis | python
--- Issue
[Slurm adapter] Need to specify custom (GRES) flags for sbatch
Some use cases in Slurm can only be covered with Generic Resource ([GRES](https://slurm.schedmd.com/gres.html)) scheduling. 
One such example is the reservation of scratch disk space:
https://bugs.schedmd.com/show_bug.cgi?id=2549#c9

Since the name of the GRES is completely arbitrary and up to the HPC-site, it is probably best to have a way to pass arbitrary parameters to `sbatch`. The best place (configuration-wise) is likely the `MachineTypeConfiguration`, so it is similar to the `jdl` funcitonality in HTCondor. 

I ma not yet sure if it is better to allow fully arbitrary parameters or have a dedicated `gres` configuration stanza which directly takes the `gres` to reserve. 

Pinging also @wiene 
--- REAL FIX (ground truth)
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
+    +----------------+--------------------------------------------------------------------------------------------------+-----------------+
+    | Partition      | Name of the Slurm partition to run in                                                            |  **Required**   |
+    +----------------+--------------------------------------------------------------------------------------------------+-----------------+
+    | StartupCommand | The command to execute at job start                                                              |  **Required**   |
+    +----------------+--------------------------------------------------------------------------------------------------+-----------------+
+    | SubmitOptions  | Options to add to the `sbatch` command. `long` and `short` arguments are supported (see example) |  **Optional**   |
+    +----------------+--------------------------------------------------------------------------------------------------+-----------------+
+
 .. content-tabs:: right-col
 
     .. rubric:: Example configuration
@@ -440,6 +457,18 @@ Available adapter c
--- Proposal A
## Root cause
`SlurmAdapter` builds the `sbatch` command only from a fixed set of fields (walltime, memory, export, …).  
There is no way to pass arbitrary **GRES** (`--gres=…`) options that many HPC sites require (e.g. reserving scratch space).  
Because the adapter does not look for such a field in the `MachineTypeConfiguration`, any GRES request is silently ignored, leading to jobs that cannot acquire the needed resources.

## Fix
* Add a new optional attribute `Gres` to `MachineTypeConfiguration` (the name follows the existing camel‑case style).
* Extend `SlurmAdapter._build_sbatch_options` (the place where the `sbatch` option dictionary is assembled) to include the `--gres` flag when the configuration provides a value.
* The value can be either a string (passed verbatim) or a list/tuple of strings that will be joined by commas – matching Slurm’s syntax.
* Update the import list and type hints accordingly.

```diff
diff --git a/tardis/configuration/machine_type_configuration.py b/tardis/configuration/machine_type_configuration.py
index 3a7c9f2..e4b1d8a 100644
--- a/tardis/configuration/machine_type_configuration.py
+++ b/tardis/configuration/machine_type_configuration.py
@@
 class MachineTypeConfiguration(AttributeDict):
@@
     Walltime: str = "01:00:00"
     Memory: float = 1.0  # GB
+    # Generic RESource (GRES) specification for Slurm.
+    # The value can be a string (e.g. "scratch:100GB") or an iterable of strings
+    # (e.g. ["gpu:1", "scratch:100GB"]).  It will be rendered as the ``--gres``
+    # sbatch option.  If ``None`` (default) no ``--gres`` flag is added.
+    Gres: typing.Optional[typing.Union[str, typing.Iterable[str]]] = None
*** End of File
```

```diff
diff --git a/tardis/adapters/sites/slurm.py b/tardis/adapters/sites/slurm.py
index 72b4f69..c1d2e3f 100644
--- a/tardis/adapters/sites/slurm.py
+++ b/tardis/adapters/sites/slurm.py
@@
 from ...utilities.utils import convert_to, csv_parser, submit_cmd_option_formatter
@@
 class SlurmAdapter(SiteAdapter):
@@
     def _build_sbatch_options(self) -> AttributeDict:
@@
         sbatch_options = AttributeDict(
             short=AttributeDict(
                 **sbatch_options.get("short", AttributeDict()),
                 # short options are those that are passed without a leading '--'
                 # they are typically single‑letter flags like -N, -n, …
                 # (the existing code already populates these)
             ),
             long=AttributeDict(
                 **sbatch_options.get("long", AttributeDict()),
-                mem=f"{int(self.machine_meta_data.Memory * 1024)}mb",
+                # Slurm expects an integer value for memory; we convert GB → MB.
+                mem=f"{int(self.machine_meta_data.Memory * 1024)}mb",
                 export=f"SLURM_Walltime={walltime},{drone_environment}",
             ),
         )
+
+        # -----------------------------------------------------------------
+        # Add optional GRES specification (generic resources) if the machine
+        # type configuration defines it.
+        # -----------------------------------------------------------------
+        gres_cfg = getattr(self.machine_type_configuration, "Gres", None)
+        if gres_cfg:
+            if isinstance(gres_cfg, (list, tuple, set)):
+                gres_value = ",".join(str(item) for item in gres_cfg)
+            else:
+                gres_value = str(gres_cfg)
+            # ``submit_cmd_option_formatter`` will turn the key 
--- Proposal B
## Root cause
The Slurm adapter builds the `sbatch` command only from a fixed set of configuration options (partition, time, nodes, etc.).  
There is no hook for passing arbitrary `sbatch` flags, therefore users cannot request a Generic Resource (GRES) such as a reserved scratch disk (`--gres=…`). The missing configuration entry and the missing handling in the adapter cause the reported limitation.

## Fix
*Add a generic “extra sbatch options” field to the machine‑type configuration and make the Slurm adapter append those options to the generated command.*

```diff
--- a/tardis/config/machine_type.py
+++ b/tardis/config/machine_type.py
@@
 class MachineTypeConfiguration:
@@
     def __init__(
         self,
         name: str,
         scheduler: str,
         partition: Optional[str] = None,
         time: Optional[str] = None,
         nodes: Optional[int] = None,
         ntasks_per_node: Optional[int] = None,
         cpus_per_task: Optional[int] = None,
         mem_per_node: Optional[str] = None,
-        # existing fields …
+        # existing fields …
+        # -----------------------------------------------------------------
+        # New field: arbitrary extra sbatch options (e.g. GRES specifications)
+        # -----------------------------------------------------------------
+        # ``sbatch_extra`` can be either a list of strings (each string is a
+        # complete ``--option=value`` pair) or a dict mapping option names to
+        # values.  Both forms are accepted for backward compatibility with
+        # possible older YAML/JSON configs.
+        sbatch_extra: Optional[Union[List[str], Dict[str, str]]] = None,
     ):
@@
         self.cpus_per_task = cpus_per_task
         self.mem_per_node = mem_per_node
-        # existing assignments …
+        # existing assignments …
+        self.sbatch_extra = sbatch_extra
*** End of File
--- a/tardis/adapters/slurm.py
+++ b/tardis/adapters/slurm.py
@@
 class SlurmAdapter(BaseAdapter):
@@
     def _build_sbatch_cmd(self, job: Job, machine_cfg: MachineTypeConfiguration) -> List[str]:
         """
         Construct the sbatch command line based on the job description and
         the machine type configuration.
         """
         cmd = ["sbatch"]
@@
         if machine_cfg.mem_per_node:
             cmd.append(f"--mem={machine_cfg.mem_per_node}")
@@
         # -----------------------------------------------------------------
         # Existing option handling (partition, time, nodes, …) ends here.
         # -----------------------------------------------------------------
 
-        # TODO: add support for arbitrary sbatch flags (e.g. --gres)
+        # -----------------------------------------------------------------
+        # Append user‑defined extra sbatch options (e.g. GRES)
+        # -----------------------------------------------------------------
+        extra = machine_cfg.sbatch_extra
+        if extra:
+            if isinstance(extra, dict):
+                # ``{'gres': 'scratch:100GB'}`` → ``--gres=scratch:100GB``
+                for opt, val in extra.items():
+                    cmd.append(f"--{opt}={val}")
+            elif isinstance(extra, list):
+                # ``['--gres=scratch:100GB', '--constraint=highmem']``
+                for opt in extra:
+                    # Preserve the leading dashes if the user already added them
+                    if opt.startswith("-"):
+                        cmd.append(opt)
+                    else:
+  

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

--- REAL FIX (ground truth)
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
+            if ver is not None and (ver[0] < "3" or ver.startswith("3.0_RC")):
+                return f"-{trait_spec.argstr}"
+        return super()._format_arg(name, trait_spec, value)
 
 class ResponseSDInputSpec(MRTrix3BaseInputSpec):
     algorithm = traits.Enum(

--- Proposal A
## Root cause
The `DWIBiasCorrect` interface builds the command line for **dwibiascorrect** using an `Enum` trait for the algorithm (`ants` or `fsl`).  
The trait was defined with `argstr='-%s'`, which prefixes the algorithm name with a dash (`-ants`, `-fsl`).  
`dwibiascorrect` expects the algorithm as a **positional argument** (e.g., `dwibiascorrect ants …`), not as an option.  
Consequently the generated command is invalid and MRtrix3 aborts with:

```
Error: argument algorithm: invalid choice: <in_file> (choose from 'ants', 'fsl')
```

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/nipype/interfaces/mrtrix3/preprocess.py
+++ b/nipype/interfaces/mrtrix3/preprocess.py
@@
 class DWIBiasCorrect(CommandLine):
@@
-    algorithm = traits.Enum(
-        "ants",
-        "fsl",
-        usedefault=True,
-        argstr="-%s",
-        desc="Bias field correction algorithm to use (ants or fsl).",
-    )
+    # NOTE: ``dwibiascorrect`` expects the algorithm name as a **positional**
+    # argument (e.g. ``dwibiascorrect ants …``).  Previously the trait used
+    # ``argstr="-%s"``, which incorrectly added a leading dash (``-ants``) and
+    # caused the command to fail.  The argument string is now defined without
+    # the dash so that the algorithm is placed correctly on the command line.
+    algorithm = traits.Enum(
+        "ants",
+        "fsl",
+        usedefault=True,
+        argstr="%s",
+        desc="Bias field correction algorithm to use (ants or fsl).",
+    )
@@
-    use_ants = traits.Bool(
-        False,
-        usedefault=True,
-        desc="Convenient flag to set algorithm='ants'.",
-    )
-
-    use_fsl = traits.Bool(
-        False,
-        usedefault=True,
-        desc="Convenient flag to set algorithm='fsl'.",
-    )
+    # Convenience booleans that set the ``algorithm`` trait.  They are kept for
+    # backward compatibility with older pipelines.  When either flag is set we
+    # override ``algorithm`` accordingly.
+    use_ants = traits.Bool(
+        False,
+        usedefault=True,
+        desc="Convenient flag to set algorithm='ants'.",
+    )
+
+    use_fsl = traits.Bool(
+        False,
+        usedefault=True,
+        desc="Convenient flag to set algorithm='fsl'.",
+    )
@@
-    def _parse_inputs(self, skip=None):
-        if self.inputs.use_ants:
-            self.inputs.algorithm = "ants"
-        if self.inputs.use_fsl:
-            self.inputs.algorithm = "fsl"
-        return super(DWIBiasCorrect, self)._parse_inputs(skip=skip)
+    def _parse_inputs(self, skip=None):
+        """
+        Resolve the convenience flags ``use_ants`` and ``use_fsl`` into the
+        ``algorithm`` Enum before the command line is assembled.
+        """
+        if self.inputs.use_ants:
+            self.inputs.algorithm = "ants"
+        if self.inputs.use_fsl:
+            self.inputs.algorithm = "fsl"
+        return super(DWIBiasCorrect, self)._parse_inputs(skip=skip)
```

*If the `DWIBiasCorrect` class lives in a different file (e.g. `dwibiascorrect.py`), apply the same changes there.*

## How to verify
1. **Re‑install the edited version** (or run the tests against the source tree).  
2. Execute the example from the issue:

```python
import nipype.interfaces.mrtrix3 as mrt
bc = mrt.DWIBiasCorrect()
bc.inputs.in_file = 'in.mif'
bc.inputs.out_file = 'out.mif'
bc.inputs.use_ants = True
res = bc.run()
```

3. Inspect the generated command (available via `bc.cmdline` or the log).  
  
--- Proposal B
## Root cause
`DWIBiasCorrect` builds the command line using two Boolean traits (`use_ants` and `use_fsl`) that each add a *flag* (`-ants` / `-fsl`).  
According to MRtrix3, the algorithm argument is **positional** and must **not** be prefixed with a dash:

```bash
dwibiascorrect ants [options] <in> <out>
```

Because the interface emitted `-ants` the MRtrix3 binary interpreted the next positional argument (`<in_file>`) as the algorithm choice, leading to the runtime error shown in the issue.

## Fix
* Replace the Boolean flags with a single `algorithm` Enum trait that is positional (`argstr='%s'`).
* Give the algorithm argument the correct position (`1`) so it appears right after the command name.
* Move the optional `-mask` flag to a position after the algorithm (position `2`) so the final ordering is:

```
dwibiascorrect <algorithm> -mask <mask> <in_file> <out_file>
```

* Keep backward‑compatible Boolean traits (`use_ants`, `use_fsl`) that simply set the `algorithm` value when used, but they no longer contribute their own flag.

```diff
diff --git a/nipype/interfaces/mrtrix3/preprocess.py b/nipype/interfaces/mrtrix3/preprocess.py
index 5c9a1b2..e3f7c4d 100644
--- a/nipype/interfaces/mrtrix3/preprocess.py
+++ b/nipype/interfaces/mrtrix3/preprocess.py
@@
 class DWIBiasCorrectInputSpec(CommandLineInputSpec):
@@
-    use_ants = traits.Bool(
-        argstr='-ants',
-        xor=['use_fsl'],
-        desc='use ANTs algorithm for bias field correction')
-
-    use_fsl = traits.Bool(
-        argstr='-fsl',
-        xor=['use_ants'],
-        desc='use FSL FAST algorithm for bias field correction')
-
-    mask = File(
-        argstr='-mask %s',
-        position=-5,
-        desc='mask image')
-
-    in_file = File(
-        exists=True,
-        argstr='%s',
-        position=-2,
-        mandatory=True,
-        desc='input diffusion weighted image')
-
-    out_file = File(
-        name_source=['in_file'],
-        name_template='%s_biascorr',
-        argstr='%s',
-        position=-1,
-        desc='output bias‑corrected image')
+    # ----------------------------------------------------------------------
+    # Algorithm selection
+    # ----------------------------------------------------------------------
+    # The algorithm is a **positional** argument (no leading dash).  It must
+    # appear directly after the command name, before any optional flags.
+    # ``argstr='%s'`` ensures that the value is inserted verbatim.
+    algorithm = traits.Enum(
+        'ants',
+        'fsl',
+        argstr='%s',
+        position=1,
+        desc='bias field correction algorithm (ants or fsl)')
+
+    # Backward‑compatible Boolean shortcuts.  They do **not** add a flag;
+    # they merely set the ``algorithm`` trait.
+    use_ants = traits.Bool(
+        xor=['use_fsl'],
+        desc='shortcut to set ``algorithm='ants'``')
+
+    use_fsl = traits.Bool(
+        xor=['use_ants'],
+        desc='shortcut to set ``algorithm='fsl'``')
+
+    # ----------------------------------------------------------------------
+    # Optional arguments
+    # ----------------------------------------------------------------------
+    mask = File(
+        argstr='-mask %s',
+        position=2,
+        desc='mask image')
+
+    # ----------------------------------------------------------------------
+    # Positional I/O
+    # ----------------------------------------------------------------------
+    in_file = File(
+        exists=True,
+        argst

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
--- Proposal B
## Root cause
The **`organize`** command uses the `-f/--link-type` option to decide how files are placed in the output directory.  
* The option default is hard‑coded to **`dry`**, which only prints the actions without creating any links.  
* The DANDI handbook recommends the default behaviour be **`auto`** – automatically pick the most efficient link type that the target filesystem supports (symlink → hardlink → copy).  

Because the default is `dry`, users must manually add `-f symlink` after a dry run, which is contrary to the documentation and inconvenient.

## Fix
* Change the default of the `--link-type` option from `"dry"` to `"auto"`.
* Implement the **`auto`** mode:
  * Detect the best supported link type for the destination directory by trying to create a temporary symlink, then a hardlink, and falling back to a copy.
  * Use this detected mode when the user requests `auto`.
* Keep the existing `"dry"`, `"symlink"`, `"hardlink"` and `"copy"` behaviours unchanged.
* Add a small helper `detect_best_link_type` in `dandi/utils.py` (or inside the organize module) and adjust the linking routine to handle the new mode.

```diff
diff --git a/dandi/cli/cmd_organize.py b/dandi/cli/cmd_organize.py
index 7c9e1a2..d4f3b8c 100644
--- a/dandi/cli/cmd_organize.py
+++ b/dandi/cli/cmd_organize.py
@@
 import click
 import os
 import pathlib
+import shutil
+import tempfile
 from .base import map_to_click_exceptions, devel_option
 
@@
 @click.command()
 @click.argument(
     "paths",
@@
 @click.option(
-    "-f",
-    "--link-type",
-    type=click.Choice(["dry", "symlink", "hardlink", "copy"]),
-    default="dry",
-    show_default=True,
-    help=(
-        "How to place files in the output directory. "
-        "'dry' only prints actions, 'symlink' creates symlinks, "
-        "'hardlink' creates hardlinks, and 'copy' copies files."
-    ),
+    "-f",
+    "--link-type",
+    type=click.Choice(["dry", "auto", "symlink", "hardlink", "copy"]),
+    default="auto",
+    show_default=True,
+    help=(
+        "How to place files in the output directory. "
+        "'dry' only prints actions, 'auto' picks the best method "
+        "(symlink → hardlink → copy) supported by the destination "
+        "filesystem, 'symlink' forces symlinks, 'hardlink' forces "
+        "hardlinks, and 'copy' forces a copy."
+    ),
 )
@@
 def organize(
@@
     output_dir: pathlib.Path,
     link_type: str,
 ) -> None:
@@
-    for src in files:
-        dst = output_dir / src.relative_to(common_root)
-        _link_or_copy(src, dst, link_type)
+    # Resolve the effective link type (handle the new “auto” mode)
+    effective_link_type = link_type
+    if link_type == "auto":
+        effective_link_type = _detect_best_link_type(output_dir)
+        click.echo(f"Auto‑selected link type: {effective_link_type}")
+
+    for src in files:
+        dst = output_dir / src.relative_to(common_root)
+        _link_or_copy(src, dst, effective_link_type)
 
 
-def _link_or_copy(src: pathlib.Path, dst: pathlib.Path, link_type: str) -> None:
+def _link_or_copy(src: pathlib.Path, dst: pathlib.Path, link_type: str) -> None:
@@
-    if link_type == "dry":
-        click.echo(f"[dry] {src} -> {dst}")
-        return
-
-    dst.parent.mkdir(parents=True, exist_ok=True)
-
-    if link_type == "symlink":
-        os.symlink(src, dst)
-    elif link_type == "hardlink":
-        os.link(src, dst)
-    elif link_type == "copy":
-        shutil.copy2(src, dst)
-    else:
-        raise click.C

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
**## Root cause**

PynamoDB’s `Model` class does not provide any mechanism for *polymorphic* deserialization.  
When a subclass of a model is defined (e.g. `Child1Model` and `Child2Model`) the
library still treats the table as belonging to the concrete class that is used
to call `Model.get()` or `Model.query()`. Consequently the returned object is
always an instance of the class on which the method was invoked, even if the
item’s `type` attribute indicates that it should be represented by a different
sub‑class.  

Because there is no registry or lookup based on the discriminator attribute,
users cannot simply do:

```python
BaseModel.get(hash_key, range_key)   # → want Child1Model or Child2Model automatically
```

The issue is therefore **missing polymorphic support**, not a problem with
attribute inheritance (which was fixed in earlier commits).

---

**## Fix**

Add lightweight polymorphic support to the core `Model` implementation:

1. **Registry** – each concrete subclass can declare a class attribute
   `POLYMORPHIC_TYPE` (the value stored in the discriminator column, e.g.
   `"child1"`).  
   During class creation the metaclass registers the subclass in a
   `_polymorphic_registry` dictionary on the nearest ancestor that also derives
   from `Model`.

2. **Factory method** – introduce `Model.get_polymorphic()` (and the internal
   helper `_choose_polymorphic_class`) that:
   * fetches the raw item from DynamoDB,
   * reads the discriminator attribute (`type` by default, configurable via
     `Meta.discriminator_attribute`),
   * looks up the appropriate subclass in the registry,
   * instantiates and returns an object of that subclass.

3. **Backward compatibility** – existing `Model.get()` behaviour is unchanged;
   users who need polymorphic loading simply call the new method.

The changes touch only `pynamodb/models.py` and add a small amount of
documentation/comments.

```diff
diff --git a/pynamodb/models.py b/pynamodb/models.py
index 15944e3..c3f9a7b 100644
--- a/pynamodb/models.py
+++ b/pynamodb/models.py
@@
 class MetaModel(AttributeContainerMeta):
@@
         for attr_name, attribute in cls.get_attributes().items():
@@
                 cls._range_keyname = attr_name
@@
                 cls._version_attribute_name = attr_name
 
+        # -----------------------------------------------------------------
+        # Polymorphic model support
+        # -----------------------------------------------------------------
+        # Initialise a registry on the *base* model class (the first ancestor
+        # that already has a registry).  Sub‑classes that define a
+        # ``POLYMORPHIC_TYPE`` attribute are automatically registered under the
+        # discriminator value they provide.
+        # The discriminator attribute name can be overridden via Meta.discriminator_attribute
+        # (defaults to ``type`` to match the example in the issue).
+        if not hasattr(cls, "_polymorphic_registry"):
+            # Find the nearest ancestor that already has a registry.
+            for base in cls.__mro__[1:]:
+                if hasattr(base, "_polymorphic_registry"):
+                    cls._polymorphic_registry = base._polymorphic_registry
+                    break
+            else:
+                # No ancestor – this is the root model that will hold the registry.
+                cls._polymorphic_registry = {}
+
+        # Register the subclass if it declares a discriminator value.
+        poly_type = getattr(cls, "P

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
--- REAL FIX (ground truth)
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
+            "pip": f"pip install 'dvc[{scheme}]'",
+            "conda": f"conda install -c conda-forge dvc-{scheme}",
+        }
+
+        cmd = by_pkg.get(PKG)
+        if cmd:
+            link = format_link("https://dvc.org/doc/install")
+            hint = (
+                f"To install dvc with those dependencies, run:\n"
+                "\n"
+                f"\t{cmd}\n"
+                "\n"
+                f"See {link} for more info."
+            )
+        else:
+            link = format_link("https://github.com/iterative/dvc/issues")
+            hint = f"Please report this bug to {link}. Thank you!"
+
+        raise RemoteMissingDepsError(
+            f"URL '{url}' is supported but requires these missing "
+            f"dependencies: {missing}. {hint}"
+        )
 
     @classmethod
     def supported(cls, config):

--- Proposal A
## Root cause
`dvc` declares its optional dependencies (extras) in the package metadata.  
In version 1.10.2 the extra for the secure WebDAV remote was mistakenly named
`webdav` (singular) while the code and the user‑facing error message refer to the
extra `webdavs` (plural). Because the extra `webdavs` is not listed in
`extras_require`, `pip` reports:

```
dvc 1.10.2 does not provide the extra 'webdavs'
```

Consequently `pip install 'dvc[webdavs]'` does nothing, leaving the required
dependency `webdavclient3` missing and causing the runtime error when a
`webdavs` remote is used.

## Fix
Add the missing `webdavs` entry to the package’s `extras_require` definition
(and keep the existing `webdav` entry for backward compatibility). The change
is made in `setup.cfg`, which is the source of the package metadata for DVC.

```diff
diff --git a/setup.cfg b/setup.cfg
index 7c9e1b2..d3f4a6c 100644
--- a/setup.cfg
+++ b/setup.cfg
@@
 [options.extras_require]
 # Existing extras …
 azure = azure-storage-blob>=12.0.0
 gdrive = pydrive2>=1.8.0
 s3 = boto3>=1.9.0
@@
- webdav = webdavclient3>=1.0.0
+ webdav = webdavclient3>=1.0.0
+ # ``webdavs`` is the name used by the CLI and documentation for the secure
+ # WebDAV remote.  It was unintentionally omitted in v1.10.2, causing
+ # ``pip install 'dvc[webdavs]'`` to fail with “does not provide the extra”.
+ # Keep the original ``webdav`` entry for backward compatibility and add an
+ # alias that points to the same dependency.
+ webdavs = webdavclient3>=1.0.0
 all =
   azure
   gdrive
   s3
   ssh
   oss
   hdfs
   webdav
   webdavs
   # other extras …
```

* The new `webdavs` extra mirrors the existing `webdav` extra, ensuring that
  `pip install 'dvc[webdavs]'` installs `webdavclient3`.
* The `all` extra already includes both names, but we explicitly list `webdavs`
  to make the intent clear.

## How to verify
1. **Re‑install the package** in a clean virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install --upgrade pip
   pip install 'dvc[webdavs]==1.10.2'   # or the newly built wheel
   ```

   The installation should succeed without the “does not provide the extra”
   warning and should pull in `webdavclient3`.

2. **Check that the extra is present**:

   ```bash
   pip show dvc
   # The “Requires” line should now include webdavclient3
   ```

3. **Run a DVC command that uses a WebDAV(S) remote**:

   ```bash
   dvc remote add -d webdavremote webdavs://example.com/repo
   dvc push -r webdavremote
   ```

   The command should no longer abort with the missing‑dependency error; it
   should either succeed or fail only for genuine connection issues, confirming
   that the required library is available.

4. **Run the test suite** (if available) to ensure no regressions:

   ```bash
   pytest -q tests/remotes/test_webdav.py
   ```

   All tests should pass, indicating that the extra works correctly.
--- Proposal B
## Root cause
`dvc` 1.10.2 ships its optional dependencies through *extras* defined in the packaging metadata (``setup.cfg`` / ``pyproject.toml``).  
The extra that enables the WebDAV S remote is **missing** from that metadata – the file only contains an entry called ``webdav`` (singular) while the code and the user‑facing error message refer to the extra ``webdavs`` (plural).  

When `pip install "dvc[webdavs]"` is executed, `pip` looks for an extra named ``webdavs`` in the package’s metadata. Because it cannot find one, it reports:

```
dvc 1.10.2 does not provide the extra 'webdavs'
```

Consequently the required dependency ``webdavclient3`` is never installed, and the remote cannot be used.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/setup.cfg
+++ b/setup.cfg
@@
 [options.extras_require]
 # Existing extras …
 # NOTE: the extra for the WebDAV S remote was mistakenly named ``webdav``.
- webdav = 
-    webdavclient3>=1.0.0
+ # The correct extra name used throughout the code base and documentation is
+ # ``webdavs`` (plural).  Keep the old name for backward compatibility but map it
+ # to the same requirement list.
+ webdav = 
+    webdavclient3>=1.0.0
+ webdavs = 
+    webdavclient3>=1.0.0
```

If the project uses a ``pyproject.toml`` instead of ``setup.cfg`` (newer builds), apply the analogous change:

```diff
--- a/pyproject.toml
+++ b/pyproject.toml
@@
 [project.optional-dependencies]
- webdav = ["webdavclient3>=1.0.0"]
+ # Keep the historic singular name for compatibility.
+ webdav = ["webdavclient3>=1.0.0"]
+ # Add the correctly‑named extra that the CLI expects.
+ webdavs = ["webdavclient3>=1.0.0"]
```

*Explanation of the changes*

1. **Add the missing ``webdavs`` extra** – maps to the same requirement list as the existing (but undocumented) ``webdav`` extra.
2. **Retain the original ``webdav`` extra** – this preserves compatibility for any external scripts that may have used the singular name.
3. No code changes are required; the remote implementation already imports ``webdavclient3`` lazily and will work once the dependency is installed.

## How to verify
1. **Re‑install the package** in a clean virtual environment to ensure the new metadata is used:

   ```bash
   python -m venv /tmp/dvc-test
   source /tmp/dvc-test/bin/activate
   pip install --upgrade pip setuptools wheel
   pip install "dvc[webdavs]==1.10.2"
   ```

2. **Check that the extra is now recognized**:

   ```bash
   pip show dvc | grep -i "Extras"
   # Expected output should contain: Extras: ..., webdavs, ...
   ```

3. **Confirm that the required dependency is installed**:

   ```bash
   pip list | grep webdavclient3
   # Should show webdavclient3 with a version >= 1.0.0
   ```

4. **Run a DVC command that uses the WebDAV S remote**:

   ```bash
   dvc remote add -d webdavremote webdavs://user:pass@host/path
   dvc push -r webdavremote
   ```

   The command should no longer emit the “missing dependencies” error. Instead, it should either succeed or fail for reasons unrelated to missing optional packages (e.g., authentication), confirming that the extra is correctly installed.

5. **Optional regression test** – run the project's test suite (if available) for the WebDAV remote:

   ```bash
   pytest tests/test_remote_webdav.py
   ```

   All tests should pass.

If all the steps above succeed, the issue is resolved.

=== TASK tomerfiliba__plumbum-366 | repo tomerfiliba/plumbum | python
--- Issue
SyntaxError in plumbum/cli/image.py
image.py contains the line

import . as cli

All versions of python I tried (3.6, 3.5, 2.7) raise SyntaxError reading this line. According to the Python Language Reference, such syntax of the import statement is not allowed. What exactly is the purpose of this line? And how to achieve this purpose in legal python?
--- REAL FIX (ground truth)
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

--- Proposal A
## Root cause
`plumbum/cli/image.py` contains the line  

```python
import . as cli
```  

which is not valid Python syntax. The intention was to import the sibling module `cli` (i.e. the package `plumbum.cli`) so that the file can refer to symbols such as `cli.Command`, `cli.Switch`, etc. The correct way to perform a relative import is `from . import cli` (or `from . import cli as cli`, though the alias is unnecessary). Because the illegal syntax is parsed at import time, *any* attempt to import `plumbum.cli.image` raises a `SyntaxError`, breaking the package for all supported Python versions.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
diff --git a/plumbum/cli/image.py b/plumbum/cli/image.py
index 0000000..1111111 100644
--- a/plumbum/cli/image.py
+++ b/plumbum/cli/image.py
@@
-import . as cli
+# The original line used an illegal import syntax (`import . as cli`).
+# The intention is to import the sibling ``cli`` module so that the
+# rest of this file can reference ``cli.Command``, ``cli.Switch`` and
+# other helpers defined in ``plumbum.cli``.
+#
+# A proper relative import is performed with ``from . import cli``.
+# The alias ``as cli`` is unnecessary because the imported name already
+# matches the desired identifier.
+from . import cli
```

*No other changes are required; the rest of the file already refers to the module via the name `cli`.*

## How to verify
1. **Run the test suite**  
   ```bash
   python -m pip install -e .[test]   # install the package in editable mode with test deps
   pytest -q
   ```  
   All tests should now pass without a `SyntaxError` during collection.

2. **Manual import check**  
   ```python
   >>> from plumbum.cli import image
   >>> image  # should show the module object, not raise SyntaxError
   <module 'plumbum.cli.image' from '.../plumbum/cli/image.py'>
   ```

3. **Functional sanity test** (optional) – if `image.py` defines a CLI command, invoke it:  
   ```bash
   python -m plumbum.cli.image --help
   ```  
   The command should display its help text without crashing.

If all three steps succeed, the illegal import has been corrected and the package works across Python 2.7, 3.5, 3.6, and newer versions.
--- Proposal B
## Root cause
`plumbum/cli/image.py` contains the line  

```python
import . as cli
```  

which is not valid Python syntax. The author intended to import the **`plumbum.cli` package** (i.e. its `__init__` module) and bind it to the name `cli` so that the rest of the file can refer to symbols defined in `plumbum.cli.__init__`. Python’s import statement does not allow a bare dot (`.`) to be used as a module name, so every interpreter raises a `SyntaxError` when parsing the file.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/plumbum/cli/image.py
+++ b/plumbum/cli/image.py
@@
-# The original (invalid) import – raises SyntaxError on all Python versions
-import . as cli
+# Import the top‑level ``plumbum.cli`` package and expose it under the name ``cli``.
+# This mirrors the intent of the original (invalid) statement while using legal
+# Python syntax.  An absolute import is used to avoid any ambiguity with relative
+# imports inside the package.
+import plumbum.cli as cli
```

*Explanation of the change*  
- The erroneous line is removed.  
- An explicit absolute import `import plumbum.cli as cli` correctly loads the package’s `__init__` module and binds it to the name `cli`, preserving the original module’s public API usage throughout `image.py`.

If the project prefers relative imports, the equivalent would be:

```python
from .. import cli as cli   # relative to the top‑level ``plumbum`` package
```

but the absolute form is clearer and works regardless of the package’s `__package__` value.

## How to verify
1. **Run the test suite** (if the project provides one):
   ```bash
   python -m pytest
   ```
   All tests should now import `plumbum.cli.image` without raising a `SyntaxError`.

2. **Manual import check**:
   ```bash
   python - <<'PY'
   import plumbum.cli.image
   print("Import succeeded, cli alias:", plumbum.cli.image.cli)
   PY
   ```
   The script should print the module object for `plumbum.cli` and exit without error.

3. **Static analysis** (optional):
   ```bash
   python -m py_compile $(git ls-files '*.py')
   ```
   No syntax errors should be reported.

4. **Runtime behavior**: Execute any example that uses the image CLI (e.g., the `plumbum` command‑line utilities that rely on `plumbum.cli.image`). They should function as before, confirming that the alias `cli` correctly references the package.

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


--- REAL FIX (ground truth)
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
+            merchant_sign_str = merchant_sign.decode("utf-8")
+            return merchant_sign_str == expected_sign

--- Proposal A
## Root cause
`Adyen.util.generate_hpp_sig()` builds the HMAC signature using the **raw HMAC key string** that is supplied by Adyen.  
According to the HPP specification the key is **Base‑64 encoded** and must be decoded before it is used with `hmac.new`.  
Because the library fed the encoded string directly to `hmac.new`, the resulting signature never matched the `additionalData['hmacSignature']` sent by Adyen, causing `is_valid_hmac` to always return `False`.

## Fix (a unified diff with your best‑guess file paths and code)
```diff
diff --git a/Adyen/util.py b/Adyen/util.py
index 3a5c9f2..e7b1d4a 100644
--- a/Adyen/util.py
+++ b/Adyen/util.py
@@
-import hashlib
-import hmac
+import hashlib
+import hmac
+import base64
@@
-def generate_hpp_sig(params, hmac_key):
-    """
-    Generate an HPP signature.
-
-    :param params: dict of parameters that will be sent to the HPP
-    :param hmac_key: the HMAC key provided by Adyen (Base64 encoded)
-    :return: Base64 encoded HMAC signature string
-    """
-    # The order of the fields is defined by Adyen documentation
-    fields = [
-        'merchantAccount',
-        'merchantReference',
-        'paymentAmount',
-        'currencyCode',
-        'shipBeforeDate',
-        'skinCode',
-        'merchantReturnData',
-        'sessionValidity',
-        'shopperEmail',
-        'shopperReference',
-        'recurringContract',
-        'allowedMethods',
-        'blockedMethods',
-        'shopperStatement',
-        'billingAddressType',
-        'deliveryAddressType',
-        'offset',
-    ]
-
-    signing_string = ':'.join([str(params.get(f, '')) for f in fields])
-    hmac_obj = hmac.new(hmac_key.encode('utf-8'), signing_string.encode('utf-8'), hashlib.sha256)
-    return base64.b64encode(hmac_obj.digest()).decode('utf-8')
+def generate_hpp_sig(params, hmac_key):
+    """
+    Generate an HPP signature.
+
+    The HMAC key supplied by Adyen is Base‑64 encoded.  It must be decoded
+    before being used with ``hmac.new``; otherwise the generated signature
+    will not match the one returned by Adyen.
+
+    :param params: dict of parameters that will be sent to the HPP
+    :param hmac_key: the HMAC key provided by Adyen (Base64 encoded string)
+    :return: Base64 encoded HMAC signature string
+    """
+    # The order of the fields is defined by Adyen documentation.
+    # Only the fields that are present in ``params`` are taken; missing
+    # fields contribute an empty string to the signing string.
+    fields = [
+        'merchantAccount',
+        'merchantReference',
+        'paymentAmount',
+        'currencyCode',
+        'shipBeforeDate',
+        'skinCode',
+        'merchantReturnData',
+        'sessionValidity',
+        'shopperEmail',
+        'shopperReference',
+        'recurringContract',
+        'allowedMethods',
+        'blockedMethods',
+        'shopperStatement',
+        'billingAddressType',
+        'deliveryAddressType',
+        'offset',
+    ]
+
+    # Build the string that will be signed – values are concatenated with ':'
+    signing_string = ':'.join([str(params.get(f, '')) for f in fields])
+
+    # Decode the Base64‑encoded key **once** before using it with HMAC.
+    # ``hmac_key`` may already be a ``bytes`` object (Python 2) or a
+    # ``str`` (Python 3).  ``base64.b64decode`` handles both.
+    decoded_key = base64.b64decode(hmac_key)
+
+    # Compute the HMAC‑SHA256 digest.
+    hmac_obj = hmac.new(decoded_key, signing_string.encode('utf-
--- Proposal B
## Root cause  

`generate_hpp_sig()` (and the related `is_valid_hmac()`) built the string that is signed and the secret key using the default *text* type.  
In Python 2 the default type is `unicode` for values that come from JSON, while the HMAC routine expects **bytes**.  
The function therefore signed a *unicode* string while the secret key was a *str* (or vice‑versa).  
The resulting binary HMAC differed from the one that Adyen generates, so the signature never matched the `additionalData['hmacSignature']` sent in the notification.

## Fix  

```diff
--- a/adyen/util.py
+++ b/adyen/util.py
@@
-def generate_hpp_sig(params, secret):
-    """
-    Generate an HPP signature.
-
-    :param params: dict of parameters that will be sent to the HPP page
-    :param secret: the HPP secret (skin secret)
-    :return: base64 encoded HMAC SHA256 signature
-    """
-    # sort the parameters alphabetically and concatenate the values with ':'
-    signing_string = ':'.join([str(params[k]) for k in sorted(params)])
-    hmac_obj = hmac.new(secret, signing_string, hashlib.sha256)
-    return base64.b64encode(hmac_obj.digest())
+def _to_bytes(value):
+    """
+    Helper that guarantees a *bytes* object on both Python 2 and Python 3.
+    """
+    if isinstance(value, bytes):
+        return value
+    # ``unicode`` on Python 2, ``str`` on Python 3
+    return value.encode('utf-8')
+
+
+def generate_hpp_sig(params, secret):
+    """
+    Generate an HPP signature.
+
+    The HPP signature is a Base64‑encoded HMAC‑SHA256 of the concatenated
+    parameter values (sorted alphabetically) using the *skin secret* as key.
+
+    The original implementation mixed ``unicode`` and ``str`` objects which
+    caused an incorrect binary HMAC on Python 2.  This version normalises
+    everything to UTF‑8 encoded *bytes* before feeding it to ``hmac``.
+
+    :param params: dict of parameters that will be sent to the HPP page
+    :param secret: the HPP secret (skin secret)
+    :return: base64 encoded HMAC SHA256 signature (as a ``str``)
+    """
+    # 1️⃣  Sort the keys alphabetically (as required by the spec)
+    sorted_keys = sorted(params)
+
+    # 2️⃣  Build the signing string – values are converted to ``str`` first
+    #     (to keep the behaviour identical to the reference implementation)
+    signing_string = ':'.join([str(params[k]) for k in sorted_keys])
+
+    # 3️⃣  Convert both the signing string and the secret to ``bytes``.
+    signing_bytes = _to_bytes(signing_string)
+    secret_bytes = _to_bytes(secret)
+
+    # 4️⃣  Compute the HMAC‑SHA256 and Base64‑encode the result.
+    hmac_obj = hmac.new(secret_bytes, signing_bytes, hashlib.sha256)
+    # ``base64.b64encode`` returns ``bytes`` on both Py2 and Py3 – decode to ``str``.
+    return base64.b64encode(hmac_obj.digest()).decode('utf-8')
 
 
-def is_valid_hmac(params, secret, hmac_to_check):
-    """
-    Validate an HMAC signature.
-
-    :param params: dict of parameters received from the HPP page
-    :param secret: the HPP secret (skin secret)
-    :param hmac_to_check: the HMAC signature to validate
-    :return: True if the signature is valid, False otherwise
-    """
-    generated_hmac = generate_hpp_sig(params, secret)
-    return generated_hmac == hmac_to_check
+def is_valid_hmac(params, secret, hmac_to_check):
+    """
+    Validate an HMAC signature generated by ``generate_hpp_sig``.
+
+    The function now uses the same byte‑normalisation logic as the generator,
+    guaranteeing t

=== TASK zulip__zulip-terminal-1463 | repo zulip/zulip-terminal | python
--- Issue
Support server-customized presence parameters
This is a fairly small migration in Zulip 7.0, server feature level 164 (https://zulip.com/api/changelog).

Quoting the changelog entry:
> [POST /register](https://zulip.com/api/register-queue): Added the server_presence_ping_interval_seconds and server_presence_offline_threshold_seconds fields for clients to use when implementing the [presence](https://zulip.com/api/get-presence) system.

Instead of hard-coded constants, we should therefore use these values provided by the server, falling back to the original constants when the server version is lower (when the keys in the response to register-queue are empty).

If tests are added or updated, note that they should likely be parametrized over different feature levels - see other tests which do so for comparisons.
--- REAL FIX (ground truth)
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
                     view.middle_column.update_message_list_status_markers()
-            time.sleep(60)
+            time.sleep(self.server_presence_ping_interval_secs)
 
     @asynch
     def toggle_message_reaction(
@@ -814,6 +815,18 @@ class Model:
             TYPING_STARTED_EXPIRY_PERIOD,
         )
 
+    def _store_server_presence_intervals(self) -> None:
+        """
+        In ZFL 164, these values were added to the register response.
+        Uses default values if not received.
+        """
+        self.server_presence_offline_threshold_secs = self.initial_data.get(
+            "server_presence_offline_threshold_seconds", PRESENCE_OFFLINE_THRESHOLD_SECS
+        )
+        self.server_presence_ping_interval_secs = self.initial_data.get(
+            "server_presence_ping_interval_seconds", PRESENCE_PING_INTERVAL_SECS
+        )
+
     @staticmethod
     def modernize_message_response(message: Message) -> Message:
         """
@@ -1202,7 +1215,7 @@ class Model:
                 *
                 * Out of the Clien
--- Proposal A
## Root cause
`zulip-terminal` uses hard‑coded constants for the presence ping interval and the offline‑threshold timeout.  
Since Zulip 7.0 the server can send the values `server_presence_ping_interval_seconds` and `server_presence_offline_threshold_seconds` in the response to **POST /register**.  
Because the client never reads these fields it always uses the old defaults, breaking compatibility with servers that customise the presence parameters (feature level 164+).

## Fix
* Read the two new fields from the *register‑queue* response and store them on the `ZulipClient` instance.
* Fall back to the original defaults when the fields are missing (i.e. older servers).
* Make the `PresenceManager` use the values from the client instead of the hard‑coded constants.
* Add sensible default constants in `presence.py` so they can be reused by tests and other modules.

```diff
--- a/zulipterminal/client.py
+++ b/zulipterminal/client.py
@@
 from .presence import PresenceManager, DEFAULT_PRESENCE_PING_INTERVAL, \
-    DEFAULT_PRESENCE_OFFLINE_THRESHOLD
+    DEFAULT_PRESENCE_OFFLINE_THRESHOLD
@@
 class ZulipClient:
@@
     def __init__(self, ...):
         ...
-        self.presence_manager = PresenceManager(self)
+        # Presence parameters are filled after a successful register request.
+        # Initialise with the library defaults so that the manager can be
+        # constructed even before we have talked to the server.
+        self.server_presence_ping_interval_seconds = DEFAULT_PRESENCE_PING_INTERVAL
+        self.server_presence_offline_threshold_seconds = DEFAULT_PRESENCE_OFFLINE_THRESHOLD
+
+        self.presence_manager = PresenceManager(self)
@@
     def register_queue(self, **kwargs):
         """
         Register for a queue and return the JSON response.
         """
@@
-        response = self.call_endpoint('POST', '/register', **kwargs)
+        response = self.call_endpoint('POST', '/register', **kwargs)
         if response.get('result') != 'success':
             raise ZulipError(response)
 
-        # Store the queue id for later use.
-        self.queue_id = response['queue_id']
+        # Store the queue id for later use.
+        self.queue_id = response['queue_id']
+
+        # ------------------------------------------------------------------
+        # Presence configuration (Zulip 7.0 / feature level 164)
+        # ------------------------------------------------------------------
+        # The server may provide custom values for the presence ping interval
+        # and the offline‑threshold.  If they are missing (older servers) we
+        # keep the defaults defined in `presence.py`.
+        self.server_presence_ping_interval_seconds = response.get(
+            'server_presence_ping_interval_seconds',
+            DEFAULT_PRESENCE_PING_INTERVAL,
+        )
+        self.server_presence_offline_threshold_seconds = response.get(
+            'server_presence_offline_threshold_seconds',
+            DEFAULT_PRESENCE_OFFLINE_THRESHOLD,
+        )
 
         return response
*** End of File
--- a/zulipterminal/presence.py
+++ b/zulipterminal/presence.py
@@
-# Default values used when the server does not provide custom settings.
-DEFAULT_PRESENCE_PING_INTERVAL = 60          # seconds
-DEFAULT_PRESENCE_OFFLINE_THRESHOLD = 300    # seconds
+# Default values used when the server does not provide custom settings.
+# These match the historic hard‑coded values in zulip‑terminal.
+DEFAULT_PRESENCE_PING_INTERVAL = 60    
--- Proposal B
## Root cause
`zulip-terminal` used hard‑coded constants for the presence subsystem:

```python
PING_INTERVAL = 60          # seconds
OFFLINE_THRESHOLD = 140     # seconds
```

Starting with Zulip 7.0 (feature level 164) the server returns the values
`server_presence_ping_interval_seconds` and
`server_presence_offline_threshold_seconds` in the *register‑queue* payload.
Because the client never reads these fields it always uses the defaults,
breaking compatibility with installations that customise the presence
parameters.

## Fix
* Read the two fields from the register‑queue response (already stored in
  `self.model.initial_data`).
* Fall back to the historic defaults when the fields are missing or `None`
  (i.e. when connected to an older server).
* Replace the hard‑coded constants with properties that return the
  appropriate value.

```diff
diff --git a/zulipterminal/presence.py b/zulipterminal/presence.py
index e3b1c2d..a7f4e9b 100644
--- a/zulipterminal/presence.py
+++ b/zulipterminal/presence.py
@@
-from typing import Any, Callable, Dict, List, Optional, Tuple
+from typing import Any, Callable, Dict, List, Optional, Tuple
 
 import urwid
@@
-# Hard‑coded defaults (pre‑Zulip 7.0 behaviour)
-PING_INTERVAL = 60          # seconds
-OFFLINE_THRESHOLD = 140     # seconds
-
-# The interval at which we send a presence ping to the server.
-# This is a global timer started by PresenceManager.start().
-DEFAULT_PING_INTERVAL = PING_INTERVAL
-
-# The amount of time after the last ping after which we consider the
-# user offline.  This is used by PresenceManager._is_user_online().
-DEFAULT_OFFLINE_THRESHOLD = OFFLINE_THRESHOLD
+# ----------------------------------------------------------------------
+# Presence timing parameters – server‑customisable in Zulip 7.0+
+# ----------------------------------------------------------------------
+#
+# The server may provide the following fields in the response to
+# ``/register`` (see Zulip changelog entry for feature level 164):
+#
+#   * ``server_presence_ping_interval_seconds``
+#   * ``server_presence_offline_threshold_seconds``
+#
+# Older servers do not include these keys, so we fall back to the historic
+# defaults (60 s ping interval, 140 s offline threshold).  The values are
+# obtained from the model's ``initial_data`` dictionary, which already
+# contains the parsed register‑queue payload.
+
+def _presence_ping_interval(model: "Model") -> int:
+    """
+    Return the presence ping interval (seconds) reported by the server,
+    or the historic default (60) if the server does not provide it.
+    """
+    # ``model.initial_data`` is populated once during start‑up from the
+    # ``/register`` endpoint.
+    return int(
+        model.initial_data.get("server_presence_ping_interval_seconds", 60) or 60
+    )
+
+
+def _presence_offline_threshold(model: "Model") -> int:
+    """
+    Return the presence offline‑threshold (seconds) reported by the server,
+    or the historic default (140) if the server does not provide it.
+    """
+    return int(
+        model.initial_data.get("server_presence_offline_threshold_seconds", 140)
+        or 140
+    )
+
+# The following two helpers expose the values in a way that existing code
+# can keep using the same attribute names without being aware of the
+# underlying change.
+def get_default_ping_interval(model: "Model") -> int:
+    return _presence_ping_interval(model)
+
+
+def get_default_offline_threshold(model: "Model") -> int:
+    return _presence