"""Volcengine TOS storage adapter with a local media-tool cache."""

from __future__ import annotations

import mimetypes
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .base import StoredFile, sha256_of


DEFAULT_SIGNED_URL_TTL_SECONDS = 900
LIST_PAGE_SIZE = 1_000


class TOSStorage:
    """Private TOS bucket storage implementing the shared StorageProvider."""

    def __init__(
        self,
        *,
        client: Any,
        bucket: str,
        cache_root: Path,
        signed_url_ttl_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS,
    ) -> None:
        if not bucket.strip():
            raise ValueError("TOS bucket must not be empty")
        if signed_url_ttl_seconds <= 0:
            raise ValueError("TOS signed URL TTL must be positive")
        self._client = client
        self.bucket = bucket
        self.cache_root = Path(cache_root).expanduser().resolve()
        self.signed_url_ttl_seconds = signed_url_ttl_seconds

    @classmethod
    def from_credentials(
        cls,
        *,
        access_key_id: str,
        secret_access_key: str,
        endpoint: str,
        region: str,
        bucket: str,
        cache_root: Path,
        signed_url_ttl_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS,
    ) -> TOSStorage:
        missing = tuple(
            name
            for name, value in (
                ("AIVE_TOS_ACCESS_KEY_ID", access_key_id),
                ("AIVE_TOS_SECRET_ACCESS_KEY", secret_access_key),
                ("AIVE_TOS_ENDPOINT", endpoint),
                ("AIVE_TOS_REGION", region),
                ("AIVE_TOS_BUCKET", bucket),
            )
            if not value.strip()
        )
        if missing:
            raise ValueError(
                "TOS storage is missing required configuration: "
                + ", ".join(missing)
            )
        try:
            import tos
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise RuntimeError(
                "TOS storage requires the official 'tos' Python SDK"
            ) from exc
        return cls(
            client=tos.TosClientV2(
                access_key_id,
                secret_access_key,
                endpoint,
                region,
            ),
            bucket=bucket,
            cache_root=cache_root,
            signed_url_ttl_seconds=signed_url_ttl_seconds,
        )

    def put(self, source: Path, key: str, move: bool = False) -> StoredFile:
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        target = self._cache_path(key)
        content_type = mimetypes.guess_type(key)[0]
        self._client.put_object_from_file(
            self.bucket,
            key,
            str(source),
            content_type=content_type,
        )
        try:
            self._cache_source(source, target, move=move)
        except Exception:
            self._client.delete_object(self.bucket, key)
            raise
        return self._describe(key, target, content_type)

    def put_bytes(self, data: bytes, key: str) -> StoredFile:
        target = self._cache_path(key)
        content_type = mimetypes.guess_type(key)[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._temporary_path(target)
        uploaded = False
        try:
            temporary.write_bytes(data)
            self._client.put_object(
                self.bucket,
                key,
                content=data,
                content_type=content_type,
            )
            uploaded = True
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            if uploaded:
                self._client.delete_object(self.bucket, key)
            raise
        return self._describe(key, target, content_type)

    def open_local(self, key: str) -> Path:
        target = self._cache_path(key)
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._temporary_path(target)
        try:
            self._client.get_object_to_file(
                self.bucket,
                key,
                str(temporary),
            )
            os.replace(temporary, target)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if _is_missing(exc):
                raise FileNotFoundError(f"no object at {key}") from exc
            raise
        return target

    def reserve_local(self, key: str) -> Path:
        target = self._cache_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def commit(self, key: str) -> StoredFile:
        target = self._cache_path(key)
        if not target.is_file():
            raise FileNotFoundError(f"nothing was written to {key}")
        content_type = mimetypes.guess_type(key)[0]
        self._client.put_object_from_file(
            self.bucket,
            key,
            str(target),
            content_type=content_type,
        )
        return self._describe(key, target, content_type)

    def exists(self, key: str) -> bool:
        self._validate_key(key)
        return self._remote_exists(key)

    def delete(self, key: str) -> None:
        target = self._cache_path(key)
        self._client.delete_object(self.bucket, key)
        target.unlink(missing_ok=True)

    def size(self, key: str) -> int:
        self._validate_key(key)
        try:
            result = self._client.head_object(self.bucket, key)
        except Exception as exc:
            if _is_missing(exc):
                raise FileNotFoundError(f"no object at {key}") from exc
            raise
        return int(result.content_length)

    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        self._validate_prefix(prefix)
        marker: str | None = None
        while True:
            result = self._client.list_objects(
                self.bucket,
                prefix=prefix,
                marker=marker,
                max_keys=LIST_PAGE_SIZE,
            )
            contents = tuple(result.contents)
            for item in contents:
                yield item.key
            if not result.is_truncated:
                return
            next_marker = result.next_marker or (
                contents[-1].key if contents else None
            )
            if next_marker is None or next_marker == marker:
                raise RuntimeError("TOS list_objects returned an invalid page marker")
            marker = next_marker

    def url(self, key: str) -> str | None:
        self._validate_key(key)
        if not self._remote_exists(key):
            raise FileNotFoundError(f"no object at {key}")
        from tos.enum import HttpMethodType

        result = self._client.pre_signed_url(
            HttpMethodType.Http_Method_Get,
            self.bucket,
            key,
            expires=self.signed_url_ttl_seconds,
        )
        return result.signed_url

    def _remote_exists(self, key: str) -> bool:
        try:
            self._client.head_object(self.bucket, key)
        except Exception as exc:
            if _is_missing(exc):
                return False
            raise
        return True

    def _cache_path(self, key: str) -> Path:
        self._validate_key(key)
        target = (self.cache_root / key).resolve()
        if not target.is_relative_to(self.cache_root):
            raise ValueError(f"invalid storage key: {key!r}")
        return target

    @staticmethod
    def _validate_key(key: str) -> None:
        parts = key.split("/")
        if (
            not key
            or key.startswith("/")
            or "\\" in key
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError(f"invalid storage key: {key!r}")

    @classmethod
    def _validate_prefix(cls, prefix: str) -> None:
        if not prefix:
            return
        cls._validate_key(prefix.rstrip("/"))

    @staticmethod
    def _temporary_path(target: Path) -> Path:
        handle = tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.",
            suffix=".partial",
            dir=target.parent,
            delete=False,
        )
        handle.close()
        return Path(handle.name)

    @classmethod
    def _cache_source(cls, source: Path, target: Path, *, move: bool) -> None:
        if source.resolve() == target:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(source), target)
            return
        temporary = cls._temporary_path(target)
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _describe(
        key: str,
        path: Path,
        content_type: str | None,
    ) -> StoredFile:
        return StoredFile(
            key=key,
            size=path.stat().st_size,
            sha256=sha256_of(path),
            content_type=content_type,
        )


def _is_missing(exc: Exception) -> bool:
    return (
        getattr(exc, "status_code", None) == 404
        or getattr(exc, "status", None) == 404
        or getattr(exc, "code", None) in {"NoSuchKey", "NotFound"}
    )
