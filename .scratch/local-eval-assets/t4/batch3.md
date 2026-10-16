

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
The library already provides a low‑level `dispatch_event(event_name, **payload)` function that is called manually at the start and end of many public methods (e.g., solver submission, polling, etc.).  This results in duplicated boilerplate:

```python
dispatch_event('sample_ising.start', args=args, kwargs=kwargs)
result = self.sample_ising(*args, **kwargs)
dispatch_event('sample_ising.end', result=result, args=args, kwargs=kwargs)
```

There is no reusable abstraction for this pattern, so developers have to repeat the same two calls for every method they want to instrument.  The missing abstraction is an **event‑dispatch decorator** that automatically performs the “before” and “after” dispatches, passing the original arguments and the return value.

## Fix
Add a generic decorator `event_dispatch` in the events module.  
The decorator:

* Takes the base event name (e.g., `'sample_ising'`).
* Calls `dispatch_event` **before** the wrapped function with `phase='start'` and the original `args`/`kwargs`.
* Calls `dispatch_event` **after** the wrapped function with `phase='end'`, the original `args`/`kwargs`, and the function’s return value (`result`).
* Silently swallows any exception raised by `dispatch_event` so that telemetry failures never break user code.
* Preserves the wrapped function’s signature and metadata via `functools.wraps`.

```diff
diff --git a/dwave/cloud/events.py b/dwave/cloud/events.py
index e3b1c2a..f7a9d4c 100644
--- a/dwave/cloud/events.py
+++ b/dwave/cloud/events.py
@@
 import logging
 import threading
+import functools
 from typing import Any, Callable, Dict, Optional
 
 __all__ = ['dispatch_event', 'event_dispatch']
 
 _event_lock = threading.Lock()
 _event_handlers: Dict[str, Callable[[Dict[str, Any]], None]] = {}
@@
 def dispatch_event(event_name: str, **payload: Any) -> None:
     """
     Dispatch an event to the registered handler (if any).
@@
     if handler:
         handler(payload)
 
+def event_dispatch(event_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
+    """
+    Decorator that automatically dispatches an event before and after the wrapped
+    callable is executed.
+
+    The decorator emits two events:
+
+    * ``<event_name>.start`` – dispatched **before** the function runs.  The
+      payload contains ``phase='start'`` together with the positional ``args``
+      and keyword ``kwargs`` that were supplied to the function.
+    *
+      ``<event_name>.end`` – dispatched **after** the function completes.  The
+      payload contains ``phase='end'``, the original ``args``/``kwargs`` and the
+      function's return value under the key ``result``.
+
+    Any exception raised by the underlying ``dispatch_event`` implementation is
+    caught and logged at ``DEBUG`` level so that telemetry failures never affect
+    the normal execution flow.
+
+    Example
+    -------
+    >>> from dwave.cloud.events import event_dispatch
+    >>> @event_dispatch('sample_ising')
+    ... def sample_ising(*args, **kwargs):
+    ...     return "dummy"
+
+    The above will emit ``sample_ising.start`` before the body runs and
+    ``sample_ising.end`` after it returns.
+    """
+
+    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
+        @functools.wraps(func)
+        def wrapper(*args: Any, **kwargs: Any) -> Any:
+            # --- before execution -------------------------------------------------
+            try:
+                dispatch_event(f"{event_name}.start",
--- Proposal B
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

--- REAL FIX (ground truth)
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
 
 # Make the NCI complex and find the lowest energy structure

--- Proposal A
## Root cause
`NCIComplex` creates each conformer by copying the parent complex and writing the geometry to a file whose name is taken directly from `self.name`.  
Because the name is never altered for the individual copies, every conformer is written to the **same** file (e.g. `water_trimer.xyz`).  
When the optimiser later scans the directory it sees only one file, so all but the first conformer are silently ignored.

## Fix
Add a small helper that generates a unique name for every conformer and use it whenever a conformer file is written.  
The change is confined to `autode/species.py` (the location of `NCIComplex`).  

```diff
diff --git a/autode/species.py b/autode/species.py
index 7c9e1a2..d4f3b8c 100644
--- a/autode/species.py
+++ b/autode/species.py
@@
 class NCIComplex(Complex):
@@
     def __init__(self, *species, name: str = "NCI_complex", **kwargs):
         """
         Initialise an NCI complex from a collection of Species objects.
@@
         super().__init__(*species, name=name, **kwargs)
+
+        # Counter used to give each generated conformer a unique filename.
+        # It is **not** part of the public API – it only serves the internal
+        # conformer‑generation machinery.
+        self._conformer_counter: int = 0
+
+    # ---------------------------------------------------------------------
+    # Helper methods
+    # ---------------------------------------------------------------------
+    def _next_conformer_name(self) -> str:
+        """
+        Return a unique name for the next conformer that will be generated.
+
+        The base name of the complex (``self.name``) is suffixed with an
+        underscore and a zero‑based index, e.g. ``water_trimer_0``,
+        ``water_trimer_1`` … This name is used for the temporary XYZ file
+        that is handed to the external conformer‑generation routine.
+        """
+        name = f"{self.name}_{self._conformer_counter}"
+        self._conformer_counter += 1
+        return name
@@
     def find_lowest_energy_conformer(self, lmethod, **kwargs):
@@
-        for i in range(num_trials):
-            # generate a random orientation of the complex
-            trial_complex = self._random_orientation()
-
-            # write the trial geometry to a temporary XYZ file
-            xyz_path = self._write_xyz(trial_complex, suffix=".xyz")
-
-            # optimise the trial geometry with the low‑level method
-            opt = lmethod.optimise(trial_complex, **kwargs)
-
-            # keep the lowest‑energy conformer
-            if opt.energy < best_energy:
-                best_energy = opt.energy
-                best_conformer = opt
-
-        self._set_conformer(best_conformer)
+        for i in range(num_trials):
+            # -----------------------------------------------------------------
+            # 1️⃣  Generate a random orientation of the complex
+            # -----------------------------------------------------------------
+            trial_complex = self._random_orientation()
+
+            # -----------------------------------------------------------------
+            # 2️⃣  Write the trial geometry to a **unique** temporary XYZ file.
+            #     The filename now contains a counter so that each trial gets its
+            #     own file on disk.
+            # -----------------------------------------------------------------
+            unique_name = self._next_conformer_name()
+            xyz_path = self._write_xyz(trial_complex, nam
--- Proposal B
## Root cause
`NCIComplex` creates all generated conformers with the **same molecule name** (the name of the parent complex).  
When the optimisation routine writes the XTB input/output files it uses this name to build the
filenames, so every conformer overwrites the previous one.  
Consequently only a single XTB output file is left in the *conformers* directory and the
optimisation loop effectively sees only the first conformer.

The bug lives in the method that builds the conformer objects – it never appends a unique
identifier (e.g. an index) to the molecule name before the files are written.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/autode/species/nci_complex.py
+++ b/autode/species/nci_complex.py
@@
 class NCIComplex(Complex):
@@
     def _generate_conformers(self) -> List[Molecule]:
         """
         Generate a set of conformers for the NCI complex by sampling points on a
         sphere around the centre of mass and applying random rotations.
@@
-        conformers = []
-        for i in range(Config.num_complex_random_rotations):
-            # generate a random rotation matrix
-            rot = random_rotation_matrix()
-            for point in sphere_points:
-                # translate the second fragment to the sampled point and rotate
-                frag2 = self.fragments[1].copy()
-                frag2.translate(point)
-                frag2.rotate(rot)
-
-                # build the combined molecule
-                mol = self._combine_fragments(self.fragments[0], frag2)
-                mol.name = self.name          # <-- bug: same name for every conformer
-                conformers.append(mol)
-
-        return conformers
+        conformers = []
+        # Counter used to give each conformer a unique name / file stem
+        conf_counter = 0
+
+        for i in range(Config.num_complex_random_rotations):
+            # generate a random rotation matrix
+            rot = random_rotation_matrix()
+            for point in sphere_points:
+                # translate the second fragment to the sampled point and rotate
+                frag2 = self.fragments[1].copy()
+                frag2.translate(point)
+                frag2.rotate(rot)
+
+                # build the combined molecule
+                mol = self._combine_fragments(self.fragments[0], frag2)
+
+                # -----------------------------------------------------------------
+                # **Fix** – give each conformer a distinct name so that file I/O
+                # does not overwrite previous results.
+                # The name is the parent complex name plus a zero‑padded index.
+                # -----------------------------------------------------------------
+                mol.name = f\"{self.name}_conf{conf_counter:03d}\"
+                conf_counter += 1
+
+                conformers.append(mol)
+
+        return conformers
```

### What the patch does
1. Introduces a `conf_counter` that increments for every generated conformer.
2. Sets the molecule name to `"{parent_name}_conf{index:03d}"` (e.g. `water_trimer_conf000`,
   `water_trimer_conf001`, …).  
   This guarantees a unique stem for every XTB input/output file.
3. Leaves the rest of the generation logic untouched.

If the project stores the conformer list elsewhere (e.g. `self.conformers`), the unique name
propagates automatically to the optimisation routine because it uses the molecule’s `name`
attribute when constructing fil

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
`ModalResult.plot_mode_2d()` forwards the call to `Shape.plot_mode_2d()` but its signature does **not** expose the `orientation` argument that `Shape.plot_mode_2d()` accepts. Consequently users cannot control the orientation of the 2‑D plot when working with modal analysis results, leading to the reported limitation.

## Fix
Add an `orientation` keyword argument to `ModalResult.plot_mode_2d()` (and propagate it to the underlying `Shape.plot_mode_2d()` call). Update the docstring accordingly.

```diff
diff --git a/ross/modal.py b/ross/modal.py
index 3a5c9f2..e7b1d4a 100644
--- a/ross/modal.py
+++ b/ross/modal.py
@@
 class ModalResult:
@@
-    def plot_mode_2d(self, mode, **kwargs):
-        \"\"\"Plot a mode shape in 2‑D.\n-\n-        Parameters\n-        ----------\n-        mode : int\n-            Mode number (starting at 0).\n-        **kwargs : dict\n-            Additional arguments passed to :meth:`Shape.plot_mode_2d`.\n-        \"\"\"\n-        self.shape.plot_mode_2d(mode, **kwargs)\n+    def plot_mode_2d(self, mode, orientation=None, **kwargs):
+        \"\"\"Plot a mode shape in 2‑D.\n+\n+        This method is a thin wrapper around :meth:`Shape.plot_mode_2d`.  The\n+        original implementation did not expose the ``orientation`` keyword\n+        that the underlying ``Shape`` method accepts, making it impossible to\n+        change the plot orientation (e.g. ``'y'``) when visualising modal\n+        results.  The fix adds the ``orientation`` argument and forwards it to\n+        the ``Shape`` method.\n+\n+        Parameters\n+        ----------\n+        mode : int\n+            Mode number (starting at 0).\n+        orientation : {\"x\", \"y\"}, optional\n+            Desired orientation of the 2‑D plot.  If ``None`` (default) the\n+            orientation defined by :class:`Shape` is used.  This argument is\n+            passed directly to :meth:`Shape.plot_mode_2d`.\n+        **kwargs : dict\n+            Additional keyword arguments passed to :meth:`Shape.plot_mode_2d`.\n+        \"\"\"\n+        # Build the argument dictionary for the underlying Shape method.\n+        shape_kwargs = dict(kwargs)\n+        if orientation is not None:\n+            shape_kwargs[\"orientation\"] = orientation\n+\n+        self.shape.plot_mode_2d(mode, **shape_kwargs)\n*** End of File
```

## How to verify
1. **Unit test** – add a test (or extend an existing one) that calls the new API:

```python
def test_plot_mode_2d_orientation(monkeypatch):
    import matplotlib.pyplot as plt
    from ross import Rotor, ModalResult

    # Build a minimal rotor (two simple shaft elements) and run modal analysis.
    rotor = Rotor([...])                     # use a simple valid rotor definition
    modal = rotor.run_modal(speed=0, n_modes=1)

    # Patch Shape.plot_mode_2d to capture the arguments it receives.
    called = {}
    def fake_plot_mode_2d(self, mode, orientation='x', **kw):
        called['mode'] = mode
        called['orientation'] = orientation
        called['kw'] = kw

    monkeypatch.setattr(modal.shape.__class__, "plot_mode_2d", fake_plot_mode_2d)

    # Call the method with an explicit orientation.
    modal.plot_mode_2d(0, orientation='y', linewidth=2)

    assert called['mode'] == 0
    assert called['orientation'] == 'y'
    assert called['kw']['linewidth'] == 2
```

2. **Manual check** – run an interactive session:

```python
>>> modal = rotor.run_modal(...)
>>> modal.plot_mode_2d(0, orientation='y')   # should pro
--- Proposal B
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
`dvc` always pipes the output of several commands (`dag`, `exp show`, `list`, `stage list`) through a pager (usually **less**).  
The pager is invoked unconditionally, even when the output fits on a single terminal screen.  
There is also no global way for a user to disable paging for any command.

## Fix
* Add a global `--no-pager` flag to the top‑level parser.
* Extend the pager utility (`dvc/utils/pager.py`) so that it:
  * skips paging when `--no‑pager` is supplied,
  * automatically skips paging when the captured output fits into the current terminal height.
* Update the commands that currently use the pager to forward the new flag.

```diff
diff --git a/dvc/cli.py b/dvc/cli.py
index 2ba52bb3..c3f9e1a4 100644
--- a/dvc/cli.py
+++ b/dvc/cli.py
@@
 def parse_args(argv=None):
     parent_parser = argparse.ArgumentParser(add_help=False)
@@
-    parent_parser.add_argument(
-        "-v",
-        "--verbose",
-        action="store_true",
-        help="Increase output verbosity.",
-    )
+    parent_parser.add_argument(
+        "-v",
+        "--verbose",
+        action="store_true",
+        help="Increase output verbosity.",
+    )
+
+    # New global flag to disable paging of command output.
+    parent_parser.add_argument(
+        "--no-pager",
+        action="store_true",
+        default=False,
+        help="Do not pipe command output through a pager (less).",
+    )
*** End of File
diff --git a/dvc/utils/pager.py b/dvc/utils/pager.py
index e3b1c2d5..a7f4c9e1 100644
--- a/dvc/utils/pager.py
+++ b/dvc/utils/pager.py
@@
-import os
-import subprocess
-import sys
-
-def pager(pager_cmd=None):
-    \"\"\"Yield a file‑like object that writes to a pager if appropriate.\"
-    if not pager_cmd:
-        pager_cmd = os.environ.get("DVC_PAGER", "less -FRX")
-
-    if not sys.stdout.isatty():
-        # Not a TTY – no paging.
-        yield sys.stdout
-        return
-
-    with subprocess.Popen(
-        pager_cmd,
-        shell=True,
-        stdin=subprocess.PIPE,
-        stdout=sys.stdout,
-        stderr=sys.stderr,
-    ) as proc:
-        try:
-            yield proc.stdin
-        finally:
-            proc.stdin.close()
-            proc.wait()
+import io
+import os
+import shutil
+import subprocess
+import sys
+from contextlib import contextmanager
+
+
+@contextmanager
+def pager(pager_cmd: str | None = None, *, no_pager: bool = False):
+    """
+    Context manager that writes to a pager **only when needed**.
+
+    * If ``no_pager`` is ``True`` the output is written directly to ``sys.stdout``.
+    * If the output fits into the current terminal height the pager is skipped.
+    * Otherwise the output is piped through the pager (default ``less -FRX`` or the
+      ``DVC_PAGER`` environment variable).
+    """
+    # Respect the explicit ``--no-pager`` flag or a non‑tty stdout.
+    if no_pager or not sys.stdout.isatty():
+        yield sys.stdout
+        return
+
+    # Capture the output first so we can decide whether paging is necessary.
+    buffer = io.StringIO()
+    try:
+        yield buffer
+    finally:
+        content = buffer.getvalue()
+        # Determine terminal size – fall back to 24 lines if unavailable.
+        rows = shutil.get_terminal_size(fallback=(80, 24)).lines
+        if content.count("\n") < rows:
+            # Output fits on screen – write directly.
+            sys.stdout.write(content)
+            return
+
+        # Output is larger than a screen – invoke the pager.
+      
--- Proposal B
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
`isort` merges two `from … import (…)` statements that import from the same module.  
During the merge the *statement‑level* comment (`self.comment`) is overwritten by the
comment of the later statement.  
Because the inline comment after each imported name is stored as the statement
comment (the per‑name comment is lost), the first comment (`# NOT DRY`) is replaced
by the second one (`# DRY`). The result is that both imported names end up with the
same comment.

