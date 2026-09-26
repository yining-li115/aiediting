"""Unit coverage for the upload-to-Patch application service."""

from __future__ import annotations

from io import BytesIO

import pytest

from aive.contracts.media import UploadAssetsRequest
from aive.media.repository import AssetRegistration, MediaAssetRecord
from aive.media.upload import (
    IncomingMedia,
    MediaProbeResult,
    MediaUploadApplication,
    MediaUploadRejected,
)
from aive.storage import LocalStorage


class MemoryAssets:
    def __init__(self) -> None:
        self.records: dict[str, MediaAssetRecord] = {}

    def get_asset(self, asset_id: str) -> MediaAssetRecord | None:
        return self.records.get(asset_id)

    def register_assets(
        self, records: tuple[MediaAssetRecord, ...]
    ) -> AssetRegistration:
        inserted = []
        for record in records:
            existing = self.records.get(record.asset_id)
            if existing is not None and existing != record:
                raise AssertionError("test repository received a conflicting identity")
            if existing is None:
                inserted.append(record.asset_id)
                self.records[record.asset_id] = record
        return AssetRegistration(records=records, inserted_ids=tuple(inserted))

    def authorize_assets(self, project_id: str, assets: tuple) -> None:
        raise AssertionError("authorization is exercised by application tests")


class CapturingProjects:
    def __init__(self) -> None:
        self.patch = None
        self.result = object()

    def apply_patch(self, patch):
        self.patch = patch
        return self.result


def _request(*, roles: tuple[str, ...] = ("source",)) -> UploadAssetsRequest:
    return UploadAssetsRequest(
        request_id="request_upload",
        patch_id="patch_upload",
        project_id="project_upload",
        base_revision_id="revision_initial",
        idempotency_key="upload-key",
        origin="user",
        summary="Add uploaded media",
        items=({"asset_id": "asset_upload", "roles": roles},),
    )


def _probe(path, project_id, asset_id, storage_key):
    assert path.read_bytes() == b"valid media"
    return MediaProbeResult(
        kind="video",
        duration_seconds=2.5,
        width=1080,
        height=1920,
        fps=30.0,
        has_audio=True,
    )


def test_upload_registers_bytes_then_commits_add_asset_patch(tmp_path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    assets = MemoryAssets()
    projects = CapturingProjects()
    application = MediaUploadApplication(
        storage=storage,
        assets=assets,
        projects=projects,
        probe=_probe,
        max_source_bytes=1_000,
        max_reference_bytes=1_000,
    )

    result = application.upload_assets(
        _request(),
        (IncomingMedia("clip.mp4", BytesIO(b"valid media")),),
    )

    record = assets.records["asset_upload"]
    assert storage.open_local(record.storage_key).read_bytes() == b"valid media"
    assert record.project_id == "project_upload"
    assert result.revision_write is projects.result
    assert result.assets[0].asset.content_hash == record.sha256
    assert result.assets[0].asset.roles == ("source",)
    assert projects.patch.base_revision_id == "revision_initial"
    assert [operation.op for operation in projects.patch.operations] == ["add_asset"]
    assert projects.patch.operations[0].asset == result.assets[0].asset


def test_invalid_batch_writes_nothing_and_does_not_create_a_patch(tmp_path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    assets = MemoryAssets()
    projects = CapturingProjects()
    application = MediaUploadApplication(
        storage=storage,
        assets=assets,
        projects=projects,
        probe=_probe,
        max_source_bytes=1_000,
        max_reference_bytes=1_000,
    )

    with pytest.raises(MediaUploadRejected) as caught:
        application.upload_assets(
            _request(),
            (IncomingMedia("notes.txt", BytesIO(b"valid media")),),
        )

    assert caught.value.issues[0].code == "unsupported_media_type"
    assert list(storage.iter_keys()) == []
    assert assets.records == {}
    assert projects.patch is None


def test_reference_role_rejects_a_photo_before_registration(tmp_path) -> None:
    def photo_probe(path, project_id, asset_id, storage_key):
        return MediaProbeResult(
            kind="photo",
            duration_seconds=3.0,
            width=1200,
            height=900,
            fps=0.0,
            has_audio=False,
        )

    storage = LocalStorage(tmp_path / "storage")
    assets = MemoryAssets()
    application = MediaUploadApplication(
        storage=storage,
        assets=assets,
        projects=CapturingProjects(),
        probe=photo_probe,
        max_source_bytes=1_000,
        max_reference_bytes=1_000,
    )

    with pytest.raises(MediaUploadRejected) as caught:
        application.upload_assets(
            _request(roles=("reference",)),
            (IncomingMedia("reference.jpg", BytesIO(b"valid media")),),
        )

    assert caught.value.issues[0].code == "reference_requires_video"
    assert list(storage.iter_keys()) == []
    assert assets.records == {}


def test_file_count_must_match_the_typed_manifest(tmp_path) -> None:
    application = MediaUploadApplication(
        storage=LocalStorage(tmp_path / "storage"),
        assets=MemoryAssets(),
        projects=CapturingProjects(),
        probe=_probe,
    )

    with pytest.raises(MediaUploadRejected) as caught:
        application.upload_assets(_request(), ())

    assert caught.value.issues[0].code == "file_manifest_mismatch"
