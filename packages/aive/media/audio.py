"""Temporary audio extraction for speech understanding."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import settings


class AudioExtractionError(RuntimeError):
    pass


def extract_audio_window(
    source: Path,
    start: float,
    end: float,
    output: Path,
) -> Path:
    """Write one mono 16 kHz WAV window; callers own its lifetime."""
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        settings.ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{max(end - start, 0.1):.3f}",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not output.exists() or output.stat().st_size <= 44:
        output.unlink(missing_ok=True)
        raise AudioExtractionError(
            f"audio extraction failed: {result.stderr.strip()[:400]}"
        )
    return output
