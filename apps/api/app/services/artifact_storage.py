from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from app.config import get_settings


class ArtifactStorage:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.local_root = Path(".artifacts")

    async def put_bytes(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if self.settings.artifact_storage_backend.lower() == "local":
            return await asyncio.to_thread(self._put_local, bucket, key, data)
        try:
            return await asyncio.to_thread(self._put_s3, bucket, key, data, content_type, metadata or {})
        except Exception as exc:
            result = self._put_local(bucket, key, data)
            result["warning"] = str(exc)
            return result

    async def get_bytes(self, bucket: str, storage_key: str, backend: str = "S3") -> bytes:
        if backend.upper() in {"LOCAL", "LOCAL_FALLBACK"} or Path(storage_key).is_absolute():
            path = self._validated_local_path(storage_key)
            return await asyncio.to_thread(path.read_bytes)
        return await asyncio.to_thread(self._get_s3, bucket, storage_key)

    def _put_local(self, bucket: str, key: str, data: bytes) -> dict[str, Any]:
        path = self._validated_local_path(Path(bucket) / key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {
            "storage_backend": "LOCAL_FALLBACK",
            "storage_key": str(path),
            "bucket": bucket,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _validated_local_path(self, relative_path: str | Path) -> Path:
        """Resolve a local artifact path without allowing it to leave local_root."""
        candidate = Path(relative_path)
        if ".." in candidate.parts:
            raise ValueError("Недопустимый путь artifact")
        root = self.local_root.resolve()
        path = (root / candidate).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Недопустимый путь artifact")
        return path

    def _client(self):
        import boto3
        from botocore.config import Config
        return boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            config=Config(connect_timeout=3, read_timeout=30, retries={"max_attempts": 2, "mode": "standard"}),
        )

    def _put_s3(self, bucket: str, key: str, data: bytes, content_type: str, metadata: dict[str, str]) -> dict[str, Any]:
        client = self._client()
        try:
            client.head_bucket(Bucket=bucket)
        except Exception:
            client.create_bucket(Bucket=bucket)
        client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type, Metadata=metadata)
        return {
            "storage_backend": "S3",
            "storage_key": key,
            "bucket": bucket,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _get_s3(self, bucket: str, key: str) -> bytes:
        response = self._client().get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
