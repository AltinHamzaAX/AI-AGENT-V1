import asyncio

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.modules.posts.providers import StorageObjectNotFoundError

_MISSING_KEY_CODES = frozenset({"NoSuchKey", "404", "NotFound"})


class S3Storage:
    def __init__(
        self,
        *,
        client: BaseClient | None = None,
        bucket: str | None = None,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        settings = None
        if any(value is None for value in (bucket, endpoint_url, access_key, secret_key)):
            settings = get_settings()
        self._bucket = bucket or settings.s3_bucket  # type: ignore[union-attr]
        self._client: BaseClient = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url or settings.s3_endpoint,  # type: ignore[union-attr]
            aws_access_key_id=access_key or settings.s3_access_key,  # type: ignore[union-attr]
            aws_secret_access_key=secret_key or settings.s3_secret_key,  # type: ignore[union-attr]
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

    async def get(self, *, key: str) -> bytes:
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self._bucket,
                Key=key,
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in _MISSING_KEY_CODES:
                raise StorageObjectNotFoundError(f"object '{key}' does not exist") from exc
            raise
        return await asyncio.to_thread(response["Body"].read)

    async def delete(self, *, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
