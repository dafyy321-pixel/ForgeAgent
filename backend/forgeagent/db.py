from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings
from .domain import Fault, now, uid


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)


class Row:
    tenant_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    id: Mapped[str] = mapped_column(String(100), primary_key=True, default=uid)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Document(Row, Base):
    __abstract__ = True
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="active")


class WorkspaceRevision(Row, Base):
    __tablename__ = "workspace_revisions"
    version: Mapped[int] = mapped_column(BigInteger, default=0)


class Project(Document):
    __tablename__ = "projects"


class TaskSpec(Document):
    __tablename__ = "task_specs"


class Run(Row, Base):
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"]),
        ForeignKeyConstraint(["tenant_id", "task_id"], ["task_specs.tenant_id", "task_specs.id"]),
        ForeignKeyConstraint(["tenant_id", "root_id"], ["runs.tenant_id", "runs.id"]),
        ForeignKeyConstraint(["tenant_id", "parent_id"], ["runs.tenant_id", "runs.id"]),
        UniqueConstraint("tenant_id", "request_key"),
        CheckConstraint(
            "status IN ('QUEUED','ACTIVE','WAITING','PAUSED','CANCELLING','SUCCEEDED','FAILED','CANCELLED')"
        ),
        Index("ix_runs_schedule", "tenant_id", "status", "available_at"),
    )
    project_id: Mapped[str] = mapped_column(String(100))
    task_id: Mapped[str] = mapped_column(String(100))
    root_id: Mapped[str] = mapped_column(String(100))
    parent_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    request_key: Mapped[str] = mapped_column(String(200))
    request_digest: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    phase: Mapped[str] = mapped_column(String(40), default="INITIALIZING")
    wait_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    epoch: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_until: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class RunRow(Row):
    run_id: Mapped[str] = mapped_column(String(100), index=True)


class Event(RunRow, Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", "seq"),
        ForeignKeyConstraint(["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"]),
    )
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)


class Action(RunRow, Base):
    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", "logical_key"),
        ForeignKeyConstraint(["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"]),
    )
    logical_key: Mapped[str] = mapped_column(String(100))
    tool: Mapped[str] = mapped_column(String(200))
    args: Mapped[dict] = mapped_column(JSON)
    effect_class: Mapped[str] = mapped_column(String(50))
    effect_digest: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="PREPARED")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    epoch: Mapped[int] = mapped_column(Integer, default=0)
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class Approval(Row, Base):
    __tablename__ = "approvals"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "action_id"], ["actions.tenant_id", "actions.id"]),)
    action_id: Mapped[str] = mapped_column(String(100))
    effect_digest: Mapped[str] = mapped_column(String(80))
    policy_epoch: Mapped[int] = mapped_column(Integer)
    resource_version: Mapped[str] = mapped_column(String(100))
    decision: Mapped[str] = mapped_column(String(20), default="pending")
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True))


class BudgetAccount(Row, Base):
    __tablename__ = "budget_accounts"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "id"], ["runs.tenant_id", "runs.id"]),
        CheckConstraint("spent >= 0 AND reserved >= 0 AND limit_micros >= 0"),
    )
    limit_micros: Mapped[int] = mapped_column(BigInteger)
    spent: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)
    resources: Mapped[dict] = mapped_column(JSON, default=dict)


class BudgetEntry(Document):
    __tablename__ = "budget_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "operation_id"),
        ForeignKeyConstraint(["tenant_id", "account_id"], ["budget_accounts.tenant_id", "budget_accounts.id"]),
    )
    account_id: Mapped[str] = mapped_column(String(100))
    operation_id: Mapped[str] = mapped_column(String(100))
    reserved: Mapped[int] = mapped_column(BigInteger)
    actual: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Artifact(RunRow, Base):
    __tablename__ = "artifacts"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"]),)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))
    ref: Mapped[dict] = mapped_column(JSON)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)


# Versioned domain records have an immutable JSON payload; lifecycle status is separate.
def record(name, table, run_bound=False):
    fields = {"__tablename__": table, "__module__": __name__}
    if run_bound:
        fields["run_id"] = mapped_column(String(100), index=True)
        fields["__table_args__"] = (ForeignKeyConstraint(["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"]),)
    return type(name, (Document,), fields)


