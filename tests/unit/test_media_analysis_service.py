from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from aive.contracts.moments import (
    MediaSourceSpan,
    MomentGroupDecision,
    TranscriptDraft,
)
from aive.material.quality import QualityMetrics
from aive.media.analysis import (
    MediaAnalysisApplication,
    analysis_windows,
    audio_windows,
)
from aive.media.analysis_repository import AnalysisAssetRecord, MediaAnalysisTicket
from aive.providers import Role
from aive.specs.segment import MaterialSegment
from aive.storage import LocalStorage


def test_analysis_windows_sample_long_shot_without_redefining_its_span() -> None:
    windows = analysis_windows(0.0, 20.0)

    assert windows == (
        (0.0, 6.0),
        (4.0, 10.0),
        (8.0, 14.0),
        (12.0, 18.0),
        (14.0, 20.0),
    )
    assert MediaSourceSpan(start=0, end=20_000).end == 20_000
    with pytest.raises(ValueError):
        MediaSourceSpan(start=10, end=10)


def test_audio_windows_bound_provider_input_without_redefining_shots() -> None:
    assert audio_windows(0.0, 500.0) == (
        (0.0, 225.0),
        (225.0, 450.0),
        (450.0, 500.0),
    )


class FakeRepository:
    def __init__(self, asset: AnalysisAssetRecord) -> None:
        self.asset = asset
        self.saved = None
        self.failed = None

    def enqueue_analysis(self, project_id, asset_ids, job_id):
        return MediaAnalysisTicket(job_id=job_id, asset_ids=asset_ids, created=True)

    def list_job_assets(self, job_id):
        return (self.asset,)

    def replace_asset_analysis(self, **values):
        self.saved = values

    def mark_analysis_failed(self, **values):
        self.failed = values

    def list_moments(self, project_id):
        return ()

    def list_transcripts(self, project_id):
        return ()


class FakeMaterial:
    async def analyze_segment(self, segment, keyframes, clip=None):
        return MaterialSegment(
            segment_id=segment.segment_id,
            project_id=segment.project_id,
            source_id=segment.asset_id,
            start=segment.start,
            end=segment.end,
            semantic="one continuous walk through a room",
            semantic_role="person_activity",
            shot_type="wide",
            people=1,
            emotion=["calm"],
            interestingness=0.8,
        )


class FakeEmbedding:
    async def embed_images(self, images):
        return np.ones((len(images), 4), dtype=np.float32)


@dataclass
class FakeHandle:
    job_id: str = "job_analysis"

    def update(self, stage=None, progress=None):
        pass

    def emit(self, type, **data):
        pass


def _quality(*args) -> QualityMetrics:
    return QualityMetrics(
        sharpness=0.9,
        exposure_score=0.9,
        stability_score=0.9,
        motion_quality=0.9,
        technical_quality=0.9,
        flat_frame_ratio=0.0,
        camera_motion="slow_pan",
    )


def _keyframes(video_path: str, offsets: list[float], targets: list[Path]):
    for target in targets:
        target.write_bytes(b"jpeg")
    return targets


