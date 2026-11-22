"""
The files a Way produces, kept so the person who paid can download them.

Measured 2026-11-22: a run stored only text. A step that says "write this into literaturereview.md" produced a
paragraph *describing* a file, and the buyer received the description. Nothing anywhere wrote or served bytes: the
sandbox returns only `stdout`, and its instance is deleted in a `finally` before anyone could ask for a file.

A Way's deliverable is usually a file — a `.tex`, a `.md`, a PDF, a set of source files — so that is two separate
gaps, and both are fixed here:

1. **Declared `produces`** (model/script steps). A step names what it makes. The step's own output *is* the file's
   body, so no sandbox is needed and every existing step becomes downloadable by declaring one field.
2. **Captured from the sandbox** (`script` / `tool` steps). A script writes into `$KELL_OUT`; kell reads those files
   back over the same exec channel *before* the instance is deleted, and the step's text becomes a summary of what
   it wrote rather than the whole file.

Two rules that are easy to get wrong and expensive to get wrong:

- **A truncated body is not a file.** `max_tokens` cuts a model's output mid-sentence; writing that to `.tex` gives
  the buyer a file that will not compile. `looks_truncated()` refuses to store it, and the step reports why.
- **Bytes go to object storage; Neon keeps the pointer** (`docs/procedure_outputs.md` §0). This module stores no
  bytes in Postgres: it writes the blob through `services/object_storage.py` and returns a 300-byte locator.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

# Extension -> media type. Anything unlisted is served as octet-stream, which downloads rather than renders —
# the safe default for a file kell did not write itself.
MEDIA_TYPES: dict[str, str] = {
    ".md": "text/markdown", ".txt": "text/plain", ".tex": "application/x-tex", ".bib": "application/x-bibtex",
    ".pdf": "application/pdf", ".json": "application/json", ".csv": "text/csv", ".py": "text/x-python",
    ".js": "text/javascript", ".ts": "text/typescript", ".html": "text/html", ".css": "text/css",
    ".yaml": "application/yaml", ".yml": "application/yaml", ".toml": "application/toml", ".zip": "application/zip",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml",
}

# A single artifact is capped well below the object store's hard limit: a Way's deliverable is a document, not a
# dataset, and a runaway step must not be able to fill a bucket.
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_ARTIFACTS_PER_STEP = 20
MAX_ARTIFACTS_PER_RUN = 60

# Where a script writes the files it wants to hand back. Matches the `printf`-into-exec pattern in sandbox.py.
OUT_DIR = "/out"


@dataclass(frozen=True)
class Artifact:
    """One file a run produced. `object_key` is a pointer; the bytes are in the bucket."""

    name: str
    media_type: str
    size_bytes: int
    sha256: str
    object_key: str
    order: int                       # the step that made it
    text: bool                       # safe to show inline on the page

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "media_type": self.media_type, "size_bytes": self.size_bytes,
                "sha256": self.sha256, "object_key": self.object_key, "order": self.order, "text": self.text}


def media_type_for(name: str) -> str:
    _, ext = posixpath.splitext(name.lower())
    return MEDIA_TYPES.get(ext, "application/octet-stream")


def safe_name(name: str) -> str:
    """A filename that cannot escape the download directory or be read as HTML by a browser.

    A step author is a stranger (contributions are open), so the name is untrusted input: strip directories, refuse
    `..`, drop anything that is not a conservative filename character, and never return an empty name.
    """
    base = posixpath.basename(str(name or "").replace("\\", "/")).strip()
    if base in ("", ".", "..") or base.startswith("-"):
        return "output.txt"
    # Take the extension only when it really is one (`.md`, `.tex`): `splitext` on `<script>x</script>.md` splits at
    # the dot inside the tag, which would keep the tag as part of the "extension".
    m = re.search(r"\.([A-Za-z0-9]{1,10})$", base)
    ext = m.group(1).lower() if m else ""
    stem = base[: -len(ext) - 1] if ext else base
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")
    if not cleaned:
        return f"output.{ext}" if ext else "output.txt"
    return f"{cleaned[:100]}{'.' + ext if ext else ''}"


def looks_truncated(text: str, *, output_cap_tokens: Optional[int] = None) -> Optional[str]:
    """Why this body cannot be trusted as a file, or None when it can.

    The two failure modes are both silent today: the model hit `max_tokens` (so the file ends mid-sentence), or the
    model wrapped the file in a code fence and the fence never closed (so the body is prose *about* a file).
    """
    if not text or not text.strip():
        return "the step produced no text to write"
    stripped = text.strip()
    # An unclosed fence means the closing fence was cut off — a reliable truncation signal.
    if stripped.count("```") % 2 == 1:
        return "the output ends inside an unclosed code fence (the reply was cut off)"
    if output_cap_tokens and len(stripped) // 4 >= output_cap_tokens * 0.95:
        return f"the output used its whole {output_cap_tokens}-token cap (the reply was probably cut off)"
    return None


def strip_fence(text: str) -> str:
    """The body of a fenced block when the whole reply is one fenced block, else the reply as-is."""
    stripped = text.strip()
    if not stripped.startswith("```") or stripped.count("```") < 2:
        return text
    first_end = stripped.find("\n")
    last = stripped.rfind("```")
    if first_end == -1 or last <= first_end:
        return text
    if stripped[last + 3:].strip():
        return text          # prose after the fence: the reply is not just a file
    return stripped[first_end + 1:last]


async def put_artifact(data: bytes, *, name: str, order: int, store: Any) -> Artifact:
    """Write one file to the object store (never to Postgres) and return its pointer."""
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError(f"{name} is {len(data) // 1024} KB, over the {MAX_ARTIFACT_BYTES // 1024} KB limit")
    name = safe_name(name)
    media_type = media_type_for(name)
    # Every ObjectStore returns (sha256, locator) — NOT (locator, sha). Getting this backwards stores a bare hex
    # digest as the object key, so every download later fails with "object not found" on a file that was written.
    # Pinned by test_put_artifact_stores_the_locator_not_the_digest.
    sha, locator = await store.put(data, content_type=media_type)
    return Artifact(name=name, media_type=media_type, size_bytes=len(data), sha256=sha,
                    object_key=locator, order=order, text=media_type.startswith("text/") or media_type in
                    ("application/json", "application/x-tex", "application/x-bibtex", "application/yaml"))


def produced_names(step: Mapping[str, Any]) -> list[str]:
    """What a step declares it makes. Absent = the step's output is prose, as it was before artifacts existed."""
    produces = step.get("produces")
    out: list[str] = []
    if isinstance(produces, str) and produces.strip():
        out.append(safe_name(produces))
    elif isinstance(produces, Mapping):
        n = produces.get("name")
        if isinstance(n, str) and n.strip():
            out.append(safe_name(n))
    elif isinstance(produces, Iterable):
        for p in produces:
            if isinstance(p, str) and p.strip():
                out.append(safe_name(p))
            elif isinstance(p, Mapping) and isinstance(p.get("name"), str):
                out.append(safe_name(p["name"]))
    return [n for n in dict.fromkeys(out)][:MAX_ARTIFACTS_PER_STEP]


