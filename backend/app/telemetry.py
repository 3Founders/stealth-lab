"""OpenTelemetry tracing for Stealth: the ONLY tracing foundation, vendor-neutral.

DIVISION OF LABOUR (the rule this module exists to enforce):

    DATABASE  (execution_run_events, route_decisions, ...) -> "what did Stealth decide?"
    TRACE     (this module)                                -> "how did it get there, how long did it take?"

Traces therefore carry only ids, counts, ranks, timings, model names, token
counts and failure codes. Never Claim text, prompts, candidate payloads or
tool output; large artifacts appear as a reference (see `artifact_attrs`).
`_clean` enforces that at the single write path, so callers cannot leak a
document into a span by accident.

OFF BY DEFAULT and failure-proof: with OBSERVABILITY_ENABLED unset, `span()`
is a null context manager and nothing is imported, exported or patched. An
exporter that is down or raises can never reach application code --
BatchSpanProcessor swallows export errors and `_TailKeepExporter` guards its
inner exporter too.

SAMPLING is tail-based and applies to EXPORT only. A finished trace is
exported in full when any span errored, was marked `stealth.force_keep`
(verification failure) or the process runs in benchmark mode. Any other
(routine, successful) trace is exported in full with probability
STEALTH_TRACE_SAMPLE_RATE, otherwise only its root span is exported (a
"minimal trace"). Canonical execution_run_events are written by
app/execution/recorder.py and are never touched by sampling.

Attribute names follow OpenInference (`openinference.span.kind`,
`llm.model_name`, `llm.token_count.*`, ...) where one exists, so Phoenix
classifies retriever / reranker / llm / embedding spans without an adapter.
"""
from __future__ import annotations

import functools
import json
import logging
import random
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

log = logging.getLogger(__name__)

try:  # the API/SDK are optional at runtime: absent => everything is a no-op
    from opentelemetry import trace as _trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanExportResult,
    )
    from opentelemetry.trace import Status, StatusCode
    _OTEL = True
except ImportError:  # pragma: no cover
    _OTEL = False
    SpanExporter = object  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Failure taxonomy: stable, machine-readable, searchable as
# `stealth.failure_code`. Never rename a value; add new ones.
# ---------------------------------------------------------------------------
class FailureCode:
    RETRIEVAL_ERROR = "RETRIEVAL_ERROR"
    CLAIM_LOOKUP_ERROR = "CLAIM_LOOKUP_ERROR"
    RERANKER_ERROR = "RERANKER_ERROR"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_ERROR = "MODEL_ERROR"
    DB_ERROR = "DB_ERROR"
    SHARD_UNAVAILABLE = "SHARD_UNAVAILABLE"
    SANDBOX_ERROR = "SANDBOX_ERROR"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    INGESTION_PARSE_ERROR = "INGESTION_PARSE_ERROR"
    INGESTION_ERROR = "INGESTION_ERROR"
    DUPLICATE_OBJECT = "DUPLICATE_OBJECT"
    UNKNOWN = "UNKNOWN"


def classify_failure(exc: BaseException, default: str = FailureCode.UNKNOWN) -> str:
    """Map an exception to a stable code; `default` names the stage's own code."""
    name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in name:
        return FailureCode.MODEL_TIMEOUT if default == FailureCode.MODEL_ERROR else default
    try:
        import asyncpg
        if isinstance(exc, (asyncpg.exceptions.CannotConnectNowError,
                            asyncpg.exceptions.ConnectionDoesNotExistError,
                            asyncpg.exceptions.InterfaceError)):
            return FailureCode.SHARD_UNAVAILABLE
        if isinstance(exc, asyncpg.PostgresError):
            return FailureCode.DB_ERROR
    except ImportError:  # pragma: no cover
        pass
    return default


# ---------------------------------------------------------------------------
# Attribute hygiene -- the single write path for span attributes.
# ---------------------------------------------------------------------------
_MAX_STR = 200
_SPAN_KIND_ATTR = "openinference.span.kind"


