"""Storage protocol.

Bytes live in storage, metadata lives in the database. A 100-clip project is
several gigabytes of video; putting that in a database makes backups
impractical, queries slow, and random access - which the renderer needs on
every cut - impossible.

Three extension points, because this layer will be added to repeatedly:

* **New backend**: implement this protocol and register it. Local disk today,
  object storage when there is a server. Business code never learns which.
* **New kind of file**: add a constant to ``keys.py``. Nothing in this module
  needs to know that subtitles or LUTs exist.
* **Remote backends**: ``open_local`` is the seam. ffmpeg and OpenCV need a
  real path on a real filesystem, so a remote backend downloads to a cache and
  returns that path. Every caller already goes through it, so no pipeline code
  changes when storage moves off the machine.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# TODO(review): 1 MiB read chunks for hashing. Large enough that syscall
# overhead is irrelevant, small enough not to matter for memory. Never
# measured against real phone footage on a slow disk.
HASH_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class StoredFile:
    """What storage knows about one object.

    ``key`` is backend-independent: the same string identifies the file
    whether it sits on a local disk or in a bucket. Nothing outside this
    package should ever hold an absolute path.
    """

    key: str
    size: int
    sha256: str
    content_type: str | None = None

    @property
    def suffix(self) -> str:
        return Path(self.key).suffix


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@runtime_checkable
class StorageProvider(Protocol):
    """Where uploaded and generated files live."""

    def put(self, source: Path, key: str, move: bool = False) -> StoredFile:
        """Store ``source`` under ``key``. ``move`` avoids copying large files."""

    def put_bytes(self, data: bytes, key: str) -> StoredFile: ...

    def open_local(self, key: str) -> Path:
        """A real filesystem path for this object.

        The seam that lets ffmpeg and OpenCV stay unaware of the backend.
        """

    def reserve_local(self, key: str) -> Path:
        """A path to write to, for tools that produce files themselves.

        Pair with ``commit`` once the tool has finished writing.
        """

    def commit(self, key: str) -> StoredFile:
        """Register a file written directly to ``reserve_local(key)``."""

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def size(self, key: str) -> int: ...

    def iter_keys(self, prefix: str = "") -> Iterator[str]: ...

    def url(self, key: str) -> str | None:
        """A URL a browser could fetch, when the backend can offer one."""
