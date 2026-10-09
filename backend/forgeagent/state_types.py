"""Types at persisted JSON boundaries. Runtime validation and version bindings remain authoritative."""

from typing import Any, TypedDict


class RunState(TypedDict, total=False):
    turn: int
    tokens: int
    artifact_version: int
    input_revision: int
    format_errors: int
    repeats: int
    repair_attempts: int
    integrated_children: list[str]


class ActionReceipt(TypedDict, total=False):
    exit_code: int
    output: str
    timed_out: bool
    truncated: bool
    error_code: str
    environment: dict[str, Any]
    workspace_digest: str
    changed_paths: list[str]
    output_ref: dict[str, Any]
    protected_output: bool


class ModelReceipt(TypedDict, total=False):
    response_ref: dict[str, Any]
    usage: dict[str, Any] | None
    provider_request_id: str | None
    receipt_state: str
    decision: dict[str, Any] | None
    validation_error: str | None
    received_at: str
    end_status: str
    protocol_receipt: dict[str, Any]
    billing_error: str | None


class EvaluationResult(TypedDict):
    id: str
    status: str
    cost: float
    verdict: str | None


