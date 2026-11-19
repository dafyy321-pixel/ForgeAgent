"""Operation deduplication and run-scoped output retention for the shared manager."""

import json
import os
import re
import shutil
from pathlib import Path

from .config import settings
from .domain import Fault, canonical, digest, uid


def checked(base, path):
    if path.resolve() != path.absolute() or not path.is_relative_to(base):
        raise Fault("MANAGER_RECEIPT_SCOPE", "Manager receipt path is outside managed storage")
    return path


def bind_claim(base, operation, request_digest):
    # Publish a complete digest atomically so another manager cannot read a partial claim.
    claims = checked(base, base / "claims")
    claims.mkdir(parents=True, exist_ok=True)
    destination = checked(base, claims / operation)
    temporary = claims / ("pending-" + uid())
    temporary.write_text(request_digest, encoding="ascii")
    try:
        try:
            os.link(temporary, destination)
            return True
        except FileExistsError:
            if destination.read_text(encoding="ascii") != request_digest:
                raise Fault("MANAGER_CONFLICT", "Operation ID already names a different request")
            return False
    finally:
        temporary.unlink(missing_ok=True)


def prepare_receipt(payload):
    base = settings.data_dir.resolve() / "sandbox-manager"
    workspace = Path(payload["root"])
    workspaces = settings.data_dir.resolve() / "workspaces"
    if not workspace.is_relative_to(workspaces) or ".." in workspace.parts or workspace == workspaces:
        raise Fault("PATH_DENIED", "Manager only mounts managed workspace directories", 403)
    parts = workspace.relative_to(workspaces).parts
    if len(parts) >= 3 and re.fullmatch(r"[a-f0-9]{16}", parts[0]) and re.fullmatch(r"[a-z0-9-]{36}", parts[1]):
        owner = Path(parts[0], parts[1])
    else:
        # Older clients can use a standalone managed directory for foreground checks.
        owner = Path("standalone", digest(str(workspace))[7:])
    operation = payload["operation"]
    directory = checked(base, base / "runs" / owner)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        if directory.is_file():
            raise Fault("MANAGER_RECEIPT_ERASED", "Task receipts have been erased", 410) from exc
        raise
    legacy = checked(base, base / operation)
    if legacy.exists():
        if checked(base, legacy / "request.json").read_bytes() != canonical(payload):
            raise Fault("MANAGER_CONFLICT", "Operation ID already names a different request")
        receipt = checked(base, legacy / "receipt.json")
    else:
        receipt = checked(base, directory / (operation + ".json"))
        if bind_claim(base, operation, digest(payload)):
            return receipt, None
    if receipt.is_file():
        return receipt, json.loads(receipt.read_bytes())
    raise Fault("MANAGER_INTERRUPTED", "Prior dispatch has no durable result; use a new bounded attempt")


def purge_receipts(tenant, ids):
    base = settings.data_dir.resolve() / "sandbox-manager"
    if not ids or (not base.exists() and not settings.sandbox_manager_url):
        return
    scope = digest(tenant)[7:23]
    runs = set(ids)
    for run_id in runs:
        directory = checked(base, base / "runs" / scope / run_id)
        directory.parent.mkdir(parents=True, exist_ok=True)
        # A regular file at the former directory path prevents even late in-flight
        # writers from creating children after the purge completes.
        while not directory.is_file():
            if directory.is_dir():
                shutil.rmtree(directory)
            try:
                directory.touch(exist_ok=False)
            except FileExistsError:
                continue
        removing = checked(base, base / "legacy-erasure" / scope / run_id)
        if removing.exists():
            shutil.rmtree(removing)

    # Legacy claims contain the original argv and absolute workspace path. Inspect only
    # the known operation directory layout; keep digest-only deduplication after erasure.
    for directory in base.iterdir():
        if not re.fullmatch(r"[a-z0-9-]{36}", directory.name):
            continue
        claim = checked(base, directory / "request.json")
        try:
            payload = json.loads(claim.read_bytes())
            workspace = Path(payload["root"])
        except FileNotFoundError:
            if directory.exists() and any(directory.iterdir()):
                raise Fault("MANAGER_RECEIPT_SCOPE", "Legacy receipt is missing its request metadata") from None
            continue
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise Fault("MANAGER_RECEIPT_SCOPE", "Legacy receipt ownership could not be determined") from exc
        workspaces = settings.data_dir.resolve() / "workspaces"
        if not workspace.is_relative_to(workspaces) or ".." in workspace.parts:
            if any(part in runs for part in workspace.parts):
                raise Fault("MANAGER_RECEIPT_SCOPE", "Restore the original workspace root to attribute this legacy receipt")
            continue
        parts = workspace.relative_to(workspaces).parts
        if len(parts) >= 3 and parts[0] == scope and parts[1] in runs:
            bind_claim(base, directory.name, digest(payload))
            removing = checked(base, base / "legacy-erasure" / scope / parts[1] / directory.name)
            removing.parent.mkdir(parents=True, exist_ok=True)
            directory.rename(removing)
            shutil.rmtree(removing)