async def artifacts_from_step_output(
    *, text: str, step: Mapping[str, Any], order: int, store: Any,
) -> tuple[list[Artifact], Optional[str]]:
    """Turn a declared step's reply into the file it asked for.

    Returns `(artifacts, refusal)`. A refusal is not an error in the run: the step's text is still a real result,
    it simply could not be trusted as a file, and saying so beats shipping a `.tex` that will not compile.
    """
    names = produced_names(step)
    if not names or store is None:
        return [], None
    cap = step.get("output_cap_tokens")
    why = looks_truncated(text, output_cap_tokens=cap if isinstance(cap, int) else None)
    if why:
        return [], why

    body = strip_fence(text)
    made: list[Artifact] = []
    for name in names:
        try:
            made.append(await put_artifact(body.encode("utf-8"), name=name, order=order, store=store))
        except Exception as exc:  # noqa: BLE001 -- a storage fault must not lose the step's text
            return made, f"{name} could not be stored: {exc}"
    return made, None


def zip_artifacts(files: Iterable[tuple[str, bytes]], *, root: str = "way-output") -> bytes:
    """One download for a multi-file deliverable (main.tex + literaturereview.md + refs.bib)."""
    buf = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            safe = safe_name(name)
            # Two steps can legitimately produce the same filename; the archive must keep both.
            if safe in seen:
                stem, dot, ext = safe.partition(".")
                n = 2
                while f"{stem}-{n}{dot}{ext}" in seen:
                    n += 1
                safe = f"{stem}-{n}{dot}{ext}"
            seen.add(safe)
            z.writestr(f"{root}/{safe}", data)
    return buf.getvalue()


def capture_script(*, produced: Iterable[str], order: int) -> str:
    """The shell tail appended to a script so its files come back over exec as `stdout`.

    Agent37's exec returns only `stdout`, so the file bytes have to travel through it. Each file is emitted as a
    length-prefixed block, base64'd so binary (a PDF, a PNG) survives a text channel.
    """
    import base64  # local: this function is the only place the encoding is needed

    lines = ["import base64, json, os, pathlib",
             "out = []",
             f"for rel in {list(map(str, produced))!r}:",
             f"    p = pathlib.Path({OUT_DIR!r}) / rel",
             "    if p.is_file():",
             "        out.append({'name': p.name, 'b64': base64.b64encode(p.read_bytes()).decode()})",
             "print('\\nKELL_ARTIFACTS:' + json.dumps(out))"]
    return "\n".join(lines)


def parse_captured(stdout: str) -> tuple[str, list[tuple[str, bytes]]]:
    """Split a script's `stdout` into (what it printed, the files it wrote)."""
    import base64

    marker = "\nKELL_ARTIFACTS:"
    idx = stdout.find(marker)
    if idx == -1:
        return stdout, []
    visible, payload = stdout[:idx], stdout[idx + len(marker):].strip()
    import json

    try:
        rows = json.loads(payload.splitlines()[0] if payload else "[]")
    except ValueError:
        return visible, []
    files: list[tuple[str, bytes]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        try:
            data = base64.b64decode(row.get("b64") or "", validate=True)
        except Exception:  # noqa: BLE001 -- a corrupt block drops that file, not the step
            continue
        if len(data) <= MAX_ARTIFACT_BYTES:
            files.append((safe_name(row["name"]), data))
        if len(files) >= MAX_ARTIFACTS_PER_STEP:
            break
    return visible, files


def content_disposition(name: str) -> str:
    """`attachment` forces a download. Without it a `.html` output renders and can run script on our origin."""
    safe = safe_name(name)
    return f'attachment; filename="{safe}"'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
