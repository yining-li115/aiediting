"""Telling a hard cut from a dissolve.

Transition density is one of the six things a remake is measured against, so
counting every boundary as a hard cut would make a reference full of dissolves
look identical to one with none.

The signal is how the change is distributed in time. A hard cut concentrates
the entire frame difference into a single frame boundary; a dissolve spreads a
smaller difference across many frames. Measuring the spread separates them
without needing to recognise the effect itself.
"""

from __future__ import annotations

import cv2
import numpy as np

# TODO(review): the window and the thresholds below were chosen from how
# dissolves typically look at 30fps (12-30 frames) versus a cut (1 frame).
# Never validated against a labelled set - the honest test is a handful of
# reference videos with their transitions marked by hand.
WINDOW_FRAMES = 12
CONCENTRATION_HARD = 0.55  # a cut puts most of the change in one frame pair
CONCENTRATION_SOFT = 0.30
MIN_DISSOLVE_FRAMES = 4


def _frame_diffs(
    capture: cv2.VideoCapture, centre: float, fps: float, window: int
) -> np.ndarray:
    """Mean absolute difference between consecutive frames around ``centre``."""
    start = max(0.0, centre - (window / 2) / max(fps, 1e-6))
    capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)

    frames: list[np.ndarray] = []
    for _ in range(window + 1):
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        small = cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA)
        frames.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32))

    if len(frames) < 3:
        return np.zeros(0, dtype=np.float32)
    return np.asarray(
        [float(np.abs(b - a).mean()) for a, b in zip(frames, frames[1:])],
        dtype=np.float32,
    )


def classify(video_path: str, boundary: float, fps: float) -> tuple[str, float]:
    """Return (transition, duration_seconds) for the cut at ``boundary``."""
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return "hard_cut", 0.0
    try:
        diffs = _frame_diffs(capture, boundary, fps, WINDOW_FRAMES)
    finally:
        capture.release()

    if diffs.size < 3 or diffs.sum() <= 1e-6:
        return "hard_cut", 0.0

    # How much of the total change sits in its single largest frame pair.
    concentration = float(diffs.max() / diffs.sum())
    if concentration >= CONCENTRATION_HARD:
        return "hard_cut", 0.0

    if concentration <= CONCENTRATION_SOFT:
        # Spread out: count the frames carrying an above-average share, which
        # approximates how long the blend lasts.
        active = int((diffs > diffs.mean()).sum())
        if active >= MIN_DISSOLVE_FRAMES:
            return "dissolve", round(active / max(fps, 1e-6), 3)

    # Between the two: not confidently either, and a hard cut is the safer
    # default because claiming a dissolve that is not there would inflate the
    # transition density a remake tries to reproduce.
    return "hard_cut", 0.0
