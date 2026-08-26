"""Quality tiers.

Proxy and high quality differ only in the numbers below and go through exactly
the same code path. If they ever diverge into separate implementations, a
critic's judgement on a proxy render stops transferring to the final video,
which is the whole reason the proxy exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..specs.timeline import RenderConstraints

Quality = Literal["proxy", "hq"]


@dataclass(frozen=True)
class RenderProfile:
    width: int
    height: int
    fps: int
    crf: int
    preset: str
    video_codec: str
    audio_codec: str
    audio_bitrate: str


def resolve_profile(constraints: RenderConstraints, quality: Quality) -> RenderProfile:
    width, height = constraints.size()
    if quality == "proxy":
        # Roughly half resolution, rounded to even numbers (h264 requires it).
        width = max(2, (width // 2) // 2 * 2)
        height = max(2, (height // 2) // 2 * 2)
        return RenderProfile(
            width=width,
            height=height,
            fps=constraints.fps,
            crf=30,
            preset="veryfast",
            video_codec="libx264",
            audio_codec="aac",
            audio_bitrate="96k",
        )
    return RenderProfile(
        width=width,
        height=height,
        fps=constraints.fps,
        crf=constraints.crf,
        preset="medium",
        video_codec="libx264",
        audio_codec="aac",
        audio_bitrate=constraints.audio_bitrate,
    )
