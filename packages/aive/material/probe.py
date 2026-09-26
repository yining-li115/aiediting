"""Asset probing and proxy transcoding.

Everything downstream analyses the proxy, not the original: phone footage
arrives at wildly different resolutions, frame rates and rotations, and
normalising once here means the quality metrics are comparable across clips
and the CV code never has to think about orientation.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import settings
from ..specs.base import new_id
from ..specs.enums import PHOTO_NOMINAL_DURATION, PHOTO_SUFFIXES
from ..specs.segment import Asset

PROXY_HEIGHT = 540
PROXY_FPS = 30
THUMBNAIL_WIDTH = 480


class ProbeError(RuntimeError):
    pass


def ffprobe(path: Path) -> dict:
    proc = subprocess.run(
        [
            settings.ffprobe_bin, "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe failed on {path}: {proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout)


def _fps(stream: dict) -> float:
    raw = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
    num, _, den = raw.partition("/")
    try:
        denom = float(den or 1)
        return float(num) / denom if denom else 0.0
    except ValueError:
        return 0.0


def _rotation(stream: dict) -> int:
    """Rotation can live in a tag or in a display matrix side-data entry."""
    tag = stream.get("tags", {}).get("rotate")
    if tag:
        return int(float(tag)) % 360
    for entry in stream.get("side_data_list", []) or []:
        if "rotation" in entry:
            return int(float(entry["rotation"])) % 360
    return 0


def is_photo(path: Path) -> bool:
    return path.suffix.lower() in PHOTO_SUFFIXES


def probe_photo(
    path: Path,
    project_id: str,
    asset_id: str | None = None,
    storage_key: str = "",
) -> Asset:
    """A still image, described in the same shape as a video.

    Duration is nominal: a photo has none, and the matcher is free to give it
    whatever a slot asks for. Reporting it as zero would make it look like a
    broken file to every length check downstream.
    """
    info = ffprobe(path)
    streams = info.get("streams", [])
    image = next((s for s in streams if s.get("codec_type") == "video"), None)
    if image is None:
        raise ProbeError(f"{path} could not be read as an image")

    return Asset(
        asset_id=asset_id or new_id("asset"),
        project_id=project_id,
        storage_key=storage_key,
        kind="photo",
        original_name=path.name,
        duration=PHOTO_NOMINAL_DURATION,
        width=int(image["width"]),
        height=int(image["height"]),
        fps=0.0,
        has_audio=False,
    )


def probe_asset(
    path: Path,
    project_id: str,
    asset_id: str | None = None,
    storage_key: str = "",
) -> Asset:
    if is_photo(path):
        return probe_photo(path, project_id, asset_id, storage_key)

    info = ffprobe(path)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError(f"{path} has no video stream")

    width, height = int(video["width"]), int(video["height"])
    # A 90/270 degree rotation means the stored dimensions are transposed
    # relative to how the clip is actually displayed.
    if _rotation(video) in (90, 270):
        width, height = height, width

    duration = float(info["format"].get("duration") or video.get("duration") or 0.0)
    if duration <= 0:
        raise ProbeError(f"{path} reports a non-positive duration")

    return Asset(
        asset_id=asset_id or new_id("asset"),
        project_id=project_id,
        storage_key=storage_key,
        original_name=path.name,
        duration=duration,
        width=width,
        height=height,
        fps=_fps(video),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def make_photo_proxy(
    asset: Asset, source: Path, out_path: Path, force: bool = False
) -> Path:
    """A resized still, so everything downstream reads one consistent size."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not force:
        return out_path
    cmd = [
        settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vf", f"scale=-2:{PROXY_HEIGHT}:force_original_aspect_ratio=decrease",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProbeError(
            f"photo proxy failed for {asset.asset_id}: {proc.stderr.strip()[:400]}"
        )
    return out_path


def make_proxy(
    asset: Asset, source: Path, out_path: Path, force: bool = False
) -> Path:
    """Transcode to a normalised analysis copy: 540p, 30fps, no audio.

    Audio is dropped deliberately - speech goes to ASR from the original file,
    and carrying an audio track here only slows the CV passes down.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not force:
        return out_path

    cmd = [
        settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vf", f"scale=-2:{PROXY_HEIGHT}:force_original_aspect_ratio=decrease",
        "-r", str(PROXY_FPS),
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProbeError(
            f"proxy transcode failed for {asset.asset_id}: "
            f"{proc.stderr.strip()[:400]}"
        )
    return out_path


def make_thumbnail(
    asset: Asset, source: Path, out_path: Path, force: bool = False
) -> Path:
    """Create one compact JPEG poster without changing authoritative media."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not force:
        return out_path

    cmd = [settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error"]
    if asset.kind == "video":
        cmd.extend(["-ss", f"{min(asset.duration / 2, 2.0):.3f}"])
    cmd.extend(
        [
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            (
                f"scale={THUMBNAIL_WIDTH}:{THUMBNAIL_WIDTH}:"
                "force_original_aspect_ratio=decrease"
            ),
            str(out_path),
        ]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProbeError(
            f"thumbnail generation failed for {asset.asset_id}: "
            f"{proc.stderr.strip()[:400]}"
        )
    return out_path


def cut_clip(source: Path, start: float, end: float, out_path: Path) -> Path:
    """Extract one segment as a standalone file.

    Segments are ranges, not files - nothing is cut during analysis, and
    cutting only happens at render time. Sending a clip to a model is the one
    case that needs a real file, so this produces a temporary one from the
    540p proxy and the caller deletes it.

    Re-encoded rather than stream-copied: a copy snaps to the nearest keyframe,
    which for a two-second segment can miss the part we meant to ask about.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}",
        "-t", f"{max(end - start, 0.1):.3f}",
        "-i", str(source),
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProbeError(f"clip cut failed: {proc.stderr.strip()[:300]}")
    return out_path
