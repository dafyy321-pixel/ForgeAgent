import hashlib
import io
import os
import re
import tempfile

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .config import settings
from .domain import Fault, canonical, uid
from .telemetry import observed


def storage_fault(exc):
    """Stable public errors without endpoints, credentials or provider messages."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"NoSuchKey", "NotFound", "404"}:
            return Fault("OBJECT_MISSING", "Referenced evidence is missing", 404)
        if code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "NoSuchBucket"}:
            return Fault("OBJECT_DEPENDENCY", "Object storage configuration or authorization failed", 503)
    return Fault("OBJECT_UNAVAILABLE", "Object storage is temporarily unavailable; retry bounded reads", 503, retryable=True)


class ObjectStore:
    def __init__(self):
        self.root = settings.data_dir.resolve() / "objects"
        self.s3 = (
            boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                aws_session_token=settings.s3_session_token or None,
                config=Config(connect_timeout=5, read_timeout=30, max_pool_connections=settings.worker_slots + 2,
                              retries={"mode": "standard", "total_max_attempts": 3}),
            )
            if settings.s3_endpoint
            else None
        )

    def put(self, tenant, run_id, value):
        body = value if isinstance(value, bytes) else canonical(value)
        return self.put_stream(tenant, run_id, io.BytesIO(body))

    @observed("object.publish")
    def put_stream(self, tenant, run_id, source):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", run_id):
            raise Fault("OBJECT_SCOPE", "Invalid object namespace", 422)
        scope = hashlib.sha256(tenant.encode()).hexdigest()
        with tempfile.SpooledTemporaryFile(max_size=settings.object_spool_bytes) as spool:
            size, hasher = 0, hashlib.sha256()
            while chunk := source.read(65536):
                size += len(chunk)
                if size > settings.max_object_bytes:
                    raise Fault("OBJECT_TOO_LARGE", "Object exceeds configured limit", 413)
                hasher.update(chunk)
                spool.write(chunk)
            checksum = "sha256:" + hasher.hexdigest()
            key = f"{scope}/{run_id}/{checksum[7:]}"
            ref = {"key": key, "digest": checksum, "bytes": size}
            spool.seek(0)
            if self.s3:
                self.upload(key, spool, size, checksum)
            else:
                import shutil

                path = self.local_path(key)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.parent / (uid() + ".tmp")
                try:
                    with temporary.open("xb") as handle:
                        shutil.copyfileobj(spool, handle, 65536)
                        handle.flush()
                        os.fsync(handle.fileno())
                    # Content addresses are immutable. Replacing an existing object can
                    # fail on Windows while another reader has it open.
                    try:
                        os.link(temporary, path)
                    except FileExistsError:
                        pass  # The winner is verified below, including concurrent writes.
                finally:
                    temporary.unlink(missing_ok=True)
            # Stream verification avoids allocating a second full object during publication.
            self.verify(tenant, ref)
            return ref

    def local_path(self, key):
        path = self.root / key
        if path.resolve() != path.absolute() or not path.resolve().is_relative_to(self.root.resolve()):
            raise Fault("OBJECT_SCOPE", "Object path escaped managed storage", 403)
        return path

    def upload(self, key, spool, size, checksum):
        assert self.s3 is not None
        upload_id = None
        options = {"Bucket": settings.s3_bucket, "Key": key}
        try:
            metadata = {"ServerSideEncryption": "AES256", "Metadata": {"sha256": checksum[7:]}}
            if size < settings.s3_multipart_bytes:
                self.s3.put_object(**options, Body=spool, **metadata)
                return
            upload_id = self.s3.create_multipart_upload(**options, **metadata)["UploadId"]
            parts = []
            while chunk := spool.read(settings.s3_multipart_bytes):
                result = self.s3.upload_part(**options, UploadId=upload_id, PartNumber=len(parts) + 1, Body=chunk)
                parts.append({"PartNumber": len(parts) + 1, "ETag": result["ETag"]})
            self.s3.complete_multipart_upload(**options, UploadId=upload_id, MultipartUpload={"Parts": parts})
        except (BotoCoreError, ClientError) as exc:
            if upload_id:
                try:
                    self.s3.abort_multipart_upload(**options, UploadId=upload_id)
                except (BotoCoreError, ClientError):
                    pass  # Bucket lifecycle/maintenance reaps abandoned uploads even when abort is unavailable.
            raise storage_fault(exc) from None

    def open(self, tenant, ref):
        scope = hashlib.sha256(tenant.encode()).hexdigest()
        key = ref["key"]
        if not re.fullmatch(rf"{scope}/[a-zA-Z0-9_-]+/[a-f0-9]{{64}}", key):
            raise Fault("OBJECT_SCOPE", "Object reference does not belong to this tenant", 404)
        if not isinstance(ref.get("bytes"), int) or not 0 <= ref["bytes"] <= settings.max_object_bytes:
            raise Fault("OBJECT_LIMIT", "Object reference size exceeds the read limit", 413)
        try:
            if self.s3:
                response = self.s3.get_object(Bucket=settings.s3_bucket, Key=key)
                if response.get("ContentLength", ref["bytes"]) != ref["bytes"]:
                    response["Body"].close()
                    raise Fault("OBJECT_CORRUPT", "Object length differs from its immutable reference")
                return response["Body"]
            return self.local_path(key).open("rb")
        except FileNotFoundError:
            raise Fault("OBJECT_MISSING", "Referenced evidence is missing", 404) from None
        except (BotoCoreError, ClientError) as exc:
            raise storage_fault(exc) from None

    @observed("object.verify")
    def verify(self, tenant, ref):
        size, hasher = 0, hashlib.sha256()
        try:
            with self.open(tenant, ref) as source:
                while chunk := source.read(65536):
                    size += len(chunk)
                    if size > ref["bytes"]:
                        raise Fault("OBJECT_CORRUPT", "Object exceeds its immutable size")
                    hasher.update(chunk)
        except (BotoCoreError, ClientError) as exc:
            raise storage_fault(exc) from None
        if size != ref["bytes"] or "sha256:" + hasher.hexdigest() != ref["digest"]:
            raise Fault("OBJECT_CORRUPT", "Object length or checksum mismatch")

    @observed("object.read")
    def get(self, tenant, ref):
        try:
            with self.open(tenant, ref) as source:
                body = bytearray()
                while chunk := source.read(min(65536, ref["bytes"] + 1 - len(body))):
                    body.extend(chunk)
                    if len(body) > ref["bytes"]:
                        break
        except (BotoCoreError, ClientError) as exc:
            raise storage_fault(exc) from None
        if len(body) != ref["bytes"] or "sha256:" + hashlib.sha256(body).hexdigest() != ref["digest"]:
            raise Fault("OBJECT_CORRUPT", "Object length or checksum mismatch")
        return bytes(body)

    def inventory(self, tenant, namespace):
        """List every version, including markers; callers decide reference/retention policy."""
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
            raise Fault("OBJECT_SCOPE", "Invalid object namespace", 422)
        scope = hashlib.sha256(tenant.encode()).hexdigest()
        prefix = f"{scope}/{namespace}/"
        if self.s3:
            try:
                for page in self.s3.get_paginator("list_object_versions").paginate(Bucket=settings.s3_bucket, Prefix=prefix):
                    for value in page.get("Versions", []) + page.get("DeleteMarkers", []):
                        if not value["Key"].startswith(prefix):
                            raise Fault("OBJECT_SCOPE", "Storage returned a key outside the requested namespace", 503)
                        yield {"key": value["Key"], "version": value["VersionId"], "bytes": value.get("Size", 0),
                               "modified": value["LastModified"].timestamp() if "LastModified" in value else float("inf")}
            except (BotoCoreError, ClientError) as exc:
                raise storage_fault(exc) from None
        else:
            directory = self.local_path(prefix.rstrip("/"))
            if directory.is_dir():
                for path in directory.iterdir():
                    if path.is_file() and not path.is_symlink():
                        self.local_path(prefix + path.name)
                        yield {"key": prefix + path.name, "bytes": path.stat().st_size,
                               "modified": path.stat().st_mtime}

    @observed("object.delete")
    def delete(self, entries):
        if self.s3:
            try:
                for offset in range(0, len(entries), 1000):
                    batch = [{"Key": item["key"], **({"VersionId": item["version"]} if "version" in item else {})}
                             for item in entries[offset:offset + 1000]]
                    result = self.s3.delete_objects(Bucket=settings.s3_bucket, Delete={"Objects": batch})
                    if result.get("Errors"):
                        raise Fault("OBJECT_DELETE_FAILED", "S3 rejected some version deletions", 503)
            except (BotoCoreError, ClientError) as exc:
                raise storage_fault(exc) from None
        else:
            for entry in entries:
                self.local_path(entry["key"]).unlink(missing_ok=True)

    def purge_scope(self, tenant, run_id):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", run_id):
            raise Fault("OBJECT_SCOPE", "Invalid deletion scope", 422)
        scope = hashlib.sha256(tenant.encode()).hexdigest()
        if self.s3:
            # Delete historical versions and delete markers too; deleting only the
            # latest object leaves recoverable sensitive data in versioned buckets.
            batch = []
            for entry in self.inventory(tenant, run_id):
                batch.append(entry)
                if len(batch) == 1000:
                    self.delete(batch)
                    batch.clear()
            if batch:
                self.delete(batch)
        else:
            import shutil

            requested = self.root / scope / run_id
            path = requested.resolve()
            if path != requested.absolute() or not path.is_relative_to(self.root.resolve()) or path == self.root.resolve():
                raise Fault("OBJECT_SCOPE", "Deletion escaped the object root")
            if path.exists():
                shutil.rmtree(path)


objects = ObjectStore()
