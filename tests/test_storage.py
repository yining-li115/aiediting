"""Storage layer.

The point of this layer is that nothing above it holds an absolute path, so
moving from a local disk to object storage is a configuration change. These
tests check the properties that make that true.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aive.storage import (
    KEYFRAME,
    MUSIC,
    ORIGINAL,
    LocalStorage,
    project_key,
    project_prefix,
    safe_name,
    shared_key,
)


@pytest.fixture
def store(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "in" / "clip.mov"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video bytes")
    return path


# --- basics ----------------------------------------------------------------


def test_put_records_size_and_hash(store, source) -> None:
    stored = store.put(source, project_key("p1", ORIGINAL, "a001.mov"))
    assert stored.size == len(b"video bytes")
    assert len(stored.sha256) == 64
    assert store.exists(stored.key)


def test_stored_copy_survives_the_source_being_deleted(store, source) -> None:
    """Ownership is the whole point: a project must not break because the user
    tidied their downloads folder."""
    key = project_key("p1", ORIGINAL, "a001.mov")
    store.put(source, key)
    source.unlink()
    assert store.open_local(key).read_bytes() == b"video bytes"


def test_identical_files_hash_identically(store, tmp_path) -> None:
    """The basis for deduplicating re-uploaded footage."""
    a, b = tmp_path / "a.mov", tmp_path / "b.mov"
    a.write_bytes(b"same"), b.write_bytes(b"same")
    first = store.put(a, project_key("p1", ORIGINAL, "a.mov"))
    second = store.put(b, project_key("p2", ORIGINAL, "b.mov"))
    assert first.sha256 == second.sha256


def test_reserve_and_commit_for_tools_that_write_their_own_files(store) -> None:
    """ffmpeg writes where it is told; storage still has to end up owning it."""
    key = project_key("p1", KEYFRAME, "seg1_0.jpg")
    target = store.reserve_local(key)
    target.write_bytes(b"jpeg")
    stored = store.commit(key)
    assert stored.size == 4
    assert store.exists(key)


def test_commit_without_a_written_file_fails(store) -> None:
    with pytest.raises(FileNotFoundError):
        store.commit(project_key("p1", KEYFRAME, "never_written.jpg"))


def test_open_local_missing_key_fails(store) -> None:
    with pytest.raises(FileNotFoundError):
        store.open_local(project_key("p1", ORIGINAL, "absent.mov"))


# --- layout ----------------------------------------------------------------


def test_project_owns_a_single_prefix(store, source) -> None:
    """One prefix per project is what makes deleting a project, or measuring
    what a user stores, a single operation."""
    store.put(source, project_key("p1", ORIGINAL, "a.mov"))
    store.put(source, project_key("p1", KEYFRAME, "s1_0.jpg"))
    store.put(source, project_key("p2", ORIGINAL, "b.mov"))

    mine = list(store.iter_keys(project_prefix("p1")))
    assert len(mine) == 2
    assert all(k.startswith("p1/") for k in mine)


def test_shared_content_is_not_owned_by_a_project(store, source) -> None:
    key = shared_key(MUSIC, "track_01.wav")
    store.put(source, key)
    assert not key.startswith("p1/")
    assert list(store.iter_keys(project_prefix("p1"))) == []


# --- hostile input ---------------------------------------------------------


@pytest.mark.parametrize(
    "key", ["../escape.mov", "p1/../../escape.mov", "/etc/passwd"]
)
def test_keys_cannot_escape_the_root(store, source, key) -> None:
    """Keys are built from user filenames, so they are untrusted input."""
    with pytest.raises((ValueError, FileNotFoundError)):
        store.open_local(key)
    with pytest.raises(ValueError):
        store.put(source, key)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("holiday clip.MOV", "a001.mov"),
        ("视频 2026.mp4", "a001.mp4"),
        ("no_extension", "a001"),
        ("../../evil.mp4", "a001.mp4"),
        ("weird.exe.tar.gz.reallylongextension", "a001"),
    ],
)
def test_user_filenames_are_reduced_to_an_extension(name, expected) -> None:
    """Identity comes from the asset id; the filename only contributes a
    suffix, so spaces, non-ASCII and path separators cannot reach a key."""
    assert safe_name(name, "a001") == expected


# --- swappability ----------------------------------------------------------


def test_business_code_only_needs_the_protocol(store, source) -> None:
    from aive.storage import StorageProvider

    assert isinstance(store, StorageProvider)


def test_unknown_backend_is_rejected() -> None:
    from aive.storage import get_storage

    with pytest.raises(KeyError, match="unknown storage backend"):
        get_storage(backend="s3-that-does-not-exist-yet")
