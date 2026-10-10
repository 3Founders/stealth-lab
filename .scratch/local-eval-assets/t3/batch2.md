

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
**## Root cause**

`dvc.api.params_show` forwards the request to `Repo.params.show`.  
`Repo.params.show` contained a guard that, when the *target* parameter file was
different from the repository’s default `params.yaml`, immediately returned the
raw contents of the file **without** taking `stages` or `deps` into account:

```python
if path != self.repo.params_file:
    # external params file – ignore stage‑related filtering
    return params
```

Consequently, calling

```python
dvc.api.params_show("params_foo.yaml", stages="prepare", deps=True)
```

always returned the whole file, because the `deps=True` flag was silently
discarded for non‑default targets.

**## Fix (a unified diff with your best‑guess file paths and code)**

```diff
--- a/dvc/repo/params.py
+++ b/dvc/repo/params.py
@@
 class Params:
     def __init__(self, repo):
         self.repo = repo
 
-    def show(self, path: Optional[str] = None,
-             stages: Optional[Union[str, List[str]]] = None,
-             deps: bool = False) -> Dict[str, Any]:
-        """
-        Show parameters from a params file.
-
-        If *stages* is provided the result is filtered to the parameters that
-        belong to the given stage(s).  When *deps* is True only the parameters
-        that are declared as dependencies of the stage(s) are kept.
-        """
-        if path is None:
-            path = self.repo.params_file
-
-        # Load the whole params file.
-        params = self.repo.fs.read_yaml(path) or {}
-
-        # When the user asks for a non‑default params file we historically
-        # returned the raw contents, ignoring *stages* and *deps*.
-        # This behaviour broke `params_show(..., stages=..., deps=True)` for
-        # external files.  The guard is therefore removed – we always apply
-        # the stage‑based filtering regardless of the file location.
-
-        if stages is None:
-            return params
-
-        # Normalise *stages* to a list.
-        if isinstance(stages, str):
-            stages = [stages]
-
-        # Resolve the set of parameter keys that are dependencies of the
-        # requested stages.
-        stage_param_keys = self._collect_stage_param_keys(stages)
-
-        if deps:
-            # Keep only parameters that are *declared* as dependencies of the
-            # stages.
-            filtered = {k: v for k, v in params.items() if k in stage_param_keys}
-        else:
-            # When deps=False we keep the same set – historically the API
-            # behaved this way, and tests rely on it.
-            filtered = {k: v for k, v in params.items() if k in stage_param_keys}
-
-        return filtered
+    def show(self, path: Optional[str] = None,\n+             stages: Optional[Union[str, List[str]]] = None,\n+             deps: bool = False) -> Dict[str, Any]:\n+        \"\"\"Return parameters from *path* optionally filtered by *stages*.\n+\n+        Parameters\n+        ----------\n+        path:\n+            Path to a ``.yaml``/``.yml`` file containing parameters.  If omitted\n+            the repository's default ``params.yaml`` is used.\n+        stages:\n+            Name or list of stage names defined in ``dvc.yaml``.  When provided\n+            the result is limited to the parameters that are *used* by those\n+            stages.\n+        deps:\n+            If ``True`` only parameters that are declared as *dependencies* of\n+            the requested stages are returned.  When ``False`` the same set is

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
`cashews` builds cache keys by concatenating the function name with the string representation of each argument.  
During this process every string argument is **lower‑cased** unconditionally (see the helper that normalises key parts).  
The library never exposed a way to change this behaviour, so users cannot keep the original case of their arguments.  
Because the lower‑casing is hard‑coded, the feature cannot be disabled without changing the source.

## Fix (a unified diff with your best‑guess file paths and code)
```diff
--- a/cashews/decorator.py
+++ b/cashews/decorator.py
@@
-def cache(
-    ttl: Optional[timedelta] = None,
-    key: Optional[str] = None,
-    tags: Optional[Iterable[str]] = None,
-    backend: Optional[str] = None,
-    serializer: Optional[BaseSerializer] = None,
-    namespace: Optional[str] = None,
-    **decorator_kwargs,
-):
+def cache(
+    ttl: Optional[timedelta] = None,
+    key: Optional[str] = None,
+    tags: Optional[Iterable[str]] = None,
+    backend: Optional[str] = None,
+    serializer: Optional[BaseSerializer] = None,
+    namespace: Optional[str] = None,
+    *,
+    case_sensitive: bool = True,
+    **decorator_kwargs,
+):
@@
-    def wrapper(*args, **kwargs):
-        # Build the cache key
-        cache_key = _make_key(func, args, kwargs, key=key, namespace=namespace)
+    def wrapper(*args, **kwargs):
+        # Build the cache key – honour the ``case_sensitive`` flag
+        cache_key = _make_key(
+            func,
+            args,
+            kwargs,
+            key=key,
+            namespace=namespace,
+            case_sensitive=case_sensitive,
+        )
@@
-    return wrapper
+    return wrapper
@@
-def _make_key(
-    func: Callable,
-    args: Tuple[Any, ...],
-    kwargs: Dict[str, Any],
-    *,
-    key: Optional[str] = None,
-    namespace: Optional[str] = None,
-) -> str:
+def _make_key(
+    func: Callable,
+    args: Tuple[Any, ...],
+    kwargs: Dict[str, Any],
+    *,
+    key: Optional[str] = None,
+    namespace: Optional[str] = None,
+    case_sensitive: bool = True,
+) -> str:
@@
-    parts = [func.__module__, func.__qualname__]
-    for arg in args:
-        parts.append(_normalize_key_part(arg))
-    for k, v in sorted(kwargs.items()):
-        parts.append(f"{k}:{_normalize_key_part(v)}")
+    parts = [func.__module__, func.__qualname__]
+    for arg in args:
+        parts.append(_normalize_key_part(arg, case_sensitive=case_sensitive))
+    for k, v in sorted(kwargs.items()):
+        parts.append(f"{k}:{_normalize_key_part(v, case_sensitive=case_sensitive)}")
@@
-    if namespace:
-        parts.insert(0, namespace)
-    if key:
-        parts.append(key)
-    return ":".join(parts)
+    if namespace:
+        parts.insert(0, namespace)
+    if key:
+        parts.append(key)
+    return ":".join(parts)
@@
-def _normalize_key_part(value: Any, case_sensitive: bool = True) -> str:
-    """
-    Convert a value to a string suitable for inclusion in a cache key.
-
-    Strings are lower‑cased to make the cache case‑insensitive.
-    """
-    if isinstance(value, str):
-        return value.lower()
-    return str(value)
+def _normalize_key_part(value: Any, case_sensitive: bool = True) -> str:
+    """
+    Convert a value to a string suitable for inclusion in a cache key.
+
+    If *case_sensitive* is ``False`` (the historic behaviour) string values are
+    lower‑cased.  When *case_sensitive* is ``True`` the original string is kept
+    unchanged.

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

=== TASK modin-project__modin-7395 | repo modin-project/modin | python
--- Issue
BUG: Series.duplicated doesn't preserve Series name
### Modin version checks

- [X] I have checked that this issue has not already been reported.

- [X] I have confirmed this bug exists on the latest released version of Modin.

- [ ] I have confirmed this bug exists on the main branch of Modin. (In order to do this you can follow [this guide](https://modin.readthedocs.io/en/stable/getting_started/installation.html#installing-from-the-github-main-branch).)


### Reproducible Example

```python
In [1]: import modin.pandas as pd

In [2]: pd.Series([1,2,3], name='a').duplicated()
Out[2]: 
0    False
1    False
2    False
dtype: bool

In [3]: pd.Series([1,2,3], name='a').duplicated().name
```


### Issue Description

The name is not preserved

I noticed this in [Narwhals](https://github.com/narwhals-dev/narwhals/pull/870)

### Expected Behavior

pandas preserves the name:
```
In [1]: import pandas as pd

In [2]: pd.Series([1,2,3], name='a').duplicated().name
Out[2]: 'a'
```

### Error Logs

<details>

```python-traceback

Replace this line with the error backtrace (if applicable).

```

</details>


### Installed Versions

<details>

INSTALLED VERSIONS
------------------
commit                : c8bbca8e4e00c681370e3736b2f73bb0352408c3
python                : 3.11.9.final.0
python-bits           : 64
OS                    : Linux
OS-release            : 5.15.153.1-microsoft-standard-WSL2
Version               : #1 SMP Fri Mar 29 23:14:13 UTC 2024
--- REAL FIX (ground truth)
diff --git a/modin/pandas/series.py b/modin/pandas/series.py
index d18a0bec..00083200 100644
--- a/modin/pandas/series.py
+++ b/modin/pandas/series.py
@@ -1022,7 +1022,12 @@ class Series(BasePandasDataset):
         """
         Indicate duplicate Series values.
         """
-        return self.to_frame().duplicated(keep=keep)
+        name = self.name
+        result = self.to_frame().duplicated(keep=keep)
+        # DataFrame.duplicated drops the name, so we need to manually restore it
+        if name is not None:
+            result.name = name
+        return result
 
     def eq(
         self, other, level=None, fill_value=None, axis=0

--- Proposal A
## Root cause
`Series.duplicated` in Modin delegates the operation to pandas via `_default_to_pandas`.  
The helper returns a **new** `Series` object but the implementation does not copy the
original series’ ``name`` attribute to the result. Consequently the returned series
has ``name = None`` while pandas correctly preserves the name.

## Fix
Add explicit propagation of the ``name`` attribute in the Modin implementation of
`Series.duplicated`.

```diff
--- a/modin/pandas/series.py
+++ b/modin/pandas/series.py
@@
     def duplicated(self, keep: str = "first") -> "Series":
         """
         Return Boolean Series denoting duplicate values.
@@
-        return self._default_to_pandas(pandas.Series.duplicated, keep=keep)
+        # NOTE: ``_default_to_pandas`` returns a new Series object that does **not**
+        # automatically inherit the ``name`` attribute from ``self``.  Pandas
+        # preserves the name, so we copy it explicitly to keep Modin behaviour
+        # consistent with pandas.
+        result = self._default_to_pandas(pandas.Series.duplicated, keep=keep)
+        # ``result`` is already a Modin ``Series`` (the wrapper created by
+        # ``_default_to_pandas``).  Propagate the original name if it is set.
+        if self.name is not None:
+            result.name = self.name
+        return result
```

### Explanation of the changes
1. Call the original pandas implementation via `_default_to_pandas` to obtain the
   duplicated boolean series.
2. After the call, explicitly set `result.name` to `self.name` when the original
   series has a name. This mirrors pandas’ behaviour.
3. Return the corrected `result`.

## How to verify
Run the following snippet both with the original Modin version and after applying the
patch:

```python
import modin.pandas as pd

# Series with a name
s = pd.Series([1, 2, 3, 2], name="a")
dup = s.duplicated()
print(dup)               # should be a boolean Series
print(dup.name)          # should output 'a'
```

Expected output:

```
0    False
1    False
2    False
3     True
dtype: bool
a
```

The test should also pass the equivalent pandas check:

```python
import pandas as pd
assert pd.Series([1,2,3,2], name="a").duplicated().name == "a"
```

Running the Modin test suite (e.g., `pytest modin/pandas/test_series.py::test_duplicated_name`)
should now succeed, confirming that the name is correctly preserved.
--- Proposal B
## Root cause
`Series.duplicated` is implemented via the generic `_default_to_pandas` helper.  
When the helper builds the resulting `Series` it uses the generic constructor
`self.__constructor__(result)`, which creates a new `Modin` `Series` from the
underlying pandas object **without copying the original series metadata** such
as the `name`. Consequently the boolean series returned by `duplicated()` has
its `name` set to `None`, diverging from pandas’ behaviour where the name is
preserved.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/modin/pandas/series.py
+++ b/modin/pandas/series.py
@@
 class Series(BasePandasDataset, pandas.core.generic.NDFrame):
@@
     def duplicated(self, keep: str = "first") -> "Series":
         """
         Return boolean Series denoting duplicate values.
@@
-        return self._default_to_pandas(pandas.Series.duplicated, keep=keep)
+        # NOTE: ``_default_to_pandas`` builds a new ``Series`` from the pandas
+        # result but does **not** propagate the original series metadata
+        # (e.g. ``name``).  Pandas preserves the name for ``duplicated``; we
+        # replicate that behaviour by explicitly copying the name after the
+        # result is materialised.
+        result = self._default_to_pandas(pandas.Series.duplicated, keep=keep)
+
+        # ``result`` can be either a pandas Series (fallback path) or a Modin
+        # Series (normal path).  In both cases we can set the ``name`` attribute
+        # directly – pandas objects expose a mutable ``name`` attribute and
+        # Modin’s ``Series`` forwards attribute access to the underlying pandas
+        # object.
+        if isinstance(result, pandas.Series):
+            # pandas fallback – set the name on the pandas object
+            result.name = self.name
+        else:
+            # Modin Series – the property setter updates the underlying pandas
+            # series, preserving the expected semantics.
+            result.name = self.name
+
+        return result
```

### Explanation of the changes
1. **Call the original implementation** via `_default_to_pandas` to obtain the
   boolean series.
2. **Explicitly copy the original `name`** onto the result, handling both the
   pandas‑fallback path (`result` is a `pandas.Series`) and the normal Modin
   path (`result` is a `modin.pandas.Series`).  
   Setting `result.name` works for both because Modin forwards attribute
   access to the underlying pandas object.
3. Return the corrected series.

## How to verify
```python
import modin.pandas as pd
import pandas as pd_original

# 1. Basic sanity check – name is preserved
s = pd.Series([1, 2, 3, 2], name="my_name")
dup = s.duplicated()
assert dup.name == "my_name", "Modin duplicated lost the name"

# 2. Compare with pandas behaviour
s_pd = pd_original.Series([1, 2, 3, 2], name="my_name")
dup_pd = s_pd.duplicated()
assert dup_pd.name == "my_name", "Pandas duplicated does not preserve name (should not happen)"
assert dup.equals(pd.Series(dup_pd, name="my_name")), "Result values differ"

# 3. Ensure the fix works for the fallback path (e.g., when Modin falls back to pandas)
# Force fallback by using an unsupported dtype or operation that triggers the fallback.
# Here we simulate by directly calling the pandas implementation:
fallback_result = s._default_to_pandas(pd_original.Series.duplicated, keep="first")
assert fallback_result.name == "my_name", "Fallback path lost the name"

print("All verification che

=== TASK dwavesystems__dwave-cloud-client-455 | repo dwavesystems/dwave-cloud-client | python
--- Issue
Event dispatch decorator
Call `dispatch_event` before and after decorated function execution, automating args and rval collection/reporting.
--- REAL FIX (ground truth)
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
     def get_solvers(self, refresh=False, order_by='avg_load', **filters):
         """Return a filtered list of solvers handled by this client.
 
@@ -913,9 +909,6 @@ class Client(object):
             )
         """
 
-        args = dict(refresh=refresh, order_by=order_by, filters=filters)
-        dispatch_event('before_get_solvers', obj=self, args=args)
-
         def covers_op(prop, val):
             """Does LHS `prop` (range) fully cover RHS `val` (range or item)?"""
 
@@ -1064,9 +1057,6 @@ class Client(object):
         if sort_reverse:
             solvers.reverse()
 
-        dispatch_event(
-            'after_get_solvers', obj=self, args=args, return_value=solvers)
-
         return solvers
 
     def solvers(self, refresh=False, **filters):
diff --git a/dwave/cloud/events.py b/dwave/cloud/events.py
index 8e9b53d..66a0a39 100644
--- a/dwave/cloud/events.py
+++ b/dwave/cloud/events.py
@@ -19,6 +19,8 @@ inspection.
 """
 
 import logging
+import inspect
+from functools import wraps
 
 __all__ = ['add_handler']
 
@@ -41,13 +
--- Proposal A
## Root cause
The library provides a low‑level `dispatch_event` function that manually sends events, but there is no convenient way to automatically fire “before” and “after” events around a function call.  Users have to sprinkle explicit `dispatch_event` calls throughout the code, which is error‑prone and leads to duplicated boiler‑plate.  The missing decorator is the reason the reported feature (“Event dispatch decorator – Call `dispatch_event` before and after decorated function execution, automating args and rval collection/reporting”) does not exist.

## Fix
Add a reusable decorator `dispatch_event_around` that automatically calls `dispatch_event` with a *before* and *after* event name, passing the original arguments and, for the after‑event, the return value.  The implementation lives in the existing event utilities module (`dwave/cloud/client/events.py`).  The decorator is deliberately simple, uses `functools.wraps`, and does not interfere with the wrapped function’s signature.

```diff
--- a/dwave/cloud/client/events.py
+++ b/dwave/cloud/client/events.py
@@
 import logging
 from typing import Any, Callable, Dict, Tuple
 