## Fix
* Preserve per‑name comments when merging import statements.
* Only set the statement‑level comment if the target statement does **not** already
  have one; never overwrite an existing comment with the comment from the merged
  statement.

```diff
diff --git a/isort/statement.py b/isort/statement.py
index 7a1b2c3..e4f5d6a 100644
--- a/isort/statement.py
+++ b/isort/statement.py
@@
 class ImportFrom:
@@
     def __init__(self, name: str, as_name: Optional[str] = None,
                  comment: Optional[str] = None):
         self.name = name
         self.as_name = as_name
-        self.comment = comment
+        # Inline comment that belongs to this specific imported name.
+        # ``None`` means the name has no dedicated comment.
+        self.comment = comment
@@
 class ImportStatement:
@@
-    def merge(self, other: \"ImportStatement\") -> None:
-        \"\"\"Merge another ImportStatement into this one.\"\"\"
-        # Merge the imported names
-        self.names.extend(other.names)
-        # If the other statement has a comment, prefer it
-        if other.comment:
-            self.comment = other.comment
+    def merge(self, other: \"ImportStatement\") -> None:
+        \"\"\"Merge another :class:`ImportStatement` into this one.\n+\n+        The merge is performed **without** losing any per‑name comments.\n+        ``self`` already contains the first occurrence of the import; ``other``\n+        represents a later, duplicate import that is being folded into the\n+        first one.\n+\n+        * All imported names from ``other`` are appended to ``self.names`` – the\n+          ``ImportFrom`` objects already carry their own ``comment`` attribute,\n+          so they keep the inline comment that originally followed the name.\n+        * The *statement* comment (a comment that appears after the closing\n+          parenthesis) is only set if ``self`` does not already have one.  This\n+          prevents the later comment from overwriting the earlier one, which\n+          caused the bug where both names ended up with the same comment.\n+        \"\"\"\n+        # 1️⃣ Preserve per‑name comments – they travel with the ``ImportFrom``\n+        #    objects, so simply extending the list is sufficient.\n+        self.names.extend(other.names)\n+\n+        # 2️⃣ Preserve the original statement‑level comment.  Only adopt the\n+        #    comment from ``other`` when ``self`` has none.\n+        if not self.comment and other.comment:\n+            self.comment = other.comment
```

If the repository stores the merge logic elsewhere (e.g. in `identify.py`), the same
principle is applied there. Below is the complementary change that ensures the\nsame behaviour when imports are combined during the identification phase.

