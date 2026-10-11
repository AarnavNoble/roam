"""
Tracing for the request path.

Each pipeline stage runs inside an OpenTelemetry span. Two things consume them:

  * an OTLP exporter, when OTEL_EXPORTER_OTLP_ENDPOINT is set, so traces land
    in whatever backend is listening (Jaeger, Tempo, Vestige);
  * StageAggregator, an in-process span processor that keeps the last N
    durations per span name. That is what /api/metrics/latency and the latency
    benchmark read, so percentiles are available with no collector running.

Spans are also collected per request through a context variable, which is how
a response can carry its own stage timings.
"""

import contextvars
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager

import numpy as np
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from backend import config

WINDOW = 500  # durations kept per stage


class StageAggregator(SpanProcessor):
    """Keeps a sliding window of span durations (ms), keyed by span name."""

    def __init__(self, window: int = WINDOW):
        self._lock = threading.Lock()
        self._durations: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))

    def on_end(self, span: ReadableSpan) -> None:
        ms = (span.end_time - span.start_time) / 1e6
        with self._lock:
            self._durations[span.name].append(ms)

    def reset(self) -> None:
        with self._lock:
            self._durations.clear()

    def samples(self, name: str) -> list[float]:
        with self._lock:
            return list(self._durations.get(name, ()))

    def summary(self) -> dict[str, dict]:
        with self._lock:
            snapshot = {k: list(v) for k, v in self._durations.items()}
        return {
            name: {
                "count": len(vals),
                "p50_ms": round(float(np.percentile(vals, 50)), 1),
                "p95_ms": round(float(np.percentile(vals, 95)), 1),
                "max_ms": round(float(max(vals)), 1),
            }
            for name, vals in sorted(snapshot.items()) if vals
        }


aggregator = StageAggregator()
_provider = TracerProvider(resource=Resource.create({"service.name": "roam-api"}))
_provider.add_span_processor(aggregator)
if config.OTEL_ENDPOINT:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(_provider)
tracer = trace.get_tracer("roam")

# Per-request stage timings. Set by collect_timings(), appended to by stage().
_request_timings: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "request_timings", default=None
)


@contextmanager
def collect_timings():
    """Collect {stage: ms} for every stage() entered inside this block."""
    timings: dict[str, float] = {}
    token = _request_timings.set(timings)
    try:
        yield timings
    finally:
        _request_timings.reset(token)


@contextmanager
def stage(name: str, **attributes):
    """Run a block inside a span named `name` and record its duration."""
    start = time.perf_counter()
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        try:
            yield span
        finally:
            timings = _request_timings.get()
            if timings is not None:
                ms = (time.perf_counter() - start) * 1000
                timings[name] = round(timings.get(name, 0.0) + ms, 1)


def in_context(fn):
    """
    Bind `fn` to the caller's context so spans started on a worker thread stay
    children of the request span. ThreadPoolExecutor does not do this itself.
    """
    ctx = contextvars.copy_context()
    return lambda *args, **kwargs: ctx.copy().run(fn, *args, **kwargs)
