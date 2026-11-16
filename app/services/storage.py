"""Private object storage for visit attachments (Cloudflare R2 / S3-compatible).

The API never proxies image bytes. It creates a small metadata record, returns a
short-lived presigned ``PUT`` URL, then verifies the uploaded object with a
``HEAD`` request before marking the attachment usable. Reads likewise return
short-lived presigned ``GET`` URLs.

Credentials are optional at boot. Calling one of these methods without complete
R2 settings raises :class:`StorageUnavailableError`, which the attachment service
maps to a ``503 dependency_unavailable`` response.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings


class StorageUnavailableError(RuntimeError):
    """Object storage is not configured or cannot be reached."""


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """A short-lived direct-upload instruction for one object."""

    url: str
    headers: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PresignedDownload:
    """A short-lived direct-download URL for one object."""

    url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StoredObject:
    """The metadata the provider reports for an uploaded object."""

    size_bytes: int
    content_type: str | None
    etag: str | None


class Storage(Protocol):
    """The operations the attachment service needs from object storage."""

    async def presign_upload(
        self,
        *,
        key: str,
        content_type: str,
    ) -> PresignedUpload: ...

    async def presign_download(self, *, key: str) -> PresignedDownload: ...

    async def head_object(self, *, key: str) -> StoredObject | None: ...

    async def delete_object(self, *, key: str) -> None: ...


class R2Storage:
    """Cloudflare R2 through its S3-compatible API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client_instance: Any = None

    def _client(self) -> Any:
        if not self._settings.storage_configured:
            raise StorageUnavailableError("Object storage is not configured")
        if self._client_instance is None:
            try:
                self._client_instance = boto3.client(
                    "s3",
                    endpoint_url=self._settings.storage_endpoint,
                    aws_access_key_id=self._settings.storage_access_key,
                    aws_secret_access_key=self._settings.storage_secret_key,
                    region_name=self._settings.storage_region,
                    config=Config(
                        signature_version="s3v4",
                        s3={"addressing_style": "path"},
                    ),
                )
            except (BotoCoreError, ValueError) as exc:
                raise StorageUnavailableError("Object storage could not be initialised") from exc
        return self._client_instance

    def _bucket(self) -> str:
        bucket = self._settings.storage_bucket
        if not bucket:
            raise StorageUnavailableError("Object storage is not configured")
        return bucket

    async def presign_upload(
        self,
        *,
        key: str,
        content_type: str,
    ) -> PresignedUpload:
        ttl = self._settings.storage_presign_ttl_seconds
        try:
            url = await asyncio.to_thread(
                self._client().generate_presigned_url,
                ClientMethod="put_object",
                Params={
                    "Bucket": self._bucket(),
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=ttl,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError("Object storage could not sign the upload") from exc
        return PresignedUpload(
            url=url,
            headers={"Content-Type": content_type},
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        )

    async def presign_download(self, *, key: str) -> PresignedDownload:
        ttl = self._settings.storage_presign_ttl_seconds
        try:
            url = await asyncio.to_thread(
                self._client().generate_presigned_url,
                ClientMethod="get_object",
                Params={"Bucket": self._bucket(), "Key": key},
                ExpiresIn=ttl,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError("Object storage could not sign the download") from exc
        return PresignedDownload(
            url=url,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        )

    async def head_object(self, *, key: str) -> StoredObject | None:
        try:
            response = await asyncio.to_thread(
                self._client().head_object,
                Bucket=self._bucket(),
                Key=key,
            )
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = str(error.get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise StorageUnavailableError("Object storage could not read the upload") from exc
        except BotoCoreError as exc:
            raise StorageUnavailableError("Object storage could not read the upload") from exc

        etag = response.get("ETag")
        return StoredObject(
            size_bytes=int(response.get("ContentLength", 0)),
            content_type=(
                str(response["ContentType"]) if response.get("ContentType") is not None else None
            ),
            etag=str(etag).strip('"') if etag is not None else None,
        )

    async def delete_object(self, *, key: str) -> None:
        try:
            await asyncio.to_thread(
                self._client().delete_object,
                Bucket=self._bucket(),
                Key=key,
            )
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = str(error.get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return
            raise StorageUnavailableError("Object storage could not remove the upload") from exc
        except BotoCoreError as exc:
            raise StorageUnavailableError("Object storage could not remove the upload") from exc


__all__ = [
    "PresignedDownload",
    "PresignedUpload",
    "R2Storage",
    "Storage",
    "StorageUnavailableError",
    "StoredObject",
]
