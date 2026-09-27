from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aive.contracts.audio import (
    AudioAssetAnalysis,
    AudioEnergySegment,
    AudioSignalAnalysis,
)
from aive.contracts.moments import (
    AudioUnderstanding,
    MediaSourceSpan,
    ModelProvenance,
)


def _signal() -> AudioSignalAnalysis:
    return AudioSignalAnalysis(
        duration_ms=2_000,
        sample_rate=16_000,
        integrated_rms_dbfs=-18.0,
        peak_dbfs=-1.0,
        bpm=120.0,
        beat_confidence=0.8,
        beat_times_ms=(0, 500, 1_000, 1_500),
        accent_times_ms=(0, 1_000),
        energy_curve=(
            AudioEnergySegment(
                source=MediaSourceSpan(start=0, end=2_000),
                rms_dbfs=-18.0,
                peak_dbfs=-1.0,
                relative_energy=0.5,
            ),
        ),
        detector="fixture-pulse-v1",
    )


def test_audio_asset_analysis_keeps_signal_facts_and_model_meaning_separate() -> None:
    analysis = AudioAssetAnalysis(
        project_id="project_audio",
        asset_id="asset_music",
        content_hash="a" * 64,
        basis_hash="b" * 64,
        signal=_signal(),
        understandings=(
            AudioUnderstanding(
                evidence_id="audio_window_0",
                source=MediaSourceSpan(start=0, end=2_000),
                summary="A restrained instrumental pulse",
                music_description="Sparse percussion with a soft harmonic bed",
                rhythm_and_energy="Stable pulse with a gradual lift",
                editorial_value="Supports a measured visual progression",
                confidence=0.9,
                provenance=ModelProvenance(
                    provider="fixture",
                    model="fixture-audio-v1",
                    prompt_version="fixture-audio-v1",
                ),
            ),
        ),
        analyzed_at=datetime.now(timezone.utc),
    )

    assert analysis.signal.beat_times_ms == (0, 500, 1_000, 1_500)
    assert analysis.understandings[0].music_description is not None


def test_audio_signal_rejects_unmeasured_accents() -> None:
    payload = _signal().model_dump()
    payload["accent_times_ms"] = (250,)

    with pytest.raises(ValidationError, match="accents must reference measured beats"):
        AudioSignalAnalysis.model_validate(payload)


def test_audio_analysis_rejects_model_evidence_outside_asset() -> None:
    with pytest.raises(ValidationError, match="understanding is outside"):
        AudioAssetAnalysis(
            project_id="project_audio",
            asset_id="asset_music",
            content_hash="a" * 64,
            basis_hash="b" * 64,
            signal=_signal(),
            understandings=(
                AudioUnderstanding(
                    evidence_id="audio_window_0",
                    source=MediaSourceSpan(start=0, end=3_000),
                    summary="Too long",
                    editorial_value="Invalid evidence",
                    confidence=0.1,
                    provenance=ModelProvenance(
                        provider="fixture",
                        model="fixture-audio-v1",
                        prompt_version="fixture-audio-v1",
                    ),
                ),
            ),
            analyzed_at=datetime.now(timezone.utc),
        )
