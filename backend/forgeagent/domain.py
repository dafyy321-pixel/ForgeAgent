import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(UTC)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def digest(value):
    return "sha256:" + hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


class Fault(Exception):
    def __init__(self, code, message, status=409, retryable=False):
        self.code, self.message, self.status, self.retryable = code, message, status, retryable
        super().__init__(message)


TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
UNSETTLED = {"DISPATCHED", "RUNNING", "UNKNOWN", "CANCEL_REQUESTED"}
CAPABILITIES = {"repo.read", "workspace.write", "tests.run", "delegate", "external.write"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Budget(Strict):
    max_cost_usd: Annotated[str, Field(pattern=r"^\d+(\.\d{1,6})?$")] = "5.00"
    max_turns: int = Field(100, ge=1, le=1000)
    max_wall_seconds: int = Field(7200, ge=10, le=86400)
    max_tool_calls: int = Field(300, ge=1, le=3000)
    max_tokens: int = Field(500000, ge=1, le=10000000)
    max_repair_attempts: int = Field(2, ge=0, le=10)
    max_no_progress_turns: int = Field(12, ge=3, le=100)
    max_concurrent_runs: int = Field(2, ge=1, le=32)


class Task(Strict):
    goal: str = Field(min_length=1, max_length=12000)
    allowed_paths: list[str] = Field(default_factory=lambda: ["src", "tests"], min_length=1, max_length=100)
    acceptance_profile: str = ""
    deliverables: list[str] = Field(default_factory=lambda: ["patch", "test_report", "summary"])
    criteria: list[str] = Field(default_factory=list, max_length=30)
    scope: str = ""

    @model_validator(mode="after")
    def paths(self):
        for path in self.allowed_paths:
            if not path or path.startswith(("/", "\\")) or ":" in path or ".." in path.replace("\\", "/").split("/"):
                raise ValueError("allowed_paths must be relative workspace paths")
        return self


class Harness(Strict):
    context_policy: Literal["elide", "full"] = "elide"
    memory: bool = True
    observation_recall: bool = True


class ChildContract(Strict):
    required: bool = True
    deadline_seconds: int | None = Field(None, ge=1, le=86400)
    join: Literal["report", "integrate"] = "report"
    deliverables: list[Literal["patch", "test_report", "summary"]] = Field(default_factory=lambda: ["summary"])


class CreateRun(Strict):
    project_id: str
    title: str = Field("", max_length=160)
    agent_version: str = "coding@1.0.0"
    task: Task
    budget: Budget = Field(default_factory=Budget)
    capabilities: list[str] = Field(default_factory=lambda: ["repo.read", "workspace.write", "tests.run"])
    model: str = "configured"
    skills: list[str] = Field(default_factory=list)
    harness: Harness = Field(default_factory=Harness)
    child_contract: ChildContract = Field(default_factory=ChildContract)

    @model_validator(mode="after")
    def caps(self):
        if not set(self.capabilities) <= CAPABILITIES:
            raise ValueError("unknown capability")
        return self


class Control(Strict):
    expected_version: int = Field(ge=1)
    reason: str = Field("User request", max_length=2000)
    checkpoint_id: str | None = None
    executor: Literal["current", "archived"] = "current"


class ApprovalDecision(Control):
    decision: Literal["approve", "deny"]
    effect_digest: str


class ToolCall(Strict):
    tool: str
    args: dict


class ReadArgs(Strict):
    path: str = Field(min_length=1, max_length=500)


class WriteArgs(ReadArgs):
    content: str = Field(max_length=1_000_000)
    expected_digest: str = Field(pattern=r"^(absent|sha256:[0-9a-f]{64})$")


class TestArgs(Strict):
    argv: list[str] = Field(min_length=1, max_length=100)


class RecallArgs(Strict):
    action_id: str
    line_start: int = Field(0, ge=0)
    max_lines: int = Field(100, ge=1, le=500)


class IntegrateArgs(Strict):
    child_id: str


TOOL_INPUTS = {
    "repo.list": Strict,
    "repo.read": ReadArgs,
    "repo.write": WriteArgs,
    "tests.run": TestArgs,
    "observation.read": RecallArgs,
    "child.integrate": IntegrateArgs,
}


class Decision(Strict):
    kind: Literal["tool_calls", "delegate", "request_input", "propose_completion"]
    summary: str = Field(max_length=5000)
    calls: list[ToolCall] = Field(default_factory=list, max_length=8)
    child: CreateRun | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.kind == "tool_calls" and not self.calls:
            raise ValueError("tool_calls requires calls")
        if self.kind == "delegate" and self.child is None:
            raise ValueError("delegate requires child")
        if self.kind != "tool_calls" and self.calls:
            raise ValueError("calls only permitted for tool_calls")
        return self
