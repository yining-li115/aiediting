"""Deterministic audio timing facts used as evidence by creative models."""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

from ..contracts.audio import AudioEnergySegment, AudioSignalAnalysis
from ..contracts.moments import MediaSourceSpan


AUDIO_SIGNAL_DETECTOR = "spectral-flux-pulse-v2"
FRAME_SIZE = 1_024
HOP_SIZE = 256
ENERGY_WINDOW_SECONDS = 1.0
MIN_TEMPO_BPM = 40.0
MAX_TEMPO_BPM = 240.0
MIN_BEAT_CONFIDENCE = 0.12
SILENCE_FLOOR_DBFS = -120.0


class AudioSignalAnalysisError(ValueError):
    pass


def analyze_audio_signal(path: Path) -> AudioSignalAnalysis:
    """Measure levels, energy, pulse times, and accents from mono PCM16 WAV."""

    samples, sample_rate = _read_pcm16_mono(path)
    duration_ms = max(1, round(samples.size / sample_rate * 1_000))
    peak_dbfs = _dbfs(float(np.max(np.abs(samples), initial=0.0)))
    integrated_rms_dbfs = _dbfs(float(np.sqrt(np.mean(np.square(samples)))))
    energy_curve = _energy_curve(samples, sample_rate, duration_ms)
    novelty = _spectral_flux(samples, sample_rate)
    transient_times_ms = _transient_times(novelty, sample_rate, duration_ms)
    bpm, confidence, beat_times_ms, accent_times_ms = _pulse_grid(
        samples,
        sample_rate,
        duration_ms,
        novelty=novelty,
    )
    return AudioSignalAnalysis(
        duration_ms=duration_ms,
        sample_rate=sample_rate,
        integrated_rms_dbfs=integrated_rms_dbfs,
        peak_dbfs=peak_dbfs,
        bpm=bpm,
        beat_confidence=confidence,
        beat_times_ms=beat_times_ms,
        accent_times_ms=accent_times_ms,
        transient_times_ms=transient_times_ms,
        energy_curve=energy_curve,
        detector=AUDIO_SIGNAL_DETECTOR,
    )


def _read_pcm16_mono(path: Path) -> tuple[np.ndarray, int]:
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioSignalAnalysisError(f"invalid WAV audio: {path}") from exc
    if sample_rate <= 0 or not frames:
        raise AudioSignalAnalysisError("audio signal is empty")
    if sample_width != 2:
        raise AudioSignalAnalysisError("audio signal analysis requires PCM16 WAV")
    values = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32_768.0
    if channels > 1:
        if values.size % channels:
            raise AudioSignalAnalysisError("audio channel data is incomplete")
        values = values.reshape(-1, channels).mean(axis=1)
    return values, sample_rate


def _energy_curve(
    samples: np.ndarray,
    sample_rate: int,
    duration_ms: int,
) -> tuple[AudioEnergySegment, ...]:
    window = max(1, round(sample_rate * ENERGY_WINDOW_SECONDS))
    measured: list[tuple[int, int, float, float]] = []
    for start in range(0, samples.size, window):
        end = min(start + window, samples.size)
        chunk = samples[start:end]
        rms = _dbfs(float(np.sqrt(np.mean(np.square(chunk)))))
        peak = _dbfs(float(np.max(np.abs(chunk), initial=0.0)))
        measured.append(
            (
                round(start / sample_rate * 1_000),
                min(duration_ms, max(1, round(end / sample_rate * 1_000))),
                rms,
                peak,
            )
        )
    levels = np.asarray([item[2] for item in measured], dtype=np.float32)
    low = float(np.min(levels))
    high = float(np.max(levels))
    spread = high - low
    return tuple(
        AudioEnergySegment(
            source=MediaSourceSpan(start=start, end=end, timescale=1_000),
            rms_dbfs=rms,
            peak_dbfs=peak,
            relative_energy=(0.5 if spread < 1e-6 else (rms - low) / spread),
        )
        for start, end, rms, peak in measured
    )


