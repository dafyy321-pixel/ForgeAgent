"""Public response contracts used by OpenAPI and generated console types."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NextAction(BaseModel):
    label: str
    href: str


class Milestone(BaseModel):
    label: str
    done: bool


class MemorySnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    title: str
    content: str
    kind: str
    source: str


class SkillSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    version: str


class ExecutionSnapshot(BaseModel):
    model: str
    skills: list[SkillSnapshot]
    memories: list[MemorySnapshot]
    time: int


class CheckpointView(BaseModel):
    id: str
    workspace: bool
    compatible: bool
    permission: bool
    environment: bool
    environment_status: Literal["ready", "unavailable", "unknown"]


class VerificationCheck(BaseModel):
    name: str
    status: Literal["passed", "failed", "not_run", "not_applicable"]
    evidence: str


class VerificationView(BaseModel):
    status: Literal["not_run", "running", "passed", "failed", "inconclusive"]
    version: int
    checks: list[VerificationCheck]


class AuthConfig(BaseModel):
    mode: Literal["local", "oidc"]
    authority: str | None = None
    client_id: str | None = None
    scope: str | None = None
    audience: str | None = None


class ConversationMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    time: str


class ConversationView(BaseModel):
    initial: str
    messages: list[ConversationMessage]
    mode: Literal["execute", "discuss"]
    plan_ready: bool
    execution_id: str | None
    version: int
    plan_digest: str


class RunView(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    title: str
    description: str
    project: str
    mode: Literal["execute", "discuss"]
    planReady: bool
    executionId: str | None
    sourceDiscussionId: str | None
    sourceRunId: str | None
    updatedAt: int
    waitReason: Literal["APPROVAL", "TOOL", "CHILD_RUN", "RETRY_TIMER", "RECONCILIATION"] | None
    scope: str
    criteria: list[str]
    milestones: list[Milestone]
    snapshot: ExecutionSnapshot
    checkpoint: CheckpointView
    verification: VerificationView
    unknownEffect: bool
    cancelRequested: bool
    version: int
    inputRequired: bool
    progress: int
    model: str
    started: str
    duration: str
    steps: int
    status: Literal["QUEUED", "ACTIVE", "WAITING", "PAUSED", "CANCELLING", "SUCCEEDED", "FAILED", "CANCELLED"]
    phase: str
    reason_code: str | None
    reason_detail: str
    user_message: str
    next_action: NextAction | None
    stateVersion: int = Field(ge=1)
    cost: float = Field(ge=0)
    reserved: float = Field(ge=0)
    budget: float = Field(ge=0)
    tokens: int = Field(ge=0)
    progress_kind: Literal["verified", "indeterminate"]
    budget_unknown: bool
    deadline: int


class RunPage(BaseModel):
    items: list[RunView]
    next_cursor: str | None


class WorkspaceIndex(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: Literal[2] = Field(alias="schema")
    runs: list[RunView]
    approvals: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    memories: list[dict[str, Any]]
    events: list[dict[str, Any]]
    settings: dict[str, Any]
    settings_revision: int
    next_cursor: str | None


class CatalogPage(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None


class FaultResponse(BaseModel):
    code: str
    message: str
    retryable: bool = False
    correlation_id: str | None = None
    errors: list[dict[str, Any]] | None = None
