"""Neutral FFmpeg/FFprobe process helpers for revision-bound rendering."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..config import settings


class FFmpegError(RuntimeError):
    """FFmpeg or FFprobe could not complete the requested media operation."""


def run_ffmpeg(command: list[str]) -> None:
    """Run an already-constructed FFmpeg command and preserve its real error."""
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        raise FFmpegError(
            f"ffmpeg failed ({process.returncode})\n"
            f"cmd: {' '.join(command)}\n"
            f"stderr: {process.stderr.strip()[:2000]}"
        )


def probe(path: Path) -> dict[str, Any]:
    """Return objective container and stream metadata from FFprobe."""
    process = subprocess.run(
        [
            settings.ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise FFmpegError(
            f"ffprobe failed on {path}: {process.stderr.strip()[:500]}"
        )
    return json.loads(process.stdout)


def probe_duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def has_audio_stream(path: Path) -> bool:
    return any(
        stream.get("codec_type") == "audio"
        for stream in probe(path).get("streams", [])
    )


# Private compatibility name inside the rendering implementation only. Public
# callers use ``run_ffmpeg`` so this helper does not become a second API.
_run = run_ffmpeg
