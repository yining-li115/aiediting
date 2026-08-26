"""Generate synthetic test clips with ffmpeg.

Block A must be verifiable without any real footage. Each clip carries a
distinct hue and an on-screen timer, so a wrong cut point or a wrong clip
order is visible on playback rather than only in the numbers.

``drawtext`` needs libfreetype, which not every ffmpeg build has, so the
generator probes for it and falls back to ``testsrc2`` (which draws its own
timer) tinted per asset.
"""

from __future__ import annotations

import argparse
import subprocess
from functools import lru_cache
from pathlib import Path

COLORS = ["red", "green", "blue", "orange", "purple", "teal", "brown", "navy"]
HUES = [0, 45, 90, 135, 180, 225, 270, 315]


@lru_cache(maxsize=1)
def has_drawtext() -> bool:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True
    )
    return " drawtext " in out.stdout


def _video_input(index: int, seconds: float) -> tuple[list[str], str]:
    """Return (input args, video filter) for one synthetic clip."""
    if has_drawtext():
        color = COLORS[index % len(COLORS)]
        src = f"color=c={color}:s=720x1280:r=30:d={seconds}"
        label = f"asset_{index + 1:03d}"
        vf = (
            f"drawtext=text='{label} %{{eif\\:t\\:d\\:2}}s':"
            f"fontcolor=white:fontsize=64:x=(w-tw)/2:y=(h-th)/2"
        )
        return ["-f", "lavfi", "-i", src], vf
    # testsrc2 renders its own running timer, so no font is required.
    src = f"testsrc2=s=720x1280:r=30:d={seconds}"
    return ["-f", "lavfi", "-i", src], f"hue=h={HUES[index % len(HUES)]}"


def make_clip(path: Path, index: int, seconds: float, tone_hz: int) -> None:
    video_args, vf = _video_input(index, seconds)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *video_args,
        "-f", "lavfi",
        "-i", f"sine=frequency={tone_hz}:sample_rate=48000:d={seconds}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-t", str(seconds),
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def make_music(path: Path, seconds: float) -> None:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:d={seconds}",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("data/media"))
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"drawtext available: {has_drawtext()}")
    for i in range(args.count):
        asset_id = f"asset_{i + 1:03d}"
        make_clip(args.out / f"{asset_id}.mp4", i, args.seconds, 220 + 110 * i)
        print(f"wrote {asset_id}.mp4")

    make_music(args.out / "music_01.m4a", 60.0)
    print("wrote music_01.m4a")


if __name__ == "__main__":
    main()