@@
 def dispatch_event(event_name: str, payload: Dict[str, Any] = None) -> None:
@@
     if payload is None:
         payload = {}
     _LOGGER.debug("Dispatching event %s with payload %s", event_name, payload)
     # The real implementation forwards the payload to the configured event sink.
     # Here we just log it for illustration.
     _LOGGER.info("Event %s: %s", event_name, payload)
+
+# -------------------------------------------------------------------------
+# Event‑dispatching decorator
+# -------------------------------------------------------------------------
+def dispatch_event_around(
+    base_event_name: str,
+    *,
+    before_suffix: str = "_before",
+    after_suffix: str = "_after",
+) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
+    """
+    Decorator that automatically dispatches *before* and *after* events around a
+    function call.
+
+    Parameters
+    ----------
+    base_event_name:
+        The core name of the event.  The decorator will emit two events:
+        ``f"{base_event_name}{before_suffix}"`` before the wrapped function is
+        executed and ``f"{base_event_name}{after_suffix}"`` after it returns.
+    before_suffix, after_suffix:
+        Optional suffixes that can be customised if a different naming scheme
+        is required.
+
+    The *before* event payload contains the positional ``args`` and keyword
+    ``kwargs`` that will be passed to the wrapped function.  The *after* event
+    payload contains the same ``args``/``kwargs`` plus the ``result`` key with
+    the function’s return value.
+    """
+    import functools
+
+    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
+        @functools.wraps(func)
+        def wrapper(*args: Tuple[Any, ...], **kwargs: Dict[str, Any]) -> Any:
+            # Dispatch the “before” event
+            before_event = f"{base_event_name}{before_suffix}"
+            dispatch_event(
+                before_event,
+                payload={"args": args, "kwargs": kwargs},
+            )
+
+            # Execute the original function
+            result = func(*args, **kwargs)
+
+            # Dispatch the “after” event, including the result
+            after_event = f"{base_event_name}{after_suffix}"
+            dispatch_event(
+                after_event,
+                payload={"args": args, "
--- Proposal B
## Root cause
The client currently calls `self.dispatch_event(...)` manually before and after many public methods. This leads to duplicated boiler‑plate code and makes it easy to forget the “after” call, causing inconsistent event reporting. A decorator that automatically performs the pre‑ and post‑dispatch would centralise the logic and guarantee that every wrapped method reports both phases.

## Fix
Add a reusable **event‑dispatch decorator** that:
* Takes the event name (or a callable that returns it) as an argument.
* Calls `self.dispatch_event(event_name, phase="pre", args=args, kwargs=kwargs)` before the wrapped function runs.
* Calls `self.dispatch_event(event_name, phase="post", args=args, kwargs=kwargs, result=result)` after the function returns.
* Returns the original result unchanged.
* Propagates any exception raised by the wrapped function (the “post” dispatch is skipped in that case – this mirrors the previous manual behaviour).

The decorator lives in `dwave/cloud/events.py` and is exported from `dwave/cloud/__init__.py` for easy import.

```diff
--- a/dwave/cloud/events.py
+++ b/dwave/cloud/events.py
@@
@@
-# Existing content (if any) …
+"""
+Event‑dispatch utilities for the D‑Wave Cloud client.
+
+This module provides a decorator, :func:`dispatch_event`, that automates the
+pattern of emitting a *pre* and *post* event around a method call.  The
+decorated method must be an instance method whose first argument is ``self`` and
+that ``self`` implements a ``dispatch_event(event_name, **payload)`` method.
+
+Typical usage::
+
+    from dwave.cloud.events import dispatch_event
+
+    class MyClient:
+        def dispatch_event(self, name, **payload):
+            # user‑defined handling …
+            pass
+
+        @dispatch_event("my_operation")
+        def my_operation(self, x, y):
+            return x + y
+
+When ``my_operation`` is called, the client will receive two events:
+``my_operation`` with ``phase="pre"`` and ``my_operation`` with ``phase="post"``,
+the latter also containing the ``result`` key.
+"""
+
+from __future__ import annotations
+
+import functools
+from typing import Any, Callable, Coroutine, TypeVar, Union, overload
+
+F = TypeVar("F", bound=Callable[..., Any])
+
+
+def _call_dispatch(
+    obj: Any,
+    event_name: str,
+    phase: str,
+    args: tuple[Any, ...],
+    kwargs: dict[str, Any],
+    result: Any = None,
+) -> None:
+    """
+    Helper that forwards the event to ``obj.dispatch_event``.
+
+    Parameters
+    ----------
+    obj:
+        The instance on which ``dispatch_event`` is defined.
+    event_name:
+        Name of the event to emit.
+    phase:
+        Either ``"pre"`` or ``"post"``.
+    args, kwargs:
+        Positional and keyword arguments passed to the wrapped method (excluding
+        ``self`` which is already bound to *obj*).
+    result:
+        The return value of the wrapped method; only supplied for the ``post``
+        phase.
+    """
+    payload: dict[str, Any] = {
+        "phase": phase,
+        "args": args,
+        "kwargs": kwargs,
+    }
+    if phase == "post":
+        payload["result"] = result
+
+    # ``dispatch_event`` is part of the public client API; we deliberately do not
+    # catch exceptions here – any failure should surface to the caller.
+    obj.dispatch_event(event_name, **payload)
+
+
+@overload
+def dispatch_event(event_name: str) -> Callable[[F], F]:
+    ...
+
+@overload
+def dispatch_event(event_name: Callable[[Any], str]) -> 

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
`isort` removes a back‑slash line‑continuation (`\`) by simply deleting the backslash and the following newline.  
The indentation (tabs or spaces) that appears on the next physical line is also stripped, **but no whitespace is inserted** between the tokens that were separated by that indentation.  

When the continuation line starts with the `import` keyword, the missing space causes the keyword and the first imported name to be concatenated:

```
from sys \          # backslash + newline removed
    import version  # indentation removed, no space inserted
=> "from sys importversion"
```

Thus the parser produces `importversion` instead of `import version`.

## Fix
Add a dedicated preprocessing step that replaces a back‑slash continuation **together with any following whitespace (including tabs)** by a **single space** before the source is tokenised.  
The change is made in `isort/parse.py` where the source line is normalised.

```diff
--- a/isort/parse.py
+++ b/isort/parse.py
@@
 import tokenize
 import re
 from typing import List, Tuple
 
+# ---------------------------------------------------------------------------
+# Helper: collapse back‑slash line continuations
+# ---------------------------------------------------------------------------
+_LINE_CONTINUATION_RE = re.compile(r'\\\s*\n\s*')
+
+def _collapse_line_continuations(source: str) -> str:
+    """
+    Replace a back‑slash line continuation (``\\``) together with any
+    whitespace that follows the newline (spaces or tabs) by a single space.
+
+    This guarantees that tokens which were separated by the indentation on the
+    continuation line stay separated after the continuation is removed.
+    """
+    # ``\\`` + optional spaces/tabs + newline + optional spaces/tabs  →  " "
+    return _LINE_CONTINUATION_RE.sub(' ', source)
+
@@
-def _tokenize_source(source: str) -> List[tokenize.TokenInfo]:
-    \"\"\"Tokenize *source* and return a list of TokenInfo objects.\"\"\"
-    return list(tokenize.generate_tokens(StringIO(source).readline))
+def _tokenize_source(source: str) -> List[tokenize.TokenInfo]:
+    \"\"\"Tokenize *source* and return a list of TokenInfo objects.\n+\n+    The source is first normalised to collapse back‑slash continuations so that\n+    indentation on the continuation line does not disappear without leaving a\n+    separating space.\n+    \"\"\n+    # Normalise line continuations before tokenisation.\n+    source = _collapse_line_continuations(source)\n+    return list(tokenize.generate_tokens(StringIO(source).readline))
```

### What the patch does
1. **Regex `_LINE_CONTINUATION_RE`** matches a back‑slash, any surrounding whitespace, the newline, and any indentation that follows it.
2. **`_collapse_line_continuations`** replaces the whole match with a single space, preserving token separation.
3. The tokenisation routine (`_tokenize_source`) now calls this helper before feeding the string to `tokenize.generate_tokens`.
4. No other part of the code needs to change – the rest of the parsing/formatting pipeline works on the already‑corrected source.

## How to verify
1. **Add a regression test** (e.g. in `tests/test_line_continuation.py`):

```python
def test_backslash_continuation_with_tab():
    src = "from sys \\\n\timport version\nprint(version)\n"
    expected = "from sys import version\n\nprint(version)\n"
    assert isort.code(src) == expected
```

2. Run the test suite:

```bash
pytest -q
```

   The new test should pass, and the f
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
--- Proposal B
## Root cause
`Pilot.click()` calculates the click coordinates from the target widget’s *layout* region and then sends synthetic mouse‑down / mouse‑up events.  
It never checks whether those coordinates are actually **visible** to the user (i.e. not clipped by a `ScrollView`, hidden behind another widget, or outside the screen).  
Consequently a test can “click” a widget that is scrolled out of view or supply an `offset` that lands outside the widget’s visible area, which does not reflect real‑world mouse behaviour.  

## Fix
```diff
--- a/textual/_pilot.py
+++ b/textual/_pilot.py
@@
 class Pilot:
@@
-    async def click(
-        self,
-        selector: str,
-        *,
-        button: MouseButton = MouseButton.LEFT,
-        modifiers: set[Modifier] | None = None,
-        offset: tuple[int, int] = (0, 0),
-        double: bool = False,
-        timeout: float | None = None,
-    ) -> None:
-        \"\"\"Click on a widget identified by a CSS selector.\n-
-
-        Parameters
-        ----------
-        selector:
-            CSS selector identifying the widget to click.
-        button:
-            Which mouse button to use.
-        modifiers:
-            Keyboard modifiers to hold during the click.
-        offset:
-            (x, y) offset from the top‑left corner of the widget's region.
-        double:
-            Perform a double‑click instead of a single click.
-        timeout:
-            Maximum time to wait for the widget to appear.
-        \"\"\"
-        widget = await self._wait_for_widget(selector, timeout=timeout)
-        x, y = widget.region.x + offset[0], widget.region.y + offset[1]
-
-        # Simulate the mouse press/release sequence.
-        await self._mouse_down(x, y, button=button, modifiers=modifiers)
-        await self._mouse_up(x, y, button=button, modifiers=modifiers)
-
-        if double:
-            await self._mouse_down(x, y, button=button, modifiers=modifiers)
-            await self._mouse_up(x, y, button=button, modifiers=modifiers)
+    async def click(
+        self,
+        selector: str,
+        *,
+        button: MouseButton = MouseButton.LEFT,
+        modifiers: set[Modifier] | None = None,
+        offset: tuple[int, int] = (0, 0),
+        double: bool = False,
+        timeout: float | None = None,
+    ) -> None:
+        \"\"\"Click on a widget identified by a CSS selector.\n+\n+        The click is only allowed if the calculated click point is **visible** to the\n+        user.  If the point lies outside the widget's visible region (for example the\n+        widget is scrolled out of view or the supplied ``offset`` points outside the\n+        widget), a :class:`ValueError` is raised.\n+\n+        Parameters\n+        ----------\n+        selector:\n+            CSS selector identifying the widget to click.\n+        button:\n+            Which mouse button to use.\n+        modifiers:\n+            Keyboard modifiers to hold during the click.\n+        offset:\n+            ``(x, y)`` offset from the top‑left corner of the widget's *visible* region.\n+        double:\n+            Perform a double‑click instead of a single click.\n+        timeout:\n+            Maximum time to wait for the widget to appear.\n+        \"\"\"\n+\n+        # Resolve the widget (wait for it to appear if necessary).\n+        widget = await self._wait_for_widget(selector, timeout=timeout)\n+\n+        # Compute the absolute click coordinates.\n+        click_x = widget.region.x + offset[0

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
--- Proposal B
## Root cause
Stochastic strategies rely on two independent random number generators:

* Python’s built‑in ``random`` module  
* NumPy’s ``numpy.random`` module  

The library only exposed a helper for the standard library RNG, so when a user sets a seed only ``random.seed`` is affected.  Any strategy that draws from ``numpy.random`` (e.g., ``Random`` or ``ProbabilisticTitForTat``) will still produce different outcomes, breaking reproducibility.  No public helper exists that synchronises both RNGs, and the documentation does not mention the need to set both seeds.

## Fix
Add a public helper ``axelrod.seed`` that sets **both** RNGs.  Expose it in ``__all__`` and provide a short docstring explaining its purpose.

```diff
--- a/axelrod/__init__.py
+++ b/axelrod/__init__.py
@@
-"""Axelrod library top‑level package."""
-
-# Import the most commonly used symbols so they are available as
-# axelrod.Strategy, axelrod.Match, etc.
-from .match import Match
-from .tournament import Tournament
-from .player import Player
-from .strategies import *
-
-__all__ = [
-    "Match",
-    "Tournament",
-    "Player",
-] + [name for name in globals() if not name.startswith("_")]
+"""Axelrod library top‑level package.\n\nThis module re‑exports the most frequently used classes (``Match``,\n``Tournament`` and all strategy classes) and provides a small utility function\n``seed`` that synchronises the random number generators used throughout the\nlibrary.\n\nHistorically only the standard library ``random`` module was seeded, which\nmeant that stochastic strategies that rely on NumPy's RNG produced non‑\nreproducible results even when a seed was supplied.  The ``seed`` function\nensures that both RNGs are set to the same value, making experiments fully\nreproducible.\n"""
+
+# --------------------------------------------------------------------------- #
+# Public imports
+# --------------------------------------------------------------------------- #
+from .match import Match
+from .tournament import Tournament
+from .player import Player
+from .strategies import *  # noqa: F403,F401 – re‑export all strategy classes
+
+# --------------------------------------------------------------------------- #
+# Helper functions
+# --------------------------------------------------------------------------- #
+def seed(seed: int | None = None) -> None:
+    """
+    Set the random seed for reproducible stochastic behaviour.
+
+    This function sets the seed for **both** the Python standard library
+    ``random`` module *and* NumPy's random generator.  Passing ``None`` leaves
+    the generators in their default (unseeded) state, which is equivalent to
+    calling the underlying ``random.seed``/``numpy.random.seed`` without an
+    argument.
+
+    Parameters
+    ----------
+    seed : int or None, optional
+        The seed value to use.  If ``None`` (the default) the generators are
+        seeded with system entropy.\n
+    """
+    import random
+    try:
+        import numpy as np
+    except ImportError:  # pragma: no cover – NumPy is a hard dependency for Axelrod
+        np = None
+
+    random.seed(seed)
+    if np is not None:
+        np.random.seed(seed)
+
+# --------------------------------------------------------------------------- #
+# Exported symbols
+# --------------------------------------------------------------------------- #
+__all__ = [
+    "Match",
+    "Tournament",
+    "Player",
+    "seed",
+] + [name for name in globals() if not name.starts

=== TASK laravel__framework-37487 | repo laravel/framework | php
--- Issue
[Eloquent] withCount()->having()->count() generates wrong query
<!-- DO NOT THROW THIS AWAY -->
<!-- Fill out the FULL versions with patch versions -->

- Laravel Version: 8.42.1
- PHP Version: 8.0.3
- Database Driver & Version: MariaDB

### Description:
Given two simple models
```php
Illuminate\Support\Facades\Schema::create('users', function (Blueprint $table) {
  $table->id();
});
Illuminate\Support\Facades\Schema::create('roles', function (Blueprint $table) {
  $table->id();
 $table->foreignId('user_id')
                ->constrained()
                ->cascadeOnDelete()->cascadeOnUpdate();
});
class Role extends Model {
    public function user() {
        return $this->belongsTo( User::class );
    }
}

class User extends Model {
     public function codes() {
        return $this->hasMany( Role::class );
    }
}
```

Running the following statement results in an sql error:

```php
User::query()->withCount('roles')->having('roles_count', '>', '2')->count()
```
```
Illuminate\Database\QueryException with message 'SQLSTATE[42S22]: Column not found: 1054 Unknown column 'roles_count' in 'having clause' (SQL: select count(*) as aggregate from `users` having `roles_count` > 2)'
```

But the following runs without error

```php
User::query()->withCount('roles')->having('roles_count', '>', '2')->get()
```

The count method should be interchangeable and work with any query that `get` works with but currently it doesn't seem to gener
--- REAL FIX (ground truth)
diff --git a/src/Illuminate/Database/Query/Builder.php b/src/Illuminate/Database/Query/Builder.php
index 9df7d46eec..0ef10ac1c2 100755
--- a/src/Illuminate/Database/Query/Builder.php
+++ b/src/Illuminate/Database/Query/Builder.php
@@ -2826,8 +2826,8 @@ public function average($column)
      */
     public function aggregate($function, $columns = ['*'])
     {
-        $results = $this->cloneWithout($this->unions ? [] : ['columns'])
-                        ->cloneWithoutBindings($this->unions ? [] : ['select'])
+        $results = $this->cloneWithout($this->unions || $this->havings ? [] : ['columns'])
+                        ->cloneWithoutBindings($this->unions || $this->havings ? [] : ['select'])
                         ->setAggregate($function, $columns)
                         ->get($columns);
 
diff --git a/src/Illuminate/Database/Query/Grammars/Grammar.php b/src/Illuminate/Database/Query/Grammars/Grammar.php
index b7305e8ea3..d7bc534da1 100755
--- a/src/Illuminate/Database/Query/Grammars/Grammar.php
+++ b/src/Illuminate/Database/Query/Grammars/Grammar.php
@@ -45,7 +45,7 @@ class Grammar extends BaseGrammar
      */
     public function compileSelect(Builder $query)
     {
-        if ($query->unions && $query->aggregate) {
+        if (($query->unions || $query->havings) && $query->aggregate) {
             return $this->compileUnionAggregate($query);
         }
 

--- Proposal A
**## Root cause**

`Eloquent\Builder::count()` simply forwards the call to the underlying query builder:

```php
return (int) $this->toBase()->count($columns);
```

When a query contains a `HAVING` clause that refers to a column added by `withCount()` (e.g. `roles_count`), the underlying query builder builds an aggregate query like:

```sql
select count(*) as aggregate from `users` having `roles_count` > 2
```

The generated query does **not** include the sub‑select that defines `roles_count`, therefore the column does not exist in the outer query and MariaDB throws *“Unknown column 'roles_count' in having clause”*.

The `count()` method must recognise when a `HAVING` clause is present (or any other clause that references a selected column) and wrap the original query in a sub‑query before applying the aggregate `COUNT(*)`. This mirrors the behaviour of `get()` which keeps the original select list.

---

**## Fix**

```diff
--- a/src/Illuminate/Database/Eloquent/Builder.php
+++ b/src/Illuminate/Database/Eloquent/Builder.php
@@
-    /**
-     * Get the total number of records for the paginator.
-     *
-     * @param  string  $columns
-     * @return int
-     */
-    public function count($columns = '*')
-    {
-        return (int) $this->toBase()->count($columns);
-    }
+    /**
+     * Get the total number of records for the paginator.
+     *
+     * When the query contains a HAVING clause that references a column added
+     * by `withCount` (or any other selected column), the normal aggregate
+     * query would lose that column, causing a SQL error. In that case we wrap
+     * the original query in a sub‑query and count the rows of the sub‑query.
+     *
+     * @param  string  $columns
+     * @return int
+     */
+    public function count($columns = '*')
+    {
+        $base = $this->toBase();
+
+        // If the query has a HAVING clause (or a HAVING RAW clause) we need
+        // to preserve the original SELECT list. The simplest way is to wrap
+        // the whole query as a sub‑query and count the rows of that sub‑query.
+        if (! empty($base->havings) || ! empty($base->havingRaw)) {
+            // Clone the original query without the aggregate columns.
+            $sub = $base->cloneWithout(['orders', 'limit', 'offset']);
+
+            // Build a new query that selects COUNT(*) from the sub‑query.
+            $wrapped = $this->newQuery()
+                ->fromSub($sub, 'laravel_temp')
+                ->selectRaw('count(*) as aggregate');
+
+            // Retrieve the aggregate value directly.
+            return (int) $wrapped->value('aggregate');
+        }
+
+        // No HAVING clause – fall back to the original fast path.
+        return (int) $base->count($columns);
+    }
```

**Explanation of the changes**

1. **Detect HAVING clauses** – The builder now checks `$base->havings` and `$base->havingRaw`. If either is non‑empty we know the query relies on a column that will disappear when the aggregate is applied.

2. **Clone the original query** – `cloneWithout(['orders', 'limit', 'offset'])` removes pagination‑related clauses that would interfere with the outer `COUNT(*)` but keeps the `SELECT` list (including the `withCount` sub‑select) and the `HAVING` clause.

3. **Wrap in a sub‑query** – `fromSub($sub, 'laravel_temp')` creates `SELECT count(*) FROM ( …original query… ) AS laravel_temp`.

4. **Fetch the aggregate** – Using `value('aggregate')` returns the scalar count without needing to fetch a f
--- Proposal B
**## Root cause**

`Builder::count()` (via `Builder::aggregate()`) builds the query as  

```sql
select count(*) as aggregate from `users` having `roles_count` > 2
```  

When a `withCount()` sub‑select is present, the column alias `roles_count` exists only in the
original `SELECT` list. The aggregate call replaces the select list with `count(*)`,
so the `HAVING` clause refers to a column that is no longer selected, producing the
SQL error.

The fix is to recognise when a query contains `HAVING` clauses (or any other clause
that depends on selected columns) and, instead of applying the aggregate directly,
wrap the original query in a sub‑query and count the rows of that sub‑query.

---

**## Fix**

```diff
--- a/src/Illuminate/Database/Query/Builder.php
+++ b/src/Illuminate/Database/Query/Builder.php
@@
-    protected function aggregate($function, $columns = ['*'])
-    {
-        $this->aggregate = compact('function', 'columns');
-
-        $previousSelect = $this->columns;
-
-        if (empty($this->groups)) {
-            $this->orders = null;
-            $this->unionOrders = null;
-        }
-
-        $results = $this->get($columns);
-
-        $this->aggregate = null;
-
-        $this->columns = $previousSelect;
-
-        if (! $results->isEmpty()) {
-            return $results[0]->aggregate;
-        }
-    }
+    /**
+     * Execute an aggregate function on the database.
+     *
+     * @param  string  $function
+     * @param  array   $columns
+     * @return mixed
+     *
+     * @throws \Exception
+     */
+    protected function aggregate($function, $columns = ['*'])
+    {
+        // Normal aggregate handling (no HAVING clauses) – keep the original behaviour.
+        if (empty($this->havings)) {
+            $this->aggregate = compact('function', 'columns');
+
+            $previousSelect = $this->columns;
+
+            if (empty($this->groups)) {
+                $this->orders = null;
+                $this->unionOrders = null;
+            }
+
+            $results = $this->get($columns);
+
+            $this->aggregate = null;
+            $this->columns = $previousSelect;
+
+            if (! $results->isEmpty()) {
+                return $results[0]->aggregate;
+            }
+
+            return null;
+        }
+
+        // -----------------------------------------------------------------
+        // When a HAVING clause is present the aggregate must be performed
+        // on a sub‑query because the HAVING may reference columns that are
+        // added by `withCount()` (or any other custom select).  We clone the
+        // current query, strip the aggregate and ordering information, and
+        // wrap it in a sub‑query that we then count.
+        // -----------------------------------------------------------------
+
+        // Clone the current query without the columns/ordering/limit etc.
+        $clone = $this->cloneWithout([
+            'columns', 'orders', 'limit', 'offset',
+            'unionOrders', 'unionLimit', 'unionOffset',
+        ]);
+
+        // Remove any aggregate that might have been set on the clone.
+        $clone->aggregate = null;
+
+        // Build a new query that selects the count from the sub‑query.
+        $subQuery = $this->newQuery()
+            ->fromSub($clone, 'laravel_temp')
+            ->selectRaw('count(*) as aggregate');
+
+        $result = $subQuery->first();
+
+        // Restore the original state of the builder.
+        $this->aggregate = null;
+
+  

=== TASK gleam-lang__gleam-4354 | repo gleam-lang/gleam | rust
--- Issue
Tweak generated code for "Generate JSON encoder" action
Currently, one thing I like about the "Generate dynamic decoder" action is the fact that if you change the type that is being decoded, the decoder function will contain a compile error. For example, let's say you have this type:
```gleam
type Person {
  Person(name: String, age: Int)
}
```
The generated decoder will look like this:
```gleam
fn person_decoder() -> decode.Decoder(Person) {
  use name <- decode.field("name", decode.string)
  use age <- decode.field("age", decode.int)
  decode.success(Person(name:, age:))
}
```
Now let's say you want to add a new field, `job: String`. That decoder function will now error because you are trying to construct a `Person` without providing all the fields. This signals to the programmer that they need to delete the decoder and rerun the code action.
However, this is what the generared JSON encoder looks like for the above type currently.
```gleam
fn encode_person(person: Person) -> json.Json {
  json.object([
    #("name", json.string(person.name)),
    #("age", json.int(person.age)),
  ])
}
```
Even with a new field, this compiles fine. Nothing here is ensuring all fields are covered, so it's possible that someone will forget to update the encoder, and end up introducing a bug in the code.
To fix this, I propose a slight change in the generated code. Instead, it would look like this:
```gleam
fn encode_person(person: Person) -> json.Json {
  let Person(name:, age:) = person
  jso
--- REAL FIX (ground truth)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 828b3100f..b04b3bd25 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -49,6 +49,42 @@
 
   ([Surya Rose](https://github.com/GearsDatapacks))
 
+- The function generated by the "Generate JSON encoder" code action has been
+  slightly modified so that it will now fail to compile if the type has new
+  fields added, ensuring the programmer remembers to re-run the code action.
+  For example, for this type:
+
+  ```gleam
+  type Person {
+    Person(name: String, age: Int)
+  }
+  ```
+
+  The following code used to be generated:
+
+  ```gleam
+  fn encode_person(person: Person) -> json.Json {
+    json.object([
+      #("name", json.string(person.name)),
+      #("age", json.int(person.age)),
+    ])
+  }
+  ```
+
+  But now, this code is generated:
+
+  ```gleam
+  fn encode_person(person: Person) -> json.Json {
+    let Person(name:, age:) = person
+    json.object([
+      #("name", json.string(name)),
+      #("age", json.int(age)),
+    ])
+  }
+  ```
+
+  ([Surya Rose](https://github.com/GearsDatapacks))
+
 ### Formatter
 
 ### Bug fixes
@@ -68,7 +104,7 @@
 - Fixed a bug where using the "Convert to pipe" code action on a function or
   record capture produces invalid code.
   ([Matias Carlander](https://github.com/matiascr))
-  
+
 
 ## v1.9.1 - 2025-03-10
 
diff --git a/compiler-core/src/language_server/code_action.rs b/compiler-core/src/language_server/code_action.rs
index 4b34eadda..95f04e21c 100644
--- a/compiler-core/src/language_server/code_action.rs
+++ b/compiler-core/src/language_server/code_action.rs
@@ -3407,7 +3407,23 @@ impl<'a> GenerateJsonEncoder<'a> {
         // require pattern matching on the argument as we can access all its fields
         // with the usual record access syntax.
         if rest.is_empty() {
-            return self.constructor_encoder(mode, first, custom_type.name.clone(), record_name, 2);
+            let encoder = self.constructor_encoder(mode, first, custom_type.name.clone(), 2)?;
+            let unpacking = if first.arguments.is_empty() {
+                ""
+            } else {
+                &eco_format!(
+                    "let {name}({fields}:) = {record_name}\n  ",
+                    name = first.name,
+                    fields = first
+                        .arguments
+                        .iter()
+                        .filter_map(|argument| {
+                            argument.label.as_ref().map(|(_location, label)| label)
+                        })
+                        .join(":, ")
+                )
+            };
+            return Some(eco_format!("{unpacking}{encoder}"));
         }
 
         // Otherwise we generate an encoder for a type with multiple constructors:
@@ -3416,17 +3432,21 @@ impl<'a> GenerateJsonEncoder<'a> {
         let mut branches = Vec::with_capacity(constructors_size);
         for constructor in iter::once(first).chain(rest) {
             let RecordConstructor { name, .. } = constructor;
-     
--- Proposal A
## Root cause
The code‑action that generates a JSON encoder simply accesses the fields of the value directly (`person.name`, `person.age`, …).  
Because the generated function never pattern‑matches on the record, the compiler cannot detect when a new field is added to the type – the function still compiles and silently drops the new field.  

## Fix
Change the generator so that the produced encoder **destructures** the record/value with a `let` pattern that lists **all** fields.  
The pattern forces the compiler to check that every field of the type is mentioned; adding a field to the type will then cause a compile‑time error until the encoder is regenerated.

```diff
--- a/src/gleam/codegen/json_encoder.rs
+++ b/src/gleam/codegen/json_encoder.rs
@@
 fn generate_json_encoder(
     module: &mut ast::Module,
     type_: &ast::TypedDefinition,
 ) -> Result<ast::UntypedDefinition, Error> {
@@
-    // Build the body that accesses each field directly:
-    //   json.object([#("field", json.string(value.field)), …])
-    let mut fields = Vec::new();
-    for (field_name, field_type) in fields_of(type_) {
-        let field_key = ast::String::new(field_name.clone());
-        let field_access = ast::Expr::FieldAccess {
-            location: Location::default(),
-            label: field_name.clone(),
-            expr: Box::new(ast::Expr::Var {
-                location: Location::default(),
-                name: param_name.clone(),
-            }),
-        };
-        let json_value = encode_expr(&field_access, field_type);
-        fields.push(ast::Expr::Tuple {
-            location: Location::default(),
-            elems: vec![
-                ast::Expr::String {
-                    location: Location::default(),
-                    value: field_key,
-                },
-                json_value,
-            ],
-        });
-    }
-
-    let body = ast::Expr::Call {
-        location: Location::default(),
-        fun: Box::new(ast::Expr::Qualified {
-            location: Location::default(),
-            module: vec!["json".into()],
-            name: "object".into(),
-        }),
-        args: vec![ast::Expr::List {
-            location: Location::default(),
-            elems: fields,
-        }],
-    };
-
-    // fn encode_<type>(value: <type>) -> json.Json { … }
-    Ok(ast::UntypedDefinition::Fn {
-        location: Location::default(),
-        public: true,
-        name: format!("encode_{}", type_name).into(),
-        args: vec![ast::Arg {
-            location: Location::default(),
-            name: param_name.clone(),
-            annotation: Some(ast::Annotation::Typed {
-                location: Location::default(),
-                typ: ast::Type::UserDefined {
-                    location: Location::default(),
-                    name: type_name.clone(),
-                    args: vec![],
-                },
-            }),
-        }],
-        body: Box::new(body),
-        return_type: Some(ast::Annotation::Typed {
-            location: Location::default(),
-            typ: ast::Type::UserDefined {
-                location: Location::default(),
-                name: "Json".into(),
-                args: vec![],
-            },
-        }),
-    })
+    // ---------------------------------------------------------------------\n"
+    // Build the body that **destructures** the record/value first:\n"
+    //   let TypeName(field1:, field2:, …) = value\n"
+    //   json.object([#(\"field1\", json.<enc>(
--- Proposal B
## Root cause
The **“Generate JSON encoder”** code‑action builds the encoder body by directly accessing the record fields on the function argument (e.g. `person.name`).  
Because the argument is never de‑structured, the generated function will still compile when new fields are added to the type – the encoder simply ignores the new fields. This differs from the generated decoder, which pattern‑matches on the record constructor and therefore fails to compile when the type changes.

## Fix
Update the JSON‑encoder generator to **pattern‑match** on the record constructor before building the JSON object. This forces the compiler to check that every field of the type is handled, mirroring the behaviour of the decoder.

### Files changed
* `gleam/src/analysis/code_actions.rs` – where the *Generate JSON encoder* action is implemented.

### Key changes
1. **Create a let‑binding pattern** that extracts all fields from the argument.
2. Insert that let‑binding as the first statement in the generated function body.
3. Adjust the field‑access generation to use the bound variables instead of `arg.field`.

```diff
--- a/gleam/src/analysis/code_actions.rs
+++ b/gleam/src/analysis/code_actions.rs
@@
 fn generate_json_encoder(
     module: &mut TypedModule,
     type_name: &str,
     fields: &[TypedRecordConstructorArg],
 ) -> Option<CodeAction> {
@@
-    // Build the body that encodes each field.
-    let mut field_encodings = Vec::new();
-    for field in fields {
-        let field_name = &field.label;
-        let field_type = &field.typ;
-        let json_encoder = json_encoder_for_type(field_type);
-        field_encodings.push(format!(
-            "#(\"{field_name}\", {json_encoder}({arg_name}.{field_name}))"
-        ));
-    }
-
-    let body = format!(
-        "json.object([\n    {}\n])",
-        field_encodings.join(",\n    ")
-    );
-
-    // Assemble the full function source.
-    let source = format!(
-        "fn encode_{type_name}({arg_name}: {type_name}) -> json.Json {{\n  {body}\n}}"
-    );
+    // ---------------------------------------------------------------------\n\
+    // 1️⃣  De‑structure the argument so the compiler checks that *all* fields\n+    //     are covered.  This mirrors the decoder implementation and makes the\n+    //     encoder fail to compile when the type changes.\n+    // ---------------------------------------------------------------------\n+    // Build a pattern like `Person(name:, age:)`.\n+    let pattern_fields: Vec<String> = fields\n+        .iter()\n+        .map(|f| format!(\"{}:,\", f.label))\n+        .collect();\n+    let pattern = format!(\"{type_name}({})\", pattern_fields.join(\" \"));\n+    let let_binding = format!(\"let {pattern} = {arg_name};\");\n+\n+    // ---------------------------------------------------------------------\n+    // 2️⃣  Encode each field using the variables bound by the pattern.\n+    // ---------------------------------------------------------------------\n+    let mut field_encodings = Vec::new();\n+    for field in fields {\n+        let field_name = &field.label;\n+        let field_type = &field.typ;\n+        let json_encoder = json_encoder_for_type(field_type);\n+        // Use the variable introduced by the pattern (e.g. `name`).\n+        field_encodings.push(format!(\n+            \"#(\\\"{field_name}\\\", {json_encoder}({field_name}))\"\n+        ));\n+    }\n+\n+    let object_body = format!(\n+        \"json.object([\\n    {}\\n])\",\n+        field_encoding

=== TASK eslint__doctrine-182 | repo eslint/doctrine | js
--- Issue
Support type expression for @this tag
See #143 

The current implementation only supports one name in the expression:
```js
/**
 * @this {Widget}
 */
```

UnionType of NameExpression should be supported as well:
```js
/**
 * @this {Widget|Button}
 */
```
--- REAL FIX (ground truth)
diff --git a/lib/doctrine.js b/lib/doctrine.js
index 095eeeb..a5804df 100644
--- a/lib/doctrine.js
+++ b/lib/doctrine.js
@@ -10,13 +10,11 @@
 
     var typed,
         utility,
-        isArray,
         jsdoc,
         esutils,
         hasOwnProperty;
 
     esutils = require('esutils');
-    isArray = require('isarray');
     typed = require('./typed');
     utility = require('./utility');
 
@@ -268,7 +266,7 @@
 
         function scanIdentifier(last) {
             var identifier;
-            if (!esutils.code.isIdentifierStartES5(source.charCodeAt(index))) {
+            if (!esutils.code.isIdentifierStartES5(source.charCodeAt(index)) && !source[index].match(/[0-9]/)) {
                 return null;
             }
             identifier = advance();
@@ -296,13 +294,13 @@
                 return null;
             }
 
-            if (allowBrackets && source.charCodeAt(index) === 0x5B  /* '[' */) {
-                useBrackets = true;
-                name = advance();
-            }
-
-            if (!esutils.code.isIdentifierStartES5(source.charCodeAt(index))) {
-                return null;
+            if (source.charCodeAt(index) === 0x5B  /* '[' */) {
+                if (allowBrackets) {
+                    useBrackets = true;
+                    name = advance();
+                } else {
+                    return null;
+                }
             }
 
             name += scanIdentifier(last);
@@ -525,8 +523,8 @@
                         // extract the default value if there is one
                         // example: @param {string} [somebody=John Doe] description
                         assign = name.substring(1, name.length - 1).split('=');
-                        if (assign[1]) {
-                            this._tag['default'] = assign[1];
+                        if (assign.length > 1) {
+                            this._tag['default'] = assign.slice(1).join('=');
                         }
                         this._tag.name = assign[0];
 
@@ -609,13 +607,13 @@
             return true;
         };
 
-        TagParser.prototype.parseThis = function parseAccess() {
-            // this name may be a name expression (e.g. {foo.bar})
-            // or a name path (e.g. foo.bar)
+        TagParser.prototype.parseThis = function parseThis() {
+            // this name may be a name expression (e.g. {foo.bar}),
+            // an union (e.g. {foo.bar|foo.baz}) or a name path (e.g. foo.bar)
             var value = trim(sliceSource(source, index, this._last));
             if (value && value.charAt(0) === '{') {
                 var gotType = this.parseType();
-                if (gotType && this._tag.type.type === 'NameExpression') {
+                if (gotType && this._tag.type.type === 'NameExpression' || this._tag.type.type === 'UnionType') {
                     this._tag.name = this._tag.type.name;
                     return true;
                 } else {
@@ -839,7 +837,7 @@
 
             // array of rel
--- Proposal A
## Root cause
`doctrine` parses the `@this` tag and validates the associated type expression.  
The validator only accepts a **`NameExpression`** (e.g. `{Widget}`) and throws an error for any other type node. Consequently a union type such as `{Widget|Button}` is rejected, even though it is a perfectly valid JSDoc type expression.

## Fix
Allow the `@this` tag to accept either a `NameExpression` **or** a `UnionType` whose elements are all `NameExpression`s. The change is made in the tag‑parsing logic where the `@this` tag is handled.

```diff
--- a/lib/tag.js
+++ b/lib/tag.js
@@
   case 'this':
-    // The @this tag only supports a single name expression.
-    if (!type || type.type !== 'NameExpression') {
-      throw new Error('@this tag requires a name expression');
-    }
-    tag.this = type;
+    // The @this tag supports a name expression or a union of name expressions.
+    //   @this {Widget}
+    //   @this {Widget|Button}
+    if (!type) {
+      throw new Error('@this tag requires a type expression');
+    }
+
+    // Helper to verify that a type node is a NameExpression.
+    function isNameExpression(node) {
+      return node && node.type === 'NameExpression';
+    }
+
+    // Accept a single NameExpression.
+    if (isNameExpression(type)) {
+      tag.this = type;
+      break;
+    }
+
+    // Accept a UnionType whose elements are all NameExpressions.
+    if (type.type === 'UnionType') {
+      var allNames = type.elements.every(isNameExpression);
+      if (!allNames) {
+        throw new Error('@this tag union type may only contain name expressions');
+      }
+      tag.this = type;
+      break;
+    }
+
+    // Anything else is invalid.
+    throw new Error('@this tag requires a name expression or a union of name expressions');
   default:
     // other tags …
```

* The new logic:
  * Checks that a type is present.
  * Accepts a single `NameExpression`.
  * Accepts a `UnionType` **only** when every element is a `NameExpression`.
  * Throws a clear error for any other kind of type expression.

## How to verify
1. **Run the existing test suite** – it should still pass, confirming that the change didn’t break current behavior.  

2. **Add a new test** (or run the one that will be added by the maintainers) that parses a comment containing a union type for `@this`:

   ```js
   const doctrine = require('doctrine');
   const comment = `
   /**
    * @this {Widget|Button}
    */
   `;
   const ast = doctrine.parse(comment, { unwrap: true, tags: ['this'] });
   const thisTag = ast.tags.find(t => t.title === 'this');
   // Assertions:
   // - thisTag.this.type === 'UnionType'
   // - thisTag.this.elements.length === 2
   // - each element.type === 'NameExpression'
   ```

3. **Execute the test** (e.g., `npm test` or `npm run test`). The test should succeed, confirming that `doctrine` now correctly parses union types for the `@this` tag.

4. **Manual verification** (optional): Run a small script that parses a JSDoc comment with `@this {Foo|Bar}` and log the resulting AST to ensure the `this` property contains a `UnionType` node with the expected elements.
--- Proposal B
## Root cause
`@this` tags are validated to accept **only** a `NameExpression` type.  
The parser’s validation logic (in `lib/doctrine.js`) checks:

```js
if (title === 'this' && type && type.type !== 'NameExpression') {
    // raise error
}
```

Because of this strict check, a union type such as `{Widget|Button}` (which is parsed as a `UnionType` whose elements are `NameExpression`s) is rejected, even though it is a perfectly valid JSDoc type for `@this`.

## Fix
Allow a `UnionType` whose **all** elements are `NameExpression`s for the `@this` tag.

```diff
--- a/lib/doctrine.js
+++ b/lib/doctrine.js
@@
   // ---------------------------------------------------------------------------
   // Tag validation
   // ---------------------------------------------------------------------------
 
   // Validate tag type requirements and restrictions
   function validateTag(tag, state) {
@@
-    // @this must be a NameExpression
-    if (tag.title === 'this' && tag.type && tag.type.type !== 'NameExpression') {
-      throw new SyntaxError(
-        'Invalid type for @this tag. Expected a NameExpression, but got ' +
-          tag.type.type
-      );
-    }
+    // @this must be a NameExpression or a UnionType of NameExpressions
+    if (tag.title === 'this' && tag.type && !isValidThisTagType(tag.type)) {
+      throw new SyntaxError(
+        'Invalid type for @this tag. Expected a NameExpression or a UnionType of NameExpressions, but got ' +
+          tag.type.type
+      );
+    }
   }
 
   // ---------------------------------------------------------------------------
   // Helpers
   // ---------------------------------------------------------------------------
 
+  /**
+   * Determines whether a parsed type node is a valid type for an `@this` tag.
+   *
+   * Valid types are:
+   *   - NameExpression (e.g. `{Widget}`)
+   *   - UnionType whose every element is a NameExpression
+   *     (e.g. `{Widget|Button}`)
+   *
+   * @param {Object} type The parsed type node.
+   * @returns {boolean} True if the type is acceptable for `@this`.
+   */
+  function isValidThisTagType(type) {
+    if (!type) {
+      return false;
+    }
+
+    // Simple name: {Foo}
+    if (type.type === 'NameExpression') {
+      return true;
+    }
+
+    // Union of names: {Foo|Bar|Baz}
+    if (type.type === 'UnionType') {
+      return type.elements.every(function (elem) {
+        return elem.type === 'NameExpression';
+      });
+    }
+
+    return false;
+  }
+
   // Existing helper functions …
```

### What the patch does
1. **Introduces `isValidThisTagType`** – a small helper that returns `true` for:
   * a plain `NameExpression`
   * a `UnionType` whose every element is a `NameExpression`
2. **Updates the validation logic** for the `@this` tag to use this helper instead of the previous strict equality check.
3. Improves the error message to reflect the newly‑supported union syntax.

## How to verify
1. **Run the existing test suite** – all current tests must still pass.
2. **Add a new test** (or run the one that will be added by the maintainers) that parses a comment containing:

   ```js
   /**
    * @this {Widget|Button}
    */
   ```

   Expect `doctrine.parse` to return a tag object whose `type` is a `UnionType` with two `NameExpression` elements and **no** syntax error.
3. **Negative test** – ensure that an invalid type such as `{Widget|Object<string>}` still triggers a syntax error for `@this`.
4. **Manual check** – run a small script:

   ```js
   const 

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
`zulip-terminal` used hard‑coded constants for the presence ping interval and the offline‑threshold timeout.  
Since Zulip 7.0 (feature level 164) the server now returns the values
`server_presence_ping_interval_seconds` and `server_presence_offline_threshold_seconds`
in the response to **POST /register** (the “register‑queue” call).  
When the client continues to use the old constants it ignores the server‑customised
values and, on newer servers, may ping too often or treat a user as offline too
early.  The fix is to read the two fields from the register‑queue payload and
store them on the client, falling back to the original defaults when the fields
are absent (i.e. when talking to an older server).

## Fix
```diff
--- a/zulipterminal/client.py
+++ b/zulipterminal/client.py
@@
-from .constants import (
-    DEFAULT_PRESENCE_PING_INTERVAL,
-    DEFAULT_PRESENCE_OFFLINE_THRESHOLD,
-)
+# Default values that were historically hard‑coded.
+# They are kept as fall‑backs for servers that do not yet provide the
+# customised presence parameters (feature level < 164).
+DEFAULT_PRESENCE_PING_INTERVAL = 60          # seconds
+DEFAULT_PRESENCE_OFFLINE_THRESHOLD = 140    # seconds
@@
 class ZulipTerminal:
@@
     def __init__(self, config: Config, ...) -> None:
@@
-        self.presence_ping_interval = DEFAULT_PRESENCE_PING_INTERVAL
-        self.presence_offline_threshold = DEFAULT_PRESENCE_OFFLINE_THRESHOLD
+        # These will be overwritten by the values returned from the
+        # register‑queue request (if the server supports them).  Keeping the
+        # defaults here ensures the client works with older servers.
+        self.presence_ping_interval = DEFAULT_PRESENCE_PING_INTERVAL
+        self.presence_offline_threshold = DEFAULT_PRESENCE_OFFLINE_THRESHOLD
@@
-    def register_queue(self) -> Dict[str, Any]:
+    def register_queue(self) -> Dict[str, Any]:
         """
         Register a new event queue with the server.
         """
@@
-        payload = {
-            "apply_markdown": True,
-            "client_gravatar": True,
-            "event_types": self.event_types,
-            "fetch_event_types": self.fetch_event_types,
-            "narrow": self.narrow,
-            "queue_id": self.queue_id,
-            "last_event_id": self.last_event_id,
-        }
+        payload = {
+            "apply_markdown": True,
+            "client_gravatar": True,
+            "event_types": self.event_types,
+            "fetch_event_types": self.fetch_event_types,
+            "narrow": self.narrow,
+            "queue_id": self.queue_id,
+            "last_event_id": self.last_event_id,
+        }
@@
-        response = self.api_post("/register", json=payload)
-        data = response.json()
-        if data.get("result") != "success":
-            raise ZulipAPIError(data)
-        return data
+        response = self.api_post("/register", json=payload)
+        data = response.json()
+        if data.get("result") != "success":
+            raise ZulipAPIError(data)
+
+        # -----------------------------------------------------------------
+        # Presence‑parameter migration (Zulip 7.0 / feature level 164)
+        # -----------------------------------------------------------------
+        # The server may include the following keys.  They are optional for
+        # backward compatibility, so we use ``dict.get`` with the historic
+        # defaults as a fallback.
+        self.presence_ping_interval = data.get(
--- Proposal B
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

--- REAL FIX (ground truth)
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
 

--- Proposal A
## Root cause
The **import‑shadowing** rule walks the AST and flags any identifier that has the same name as an imported package. It treats *all* `*ast.FuncDecl` nodes the same, so a method name (a `FuncDecl` with a non‑nil `Recv` field) is also considered a shadowing identifier. Method names, however, are scoped to the type they belong to and should **not** be reported as import shadowing. The rule therefore needs to ignore function declarations that are methods.

## Fix
```diff
--- a/rule/import-shadowing.go
+++ b/rule/import-shadowing.go
@@
 import (
     "go/ast"
     "go/token"
     "strings"
 
     "github.com/mgechev/revive/lint"
 )
@@
 func (r *ImportShadowingRule) Apply(file *lint.File, _ []lint.Rule) []lint.Failure {
     var failures []lint.Failure
     imports := r.imports(file.AST)
 
     // Walk the file looking for identifiers that shadow an import name.
-    ast.Inspect(file.AST, func(n ast.Node) bool {
+    ast.Inspect(file.AST, func(n ast.Node) bool {
         // We only care about top‑level declarations (functions, vars, consts, types).
         // Method declarations are also *ast.FuncDecl* nodes, but they have a non‑nil
         // Recv field. Those should be ignored because the method name is scoped to
         // its receiver type and does not shadow the import at package level.
-        if fd, ok := n.(*ast.FuncDecl); ok {
-            // If the function has a receiver, it is a method – skip it.
-            if fd.Recv != nil {
-                return true
-            }
-            // For ordinary functions we still need to check the name.
-            if r.isShadow(fd.Name, imports) {
-                failures = append(failures, lint.Failure{
-                    Confidence: 1,
-                    Category:   "style",
-                    Failure:    fmt.Sprintf("The name %q shadows an import name", fd.Name.Name),
-                    Node:       fd.Name,
-                })
-            }
-            return true
-        }
+        if fd, ok := n.(*ast.FuncDecl); ok {
+            // Skip methods – they have a receiver.
+            if fd.Recv != nil {
+                return true
+            }
+            // Ordinary (package‑level) functions are still checked.
+            if r.isShadow(fd.Name, imports) {
+                failures = append(failures, lint.Failure{
+                    Confidence: 1,
+                    Category:   "style",
+                    Failure:    fmt.Sprintf("The name %q shadows an import name", fd.Name.Name),
+                    Node:       fd.Name,
+                })
+            }
+            return true
+        }
 
         // Variable, constant and type declarations.
         switch decl := n.(type) {
         case *ast.GenDecl:
@@
         }
         return true
     })
```

### What changed
* Added a guard that **skips** any `*ast.FuncDecl` whose `Recv` field is non‑nil (i.e., methods).
* Kept the original shadow‑checking logic for package‑level functions.
* Updated the comment to explain the reasoning.

## How to verify
1. **Build the updated Revive binary** (or run `go test ./...` if the project has tests) to ensure the code compiles.
2. Create a file `sample.go` with the content from the issue:

   ```go
   package somepkg

   import (
       metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
       "sigs.k8s.io/controller-runtime/pkg/reconcile"
   )

   type Struct struct{}

   func (s *Struct) reconcile(md metav1.ObjectMeta) (reconcile.Result, error) {
       return
--- Proposal B
## Root cause
`import-shadowing` walks the AST and flags any identifier whose name matches an imported package name **and** that identifier has a defined object (`ident.Obj != nil`).  
Method names are also represented by an `*ast.Ident` whose `Obj.Kind` is `ast.Fun`. Because the rule does not distinguish between a plain function declaration and a method declaration (a `FuncDecl` with a receiver), it incorrectly reports the method name as a shadow of the import.

## Fix
Skip the check when the identifier is the name of a method (i.e. a `FuncDecl` whose `Recv` field is non‑nil).

```diff
--- a/rule/import-shadowing.go
+++ b/rule/import-shadowing.go
@@
 import (
     "go/ast"
     "go/token"
     "strings"
 
     "github.com/mgechev/revive/lint"
 )
@@
 func (r *ImportShadowingRule) Apply(file *lint.File, _ []lint.Rule) []lint.Failure {
     var failures []lint.Failure
     // Build a map of import names to their positions.
     imports := map[string]token.Position{}
     for _, imp := range file.AST.Imports {
         name := importName(imp)
         if name != "" {
             imports[name] = file.Position(imp.Pos())
         }
     }
 
-    // Walk the file looking for identifiers that shadow an import name.
-    ast.Inspect(file.AST, func(n ast.Node) bool {
-        ident, ok := n.(*ast.Ident)
-        if !ok {
-            return true
-        }
-
-        // If the identifier is defined in this file (Obj != nil) and its name
-        // matches an import name, report a failure.
-        if ident.Obj != nil && ident.Obj.Kind == ast.Fun {
-            if pos, ok := imports[ident.Name]; ok {
-                failures = append(failures, lint.Failure{
-                    Confidence: 1,
-                    Category:   "style",
-                    Failure:    fmt.Sprintf("The name '%s' shadows an import name", ident.Name),
-                    Node:       ident,
-                    Position:   pos,
-                })
-            }
-        }
-        return true
-    })
+    // Walk the file looking for identifiers that shadow an import name.
+    // Method names (functions with a receiver) must be ignored because they are
+    // not true shadows – they belong to a type, not the package scope.
+    ast.Inspect(file.AST, func(n ast.Node) bool {
+        ident, ok := n.(*ast.Ident)
+        if !ok {
+            return true
+        }
+
+        // If the identifier is defined in this file (Obj != nil) and its name
+        // matches an import name, we may have a shadowing case.
+        if ident.Obj != nil && ident.Obj.Kind == ast.Fun {
+            // Distinguish between plain functions and methods.
+            // For a method, the object's declaration is a *ast.FuncDecl with a
+            // non‑nil Recv field. In that case we skip the warning.
+            if fd, ok := ident.Obj.Decl.(*ast.FuncDecl); ok && fd.Recv != nil {
+                // This is a method name – do not treat it as shadowing.
+                return true
+            }
+
+            if pos, ok := imports[ident.Name]; ok {
+                failures = append(failures, lint.Failure{
+                    Confidence: 1,
+                    Category:   "style",
+                    Failure:    fmt.Sprintf("The name '%s' shadows an import name", ident.Name),
+                    Node:       ident,
+                    Position:   pos,
+                })
+            }
+        }
+        return true
+    })
 
     return failures
 }
```

## How to verify
1. **Ru

=== TASK sasstools__sass-lint-893 | repo sasstools/sass-lint | js
--- Issue
UTF-8 w/ BOM + @mixin causes fatal parsing error
Demo repo [here](https://github.com/mattdistefano/sass-lint-bom-issue-demo)

Short version is that when you have a file encoded as UTF-8 w/ BOM that starts with `@mixin`, the CLI linter fails with a `1:1  error  Please check validity of the block starting from line #1  Fatal`.

UTF-8 w/out BOM works fine, as does UTF-8 w/ BOM without `@mixin` at the start. 

Happens on OS X w/ node 6.2.0 and Windows 10 w/ node 5.8.0.

The gulp-sass-lint plugin actually works fine in all scenarios.
--- REAL FIX (ground truth)
diff --git a/lib/groot.js b/lib/groot.js
index 986ce8b..1d2ee52 100644
--- a/lib/groot.js
+++ b/lib/groot.js
@@ -3,14 +3,15 @@
 //////////////////////////////
 'use strict';
 
-var gonzales = require('gonzales-pe'),
-    fm = require('front-matter');
+var gonzales = require('gonzales-pe');
+var fm = require('front-matter');
+var helpers = require('./helpers');
 
 module.exports = function (text, syntax, filename) {
   var tree;
 
   // Run `.toString()` to allow Buffers to be passed in
-  text = text.toString();
+  text = helpers.stripBom(text.toString());
 
   // if we're skipping front matter do it here, fall back to just our text in case it fails
   if (fm.test(text)) {
diff --git a/lib/helpers.js b/lib/helpers.js
index d2cb330..0b9af3c 100644
--- a/lib/helpers.js
+++ b/lib/helpers.js
@@ -394,4 +394,24 @@ helpers.isPartialStringMatch = function (needle, haystack) {
   return false;
 };
 
+/**
+ *  A copy of the the stripBom module from https://github.com/sindresorhus/strip-bom/blob/master/index.js
+ *  The module requires node > 4 whereas we support earlier versions.
+ *  This function strips the BOM marker from the beginning of a file
+ *
+ * @param {string} str - The string we wish to strip the BOM marker from
+ * @returns {string} The string without a BOM marker
+ */
+helpers.stripBom = function (str) {
+  if (typeof str !== 'string') {
+    throw new TypeError('Expected a string, got ' + typeof str);
+  }
+
+  if (str.charCodeAt(0) === 0xFEFF) {
+    return str.slice(1);
+  }
+
+  return str;
+};
+
 module.exports = helpers;

--- Proposal A
## Root cause
`sass‑lint` reads source files with `fs.readFileSync(..., 'utf8')`.  
When a file is saved **UTF‑8 with BOM**, the first character of the string is the Unicode
Byte‑Order‑Mark (`U+FEFF`). The parser that walks the token stream does not recognise this
character and, when the very first token of the file is an `@mixin` at‑rule, it treats the
BOM as part of the token stream. This makes the parser think the block is malformed and it
throws the generic fatal error:

```
1:1  error  Please check validity of the block starting from line #1  Fatal
```

If the file does not start with an at‑rule (e.g. a comment) the parser skips over the BOM
indirectly, which is why the problem only appears for `@mixin` at the top of a BOM‑encoded
file.

**Fix** – strip a leading BOM from the source string before handing it to the parser.

---

## Fix
```diff
--- a/lib/linter.js
+++ b/lib/linter.js
@@
   var fs = require('fs');
   var path = require('path');
   var parser = require('./parser');
   var config = require('./config');
   var utils = require('./utils');
 
   function Linter() {
@@
   Linter.prototype.lintFile = function (filePath) {
-    var src = fs.readFileSync(filePath, 'utf8');
+    // Read the file as UTF‑8.  If the file was saved with a BOM, Node will include the
+    // U+FEFF character as the first code unit.  The Sass parser cannot handle this
+    // character, so we strip it out before parsing.
+    var src = fs.readFileSync(filePath, 'utf8');
+    if (src.charCodeAt(0) === 0xFEFF) {
+      src = src.slice(1);
+    }
 
     var results = [];
 
     // Parse the source into an AST.  Any parsing errors are caught and turned into a
@@
   };
 
   module.exports = Linter;
```

*Explanation of the changes*

1. After reading the file we check `src.charCodeAt(0)`.  
2. If it equals `0xFEFF` (the BOM), we remove it with `src = src.slice(1)`.  
3. The rest of the linting pipeline receives a clean string, so the parser no longer
   crashes on files that start with `@mixin` (or any other at‑rule) when they contain a BOM.

---

## How to verify
1. **Prepare test files**
   ```bash
   # file without BOM – should already work
   echo "@mixin foo { color: red; }" > no-bom.scss

   # file with BOM – create using iconv (or any editor that can save with BOM)
   printf '\xEF\xBB\xBF' > bom.scss
   echo "@mixin foo { color: red; }" >> bom.scss
   ```

2. **Run the CLI linter**
   ```bash
   npx sass-lint -v -c .sass-lint.yml no-bom.scss
   # → should output normal lint results (or none) and exit code 0

   npx sass-lint -v -c .sass-lint.yml bom.scss
   # → should output the same lint results as above, **no fatal error**,
   #    and exit code 0
   ```

3. **Confirm that the error is gone**
   The command should **not** print:
   ```
   1:1  error  Please check validity of the block starting from line #1  Fatal
   ```
   and the process should finish cleanly.

4. **Cross‑platform check**
   Run the same steps on macOS and Windows (Node ≥ 5.8) to ensure the fix works on both
   environments.

If the linter now processes the BOM‑encoded file without crashing, the issue is resolved.
--- Proposal B
## Root cause
`sass-lint` reads the source file as a raw string and feeds it directly to the **gonzales‑pe** parser.  
When a file is saved as **UTF‑8 with BOM**, the first three bytes (`0xEF 0xBB 0xBF`) are interpreted as the Unicode **ZERO WIDTH NO‑BREAK SPACE** (`\uFEFF`).  
Because the parser is not aware of this invisible character, the very first token of the file becomes `\uFEFF@` instead of a proper `@`‑keyword.  
When the file also starts with an `@mixin` block the parser tries to build a block from an unexpected token and throws the generic *“Please check validity of the block starting from line #1 Fatal”* error.

The CLI version of `sass-lint` therefore crashes, while the gulp plugin (which strips the BOM before passing the source to the linter) works correctly.

## Fix
Strip a leading UTF‑8 BOM from the source string **before** it is handed to the parser.

```diff
--- a/lib/linter.js
+++ b/lib/linter.js
@@
 const fs = require('fs');
 const path = require('path');
 const gonzales = require('gonzales-pe-sl');
 const config = require('./config');
 const rules = require('./rules');
+// ---------------------------------------------------------------------------
+// Helper: remove a UTF‑8 BOM if present.
+// The BOM is represented by the Unicode character U+FEFF (ZERO WIDTH NO‑BREAK SPACE)
+// at the very start of the file.  It is invisible but breaks the SCSS parser.
+// ---------------------------------------------------------------------------
+function stripBOM (source) {
+  // If the first character is the BOM, drop it.
+  if (source && source.charCodeAt(0) === 0xFEFF) {
+    return source.slice(1);
+  }
+  return source;
+}
 
 /**
  * Lint a file.
  *
  * @param {String} filePath
@@
 function lintFile (filePath, options) {
   const file = path.resolve(filePath);
   const source = fs.readFileSync(file, 'utf8');
 
-  const ast = gonzales.parse(source, {
+  // Remove a possible UTF‑8 BOM before parsing.
+  const cleanSource = stripBOM(source);
+
+  const ast = gonzales.parse(cleanSource, {
     syntax: 'scss',
     // gonzales-pe throws on syntax errors, we want to catch them.
     // The `error` event is emitted on the parser instance.
   });
```

*Explanation of the changes*

1. **`stripBOM` helper** – Detects the BOM by checking the first character’s code point (`0xFEFF`) and removes it.
2. **Integration** – The source read from disk (`source`) is passed through `stripBOM` before being parsed by **gonzales‑pe**.
3. No other logic is altered, so normal linting behaviour remains unchanged.

## How to verify
1. **Clone the demo repository** (or any repo that reproduces the issue).  
   ```bash
   git clone https://github.com/mattdistefano/sass-lint-bom-issue-demo.git
   cd sass-lint-bom-issue-demo
   ```
2. **Install the patched version of `sass-lint`** (point to the local repo or publish a temporary npm link).  
   ```bash
   npm install   # install dependencies
   npm link      # from the sass-lint repo root after applying the patch
   npm link sass-lint
   ```
3. **Run the CLI on the BOM‑encoded file that starts with `@mixin`.**  
   ```bash
   npx sass-lint -v -q -c .sass-lint.yml path/to/bom-mixin.scss
   ```
   *Expected result*: The command exits with status `0` (or with the usual lint warnings) **without** the fatal “Please check validity of the block starting from line #1” error.

4. **Run the CLI on a normal UTF‑8 file (no BOM) that also starts with `@mixin`.**  
   ```bash
   npx sass-lint -v -q -c .sass-lint.yml pat

=== TASK lunarphp__lunar-2124 | repo lunarphp/lunar | php
--- Issue
Add support for Laravel 12
<!-- DO NOT THROW THIS AWAY -->
<!-- Fill out the FULL versions with patch versions -->

- Lunar version: latest
- Laravel Version: 12.x
- PHP Version: 8.4.0

### Expected Behaviour:
Allow to be installed in a laravel 12 project.

### Actual Behaviour:
  Problem 2
    - Root composer.json requires lunarphp/core * -> satisfiable by lunarphp/core[dev-main].
    - lunarphp/core dev-main requires laravel/framework ^10.0|^11.0 -> found laravel/framework[v10.0.0, ..., 10.x-dev, v11.0.0, ..., 11.x-dev] but it conflicts with your root composer.json require (^12.0).

### Steps To Reproduce:
Fresh laravel 12 project and try to install this package.
--- REAL FIX (ground truth)
diff --git a/.github/workflows/static-analysis.yml b/.github/workflows/static-analysis.yml
index 3ccfd745..f46dcd8b 100644
--- a/.github/workflows/static-analysis.yml
+++ b/.github/workflows/static-analysis.yml
@@ -12,10 +12,18 @@ jobs:
     strategy:
       fail-fast: false
       matrix:
-        php: ["8.2", "8.3"]
-        laravel: ["^10.0", "^11.0"]
+        php: ["8.2", "8.3", "8.4"]
+        laravel: ["^10.0", "^11.0", "^12.0"]
         dependencies: ["highest"]
-
+        exclude:
+          - php: "8.3"
+            laravel: "^10.0"
+          - php: "8.4"
+            laravel: "^10.0"
+          - php: "8.2"
+            laravel: "^11.0"
+          - php: "8.2"
+            laravel: "^12.0"
     name: "PHP ${{ matrix.php }} - L${{ matrix.laravel }} ${{ matrix.dependencies == 'highest' && '↑' || '↓' }}"
 
     runs-on: "ubuntu-latest"
diff --git a/composer.json b/composer.json
index 526ffbbf..05c4790b 100644
--- a/composer.json
+++ b/composer.json
@@ -1,5 +1,5 @@
 {
-    "name": "lunarphp/lunar",
+    "name": "lunarphp/lunarmono",
     "description": "Lunar Monorepo",
     "license": "MIT",
     "authors": [
@@ -10,44 +10,44 @@
     ],
     "require": {
         "awcodes/filament-badgeable-column": "^2.3.2",
-        "awcodes/shout": "^2.0.2",
-        "barryvdh/laravel-dompdf": "^2.0",
-        "cartalyst/converter": "^8.0|^9.0",
+        "awcodes/shout": "^2.0.4",
+        "barryvdh/laravel-dompdf": "^3.0",
+        "cartalyst/converter": "^8.0|^9.0|^10",
         "doctrine/dbal": "^3.6",
-        "dompdf/dompdf": "^2.0.7",
+        "dompdf/dompdf": "^3.1",
         "ext-bcmath": "*",
         "ext-exif": "*",
         "ext-intl": "*",
-        "filament/filament": "^3.2.25",
-        "filament/spatie-laravel-media-library-plugin": "^3.2",
+        "filament/filament": "^3.3.4",
+        "filament/spatie-laravel-media-library-plugin": "^3.3.4",
         "guzzlehttp/guzzle": "^7.3",
         "http-interop/http-factory-guzzle": "^1.2",
-        "kalnoy/nestedset": "^v6.x-dev",
-        "laravel/framework": "^10.0|^11.0",
-        "laravel/scout": "^10.0",
-        "leandrocfe/filament-apex-charts": "^3.1.3",
+        "kalnoy/nestedset": "^v6.0.5",
+        "laravel/framework": "^10.0|^11.0|^12.0",
+        "laravel/scout": "^10.13.1",
+        "leandrocfe/filament-apex-charts": "^3.2.0",
         "livewire/livewire": "^3.0",
         "lukascivil/treewalker": "0.9.1",
-        "marvinosswald/filament-input-select-affix": "^0.2.0",
+        "lunarphp/lunar": "self.version",
         "meilisearch/meilisearch-php": "^1.10",
         "php": "^8.2",
-        "spatie/laravel-activitylog": "^4.4",
-        "spatie/laravel-blink": "^1.7",
-        "spatie/laravel-data": "^4.9.0",
-        "spatie/laravel-medialibrary": "^11.0.0",
-        "spatie/laravel-permission": "^6.4",
-        "spatie/php-structure-discoverer": "^2.0",
+        "spatie/laravel-activitylog": "^4.10.1",
+        "spatie/laravel-blink": "^1.7.1",
+        "spatie/laravel-data
--- Proposal A
## Root cause
`lunarphp/core` (and consequently the meta‑package `lunarphp/lunar`) restricts the Laravel framework version to `^10.0|^11.0` in its `composer.json`. When a consumer project requires Laravel 12 (`^12.0`), Composer cannot resolve the dependency graph, resulting in the conflict shown in the issue. The package simply needs to declare compatibility with Laravel 12.

## Fix
```diff
--- a/core/composer.json
+++ b/core/composer.json
@@
-    "require": {
-        "php": "^8.1",
-        "illuminate/support": "^10.0|^11.0",
-        "laravel/framework": "^10.0|^11.0",
-        "spatie/laravel-permission": "^5.0"
-    },
+    "require": {
+        "php": "^8.1",
+        "illuminate/support": "^10.0|^11.0|^12.0",
+        "laravel/framework": "^10.0|^11.0|^12.0",
+        "spatie/laravel-permission": "^5.0"
+    },
```

If the repository contains other sub‑packages (e.g., `lunarphp/checkout`, `lunarphp/checkout-frontend`, etc.) that also pin Laravel, apply the same change to each of their `composer.json` files:

```diff
--- a/checkout/composer.json
+++ b/checkout/composer.json
@@
-        "laravel/framework": "^10.0|^11.0",
+        "laravel/framework": "^10.0|^11.0|^12.0",
```

(Repeat for any other packages that list `laravel/framework`.)

## How to verify
1. **Create a fresh Laravel 12 project**  
   ```bash
   composer create-project laravel/laravel:^12.0 test-laravel12
   cd test-laravel12
   ```

2. **Require the Lunar package**  
   ```bash
   composer require lunarphp/lunar
   ```

3. **Observe Composer output** – installation should complete without version‑conflict errors.

4. **Run the package’s test suite** (if available) inside the Laravel 12 project to ensure runtime compatibility:  
   ```bash
   php artisan test
   ```

5. **Optional sanity check** – start the Laravel development server and verify that Lunar’s routes/pages load correctly.  

Successful installation and passing tests confirm that Lunar now supports Laravel 12.
--- Proposal B
## Root cause
`lunarphp/core` (and possibly other Lunar packages) declare a hard dependency on Laravel 10 or 11 only:

```json
"require": {
    "laravel/framework": "^10.0|^11.0",
    …
}
```

When a Laravel 12 project tries to install Lunar, Composer cannot resolve the dependency because the constraint does not include `^12.0`. This is the only blocker; the codebase itself is already compatible with Laravel 12.

## Fix
Update the Laravel framework version constraint in every Lunar package that pins Laravel to `^10.0|^11.0` so that it also allows Laravel 12. The change is limited to the `composer.json` files; no runtime code changes are required.

```diff
--- a/core/composer.json
+++ b/core/composer.json
@@
     "require": {
-        "laravel/framework": "^10.0|^11.0",
+        "laravel/framework": "^10.0|^11.0|^12.0",
         "php": "^8.1",
         "illuminate/support": "^10.0|^11.0|^12.0",
         "illuminate/database": "^10.0|^11.0|^12.0",
         "illuminate/validation": "^10.0|^11.0|^12.0",
         "illuminate/filesystem": "^10.0|^11.0|^12.0",
         "illuminate/queue": "^10.0|^11.0|^12.0",
         "illuminate/cache": "^10.0|^11.0|^12.0",
         "illuminate/redis": "^10.0|^11.0|^12.0",
         "illuminate/console": "^10.0|^11.0|^12.0",
         "illuminate/events": "^10.0|^11.0|^12.0",
         "illuminate/contracts": "^10.0|^11.0|^12.0"
     },
*** End of File ***
--- a/composer.json
+++ b/composer.json
@@
     "require": {
-        "laravel/framework": "^10.0|^11.0",
+        "laravel/framework": "^10.0|^11.0|^12.0",
         "php": "^8.1",
         "lunarphp/core": "dev-main"
     },
*** End of File ***
--- a/checkout/composer.json
+++ b/checkout/composer.json
@@
     "require": {
-        "laravel/framework": "^10.0|^11.0",
+        "laravel/framework": "^10.0|^11.0|^12.0",
         "php": "^8.1",
         "lunarphp/core": "dev-main"
     },
*** End of File ***
--- a/checkout/src/Providers/CheckoutServiceProvider.php
+++ b/checkout/src/Providers/CheckoutServiceProvider.php
@@
-use Illuminate\Support\ServiceProvider;
+use Illuminate\Support\ServiceProvider;
 
 /**
  * Register the service provider.
  *
  * @return void
  */
 public function register()
 {
-    $this->app->register(\Laravel\Cashier\CashierServiceProvider::class);
+    // Laravel Cashier is optional; only register when the package exists.
+    if (class_exists(\Laravel\Cashier\CashierServiceProvider::class)) {
+        $this->app->register(\Laravel\Cashier\CashierServiceProvider::class);
+    }
 }
*** End Patch ***
```

**Explanation of changes**

1. **`core/composer.json`** – Expanded the Laravel framework constraint to include `^12.0`. All Laravel‑related Illuminate component constraints were also broadened to `^12.0` to keep them in sync.

2. **Root `composer.json`** – Updated the top‑level Laravel constraint so the meta‑package can be required directly in a Laravel 12 project.

3. **`checkout/composer.json`** – Same version bump for the Checkout package.

4. **`checkout/src/Providers/CheckoutServiceProvider.php`** – Guarded the optional Cashier registration with a `class_exists` check. Laravel 12 ships with Cashier as a separate optional package; the guard prevents a fatal error if Cashier isn’t installed, ensuring smooth installation on fresh Laravel 12 projects.

(If other Lunar sub‑packages (e.g., `admin`, `shipping`, `taxes`, etc.) also contain a `composer.json` with the same Laravel constraint, apply the identical change to them.)

## How to ver

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
--- REAL FIX (ground truth)
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
 
-For Java the easiest way to set up environment like configuration is through [property files](https://docs.oracle.com/javase/tutorial/essential/environment/properties.html). While those will not be available as environment variables they are very commonly used configuration mechanisms throughout Java.
+functions:
+  hello: # this function will overwrite the service level environment config above
+    handler: handler.hello
+    environment:
+      TABLE_NAME: tableName2
+  users: # this function will inherit the service level environment config above
+    handler: handler.users
+```
 
 ## Log Group Resources
 
diff --git a/lib/plugins/aws/deploy/compile/functions/index.js b/lib/plugins/aws/deploy/compile/functions/index.js
index 7a2c28dd7..44c61a827 100644
--- a/lib/plugins/aws/deploy/compile/functions/index.js
+++ b/lib/plugins/aws/deploy/compile/functions/index.js
@@ -81,6 +81,23 @@ class AwsCompileFunctions {
       newFunction.Properties.Description = functionObject.description;
     }
 
+    if (functionObject.environment || this.serverless.service.provider.environment) {
+      newFunction.Properties.Environment = {};
+      newFunction.Properties.Environm
--- Proposal A
## Root cause
The framework does not yet translate the new `environment` configuration block (both at the provider level and the function level) into the CloudFormation template for Lambda functions. Consequently, users cannot expose custom data, service‑generated resources, or future SDK configuration via environment variables.

## Fix
Add support for the `environment` block:

1. **Merge provider‑level and function‑level environment variables.**  
2. **Inject the merged map into the generated Lambda CloudFormation resource** under `Properties.Environment.Variables`.  
3. **Preserve CloudFormation intrinsic functions** (e.g. `{ Ref: 'MyBucket' }`) by copying values verbatim.

```diff
--- a/lib/plugins/aws/package/compile/functions.js
+++ b/lib/plugins/aws/package/compile/functions.js
@@
   compileFunctions() {
     const compiledFunctions = {};
     const service = this.serverless.service;
     const provider = service.provider;
 
-    // existing code that creates each function resource …
+    // existing code that creates each function resource …
+    // -------------------------------------------------
+    // NEW: inject environment variables
+    // -------------------------------------------------
+    // Helper to merge provider‑level and function‑level env vars.
+    // Provider vars are applied to every function unless overridden.
+    const mergeEnvs = (funcEnv) => {
+      const providerEnv = provider.environment || {};
+      // If the function does not define its own env, just use provider env.
+      if (!funcEnv) return { ...providerEnv };
+      // Shallow merge – function values win over provider values.
+      return { ...providerEnv, ...funcEnv };
+    };
+
+    // Iterate over all defined functions and augment the CF resource.
+    Object.entries(service.functions).forEach(([functionName, functionObj]) => {
+      const functionLogicalId = this.provider.naming.getLambdaLogicalId(functionName);
+      const functionResource = compiledFunctions[functionLogicalId];
+
+      if (!functionResource) return; // safety – should never happen
+
+      // Resolve the final environment map.
+      const mergedEnv = mergeEnvs(functionObj.environment);
+
+      // Only add the block if there is at least one variable.
+      if (Object.keys(mergedEnv).length > 0) {
+        // CloudFormation expects the exact structure, so we keep any
+        // intrinsic functions (Ref, Fn::GetAtt, etc.) as‑is.
+        functionResource.Properties.Environment = {
+          Variables: mergedEnv,
+        };
+      }
+    });
   }
 
   // -------------------------------------------------------------------------
   // Existing helper methods (unchanged)
   // -------------------------------------------------------------------------
```

*Explanation of the diff*  

* Added a `mergeEnvs` helper that shallow‑merges `provider.environment` with a function’s own `environment` (function values override provider values).  
* After the original function‑resource creation loop, we walk through every defined function, locate its compiled CloudFormation resource (`compiledFunctions[logicalId]`), compute the merged environment map, and, when non‑empty, inject it under `Properties.Environment.Variables`.  
* The merge is shallow because environment values are simple scalars or CloudFormation intrinsic objects; deep merging is unnecessary and could mask intentional overrides.

## How to verify
1. **Add a test case** (e.g. in `test/unit/lib/plugins/aws/package/co
--- Proposal B
## Root cause
Serverless did not yet implement the `environment` configuration key.  
When a user defines `provider.environment` (global for all functions) or
`functions.<name>.environment` (per‑function) the framework silently ignores it,
so the generated CloudFormation template never contains an `Environment`
section for the Lambda resources. This makes the feature proposal unusable
and causes tests that expect environment variables to fail.

## Fix
Add support for the `environment` key:

1. **Validate the new key** in the provider schema.  
2. **Merge** `provider.environment` with each function’s own `environment`
   (function‑level values win) when the CloudFormation template is compiled.  
3. **Emit** an `Environment` block with a `Variables` map in the Lambda
   resource definition.

```diff
--- a/lib/plugins/aws/provider.js
+++ b/lib/plugins/aws/provider.js
@@
   schema: {
     type: 'object',
     properties: {
       name: {
         type: 'string',
         enum: ['aws'],
       },
@@
         stage: {
           type: 'string',
         },
+        // New: global environment variables for all functions
+        environment: {
+          type: 'object',
+          additionalProperties: true,
+        },
         // existing properties …
       },
       required: ['name', 'runtime'],
     },
   },
*** End of File
--- a/lib/plugins/aws/package/compile/functions.js
+++ b/lib/plugins/aws/package/compile/functions.js
@@
   compileFunctions() {
     const compiledFunctions = {};
     const providerEnv = this.serverless.service.provider.environment || {};
 
     Object.entries(this.serverless.service.functions).forEach(
       ([functionName, functionObj]) => {
         const functionLogicalId = this.provider.naming.getLambdaLogicalId(
           functionName
         );
 
         // -----------------------------------------------------------------
         // Existing code that builds the basic Lambda CF resource …
         // -----------------------------------------------------------------
 
         const cfFunction = {
           Type: 'AWS::Lambda::Function',
           Properties: {
@@
           },
         };
 
-        // Existing handling of other properties (handler, runtime, etc.)
+        // -----------------------------------------------------------------
+        // Environment variables handling
+        // -----------------------------------------------------------------
+        // Merge provider‑level env (global) with function‑level env.
+        // Function‑level definitions override provider‑level ones.
+        const functionEnv = functionObj.environment || {};
+        const mergedEnv = Object.assign({}, providerEnv, functionEnv);
+
+        if (Object.keys(mergedEnv).length > 0) {
+          // CloudFormation expects an `Environment` object with a `Variables` map.
+          // Values can be plain strings or CF intrinsic functions (e.g. Ref).
+          cfFunction.Properties.Environment = {
+            Variables: mergedEnv,
+          };
+        }
 
         // -----------------------------------------------------------------
         // Existing handling of other properties (VPC, dead‑letter queue, …)
         // -----------------------------------------------------------------
 
         compiledFunctions[functionLogicalId] = cfFunction;
       }
     );
 
*** End of File
```

## How to verify
1. **Create a test service** (e.g. `test-service`) with the following `serverless.yml`:

   ```yaml
   service

=== TASK casey__just-1809 | repo casey/just | rust
--- Issue
completions and modules don't work together
The completions script uses `just --summary` to fetch a list of recipes. A recipe `bar` inside module `foo` is listed as `foo::bar`, however module recipes can't be invoked like this ("Justfile does not contain recipe `foo::bar`").

I'm not sure the best solution although I do personally find `foo::bar` easier syntax to read as `foo bar` in my mind looks like a recipe `foo` that takes a parameter `bar`. 

I wonder whether `foo::bar` should be added as additional valid syntax to run a module's recipe?
--- REAL FIX (ground truth)
diff --git a/README.md b/README.md
index f2cd1191..835dfbd8 100644
--- a/README.md
+++ b/README.md
@@ -2669,6 +2669,13 @@ $ just --unstable bar b
 B
 ```
 
+Or with path syntax:
+
+```sh
+$ just --unstable bar::b
+B
+```
+
 If a module is named `foo`, just will search for the module file in `foo.just`,
 `foo/mod.just`, `foo/justfile`, and `foo/.justfile`. In the latter two cases,
 the module file may have any capitalization.
diff --git a/src/justfile.rs b/src/justfile.rs
index 6105e154..8bec2956 100644
--- a/src/justfile.rs
+++ b/src/justfile.rs
@@ -2,7 +2,7 @@ use {super::*, serde::Serialize};
 
 #[derive(Debug)]
 struct Invocation<'src: 'run, 'run> {
-  arguments: &'run [&'run str],
+  arguments: Vec<&'run str>,
   recipe: &'run Recipe<'src>,
   settings: &'run Settings<'src>,
   scope: &'run Scope<'src, 'run>,
@@ -209,7 +209,7 @@ impl<'src> Justfile<'src> {
       _ => {}
     }
 
-    let argvec: Vec<&str> = if !arguments.is_empty() {
+    let mut remaining: Vec<&str> = if !arguments.is_empty() {
       arguments.iter().map(String::as_str).collect()
     } else if let Some(recipe) = &self.default {
       recipe.check_can_be_default_recipe()?;
@@ -220,15 +220,29 @@ impl<'src> Justfile<'src> {
       return Err(Error::NoDefaultRecipe);
     };
 
-    let arguments = argvec.as_slice();
-
     let mut missing = Vec::new();
     let mut invocations = Vec::new();
-    let mut remaining = arguments;
     let mut scopes = BTreeMap::new();
     let arena: Arena<Scope> = Arena::new();
 
-    while let Some((first, mut rest)) = remaining.split_first() {
+    while let Some(first) = remaining.first().copied() {
+      if first.contains("::") {
+        if first.starts_with(':') || first.ends_with(':') || first.contains(":::") {
+          missing.push(first.to_string());
+          remaining = remaining[1..].to_vec();
+          continue;
+        }
+
+        remaining = first
+          .split("::")
+          .chain(remaining[1..].iter().copied())
+          .collect();
+
+        continue;
+      }
+
+      let rest = &remaining[1..];
+
       if let Some((invocation, consumed)) = self.invocation(
         0,
         &mut Vec::new(),
@@ -241,12 +255,12 @@ impl<'src> Justfile<'src> {
         first,
         rest,
       )? {
-        rest = &rest[consumed..];
+        remaining = rest[consumed..].to_vec();
         invocations.push(invocation);
       } else {
-        missing.push((*first).to_owned());
+        missing.push(first.to_string());
+        remaining = rest.to_vec();
       }
-      remaining = rest;
     }
 
     if !missing.is_empty() {
@@ -273,7 +287,7 @@ impl<'src> Justfile<'src> {
       Self::run_recipe(
         &context,
         invocation.recipe,
-        invocation.arguments,
+        &invocation.arguments,
         &dotenv,
         search,
         &mut ran,
@@ -306,7 +320,7 @@ impl<'src> Justfile<'src> {
     search: &'run Search,
     parent: &'run Scope<'src, 'run>,
     first: &'run str,
-    rest: &'run [&'run str],
+
--- Proposal A
## Root cause
The Bash completion script for **just** parses the output of `just --summary` to obtain the list of available recipes.  
When a recipe lives inside a module, `just --summary` prints it as `module::recipe`.  
However the actual command‑line syntax to invoke such a recipe is `just module recipe` (a space, not `::`).  
Because the completion script forwards the raw `module::recipe` strings to `compgen`, the generated completions are invalid – they suggest `foo::bar`, which `just` cannot execute.

## Fix (a unified diff with your best‑guess file paths and code)
```diff
--- a/completions/just.bash
+++ b/completions/just.bash
@@
-    # Get the list of recipes from `just --summary`.
-    local recipes=$(just --summary | awk '{print $1}')
+    # Get the list of recipes from `just --summary`.
+    #
+    # `just --summary` prints module recipes as `module::recipe`,
+    # but the correct invocation syntax is `just module recipe`
+    # (i.e. a space instead of `::`).  Convert the `::` separator to a
+    # space so that the completion suggestions are actually usable.
+    local recipes=$(just --summary \
+        | awk '{print $1}' \
+        | sed 's/::/ /g')
```

*Explanation of the change*  
- The original line simply extracted the first column of the summary output.  
- The patched version adds a `sed 's/::/ /g'` step that replaces every `::` with a single space, turning `foo::bar` into `foo bar`.  
- The comment clarifies why this transformation is required.

No other parts of the script need modification because the rest of the completion logic already works with a whitespace‑separated list of recipe names.

## How to verify
1. **Build/install the updated completion script**  
   ```bash
   # Assuming you have the repository checked out
   source completions/just.bash   # or copy it to /etc/bash_completion.d/
   ```

2. **Create a Justfile with a module recipe**  
   ```just
   module foo {
       bar:
           echo "inside foo::bar"
   }
   ```

3. **Check the summary output**  
   ```bash
   $ just --summary
   foo::bar
   ```

4. **Test Bash completion**  
   - Type `just f` and press `<TAB>` twice.  
   - The completion should suggest `foo` (the module) and, after a space, `bar`.  
   - The final suggestion should be `foo bar`, **not** `foo::bar`.

5. **Execute the recipe using the suggested completion**  
   ```bash
   $ just foo bar
   inside foo::bar
   ```

6. **Confirm that the old, invalid suggestion no longer appears**  
   - Run `compgen -W "$(just --summary | awk '{print $1}')" -- "foo::"` and verify it returns nothing.  
   - Run `compgen -W "$(just --summary | awk '{print $1}' | sed 's/::/ /g')" -- "foo "` and verify it returns `bar`.

If the completion now offers `foo bar` and the command executes successfully, the issue is resolved.
--- Proposal B
## Root cause
The completion script obtains the list of recipes with `just --summary`.  
For recipes that live inside a module the summary prints the name as
`foo::bar`.  

When the user tries to invoke such a recipe with the same syntax
(`just foo::bar`) the CLI parser does **not** recognise the `::` separator.
`just` only understands the *space* separator (`just foo bar`) and therefore
fails with:

```
Justfile does not contain recipe `foo::bar`
```

The parser that resolves a recipe name from the command‑line arguments only
splits on whitespace and never looks for `::`, so module recipes cannot be
addressed with the name shown by `--summary`.

## Fix
Add support for the `::` separator when parsing the command‑line recipe name.
The change is made in the CLI argument handling code (`src/cli.rs`).  
If the first argument contains `::`, it is split into module components and
the last component is treated as the recipe name; the preceding components
form the module path. The resulting arguments are then processed exactly as
if the user had typed them separated by spaces.

```diff
--- a/src/cli.rs
+++ b/src/cli.rs
@@
 fn run() -> Result<()> {
@@
-    // The first CLI argument is the recipe name, the rest are its arguments.
-    let mut args = env::args_os().skip(1);
-    let recipe_name = match args.next() {
-        Some(name) => name,
-        None => {
-            // No recipe name supplied → show help / summary.
-            return Justfile::default().run_summary();
-        }
-    };
-
-    // Remaining arguments are passed to the recipe.
-    let recipe_args: Vec<OsString> = args.collect();
-
-    // Resolve the recipe (including module handling) and execute it.
-    let justfile = Justfile::load()?;
-    justfile.run_recipe(&recipe_name, &recipe_args)
+    // -----------------------------------------------------------------------
+    // Parse the command line.
+    // -----------------------------------------------------------------------
+    // `just` accepts two forms for a module recipe:
+    //   * space separated:   `just foo bar arg1 arg2`
+    //   * double‑colon:      `just foo::bar arg1 arg2`
+    // The original implementation only handled the space‑separated form.
+    // We now recognise the `::` syntax as an alias.
+    // -----------------------------------------------------------------------
+
+    // Skip the program name.
+    let mut raw_args = env::args_os().skip(1);
+
+    // The first token may contain `::`.  If it does, split it into the
+    // module path and the actual recipe name.
+    let first = match raw_args.next() {
+        Some(arg) => arg,
+        None => {
+            // No recipe name supplied → show help / summary.
+            return Justfile::default().run_summary();
+        }
+    };
+
+    // Convert the first argument to a UTF‑8 string for easier processing.
+    // If it is not valid UTF‑8 we fall back to the original behaviour
+    // (the Justfile parser will later emit a proper error).
+    let first_str = first.to_string_lossy();
+
+    // `parts` will hold the split components.  For a normal call it will be
+    // a single element (`["foo"]`).  For the `::` form it will be
+    // `["foo", "bar"]` or deeper (`["a", "b", "c"]`).
+    let parts: Vec<&str> = if first_str.contains("::") {
+        first_str.split("::").collect()
+    } else {
+        vec![first_str.as_ref()]
+    };
+
+    // The last component is the actual recipe name; everything before it
+    // forms the modul

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
`all-repos` builds the HTTPS clone URL for a GitHub repository with a hard‑coded domain:

```python
repo_url = f"https://github.com/{owner}/{name}.git"
```

When a user runs the tool against a GitHub Enterprise installation that uses a custom host name (e.g. `git.mycompany.com`), the generated URL points to the public `github.com` site, causing the clone to fail. The code never gives the user a way to override the host name, so Enterprise users cannot use the tool out‑of‑the‑box.

## Fix
* Add a configurable `github_domain` option (default `"github.com"`) to the global `Settings` dataclass.
* Expose the option on the command line as `--github-domain`.
* Use the configured domain when constructing the clone URL in `github_api.py`.

```diff
--- a/all_repos/settings.py
+++ b/all_repos/settings.py
@@
 @dataclass
 class Settings:
@@
     # existing fields …
     # e.g. include_archived: bool = False
+    # Domain used for cloning GitHub repositories.  Defaults to the public
+    # github.com but can be overridden for GitHub Enterprise installations.
+    github_domain: str = "github.com"
@@
     # other existing fields …
 
--- a/all_repos/cli.py
+++ b/all_repos/cli.py
@@
 def build_parser() -> argparse.ArgumentParser:
     parser = argparse.ArgumentParser(
         prog="all-repos",
         description="Run a command across many repositories",
     )
@@
     parser.add_argument(
         "--include-archived",
         action="store_true",
         help="Include archived repositories (default: skip them)",
     )
+    parser.add_argument(
+        "--github-domain",
+        default="github.com",
+        help=(
+            "Domain to use when constructing HTTPS clone URLs for GitHub. "
+            "Useful for GitHub Enterprise installations (default: github.com)."
+        ),
+    )
@@
     # after parsing arguments
-    settings = Settings(
-        include_archived=args.include_archived,
-        # … other settings …
-    )
+    settings = Settings(
+        include_archived=args.include_archived,
+        github_domain=args.github_domain,
+        # … other settings …
+    )
*** End of File
--- a/all_repos/github_api.py
+++ b/all_repos/github_api.py
@@
 class GitHubAPI:
@@
-    def _clone_url(self, owner: str, name: str) -> str:
-        # NOTE: this is deliberately simple – we only need a URL that `git clone`
-        # can understand.  Authentication is handled by the user's git config.
-        return f"https://github.com/{owner}/{name}.git"
+    def _clone_url(self, owner: str, name: str) -> str:
+        """
+        Return the HTTPS clone URL for a repository.
+
+        The domain part is taken from ``self.settings.github_domain`` so that
+        GitHub Enterprise installations can supply a custom host name.
+        """
+        domain = getattr(self.settings, "github_domain", "github.com")
+        return f"https://{domain}/{owner}/{name}.git"
*** End of File
```

*Explanation of changes*

1. **`Settings` dataclass** – a new `github_domain` field with the public default.
2. **CLI parser** – a `--github-domain` flag that populates the new setting.
3. **`GitHubAPI._clone_url`** – builds the URL using the configured domain instead of the hard‑coded `github.com`. The `getattr` fallback keeps the code safe if an older `Settings` object (e.g. from a saved config) is passed.

## How to verify
1. **Unit test for URL generation**  
   ```python
   from all_repos.settings import Settings
   from all_repos.github_api impor

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
--- REAL FIX (ground truth)
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
+export default isString;
diff --git a/packages/plexis/package.json b/packages/plexis/package.json
index bc38600..7e55d13 100644
--- a/packages/plexis/package.json
+++ b/packages/plexis/package.json
@@ -22,7 +22,8 @@
     "@plexis/to-pred": "^0.0.14",
     "@plexis/to-succ": "^0.0.14",
     "@plexis/to-title": "^0.0.14",
-    "@plexis/without-diacritics": "^0.0.14"
+    "@plexis/without-diacritics": "^0.0.14",
+    "@plexis/is-string": "^0.0.14"
   },
   "keywords": [
     "utility",
diff --git a/packages/plexis/src/index.js b/packages/plexis/src/index.js
index 899d511..cd5f0ee 100644
--- a/packages/plexis/src/index.js
+++ b/packages/plexis/src/index.js
@@ -21,3 +21,4 @@ export {
   default as toChicagoTitle
 } from '@plexis/to-chicago';
 export {default as startsWith} from '@plexis/starts-with';
+export {default as isString} from '@plexis/is-string';

--- Proposal A
## Root cause
The repository does not contain an `isString` utility. Consumers expect a module `@plexis/is-string` that returns `true` only for JavaScript string primitives and `false` for everything else. Because the module is missing, imports fail and the feature request cannot be satisfied.

## Fix
Add a new package **`@plexis/is-string`** that follows the existing pattern of the other utilities (e.g., `@plexis/is-alpha-numeric`, `@plexis/tail`). The package exports a single function `isString` which:

1. Returns `false` for `null`/`undefined`/non‑string values.
2. Returns `true` only when `typeof value === 'string'`.

Also expose the function from the monorepo’s top‑level entry point so that `import isString from '@plexis/is-string'` works after the package is built.

```diff
diff --git a/packages/is-string/package.json b/packages/is-string/package.json
new file mode 100644
--- /dev/null
+++ b/packages/is-string/package.json
@@
+{
+  "name": "@plexis/is-string",
+  "version": "1.0.0",
+  "description": "Check whether a value is a string primitive.",
+  "main": "dist/index.js",
+  "module": "dist/index.esm.js",
+  "types": "dist/index.d.ts",
+  "scripts": {
+    "build": "babel src -d dist --copy-files",
+    "test": "npm run build && jest"
+  },
+  "keywords": [
+    "isString",
+    "type",
+    "validation",
+    "string"
+  ],
+  "author": "Plexis",
+  "license": "MIT",
+  "peerDependencies": {},
+  "devDependencies": {
+    "@babel/cli": "^7.0.0",
+    "@babel/core": "^7.0.0",
+    "@babel/preset-env": "^7.0.0",
+    "jest": "^29.0.0"
+  }
+}
\ No newline at end of file
diff --git a/packages/is-string/src/index.js b/packages/is-string/src/index.js
new file mode 100644
--- /dev/null
+++ b/packages/is-string/src/index.js
@@
+/**
+ * Checks whether the supplied value is a string primitive.
+ *
+ * @param {*} value - The value to test.
+ * @returns {boolean} `true` if `value` is a string primitive, otherwise `false`.
+ *
+ * @example
+ *   import isString from '@plexis/is-string';
+ *
+ *   isString('Foo'); // true
+ *   isString(1);     // false
+ *   isString(null);  // false
+ */
+export default function isString(value) {
+  return typeof value === 'string';
+}
+
+// TypeScript definition (optional but kept for consistency with other packages)
+// declare const isString: (value: any) => boolean;
+// export default isString;
diff --git a/packages/plexis/src/index.js b/packages/plexis/src/index.js
index 5c3e1a2..d4f7b9c 100644
--- a/packages/plexis/src/index.js
+++ b/packages/plexis/src/index.js
@@
 export { default as tail } from '@plexis/tail';
 export { default as head } from '@plexis/head';
 export { default as isAlphaNumeric } from '@plexis/is-alpha-numeric';
+export { default as isString } from '@plexis/is-string';
 // other existing exports …
```

*Explanation of the changes*

1. **`packages/is-string/package.json`** – introduces a new package with the same build‑and‑test conventions used by the other utilities.
2. **`packages/is-string/src/index.js`** – implements the `isString` function using a simple `typeof` check.
3. **`packages/plexis/src/index.js`** – re‑exports the new utility from the library’s public API so that consumers can import it directly from the monorepo entry point if they wish.

## How to verify
1. **Run the repository’s test suite** – the new package includes a minimal Jest test configuration (already present in the repo) and the existing CI will now also build the new module.
2. **Add a quick sanity tes
--- Proposal B
## Root cause
The library does not provide a utility to check whether a value is a **string primitive**. Users expect an `isString` function (similar to the existing type‑checking helpers) but it is missing, causing import failures such as `import isString from '@plexis/is-string'`.

## Fix
Add a new module `src/is-string.js` that implements the check, and expose it from the package’s public entry point (`src/index.js`). The implementation follows the same pattern used by the other helpers (e.g., `isNumber`, `isArray`).

```diff
--- a/src/index.js
+++ b/src/index.js
@@
 export { default as isNumber } from './is-number';
 export { default as isArray } from './is-array';
 export { default as isObject } from './is-object';
+export { default as isString } from './is-string';
 
 // other exports …
```

```diff
--- /dev/null
+++ b/src/is-string.js
@@
+/**
+ * Checks whether the supplied value is a **string primitive**.
+ *
+ * @param {*} value - The value to test.
+ * @returns {boolean} `true` if `value` is a string primitive, otherwise `false`.
+ *
+ * @example
+ *   import isString from '@plexis/is-string';
+ *
+ *   isString('Foo'); // true
+ *   isString(1);     // false
+ *   isString(null);  // false
+ */
+export default function isString(value) {
+  return typeof value === 'string';
+}
```

If the project uses a compiled `dist/` folder, the same file should be added there (or the build step will generate it). No other changes are required.

## How to verify
1. **Run the test suite** (if one exists) and ensure all existing tests still pass.  
   ```bash
   npm test
   ```

2. **Add a quick sanity check** (can be done in a REPL or a new test file):
   ```javascript
   import isString from '@plexis/is-string';

   console.assert(isString('Foo') === true, 'primitive string should be true');
   console.assert(isString('') === true, 'empty string should be true');
   console.assert(isString(new String('Bar')) === false, 'String object should be false');
   console.assert(isString(123) === false, 'number should be false');
   console.assert(isString(null) === false, 'null should be false');
   console.assert(isString(undefined) === false, 'undefined should be false');
   console.log('All isString checks passed');
   ```

3. **Build the package** (if a build step exists) and verify that the compiled output includes the new module and that the package’s `main`/`exports` field correctly resolves `@plexis/is-string` to the new file.

4. **Publish locally** (optional) and import it in a separate project to confirm the usage shown in the issue works:
   ```bash
   npm link
   # In another project
   import isString from '@plexis/is-string';
   console.log(isString('test')); // should log true
   ```

All checks passing confirms that the `isString` utility is correctly added and functional.

=== TASK equinor__webviz-config-656 | repo equinor/webviz-config | python
--- Issue
Intelligent loading of plugins through entry_points
`webviz-config` is using a quite standard way of discovering and loading plugins, i.e. https://github.com/equinor/webviz-config/blob/5139c3039f0198ba23b55ec2ca9e7019ae6dd54d/webviz_config/plugins/__init__.py#L51-L53

However, we could change this slightly to easily improve loading time on systems where many plugins are installed (but only a small subset of them are used in an actual user configuration file) by only loading the modules / `entry_points` related to plugins used in the given `yaml` file.

Related to #185.
--- REAL FIX (ground truth)
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
+                    if plugin_name not in ConfigParser.INSTALLED_PLUGINS:
                         raise ParserError(
                             f"{terminal_colors.RED}{terminal_colors.BOLD}"
                             "You have included a plugin with "
                             f"name `{plugin_name}` in your "
-                            "configuration file. This is not a "
-                            "standard plugin."
+                            "configuration file. This is not an "
+                            "installed plugin."
                             f"{terminal_colors.END}"
                         )
 
diff --git a/webviz_config/_docs/_build_docs.py b/webviz_config/_docs/_build_docs.py
index 6313ebd..2439d8c 100644
--- a/webviz_config/_docs/_build_docs.py
+++ b/webviz_config/_docs/_build_docs.py
@@ -23,7 +23,6 @@ import jinja2
 import webviz_config.plugins
 from webviz_config.plugins import PLUGIN_METADATA, PLUGIN_PROJECT_METADATA
 from .._config_parser import SPECIAL_ARGS
-from ..utils._get_webviz_plugins import _get_webviz_plu
--- Proposal A
## Root cause
`webviz-config` loads **all** registered plugin entry‑points at start‑up:

```python
for ep in pkg_resources.iter_entry_points('webviz_config.plugins'):
    plugins[ep.name] = ep.load()
```

When a system has many installed plugins this import step becomes a noticeable bottleneck, even though a typical user configuration (`.yaml`) only uses a handful of them.  

The code never looks at the user‑provided configuration before importing the plugins, so unnecessary imports are performed on every run.

## Fix
*Make plugin loading lazy – only import the entry‑points that are actually referenced in the supplied YAML configuration.*

### 1. `webviz_config/plugins/__init__.py`
```diff
@@
-import pkg_resources
-
-def load_plugins():
-    """Load all plugins registered via entry points."""
-    plugins = {}
-    for ep in pkg_resources.iter_entry_points('webviz_config.plugins'):
-        plugins[ep.name] = ep.load()
-    return plugins
+import importlib
+import importlib.metadata
+from typing import Dict, Iterable, Optional, Set
+
+# --------------------------------------------------------------------------- #
+# Public API
+# --------------------------------------------------------------------------- #
+
+def load_plugins(used_plugins: Optional[Iterable[str]] = None) -> Dict[str, object]:
+    """
+    Load plugins registered via the ``webviz_config.plugins`` entry‑point group.
+
+    Parameters
+    ----------
+    used_plugins:
+        An optional iterable of plugin names that should be loaded.  If ``None``
+        (the default) **all** discovered plugins are loaded – this preserves the
+        original behaviour for callers that do not care about optimisation.
+
+    Returns
+    -------
+    dict
+        Mapping from plugin name to the loaded plugin object.
+    """
+    # Resolve entry‑points once – this works for both importlib.metadata (Python ≥3.8)
+    # and the older pkg_resources fallback.
+    try:
+        eps = {
+            ep.name: ep
+            for ep in importlib.metadata.entry_points().select(group='webviz_config.plugins')
+        }
+    except Exception:  # pragma: no cover – fallback for very old environments
+        import pkg_resources
+        eps = {ep.name: ep for ep in pkg_resources.iter_entry_points('webviz_config.plugins')}
+
+    # If the caller did not restrict the set, load everything (original behaviour)
+    if used_plugins is None:
+        target_names: Set[str] = set(eps.keys())
+    else:
+        target_names = set(used_plugins)
+
+    plugins: Dict[str, object] = {}
+    for name in target_names:
+        if name not in eps:
+            raise ImportError(f\"Plugin '{name}' is not registered via the 'webviz_config.plugins' entry‑point.\")
+        plugins[name] = eps[name].load()
+    return plugins
+
+# Backwards compatibility – the original name without arguments is still valid.
+def load_all_plugins() -> Dict[str, object]:
+    \"\"\"Legacy wrapper that loads *all* plugins.  Kept for external callers.\n+\n+    The internal code now uses :func:`load_plugins` directly.\n+    \"\"\"\n+    return load_plugins()
```

### 2. `webviz_config/config.py` (or the module that reads the user YAML)
```diff
@@
-import yaml
-from .plugins import load_plugins
+import yaml
+from .plugins import load_plugins
+from typing import Any, Dict, Set
@@
-def load_config(yaml_path: str) -> Dict[str, Any]:
-    \"\"\"Read a WebViz configuration file and return a dictionary.\n-\n-    The function also loads all
--- Proposal B
## Root cause
`webviz_config/plugins/__init__.py` eagerly imports **all** entry‑point plugins at import time:

```python
# current (simplified)
for ep in pkg_resources.iter_entry_points("webviz_plugins"):
    ep.load()
```

When a user runs a configuration that only uses a handful of plugins, the interpreter still spends time importing every installed plugin. On systems with many third‑party plugins this adds noticeable start‑up latency.

The loader has no way to know which plugins are actually required for a particular YAML configuration, because the import happens **before** the configuration file is parsed.

## Fix
1. **Make the loader lazy** – expose a function `load_plugins(required: Iterable[str] | None = None)` that only loads the entry‑points whose name appears in `required`.  
2. **Call the loader after the YAML file has been parsed** – extract the set of plugin identifiers used in the configuration and pass it to `load_plugins`.  
3. Preserve the original behaviour (load everything) when the function is called without arguments, so existing imports that rely on the side‑effect continue to work.

### Files changed
* `webviz_config/plugins/__init__.py` – replace eager import with a lazy loader function.
* `webviz_config/config.py` (or the module that reads the YAML) – after parsing the configuration, compute the required plugin names and invoke the new loader.

Below is a **unified diff** with the best‑guess locations and code.

```diff
--- a/webviz_config/plugins/__init__.py
+++ b/webviz_config/plugins/__init__.py
@@
-import pkg_resources
-
-# Eagerly import all plugins registered under the "webviz_plugins" entry point.
-# This has the side‑effect of registering the plugin classes in the global
-# plugin registry.
-for ep in pkg_resources.iter_entry_points("webviz_plugins"):
-    try:
-        ep.load()
-    except Exception as exc:  # pragma: no cover – defensive
-        # Import errors in optional plugins should not break the whole app.
-        # They are logged for debugging purposes.
-        import logging
-
-        logging.getLogger(__name__).warning(
-            "Failed to load webviz plugin %s: %s", ep.name, exc
-        )
+import pkg_resources
+import logging
+from typing import Iterable, Set, Optional
+
+_logger = logging.getLogger(__name__)
+
+# ---------------------------------------------------------------------------
+# Lazy plugin loader
+# ---------------------------------------------------------------------------
+def _load_entry_point(ep: pkg_resources.EntryPoint) -> None:
+    """Helper that loads a single entry‑point and logs failures."""
+    try:
+        ep.load()
+    except Exception as exc:  # pragma: no cover – defensive
+        _logger.warning("Failed to load webviz plugin %s: %s", ep.name, exc)
+
+
+def load_plugins(required: Optional[Iterable[str]] = None) -> None:
+    """
+    Load plugins registered under the ``webviz_plugins`` entry‑point.
+
+    Parameters
+    ----------
+    required:
+        An optional iterable of plugin names that should be loaded.
+        If ``None`` (default) **all** discovered plugins are loaded – this
+        preserves the historic behaviour for callers that relied on the side‑
+        effect of importing this module.
+    """
+    # Resolve the set of names we are interested in.  ``None`` means “load all”.
+    required_set: Optional[Set[str]]
+    if required is None:
+        required_set = None
+    else:
+        required_set = set(required)
+
+    for e

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
`blink.start()` eventually fails with a `json.decoder.JSONDecodeError`.  
The failure happens when the authentication token stored in *cred.json* expires.  
The Blink API then returns a **401/403** response with an empty (non‑JSON) body.  
`blinkpy.auth.Auth.validate_response()` blindly calls `response.json()` assuming the
payload is always JSON, so the empty body raises the `JSONDecodeError` and the
client aborts instead of refreshing the token or raising a clear error.

## Fix
* Make `Auth.validate_response()` robust:
  * Detect non‑JSON responses (empty body, HTML, 401/403, etc.).
  * If the status code indicates an authentication problem (`401` or `403`),
    automatically try to **refresh** the token (or re‑login) and retry the
    original request.
  * Raise a descriptive `BlinkException` when the response truly cannot be
    parsed.
* Adjust the low‑level `http_get`/`http_post` helpers to propagate the original
  request information so it can be retried after a refresh.

```diff
--- a/blinkpy/auth.py
+++ b/blinkpy/auth.py
@@
 import json
 import logging
 import os
 from typing import Any, Dict, Optional
+from requests.exceptions import RequestException
+from .exceptions import BlinkException   # existing exception class
 
 logger = logging.getLogger(__name__)
@@
-    def validate_response(self, response, json_resp):
-        """Validate the response from the Blink API."""
-        json_data = response.json()
-        if response.status_code != 200:
-            raise BlinkException(
-                f"Error from Blink API: {response.status_code} - {json_data}"
-            )
-        return json_data
+    def validate_response(self, response, json_resp, original_kwargs=None):
+        """
+        Validate the response from the Blink API.
+
+        The original implementation assumed every successful HTTP response
+        contained JSON.  When the authentication token expires Blink returns a
+        401/403 with an empty body, causing ``response.json()`` to raise a
+        ``json.JSONDecodeError``.  This method now:
+
+        * Safely attempts to decode JSON.
+        * Detects authentication‑related status codes (401/403) and tries to
+          refresh the token automatically.
+        * Retries the original request once after a successful refresh.
+        * Raises a clear ``BlinkException`` for all other error conditions.
+        """
+        # ------------------------------------------------------------------
+        # Helper to actually parse JSON, handling empty bodies gracefully.
+        # ------------------------------------------------------------------
+        def _parse_json(resp):
+            try:
+                return resp.json()
+            except json.JSONDecodeError:
+                # Empty or non‑JSON payload – treat as ``None`` for further logic.
+                return None
+
+        json_data = _parse_json(response)
+
+        # ------------------------------------------------------------------
+        # If we got an authentication error, try to refresh the token and
+        # retry the original request (only once to avoid infinite loops).
+        # ------------------------------------------------------------------
+        if response.status_code in (401, 403):
+            logger.debug(
+                "Authentication error (%s) – attempting token refresh",
+                response.status_code,
+            )
+            try:
+                self.refresh_token()
+        
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