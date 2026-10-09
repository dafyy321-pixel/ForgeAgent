"""Export the same OpenAPI contract served by the API; no database connection required."""

import json
from pathlib import Path

from forgeagent.api import app
from forgeagent.contracts import FaultResponse

schema = app.openapi()
schema["components"]["schemas"]["FaultResponse"] = FaultResponse.model_json_schema()
for path in schema["paths"].values():
    for operation in path.values():
        if isinstance(operation, dict) and "responses" in operation:
            for code in ("400", "401", "403", "409", "422", "503"):
                operation["responses"][code] = {"description": "Structured API error",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/FaultResponse"}}}}
target = Path(".forge/openapi.json")
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