```diff
diff --git a/isort/identify.py b/isort/identify.py
index a1b2c3d..d4e5f6g 100644
--- a/isort/identify.py
+++ b/isort/identify.py
@@
-            if import_statement.from_import:
-                # combine duplicate imports from the sa
--- Proposal B
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
**## Root cause**

The AWS provider plugin does not translate the new `environment` configuration keys (both at the provider level and the function level) into the CloudFormation `AWS::Lambda::Function` resources. Consequently, users cannot expose custom or generated values to their Lambda functions via environment variables, even though the configuration syntax is accepted by the framework.

**## Fix (unified diff)**

```diff
diff --git a/lib/plugins/aws/lib/functionCompile.js b/lib/plugins/aws/lib/functionCompile.js
index 7c9a1f2..e3b4d8a 100644
--- a/lib/plugins/aws/lib/functionCompile.js
+++ b/lib/plugins/aws/lib/functionCompile.js
@@
   compileFunction(functionName) {
     const functionObject = this.serverless.service.getFunction(functionName);
     const functionLogicalId = this.provider.naming.getLambdaLogicalId(functionName);
@@
     const functionResource = {
       Type: 'AWS::Lambda::Function',
       Properties: {
         FunctionName: functionObject.name,
         Handler: functionObject.handler,
         Role: functionObject.role || this.provider.getIamRoleArn(),
         Runtime: functionObject.runtime || this.provider.getRuntime(),
         Code: {
           S3Bucket: this.provider.getDeploymentBucketName(),
           S3Key: this.provider.getDeploymentPrefix() + '/' + this.provider.getArtifactFileName(),
         },
         Timeout: functionObject.timeout || this.provider.getFunctionTimeout(),
         MemorySize: functionObject.memorySize || this.provider.getFunctionMemorySize(),
         Description: functionObject.description,
         TracingConfig: functionObject.tracing && { Mode: functionObject.tracing },
         Tags: this.provider.getTags(),
-        // NOTE: Environment variables are added later (see below)
       },
     };
 
+    // -------------------------------------------------------------------------
+    // Environment variable handling
+    // -------------------------------------------------------------------------
+    // 1️⃣ Provider‑level environment variables are applied to **all** functions.
+    // 2️⃣ Function‑level environment variables override provider‑level ones.
+    // 3️⃣ Values may be plain strings **or** CloudFormation intrinsic functions
+    //    (e.g. Ref, Fn::GetAtt).  We therefore keep the raw value without any
+    //    transformation.
+    const providerEnv = this.serverless.service.provider.environment || {};
+    const functionEnv = functionObject.environment || {};
+
+    // Merge with function‑level taking precedence
+    const mergedEnv = Object.assign({}, providerEnv, functionEnv);
+
+    if (Object.keys(mergedEnv).length > 0) {
+      functionResource.Properties.Environment = { Variables: mergedEnv };
+    }
+
     // -------------------------------------------------------------------------
     // Existing compilation steps (events, layers, etc.) remain unchanged
     // -------------------------------------------------------------------------
 
     // Compile events (http, s3, schedule, …)
```

*Explanation of the changes*

1. **Read provider‑level `environment`** (`this.serverless.service.provider.environment`).
2. **Read function‑level `environment`** (`functionObject.environment`).
3. **Merge** them, letting function‑level entries override provider‑level ones.
4. **If any variables exist**, add an `Environment` property to the Lambda CloudFormation resource with the merged map.
5. The merge keeps values exactly as supplied, so CloudFormation intrinsic functi
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
--- REAL FIX (ground truth)
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

--- Proposal A
## Root cause
`jsx-no-comment-textnodes` checks JSX text nodes for comment‑like patterns (e.g. `//` or `/* … */`).  
The rule first **decodes HTML entities** before performing the check, so an entity such as `&#x2F;` (the slash character) is turned into `/`.  
Consequently a text node like  

```html
<pre>&#x2F;&#x2F; TODO …</pre>
```  

is decoded to `// TODO …` and the rule incorrectly reports it as a comment, even though the source contains the entity representation and is **not** a real comment.

## Fix
Skip the comment detection when the original source text contains an HTML entity that represents a slash. This keeps the rule’s original behaviour for real comments while preventing false positives for encoded slashes.

```diff
--- a/lib/rules/jsx-no-comment-textnodes.js
+++ b/lib/rules/jsx-no-comment-textnodes.js
@@
 module.exports = {
   meta: {
@@
   create(context) {
-    const sourceCode = context.getSourceCode();
-
-    /**
-     * Determines whether a given JSXText node looks like a comment.
-     * @param {ASTNode} node The JSXText node.
-     * @returns {boolean} True if it looks like a comment.
-     */
-    function isComment(node) {
-      // Decode HTML entities because JSXText values are already decoded.
-      const decoded = sourceCode.getText(node);
-
-      // Trim leading whitespace before checking for comment markers.
-      const trimmed = decoded.trimStart();
-
-      return (
-        trimmed.startsWith('//') ||
-        trimmed.startsWith('/*') ||
-        trimmed.startsWith('*')
-      );
-    }
-
-    return {
-      JSXText(node) {
-        if (isComment(node)) {
-          context.report({
-            node,
-            messageId: 'unexpected',
-          });
-        }
-      },
-    };
+    const sourceCode = context.getSourceCode();
+
+    /**
+     * Determines whether a given JSXText node looks like a comment.
+     * The rule decodes HTML entities before checking, but we must ignore
+     * cases where the original source contains an entity that represents a slash
+     * (e.g. &#x2F; or &#47;). Those are not real comments.
+     *
+     * @param {ASTNode} node The JSXText node.
+     * @returns {boolean} True if it looks like a comment.
+     */
+    function isComment(node) {
+      // The raw text as it appears in the source (including entities).
+      const raw = sourceCode.getText(node);
+
+      // If the raw source contains an HTML entity for a slash, we treat it
+      // as *not* a comment, because the author deliberately encoded the slash.
+      // This covers both hexadecimal (&#x2F;) and decimal (&#47;) forms.
+      if (/&#x2F;|&#47;/i.test(raw)) {
+        return false;
+      }
+
+      // Decode HTML entities – JSXText values are already decoded by the parser,
+      // but we keep this step for clarity and future‑proofing.
+      const decoded = raw;
+
+      // Trim leading whitespace before checking for comment markers.
+      const trimmed = decoded.trimStart();
+
+      return (
+        trimmed.startsWith('//') ||
+        trimmed.startsWith('/*') ||
+        trimmed.startsWith('*')
+      );
+    }
+
+    return {
+      JSXText(node) {
+        if (isComment(node)) {
+          context.report({
+            node,
+            messageId: 'unexpected',
+          });
+        }
+      },
+    };
   },
 };
```

### What changed
1. **Early exit for encoded slashes** – If the raw source contains `&#x2F;` or `&#47;`, the function returns `false` and the rule does not report.
2. A
--- Proposal B
## Root cause
`jsx-no-comment-textnodes` looks for comment markers (`//` or `/*`) by testing the **raw** text of a JSX literal / JSXText node with the regular expression  

```js
/^\s*\/(\/|\*)/m
```  

When a developer writes the slashes as HTML entities (`&#x2F;` or `&#47;`), the node’s value is the string `"&#x2F;&#x2F; …"` – it does **not** contain the literal `/` characters.  
The rule still flags the node because the regular expression is applied **before** the entities are decoded, causing a false‑positive warning for valid JSX that only displays the characters “//”.

## Fix
1. Decode the common HTML‑entity representations of a forward slash before running the comment‑detection regex.  
2. Apply the same decoding for both `Literal` (used by older parsers) and `JSXText` nodes.  
3. Keep the existing logic that ignores attribute values and expression containers.

```diff
diff --git a/lib/rules/no-comment-textnodes.js b/lib/rules/no-comment-textnodes.js
index 7a3bfc58..c1d9e7ab 100644
--- a/lib/rules/no-comment-textnodes.js
+++ b/lib/rules/no-comment-textnodes.js
@@
 module.exports = function(context) {
 
   // ----------------------------------------------------------------------
   // Helpers
   // ----------------------------------------------------------------------
+  /**
+   * Replace HTML entity representations of a forward slash with the actual
+   * character so that comment detection works correctly for JSX that uses
+   * entities such as &#x2F; or &#47;.
+   *
+   * @param {string} str The raw node value.
+   * @returns {string} The value with slash entities normalised.
+   */
+  function normaliseSlashEntities(str) {
+    // &#x2F;  – hexadecimal entity for '/' (common in JSX)
+    // &#47;   – decimal entity for '/' (also possible)
+    // The regex is case‑insensitive and global.
+    return str.replace(/&#x2F;|&#47;/gi, '/');
+  }
 
   // ----------------------------------------------------------------------
   // Public
   // ----------------------------------------------------------------------
 
   return {
-    Literal: function(node) {
-      if (/^\s*\/(\/|\*)/m.test(node.value)) {
-        // inside component, e.g. <div>literal</div>
-        if (node.parent.type !== 'JSXAttribute' &&
-            node.parent.type !== 'JSXExpressionContainer' &&
-            node.parent.type !== 'JSXSpreadAttribute') {
-          context.report(node, 'Comments inside JSX text nodes are forbidden');
-        }
-      }
-    },
+    // Handles string literals (e.g. when using the `babel-eslint` parser with
+    // older versions) and JSX text nodes.
+    Literal: function(node) {
+      var value = normaliseSlashEntities(node.value);
+      if (/^\s*\/(\/|\*)/m.test(value)) {
+        // inside component, e.g. <div>literal</div>
+        if (node.parent.type !== 'JSXAttribute' &&
+            node.parent.type !== 'JSXExpressionContainer' &&
+            node.parent.type !== 'JSXSpreadAttribute') {
+          context.report(node, 'Comments inside JSX text nodes are forbidden');
+        }
+      }
+    },
+
+    JSXText: function(node) {
+      var value = normaliseSlashEntities(node.value);
+      if (/^\s*\/(\/|\*)/m.test(value)) {
+        // Inside a JSX element but not an attribute or expression container.
+        if (node.parent.type !== 'JSXAttribute' &&
+            node.parent.type !== 'JSXExpressionContainer' &&
+            node.parent.type !== 'JSXSpreadAttribute') {
+          context.report(node, 'Comments inside J

=== TASK vaskoz__dailycodingproblem-go-12 | repo vaskoz/dailycodingproblem-go | go
--- Issue
Day 5 extra - solve hard interview problems email
The question we'll work through is the following: **return a new sorted merged list from K sorted lists, each with size N.** Before we move on any further, you should take some time to think about the solution!
--- REAL FIX (ground truth)
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
+// MergeKSortedListsUsingHeap merges K-sorted lists into a single list using a min-heap.
+// Runtime is O(N*K log K) assuming each list of is length N
+// Space is O(K) for the heap without considering the answer list of O(K*N) size.
+// Use this if K is very large.
+func MergeKSortedListsUsingHeap(lists [][]int) []int {
+	pq := make(priorityQueue, len(lists))
+	for i, lst := range lists {
+		pq[i] = &tuple{id: i, pos: 0, value: lst[0]}
+	}
+	heap.Init(&pq)
+	var result []int
+	for pq.Len() > 0 {
+		tup := heap.Pop(&pq).(*tuple)
+		result = append(result, tup.value)
+		id := tup.id
+		nextpos := tup.pos + 1
+		if nextpos < len(lists[id]) {
+			heap.Push(&pq, &tuple{id: id, pos: nextpos, value: lists[id][nextpos]})
+		}
+	}
+	return result
+}

--- Proposal A
## Root cause
The repository contains a placeholder for the *Day 5 extra* problem – merging **K** sorted lists – but the function that should perform the merge is either missing or returns an empty result. Consequently the unit test that expects a correctly merged, sorted slice fails.

## Fix
Add a proper implementation that merges the K sorted slices using a min‑heap (the idiomatic O(N log K) solution). The implementation lives in `problems/day5_extra.go`.

```diff
--- a/problems/day5_extra.go
+++ b/problems/day5_extra.go
@@
 package problems
 
 import (
+    "container/heap"
 )
 
 // MergeKSortedLists merges K sorted integer slices into a single sorted slice.
 // Each inner slice is assumed to be sorted in non‑decreasing order.
 // The function returns a new slice containing all elements from the input
 // slices, also sorted in non‑decreasing order.
 //
 // Expected time complexity: O(N·log K) where N = K * size of each list.
 // Expected space complexity: O(N) for the output slice plus O(K) for the heap.
 func MergeKSortedLists(lists [][]int) []int {
-    // TODO: implement
-    return nil
+    // Edge cases: no lists or all lists empty.
+    if len(lists) == 0 {
+        return []int{}
+    }
+
+    // Initialise a min‑heap that will store (value, listIndex, elementIndex).
+    h := &intHeap{}
+    heap.Init(h)
+
+    // Total number of elements to allocate the result slice once.
+    total := 0
+    for i, lst := range lists {
+        if len(lst) > 0 {
+            // Push the first element of each non‑empty list.
+            heap.Push(h, heapItem{value: lst[0], listIdx: i, elemIdx: 0})
+            total += len(lst)
+        }
+    }
+
+    // Result slice pre‑allocated to the exact size.
+    result := make([]int, 0, total)
+
+    // Extract the smallest element, then push the next element from the same list.
+    for h.Len() > 0 {
+        item := heap.Pop(h).(heapItem)
+        result = append(result, item.value)
+
+        nextIdx := item.elemIdx + 1
+        if nextIdx < len(lists[item.listIdx]) {
+            heap.Push(h, heapItem{
+                value:   lists[item.listIdx][nextIdx],
+                listIdx: item.listIdx,
+                elemIdx: nextIdx,
+            })
+        }
+    }
+
+    return result
 }
 
 // ---------------------------------------------------------------------------
 // Helper types for the min‑heap
 // ---------------------------------------------------------------------------
 
 type heapItem struct {
     value   int
     listIdx int // which input list this value came from
     elemIdx int // index within that list
 }
 
 type intHeap []heapItem
@@
 func (h intHeap) Len() int { return len(h) }
 func (h intHeap) Less(i, j int) bool { return h[i].value < h[j].value }
 func (h intHeap) Swap(i, j int) { h[i], h[j] = h[j], h[i] }
 
 func (h *intHeap) Push(x interface{}) {
-    // TODO: Please implement
+    *h = append(*h, x.(heapItem))
 }
 
 func (h *intHeap) Pop() interface{} {
-    // TODO: Please implement
-    panic("Not implemented")
+    old := *h
+    n := len(old)
+    item := old[n-1]
+    *h = old[0 : n-1]
+    return item
 }
```

### What was added
1. **Full implementation** of `MergeKSortedLists` using a min‑heap.
2. **Heap helper types** (`heapItem`, `intHeap`) with proper `Push` and `Pop` methods.
3. Necessary import of `container/heap`.
4. Edge‑case handling and pre‑allocation of the result slice for efficiency.

## How to verify
1. **Run the test suite**  
   ```bash
   go te
--- Proposal B
## Root cause
The repository is missing an implementation for the “Day 5 extra” problem: *return a new sorted merged list from K sorted lists, each with size N*. No Go source file provides a function that merges K sorted slices, so any code that tries to import or test this functionality fails to compile.

## Fix
Add a new Go source file that implements the required algorithm using a min‑heap (O(K · N log K) time, O(K) extra space). Also update the README to list the new problem.

```diff
diff --git a/README.md b/README.md
index 70851d2..c3f9a1b 100644
--- a/README.md
+++ b/README.md
@@ -295,6 +295,7 @@ problems from
 * [Day 291](https://github.com/vaskoz/dailycodingproblem-go/issues/596)
 * [Day 294](https://github.com/vaskoz/dailycodingproblem-go/issues/600)
 * [Day 295](https://github.com/vaskoz/dailycodingproblem-go/issues/601)
+* [Day 5 extra](https://github.com/vaskoz/dailycodingproblem-go/issues/???)
 * [Day 296](https://github.com/vaskoz/dailycodingproblem-go/issues/605)
```

```diff
diff --git a/day5/merge_k_sorted.go b/day5/merge_k_sorted.go
new file mode 100644
index 0000000..e3b7c9a
--- /dev/null
+++ b/day5/merge_k_sorted.go
@@
+package day5
+
+import (
+	"container/heap"
+)
+
+// node is an element stored in the min‑heap. It keeps the value, the index of the
+// list the value came from, and the index of the element inside that list.
+type node struct {
+	value   int
+	listIdx int
+	elemIdx int
+}
+
+// minHeap implements heap.Interface for a slice of *node, ordering by node.value.
+type minHeap []node
+
+func (h minHeap) Len() int            { return len(h) }
+func (h minHeap) Less(i, j int) bool  { return h[i].value < h[j].value }
+func (h minHeap) Swap(i, j int)       { h[i], h[j] = h[j], h[i] }
+func (h *minHeap) Push(x interface{}) { *h = append(*h, x.(node)) }
+func (h *minHeap) Pop() interface{} {
+	old := *h
+	n := len(old)
+	x := old[n-1]
+	*h = old[0 : n-1]
+	return x
+}
+
+// MergeKSorted merges K sorted integer slices (each of length N) into a single
+// sorted slice. The input slices are not modified; a new slice containing all
+// elements in sorted order is returned.
+//
+// The algorithm uses a min‑heap that always holds the smallest unprocessed
+// element from each list. Each pop extracts the next smallest value and pushes
+// the following element from the same list (if any). This yields O(K·N·logK)
+// time and O(K) auxiliary space.
+func MergeKSorted(lists [][]int) []int {
+	// Edge case: no lists.
+	if len(lists) == 0 {
+		return []int{}
+	}
+
+	// Initialise the heap with the first element of each non‑empty list.
+	h := &minHeap{}
+	total := 0
+	for i, lst := range lists {
+		if len(lst) == 0 {
+			continue
+		}
+		*h = append(*h, node{value: lst[0], listIdx: i, elemIdx: 0})
+		total += len(lst)
+	}
+	heap.Init(h)
+
+	merged := make([]int, 0, total)
+
+	for h.Len() > 0 {
+		// Extract the smallest current element.
+		minNode := heap.Pop(h).(node)
+		merged = append(merged, minNode.value)
+
+		// Push the next element from the same list, if it exists.
+		nextIdx := minNode.elemIdx + 1
+		if nextIdx < len(lists[minNode.listIdx]) {
+			nextVal := lists[minNode.listIdx][nextIdx]
+			heap.Push(h, node{value: nextVal, listIdx: minNode.listIdx, elemIdx: nextIdx})
+		}
+	}
+
+	return merged
+}
```

## How to verify
1. **Run the repository’s test suite** – the new file compiles and does not break existing tests.  
   ```bash
   go test ./...
   ```

2. **Add a quick sanity test** (or run the existing one if th

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
--- REAL FIX (ground truth)
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

--- Proposal A
## Root cause
The *Postpone* button works on the `Task` object that was created when the query was first rendered.  
When the button is clicked the plugin:

1. builds the new markdown line (e.g. `- [ ] #task Do stuff 📅 2025‑12‑03`);
2. calls `TaskEditor.replaceTaskLine(task, newLine)`.

`replaceTaskLine` searches the file for **`task.originalMarkdown`** (the line that existed **before** the first edit) and replaces it.  
After the first postponement `task.originalMarkdown` still contains the old text (`📅 2025‑12‑02`).  
On the second click the plugin tries to replace that stale text, cannot find it, and throws the *“Unable to edit task – original line not found”* error.  
The UI is refreshed (so the task shown in the query has the correct date) but the underlying edit fails, producing the second *failure* notice.

**In short:** the task’s cached `originalMarkdown` (and consequently the edit logic) is not updated after a successful postponement, so subsequent postpones operate on stale data.

---

## Fix
*Update the postponement command to replace the line by its **line number** and refresh the cached markdown after a successful edit.*

```diff
diff --git a/src/Commands/PostponeCommand.ts b/src/Commands/PostponeCommand.ts
index 7c9a1f2..e3b4d8a 100644
--- a/src/Commands/PostponeCommand.ts
+++ b/src/Commands/PostponeCommand.ts
@@
 import { Task } from '../Task';
 import { TaskEditor } from '../TaskEditor';
 import { Notice } from 'obsidian';
 
 export class PostponeCommand {
     public static async run(task: Task): Promise<void> {
-        // Build the new markdown line with the date moved forward by one day.
-        const newLine = task.postponeOneDay().toFileLineString();
-
-        // Replace the original line in the file.
-        try {
-            await TaskEditor.replaceTaskLine(task, newLine);
-            new Notice('Task postponed by one day.');
-        } catch (e) {
-            console.error(e);
-            new Notice('Failed to postpone task.');
-        }
+        // -----------------------------------------------------------------
+        // 1️⃣  Build the new markdown line with the date moved forward by one day.
+        // -----------------------------------------------------------------
+        const newTask = task.postponeOneDay();          // returns a *new* Task instance
+        const newLine = newTask.toFileLineString();    // the exact text that must appear in the file
+
+        // -----------------------------------------------------------------
+        // 2️⃣  Replace the line **by its line number** instead of by the stale
+        //     original markdown text.  This works even when the file has been
+        //     edited previously.
+        // -----------------------------------------------------------------
+        try {
+            // `task.position.start.line` is the zero‑based line number where the
+            // original task lives.  `TaskEditor.replaceTaskLineAt` is a new helper
+            // that performs a line‑number based replacement.
+            await TaskEditor.replaceTaskLineAt(task, newLine);
+
+            // -----------------------------------------------------------------
+            // 3️⃣  Keep the in‑memory `Task` object in sync for the next click.
+            // -----------------------------------------------------------------
+            task.originalMarkdown = newLine;            // cache the fresh markdown
+            task.scheduledDate = newTask.scheduledDate; // upd
--- Proposal B
## Root cause
The *Postpone* button updates the markdown line by editing the file at the task’s original line number.  
After the first postponement the task’s line number changes (the line is rewritten), but the
`Task` object that the button handler keeps using still holds the **old** line position.  
When the button is clicked a second time the plugin tries to edit the stale line, the edit
fails and the plugin shows an error notice.

## Fix
Update the UI after a successful postponement so that the task list (and the `Task` objects)
are rebuilt with the correct line numbers. The simplest place to do this is in the click‑
handler for the postpone button.

```diff
--- a/src/ui/TaskResultView.ts
+++ b/src/ui/TaskResultView.ts
@@
-    private async onPostponeClick(task: Task) {
-        try {
-            await this.taskEditor.postpone(task);
-            new Notice(t("postpone_success"));
-        } catch (e) {
-            console.error(e);
-            new Notice(t("postpone_error"));
-        }
-    }
+    /**
+     * Handler for the *Postpone* button shown next to a task in the query results.
+     *
+     * The original implementation edited the file but kept the stale `Task` instance,
+     * causing the second postponement to try to edit the wrong line and raise an error.
+     *
+     * After a successful postponement we now **refresh the query view**, which rebuilds
+     * all `Task` objects with up‑to‑date positions. This makes subsequent postponements work
+     * correctly.
+     */
+    private async onPostponeClick(task: Task) {
+        try {
+            await this.taskEditor.postpone(task);
+            new Notice(t("postpone_success"));
+
+            // Refresh the result view so that task positions are recomputed.
+            // This also updates the displayed date in the query results.
+            this.refresh();   // <-- added line
+        } catch (e) {
+            console.error(e);
+            new Notice(t("postpone_error"));
+        }
+    }
```

*Explanation of the change*  

* `this.refresh()` (already defined on `TaskResultView`) re‑runs the underlying
  `TasksQuery` and rebuilds the list of `Task` objects, thereby discarding the stale
  line information.  
* The rest of the logic (calling `taskEditor.postpone` and showing notices) stays the same.  
* No other files need to be touched.

## How to verify
1. Install the development build of the Tasks plugin containing the change.  
2. Open a vault, create a file `Postpone Tests.md` with the content from the issue.  
3. Open the file in Live Preview or Reading mode so the query block is rendered.  
4. Click the **Postpone** icon next to the task **once** – the date should change from
   `2025‑12‑02` to `2025‑12‑03` and a success notice appears.  
5. Click the **Postpone** icon **again** – the date should now change to `2025‑12‑04`,
   a success notice appears **once**, and **no error notice** is shown.  
6. Repeat the click several more times; each click should advance the date by one day
   without any error messages.

If the task line in the markdown file and the date shown in the query results both
advance correctly on every click, the bug is fixed.

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
--- REAL FIX (ground truth)
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
+// GetResponseCache return response cache for retransmit allocation request
+func (a *Allocation) GetResponseCache() (id [stun.TransactionIDSize]byte, attrs []stun.Setter) {
+	if r := a.responseCache.Load(); r != nil {
+		res := r.(*allocationResponse)
+		id, attrs = res.transactionID, res.responseAttrs
+	}
+	return
+}
+
 // Close closes the allocation
 func (a *Allocation) Close() error {
 	select {
diff --git a/internal/server/turn.go b/internal/server/turn.go
index cbac09c..ca92f8d 100644
--- a/internal/server/turn.go
+++ b/internal/server/turn.go
@@ -39,8 +39,14 @@ func handleAllocateRequest(r Request, m *stun.Message) error {
 	//    existing allocation.  If yes, the server rejects the request with
 	//    a 437 (Allocation Mismatch) error.
 	if alloc := r.AllocationManager.GetAllocation(fiveTuple); alloc != nil {
-		msg := buildMsg(m.TransactionID, stun.NewType(stun.MethodAllocate, stun.ClassErrorResponse), &stun.ErrorCodeAttribute{Code: stun.CodeAllocMismatch})
-		return buildAndSendErr(r.Conn,
--- Proposal A
## Root cause
The TURN server parses every incoming UDP packet directly from the slice that is reused by the listener’s read loop.  
When many packets arrive concurrently the same underlying buffer is handed to several goroutines.  
While one goroutine is still decoding a STUN message, another read overwrites the buffer.  
This results in:

* **`unexpected EOF: not enough bytes to read header`** – the STUN decoder sees a truncated packet because the buffer was overwritten.
* **`no allocation found …`** – the allocation lookup is performed with a corrupted client address (the source port may have been overwritten), so the server cannot find the previously created allocation.
* **`relay already allocated for 5‑TUPLE`** – the corrupted packet is interpreted as a new Allocate request, causing the server to think a second allocation for the same 5‑tuple is being created.

The bug is a classic data‑race caused by re‑using the read buffer across goroutines.

## Fix
* Allocate a fresh copy of the received packet before handing it to any worker goroutine.
* Keep the client address (`net.UDPAddr`) immutable for the lifetime of the request.
* Update the packet‑handling code to use the copied buffer, eliminating the race.

```diff
--- a/turn/server.go
+++ b/turn/server.go
@@
 func (s *Server) serveUDP(conn *net.UDPConn) {
-    buf := make([]byte, maxPacketSize)
-    for {
-        n, addr, err := conn.ReadFromUDP(buf)
-        if err != nil {
-            s.logger.Errorf("read udp error: %v", err)
-            continue
-        }
-
-        // Hand the packet to a worker goroutine.
-        go s.handleUDPDatagram(conn, buf[:n], addr)
-    }
+    // NOTE: The buffer used for ReadFromUDP must not be shared between
+    // concurrent goroutines.  Each incoming packet gets its own copy so
+    // that the decoder cannot see data that is overwritten by a later
+    // read.
+    sharedBuf := make([]byte, maxPacketSize)
+    for {
+        n, addr, err := conn.ReadFromUDP(sharedBuf)
+        if err != nil {
+            s.logger.Errorf("read udp error: %v", err)
+            continue
+        }
+
+        // Copy the exact payload that was read.  This copy is owned by the
+        // goroutine that will process the packet, preventing data races.
+        pktCopy := make([]byte, n)
+        copy(pktCopy, sharedBuf[:n])
+
+        // Preserve the client address – net.UDPAddr is a struct containing a
+        // slice for IP, which must also be copied to avoid later mutation.
+        clientAddr := &net.UDPAddr{
+            IP:   append([]byte(nil), addr.IP...), // deep copy of IP slice
+            Port: addr.Port,
+            Zone: addr.Zone,
+        }
+
+        // Hand the packet to a worker goroutine.
+        go s.handleUDPDatagram(conn, pktCopy, clientAddr)
+    }
 }
 
 func (s *Server) handleUDPDatagram(conn *net.UDPConn, pkt []byte, src *net.UDPAddr) {
@@
-    msg, err := stun.NewMessage(pkt)
-    if err != nil {
-        s.logger.Errorf("failed to create stun message from packet: %v", err)
-        return
-    }
+    // Decode the STUN message.  Any parsing error is now safe – the packet
+    // cannot be corrupted by another concurrent read.
+    msg, err := stun.NewMessage(pkt)
+    if err != nil {
+        s.logger.Errorf("failed to create stun message from packet: %v", err)
+        return
+    }
```

### What was changed?
1. **Buffer handling** – a single reusable buffer (`sharedBuf`) is still used for the `ReadFromUDP` call, but a *new* s
--- Proposal B
## Root cause
`turn-server` reads incoming UDP packets into a single reusable buffer (`buf`).  
For each packet it spawns a goroutine that processes the slice `buf[:n]`.  
Because the next read can overwrite the same underlying array **before** the
goroutine finishes, the STUN message that the goroutine tries to decode is
corrupted. This manifests as:

* `failed to create stun message from packet: unexpected EOF: not enough bytes to read header`
* “no allocation found” or “relay already allocated for 5‑TUPLE” errors that appear
  intermittently and cause video‑calls to fail.

The problem is a classic data‑race caused by re‑using the same buffer for
concurrent packet handling.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
diff --git a/server.go b/server.go
index 919d19e..d6be792 100644
--- a/server.go
+++ b/server.go
@@
-func (s *Server) connReadLoop(c net.Conn, r RelayAddressGenerator) {
-    buf := make([]byte, inboundMTU)
-    allocationManager, err := allocation.NewManager(allocation.ManagerConfig{
-        AllocatePacketConn: r.AllocatePacketConn,
-        AllocateConn:       r.AllocateConn,
-        LoggerFactory:      s.LoggerFactory,
-    })
-    if err != nil {
-        s.LoggerFactory.NewLogger("turn").Errorf("failed to create allocation manager: %v", err)
-        return
-    }
-
-    defer func() {
-        if err := allocationManager.Close(); err != nil {
-            s.LoggerFactory.NewLogger("turn").Errorf("failed to close allocation manager: %v", err)
-        }
-    }()
-
-    stunConn := NewSTUNConn(c)
-    for {
-        n, addr, err := stunConn.ReadFrom(buf)
-        if err != nil {
-            s.LoggerFactory.NewLogger("turn").Errorf("error reading from conn: %v", err)
-            continue
-        }
-
-        go s.handlePacket(buf[:n], addr, allocationManager, r)
-    }
-}
+func (s *Server) connReadLoop(c net.Conn, r RelayAddressGenerator) {
+    // Allocate the read buffer **once** – it is only used for the ReadFrom call.
+    // The actual packet data that is handed to a goroutine must be a *copy*,
+    // otherwise the next read can overwrite the data before the goroutine finishes.
+    buf := make([]byte, inboundMTU)
+
+    allocationManager, err := allocation.NewManager(allocation.ManagerConfig{
+        AllocatePacketConn: r.AllocatePacketConn,
+        AllocateConn:       r.AllocateConn,
+        LoggerFactory:      s.LoggerFactory,
+    })
+    if err != nil {
+        s.LoggerFactory.NewLogger("turn").Errorf("failed to create allocation manager: %v", err)
+        return
+    }
+    defer func() {
+        if err := allocationManager.Close(); err != nil {
+            s.LoggerFactory.NewLogger("turn").Errorf("failed to close allocation manager: %v", err)
+        }
+    }()
+
+    stunConn := NewSTUNConn(c)
+    for {
+        n, addr, err := stunConn.ReadFrom(buf)
+        if err != nil {
+            s.LoggerFactory.NewLogger("turn").Errorf("error reading from conn: %v", err)
+            continue
+        }
+
+        // Make a copy of the packet data for the goroutine.
+        pkt := make([]byte, n)
+        copy(pkt, buf[:n])
+
+        go s.handlePacket(pkt, addr, allocationManager, r)
+    }
+}
 
-func (s *Server) packetConnReadLoop(p net.PacketConn, r RelayAddressGenerator) {
-    buf := make([]byte, inboundMTU)
-    allocationManager, err := allocation.NewManager(allocation.ManagerConfig{
-        AllocatePacketConn: r.AllocatePacketConn,
-        AllocateConn:     

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
--- REAL FIX (ground truth)
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

--- Proposal A
## Root cause
`svdvals!` always calls `bidiagonalize!` on the input matrix.  
For matrices with a zero dimension (e.g. `10×0`) and element types that are **not** a `BlasFloat`, `bidiagonalize!` tries to construct a `Bidiagonal` object from two zero‑length vectors. The `Bidiagonal` constructor in Base checks that the diagonal and off‑diagonal vectors have compatible lengths and throws

```
DimensionMismatch("length of diagonal vector is 0, length of off-diagonal vector is 0")
```

The LAPACK‑based path (used for `BlasFloat` types) bypasses this constructor, which is why the problem only appears for generic element types such as `Float16` or `BigFloat`.

## Fix
Add an early‑exit for empty matrices in `svdvals!`.  
When either dimension of `A` is zero we can directly return an empty vector of the appropriate element type, avoiding the call to `bidiagonalize!` altogether.

```diff
diff --git a/src/svd.jl b/src/svd.jl
index 1a89e04..c3f7b2a 100644
--- a/src/svd.jl
+++ b/src/svd.jl
@@
-"""
-    svdvals!(A::AbstractMatrix{T}; tol::T = eps(T), debug::Bool = false) where {T}
-
-Compute the singular values of `A` **in‑place**, overwriting `A` with the
-bidiagonal form used internally. Returns a vector of singular values.
-
-This routine is a thin wrapper around the generic bidiagonal SVD algorithm
-implemented in this package.  For `BlasFloat` types the LAPACK implementation
-`LinearAlgebra.svdvals!` is used instead.
-"""
-function svdvals!(A::AbstractMatrix{T}; tol::T = eps(T), debug::Bool = false) where {T}
-    # For BLAS‑compatible types we delegate to the highly‑optimised LAPACK
-    # implementation.  This also correctly handles the empty‑matrix case.
-    if T <: BlasFloat
-        return LinearAlgebra.svdvals!(A; tol = tol, debug = debug)
-    end
-
-    # Generic path – first bidiagonalise the matrix in‑place.
-    B = bidiagonalize!(A)
-
-    # Compute singular values from the bidiagonal matrix.
-    σ = similar(A, zero(T), min(size(A)...))
-    svdvals_from_bidiagonal!(σ, B; tol = tol, debug = debug)
-    return σ
-end
+"""
+    svdvals!(A::AbstractMatrix{T}; tol::T = eps(T), debug::Bool = false) where {T}
+
+Compute the singular values of `A` **in‑place**, overwriting `A` with the
+bidiagonal form used internally. Returns a vector of singular values.
+
+For `BlasFloat` types the LAPACK implementation `LinearAlgebra.svdvals!` is
+used.  For all other element types we fall back to the generic bidiagonal SVD
+algorithm provided by this package.  The generic algorithm previously failed
+for empty matrices because it attempted to construct a `Bidiagonal` object
+from two zero‑length vectors, triggering a `DimensionMismatch`.  The function
+now detects empty inputs up‑front and returns an empty vector of the correct
+element type, matching the behaviour of the LAPACK path.
+"""
+function svdvals!(A::AbstractMatrix{T}; tol::T = eps(T), debug::Bool = false) where {T}
+    # Empty matrix → no singular values.
+    if isempty(A)   # either dimension is zero
+        return T[]
+    end
+
+    # For BLAS‑compatible types we delegate to the highly‑optimised LAPACK
+    # implementation.  This also correctly handles the empty‑matrix case.
+    if T <: BlasFloat
+        return LinearAlgebra.svdvals!(A; tol = tol, debug = debug)
+    end
+
+    # Generic path – first bidiagonalise the matrix in‑place.
+    B = bidiagonalize!(A)
+
+    # Compute singular values from the bidiagonal matrix.
+    σ = similar(A, zero(T), min(size(A)...))
+    svdvals_fr
--- Proposal B
## Root cause
`svdvals!` always calls `bidiagonalize!` which builds a `Bidiagonal` object from the diagonal and off‑diagonal vectors of the bidiagonal reduction.  
For an empty matrix (`size(A,1)==0` or `size(A,2)==0`) both vectors have length 0, and the generic `Bidiagonal` constructor checks that `length(e) == length(d)-1`.  
When `length(d)==0` this condition becomes `0 == -1` and a `DimensionMismatch` is thrown.  
The BLAS‑based path (`<:BlasFloat`) bypasses this because the LAPACK driver has a special case for zero‑size matrices, but the generic path (used for `Float16`, `BigFloat`, etc.) does not, causing the failure reported.

## Fix
Add an early‑exit for empty inputs in the generic SVD implementation so that no bidiagonalisation is attempted and an empty vector of the appropriate element type is returned.

```diff
--- a/src/svd.jl
+++ b/src/svd.jl
@@
-"""
-    svdvals!(A::AbstractMatrix{T}; tol::T = zero(T), debug::Bool = false) -> Vector{T}
-
-Compute the singular values of `A` in‑place, overwriting `A`.  The algorithm
-works for any element type `T` that supports the required arithmetic
-operations.  For `T <: BlasFloat` a LAPACK driver is used; otherwise a
-generic bidiagonalisation + QR iteration is performed.
-"""
-function svdvals!(A::AbstractMatrix{T}; tol::T = zero(T), debug::Bool = false) where {T}
-    m, n = size(A)
-    # Reduce to bidiagonal form
-    B = bidiagonalize!(A)
-    # Compute singular values from the bidiagonal matrix
-    s = _svdvals_from_bidiagonal!(B, tol=tol, debug=debug)
-    return s
-end
+"""
+    svdvals!(A::AbstractMatrix{T}; tol::T = zero(T), debug::Bool = false) -> Vector{T}
+
+Compute the singular values of `A` **in‑place**, overwriting `A`.  The algorithm
+works for any element type `T` that supports the required arithmetic
+operations.  For `T <: BlasFloat` a LAPACK driver is used; otherwise a
+generic bidiagonalisation + QR iteration is performed.
+
+The generic implementation previously failed for empty matrices (e.g. `10×0`
+or `0×5`) because it attempted to construct a `Bidiagonal` object with empty
+diagonal and off‑diagonal vectors, which violates the invariant
+`length(e) == length(d)-1`.  The fix adds an early‑exit that returns an empty
+vector of the correct element type when either dimension of `A` is zero.
+"""
+function svdvals!(A::AbstractMatrix{T}; tol::T = zero(T), debug::Bool = false) where {T}
+    # ----------------------------------------------------------------------
+    # Special case: empty matrix
+    # ----------------------------------------------------------------------
+    if isempty(A)   # true if any dimension is zero
+        return T[]   # an empty vector of the appropriate element type
+    end
+
+    m, n = size(A)
+    # Reduce to bidiagonal form
+    B = bidiagonalize!(A)
+    # Compute singular values from the bidiagonal matrix
+    s = _svdvals_from_bidiagonal!(B, tol=tol, debug=debug)
+    return s
+end
```

If the repository also defines `svd!` (the full decomposition) we add the same guard there to keep behaviour consistent:

```diff
--- a/src/svd.jl
+++ b/src/svd.jl
@@
 function svd!(A::AbstractMatrix{T}; full::Bool = false, tol::T = zero(T), debug::Bool = false) where {T}
-    m, n = size(A)
-    B = bidiagonalize!(A)
-    U, s, Vt = _svd_from_bidiagonal!(B; full=full, tol=tol, debug=debug)
-    return SVD{T}(U, s, Vt)
+    # ----------------------------------------------------------------------
+    # Special case: empty matrix
+    # ----------

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
--- Proposal B
## Root cause
`Session.run` resolves the executable to run by calling `py.path.local.sysfind(program, paths=[path])` where `path` is the *Scripts* (or *bin*) directory of the virtual environment.  
On Windows (and some Linux conda setups) the actual `python` executable lives **one directory level above** that folder (e.g. `…\env\python.exe` instead of `…\env\Scripts\python.exe`).  
Because only the *Scripts* directory is searched, `sysfind` falls back to the system‑wide `python` found on `$PATH`, causing the session to run the wrong interpreter.

## Fix
Search both the virtual‑environment *Scripts* directory **and its parent directory** when locating the program.

```diff
diff --git a/nox/command.py b/nox/command.py
index 5c2e1a7..d3f9b4c 100644
--- a/nox/command.py
+++ b/nox/command.py
@@
 import subprocess
 import sys
 import typing
+import os
 import warnings
 
 from . import _compat
 from . import _session
 from . import _virtualenv
@@
-        if path:
-            full_path = py.path.local.sysfind(program, paths=[path])
-        else:
-            full_path = py.path.local.sysfind(program)
+        # ``path`` points at the virtual‑env's *Scripts* (or *bin*) directory.
+        # On Windows (and some conda layouts) the ``python`` executable lives
+        # one level *above* that directory (e.g. ``<venv>\\python.exe``).
+        # To make ``session.run('python', …)`` resolve to the interpreter inside
+        # the virtual environment we search both the directory itself and its
+        # parent.
+        if path:
+            search_paths = [path, os.path.dirname(path)]
+            full_path = py.path.local.sysfind(program, paths=search_paths)
+        else:
+            full_path = py.path.local.sysfind(program)
```

* The new `import os` brings in `os.path.dirname`.
* The logic now builds `search_paths = [path, os.path.dirname(path)]` and passes that list to `sysfind`, ensuring the interpreter in the virtual environment is found before falling back to the system interpreter.

## How to verify
1. **Create a reproducible environment**  
   ```bash
   # clone the repo
   git clone https://github.com/theacodes/nox.git
   cd nox
   # install nox itself (editable) so we test the patched code
   pip install -e .
   ```

2. **Add the failing Noxfile** (as in the issue) to a temporary project:
   ```python
   # noxfile.py
   import nox, os
   VENV = 'conda' if os.name == 'nt' else None
   NAME = 'win' if os.name == 'nt' else 'linux'

   @nox.session(python=['3.6', '3.7'],
                 reuse_venv=True,
                 venv_backend=VENV,
                 name='test_' + NAME)
   def test(session):
       if VENV == 'conda':
           session.conda_install('--channel=conda-forge', 'shapely')
       session.install('-e', '.[dev]')
       session.run('pytest')
       # this line previously invoked the system python
       session.run('python', 'setup.py', 'sdist', 'bdist_wheel')
   ```

3. **Run the session**  
   ```bash
   nox -s test_linux   # or test_win on Windows
   ```

4. **Observe the output** – the last `session.run('python', ...)` should now report that it is using the interpreter inside the created conda environment (e.g. path ends with `.../env/bin/python` or `...\\env\\python.exe`).  
   You can add a debug line inside the session:

   ```python
   session.run('python', '--version')
   ```

   The version line should be printed from the virtual‑env interpreter, not the system one.

5. **Run the project's own test suite** t

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
--- Proposal B
## Root cause
The repository’s public API lists `isString`, but the package `@plexis/is-string` (source code, README, npmignore, and export entry) does not exist. Consequently importing `isString` fails and the feature request cannot be satisfied.

## Fix
Add a full implementation of the **isString** utility following the same pattern used for the other string‑related helpers (e.g., `isAlphaNumeric`, `head`, `tail`).  
The fix introduces:

* `packages/isString/.npmignore` – files to exclude from the published package.  
* `packages/isString/README.md` – usage documentation.  
* `packages/isString/package.json` – package metadata.  
* `packages/isString/src/index.js` – the actual implementation (`typeof value === 'string'`).  
* Update the central API documentation (`docs/pages/api.md`) to list the new package.  
* Export the new helper from the monorepo’s main entry (`src/index.js`).

```diff
diff --git a/docs/pages/api.md b/docs/pages/api.md
index a9d68cc..b2f7e1a 100644
--- a/docs/pages/api.md
+++ b/docs/pages/api.md
@@ -1,6 +1,7 @@
 - [distance](/packages/distance)
 - [endsWith](/packages/endsWith)
 - [escapeHTML](/packages/escapeHTML)
+- [isString](/packages/isString)
 - [head](/packages/head)
 - [isAlpha](/packages/isAlpha)
 - [isAlphaNumeric](/packages/isAlphaNumeric)
@@ -30,6 +31,7 @@
 - [withoutDiacritics](/packages/withoutDiacritics)
 - [withoutHTML](/packages/withoutHTML)
 - [withoutIndent](/packages/withoutIndent)
+- [isString](/packages/isString)
 
diff --git a/packages/isString/.npmignore b/packages/isString/.npmignore
new file mode 100644
index 0000000..552cf85
--- /dev/null
+++ b/packages/isString/.npmignore
@@
+.DS_Store
+*.test.js
+.prettierrc
+.github
+.template
+.package-template
+.nyc_output
+.circleci
+.commitlintrc.js
+coverage
+coverage.lcov
+docs
+babel.config.js
+test
+CONTRIBUTING.md
+CODE_OF_CONDUCT.md
+LICENCE
\ No newline at end of file
diff --git a/packages/isString/README.md b/packages/isString/README.md
new file mode 100644
index 0000000..e1a2c3d
--- /dev/null
+++ b/packages/isString/README.md
@@
+## `isString`
+
+Checks whether the input is a **string primitive**.
+
+```bash
+npm i @plexis/is-string
+```
+
+**Usage**
+
+```javascript
+import isString from '@plexis/is-string';
+
+isString('Foo');   // => true
+isString(1);       // => false
+isString(null);    // => false
+```
+
+**Aliases**
+
+```javascript
+import isString from '@plexis/is-string';
+import { isString } from 'plexis';
+```
+
+The function returns `true` only for values whose `typeof` is `'string'`. All other
+types (including `String` objects created with `new String()`) return `false`.
+
diff --git a/packages/isString/package.json b/packages/isString/package.json
new file mode 100644
index 0000000..8dccdd0
--- /dev/null
+++ b/packages/isString/package.json
@@
+{
+  "name": "@plexis/is-string",
+  "version": "0.0.21",
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
+    "type",
+    "validation"
+  ],
+  "repository": {
+    "type": "git",
+    "url": "git+https://github.com/plexis-js/plexis.git"
+  },
+  "publishConfig": {
+    "access": "public"
+  }
+}
\ No newline at end of file
diff --git a/packages/isString/src/index.js b/packages/isString/src/index.js
new file mode 100644
index 0000000..1954ba4
--- /dev/null
+++ b/packages/isString/src/index.js
@@
+/**
+ * @description C

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
`import-shadowing` walks the AST and treats **any** identifier that has the same name as an imported package as a shadow, without distinguishing between package‑level functions and methods.  
When a method is declared (`func (s *Struct) reconcile(...)`), the rule looks at the `FuncDecl.Name` (`reconcile`) and reports it as a shadow of the imported package `reconcile`.  
Since a method name is scoped to its receiver type and cannot actually shadow the import, the rule should ignore identifiers that belong to method declarations (i.e. `FuncDecl` nodes that have a non‑nil `Recv` field).

## Fix
```diff
diff --git a/rule/import-shadowing.go b/rule/import-shadowing.go
index 7c9f2a1..e3b4d6c 100644
--- a/rule/import-shadowing.go
+++ b/rule/import-shadowing.go
@@
 func (r *ImportShadowingRule) Apply(file *lint.File, args lint.Arguments) []lint.Failure {
     var failures []lint.Failure
@@
-    ast.Inspect(file.AST, func(n ast.Node) bool {
-        // Look for function declarations (including methods) whose name
-        // matches an imported package name.
-        if fn, ok := n.(*ast.FuncDecl); ok {
-            if _, ok := importNames[fn.Name.Name]; ok {
-                failures = append(failures, lint.Failure{
-                    Confidence: 1,
-                    Category:   "style",
-                    Failure:    fmt.Sprintf(\"The name '%s' shadows an import name\", fn.Name.Name),
-                    Node:       fn.Name,
-                })
-            }
-        }
-        return true
-    })
+    ast.Inspect(file.AST, func(n ast.Node) bool {
+        // Look for **function** declarations whose name matches an imported
+        // package name.  Method declarations (functions with a receiver) are
+        // *not* considered shadowing because the method name is scoped to its
+        // receiver type and cannot be used as an unqualified identifier.
+        if fn, ok := n.(*ast.FuncDecl); ok {
+            // Skip methods – they have a non‑nil Recv field.
+            if fn.Recv != nil {
+                return true
+            }
+
+            if _, ok := importNames[fn.Name.Name]; ok {
+                failures = append(failures, lint.Failure{
+                    Confidence: 1,
+                    Category:   \"style\",
+                    Failure:    fmt.Sprintf(\"The name '%s' shadows an import name\", fn.Name.Name),
+                    Node:       fn.Name,
+                })
+            }
+        }
+        return true
+    })
```

### What the patch does
1. During the AST walk, when a `*ast.FuncDecl` node is encountered, it now checks `fn.Recv`.
2. If `fn.Recv` is non‑nil (i.e., the declaration is a method), the node is ignored for shadow‑checking.
3. Only plain functions (`fn.Recv == nil`) are compared against the set of imported package names, preserving the original behaviour for true shadowing cases.

## How to verify
1. **Run the linter on the reproducing example**  

   ```bash
   cat > /tmp/t.go <<'EOF'
   package somepkg
   import (
       metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
       "sigs.k8s.io/controller-runtime/pkg/reconcile"
   )
   type Struct struct{}
   func (s *Struct) reconcile(md metav1.ObjectMeta) (reconcile.Result, error) {
       return reconcile.Result{}, nil
   }
   EOF

   revive -config defaults.toml /tmp/t.go
   ```

   *Expected result*: No `import-shadowing` warning is emitted.

2. **Check that genuine shadowing is still reported**  

   ```go
   package foo
   impo

=== TASK urlaubsverwaltung__urlaubsverwaltung-4789 | repo urlaubsverwaltung/urlaubsverwaltung | java
--- Issue
Person mit Leerzeichen am Anfang/Ende des Namens kann nicht gelöscht werden
**Describe the bug**

Eine Person mit Leerzeichen am Anfang oder Ende des Namens kann quasi nicht gelöscht werden.

Der Hinweis zum Löschen zeigt die Leerzeichen nicht an.  Dort steht:

> Bitte gebe zur Bestätigung "Hans Dampf" ein.

Obwohl der Name in der Datenbank `Hans ` `Dampf ` ist.

**To Reproduce**
Steps to reproduce the behavior:
1. Starte Anwendung mit Demo Daten
2. Füge in der Datenbank ein Leereichen z. B. an den Vornamen von "Hans Dampf"
3. Melde dich mit `office` an
4. Löscche die Person "Hans Dampf"

**Expected behavior**

Person wird gelöscht
--- REAL FIX (ground truth)
diff --git a/src/main/java/org/synyx/urlaubsverwaltung/person/PersonServiceImpl.java b/src/main/java/org/synyx/urlaubsverwaltung/person/PersonServiceImpl.java
index 561a9432c..c358137fd 100644
--- a/src/main/java/org/synyx/urlaubsverwaltung/person/PersonServiceImpl.java
+++ b/src/main/java/org/synyx/urlaubsverwaltung/person/PersonServiceImpl.java
@@ -99,7 +99,7 @@ class PersonServiceImpl implements PersonService {
     public Person create(String username, String firstName, String lastName, String email,
                          List<MailNotification> notifications, List<Role> permissions) {
 
-        final Person person = new Person(username, lastName, firstName, email);
+        final Person person = normalizePerson(new Person(username, lastName, firstName, email));
         person.setNotifications(notifications);
         person.setPermissions(permissions);
 
@@ -121,7 +121,7 @@ class PersonServiceImpl implements PersonService {
             throw new IllegalArgumentException("Can not update a person that is not persisted yet");
         }
 
-        final Person updatedPerson = personRepository.save(person);
+        final Person updatedPerson = personRepository.save(normalizePerson(person));
         LOG.info("Updated person: {}", updatedPerson);
 
         if (updatedPerson.isInactive()) {
@@ -251,6 +251,21 @@ class PersonServiceImpl implements PersonService {
         return personRepository.countByPermissionsContainingAndIdNotIn(OFFICE, List.of(excludingId));
     }
 
+    private Person normalizePerson(Person person) {
+        final Person normalized = new Person();
+
+        normalized.setUsername(person.getUsername().strip());
+        normalized.setLastName(person.getLastName().strip());
+        normalized.setFirstName(person.getFirstName().strip());
+        normalized.setEmail(person.getEmail().strip());
+
+        normalized.setId(person.getId());
+        normalized.setPermissions(person.getPermissions());
+        normalized.setNotifications(person.getNotifications());
+
+        return normalized;
+    }
+
     private static Sort mapToImplicitPersonSort(Sort requestedSort) {
         final Sort.Order firstNameOrder = requestedSort.getOrderFor("firstName");
         final Sort.Order lastNameOrder = requestedSort.getOrderFor("lastName");
diff --git a/src/main/resources/dbchangelogs/changelog-5.6.0-trim-person.xml b/src/main/resources/dbchangelogs/changelog-5.6.0-trim-person.xml
new file mode 100644
index 000000000..bb4f15f27
--- /dev/null
+++ b/src/main/resources/dbchangelogs/changelog-5.6.0-trim-person.xml
@@ -0,0 +1,41 @@
+<?xml version="1.1" encoding="UTF-8" standalone="no"?>
+<databaseChangeLog xmlns="http://www.liquibase.org/xml/ns/dbchangelog"
+                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
+                   xsi:schemaLocation="http://www.liquibase.org/xml/ns/dbchangelog https://www.liquibase.org/xml/ns/dbchangelog/dbchangelog-4.27.xsd">
+
+  <changeSet author="seber" id="strip-person-userna
--- Proposal A
## Root cause
The delete‑person confirmation compares the user input with the *raw* first‑ and last‑name stored in the database.  
If a name contains leading or trailing blanks, the UI message that asks the user to type the name shows a **trimmed** version (e.g. “Hans Dampf”).  
When the user types exactly what is shown, the comparison fails because the stored value still contains the blanks (`"Hans "` / `" Dampf"`).  
Thus the deletion is rejected and the person cannot be removed.

## Fix
Trim the stored name parts before the comparison (or compare the trimmed user input with the trimmed stored name).  
The change is applied in the controller that handles the delete‑person POST request.

```diff
--- a/src/main/java/org/urlaubsverwaltung/person/web/PersonDeleteController.java
+++ b/src/main/java/org/urlaubsverwaltung/person/web/PersonDeleteController.java
@@
-    @PostMapping("/{personId}/delete")
-    public String deletePerson(@PathVariable("personId") Long personId,
-                               @RequestParam("confirmation") String confirmation,
-                               Model model,
-                               RedirectAttributes redirectAttributes) {
-
-        Person person = personService.getPersonByID(personId)
-                .orElseThrow(() -> new IllegalArgumentException("Invalid person Id:" + personId));
-
-        // the confirmation text must exactly match the person's full name
-        String expectedConfirmation = person.getNiceName(); // e.g. "Hans Dampf"
-        if (!expectedConfirmation.equals(confirmation)) {
-            model.addAttribute("person", person);
-            model.addAttribute("error", "person.delete.confirmation.wrong");
-            return "person/person_delete";
-        }
-
-        personService.delete(person);
-        redirectAttributes.addFlashAttribute("success", "person.delete.success");
-        return "redirect:/persons";
-    }
+    @PostMapping("/{personId}/delete")
+    public String deletePerson(@PathVariable("personId") Long personId,
+                               @RequestParam("confirmation") String confirmation,
+                               Model model,
+                               RedirectAttributes redirectAttributes) {
+
+        Person person = personService.getPersonByID(personId)
+                .orElseThrow(() -> new IllegalArgumentException("Invalid person Id:" + personId));
+
+        /* --------------------------------------------------------------
+         * The UI shows the *trimmed* full name (firstName + " " + lastName)
+         * but the stored values may contain leading/trailing blanks.
+         * To make the confirmation robust we compare the *trimmed* versions
+         * of both strings.
+         * -------------------------------------------------------------- */
+        String expectedConfirmation = StringUtils.trimToEmpty(person.getFirstName())
+                + " "
+                + StringUtils.trimToEmpty(person.getLastName());
+
+        if (!expectedConfirmation.equals(StringUtils.trimToEmpty(confirmation))) {
+            model.addAttribute("person", person);
+            model.addAttribute("error", "person.delete.confirmation.wrong");
+            return "person/person_delete";
+        }
+
+        personService.delete(person);
+        redirectAttributes.addFlashAttribute("success", "person.delete.success");
+        return "redirect:/persons";
+    }
```

* The code now builds the expected confirmation string from the **trimm
--- Proposal B
## Root cause
The delete‑person confirmation compares the user input with the stored person name **exactly**.  
When a person’s first‑ or last‑name contains leading or trailing blanks the stored “nice name” (e.g. `"Hans  "` + `" Dampf"`) differs from the string shown in the confirmation message, which is built from a trimmed version of the name.  
Therefore the user cannot type the displayed name (without the blanks) and the validation fails, making the person undeletable.

## Fix
* Do not trim the name when it is shown in the confirmation dialog.
* When validating the confirmation input, compare the **trimmed** versions of both strings so that surrounding blanks are ignored.

```diff
diff --git a/src/main/java/org/synyx/urlaubsverwaltung/person/PersonDeletionValidator.java b/src/main/java/org/synyx/urlaubsverwaltung/person/PersonDeletionValidator.java
index e3b1c9a..a7f4d2c 100644
--- a/src/main/java/org/synyx/urlaubsverwaltung/person/PersonDeletionValidator.java
+++ b/src/main/java/org/synyx/urlaubsverwaltung/person/PersonDeletionValidator.java
@@
 public class PersonDeletionValidator {
 
     /**
      * Checks whether the confirmation string entered by the user matches the person's name.
      */
-    public boolean isDeletionConfirmed(Person person, String confirmation) {
-        return person.getNiceName().equals(confirmation);
-    }
+    public boolean isDeletionConfirmed(Person person, String confirmation) {
+        // The stored name may contain leading/trailing blanks (e.g. entered via the DB directly).
+        // Users see the name without those blanks in the UI, therefore we compare the trimmed
+        // versions of both strings. This makes the confirmation robust while still preventing
+        // accidental deletions.
+        if (person == null || confirmation == null) {
+            return false;
+        }
+        String stored = person.getNiceName();
+        return stored != null && stored.trim().equals(confirmation.trim());
+    }
 }
diff --git a/src/main/java/org/synyx/urlaubsverwaltung/person/PersonController.java b/src/main/java/org/synyx/urlaubsverwaltung/person/PersonController.java
index 5d2a1f4..c9e8b7a 100644
--- a/src/main/java/org/synyx/urlaubsverwaltung/person/PersonController.java
+++ b/src/main/java/org/synyx/urlaubsverwaltung/person/PersonController.java
@@
     @GetMapping("/{personId}/delete")
     public String deletePersonForm(@PathVariable("personId") Long personId, Model model) {
         Person person = personService.getPersonByID(personId);
         model.addAttribute("person", person);
-        // The confirmation message should show the exact name; previously we used trim()
-        // which hid leading/trailing blanks and made the check impossible.
-        model.addAttribute("personNameForConfirmation", person.getNiceName().trim());
+        // Show the raw name (including possible leading/trailing blanks) so the user knows
+        // exactly which person is being deleted. The validator will ignore surrounding blanks
+        // when comparing the entered value.
+        model.addAttribute("personNameForConfirmation", person.getNiceName());
         return "person/delete";
     }
 
@@
     @PostMapping("/{personId}/delete")
     public String deletePerson(@PathVariable("personId") Long personId,
                                @RequestParam("confirmation") String confirmation,
                                RedirectAttributes redirectAttributes) {
         Person person = personServ

=== TASK doctrine__orm-11826 | repo doctrine/orm | php
--- Issue
Fetch mode hint is ignored when using `Paginator` 
### Bug Report

<!-- Fill in the relevant information below to help triage your issue. -->

|    Q                                        |   A
|-------------------------------------------- | --------------
| Version                                     | 2.20.0 / 3.3.0
| Previous working version                    | 2.19.8 / 3.2.3

#### Summary

This is related to issue #11128 and PR #11445.

When a `fetch=EAGER` association is filtered using `WITH`, it raises an error unless fetch mode is overridden by calling `setFetchMode(LAZY)` over the association.

This worked well after the fix in #11445.

Somehow, in 2.20.x and 3.3.x this bug returned, partially, only when using `Paginator` with `fetchJoinCollection` parameter set to `true`.

Seems like #11188 introduced this bug.


#### Current behavior

An exception is raised even when fetch mode is overridden.

#### Expected behavior

It should work the same with or without pagination.

#### How to reproduce

See test in https://github.com/aprat84/orm/tree/gh-11741

```
vendor/bin/phpunit --filter testSubselectFetchJoinWithAllowedWhenOverriddenNotEagerPaginator

There was 1 error:

1) Doctrine\Tests\ORM\Functional\EagerFetchCollectionTest::testSubselectFetchJoinWithAllowedWhenOverriddenNotEagerPaginator
Doctrine\ORM\Query\QueryException: Associations with fetch-mode=EAGER may not be using WITH conditions in "Doctrine\Tests\ORM\Functional\Eage
--- REAL FIX (ground truth)
diff --git a/src/Persisters/Entity/BasicEntityPersister.php b/src/Persisters/Entity/BasicEntityPersister.php
index 2e905bca7..4073c606e 100644
--- a/src/Persisters/Entity/BasicEntityPersister.php
+++ b/src/Persisters/Entity/BasicEntityPersister.php
@@ -1374,12 +1374,6 @@ class BasicEntityPersister implements EntityPersister
                     $joinCondition[] = $this->getSQLTableAlias($association['sourceEntity'], $assocAlias) . '.' . $sourceCol . ' = '
                         . $this->getSQLTableAlias($association['targetEntity']) . '.' . $targetCol;
                 }
-
-                // Add filter SQL
-                $filterSql = $this->generateFilterConditionSQL($eagerEntity, $joinTableAlias);
-                if ($filterSql) {
-                    $joinCondition[] = $filterSql;
-                }
             }
 
             $this->currentPersisterContext->selectJoinSql .= ' ' . $joinTableName . ' ' . $joinTableAlias . ' ON ';
diff --git a/src/Tools/Pagination/LimitSubqueryOutputWalker.php b/src/Tools/Pagination/LimitSubqueryOutputWalker.php
index 95b9066db..ea7ec0f63 100644
--- a/src/Tools/Pagination/LimitSubqueryOutputWalker.php
+++ b/src/Tools/Pagination/LimitSubqueryOutputWalker.php
@@ -105,17 +105,24 @@ class LimitSubqueryOutputWalker extends SqlOutputWalker
         $this->platform = $query->getEntityManager()->getConnection()->getDatabasePlatform();
         $this->rsm      = $parserResult->getResultSetMapping();
 
-        $query = clone $query;
+        $cloneQuery = clone $query;
+
+        $cloneQuery->setParameters(clone $query->getParameters());
+        $cloneQuery->setCacheable(false);
+
+        foreach ($query->getHints() as $name => $value) {
+            $cloneQuery->setHint($name, $value);
+        }
 
         // Reset limit and offset
-        $this->firstResult = $query->getFirstResult();
-        $this->maxResults  = $query->getMaxResults();
-        $query->setFirstResult(0)->setMaxResults(null);
+        $this->firstResult = $cloneQuery->getFirstResult();
+        $this->maxResults  = $cloneQuery->getMaxResults();
+        $cloneQuery->setFirstResult(0)->setMaxResults(null);
 
-        $this->em            = $query->getEntityManager();
+        $this->em            = $cloneQuery->getEntityManager();
         $this->quoteStrategy = $this->em->getConfiguration()->getQuoteStrategy();
 
-        parent::__construct($query, $parserResult, $queryComponents);
+        parent::__construct($cloneQuery, $parserResult, $queryComponents);
     }
 
     /**

--- Proposal A
**## Root cause**

`Doctrine\ORM\Tools\Pagination\Paginator` creates a *count* query by cloning the original `Query` object and then adds its own pagination‑specific hints (e.g. `Query::HINT_CUSTOM_OUTPUT_WALKER`).  
During this process the **fetch‑mode hint** (`Query::HINT_FETCH_MODE`) that may have been set on the original query is **lost** because the paginator overwrites the whole hint array when it sets its own hint.  

When the original query contains an association that is eager‑fetched but is filtered with a `WITH` clause, Doctrine validates the query and throws  

```
Associations with fetch-mode=EAGER may not be using WITH conditions …
```

If the fetch‑mode hint is correctly present (e.g. overridden to `LAZY` via `setFetchMode()`), the validation would be bypassed.  
Because the paginator discards the hint, the validation sees the mapping’s default eager fetch mode and the exception is raised. This only happens when the paginator is used with `fetchJoinCollection = true`, which is why the bug appears only in that scenario.

**## Fix (a unified diff with your best‑guess file paths and code)**

```diff
--- a/src/Doctrine/ORM/Tools/Pagination/Paginator.php
+++ b/src/Doctrine/ORM/Tools/Pagination/Paginator.php
@@
 class Paginator implements \IteratorAggregate, \Countable
 {
@@
     public function __construct(Query $query, $fetchJoinCollection = true)
     {
         $this->query               = $query;
         $this->fetchJoinCollection = (bool) $fetchJoinCollection;
-
-        // Force partial objects to avoid loading the whole collection when
-        // pagination is used. This hint is required for the LimitSubselectWalker.
-        $this->query->setHint(Query::HINT_FORCE_PARTIAL_LOAD, true);
+        // Force partial objects to avoid loading the whole collection when
+        // pagination is used. This hint is required for the LimitSubselectWalker.
+        $this->query->setHint(Query::HINT_FORCE_PARTIAL_LOAD, true);
+
+        // -----------------------------------------------------------------
+        // Preserve fetch‑mode hint for the paginator’s internal count query.
+        // -----------------------------------------------------------------
+        // The paginator creates a clone of the original query for the count
+        // operation and then adds its own pagination‑specific hints.  The
+        // original implementation overwrote the whole hint array, which
+        // unintentionally removed the fetch‑mode hint (`Query::HINT_FETCH_MODE`).
+        // When an association is eager‑fetched but filtered with a WITH clause,
+        // Doctrine validates the query and throws an exception unless the
+        // fetch mode is explicitly overridden.  By copying the fetch‑mode hint
+        // to the cloned count query we ensure that the overridden mode is
+        // respected.
+        // -----------------------------------------------------------------
+        $this->originalFetchModeHint = $this->query->getHint(Query::HINT_FETCH_MODE);
     }
@@
     public function getIterator()
     {
         $this->initialize();
@@
     private function initialize()
     {
         if ($this->initialized) {
             return;
         }
@@
-        $countQuery = clone $this->query;
-        $countQuery->setFirstResult(null)
-                   ->setMaxResults(null);
-
-        $countQuery->setHint(Query::HINT_CUSTOM_OUTPUT_WALKER, CountWalker::class);
-
-        $this->count = (int) $countQuery->getSingleScalarResult();

--- Proposal B
**## Root cause**

`Doctrine\ORM\Tools\Pagination\Paginator` builds a *count* query by cloning the original `Doctrine\ORM\Query`.  
During cloning `Doctrine\ORM\Query::__clone()` resets the `$hints` property to an empty array:

```php
public function __clone()
{
    $this->parameters = clone $this->parameters;
    …
    $this->hints = [];               // <-- all hints are lost
}
```

Fetch‑mode overrides are stored as a hint (`Query::HINT_FETCH_MODE`).  
When the paginator executes the cloned query the hint is no longer present, so the
original fetch‑mode (`EAGER`) is used and the query fails with  

```
Associations with fetch-mode=EAGER may not be using WITH conditions …
```

The bug appears only when the paginator is used with `$fetchJoinCollection = true`
because that code path clones the query; the normal query execution (without
paginator) keeps the hint.

---

**## Fix (unified diff)**

```diff
diff --git a/src/ORM/Query.php b/src/ORM/Query.php
index 7c9f3a1..e5b2d4c 100644
--- a/src/ORM/Query.php
+++ b/src/ORM/Query.php
@@
  * @psalm-import-type ParameterType from \Doctrine\DBAL\ParameterType
  */
 class Query implements QueryInterface
 {
@@
     public function __clone()
     {
-        $this->parameters = clone $this->parameters;
-        $this->resultCache = null;
-        $this->resultCacheId = null;
-        $this->resultCacheRegion = null;
-        $this->hydrationCache = null;
-        $this->cacheable = false;
-        $this->cacheMode = null;
-        $this->cacheRegion = null;
-        $this->outputWalker = null;
-        $this->treeWalker = null;
-        $this->parserResult = null;
-        $this->sql = null;
-        $this->firstResult = null;
-        $this->maxResults = null;
-        $this->hintedResultCache = null;
-        $this->hints = [];
+        // Preserve the original hints (e.g. HINT_FETCH_MODE) when the query is
+        // cloned.  The paginator (and other components) rely on cloning a query
+        // to build a count query.  Previously the clone cleared the $hints array,
+        // causing fetch‑mode overrides to be lost and leading to the exception
+        // “Associations with fetch-mode=EAGER may not be using WITH conditions”.
+        $originalHints = $this->hints;
+
+        $this->parameters = clone $this->parameters;
+        $this->resultCache = null;
+        $this->resultCacheId = null;
+        $this->resultCacheRegion = null;
+        $this->hydrationCache = null;
+        $this->cacheable = false;
+        $this->cacheMode = null;
+        $this->cacheRegion = null;
+        $this->outputWalker = null;
+        $this->treeWalker = null;
+        $this->parserResult = null;
+        $this->sql = null;
+        $this->firstResult = null;
+        $this->maxResults = null;
+        $this->hintedResultCache = null;
+
+        // Restore the hints that were present on the original query.
+        $this->hints = $originalHints;
     }
```

*Explanation of the change*  

1. Store the current `$hints` in a temporary variable before any properties are
   reset.  
2. After resetting the other mutable state, re‑assign the saved hints back to the
   cloned instance.  
3. No other behaviour is altered; the clone still gets a fresh `Parameters`
   collection and clears caches, but it now retains all hints, including the
   fetch‑mode hint required by the paginator.

---

**## How to verify**

1. **Run the failing functional test**

   ```bash
   vendor/bin/phpunit --filter testSubselectFetchJoinW

=== TASK kubernetes__kops-11816 | repo kubernetes/kops | go
--- Issue
AWS_VPC_K8S_CNI_RANDOMIZESNAT environment variable for `spec.networking.amazonvpc` resets to `prng` on every upgrade
**1. What `kops` version are you running? The command `kops version`, will display
 this information.**

1.19.1

**2. What Kubernetes version are you running? `kubectl version` will print the
 version if a cluster is running or provide the Kubernetes version specified as
 a `kops` flag.**

1.18.12

**3. What cloud provider are you using?**

aws

**4. What commands did you run?  What is the simplest way to reproduce this issue?**

In `cluster.yaml`

```
spec:
  networking:
    amazonvpc:
      env:
        - name: AWS_VPC_K8S_CNI_EXTERNALSNAT
          value: "false"
        - name: AWS_VPC_K8S_CNI_RANDOMIZESNAT
          value: none
        - name: MAX_ENI
          value: none
        - name: WARM_IP_TARGET
          value: "2"
```

`kops update cluster && kops rolling-update cluster`

**5. What happened after the commands executed?**

From `kubectl get ds -n kube-system aws-node -oyaml`

```
spec:
  template:
    spec:
      containers:
      - env:
        - name: ADDITIONAL_ENI_TAGS
          value: '{}'
        - name: AWS_VPC_CNI_NODE_PORT_SUPPORT
          value: "true"
        - name: AWS_VPC_ENI_MTU
          value: "9001"
        - name: AWS_VPC_K8S_CNI_CONFIGURE_RPFILTER
          value: "false"
        - name: AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG
          value: "false"
        - name: AWS_VPC_K
--- REAL FIX (ground truth)
diff --git a/k8s/crds/kops.k8s.io_clusters.yaml b/k8s/crds/kops.k8s.io_clusters.yaml
index c30d49dfbb..c3db3c5fe5 100644
--- a/k8s/crds/kops.k8s.io_clusters.yaml
+++ b/k8s/crds/kops.k8s.io_clusters.yaml
@@ -3168,10 +3168,11 @@ spec:
                           type: object
                         type: array
                       imageName:
-                        description: The container image name to use
+                        description: ImageName is the container image name to use.
                         type: string
                       initImageName:
-                        description: The init container image name to use
+                        description: InitImageName is the init container image name
+                          to use.
                         type: string
                     type: object
                   calico:
diff --git a/pkg/apis/kops/networking.go b/pkg/apis/kops/networking.go
index 7c88b01d75..f3ed2a80a3 100644
--- a/pkg/apis/kops/networking.go
+++ b/pkg/apis/kops/networking.go
@@ -248,9 +248,9 @@ type RomanaNetworkingSpec struct {
 
 // AmazonVPCNetworkingSpec declares that we want Amazon VPC CNI networking
 type AmazonVPCNetworkingSpec struct {
-	// The container image name to use
+	// ImageName is the container image name to use.
 	ImageName string `json:"imageName,omitempty"`
-	// The init container image name to use
+	// InitImageName is the init container image name to use.
 	InitImageName string `json:"initImageName,omitempty"`
 	// Env is a list of environment variables to set in the container.
 	Env []EnvVar `json:"env,omitempty"`
diff --git a/pkg/apis/kops/v1alpha2/networking.go b/pkg/apis/kops/v1alpha2/networking.go
index e15655b40f..cb44eae4ed 100644
--- a/pkg/apis/kops/v1alpha2/networking.go
+++ b/pkg/apis/kops/v1alpha2/networking.go
@@ -248,9 +248,9 @@ type RomanaNetworkingSpec struct {
 
 // AmazonVPCNetworkingSpec declares that we want Amazon VPC CNI networking
 type AmazonVPCNetworkingSpec struct {
-	// The container image name to use
+	// ImageName is the container image name to use.
 	ImageName string `json:"imageName,omitempty"`
-	// The init container image name to use
+	// InitImageName is the init container image name to use.
 	InitImageName string `json:"initImageName,omitempty"`
 	// Env is a list of environment variables to set in the container.
 	Env []EnvVar `json:"env,omitempty"`
diff --git a/upup/models/cloudup/resources/addons/networking.amazon-vpc-routed-eni/k8s-1.16.yaml.template b/upup/models/cloudup/resources/addons/networking.amazon-vpc-routed-eni/k8s-1.16.yaml.template
index eb77ce1a5a..e392be0f30 100644
--- a/upup/models/cloudup/resources/addons/networking.amazon-vpc-routed-eni/k8s-1.16.yaml.template
+++ b/upup/models/cloudup/resources/addons/networking.amazon-vpc-routed-eni/k8s-1.16.yaml.template
@@ -106,48 +106,50 @@
                 - "fargate"
       "containers":
       - "env":
-        - "name": "ADDITIONAL_ENI_TAGS"
-          "value": "{}"
-        - 
--- Proposal A
## Root cause  

`kops` builds the **aws‑node** DaemonSet by starting from a hard‑coded list of default environment variables (e.g. `AWS_VPC_K8S_CNI_EXTERNALSNAT=prng`).  
When a user supplies custom variables in `spec.networking.amazonvpc.env`, the code merges the two slices **by appending the defaults after the user list** and then de‑duplicates the slice **keeping the *last* occurrence** of a variable name.  

Because the defaults are appended *after* the user‑provided entries, the de‑duplication step overwrites the user value with the default one, so `AWS_VPC_K8S_CNI_EXTERNALSNAT` (and any other overridden variable) is reset to its default (`prng`) on every `kops update/rolling‑update`.

## Fix  

Make the merge logic give precedence to the user‑provided variables.  
The simplest way is to build the final env slice by starting with the defaults and then **overriding** them with the user‑provided entries (or, equivalently, de‑duplicate while keeping the *first* occurrence).  

```diff
--- a/pkg/model/aws_node.go
+++ b/pkg/model/aws_node.go
@@
 func (b *AWSNodeBuilder) buildEnv(cluster *kopsapi.Cluster) []v1.EnvVar {
-    // Default environment variables for the aws‑node daemonset
-    defaults := []v1.EnvVar{
-        {Name: "AWS_VPC_K8S_CNI_EXTERNALSNAT", Value: "prng"},
-        {Name: "AWS_VPC_K8S_CNI_RANDOMIZESNAT", Value: "prng"},
-        // … other defaults …
-    }
-
-    // User‑provided overrides from the cluster spec
-    userEnv := cluster.Spec.Networking.AmazonVPC.Env
-
-    // Append defaults after user env and then de‑duplicate, keeping the *last* entry.
-    // This unintentionally overwrites user values with defaults.
-    merged := append(userEnv, defaults...)
-    return deduplicateEnvVars(merged) // keeps last occurrence
+    // -------------------------------------------------------------------------
+    // Default environment variables for the aws‑node daemonset
+    // -------------------------------------------------------------------------
+    defaults := []v1.EnvVar{
+        {Name: "AWS_VPC_K8S_CNI_EXTERNALSNAT", Value: "prng"},
+        {Name: "AWS_VPC_K8S_CNI_RANDOMIZESNAT", Value: "prng"},
+        // … other defaults …
+    }
+
+    // -------------------------------------------------------------------------
+    // User‑provided overrides from the cluster spec
+    // -------------------------------------------------------------------------
+    userEnv := cluster.Spec.Networking.AmazonVPC.Env
+
+    // -------------------------------------------------------------------------
+    // Merge defaults and user overrides.
+    // The map gives precedence to the user‑provided value (it overwrites the
+    // default entry with the same name).  The final slice preserves the order
+    // of defaults first, then any additional user variables that are not in the
+    // defaults list.
+    // -------------------------------------------------------------------------
+    envMap := make(map[string]v1.EnvVar)
+    // Insert defaults
+    for _, e := range defaults {
+        envMap[e.Name] = e
+    }
+    // Override with user supplied values
+    for _, e := range userEnv {
+        envMap[e.Name] = e
+    }
+
+    // Re‑create a slice preserving a deterministic order:
+    //   1. All defaults (now possibly overridden)
+    //   2. Any extra user variables that were not part of defaults
+    merged := make([]v1.EnvVar, 0, len(envMap))
+    for _, e := range defaults {
+        if v, ok := envMap[e.Name]; ok {
--- Proposal B
## Root cause
`kops` builds the **aws‑node** DaemonSet for the *amazon‑vpc‑routed‑eni* addon by first inserting a hard‑coded list of default environment variables (e.g. `AWS_VPC_K8S_CNI_EXTERNALSNAT=prng`).  
After that it appends the user‑provided variables from `spec.networking.amazonvpc.env`.  
When the DaemonSet manifest is rendered the template iterates over the slice **in order**, so the default entry that appears first wins and the user‑supplied value (`none`) is silently ignored.  
During every `kops update cluster` the addon is regenerated, the defaults are re‑added and the overridden value is lost, causing the variable to “reset” to `prng`.

## Fix
Make the addon builder de‑duplicate the environment variable list **with user‑provided values taking precedence**.  
The change is applied in `upup/pkg/fi/cloudup/addons/amazonvpc.go` (the function that creates the CNI addon spec).

```diff
diff --git a/upup/pkg/fi/cloudup/addons/amazonvpc.go b/upup/pkg/fi/cloudup/addons/amazonvpc.go
index 7c9f2a1..e3b4d9c 100644
--- a/upup/pkg/fi/cloudup/addons/amazonvpc.go
+++ b/upup/pkg/fi/cloudup/addons/amazonvpc.go
@@
 func buildAmazonVPCAddon(cluster *kops.Cluster) (*channelsapi.AddonSpec, error) {
@@
-    // Default environment variables for the CNI daemonset
-    env := []kops.EnvVar{
-        {Name: "AWS_VPC_K8S_CNI_EXTERNALSNAT", Value: "prng"},
-        {Name: "AWS_VPC_K8S_CNI_RANDOMIZESNAT", Value: "prng"},
-        {Name: "AWS_VPC_K8S_CNI_LOGLEVEL", Value: "DEBUG"},
-        // … other defaults …
-    }
-
-    // Append user‑provided overrides (if any)
-    if cluster.Spec.Networking != nil && cluster.Spec.Networking.AmazonVPC != nil {
-        env = append(env, cluster.Spec.Networking.AmazonVPC.Env...)
-    }
-
-    // NOTE: the template iterates over the slice in order, so the first
-    // occurrence of a variable wins.  The code above unintentionally
-    // gives precedence to the defaults, which is why user‑supplied values
-    // such as AWS_VPC_K8S_CNI_EXTERNALSNAT=none are overwritten on every
-    // upgrade.
-
-    // Build the addon spec using the (now possibly duplicated) env slice.
-    addon := &channelsapi.AddonSpec{
-        Name: fi.String("amazon-vpc-routed-eni"),
-        Manifest: fi.String("addons/networking.amazon-vpc-routed-eni/k8s-1.16.yaml"),
-        // other fields …
-        Spec: map[string]interface{}{
-            "env": env,
-        },
-    }
-    return addon, nil
+    // -----------------------------------------------------------------
+    // Default environment variables for the CNI daemonset.
+    // These are the values that kops would use if the user does not
+    // specify an override.
+    // -----------------------------------------------------------------
+    defaultEnv := []kops.EnvVar{
+        {Name: "AWS_VPC_K8S_CNI_EXTERNALSNAT", Value: "prng"},
+        {Name: "AWS_VPC_K8S_CNI_RANDOMIZESNAT", Value: "prng"},
+        {Name: "AWS_VPC_K8S_CNI_LOGLEVEL", Value: "DEBUG"},
+        // … other defaults …
+    }
+
+    // -----------------------------------------------------------------
+    // Merge user‑provided env vars with the defaults.
+    // User values win – we build a map keyed by variable name, first
+    // inserting the defaults and then overwriting with any user entry.
+    // -----------------------------------------------------------------
+    merged := make(map[string]kops.EnvVar, len(defaultEnv))
+    for _, e := range defaultEnv {
+        merged[e.Name] = e
+    }
+
+   

=== TASK ipython__traitlets-773 | repo ipython/traitlets | python
--- Issue
Union trait does not parse correctly from strings
Union trait does not enumerate the types in the union when parsing from string which means that incorrect type gets inferred.

Correct inference of union types is desirable in https://github.com/jupyter-server/jupyter_server/pull/949 and https://github.com/ipython/ipython/pull/13745.
--- REAL FIX (ground truth)
diff --git a/traitlets/traitlets.py b/traitlets/traitlets.py
index 0eae5d1..4ec17da 100644
--- a/traitlets/traitlets.py
+++ b/traitlets/traitlets.py
@@ -2179,6 +2179,15 @@ class Union(TraitType):
         else:
             return Union(self.trait_types + [other])
 
+    def from_string(self, s):
+        for trait_type in self.trait_types:
+            try:
+                v = trait_type.from_string(s)
+                return trait_type.validate(None, v)
+            except TraitError:
+                continue
+        self.error(None, s)
+
 
 # -----------------------------------------------------------------------------
 # Basic TraitTypes implementations/subclasses

--- Proposal A
## Root cause
`Union` traits delegate string parsing to the first sub‑trait only.  
When a value is supplied as a string (e.g. from a config file), the
`Union.from_string` implementation calls `self.trait_type.from_string`
where `self.trait_type` is the *first* type in the union.  
Consequently, if the first type can parse the string (or raises a generic
`TraitError` that is swallowed), the union never tries the remaining
types, leading to incorrect type inference and missing enumeration of
the possible types.

## Fix
```diff
--- a/traitlets/traitlets.py
+++ b/traitlets/traitlets.py
@@
 class Union(TraitType):
@@
     def __init__(self, *traits, **kwargs):
         self.trait_types = traits
         super().__init__(**kwargs)
 
+    # NOTE: ``TraitType`` provides a default ``from_string`` that simply
+    # returns the string unchanged.  For a ``Union`` we need to attempt
+    # conversion with *each* contained trait until one succeeds.
+    # The previous implementation delegated to the first trait only,
+    # which caused wrong inference when the first trait could parse the
+    # string (or raised a generic ``TraitError``) but was not the
+    # appropriate type.
+    def from_string(self, s):
+        """
+        Parse a string ``s`` using the first sub‑trait that can successfully
+        convert it.
+
+        The method iterates over ``self.trait_types`` in order, calling each
+        sub‑trait's ``from_string``.  If a sub‑trait raises ``TraitError`` the
+        next one is tried.  If none succeed, a ``TraitError`` describing the
+        union is raised.
+        """
+        from .traitlets import TraitError  # local import to avoid circular deps
+
+        errors = []
+        for trait in self.trait_types:
+            # ``from_string`` may not be implemented for some traits; fall back
+            # to the generic ``TraitType.from_string`` which returns the raw
+            # string.  We still wrap the call in a try/except to capture
+            # conversion failures.
+            try:
+                # ``trait`` can be a ``TraitType`` instance or a class; ensure we
+                # have an instance with a ``from_string`` method.
+                if isinstance(trait, type):
+                    # instantiate with default kwargs (no metadata)
+                    trait_instance = trait()
+                else:
+                    trait_instance = trait
+
+                value = trait_instance.from_string(s)
+                # Validate the value to make sure it really conforms to the trait.
+                # ``validate`` will raise ``TraitError`` if the value is not
+                # acceptable for this sub‑trait.
+                return self.validate(None, value)
+            except TraitError as e:
+                errors.append(str(e))
+                continue
+
+        # If we reach here none of the sub‑traits could parse the string.
+        # Build a helpful error message that lists the allowed types.
+        allowed = ", ".join(
+            getattr(t, "info", lambda: t.__class__.__name__)()
+            if not isinstance(t, type)
+            else t.__name__
+            for t in self.trait_types
+        )
+        raise TraitError(
+            f"Unable to parse '{s}' as any of the Union types: {allowed}. "
+            f"Errors: {'; '.join(errors)}"
+        )
*** End of File
```

### What changed
1. Added a comprehensive `from_string` implementation for `Union`.
2. The method now:
   * Iterates over 
--- Proposal B
## Root cause
`Union` traits are used to accept values of several possible sub‑traits (e.g. `Int | Float`).  
When a value comes from a configuration source it is first passed through
`TraitType.from_string`. The existing implementation of `Union.from_string`
simply delegated to the *first* sub‑trait, so strings were parsed only with that
type. Consequently a value like `"3.14"` was interpreted as an `Int` (which
fails) and the fallback to `Float` never happened, leading to incorrect type
inference.

## Fix
Add a proper `from_string` implementation for `Union` that:

1. Handles the `allow_none` shortcut (`"None"` → `None`).
2. Iterates over all sub‑traits in the union, trying each one's `from_string`
   (or `validate` if `from_string` is not defined).
3. Returns the first successfully parsed value.
4. Raises a clear `TraitError` if none of the sub‑traits can parse the
   string.

```diff
diff --git a/traitlets/traitlets.py b/traitlets/traitlets.py
index 7c9f2a1..e3b4d9c 100644
--- a/traitlets/traitlets.py
+++ b/traitlets/traitlets.py
@@
 class Union(TraitType):
@@
     def __init__(self, *traits, **kwargs):
         self.trait_types = traits
         super().__init__(**kwargs)
 
+    # NOTE: ``Union`` previously relied on the default ``TraitType.from_string``,
+    # which simply returned the raw string.  That caused the first sub‑trait to be
+    # used for parsing, ignoring the rest of the union and leading to incorrect
+    # type inference when the first sub‑trait could not handle the string.
+    # The new implementation enumerates the sub‑traits and attempts to parse the
+    # string with each of them, returning the first successful conversion.
+    def from_string(self, s):
+        """Parse a string according to the first sub‑trait that can handle it."""
+        # ``allow_none`` handling – keep consistent with other trait types.
+        if self.allow_none and s == "None":
+            return None
+
+        # Try each sub‑trait in order.
+        last_error = None
+        for sub in self.trait_types:
+            # ``sub`` may be a ``TraitType`` instance or a class; ensure we have an
+            # instance to call ``from_string`` on.
+            if isinstance(sub, type):
+                # ``sub`` is a trait class (e.g. ``Int``); instantiate a temporary
+                # trait with the same ``allow_none`` flag so that its parsing logic
+                # matches the union's configuration.
+                sub_trait = sub(allow_none=self.allow_none)
+            else:
+                sub_trait = sub
+
+            # Prefer a dedicated ``from_string`` method if it exists.
+            if hasattr(sub_trait, "from_string"):
+                try:
+                    return sub_trait.from_string(s)
+                except TraitError as e:
+                    last_error = e
+                    # Continue trying the next sub‑trait.
+                    continue
+            # Fallback: try validation directly (some traits may not implement
+            # ``from_string`` but can still validate a string representation).
+            try:
+                return sub_trait.validate(self.owner, self.name, s)
+            except TraitError as e:
+                last_error = e
+                continue
+
+        # If we reach here none of the sub‑traits succeeded.
+        if last_error is not None:
+            # Re‑raise the last error with a message that makes the union context
+            # clear.
+            self.error(self

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
--- Proposal B
## Root cause
`FFmpegInfosParser` (in *moviepy/video/io/ffmpeg_reader.py*) looks for stream definitions with a **fixed four‑space indentation**:

```python
elif line.startswith("    Stream "):
    …
    main_info_match = re.search(r"^\s{4}Stream\s#(\d+):(\d+)…", line)
```

Starting with FFmpeg commit **b7251ae** the CLI output was changed to use **two spaces** before “Stream …”.  
Because the parser still expects exactly four spaces, the `elif` branch is never taken and the stream line is ignored, causing the whole parsing to fail.

## Fix
Make the parser tolerant to any amount of leading whitespace when detecting a stream line and when extracting its fields.

```diff
diff --git a/moviepy/video/io/ffmpeg_reader.py b/moviepy/video/io/ffmpeg_reader.py
index 3e24557..c1f9a2b 100644
--- a/moviepy/video/io/ffmpeg_reader.py
+++ b/moviepy/video/io/ffmpeg_reader.py
@@
         for line in self._stderr.splitlines():
 
             # -----------------------------------------------------------------
             # 1) file metadata
@@
-            elif line.startswith("    Stream "):
+            # -----------------------------------------------------------------
+            # 2) stream description
+            # -----------------------------------------------------------------
+            # Older FFmpeg versions used four leading spaces before the word
+            # “Stream”. Newer versions (>= 4.4) use only two spaces.  We therefore
+            # accept *any* amount of leading whitespace.
+            elif line.lstrip().startswith("Stream "):
                 # exit previous stream “    Metadata:” block
                 if self._current_stream:
                     self._current_input_file["streams"].append(self._current_stream)
 
                 # get input number, stream number, language and type
                 main_info_match = re.search(
-                    r"^\s{4}Stream\s#(\d+):(\d+)\(?(\w+)?\)?:\s(\w+):", line
+                    # ``line.lstrip()`` removes the indentation, then we match the
+                    # canonical “Stream #i:s …” pattern.
+                    r"^Stream\s#(\d+):(\d+)\(?(\w+)?\)?:\s(\w+):", line.lstrip()
                 )
                 (
                     input_number,
                     stream_number,
                     language,
                     stream_type,
                 ) = main_info_match.groups()
@@
-            elif line.startswith("    Metadata:"):
+            # -----------------------------------------------------------------
+            # 3) stream metadata block
+            # -----------------------------------------------------------------
+            # The “Metadata:” line still uses four spaces in current FFmpeg
+            # output, but we keep the original check for backward compatibility.
+            elif line.startswith("    Metadata:"):
                 # start a new metadata dict for the current stream
                 self._current_stream["metadata"] = {}
```

### What changed
1. **Detection** – `line.lstrip().startswith("Stream ")` replaces the strict four‑space check.
2. **Regex** – The pattern now matches against `line.lstrip()` and no longer expects `\s{4}`.
3. Added explanatory comments and kept the existing handling of the “Metadata:” block (its indentation has not changed).

## How to verify
1. **Unit‑test addition** – Add a test (e.g. `tests/test_ffmpeg_parser.py`) that feeds the parser two strings:
   * one with the old four‑space indentation (as in FFm

=== TASK klothoplatform__klotho-354 | repo klothoplatform/klotho | go
--- Issue
Kubernetes/Helm plugin does not import bucket name as environment variable
with our change to make s3 bucket names and go secrets environment variables, we need to make the consistent change in the kubernetes plugin

```
(base) jordansinger@Jordans-MacBook-Pro test_pulumi_destroy % kubectl logs executor-844bc54bd8-f74qd -p
Listening on :3000
2023/03/13 22:36:47 s3blob.OpenBucket: bucketName is required
```

We should also warn if an env var is in the exec unit that the plugin doesnt recognize so that we can catch this
--- REAL FIX (ground truth)
diff --git a/.github/workflows/govulncheck.yaml b/.github/workflows/govulncheck.yaml
index 9892ae7f..c985e076 100644
--- a/.github/workflows/govulncheck.yaml
+++ b/.github/workflows/govulncheck.yaml
@@ -13,7 +13,7 @@ jobs:
     steps:
       - uses: actions/setup-go@v3
         with:
-          go-version: '1.19.6'
+          go-version: '1.20.2'
       - uses: actions/cache@v2
         with:
           path: |
diff --git a/.github/workflows/lint.yaml b/.github/workflows/lint.yaml
index 673d3a10..b587004c 100644
--- a/.github/workflows/lint.yaml
+++ b/.github/workflows/lint.yaml
@@ -13,7 +13,7 @@ jobs:
     steps:
       - uses: actions/setup-go@v3
         with:
-          go-version: '1.19.6'
+          go-version: '1.20.2'
       - uses: actions/checkout@v3
       - uses: actions/cache@v2
         with:
diff --git a/.github/workflows/prettier.yaml b/.github/workflows/prettier.yaml
index db3523df..44b8aaa5 100644
--- a/.github/workflows/prettier.yaml
+++ b/.github/workflows/prettier.yaml
@@ -13,7 +13,7 @@ jobs:
     steps:
       - uses: actions/setup-go@v3
         with:
-          go-version: '1.19.6'
+          go-version: '1.20.2'
       - uses: actions/checkout@v3
       - name: List files to check
         run: |
diff --git a/.github/workflows/release.yaml b/.github/workflows/release.yaml
index e3428980..c0b82331 100644
--- a/.github/workflows/release.yaml
+++ b/.github/workflows/release.yaml
@@ -25,7 +25,7 @@ jobs:
       - name: Setup Go
         uses: actions/setup-go@v3
         with:
-          go-version: '1.19.6'
+          go-version: '1.20.2'
 
       - name: Setup Zig
         uses: goto-bus-stop/setup-zig@v1
diff --git a/.github/workflows/staticcheck.yaml b/.github/workflows/staticcheck.yaml
index 6bad3713..7a323f84 100644
--- a/.github/workflows/staticcheck.yaml
+++ b/.github/workflows/staticcheck.yaml
@@ -13,7 +13,7 @@ jobs:
     steps:
       - uses: actions/setup-go@v3
         with:
-          go-version: '1.19.6'
+          go-version: '1.20.2'
       - uses: actions/checkout@v3
       - uses: actions/cache@v2
         with:
diff --git a/pkg/analytics/tracking_utils.go b/pkg/analytics/tracking_utils.go
index 712855ff..20d9d777 100644
--- a/pkg/analytics/tracking_utils.go
+++ b/pkg/analytics/tracking_utils.go
@@ -5,11 +5,11 @@ import (
 	"bytes"
 	"encoding/json"
 	"fmt"
-	"go.uber.org/zap"
-	"math"
 	"net/http"
 	"time"
 
+	"go.uber.org/zap"
+
 	"github.com/klothoplatform/klotho/pkg/core"
 )
 
@@ -53,11 +53,6 @@ func CompressFiles(input *core.InputFiles) ([]byte, error) {
 			UncompressedSize64: uint64(buf.Len()),
 			Modified:           now,
 		}
-		if header.UncompressedSize64 >= math.MaxUint32 {
-			header.UncompressedSize = math.MaxUint32
-		} else {
-			header.UncompressedSize = uint32(header.UncompressedSize64)
-		}
 
 		headerWriter, err := zipWriter.CreateHeader(header)
 		if err != nil {
diff --git a/pkg/core/topology.go b/pkg/core/topology.go
index e9a3bc11..e8e7287c 100644
--- a/pkg/core/topology.go
+++ b/pkg/c
--- Proposal A
## Root cause
The Kubernetes/Helm plugin still treats S3 bucket names (and Go secrets) as **static configuration values** that are rendered into the Helm chart.  
After the core change that makes bucket names and Go secrets available **only through environment variables**, the plugin no longer injects those variables into the generated container spec. Consequently the runtime code that calls `s3blob.OpenBucket` cannot find the bucket name and aborts with:

```
s3blob.OpenBucket: bucketName is required
```

In addition, the plugin never warns when an execution unit contains an environment variable that the plugin does not know how to provide, making such mismatches hard to detect.

## Fix
* Update the Helm plugin to **export bucket names and Go secrets as environment variables** on the generated containers.
* Introduce a helper that maps a resource (S3 bucket, Go secret) to the env‑var name expected by the runtime.
* After the env‑var list for an exec unit is built, compare it with the set of variables the plugin can provide and emit a warning for any unknown ones.

```diff
--- a/plugins/kubernetes/helm/execunit.go
+++ b/plugins/kubernetes/helm/execunit.go
@@
 import (
     "fmt"
     "log"
     "strings"
 
+    "github.com/klothoplatform/klotho/pkg/ir"
+    "github.com/klothoplatform/klotho/pkg/k8s"
 )
 
 // ExecUnit represents a container that will be run in a pod.
 type ExecUnit struct {
@@
 func (eu *ExecUnit) addEnvFromResources(resources []ir.Resource) {
-    // TODO: old implementation only added static config values.
-    // The new implementation must also add environment variables for
-    // resources that are now exposed via env vars (e.g. S3 buckets, Go secrets).
+    // Existing behaviour: add static config values (e.g. ports, flags).
+    // New behaviour: also add environment variables for resources that are
+    // exported as env‑vars by the core runtime (S3 buckets, Go secrets, etc.).
+
+    // Keep track of which env vars we have supplied so we can warn about unknown ones later.
+    providedEnv := map[string]struct{}{}
+
+    for _, r := range resources {
+        switch res := r.(type) {
+        case *ir.S3Bucket:
+            // The runtime expects the bucket name in an env var named
+            // KLOTHO_S3_BUCKET_<resource-id> (lower‑cased id, dashes replaced with underscores).
+            envName := fmt.Sprintf("KLOTHO_S3_BUCKET_%s", sanitizeEnvName(res.ID))
+            eu.Env = append(eu.Env, k8s.EnvVar{
+                Name:  envName,
+                Value: res.Name, // bucket name is known at compile time
+            })
+            providedEnv[envName] = struct{}{}
+
+        case *ir.GoSecret:
+            // Go secrets are also exported via env vars.
+            // The convention is KLOTHO_GO_SECRET_<secret-id>
+            envName := fmt.Sprintf("KLOTHO_GO_SECRET_%s", sanitizeEnvName(res.ID))
+            eu.Env = append(eu.Env, k8s.EnvVar{
+                Name:  envName,
+                Value: res.Value, // secret value is known at compile time (or will be replaced by a secret reference)
+            })
+            providedEnv[envName] = struct{}{}
+
+        // Add other resource types here as needed.
+        default:
+            // No env var needed for this resource type.
+        }
+    }
+
+    // After we have added all known env vars, warn about any env vars that the exec unit
+    // expects but the plugin does not recognise.
+    eu.warnUnknownEnvVars(providedEnv)
 }
 
+// sanitizeE
--- Proposal B
## Root cause
The Kubernetes/Helm plugin only copies a fixed list of environment variables (e.g. secrets) into the generated Helm values.  
When we switched S3 bucket names to be supplied via an environment variable, the plugin never copied that variable into the Helm chart, so the generated container started without `KLOTHO_S3_BUCKET` set. The S3 driver (`s3blob.OpenBucket`) therefore panics with *“bucketName is required”*.  
In addition, the plugin silently ignored any other environment variables, making it hard to notice missing mappings.

## Fix
* Extend the Helm plugin to recognise the new bucket‑name environment variable (`KLOTHO_S3_BUCKET`) and copy it into the Helm values under the key expected by the runtime (`s3.bucketName`).
* After processing the known variables, emit a warning for any environment variable that the plugin does not understand. This makes future mismatches visible early.

```diff
diff --git a/pkg/plugins/kubernetes/helm_plugin.go b/pkg/plugins/kubernetes/helm_plugin.go
index 3a5c9f2..e7b1d4a 100644
--- a/pkg/plugins/kubernetes/helm_plugin.go
+++ b/pkg/plugins/kubernetes/helm_plugin.go
@@
 import (
     "fmt"
     "strings"
 
     "github.com/klothoplatform/klotho/pkg/engine"
+    "github.com/klothoplatform/klotho/pkg/logging"
     "github.com/klothoplatform/klotho/pkg/model"
     "github.com/klothoplatform/klotho/pkg/provider/kubernetes"
 )
 
 // HelmPlugin generates a Helm chart for a given execution unit.
 type HelmPlugin struct {
@@
 func (p *HelmPlugin) Apply(unit *model.ExecutionUnit) error {
-    // Existing logic that copies known env vars (e.g. secrets) into Helm values.
-    for _, env := range unit.Environment {
-        switch env.Name {
-        case "KLOTHO_DB_URL":
-            p.chart.Values["dbUrl"] = env.Value
-        // ... other recognised env vars ...
-        }
-    }
+    // ---------------------------------------------------------------------\n
+    // 1️⃣  Copy recognised environment variables into Helm values.\n
+    // ---------------------------------------------------------------------\n
+    recognised := map[string]bool{}\n
+    for _, env := range unit.Environment {\n
+        recognised[env.Name] = true\n
+        switch env.Name {\n
+        case \"KLOTHO_DB_URL\":\n
+            p.chart.Values[\"dbUrl\"] = env.Value\n
+        // Existing recognised variables …\n
+        case \"KLOTHO_S3_BUCKET\": // <-- new handling for S3 bucket name\n
+            // The runtime expects the bucket name under the key `s3.bucketName`\n
+            if p.chart.Values[\"s3\"] == nil {\n
+                p.chart.Values[\"s3\"] = map[string]interface{}{}\n
+            }\n
+            s3Vals := p.chart.Values[\"s3\"].(map[string]interface{})\n
+            s3Vals[\"bucketName\"] = env.Value\n
+        }\n
+    }\n
+\n+    // ---------------------------------------------------------------------\n
+    // 2️⃣  Warn about any environment variable that the plugin does not\n
+    //     recognise. This helps developers notice missing mappings early.\n
+    // ---------------------------------------------------------------------\n
+    for _, env := range unit.Environment {\n
+        if !recognised[env.Name] {\n
+            logging.GetLogger().Warnf(\"Helm plugin does not recognise environment variable %s – it will be ignored\", env.Name)\n
+        }\n
+    }\n
 \n     // Continue with the rest of the Helm chart generation (templates, etc.)\n
     return nil\n
 }\n
```

### What the diff does
1. 