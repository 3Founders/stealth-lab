"""Files a Way produces (app/submissions/artifacts.py, docs/procedure_outputs.md).

Measured 2026-11-22: a run stored text only. A step saying "write this into literaturereview.md" produced a
paragraph describing a file, and the buyer received the description. These tests pin the three things that make the
fix safe:

  1. **A truncated body is refused, not stored.** `max_tokens` cuts mid-sentence; that is not a `.tex` file.
  2. **A name from a stranger cannot escape the download directory** or be rendered as HTML.
  3. **A multi-file deliverable arrives as one archive** without one file overwriting another.
"""
from __future__ import annotations

import asyncio
import io
import zipfile

from app.submissions import artifacts as art
from app.submissions.sandbox import ScriptResult


class FakeStore:
    """The object_storage.ObjectStore interface, in memory.

    Returns **(sha256, locator)** — the order every real store uses (MemoryStore, LocalFileStore, S3Store). A fake
    that returned them the other way round is how a reversed unpack survived 31 tests once.
    """

    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, str]] = {}

    async def put(self, data: bytes, *, content_type: str = "application/octet-stream") -> tuple[str, str]:
        sha = art.digest(data)
        locator = f"s3://bucket/{sha}"
        self.blobs[locator] = (data, content_type)
        return sha, locator

    async def get(self, locator: str) -> bytes:
        if locator not in self.blobs:
            raise KeyError(locator)
        return self.blobs[locator][0]


def _store() -> FakeStore:
    return FakeStore()


# --- declared produces: a model step's text becomes its file -----------------------------------------------------

def test_a_declared_step_writes_its_file():
    store = _store()
    step = {"order": 1, "produces": "literaturereview.md"}
    made, refusal = asyncio.run(art.artifacts_from_step_output(
        text="# Review\n\nSome prose.", step=step, order=1, store=store))
    assert refusal is None
    assert [a.name for a in made] == ["literaturereview.md"]
    assert made[0].media_type == "text/markdown"
    assert made[0].text is True


def test_a_step_that_declares_nothing_writes_nothing():
    """Every existing step keeps working exactly as it did: prose in, no file."""
    made, refusal = asyncio.run(art.artifacts_from_step_output(
        text="just an answer", step={"order": 1}, order=1, store=_store()))
    assert made == [] and refusal is None


def test_the_bytes_are_the_text_not_a_description_of_it():
    store = _store()
    body = "# Review\n\nThe actual content."
    made, _ = asyncio.run(art.artifacts_from_step_output(
        text=body, step={"order": 1, "produces": "r.md"}, order=1, store=store))
    assert asyncio.run(store.get(made[0].object_key)) == body.encode()


def test_a_fenced_reply_stores_the_body_without_the_fence():
    """Models habitually wrap a file in ```; the fence is not part of the .tex."""
    store = _store()
    text = "```latex\n\\documentclass{article}\n\\begin{document}hi\\end{document}\n```"
    made, _ = asyncio.run(art.artifacts_from_step_output(
        text=text, step={"order": 1, "produces": "main.tex"}, order=1, store=store))
    stored = asyncio.run(store.get(made[0].object_key)).decode()
    assert "```" not in stored
    assert stored.startswith("\\documentclass")


def test_a_reply_that_is_prose_around_a_fence_is_not_stripped():
    """Only a reply that IS a file gets unwrapped; prose with an example must not lose its example."""
    prose = "Here is the file:\n```\nx\n```\nHope that helps."
    assert art.strip_fence(prose) == prose


# --- refusal: a body that is not a usable file ----------------------------------------------------------------

def test_a_truncated_reply_is_refused_rather_than_stored():
    step = {"order": 1, "produces": "main.tex", "output_cap_tokens": 100}
    made, refusal = asyncio.run(art.artifacts_from_step_output(
        text="x" * 400, step=step, order=1, store=_store()))
    assert made == []
    assert refusal and "cap" in refusal


