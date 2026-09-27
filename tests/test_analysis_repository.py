from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aive.contracts.moments import (
    DetectedShot,
    MediaSourceSpan,
    MomentEvidence,
    TranscriptEvidence,
)
from aive.db.analysis_repository import SqlAlchemyMediaAnalysisRepository
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.models import Base, Job, Project
from aive.media.analysis_repository import (
    MediaAnalysisConflict,
    PersistedMoment,
    PersistedTranscript,
)
from aive.media.repository import MediaAssetRecord
from tests.v2_fixtures import source_understanding


@pytest.fixture
def repositories(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'analysis.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with sessions.begin() as session:
        session.add(Project(id="project_a"))
    media = SqlAlchemyMediaAssetRepository(sessions)
    media.register_assets(
        (
            MediaAssetRecord(
                asset_id="asset_a",
                project_id="project_a",
                storage_key="project_a/original/asset_a.mp4",
                kind="video",
                original_name="a.mp4",
                sha256="a" * 64,
                size_bytes=10,
                duration_seconds=8.0,
                width=1920,
                height=1080,
                fps=30.0,
                has_audio=True,
            ),
        )
    )
    media.enqueue_processing("project_a", ("asset_a",), "job_processing")
    media.mark_processing_ready(
        project_id="project_a",
        asset_id="asset_a",
        job_id="job_processing",
        proxy_key="project_a/proxy/asset_a.mp4",
        thumbnail_key="project_a/thumbnail/asset_a.jpg",
    )
    return SqlAlchemyMediaAnalysisRepository(sessions), media, sessions


def test_analysis_claim_is_replay_safe_and_requires_ready_asset(repositories) -> None:
    repository, _, sessions = repositories

    ticket = repository.enqueue_analysis(
        "project_a", ("asset_a",), "job_analysis"
    )
    replay = repository.enqueue_analysis(
        "project_a", ("asset_a",), "job_unused"
    )

    assert ticket.created is True
    assert replay.created is False
    assert replay.job_id == "job_analysis"
    with sessions() as session:
        assert session.get(Job, "job_analysis").status == "queued"
        assert session.get(Job, "job_unused") is None


def test_persisted_moment_is_listed_and_old_job_cannot_overwrite(repositories) -> None:
    repository, media, _ = repositories
    repository.enqueue_analysis("project_a", ("asset_a",), "job_analysis")
    source = MediaSourceSpan(start=0, end=8_000)
    shot = DetectedShot(
        shot_id="asset_a_shot_0000",
        project_id="project_a",
        asset_id="asset_a",
        ordinal=0,
        source=source,
        detector="test-detector",
    )
    moment = MomentEvidence(
        moment_id="asset_a_moment_0000",
        project_id="project_a",
        asset_id="asset_a",
        ordinal=0,
        shot_ids=(shot.shot_id,),
        source=source,
        analysis_window_count=2,
        keyframe_count=2,
        understanding=source_understanding(
            shot_ids=(shot.shot_id,),
            source=source,
            summary="walking outside",
        ),
        transcript_segment_ids=("asset_a_transcript_0000",),
        has_speech=True,
        asr_text="hello outside",
    )
    transcript = TranscriptEvidence(
        transcript_id="asset_a_transcript_0000",
        project_id="project_a",
        asset_id="asset_a",
        source=source,
        text="hello outside",
        language="en",
        confidence=0.92,
    )
    repository.replace_asset_analysis(
        project_id="project_a",
        asset_id="asset_a",
        job_id="job_analysis",
        shots=(shot,),
        moments=(
            PersistedMoment(
                evidence=moment,
                keyframe_keys=("one.jpg", "two.jpg"),
                analyzed_at=datetime.now(timezone.utc),
            ),
        ),
        transcripts=(
            PersistedTranscript(
                evidence=transcript,
                raw_response="hello outside",
                analyzed_at=datetime.now(timezone.utc),
            ),
        ),
        readiness="ready",
    )

    assert repository.list_moments("project_a") == (moment,)
    assert repository.list_transcripts("project_a") == (transcript,)
    status = media.list_processing_status("project_a")[0]
    assert status.analysis_readiness == "ready"
    assert status.moment_count == 1
    assert status.transcript_count == 1
    with pytest.raises(MediaAnalysisConflict):
        repository.mark_analysis_failed(
            project_id="project_a",
            asset_id="asset_a",
            job_id="job_old",
            error="late failure",
        )
