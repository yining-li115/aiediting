"""What the reference's audio tells us, and what must never leave this module.

Only three things travel onward: tempo, the beat grid, and how the cuts sit
against it. The audio itself is extracted to a temporary file, measured, and
deleted - it is not stored, not referenced by any spec, and cannot reach a
rendered video. Section 16.1: transfer editing grammar, not protected content.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ..config import settings
from ..specs.reference import DurationStats, ReferenceMusic


def _extract_audio(video: Path) -> Path | None:
    """Pull the audio to a temp file. The caller must delete it."""
    handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    handle.close()
    out = Path(handle.name)
    proc = subprocess.run(
        [
            settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video),
            "-vn", "-ac", "1", "-ar", "22050",
            "-c:a", "pcm_s16le",
            str(out),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        return None
    return out


def analyse(video: Path, cut_points: list[float]) -> ReferenceMusic:
    """Tempo, beat grid, and how tightly the cuts follow it.

    The audio file is always removed before returning, including on failure.
    """
    audio = _extract_audio(video)
    if audio is None:
        return ReferenceMusic()

    try:
        try:
            import librosa  # noqa: PLC0415
        except ImportError:
            return ReferenceMusic()

        y, sr = librosa.load(str(audio), mono=True)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
        bpm = float(tempo) if not hasattr(tempo, "__len__") else float(tempo[0])
        beat_times = [float(t) for t in beats]

        music = ReferenceMusic(bpm=bpm, beat_count=len(beat_times))
        if beat_times and cut_points:
            music.beat_sync_strength, music.cut_to_beat_offset_ms = _alignment(
                cut_points, beat_times
            )
        return music
    finally:
        # Unconditional: the extracted audio must not survive this call even
        # if the analysis raised.
        audio.unlink(missing_ok=True)


def _alignment(
    cuts: list[float], beats: list[float]
) -> tuple[float, DurationStats]:
    """How close the cuts sit to the beats.

    Strength is the fraction landing within half a beat, which is the widest
    window in which a cut still reads as being "on" that beat rather than the
    next one.
    """
    grid = np.asarray(beats, dtype=np.float64)
    if grid.size < 2:
        return 0.0, DurationStats()

    interval = float(np.median(np.diff(grid)))
    offsets = np.asarray(
        [float(cut - grid[np.argmin(np.abs(grid - cut))]) for cut in cuts]
    )
    within = float((np.abs(offsets) <= interval / 2).mean())

    ms = offsets * 1000.0
    stats = DurationStats(
        mean=round(float(ms.mean()), 1),
        median=round(float(np.median(ms)), 1),
        p10=round(float(np.percentile(ms, 10)), 1),
        p90=round(float(np.percentile(ms, 90)), 1),
        std=round(float(ms.std()), 1),
    )
    # Being within half a beat is not the same as being deliberate: a slow
    # edit hits that window by chance. Scale by how tight the offsets are.
    tightness = max(0.0, 1.0 - float(np.abs(offsets).mean()) / max(interval / 2, 1e-6))
    return round(within * tightness, 3), stats
