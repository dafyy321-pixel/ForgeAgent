import hashlib
import re

import boto3

from .config import settings
from .domain import Fault, canonical, digest, uid


class ObjectStore:
    def __init__(self):
        self.root = settings.data_dir.resolve() / "objects"
        self.s3 = (
            boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
            )
            if settings.s3_endpoint
            else None
        )

    def put(self, tenant, run_id, value):
        body = value if isinstance(value, bytes) else canonical(value)
        if len(body) > settings.max_object_bytes:
            raise Fault("OBJECT_TOO_LARGE", "Object exceeds configured limit", 413)
        checksum = digest(body)
        scope = hashlib.sha256(tenant.encode()).hexdigest()
        key = f"{scope}/{run_id}/{checksum[7:]}"
        ref = {"key": key, "digest": checksum, "bytes": len(body)}
        if self.s3:
            self.s3.put_object(
                Bucket=settings.s3_bucket,
                Key=key,
                Body=body,
                ServerSideEncryption="AES256",
                Metadata={"sha256": checksum[7:]},
            )
        else:
            path = self.root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix("." + uid() + ".tmp")
            temporary.write_bytes(body)
            temporary.replace(path)
        self.get(tenant, ref)
        return ref

    def get(self, tenant, ref):
        scope = hashlib.sha256(tenant.encode()).hexdigest()
        key = ref["key"]
        if not re.fullmatch(rf"{scope}/[a-zA-Z0-9_-]+/[a-f0-9]{{64}}", key):
            raise Fault("OBJECT_SCOPE", "Object reference does not belong to this tenant", 404)
        if self.s3:
            body = self.s3.get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read(settings.max_object_bytes + 1)
        else:
            try:
                body = (self.root / key).read_bytes()
            except FileNotFoundError:
                raise Fault("OBJECT_MISSING", "Referenced evidence is missing")
        if len(body) != ref["bytes"] or digest(body) != ref["digest"]:
            raise Fault("OBJECT_CORRUPT", "Object length or checksum mismatch")
        return body


objects = ObjectStore()
