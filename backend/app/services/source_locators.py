"""
Source locators (provenance pointers) and step execution bindings, both stored INSIDE the Procedure.

    procedures.source_locator        where the whole procedure came from
    procedures.steps[i].source_locator   where THIS step came from  (required for canonical writes)
    procedures.steps[i].binding      how THIS step is executed      (replaces the global Implementation object)

A locator identifies an immutable place in a source::

    {"source_id": "anthropic-skills", "uri": "https://github.com/...", "path": "skills/pdf/SKILL.md",
     "commit": "<sha>", "content_hash": "<sha256>", "line_start": 40, "line_end": 52,      # or "anchor": "#step-3", "page": 4
     "object_locator": "s3://bucket/ab/cd/<sha>",                                            # raw bytes in object storage
     "granularity": "span" | "section" | "document"}

A step with no span of its own inherits the procedure locator and is MARKED (`granularity: "document"`,
`inherited: true`, `step_index`), so nobody mistakes "somewhere in this document" for a precise citation.

A binding is a closed vocabulary; unknown keys are rejected so it cannot become a junk drawer::

    {"kind": "tool|model|mcp_tool|adapter|sandbox|command|runtime|slm_artifact|http_api|binary|wasm|container",
     "<kind>": "<primary value, e.g. the MCP tool name>",
     "endpoint": "https://...", "server_url": "https://mcp...", "path": "bin/tool or module.wasm", "image": "registry/img:tag",
     "args": [...], "env_refs": ["NAME_OF_ENV_VAR"],          # credentials are referenced by NAME, never stored
     "locator": ..., "parameters": {...}, "verifier": {...}, "resources": {...}}
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional, Sequence

LOCATOR_KEYS = frozenset({
    "source_id", "uri", "path", "commit", "content_hash", "line_start", "line_end", "anchor", "page",
    "object_locator", "granularity", "inherited", "step_index", "captured_at",
})
GRANULARITIES = ("span", "section", "document")
# What a step is executed BY. Each kind's own key holds its primary value (`kind: "http_api"` -> `http_api: "<name>"`);
# the concrete address lives in `endpoint` / `server_url` / `path` / `image` so nothing is a junk drawer.
BINDING_KINDS = (
    "tool", "model", "mcp_tool", "adapter", "sandbox", "command", "runtime", "slm_artifact",
    "http_api", "binary", "wasm", "container",
    "source_artifact",   # run an immutable ingested artifact (by id) -- only if that artifact is screened + execution_allowed
)
BINDING_ADDRESS_KEYS = frozenset({"endpoint", "server_url", "path", "image", "args", "env_refs", "entrypoint", "sandbox_policy"})
ARTIFACT_ROLES = ("executable_source", "style_reference", "design_reference", "documentation", "dependency_manifest", "test_fixture")
SANDBOX_POLICIES = ("isolated", "isolated_network")
BINDING_KEYS = frozenset({"kind", *BINDING_KINDS, *BINDING_ADDRESS_KEYS, "locator", "parameters", "verifier", "resources"})


class SourceLocatorError(ValueError):
    pass


def require_locators_default() -> bool:
    return os.environ.get("STEALTH_REQUIRE_SOURCE_LOCATORS", "0") in ("1", "true", "True")


def validate_locator(loc: Any, *, where: str = "source_locator") -> dict:
    if not isinstance(loc, Mapping):
        raise SourceLocatorError(f"{where} must be an object")
    unknown = set(loc) - LOCATOR_KEYS
    if unknown:
        raise SourceLocatorError(f"{where}: unknown keys {sorted(unknown)}")
    if not (loc.get("source_id") or loc.get("uri") or loc.get("object_locator")):
        raise SourceLocatorError(f"{where}: needs at least one of source_id / uri / object_locator")
    if loc.get("granularity") is not None and loc["granularity"] not in GRANULARITIES:
        raise SourceLocatorError(f"{where}: granularity must be one of {GRANULARITIES}")
    for k in ("line_start", "line_end", "page"):
        if k in loc and (not isinstance(loc[k], int) or loc[k] < 0):
            raise SourceLocatorError(f"{where}: {k} must be a non-negative integer")
    if "line_start" in loc and "line_end" in loc and loc["line_end"] < loc["line_start"]:
        raise SourceLocatorError(f"{where}: line_end < line_start")
    return dict(loc)


def validate_binding(binding: Any, *, where: str = "binding") -> dict:
    if not isinstance(binding, Mapping):
        raise SourceLocatorError(f"{where} must be an object")
    unknown = set(binding) - BINDING_KEYS
    if unknown:
        raise SourceLocatorError(f"{where}: unknown keys {sorted(unknown)} (allowed: {sorted(BINDING_KEYS)})")
    kind = binding.get("kind")
    if kind is not None and kind not in BINDING_KINDS:
        raise SourceLocatorError(f"{where}: kind must be one of {BINDING_KINDS}")
    if kind is not None and not binding.get(kind):
        raise SourceLocatorError(f"{where}: kind={kind!r} but no {kind!r} value given")
    for k in ("parameters", "verifier", "resources"):
        if k in binding and not isinstance(binding[k], Mapping):
            raise SourceLocatorError(f"{where}: {k} must be an object")
    for k in ("endpoint", "server_url", "path", "image"):
        if k in binding and not isinstance(binding[k], str):
            raise SourceLocatorError(f"{where}: {k} must be a string")
    for k in ("endpoint", "server_url"):
        if binding.get(k) and not str(binding[k]).startswith(("http://", "https://", "ws://", "wss://", "grpc://")):
            raise SourceLocatorError(f"{where}: {k} must be an http(s)/ws(s)/grpc URL")
    if "args" in binding and not (isinstance(binding["args"], (list, tuple)) and all(isinstance(a, str) for a in binding["args"])):
        raise SourceLocatorError(f"{where}: args must be a list of strings")
    if "env_refs" in binding and not (isinstance(binding["env_refs"], (list, tuple)) and all(isinstance(a, str) for a in binding["env_refs"])):
        raise SourceLocatorError(f"{where}: env_refs must be a list of variable NAMES (never values)")
    if binding.get("sandbox_policy") is not None and binding["sandbox_policy"] not in SANDBOX_POLICIES:
        raise SourceLocatorError(f"{where}: sandbox_policy must be one of {SANDBOX_POLICIES}")
    if kind == "source_artifact":
        if not binding.get("entrypoint"):
            raise SourceLocatorError(f"{where}: kind='source_artifact' needs an entrypoint (repository-relative path)")
        if not binding.get("runtime"):
            raise SourceLocatorError(f"{where}: kind='source_artifact' needs a runtime (e.g. 'python')")
        if not binding.get("sandbox_policy"):
            raise SourceLocatorError(f"{where}: kind='source_artifact' needs a sandbox_policy")
    # kind-specific required addresses
    need = {"mcp_tool": ("server_url", "locator"), "http_api": ("endpoint", "locator"), "binary": ("path", "locator"),
            "wasm": ("path", "locator"), "container": ("image", "locator")}.get(kind or "")
    if need and not any(binding.get(k) for k in need):
        raise SourceLocatorError(f"{where}: kind={kind!r} needs one of {need} (where it lives)")
    return dict(binding)


def validate_source_artifacts(refs: Any, *, where: str = "source_artifacts") -> list:
    """`procedures.source_artifacts`: preserved source material a procedure draws on. Each ref names an
    ingested artifact (`artifact_id`), the path inside its source, and a ROLE. Only `executable_source` refs may
    ever be executed, and even then only when the artifact itself is screened and `execution_allowed`; style/design
    references are retrievable context and are never executable."""
    if refs is None:
        return []
    if not isinstance(refs, (list, tuple)):
        raise SourceLocatorError(f"{where} must be a list")
    out = []
    for i, r in enumerate(refs):
        if not isinstance(r, Mapping):
            raise SourceLocatorError(f"{where}[{i}] must be an object")
        unknown = set(r) - {"artifact_id", "path", "role", "execution_allowed", "note"}
        if unknown:
            raise SourceLocatorError(f"{where}[{i}]: unknown keys {sorted(unknown)}")
        if not r.get("artifact_id"):
            raise SourceLocatorError(f"{where}[{i}]: artifact_id is required")
        role = r.get("role")
        if role not in ARTIFACT_ROLES:
            raise SourceLocatorError(f"{where}[{i}]: role must be one of {ARTIFACT_ROLES}")
        if role != "executable_source" and r.get("execution_allowed"):
            raise SourceLocatorError(f"{where}[{i}]: only role='executable_source' may set execution_allowed")
        out.append(dict(r))
    return out


def normalize_steps(
    steps: Optional[Sequence[Any]], procedure_locator: Optional[Mapping], *, strict: bool,
) -> list:
    """Validate step locators/bindings; give locator-less steps the procedure's locator (marked inherited).
    ``strict``: every step must end up with a locator (a step with none and no procedure locator raises).
    Non-mapping steps (plain strings) are only tolerated when not strict."""
    out: list = []
    for i, step in enumerate(steps or []):
        if not isinstance(step, Mapping):
            if strict:
                raise SourceLocatorError(f"steps[{i}] must be an object carrying a source_locator (got {type(step).__name__})")
            out.append(step)
            continue
        s = dict(step)
        if s.get("source_locator") is not None:
            s["source_locator"] = validate_locator(s["source_locator"], where=f"steps[{i}].source_locator")
        elif procedure_locator:
            s["source_locator"] = {**validate_locator(procedure_locator), "granularity": "document", "inherited": True, "step_index": i}
        elif strict:
            raise SourceLocatorError(f"steps[{i}] has no source_locator and the procedure has none to inherit")
        if s.get("binding") is not None:
            s["binding"] = validate_binding(s["binding"], where=f"steps[{i}].binding")
        out.append(s)
    return out


def procedure_locator_from_source(*, source_key: Optional[str] = None, uri: Optional[str] = None, content_hash: Optional[str] = None,
                                  commit: Optional[str] = None, path: Optional[str] = None, source_id: Optional[str] = None,
                                  object_locator: Optional[str] = None) -> Optional[dict]:
    loc = {k: v for k, v in dict(source_id=source_id or source_key, uri=uri, content_hash=content_hash, commit=commit, path=path,
                                 object_locator=object_locator, granularity="document").items() if v}
    return loc if (loc.get("source_id") or loc.get("uri") or loc.get("object_locator")) else None


def binding_from_implementation(impl: Mapping) -> dict:
    """Fold a legacy `implementations` row into a step binding (used by the fold-implementations migration tool)."""
    kind_map = {"deterministic": "command", "mcp_tool": "mcp_tool", "model": "model", "frontier": "model", "slm": "slm_artifact",
                "tool": "tool", "adapter": "adapter", "sandbox": "sandbox", "runtime": "runtime", "api": "http_api", "wasm": "wasm"}
    raw_kind = str(impl.get("kind") or "")
    kind = kind_map.get(raw_kind, "tool")
    locator = impl.get("locator") if isinstance(impl.get("locator"), Mapping) else {}
    value = locator.get("path") or locator.get("command") or locator.get("name") or impl.get("name") or impl.get("provider") or raw_kind
    b: dict[str, Any] = {"kind": kind, kind: str(value)}
    if locator:
        b["locator"] = str(locator.get("path") or locator.get("uri") or "") or None
        if b["locator"] is None:
            del b["locator"]
    if impl.get("requirements"):
        b["resources"] = dict(impl["requirements"]) if isinstance(impl["requirements"], Mapping) else {}
    if impl.get("verification_contract"):
        b["verifier"] = dict(impl["verification_contract"]) if isinstance(impl["verification_contract"], Mapping) else {}
    return validate_binding(b)