def test_an_unclosed_code_fence_is_read_as_truncation():
    why = art.looks_truncated("```tex\n\\section{Intro}\n")
    assert why and "fence" in why


def test_an_empty_step_output_is_refused():
    assert art.looks_truncated("   ") is not None


def test_a_healthy_body_passes():
    assert art.looks_truncated("# Review\n\nComplete.") is None


# --- untrusted names -------------------------------------------------------------------------------------------

def test_a_name_cannot_escape_the_download_directory():
    assert art.safe_name("../../etc/passwd") == "passwd"
    assert art.safe_name("..\\..\\windows\\system32\\cmd.exe") == "cmd.exe"


def test_a_dotfile_or_dotdot_becomes_a_safe_default():
    assert art.safe_name("..") == "output.txt"
    assert art.safe_name("") == "output.txt"


def test_a_name_cannot_smuggle_markup():
    """A stranger names their output; the name must not carry `<script>` into a header or the served filename."""
    for hostile in ("<script>alert(1)</script>.md", "a<b>c</b>.tex", "x\"onload=y.html"):
        got = art.safe_name(hostile)
        assert "<" not in got and ">" not in got and '"' not in got, got
        assert got.endswith((".md", ".tex", ".html")), got


def test_a_space_in_a_name_does_not_break_the_header():
    assert art.safe_name("main tex.tex") == "main-tex.tex"


def test_html_is_forced_to_download_never_rendered():
    assert art.content_disposition("x.html").startswith("attachment;")
    assert 'filename="x.html"' in art.content_disposition("x.html")


def test_an_unknown_extension_is_octet_stream():
    """The safe default: a browser downloads a type it cannot render."""
    assert art.media_type_for("thing.xyz") == "application/octet-stream"


# --- producing several files -----------------------------------------------------------------------------------

def test_a_step_may_declare_several_files():
    names = art.produced_names({"produces": ["main.tex", "refs.bib"]})
    assert names == ["main.tex", "refs.bib"]


def test_a_declared_produces_object_is_accepted():
    assert art.produced_names({"produces": {"name": "report.pdf"}}) == ["report.pdf"]


def test_the_zip_holds_every_file():
    data = art.zip_artifacts([("main.tex", b"\\documentclass{article}"), ("refs.bib", b"@book{a}")])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert sorted(z.namelist()) == ["way-output/main.tex", "way-output/refs.bib"]
    assert art.media_type_for("out.zip") == "application/zip"


def test_two_steps_writing_the_same_name_keep_both_in_the_zip():
    """Otherwise the archive silently loses one of a buyer's files."""
    data = art.zip_artifacts([("notes.md", b"first"), ("notes.md", b"second")])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = sorted(z.namelist())
        assert names == ["way-output/notes-2.md", "way-output/notes.md"]
        assert z.read("way-output/notes.md") == b"first"
        assert z.read("way-output/notes-2.md") == b"second"


def test_put_artifact_stores_the_locator_not_the_digest():
    """Every ObjectStore returns (sha256, locator). Reversing them stores a bare hex digest as the object key, so
    the file is written and every later download fails — a bug that only shows up on a real download."""
    store = _store()
    a = asyncio.run(art.put_artifact(b"hello", name="x.md", order=1, store=store))
    assert a.object_key.startswith("s3://bucket/"), a.object_key
    assert a.sha256 == art.digest(b"hello")
    assert asyncio.run(store.get(a.object_key)) == b"hello", "the stored key must actually resolve"


def test_a_file_over_the_size_limit_is_refused():
    try:
        asyncio.run(art.put_artifact(b"x" * (art.MAX_ARTIFACT_BYTES + 1), name="big.bin", order=1, store=_store()))
    except ValueError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("an oversized file must not be stored")


# --- the script path: real bytes written inside the sandbox -----------------------------------------------------

