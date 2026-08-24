import asyncio
import os
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config

from app.core.config import get_settings
from app.infrastructure.storage.s3 import S3Storage

TEST_S3_ENDPOINT = os.getenv("TEST_S3_ENDPOINT")
pytestmark = pytest.mark.skipif(
    not TEST_S3_ENDPOINT,
    reason="TEST_S3_ENDPOINT is required for object-storage integration tests",
)


@pytest.mark.asyncio
async def test_s3_adapter_puts_and_deletes_minio_object() -> None:
    assert TEST_S3_ENDPOINT is not None
    settings = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=TEST_S3_ENDPOINT,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    storage = S3Storage(client=client, bucket=settings.s3_bucket)
    key = f"tests/ticket-04/{uuid4()}.txt"

    try:
        assert await storage.is_available() is True
        await storage.put(
            key=key,
            data=b"ticket-04-storage-probe",
            content_type="text/plain",
            metadata={"purpose": "integration-test"},
        )
        response = await asyncio.to_thread(
            client.get_object,
            Bucket=settings.s3_bucket,
            Key=key,
        )
        body = await asyncio.to_thread(response["Body"].read)
        assert body == b"ticket-04-storage-probe"
        assert response["ContentType"] == "text/plain"
        assert response["Metadata"] == {"purpose": "integration-test"}
    finally:
        await storage.delete(key=key)

    with pytest.raises(client.exceptions.NoSuchKey):
        await asyncio.to_thread(
            client.get_object,
            Bucket=settings.s3_bucket,
            Key=key,
        )
