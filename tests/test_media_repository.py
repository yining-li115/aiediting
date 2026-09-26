from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aive.contracts.project_doc import AssetRef
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.models import Base, Job, Project
from aive.media.repository import (
    AssetIdentityConflict,
    MediaAssetRecord,
    MediaProcessingConflict,
    ProjectAssetNotFound,
)


@pytest.fixture
def repository(tmp_path) -> SqlAlchemyMediaAssetRepository:
    engine = create_engine(f"sqlite:///{tmp_path / 'assets.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with sessions.begin() as session:
        session.add_all([Project(id="project_a"), Project(id="project_b")])
    return SqlAlchemyMediaAssetRepository(sessions)


def _record(**changes) -> MediaAssetRecord:
    values = {
        "asset_id": "asset_001",
        "project_id": "project_a",
        "storage_key": "project_a/original/asset_001.mp4",
        "kind": "video",
        "original_name": "clip.mp4",
        "sha256": "a" * 64,
        "size_bytes": 123,
        "duration_seconds": 2.5,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "has_audio": True,
    }
    values.update(changes)
    return MediaAssetRecord(**values)


def test_register_is_replay_safe_and_preserves_asset_identity(repository) -> None:
    record = _record()

    first = repository.register_assets((record,))
    replay = repository.register_assets((record,))

    assert first.inserted_ids == ("asset_001",)
    assert replay.inserted_ids == ()
    assert repository.get_asset("asset_001") == record

    with pytest.raises(AssetIdentityConflict):
        repository.register_assets((_record(sha256="b" * 64),))


def test_asset_authorization_checks_project_kind_and_hash(repository) -> None:
    repository.register_assets((_record(),))
    authorized = AssetRef(
        asset_id="asset_001",
        kind="video",
        roles=("source",),
        content_hash="a" * 64,
    )

    repository.authorize_assets("project_a", (authorized,))

    with pytest.raises(ProjectAssetNotFound):
        repository.authorize_assets("project_b", (authorized,))
    with pytest.raises(AssetIdentityConflict):
        repository.authorize_assets(
            "project_a",
            (authorized.model_copy(update={"kind": "photo"}),),
        )
    with pytest.raises(AssetIdentityConflict):
        repository.authorize_assets(
            "project_a",
            (authorized.model_copy(update={"content_hash": "b" * 64}),),
        )


def test_processing_claim_is_durable_replay_safe_and_owned(repository) -> None:
    repository.register_assets((_record(),))

    claimed = repository.enqueue_processing(
        "project_a", ("asset_001",), "job_media_001"
    )
    replay = repository.enqueue_processing(
        "project_a", ("asset_001",), "job_media_replay"
    )

    assert claimed.created is True
    assert replay.created is False
    assert replay.job_id == "job_media_001"
    status = repository.list_processing_status("project_a")[0]
    assert status.readiness == "processing"
    assert status.processing_job_id == "job_media_001"
    with repository.session_factory() as session:
        assert session.get(Job, "job_media_001").status == "queued"
        assert session.get(Job, "job_media_replay") is None

    ready = repository.mark_processing_ready(
        project_id="project_a",
        asset_id="asset_001",
        job_id="job_media_001",
        proxy_key="project_a/proxy/asset_001.mp4",
        thumbnail_key="project_a/thumbnail/asset_001.jpg",
    )
    assert ready.readiness == "ready"
    assert ready.proxy_key is not None
    assert ready.thumbnail_key is not None

    no_work = repository.enqueue_processing(
        "project_a", ("asset_001",), "job_media_unused"
    )
    assert no_work.created is False
    assert no_work.job_id == "job_media_001"


def test_processing_state_rejects_wrong_job_and_can_retry_failure(repository) -> None:
    repository.register_assets((_record(),))
    repository.enqueue_processing("project_a", ("asset_001",), "job_media_001")

    with pytest.raises(MediaProcessingConflict):
        repository.mark_processing_ready(
            project_id="project_a",
            asset_id="asset_001",
            job_id="job_wrong",
            proxy_key="proxy",
            thumbnail_key="thumbnail",
        )

    failed = repository.mark_processing_failed(
        project_id="project_a",
        asset_id="asset_001",
        job_id="job_media_001",
        error="ffmpeg failed",
    )
    retried = repository.enqueue_processing(
        "project_a", ("asset_001",), "job_media_002"
    )
    assert failed.readiness == "failed"
    assert retried.created is True
    assert retried.job_id == "job_media_002"
