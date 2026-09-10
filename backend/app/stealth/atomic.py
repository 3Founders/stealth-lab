"""
Atomic filesystem writes for the `.stealth/` projection.

`atomic_write` is the same write-temp -> fsync -> `os.replace` pattern
that `app.execution.stealth_projection._atomic_write` has always used
(POSIX + Windows atomic rename); it is centralised here so the multi-file
generator and the shim share one implementation.

`atomic_write_batch` writes a whole projection: every file is staged to a
`.tmp-stealth-*` sibling and fsync'd, then the renames are applied in the
caller's order. This is NOT a cross-file transaction -- the OS gives no
such guarantee for independent paths -- but no reader ever observes a
half-written file, and callers regenerate `meta.json` LAST so a reader
that keys off `meta.json.projection_revision` only ever sees it once the
rest of the set is already in place.
"""
from __future__ import annotations

import os
import tempfile
from typing import Iterable, Tuple


def atomic_write(path: str, content: str) -> None:
    """Write `content` to `path` atomically, creating parent dirs."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-stealth-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_batch(files: Iterable[Tuple[str, str]]) -> list[str]:
    """
    Stage every `(path, content)` to a temp sibling (each fsync'd), then
    apply the renames in iteration order. Returns the list of final
    paths. On any failure before the rename phase, every staged temp
    file is removed and nothing is published; a failure *during* the
    rename phase leaves the already-renamed files in place (the caller's
    ordering -- data files first, `meta.json` last -- makes that safe to
    observe) and re-raises.
    """
    staged: list[tuple[str, str]] = []  # (tmp_path, final_path)
    try:
        for path, content in files:
            directory = os.path.dirname(path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-stealth-")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            staged.append((tmp_path, path))
    except BaseException:
        for tmp_path, _ in staged:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise

    finals: list[str] = []
    for tmp_path, final_path in staged:
        os.replace(tmp_path, final_path)
        finals.append(final_path)
    return finals
