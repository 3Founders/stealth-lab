

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

--- REAL FIX (ground truth)
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
 for name in ['float64', 'float32', 'int64', 'int32', 'complex128', 'complex64',
              'is_tensor', 'ones', 'zeros', 'any', 'trace', 'cumsum', 'tensordot',
              'zeros_like', 'reshape', 'eye', 'max', 'min', 'prod', 'abs', 'matmul',
-             'sqrt', 'sign', 'where', 'conj', 'diag', 'finfo', 'einsum', 'log2', 'sin', 'cos']:
+             'sqrt', 'sign', 'where', 'conj', 'finfo', 'einsum', 'log2', 'sin', 'cos']:
     PyTorchBackend.register_method(name, getattr(torch, name))
 
 
diff --git a/tensorly/backend/tensorflow_backend.py b/tensorly/backend/tensorflow_backend.py
index 3da3142..1d89fb5 100644
--- a/tensorly/backend/tensorflow_backend.py
+++ b/tensorly/backend/tensorflow_backend.py
@@ -191,7 +191,7 @@ _FUN_NAMES = [
     (np.complex64, 'complex64'),
     (tf.ones, 'ones'),
     (tf.zeros, 'zeros'),
-    (tf.linalg.tensor_diag, 'diag'),
+    (tf.linalg.diag, 'diag'),
     (tf.zeros_like, 'zeros_like'),
     (tf.eye, 'eye'),
     (tf.reshape, 'reshape'),

--- Proposal A
## Root cause
`tl.partial_svd` truncates the full SVD to `n_eigenvecs` columns.  
When `n_eigenvecs` is larger than the true rank of the matrix, the function simply slices the already‑computed factors:

```python
u = u[:, :n_eigenvecs]
s = s[:n_eigenvecs]
v = v[:n_eigenvecs, :]
```

If the matrix rank is `r < n_eigenvecs`, the extra columns of `u` (and rows of `v`) are filled with **zeros**.  
Zero columns are not orthonormal, so `U.T @ U` contains a zero block on the diagonal, which is exactly the behaviour observed in the issue.

## Fix
* After computing the full SVD we keep the `r` non‑zero singular vectors.
* If `n_eigenvecs > r` we **extend** the left and right singular bases with an orthonormal complement (using a QR decomposition on a random matrix that is orthogonalised against the existing basis).
* The singular values are padded with zeros to keep the decomposition consistent.

```diff
--- a/tensorly/decomposition/svd.py
+++ b/tensorly/decomposition/svd.py
@@
-def partial_svd(matrix, n_eigenvecs=None, **kwargs):
-    """Partial singular value decomposition.
-
-    Parameters
-    ----------
-    matrix : tensor
-        Input matrix.
-    n_eigenvecs : int, optional
-        Number of singular vectors/values to compute. If ``None`` (default) the
-        full decomposition is returned.
-
-    Returns
-    -------
-    u, s, vt : tensors
-        Left singular vectors, singular values and right singular vectors.
-    """
-    # Compute full SVD
-    u, s, vt = tl.linalg.svd(matrix, full_matrices=False, **kwargs)
-
-    if n_eigenvecs is None:
-        return u, s, vt
-
-    # Truncate to the requested number of components
-    u = u[:, :n_eigenvecs]
-    s = s[:n_eigenvecs]
-    vt = vt[:n_eigenvecs, :]
-    return u, s, vt
+def partial_svd(matrix, n_eigenvecs=None, **kwargs):
+    """Partial singular value decomposition.
+
+    This function returns the first ``n_eigenvecs`` singular vectors/values.
+    If ``n_eigenvecs`` is larger than the true rank of ``matrix`` the
+    decomposition is **extended** with an orthonormal complement so that the
+    returned left and right singular vectors are always orthonormal.
+
+    Parameters
+    ----------
+    matrix : tensor
+        Input matrix.
+    n_eigenvecs : int, optional
+        Number of singular vectors/values to compute. If ``None`` (default) the
+        full decomposition is returned.
+
+    Returns
+    -------
+    u, s, vt : tensors
+        Left singular vectors, singular values and right singular vectors.
+    """
+    # ------------------------------------------------------------------ #
+    # 1. Compute the full (economy) SVD
+    # ------------------------------------------------------------------ #
+    u_full, s_full, vt_full = tl.linalg.svd(matrix, full_matrices=False, **kwargs)
+    rank = s_full.shape[0]                     # true numerical rank
+
+    # ------------------------------------------------------------------ #
+    # 2. If no truncation is requested, simply return the full factors
+    # ------------------------------------------------------------------ #
+    if n_eigenvecs is None:
+        return u_full, s_full, vt_full
+
+    # ------------------------------------------------------------------ #
+    # 3. Keep the existing singular vectors/values up to the true rank
+    # ------------------------------------------------------------------ #
+    keep = min(rank, n_eigenvecs)
+    u = u_full[:, :keep]
+    s = s_full[:keep]
+    vt = vt_ful
--- Proposal B
**## Root cause**

`tl.partial_svd` builds the singular vectors from the eigen‑decomposition of `A @ Aᵀ`.  
When the requested number of singular vectors (`n_eigenvecs`) is larger than the true rank of the matrix, the algorithm pads the remaining columns with zeros.  
These zero‑columns are **not orthogonalised** to the already computed basis, so the returned matrix `U` (and analogously `V`) is no longer orthonormal:

```
U = [u₁ … u_r  0 … 0]          # r = rank(A)
UᵀU = diag(1,…,1,0,…,0) ≠ I
```

Hence `UᵀU` (and `VᵀV`) fails the orthonormality check.

**## Fix (a unified diff with your best‑guess file paths and code)**

```diff
--- a/tensorly/decomposition/svd.py
+++ b/tensorly/decomposition/svd.py
@@
 def partial_svd(tensor, n_eigenvecs=None, **kwargs):
@@
-    # Existing implementation (simplified)
-    U, s, V = _backend.partial_svd(tensor, n_eigenvecs=n_eigenvecs, **kwargs)
-    return U, s, V
+    # ------------------------------------------------------------------
+    # Original backend call – it returns U (m×k), s (k,) and V (n×k)
+    # where k = n_eigenvecs (or the full rank if None).  When k is larger
+    # than the true numerical rank, the extra columns are zero and are
+    # therefore not orthonormal.
+    # ------------------------------------------------------------------
+    U, s, V = _backend.partial_svd(tensor, n_eigenvecs=n_eigenvecs, **kwargs)
+
+    # ------------------------------------------------------------------
+    # Post‑process the result to guarantee orthonormal columns even when
+    # more vectors are requested than the matrix rank.
+    # ------------------------------------------------------------------
+    if n_eigenvecs is not None:
+        # Numerical tolerance for considering a singular value as zero
+        eps = tl.finfo(s.dtype).eps
+        rank = int(tl.sum(s > eps))
+
+        if n_eigenvecs > rank:
+            # ------------------------------------------------------------------
+            # 1) Pad the singular values with exact zeros
+            # ------------------------------------------------------------------
+            extra = n_eigenvecs - rank
+            s = tl.concat([s[:rank],
+                           tl.zeros((extra,), **tl.context(tensor))],
+                          axis=0)
+
+            # ------------------------------------------------------------------
+            # 2) Extend U and V to orthonormal bases of ℝᵐ and ℝⁿ
+            #    using a QR decomposition on a random matrix that is
+            #    orthogonalised against the already computed basis.
+            # ------------------------------------------------------------------
+            # Helper to build an orthonormal matrix of size (dim, n_eigenvecs)
+            def _extend_orthonormal(basis, dim):
+                # basis: (dim, rank) – already orthonormal columns
+                if rank == 0:
+                    # No existing basis – just QR a random matrix
+                    Q, _ = tl.linalg.qr(tl.randn((dim, n_eigenvecs), **tl.context(tensor)))
+                    return Q
+                # Random directions for the missing columns
+                rand = tl.randn((dim, extra), **tl.context(tensor))
+                # Concatenate and orthogonalise
+                concat = tl.concat([basis, rand], axis=1)
+                Q, _ = tl.linalg.qr(concat)
+                return Q[:, :n_eigenvecs]
+
+            # Extend U (shape m × n_eigenvecs)
+            U = _extend_orthonormal(U[:,

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
--- REAL FIX (ground truth)
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

--- Proposal A
## Root cause
`pyfar.dsp.normalize` computes a scaling factor for the *energy* and *power* reference methods as  

```python
scale = target / reference_value
```  

where `reference_value` is the signal’s total energy (or average power).  
For an energy‑type reference the correct scaling factor must be the **square‑root** of the ratio, because energy (and power) are proportional to the **square** of the signal amplitude.  
Dividing by the reference value therefore yields a signal whose energy is `target / reference_value` (e.g. `1/20 = 0.05`), which is exactly the behaviour reported in the issue.

## Fix
Update the implementation of `normalize` so that for the `"energy"` and `"power"` reference methods the scaling factor is the square‑root of the ratio `target / reference`.  
All other reference methods (e.g. `"rms"`, `"peak"`) keep the original linear scaling.

```diff
--- a/pyfar/dsp.py
+++ b/pyfar/dsp.py
@@
-def normalize(signal, reference_method="rms", target=1.0):
-    """
-    Normalize a signal with respect to a reference value.
-
-    Parameters
-    ----------
-    signal : Signal
-        Input signal that shall be normalized.
-    reference_method : str, optional
-        Method used to compute the reference value.  Options are
-        ``"rms"``, ``"peak"``, ``"power"``, and ``"energy"``.
-        Default is ``"rms"``.
-    target : float, optional
-        Desired value of the reference after normalization.  Default is ``1.0``.
-
-    Returns
-    -------
-    Signal
-        Normalized copy of the input signal.
-    """
-    # Compute reference value
-    if reference_method == "rms":
-        reference = rms(signal)
-    elif reference_method == "peak":
-        reference = np.max(np.abs(signal.time))
-    elif reference_method == "power":
-        reference = power(signal)
-    elif reference_method == "energy":
-        reference = energy(signal)
-    else:
-        raise ValueError(f"Unknown reference method '{reference_method}'.")
-
-    # Compute scaling factor and apply it
-    scale = target / reference
-    normalized = signal.copy()
-    normalized.time *= scale
-    return normalized
+def normalize(signal, reference_method="rms", target=1.0):
+    """
+    Normalize a signal with respect to a reference value.
+
+    Parameters
+    ----------
+    signal : Signal
+        Input signal that shall be normalized.
+    reference_method : str, optional
+        Method used to compute the reference value.  Options are
+        ``"rms"``, ``"peak"``, ``"power"``, and ``"energy"``.
+        Default is ``"rms"``.
+    target : float, optional
+        Desired value of the reference after normalization.  Default is ``1.0``.
+
+    Returns
+    -------
+    Signal
+        Normalized copy of the input signal.
+    """
+    # --------------------------------------------------------------------- #
+    # 1) Compute the reference value according to the selected method.
+    # --------------------------------------------------------------------- #
+    if reference_method == "rms":
+        reference = rms(signal)
+    elif reference_method == "peak":
+        reference = np.max(np.abs(signal.time))
+    elif reference_method == "power":
+        reference = power(signal)
+    elif reference_method == "energy":
+        reference = energy(signal)
+    else:
+        raise ValueError(f"Unknown reference method '{reference_method}'.")
+
+    # --------------------------------------------------------------------- #
+    # 2) Dete
--- Proposal B
## Root cause
`pyfar.dsp.normalize` treats the *reference* value for the `"power"` and `"energy"` methods as a linear scaling factor.  
For these two methods the reference is a **quadratic** quantity (mean‑square power or summed‑square energy).  
To obtain a signal with a desired power/energy the signal must be scaled by the **square‑root** of the ratio between the target value and the measured reference.  
Because the function divides (or multiplies) by the reference itself, a signal with energy = 20 is scaled by `1/20` instead of `√(1/20)`, leading to the observed inverse‑square‑law behaviour.

## Fix
```diff
--- a/pyfar/dsp.py
+++ b/pyfar/dsp.py
@@
-def normalize(signal, reference_method="rms", target=1.0):
-    """
-    Normalize a signal with respect to a reference value.
-
-    Parameters
-    ----------
-    signal : Signal
-        Input signal.
-    reference_method : str, optional
-        Method used to compute the reference value.  Supported values are
-        ``"rms"``, ``"power"``, and ``"energy"``.  Default is ``"rms"``.
-    target : float, optional
-        Desired value of the reference after normalisation.  Default is ``1.0``.
-
-    Returns
-    -------
-    Signal
-        Normalised signal.
-    """
-    # compute reference value
-    if reference_method == "rms":
-        ref = np.sqrt(np.mean(signal.time ** 2, axis=-1, keepdims=True))
-        scaling = target / ref
-    elif reference_method == "power":
-        ref = np.mean(signal.time ** 2, axis=-1, keepdims=True)
-        scaling = target / ref
-    elif reference_method == "energy":
-        ref = np.sum(signal.time ** 2, axis=-1, keepdims=True)
-        scaling = target / ref
-    else:
-        raise ValueError(f"Unsupported reference_method: {reference_method}")
-
-    # apply scaling
-    out = signal.copy()
-    out.time = signal.time * scaling
-    return out
+def normalize(signal, reference_method="rms", target=1.0):
+    """
+    Normalize a signal with respect to a reference value.
+
+    Parameters
+    ----------
+    signal : Signal
+        Input signal.
+    reference_method : str, optional
+        Method used to compute the reference value.  Supported values are
+        ``"rms"``, ``"power"``, and ``"energy"``.  Default is ``"rms"``.
+    target : float, optional
+        Desired value of the reference after normalisation.  Default is ``1.0``.
+
+    Returns
+    -------
+    Signal
+        Normalised signal.
+    """
+    # ------------------------------------------------------------------
+    # Compute the reference quantity.
+    # For ``rms`` the reference itself is a linear quantity, therefore the
+    # scaling factor is ``target / ref``.
+    # For ``power`` and ``energy`` the reference is quadratic (mean‑square or
+    # summed‑square).  To obtain the requested *target* value we have to scale
+    # the signal by the square‑root of the ratio between the target and the
+    # measured reference.
+    # ------------------------------------------------------------------
+    if reference_method == "rms":
+        # RMS = sqrt(mean(x^2))
+        ref = np.sqrt(np.mean(signal.time ** 2, axis=-1, keepdims=True))
+        scaling = target / ref
+    elif reference_method == "power":
+        # Power = mean(x^2)  (quadratic)
+        ref = np.mean(signal.time ** 2, axis=-1, keepdims=True)
+        scaling = np.sqrt(target / ref)
+    elif reference_method == "energy":
+        # Energy = sum(x^2)  (quadratic)
+        ref = np.sum(signal

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
--- Proposal B
## Root cause
Serverless does not currently translate the `environment` configuration block (both at the **provider** level and the **function** level) into the CloudFormation template for Lambda functions. Consequently, any variables defined by users are never passed to the Lambda runtime, and the feature proposal cannot be realized.

## Fix
Add logic that merges `provider.environment` with `function.environment` (function‑level values overriding provider‑level ones) and injects the resulting map into the generated CloudFormation resources under `Properties.Environment.Variables`.

### Files changed
* `lib/plugins/aws/package/compile/functions.js` – core place where Lambda function resources are built.
* `lib/plugins/aws/provider.js` – expose a helper to resolve environment objects (optional, but keeps code tidy).

```diff
--- a/lib/plugins/aws/package/compile/functions.js
+++ b/lib/plugins/aws/package/compile/functions.js
@@
   compileFunctions() {
     const compiledFunctions = {};
     const allFunctions = this.serverless.service.getAllFunctions();
 
     allFunctions.forEach((functionName) => {
       const func = this.serverless.service.getFunction(functionName);
+      // -----------------------------------------------------------------
+      // 1️⃣ Resolve environment variables
+      // -----------------------------------------------------------------
+      // Provider‑level environment variables are defined under
+      // `service.provider.environment`.  Function‑level variables are
+      // defined under `function.environment`.  Function‑level values
+      // must override provider‑level values.
+      //
+      // The Serverless variable system (e.g. ${self:custom.foo}) is already
+      // resolved elsewhere (via `this.serverless.variables.populateObject`),
+      // so we can safely merge the plain objects here.
+      const providerEnv = this.serverless.service.provider.environment || {};
+      const functionEnv = func.environment || {};
+      const mergedEnv = Object.assign({}, providerEnv, functionEnv);
+
+      // Only add the Environment block if there is at least one variable.
+      const hasEnv = Object.keys(mergedEnv).length > 0;
+
+      // -----------------------------------------------------------------
+      // 2️⃣ Build the CloudFormation resource for the function
+      // -----------------------------------------------------------------
       const functionObject = {
         Type: 'AWS::Lambda::Function',
         Properties: {
           FunctionName: this.provider.naming.getLambdaLogicalId(functionName),
@@
           Role: this.provider.getIamRoleArn(),
           Runtime: func.runtime || this.provider.getRuntime(),
           Timeout: func.timeout || this.provider.getTimeout(),
-          // existing properties …
+          // existing properties …
         },
         DependsOn: this.provider.getDependsOn(),
       };
 
+      // -----------------------------------------------------------------
+      // 3️⃣ Attach the Environment block if needed
+      // -----------------------------------------------------------------
+      if (hasEnv) {
+        functionObject.Properties.Environment = {
+          Variables: mergedEnv,
+        };
+      }
+
       // Attach any additional properties that the user may have defined
       // (e.g. VpcConfig, DeadLetterConfig, etc.)
       if (func.vpc) {
         functionObject.Properties.VpcConfig = func.vpc;
       }
@@
       compiledFunctions[function

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
--- REAL FIX (ground truth)
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

--- Proposal A
## Root cause
`and_then` directly calls the supplied function when the input result is `Ok`.  
If that function raises an exception the exception propagates out of `and_then`, because the call is not wrapped in a `try/except`. The intended semantics of `and_then` (as a monadic bind) are to return an `Err` result when the supplied function fails, not to raise the exception.

## Fix
```diff
--- a/pyresult/operators.py
+++ b/pyresult/operators.py
@@
-from .result import result, is_ok, ok
+from .result import result, is_ok, ok, err
@@
-def and_then(func, res):
-    '''
-    res = result(res)
-    return result(func(res.value)) if is_ok(res) else res
-    '''
-    res = result(res)
-    return result(func(res.value)) if is_ok(res) else res
+def and_then(func, res):
+    """
+    Apply ``func`` to the value inside an ``Ok`` result, returning the
+    result of ``func`` (which must itself be a ``Result``).  If ``res`` is an
+    ``Err`` the original error is propagated unchanged.
+
+    If ``func`` raises an exception, the exception is captured and converted
+    into an ``Err`` result instead of bubbling up.  This mirrors the behaviour
+    of the other combinators (e.g. ``map``) which also protect against user
+    code raising.
+    """
+    # Normalise the incoming value to a Result instance.
+    res = result(res)
+
+    # If the incoming result is an error, just propagate it.
+    if not is_ok(res):
+        return res
+
+    # ``res`` is an ``Ok`` – apply ``func`` safely.
+    try:
+        # ``func`` is expected to return a Result; we normalise it just in case.
+        return result(func(res.value))
+    except Exception as exc:          # pragma: no cover – exercised by the test below
+        # Convert the exception into an Err result.
+        return err(exc)
```

### What was changed
1. Imported `err` from `pyresult.result`.
2. Re‑implemented `and_then`:
   * Added detailed docstring.
   * Normalised the input with `result(res)`.
   * Early‑return the original `Err` when `res` is not `Ok`.
   * Wrapped the call to `func` in a `try/except` block.
   * On exception, return `err(exc)` instead of propagating the exception.

## How to verify
Create a small test (or add to the project's test suite) that ensures an exception raised inside the function passed to `and_then` is turned into an `Err` result.

```python
import pytest
from pyresult import and_then, ok, err, is_err, is_ok

def raise_exception(_):
    raise RuntimeError("boom")

def test_and_then_catches_exception():
    result = and_then(raise_exception, ok(1))
    assert is_err(result)                     # should be an Err, not raise
    # optional: check that the stored exception is the one we raised
    assert isinstance(result.error, RuntimeError)
    assert str(result.error) == "boom"
```

Run the test suite:

```bash
pytest -q
```

All existing tests should still pass, and the new test must succeed, confirming that `and_then` now returns an `Err` when the supplied function raises.
--- Proposal B
## Root cause
`and_then` calls the supplied function directly:

```python
return result(func(res.value)) if is_ok(res) else res
```

If `func` raises an exception the exception propagates out of `and_then`.  
The operator is supposed to convert any exception raised by `func` into an
`Err` result (the same behaviour as the other combinators in the library).

## Fix
Wrap the call to `func` in a `try/except` block and return an `Err` result
when an exception occurs.

```diff
--- a/pyresult/operators.py
+++ b/pyresult/operators.py
@@
-from .result import result, is_ok, ok, err
+from .result import result, is_ok, ok, err
@@
-def and_then(func, res):
-    '''
-    '''
-    res = result(res)
-    return result(func(res.value)) if is_ok(res) else res
+def and_then(func, res):
+    """
+    Chain two ``Result`` values.
+
+    If ``res`` is an ``Ok`` value, ``func`` is called with the inner value.
+    The return value of ``func`` is normalised with :func:`result` and
+    returned.  If ``func`` raises an exception, the exception is captured
+    and turned into an ``Err`` result instead of bubbling up.
+
+    If ``res`` is already an ``Err`` the original error is returned unchanged.
+    """
+    # Normalise the incoming value to a ``Result`` instance.
+    res = result(res)
+
+    # Short‑circuit on an existing error.
+    if not is_ok(res):
+        return res
+
+    # ``res`` is ``Ok`` – apply ``func`` safely.
+    try:
+        return result(func(res.value))
+    except Exception as exc:          # pragma: no cover – exercised by the test
+        # Convert the exception into an ``Err`` result.
+        return err(exc)
```

## How to verify
Create a small script (or unit test) that exercises the new behaviour:

```python
from pyresult import and_then, ok, err, is_err

def raise_exception(_):
    raise RuntimeError("boom")

# Should *not* raise; should return an Err result.
result = and_then(raise_exception, ok(1))

assert is_err(result), "and_then should return Err when the function raises"
assert isinstance(result.value, RuntimeError)
assert str(result.value) == "boom"
print("✅ and_then correctly converts exceptions to Err")
```

Running the script should finish without an uncaught exception and the
assertions should pass, confirming that `and_then` now returns an `Err`
instead of propagating the exception. Additionally, the existing test
suite should still pass.

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
--- REAL FIX (ground truth)
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
+            if key in self._TRANSFER_CONFIG_ALIASES:
+                if key in {"multipart_chunksize", "multipart_threshold"}:
+                    # cast human readable sizes (like 24MiB) to integers
+                    value = conversions.human_readable_to_bytes(value)
+                else:
+                    value = int(value)
+                transfer_config[self._TRANSFER_CONFIG_ALIASES[key]] = value
+            else:
+                config[key] = value
+
+        return config, transfer_config
+
+    def _process_config(self):
+        from boto3.s3.transfer import TransferConfig
+        from botocore.configloader import load_config
+
+        config_path = os.environ.get("AWS_CONFIG_FILE", _AWS_CONFIG_PATH)
+        if not os.path.exists(config_path):
+            return None
+
+        config = load_config(config_path)
+        profile = config["profiles"].get(self.profile or "default")
+        if not profile:
+            return None
+
+        s3_config = profile.get("s3", {})
+        s3_config, trans
--- Proposal A
## Root cause
`dvc` creates a **boto3** client for S3 and builds a `botocore.config.TransferConfig` only from the options that are explicitly defined in the DVC remote configuration (`dvc remote add …`).  
The AWS CLI configuration file (`~/.aws/config`) is read by **boto3** for credentials and basic client settings, but the values that belong to the `s3` section (e.g. `multipart_threshold`, `max_concurrent_requests`, …) are **not** forwarded to the `TransferConfig` nor to the client‑side `botocore.config.Config`.  
Consequently parameters such as `multipart_threshold = 500MB` are silently ignored while other parameters (e.g. `signature_version`) work because they are part of the regular client config.

## Fix
* Load the `s3` subsection of the AWS CLI config file for the profile that the remote uses.  
* Map the CLI‑style option names to the names expected by `TransferConfig` / `botocore.config.Config`.  
* Merge those values with the existing DVC remote options so that they are honoured during uploads/downloads.

```diff
--- a/dvc/remote/s3.py
+++ b/dvc/remote/s3.py
@@
 import logging
 from typing import Any, Dict, Optional
 
 import boto3
+from botocore.config import Config as BotocoreConfig
+from botocore.configloader import load_config as load_aws_config
 from botocore.exceptions import ClientError
 from boto3.s3.transfer import TransferConfig
@@
 from .base import RemoteBase
 from ..utils import parse_bytes, relpath
@@
 class S3FileSystem(RemoteBase):
@@
     def __init__(self, repo, config):
         super().__init__(repo, config)
         self.path_info = self._parse_path(config)
         self._session = None
         self._client = None
         self._transfer_config = None
@@
     def _make_client(self):
         """Create a boto3 S3 client with the appropriate configuration."""
         session = self.session
-        client_kwargs = self.fs_config.get("client_kwargs", {})
-        client = session.client("s3", **client_kwargs)
+        client_kwargs = self.fs_config.get("client_kwargs", {})
+
+        # ------------------------------------------------------------------
+        # 1) Load extra S3 options from the AWS CLI config file (if any)
+        # ------------------------------------------------------------------
+        aws_s3_cfg = self._load_aws_s3_config()
+
+        # Botocore client‑side configuration (addressing style, accelerate, …)
+        client_cfg_kwargs = {}
+        s3_client_cfg = {}
+        if "addressing_style" in aws_s3_cfg:
+            s3_client_cfg["addressing_style"] = aws_s3_cfg["addressing_style"]
+        if "use_accelerate_endpoint" in aws_s3_cfg:
+            s3_client_cfg["use_accelerate_endpoint"] = aws_s3_cfg[
+                "use_accelerate_endpoint"
+            ]
+        if "use_dualstack_endpoint" in aws_s3_cfg:
+            s3_client_cfg["use_dualstack_endpoint"] = aws_s3_cfg[
+                "use_dualstack_endpoint"
+            ]
+        if "payload_signing_enabled" in aws_s3_cfg:
+            s3_client_cfg["payload_signing_enabled"] = aws_s3_cfg[
+                "payload_signing_enabled"
+            ]
+        if s3_client_cfg:
+            client_cfg_kwargs["s3"] = s3_client_cfg
+
+        if client_cfg_kwargs:
+            client_kwargs["config"] = BotocoreConfig(**client_cfg_kwargs)
+
+        # ------------------------------------------------------------------
+        # 2) Create the client
+        # ------------------------------------------------------------------
+ 
--- Proposal B
## Root cause  

`dvc` builds a **boto3** `TransferConfig` for S3 remotes only from the options that are explicitly supported in its own ``dvc config`` (e.g. ``s3_endpointurl``, ``s3_region`` …).  
The AWS CLI configuration file ``~/.aws/config`` is read by **boto3**, but DVC never forwards the additional *S3 transfer* keys that the CLI supports (``max_concurrent_requests``, ``multipart_threshold`` …).  
Consequently those values are ignored – the CLI would honour them, DVC does not.

## Fix  

Add a mapping layer that extracts the supported keys from the AWS config (or from the DVC remote’s ``s3`` sub‑section) and passes them to the boto3 ``TransferConfig`` (and, where required, to the client ``botocore.config.Config``).  

The change touches the S3 remote implementation (`dvc/remote/s3.py`).  
The new code:

```diff
--- a/dvc/remote/s3.py
+++ b/dvc/remote/s3.py
@@
 from pathlib import Path
 from typing import Any, Dict, Optional
 
 import boto3
+from botocore.config import Config as BotocoreConfig
 from boto3.s3.transfer import TransferConfig
 from dvc.exceptions import DvcException
 from dvc.fs.s3 import S3FileSystem
 from dvc.utils import parse_bytes, parse_bool
@@
 class S3Remote(RemoteBase):
@@
     def __init__(self, repo, config):
         super().__init__(repo, config)
         self.path = config.get("url")
         self.profile = config.get("profile")
         self.endpoint_url = config.get("endpointurl")
         self.region = config.get("region")
         self.s3_extra_args = config.get("extra_args", {})
+        # -----------------------------------------------------------------
+        # New: read additional transfer‑related options from the AWS config
+        # (or from the DVC remote's ``s3`` sub‑section) and store them so
+        # they can be passed to boto3's TransferConfig / Botocore Config.
+        # -----------------------------------------------------------------
+        self._s3_transfer_opts = self._collect_s3_transfer_options()
 
         self.fs = S3FileSystem(
             self.path,
             self.profile,
             self.endpoint_url,
             self.region,
             self.s3_extra_args,
-            self._get_transfer_config(),
+            self._get_transfer_config(),
+            self._get_botocore_config(),
         )
 
-    def _get_transfer_config(self) -> TransferConfig:
-        # NOTE: DVC currently only supports ``multipart_threshold`` and
-        # ``multipart_chunksize`` via its own config.  All other options are
-        # taken from the default boto3 behaviour.
-        multipart_threshold = self.config.get("s3_multipart_threshold")
-        multipart_chunksize = self.config.get("s3_multipart_chunksize")
-
-        return TransferConfig(
-            multipart_threshold=multipart_threshold,
-            multipart_chunksize=multipart_chunksize,
-        )
+    # -----------------------------------------------------------------
+    # Helper: collect S3 transfer‑related options from the DVC config.
+    # The values are read from the ``s3`` sub‑section of the remote
+    # configuration (which mirrors the layout of ``~/.aws/config``).
+    # -----------------------------------------------------------------
+    def _collect_s3_transfer_options(self) -> Dict[str, Any]:
+        raw_opts: Dict[str, Any] = self.config.get("s3", {})
+        if not isinstance(raw_opts, dict):
+            return {}
+
+        # Normalise keys – boto3 uses ``max_concurrency`` and ``max_io_queue``.
+  

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
## Root cause
`Builder::count()` (and the generic `aggregate()` method) builds the aggregate query by **replacing the selected columns with the aggregate expression** while keeping the existing `HAVING` clauses.  
When a query contains a `withCount()` sub‑select, the alias created by `withCount` (e.g. `roles_count`) exists only in the original `SELECT` list. After the columns are replaced, the alias disappears, but the `HAVING` clause is still present, producing an invalid SQL like:

```sql
select count(*) as aggregate from `users` having `roles_count` > 2
```

The correct behaviour is to first apply the `HAVING` filter on the original query and then count the rows of that filtered result set – i.e. wrap the original query in a sub‑query before applying the aggregate.

## Fix
Add logic to the query builder’s `aggregate()` method to detect when a `HAVING` clause is present. In that case the builder clones the current query (without ordering/limit/offset), wraps it in a sub‑query via `fromSub()`, and runs the aggregate on the outer query. This preserves the alias needed by the `HAVING` clause.

```diff
--- a/src/Illuminate/Database/Query/Builder.php
+++ b/src/Illuminate/Database/Query/Builder.php
@@
     public function aggregate($function, $columns = [])
     {
         $this->aggregate = compact('function', 'columns');
 
-        $previousSelect = $this->columns;
-
-        if (empty($this->groups)) {
-            $this->orders = null;
-            $this->bindings['order'] = [];
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
+        /*
+         * If the query contains a HAVING clause we must first evaluate the
+         * HAVING on the original query and then apply the aggregate on the
+         * resulting rows.  Wrapping the original query in a sub‑query achieves
+         * this while keeping all bindings intact.
+         */
+        if (! empty($this->havings)) {
+            // Clone the current query without ordering/limit/offset – they are
+            // irrelevant for a simple row count.
+            $sub = $this->cloneWithout(['orders', 'limit', 'offset']);
+
+            // Build a new query that selects from the sub‑query.
+            $query = $this->newQuery()->fromSub($sub, 'laravel_temp');
+            $query->aggregate = $this->aggregate;
+
+            $results = $query->get($columns);
+
+            $this->aggregate = null;
+
+            return $results->isNotEmpty() ? $results[0]->aggregate : null;
+        }
+
+        $previousSelect = $this->columns;
+
+        if (empty($this->groups)) {
+            $this->orders = null;
+            $this->bindings['order'] = [];
+        }
+
+        $results = $this->get($columns);
+
+        $this->aggregate = null;
+
+        $this->columns = $previousSelect;
+
+        if (! $results->isEmpty()) {
+            return $results[0]->aggregate;
+        }
     }
```

The change lives in `Illuminate\Database\Query\Builder`, which is used by both the Eloquent and the base query builders, so the fix automatically applies to `count()`, `sum()`, `avg()`, etc., when a `HAVING` clause is present.

## How to verify
1. **Run the original failing example**

   ```php
   $cnt = User::query()
       ->withCount('roles')
       ->having('roles_count', '>', 2)
       ->count();   // should now return an in
--- Proposal B
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

=== TASK crossbeam-rs__crossbeam-999 | repo crossbeam-rs/crossbeam | rust
--- Issue
Use own unix::JoinHandleExt trait
The standard library's extension traits are not intended to be implemented for types other than the standard library.
Actually, there are discussions about sealing them: rust-lang/rust#81213, rust-lang/rust#80634
As long as we actually have a crate that depends on it, the standard library won't seal it, but it seems preferable to use our own extension trait anyway.

https://github.com/crossbeam-rs/crossbeam/blob/a95c02ea75d236a3560cd85075f71f8982bbf307/crossbeam-utils/src/thread.rs#L550


--- REAL FIX (ground truth)
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index 347c303..9b51b28 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -61,7 +61,7 @@ jobs:
           # Test 32-bit target that does not have AtomicU64/AtomicI64.
           - rust: nightly
             os: ubuntu-latest
-            target: mips-unknown-linux-gnu
+            target: armv5te-unknown-linux-gnueabi
     runs-on: ${{ matrix.os }}
     steps:
       - uses: actions/checkout@v3
diff --git a/crossbeam-deque/src/deque.rs b/crossbeam-deque/src/deque.rs
index 8afe15f..c37de2d 100644
--- a/crossbeam-deque/src/deque.rs
+++ b/crossbeam-deque/src/deque.rs
@@ -80,10 +80,7 @@ impl<T> Buffer<T> {
 
 impl<T> Clone for Buffer<T> {
     fn clone(&self) -> Buffer<T> {
-        Buffer {
-            ptr: self.ptr,
-            cap: self.cap,
-        }
+        *self
     }
 }
 
diff --git a/crossbeam-epoch/src/atomic.rs b/crossbeam-epoch/src/atomic.rs
index b33a8ac..9ea07eb 100644
--- a/crossbeam-epoch/src/atomic.rs
+++ b/crossbeam-epoch/src/atomic.rs
@@ -1169,10 +1169,7 @@ pub struct Shared<'g, T: 'g + ?Sized + Pointable> {
 
 impl<T: ?Sized + Pointable> Clone for Shared<'_, T> {
     fn clone(&self) -> Self {
-        Self {
-            data: self.data,
-            _marker: PhantomData,
-        }
+        *self
     }
 }
 
diff --git a/crossbeam-utils/src/thread.rs b/crossbeam-utils/src/thread.rs
index 2985790..71856eb 100644
--- a/crossbeam-utils/src/thread.rs
+++ b/crossbeam-utils/src/thread.rs
@@ -120,7 +120,6 @@ use std::sync::{Arc, Mutex};
 use std::thread;
 
 use crate::sync::WaitGroup;
-use cfg_if::cfg_if;
 
 type SharedVec<T> = Arc<Mutex<Vec<T>>>;
 type SharedOption<T> = Arc<Mutex<Option<T>>>;
@@ -548,37 +547,63 @@ impl<T> ScopedJoinHandle<'_, T> {
     }
 }
 
-cfg_if! {
-    if #[cfg(unix)] {
-        use std::os::unix::thread::{JoinHandleExt, RawPthread};
+/// Unix-specific extensions.
+#[cfg(unix)]
+pub mod unix {
+    use super::ScopedJoinHandle;
+    use std::os::unix::thread::JoinHandleExt as _;
 
-        impl<T> JoinHandleExt for ScopedJoinHandle<'_, T> {
-            fn as_pthread_t(&self) -> RawPthread {
-                // Borrow the handle. The handle will surely be available because the root scope waits
-                // for nested scopes before joining remaining threads.
-                let handle = self.handle.lock().unwrap();
-                handle.as_ref().unwrap().as_pthread_t()
-            }
-            fn into_pthread_t(self) -> RawPthread {
-                self.as_pthread_t()
-            }
+    #[doc(no_inline)]
+    pub use std::os::unix::thread::RawPthread;
+
+    mod sealed {
+        pub trait Sealed {}
+    }
+
+    /// Unix-specific extensions to [`ScopedJoinHandle`].
+    pub trait JoinHandleExt: sealed::Sealed {
+        /// Extracts the raw pthread_t without taking ownership
+        fn as_pthread_t(&self) -> RawPthread;
+
+        /// Consumes the thread, returning the raw pthread_t
+        ///
+        /// 
--- Proposal A
## Root cause
`crossbeam-utils` directly uses the standard library’s extension trait
`std::os::unix::thread::JoinHandleExt`.  
Implementing a foreign trait (`JoinHandleExt`) for a foreign type
(`std::thread::JoinHandle`) is discouraged and may become impossible once the
standard library seals the trait. The crate should provide its own extension
trait and forward the implementation to the std trait instead of depending on
the std‑provided one.

## Fix
* Introduce a private `unix` module that defines a `JoinHandleExt` trait mirroring
  the std trait.
* Implement this trait for `std::thread::JoinHandle<T>` by delegating to the
  std implementation.
* Replace all uses of `std::os::unix::thread::JoinHandleExt` with the new
  `crate::thread::unix::JoinHandleExt`.
* Export the associated `RawHandle` type so existing code continues to compile
  unchanged.

```diff
--- a/crossbeam-utils/src/thread.rs
+++ b/crossbeam-utils/src/thread.rs
@@
-use std::thread;
-use std::thread::JoinHandle;
+use std::thread;
+use std::thread::JoinHandle;
@@
-#[cfg(unix)]
-use std::os::unix::thread::JoinHandleExt;
+#[cfg(unix)]
+mod unix {
+    // Re‑export the raw handle type from the standard library so callers do not
+    // need to know the exact definition.
+    pub use std::os::unix::thread::RawHandle;
+
+    /// Extension trait for `std::thread::JoinHandle` on Unix platforms.
+    ///
+    /// This trait mirrors `std::os::unix::thread::JoinHandleExt` but lives inside
+    /// Crossbeam, avoiding the “foreign trait for foreign type” problem.
+    pub trait JoinHandleExt {
+        /// Consumes the `JoinHandle`, returning the underlying OS thread handle.
+        fn into_raw_handle(self) -> RawHandle;
+    }
+
+    impl<T> JoinHandleExt for super::JoinHandle<T> {
+        fn into_raw_handle(self) -> RawHandle {
+            // Forward to the standard library implementation.
+            std::os::unix::thread::JoinHandleExt::into_raw_handle(self)
+        }
+    }
+}
+
+#[cfg(unix)]
+use unix::JoinHandleExt;
@@
-    #[cfg(unix)]
-    let raw = handle.into_raw_handle();
+    #[cfg(unix)]
+    let raw = handle.into_raw_handle(); // uses our own `JoinHandleExt`
```

*Explanation of changes*  

1. **`unix` module** – Declares a local `JoinHandleExt` trait with the same API as
   the std one and re‑exports the `RawHandle` type.
2. **Implementation** – Provides a blanket implementation for any
   `std::thread::JoinHandle<T>` that simply forwards to the std trait.
3. **Import** – The file now imports `unix::JoinHandleExt` instead of the std
   trait, so all subsequent calls (`handle.into_raw_handle()`) resolve to the
   local trait.
4. **No other files need changes** because the trait name and method signature
   remain identical; only the source of the trait has moved.

## How to verify
1. **Build on Unix**  
   ```bash
   cargo build --features=std
   ```
   The build must succeed without warnings about implementing a foreign trait
   for a foreign type.

2. **Run the test suite on a Unix platform**  
   ```bash
   cargo test
   ```
   All existing tests should pass, confirming that the behaviour of
   `into_raw_handle` is unchanged.

3. **Check that the trait is the one from Crossbeam**  
   Add a small snippet (or use `cargo expand`) to ensure that the resolved
   trait for `handle.into_raw_handle()` is `crossbeam_utils::thread::unix::JoinHandleExt`.

4. **Future‑proofing** – Verify that the code still compiles on the latest
   stable Rust after the standard library
--- Proposal B
**## Root cause**

`crossbeam-utils` implements the standard library’s extension trait `std::os::unix::thread::JoinHandleExt` for its own `JoinHandle` type.  
Extension traits in `std` are meant to be implemented **only** for types defined in the standard library. Implementing them for external types is discouraged and may be sealed in the future, causing a breaking change. The fix is to provide a *local* extension trait (e.g., `crossbeam_utils::thread::unix::JoinHandleExt`) and implement that instead.

**## Fix**

```diff
--- a/crossbeam-utils/src/thread.rs
+++ b/crossbeam-utils/src/thread.rs
@@
 use std::thread;
 use std::thread::JoinHandle as StdJoinHandle;
@@
 pub struct JoinHandle<T> {
     inner: StdJoinHandle<T>,
 }
 
@@
 impl<T> JoinHandle<T> {
     /// Joins the associated thread.
     #[inline]
     pub fn join(self) -> thread::Result<T> {
         self.inner.join()
     }
@@
 }
 
-// -------------------------------------------------------------------------
-// Unix-specific extensions
-
-#[cfg(unix)]
-impl std::os::unix::thread::JoinHandleExt for JoinHandle<()> {
-    #[inline]
-    fn into_raw_handle(self) -> std::os::unix::thread::RawHandle {
-        // SAFETY: `self.inner` is a `std::thread::JoinHandle<()>` which
-        // implements the same trait, so delegating is safe.
-        std::os::unix::thread::JoinHandleExt::into_raw_handle(self.inner)
-    }
-}
-
-// -------------------------------------------------------------------------
-// Windows-specific extensions
-
-#[cfg(windows)]
-impl std::os::windows::thread::JoinHandleExt for JoinHandle<()> {
-    #[inline]
-    fn into_raw_handle(self) -> std::os::windows::thread::RawHandle {
-        // SAFETY: same reasoning as the Unix impl.
-        std::os::windows::thread::JoinHandleExt::into_raw_handle(self.inner)
-    }
-}
-
-// -------------------------------------------------------------------------
-// Documentation
-
-#[cfg(test)]
-mod tests {
-    // … existing tests …
-}
+// -------------------------------------------------------------------------
+// Unix‑specific extensions (own trait)
+
+/// Unix‑specific extensions for `crossbeam_utils::thread::JoinHandle`.
+///
+/// This trait mirrors the API of `std::os::unix::thread::JoinHandleExt` but is
+/// defined inside Crossbeam so that it can be implemented for Crossbeam’s own
+/// `JoinHandle` type without violating the “standard library extension traits
+/// are not intended to be implemented for non‑standard types” rule.
+#[cfg(unix)]
+pub mod unix {
+    use super::JoinHandle;
+    use std::os::unix::thread::{JoinHandleExt as StdJoinHandleExt, RawHandle};
+
+    /// Extension methods for `JoinHandle` on Unix platforms.
+    ///
+    /// The only method currently required by Crossbeam is `into_raw_handle`,
+    /// which yields the underlying OS thread handle.
+    pub trait JoinHandleExt {
+        /// Consumes the `JoinHandle`, returning the raw OS handle.
+        ///
+        /// This is equivalent to `std::os::unix::thread::JoinHandleExt::into_raw_handle`.
+        fn into_raw_handle(self) -> RawHandle;
+    }
+
+    impl JoinHandleExt for JoinHandle<()> {
+        #[inline]
+        fn into_raw_handle(self) -> RawHandle {
+            // Delegate to the standard library implementation for the inner
+            // `std::thread::JoinHandle`. This is safe because the inner handle
+            // implements the same trait.
+            StdJoinHandleExt::into_raw_handle(self.inner)
+        }
+    }
+}
+
+// ----

=== TASK mantoni__eslint_d.js-323 | repo mantoni/eslint_d.js | js
--- Issue
Debug mode
I sometimes have issues with eslint_d. To understand what is happening, I'm adding some log instructions to the code. It would be nice if eslint_d had a debug mode builtin. This can be done by writing log to stderr if that doesn't conflict with the client/daemon communication. Otherwise, this can be done by writing log to a temporary file.
--- REAL FIX (ground truth)
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
-      await launchDaemon(resolver, hash);
+      await launchDaemon(resolver, hash, is_debug_mode);
       return;
     case 'status':
       (await import('../lib/status.js')).status(resolver, config);
@@ -65,7 +73,7 @@ const command = process.argv[2];
         config = null;
       }
       if (!config) {
-        config = await launchDaemon(resolver, hash);
+        config = await launchDaemon(resolver, hash, false);
         if (!config) {
           return;
         }
diff --git a/lib/config.js b/lib/config.js
index 0e1528d..c0b8328 100644
--- a/lib/config.js
+++ b/lib/config.js
@@ -1,5 +1,8 @@
+import debug from 'debug';
 import fs from 'node:fs/promises';
 
+const log = debug('eslint_d:config');
+
 /**
  * @import { Resolver} from './resolver.js'
  */
@@ -18,6 +21,8 @@ import fs from 'node:fs/promises';
  */
 export async function loadConfig(resolver) {
   const filename = configFile(resolver);
+  log('Reading config from %s', filename);
+
   try {
     let raw = await fs.readFile(filename,
--- Proposal A
## Root cause
`eslint_d` has no built‑in way to emit internal diagnostics.  
When something goes wrong it is hard for a user to know *why* the daemon
failed to start, why a request was dropped, etc.  Adding a “debug mode”
that writes diagnostic information to a safe place (a temporary file) gives
users a way to investigate without breaking the daemon’s stdin/stdout
protocol.

## Fix
Add a very small debug logger that is enabled when the environment variable
`ESLINT_D_DEBUG` is set (any non‑empty value).  The logger writes timestamped
messages to a temporary file (`$TMPDIR/eslint_d_debug.log` by default, or a
path supplied via `ESLINT_D_DEBUG_LOG`).  The logger is used in the daemon
process and in the client wrapper to trace the most important events.

```diff
--- a/lib/daemon.js
+++ b/lib/daemon.js
@@
 const fs = require('fs');
 const path = require('path');
+const os = require('os');
 const { spawn } = require('child_process');
 const { createInterface } = require('readline');
 
+// ---------------------------------------------------------------------------
+// Debug logger
+// ---------------------------------------------------------------------------
+// Enable with `ESLINT_D_DEBUG=1`.  The log is written to a temporary file so
+// that it does not interfere with the daemon’s stdin/stdout protocol.
+// Users can also point it at a custom location with `ESLINT_D_DEBUG_LOG`.
+const DEBUG_ENABLED = !!process.env.ESLINT_D_DEBUG;
+const DEBUG_LOG_PATH = process.env.ESLINT_D_DEBUG_LOG ||
+  path.join(os.tmpdir(), 'eslint_d_debug.log');
+
+function debugLog(message) {
+  if (!DEBUG_ENABLED) return;
+  try {
+    const line = `${new Date().toISOString()} ${message}\n`;
+    fs.appendFileSync(DEBUG_LOG_PATH, line, { encoding: 'utf8' });
+  } catch (_) {
+    // If logging fails we deliberately ignore it – we must never crash the
+    // daemon because of a logging error.
+  }
+}
+
 // ---------------------------------------------------------------------------
 // Daemon implementation
 // ---------------------------------------------------------------------------
 
@@
 function startDaemon() {
-  // TODO: spawn the real eslint daemon process
+  debugLog('Starting eslint daemon process');
   const child = spawn(process.execPath, [daemonPath], {
     stdio: ['pipe', 'pipe', 'pipe'],
   });
 
+  debugLog(`Daemon PID: ${child.pid}`);
+
   child.on('error', (err) => {
-    // TODO: handle spawn errors
+    debugLog(`Daemon spawn error: ${err.message}`);
     process.exit(1);
   });
 
   child.stderr.on('data', (data) => {
-    // TODO: forward daemon stderr?
+    debugLog(`Daemon stderr: ${data.toString().trim()}`);
   });
 
   // Forward messages from the daemon to the client
   const rl = createInterface({ input: child.stdout });
   rl.on('line', (line) => {
-    // TODO: parse and forward messages
+    debugLog(`Daemon stdout line: ${line}`);
     try {
       const msg = JSON.parse(line);
       // Forward the response to the client (stdout)
@@
   child.on('exit', (code, signal) => {
-    // TODO: cleanup
+    debugLog(`Daemon exited with code=${code} signal=${signal}`);
     process.exit(code ?? 0);
   });
 
   return child;
 }
 
@@
 function handleClientMessage(msg) {
   debugLog(`Received client message: ${JSON.stringify(msg)}`);
   // Forward the request to the daemon
   if (!daemon) {
-    // TODO: start daemon if not running
+    debugLog('Daemon not running – starting now');
     daemon = startDaemon();
   }
   daemon.stdin.write(JSON.s
--- Proposal B
## Root cause
`eslint_d` has no way to emit internal diagnostic information.  
When something goes wrong (e.g. the daemon crashes, messages are malformed, or the
client cannot talk to the daemon) the user has no visibility into what the
process is doing.  Adding a *debug mode* that writes log messages to **stderr**
(or a temporary file) solves this without interfering with the JSON protocol
that uses **stdout** for client‑daemon communication.

## Fix
```diff
--- a/bin/eslint_d.js
+++ b/bin/eslint_d.js
@@
-#!/usr/bin/env node
+#!/usr/bin/env node
 /*
  * eslint-d – a wrapper that runs eslint as a daemon.
  */
 
+// ---------------------------------------------------------------------------
+// Debug support
+// ---------------------------------------------------------------------------
+// The daemon and the client can emit diagnostic information when the user
+// requests it via the `--debug` CLI flag or the `ESLINT_D_DEBUG` environment
+// variable.  All debug output is written to `process.stderr` so that it does
+// not interfere with the JSON protocol that uses `process.stdout`.
+//
+// The implementation lives in `lib/debug.js`.  Here we only need to enable it
+// early, before any other module is required.
+const debug = require('../lib/debug');
+if (process.argv.includes('--debug') || process.env.ESLINT_D_DEBUG) {
+  debug.enable();
+}
+
 const path = require('path');
 const fs = require('fs');
 const child_process = require('child_process');
 const net = require('net');
 const os = require('os');
@@
-// Parse command‑line arguments (everything after `--` is passed to eslint)
-const args = process.argv.slice(2);
+// Parse command‑line arguments (everything after `--` is passed to eslint)
+// The `--debug` flag is removed so that it is not forwarded to the real eslint.
+let rawArgs = process.argv.slice(2);
+if (rawArgs.includes('--debug')) {
+  rawArgs = rawArgs.filter(arg => arg !== '--debug');
+}
+const args = rawArgs;
 
 // ---------------------------------------------------------------------------
 // Client implementation (talks to the daemon)
 // ---------------------------------------------------------------------------
@@
   const client = net.createConnection({ path: socketPath }, () => {
-    // Send the request to the daemon
+    debug.log('client: connected to daemon socket', socketPath);
+    // Send the request to the daemon
     client.write(JSON.stringify(request));
   });
 
   client.on('data', data => {
-    // Forward daemon output to stdout
+    debug.log('client: received data from daemon');
+    // Forward daemon output to stdout
     process.stdout.write(data);
   });
 
   client.on('error', err => {
-    // If we cannot connect, fall back to a direct eslint run
+    debug.log('client: connection error – falling back to direct eslint', err);
+    // If we cannot connect, fall back to a direct eslint run
     fallback();
   });
 
   client.on('close', () => {
-    // Daemon closed the connection – nothing to do
+    debug.log('client: connection closed');
+    // Daemon closed the connection – nothing to do
   });
 };
 
 // ---------------------------------------------------------------------------
 // Daemon implementation (runs as a background process)
 // ---------------------------------------------------------------------------
@@
   const server = net.createServer(socket => {
+    debug.log('daemon: new client connection');
 
     let buffer = '';
 
     socket.on('data', data => {
+      debug.log('dae

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
The completion script builds its list of possible commands by running `just --summary` and using the recipe names exactly as they appear in the summary output.  
When a recipe lives inside a module, `just --summary` prints it as `module::recipe` (e.g., `foo::bar`).  
However, Just does **not** accept the `module::recipe` syntax for execution – a module recipe must be invoked as two separate arguments (`just foo bar`).  
Because the script forwards the raw `module::recipe` strings to the shell completion engine, the generated completions are invalid and result in “Justfile does not contain recipe `foo::bar`”.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/scripts/completions.bash
+++ b/scripts/completions.bash
@@
 # Generate completions for Just recipes.
 # This script is invoked by the shell completion function.
 #
 # It calls `just --summary` to obtain the list of recipes and prints them,
 # one per line, for the completion engine to consume.
 #
 # NOTE: Recipes that belong to a module are displayed by `just --summary`
 # as `module::recipe`.  They must be invoked as `just module recipe`,
 # i.e. the `::` separator is **not** part of the command line syntax.
 # Previously the script passed the raw `module::recipe` strings to the
 # completion engine, which made completions for module recipes unusable.
 # The fix below normalises the names by replacing `::` with a space.
 
 just --summary "$@" |
-    awk '/^[[:space:]]*[^#]/ {print $1}'
+    awk '/^[[:space:]]*[^#]/ {
+        # $1 contains the recipe name as printed by `just --summary`.
+        # If the name contains a module separator `::`, replace it with a space
+        # so that the completion suggestion matches the actual invocation syntax.
+        name = $1
+        gsub(/::/, " ", name)
+        print name
+    }'
```

### Explanation of changes
1. **Added a comment** explaining the discrepancy between the summary output and the actual command‑line syntax for module recipes.
2. **Modified the `awk` pipeline**:
   * Previously it simply printed the first field (`$1`) from each non‑comment line.
   * The new version stores `$1` in a variable `name`, runs `gsub(/::/, " ", name)` to replace every occurrence of `::` with a single space, and then prints the transformed name.
   * This turns `foo::bar` → `foo bar`, which is the correct way to invoke the recipe.

The rest of the script (argument handling, error checking, etc.) remains unchanged.

## How to verify
1. **Create a Justfile with a module recipe**  
   ```make
   # Justfile
   module foo {
       bar:
           @echo "inside foo::bar"
   }
   ```
2. **Run the summary manually** to see the raw output:  
   ```sh
   $ just --summary
   foo::bar
   ```
3. **Run the completion script** (the one we just patched) and ensure it outputs the normalized name:  
   ```sh
   $ scripts/completions.bash
   foo bar
   ```
   The output should **not** contain `foo::bar`.

4. **Test the actual command** to confirm it works:  
   ```sh
   $ just foo bar
   inside foo::bar
   ```

5. **Test shell completion** (e.g., Bash) after sourcing the completion script:  
   ```sh
   $ source scripts/completions.bash   # or the appropriate install step
   $ just <TAB><TAB>
   ```
   The completion list should include `foo bar` (as two separate words) and invoking it via completion should succeed without the “Justfile does not contain recipe `foo::bar`” error.

6. **Run the project's test suite** (if it i
--- Proposal B
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

=== TASK sdv-dev__rdt-942 | repo sdv-dev/RDT | python
--- Issue
Allow me to opt out of replacing missing values (`missing_value_replacement=None`)
### Problem Description
A number of the RDT transformers have a `missing_value_replacement` parameter that describes how to replace missing values in the forward transform. There are a variety of strategies that include replacement with a random value (`'random'`), replacing with the mean of the data (`'mean'`), etc. This was done under the assumption that the downstream software does not accept missing values.

However, I may have some use cases where my downstream software _does_ handle missing values. In this case, I do not want the RDTs to do anything to the missing values. In such cases, we should offer `missing_value_replacement=None` as an option.

_Note that we used to have this option. Just that it is listed as deprecated for all the transformers. We should un-deprecated it because there are valid use cases that need this._

### Expected behavior
Reinstate the `None` option for the `missing_value_replacement` parameter, wherever it exists. If this is passed in, then during the **forward transform**: do not replace the missing values with anything. Just pass the missing values along. 

- Note that the default value for `missing_value_replacement` should not change; in most cases, it is `'random'`. The user would need to explicitly pass in `missing_value_replacement=None` to access this new functionality.

For the **reverse transform** things can be a bit trickier. Ideally the `missing_v
--- REAL FIX (ground truth)
diff --git a/rdt/transformers/base.py b/rdt/transformers/base.py
index 84c44b8..808e32a 100644
--- a/rdt/transformers/base.py
+++ b/rdt/transformers/base.py
@@ -142,17 +142,6 @@ class BaseTransformer:
         elif model_missing_values is False:
             self._set_missing_value_generation('random')
 
-    def _set_missing_value_replacement(self, default, missing_value_replacement):
-        if missing_value_replacement is None:
-            warnings.warn(
-                "Setting 'missing_value_replacement' to 'None' is no longer supported. "
-                f"Imputing with the '{default}' instead.",
-                FutureWarning,
-            )
-            self.missing_value_replacement = default
-        else:
-            self.missing_value_replacement = missing_value_replacement
-
     @classmethod
     def get_name(cls):
         """Return transformer name.
@@ -478,7 +467,18 @@ class BaseTransformer:
 
         data = data.copy()
         columns_data = self._get_columns_data(data, self.output_columns)
+        original_missing_values = self.missing_value_generation
+        if self.missing_value_generation is not None and pd.isna(columns_data).any().any():
+            warnings.warn(
+                "The 'missing_value_generation' parameter is set to '"
+                f"{self.missing_value_generation}' but the data already contains missing values."
+                ' Missing value generation will be skipped.',
+                UserWarning,
+            )
+            self.missing_value_generation = None
+
         reversed_data = self._reverse_transform(columns_data)
+        self.missing_value_generation = original_missing_values
         data = data.drop(self.output_columns, axis=1)
         data = self._add_columns_to_data(data, reversed_data, self.columns)
 
diff --git a/rdt/transformers/boolean.py b/rdt/transformers/boolean.py
index 8c8711b..a1eb0e9 100644
--- a/rdt/transformers/boolean.py
+++ b/rdt/transformers/boolean.py
@@ -46,8 +46,8 @@ class BinaryEncoder(BaseTransformer):
         missing_value_generation='random',
     ):
         super().__init__()
+        self.missing_value_replacement = missing_value_replacement
         self._set_missing_value_generation(missing_value_generation)
-        self._set_missing_value_replacement('random', missing_value_replacement)
         if model_missing_values is not None:
             self._set_model_missing_values(model_missing_values)
 
diff --git a/rdt/transformers/datetime.py b/rdt/transformers/datetime.py
index 5b8ef9b..e188938 100644
--- a/rdt/transformers/datetime.py
+++ b/rdt/transformers/datetime.py
@@ -60,7 +60,7 @@ class UnixTimestampEncoder(BaseTransformer):
         enforce_min_max_values=False,
     ):
         super().__init__()
-        self._set_missing_value_replacement('mean', missing_value_replacement)
+        self.missing_value_replacement = missing_value_replacement
         self._set_missing_value_generation(missing_value_generation)
         self.enforce_
--- Proposal A
## Root cause
`missing_value_replacement` is forced to a non‑`None` value in the base transformer implementation.  
When the parameter is set to `None` the code still tries to replace missing values, which raises an error or replaces the values unintentionally.  
The same logic is applied to `missing_value_generation` in the reverse direction, causing unwanted generation when the input already contains `NaN`s.

## Fix
```diff
--- a/rdt/transformers/base.py
+++ b/rdt/transformers/base.py
@@
-from typing import Any, Callable, Dict, List, Optional, Tuple, Union
+from typing import Any, Callable, Dict, List, Optional, Tuple, Union
+import warnings
 import numpy as np
 
 
 class BaseTransformer:
@@
-    def __init__(
-        self,
-        *,
-        missing_value_replacement: Union[str, float] = "random",
-        missing_value_generation: Optional[str] = "from_column",
-        **kwargs,
-    ):
-        """
-        Base class for all transformers.
-
-        Parameters
-        ----------
-        missing_value_replacement: Union[str, float], default ``\"random\"``
-            Strategy used to replace missing values during the forward transform.
-
-        missing_value_generation: Optional[str], default ``\"from_column\"``
-            Strategy used to generate missing values during the reverse transform.
-        """
-        self.missing_value_replacement = missing_value_replacement
-        self.missing_value_generation = missing_value_generation
-        super().__init__(**kwargs)
+    def __init__(
+        self,
+        *,
+        missing_value_replacement: Union[str, float, None] = "random",
+        missing_value_generation: Optional[str] = "from_column",
+        **kwargs,
+    ):
+        """
+        Base class for all transformers.
+
+        Parameters
+        ----------
+        missing_value_replacement: Union[str, float, None], default ``\"random\"``
+            Strategy used to replace missing values during the forward transform.
+            If ``None`` is supplied, missing values are left untouched.
+
+        missing_value_generation: Optional[str], default ``\"from_column\"``
+            Strategy used to generate missing values during the reverse transform.
+            If ``None`` is supplied, no generation is performed.
+        """
+        # ``None`` means *do not touch* missing values.
+        self.missing_value_replacement = missing_value_replacement
+        self.missing_value_generation = missing_value_generation
+        super().__init__(**kwargs)
@@
-    def forward(self, data: pd.DataFrame) -> pd.DataFrame:
-        \"\"\"Apply the forward transformation to ``data``.\"
-
-        transformed = self._forward(data)
-        # Replace missing values according to the chosen strategy.
-        return self._replace_missing(transformed, self.missing_value_replacement)
+    def forward(self, data: pd.DataFrame) -> pd.DataFrame:
+        \"\"\"Apply the forward transformation to ``data``.\"
+
+        transformed = self._forward(data)
+
+        # ------------------------------------------------------------------
+        # Missing‑value replacement
+        # ------------------------------------------------------------------
+        if self.missing_value_replacement is None:
+            # User explicitly asked to keep missing values as‑is.
+            return transformed
+        return self._replace_missing(transformed, self.missing_value_replacement)
@@
-    def reverse(self, data: pd.DataFrame) -> p
--- Proposal B
## Root cause
`RDT` transformers inherit the handling of missing values from a common base class.  
The base class validates the `missing_value_replacement` argument against a fixed list
(`'random'`, `'mean'`, …) and, during the **forward** transform, always calls a helper
that replaces `NaN`/`None` values according to that strategy.  
Because `None` was deprecated, the validation now rejects it and the replacement
logic is always executed, even when the user explicitly wants to keep the original
missing values.

The **reverse** transform has a similar hard‑wired behaviour: if a
`missing_value_generation` strategy is set it is applied unconditionally, which
produces wrong results when the input already contains missing values.

## Fix
* Re‑introduce `None` as a valid value for `missing_value_replacement`.
* In the forward transform, skip the replacement step when the parameter is `None`.
* In the reverse transform, when `missing_value_generation` is not `None` **and**
  the incoming data already contains missing values, emit a warning and skip the
  generation step.
* Centralise the logic in the base transformer so all concrete transformers
  (`ClusterBasedNormalizer`, `FloatFormatter`, `GaussianNormalizer`,
  `XGaussianNormalizer`, `BinaryEncoder`, `OptimizedTimestampEncoder`,
  `UnixTimestampEncoder`) automatically inherit the new behaviour.

Below is a best‑guess implementation based on the current project layout.

```diff
--- a/rdt/transformers/base.py
+++ b/rdt/transformers/base.py
@@
 class BaseTransformer:
@@
-    def __init__(
-        self,
-        *,
-        missing_value_replacement: str = "random",
-        missing_value_generation: Optional[str] = None,
-        **kwargs,
-    ):
-        """
-        Parameters
-        ----------
-        missing_value_replacement: str, default "random"
-            Strategy used to replace missing values during forward transform.
-        missing_value_generation: str or None, default None
-            Strategy used to generate missing values during reverse transform.
-        """
-        allowed_replacements = {"random", "mean", "median", "mode"}
-        if missing_value_replacement not in allowed_replacements:
-            raise ValueError(
-                f"missing_value_replacement must be one of {allowed_replacements}"
-            )
-
-        self.missing_value_replacement = missing_value_replacement
-        self.missing_value_generation = missing_value_generation
-        super().__init__(**kwargs)
+    def __init__(
+        self,
+        *,
+        missing_value_replacement: Optional[Union[str, None]] = "random",
+        missing_value_generation: Optional[str] = None,
+        **kwargs,
+    ):
+        """
+        Parameters
+        ----------
+        missing_value_replacement: str or None, default "random"
+            Strategy used to replace missing values during forward transform.
+            If ``None`` the transformer will **not** modify missing values.
+        missing_value_generation: str or None, default None
+            Strategy used to generate missing values during reverse transform.
+        """
+        # ``None`` used to be a valid (deprecated) option – re‑enable it.
+        allowed_replacements = {"random", "mean", "median", "mode", None}
+        if missing_value_replacement not in allowed_replacements:
+            raise ValueError(
+                f"missing_value_replacement must be one of {allowed_replacements}"
+            )
+
+        se

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
**## Root cause**

`Util.generate_hpp_sig()` builds the signing string from **all** keys that are present in the supplied dictionary.  
When a webhook notification is processed the dictionary that is passed to the function is the whole
`additionalData` payload, which already contains the field `hmacSignature`.  
Including this field in the signing string makes the calculated HMAC different from the one sent by
Adyen, so `generate_hpp_sig()` (and consequently `is_valid_hmac`) never match the signature that
Adyen provides.

**## Fix (unified diff)**

```diff
--- a/adyen/util.py
+++ b/adyen/util.py
@@
-def generate_hpp_sig(params, hmac_key):
-    """
-    Generate the HPP signature for a set of parameters.
-
-    :param dict params: Dictionary with the parameters that will be sent to HPP.
-    :param str hmac_key: HMAC key (base64 encoded) from the Adyen Customer Area.
-    :return: The generated HMAC signature (base64 encoded).
-    :rtype: str
-    """
-    # Sort the parameters alphabetically and concatenate the values with a colon.
-    signing_data = ':'.join(str(params[k]) for k in sorted(params) if params[k] is not None)
-
-    # Decode the HMAC key from base64.
-    decoded_key = base64.b64decode(hmac_key)
-
-    # Calculate the HMAC SHA‑256 signature.
-    signature = hmac.new(decoded_key, signing_data.encode('utf-8'), hashlib.sha256).digest()
-
-    # Return the signature encoded in base64.
-    return base64.b64encode(signature).decode()
+def generate_hpp_sig(params, hmac_key):
+    """
+    Generate the HPP signature for a set of parameters.
+
+    The Adyen HPP specification requires that **only** the parameters that will be sent back
+    to the HPP are used for the signature calculation.  The ``additionalData`` payload that
+    arrives in a webhook also contains the field ``hmacSignature`` – this field must **not**
+    be part of the signing string, otherwise the calculated signature will never match the
+    one supplied by Adyen.
+
+    This implementation therefore:
+
+    1. Removes the ``hmacSignature`` key (if present) before building the signing string.
+    2. Sorts the remaining keys alphabetically.
+    3. Concatenates the *string* representation of each non‑``None`` value with a colon
+       separator (no trailing colon).
+    4. Calculates an HMAC‑SHA256 using the base‑64 decoded secret key.
+    5. Returns the result base‑64 encoded.
+
+    :param dict params: Dictionary with the parameters that will be sent to HPP.
+    :param str hmac_key: HMAC key (base64 encoded) from the Adyen Customer Area.
+    :return: The generated HMAC signature (base64 encoded).
+    :rtype: str
+    """
+    # ``hmacSignature`` must be excluded from the signing data – it is the value we are trying
+    # to reproduce.  ``merchantSig`` (used for the classic API) is also ignored for the same
+    # reason, keeping the function safe for both use‑cases.
+    excluded_keys = {'hmacSignature', 'merchantSig'}
+
+    # Build the signing string from the remaining parameters.
+    signing_parts = []
+    for key in sorted(params):
+        if key in excluded_keys:
+            continue
+        value = params[key]
+        if value is None:
+            continue
+        signing_parts.append(str(value))
+
+    signing_data = ':'.join(signing_parts)
+
+    # Decode the HMAC key from base64 (Adyen provides it in that format).
+    decoded_key = base64.b64decode(hmac_key)
+
+    # Calculate the HMAC SHA‑256 signature.
+    signature = hmac.new
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
--- Proposal B
**## Root cause**

`autode.values.Value` (and its subclasses such as `PotentialEnergy`) overload the arithmetic operators.  
The implementations of `__mul__` and `__truediv__` simply performed the operation on the underlying
numeric value **and then wrapped the result back into the same subclass**, keeping the original
unit unchanged. Consequently:

```python
e * e          # → Energy(1.0 Ha)   (should be Ha² or a plain number)
e / e          # → Energy(1.0 Ha)   (should be dimension‑less)
```

The code never combined the units of the two operands, nor did it recognise that the result of a
multiplication/division of two `Value` objects may no longer belong to the original physical
quantity.

**## Fix (unified diff)**

```diff
--- a/autode/values.py
+++ b/autode/values.py
@@
 class Value:
@@
     def __add__(self, other):
         if isinstance(other, Value):
             self._check_compatible_units(other)
             return self.__class__(self._value + other._value, self._units)
         else:
             return self.__class__(self._value + other, self._units)
@@
-    def __mul__(self, other):
-        """Multiplication – keep the same unit (bug)."""
-        if isinstance(other, Value):
-            # BUG: returns same subclass with unchanged units
-            return self.__class__(self._value * other._value, self._units)
-        else:
-            return self.__class__(self._value * other, self._units)
+    def __mul__(self, other):
+        """
+        Multiplication.
+
+        * If ``other`` is another :class:`Value` we **do not** know the appropriate
+          physical quantity for the product (e.g. ``Energy * Energy`` would have
+          units ``Ha**2``).  Returning a ``Value`` with the original units is
+          misleading, therefore we return a plain ``float`` representing the
+          numerical product.
+        * If ``other`` is a scalar (int/float) we keep the original unit and
+          return an instance of the same subclass.
+        """
+        if isinstance(other, Value):
+            # Return a dimension‑less float – the caller can decide how to handle
+            # the resulting units (e.g. wrap it in a generic ``Value`` if needed).
+            return float(self._value) * float(other._value)
+        else:
+            return self.__class__(self._value * other, self._units)
@@
-    def __truediv__(self, other):
-        """Division – keep the same unit (bug)."""
-        if isinstance(other, Value):
-            # BUG: returns same subclass with unchanged units
-            return self.__class__(self._value / other._value, self._units)
-        else:
-            return self.__class__(self._value / other, self._units)
+    def __truediv__(self, other):
+        """
+        True division.
+
+        * When dividing by another :class:`Value` the resulting quantity is
+          generally dimension‑less (e.g. ``Energy / Energy``) or has different
+          dimensions (e.g. ``Length / Time``).  As with multiplication we return a
+          plain ``float`` to avoid attaching the wrong unit to the original
+          subclass.
+        * Division by a scalar keeps the original unit.
+        """
+        if isinstance(other, Value):
+            return float(self._value) / float(other._value)
+        else:
+            return self.__class__(self._value / other, self._units)
@@
-    def __rmul__(self, other):
-        return self.__mul__(other)
+    def __rmul__(self, other):
+        # ``other * self`` s

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
`dvc exp show` only accepted a single `--rev` (or a list of revisions) **and** a single global `-n/--num` that limited the number of commits for **all** revisions. The CLI parser therefore could not express “different limits per revision”, forcing the VS Code extension to invoke the command once per branch. The underlying implementation simply applied the same `n` value to every revision it iterated over.

## Fix
Add a new `--num` option that can be supplied multiple times, one value per `--rev`.  When the user provides a single `-n` we keep the historic behaviour (the same limit for every revision).  When multiple `-n` values are supplied we zip them with the `--rev` values, validating that the counts match.  The command implementation is updated to iterate over the `(rev, num)` pairs and call the existing logic for each pair.

```diff
--- a/dvc/command/exp.py
+++ b/dvc/command/exp.py
@@
-@click.command()
-@click.option(
-    "-r",
-    "--rev",
-    "rev",
-    multiple=True,
-    help="Git revision(s) to show.",
-)
-@click.option(
-    "-n",
-    "--num",
-    "num",
-    type=int,
-    default=None,
-    help="Number of commits to show.",
-)
+@click.command()
+@click.option(
+    "-r",
+    "--rev",
+    "revs",
+    multiple=True,
+    help="Git revision(s) to show.  Can be supplied multiple times.",
+)
+@click.option(
+    "-n",
+    "--num",
+    "nums",
+    type=int,
+    multiple=True,
+    default=None,
+    help=(
+        "Number of commits to show.  When supplied once it applies to all "
+        "revisions; when supplied multiple times it must match the number of "
+        "``--rev`` arguments (e.g. ``--rev main -n 5 --rev other -n 3``)."
+    ),
+)
 def exp_show(
-    rev: Tuple[str, ...],
-    num: Optional[int],
+    revs: Tuple[str, ...],
+    nums: Tuple[int, ...],
     **kwargs,
 ):
@@
-    if not rev:
-        rev = (None,)
-
-    for r in rev:
-        _exp_show(r, num, **kwargs)
+    # --------------------------------------------------------------------- #
+    # Normalise arguments -------------------------------------------------- #
+    # --------------------------------------------------------------------- #
+    # ``revs`` may be empty (no ``--rev`` supplied) → show the current workspace.
+    if not revs:
+        revs = (None,)
+
+    # ``nums`` handling:
+    #   * No ``-n`` supplied → ``None`` for every rev (no limit).
+    #   * One ``-n`` supplied → apply the same limit to all revs (back‑compat).
+    #   * Multiple ``-n`` supplied → must match the number of revs.
+    if not nums:
+        nums = (None,) * len(revs)
+    elif len(nums) == 1:
+        nums = nums * len(revs)
+    elif len(nums) != len(revs):
+        raise DvcException(
+            "The number of ``--num`` values must be 1 or must match the number "
+            "of ``--rev`` values."
+        )
+
+    # --------------------------------------------------------------------- #
+    # Dispatch per (rev, num) pair ------------------------------------------ #
+    # --------------------------------------------------------------------- #
+    for r, n in zip(revs, nums):
+        _exp_show(r, n, **kwargs)
```

```diff
--- a/dvc/exp/show.py
+++ b/dvc/exp/show.py
@@
-def _exp_show(rev: Optional[str], n: Optional[int], **kwargs) -> None:
+def _exp_show(rev: Optional[str], n: Optional[int], **kwargs) -> None:
@@
-    # Existing implementation that expects a single rev and a single n.
-    # No changes required here – the wrapper 
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

=== TASK octokit__auth-app.js-689 | repo octokit/auth-app.js | ts
--- Issue
[FEAT]: coalesce concurrent authentication calls
### Describe the need

#### Context
This is somewhat related to https://github.com/octokit/octokit.js/issues/2800. Although installation access tokens are cached for an hour, this caching only takes effect once a token is acquired. If a token is not yet cached (or is expired), and you trigger authentication multiple times concurrently, then multiple identical requests will be made to `/app/installations/.../access_tokens`. This is redundant and inefficient, and can negatively impact other API calls, depending on your throttling configuration. This suboptimal behavior was observed in writing tests for https://github.com/octokit/plugin-throttling.js/pull/779.

#### Proposal
If an authentication request is already in flight (i.e. `getInstallationAuthentication` has been called but the promise has not yet resolved), subsequent calls with the same options should return the same promise. This way we ensure that we only query and cache the token once.

### SDK Version

_No response_

### API Version

_No response_

### Relevant log output

```shell

```

### Code of Conduct

- [x] I agree to follow this project's Code of Conduct
--- REAL FIX (ground truth)
diff --git a/src/cache.ts b/src/cache.ts
index ecfe999..3022b9f 100644
--- a/src/cache.ts
+++ b/src/cache.ts
@@ -2,7 +2,7 @@ import { Lru } from "toad-cache";
 
 /* v8 ignore next */
 import type {
-  InstallationAuthOptions,
+  CacheableInstallationAuthOptions,
   Cache,
   CacheData,
   Permissions,
@@ -21,7 +21,7 @@ export function getCache() {
 
 export async function get(
   cache: Cache,
-  options: InstallationAuthOptions,
+  options: CacheableInstallationAuthOptions,
 ): Promise<InstallationAccessTokenData | void> {
   const cacheKey = optionsToCacheKey(options);
   const result = await cache.get(cacheKey);
@@ -66,7 +66,7 @@ export async function get(
 }
 export async function set(
   cache: Cache,
-  options: InstallationAuthOptions,
+  options: CacheableInstallationAuthOptions,
   data: CacheData,
 ): Promise<void> {
   const key = optionsToCacheKey(options);
@@ -91,12 +91,13 @@ export async function set(
   await cache.set(key, value);
 }
 
-function optionsToCacheKey({
+// TODO: consider baseUrl and appId too, so that we don't incorrectly cache tokens across them
+export function optionsToCacheKey({
   installationId,
   permissions = {},
   repositoryIds = [],
   repositoryNames = [],
-}: InstallationAuthOptions) {
+}: CacheableInstallationAuthOptions) {
   const permissionsString = Object.keys(permissions)
     .sort()
     .map((name) => (permissions[name] === "read" ? name : `${name}!`))
diff --git a/src/get-installation-authentication.ts b/src/get-installation-authentication.ts
index 889e5ee..08758f6 100644
--- a/src/get-installation-authentication.ts
+++ b/src/get-installation-authentication.ts
@@ -1,4 +1,4 @@
-import { get, set } from "./cache.js";
+import { get, optionsToCacheKey, set } from "./cache.js";
 import { getAppAuthentication } from "./get-app-authentication.js";
 import { toTokenAuthentication } from "./to-token-authentication.js";
 import type {
@@ -6,6 +6,7 @@ import type {
   InstallationAccessTokenAuthentication,
   RequestInterface,
   State,
+  CacheableInstallationAuthOptions,
 } from "./types.js";
 
 export async function getInstallationAuthentication(
@@ -30,16 +31,45 @@ export async function getInstallationAuthentication(
     return factory(factoryAuthOptions);
   }
 
-  const optionsWithInstallationTokenFromState = Object.assign(
-    { installationId },
-    options,
+  const request = customRequest || state.request;
+
+  return getInstallationAuthenticationConcurrently(
+    state,
+    { ...options, installationId },
+    request,
   );
+}
 
+const pendingPromises = new Map<
+  string,
+  Promise<InstallationAccessTokenAuthentication>
+>();
+
+function getInstallationAuthenticationConcurrently(
+  state: State,
+  options: CacheableInstallationAuthOptions,
+  request: RequestInterface,
+) {
+  const cacheKey = optionsToCacheKey(options);
+  if (pendingPromises.has(cacheKey)) {
+    return pendingPromises.get(cacheKey)!;
+  }
+  const promise = getInstallationAuthenticationImpl(
+    state,
+    option
--- Proposal A
## Root cause
`getInstallationAuthentication` creates a new request for an installation access token every time it is called when no valid token is cached.  
When several calls are made concurrently (e.g. in parallel tests) the function does not recognise that a request for the same installation is already in‑flight, so it issues **multiple identical HTTP requests** to  
`POST /app/installations/:installation_id/access_tokens`.  
The library only caches the token **after** the request resolves, therefore the duplicate requests cannot be avoided with the existing cache alone.

## Fix
Coalesce concurrent authentication calls by keeping a map of *pending* promises keyed by the installation‑specific authentication options.  
If a request for the same installation (and the same permission set) is already in progress, subsequent calls return the same promise instead of starting a new request.

```diff
--- a/src/auth-app.ts
+++ b/src/auth-app.ts
@@
 export class AuthApp implements AuthInterface {
   // existing fields …
   private tokenCache = new Map<string, CachedToken>();
+  // <--- NEW: map of pending authentication promises
+  // The key is a deterministic string that represents the installation
+  // together with the requested permissions, repository IDs, etc.
+  private pendingAuth = new Map<string, Promise<Authentication>>();
 
   // -------------------------------------------------------------------------
   // Helper to build a cache key that uniquely identifies a token request
   // -------------------------------------------------------------------------
   private getCacheKey(installationId: number, options: InstallationAuthOptions): string {
     // The key must include everything that influences the resulting token
-    // (e.g. permissions, repository IDs, etc.).  For simplicity we JSON‑stringify
-    // the relevant parts.
+    // (e.g. permissions, repository IDs, etc.).  For simplicity we JSON‑stringify
+    // the relevant parts.
     const { permissions, repositoryIds, repositorySelection } = options;
     return [
       installationId,
@@
   async getInstallationAuthentication(
     options: InstallationAuthOptions = {}
   ): Promise<Authentication> {
-    const installationId = await this.getInstallationId(options);
-
-    // 1️⃣ Try to return a cached token
-    const cached = this.getCachedToken(installationId, options);
-    if (cached) {
-      return cached;
-    }
-
-    // 2️⃣ No cached token → request a new one
-    const token = await this.requestNewInstallationToken(installationId, options);
-
-    // 3️⃣ Cache the newly obtained token for future calls
-    this.cacheToken(installationId, token, options);
-
-    return token;
+    const installationId = await this.getInstallationId(options);
+
+    // -----------------------------------------------------------------
+    // 1️⃣ Return a cached token if it is still valid
+    // -----------------------------------------------------------------
+    const cached = this.getCachedToken(installationId, options);
+    if (cached) {
+      return cached;
+    }
+
+    // -----------------------------------------------------------------
+    // 2️⃣ Coalesce concurrent requests
+    // -----------------------------------------------------------------
+    const cacheKey = this.getCacheKey(installationId, options);
+
+    // If a request for the same installation/options is already in flight,
+    // return the same promise.
+    const pending = this.pendingAuth.get(ca
--- Proposal B
## Root cause
`getInstallationAuthentication` (exposed as `auth({ type: "installation", ... })`) checks the in‑memory token cache and, if the token is missing or expired, immediately issues a request to **/app/installations/:id/access_tokens**.  
When the same installation is authenticated concurrently, each call goes through the same “missing‑or‑expired” path, creates its own request and its own promise. The library never records that a request is already *in flight*, so the cache is only populated after the first request resolves. This results in duplicate network calls and unnecessary load on the GitHub API.

## Fix
Coalesce concurrent authentication calls by storing a *pending* promise for each installation (and permission set). Subsequent calls while a request is pending will return the same promise. The pending entry is cleared once the request resolves or rejects.

```diff
--- a/src/auth-app.ts
+++ b/src/auth-app.ts
@@
 import { request } from "@octokit/request";
 import { createOAuthAppAuth } from "@octokit/auth-oauth-app";
 import { createAppAuth } from "@octokit/auth-app";
 import type {
   AuthInterface,
   Authentication,
   InstallationAuthOptions,
   State,
   StrategyOptions,
 } from "./types";
+import { createHash } from "crypto";

 // existing code ...

 export function createAppAuth(state: State): AuthInterface {
   // a map that holds pending authentication promises
-  // (keyed by a hash of installationId + permissions + repositoryIds)
-  const pendingAuthPromises = new Map<string, Promise<Authentication>>();
+  const pendingAuthPromises = new Map<string, Promise<Authentication>>();

   async function getInstallationAuthentication(
     options: InstallationAuthOptions
   ): Promise<Authentication> {
@@
-    // 1️⃣ Check the cache first
-    const cached = state.cache.getInstallationAuthentication(options);
-    if (cached) {
-      return cached;
-    }
+    // 1️⃣ Check the cache first
+    const cached = state.cache.getInstallationAuthentication(options);
+    if (cached) {
+      return cached;
+    }
+
+    // 2️⃣ If a request for the same installation/permissions is already in flight,
+    //    return the same promise instead of creating a new request.
+    const pendingKey = hashInstallationOptions(options);
+    const pending = pendingAuthPromises.get(pendingKey);
+    if (pending) {
+      return pending;
+    }

-    // 2️⃣ No cached token – request a new one
-    const token = await requestInstallationAccessToken(state, options);
-    // 3️⃣ Store it in the cache for future calls
-    state.cache.setInstallationAuthentication(options, token);
-    return token;
+    // 3️⃣ No cached token and no pending request – start a new request
+    const promise = (async () => {
+      try {
+        const token = await requestInstallationAccessToken(state, options);
+        // store the fresh token in the cache for future calls
+        state.cache.setInstallationAuthentication(options, token);
+        return token;
+      } finally {
+        // clean up the pending entry regardless of success or failure
+        pendingAuthPromises.delete(pendingKey);
+      }
+    })();
+
+    // store the pending promise before awaiting it
+    pendingAuthPromises.set(pendingKey, promise);
+    return promise;
   }

   // Helper to create a deterministic key for the pending map.
-  // The key must consider installationId, permissions, repositoryIds and any other
-  // option that influences the resulting token.
+  // The key 

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
`glam` provides many component‑wise math helpers (e.g. `floor`, `ceil`, `round`, `trunc`) for its vector types, but it does **not** expose a `fract` method.  GLSL’s `fract(x)` is defined as `x - floor(x)`, and the lack of a corresponding Rust method forces users to write the expression manually when porting shaders.  Adding a native `fract` implementation for `Vec2`, `Vec3` and `Vec4` removes this inconvenience.

## Fix
Implemented a `fract` method for the three primary vector types.  
The method is added as an inherent function (just like the existing `floor`, `ceil`, `round`, `trunc` methods) and uses the already‑available `f32::fract()` intrinsic.

```diff
--- a/src/vec2.rs
+++ b/src/vec2.rs
@@
 impl Vec2 {
@@
     #[inline]
     pub fn trunc(self) -> Self {
         Self::new(self.x.trunc(), self.y.trunc())
     }
+
+    /// Returns the fractional part of each component.
+    ///
+    /// Equivalent to `self - self.floor()`.  This mirrors GLSL’s `fract`
+    /// function and is useful when porting shader code.
+    ///
+    /// # Examples
+    ///
+    /// ```
+    /// # use glam::Vec2;
+    /// let v = Vec2::new(1.7, -0.3);
+    /// assert_eq!(v.fract(), Vec2::new(0.7, 0.7));
+    /// ```
+    #[inline]
+    pub fn fract(self) -> Self {
+        Self::new(self.x.fract(), self.y.fract())
+    }
 }
```

```diff
--- a/src/vec3.rs
+++ b/src/vec3.rs
@@
 impl Vec3 {
@@
     #[inline]
     pub fn trunc(self) -> Self {
         Self::new(self.x.trunc(), self.y.trunc(), self.z.trunc())
     }
+
+    /// Returns the fractional part of each component.
+    ///
+    /// Mirrors GLSL’s `fract` function (`x - floor(x)`).
+    ///
+    /// # Examples
+    ///
+    /// ```
+    /// # use glam::Vec3;
+    /// let v = Vec3::new(2.4, -1.2, 3.0);
+    /// assert_eq!(v.fract(), Vec3::new(0.4, 0.8, 0.0));
+    /// ```
+    #[inline]
+    pub fn fract(self) -> Self {
+        Self::new(self.x.fract(), self.y.fract(), self.z.fract())
+    }
 }
```

```diff
--- a/src/vec4.rs
+++ b/src/vec4.rs
@@
 impl Vec4 {
@@
     #[inline]
     pub fn trunc(self) -> Self {
         Self::new(self.x.trunc(), self.y.trunc(), self.z.trunc(), self.w.trunc())
     }
+
+    /// Returns the fractional part of each component.
+    ///
+    /// Equivalent to `self - self.floor()`.  Provides the same behaviour as
+    /// GLSL’s `fract` for 4‑component vectors.
+    ///
+    /// # Examples
+    ///
+    /// ```
+    /// # use glam::Vec4;
+    /// let v = Vec4::new(0.9, -2.3, 5.0, 1.25);
+    /// assert_eq!(v.fract(), Vec4::new(0.9, 0.7, 0.0, 0.25));
+    /// ```
+    #[inline]
+    pub fn fract(self) -> Self {
+        Self::new(
+            self.x.fract(),
+            self.y.fract(),
+            self.z.fract(),
+            self.w.fract(),
+        )
+    }
 }
```

No additional changes to `lib.rs` are required because the methods are inherent to the public vector types already re‑exported by the crate.

## How to verify
1. **Compile the crate** – run `cargo build` to ensure the new methods do not introduce any compilation errors.

2. **Run the existing test suite** – `cargo test`. All current tests should still pass.

3. **Add a new test** (or run the snippet below) to confirm the behaviour:

```rust
#[test]
fn test_vec_fract() {
    use glam::{Vec2, Vec3, Vec4};

    let v2 = Vec2::new(1.7, -0.3);
    assert_eq!(v2.fract(), Vec2::new(0.7, 0.7));

    let v3 = Vec3::new(2.4, -1.2, 3.0);
    assert_eq!(v3.fract(), Vec3::new(0.4, 0.8, 0.0));

    let v4 = Vec4::n

=== TASK compuives__codesandbox-client-321 | repo CompuIves/codesandbox-client | js
--- Issue
[feature request] Honour NODE_PATH in package.json
Please consider honouring `NODE_PATH` in package.json. For instance:

```js
  "scripts": {
    "start": "NODE_PATH=src react-scripts start",
    "build": "NODE_PATH=src react-scripts build",
    "test": "NODE_PATH=src react-scripts test --env=jsdom",
    "eject": "NODE_PATH=src react-scripts eject"
  }
```

Then, things like this:

`import "../../../../../../../../library/NavBar.js"`

will become:

`import "library/NavBar.js"`
--- REAL FIX (ground truth)
diff --git a/packages/app/src/app/pages/Sandbox/Editor/Workspace/EntryContainer.js b/packages/app/src/app/pages/Sandbox/Editor/Workspace/EntryContainer.js
index 1a19b0c2e..bf0c52847 100644
--- a/packages/app/src/app/pages/Sandbox/Editor/Workspace/EntryContainer.js
+++ b/packages/app/src/app/pages/Sandbox/Editor/Workspace/EntryContainer.js
@@ -12,12 +12,12 @@ export const getContainerStyles = props => {
     position: relative;
     display: flex;
     font-size: 14px;
-    padding: 0.6rem;
-    padding-left: ${props.depth != null
-      ? `${props.depth + 1.5}rem`
-      : 'calc(1rem - 2px)'};
+    padding: 0.4rem;
+    padding-left: ${
+      props.depth != null ? `${props.depth + 1.5}rem` : 'calc(1rem - 2px)'
+    };
     padding-right: 3rem;
-    color: ${theme.background.lighten(2)()};
+    color: rgba(255, 255, 255, 0.5);
     text-decoration: none;
     font-weight: 400;
     min-width: 100px;
@@ -26,13 +26,15 @@ export const getContainerStyles = props => {
     user-select: none;
 
     &:hover {
-      ${props.active || props.editing
-        ? ''
-        : `
+      ${
+        props.active || props.editing
+          ? ''
+          : `
         background-color: ${color.clearer(0.9)()};
         color: ${theme.background.lighten(5)()};
         border-color: ${color.darken(0.4)()};
-      `}
+      `
+      }
 
       > div {
         opacity: 1 !important;
diff --git a/packages/app/src/app/pages/Sandbox/Editor/Workspace/Files/DirectoryEntry/Entry/EntryIcons.js b/packages/app/src/app/pages/Sandbox/Editor/Workspace/Files/DirectoryEntry/Entry/EntryIcons.js
index 4ce43ec2b..d4473f57c 100644
--- a/packages/app/src/app/pages/Sandbox/Editor/Workspace/Files/DirectoryEntry/Entry/EntryIcons.js
+++ b/packages/app/src/app/pages/Sandbox/Editor/Workspace/Files/DirectoryEntry/Entry/EntryIcons.js
@@ -4,7 +4,8 @@ import styled from 'styled-components';
 import ProjectIcon from 'react-icons/lib/go/file-code';
 import FunctionIcon from 'react-icons/lib/fa/code';
 import FolderIcon from 'react-icons/lib/md/keyboard-arrow-down';
-import DirectoryIcon from 'react-icons/lib/go/file-directory';
+import DirectoryIcon from 'react-icons/lib/md/folder';
+import DirectoryOpenIcon from 'react-icons/lib/md/folder-open';
 import NotSyncedIcon from 'react-icons/lib/go/primitive-dot';
 import CSSIcon from 'react-icons/lib/fa/css3';
 import HTMLIcon from 'react-icons/lib/fa/html5';
@@ -19,7 +20,9 @@ const NotSyncedIconWithMargin = styled(NotSyncedIcon)`
   color: ${props => props.theme.templateColor || props.theme.secondary};
 `;
 
-const RedIcon = styled.span`color: ${props => props.theme.red};`;
+const RedIcon = styled.span`
+  color: ${props => props.theme.red};
+`;
 
 const StyledFolderIcon = styled.span`
   svg {
@@ -31,7 +34,7 @@ const StyledFolderIcon = styled.span`
   }
 `;
 
-const getIcon = (type, error, root) => {
+const getIcon = (type, error, root, open) => {
   if (root) {
     return <ProjectIcon />;
   }
@@ -51,7 +54,7 @@ const getIcon = (type, error, r
--- Proposal A
## Root cause
The sandbox bundler builds its own Webpack configuration and extracts environment variables from the `scripts` field of **package.json**.  
Only a whitelist of variables (e.g. `REACT_APP_…`) is currently parsed, so a line such as  

```json
"start": "NODE_PATH=src react-scripts start"
```  

does **not** set `process.env.NODE_PATH` for the sandbox.  
Because the generated Webpack config never reads `process.env.NODE_PATH`, the `resolve.modules` array does not contain the user‑provided source folder, and absolute imports like  

```js
import "library/NavBar.js"
```  

cannot be resolved.  

## Fix
* Parse `NODE_PATH` from the script string and expose it as an environment variable for the sandbox.
* Extend the generated Webpack configuration to add the paths from `NODE_PATH` to `resolve.modules`, mirroring Create‑React‑App’s behaviour.

```diff
--- a/src/sandpack/resolveEnv.ts
+++ b/src/sandpack/resolveEnv.ts
@@
 import { readFileSync } from 'fs';
 import path from 'path';
 
 /** Extract environment variables from a script string (e.g. "NODE_PATH=src react-scripts start") */
 export function extractEnvFromScript(script: string): Record<string, string> {
   const env: Record<string, string> = {};
-  // Existing logic only extracts REACT_APP_ variables
-  const parts = script.split(' ');
-  for (const part of parts) {
-    const match = part.match(/^([A-Z0-9_]+)=(.*)$/);
-    if (match) {
-      const [, key, value] = match;
-      if (key.startsWith('REACT_APP_')) {
-        env[key] = value;
-      }
-    }
-  }
+  // Existing logic only extracts REACT_APP_ variables, but we also need NODE_PATH
+  const parts = script.split(' ');
+  for (const part of parts) {
+    const match = part.match(/^([A-Z0-9_]+)=(.*)$/);
+    if (match) {
+      const [, key, value] = match;
+      // Preserve the original whitelist (REACT_APP_…) …
+      if (key.startsWith('REACT_APP_')) {
+        env[key] = value;
+      }
+      // … and additionally capture NODE_PATH so that it can be honoured later.
+      if (key === 'NODE_PATH') {
+        env[key] = value;
+      }
+    }
+  }
   return env;
 }
 
 /** Load the package.json of a sandbox and return its scripts */
 export function getPackageScripts(sandboxPath: string): Record<string, string> {
   try {
@@
   }
 }
 
*** End of File
--- a/src/sandpack/webpackConfig.ts
+++ b/src/sandpack/webpackConfig.ts
@@
 import path from 'path';
 import webpack from 'webpack';
 import { extractEnvFromScript, getPackageScripts } from './resolveEnv';
 
 export function createWebpackConfig(sandboxDir: string, entryFile: string): webpack.Configuration {
   const scripts = getPackageScripts(sandboxDir);
   const startScript = scripts.start || '';
   const env = extractEnvFromScript(startScript);
 
   // Base config
   const config: webpack.Configuration = {
     mode: 'development',
     context: sandboxDir,
     entry: entryFile,
     output: {
       path: path.resolve(sandboxDir, 'dist'),
       filename: 'bundle.js',
       publicPath: '/',
     },
     resolve: {
-      modules: ['node_modules'],
+      // By default Webpack resolves modules from node_modules.
+      // If the user defined NODE_PATH in their start script we prepend those
+      // directories so that absolute imports (e.g. "library/NavBar.js") work.
+      modules: ['node_modules'],
     },
     module: {
       rules: [
         {
           test: /\.[jt]sx?$/,
@@
     plugins: [
       // Define process.env variables for the sandbox runtim
--- Proposal B
## Root cause
The sandbox runner executes the commands defined in **package.json** by spawning a child process with the current process environment (`process.env`).  
When a script contains an inline environment variable assignment (e.g. `NODE_PATH=src react‑scripts start`), that assignment is **not** parsed – it is passed to the shell as part of the command string.  
Because the sandbox spawns the command with `{ shell: false }` (using `cross‑spawn`/`execa`), the inline assignment is ignored, so `process.env.NODE_PATH` never gets set. Consequently absolute imports that rely on `NODE_PATH` do not work.

## Fix
Parse inline environment variable assignments at the beginning of a script line, inject them into the child‑process environment, and strip them from the command that is actually executed.

```diff
--- a/src/sandbox/executeScript.ts
+++ b/src/sandbox/executeScript.ts
@@
-import { spawn } from 'cross-spawn';
+import { spawn } from 'cross-spawn';
 
 export async function runPackageScript(
   script: string,
   cwd: string,
   extraEnv: NodeJS.ProcessEnv = {}
 ): Promise<{ stdout: string; stderr: string }> {
-  return new Promise((resolve, reject) => {
-    const child = spawn(script, {
-      cwd,
-      env: { ...process.env, ...extraEnv },
-      stdio: 'pipe',
-      shell: false,
-    });
+  // -------------------------------------------------------------------------
+  // 1️⃣  Extract inline env assignments (e.g. NODE_PATH=src) from the script.
+  // -------------------------------------------------------------------------
+  // The syntax supported by npm/yarn is a series of KEY=VALUE tokens at the
+  // very start of the command line, separated by spaces.  We stop parsing as
+  // soon as we encounter a token that does **not** contain an '='.
+  const envFromScript: NodeJS.ProcessEnv = {};
+  const parts = script.split(' ');
+  let commandStartIdx = 0;
+
+  for (let i = 0; i < parts.length; i++) {
+    const part = parts[i];
+    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(part)) {
+      const [key, ...rest] = part.split('=');
+      envFromScript[key] = rest.join('='); // keep possible '=' in value
+      commandStartIdx = i + 1;
+    } else {
+      break;
+    }
+  }
+
+  // Re‑assemble the actual command without the env assignments.
+  const cleanCommand = parts.slice(commandStartIdx).join(' ');
+
+  // -------------------------------------------------------------------------
+  // 2️⃣  Spawn the process with the merged environment.
+  // -------------------------------------------------------------------------
+  return new Promise((resolve, reject) => {
+    const child = spawn(cleanCommand, {
+      cwd,
+      env: { ...process.env, ...extraEnv, ...envFromScript },
+      stdio: 'pipe',
+      shell: false,
+    });
 
     let stdout = '';
     let stderr = '';
@@
-    child.on('close', code => {
-      if (code === 0) {
-        resolve({ stdout, stderr });
-      } else {
-        const err = new Error(`Command failed with exit code ${code}`);
-        // @ts-ignore
-        err.code = code;
-        reject(err);
-      }
-    });
+    child.on('close', code => {
+      if (code === 0) {
+        resolve({ stdout, stderr });
+      } else {
+        const err = new Error(`Command failed with exit code ${code}`);
+        // @ts-ignore
+        err.code = code;
+        reject(err);
+      }
+    });
   });
 }
```

### What the patch does
1. **Parse** the leading `KEY=VALUE` tokens from the script string.
2. **Inject** those

=== TASK BraeWebb__allocate-10 | repo BraeWebb/allocate | python
--- Issue
Compositional Allocations
Allow for composition allocations by exporting an updated availability sheet based on an allocation run.

This would allow for a regular allocation to be generated for normal weeks and then an additional 'overflow' allocation to be generated when more tutors are required.

Example usage:
```bash
allocate --update-availability tutors.csv sessions.csv allocation.csv > new_allocation.csv
```
--- REAL FIX (ground truth)
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
+|   | February 2020 |   |   |  |  |  |  |  |  |
+|   | Tue 4 |   |   | Wed 5 |  |  | Thu 6 | Fri 7 |  |
+|   | 11:00 AM – 1:00 PM | 1:00 PM – 3:00 PM | 3:00 PM – 5:00 PM | 8:00 AM – 9:00 AM | 9:00 AM – 10:00 AM | 12:00 PM – 2:00 PM | 9:00 AM – 11:00 AM | 11:00 AM – 12:00 PM | 12:00 PM – 1:00 PM |
+| Brae | OK |   | OK |  | OK | OK | OK |  | OK |
+| Henry | OK | OK | OK | OK |  |  | OK |  |  |
+| Emily |   | OK |   |  | OK | OK |  | OK | OK |
+| Count | 2 | 2 | 1 | 2 | 2 | 2 | 2 | 2 | 2 |
+
+The first thing we do is generate `tutors.csv` and `sessions.csv` files from the availability:
+```bash
+allocate --stub --doodle tutors.csv sessions.csv doodle.csv
+```
+
+Once this is completed we open the `tutors.csv` and `sessions.csv` files and fill out the required details.
+
+After filling out details in those spreadsheets, we perform the first allocations.
+```bash
+allocate --doodle tutors.csv sessions.csv doodle.csv
+```
+
+This will provide us the allocation for regular weeks. If we want allocations for extra weeks, we fir
--- Proposal A
## Root cause
The CLI only writes the allocation result to the file given as the third positional argument.  
There is no way to obtain the *updated* tutor‑availability sheet after the allocation run, which is required for compositional (overflow) allocations. The `--update-availability` flag mentioned in the issue does not exist, so the command fails or simply ignores the request.

## Fix
Add a `--update-availability` flag to the command‑line interface.  
When the flag is present the program:

1. Performs the allocation as before (writing the allocation CSV to the supplied output file).  
2. Computes the remaining availability for each tutor based on the allocation that was just performed.  
3. Writes the updated availability sheet to **stdout** (so the caller can redirect it to a new file).

The implementation touches the CLI entry point (`allocate/cli.py`) and adds a small helper that updates the tutor records.

```diff
--- a/allocate/cli.py
+++ b/allocate/cli.py
@@
-import argparse
-import csv
-import sys
+import argparse
+import csv
+import sys
+from collections import Counter
@@
-def main():
-    parser = argparse.ArgumentParser(
-        description="Allocate tutors to sessions."
-    )
-    parser.add_argument("tutors", help="CSV file with tutor availability")
-    parser.add_argument("sessions", help="CSV file with session requirements")
-    parser.add_argument(
-        "allocation", help="Path to write the allocation CSV"
-    )
-    args = parser.parse_args()
-
-    # Load data
-    tutors = list(csv.DictReader(open(args.tutors, newline=\"\")))
-    sessions = list(csv.DictReader(open(args.sessions, newline=\"\")))
-
-    # Run allocation algorithm (implementation lives in allocate.core)
-    allocation = allocate_core(tutors, sessions)
-
-    # Write allocation result
-    with open(args.allocation, \"w\", newline=\"\") as f:
-        writer = csv.DictWriter(f, fieldnames=[\"tutor_id\", \"session_id\"])
-        writer.writeheader()
-        for row in allocation:
-            writer.writerow(row)
-
-
-if __name__ == \"__main__\":
-    main()
+def _update_tutor_availability(tutors, allocation):
+    """
+    Return a new list of tutor dictionaries with the availability column
+    (named ``capacity``, ``max_sessions`` or ``available``) reduced by the
+    number of sessions allocated to each tutor.
+    """
+    # Determine which column stores the numeric capacity.
+    capacity_keys = [\"capacity\", \"max_sessions\", \"available\", \"slots\"]
+    cap_key = None
+    for key in capacity_keys:
+        if key in tutors[0]:
+            cap_key = key
+            break
+    if cap_key is None:
+        # No recognised capacity column – nothing to update.
+        return tutors
+
+    # Count how many sessions each tutor received.
+    allocated_counts = Counter(row[\"tutor_id\"] for row in allocation)
+
+    updated = []
+    for tutor in tutors:
+        tutor_id = tutor[\"tutor_id\"]
+        try:
+            original = int(tutor[cap_key])
+        except (KeyError, ValueError):
+            # If the column is missing or not an int, leave the row unchanged.
+            updated.append(tutor.copy())
+            continue
+        remaining = original - allocated_counts.get(tutor_id, 0)
+        # Guard against negative numbers – set to 0 if over‑allocated.
+        remaining = max(remaining, 0)
+        new_tutor = tutor.copy()
+        new_tutor[cap_key] = str(remaining)
+        updated.append(new_tutor)
+    return upd
--- Proposal B
## Root cause
`allocate` only produces an allocation CSV. There is no way to feed the result of one allocation back into a subsequent run, because the tool never writes out the tutors’ remaining availability after the allocation has been applied. The missing feature is an **`--update-availability`** option that writes a new availability sheet reflecting the hours already assigned.

## Fix
Add a new CLI flag `--update-availability` (optionally with an output path) and implement the logic that:
1. Reads the original tutors‑availability CSV.
2. Performs the allocation as before.
3. Calculates the remaining availability for each tutor by subtracting the total hours allocated to that tutor.
4. Writes the updated availability CSV (either to a user‑specified file or to `<original‑file‑stem>_updated.csv`).

```diff
--- a/allocate/cli.py
+++ b/allocate/cli.py
@@
-import argparse
-import sys
-from .allocator import allocate_sessions
+import argparse
+import sys
+import os
+from .allocator import allocate_sessions, update_availability
@@
-def main(argv=None):
-    parser = argparse.ArgumentParser(
-        description="Allocate tutors to sessions based on availability."
-    )
-    parser.add_argument("tutors", help="CSV file with tutor availability")
-    parser.add_argument("sessions", help="CSV file with session requirements")
-    parser.add_argument(
-        "-o",
-        "--output",
-        help="File to write allocation CSV to (default: stdout)",
-        default="-",
-    )
-    args = parser.parse_args(argv)
-
-    allocation = allocate_sessions(args.tutors, args.sessions)
-
-    if args.output == "-":
-        sys.stdout.write(allocation.to_csv(index=False))
-    else:
-        allocation.to_csv(args.output, index=False)
-
-    return 0
+def main(argv=None):
+    parser = argparse.ArgumentParser(
+        description="Allocate tutors to sessions based on availability."
+    )
+    parser.add_argument("tutors", help="CSV file with tutor availability")
+    parser.add_argument("sessions", help="CSV file with session requirements")
+    parser.add_argument(
+        "-o",
+        "--output",
+        help="File to write allocation CSV to (default: stdout)",
+        default="-",
+    )
+    # New flag – either a boolean (write to default‑named file) or a path
+    parser.add_argument(
+        "--update-availability",
+        nargs="?",
+        const=True,
+        metavar="FILE",
+        help=(
+            "Export an updated availability CSV after allocation. "
+            "If FILE is omitted, the updated sheet is written to "
+            "`<tutors‑file‑stem>_updated.csv` in the current directory."
+        ),
+    )
+
+    args = parser.parse_args(argv)
+
+    # ------------------------------------------------------------------
+    # 1️⃣  Perform the allocation
+    # ------------------------------------------------------------------
+    allocation = allocate_sessions(args.tutors, args.sessions)
+
+    # ------------------------------------------------------------------
+    # 2️⃣  Write allocation result (unchanged behaviour)
+    # ------------------------------------------------------------------
+    if args.output == "-":
+        sys.stdout.write(allocation.to_csv(index=False))
+    else:
+        allocation.to_csv(args.output, index=False)
+
+    # ------------------------------------------------------------------
+    # 3️⃣  If requested, write the updated availability sheet
+    # -------------------------------------

=== TASK pydantic__pydantic-817 | repo pydantic/pydantic | python
--- Issue
Root Errors and "__obj__" vs "__root__"
Currently [here](https://github.com/samuelcolvin/pydantic/blob/master/pydantic/main.py#L343) (for invalid data to `parse_obj`) and [here](https://github.com/samuelcolvin/pydantic/blob/master/pydantic/main.py#L361) (in `parse_raw`) we use `'__obj__'` as the location for errors to indicate an error which is general to the model, not specific to a field.

We should change this in v1 to `__root__` to better match other nomenclature, eg. custom root types, where we use `__root__`.

We should also implement at some point entire model validation, as discussed at https://github.com/samuelcolvin/pydantic/issues/691#issuecomment-518318096. I guess to match the above naming, we should do this using a special value to a validator `@validator('__root__')`. If this raised an error it would end up in `ValidationError` details with `'location': '__root__'`, (Unless it's a "`@validator('__root__')`" validator for a submodel, when obviously it should have the location of that model. 

Perhaps it would would be nice to have a special decorator `@root_validator` which is an alias of `@validator('__root__')`, we should support multiple root validators either as a result of inheritance or on a single class.

I guess the `__obj__` > `__root__` change must happen with v1 and the new root validator could go in a later release, but we should be sure we won't need other backwards incompatible changes to implement the root validator.

Thoughts?

--- REAL FIX (ground truth)
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
-# > UserModel name='Samuel Colvin' password1='zxcvbn' password2='zxcvbn'
+#> UserModel name='Samuel Colvin' username='scolvin' password1='zxcvbn' password2='zxcvbn'
 
 try:
     UserModel(name='samuel', username='scolvin', password1='zxcvbn', password2='zxcvbn2')
 except ValidationError as e:
     print(e)
 """
-2 validation errors
+2 validation errors for UserModel
 name
   must contain a space (type=value_error)
 password2
diff --git a/pydantic/__init__.py b/pydantic/__init__.py
--- a/pydantic/__init__.py
+++ b/pydantic/__init__.py
@@ -1,6 +1,6 @@
 # flake8: noqa
 from . import dataclasses
-from .class_validators import validator
+from .class_validators import root_validator, validator
 from .env_settings import BaseSettings
 from .error_wrappers import ValidationError
 from .errors import *
diff --git a/pydantic/class_validators.py b/pydantic/class_validators.py
--- a/pydantic/class_validators.py
+++ b/pydantic/class_validators.py
@@ -4,24 +4,24 @@
 from inspect import Signature, signature
 from itertools im
--- Proposal A
## Root cause
`BaseModel.parse_obj` and `BaseModel.parse_raw` construct a `ValidationError` for errors that are not tied to a specific field.  
Historically the location for such “model‑level” errors was the sentinel string `__obj__`.  
Pydantic already uses `__root__` for custom root types, so using `__obj__` is inconsistent and confusing.  
The code that builds the `ValidationError` therefore injects the wrong location, and any downstream logic (error rendering, tests, user code) that expects the canonical `__root__` name will not work.

## Fix
Replace every occurrence of the sentinel `__obj__` that is used as the error location in `parse_obj` and `parse_raw` with `__root__`.  
The change is isolated to `pydantic/main.py`; no other parts of the library rely on the old sentinel, so the modification is safe and does not affect the public API apart from the intended location rename.

```diff
--- a/pydantic/main.py
+++ b/pydantic/main.py
@@
     @classmethod
     def parse_obj(cls: Type[T], obj: Any) -> T:
         """
         Parse an object into a model, raising ValidationError if the object is invalid.
         """
-        try:
-            return cls.__validate__(obj)
-        except ValidationError as e:
-            # ``__obj__`` is used as a sentinel location for errors that are not tied to a specific field.
-            # It is replaced with ``__root__`` to be consistent with the rest of the library.
-            raise ValidationError(e.raw_errors, model=cls, loc=("__obj__",))
+        try:
+            return cls.__validate__(obj)
+        except ValidationError as e:
+            # ``__root__`` is the canonical location for model‑level errors (e.g. errors raised by a
+            # ``@validator('__root__')`` or by the model itself).  Previously ``__obj__`` was used,
+            # which was inconsistent with the rest of the code‑base.
+            raise ValidationError(e.raw_errors, model=cls, loc=("__root__",))
 
     @classmethod
     def parse_raw(
         cls: Type[T],
         b: Union[bytes, str, bytearray],
         *,
         content_type: str = None,
         encoding: str = "utf8",
         proto: str = None,
         allow_pickle: bool = False,
         json_loads: Callable[[str], Any] = None,
         **kwargs: Any,
     ) -> T:
@@
         try:
             obj = json_loads(b)
         except Exception as exc:  # pragma: no cover
             raise ValidationError(
                 [
                     ErrorWrapper(
                         exc,
-                        loc=("__obj__",),
+                        loc=("__root__",),
                     )
                 ],
                 model=cls,
             )
 
-        try:
-            return cls.__validate__(obj, **kwargs)
-        except ValidationError as e:
-            # ``__obj__`` is used as a sentinel location for errors that are not tied to a specific field.
-            # It is replaced with ``__root__`` to be consistent with the rest of the library.
-            raise ValidationError(e.raw_errors, model=cls, loc=("__obj__",))
+        try:
+            return cls.__validate__(obj, **kwargs)
+        except ValidationError as e:
+            # ``__root__`` is the canonical location for model‑level errors.
+            raise ValidationError(e.raw_errors, model=cls, loc=("__root__",))
```

### Additional clean‑up (optional but recommended)
If the repository contains any other references to the sentinel `__obj__` (e.g. in tests, documentation, o
--- Proposal B
## Root cause
`BaseModel.parse_obj` and `BaseModel.parse_raw` raise a `ValidationError` with the location key `('__obj__',)` when the supplied data cannot be interpreted as a mapping for the model.  
`'__obj__'` is an internal placeholder that does not match the public naming used elsewhere in Pydantic (e.g. custom root types use `__root__`). This inconsistency makes error locations confusing and prevents the future implementation of true *root* validators that would also report errors under the `__root__` key.

## Fix
```diff
--- a/pydantic/main.py
+++ b/pydantic/main.py
@@
 class BaseModel:
@@
     @classmethod
     def parse_obj(cls: Type[T], obj: Any) -> T:
         """
         Parse an object into a model.
@@
-        if not isinstance(obj, Mapping):
-            # ``obj`` is not a mapping – raise a validation error that points to the whole object.
-            # ``__obj__`` is a historic placeholder used for this purpose.
-            raise ValidationError(
-                [ErrorWrapper(TypeError('input should be a mapping'), loc=('__obj__',))],
-                cls,
-            )
+        if not isinstance(obj, Mapping):
+            # ``obj`` is not a mapping – raise a validation error that points to the whole object.
+            # ``__root__`` is the public key used for errors that refer to the entire model.
+            raise ValidationError(
+                [ErrorWrapper(TypeError('input should be a mapping'), loc=('__root__',))],
+                cls,
+            )
@@
     @classmethod
     def parse_raw(
         cls: Type[T],
         b: Union[bytes, str],
         *,
         content_type: str = None,
         encoding: str = 'utf8',
         proto: str = None,
         allow_pickle: bool = False,
         json_loads: Callable[[str], Any] = None,
     ) -> T:
@@
-        try:
-            obj = json_loads(data)
-        except Exception as e:
-            # ``json_loads`` raised – raise a validation error that points to the whole object.
-            # ``__obj__`` is a historic placeholder used for this purpose.
-            raise ValidationError(
-                [ErrorWrapper(e, loc=('__obj__',))],
-                cls,
-            )
+        try:
+            obj = json_loads(data)
+        except Exception as e:
+            # ``json_loads`` raised – raise a validation error that points to the whole object.
+            # ``__root__`` is the public key used for errors that refer to the entire model.
+            raise ValidationError(
+                [ErrorWrapper(e, loc=('__root__',))],
+                cls,
+            )
         return cls.parse_obj(obj)
 
@@
 def validator(
     *fields: str,
     pre: bool = False,
     each_item: bool = False,
     always: bool = False,
@@
     return _validator
+
+
+def root_validator(
+    *,
+    pre: bool = False,
+    each_item: bool = False,
+    skip_on_failure: bool = False,
+) -> Callable[[Callable[..., Any]], classmethod]:
+    """
+    Alias for ``validator('__root__')`` that validates the whole model.
+
+    The function behaves exactly like ``validator('__root__')`` and therefore
+    supports multiple validators, inheritance and all the usual keyword
+    arguments (``pre``, ``each_item`` and ``skip_on_failure``).  Using a dedicated
+    ``@root_validator`` decorator makes the intent clearer and mirrors the
+    naming used for custom root types.
+    """
+    # ``validator`` returns a classmethod, so we simply forward the call.
+    return validator('__ro

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
`NCIComplex` creates each trial conformer by copying the original complex and **keeps the original `name` unchanged**.  
When the optimisation routine writes the results it uses `conformer.name` to build the output filename, so every trial is written to the same file (e.g. `water_trimer.xyz`). The optimiser therefore sees only the first conformer and skips the rest.

## Fix
Give every generated conformer a unique name that incorporates an index (or a UUID).  
The change is made in `autode/species.py` where the conformers are built inside `_generate_complex_conformers`. The new name is stored on the copy before it is handed to the optimiser.

```diff
--- a/autode/species.py
+++ b/autode/species.py
@@
 class NCIComplex(Complex):
@@
     def _generate_complex_conformers(self):
         """
         Generate a set of trial conformers for the NCI complex by
         sampling points on a sphere around each fragment and applying
         random rotations.
         """
-        for i in range(self.n_trials):
-            # copy the original complex (keeps the same name)
-            conf = copy.deepcopy(self)
-            # apply random rotation and translation to each fragment
-            conf._randomise_fragments()
-            self._conformers.append(conf)
+        for i in range(self.n_trials):
+            # copy the original complex – the copy initially has the same
+            # ``name`` as the parent, which caused all conformers to be
+            # written to the same file.  Append a unique suffix so that
+            # each conformer gets its own output filename.
+            conf = copy.deepcopy(self)
+
+            # ---- NEW: give a unique name to the conformer ----
+            # ``self.name`` may be ``None``; fall back to the class name.
+            base_name = self.name or self.__class__.__name__
+            conf.name = f\"{base_name}_conf_{i+1}\"
+            # --------------------------------------------------
+
+            # apply random rotation and translation to each fragment
+            conf._randomise_fragments()
+            self._conformers.append(conf)
*** End of File ***
```

*Explanation of the changes*

1. **Create a unique name** – after deep‑copying the parent complex we construct a new name using the parent’s name (or the class name if none was supplied) and the 1‑based index of the trial conformer.
2. **Assign the name to the copy** – `conf.name` is overwritten before any geometry manipulation or optimisation occurs.
3. The rest of the logic (random rotations, storage in `_conformers`) stays unchanged.

With distinct names, the downstream optimisation (`find_lowest_energy_conformer`) writes each result to a separate file (e.g. `water_trimer_conf_1.xyz`, `water_trimer_conf_2.xyz`, …), allowing all conformers to be evaluated.

## How to verify
1. **Run the reproducing script** (the one from the issue) after installing the patched version:

   ```python
   import autode as ade
   from autode.species import NCIComplex

   ade.Config.num_complex_sphere_points = 5
   ade.Config.num_complex_random_rotations = 3

   water = ade.Molecule(name='water', smiles='O')
   trimer = NCIComplex(water, water, water, name='water_trimer')
   trimer.find_lowest_energy_conformer(lmethod=ade.methods.XTB())
   trimer.print_xyz_file()
   ```

2. **Inspect the conformer directory** (usually `autode/conformers/` or the directory printed by `print_xyz_file`).  
   You should now see **multiple XTB output files**, e.g.:

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

=== TASK sphinx-gallery__sphinx-gallery-511 | repo sphinx-gallery/sphinx-gallery | python
--- Issue
ENH: remove API crossrefs from hover text
Currently if the first sentence of an example/tutorial/whatever contains an API cross-ref, the hover text can end up looking like this:

![Screenshot_2019-05-28_11-33-48](https://user-images.githubusercontent.com/1810515/58506751-989da580-813c-11e9-915d-f827ce715a82.png)

Enhancement would be to add a regex to the hover text generator that would strip out the cross-reference-generating text and leave just the class/function/method/attribute name. Ideally would be smart enough to behave the same way the cross-refs do w/r/t the presence of the leading `~`.
--- REAL FIX (ground truth)
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
     # lstrip is just in case docstring has a '\n\n' at the beginning
     paragraphs = docstring.lstrip().split('\n\n')
     # remove comments and other syntax like `.. _link:`
@@ -223,9 +245,9 @@ def extract_intro_and_title(filename, docstring):
     intro_paragraph = title if len(paragraphs) < 2 else paragraphs[1]
     # Concatenate all lines of the first paragraph and truncate at 95 chars
     intro = re.sub('\n', ' ', intro_paragraph)
+    intro = _sanitize_rst(intro)
     if len(intro) > 95:
         intro = intro[:95] + '...'
-
     return intro, title
 
 
@@ -709,7 +731,6 @@ def generate_file_rst(fname, target_dir, src_dir, gallery_conf):
             for label, content, line_number in script_blocks
         ]
 
-
     output_blocks, time_elapsed = execute_script(script_blocks,
                                                  script_vars,
                                                  gallery_conf)

--- Proposal A
**## Root cause**

`Sphinx-Gallery` builds the thumbnail hover‑text from the first line of an example’s docstring (or the first paragraph).  
That line is taken verbatim, so any Sphinx cross‑reference markup such as  

```
:func:`~numpy.mean`
```

remains in the generated HTML `title` attribute. Browsers display the raw markup in the tooltip, which is undesirable. The code that creates the hover text never strips or resolves these roles.

**## Fix (a unified diff with your best‑guess file paths and code)**

```diff
--- a/sphinx_gallery/gen_gallery.py
+++ b/sphinx_gallery/gen_gallery.py
@@
 import os
 import re
 import sys
 from pathlib import Path
@@
 from .utils import (  # noqa: F401
     _get_image_path, _get_image_path_from_example,
     _get_image_path_from_thumbnail, _get_thumbnail_path,
     _get_thumbnail_path_from_example, _get_thumbnail_path_from_image,
     _get_thumbnail_path_from_thumbnail, _get_image_path_from_thumbnail,
     _get_image_path_from_image, _get_image_path_from_example,
 )
 
+# ----------------------------------------------------------------------
+# Helper to clean hover‑text generated from example docstrings.
+# ----------------------------------------------------------------------
+def _strip_crossrefs(text: str) -> str:
+    """
+    Remove Sphinx cross‑reference markup from *text*.
+
+    The function recognises the generic ``:role:`target` `` syntax used by
+    Sphinx (e.g. ``:func:`~numpy.mean``, ``:class:`pandas.DataFrame``, etc.).
+    If the target starts with a leading ``~`` the displayed name should be
+    the *short* name (the part after the last dot).  Otherwise the full
+    target is kept.
+
+    Parameters
+    ----------
+    text
+        The raw string possibly containing cross‑reference markup.
+
+    Returns
+    -------
+    str
+        *text* with all cross‑reference markup stripped.
+    """
+    # Pattern matches ``:role:`~?target` `` where ``role`` is any word
+    # characters (func, class, meth, attr, mod, ref, etc.).
+    pattern = re.compile(r':\w+:`(~?)([^`]+)`')
+
+    def _repl(match: re.Match) -> str:
+        leading_tilde, target = match.group(1), match.group(2)
+        if leading_tilde:          # ``~`` present → use short name
+            return target.split('.')[-1]
+        else:                     # no ``~`` → keep the full target
+            return target
+
+    return pattern.sub(_repl, text)
+
@@
 def _example2html(example_path, src_dir, output_dir, thumb_dir,
                   gallery_conf, src_dir_rel, thumb_dir_rel,
                   example_conf, gallery_conf):
@@
-    # The first line of the docstring (or the first paragraph) is used as
-    # the hover text for the thumbnail.
-    hover_text = _get_first_paragraph(example_path)
+    # The first line of the docstring (or the first paragraph) is used as
+    # the hover text for the thumbnail.  Strip any Sphinx cross‑reference
+    # markup so that the tooltip shows a clean, human‑readable string.
+    raw_hover = _get_first_paragraph(example_path)
+    hover_text = _strip_crossrefs(raw_hover)
@@
-    thumb = f'<a href="{example_rel_path}"><img src="{thumb_rel_path}" ' \
-            f'alt="{example_name}" title="{hover_text}" /></a>'
+    thumb = (f'<a href="{example_rel_path}"><img src="{thumb_rel_path}" '
+             f'alt="{example_name}" title="{hover_text}" /></a>')
*** End of File
```

**Explanation of changes**

1. **Added `_strip_crossrefs`** – a small utility that uses a regular expression t
--- Proposal B
## Root cause
The hover text for each example is taken from the first line of the example’s docstring and is inserted verbatim into the HTML `title` attribute.  
When that line contains a Sphinx cross‑reference (e.g. ``:func:`~my_pkg.my_func` ``) the raw markup is shown in the tooltip, because the markup is never rendered when it is used as plain text. The code that builds the thumbnail HTML does not strip or resolve these cross‑references.

## Fix
Add a small utility that removes Sphinx cross‑reference markup from a string, keeping only the displayed name (respecting the optional leading `~`). Apply this utility to the title text before it is written to the HTML.

```diff
--- a/sphinx_gallery/gen_gallery.py
+++ b/sphinx_gallery/gen_gallery.py
@@
 import os
 import sys
 import warnings
+import re
 from pathlib import Path
@@
 from .utils import (  # noqa: F401
     _get_image_path, _get_thumbnail_path, _get_image_path,
     _get_thumbnail_path, _get_image_path, _get_thumbnail_path,
 )
 
+# ----------------------------------------------------------------------
+# Helper to clean hover‑text titles
+# ----------------------------------------------------------------------
+_CROSSREF_RE = re.compile(
+    r""":          # leading colon
+        (?P<role>[a-zA-Z]+)   # the Sphinx role (func, class, meth, attr, etc.)
+        :`                     # colon and opening back‑tick
+        (?P<tilde>~?)          # optional leading tilde
+        (?P<target>[^`]+)      # the target name (module.path.object)
+        `                      # closing back‑tick
+    """,
+    re.VERBOSE,
+)
+
+
+def _strip_cross_refs(text: str) -> str:
+    """
+    Remove Sphinx cross‑reference markup from *text*.
+
+    ``:func:`~pkg.mod.func``` → ``func``
+    ``:class:`pkg.mod.Class``` → ``pkg.mod.Class``
+
+    The function is deliberately permissive – it only looks for the
+    ``:role:`...``` pattern used for hover‑text generation.
+    """
+
+    def _replace(match: re.Match) -> str:
+        target = match.group("target")
+        if match.group("tilde"):
+            # ``~`` means “show only the last component”
+            return target.split(".")[-1]
+        return target
+
+    return _CROSSREF_RE.sub(_replace, text)
+
@@
-        # The title (hover text) is the first line of the docstring.
-        title = example.title
+        # The title (hover text) is the first line of the docstring.
+        # Strip any Sphinx cross‑reference markup so the tooltip shows a
+        # clean, human‑readable name.
+        title = _strip_cross_refs(example.title)
```

**Explanation of changes**

1. **Import `re`** – needed for the regular expression.
2. **Define `_CROSSREF_RE`** – matches the generic ``:role:`~?target` `` pattern used by Sphinx for cross‑references.
3. **Implement `_strip_cross_refs`** – replaces each match with either the full target (no leading `~`) or just the last component of the target (when `~` is present), mimicking Sphinx’s rendering rules.
4. **Apply the helper** when constructing the thumbnail HTML (`title = _strip_cross_refs(example.title)`), ensuring the tooltip no longer contains raw markup.

## How to verify
1. **Build the documentation** (e.g. `python -m sphinx -b html docs build`).
2. Open the generated gallery page in a browser.
3. Hover over a thumbnail whose first docstring line contains a cross‑reference such as  
   ``:func:`~my_pkg.my_module.my_func` ``.
4. The tooltip should now display only `my_func` (or the full target if th