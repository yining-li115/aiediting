from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from aive.storage import TOSStorage, get_storage


class MissingObject(Exception):
    status_code = 404
    code = "NoSuchKey"


class FakeTOSClient:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.signed: list[tuple[str, str, int]] = []

    def put_object_from_file(
        self,
        bucket: str,
        key: str,
        file_path: str,
        **_: object,
    ) -> None:
        self.objects[f"{bucket}/{key}"] = Path(file_path).read_bytes()

    def put_object(
        self,
        bucket: str,
        key: str,
        *,
        content: bytes,
        **_: object,
    ) -> None:
        self.objects[f"{bucket}/{key}"] = content

    def get_object_to_file(
        self,
        bucket: str,
        key: str,
        file_path: str,
    ) -> None:
        try:
            data = self.objects[f"{bucket}/{key}"]
        except KeyError as exc:
            raise MissingObject from exc
        Path(file_path).write_bytes(data)

    def head_object(self, bucket: str, key: str) -> SimpleNamespace:
        try:
            data = self.objects[f"{bucket}/{key}"]
        except KeyError as exc:
            raise MissingObject from exc
        return SimpleNamespace(content_length=len(data))

    def delete_object(self, bucket: str, key: str) -> None:
        self.objects.pop(f"{bucket}/{key}", None)

    def list_objects(
        self,
        bucket: str,
        *,
        prefix: str,
        marker: str | None = None,
        max_keys: int = 1_000,
    ) -> SimpleNamespace:
        keys = sorted(
            full_key.removeprefix(f"{bucket}/")
            for full_key in self.objects
            if full_key.startswith(f"{bucket}/{prefix}")
        )
        if marker is not None:
            keys = [key for key in keys if key > marker]
        page = keys[:max_keys]
        return SimpleNamespace(
            contents=[SimpleNamespace(key=key) for key in page],
            is_truncated=len(keys) > len(page),
            next_marker=page[-1] if len(keys) > len(page) else None,
        )

    def pre_signed_url(
        self,
        method: object,
        bucket: str,
        key: str,
        *,
        expires: int,
    ) -> SimpleNamespace:
        assert getattr(method, "value", method) == "GET"
        self.signed.append((bucket, key, expires))
        return SimpleNamespace(
            signed_url=(
                f"https://{bucket}.tos.example.invalid/{key}"
                f"?X-Tos-Expires={expires}&X-Tos-Signature=test"
            )
        )


@pytest.fixture
def client() -> FakeTOSClient:
    return FakeTOSClient()


@pytest.fixture
def store(tmp_path: Path, client: FakeTOSClient) -> TOSStorage:
    return TOSStorage(
        client=client,
        bucket="aive-demo",
        cache_root=tmp_path / "cache",
        signed_url_ttl_seconds=900,
    )


def test_put_persists_remote_object_and_keeps_local_cache(
    store: TOSStorage,
    client: FakeTOSClient,
    tmp_path: Path,
) -> None:
    source = tmp_path / "clip.mov"
    source.write_bytes(b"video bytes")

    stored = store.put(source, "project_a/original/clip.mov")
    source.unlink()

    assert client.objects["aive-demo/project_a/original/clip.mov"] == b"video bytes"
    assert stored.size == len(b"video bytes")
    assert stored.sha256 == sha256(b"video bytes").hexdigest()
    assert store.open_local(stored.key).read_bytes() == b"video bytes"


def test_open_local_downloads_an_uncached_private_object(
    client: FakeTOSClient,
    tmp_path: Path,
) -> None:
    client.objects["aive-demo/project_a/proxy/clip.mp4"] = b"proxy"
    store = TOSStorage(
        client=client,
        bucket="aive-demo",
        cache_root=tmp_path / "empty-cache",
    )

    local = store.open_local("project_a/proxy/clip.mp4")

    assert local.read_bytes() == b"proxy"
    assert local.is_relative_to(tmp_path / "empty-cache")


def test_storage_registry_builds_tos_without_leaking_into_callers(
    client: FakeTOSClient,
    tmp_path: Path,
) -> None:
    storage = get_storage(
        backend="tos",
        client=client,
        bucket="aive-demo",
        root=tmp_path / "cache",
    )

    assert isinstance(storage, TOSStorage)


def test_commit_uploads_reserved_output_and_url_is_short_lived(
    store: TOSStorage,
    client: FakeTOSClient,
) -> None:
    key = "project_a/analysis-audio/window.wav"
    target = store.reserve_local(key)
    target.write_bytes(b"wav")

    committed = store.commit(key)
    signed_url = store.url(key)

    assert committed.size == 3
    assert client.objects[f"aive-demo/{key}"] == b"wav"
    assert signed_url is not None
    assert "X-Tos-Expires=900" in signed_url
    assert client.signed == [("aive-demo", key, 900)]


def test_exists_iter_size_and_delete_share_remote_truth(
    store: TOSStorage,
) -> None:
    store.put_bytes(b"one", "project_a/original/one.mov")
    store.put_bytes(b"two", "project_a/original/two.mov")

    assert store.exists("project_a/original/one.mov")
    assert not store.exists("project_a/original/missing.mov")
    assert store.size("project_a/original/two.mov") == 3
    assert tuple(store.iter_keys("project_a/original")) == (
        "project_a/original/one.mov",
        "project_a/original/two.mov",
    )

    cached = store.open_local("project_a/original/one.mov")
    store.delete("project_a/original/one.mov")
    assert not store.exists("project_a/original/one.mov")
    assert not cached.exists()


def test_failed_put_bytes_preserves_the_original_transport_error(
    tmp_path: Path,
) -> None:
    class UnavailableClient(FakeTOSClient):
        def put_object(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("upload unavailable")

        def head_object(self, bucket: str, key: str) -> SimpleNamespace:
            raise AssertionError("failed upload must not trigger a HEAD request")

    store = TOSStorage(
        client=UnavailableClient(),
        bucket="aive-demo",
        cache_root=tmp_path / "cache",
    )

    with pytest.raises(RuntimeError, match="upload unavailable"):
        store.put_bytes(b"payload", "project_a/original/clip.mov")


@pytest.mark.parametrize(
    "key",
    ("", "/absolute.mov", "../escape.mov", "project_a/../../escape.mov"),
)
def test_tos_keys_cannot_escape_the_cache_or_bucket_prefix(
    store: TOSStorage,
    tmp_path: Path,
    key: str,
) -> None:
    source = tmp_path / "clip.mov"
    source.write_bytes(b"video")

    with pytest.raises(ValueError, match="invalid storage key"):
        store.put(source, key)
