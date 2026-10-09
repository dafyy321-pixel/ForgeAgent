"""Local read-path pilot with isolated historical metadata; no model or repository code execution."""

import argparse
import json
import os
import platform
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from forgeagent import db, knowledge, operations, service
from forgeagent.domain import CreateRun, Task, uid
from forgeagent.reducer import rebuild
from sqlalchemy import select, text

parser = argparse.ArgumentParser()
parser.add_argument("--history", type=int, default=1000, choices=[100, 1000, 5000])
parser.add_argument("--samples", type=int, default=20, choices=range(10, 101))
parser.add_argument("--concurrency", type=int, default=4, choices=range(1, 9))
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
tenant = "history-pilot-" + uid()
service.provision(tenant, "pilot")
with db.transaction(tenant) as s:
    run = service.create_run(s, tenant, "pilot", CreateRun(project_id="runtime-lab", model="fixture",
        task=Task(goal="Inspect calculator", allowed_paths=["src"])), uid())
    service.checkpoint(s, run)
    checkpoint = db.rows(s, db.Checkpoint, tenant, run_id=run.id)[0]
    for index in range(args.history):
        s.add(db.Checkpoint(tenant_id=tenant, run_id=run.id, status="READY", data=checkpoint.data))
        s.add(db.Memory(tenant_id=tenant, id="history-"+str(index), status="withdrawn", data={"content": "historical inactive fact"}))
    run_id = run.id


def measure(index):
    started = time.perf_counter()
    with db.transaction(tenant) as s:
        s.execute(text("SET LOCAL application_name='forge-history-pilot'"))
        list(s.execute(select(db.WorkspaceRevision.id, db.WorkspaceRevision.version).where(db.WorkspaceRevision.tenant_id == tenant)))
        rebuild(s, tenant, run_id)
        knowledge.retrieve(s, tenant, "runtime-lab", {"goal": "calculator"})
        operations.health(s, tenant)
        waits, connections = s.execute(text("""SELECT count(*) FILTER(WHERE wait_event_type='Lock'),count(*)
            FROM pg_stat_activity WHERE application_name='forge-history-pilot'""")).one()
    return {"latency_ms": (time.perf_counter()-started)*1000, "lock_waiters": waits, "connections": connections}


with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
    observations = list(executor.map(measure, range(args.samples)))
latencies = sorted(row["latency_ms"] for row in observations)
with db.engine.connect() as connection:
    postgres = connection.scalar(text("SHOW server_version"))
report = {"kind": "local_read_path_pilot", "tenant": tenant, "run_id": run_id,
    "environment": {"platform": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count(), "postgres": postgres},
    "history": {"checkpoints": args.history+1, "inactive_memories": args.history}, "samples": args.samples,
    "concurrency": args.concurrency, "p50_ms": statistics.median(latencies), "p95_ms": latencies[int(.95*(len(latencies)-1))],
    "peak_sampled_lock_waiters": max(row["lock_waiters"] for row in observations),
    "peak_sampled_connections": max(row["connections"] for row in observations), "observations": observations,
    "limitations": "Shared local host; read-only workload; sampled waits may miss transients. No throughput, write contention, real recovery or production SLO claim. Isolated pilot data retained for reproduction."}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print(json.dumps({key: report[key] for key in ["history", "samples", "concurrency", "p50_ms", "p95_ms", "peak_sampled_lock_waiters"]}))
