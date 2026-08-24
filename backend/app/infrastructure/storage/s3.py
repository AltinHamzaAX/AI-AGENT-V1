import asyncio

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from app.core.config import get_settings


class S3Storage:
    def __init__(
        self,
        *,
        client: BaseClient | None = None,
        bucket: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        settings = get_settings()
        self._bucket = bucket or settings.s3_bucket
        self._client: BaseClient = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url or settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def is_available(self) -> bool:
        await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
        return True

    async def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            Metadata=metadata or {},
        )

    async def delete(self, *, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
