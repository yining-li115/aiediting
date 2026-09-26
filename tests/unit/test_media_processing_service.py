from __future__ import annotations

from dataclasses import replace

import pytest

from aive.jobs import Stage
from aive.media.processing import (
    MediaProcessingApplication,
    MediaProcessingBatchFailed,
)
from aive.media.repository import (
    AssetProcessingRecord,
    MediaProcessingTicket,
)
from aive.storage import LocalStorage


def _record(**changes) -> AssetProcessingRecord:
    values = {
        "asset_id": "asset_001",
        "project_id": "project_001",
        "storage_key": "project_001/original/asset_001.mp4",
        "kind": "video",
        "original_name": "clip.mp4",
        "duration_seconds": 2.0,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "has_audio": True,
        "readiness": "processing",
        "processing_job_id": "job_media_001",
    }
    values.update(changes)
    return AssetProcessingRecord(**values)


class MemoryProcessingAssets:
    def __init__(self) -> None:
        self.record = _record(readiness="uploaded", processing_job_id=None)

    def enqueue_processing(self, project_id, asset_ids, job_id):
        self.record = replace(
            self.record,
            readiness="processing",
            processing_job_id=job_id,
            processing_error=None,
        )
        return MediaProcessingTicket(job_id=job_id, asset_ids=asset_ids, created=True)

    def list_job_assets(self, job_id):
        return (self.record,) if self.record.processing_job_id == job_id else ()

    def list_processing_status(self, project_id):
        return (self.record,)

    def mark_processing_ready(
        self, *, project_id, asset_id, job_id, proxy_key, thumbnail_key
    ):
        self.record = replace(
            self.record,
            readiness="ready",
            proxy_key=proxy_key,
            thumbnail_key=thumbnail_key,
        )
        return self.record

    def mark_processing_failed(self, *, project_id, asset_id, job_id, error):
        self.record = replace(
            self.record,
            readiness="failed",
            processing_error=error,
        )
        return self.record


class CapturingHandle:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.updates: list[tuple[Stage | None, float | None]] = []
        self.events: list[tuple[str, dict]] = []

    def update(self, stage=None, progress=None):
        self.updates.append((stage, progress))

    def emit(self, type, **data):
        self.events.append((type, data))


def _write_artifact(record, source, output):
    assert source.read_bytes() == b"original media"
    output.write_bytes(b"derived media")
    return output


async def test_processing_generates_proxy_thumbnail_and_marks_ready(tmp_path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    storage.put_bytes(b"original media", _record().storage_key)
    assets = MemoryProcessingAssets()
    application = MediaProcessingApplication(
        storage=storage,
        assets=assets,
        proxy_generator=_write_artifact,
        thumbnail_generator=_write_artifact,
        id_factory=lambda _: "job_media_001",
    )
    ticket = application.enqueue("project_001", ("asset_001",))
    handle = CapturingHandle(ticket.job_id)

    result = await application.process(handle)

    assert assets.record.readiness == "ready"
    assert storage.exists(assets.record.proxy_key)
    assert storage.exists(assets.record.thumbnail_key)
    assert result["ready_count"] == 1
    assert [stage for stage, _ in handle.updates if stage] == [
        Stage.GENERATING_PROXY,
        Stage.GENERATING_THUMBNAIL,
    ]
    assert handle.events == [("asset_ready", {"asset_id": "asset_001"})]


async def test_processing_failure_is_persisted_on_the_asset(tmp_path) -> None:
    def fail_thumbnail(record, source, output):
        raise RuntimeError("thumbnail exploded")

    storage = LocalStorage(tmp_path / "storage")
    storage.put_bytes(b"original media", _record().storage_key)
    assets = MemoryProcessingAssets()
    application = MediaProcessingApplication(
        storage=storage,
        assets=assets,
        proxy_generator=_write_artifact,
        thumbnail_generator=fail_thumbnail,
        id_factory=lambda _: "job_media_001",
    )
    application.enqueue("project_001", ("asset_001",))
    handle = CapturingHandle("job_media_001")

    with pytest.raises(MediaProcessingBatchFailed, match="asset_001"):
        await application.process(handle)

    assert assets.record.readiness == "failed"
    assert assets.record.processing_error == "thumbnail exploded"
    assert handle.events[-1][0] == "asset_failed"