def _pulse_grid(
    samples: np.ndarray,
    sample_rate: int,
    duration_ms: int,
    *,
    novelty: np.ndarray | None = None,
) -> tuple[float | None, float, tuple[int, ...], tuple[int, ...]]:
    if samples.size < FRAME_SIZE * 4:
        return None, 0.0, (), ()
    if novelty is None:
        novelty = _spectral_flux(samples, sample_rate)
    if novelty.size < 4 or float(np.max(novelty, initial=0.0)) <= 1e-9:
        return None, 0.0, (), ()
    hop_seconds = HOP_SIZE / sample_rate
    min_lag = max(1, round(60.0 / MAX_TEMPO_BPM / hop_seconds))
    max_lag = min(
        novelty.size - 1,
        round(60.0 / MIN_TEMPO_BPM / hop_seconds),
    )
    if max_lag <= min_lag:
        return None, 0.0, (), ()

    correlations: list[float] = []
    lags = range(min_lag, max_lag + 1)
    for lag in lags:
        left = novelty[:-lag]
        right = novelty[lag:]
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        correlations.append(
            0.0 if denominator <= 1e-12 else float(np.dot(left, right) / denominator)
        )
    scores = np.asarray(correlations, dtype=np.float32)
    raw_best = float(np.max(scores))
    # Autocorrelation also peaks at integer multiples of the pulse period.
    # When those peaks are effectively tied, the shortest lag represents the
    # actually observed pulse grid instead of an arbitrary half/third tempo.
    near_best = np.flatnonzero(scores >= raw_best * 0.95)
    best_index = int(near_best[0])
    best_lag = min_lag + best_index
    best = float(scores[best_index])
    baseline = float(np.median(scores))
    confidence = max(0.0, min(1.0, (best - baseline) / max(1.0 - baseline, 1e-6)))
    if confidence < MIN_BEAT_CONFIDENCE:
        return None, confidence, (), ()

    phase_scores = np.asarray(
        [float(np.sum(novelty[offset::best_lag])) for offset in range(best_lag)],
        dtype=np.float32,
    )
    phase = int(np.argmax(phase_scores))
    beat_frames: list[int] = []
    search_radius = max(1, best_lag // 5)
    cursor = phase
    while cursor < novelty.size:
        start = max(0, cursor - search_radius)
        end = min(novelty.size, cursor + search_radius + 1)
        refined = start + int(np.argmax(novelty[start:end]))
        if not beat_frames or refined > beat_frames[-1]:
            beat_frames.append(refined)
        cursor += best_lag
    beat_times = tuple(
        sorted(
            {
                min(duration_ms - 1, max(0, round(frame * hop_seconds * 1_000)))
                for frame in beat_frames
            }
        )
    )
    if len(beat_times) < 2:
        return None, confidence, (), ()
    beat_strengths = np.asarray(
        [novelty[min(frame, novelty.size - 1)] for frame in beat_frames],
        dtype=np.float32,
    )
    accent_floor = float(np.quantile(beat_strengths, 0.75))
    accent_times = tuple(
        time
        for time, strength in zip(beat_times, beat_strengths)
        if float(strength) >= accent_floor
    )
    bpm = 60.0 / (best_lag * hop_seconds)
    return round(bpm, 3), confidence, beat_times, accent_times


def _transient_times(
    novelty: np.ndarray,
    sample_rate: int,
    duration_ms: int,
) -> tuple[int, ...]:
    """Return measured spectral onsets without assigning semantic meaning."""

    if novelty.size < 3:
        return ()
    threshold = max(0.2, float(np.quantile(novelty, 0.85)))
    candidates = np.flatnonzero(
        (novelty[1:-1] >= novelty[:-2])
        & (novelty[1:-1] > novelty[2:])
        & (novelty[1:-1] >= threshold)
    ) + 1
    hop_ms = HOP_SIZE / sample_rate * 1_000
    minimum_gap_frames = max(1, round(80.0 / hop_ms))
    selected: list[int] = []
    for frame in candidates:
        frame = int(frame)
        if selected and frame - selected[-1] < minimum_gap_frames:
            if novelty[frame] > novelty[selected[-1]]:
                selected[-1] = frame
            continue
        selected.append(frame)
    return tuple(
        sorted(
            {
                min(duration_ms - 1, max(0, round(frame * hop_ms)))
                for frame in selected
            }
        )
    )


def _spectral_flux(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    window = np.hanning(FRAME_SIZE).astype(np.float32)
    values: list[float] = []
    previous: np.ndarray | None = None
    for start in range(0, samples.size - FRAME_SIZE + 1, HOP_SIZE):
        spectrum = np.abs(np.fft.rfft(samples[start : start + FRAME_SIZE] * window))
        if previous is None:
            values.append(0.0)
        else:
            values.append(float(np.sum(np.maximum(spectrum - previous, 0.0))))
        previous = spectrum
    flux = np.log1p(np.asarray(values, dtype=np.float32))
    local_window = max(3, round(1.0 / (HOP_SIZE / sample_rate)))
    local_mean = np.convolve(
        flux,
        np.ones(local_window, dtype=np.float32) / local_window,
        mode="same",
    )
    novelty = np.maximum(flux - local_mean, 0.0)
    scale = float(np.max(novelty, initial=0.0))
    return novelty if scale <= 1e-12 else novelty / scale


def _dbfs(amplitude: float) -> float:
    if amplitude <= 1e-6:
        return SILENCE_FLOOR_DBFS
    return max(SILENCE_FLOOR_DBFS, min(0.0, 20.0 * math.log10(amplitude)))
