from __future__ import annotations

import wave

import numpy as np
import pytest

from aive.media.audio_signal import analyze_audio_signal


def _write_click_track(path, *, bpm: float, duration_seconds: float = 8.0) -> None:
    sample_rate = 16_000
    samples = np.zeros(round(sample_rate * duration_seconds), dtype=np.float32)
    beat_samples = round(sample_rate * 60 / bpm)
    click_length = round(sample_rate * 0.02)
    click = np.hanning(click_length * 2)[:click_length].astype(np.float32) * 0.9
    for start in range(0, samples.size, beat_samples):
        end = min(samples.size, start + click_length)
        samples[start:end] += click[: end - start]
    pcm = np.clip(samples, -1, 1)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes((pcm * 32_767).astype("<i2").tobytes())


def test_signal_analyzer_measures_pulse_grid_and_levels(tmp_path) -> None:
    audio = tmp_path / "clicks.wav"
    _write_click_track(audio, bpm=120.0)

    result = analyze_audio_signal(audio)

    assert result.duration_ms == 8_000
    assert result.bpm is not None
    assert result.bpm == pytest.approx(120.0, abs=4.0)
    assert result.beat_confidence > 0.1
    assert len(result.beat_times_ms) >= 12
    assert set(result.accent_times_ms) <= set(result.beat_times_ms)
    assert result.peak_dbfs > -3.0
    assert len(result.energy_curve) == 8


def test_signal_analyzer_does_not_invent_beats_for_silence(tmp_path) -> None:
    audio = tmp_path / "silence.wav"
    sample_rate = 16_000
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(np.zeros(sample_rate * 2, dtype="<i2").tobytes())

    result = analyze_audio_signal(audio)

    assert result.bpm is None
    assert result.beat_times_ms == ()
    assert result.integrated_rms_dbfs == -120.0
