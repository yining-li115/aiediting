"""Shot boundary detection.

Timing comes from a deterministic detector, never from a model. This is
FD-M04, and it is not a cost decision: shot boundaries are exactly computable,
so asking a VLM to guess them trades accuracy and reproducibility for nothing.

Two rules on top of raw detection, both about what the matcher can use later:

* a shot longer than ``MAX_SEGMENT`` is split, because a 30-second continuous
  take is not one candidate - it contains many, and slot matching needs to be
  able to choose between them;
* a shot shorter than ``MIN_SEGMENT`` is merged into its neighbour, because it
  is below the length at which anything can be shown on screen.
"""

from __future__ import annotations

from pathlib import Path

from scenedetect import SceneManager, open_video
from scenedetect.detectors import ContentDetector

from ..specs.base import new_id
from ..specs.segment import Asset, RawSegment

# Content-aware threshold. Lower detects more cuts; 27 is the library default
# and behaves reasonably on handheld vlog footage.
DEFAULT_THRESHOLD = 27.0
MIN_SCENE_LEN_FRAMES = 15

MIN_SEGMENT = 0.6
MAX_SEGMENT = 6.0
TARGET_SPLIT = 4.0


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
        # one shot and let the length rules below break it up.
        return [(0.0, float(video.duration.seconds))]
    return [(s.seconds, e.seconds) for s, e in scenes]


def apply_length_rules(
    spans: list[tuple[float, float]],
    min_segment: float = MIN_SEGMENT,
    max_segment: float = MAX_SEGMENT,
    target_split: float = TARGET_SPLIT,
) -> list[tuple[float, float]]:
    """Split over-long shots, merge under-length ones."""
    split: list[tuple[float, float]] = []
    for start, end in spans:
        length = end - start
        if length <= max_segment:
            split.append((start, end))
            continue
        parts = max(2, round(length / target_split))
        step = length / parts
        split.extend(
            (start + i * step, start + (i + 1) * step) for i in range(parts)
        )

    merged: list[tuple[float, float]] = []
    for start, end in split:
        if end - start >= min_segment or not merged:
            merged.append((start, end))
            continue
        # Absorb the runt into the previous span rather than dropping it, so
        # the segment list still tiles the whole asset.
        prev_start, _ = merged[-1]
        merged[-1] = (prev_start, end)

    # A leading runt can survive the loop above if it was the very first span.
    if len(merged) > 1 and merged[0][1] - merged[0][0] < min_segment:
        merged[1] = (merged[0][0], merged[1][1])
        merged.pop(0)
    return merged


def split_asset(
    asset: Asset,
    video_path: Path | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[RawSegment]:
    """Full split for one asset, analysed on its proxy when there is one.

    ``video_path`` is supplied by the caller because storage owns key
    resolution; this module only knows how to find cuts in a file.
    """
    if asset.kind == "photo":
        # Nothing to detect: a still is exactly one candidate. Running a shot
        # detector over it would return one scene anyway, at the cost of
        # decoding a video that is not one.
        return [
            RawSegment(
                segment_id=f"{asset.asset_id}_seg01",
                project_id=asset.project_id,
                asset_id=asset.asset_id,
                kind="photo",
                start=0.0,
                end=asset.duration,
            )
        ]

    if video_path is None:
        from ..storage import get_storage  # noqa: PLC0415

        storage = get_storage()
        video_path = storage.open_local(asset.proxy_key or asset.storage_key)
    source = Path(video_path)
    spans = apply_length_rules(detect_boundaries(source, threshold))
    return [
        RawSegment(
            segment_id=f"{asset.asset_id}_seg{index:02d}",
            project_id=asset.project_id,
            asset_id=asset.asset_id,
            start=round(start, 3),
            end=round(end, 3),
        )
        for index, (start, end) in enumerate(spans, start=1)
    ]
