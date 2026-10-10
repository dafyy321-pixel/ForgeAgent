import hashlib
import inspect
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from prometheus_client import Counter, Histogram

calls = Counter("forge_operations_total", "Completed runtime operations", ["operation", "outcome"])
latency = Histogram("forge_operation_seconds", "Runtime operation latency", ["operation"],
                    buckets=(.01, .1, .5, 1, 5, 15, 30, 60, 120, 300))
correlation: ContextVar[dict] = ContextVar("forge_correlation", default={})
FIELDS = {"forge.run_id", "forge.root_id", "forge.lease_epoch", "forge.call_id", "forge.action_id",
          "forge.event_seq", "forge.reserved_micros", "forge.charged_micros", "forge.cost_known"}


def lineage(tenant, root_id):
    # Stable across process restart/epoch; tenant is hashed, never exported as a label.
    checksum = hashlib.sha256((tenant + "\0" + root_id).encode()).digest()
    return SpanContext(int.from_bytes(checksum[:16], "big") or 1,
                       int.from_bytes(checksum[16:24], "big") or 1, False, TraceFlags(TraceFlags.SAMPLED))


def annotate(**attributes):
    span = trace.get_current_span()
    for name, value in attributes.items():
        if name in FIELDS and isinstance(value, (str, int, bool)):
            span.set_attribute(name, value)


def event_trace(tenant, root_id):
    current = trace.get_current_span().get_span_context()
    result = {"run_trace_id": f"{lineage(tenant, root_id).trace_id:032x}"}
    if current.is_valid:
        root = lineage(tenant, root_id)
        if current.trace_id != root.trace_id:
            trace.get_current_span().add_link(root)
        result.update(trace_id=f"{current.trace_id:032x}", span_id=f"{current.span_id:016x}")
    return result


@contextmanager
def run_operation(name, tenant, run_id, root_id, epoch):
    token = correlation.set({"forge.run_id": run_id, "forge.root_id": root_id, "forge.lease_epoch": epoch})
    context = trace.set_span_in_context(NonRecordingSpan(lineage(tenant, root_id)))
    try:
        with operation(name, context=context):
            yield
    finally:
        correlation.reset(token)


@contextmanager
def operation(name, context=None):
    # Never send arguments, exception messages, prompts, filenames or HTTP headers to exporters.
    start, outcome = time.monotonic(), "ok"
    with tracer.start_as_current_span(name, context=context, attributes=correlation.get(),
                                      record_exception=False, set_status_on_exception=False) as span:
        try:
            yield span
        except BaseException as exc:
            from .domain import Fault

            outcome = "fault" if isinstance(exc, Fault) else "cancelled" if type(exc).__name__ == "CancelledError" else "error"
            span.set_attribute("forge.outcome", outcome)
            span.set_attribute("forge.failure_type", type(exc).__name__)
            raise
        finally:
            calls.labels(name, outcome).inc()
            latency.labels(name).observe(time.monotonic() - start)


def observed(name, identifiers=None):
    def decorate(function):
        signature = inspect.signature(function) if identifiers else None

        @contextmanager
        def scope(args, kwargs):
            attributes = dict(correlation.get())
            if signature and identifiers:
                bound = signature.bind(*args, **kwargs)
                for parameter, attribute in identifiers.items():
                    value = bound.arguments.get(parameter)
                    if attribute in FIELDS and isinstance(value, (str, int, bool)):
                        attributes[attribute] = value
            token = correlation.set(attributes)
            try:
                with operation(name):
                    yield
            finally:
                correlation.reset(token)

        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with scope(args, kwargs):
                    return await function(*args, **kwargs)
            return asynchronous
        @wraps(function)
        def synchronous(*args, **kwargs):
            with scope(args, kwargs):
                return function(*args, **kwargs)
        return synchronous
    return decorate


configured = False


def configure():
    global configured
    if configured:
        return
    configured = True
    provider = TracerProvider(resource=Resource.create({"service.name": "forgeagent", "service.version": "0.2.0"}))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(), max_queue_size=1024))
    trace.set_tracer_provider(provider)


tracer = trace.get_tracer("forge.runtime", "1")
