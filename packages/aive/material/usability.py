"""Which segments are allowed into the candidate pool.

Rejections are recorded with a reason rather than silently dropped. "Why was
this clip never used?" is a question that comes up constantly while tuning,
and it is unanswerable if the pool only contains survivors.
"""

from __future__ import annotations

from .quality import QualityMetrics

MIN_DURATION = 0.6
MIN_TECHNICAL_QUALITY = 0.35
MIN_SHARPNESS = 0.2
MAX_FLAT_FRAME_RATIO = 0.6


def judge(
    duration: float, metrics: QualityMetrics, kind: str = "video"
) -> tuple[bool, str | None]:
    """Return (is_usable, reject_reason).

    ``kind`` matters because a rule can be meaningless rather than merely
    lenient: a still has no duration to be too short, so applying the video
    floor to it would reject every photo for a property it cannot have.
    """
    if kind != "photo" and duration < MIN_DURATION:
        return False, f"too_short:{duration:.2f}s"
    if metrics.flat_frame_ratio > MAX_FLAT_FRAME_RATIO:
        return False, f"blank_frames:{metrics.flat_frame_ratio:.2f}"
    if metrics.sharpness < MIN_SHARPNESS:
        return False, f"out_of_focus:{metrics.sharpness:.2f}"
    if metrics.technical_quality < MIN_TECHNICAL_QUALITY:
        return False, f"low_quality:{metrics.technical_quality:.2f}"
    return True, None
