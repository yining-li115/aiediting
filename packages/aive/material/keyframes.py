"""Keyframe extraction.

Three frames per segment - first, middle, last. The middle frame is what the
material VLM sees; all three are averaged into the embedding, which is what
makes a segment's vector represent the whole shot rather than one instant of
it. That matters for shots with camera movement, where the first and last
frames can show quite different content.
"""

from __future__ import annotations

from pathlib import Path

import cv2

FRACTIONS = (0.1, 0.5, 0.9)
MIDDLE_INDEX = 1


def extract(
    video_path: str,
    start: float,
    end: float,
    targets: list[Path],
    width: int = 512,
) -> list[Path]:
    """Write up to ``len(targets)`` stills to the given paths.

    Paths are supplied by the caller because storage owns the layout; this
    function only knows how to pull frames out of a video.
    """
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return []

    paths: list[Path] = []
    try:
        span = max(end - start, 1e-3)
        for fraction, path in zip(FRACTIONS, targets):
            capture.set(cv2.CAP_PROP_POS_MSEC, (start + span * fraction) * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            height = int(frame.shape[0] * width / frame.shape[1])
            resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
            path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(path), resized, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            paths.append(path)
    finally:
        capture.release()
    return paths
