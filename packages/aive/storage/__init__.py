"""File storage. Bytes here, metadata in the database.

Get the configured backend with ``get_storage()``. Business code should never
construct a backend directly, and never hold an absolute path - keys are the
currency, and ``open_local`` turns one into a path when a tool needs it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..config import settings
from .base import StoredFile, StorageProvider, sha256_of
from .keys import (
    ANALYSIS_AUDIO,
    EXPORT,
    KEYFRAME,
    MUSIC,
    ORIGINAL,
    PROXY,
    REFERENCE,
    RENDER,
    SHARED,
    THUMBNAIL,
    project_key,
    project_prefix,
    safe_name,
    shared_key,
)
from .local import LocalStorage
from .tos import TOSStorage


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return getter() if getter is not None else str(value)


def _tos_storage(**kwargs: Any) -> StorageProvider:
    client = kwargs.pop("client", None)
    bucket = kwargs.pop("bucket", settings.tos_bucket or "")
    cache_root = Path(kwargs.pop("root", settings.storage_root))
    signed_url_ttl_seconds = int(kwargs.pop("signed_url_ttl_seconds", 900))
    if kwargs:
        raise TypeError(f"unknown TOS storage options: {sorted(kwargs)}")
    if client is not None:
        return TOSStorage(
            client=client,
            bucket=bucket,
            cache_root=cache_root,
            signed_url_ttl_seconds=signed_url_ttl_seconds,
        )
    return TOSStorage.from_credentials(
        access_key_id=_secret_value(settings.tos_access_key_id),
        secret_access_key=_secret_value(settings.tos_secret_access_key),
        endpoint=settings.tos_endpoint or "",
        region=settings.tos_region or "",
        bucket=bucket,
        cache_root=cache_root,
        signed_url_ttl_seconds=signed_url_ttl_seconds,
    )

# Backend registry. Adding object storage means implementing StorageProvider
# and registering it here; nothing else changes.
_BACKENDS: dict[str, Callable[..., StorageProvider]] = {
    "local": lambda **kw: LocalStorage(Path(kw.get("root", "data/storage"))),
    "tos": _tos_storage,
}

_INSTANCE: StorageProvider | None = None


def register_backend(name: str, factory: Callable[..., StorageProvider]) -> None:
    _BACKENDS[name] = factory


def get_storage(**overrides: Any) -> StorageProvider:
    """The configured backend, created once."""
    global _INSTANCE
    if overrides:
        return _build(**overrides)
    if _INSTANCE is None:
        _INSTANCE = _build()
    return _INSTANCE


def set_storage(provider: StorageProvider | None) -> None:
    """Swap the backend, for tests and for callers that need an isolated root."""
    global _INSTANCE
    _INSTANCE = provider


def _build(**overrides: Any) -> StorageProvider:
    name = overrides.pop("backend", settings.storage_backend)
    factory = _BACKENDS.get(name)
    if factory is None:
        raise KeyError(
            f"unknown storage backend {name!r}; known: {sorted(_BACKENDS)}"
        )
    return factory(root=overrides.pop("root", settings.storage_root), **overrides)


__all__ = [
    "StorageProvider",
    "StoredFile",
    "LocalStorage",
    "TOSStorage",
    "get_storage",
    "set_storage",
    "register_backend",
    "sha256_of",
    "project_key",
    "shared_key",
    "project_prefix",
    "safe_name",
    "ORIGINAL",
    "PROXY",
    "THUMBNAIL",
    "KEYFRAME",
    "ANALYSIS_AUDIO",
    "MUSIC",
    "RENDER",
    "REFERENCE",
    "EXPORT",
    "SHARED",
]
