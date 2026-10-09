import io
import os
from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError
from forgeagent.config import settings
from forgeagent.domain import Fault
from forgeagent.storage import ObjectStore, storage_fault


class S3:
    def __init__(self):
        self.data, self.parts, self.deleted = {}, [], []
        self.fail, self.aborted = False, False

    def put_object(self, **kw):
        self.data[kw["Key"]] = kw["Body"].read()

    def get_object(self, **kw):
        value = self.data[kw["Key"]]
        return {"Body": io.BytesIO(value), "ContentLength": len(value)}

    def create_multipart_upload(self, **kw):
        return {"UploadId": "upload"}

    def upload_part(self, **kw):
        if self.fail:
            raise ClientError({"Error": {"Code": "SlowDown", "Message": "secret"}}, "UploadPart")
        self.parts.append(kw["Body"])
        return {"ETag": str(kw["PartNumber"])}

    def complete_multipart_upload(self, **kw):
        self.data[kw["Key"]] = b"".join(self.parts)

    def abort_multipart_upload(self, **kw):
        self.aborted = True

    def delete_objects(self, **kw):
        self.deleted.append(kw["Delete"]["Objects"])
        return {}

    def get_paginator(self, name):
        return self

    def paginate(self, **kw):
        yield {"Versions": [{"Key": kw["Prefix"] + "a" * 64, "VersionId": str(i),
                             "LastModified": datetime(2000, 1, 1, tzinfo=UTC), "Size": 1} for i in range(1005)]}


def store(tmp_path):
    result = ObjectStore()
    result.s3, result.root = None, tmp_path
    return result


def test_stream_publication_is_bounded_and_verifies_digest(tmp_path, monkeypatch):
    target = store(tmp_path)
    monkeypatch.setattr(settings, "object_spool_bytes", 65536)
    value = b"x" * 200000
    ref = target.put_stream("tenant", "run", io.BytesIO(value))
    assert target.get("tenant", ref) == value
    target.local_path(ref["key"]).write_bytes(b"y" * len(value))
    with pytest.raises(Fault, match="checksum"):
        target.verify("tenant", ref)
    monkeypatch.setattr(settings, "max_object_bytes", 100)
    with pytest.raises(Fault, match="limit"):
        target.put("tenant", "run", value)
    assert not list(tmp_path.rglob("*.tmp"))


def test_multipart_completes_and_failed_upload_aborts(tmp_path, monkeypatch):
    target = store(tmp_path)
    target.s3 = S3()
    monkeypatch.setattr(settings, "s3_multipart_bytes", 5 * 1024 * 1024)
    value = b"a" * (6 * 1024 * 1024)
    ref = target.put("tenant", "run", value)
    assert len(target.s3.parts) == 2 and target.get("tenant", ref) == value
    target.s3.fail = True
    with pytest.raises(Fault) as error:
        target.put("tenant", "run", value)
    assert error.value.code == "OBJECT_UNAVAILABLE" and target.s3.aborted
    assert "secret" not in str(error.value)


def test_purge_deletes_all_versions_in_bounded_batches(tmp_path):
    target = store(tmp_path)
    target.s3 = S3()
    target.purge_scope("tenant", "run")
    assert [len(x) for x in target.s3.deleted] == [1000, 5]
    assert all("VersionId" in item for batch in target.s3.deleted for item in batch)


@pytest.mark.parametrize("code,expected", [("NoSuchKey", "OBJECT_MISSING"), ("AccessDenied", "OBJECT_DEPENDENCY"),
                                         ("NoSuchBucket", "OBJECT_DEPENDENCY"), ("SlowDown", "OBJECT_UNAVAILABLE")])
def test_s3_errors_are_classified_and_redacted(code, expected):
    error = storage_fault(ClientError({"Error": {"Code": code, "Message": "credential"}}, "GetObject"))
    assert error.code == expected and "credential" not in error.message


def test_path_escape_is_rejected(tmp_path):
    with pytest.raises(Fault, match="escaped"):
        store(tmp_path).local_path("../outside")


@pytest.mark.skipif(os.environ.get("FORGE_TEST_S3") != "1", reason="Requires explicitly confirmed isolated S3 test bucket")
def test_real_s3_multipart_integrity_and_all_version_erasure():
    from forgeagent.domain import uid

    assert settings.s3_endpoint and os.environ.get("FORGE_TEST_S3_BUCKET_CONFIRM") == settings.s3_bucket
    target = ObjectStore()
    namespace = "storage-test-" + uid()
    tenant = "forge-storage-integration"
    value = b"bounded-s3-test" * 700000
    try:
        ref = target.put_stream(tenant, namespace, io.BytesIO(value))
        assert target.get(tenant, ref) == value
        target.verify(tenant, ref)
        assert list(target.inventory(tenant, namespace))
    finally:
        target.purge_scope(tenant, namespace)
    assert list(target.inventory(tenant, namespace)) == []