def test_a_script_that_wrote_nothing_returns_a_plain_result():
    """No `produces` means the script path is byte-for-byte what it was."""
    out, files = art.parse_captured("hello\nworld")
    assert out == "hello\nworld" and files == []


def test_captured_files_come_back_out_of_stdout_as_bytes():
    """Agent37's exec returns only stdout, so a file has to travel through it — base64, binary-safe."""
    import base64
    import json

    payload = json.dumps([{"name": "chart.png", "b64": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()}])
    visible, files = art.parse_captured("made 1 file\nKELL_ARTIFACTS:" + payload)
    assert visible.rstrip() == "made 1 file"
    assert files == [("chart.png", b"\x89PNG\r\n\x1a\n")]


def test_a_corrupt_capture_block_drops_that_file_not_the_step():
    visible, files = art.parse_captured("done\nKELL_ARTIFACTS:[{\"name\": \"a.md\", \"b64\": \"!!!not-base64\"}]")
    assert visible.rstrip() == "done" and files == []


def test_a_captured_name_is_still_sanitised():
    import base64
    import json

    payload = json.dumps([{"name": "../../x.sh", "b64": base64.b64encode(b"echo hi").decode()}])
    _, files = art.parse_captured("\nKELL_ARTIFACTS:" + payload)
    assert files[0][0] == "x.sh"


def test_the_capture_tail_asks_only_for_declared_files():
    tail = art.capture_script(produced=["main.tex"], order=1)
    assert "main.tex" in tail and "KELL_ARTIFACTS" in tail
    assert art.OUT_DIR == "/out"


def test_script_result_shape_is_unchanged():
    """The new `produces` argument must not alter what an existing caller receives."""
    r = ScriptResult(0, "out", "")
    assert (r.exit_code, r.stdout, r.stderr) == (0, "out", "")


# --- imports into the sandbox: the literature.md -> .tex case ---------------------------------------------

def test_an_imported_file_is_written_into_out_before_the_step_runs():
    from app.submissions.sandbox import _materialise_agent37_command

    cmd = _materialise_agent37_command({"literaturereview.md": "# Review\n\nBody."})
    assert "/out/literaturereview.md" in cmd
    assert "base64 -d" in cmd, "binary inputs must survive a text channel"
    assert cmd.startswith("mkdir -p /out")


def test_a_large_import_is_chunked_under_the_request_cap():
    """Agent37 caps an exec request body at 1 MB; a 2 MB import must not be sent as one command."""
    from app.submissions.sandbox import _materialise_agent37_command

    big = "x" * 3_000_000
    cmd = _materialise_agent37_command({"big.md": big})
    # 3 MB base64s to ~4 MB, so it must arrive as several appends rather than one.
    assert cmd.count("base64 -d >>") >= 4
    assert all(len(part) < 1_000_000 for part in cmd.split(" && "))


def test_an_import_round_trips_byte_for_byte():
    """The encoding is the whole risk: a corrupted .tex compiles wrong or not at all."""
    import base64
    import re

    from app.submissions.sandbox import _materialise_agent37_command

    body = "\\documentclass{article}\n\\begin{document}\nnäive — 日本語\n\\end{document}\n"
    cmd = _materialise_agent37_command({"main.tex": body})
    pieces = re.findall(r"printf '%s' '([A-Za-z0-9+/=]+)' \| base64 -d", cmd)
    assert base64.b64decode("".join(pieces)).decode() == body


def test_an_import_name_cannot_walk_out_of_out():
    from app.submissions.sandbox import _safe_relpath

    assert _safe_relpath("../../etc/passwd").startswith("etc/passwd")


def test_a_path_escaping_out_is_refused():
    from app.submissions.sandbox import _safe_relpath

    assert _safe_relpath("a/b.md") == "a/b.md"
    try:
        _safe_relpath("..")
    except Exception:
        pass
    else:
        raise AssertionError("a name with no legal path must be refused, not silently kept")

