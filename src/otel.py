"""OpenTelemetry tracing — optional, swappable-exporter instrumentation.

A TRACE is one query's full story; a SPAN is one step inside it (retrieval, a model call,
a tool call), nested parent->child with timings + attributes. Instrument once here, export
anywhere: console now, Google Cloud Trace in Phase B — by swapping only the exporter below.

OFF BY DEFAULT. Turn on with OTEL_ENABLED=1. If the SDK isn't installed or it's disabled,
every `with otel.span(...)` becomes a no-op — the agent code is identical either way, and
costs nothing when off.
"""
import os
import functools
from contextlib import contextmanager

_tracer = None   # None = not set up yet; False = tried and disabled/unavailable; else a real tracer


def _get_tracer():
    global _tracer
    if _tracer is not None:
        return _tracer
    if os.getenv("OTEL_ENABLED", "").lower() not in ("1", "true", "yes"):
        _tracer = False
        return _tracer
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
        provider = TracerProvider(resource=Resource.create({"service.name": "oncall-copilot"}))

        def _fmt(s):   # compact one-line-per-span output instead of a wall of JSON
            dur_ms = (s.end_time - s.start_time) / 1e6      # OTel times are nanoseconds
            attrs = "  ".join(f"{k}={v}" for k, v in (s.attributes or {}).items())
            return f"[trace] {s.name:14} {dur_ms:8.1f}ms   {attrs}\n"

        # THE EXPORTER = where spans go. Console for local dev; swap this one line for the
        # Cloud Trace exporter (Phase B) or an OTLP collector. SimpleSpanProcessor exports each
        # span immediately (right for a CLI); a long-running service would use BatchSpanProcessor.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(formatter=_fmt)))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("oncall-copilot")
    except ImportError:
        _tracer = False   # SDK not installed -> no-op
    return _tracer


def set_attrs(span_obj, **attrs):
    """Attach attributes to a span; no-op if the span is None (tracing off) or a value is None."""
    if span_obj is None:
        return
    for k, v in attrs.items():
        if v is not None:
            span_obj.set_attribute(k, v)


@contextmanager
def span(name, **attrs):
    """Open a span named `name`. Spans opened inside auto-nest as children (OTel tracks the
    'current' span in context). Yields the span object, or None when tracing is off."""
    t = _get_tracer()
    if not t:
        yield None
        return
    with t.start_as_current_span(name) as s:
        set_attrs(s, **attrs)
        yield s


def traced(name):
    """Decorator: wrap a whole function call in a root span (used on agent.answer so every
    query is one trace, with retrieval/model/tool spans nested underneath)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with span(name):
                return fn(*args, **kwargs)
        return wrapper
    return deco
