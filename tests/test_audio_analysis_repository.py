from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from aive.contracts.audio import (
    AudioAssetAnalysis,
    AudioEnergySegment,
    AudioSignalAnalysis,
)
from aive.contracts.moments import AudioUnderstanding, MediaSourceSpan
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.models import Base, Project
from aive.media.repository import MediaAssetRecord
from tests.v2_fixtures import FIXTURE_PROVENANCE


def test_audio_asset_analysis_round_trips_through_media_repository(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'audio-analysis.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Session(engine) as session:
        session.add(Project(id="project_audio_repo"))
        session.commit()
    repository = SqlAlchemyMediaAssetRepository(sessions)
    repository.register_assets(
        (
            MediaAssetRecord(
                asset_id="asset_audio_repo",
                project_id="project_audio_repo",
                storage_key="project_audio_repo/original/music.wav",
                kind="audio",
                original_name="music.wav",
                sha256="a" * 64,
                size_bytes=1_024,
                duration_seconds=2,
                width=1,
                height=1,
                fps=0,
                has_audio=True,
            ),
        )
    )
    analysis = AudioAssetAnalysis(
        project_id="project_audio_repo",
        asset_id="asset_audio_repo",
        content_hash="a" * 64,
        basis_hash="b" * 64,
        signal=AudioSignalAnalysis(
            duration_ms=2_000,
            sample_rate=16_000,
            integrated_rms_dbfs=-18,
            peak_dbfs=-1,
            bpm=120,
            beat_confidence=0.8,
            beat_times_ms=(0, 500, 1_000, 1_500),
            accent_times_ms=(0, 1_000),
            transient_times_ms=(0, 500, 1_000, 1_500),
            energy_curve=(
                AudioEnergySegment(
                    source=MediaSourceSpan(start=0, end=2_000),
                    rms_dbfs=-18,
                    peak_dbfs=-1,
                    relative_energy=0.5,
                ),
            ),
            detector="fixture-signal-v2",
        ),
        understandings=(
            AudioUnderstanding(
                evidence_id="audio_evidence_repo",
                source=MediaSourceSpan(start=0, end=2_000),
                summary="A measured music bed",
                music_description="Warm instrumental pulse",
                editorial_value="Supports a gentle montage",
                confidence=0.9,
                provenance=FIXTURE_PROVENANCE,
            ),
        ),
        analyzed_at=datetime.now(timezone.utc),
    )

    saved = repository.save_audio_analysis(analysis)
    (reloaded,) = repository.list_processing_status("project_audio_repo")

    assert saved.audio_analysis == analysis
    assert reloaded.audio_analysis == analysis
