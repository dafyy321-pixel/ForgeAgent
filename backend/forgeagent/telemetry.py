import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure():
    provider = TracerProvider(resource=Resource.create({"service.name": "forgeagent", "service.version": "0.2.0"}))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(), max_queue_size=1024))
    trace.set_tracer_provider(provider)


tracer = trace.get_tracer("forge.runtime", "1")
