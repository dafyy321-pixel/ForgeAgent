"""Provider wire schemas and normalized decisions; the runtime still authorizes every intent."""

import copy
import json

from .config import settings
from .context import decision_schema
from .domain import TOOL_INPUTS, CreateRun, Decision, Fault, digest


def strict_schema(value):
    value = copy.deepcopy(value)
    def visit(node):
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("default", None)
            # Runtime validators retain these bounds; this wire subset is accepted
            # by providers whose strict string schemas expose pattern/format only.
            node.pop("minLength", None)
            node.pop("maxLength", None)
            if node.get("type") == "object":
                if node.get("additionalProperties") not in (None, False):
                    raise Fault("MODEL_SCHEMA_UNSUPPORTED", "Open-ended objects cannot be used in a strict provider schema")
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(value)
    return value


def native_tools(catalog, capabilities):
    tools, names = [], {}
    for name, descriptor in sorted(catalog.items()):
        if descriptor["capability"] not in capabilities or name not in TOOL_INPUTS:
            continue
        wire_name = "forge_" + digest(name)[7:31]
        names[wire_name] = name
        tools.append({"type": "function", "name": wire_name, "description": name,
                      "parameters": strict_schema(TOOL_INPUTS[name].model_json_schema()), "strict": True})
    for kind in ["request_input", "propose_completion", *(["delegate"] if "delegate" in capabilities else [])]:
        wire_name = "forge_" + kind
        names[wire_name] = kind
        schema = {"type": "object", "properties": {"summary": {"type": "string", "maxLength": 5000}}}
        if kind == "delegate":
            child = CreateRun.model_json_schema()
            schema["$defs"] = child.pop("$defs", {})
            schema["properties"]["child"] = child
        tools.append({"type": "function", "name": wire_name, "description": kind,
                      "parameters": strict_schema(schema), "strict": True})
    return tools, names


def structured_schema(state, catalog):
    schema = decision_schema(state)
    schema.get("$defs", {}).pop("ToolCall", None)
    variants = []
    for name, descriptor in sorted(catalog.items()):
        if descriptor["capability"] not in state.get("capabilities", []) or name not in TOOL_INPUTS:
            continue
        variants.append({"type": "object", "properties": {"tool": {"type": "string", "const": name},
                          "args": TOOL_INPUTS[name].model_json_schema()}})
    if not variants:
        variants = [{"type": "object", "properties": {}}]
        schema["properties"]["calls"]["maxItems"] = 0
    schema["properties"]["calls"]["items"] = {"anyOf": variants}
    return strict_schema(schema)


def decode_calls(calls, names):
    if not calls or len(calls) > 8 or len({call["call_id"] for call in calls}) != len(calls):
        raise ValueError("Native tool batch is empty, too large, or repeats call IDs")
    local, controls = [], []
    for call in calls:
        name = names.get(call["name"])
        if not name:
            raise ValueError("Provider called a tool outside the advertised catalog")
        args = json.loads(call["arguments"])
        if name in {"request_input", "propose_completion", "delegate"}:
            controls.append({**args, "kind": name})
        else:
            args = TOOL_INPUTS[name].model_validate(args).model_dump(exclude_none=True)
            local.append({"tool": name, "args": args, "provider_call_id": call["call_id"]})
    if controls:
        if local or len(controls) != 1:
            raise ValueError("Control decisions cannot be mixed with tool effects")
        decision = controls[0]
    else:
        decision = {"kind": "tool_calls", "summary": "Provider proposed scoped tool calls", "calls": local}
    return Decision.model_validate(decision).model_dump_json()


