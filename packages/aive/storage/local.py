"""Local filesystem backend.

The default, and the only one needed until there is a server. It keeps the
same key structure a bucket would use, so moving to object storage is a
configuration change rather than a re-layout.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

from .base import StoredFile, sha256_of


class LocalStorage:
    def __init__(self, root: Path) -> None:
        # Resolved once: on macOS /tmp is a symlink to /private/tmp, so an
        # unresolved root and a resolved path never compare equal and both the
        # containment check and relative_to break.
        self.root = Path(root).expanduser().resolve()

    # --- paths -------------------------------------------------------------

    def _path(self, key: str) -> Path:
        # A key is data, and data can be hostile: "../../etc/passwd" must not
        # escape the storage root even if it arrives via a user's filename.
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"key escapes the storage root: {key!r}")
        return path

    # --- writing -----------------------------------------------------------

    def put(self, source: Path, key: str, move: bool = False) -> StoredFile:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(source), target)
        else:
            shutil.copy2(source, target)
        return self._describe(key, target)

    def put_bytes(self, data: bytes, key: str) -> StoredFile:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return self._describe(key, target)

    def reserve_local(self, key: str) -> Path:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def commit(self, key: str) -> StoredFile:
        target = self._path(key)
        if not target.exists():
            raise FileNotFoundError(f"nothing was written to {key}")
        return self._describe(key, target)

    # --- reading -----------------------------------------------------------

    def open_local(self, key: str) -> Path:
        target = self._path(key)
        if not target.exists():
            raise FileNotFoundError(f"no object at {key}")
        return target

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).exists()
        except ValueError:
            return False

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def delete(self, key: str) -> None:
        target = self._path(key)
        if target.exists():
            target.unlink()

    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        base = self._path(prefix) if prefix else self.root
        if not base.exists():
            return
        for path in sorted(base.rglob("*")):
            if path.is_file():
                yield str(path.relative_to(self.root))

    def url(self, key: str) -> str | None:
        # A local path is not fetchable by a browser. Serving files is the
        # API's job, and it will use open_local; returning a file:// URL here
        # would only invite someone to put it in an HTML page.
        return None

    # --- helpers -----------------------------------------------------------

    def _describe(self, key: str, path: Path) -> StoredFile:
        return StoredFile(
            key=key,
            size=path.stat().st_size,
            sha256=sha256_of(path),
        )
