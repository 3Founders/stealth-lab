"""
Shell command implementations.

    {"template": "pdftotext {pdf_path} {out_path}", "timeout_s": 60,
     "outputs": {"text_path": "{out_path}"}}

Two properties are load-bearing:

  **No shell.** `subprocess` with a list argv and `shell=False`. The template
  comes out of the database, and a database value reaching `/bin/sh` is the
  same class of mistake as string-concatenating SQL.

  **Split before substituting.** The template is tokenised first, then each
  token has its placeholders filled. A value containing spaces therefore
  stays exactly one argv element and cannot inject additional arguments --
  substituting first and splitting after would make `pdf_path` of
  `"x.pdf --output /etc/passwd"` into three tokens.

The working directory is the per-run temp directory, so a command that writes
relative paths writes them somewhere the run owns.
"""
from __future__ import annotations

import asyncio
import re
import shlex
from pathlib import Path
from typing import Any, Mapping

from app.runners.base import RunContext, RunnerError, RunnerResult

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
DEFAULT_TIMEOUT_S = 60


def substitute(token: str, values: Mapping[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise RunnerError(
                f"command template references '{{{name}}}', which is neither an input "
                f"nor a known placeholder (have: {', '.join(sorted(values)) or 'nothing'})"
            )
        return str(values[name])

    return _PLACEHOLDER.sub(replace, token)


class CommandRunner:
    kind = "command"

    async def run(
        self, spec: dict[str, Any], inputs: dict[str, Any], ctx: RunContext
    ) -> RunnerResult:
        template = spec.get("template")
        if not template:
            raise RunnerError("command implementation has no 'template'")

        values: dict[str, Any] = {**inputs, "workdir": str(ctx.workdir)}
        argv = [substitute(tok, values) for tok in shlex.split(template)]
        if not argv:
            raise RunnerError("command template is empty after tokenisation")

        timeout = float(spec.get("timeout_s") or DEFAULT_TIMEOUT_S)

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(ctx.workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RunnerError(f"command not found: {argv[0]}") from exc
        except OSError as exc:
            raise RunnerError(f"could not start {argv[0]}: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RunnerError(f"command timed out after {timeout}s: {argv[0]}") from exc

        out = stdout.decode("utf-8", "replace")
        err = stderr.decode("utf-8", "replace")

        if process.returncode != 0:
            raise RunnerError(
                f"{argv[0]} exited {process.returncode}: {err.strip() or out.strip()}"
            )

        output: dict[str, Any] = {
            "stdout": out,
            "stderr": err,
            "returncode": process.returncode,
        }
        for key, path_template in (spec.get("outputs") or {}).items():
            resolved = substitute(str(path_template), values)
            output[key] = str(Path(ctx.workdir, resolved).resolve())

        return RunnerResult(output=output)
