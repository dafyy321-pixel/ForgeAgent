"""Public artifact bytes and their matching download metadata."""

import json
from functools import lru_cache

from . import db, service
from .domain import canonical, digest
from .storage import objects


def _legacy_report(artifact):
    return artifact.kind == "test_report" and artifact.ref.get("public_report_version") != 1


def _protected(s, artifact):
    contract = db.get(s, db.Run, artifact.tenant_id, artifact.run_id).state["acceptance"]
    return bool(contract.get("protected_tests") or contract.get("protected_tests_ref"))


def _redact(body, protected):
    return canonical(service.public_report({"protected_tests": protected}, json.loads(body)))


def body(s, artifact):
    data = objects.get(artifact.tenant_id, artifact.ref)
    return _redact(data, _protected(s, artifact)) if _legacy_report(artifact) else data


@lru_cache(maxsize=512)
def _legacy_metadata(tenant, key, checksum, size, protected):
    # Historical rows point at immutable private evidence. Cache only its public size and digest.
    data = _redact(objects.get(tenant, {"key": key, "digest": checksum, "bytes": size}), protected)
    return digest(data), len(data)


def metadata(s, artifact):
    ref = artifact.ref
    if _legacy_report(artifact):
        return _legacy_metadata(artifact.tenant_id, ref["key"], ref["digest"], ref["bytes"], _protected(s, artifact))
    return ref["digest"], ref["bytes"]
