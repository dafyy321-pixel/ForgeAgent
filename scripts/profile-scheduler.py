"""Run the unchanged scheduler regression and retain bounded, redacted timings."""

import argparse
import json
import os
import platform
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import psycopg
import pytest
from forgeagent import context, db, service, worker
from forgeagent.domain import digest
from forgeagent.sandbox import sandbox
from forgeagent.telemetry import observed
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from sqlalchemy import event, select


def summary(values):
    ordered = sorted(values)
    return {"count": len(values), "total_seconds": sum(values),
            "p50_seconds": ordered[(len(ordered) - 1) // 2],
            "p95_seconds": ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)]} if values else {}


class Samples(SpanExporter):
    def __init__(self):
        self.values = defaultdict(list)

    def export(self, spans):
        for span in spans:
            self.values[span.name].append((span.end_time - span.start_time) / 1e9)
        return SpanExportResult.SUCCESS


class Profile:
    def __init__(self):
        self.scopes = set()
        self.seconds = 0

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        start = time.monotonic()
        yield
        self.seconds = time.monotonic() - start


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = db.engine.url
    try:
        with psycopg.connect(host=url.host, port=url.port, dbname=url.database, user=url.username,
                             password=url.password, connect_timeout=3):
            pass
    except psycopg.Error:
        raise SystemExit("PostgreSQL is unavailable; start the local database before profiling.") from None
    samples, profile = Samples(), Profile()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(samples))
    trace.set_tracer_provider(provider)
    for module, name, stage in [(service, "checkpoint", "state.checkpoint"),
                                (context, "count", "context.tokenization"),
                                (worker, "compile_context", "context.compile"),
                                (sandbox, "restore", "workspace.restore"),
                                (sandbox, "patch", "delivery.patch"),
                                (sandbox, "delivery", "delivery.generate_and_check"),
                                (sandbox, "check_patch", "delivery.check_patch")]:
        if hasattr(module, name):
            setattr(module, name, observed(stage)(getattr(module, name)))
    claim = worker.Worker.claim

    def capture(self, tenant):
        result = claim(self, tenant)
        if result:
            profile.scopes.add(tenant)
        return result

    worker.Worker.claim = capture

    @event.listens_for(db.engine, "before_cursor_execute")
    def before(conn, cursor, statement, parameters, context, executemany):
        context.profile_started = time.perf_counter()

    @event.listens_for(db.engine, "after_cursor_execute")
    def after(conn, cursor, statement, parameters, context, executemany):
        stage = "sql.lock" if "pg_advisory" in statement else "sql." + statement.split()[0].lower()
        samples.values[stage].append(time.perf_counter() - context.profile_started)

    code = pytest.main(["tests/backend/test_runtime_upgrades.py::test_twenty_active_runs_finish_under_bounded_fair_scheduler", "-q"], plugins=[profile])
    timelines = []
    for scope in sorted(profile.scopes):
        with db.transaction(scope) as session:
            for run in session.scalars(select(db.Run).where(db.Run.tenant_id == scope)):
                events = sorted(db.rows(session, db.Event, scope, run_id=run.id), key=lambda e: e.seq)
                timelines.append({"run_id": run.id, "status": run.status, "events": [
                    {"seq": e.seq, "kind": e.type, "seconds": (e.created_at - run.created_at).total_seconds()}
                    for e in events]})
    completed = [entry["events"][-1]["seconds"] for entry in timelines if entry["status"] == "SUCCEEDED"]
    report = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "platform": platform.platform(), "python": platform.python_version(),
              "logical_cpus": os.cpu_count(), "implementation_digest": digest(service.implementation_bindings()),
              "pytest_exit_code": code, "call_seconds": profile.seconds,
              "contract": {"tasks": 20, "tenants": 2, "slots": 4, "tenant_limit": 2, "timeout_seconds": 45},
              "latencies": {name: summary(values) for name, values in samples.values.items()},
              "operation_samples_seconds": {name: values for name, values in samples.values.items() if not name.startswith("sql.")},
              "task_elapsed_including_setup": summary(completed), "timelines": timelines,
              "limitations": ["Call duration includes task creation; the 45s assertion covers scheduling only.",
                              "SQL durations include server execution/waits, not separately identified lock wait.",
                              "Nested span totals overlap; do not add stage totals.",
                              "Fixture verification is AST acceptance, not Docker or model capacity."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