def _clean(value: Any) -> Any:
    """Scalars only, strings capped. Collections, bytes and objects are
    dropped: a trace is not a place to carry a payload."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "..."
    return None


def _prefixed(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in attrs.items():
        cleaned = _clean(value)
        if cleaned is None:
            continue
        out[key if "." in key else f"stealth.{key}"] = cleaned
    return out


def artifact_attrs(
    *, artifact_id: Optional[str] = None, uri: Optional[str] = None,
    content_hash: Optional[str] = None, size: Optional[int] = None,
    content_type: Optional[str] = None,
) -> dict[str, Any]:
    """Reference to a large payload (prompt, sandbox log, screenshot, ...)
    that lives in canonical/object storage. Never pass the payload itself."""
    return {
        "artifact.id": artifact_id, "artifact.uri": uri,
        "artifact.content_hash": content_hash, "artifact.size": size,
        "artifact.content_type": content_type,
    }


# ---------------------------------------------------------------------------
# Tail-keep exporter (sampling)
# ---------------------------------------------------------------------------
class _TailKeepExporter(SpanExporter):  # type: ignore[misc,valid-type]
    """Buffers spans per trace until the root span ends, then exports the
    whole trace (failure / force_keep / benchmark / lucky draw) or only the
    root. Bounded, and never raises into the span processor."""

    def __init__(self, inner: "SpanExporter", rate: float, *, benchmark: bool = False,
                 rng: Callable[[], float] = random.random, max_traces: int = 512):
        self._inner, self._rate, self._benchmark = inner, rate, benchmark
        self._rng, self._max = rng, max_traces
        self._pending: "OrderedDict[int, list]" = OrderedDict()
        self._decided: "OrderedDict[int, bool]" = OrderedDict()
        self._lock = threading.Lock()

    def _keep(self, spans: list) -> bool:
        if self._benchmark:
            return True
        for s in spans:
            if s.status.status_code == StatusCode.ERROR:
                return True
            if (s.attributes or {}).get("stealth.force_keep"):
                return True
        return self._rng() < self._rate

    def _send(self, spans: list):
        try:
            return self._inner.export(spans)
        except Exception:  # noqa: BLE001 -- exporter failure must never reach the app
            log.warning("telemetry: exporter failed; spans dropped", exc_info=True)
            return SpanExportResult.FAILURE

    def export(self, spans):  # type: ignore[override]
        to_send: list = []
        with self._lock:
            for s in spans:
                tid = s.context.trace_id
                if tid in self._decided:  # late child of an already-decided trace
                    if self._decided[tid]:
                        to_send.append(s)
                    continue
                self._pending.setdefault(tid, []).append(s)
                if s.parent is None:  # root ended: decide the whole trace
                    group = self._pending.pop(tid)
                    keep = self._keep(group)
                    self._decided[tid] = keep
                    to_send.extend(group if keep else [s])
            while len(self._pending) > self._max:  # root never ended: cap memory
                self._pending.popitem(last=False)
            while len(self._decided) > self._max * 4:
                self._decided.popitem(last=False)
        return self._send(to_send) if to_send else SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        with self._lock:
            leftovers = [s for group in self._pending.values() for s in group]
            self._pending.clear()
        if leftovers:
            self._send(leftovers)
        try:
            self._inner.shutdown()
        except Exception:  # noqa: BLE001
            log.warning("telemetry: exporter shutdown failed", exc_info=True)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception:  # noqa: BLE001
            return False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_provider: Any = None
_tracer: Any = None
_benchmark = False
_init_lock = threading.Lock()


def is_enabled() -> bool:
    return _tracer is not None


def _otlp_traces_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    return base if base.endswith("/v1/traces") else base + "/v1/traces"


def _build_exporter(settings: Any) -> Optional["SpanExporter"]:
    backend = (settings.observability_backend or "none").lower()
    endpoint = settings.otel_exporter_otlp_endpoint
    if backend == "phoenix" and not endpoint:
        endpoint = "http://localhost:6006"
    if backend == "console":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        return ConsoleSpanExporter()
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            log.warning("telemetry: OTLP endpoint set but exporter package missing")
            return None
        return OTLPSpanExporter(endpoint=_otlp_traces_url(endpoint))
    return None  # enabled, no backend: ids still correlate, nothing leaves the process


def configure(
    component: str, *, exporter: Optional["SpanExporter"] = None,
    sample_rate: Optional[float] = None, benchmark: Optional[bool] = None,
    batch: bool = True, settings: Any = None,
) -> bool:
    """(Re)build the tracer. `exporter=` is the test/embedding seam."""
    global _provider, _tracer, _benchmark
    if not _OTEL:
        return False
    if settings is None:
        from app.config import get_settings
        settings = get_settings()
    rate = settings.stealth_trace_sample_rate if sample_rate is None else sample_rate
    bench = settings.stealth_benchmark if benchmark is None else benchmark
    with _init_lock:
        exp = exporter if exporter is not None else _build_exporter(settings)
        resource = Resource.create({
            "service.name": settings.otel_service_name,
            "service.version": settings.release or "dev",
            "deployment.environment": str(settings.environment),
            "stealth.component": component,
            "openinference.project.name": settings.otel_service_name,
        })
        provider = TracerProvider(resource=resource)
        if exp is not None:
            # Always wrapped: even at rate 1.0 the wrapper is what guarantees a
            # failing exporter (export OR shutdown) cannot raise into the app.
            if not isinstance(exp, _TailKeepExporter):
                exp = _TailKeepExporter(exp, rate, benchmark=bench)
            provider.add_span_processor(
                BatchSpanProcessor(exp) if batch else SimpleSpanProcessor(exp))
        _provider, _tracer, _benchmark = provider, provider.get_tracer("stealth"), bench
    instrument_openai()
    return True


def init(component: str) -> bool:
    """Entry-point hook (api / mcp / worker). Idempotent, never raises."""
    try:
        from app.config import get_settings
        if not get_settings().observability_enabled:
            return False
        if _tracer is not None:
            return True
        return configure(component)
    except Exception:  # noqa: BLE001 -- tracing is never a dependency of serving traffic
        log.warning("telemetry: init failed; tracing disabled", exc_info=True)
        return False


def shutdown() -> None:
    global _provider, _tracer
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception:  # noqa: BLE001
            pass
    _provider = _tracer = None


# ---------------------------------------------------------------------------
# Span API
# ---------------------------------------------------------------------------
class _NullSpan:
    def set_attribute(self, *_a: Any, **_k: Any) -> None: ...
    def set_attributes(self, *_a: Any, **_k: Any) -> None: ...
    def add_event(self, *_a: Any, **_k: Any) -> None: ...
    def set_status(self, *_a: Any, **_k: Any) -> None: ...
    def get_span_context(self) -> Any: return None


_NULL = _NullSpan()


@contextmanager
def span(name: str, *, kind: Optional[str] = None, on_error: str = FailureCode.UNKNOWN,
         attributes: Optional[dict[str, Any]] = None, **stealth_attrs: Any) -> Iterator[Any]:
    """Coarse span. `kind` is an OpenInference span kind (CHAIN, RETRIEVER,
    RERANKER, LLM, EMBEDDING, TOOL, AGENT, EVALUATOR). Keyword attributes get
    the `stealth.` prefix. An escaping exception marks the span ERROR with a
    stable `stealth.failure_code` (`on_error` names the stage's own code) and
    is re-raised untouched."""
    if _tracer is None:
        yield _NULL
        return
    try:
        cm = _tracer.start_as_current_span(name, record_exception=False, set_status_on_exception=False)
        sp = cm.__enter__()
    except Exception:  # noqa: BLE001
        yield _NULL
        return
    try:
        attrs = _prefixed({**(attributes or {}), **stealth_attrs})
        if kind:
            attrs[_SPAN_KIND_ATTR] = kind
        if _benchmark:
            attrs["stealth.force_keep"] = True
        sp.set_attributes(attrs)
    except Exception:  # noqa: BLE001
        pass
    try:
        yield sp
    except BaseException as exc:
        try:
            sp.set_attribute("stealth.failure_code", classify_failure(exc, on_error))
            sp.set_attribute("exception.type", type(exc).__name__)
            sp.set_status(Status(StatusCode.ERROR, type(exc).__name__))
        except Exception:  # noqa: BLE001
            pass
        cm.__exit__(None, None, None)
        raise
    else:
        cm.__exit__(None, None, None)


def set_attrs(sp: Any, **stealth_attrs: Any) -> None:
    try:
        sp.set_attributes(_prefixed(stealth_attrs))
    except Exception:  # noqa: BLE001
        pass


def fail(sp: Any, code: str, *, force_keep: bool = True) -> None:
    """Mark a span failed WITHOUT an exception (e.g. verification failed)."""
    try:
        sp.set_attribute("stealth.failure_code", code)
        if force_keep:
            sp.set_attribute("stealth.force_keep", True)
        sp.set_status(Status(StatusCode.ERROR, code))
    except Exception:  # noqa: BLE001
        pass


def add_event(name: str, **attrs: Any) -> None:
    """Timeline marker on the current span (bounded scalars only)."""
    if _tracer is None:
        return
    try:
        _trace.get_current_span().add_event(name, _prefixed(attrs))
    except Exception:  # noqa: BLE001
        pass


def current_ids() -> tuple[Optional[str], Optional[str]]:
    """(trace_id, span_id) of the active span as hex, or (None, None).
    Stored beside canonical events so a trace can be joined to state."""
    if _tracer is None:
        return None, None
    try:
        ctx = _trace.get_current_span().get_span_context()
        if ctx.is_valid:
            return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    except Exception:  # noqa: BLE001
        pass
    return None, None


def traced(name: str, *, kind: Optional[str] = None, on_error: str = FailureCode.UNKNOWN):
    """Decorator for an async or sync function with no interesting attributes."""
    def deco(fn: Callable) -> Callable:
        import inspect
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrap(*a: Any, **k: Any):
                with span(name, kind=kind, on_error=on_error):
                    return await fn(*a, **k)
            return awrap

        @functools.wraps(fn)
        def wrap(*a: Any, **k: Any):
            with span(name, kind=kind, on_error=on_error):
                return fn(*a, **k)
        return wrap
    return deco


# ---------------------------------------------------------------------------
# Model calls: ONE shared path. Every `client.chat.completions.create(...)`
# in the codebase (sync or async OpenAI-compatible client, incl. Gemini's
# OpenAI endpoint) is instrumented by patching the SDK class once.
# ---------------------------------------------------------------------------
def _price(model: str) -> Optional[tuple[float, float]]:
    try:
        from app.config import get_settings
        raw = get_settings().stealth_model_prices
        if not raw:
            return None
        table = json.loads(raw)
        for prefix in sorted(table, key=len, reverse=True):
            if model.startswith(prefix):
                a, b = table[prefix]
                return float(a), float(b)
    except Exception:  # noqa: BLE001
        pass
    return None


def estimate_cost_usd(model: str, tokens_in: Optional[int], tokens_out: Optional[int]) -> Optional[float]:
    price = _price(model or "")
    if price is None or tokens_in is None or tokens_out is None:
        return None
    return (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000


def _provider_of(instance: Any) -> Optional[str]:
    try:
        host = str(instance._client.base_url.host)
    except Exception:  # noqa: BLE001
        return None
    for needle, name in (("openai.com", "openai"), ("googleapis", "google"),
                         ("anthropic", "anthropic"), ("localhost", "local"), ("127.0.0.1", "local")):
        if needle in host:
            return name
    return host


def record_llm_usage(sp: Any, *, model: Optional[str], provider: Optional[str], response: Any) -> None:
    usage = getattr(response, "usage", None)
    tin = getattr(usage, "prompt_tokens", None)
    tout = getattr(usage, "completion_tokens", None)
    attrs: dict[str, Any] = {
        "model": model, "llm.model_name": model, "llm.provider": provider,
        "tokens_input": tin, "tokens_output": tout,
        "llm.token_count.prompt": tin, "llm.token_count.completion": tout,
        "cost_usd": estimate_cost_usd(model or "", tin, tout),
    }
    set_attrs(sp, **attrs)


def instrument_callable(fn: Callable, *, provider_of: Callable[[Any], Optional[str]] = _provider_of) -> Callable:
    """Wrap an OpenAI-style `create` (sync or async). Exposed for tests."""
    import inspect

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrap(self: Any, *a: Any, **k: Any):
            model = k.get("model")
            with span("llm.chat", kind="LLM", on_error=FailureCode.MODEL_ERROR,
                      attributes={"llm.model_name": model, "model": model}) as sp:
                resp = await fn(self, *a, **k)
                record_llm_usage(sp, model=model, provider=provider_of(self), response=resp)
                return resp
        awrap._stealth_instrumented = True  # type: ignore[attr-defined]
        return awrap

    @functools.wraps(fn)
    def wrap(self: Any, *a: Any, **k: Any):
        model = k.get("model")
        with span("llm.chat", kind="LLM", on_error=FailureCode.MODEL_ERROR,
                  attributes={"llm.model_name": model, "model": model}) as sp:
            resp = fn(self, *a, **k)
            record_llm_usage(sp, model=model, provider=provider_of(self), response=resp)
            return resp
    wrap._stealth_instrumented = True  # type: ignore[attr-defined]
    return wrap


def instrument_openai() -> bool:
    """Patch OpenAI chat completions once (idempotent). No-op if the SDK is absent."""
    try:
        from openai.resources.chat.completions import AsyncCompletions, Completions
    except ImportError:
        return False
    for cls in (Completions, AsyncCompletions):
        if not getattr(cls.create, "_stealth_instrumented", False):
            cls.create = instrument_callable(cls.create)  # type: ignore[method-assign]
    return True


def shard_id() -> str:
    """Configured label for the DB / vector index serving this process."""
    try:
        from app.config import get_settings
        return get_settings().stealth_shard_id
    except Exception:  # noqa: BLE001
        return "primary"


class Stopwatch:
    """Millisecond timer for attributes such as `stealth.latency_ms`."""
    def __init__(self) -> None:
        self._t = time.monotonic()

    def ms(self) -> float:
        return round((time.monotonic() - self._t) * 1000, 2)
