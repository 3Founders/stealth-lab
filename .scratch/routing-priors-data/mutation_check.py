"""Mutation check (securityp1.md §5.4): re-introduce the old fail-open instance_key guard, show the tests catch it,
restore the file. Run from backend/."""
import shutil
import subprocess
import sys
from pathlib import Path

plan = Path("app/routing/plan.py")
backup = plan.with_suffix(".py.mutation-bak")
shutil.copy(plan, backup)
try:
    s = plan.read_text(encoding="utf-8")
    good = "    if not scope.is_unrestricted and owner != (str(scope.viewer_id) if scope.viewer_id is not None else None):\n"
    bad = "    if owner is not None and not scope.is_unrestricted and scope.viewer_id != owner:   # MUTATION: old fail-open\n"
    assert s.count(good) == 1
    plan.write_text(s.replace(good, bad), encoding="utf-8")
    env_args = [sys.executable, "scripts/run_isolation_suites.py", "--dsn",
                "postgresql://stealth_app@127.0.0.1:55432/kel_v2_isolation", "--only",
                "tests/test_tenant_isolation_p1a_e2e.py"]
    print("--- with the guard REMOVED (expect failures):")
    print(subprocess.run(env_args, capture_output=True, text=True).stdout)
    off = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "tests/test_routing_plan_offline.py", "-k", "nobody"], capture_output=True, text=True)
    print("offline plan test:", [ln for ln in off.stdout.splitlines() if "passed" in ln or "failed" in ln][-1:])
finally:
    shutil.move(backup, plan)
print("--- restored:", "OK" if "MUTATION" not in plan.read_text(encoding="utf-8") else "NOT RESTORED")