@pytest.mark.asyncio
async def test_long_physical_shot_becomes_one_complete_moment(tmp_path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    storage.put_bytes(b"proxy", "project_a/proxy/asset_a.mp4")
    asset = AnalysisAssetRecord(
        asset_id="asset_a",
        project_id="project_a",
        storage_key="project_a/original/asset_a.mp4",
        proxy_key="project_a/proxy/asset_a.mp4",
        kind="video",
        duration_seconds=20.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=False,
        readiness="ready",
        analysis_readiness="processing",
        analysis_job_id="job_analysis",
    )
    repository = FakeRepository(asset)

    def provider(role: Role, project_id: str):
        return FakeMaterial() if role == Role.MATERIAL else FakeEmbedding()

    application = MediaAnalysisApplication(
        storage=storage,
        repository=repository,
        shot_detector=lambda path: [(0.0, 20.0)],
        video_quality=_quality,
        keyframe_extractor=_keyframes,
        provider_factory=provider,
    )

    result = await application.process(FakeHandle())

    assert repository.failed is None
    assert result == {
        "asset_count": 1,
        "moment_count": 1,
        "transcript_count": 0,
        "partial_count": 0,
    }
    assert repository.saved["readiness"] == "ready"
    assert len(repository.saved["shots"]) == 1
    assert len(repository.saved["moments"]) == 1
    moment = repository.saved["moments"][0].evidence
    assert moment.source == MediaSourceSpan(start=0, end=20_000)
    assert moment.analysis_window_count == 5
    assert moment.keyframe_count == 5
    assert moment.semantic == "one continuous walk through a room"


class GroupingMaterial(FakeMaterial):
    def __init__(self, *, invalid: bool = False) -> None:
        self.invalid = invalid
        self.observations = ()

    async def group_moments(self, shots):
        self.observations = shots
        if self.invalid:
            return (
                MomentGroupDecision(
                    shot_ids=(shots[1].shot_id, shots[0].shot_id),
                    semantic="invalid order",
                ),
                MomentGroupDecision(
                    shot_ids=(shots[2].shot_id,),
                    semantic="last shot",
                ),
            )
        return (
            MomentGroupDecision(
                shot_ids=(shots[0].shot_id, shots[1].shot_id),
                semantic="a continuous spoken explanation",
                semantic_role="person_activity",
            ),
            MomentGroupDecision(
                shot_ids=(shots[2].shot_id,),
                semantic="the speaker concludes",
                semantic_role="person_activity",
            ),
        )


class FakeASR:
    async def transcribe(self, audio):
        return (
            TranscriptDraft(
                source=MediaSourceSpan(start=0, end=2_000),
                text="spoken line",
                language="en",
                confidence=0.9,
            ),
        )


def _audio(source: Path, start: float, end: float, target: Path) -> Path:
    target.write_bytes(b"temporary wav")
    return target


@pytest.mark.asyncio
async def test_asr_evidence_drives_consecutive_multi_shot_moments(tmp_path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    storage.put_bytes(b"original", "project_a/original/asset_a.mp4")
    storage.put_bytes(b"proxy", "project_a/proxy/asset_a.mp4")
    asset = AnalysisAssetRecord(
        asset_id="asset_a",
        project_id="project_a",
        storage_key="project_a/original/asset_a.mp4",
        proxy_key="project_a/proxy/asset_a.mp4",
        kind="video",
        duration_seconds=6.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
        readiness="ready",
        analysis_readiness="processing",
        analysis_job_id="job_analysis",
    )
    repository = FakeRepository(asset)
    material = GroupingMaterial()

    def provider(role: Role, project_id: str):
        if role == Role.MATERIAL:
            return material
        if role == Role.ASR:
            return FakeASR()
        return FakeEmbedding()

    application = MediaAnalysisApplication(
        storage=storage,
        repository=repository,
        shot_detector=lambda path: [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)],
        video_quality=_quality,
        keyframe_extractor=_keyframes,
        audio_extractor=_audio,
        provider_factory=provider,
    )

    result = await application.process(FakeHandle())

    assert result == {
        "asset_count": 1,
        "moment_count": 2,
        "transcript_count": 3,
        "partial_count": 0,
    }
    assert repository.saved["readiness"] == "ready"
    assert len(repository.saved["transcripts"]) == 3
    assert [shot.transcript for shot in material.observations] == [
        "spoken line",
        "spoken line",
        "spoken line",
    ]
    first = repository.saved["moments"][0].evidence
    assert first.shot_ids == ("asset_a_shot_0000", "asset_a_shot_0001")
    assert first.source == MediaSourceSpan(start=0, end=4_000)
    assert first.asr_text == "spoken line spoken line"
    assert first.has_speech is True


@pytest.mark.asyncio
async def test_invalid_grouping_falls_back_to_single_shot_moments(tmp_path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    storage.put_bytes(b"proxy", "project_a/proxy/asset_a.mp4")
    asset = AnalysisAssetRecord(
        asset_id="asset_a",
        project_id="project_a",
        storage_key="project_a/original/asset_a.mp4",
        proxy_key="project_a/proxy/asset_a.mp4",
        kind="video",
        duration_seconds=6.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=False,
        readiness="ready",
        analysis_readiness="processing",
        analysis_job_id="job_analysis",
    )
    repository = FakeRepository(asset)
    material = GroupingMaterial(invalid=True)

    def provider(role: Role, project_id: str):
        return material if role == Role.MATERIAL else FakeEmbedding()

    application = MediaAnalysisApplication(
        storage=storage,
        repository=repository,
        shot_detector=lambda path: [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)],
        video_quality=_quality,
        keyframe_extractor=_keyframes,
        provider_factory=provider,
    )

    result = await application.process(FakeHandle())

    assert result["partial_count"] == 1
    assert repository.saved["readiness"] == "partial"
    assert len(repository.saved["moments"]) == 3
    assert all(
        len(moment.evidence.shot_ids) == 1
        for moment in repository.saved["moments"]
    )


class FailingMaterial:
    async def analyze_segment(self, segment, keyframes, clip=None):
        raise RuntimeError("model unavailable")


@pytest.mark.asyncio
async def test_semantic_failure_keeps_deterministic_moment_as_partial(tmp_path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    storage.put_bytes(b"proxy", "project_a/proxy/asset_a.mp4")
    asset = AnalysisAssetRecord(
        asset_id="asset_a",
        project_id="project_a",
        storage_key="project_a/original/asset_a.mp4",
        proxy_key="project_a/proxy/asset_a.mp4",
        kind="video",
        duration_seconds=2.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=False,
        readiness="ready",
        analysis_readiness="processing",
        analysis_job_id="job_analysis",
    )
    repository = FakeRepository(asset)

    def provider(role: Role, project_id: str):
        return FailingMaterial() if role == Role.MATERIAL else FakeEmbedding()

    application = MediaAnalysisApplication(
        storage=storage,
        repository=repository,
        shot_detector=lambda path: [(0.0, 2.0)],
        video_quality=_quality,
        keyframe_extractor=_keyframes,
        provider_factory=provider,
    )

    result = await application.process(FakeHandle())

    assert result["partial_count"] == 1
    assert repository.saved["readiness"] == "partial"
    moment = repository.saved["moments"][0].evidence
    assert moment.semantic_status == "failed"
    assert moment.technical_quality == 0.9
    assert moment.source.end == 2_000
