"""Deterministic quality metrics.

Nothing here calls a model. Sharpness, exposure and shake are exactly
computable from pixels, so asking a VLM would cost money, add latency and give
a less reproducible answer. The rule is: anything a deterministic method can
compute accurately must not be handed to a model.

All formulas follow docs/TECHNICAL_PLAN.md section 4.5. Every output is
clamped to [0, 1] so they can be combined without further normalisation.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# Frames sampled per segment for the per-frame statistics (sharpness,
# exposure). Spread evenly: enough to be robust to one bad frame, few enough
# that a few hundred segments stay fast.
SAMPLE_FRAMES = 7

# Motion is measured differently, and the distinction matters. Shake is a
# frame-to-frame property, so it must be measured between *consecutive*
# frames. Comparing evenly-spread samples half a second apart measures how
# much the scene changed, not how steady the camera was - any moving subject
# then reads as violent shake.
MOTION_BURSTS = 3
MOTION_BURST_FRAMES = 6

# TODO(review): the weights that decide whether footage enters the pool at
# all. Chosen by judgement; the honest test is whether a person shown the
# rejected pile agrees it should have been rejected.
W_SHARPNESS = 0.35
W_EXPOSURE = 0.25
W_STABILITY = 0.30
W_MOTION = 0.10

# A still has no camera to be steady and no motion to be smooth. Scoring those
# two as zero would drag every photo's technical quality below the usability
# floor and delete the user's photos without saying so - a metric that does
# not apply must be excluded from the average, not counted as a failure.
PHOTO_W_SHARPNESS = 0.6
PHOTO_W_EXPOSURE = 0.4

# Per-frame displacement, as a fraction of frame width, at which a shot reads
# as fully shaky. At 30fps, 1.5% of the frame width every frame is already a
# very unsteady hand.
SHAKE_FULL_SCALE = 0.015


@dataclass
class QualityMetrics:
    sharpness: float
    exposure_score: float
    # None means "does not apply to this kind of asset", which is different
    # from 0.0 meaning "measured, and bad".
    stability_score: float | None
    motion_quality: float | None
    technical_quality: float
    flat_frame_ratio: float  # fraction of near-black / near-white frames

    def as_dict(self) -> dict[str, float | None]:
        return {
            "sharpness": self.sharpness,
            "exposure_score": self.exposure_score,
            "stability_score": self.stability_score,
            "motion_quality": self.motion_quality,
            "technical_quality": self.technical_quality,
        }


def _clip01(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def sharpness_of(gray: np.ndarray) -> float:
    """Variance of the Laplacian, compressed logarithmically.

    Raw variance spans several orders of magnitude, so a log scale is what
    makes the score comparable between a crisp daylight shot and a dim one.
    """
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if variance <= 0:
        return 0.0
    return _clip01(np.log10(variance) / 3.5)


def exposure_of(gray: np.ndarray) -> tuple[float, bool]:
    """Penalise both a wrong average brightness and clipped highlights/shadows.

    Also returns whether the frame is *featureless*: either mostly clipped, or
    with almost no variation at any brightness. A real sensor never produces a
    near-zero-variance frame, so that condition means a covered lens or a flat
    wall - useless footage regardless of how well exposed it is. This is a
    separate, harder rejection signal than a merely poor exposure score.
    """
    normalised = gray.astype(np.float32) / 255.0
    mean = float(normalised.mean())
    clipped = float(((normalised < 0.02) | (normalised > 0.98)).mean())
    score = _clip01(1.0 - (2.0 * abs(mean - 0.5) + clipped))
    is_flat = clipped > 0.9 or normalised.std() < 0.02
    return score, is_flat


def _displacements(frames: list[np.ndarray]) -> np.ndarray:
    """Median per-frame motion, as a fraction of frame width.

    Sparse Lucas-Kanade rather than dense flow: it is an order of magnitude
    cheaper and the median of tracked corners is what the shake estimate needs
    anyway.
    """
    if len(frames) < 2:
        return np.zeros(0, dtype=np.float32)

    width = frames[0].shape[1]
    out: list[float] = []
    for previous, current in zip(frames, frames[1:]):
        corners = cv2.goodFeaturesToTrack(
            previous, maxCorners=60, qualityLevel=0.01, minDistance=8
        )
        if corners is None or len(corners) < 5:
            continue
        moved, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, corners, None)
        if moved is None or status is None:
            continue
        keep = status.reshape(-1).astype(bool)
        if keep.sum() < 5:
            continue
        delta = np.linalg.norm(
            moved.reshape(-1, 2)[keep] - corners.reshape(-1, 2)[keep], axis=1
        )
        out.append(float(np.median(delta)) / max(width, 1))
    return np.asarray(out, dtype=np.float32)


def sample_motion_bursts(
    video_path: str,
    start: float,
    end: float,
    bursts: int = MOTION_BURSTS,
    per_burst: int = MOTION_BURST_FRAMES,
) -> list[list[np.ndarray]]:
    """Short runs of consecutive frames, sampled at a few points in the shot.

    Consecutive is the whole point: the gap between frames must be one frame
    interval, not one sampling interval, or the result describes the subject
    rather than the camera.
    """
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return []
    try:
        span = max(end - start, 1e-3)
        out: list[list[np.ndarray]] = []
        for index in range(bursts):
            offset = start + span * (index + 0.5) / bursts
            capture.set(cv2.CAP_PROP_POS_MSEC, offset * 1000.0)
            burst: list[np.ndarray] = []
            for _ in range(per_burst):
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                burst.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            if len(burst) >= 2:
                out.append(burst)
        return out
    finally:
        capture.release()


def sample_gray_frames(
    video_path: str, start: float, end: float, count: int = SAMPLE_FRAMES
) -> list[np.ndarray]:
    """Evenly spaced grayscale frames from [start, end)."""
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return []
    try:
        span = max(end - start, 1e-3)
        offsets = [start + span * (i + 0.5) / count for i in range(count)]
        frames: list[np.ndarray] = []
        for offset in offsets:
            capture.set(cv2.CAP_PROP_POS_MSEC, offset * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        return frames
    finally:
        capture.release()


def measure_photo(image_path: str) -> QualityMetrics:
    """Quality of a still.

    Only the two metrics that mean anything for a single frame. Stability and
    motion are reported as None so that nothing downstream can average them in
    as zeros.
    """
    image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return QualityMetrics(0.0, 0.0, None, None, 0.0, 1.0)

    sharpness = sharpness_of(image)
    exposure, is_flat = exposure_of(image)
    technical = _clip01(
        PHOTO_W_SHARPNESS * sharpness + PHOTO_W_EXPOSURE * exposure
    )
    return QualityMetrics(
        sharpness=round(sharpness, 4),
        exposure_score=round(exposure, 4),
        stability_score=None,
        motion_quality=None,
        technical_quality=round(technical, 4),
        flat_frame_ratio=1.0 if is_flat else 0.0,
    )


def measure(video_path: str, start: float, end: float) -> QualityMetrics:
    frames = sample_gray_frames(video_path, start, end)
    if not frames:
        # Unreadable range: report the worst score rather than a neutral one,
        # so it gets rejected instead of silently competing with real footage.
        return QualityMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

    sharpness = float(np.median([sharpness_of(f) for f in frames]))

    exposures, flats = [], []
    for frame in frames:
        score, is_flat = exposure_of(frame)
        exposures.append(score)
        flats.append(is_flat)
    exposure = float(np.median(exposures))
    flat_ratio = float(np.mean(flats))

    bursts = sample_motion_bursts(video_path, start, end)
    per_burst = [_displacements(burst) for burst in bursts]
    displacements = np.concatenate([d for d in per_burst if d.size]) if any(
        d.size for d in per_burst
    ) else np.zeros(0, dtype=np.float32)

    if displacements.size:
        stability = _clip01(1.0 - float(np.median(displacements)) / SHAKE_FULL_SCALE)
        # Jerky motion shows up as variance between consecutive frames, not as
        # raw speed: a smooth pan should not be penalised, a shaky one should.
        jitter = float(np.std(displacements)) / SHAKE_FULL_SCALE
        motion = _clip01(1.0 - jitter)
    else:
        # No trackable features usually means a flat or blurred shot; neither
        # deserves a confident stability score.
        stability, motion = 0.5, 0.5

    technical = _clip01(
        W_SHARPNESS * sharpness
        + W_EXPOSURE * exposure
        + W_STABILITY * stability
        + W_MOTION * motion
    )
    return QualityMetrics(
        sharpness=round(sharpness, 4),
        exposure_score=round(exposure, 4),
        stability_score=round(stability, 4),
        motion_quality=round(motion, 4),
        technical_quality=round(technical, 4),
        flat_frame_ratio=round(flat_ratio, 4),
    )
