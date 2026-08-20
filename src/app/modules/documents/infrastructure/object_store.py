"""S3ObjectStore — ObjectStorePort adapter over aioboto3 (AWS S3 or MinIO).

The wrapper holds only the cheap `aioboto3.Session` (no I/O) and connection
parameters; it opens a fresh client PER OPERATION rather than holding a
long-lived socket — the documented aioboto3 pattern, since its client is not
safe to share across event-loop iterations/tasks the way a sync boto3 client is.
"""

from __future__ import annotations

from typing import Any

import aioboto3
import structlog
from botocore.exceptions import BotoCoreError, ClientError

from app.modules.documents.domain.errors import ObjectStoreUnavailableError

logger = structlog.get_logger(__name__)

_BUCKET_NOT_FOUND_CODES = frozenset({"404", "NoSuchBucket"})


class S3ObjectStore:
    """ObjectStorePort adapter over aioboto3."""

    def __init__(
        self,
        *,
        session: aioboto3.Session,
        endpoint_url: str | None,
        access_key: str,
        secret_key: str,
        region: str,
    ) -> None:
        self._session = session
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region

    def _client(self) -> Any:
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
        )

    async def ensure_bucket(self, bucket: str) -> None:
        try:
            async with self._client() as s3:
                try:
                    await s3.head_bucket(Bucket=bucket)
                except ClientError as exc:
                    code = exc.response.get("Error", {}).get("Code")
                    if code not in _BUCKET_NOT_FOUND_CODES:
                        raise
                    await s3.create_bucket(Bucket=bucket)
        except (BotoCoreError, ClientError) as exc:
            logger.warning("s3.ensure_bucket_failed", bucket=bucket, error=str(exc))
            raise ObjectStoreUnavailableError("Object store is unavailable") from exc

    async def put(self, *, bucket: str, key: str, data: bytes, content_type: str) -> None:
        try:
            async with self._client() as s3:
                await s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        except (BotoCoreError, ClientError) as exc:
            logger.warning("s3.put_failed", bucket=bucket, key=key, error=str(exc))
            raise ObjectStoreUnavailableError("Object store is unavailable") from exc

    async def get(self, *, bucket: str, key: str) -> bytes:
        try:
            async with self._client() as s3:
                response = await s3.get_object(Bucket=bucket, Key=key)
                body: bytes = await response["Body"].read()
                return body
        except (BotoCoreError, ClientError) as exc:
            logger.warning("s3.get_failed", bucket=bucket, key=key, error=str(exc))
            raise ObjectStoreUnavailableError("Object store is unavailable") from exc

    async def delete(self, *, bucket: str, key: str) -> None:
        try:
            async with self._client() as s3:
                await s3.delete_object(Bucket=bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            logger.warning("s3.delete_failed", bucket=bucket, key=key, error=str(exc))
            raise ObjectStoreUnavailableError("Object store is unavailable") from exc

    async def delete_bucket(self, bucket: str) -> None:
        try:
            async with self._client() as s3:
                await s3.delete_bucket(Bucket=bucket)
        except (BotoCoreError, ClientError) as exc:
            logger.warning("s3.delete_bucket_failed", bucket=bucket, error=str(exc))
            raise ObjectStoreUnavailableError("Object store is unavailable") from exc
