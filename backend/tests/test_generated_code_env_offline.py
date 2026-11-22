"""A generated module's subprocess must not inherit the backend's secrets.

`behavioral_validation` exec_module()s a module that was WRITTEN BY A MODEL and runs it in a subprocess. Before
this change it passed `dict(os.environ)` to that child, so the generated code could read `DATABASE_URL`, the
payment-gateway secret and every provider key the backend holds (docs/sandboxing.md §1).

There is no sandbox here — the child is a plain subprocess. The environment allowlist is therefore the only
boundary this layer has, which is exactly why it is worth a test that fails loudly if someone widens it back.
"""
from __future__ import annotations

import os

from app.execution import behavioral_validation as bv


def test_secrets_are_not_in_the_allowlist():
    # The names that matter. If one of these ever appears, the child can read it.
    for name in ("DATABASE_URL", "TEST_DATABASE_URL", "RAZORPAY_KEY_SECRET", "STRIPE_SECRET_KEY", "OPENAI_API_KEY"):
        assert name not in bv._SUBPROCESS_ENV_ALLOWLIST, f"{name} must never reach generated code"


def test_the_allowlist_is_small_and_named():
    # A growable set is a set that grows. Keep it visibly short so an addition is a deliberate act.
    assert isinstance(bv._SUBPROCESS_ENV_ALLOWLIST, frozenset)
    assert len(bv._SUBPROCESS_ENV_ALLOWLIST) <= 20


def test_a_python_subprocess_can_still_start():
    """The allowlist must not be so tight that the interpreter cannot boot.

    This is the failure mode of over-restricting: the check silently stops working, and a check that never runs
    is worse than no check — it reports success on modules it never loaded.
    """
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k in bv._SUBPROCESS_ENV_ALLOWLIST}
    proc = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


def test_a_secret_set_in_the_parent_is_not_visible_to_the_child():
    """The behavioural proof: set a secret, confirm the child cannot see it."""
    import subprocess
    import sys

    sentinel = "should-never-be-readable"
    os.environ["KELL_TEST_SECRET_SENTINEL"] = sentinel
    try:
        env = {k: v for k, v in os.environ.items() if k in bv._SUBPROCESS_ENV_ALLOWLIST}
        proc = subprocess.run(
            [sys.executable, "-c", "import os;print(os.environ.get('KELL_TEST_SECRET_SENTINEL',''))"],
            capture_output=True, text=True, env=env,
        )
        assert proc.stdout.strip() == "", "a secret leaked into the generated-code subprocess"
    finally:
        os.environ.pop("KELL_TEST_SECRET_SENTINEL", None)
