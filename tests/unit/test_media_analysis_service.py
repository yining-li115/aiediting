from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from aive.contracts.moments import (
    AudioUnderstanding,
    DetectedShot,
    MediaSourceSpan,
    ModelProvenance,
    MomentGroupDecision,
    SourceUnderstanding,
    TranscriptDraft,
    UnderstandingEvidence,
)
from aive.media.analysis import (
    MediaAnalysisBatchFailed,
    MediaAnalysisApplication,
    analysis_windows,
    audio_windows,
)
from aive.media.analysis_repository import AnalysisAssetRecord, MediaAnalysisTicket
from aive.providers import Role
from aive.storage import LocalStorage
from tests.v2_fixtures import source_understanding


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
    async def understand_source(self, evidence, samples, clip=None):
        return SourceUnderstanding(
            summary="one continuous walk through a room",
            subjects=("person",),
            actions=("walking",),
            narrative_function="continuous activity",
            visual_language=("wide spatial view",),
            emotions=("calm",),
            editorial_value="Useful continuous movement",
            worth_using=True,
            confidence=0.8,
            evidence=(
                UnderstandingEvidence(
                    evidence_id=evidence.evidence_id,
                    kind="visual_sample",
                    description="Supplied source samples",
                    source=evidence.source,
                    shot_ids=evidence.shot_ids,
                ),
            ),
            provenance=ModelProvenance(
                provider="fixture-material",
                model="fixture-v2",
                prompt_version="fixture-v2",
            ),
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
                    understanding=source_understanding(
                        shot_ids=(shots[1].shot_id, shots[0].shot_id),
                        source=MediaSourceSpan(start=0, end=4_000),
                        summary="invalid order",
                    ),
                ),
                MomentGroupDecision(
                    shot_ids=(shots[2].shot_id,),
                    understanding=source_understanding(
                        shot_ids=(shots[2].shot_id,),
                        source=shots[2].source,
                        summary="last shot",
                    ),
                ),
            )
        return (
            MomentGroupDecision(
                shot_ids=(shots[0].shot_id, shots[1].shot_id),
                understanding=source_understanding(
                    shot_ids=(shots[0].shot_id, shots[1].shot_id),
                    source=MediaSourceSpan(start=0, end=4_000),
                    summary="a continuous spoken explanation",
                ),
            ),
            MomentGroupDecision(
                shot_ids=(shots[2].shot_id,),
                understanding=source_understanding(
                    shot_ids=(shots[2].shot_id,),
                    source=shots[2].source,
                    summary="the speaker concludes",
                ),
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


class SignedURLStorage(LocalStorage):
    def url(self, key: str) -> str | None:
        return f"https://media.example.invalid/{key}"


class RemoteURLASR:
    requires_remote_url = True

    def __init__(self) -> None:
        self.source_urls: list[str | None] = []

    async def transcribe(self, audio, *, source_url=None):
        self.source_urls.append(source_url)
        return (
            TranscriptDraft(
                source=MediaSourceSpan(start=100, end=900),
                text="remote timed line",
            ),
        )


class FakeAudioUnderstanding:
    async def understand_audio(self, evidence, audio):
        return AudioUnderstanding(
            evidence_id=evidence.evidence_id,
            source=evidence.source,
            summary="Footsteps and a spoken line in a room",
            speech_meaning=("The speaker delivers the supplied line",),
            sound_events=("footsteps", "room ambience"),
            rhythm_and_energy="Steady walking rhythm",
            editorial_value="Useful synchronized production sound",
            confidence=0.9,
            provenance=ModelProvenance(
                provider="fixture-audio",
                model="fixture-audio-v2",
                prompt_version="fixture-audio-v2",
            ),
        )


def _audio(source: Path, start: float, end: float, target: Path) -> Path:
    target.write_bytes(b"temporary wav")
    return target


@pytest.mark.asyncio
async def test_remote_asr_receives_signed_audio_window_and_cleans_it_up(
    tmp_path,
) -> None:
    storage = SignedURLStorage(tmp_path / "storage")
    storage.put_bytes(b"original", "project_a/original/asset_a.mp4")
    asset = AnalysisAssetRecord(
        asset_id="asset_a",
        project_id="project_a",
        storage_key="project_a/original/asset_a.mp4",
        proxy_key=None,
        kind="video",
        duration_seconds=2.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
        readiness="ready",
        analysis_readiness="processing",
        analysis_job_id="job_analysis",
    )
    repository = FakeRepository(asset)
    asr = RemoteURLASR()

    def provider(role: Role, project_id: str):
        assert role == Role.ASR
        return asr

    application = MediaAnalysisApplication(
        storage=storage,
        repository=repository,
        audio_extractor=_audio,
        provider_factory=provider,
    )
    shot = DetectedShot(
        shot_id="shot_1",
        project_id="project_a",
        asset_id="asset_a",
        ordinal=0,
        source=MediaSourceSpan(start=0, end=2_000),
        detector="fixture",
    )

    transcripts = await application._transcribe_asset(
        asset,
        (shot,),
        FakeHandle(),
    )

    assert len(transcripts) == 1
    assert asr.source_urls == [
        "https://media.example.invalid/project_a/analysis-audio/"
        "job_analysis-asset_a-0000.wav"
    ]
    assert not tuple(storage.iter_keys("project_a/analysis-audio"))


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
        if role == Role.AUDIO:
            return FakeAudioUnderstanding()
        return FakeEmbedding()

    application = MediaAnalysisApplication(
        storage=storage,
        repository=repository,
        shot_detector=lambda path: [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)],
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
    assert len(first.understanding.audio_understanding) == 2
    assert first.understanding.audio_understanding[0].sound_events == (
        "footsteps",
        "room ambience",
    )


@pytest.mark.asyncio
async def test_invalid_grouping_fails_closed_without_semantic_fallback(tmp_path) -> None:
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
        keyframe_extractor=_keyframes,
        provider_factory=provider,
    )

    with pytest.raises(MediaAnalysisBatchFailed):
        await application.process(FakeHandle())

    assert repository.saved is None
    assert repository.failed is not None


class FailingMaterial:
    async def understand_source(self, evidence, samples, clip=None):
        raise RuntimeError("model unavailable")


@pytest.mark.asyncio
async def test_semantic_failure_is_durable_and_has_no_rule_fallback(tmp_path) -> None:
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
        keyframe_extractor=_keyframes,
        provider_factory=provider,
    )

    with pytest.raises(MediaAnalysisBatchFailed):
        await application.process(FakeHandle())

    assert repository.saved is None
    assert repository.failed["error"] == "model unavailable"
