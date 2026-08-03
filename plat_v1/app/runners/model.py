"""
Model implementations.

    {"model": "claude-opus-5", "system": "...", "output_schema": {...},
     "user_template": "Map these rows: {typed_grid}"}

Structured outputs, not JSON parsed out of prose. `output_config.format`
constrains the response to the schema at generation time, so the failure mode
where a model wraps valid JSON in "Here's the mapping:" and a regex misses it
simply does not exist. That failure is the single most likely thing to break
in a chain like this and there is no reason to inherit it in new code.

Streaming with `get_final_message()` rather than a plain create: `max_tokens`
is generous here, and a non-streaming request at that size risks an HTTP
timeout that looks like a model failure and isn't.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Mapping

from app.config import settings
from app.runners.base import RunContext, RunnerError, RunnerResult
from app.runners.command import substitute

log = logging.getLogger(__name__)

# First release whose non-beta messages resource accepts `output_config`.
MIN_SDK_FOR_STRUCTURED_OUTPUTS = "0.77"


@lru_cache(maxsize=1)
def assert_structured_outputs_supported() -> None:
    """
    Fail with the actual problem rather than a bare TypeError.

    An SDK too old for `output_config` raises "unexpected keyword argument",
    which reads like a bug in this code. It is a pinned dependency being out
    of date, and the fix is one pip install. Cached, so the introspection
    happens once per process rather than per stage.
    """
    import inspect

    import anthropic
    from anthropic.resources.messages import AsyncMessages

    if "output_config" not in inspect.signature(AsyncMessages.stream).parameters:
        raise RunnerError(
            f"anthropic {anthropic.__version__} does not accept output_config, so "
            f"structured outputs are unavailable. Install "
            f"anthropic>={MIN_SDK_FOR_STRUCTURED_OUTPUTS}. Parsing JSON out of prose "
            f"is not a fallback this platform accepts -- it is the failure mode "
            f"structured outputs exist to remove."
        )


DEFAULT_SYSTEM = (
    "You are an execution stage inside a typed task pipeline. You are given a "
    "stage's inputs and must produce exactly the declared output. Do not "
    "explain, do not add fields, and do not invent values that are not "
    "derivable from the input -- a stage that guesses is worse than a stage "
    "that fails, because a guess is indistinguishable from a correct answer "
    "downstream."
)


def strictify(schema: Mapping[str, Any]) -> dict[str, Any]:
    """
    Make a JSON Schema acceptable to structured outputs.

    Every object needs `additionalProperties: false` and an explicit
    `required`. Absent `required`, every declared property is required --
    the same convention the typechecker uses, so a stage cannot promise a
    field structurally and then omit it at generation time.
    """
    out = dict(schema)
    types = out.get("type")
    type_set = {types} if isinstance(types, str) else set(types or [])

    if "properties" in out:
        out["properties"] = {k: strictify(v or {}) for k, v in out["properties"].items()}
        out.setdefault("required", list(out["properties"].keys()))
        out["additionalProperties"] = False
        if not type_set:
            out["type"] = "object"
    elif type_set == {"object"}:
        out["additionalProperties"] = False

    if isinstance(out.get("items"), dict):
        out["items"] = strictify(out["items"])

    for combinator in ("anyOf", "allOf", "oneOf"):
        if isinstance(out.get(combinator), list):
            out[combinator] = [strictify(s) for s in out[combinator]]

    return out


def _render_user(spec: Mapping[str, Any], inputs: Mapping[str, Any]) -> str:
    template = spec.get("user_template")
    if template:
        # Same placeholder syntax as the command runner, so one substitution
        # convention covers both.
        rendered = {k: json.dumps(v, default=str) if not isinstance(v, str) else v
                    for k, v in inputs.items()}
        return substitute(str(template), rendered)
    return (
        "Stage inputs:\n\n"
        + json.dumps(inputs, indent=2, default=str)
        + "\n\nProduce the declared output."
    )


def _document_blocks(spec: Mapping[str, Any], inputs: Mapping[str, Any]) -> tuple[list, dict]:
    """
    Attach declared inputs as documents rather than as paths.

    `{"attach_documents": ["pdf_path"]}` on the spec. Without this a model
    fallback for a stage whose input is a file receives the *string*
    "/tmp/x.pdf" and confidently invents an answer about a document it never
    saw -- which is exactly the failure a fallback is supposed to prevent.
    Attached inputs are removed from the JSON payload so the path itself
    doesn't also appear as data.
    """
    import base64
    from pathlib import Path

    names = spec.get("attach_documents") or []
    if not names:
        return [], dict(inputs)

    blocks: list[dict[str, Any]] = []
    remaining = dict(inputs)
    for name in names:
        path = remaining.pop(name, None)
        if not path:
            continue
        file = Path(str(path))
        if not file.exists():
            raise RunnerError(f"input '{name}' points at {path}, which does not exist")
        data = base64.standard_b64encode(file.read_bytes()).decode("ascii")
        blocks.append(
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                "title": file.name,
            }
        )
    return blocks, remaining


def _postprocess(spec: Mapping[str, Any], output: dict[str, Any],
                 inputs: Mapping[str, Any], ctx: RunContext) -> dict[str, Any]:
    """
    Hand the model's answer to a deterministic function before returning it.

    `{"postprocess": "tables:apply_column_mapping"}`, resolved against the
    same explicit registry the python runner uses -- never importlib on a
    database value.

    This exists because the two halves of a stage like map_to_schema want
    different tools. Deciding *which* column is the SKU needs reasoning;
    moving the values into place does not, and structured outputs cannot
    describe an object whose keys the caller chose anyway. Letting the model
    return a mapping and the code apply it keeps the reasoning where it
    belongs and the data movement exact.
    """
    ref = spec.get("postprocess")
    if not ref:
        return output

    from app.runners.registry import REGISTRY

    fn = REGISTRY.get(str(ref))
    if fn is None:
        raise RunnerError(
            f"unknown postprocess ref '{ref}'. Registered: {', '.join(sorted(REGISTRY))}"
        )
    try:
        result = fn({**inputs, **output}, ctx)
    except RunnerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RunnerError(f"postprocess {ref} raised {type(exc).__name__}: {exc}") from exc

    if not isinstance(result, dict):
        raise RunnerError(f"postprocess {ref} returned {type(result).__name__}, expected a dict")
    return result


class ModelRunner:
    kind = "model"

    def __init__(self, client_factory=None):
        # Injectable so tests can dispatch to a fake without an API key.
        self._client_factory = client_factory

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()

        assert_structured_outputs_supported()
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(
            api_key=settings.require("anthropic_api_key"),
            timeout=settings.model_timeout_s,
        )

    async def run(
        self, spec: dict[str, Any], inputs: dict[str, Any], ctx: RunContext
    ) -> RunnerResult:
        schema = spec.get("output_schema") or ctx.output_schema
        if not schema:
            raise RunnerError(
                "model implementation has no output_schema and the task node declares "
                "none; structured outputs need a schema to constrain against"
            )

        client = self._client()
        model = spec.get("model") or settings.model_id
        max_tokens = int(spec.get("max_tokens") or settings.model_max_tokens)

        documents, payload = _document_blocks(spec, inputs)
        content: list[dict[str, Any]] = [
            *documents,
            {"type": "text", "text": _render_user(spec, payload)},
        ]

        try:
            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                output_config={
                    "effort": spec.get("effort") or settings.model_effort,
                    "format": {"type": "json_schema", "schema": strictify(schema)},
                },
                system=spec.get("system") or DEFAULT_SYSTEM,
                messages=[{"role": "user", "content": content}],
            ) as stream:
                message = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001
            raise RunnerError(f"model call failed: {type(exc).__name__}: {exc}") from exc

        # Check stop_reason before touching content: on a refusal the content
        # list is empty or partial, and indexing it raises something that
        # looks nothing like the actual problem.
        if getattr(message, "stop_reason", None) == "refusal":
            detail = getattr(getattr(message, "stop_details", None), "explanation", "")
            raise RunnerError(f"model declined this request. {detail}".strip())

        cost = self._cost(message)

        text = next((b.text for b in message.content if b.type == "text"), None)
        if text is None:
            raise RunnerError(
                f"model returned no text block (stop_reason={message.stop_reason}); "
                f"nothing to parse"
            )

        try:
            output = json.loads(text)
        except json.JSONDecodeError as exc:
            # Should be unreachable -- output_config.format guarantees the
            # shape. Reported rather than swallowed precisely because if it
            # ever fires, the guarantee has changed and we need to know.
            raise RunnerError(f"structured output was not valid JSON: {exc}") from exc

        if not isinstance(output, dict):
            raise RunnerError(f"structured output was {type(output).__name__}, expected object")

        # The model's raw answer becomes the cache entry's params. For a
        # stage like map_to_schema that answer *is* the reusable part -- the
        # column mapping for this layout -- so recording it is what lets a
        # later run of the same layout replay it for nothing.
        params = dict(output)
        output = _postprocess(spec, output, inputs, ctx)
        return RunnerResult(output=output, cost=cost, params=params)

    @staticmethod
    def _cost(message) -> float:
        usage = getattr(message, "usage", None)
        if usage is None:
            return 0.0
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        # Cache reads are billed at ~0.1x and writes at ~1.25x. Folded in so a
        # trace's cost stays comparable across cached and uncached calls --
        # the router sorts on measured cost, so a systematically wrong number
        # here shows up as systematically wrong routing.
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        return (
            inp * settings.model_input_cost_per_token
            + out * settings.model_output_cost_per_token
            + cache_read * settings.model_input_cost_per_token * 0.1
            + cache_write * settings.model_input_cost_per_token * 1.25
        )
