"""Objective physical-shot boundary detection."""

from __future__ import annotations

from pathlib import Path

from scenedetect import SceneManager, open_video
from scenedetect.detectors import ContentDetector

# Content-aware threshold. Lower detects more cuts; 27 is the library default
# and behaves reasonably on handheld vlog footage.
DEFAULT_THRESHOLD = 27.0
MIN_SCENE_LEN_FRAMES = 15

def detect_boundaries(
    video_path: Path,
    threshold: float = DEFAULT_THRESHOLD,
    min_scene_len: int = MIN_SCENE_LEN_FRAMES,
) -> list[tuple[float, float]]:
    """Raw (start, end) pairs in seconds."""
    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(
        ContentDetector(threshold=threshold, min_scene_len=min_scene_len)
    )
    manager.detect_scenes(video, show_progress=False)
    scenes = manager.get_scene_list()

    if not scenes:
        # A single continuous take produces no cuts; treat the whole file as
        # one physical shot. Semantic grouping remains a model decision.
        return [(0.0, float(video.duration.seconds))]
    return [(s.seconds, e.seconds) for s, e in scenes]


__all__ = ["DEFAULT_THRESHOLD", "MIN_SCENE_LEN_FRAMES", "detect_boundaries"]
