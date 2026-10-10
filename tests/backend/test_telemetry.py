import pytest
from forgeagent import db, telemetry
from forgeagent.domain import Fault, Task
from forgeagent.worker import Worker
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("forge-test"))
    yield exporter
    provider.shutdown()


async def test_epochs_actions_cost_and_events_share_run_trace(spans, tenant, make_run):
    run_id = make_run(task=Task(goal="PRIVATE_PROMPT_SENTINEL", allowed_paths=["src"]))
    worker = Worker(target_run=run_id)
    for _ in range(4):
        await worker.once(tenant)
    exported = spans.get_finished_spans()
    quanta = [span for span in exported if span.name == "run.quantum"]
    expected = telemetry.lineage(tenant, run_id).trace_id
    assert len(quanta) == 4 and {span.context.trace_id for span in quanta} == {expected}
    assert {span.attributes["forge.lease_epoch"] for span in quanta} == {1, 2, 3, 4}
    names = {span.name for span in exported if span.context.trace_id == expected}
    assert {"context.compile", "context.tokenization", "db.transaction", "budget.reserve", "budget.settle",
            "model.consume", "tool.dispatch", "verification.execute", "delivery.generate_and_check",
            "object.publish", "state.checkpoint"} <= names
    assert any("forge.action_id" in span.attributes for span in exported if span.name == "tool.dispatch")
    assert any(span.attributes.get("forge.cost_known") is True for span in exported if span.name == "budget.settle")
    with db.transaction(tenant) as session:
        run = db.get(session, db.Run, tenant, run_id)
        assert run.status == "SUCCEEDED"
        events = db.rows(session, db.Event, tenant, run_id=run_id)
        assert all(event.payload["trace"]["run_trace_id"] == f"{expected:032x}" for event in events)
        assert any(event.payload["trace"].get("trace_id") == f"{expected:032x}" for event in events)
    assert "PRIVATE_PROMPT_SENTINEL" not in str([(span.attributes, span.events) for span in exported])
    assert telemetry.correlation.get() == {}


async def test_model_span_has_call_identity_without_exporting_request(spans):
    async def model(messages, model_id):
        return "PRIVATE_RESPONSE", {"input_tokens": 0, "output_tokens": 0}, {}, "provider"

    worker = Worker(model=model)
    with telemetry.run_operation("run.quantum", "tenant", "run", "root", 9):
        await worker.invoke_model("call-id", ["PRIVATE_REQUEST"], "PRIVATE_MODEL_CONFIG", None)
    dispatch = next(span for span in spans.get_finished_spans() if span.name == "model.dispatch")
    assert dispatch.attributes["forge.call_id"] == "call-id"
    assert dispatch.attributes["forge.run_id"] == "run" and dispatch.attributes["forge.lease_epoch"] == 9
    assert "PRIVATE" not in str(dispatch.attributes)
    assert dispatch.context.trace_id == telemetry.lineage("tenant", "root").trace_id
    assert dispatch.context.trace_id != telemetry.lineage("another", "root").trace_id


def test_exception_and_unapproved_identifier_fields_are_redacted(spans):
    @telemetry.observed("test.failed", {"prompt": "unapproved.secret"})
    def fail(prompt):
        raise Fault("BAD_INPUT", "PRIVATE_ERROR_SECRET")

    with pytest.raises(Fault):
        fail("PRIVATE_ARGUMENT")
    span = spans.get_finished_spans()[0]
    assert span.attributes["forge.outcome"] == "fault" and span.attributes["forge.failure_type"] == "Fault"
    assert "PRIVATE" not in str((span.attributes, span.events))