def build_request(messages, state, catalog):
    mode, provider, protocol = settings.model_output_mode, settings.model_provider, settings.model_protocol
    names, tools = {}, []
    messages = copy.deepcopy(messages)
    if mode == "native":
        if not settings.model_supports_tools:
            raise Fault("MODEL_CAPABILITY", "Selected model profile does not support native tools")
        tools, names = native_tools(catalog, state.get("capabilities", []))
        messages[0]["content"] = messages[0]["content"].split("\nJSON decision schema:", 1)[0].replace(
            "Return only a JSON decision matching the supplied schema.", "Use the supplied native functions for scoped actions and control decisions.")
    schema = None
    if mode == "structured":
        if not settings.model_supports_schema:
            raise Fault("MODEL_CAPABILITY", "Selected model profile does not support structured output")
        schema = structured_schema(state, catalog)
    model = state["semantic"]["model_id"]
    if provider == "anthropic":
        payload = {"model": model, "system": messages[0]["content"], "messages": messages[1:], "max_tokens": settings.max_output}
        if tools:
            payload["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        if schema:
            payload["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    elif protocol == "responses":
        payload = {"model": model, "input": messages, "max_output_tokens": settings.max_output,
                   "store": False, "service_tier": "default", "truncation": "disabled"}
        if tools:
            payload.update(tools=tools, parallel_tool_calls=False, include=["reasoning.encrypted_content"])
        elif schema:
            payload["text"] = {"format": {"type": "json_schema", "name": "forge_decision", "strict": True, "schema": schema}}
        else:
            payload["text"] = {"format": {"type": "json_object"}}
    else:
        payload = {"model": model, "messages": messages, "max_completion_tokens": settings.max_output}
        if tools:
            payload.update(tools=[{"type": "function", "function": {k: v for k, v in t.items() if k != "type"}} for t in tools], parallel_tool_calls=False)
        elif schema:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "forge_decision", "strict": True, "schema": schema}}
        else:
            payload["response_format"] = {"type": "json_object"}
    return {"provider": provider, "protocol": protocol, "mode": mode, "names": names, "payload": payload,
            "rate_card": state.get("semantic", {}).get("model_profile")}


def normalize_response(raw, request):
    provider, protocol = request["provider"], request["protocol"]
    calls, continuation, refusal = [], [], None
    if provider == "anthropic":
        status = "completed" if raw.get("stop_reason") in {"end_turn", "tool_use"} else raw.get("stop_reason", "unknown")
        continuation = raw.get("content", [])
        content = "".join(b.get("text", "") for b in continuation if b["type"] == "text")
        calls = [{"name": b["name"], "call_id": b["id"], "arguments": json.dumps(b["input"])} for b in continuation if b["type"] == "tool_use"]
    elif protocol == "responses":
        status, continuation = raw.get("status", "unknown"), raw.get("output", [])
        content = "".join(p.get("text", "") for i in continuation if i.get("type") == "message"
                          for p in i.get("content", []) if p.get("type") == "output_text")
        for item in continuation:
            if item.get("type") == "function_call":
                calls.append({"name": item["name"], "call_id": item["call_id"], "arguments": item["arguments"]})
            if item.get("type") == "message":
                refusal = next((p["refusal"] for p in item["content"] if p.get("type") == "refusal"), refusal)
    else:
        choice = raw.get("choices", [{}])[0]
        status = "completed" if choice.get("finish_reason") in {"stop", "tool_calls"} else choice.get("finish_reason", "unknown")
        message = choice.get("message", {})
        content, refusal, continuation = message.get("content") or "", message.get("refusal"), [message]
        calls = [{"name": c["function"]["name"], "call_id": c["id"], "arguments": c["function"]["arguments"]} for c in message.get("tool_calls", [])]
    if refusal:
        status = "refused"
    if status == "completed" and calls:
        try:
            content = decode_calls(calls, request["names"])
        except (ValueError, TypeError, KeyError) as exc:
            status, content = "invalid_tool_call", json.dumps({"error": str(exc)[:800]})
    elif status == "completed" and request["mode"] == "native":
        content = json.dumps({"kind": "propose_completion", "summary": content[:5000]}) if content else ""
        if not content:
            status = "empty_response"
    return content, {"end_status": status, "refusal": bool(refusal), "calls": calls, "continuation": continuation,
                     "response_id": raw.get("id"), "protocol": protocol, "provider": provider}


def attach_continuation(request, metadata, results):
    if not metadata or not metadata.get("calls"):
        return request
    request = copy.deepcopy(request)
    continuation = metadata["continuation"]
    if metadata.get("provider") != request["provider"] or metadata.get("protocol") != request["protocol"]:
        raise Fault("MODEL_PROTOCOL_DRIFT", "Persisted tool dialogue uses a different protocol")
    outputs = [{"call_id": c["call_id"], "output": json.dumps(results[c["call_id"]], ensure_ascii=False)} for c in metadata["calls"]]
    if request["provider"] == "anthropic":
        request["payload"]["messages"] += [{"role": "assistant", "content": continuation},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": o["call_id"], "content": o["output"]} for o in outputs]}]
    elif request["protocol"] == "responses":
        request["payload"]["input"] += continuation + [{"type": "function_call_output", **o} for o in outputs]
    else:
        request["payload"]["messages"] += continuation + [{"role": "tool", "tool_call_id": o["call_id"], "content": o["output"]} for o in outputs]
    return request
