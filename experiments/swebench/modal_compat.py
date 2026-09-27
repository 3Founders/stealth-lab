"""Make the official harness's Modal path run again (swebench 5.0.2 + modal >= 1.5.3). Grading logic untouched.

    python modal_compat.py            # apply (idempotent); prints the patched file's sha256
    python modal_compat.py --check    # exit 1 unless the patch is applied

Transport-level fixes to swebench/harness/modal_eval/run_evaluation_modal.py, nothing else:

1. write_file: Modal removed the legacy Sandbox filesystem API server-side (`sandbox.open(...)` ->
   FAILED_PRECONDITION "The legacy Sandbox filesystem API is no longer supported"). Every released swebench
   still calls it. Replaced with the documented successor `sandbox.filesystem.write_text(data, path)`.
2. get_instance_image: 5.0.2 still reads `test_spec.setup_env_script`, which 5.x removed from TestSpec
   (its own TODO says so), so every sandbox failed to build. Replaced with the instance's official prebuilt
   SWE-bench eval image (`test_spec.image`, e.g. swebench/sweb.eval.x86_64.<id>) -- the same image the local
   Docker harness runs -- plus a Python for Modal's exec entrypoint, workdir /testbed.

3. Results: the remote function returned `TestOutput.log_dir` as a Linux PosixPath, which cannot be unpickled on
   a Windows client ("Encountered an error when deserializing"), so every result -- including resolved ones --
   was dropped before report.json was saved. The remote side now returns it as a str, and the client saves the
   logs under the log dir it computes itself (the same `get_log_dir` path).

4. SWE-rebench images (swerebench/*) install the repo editable from /<name> but ship it at /testbed, so the
   package does not import on Modal (gold check: ModuleNotFoundError for dask/briefcase/pennylane). Each missing
   editable root is symlinked to /testbed at image build; Verified images are untouched.

The patch applied, the eval script, the log parser and report.json are the harness's own. Logged as a
deviation (DEVIATIONS.md 8); check_env.py refuses Modal grading unless this is applied.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

MARK = "# kel-modal-compat v3"

OLD_WRITE = '''    def write_file(self, file_path: str, content: str):
        self.sandbox.open(file_path, "w").write(content)'''
NEW_WRITE = f'''    def write_file(self, file_path: str, content: str):  {MARK}
        self.sandbox.filesystem.write_text(content, file_path)'''

OLD_IMAGE_HEAD = '''    @staticmethod
    def get_instance_image(test_spec: TestSpec) -> modal.Image:'''
# SWE-rebench images install the repo editable from /<name> but ship it at /testbed: link each missing
# absolute root named in the testbed env's editable finders / .pth files to /testbed (never overwrites).
REBENCH_LINK_SH = """\
for f in /opt/conda/envs/testbed/lib/python3*/site-packages/__editable__*finder.py \\
         /opt/conda/envs/testbed/lib/python3*/site-packages/*.pth; do
  [ -f "$f" ] || continue
  for d in $(grep -oE "['\\" ]?/[A-Za-z0-9_.-]+" "$f" | tr -d "'\\" " | sort -u); do
    [ -e "$d" ] || ln -s /testbed "$d"
  done
done
true
"""
_B64 = __import__("base64").b64encode(REBENCH_LINK_SH.encode()).decode()

NEW_IMAGE = f'''    @staticmethod
    def get_instance_image(test_spec: TestSpec) -> modal.Image:  {MARK}
        image = modal.Image.from_registry(test_spec.image, add_python="3.11")
        if test_spec.image.startswith("swerebench/"):
            # SWE-rebench images install the repo editable from /<name> but ship it at /testbed; link each
            # missing editable root to /testbed so imports resolve to the code the patch is applied to.
            # (script: experiments/swebench/modal_compat.py REBENCH_LINK_SH, base64 to avoid quoting)
            image = image.run_commands("echo {_B64} | base64 -d | bash")
        return image.workdir("/testbed/")

    @staticmethod
    def _unused_legacy_get_instance_image(test_spec: TestSpec) -> modal.Image:'''

OLD_CGROUP = '''        self.write_file("/sys/fs/cgroup/cpu/cpu.shares", "2048")'''
NEW_CGROUP = '''        try:  # pylint cpu-shares hack; the cgroup v1 path does not exist on every host
            self.write_file("/sys/fs/cgroup/cpu/cpu.shares", "2048")
        except Exception:  # noqa: BLE001
            pass'''


OLD_RETURN_DIR = "            log_dir=log_dir,\n"
NEW_RETURN_DIR = "            log_dir=str(log_dir),\n"
OLD_LOCAL_DIR = "                    log_dir = result.log_dir\n"
NEW_LOCAL_DIR = "                    log_dir = get_log_dir(predictions[result.instance_id], run_id, result.instance_id)\n"


def target() -> Path:
    from importlib.metadata import version

    if version("swebench") != "5.0.2":
        raise SystemExit(f"modal_compat targets swebench 5.0.2, found {version('swebench')}")
    import swebench

    return Path(swebench.__file__).parent / "harness" / "modal_eval" / "run_evaluation_modal.py"


def applied(path: Path | None = None) -> bool:
    try:
        return MARK in (path or target()).read_text(encoding="utf-8")
    except (SystemExit, Exception):  # noqa: BLE001
        return False


def apply() -> str:
    path = target()
    src = path.read_text(encoding="utf-8")
    if MARK not in src:
        if "# kel-modal-compat v" in src:
            raise SystemExit("an older modal_compat patch is applied: pip install --force-reinstall --no-deps "
                             "swebench==5.0.2, then re-run")
        for old, new, count in ((OLD_WRITE, NEW_WRITE, 1), (OLD_IMAGE_HEAD, NEW_IMAGE, 1), (OLD_CGROUP, NEW_CGROUP, 1),
                                (OLD_RETURN_DIR, NEW_RETURN_DIR, 3), (OLD_LOCAL_DIR, NEW_LOCAL_DIR, 1)):
            if src.count(old) != count:
                raise SystemExit(f"unexpected harness source; anchor not found {count}x:\n{old}")
            src = src.replace(old, new)
        path.write_text(src, encoding="utf-8")
        for pyc in path.parent.glob("__pycache__/run_evaluation_modal*.pyc"):
            pyc.unlink()
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    if "--check" in sys.argv:
        ok = applied()
        print("modal compat patch applied" if ok else "modal compat patch NOT applied")
        raise SystemExit(0 if ok else 1)
    print("patched", target(), "sha256", apply())
