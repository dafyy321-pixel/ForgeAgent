"""Administrator-reviewed tool contracts and externally configured, origin-bound secrets."""

import copy
import json
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import Field, model_validator

from . import db
from .config import settings
from .domain import Fault, Strict, canonical, digest


class RemoteTool(Strict):
    operation: str = Field(min_length=1, max_length=100)
    description: str = Field("", max_length=1000)
    input_schema: dict
    effect: str = Field(pattern=r"^(read|external_write|irreversible)$")
    idempotency_field: str | None = None
    cas_field: str | None = None
    reconcile_operation: str | None = None
    reconcile_key_field: str | None = None

    @model_validator(mode="after")
    def contract(self):
        validate_schema(self.input_schema)
        properties = self.input_schema.get("properties", {})
        for field in [self.idempotency_field, self.cas_field]:
            if field and (field not in properties or properties[field].get("type") != "string"):
                raise ValueError("Idempotency/CAS fields must be declared string properties")
        if self.cas_field and self.cas_field not in self.input_schema.get("required", []):
            raise ValueError("CAS precondition must be required")
        if bool(self.reconcile_operation) != bool(self.reconcile_key_field):
            raise ValueError("Reconciliation requires an operation and lookup field")
        if self.reconcile_operation and not self.idempotency_field:
            raise ValueError("Reconciliation lookup requires a business idempotency key")
        return self


def validate_schema(schema):
    if len(canonical(schema)) > 32768 or schema.get("type") != "object":
        raise ValueError("Remote input schema must be a bounded object")
    def visit(value):
        if isinstance(value, dict):
            if "$ref" in value and not value["$ref"].startswith("#/$defs/"):
                raise ValueError("External JSON Schema references are forbidden")
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("Invalid remote JSON Schema") from exc


def validate_arguments(schema, arguments):
    if len(canonical(arguments)) > 65536:
        raise Fault("REMOTE_ARGUMENTS", "Remote arguments exceed 64 KiB", 422)
    try:
        Draft202012Validator(schema).validate(arguments)
    except (ValidationError, SchemaError, RecursionError) as exc:
        raise Fault("REMOTE_ARGUMENTS", "Arguments do not satisfy the reviewed remote schema", 422) from exc


def validate_protocol_arguments(connection, arguments):
    if connection["kind"] != "a2a":
        return
    if connection["protocol"] == "0.3.0":
        from a2a.types import MessageSendParams
        from pydantic import ValidationError as InputError

        try:
            MessageSendParams.model_validate(arguments)
        except InputError:
            raise Fault("A2A_ARGUMENTS", "Message does not satisfy the pinned legacy A2A format", 422) from None
        return
    message = arguments.get("message", {})
    if (not isinstance(message, dict) or not isinstance(message.get("messageId"), str) or not message["messageId"] or message.get("role") != "ROLE_USER"
        or not isinstance(message.get("parts"), list) or not message["parts"]):
        raise Fault("A2A_ARGUMENTS", "A2A 1.0 requires messageId, ROLE_USER and member-discriminated parts", 422)
    for part in message["parts"]:
        if not isinstance(part, dict) or "kind" in part or sum(k in part for k in ["text", "data", "raw", "url"]) != 1:
            raise Fault("A2A_ARGUMENTS", "A2A 1.0 parts require exactly one content member and no legacy kind", 422)
        if any(k in part and not isinstance(part[k], str) for k in ["text", "raw", "url"]):
            raise Fault("A2A_ARGUMENTS", "Text, raw and URL part members must be strings", 422)


def credentials(connection, reference=None):
    reference = reference or connection.get("credential_ref")
    if not reference:
        return {}
    try:
        value = json.loads(settings.remote_credentials)[reference]
        endpoint = urlsplit(connection["url"])
        bound = urlsplit(value["origin"])
        if (value["tenant_id"] != connection["tenant_id"] or bound.scheme != "https"
            or (bound.scheme, bound.hostname, bound.port or 443) != (endpoint.scheme, endpoint.hostname, endpoint.port or 443)):
            raise ValueError("scope mismatch")
        return value
    except (ValueError, KeyError, TypeError):
        raise Fault("REMOTE_CREDENTIALS", "Credential reference is unavailable for this tenant/origin", 503) from None


def headers(connection):
    value = credentials(connection)
    authorization = value.get("authorization")
    if authorization and ("\r" in authorization or "\n" in authorization):
        raise Fault("REMOTE_CREDENTIALS", "Invalid authorization header", 503)
    return {"Authorization": authorization} if authorization else {}


def tool_name(connection_id, operation):
    return "remote_" + digest([connection_id, operation])[7:31]


def pin(s, tenant, ids, parent=None):
    if len(ids) != len(set(ids)):
        raise Fault("CONNECTION_DUPLICATE", "Remote connection selection repeats an ID", 422)
    if parent and not set(ids) <= {c["id"] for c in parent.state.get("remote_connections", [])}:
        raise Fault("CHILD_SCOPE", "Child remote connections must be a subset of the parent", 403)
    result = []
    for key in ids:
        current = db.get(s, db.ToolVersion, tenant, key)
        if current.status != "active" or not current.data.get("negotiated") or not current.data.get("tools"):
            raise Fault("CONNECTION_UNVERIFIED", "Discover and review remote contracts before task selection")
        snapshot = copy.deepcopy(current.data)
        if parent:
            snapshot = next(c["data"] for c in parent.state["remote_connections"] if c["id"] == key)
            if digest(snapshot) != digest(current.data):
                raise Fault("CONNECTION_CHANGED", "Parent remote binding has changed")
        result.append({"id": key, "digest": digest(snapshot), "data": snapshot})
    return result


def catalog(state):
    from .service import TOOLS

    result = copy.deepcopy(TOOLS)
    for connection in state.get("remote_connections", []):
        for tool in connection["data"]["tools"]:
            schema = copy.deepcopy(tool["input_schema"])
            if tool.get("idempotency_field"):
                field = tool["idempotency_field"]
                # This key is runtime-owned, never chosen by a model.
                schema.get("properties", {}).pop(field, None)
                schema["required"] = [p for p in schema.get("required", []) if p != field]
            result[tool_name(connection["id"], tool["operation"])] = {
                "effect": tool["effect"], "capability": "external.read" if tool["effect"] == "read" else "external.write",
                "input_schema": schema, "description": tool["description"] or tool["operation"],
                "connection_id": connection["id"], "operation": tool["operation"],
            }
    return result


def current(s, run, connection_id):
    value = db.get(s, db.ToolVersion, run.tenant_id, connection_id)
    pinned = next((c for c in run.state.get("remote_connections", []) if c["id"] == connection_id), None)
    if not pinned or value.status != "active" or digest(value.data) != pinned["digest"]:
        raise Fault("CONNECTION_CHANGED", "Remote connection is disabled, unselected, or changed since task binding")
    return pinned["data"]
