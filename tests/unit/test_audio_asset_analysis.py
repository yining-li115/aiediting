from __future__ import annotations

import shutil
import wave
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np

from aive.contracts.moments import AudioUnderstanding
from aive.contracts.project_doc import (
    AssetRef,
    DependencyRef,
    DependencySet,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    Timeline,
)
from aive.media.audio_asset_analysis import AudioAssetAnalysisApplication
from aive.media.repository import AssetProcessingRecord, MediaAssetRecord
from tests.v2_fixtures import FIXTURE_PROVENANCE


def _write_click_track(path) -> None:
    sample_rate = 16_000
    samples = np.zeros(sample_rate * 4, dtype=np.float32)
    click = np.hanning(640)[:320].astype(np.float32) * 0.9
    for start in range(0, samples.size, sample_rate // 2):
        samples[start : start + click.size] += click
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes((samples * 32_767).astype("<i2").tobytes())


class _Storage:
    def __init__(self, path) -> None:
        self.path = path

    def open_local(self, key):
        return self.path


class _Repository:
    def __init__(self, identity, status) -> None:
        self.identity = identity
        self.status = status
        self.saved = []

    def get_asset(self, asset_id):
        return self.identity if asset_id == self.identity.asset_id else None

    def save_audio_analysis(self, analysis):
        self.saved.append(analysis)
        self.status = replace(self.status, audio_analysis=analysis)
        return self.status


class _Provider:
    name = "fixture-audio"
    model = "fixture-audio-v1"
    prompt_version = "fixture-audio-prompt-v1"

    def __init__(self) -> None:
        self.calls = 0

    async def understand_audio(self, evidence, audio):
        self.calls += 1
        return AudioUnderstanding(
            evidence_id=evidence.evidence_id,
            source=evidence.source,
            summary="Warm rhythmic instrumental",
            music_description="Acoustic pulse with a gentle lift",
            rhythm_and_energy="Stable 120 BPM pulse",
            editorial_value="Supports an upbeat travel progression",
            confidence=0.9,
            provenance=FIXTURE_PROVENANCE,
        )


def _copy_audio(source, start, end, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    return output


async def test_audio_asset_analysis_combines_signal_facts_and_model_meaning(tmp_path) -> None:
    source = tmp_path / "music.wav"
    _write_click_track(source)
    status = AssetProcessingRecord(
        asset_id="asset_music",
        project_id="project_audio",
        storage_key="project_audio/original/music.wav",
        kind="audio",
        original_name="music.wav",
        duration_seconds=4,
        width=1,
        height=1,
        fps=0,
        has_audio=True,
        readiness="ready",
    )
    identity = MediaAssetRecord(
        asset_id=status.asset_id,
        project_id=status.project_id,
        storage_key=status.storage_key,
        kind="audio",
        original_name=status.original_name,
        sha256="a" * 64,
        size_bytes=source.stat().st_size,
        duration_seconds=4,
        width=1,
        height=1,
        fps=0,
        has_audio=True,
    )
    document = ProjectDoc(
        project_id="project_audio",
        revision_id="revision_audio",
        intent=ProjectIntent(task_mode="autonomous", target_duration_frames=120),
        assets=(
            AssetRef(
                asset_id="asset_music",
                kind="audio",
                roles=("music",),
            ),
        ),
        timeline=Timeline(
            duration_frames=120,
            output=OutputSettings(
                width=1080,
                height=1920,
                fps=Rational(num=30, den=1),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1")
        ),
    )
    repository = _Repository(identity, status)
    provider = _Provider()
    application = AudioAssetAnalysisApplication(
        storage=_Storage(source),
        assets=repository,
        provider=provider,
        audio_extractor=_copy_audio,
    )

    (analysis,) = await application.resolve(document, (status,))
    (cached,) = await application.resolve(document, (repository.status,))

    assert analysis.signal.bpm is not None
    assert analysis.signal.transient_times_ms
    assert analysis.understandings[0].music_description is not None
    assert repository.saved == [analysis]
    assert cached == analysis
    assert provider.calls == 1
