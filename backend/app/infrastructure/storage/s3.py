import asyncio

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from app.core.config import get_settings


class S3Storage:
    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.s3_bucket
        self._client: BaseClient = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(signature_version="s3v4"),
        )

    async def is_available(self) -> bool:
        await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
        return True
