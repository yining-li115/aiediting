"""Accepted upload containers and neutral media defaults."""

from __future__ import annotations


PHOTO_NOMINAL_DURATION = 3.0
VIDEO_SUFFIXES: frozenset[str] = frozenset(
    {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mpg", ".mpeg"}
)
PHOTO_SUFFIXES: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".bmp", ".tiff"}
)
AUDIO_SUFFIXES: frozenset[str] = frozenset(
    {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
)


__all__ = [
    "AUDIO_SUFFIXES",
    "PHOTO_NOMINAL_DURATION",
    "PHOTO_SUFFIXES",
    "VIDEO_SUFFIXES",
]