AgentVersion = record("AgentVersion", "agent_versions")
Job = record("Job", "jobs", True)
Turn = record("Turn", "turns", True)
ModelCall = record("ModelCall", "model_calls", True)
Attempt = record("Attempt", "action_attempts", True)
Checkpoint = record("Checkpoint", "checkpoints", True)
SemanticManifest = record("SemanticManifest", "semantic_manifests", True)
ContextManifest = record("ContextManifest", "context_manifests", True)
SandboxRecord = record("SandboxRecord", "sandboxes", True)
WorkspaceSnapshot = record("WorkspaceSnapshot", "workspace_snapshots", True)
ToolVersion = record("ToolVersion", "tool_versions")
SkillVersion = record("SkillVersion", "skill_versions")
Memory = record("Memory", "memories")
PolicyVersion = record("PolicyVersion", "policy_versions")
Authorization = record("Authorization", "authorization_epochs")
VerificationResult = record("VerificationResult", "verification_results", True)
Outbox = record("Outbox", "outbox")
Inbox = record("Inbox", "inbox")
Evaluation = record("Evaluation", "evaluation_runs")
EvaluationDataset = record("EvaluationDataset", "evaluation_datasets")
EvaluationResult = record("EvaluationResult", "evaluation_results", True)

engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=12, max_overflow=8)
Session = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def transaction(tenant):
    with Session.begin() as s:
        s.execute(text("SELECT set_config('forge.tenant_id', :tenant, true)"), {"tenant": tenant})
        yield s


def clock(s):
    return s.scalar(select(__import__("sqlalchemy").func.clock_timestamp()))


def get(s, cls, tenant, id, lock=False):
    query = select(cls).where(cls.tenant_id == tenant, cls.id == id)
    if lock:
        query = query.with_for_update()
    value = s.scalar(query)
    if value is None:
        raise Fault("NOT_FOUND", "Resource not found", 404)
    return value


def rows(s, cls, tenant, **filters):
    query = select(cls).where(cls.tenant_id == tenant)
    for key, value in filters.items():
        query = query.where(getattr(cls, key) == value)
    return list(s.scalars(query.order_by(cls.created_at, cls.id)))


def list_rows(s, cls, tenant, limit=100, **filters):
    """Bound legacy console lists; use the signed catalog cursor for later pages."""
    query = select(cls).where(cls.tenant_id == tenant)
    for key, value in filters.items():
        query = query.where(getattr(cls, key) == value)
    return list(s.scalars(query.order_by(cls.created_at.desc(), cls.id.desc()).limit(limit)))


def projection(run):
    return {
        "status": run.status,
        "phase": run.phase,
        "wait_reason": run.wait_reason,
        "version": run.version,
        "epoch": run.epoch,
        "cancel_requested": run.cancel_requested,
        "pause_requested": run.pause_requested,
        "state": run.state,
    }


def emit(s, run, kind, message, **details):
    from .domain import digest
    from .reducer import EventReducer, rebuild

    cache_key = (run.tenant_id, run.id)
    cached = s.info.setdefault("event_projections", {}).get(cache_key)
    previous = cached[1] if cached and cached[0] == run.seq else rebuild(s, run.tenant_id, run.id)
    run.seq += 1
    run.version += 1
    run.updated_at = clock(s)
    current = deepcopy(projection(run))
    payload = {"schema_version": 2, "message": message, "transition": EventReducer.transition(previous, current),
               "prior_digest": digest(previous), "projection_digest": digest(current), **details}
    s.add(
        Event(
            tenant_id=run.tenant_id,
            run_id=run.id,
            seq=run.seq,
            type=kind,
            payload=payload,
        )
    )
    s.flush()
    s.info["event_projections"][cache_key] = (run.seq, current)


def fence(s, tenant, id, owner, epoch):
    r = get(s, Run, tenant, id, True)
    if r.lease_owner != owner or r.epoch != epoch or not r.lease_until or r.lease_until <= clock(s):
        raise Fault("STALE_LEASE", "Worker lease expired or superseded")
    return r


def schedule(s, run, seconds=0):
    run.available_at = clock(s) + timedelta(seconds=seconds)
    job = s.get(Job, (run.tenant_id, run.id))
    if not job:
        job = Job(tenant_id=run.tenant_id, id=run.id, run_id=run.id, data={})
        s.add(job)
    job.status = "READY"
    job.data = {"available_at": run.available_at.isoformat(), "kind": "advance", "epoch": run.epoch}
