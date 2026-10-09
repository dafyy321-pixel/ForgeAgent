import inspect
import os
import time
from contextlib import contextmanager
from functools import wraps

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram

calls = Counter("forge_operations_total", "Completed runtime operations", ["operation", "outcome"])
latency = Histogram("forge_operation_seconds", "Runtime operation latency", ["operation"],
                    buckets=(.01, .1, .5, 1, 5, 15, 30, 60, 120, 300))


@contextmanager
def operation(name):
    # Never send arguments, exception messages, prompts, filenames or HTTP headers to exporters.
    start, outcome = time.monotonic(), "ok"
    with tracer.start_as_current_span(name, record_exception=False, set_status_on_exception=False) as span:
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


def observed(name):
    def decorate(function):
        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with operation(name):
                    return await function(*args, **kwargs)
            return asynchronous
        @wraps(function)
        def synchronous(*args, **kwargs):
            with operation(name):
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
