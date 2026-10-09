"""Public response contracts used by OpenAPI and generated console types."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RunView(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    title: str
    status: Literal["QUEUED", "ACTIVE", "WAITING", "PAUSED", "CANCELLING", "SUCCEEDED", "FAILED", "CANCELLED"]
    phase: str
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
